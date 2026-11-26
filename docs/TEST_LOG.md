# TDD 测试与问题解决日志（Test Log）

> 本文件用于在 TDD 开发过程中如实记录每一轮测试覆盖的场景、通过/失败统计、失败根因与修复方案，同时作为项目提交时的「问题解决记录」。

## 执行总览

- 测试框架：`pytest`（全量离线执行，不依赖外部网络）
- 详细用例规范：[`docs/specs/04_tdd_and_test_log.md`](specs/04_tdd_and_test_log.md)

## 分阶段测试记录

| 轮次 / Phase | 测试文件与核心场景 | 总数 / 通过 / 失败 | 失败用例与根因分析 | 修复方案与复测结果 |
| :--- | :--- | :--- | :--- | :--- |
| Phase 1 | `tests/test_domain.py` — 领域对象、Token 估算、原子对切分 | 8 / 8 / 0 | 首次运行缺少领域包（见 Red 行） | 完整实现后 8 项通过 |
| Phase 2 | `tests/test_rule_reviewer.py` — 规则审核器正常/意外/绕过边界、组合短路 | 66 / 66 / 0 | 首次 66 项失败，详见 2 Red | 规则、短路与模型审核测试通过，全量 74/74 |
| Phase 3 | `tests/test_tools_and_store.py` — 4 个工具、`todo` 会话隔离、JSONL 往返 | 41 / 41 / 0 | 三组初次运行缺少对应适配器 | 当阶段全量 115/115；后补大小写别名回归 3 项，本模块最终 44/44 |
| Phase 4 | `tests/test_agent_service.py` — 主循环、多会话隔离、70% 压缩与失败降级重试 | 16 / 16 / 0 | 首次运行缺少服务、脚本模型或日志适配器 | 14 项服务 + 2 项适配器，通过全量 131/131 |
| Phase 5 | `tests/test_openai_and_architecture.py` — OpenAI 翻译契约、AST 依赖方向守卫 | 47 / 47 / 0 | 见协议收集错误、各模块 Red 与方括号摘要回归 | 34 项协议与架构、6 项 CLI、7 项入口测试全部通过 |
| 环境准备 | 系统 Python 3.9、Anaconda Python 3.12 均缺 pytest | 未启动 / 0 / 0 | 解释器报告 No module named pytest | 建立 .venv 并安装项目测试依赖，用 Python 3.12 复测 |
| 1 Red | test_domain.py 全部场景 | 8 / 0 / 8 个 setup 错误 | 项目初始没有 agent 包 | 按领域规格实现消息、上下文与三个角色 |
| 1 Refactor | test_domain.py 改用直接导入，保留所有断言 | 8 / 8 / 0 | 无 | 领域测试全部通过 |
| 2 Red | test_rule_reviewer.py（R-01～08、审核模型异常与参数） | 66 / 0 / 66 | 审核器与端口尚未实现 | 实现规则、LLM、组合审核器与端口 |
| 3 Red（待办） | test_todo.py（后合入 test_tools_and_store.py） | 7 / 0 / 7 | 尚无 TodoTool | 按可信会话 ID 持锁读写待办 |
| 3 Red（三个工具） | test_tools_and_store.py：计算、天气、命令执行 | 20 / 0 / 20 | 三个工具尚未实现 | 实现安全 AST 求值、确定性天气、限时命令执行 |
| 3 Green（三个工具） | test_tools_and_store.py | 20 / 20 / 0 | 无 | 命令实际执行与超时、幂运算边界、模拟天气均通过 |
| 3 Red（存储） | test_store_jsonl.py（后合入 test_tools_and_store.py） | 14 / 0 / 14 | 尚无 JSONL 存储适配器 | 加入原子写入、校验与独立会话锁 |
| 3 Green（存储） | test_store_jsonl.py | 14 / 14 / 0 | 无 | 旧格式、非法 ID、原子替换失败、并发锁及独立会话均通过 |
| 3 Green（待办） | test_todo.py | 7 / 7 / 0 | 无 | 两个窗口、伪造 session_id、非法写入与并发添加均通过 |
| 4 Red（模型与日志） | test_runtime_adapters.py | 2 / 0 / 2 | FakeModelClient、日志适配器尚未实现 | 实现脚本模型与结构化日志 |
| 4 Green（模型与日志） | test_runtime_adapters.py，返回类型与异常、可解析单行 JSON | 2 / 2 / 0 | 无 | 脚本模型与日志验证通过 |
| 4 Red（主循环） | test_agent_service.py | 6 / 0 / 6 | 尚无 AgentService | 实现多轮调用、工具异常闭环与最大轮数 |
| 4 Red（压缩与恢复） | test_compaction.py | 6 / 0 / 6 | 尚无 AgentService | 实现滚动摘要、失败降级和下轮重试 |
| 4 Red（异常恢复） | test_compaction.py -k duplicate 或 reviewer_exception | 2 / 0 / 2（另 6 项未选） | 尚无服务实现 | 加入重复会话、模型失败和审核异常恢复处理 |
| 4 Green（服务） | test_agent_service.py + test_compaction.py | 14 / 14 / 0 | 无 | 会话恢复、原子对、阈值、失败后下轮重试及工具异常均通过 |
| 回归 Red（会话 ID） | test_tools_and_store.py：大小写别名、首次写入竞争 | 3 / 0 / 3（41 项未选） | 本机卷大小写不敏感，Team/team 可解析到同一文件 | 保留合法大写 ID，锁内校验磁盘实际拼写 |
| 回归 Green（会话 ID） | test_tools_and_store.py | 44 / 44 / 0 | 已修复大小写别名及首次写入竞争 | 大小写不敏感卷拒绝别名，原始会话内容保留 |
| 5 Red（入口与架构） | test_main.py + test_architecture.py | 10 / 3 / 7 | 3 项 AST 检查通过，缺少 agent.main 致 7 项失败 | 实现 .env 读取、组装与 CLI 入口 |
| 5 Red（CLI） | test_cli.py | 6 / 0 / 6 | CLI 适配器尚未实现 | 实现会话命令、增量展示和错误恢复 |
| 5 Green（CLI） | test_cli.py | 6 / 6 / 0 | 无 | 切换、恢复、历史、工具审查结果、错误后继续与退出全部通过 |
| 5 收集错误（协议） | test_openai_and_architecture.py | 0 项执行 / 0 / 1 个收集错误 | 本机 openai 3.59 使用 httpx2，测试直接导入了 httpx | 按实际 SDK 选择对应的离线传输后复测 |
| 5 Red（协议） | test_openai_and_architecture.py | 28 / 0 / 28 | 尚无 OpenAIClient | 实现厂商消息翻译、结构化输出解析和安全错误信息 |
| 5 Green（协议） | test_openai_and_architecture.py | 28 / 28 / 0 | 无 | 官方 SDK + MockTransport 验证请求体、模型响应、401、JSON 类型与摘要边界 |
| 5 Green（入口） | test_main.py | 7 / 7 / 0 | 无 | 三种审核模式、实际模块入口和配置报错均通过 |
| 入口烟测 | 两个独立 python -m agent.main 进程，新建后 /use 恢复旧会话 | 1 / 1 / 0（非 pytest） | 无 | 已验证安装后的模块入口、跨进程列表和历史 |
| 文档核对 | 与 Git 基线逐字校对初始 Prompt、题目、架构与四份规格 | 首次校验误报，复核通过（非 pytest） | 校对脚本误把正文内的勾选示例也归一化 | 仅忽略行首清单标记后正文完全相同 |
| 5 回归 Red（摘要） | test_openai_and_architecture.py -k bracketed | 2 / 0 / 2（32 项未选） | 方括号开头的标题或 Markdown 链接被当作 JSON 解析 | 区分普通文本与结构化 JSON 数组 |
| 5 回归 Green（摘要及架构） | test_openai_and_architecture.py（已合入 AST 守卫） | 34 / 34 / 0 | 已修复 | 方括号文本正确往返，合法 JSON 数组继续拒绝 |
| 最终验收 | .venv/bin/pytest -v；Python 3.12.2，pytest 9.2.0 | 181 / 181 / 0 | 无失败、无跳过 | 1.75s；模型使用脚本或 SDK 内存传输，未消耗真实 API Token |
| 复审基线 | .venv/bin/pytest -q | 181 / 181 / 0 | 无 | 原有测试全部通过 |
| 复审 Red（压缩） | test_agent_service.py、test_openai_and_architecture.py 定向测试 | 4 / 1 / 3（51 项未选） | 保留数为 0 时当前问题或工具批次被全部移入摘要；压缩请求将工具结果映射为原生消息时丢失失败状态 | 主对话至少保留最新消息及其完整工具批次，摘要请求使用保留状态的历史数据 |
| 复审 Green（压缩） | 同一组定向测试 | 4 / 4 / 0（47 项未选） | 已修复 | 当前问题和工具批次分别通过回归，摘要请求保留工具成功和失败状态 |
| 复审 Red（搜索） | test_main.py、test_tools_and_store.py 定向测试 | 7 / 0 / 7（48 项未选） | 原题列出的 search 没有实现和注册 | 增加固定资料库模拟搜索，沿用工具注册和审核；校对同批两个调用的模型请求次数 |
| 复审 Green（搜索） | 同一组定向测试 | 7 / 7 / 0（48 项未选） | 已补齐 | 验证模拟命中、无结果、非法输入和三种审核模式的完整调用链 |
| 复审全量 | .venv/bin/pytest -v | 188 / 188 / 0 | 无失败、无跳过 | 1.82s；保留原题及规格正文，线上模型调用仍用离线传输模拟 |

