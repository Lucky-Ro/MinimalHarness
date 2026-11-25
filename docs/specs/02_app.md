# Spec 02：应用层（`agent/app/`）

> **硬约束**：本层只允许 `import` `agent.domain` 和 Python 标准库，**严禁 `import` `agent.adapters` 或任何第三方 SDK（如 `openai`）**。所有外部能力（LLM、存储、审核器、日志）全部通过本层定义的 Port（抽象接口）进行依赖倒置。

---

## 1. 模块划分

```text
agent/app/
├── __init__.py
├── ports.py        # Effort, ModelSettings, ModelClient, SessionStore, ToolReviewer, TraceLogger
└── service.py      # AgentService
```

---

## 2. `ports.py` — 抽象端口定义

- **`Effort`**（`Enum`）
  - `NONE`, `MINIMAL`, `LOW`, `MEDIUM`, `HIGH`, `MAX`（适配层负责将其转译为具体厂商参数，如 OpenAI 的 `reasoning_effort`）
- **`ModelSettings`**（不可变 dataclass）
  - `model_id: str`
  - `effort: Effort = Effort.MEDIUM`
  - `context_window: int = 64000`（用于 70% 上下文压缩触发计算，测试中可设为较小值如 `200`）
- **`ModelClient`**（`Protocol` 或 `ABC`）
  - `generate(context: Context[O], model_settings: ModelSettings) -> O`
  - 返回类型必须与 `context.output_type` 一致（`AssistantMessage` / `Decision` / `CompactionSummary`）。
- **`SessionStore`**（`Protocol` 或 `ABC`）
  - `list() -> list[Session]`
  - `save(session: Session) -> None`
  - `load(session_id: str) -> Session`（找不到时抛出明确的领域/应用异常，如 `SessionNotFoundError`）
- **`ToolReviewer`**（`Protocol` 或 `ABC`）
  - `review(session: Session, call: ToolCall) -> Decision`
  - 设计意图：让 `AgentService` 既能注入 `RuleBasedReviewer`（TDD 离线测试 / 硬护栏），也能注入 `LlmToolReviewer`（真实 Flash 轻量模型）或 `CompositeReviewer`（先规则后模型），无需改动一行编排代码。
- **`TraceLogger`**（`Protocol` 或 `ABC`，可选默认空实现 `NullTraceLogger`）
  - `log(event: str, payload: dict[str, Any]) -> None`
  - 用于记录关键 trace 事件：`session_created`、`user_message`、`compaction_triggered`、`compaction_succeeded`、`compaction_failed`、`model_called`、`tool_reviewed`、`tool_executed`、`tool_error`、`max_iterations_reached`。

---

## 3. `service.py` — `AgentService` 编排契约

### 3.1 构造注入参数
- `model_client: ModelClient`
- `session_store: SessionStore`
- `assistant: Assistant`
- `reviewer: ToolReviewer`
- `compactor: ContextCompactor`
- `assistant_model_settings: ModelSettings`
- `compactor_model_settings: ModelSettings`（指向 Flash / 轻量模型）
- `max_iterations: int = 8`
- `compaction_threshold: float = 0.7`（窗口占用达到 70% 时触发压缩）
- `keep_recent_messages: int = 4`
- `tracer: TraceLogger | None = None`

### 3.2 对外方法（供 CLI / 未来 WebApi 调用）
1. **`new_session(session_id: str | None = None) -> Session`**
   - 创建新 `Session`，调用 `session_store.save(session)` 立即持久化并返回。
2. **`list_sessions() -> list[Session]`**
   - 返回所有已保存的会话列表。
3. **`get_session(session_id: str) -> Session`**
   - 从 `session_store.load(session_id)` 加载指定会话。
4. **`chat(session_id: str, user_message: str) -> list[Message]`**
   - 返回**本次 `chat()` 调用期间新产生的所有 `Message`**（不含旧历史），方便 CLI 增量打印和单测精准断言。

### 3.3 `chat()` 核心状态机（逐步规范）

1. **加载会话并写入用户消息**
   - `session = self.session_store.load(session_id)`
   - 构造 `UserMessage(text=user_message)`，调用 `session.append(...)` 并立即 `session_store.save(session)`。
   - 记录 `compaction_attempted_this_turn = False`。

