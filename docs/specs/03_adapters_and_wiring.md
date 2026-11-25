# Spec 03：适配层（`agent/adapters/`）与组装根（`agent/main.py`）

> **职责定位**：适配层负责把外部世界（OpenAI 兼容 API、JSONL 文件、具体工具、审核器、CLI 交互）翻译成领域层与应用层的语言；`main.py` 是整个系统中**唯一**同时引用所有具体实现类并完成依赖注入的地方。

---

## 1. 目录结构

```text
agent/
├── adapters/
│   ├── __init__.py
│   ├── models/
│   │   ├── openai_client.py      # OpenAIClient（原生 Function Calling + DeepSeek 兼容）
│   │   └── fake_client.py        # FakeModelClient（离线 TDD 专用）
│   ├── stores/
│   │   └── jsonl.py              # JsonlSessionStore
│   ├── reviewers/
│   │   ├── rule_reviewer.py      # RuleBasedReviewer（硬护栏 + TDD 边界测试核心）
│   │   ├── llm_reviewer.py       # LlmToolReviewer（接入真实 Flash 轻量模型）
│   │   └── composite_reviewer.py # CompositeReviewer（先规则后模型）
│   ├── tools/
│   │   ├── todo.py               # TodoTool（按 ToolContext.session_id 严格隔离）
│   │   ├── weather.py            # WeatherTool（Mock 天气，直接支撑原题双窗口场景）
│   │   ├── calculator.py         # CalculatorTool（安全 AST 算术求值，禁止裸 eval）
│   │   └── bash.py               # BashTool（受控命令执行，用于配合 Reviewer 演示安全拦截）
│   ├── logging/
│   │   └── trace_logger.py       # Console / JSONL 结构化 TraceLogger
│   └── cli/
│       └── app.py                # 多会话交互式 CLI（支持 /new, /list, /use, /history, /quit）
└── main.py                       # 组装根（Composition Root）
```

---

## 2. `models/openai_client.py` — OpenAI 兼容客户端

- **接入范围**：基于官方 `openai` Python SDK（或兼容 HTTP 客户端），通过 `base_url` 与 `api_key` 支持 DeepSeek / OpenAI / 硅基流动等所有 OpenAI 兼容接口。
- **原生 Function Calling 翻译契约**：
  - **`Context[AssistantMessage]`**：
    - `system` 消息：静态 `context.instruction`（绝不把动态变化内容插在它前面）。
    - 若 `context.summary` 非空：紧跟一条带 XML 边界标签的摘要消息 `<conversation_summary>...</conversation_summary>`，明确声明仅为历史参考。
    - `context.messages` 映射：
      - `UserMessage` -> `{"role": "user", "content": msg.text}`
      - `AssistantMessage` -> `{"role": "assistant", "content": msg.text, "tool_calls": [...]}`（**不回传** `msg.thinking`）
      - `ToolResult` -> `{"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content}`
    - `tools` 映射为 OpenAI `[{"type": "function", "function": {"name", "description", "parameters"}}]`。
    - **响应解析**：
      - 提取 `message.content` -> `AssistantMessage.text`
      - 提取 `getattr(message, "reasoning_content", None)` -> `AssistantMessage.thinking`（兼容 DeepSeek 思考模型）
      - 提取 `message.tool_calls` -> `tuple[ToolCall, ...]`（对 `function.arguments` 做 `json.loads` 容错，若非法 JSON 则保留可读错误或抛清晰解析异常）
  - **`Context[Decision]`**：
    - 要求模型返回 JSON 对象 `{"should_allow": bool, "reason": str | null}`（可使用 `response_format={"type": "json_object"}` 或带容错的 JSON 解析），映射为 `Decision`。
  - **`Context[CompactionSummary]`**：
    - 要求模型返回简洁摘要文本（或 JSON `{"summary": str}`），映射为 `CompactionSummary`。
  - **`Effort` 参数转译**：仅在目标模型支持 `reasoning_effort` 时透传，对不支持该参数的兼容接口安全省略，避免 400 报错。

---

## 3. `stores/jsonl.py` — JSONL 会话持久化

- 按 `session_id` 独立存为 `<root>/<session_id>.jsonl`（同时校验 `session_id` 合法性，防止路径穿越如 `../`）。
- 文件首行（meta 行）存储 `{"type": "meta", "id": ..., "summary": ..., "compacted_until": ...}`，后续每行存储一条带 `type` 标记的序列化 `Message`（`user` / `assistant` / `tool_result`）。
- 要求实现 `save`、`load`、`list`，并在重读（round-trip）后**完整还原** `summary`、`compacted_until`、`tool_calls`、`thinking`、`is_error`。

---

## 4. `reviewers/` — 规则审核器 + Flash 模型审核器

