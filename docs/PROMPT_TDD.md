# 系统实现提示词（TDD · 渐进式披露版）

> **使用方式**：将本文件作为主提示词交给代码模型（GPT / Codex / Claude Code）。本文件只定义**地图（Map）、边界（Boundaries）与硬约束（Constraints）**；实现每一层前，**必须按「渐进式披露索引表」先读取对应的 `docs/specs/*.md` 细节文档**，不要靠猜补全接口。

---

## 0. 角色与工作方式

你是一名精通 Clean Architecture（端口与适配器 / 依赖倒置）和 TDD（测试驱动开发）的高级 Python 后端工程师。你的任务是从零实现一个**不依赖任何 Agent 框架**的最小可用 Agent Runtime。

**工作铁律**：
1. **先读规格再动笔**：按下方「分阶段 TDD 路线」，每进入一个 Phase，先读取对应 `docs/specs/*.md` 文件。
2. **严格 TDD（Red → Green → Refactor）**：每个 Phase 先写 `pytest` 测试，再写最小实现代码使其通过，最后清理重构。
3. **实时维护 `docs/TEST_LOG.md`**：每跑完一轮测试，必须如实把测试范围、通过/失败统计、失败根因和修复方案记录到 `docs/TEST_LOG.md`；**禁止隐瞒失败或篡改测试敷衍通过**。
4. **逐项勾选文档待办清单（保留初始 Prompt）**：每完成一项实现或测试，必须将对应文档末尾「执行待办清单」中的 `- [ ]` 勾选为 `- [x]`（可在条目后简注关键文件或修复记录），**严禁删改各文档的原有提示词与规格正文**，以完整保留初始 Prompt 与执行轨迹。
5. **离线可测**：全量 `pytest` 必须在不联网、不消耗真实 API Token 的前提下 100% 通过。

---

## 1. 地图（Map — 做什么 & 细节去哪找）

### 1.1 目标目录结构

```text
agent/
├── domain/                     # 领域层：纯业务对象、泛型 Context、安全切分、3 个领域角色（零三方依赖）
├── app/                        # 应用层：抽象端口（Ports）与 AgentService 主编排器
├── adapters/                   # 适配层：OpenAI 兼容客户端、Fake 客户端、JSONL 存储、审核器、4 个工具、日志、多会话 CLI
└── main.py                     # 组装根（Composition Root）：全系统唯一实例化并组装所有具体类的地方
tests/                          # 分层 pytest 套件（全离线可跑）
docs/
├── specs/                      # 渐进式披露详细规格（按需读取）
└── TEST_LOG.md                 # TDD 测试与问题修复日志（由你实时维护）
```

### 1.2 渐进式披露索引表（按需读取细节）

| 你正在做什么 | 必须先读取的细节文档 | 核心内容摘要 |
| :--- | :--- | :--- |
| 实现 `agent/domain/` | [`docs/specs/01_domain.md`](specs/01_domain.md) | `Session`（含 `summary` / `compacted_until`）、3 类 `Message`（含 `thinking`）、`ToolContext`、`Tool`、`Context[O]`（预留 `memory`）、`Assistant` / `ToolCallReviewer` / `ContextCompactor`、Token 估算与原子对安全切分 |
| 实现 `agent/app/` | [`docs/specs/02_app.md`](specs/02_app.md) | `ModelClient` / `SessionStore` / `ToolReviewer` / `TraceLogger` 端口、`AgentService` 状态机、70% LLM 滚动压缩与失败优雅降级下轮重试、`max_iterations` 兜底 |
| 实现 `agent/adapters/` 与 `agent/main.py` | [`docs/specs/03_adapters_and_wiring.md`](specs/03_adapters_and_wiring.md) | `OpenAIClient`（原生 Function Calling + DeepSeek 兼容）、`FakeModelClient`、`JsonlSessionStore`、`RuleBasedReviewer` + `LlmToolReviewer` + `CompositeReviewer`、`todo`/`weather`/`calculator`/`bash` 4 个工具、多会话 CLI、`main.py` |
| 编写测试与维护日志 | [`docs/specs/04_tdd_and_test_log.md`](specs/04_tdd_and_test_log.md) | 5 个 Phase 的完整验收用例清单（含 `RuleBasedReviewer` 异常与恶意绕过矩阵、70% 压缩与降级重试、架构依赖 AST 检查）与 `docs/TEST_LOG.md` 格式 |
| 核对原始题目要求 | [`docs/topic.md`](topic.md) | 原笔试题全部要求 |

