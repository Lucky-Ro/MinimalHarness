# 从零实现一个最小可用 Agent（Minimal Agent Harness）

本项目从零实现一个**不依赖任何 Agent 框架**（无 LangChain / LangGraph / OpenHands）的最小可用 Agent Runtime，采用 **三层架构 + 组装根（Ports & Adapters / 依赖倒置）**，支持多会话隔离、原生 Function Calling、规则与轻量模型双层工具审核、以及 70% 上下文窗口触发的 LLM 滚动压缩。

---

## 1. 系统设计概览

```text
main.py (组装根)
  └── 组装并注入 ──▶ adapters/ (OpenAIClient · JsonlSessionStore · Reviewers · Tools · 多会话 CLI)
                        └── 实现接口（依赖倒置）──▶ app/ (Ports 端口 · AgentService 主循环编排)
                                                       └── 编排 ──▶ domain/ (11+ 纯领域对象，零第三方依赖)
```

- **领域层 `agent/domain/`**：定义系统自有语义（`Session`、`Message`、`Tool`、`ToolContext`、`Context[O]`、`Assistant`、`ToolCallReviewer`、`ContextCompactor`），零第三方依赖。
- **应用层 `agent/app/`**：通过 `ModelClient`、`SessionStore`、`ToolReviewer`、`TraceLogger` 端口隔离外部依赖，`AgentService` 负责主循环、最大轮次限制、异常闭环及 70% 上下文滚动压缩编排。
- **适配层 `agent/adapters/`**：提供 OpenAI 兼容接口、JSONL 会话存储、工具审核器和多会话 CLI。注册了 `todo`、`weather`、`calculator`、`bash` 四个核心工具和原题允许的模拟 `search`，通过 `FakeModelClient` 支持离线测试。
- **组装根 `agent/main.py`**：全系统唯一同时认识所有具体实现类的地方，切换模型、审核策略或入口（如 CLI 换 Web）只需改此文件。

> 完整设计细节与分层规格见 [`docs/Agent_Architecture.md`](docs/Agent_Architecture.md) 及 `docs/specs/`。

---

## 2. Memory 的召回时机与放置方式说明

在我们的架构中，记忆按生命周期划分为三层：
1. **工作记忆（Working Memory）**：当前会话尚未压缩的近期原始消息 `session.messages[compacted_until:]`。
2. **会话压缩记忆（Session Summary）**：当估算上下文达到模型窗口 **70%** 时，由 `ContextCompactor` 调用轻量模型（Flash）滚动生成的 `session.summary`。原始完整历史保留在 JSONL 中不删除；若压缩调用失败则捕获异常记入 trace 日志、回退到不压缩继续主对话，并在下一次用户发消息时自动重试。
3. **跨会话长期记忆（Long-Term Memory，架构预留）**：在 `Context` 中预留 `memory: tuple[str, ...] = ()` 槽位，未来可通过在 `app/ports.py` 新增 `MemoryStore` 端口接入，不破坏领域层纯度。

### 2.1 召回时机（When to Recall）

| 记忆类型 | 召回时机 | 工程权衡 |
| :--- | :--- | :--- |
| **会话压缩摘要（`session.summary`）** | 每次组装 `Assistant.context(session)` 时必然注入 | 作为被折叠历史的总入口，保证多轮长对话不丢失目标与工具结论 |
| **稳定用户偏好（长期记忆预留）** | 会话初始化（Session Start）加载一次 | 延迟只付一次，且前缀稳定，最大化 Prompt Cache 命中率 |
| **情景记忆 / 历史事件（长期记忆预留）** | 每轮用户输入前轻量 Top-K 预召回 + `search_memory` 工具按需兜底 | 兼顾当前话题精准度与低 token 消耗；由应用层通过 `MemoryStore` 端口查询并注入 |

### 2.2 放置方式（Where to Place in Context）

上下文按 **「静态在前、动态在后、指令与数据隔离」** 原则排布：

1. **System Prompt 顶部（静态前缀区）**：放置固定不变的 `instruction`（角色设定、工具使用规范、安全边界），最大化命中 DeepSeek / OpenAI 的自动前缀缓存（Prompt Cache），降低成本与首字延迟。
2. **历史消息最前端（半静态摘要区）**：紧跟静态指令之后、近期原始消息之前，用 XML 标签 `<conversation_summary>...</conversation_summary>` 包裹 `session.summary`。该区块仅在触发 70% 压缩时才变化，兼顾缓存稳定与上下文衔接。
3. **最新 `UserMessage` 附近（动态尾部区，预留）**：每轮动态召回的跨会话记忆放于尾部并以 `<recalled_memory>...</recalled_memory>` 包裹——既不破坏前面长历史的 Prompt Cache，又紧贴当前问题避免“中间迷失（Lost in the Middle）”效应。
4. **历史思考过程不回传**：`AssistantMessage.thinking` 仅用于终端展示与 trace 日志，不塞回后续轮次上下文，减少 token 浪费与推理干扰。
5. **指令与数据边界隔离**：所有注入的摘要与记忆统一用明确标签包裹，并在系统指令中声明其仅作参考事实而非新指令，降低间接提示词注入（Indirect Prompt Injection）风险。

