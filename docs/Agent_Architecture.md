# Agent 架构设计（定稿版）

本项目采用 **三层架构 + 组装根（Ports & Adapters / 依赖倒置）**，不依赖任何 Agent 框架，目标是让核心循环**高内聚、易替换、全离线可测**。

---

## 1. 核心架构原则

1. **依赖向内收敛**：`main.py` → `adapters/` → `app/` → `domain/`；最内层 `domain/` 零第三方依赖，只有入边、没有出边。
2. **端口与适配器解耦**：`app/ports.py` 只定义业务需要的抽象能力（`ModelClient`、`SessionStore`、`ToolReviewer`、`TraceLogger`），具体外部协议翻译全部下沉到 `adapters/`。
3. **单一组装根**：`agent/main.py` 是整个系统唯一知道所有具体实现类的地方，切换模型、存储、审核策略或交互入口只需改动这里。

---

## 2. 项目目录结构

```text
agent/
├── domain/                       # 领域层（零第三方依赖）
│   ├── session.py                # Session(含 summary / compacted_until), UserMessage, AssistantMessage(含 thinking), ToolCall, ToolResult, Message
│   ├── tool.py                   # ToolContext(session_id), Tool
│   ├── context.py                # Context[O](预留 memory), Token 估算, 原子对安全切分
│   └── roles.py                  # Assistant, ToolCallReviewer, Decision, ContextCompactor, CompactionSummary
├── app/                          # 应用层（仅依赖 domain）
│   ├── ports.py                  # Effort, ModelSettings, ModelClient, SessionStore, ToolReviewer, TraceLogger
│   └── service.py                # AgentService（主循环、70% 压缩编排与降级重试、异常闭环）
├── adapters/                     # 适配层（外部协议与 I/O 翻译）
│   ├── models/
│   │   ├── openai_client.py      # OpenAI 兼容接口（原生 Function Calling + DeepSeek reasoning_content）
│   │   └── fake_client.py        # FakeModelClient（离线 TDD 专用）
│   ├── stores/
│   │   └── jsonl.py              # JsonlSessionStore（按 session_id 持久化）
│   ├── reviewers/
│   │   ├── rule_reviewer.py      # RuleBasedReviewer（硬护栏 + 边界 TDD）
│   │   ├── llm_reviewer.py       # LlmToolReviewer（接入真实 Flash 轻量模型）
│   │   └── composite_reviewer.py # CompositeReviewer（先规则后模型）
│   ├── tools/
│   │   ├── todo.py               # TodoTool（按 ToolContext.session_id 隔离）
│   │   ├── weather.py            # WeatherTool（Mock 天气）
│   │   ├── calculator.py         # CalculatorTool（安全 AST 算术求值）
│   │   └── bash.py               # BashTool（受控超时命令执行）
│   ├── logging/
│   │   └── trace_logger.py       # 结构化 Trace 日志
│   └── cli/
│       └── app.py                # 多会话 CLI（/new, /list, /use, /history, /quit）
└── main.py                       # 组装根（Composition Root）
```

---

## 3. 关键设计决策

| 设计主题 | 选定方案 | 核心理由 |
| :--- | :--- | :--- |
| **LLM 输出解析** | 原生 Function Calling（OpenAI 兼容） | 稳定可靠，协议翻译完全封闭在 `OpenAIClient` 内，兼容 DeepSeek / GPT / 硅基流动 |
| **工具审核** | `ToolReviewer` Port + 规则版 / Flash 模型版 / 组合版 | TDD 阶段纯规则覆盖边界与恶意绕过，真实运行先过规则硬护栏再由 Flash 模型做语义把关 |
| **多会话 `todo` 隔离** | `Tool.execute(arguments, ctx: ToolContext)` | 将「LLM 决定的参数」与「系统注入的 `session_id`」彻底隔离，防止模型幻觉或越权串话 |
| **上下文压缩** | 窗口达 70% 时触发 `ContextCompactor`（Flash 模型）滚动摘要 | 保留完整原始历史仅推进 `Session.compacted_until`；切分严格保护 `ToolCall` / `ToolResult` 原子对；不做单条超长 `ToolResult` 硬截断；压缩失败优雅降级记日志，不推进指针并在下次用户发消息时重试 |
| **跨会话长期记忆** | 架构预留（`Context.memory` 槽位） | 保持 MVP 轻量无向量库依赖，未来仅需在 `app/ports.py` 新增 `MemoryStore` 端口即可接入 |

---

## 4. 渐进式规格索引

需要查看某一层的完整字段、方法签名和边界规则时，请按需查阅：

- 领域层规格：[`docs/specs/01_domain.md`](specs/01_domain.md)
- 应用层规格：[`docs/specs/02_app.md`](specs/02_app.md)
- 适配层与组装根规格：[`docs/specs/03_adapters_and_wiring.md`](specs/03_adapters_and_wiring.md)
- TDD 测试矩阵与日志规范：[`docs/specs/04_tdd_and_test_log.md`](specs/04_tdd_and_test_log.md)
- GPT 一次性 TDD 系统提示词：[`docs/PROMPT_TDD.md`](PROMPT_TDD.md)

---

## 5. 架构落地验收清单（GPT 完成后勾选）

- [ ] 三层目录结构与模块划分已按第 2 节完整落地，且通过 AST 依赖方向检查
- [ ] 原生 Function Calling、`ToolReviewer` 三模式、`ToolContext` 会话隔离、70% 滚动压缩与降级重试均已实现
- [ ] `Context.memory` 预留槽位保留完整，未引入任何第三方 Agent 框架
- [ ] `agent/main.py` 保持唯一组装根职责
