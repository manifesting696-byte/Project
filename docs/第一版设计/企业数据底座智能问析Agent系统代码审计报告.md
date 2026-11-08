# 企业数据底座智能问析 Agent 系统代码审计报告

## 1. 总体结论

**当前项目未达到设计方案第 0、1 阶段的验收要求，不建议按“设计完成”进入下一阶段。** 它是一个可运行的、限定主题的问析原型：本地数据导入、账号鉴权、两项固定指标查询、表格和图表展示，以及一条带条件分支的供应商探查流程均有实际运行证据。但已发布语义注册表、受控语义 IR、完整 SQL 安全网关、真正的专业 Agent 分工、结果驱动的任务追加和中断恢复尚未形成设计要求的闭环。

主要验收依据是现有的[系统设计方案](./企业数据底座智能问析Agent系统设计方案.md)。用户所称的“设计方案(2).md”在项目目录中未找到；本报告使用上述实际文件，并参考了[任务背景](./【A07】企业数据底座智能问析%20Agent%20系统【杭自院】.docx)和[数据表说明](./电子供应链数据表说明.md)。审计没有修改原项目源代码、业务数据或配置。

## 2. 需求符合性矩阵

“已验证”仅指本次在隔离副本中观察到的行为，不代表生产 PostgreSQL 或外部服务验收。