## 规则审核器（`RuleBasedReviewer`）边界覆盖追踪

| 编号 | 意外 / 恶意场景 | 预期结果 | 实际结果 | 备注 / 修复记录 |
| :--- | :--- | :--- | :--- | :--- |
| R-01 | 未知工具名 / 空工具名 | 拒绝（`should_allow=False`） | 未知、空字符串、纯空白及空值均拒绝 | Phase 2 参数化用例 |
| R-02 | 非 `dict` 参数 / 缺失必填字段 | 拒绝 | 非对象、缺字段、类型错误及空白字符串均拒绝 | Phase 2 参数化用例 |
| R-03 | `bash` 破坏性命令（`rm -rf` / `mkfs` / `dd` / `sudo`） | 拒绝 | 高危命令均在创建子进程前拒绝 | Phase 2 参数化用例 |
| R-04 | `bash` 拼接绕过与管道执行（`&&` / `;` / `curl \| sh` / `$(...)`） | 拒绝 | 原版拒绝所有串接；真实 Bash 版本区分合法组合与已知高危调用，见下方变更记录 | Phase 2 参数化用例 |
| R-05 | `bash` 敏感文件与路径穿越（`/etc/passwd` / `~/.ssh` / `.env`） | 拒绝 | 读取敏感文件和向上遍历均拒绝 | Phase 2 参数化用例 |
| R-06 | `calculator` 代码注入尝试（`__import__` / `eval` / `open`） | 拒绝 | 函数调用、导入、空表达式及超长表达式均拒绝 | Phase 2 参数化用例 |
| R-07 | `todo` 非法 `action` 或缺失 `title`/`todo_id` | 拒绝 | 非法动作及对应动作缺参均拒绝 | Phase 2 参数化用例 |
| R-08 | `CompositeReviewer` 规则层拒审时短路不调 LLM | 拒绝且 LLM 调用次数 = 0 | 规则拒绝后模型调用 0 次，合法 pwd 调用模型 1 次 | Phase 2 参数化用例 |