---

## 2. 边界（Boundaries — 做到哪，明确不做什么）

### In Scope（必须完整实现）
- **框架无关的核心 Agent Loop**：接收输入 → 组装上下文（含 70% 压缩检查） → 调 LLM → 解析思考过程 / 原生 Function Calling / 最终回复 → 审核工具调用 → 执行工具并回写 `ToolResult` → 循环直至结束或达 `max_iterations`。
- **OpenAI 兼容接口**：一套 `OpenAIClient` 通过 `base_url` / `api_key` 兼容 DeepSeek、GPT、硅基流动等；真实演示时主模型接 DeepSeek，审核与压缩接 Flash / 轻量模型。
- **4 个工具 + 安全上下文隔离**：`todo`（必做，通过 `ToolContext.session_id` 严格按会话隔离）、`weather`（mock）、`calculator`（安全 AST 求值）、`bash`（受控超时执行）。
- **双模式工具审核**：`RuleBasedReviewer`（覆盖高危命令、绕过拼接、非法参数等边界，作为 TDD 重点和前置硬护栏）+ `LlmToolReviewer`（接 Flash 模型）+ `CompositeReviewer`。
- **70% 窗口触发 LLM 滚动压缩**：达到窗口 70% 时调用 `ContextCompactor` 生成摘要，保留完整原始历史只推进 `compacted_until` 指针；**压缩失败时捕获异常记入 trace 日志、回退到不压缩继续主对话、不推进指针，在下一次用户发消息时自动重试**。
- **多会话持久化 CLI**：基于 `JsonlSessionStore`，支持 `/new`、`/list`、`/use <id>`、`/history`、`/quit`，双窗口会话完全隔离。
- **结构化 Trace 日志与基础异常处理**：记录每轮模型调用、审核结论、工具执行与异常、压缩触发/失败事件。

### Out of Scope（明确不做，禁止过度设计）
- **严禁引入** LangChain、LangGraph、OpenHands、LlamaIndex 等 Agent 框架。
- **不做**单条超长 `ToolResult` 的字符硬截断。
- **不做**跨会话长期记忆存储与向量检索（仅在 `Context` 中保留 `memory: tuple[str, ...] = ()` 架构预留槽位）。
- **不做** Web UI / FastAPI 服务、不做 Anthropic / Google 原生 SDK 适配。

---

## 3. 硬约束（Constraints — 不可违背的铁律）

1. **依赖方向铁律（必须由 AST 单测自动守卫）**
   - `agent/domain/` 严禁 `import` 任何第三方库，严禁 `import` `agent.app` 或 `agent.adapters`。
   - `agent/app/` 只允许 `import` 标准库与 `agent.domain`，严禁 `import` `agent.adapters` 或 `openai`。
   - `agent/main.py` 是唯一允许组装所有具体实现类的组装根。
2. **`ToolContext` 安全隔离铁律**
   - `Tool.execute(arguments: dict[str, Any], ctx: ToolContext) -> str`；`session_id` 只能从系统注入的 `ctx.session_id` 读取，绝不允许 LLM 通过 `arguments` 伪造或跨会话读写 `todo`。
3. **协议翻译隔离铁律**
   - OpenAI 的 `messages` / `tools` / `tool_calls` / `reasoning_content` 格式转换全部封闭在 `OpenAIClient` 内，领域层与应用层不得出现任何厂商协议字段。
   - 历史 `AssistantMessage.thinking` 仅用于展示与日志，**不回传**给后续轮次的 LLM。
4. **工具调用原子对保护铁律**
   - 70% 压缩切分历史消息时，绝不能拆散 `AssistantMessage(tool_calls)` 与紧跟其后的 `ToolResult`，确保真实 API 不会报 `400 Bad Request`。
5. **异常闭环铁律**
   - 工具审核被拒、工具不存在、工具执行抛异常均不得导致主循环崩溃，必须封装为 `ToolResult(is_error=True)` 交还模型自我修正。
