# TDD 测试与问题解决日志（Test Log）

> 本文件用于在 TDD 开发过程中如实记录每一轮测试覆盖的场景、通过/失败统计、失败根因与修复方案，同时作为项目提交时的「问题解决记录」。

## 执行总览

- 测试框架：`pytest`（全量离线执行，不依赖外部网络）
- 详细用例规范：[`docs/specs/04_tdd_and_test_log.md`](specs/04_tdd_and_test_log.md)

## 分阶段测试记录

| 轮次 / Phase | 测试文件与核心场景 | 总数 / 通过 / 失败 | 失败用例与根因分析 | 修复方案与复测结果 |
| :--- | :--- | :--- | :--- | :--- |
| Phase 1 | `tests/test_domain.py` — 领域对象、Token 估算、原子对切分 | 待执行 | - | - |
| Phase 2 | `tests/test_rule_reviewer.py` — 规则审核器正常/意外/绕过边界、组合短路 | 待执行 | - | - |
| Phase 3 | `tests/test_tools_and_store.py` — 4 个工具、`todo` 会话隔离、JSONL 往返 | 待执行 | - | - |
| Phase 4 | `tests/test_agent_service.py` — 主循环、多会话隔离、70% 压缩与失败降级重试 | 待执行 | - | - |
| Phase 5 | `tests/test_openai_and_architecture.py` — OpenAI 翻译契约、AST 依赖方向守卫 | 待执行 | - | - |

## 规则审核器（`RuleBasedReviewer`）边界覆盖追踪

| 编号 | 意外 / 恶意场景 | 预期结果 | 实际结果 | 备注 / 修复记录 |
| :--- | :--- | :--- | :--- | :--- |
| R-01 | 未知工具名 / 空工具名 | 拒绝（`should_allow=False`） | 待执行 | - |
| R-02 | 非 `dict` 参数 / 缺失必填字段 | 拒绝 | 待执行 | - |
| R-03 | `bash` 破坏性命令（`rm -rf` / `mkfs` / `dd` / `sudo`） | 拒绝 | 待执行 | - |
| R-04 | `bash` 拼接绕过与管道执行（`&&` / `;` / `curl \| sh` / `$(...)`） | 拒绝 | 待执行 | - |
| R-05 | `bash` 敏感文件与路径穿越（`/etc/passwd` / `~/.ssh` / `.env`） | 拒绝 | 待执行 | - |
| R-06 | `calculator` 代码注入尝试（`__import__` / `eval` / `open`） | 拒绝 | 待执行 | - |
| R-07 | `todo` 非法 `action` 或缺失 `title`/`todo_id` | 拒绝 | 待执行 | - |
| R-08 | `CompositeReviewer` 规则层拒审时短路不调 LLM | 拒绝且 LLM 调用次数 = 0 | 待执行 | - |

## 收尾核对清单（GPT 完成后勾选）

- [ ] Phase 1 ~ Phase 5 测试统计已全部如实填入上表（非“待执行”）
- [ ] R-01 ~ R-08 边界场景已全部更新为真实执行结果
- [ ] 过程中出现的所有失败用例、根因与修复方案均已如实记录，未篡改测试敷衍通过
- [ ] 全量离线 `pytest -v` 最终通过，且各设计文档末尾待办清单已全部勾选
