# 2026-11-04 改进计划验证记录

本记录只描述本次实际执行和当前交付边界。固定数据快照为 `bc507973f100da89`，运行语义目录为 `demo-1`，其中指标与关系均为 `candidate` 演示口径。原始审计报告是改进前的基线，不能代表当前代码状态。

## 变更与审计问题

| 审计编号 | 当前改进和证据 | 状态 |
| --- | --- | --- |
| P1-1、P1-2 | 数据库画像、带字段说明与脱敏样例的离线模型候选、四项指标/三个维度/五条关系的责任域审核与不可覆盖目录发布；运行时校验发布文件、语义版本及数据快照；在线规则加受限模型候选、IR 定义指纹校验 | 演示链路及模拟发布机制已验证；业务审核和正式授权尚未完成 |
| P1-3 | Supervisor 读取筛查结果后追加质量任务，再按质量结果追加材料任务；任务记录依赖、触发引用、预算与停止原因 | 本次真实模型轨迹通过 |
| P1-4 | SQL AST 逐表字段、函数、注册 JOIN、LIMIT 和一对多聚合检查；PostgreSQL 只读事务与计划成本限制 | 本地和隔离 PostgreSQL 通过 |
| P1-5、P1-8 | 管理员导入与应用启动分离，已有业务 Schema 拒绝重导入；运行容器仅持只读查询及独立 checkpoint 账号 | 隔离 PostgreSQL 通过 |
| P1-6 | 结果引用按执行用户授权；IR 明示 `single_tenant_demo` | **未关闭正式数据域/租户权限要求** |
| P1-7 | 中断、同运行 ID 恢复、快照及语义校验、查询幂等回读 | 本地和隔离 PostgreSQL 通过 |
| P2-9 至 P2-15 | 画像、黄金 SQL、过程评测、带列类型/预览/统计摘要的结果分页与任务引用、结构化错误、单分支查询失败降级、空结果与零分母状态、长链路图表和 Markdown 报告；报告正文重算校验 | 所列本地场景通过；Langfuse 实例未验证 |

## 实际运行

