# 重建交付记录（2026-11-05）

依据：根目录 query.md、Agent.md、memory.md、context.md，以及用户提供的 src 分层参考图。

## 已落实

- 代码迁入 src/enterprise_agent，按入口、应用、Agent、意图、存储、上下文、调度、语义、查询、数据准备、界面和验收分层。
- Unified Supervisor 与四个专业 Agent 独立角色和工具权限；不新增 Planner、Memory、Chart 等逻辑 Agent。
- 四类存储、七张核心表、稳定 Request ID、完整 Revision、动态 Subtask DAG、命名输入输出、四种 Artifact、来源链和内容地址。
- 五阶段 Context，历史回答 blocks 精确定位；大结果选择性读取、精确计算和分页。
- 动态并行调度、中间证据重规划、跨轮需求合并、受影响节点重做、逐项需求验收。
- 只读查询、字段和表关系校验、受控产物计算、真实图表配置与图例、Markdown 分析和报告。
- 更新启动、打包、Docker 和会话页面入口；旧固定业务流程及旧验收代码已删除。

## 本次验证

只进行基础冒烟，不执行旧的大规模回归或负载测试。

1. HTTP 登录、普通问答、会话事件和页面入口通过。
2. Python / JavaScript 基础语法通过。
3. 动态 DAG、专业 Agent 的真实工具执行、四类产物、跨轮改图且不重复查询通过。
4. Summary 压缩、严格通信模型、未知字段/写操作拒绝、会话归属检查通过。
5. 真实模型安排 SemanticAgent 和 QueryAgent，执行 `SELECT COUNT(*) AS row_count FROM products AS products`，数据库返回 **16**，运行状态 completed。查询记录见 rebuild-live.json。

最初检查发现 SQLGlot 参数不兼容，修正后只补查受影响的数据流程；真实模型接入还修正了 Request 引用应表示需求本身，以及验收证据应指向实际输出的契约。其余已通过项目未反复重跑。最终补充的 ResourceRef 校验禁止在 Artifact 引用的 output 字段里放行值。

固定夹具位于 evaluation，未进入生产业务路径。PostgreSQL 容器部署、浏览器视觉回归及大规模自然语言准确率不属于本次简明检查的结论。现有候选指标保持未发布状态，系统不会代替业务负责人批准口径。