| 需求 | 状态 | 代码证据与本次验证 | 主要差距 |
|---|---|---|---|
| 16 表数据接入 | 已实现并验证，本地模式 | [data.py:103](../agent_system/data.py#L103)；隔离导入成功，62,077 行、快照 `bc507973f100da89` | PostgreSQL 导入本次未复验 |
| 元数据提取与画像 | 部分实现 | [data.py:50](../agent_system/data.py#L50)记录列、推断类型、空值和主键重复；`/tables` 可读 | 未扫描真实数据库 Schema、权限和约束；缺少列基数、极值、枚举、时间覆盖及关系基数画像 |
| 自动语义建模与审核 | 未实现 | [semantic.yaml](../semantic.yaml)是静态配置 | 无 LLM 候选生成、程序化业务校验、人工审核流程 |
| 语义注册表与版本依赖 | 实现与方案不一致 | YAML 顶层 `status: provisional`；[core.py:18](../agent_system/core.py#L18)直接读取 | 无对象级 `candidate/reviewed/published/deprecated`、不可变发布、数据库投影和依赖失效 |
| 自然语言语义绑定 | 部分实现 | [core.py:36](../agent_system/core.py#L36)按关键词和日期规则路由；现有题目测试通过 | 仅支持少数固定意图；未绑定授权的已发布指标、维度、过滤和关系路径 |
| IR 生成与校验 | 未实现 | [core.py:75](../agent_system/core.py#L75)只返回简化字典 | 无可解析的指标、关系 ID 和版本，也无 JOIN 路径及粒度校验 |
| SQL 生成、验证与执行 | 部分实现 | [core.py:78](../agent_system/core.py#L78)、[core.py:132](../agent_system/core.py#L132)使用 AST 检查、参数化固定模板和超时；本地查询通过 | AST 白名单不完整，错误 JOIN、CROSS JOIN、任意函数和极大 LIMIT 可通过预检；无 `EXPLAIN` 成本门控 |
| LangGraph 编排与状态 | 部分实现 | [core.py:330](../agent_system/core.py#L330)确实构建并运行 `StateGraph`，条件边随结果分流 | 图状态无任务 DAG、预算、正式状态迁移、分支验收和终止原因 |
| Supervisor 与专业 Agent | 未实现 | 图仅含四个业务函数节点；[core.py:246](../agent_system/core.py#L246)起 | 无 Supervisor 决策节点、四类 Agent 的独立契约与工具权限，也没有模型调用 |
| 动态规划与长链路 | 部分实现 | 交付筛查后才查候选质量；2023 年无候选时跳过质量查询，已验证 | 后续节点和 SQL 预先写死；不追加带 ID、依赖、触发证据及预算的新任务；质量改善后的构成核查缺失 |
| 工具调用与失败恢复 | 部分实现 | [core.py:103](../agent_system/core.py#L103)对查询设时限；非法写查询被拦截 | 无统一工具 Schema、错误契约、有限重试、熔断及幂等记录 |
| Memory 与 Context Engineering | 未实现 | [core.py:344](../agent_system/core.py#L344)配置 checkpointer | 无持久 Store、上下文预算、压缩和约束保留核验；未提供真实中断恢复入口 |
| Langfuse 可观测性 | 已实现但未验证，且范围不完整 | [telemetry.py:10](../agent_system/telemetry.py#L10)有可选 span | 本次无实例和凭据；无 LangGraph callback、trace 关联、dataset/experiment 证据 |
| 图表与报告 | 部分实现 | [api.py:175](../agent_system/api.py#L175)、[core.py:293](../agent_system/core.py#L293)、[web/app.js:95](../web/app.js#L95) | 长链路报告无图表、数值一致性门控和导出；证据、覆盖率与口径限制展示不足 |
| 评测体系和安全控制 | 部分实现 | [test_system.py](../tests/test_system.py)、[evaluate.py](../evaluate.py)、[security.py](../agent_system/security.py)；现有测试通过 | 缺黄金 SQL 题库、反事实分支和故障注入；无租户或数据域权限隔离 |

## 3. 代码问题清单

本次没有发现可证实的 **P0**。以下 P1 是进入设计验收前必须解决的问题；安全网关问题须注意：目前 `/ask` 使用固定 SQL 模板，`/sql-check` 只预检用户提交的 SQL，本次未发现直接执行任意用户 SQL 的接口。

### P1

1. **未发布口径仍可用于回答。** [semantic.yaml:1](../semantic.yaml#L1)标记 `provisional`，但[core.py:75](../agent_system/core.py#L75)仅将状态放入返回值，[api.py:166](../agent_system/api.py#L166)继续执行。`validate_semantics()`只核对少量 CSV 列名。影响：演示口径可能被当作正式业务指标。应建立对象级发布门控、审核记录、版本和依赖校验；未发布口径只能明确标注为待确认试算。

2. **实际能力是规则路由与固定 SQL，而非方案中的专业 Agent。** [core.py:36](../agent_system/core.py#L36)用关键词决定意图，[core.py:132](../agent_system/core.py#L132)按意图选择固定模板；未见模型服务调用或专业 Agent 的输入输出契约。影响：无法处理设计方案要求的开放式语义绑定、动态分析建议和职责隔离。应先围绕现有两项指标建立受控 IR，再引入确有必要的语义、查询、分析和呈现节点，保持确定性权限检查。

3. **长链路只有固定条件分支，且遗漏关键反证路径。** [core.py:267](../agent_system/core.py#L267)仅在拒收率上升时进入材料核查；[core.py:330](../agent_system/core.py#L330)的图结构固定。隔离执行中供应商 11 的拒收率从 `2.9272%` 降至 `1.9914%`，却没有进入材料构成核查；该查询只覆盖供应商 3、21。影响：不能排除总体改善掩盖类别内恶化，也无法完成交付阶段拆解。应在观察节点记录触发事实，按证据追加有 ID、依赖、验收条件和停止条件的探查任务。

4. **SQL AST 检查未约束函数、JOIN 关系和资源消耗。** [core.py:78](../agent_system/core.py#L78)虽使用 `sqlglot`，但隔离验证表明 `SELECT pg_sleep(5) FROM Suppliers`、错误键 JOIN、CROSS JOIN 和 `LIMIT 1000000000` 均返回允许。影响：`/sql-check` 会对不安全或错误的查询给出错误安全信号，未来复用执行网关风险更高。应按表别名绑定列权限，只允许注册关系与函数，校验 JOIN 基数、LIMIT 和估计成本，再用数据库只读权限兜底。

5. **PostgreSQL 管理凭据进入应用容器。** [compose.yaml](../compose.yaml)同时向 `app` 提供 `PG_DSN`、`PG_ADMIN_DSN` 和只读 `DATABASE_URL`；[core.py:347](../agent_system/core.py#L347)用 `PG_DSN` 保存 checkpoint。影响：应用运行进程持有重建业务 Schema 的高权限凭据。应将导入与授权配置移至独立初始化作业；运行应用只持有查询视图和单独 checkpoint 所需的最小权限账号。

6. **无数据域、行列和租户授权绑定。** [api.py:184](../agent_system/api.py#L184)只检查 `admin/analyst` 角色，[security.py:135](../agent_system/security.py#L135)的身份记录没有数据范围；所有分析员共用相同查询视图。影响：无法满足方案要求的租户与数据范围隔离。应由后端可信上下文注入权限快照，并在语义检索、SQL 编译和数据库视图或行策略上同时约束。

7. **Checkpoint 存在，但真实中断恢复链路未实现。** [core.py:344](../agent_system/core.py#L344)每次用输入重新 `invoke` 图；未见 `interrupt()`、`Command(resume=...)`、恢复前版本核验或幂等键。影响：不能据此认定可恢复中断任务，节点重入也可能重复查询或生成产物。应建立任务状态迁移与恢复接口，并用故障注入验证同一 `thread_id` 的暂停、恢复和副作用去重。

8. **数据快照变化时启动流程会重建 PostgreSQL 业务 Schema。** [bootstrap.py:18](../agent_system/bootstrap.py#L18)在快照 ID 变化时调用导入；[postgres_import.py:36](../agent_system/postgres_import.py#L36)执行 `DROP SCHEMA ... CASCADE`。影响：业务表、依赖对象或历史数据有被整体替换的风险，且与增量维护目标不符。本次未运行该路径。应将导入变成明确的离线发布操作，先核对目标库与备份，再采用版本化 Schema 或迁移方案。

### P2

9. **画像和语义校验依据 CSV，不依据运行中的数据库。** [data.py:50](../agent_system/data.py#L50)、[core.py:26](../agent_system/core.py#L26)。应读取数据库目录、约束和视图权限，补齐去重数、日期范围、枚举、引用覆盖率和联结基数，并与导入快照比对。

10. **缺少指标粒度和重复计数门控。** [core.py:132](../agent_system/core.py#L132)的固定模板目前两项核心汇总可复核，但网关不记录期望粒度，也不检查 JOIN 后行数膨胀；错误 JOIN 已能通过预检。应在 IR 和执行结果中记录事实粒度、预聚合策略及膨胀检查。

11. **长链路报告证据不足。** [core.py:293](../agent_system/core.py#L293)报告展示部分比率，却没有方案要求的平均延期天数、收货覆盖率、缺陷原因、构成反证、分支缺口和每条结论对应的指标版本。应先补计算与证据字段，再做报告数值和引用一致性校验。

12. **查询结果管理不满足分页和预算要求。** [core.py:103](../agent_system/core.py#L103)超过 `max_rows` 就报错，没有总行数、结果引用分页、扫描成本或并发预算。应保留受控聚合，同时为明细查询提供分页引用和资源门控。

13. **异常契约不统一。** [api.py:196](../agent_system/api.py#L196)仅专门处理 `Blocked`；其他异常记录后重新抛出。图节点没有结构化错误码、重试次数或 `partial/failed/blocked` 状态。应统一错误模型，并区分业务空结果、可重试故障和安全拒绝。

14. **评测覆盖不足。** [test_system.py:52](../tests/test_system.py#L52)断言固定步骤序列；[evaluate.py:44](../evaluate.py#L44)写入固定的 `checks: 14`，这不是逐项自动统计的验收总数。应建立独立黄金 SQL、无候选、质量反证、构成变化、错误 JOIN、权限、超时和恢复场景。

15. **Langfuse、图表和交付功能仍是局部实现。** [telemetry.py:10](../agent_system/telemetry.py#L10)仅提供可选 span；[api.py:175](../agent_system/api.py#L175)只为两类简单查询配置图表。应在有实例时核验脱敏 trace 与图节点关联，并补长链路图表、报告证据和导出。此项按设计阶段顺序处理，无需先扩展无数据支持的机器学习平台。

## 4. 架构符合性分析

项目**真实使用了 `StateGraph`**，不是仅声明依赖。执行轨迹证明“先交付、后候选质量”这一层条件决策确实受数据影响：2023 年无候选时跳过质量查询；2025 年候选仅有 5 家进入质量查询。这是已验证的局部能力。

但它尚不是设计方案中的 Supervisor 加专业 Agent 架构。没有 `Send`、`Command`、reducer、任务 DAG、分支 ID、预算或独立 Agent 上下文；四个图节点运行固定 SQL 和报告模板。不能把该条件工作流等同于动态规划，也不能把 checkpointer 文件存在等同于中断恢复成功。

离线与在线语义建模也未解耦：静态 YAML 直接供运行时读取，状态仍是 `provisional`；没有从真实数据库发现、审核并发布对象的过程。好的一点是当前两个固定 SQL 模板没有让 LLM 临时猜公式或 JOIN；缺点是系统还不具备方案要求的受控开放式绑定能力。

## 5. 测试执行报告

所有写入型测试均在系统临时目录中的项目副本运行。原项目的既有系统验收记录作为线索阅读，没有把其中的历史声明当作本次复验结果。

| 实际执行 | 结果 |
|---|---|
| `python -m agent_system.data`，隔离副本 | 退出码 0；导入 16 表、62,077 行 |
| `python evaluate.py`，隔离副本 | 退出码 0；脚本自报 `passed`，后端为 SQLite |
| `python -m unittest discover -s tests -v` | 5 项通过，0 失败 |
| `python -m compileall -q agent_system tests evaluate.py` | 退出码 0 |
| `node --check web/app.js`、`python -m pip check` | 均退出码 0 |
| 隔离副本启动 Uvicorn，访问 `/health`、匿名 `/catalog` | `/health` 200 且快照 ID 正确；匿名目录请求 401 |
| 独立 SQL 对照核心指标 | 25 家供应商准时率的分子、分母均一致；6 个月拒收量、收货量均一致 |
| 边界探测 | 写 SQL 和敏感列查询被阻断；1 ms SQLite 查询超时被阻断；错误 JOIN、CROSS JOIN、`pg_sleep` 和极大 LIMIT **错误地通过预检** |

独立基准 SQL 使用原始事实表计算，关键查询如下；日期均为左闭右开区间：

```sql
SELECT supplier_id, COUNT(*) AS shipments,
       SUM(actual_arrival_date <= expected_arrival_date) AS on_time
FROM SupplierShipments
WHERE expected_arrival_date >= '2025-01-01'
  AND expected_arrival_date < '2025-07-01'
  AND actual_arrival_date IS NOT NULL
GROUP BY supplier_id;

SELECT SUBSTR(gr.receipt_date, 1, 7) AS month,
       SUM(d.rejected_quantity) AS rejected_quantity,
       SUM(d.received_quantity) AS received_quantity
FROM GoodsReceiptDetails d
JOIN GoodsReceipts gr ON gr.gr_id = d.gr_id
WHERE gr.receipt_date >= '2025-01-01'
  AND gr.receipt_date < '2025-07-01'
GROUP BY SUBSTR(gr.receipt_date, 1, 7);
```

供应商 11 的独立队列核算得到交付准时数 `8/10 → 4/10`，来料拒收量/收货量 `118626/4052552 → 90102/4524521`，即 `2.9272% → 1.9914%`。这验证了现有数值，也验证了报告中“不支持双重恶化”的狭义判断；**材料构成是否掩盖类别内恶化仍未核查**。

**未运行且不得视为通过：** Docker 引擎未连接，因此本次无法启动或复验 PostgreSQL 导入、数据库级只读角色、PostgreSQL checkpoint；没有可用 Langfuse 实例与凭据，无法核验真实 trace；没有实现的中断恢复、租户隔离、提示注入和完整故障注入链路也无法做通过判定。本次没有调用外部模型或修改生产数据。

## 6. 关键风险与修复顺序

最高风险是**语义口径未发布却能产生正式形式的数值回答**，以及 SQL 预检把错误关联判断为合法。长链路当前可根据第一轮结果选择候选，但在质量改善这类关键反证出现时停止得过早，可能漏掉构成变化；状态存储虽存在，却缺少可证明的恢复语义。权限方面，角色限制有效，但数据范围隔离缺失，且运行容器持有管理数据库凭据。

按依赖关系，建议依次修复：

1. **先锁定数据与权限边界：** 分离导入和运行凭据，停止启动时自动重建业务 Schema；定义只读视图、数据范围与审计记录。
2. **发布最小可信语义层：** 先对现有两项指标及关系做数据库画像、业务审核、对象版本和发布门控；建立可校验 IR 与确定性 SQL 编译。
3. **收紧 SQL 网关：** 逐表列权限、函数和 JOIN 白名单、粒度检查、成本预估与结果限额；用错误 JOIN、函数和资源耗尽用例回归。
4. **补齐长链路证据门控：** 对质量改善者检查覆盖率与材料构成，必要时拆解交付阶段；记录每次追加任务的触发结果、预算和停止理由。
5. **再做恢复与可观测验收：** 统一任务状态和错误契约，测试真实中断恢复、幂等性及 Langfuse trace；最后扩充黄金 SQL 题库和故障注入。

## 7. 最终验收结论

| 维度 | 评价 |
|---|---|
| 功能完整性 | **部分通过**：限定主题的本地闭环可运行，设计要求的语义治理和长链路能力缺口明显 |
| 设计符合性 | **未通过**：实际是固定模板加条件工作流，未形成设计中的受控多 Agent 架构 |
| 数据正确性 | **局部通过**：本次核对的两项核心指标和供应商 11 样本正确；跨表粒度与完整长链路结论未获系统性验证 |
| Agent 真实能力 | **未通过**：没有模型驱动的专业 Agent、动态任务追加和结构化观察决策 |
| 系统可靠性 | **部分通过**：现有回归测试和本地超时保护有效；恢复、幂等和故障降级未验证 |
| 安全性 | **未通过设计验收**：基础鉴权和固定查询保护存在，SQL 网关、最小权限与数据范围隔离不足 |

**建议进入针对上述问题的修复与复验阶段；暂不签署设计方案第 0、1 阶段完成验收。**