## 收尾核对清单（GPT 完成后勾选）

- [x] Phase 1 ~ Phase 5 测试统计已全部如实填入上表（非“待执行”）
- [x] R-01 ~ R-08 边界场景已全部更新为真实执行结果
- [x] 过程中出现的所有失败用例、根因与修复方案均已如实记录，未篡改测试敷衍通过
- [x] 全量离线 `pytest -v` 最终通过，且各设计文档末尾待办清单已全部勾选

## 真实 Bash 与轻量模型审核

| 阶段 | 测试与结果 | 发现与处理 |
| --- | --- | --- |
| 规则 Red / Green | 首轮 9 失败、64 通过；实现后 73 通过。补测字面词时 1 失败、8 通过、65 未选，修复后 74 通过。 | 原有三命令白名单阻止管道进入模型审核。改为高危模式检查，修正将普通输出中的 reboot 误判为命令的问题。 |
| 审核链 Red | 三个分支均失败，另 7 项未选。 | 正常脚本在调用 Flash 前即遭旧规则拒绝。 |
| 进程监督 Red / Green | 模块未实现时 6 失败，实现后 6 通过。 | 验证实际退出码、两个输出流、输出上限、超时与同组后台进程清理。 |
| 原生执行 Red | 5 失败、45 未选；平台启动检查 2 失败、50 未选。 | 旧执行器依赖已经替换的白名单函数，尚不能执行真实 Bash 脚本。 |
| 原生执行 Green | Codex 外层沙箱中 4 失败、3 通过、45 未选；在正常 macOS 环境复测 7 通过、45 未选。 | 系统拒绝嵌套开启 Seatbelt。独立探测还确认当前系统需要 dyld 引导配置。正常环境验证了脚本输出、会话文件持久化与隔离、语法错误、退出码和启动器不可用时的处理。 |
| 审核链 Green | Codex 外层沙箱内运行入口模块为 1 失败、9 通过，正常 macOS 环境为 10 通过。 | 实际组装的服务将同一条脚本交给离线审核模型，再由原生 Bash 执行。批准时 tee 写入并返回 DEEP SKY，拒绝与审核超时均未产生文件。 |
| 本次全量验收 | `.venv/bin/pytest -v`，macOS，209 通过，0 失败，0 跳过，2.93 秒。 | 未调用线上模型。Linux 验证了 bubblewrap 启动参数；本机实际执行的是 macOS 后端。 |