2. **进入主循环：`for iteration in range(1, self.max_iterations + 1)`**
   - **Step A — 组装上下文与 70% 压缩检查**
     - `ctx = self.assistant.context(session)`
     - 计算 `estimated = estimate_context_tokens(ctx)` 与阈值 `limit = int(self.assistant_model_settings.context_window * self.compaction_threshold)`。
     - 若 `not compaction_attempted_this_turn` 且 `estimated >= limit`：
       - 标记 `compaction_attempted_this_turn = True`（防止单次 `chat` 死循环触发）。
       - 调用 `split_for_compaction(session.active_messages(), keep_recent=self.keep_recent_messages)` 得到 `(to_compact, delta)`。
       - 若 `delta > 0` 且 `to_compact` 非空：
         - 记录 trace：`compaction_triggered`。
         - 组装 `compact_ctx = self.compactor.context(session.summary, to_compact)`。
         - 在 `try ... except Exception as exc:` 中调用 `result = self.model_client.generate(compact_ctx, self.compactor_model_settings)`：
           - **成功**：更新 `session.summary = result.summary`、`session.compacted_until += delta`，立即 `session_store.save(session)`，记录 trace `compaction_succeeded`，并用更新后的 `session` 重新生成 `ctx = self.assistant.context(session)`。
           - **失败（优雅降级）**：**不修改** `session.summary` 与 `session.compacted_until`，记录 trace `compaction_failed`（含异常信息），继续用原 `ctx` 走主对话；下一次用户发消息进入 `chat()` 时，由于指针未推进，会自动再次尝试压缩。
       - 若 `delta == 0`：记录 trace `compaction_skipped`（无可压缩的旧回合），直接继续。

   - **Step B — 调用主模型**
     - 调用 `assistant_msg = self.model_client.generate(ctx, self.assistant_model_settings)`。
     - 将 `assistant_msg` 追加到 `session` 并立即持久化，记录 trace `model_called`（含是否有 `thinking`、工具调用数）。
     - 若 `not assistant_msg.tool_calls`：**循环结束**，返回本轮新产生的 `messages`。

   - **Step C — 逐个审核并执行工具调用**
     - 对 `assistant_msg.tool_calls` 中的每个 `call`：
       1. 调用 `decision = self.reviewer.review(session, call)`，记录 trace `tool_reviewed`。
       2. 若 `not decision.should_allow`：
          - 构造 `ToolResult(tool_call_id=call.id, content=f"Tool call rejected: {decision.reason or 'denied by reviewer'}", is_error=True)`。
       3. 若 `decision.should_allow`：
          - 在 `self.assistant.tools` 中按 `call.tool_name` 查找工具：
            - 找不到：构造 `ToolResult(tool_call_id=call.id, content=f"Unknown tool: {call.tool_name}", is_error=True)`。
            - 找到：在 `try ... except Exception as exc:` 中调用 `tool.execute(call.arguments, ToolContext(session_id=session.id))`；
              - 成功：构造 `ToolResult(tool_call_id=call.id, content=output, is_error=False)`，记录 trace `tool_executed`（含耗时）。
              - 异常：捕获所有工具执行异常，构造 `ToolResult(tool_call_id=call.id, content=f"Tool execution error ({type(exc).__name__}): {exc}", is_error=True)`，记录 trace `tool_error`——绝不让单个工具崩溃拖垮主循环。
       4. 每产生一条 `ToolResult`，立即 `session.append(result)` 并 `session_store.save(session)`。

3. **达到 `max_iterations` 兜底**
   - 若循环跑满 `self.max_iterations` 仍未自然结束（最后一条仍是 `ToolResult`）：
     - 记录 trace `max_iterations_reached`。
     - 追加一条明确提示达到最大轮次上限的 `AssistantMessage(text="已达到最大工具调用轮次上限，请尝试拆分任务或补充信息后继续。")` 并保存，返回本轮新产生的消息。

---

## 4. 执行待办清单（GPT 逐项勾选）

- [x] 实现 `agent/app/ports.py`（`Effort`、`ModelSettings`、`ModelClient`、`SessionStore` 含 `SessionNotFoundError`、`ToolReviewer`、`TraceLogger` / `NullTraceLogger`）
- [x] 实现 `agent/app/service.py` 会话管理接口（`new_session`、`list_sessions`、`get_session`）与 `chat()` 主循环
- [x] 实现 70% 上下文窗口 LLM 滚动压缩编排（保留完整历史仅推进 `compacted_until`、失败捕获记 `compaction_failed` 优雅降级且下次发消息自动重试、`delta == 0` 跳过）
- [x] 实现工具审核拒绝、未知工具、工具执行异常转 `ToolResult(is_error=True)` 闭环与 `max_iterations` 兜底
- [x] 验证 `agent/app/` 仅依赖标准库与 `agent.domain`，且 `tests/test_agent_service.py` 全绿
