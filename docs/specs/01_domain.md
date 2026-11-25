# Spec 01：领域层（`agent/domain/`）

> **硬约束**：本层定义系统自身的业务语言，**严禁 `import` 任何第三方库（如 `openai`、`pydantic`）或 `agent.app`、`agent.adapters`**，仅允许使用 Python 标准库（`dataclasses`、`typing`、`abc`、`enum`、`uuid` 等）。

---

## 1. 模块划分

```text
agent/domain/
├── __init__.py
├── session.py      # Session, UserMessage, AssistantMessage, ToolCall, ToolResult, Message
├── tool.py         # ToolContext, Tool
├── context.py      # Context[O], TokenEstimator, split_for_compaction
└── roles.py        # Assistant, ToolCallReviewer, Decision, ContextCompactor, CompactionSummary
```

---

## 2. `session.py` — 对话与消息模型

- **`ToolCall`**（不可变 dataclass）
  - `id: str`
  - `tool_name: str`
  - `arguments: dict[str, Any]`
- **`UserMessage`**（不可变 dataclass）
  - `text: str`
- **`AssistantMessage`**（不可变 dataclass）
  - `text: str = ""`
  - `tool_calls: tuple[ToolCall, ...] = ()`
  - `thinking: str | None = None`（用于承载 DeepSeek `reasoning_content` 等思考过程；仅作展示与日志，**不回传**到后续历史上下文）
- **`ToolResult`**（不可变 dataclass）
  - `tool_call_id: str`
  - `content: str`
  - `is_error: bool = False`（标识工具执行异常或审核被拒，便于 trace 与测试断言）
- **`Message = UserMessage | AssistantMessage | ToolResult`**
- **`Session`**（dataclass）
  - `id: str`（默认生成唯一 ID，如 UUID 短串或调用方传入）
  - `messages: list[Message] = field(default_factory=list)`
  - `summary: str | None = None`（最新一次 LLM 滚动压缩生成的摘要）
  - `compacted_until: int = 0`（已压缩覆盖到的 `messages` 下标，左闭右开 `messages[:compacted_until]`）
  - `append(message: Message) -> None`：追加消息，不修改历史消息。
  - `active_messages() -> list[Message]`：返回 `self.messages[self.compacted_until:]`，保证原始历史永不删除。

---

## 3. `tool.py` — 工具抽象与安全上下文

- **`ToolContext`**（不可变 dataclass）
  - `session_id: str`
  - 设计意图：将「LLM 决定的参数 `arguments`」与「系统注入的可信运行时身份 `ctx`」彻底解耦。LLM 永远无法在参数里伪造或修改 `session_id`。
- **`Tool`**（`ABC` 或 `Protocol`）
  - `name: str`
  - `description: str`
  - `parameters: dict[str, Any]`（标准 JSON Schema `object` 格式，供适配层直接翻译给 OpenAI 原生 Function Calling）
  - `execute(arguments: dict[str, Any], ctx: ToolContext) -> str`
    - 输入：模型传入的 `arguments` + 系统注入的 `ToolContext`
    - 输出：字符串结果，作为 `ToolResult.content` 写回会话

---

## 4. `context.py` — 泛型上下文、Token 估算与原子对安全切分

- **`Context[O]`**（Python 泛型 `Generic[O]`，不可变 dataclass）
  - `instruction: str`：本轮调用的系统指令与边界
  - `tools: tuple[Tool, ...] = ()`：本轮可用工具列表
  - `messages: tuple[Message, ...] = ()`：送入模型的有效历史消息
  - `output_type: type[O]`：规定 `ModelClient.generate(...)` 必须返回的领域对象类型
    - 主对话：`Context[AssistantMessage]`
    - 工具审核：`Context[Decision]`
    - 上下文压缩：`Context[CompactionSummary]`
  - `summary: str | None = None`：当前会话已有的压缩摘要（非空时由适配层以 `<conversation_summary>` 形式置于静态指令后、近期消息前）
  - `memory: tuple[str, ...] = ()`：**跨会话长期记忆预留槽位**（当前 MVP 保持空元组，不接入向量库）