## CLI 统一 Effort 选择与适配层协议翻译

| 阶段 | 测试与结果 | 发现与处理 |
| --- | --- | --- |
| Effort 改造 Red | 先补齐 `test_domain.py`、`test_agent_service.py`、`test_openai_and_architecture.py`、`test_cli.py`、`test_main.py` 后执行 `.venv/bin/pytest -q`，在收集阶段报错（`AttributeError: type object 'Effort' has no attribute 'XHIGH'`）。 | 旧 `Effort` 枚举为 `NONE/MINIMAL/LOW/MEDIUM/HIGH/MAX`，尚未对齐 6 档 `off/low/medium/high/xhigh/max` 及各层新契约。 |
| Effort 改造 Green | 实现 `roles.py`、`ports.py`、`service.py`、`openai_client.py`、`cli/app.py`、`main.py` 后执行 `.venv/bin/pytest -v`：234 通过，0 失败，0 跳过，3.33 秒。 | 覆盖 `Assistant.context` 保留未压缩历史 `thinking`、`AgentService.set_effort` 运行期全局切换且不影响压缩/审核模型、6 档 `Effort` 跨端点安全映射、带 `tools` 且非 `off` 时原样回传 `reasoning_content`、CLI `/effort` 命令、入口 `ASSISTANT_EFFORT` 校验以及应用层/CLI 零厂商字段泄漏与 AST 依赖方向守卫。 |
