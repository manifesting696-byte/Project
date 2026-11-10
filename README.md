# 企业数据智能问析系统

当前实现采用包含完整 spec 的 Request 读取对象、不可覆盖的 RequestRevision、执行 Task 和可追溯 Artifact。当前规格由版本表统一加载，实际交付通过执行记录按需解析。Supervisor 负责意图、一次生成完整要求与初始计划、动态重规划、验收与回答。默认 Profile 启用语义、查询、分析和展示四个专业 Agent，扩展角色通过插件注册。

## 启动

在 PowerShell 中执行下列命令。

```powershell
.\start-local.ps1
```

浏览器打开 http://127.0.0.1:8000。启动脚本使用 Docker Compose 构建应用并初始化 PostgreSQL。请在 .env 配置 POSTGRES_ADMIN_PASSWORD、POSTGRES_RUNTIME_PASSWORD、POSTGRES_READONLY_PASSWORD、INITIAL_ADMIN_PASSWORD，以及 LLM_URL、LLM_API_KEY、LLM_MODEL。密码使用适合 URL 的随机值。已有业务快照和账号继续使用。数据库仅绑定本机 5433 端口。

## 当前设计

完整字段定义、数据流、HTTP 接口和迁移说明见 [第三版字段与数据流](docs/第三版设计/字段与数据流.md)。

- intent/models.py 定义 none/create_task/update_task 和四类 category。
- core/models.py 定义 Request、快照、执行节点、交付与引用。
- application/requests.py 管理请求身份、版本、输入依赖、交付解析和失效传播。
- application/preparation.py 合并要求整理与首次规划，校验候选结果并原子保存 Revision 和初始计划。
- application/tasks.py 校验执行 DAG，绑定真实输入及交付。
- application/service.py 提供会话锁、业务校验和图执行入口。
- pipeline/turns.py 用 LangGraph 串联意图、准备、提交、逐项处理和发布回复。
- pipeline/execution.py 用 LangGraph 管理执行、验收、重规划和 interrupt/resume。
- pipeline/scheduler.py 用 Send 并行分发就绪 Task，并按 task_id 汇总结果。
- pipeline/checkpoints.py 使用 PostgresSaver 将运行现场保存到 agent_checkpoints Schema。
- harness/ 管理插件发现、服务依赖、作用域事件、动态装卸和失败回滚。config/harness.yaml 定义 Profile 与 Bundle。
- query/database.py 提供只读数据源与实际表结构，semantic/catalog.py 保存表接入状态。
- application/responses.py 将文字草稿保存为 Artifact，组装只含产物引用的 Response 块。前端据此展示正文和结果卡片。
- persistence/postgres.py 提供有界连接池、事务、版本化建表和跨连接会话锁。persistence/stores.py 保存 PostgreSQL 账本。
- persistence/migrate_postgres.py 离线转换历史 SQLite 副本，导入 PostgreSQL，保留原数据、SQL 和密码哈希。
- agents/prompt.py 集中说明角色和字段取值。

## 验证

```powershell
.\.venv\Scripts\python.exe evaluate.py
.\.venv\Scripts\python.exe evaluate.py --live-only
```

本机测试需要安装项目依赖，并配置 APP_DATABASE_URL、DATABASE_URL、独立 TEST_DATABASE_URL。首次可运行 python -m enterprise_agent.application.provision_postgres --with-test-database 创建测试库。测试使用隔离 Schema。第二条命令通过真实模型查询 Products 产品数，并与业务快照核对。测试覆盖并行调度、重启恢复、产物写入后中断、计划与回复保存后的重放。

## 插件与数据表管理

AGENT_PROFILE 可选择 default、data 或 minimal。管理员通过 /runtime/plugins 管理运行时插件，通过 /catalog/tables 接入或移出已有数据表。运行中的工作结束后可以切换插件及目录配置。

字段、接口、插件示例和数据流见 [插件运行时与数据表接入](docs/第三版设计/插件运行时与数据表接入.md)。

## 原项目记录

main 分支提交 fdde3c2 和标签 baseline-before-request-refactor 保存改造前源码。新实现位于 refactor/request-task-model 分支。.env、虚拟环境和 runtime 被 Git 排除。PostgreSQL 改造前的源码由 before-postgres-only 标签保留。旧 SQLite 文件仅作为离线迁移来源与备份保存，运行时持久化使用 PostgreSQL。部署、迁移结果和恢复说明见 [PostgreSQL 部署与迁移](docs/第三版设计/PostgreSQL部署与迁移.md)。

旧设计材料保存在 docs/第一版设计 和 docs/第二版设计。
