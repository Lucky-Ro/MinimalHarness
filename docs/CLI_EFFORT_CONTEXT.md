# 对话上下文归档：CLI 统一 Effort 选择与适配层协议翻译

> **归档时间**：2026-11-26  
> **关联规划文档**：[`docs/TODO_CLI_EFFORT.md`](TODO_CLI_EFFORT.md)  
> **参考资料**：本地课程笔记（`references/tutor.md`、`IMG_5238.PNG`、`IMG_5240.PNG`）、[`docs/specs/01_domain.md`](specs/01_domain.md)、[`docs/specs/02_app.md`](specs/02_app.md)、[`docs/specs/03_adapters_and_wiring.md`](specs/03_adapters_and_wiring.md)

---

# 已严格按照 TODO_CLI_EFFORT.md 完成分层改造、测试覆盖与文档同步：

## 1. 各层核心改动
领域层（roles.py）：Assistant.context(session) 保留活跃历史（session.active_messages()）中的 AssistantMessage.thinking，压缩推进 compacted_until 后随旧消息自然淘汰；ToolCallReviewer 与 ContextCompactor 保持剥离历史 thinking。
应用层（ports.py、service.py）：
将 Effort 统一对齐为 6 档：off / low / medium / high / xhigh / max（默认 Effort.MEDIUM），并提供大小写不敏感解析方法 Effort.parse(value)。
在 AgentService 中新增运行期全局切换接口 get_effort() / set_effort(effort)（通过 dataclasses.replace 更新 assistant_model_settings，不影响 compactor_model_settings 和审核模型），记录 effort_changed 事件，并在 model_called 日志中附加当前 effort。
适配层 — 模型客户端（openai_client.py）：
_model_options 适配 6 档 Effort：api.deepseek.com 在 Effort.OFF 时下发 {"extra_body": {"thinking": {"type": "disabled"}}}，其余 5 档不再因携带 tools 强制关闭 thinking，按支持模型映射并透传 reasoning_effort；api.openai.com（o3 / o3-mini / o4-mini）仅透传受支持的 low / medium / high；其他自定义/不兼容端点安全省略厂商参数。
_wire_message 在且仅在「当前请求带 tools 且非 Effort.OFF」时，将活跃历史中非 None 的 AssistantMessage.thinking 原样回传为 "reasoning_content"。
适配层 — CLI 与组装根（app.py、main.py、.env.example、README.md）：
CLI 启动时展示当前思考档位与 /effort 命令说明；支持 /effort 查看当前档位及可选值、/effort <off|low|medium|high|xhigh|max> 全局切换及非法参数用法提示，不暴露任何厂商字段名。
main.py 新增读取与校验环境变量 ASSISTANT_EFFORT（默认 medium），并同步更新 .env.example 与 README.md。

---

## 2. 测试与文档核对
已补齐并更新 test_domain.py、test_agent_service.py、test_openai_and_architecture.py、test_cli.py 与 test_main.py（含 AST 依赖方向守卫及应用层/CLI 零厂商参数名检查）。
全量离线测试 .venv/bin/pytest -v 结果：234 passed, 0 failed, 0 skipped。
已勾选 TODO_CLI_EFFORT.md、01_domain.md、02_app.md、03_adapters_and_wiring.md 全部待办项，并将 TDD 执行记录写入 TEST_LOG.md。

---

## 3. 使用速查与后续可扩展方向

### 3.1 启动与交互示例
```bash
# 1. 可选：在 .env 或环境变量设置初始档位（默认 medium）
export ASSISTANT_EFFORT=high

# 2. 启动多会话 CLI
.venv/bin/python -m agent.main
```

终端内交互：
```text
当前会话 8f3c...（思考档位 high）
命令  /new  /list  /use <会话 ID>  /history  /effort [档位]  /quit
[8f3c...] > /effort
当前思考档位 high（可选：off / low / medium / high / xhigh / max）
[8f3c...] > /effort max
已切换思考档位为 max
```

### 3.2 后续演进切入点（如需扩展）
1. **接入更多模型厂商适配器**（对齐 `IMG_5239.PNG` / `IMG_5240.PNG`）：应用层 `Effort` 与 `ModelClient` 端口无需修改，只需在 `agent/adapters/models/` 下新增 `AnthropicClient`（将 `Effort` 翻译为 `output_config.effort` / thinking tokens）或 `GoogleClient`（翻译为 `thinking_level`），并在 `agent/main.py` 组装切换。
2. **按会话粒度记忆 Effort（可选扩展）**：当前按已拍板决策采用运行期全局切换且不改 JSONL schema；若未来需要每个会话独立保存思考档位，只需在 `Session` 与 `JsonlSessionStore` meta 行增加可选字段 `effort: str | None = None`（向后兼容旧会话文件），由 `AgentService` 优先读取会话级覆盖值即可。