- **Token 估算（纯函数，无第三方依赖）**
  - `estimate_tokens(text: str) -> int`：轻量启发式估算（例如：非 ASCII 字符按约 1.5 字/token、ASCII 字符按约 4 字符/token 向上取整，保证结果确定且单调递增）。
  - `estimate_context_tokens(context: Context[Any]) -> int`：累计 `instruction`、`summary`、`memory`、各 `tools` 的 schema 文本以及 `messages` 中有效字段（**不含** `AssistantMessage.thinking`，且包含 `tool_calls` 序列化文本与 `ToolResult.content`）。

- **原子对安全切分：`split_for_compaction(messages: list[Message], keep_recent: int = 4) -> tuple[list[Message], int]`**
  - 作用：给定当前未压缩的 `active_messages`，切分出「需要被压缩的旧消息段」以及「推进的相对偏移量 `delta`」，保留尾部至少 `keep_recent` 条消息。
  - **硬性原子对保护规则**：
    - 绝不能把 `AssistantMessage(tool_calls 非空)` 和紧跟在它后面的所有 `ToolResult` 拆散到切分线两侧。
    - 如果目标切分点落在某个 `ToolResult` 上，或刚好落在 `AssistantMessage(tool_calls)` 之后，必须将切分点向前（或向后）调整到完整的回合边界（通常对齐到某条 `UserMessage` 或独立回复之前）。
    - 若调整后可压缩旧消息为空（`delta == 0`，例如当前仅有一轮超长工具调用），直接返回 `([], 0)`，由应用层跳过本次压缩。
  - **明确不做**：不对单条超长 `ToolResult.content` 做字符硬截断。

---

## 5. `roles.py` — 三个领域角色与结构化输出对象

- **`Decision`**（不可变 dataclass）
  - `should_allow: bool`
  - `reason: str | None = None`（拒绝时必填原因，将包装为 `ToolResult(is_error=True)` 交还模型）

- **`CompactionSummary`**（不可变 dataclass）
  - `summary: str`（压缩后的滚动摘要文本）

- **`Assistant`**（主对话上下文组装者）
  - `instruction: str`
  - `tools: tuple[Tool, ...]`
  - `context(session: Session) -> Context[AssistantMessage]`：
    - 读取 `session.summary` 与 `session.active_messages()`；
    - 构造不含历史 `thinking` 的 `Context[AssistantMessage]`（`memory=()` 保持预留）。

- **`ToolCallReviewer`**（工具调用审核上下文组装者）
  - `instruction: str`
  - `context(session: Session, call: ToolCall) -> Context[Decision]`：
    - 不传 `tools`（`tools=()`），携带当前有效上下文与待审核的 `ToolCall` 描述，要求模型返回 `Decision`。

- **`ContextCompactor`**（上下文滚动压缩组装者）
  - `instruction: str`（要求保留用户目标、已确认事实、工具执行关键结论与未完成事项）
  - `context(previous_summary: str | None, messages_to_compact: list[Message]) -> Context[CompactionSummary]`：
    - 不传 `tools`（`tools=()`），将上一次 `previous_summary` 与本批 `messages_to_compact` 组装为 `Context[CompactionSummary]`。

---

## 6. 执行待办清单（GPT 逐项勾选）

- [x] 实现 `agent/domain/session.py`（`ToolCall`、`UserMessage`、`AssistantMessage` 含 `thinking`、`ToolResult` 含 `is_error`、`Message`、`Session` 含 `summary` / `compacted_until` / `append` / `active_messages`）
- [x] 实现 `agent/domain/tool.py`（`ToolContext(session_id)` 与 `Tool` 抽象）
- [x] 实现 `agent/domain/context.py`（`Context[O]` 含 `memory` 预留槽位、`estimate_tokens`、`estimate_context_tokens`、`split_for_compaction` 原子对安全切分）
- [x] 实现 `agent/domain/roles.py`（`Decision`、`CompactionSummary`、`Assistant`、`ToolCallReviewer`、`ContextCompactor`）
- [x] 验证 `agent/domain/` 零第三方依赖、零 `app`/`adapters` 导入，且 `tests/test_domain.py` 全绿