### 4.1 `RuleBasedReviewer`（硬护栏 + TDD 重点）
必须显式拦截并给出清晰 `reason` 的边界与意外情况：
1. **工具名异常**：`tool_name` 为空、仅空白、或不在白名单/已注册工具集合内。
2. **参数结构异常**：`arguments` 不是 `dict`、缺少该工具 schema 声明的必填字段（`required`）、或关键字段类型错误/为空字符串。
3. **`bash` 高危命令拦截**：
   - 破坏性删除与格式化：`rm -rf`、`rm -fr`、`mkfs`、`dd if=`、`> /dev/sd`
   - 提权与系统控制：`sudo`、`su `、`shutdown`、`reboot`、`chmod 777 /`
   - 危险下载执行与反弹壳：`curl ... | sh`、`wget ... | bash`、`nc -e`、`/dev/tcp/`
   - 命令拼接绕过尝试：命令中包含 `;`、`&&`、`||`、`` ` ``、`$(...)` 且关联高危命令
   - 路径穿越或敏感文件读取：访问 `/etc/passwd`、`/etc/shadow`、`~/.ssh`、`.env`
   - 空命令或超长命令（如超过 1000 字符）
4. **`calculator` 异常输入拦截**：表达式为空、包含非法关键字（`import`、`__`、`exec`、`eval`、`open`）或超长表达式。
5. **`todo` 异常输入拦截**：非法 `action`（非 `add` / `list` / `complete` / `delete`）、`add` 时 `title` 为空、`complete`/`delete` 时缺少有效 `todo_id`。
6. **正常合法调用**：返回 `Decision(should_allow=True, reason=None)`。

### 4.2 `LlmToolReviewer`
- 持有领域对象 `ToolCallReviewer` + `ModelClient` + `reviewer_model_settings`（接入 Flash / 轻量模型）。
- `review(session, call)` 组装 `Context[Decision]` 并调用 `model_client.generate(...)`；若审核模型自身超时/异常，按安全默认策略（fail-closed 返回拒绝或记录明确原因）处理。

### 4.3 `CompositeReviewer`
- 接收 `reviewers: list[ToolReviewer]`，按顺序执行：任一拒绝立即短路返回该 `Decision`，全部通过才放行。
- 生产推荐组合：`CompositeReviewer([RuleBasedReviewer(...), LlmToolReviewer(...)])`（先零延迟硬规则兜底，再由 Flash 模型做语义审核）。

---

## 5. `tools/` — 4 个核心工具

1. **`TodoTool`（必做，严格按 `ctx.session_id` 隔离）**
   - 持久化目录：`<data_dir>/todos/<session_id>.json`（或独立的 `TodoStore`），绝不与其他 session 混存。
   - 参数：`action`（`add` / `list` / `complete` / `delete`）、`title`（可选）、`todo_id`（可选）。
   - 返回清晰可读的当前待办状态文本。
2. **`WeatherTool`（Mock 数据，复现原题场景）**
   - 参数：`city: str`、`date: str = "today"`。
   - 内置常见城市 mock 天气数据，未知城市返回友好默认 mock 结果，支持确定性单测。
3. **`CalculatorTool`**
   - 参数：`expression: str`。
   - 使用 Python `ast` 安全解析四则运算、括号、幂运算（限制指数上限防 DoS），**严禁使用裸 `eval()`**；除零或语法错误返回清晰错误文本或抛异常由 `AgentService` 捕获转为 `ToolResult(is_error=True)`。
4. **`BashTool`**
   - 参数：`command: str`。
   - 使用 `subprocess.run(..., timeout=5, capture_output=True, text=True)` 在受控超时下执行，返回 stdout/stderr 与退出码。

---

## 6. `cli/app.py` 与 `main.py` — 多会话 CLI 与组装根

- **多会话 CLI 命令**：
  - 启动时自动创建或加载最近会话，提示当前 `session_id`。
  - `/new`：调用 `service.new_session()` 并切换到新会话。
  - `/list`：列出所有会话 ID、消息条数及是否已有压缩摘要。
  - `/use <session_id>`：切换到指定会话继续对话。
  - `/history`：打印当前会话的历史消息（含摘要状态）。
  - `/quit` 或 `/exit`：退出 CLI。
  - 普通文本输入：调用 `service.chat(current_session_id, text)`，清晰打印思考过程（如有）、工具调用/审核摘要及最终回复。
- **`main.py` 组装根**：
  - 从环境变量（支持 `.env`）读取 `OPENAI_API_KEY`、`OPENAI_BASE_URL`（如 DeepSeek `https://api.deepseek.com`）、`ASSISTANT_MODEL`（如 `deepseek-chat` / `deepseek-reasoner`）、`FLASH_MODEL`（用于审核与 70% 压缩的轻量模型）、`CONTEXT_WINDOW`、`REVIEWER_MODE`（`rule` / `llm` / `composite`）。
  - 创建具体对象并组装 `AgentService` 与 `CliApp`，调用 `cli.run()`。

---

## 7. 执行待办清单（GPT 逐项勾选）

- [x] 实现 `agent/adapters/models/fake_client.py`（离线 TDD 专用）与 `openai_client.py`（原生 Function Calling、DeepSeek `reasoning_content`、`<conversation_summary>`、不回传历史 `thinking`）
- [x] 实现 `agent/adapters/stores/jsonl.py`（`save` / `load` / `list` 完整往返与防路径穿越校验）
- [x] 实现 `agent/adapters/reviewers/`（`RuleBasedReviewer`、`LlmToolReviewer`、`CompositeReviewer`）
- [x] 实现 `agent/adapters/tools/` 4 个工具（`TodoTool` 按 `ctx.session_id` 隔离、`WeatherTool`、`CalculatorTool` 安全 AST、`BashTool` 超时受控）
- [x] 实现 `agent/adapters/logging/trace_logger.py` 结构化 Trace 日志
- [x] 实现 `agent/adapters/cli/app.py` 多会话 CLI（`/new`、`/list`、`/use`、`/history`、`/quit`）与 `agent/main.py` 组装根
- [x] 补齐 `.env.example` 与依赖文件（`pyproject.toml` 或 `requirements.txt`）