---

## 3. 目录导航

```text
.
├── README.md                          # 项目说明、系统设计与 Memory 策略
├── docs/
│   ├── topic.md                       # 原始笔试题目要求
│   ├── Agent_Architecture.md          # 定稿版三层架构总览
│   ├── PROMPT_TDD.md                  # 渐进式披露主提示词（地图 · 边界 · 约束）
│   ├── TEST_LOG.md                    # TDD 测试执行与问题解决记录
│   └── specs/                         # 渐进式披露分层详细规格
│       ├── 01_domain.md               # 领域层规格
│       ├── 02_app.md                  # 应用层与 70% 压缩编排规格
│       ├── 03_adapters_and_wiring.md  # 适配层、工具、审核器与组装根规格
│       └── 04_tdd_and_test_log.md     # TDD 测试矩阵与日志规范
├── references/                        # 学习笔记参考教程与图解资料
├── agent/                             # Agent Runtime 源码
└── tests/                             # 全离线 pytest 测试套件
```

---

## 4. 运行与测试方式

### 安装与配置

在项目根目录使用 macOS 或 Linux 和 Python 3.10+。会话文件锁依赖 POSIX，计算器和模拟天气均在本地执行。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

在当前目录的 `.env` 中按 [.env.example](.env.example) 填入 `OPENAI_API_KEY`。已导出的环境变量优先于文件配置。

| 变量 | 用途与默认值 |
| --- | --- |
| `OPENAI_API_KEY` | 真实对话所需的接口密钥 |
| `OPENAI_BASE_URL` | 兼容服务地址，默认 `https://api.deepseek.com` |
| `ASSISTANT_MODEL` | 主对话模型，默认 `deepseek-v4-pro` |
| `FLASH_MODEL` | 工具审核与滚动摘要的轻量模型，默认 `deepseek-flash` |
| `CONTEXT_WINDOW` | 估算窗口大小，默认 64000，达 70% 时尝试压缩 |
| `REVIEWER_MODE` | `rule`、`llm`、`composite`，默认 `composite` |
| `AGENT_DATA_DIR` | 会话与待办存储目录，默认 `./data`；从不同目录启动的窗口可设为相同的绝对路径 |

DeepSeek 主对话启用工具时会显式关闭 thinking 模式，以遵守项目对历史思考内容的隔离要求。模型和协议行为见 [DeepSeek 模型列表](https://api-docs.deepseek.com/quick_start/pricing/) 和 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。

### 运行离线测试

```bash
pytest -v
```

测试用脚本模型和本地模拟传输验证多轮请求，测试进程内拦截外部网络连接。无需 API 密钥。

### 启动多会话 CLI

```bash
python -m agent.main
```

每个新窗口获得独立会话。可以在一个窗口输入“查上海天气，帮我把带伞记入待办”，在另一个窗口输入“把写周报记入待办”，再分别追问“我刚才记了什么？”。重启后用 `/list` 找到原会话，再用 `/use <id>` 继续。

完整对话和待办分别保存在 `data/sessions/` 与 `data/todos/`。JSON 格式的 trace 写到标准错误。受限 bash 工具支持 `pwd`、`ls` 和输出字面文本的 `echo`，超时为 5 秒。

可以输入“搜索如何写周报”试用 `search`。搜索查询内置样例资料，覆盖周报、会议纪要和会话管理。

CLI 内置命令：`/new`（新建会话）、`/list`（列出会话）、`/use <session_id>`（切换会话）、`/history`（查看历史与摘要状态）、`/quit`（退出）。

---

## 5. 提交资料核对

- 代码仓库与源码：`agent/` + `tests/`
- 系统设计与 Memory 策略：本 [`README.md`](README.md) 与 [`docs/Agent_Architecture.md`](docs/Agent_Architecture.md)
- AI Prompt 与问题解决记录：[`docs/PROMPT_TDD.md`](docs/PROMPT_TDD.md) + `docs/specs/*.md`（含末尾逐项勾选记录）+ [`docs/TEST_LOG.md`](docs/TEST_LOG.md)

---

## 6. 交付核对清单（GPT 完成后勾选）

- [x] `agent/` 与 `tests/` 已按架构实现，全量 `pytest -v` 离线通过
- [x] `.env.example` 与依赖配置已就绪，多会话 CLI（`python -m agent.main`）可正常启动
- [x] `docs/PROMPT_TDD.md`、`docs/specs/*.md`、`docs/Agent_Architecture.md` 与 `docs/TEST_LOG.md` 末尾待办清单均已勾选且原有规格正文未被篡改