6. **无裸 `eval` / 防路径穿越铁律**
   - `CalculatorTool` 必须使用 AST 安全求值；`JsonlSessionStore` 与 `TodoTool` 必须校验 `session_id` 防止 `../` 路径穿越。

---

## 4. 分阶段 TDD 执行路线

请严格按以下 5 个 Phase 顺序推进；每个 Phase 完成后运行 `pytest` 并更新 [`docs/TEST_LOG.md`](TEST_LOG.md)，全绿后再进入下一个 Phase：

1. **Phase 1 — 领域层**：读 `docs/specs/01_domain.md` 与 `04_tdd_and_test_log.md` → 写 `tests/test_domain.py` → 实现 `agent/domain/` → 跑 `pytest` → 更新 `docs/TEST_LOG.md`。
2. **Phase 2 — 规则审核器与端口**：读 `docs/specs/02_app.md` 与 `03_adapters_and_wiring.md` → 写 `tests/test_rule_reviewer.py`（穷举意外与恶意输入）→ 实现 `agent/app/ports.py` 与 `agent/adapters/reviewers/` → 跑 `pytest` → 更新 `docs/TEST_LOG.md`。
3. **Phase 3 — 工具与 JSONL 存储**：读 `docs/specs/03_adapters_and_wiring.md` → 写 `tests/test_tools_and_store.py` → 实现 `agent/adapters/tools/` 与 `agent/adapters/stores/jsonl.py` → 跑 `pytest` → 更新 `docs/TEST_LOG.md`。
4. **Phase 4 — 应用层编排与 70% 压缩**：读 `docs/specs/02_app.md` 与 `04_tdd_and_test_log.md` → 写 `tests/test_agent_service.py`（使用 `FakeModelClient`）→ 实现 `agent/app/service.py`、`agent/adapters/models/fake_client.py`、`agent/adapters/logging/trace_logger.py` → 跑 `pytest` → 更新 `docs/TEST_LOG.md`。
5. **Phase 5 — OpenAI 适配、CLI、组装根与架构守卫**：读 `docs/specs/03_adapters_and_wiring.md` 与 `04_tdd_and_test_log.md` → 写 `tests/test_openai_and_architecture.py` → 实现 `agent/adapters/models/openai_client.py`、`agent/adapters/cli/app.py`、`agent/main.py`、`.env.example`、`pyproject.toml`（或 `requirements.txt`）→ 跑全量 `pytest` → 最终更新 `docs/TEST_LOG.md`。

完成后，在最终回复中汇报：
1. 全量 `pytest` 执行结果摘要；
2. 我们在 `docs/TEST_LOG.md` 中记录了哪些关键测试、是否出现过失败用例以及对应的修复方式；
3. 本地运行多会话 CLI 的启动命令与所需环境变量。

---

## 5. 执行待办清单（总进度，GPT 逐项勾选）

> 完成每阶段后将 `- [ ]` 改为 `- [x]`，并同步勾选对应 `docs/specs/*.md` 末尾的细分清单。

- [x] **Phase 1**：完成 `tests/test_domain.py`、`agent/domain/` 与 `docs/specs/01_domain.md` 清单勾选，更新 `docs/TEST_LOG.md`
- [x] **Phase 2**：完成 `tests/test_rule_reviewer.py`、`agent/app/ports.py`、`agent/adapters/reviewers/`，更新 `docs/TEST_LOG.md`
- [x] **Phase 3**：完成 `tests/test_tools_and_store.py`、`agent/adapters/tools/`、`agent/adapters/stores/jsonl.py`，更新 `docs/TEST_LOG.md`
- [x] **Phase 4**：完成 `tests/test_agent_service.py`、`agent/app/service.py`、`fake_client.py`、`trace_logger.py` 与 `docs/specs/02_app.md` 清单勾选，更新 `docs/TEST_LOG.md`
- [x] **Phase 5**：完成 `tests/test_openai_and_architecture.py`、`openai_client.py`、`cli/app.py`、`main.py`、`.env.example` 与依赖清单，勾选 `docs/specs/03_adapters_and_wiring.md` 与 `04_tdd_and_test_log.md`
- [x] **最终收尾**：全量离线 `pytest -v` 全部通过，完成 `docs/Agent_Architecture.md`、`docs/TEST_LOG.md` 与 `README.md` 末尾清单勾选