| 命令或操作 | 退出码与结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | 0；26 项通过，含单个质量分支超时后的部分报告、空结果/零分母状态、结果摘要、脱敏候选上下文及临时目录中的模拟发布 |
| `.\.venv\Scripts\python.exe evaluate.py` | 0；SQLite 固定快照 22/22 项通过，0 项未运行；新增分支超时、空结果、零分母及闭开日期边界场景 |
| `node --check web/app.js` | 0 |
| 早期代码调用真实模型完成 2025 年上半年交付质量探查 | 0；`completed`，当时为 11 个查询任务，3 个计划版本，2 次真实模型规划决策；轨迹已由本轮结果覆盖 |
| 当前代码对非固定措辞“供应商按期到货表现”调用真实模型语义候选 | 0；绑定 `on_time_rate`，随后由程序生成并校验 IR；模型 `deepseek-flash`，配置指纹 `2e5af18bb166` |
| `docker compose -p codex_acceptance_1104 up -d postgres` | 0；创建隔离数据库和数据卷 |
| `docker compose -p codex_acceptance_1104 --profile setup run --build --rm init` | 0；导入 16 表，建立只读与 checkpoint 账号 |
| `docker compose -p codex_acceptance_1104 run --build --rm --no-deps app python tests/postgres_acceptance.py` | 0；只读权限拒绝写操作和原业务 Schema 访问；25 行交付指标、6 行质量指标、11 任务探查及 checkpoint 恢复通过 |
| `docker compose -p codex_acceptance_1104 down -v` | 0；隔离容器、网络和数据卷已清理 |
| `docker compose -p codex_eval_1104 run --build --rm --no-deps app python evaluate.py`（独立初始化后） | 0；PostgreSQL 固定快照 21/21 项通过，0 项未运行；随后 `down -v` 退出码 0，容器与数据卷已清理 |
| `docker compose -p codex_eval_latest run --build --rm --no-deps app python evaluate.py`（独立初始化后） | 0；PostgreSQL 固定快照 22/22 项通过，含闭开日期边界；随后 `down -v` 退出码 0，容器与数据卷已清理 |
| `.\.venv\Scripts\python.exe -m agent_system.semantic_governance suggest` | 0；真实模型生成 16 条表、业务对象、关系、维度和指标草稿，其中月、季两条使用未登记的 `ship_date`，程序标出 2 条无效；草稿写入 `runtime/semantic_candidates.json`，未发布 |
| `docker compose -p codex_final_1104 run --build --rm --no-deps app python tests/postgres_acceptance.py` | 1；发现镜像未包含离线候选所需的数据表说明。随后已将该说明加入镜像并清理隔离数据卷 |
| `docker compose -p codex_final_recheck run --build --rm --no-deps app python tests/postgres_acceptance.py` | 0；修复后 PostgreSQL 权限、指标、任务图、恢复和脱敏样例读取通过 |
| `docker compose -p codex_final_recheck run --rm --no-deps app python evaluate.py` | 0；PostgreSQL 固定快照 22/22 项通过，0 项未运行；随后 `down -v` 退出码 0，容器与数据卷已清理 |
| `docker compose -p codex_dimensions_1104 run --build --rm --no-deps app python tests/postgres_acceptance.py` | 0；指标、维度和关系目录在 PostgreSQL 画像上通过校验，权限、恢复与脱敏样例读取通过 |
| `docker compose -p codex_dimensions_1104 run --rm --no-deps app python evaluate.py` | 0；PostgreSQL 固定快照 22/22 项通过，0 项未运行；随后 `down -v` 退出码 0，隔离资源已清理 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`、`python evaluate.py`（加入独立平均延期指标后） | 退出码均为 0；28 项单测、SQLite 24/24 项评测通过；供应商 11 上半年平均延期 2.5 天 |
| `docker compose -p codex_delay_1104 run --build --rm --no-deps app python tests/postgres_acceptance.py` | 0；PostgreSQL 上新指标返回 25 家供应商，供应商 11 为 2.5 天；权限、任务图和恢复通过 |
| `docker compose -p codex_delay_1104 run --rm --no-deps app python evaluate.py` | 0；PostgreSQL 24/24 项通过，0 项未运行；随后 `down -v` 退出码 0，隔离资源已清理 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`（加入诊断工具后） | 0；31 项通过，含日期阶段与缺陷分类的供应商、发货队列范围检查，以及质量结果变化触发不同诊断任务 |
| `.\.venv\Scripts\python.exe evaluate.py`（加入诊断工具后） | 0；SQLite 固定快照 26/26 项通过，0 项未运行 |
| `node --check web/app.js`、Python 编译检查、`pip check` | 均为 0 |
| 当前代码重新调用真实模型生成探查轨迹 | 0；`completed`，12 次查询、3 个计划版本、两次模型规划决策；供应商 11 的交付日期阶段任务依赖质量任务，报告证据校验通过；见 [real-model-current-trace.json](real-model-current-trace.json) |
| `docker compose -p codex_diagnostic_1104` 隔离初始化及 `tests/postgres_acceptance.py` | 0；16 表导入、只读和 checkpoint 权限、平均延期指标、日期阶段和缺陷诊断、12 任务探查及同运行恢复通过 |
| `docker compose -p codex_diagnostic_1104 run --rm --no-deps app python evaluate.py` | 0；PostgreSQL 固定快照 26/26 项通过，0 项未运行；随后 `down -v` 退出码 0，容器、网络和数据卷已清理 |
| `.\.venv\Scripts\python.exe -m agent_system.semantic_governance suggest`（最新语义源） | 0；重新生成 16 条离线草稿，2 条因使用未登记字段无效；`source_hash` 与当前 `semantic.yaml` 一致，未发布 |
| `docker compose -p codex_restore_1104` 隔离备份与恢复演练 | 0；初始化后先运行 PostgreSQL 验收以产生 checkpoint，再分别对业务库和 checkpoint 库执行 `pg_dump -Fc` 与 `pg_restore --exit-on-error` 到新库；恢复后发货表源库/新库均为 2200 行，checkpoint 表均为 52 行；随后 `down -v` 清理隔离容器和数据卷 |
| `./verify-restore.ps1` | 0；可重复脚本在随机隔离项目内恢复业务库、checkpoint 库及应用持久卷；17 张业务 Schema 表、4 张 checkpoint 表逐表行数与内容摘要一致，40 个应用文件的 SHA-256 一致；容器和临时数据卷均已清理 |
| `.\.venv\Scripts\python.exe -m agent_system.semantic_governance validate` | 0；当前 `semantic.yaml` 在固定快照画像上返回 `errors: []`；公式、例外、关系与责任域的待确认项汇总于 [语义口径审核包](semantic-review-packet.md) |
| 正式目录降级路径回归 | 0；已发布目录不能使用 `demo` IR；缺少正式授权快照时，绑定、目录、结果回读和探查等数据入口返回 `FORMAL_AUTHORIZATION_REQUIRED`，健康检查返回 503，不执行候选演示权限下的正式查询；单测覆盖绑定、目录与健康检查入口 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`、`.\.venv\Scripts\python.exe evaluate.py`（正式目录门控后） | 均为 0；32 项单测通过，SQLite 固定快照 26/26 项评测通过 |
| 单供应商受控过滤回归 | 问题中的“供应商 11”或结构化 `supplier_id=11` 写入 IR，并以参数化 SQL 过滤准时率和平均延期；多 ID、冲突 ID 及暂不支持的拒收率过滤均返回歧义提示，不返回未过滤的总体数据 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`、`.\.venv\Scripts\python.exe evaluate.py`（加入过滤后） | 均为 0；33 项单测通过，SQLite 固定快照 28/28 项评测通过，过滤结果与独立事实表计算一致 |
| `docker compose -p codex_filter_1104` 隔离初始化及 `python evaluate.py` | 0；PostgreSQL 固定快照 28/28 项评测通过，含单供应商过滤与拒绝未支持筛选；随后 `down -v` 清理隔离资源 |
| 已登记季度维度回归 | 到货准时率、平均延期天数按预计到货日分季度；供应商 11 的准时数为 Q1 `8/10`、Q2 `4/10`；季度数值与独立事实表计算一致，图表 X 轴使用季度标签；未登记的月度交付或季度拒收分组返回歧义提示 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`、`.\.venv\Scripts\python.exe evaluate.py`（季度维度后） | 均为 0；34 项单测通过，SQLite 固定快照 30/30 项评测通过；随后补充多指标同问的澄清回归，定向单测及 SQLite 30/30 再次通过 |
| `docker compose -p codex_quarter_1104` 隔离初始化及 `python evaluate.py` | 0；PostgreSQL 固定快照 30/30 项评测通过，含季度指标独立计算；随后 `down -v` 清理隔离资源 |
| 当前代码重新调用真实模型生成 [探查轨迹](real-model-current-trace.json) | 0；`completed`、12 次查询、3 个计划版本，报告证据校验通过；供应商 11 的日期阶段任务仍依赖质量任务 |
| 命名季度日期边界回归 | “2025 年 Q1”解析为 `[2025-01-01, 2025-04-01)`，“2025 年第二季度”解析为 `[2025-04-01, 2025-07-01)`；供应商 11 分别为 `8/10`、`4/10`，季度与半年并写时要求澄清；定向单测及 SQLite 固定快照 31/31 项通过 |

最新真实模型轨迹中，交付筛查给出候选 `11、14、13、3、21`。质量任务仅针对这些候选，后续材料及日期阶段任务由质量结果触发。供应商 11 的交付准时数为 `8/10 → 4/10`，同队列拒收率为 `2.93% → 1.99%`；报告核查了两期 `10/10` 收货覆盖和材料分层，并列出发运至预计、实际到货的日期差，未把总体质量改善写成双域恶化，也未将日期差推断为根因。报告正文、分区、图表数据、SQL 哈希与结果引用均可由轨迹检查。

## 尚未通过的正式门槛

- 采购、质量、数据负责人尚未审核并发布 `published` 语义版本。因此所有数字仍为演示试算，P1-1 的正式业务口径门槛未关闭。
- 当前身份范围是单租户演示；尚无两种数据域身份、行级策略或正式授权快照，P1-6 未关闭。
- 未配置经批准的 Langfuse 实例与凭据，不能宣称外部 trace 或实验集通过。
- 表与业务对象候选目前只保留为离线草稿；可发布的最小语义包限定到货准时率、平均延期天数及两种来料拒收率、三个维度和五条关联。在线绑定与编译仍限定首批供应商交付和来料质量主题；当前只增加单供应商受控过滤，通用受限临时查询未开放。
- 长链路可追加受控的缺陷分类及交付日期阶段探查任务，但分类与日期差不能证明根因；报告不把推测原因写入已核验事实。
- PostgreSQL 验证使用隔离容器、固定快照和测试账号；本次备份恢复演练证明两个库逐表内容摘要及应用持久卷逐文件哈希一致，尚未验证正式环境的迁移、持续备份、切换回退与并发负载。
