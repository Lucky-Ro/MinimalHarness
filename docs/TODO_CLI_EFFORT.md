# 待办规划：终端（CLI）支持选择统一 Effort 并由适配层翻译

## 1. 背景与已拍板设计决策

- **架构对齐**：参考笔记（`references/tutor_pic/IMG_5238.PNG`、`IMG_5240.PNG`、`references/tutor.md`），应用层统一定义 6 档 `Effort`，CLI 仅交互应用层语义，适配层负责翻译为各厂商协议参数。
- **已确认的 3 项核心决策**：
  1. **Effort 枚举命名（对齐笔记）**：统一改为 6 档 `off / low / medium / high / xhigh / max`（`Effort.OFF` / `LOW` / `MEDIUM` / `HIGH` / `XHIGH` / `MAX`，默认 `MEDIUM`）。
  2. **生效范围（运行期全局切换）**：通过 `AgentService.get_effort()` / `set_effort(effort)` 动态替换 `assistant_model_settings`（使用 `dataclasses.replace`），对后续所有会话的主对话生效；不改 `Session` 领域模型与 JSONL 格式，也不影响 `compactor_model_settings` 和审核模型。
  3. **OpenAIClient 历史 `reasoning_content` 回传策略**：
     - 消息历史（`Session.messages` 与 `JsonlSessionStore`）已保存每条 `AssistantMessage.thinking`。
     - `Assistant.context(session)` 不再提前剥离历史 `thinking`，将其保留到 `Context[AssistantMessage]` 中交给适配层判断（`ToolCallReviewer` 与 `ContextCompactor` 仍剥离）。
     - 在 `OpenAIClient` 中，只要**当前请求带了 `tools` 且处于 thinking 模式**（非 `Effort.OFF`），就把历史 `AssistantMessage` 里的 `thinking` 原样回传为 `"reasoning_content"`；仅在会话压缩/截断历史推进 `compacted_until` 时随旧消息自然丢弃。

---

## 2. 目标数据流

```text
终端输入 /effort <off|low|medium|high|xhigh|max>
  ──▶ CliApp（适配层：解析为应用层 Effort 枚举，不感知厂商字段）
  ──▶ AgentService.set_effort（应用层：全局替换 assistant_model_settings，记录 effort_changed）
  ──▶ Assistant.context（领域层：保留未压缩历史中的 AssistantMessage.thinking）
  ──▶ OpenAIClient（适配层：翻译 Effort → reasoning_effort / thinking；带 tools 且 thinking 模式时原样回传历史 reasoning_content）
```

---

## 3. 分层改造待办清单（逐项勾选）

### 3.1 领域层（`agent/domain/`）
- [x] `agent/domain/roles.py`：`Assistant.context(session)` 改为保留 `session.active_messages()` 中的 `AssistantMessage.thinking`（不再调用 `_visible_messages` 置空）；`ToolCallReviewer` 与 `ContextCompactor` 保持剥离历史 `thinking`。

### 3.2 应用层（`agent/app/`）
- [x] `agent/app/ports.py`：将 `Effort` 枚举改为 `OFF = "off"`, `LOW = "low"`, `MEDIUM = "medium"`, `HIGH = "high"`, `XHIGH = "xhigh"`, `MAX = "max"`，并补充大小写不敏感解析方法 `Effort.parse(value: str)`。
- [x] `agent/app/service.py`：
  - 新增 `get_effort() -> Effort` 与 `set_effort(effort: Effort) -> ModelSettings`（基于 `dataclasses.replace` 更新 `assistant_model_settings`）。
  - 切换时记录 `effort_changed` trace，并在 `model_called` trace 中补充 `effort=self.assistant_model_settings.effort.value`。

### 3.3 适配层 — 模型客户端（`agent/adapters/models/openai_client.py`）
- [x] 重写 `_model_options(settings, has_tools)` 适配 6 档 `Effort`：
  - `api.deepseek.com`：`Effort.OFF` → `{"extra_body": {"thinking": {"type": "disabled"}}}`；其余 5 档不再因 `has_tools` 强制关闭 thinking，按支持模型映射并透传 `reasoning_effort`。
  - `api.openai.com`（`o3` / `o3-mini` / `o4-mini`）：安全映射支持的 `reasoning_effort` 档位，不支持的档位不引发 400。
  - 其他自定义/兼容端点：安全省略厂商专属参数。
- [x] 改造 `_wire_message(message, *, include_reasoning: bool)`：当且仅当当前请求带 `tools` 且处于 thinking 模式时，若历史 `AssistantMessage.thinking is not None`，原样附加 `"reasoning_content": message.thinking`；否则省略。

### 3.4 适配层 — 终端机 CLI（`agent/adapters/cli/app.py`）
- [x] 在 `run()` 启动输出中补充 `/effort` 命令说明，并展示当前默认档位。
- [x] 在 `_command()` 中新增 `/effort` 分支：
  - `/effort`：打印当前档位与可选值 `off / low / medium / high / xhigh / max`。
  - `/effort <档位>`：解析并调用 `self.service.set_effort(...)`，打印切换结果；非法参数给出用法提示。

### 3.5 组装根与配置（`agent/main.py`、`.env.example`、`README.md`）
- [x] `agent/main.py`：读取 `ASSISTANT_EFFORT`（默认 `medium`），校验合法性后注入主助手的 `ModelSettings`。
- [x] `.env.example` 与 `README.md`：补充 `ASSISTANT_EFFORT` 与 `/effort` 命令说明，同步更新 `README.md` 中关于 DeepSeek thinking + tools 的描述。

### 3.6 自动化测试（`tests/`）
- [x] `tests/test_domain.py`：验证 `Assistant.context()` 保留未压缩消息的 `thinking`，压缩推进 `compacted_until` 后旧消息及其 `thinking` 随之退出活跃上下文；`ToolCallReviewer` / `ContextCompactor` 不携带 `thinking`。
- [x] `tests/test_agent_service.py`：验证 `set_effort()` 运行期全局生效且不影响 `compactor_model_settings`。
- [x] `tests/test_openai_and_architecture.py`：
  - 覆盖 6 档 `Effort` 翻译矩阵。
  - 验证「带 tools + thinking 模式」时历史 `AssistantMessage.thinking` 原样回传为 `reasoning_content`；「无 tools」或「`Effort.OFF`」时不回传 `reasoning_content`。
  - 确认 AST 依赖方向守卫全绿。
- [x] `tests/test_cli.py`：验证 `/effort` 查看、`/effort xhigh` 切换成功、`/effort bad` 友好报错且不中断会话。

---

## 4. 验收标准

- [x] 终端内可通过 `/effort` 查看和运行期全局切换 6 档 `off / low / medium / high / xhigh / max`，CLI 与应用层不出现任何厂商专属参数名。
- [x] DeepSeek 带工具且非 `off` 时，适配层正确透传思考参数并原样回传活跃历史中的 `reasoning_content`；压缩后旧消息的 `reasoning_content` 随旧消息自然丢弃。
- [x] `pytest -v` 全量离线测试通过。
