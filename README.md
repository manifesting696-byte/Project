# 企业数据智能问析系统

当前实现采用包含完整 spec 的 Request 读取对象、不可覆盖的 RequestRevision、执行 Task 和可追溯 Artifact。当前规格由版本表统一加载，实际交付通过执行记录按需解析。Supervisor 负责意图、一次生成完整要求与初始计划、动态重规划、验收与回答。默认 Profile 启用语义、查询、分析和展示四个专业 Agent，扩展角色通过插件注册。

## 启动

在 PowerShell 中执行下列命令。

```powershell
.\start-local.ps1
```

浏览器打开 http://127.0.0.1:8000。启动脚本使用 Docker Compose 构建应用并初始化 PostgreSQL。请在 .env 配置 POSTGRES_ADMIN_PASSWORD、POSTGRES_RUNTIME_PASSWORD、POSTGRES_READONLY_PASSWORD、INITIAL_ADMIN_PASSWORD，以及 LLM_URL、LLM_API_KEY、LLM_MODEL。密码使用适合 URL 的随机值。已有业务快照和账号继续使用。数据库仅绑定本机 5433 端口。

## 目录结构

```text
Project/
├── src/enterprise_agent/       应用源码
│   ├── main.py                 进程入口：uvicorn enterprise_agent.main:app
│   ├── settings.py             路径与限额的唯一读取点
│   ├── domain/                 数据形状与不变量（base/refs/request/task/answer/artifact/session/errors）
│   ├── flow/                   主流程：三张图、状态、checkpoint、Application 编排、FLOW.md
│   ├── stages/                 按阶段竖切
│   │   ├── intent/             意图契约与输出
│   │   ├── prepare/            Request 聚合根、规格合并、计划校验、提交
│   │   ├── execute/agents/     Supervisor 与四个专业 Agent、工具与授权
│   │   ├── verify/             交付验收的硬校验
│   │   └── answer/             文稿→Artifact→Response 块
│   ├── prompt/                 提示词：supervisor.py、specialists.py
│   ├── ledger/                 PostgreSQL 账本、产物、会话摘要
│   │   └── migrate/            三代离线迁移
│   ├── catalog/                语义目录、指标发布门禁、表接入状态
│   ├── query/                  只读数据源、SQL 静态校验、查询网关
│   ├── model/                  模型客户端与 OpenAI 兼容传输
│   ├── plugins/                插件发现、依赖、生命周期、内置目录
│   ├── server/                 FastAPI 装配、路由（routes/）、身份认证、静态资源
│   └── ops/                    建库、只读授权、数据画像等运维脚本
├── tests/                     回归测试、实库验收及测试夹具
├── config/                    插件组合、关系与语义配置
│   └── semantic/              catalog.yaml 与 versions/
├── data/                      schema.sql 与 seed/ 示例 CSV
├── docs/                      文档索引
│   ├── architecture/          当前字段、数据流和插件架构
│   ├── operations/            PostgreSQL 部署与迁移
│   ├── reference/             数据字典及原始需求文档
│   └── archive/               v1、v2 设计和早期验收记录
└── runtime/                   本地运行文件，由 Git 排除
    ├── content/               当前产物正文
    ├── backups/               数据备份与目录调整记录
    ├── diagnostics/           验证报告
    ├── logs/                  运行与测试日志
    ├── scratch/               临时验证脚本
    └── archive/               旧版本运行文件
```

根目录的 start-local.ps1、evaluate.py 分别是启动和验收入口。Dockerfile、compose.yaml 与依赖声明保留在根目录。runtime 中的 manifest.json、data_quality.json、db_profile.json 为本地数据画像文件。

**先读 [src/enterprise_agent/flow/FLOW.md](src/enterprise_agent/flow/FLOW.md)**：一页纸说明一次提问经过哪些节点、失败停在哪里、四种挂起如何恢复，以及不得改动的冻结契约（thread_id、workflow_key、事件名、图节点名、产物路径）。

## 当前设计

完整字段定义、数据流、HTTP 接口和迁移说明见 [第三版字段与数据流](docs/architecture/字段与数据流.md)。

- stages/intent/schema.py 定义 none/create_task/update_task 和四类 category。
- domain/ 定义 Request、快照、执行节点、交付与引用；不导入任何 I/O。
- stages/prepare/requests.py 管理请求身份、版本、输入依赖、交付解析和失效传播。
- stages/prepare/preparation.py 合并要求整理与首次规划，校验候选结果并原子保存 Revision 和初始计划。
- stages/prepare/plan.py 校验执行 DAG，绑定真实输入及交付。
- flow/application.py 提供会话锁、业务校验和图执行入口。
- flow/turn_graph.py 用 LangGraph 串联意图、准备、提交、逐项处理和发布回复。
- flow/request_graph.py 用 LangGraph 管理执行、验收、重规划和 interrupt/resume。
- flow/task_graph.py 用 Send 并行分发就绪 Task，并按 task_id 汇总结果。
- flow/checkpoints.py 使用 PostgresSaver 将运行现场保存到 agent_checkpoints Schema。
- plugins/ 管理插件发现、服务依赖、作用域事件、动态装卸和失败回滚。config/harness.yaml 定义 Profile 与 Bundle。
- query/database.py 提供只读数据源与实际表结构，catalog/tables.py 保存表接入状态。
- stages/answer/responses.py 将文字草稿保存为 Artifact，组装只含产物引用的 Response 块。前端据此展示正文和结果卡片。
- persistence/postgres.py 提供有界连接池、事务、版本化建表和跨连接会话锁。persistence/stores.py 保存 PostgreSQL 账本。
- persistence/migrate_postgres.py 离线转换历史 SQLite 副本，导入 PostgreSQL，保留原数据、SQL 和密码哈希。
- agents/prompt.py 集中说明角色和字段取值。

## 验证

```powershell
.\.venv\Scripts\python.exe evaluate.py
.\.venv\Scripts\python.exe evaluate.py --live-only
```

本机测试需要安装项目依赖，并配置 APP_DATABASE_URL、DATABASE_URL、独立 TEST_DATABASE_URL。首次可运行 python -m ops.provision --with-test-database 创建测试库。测试使用隔离 Schema。第二条命令通过真实模型查询 Products 产品数，并与业务快照核对。测试覆盖并行调度、重启恢复、产物写入后中断、计划与回复保存后的重放。

## 插件与数据表管理

AGENT_PROFILE 可选择 default、data 或 minimal。管理员通过 /runtime/plugins 管理运行时插件，通过 /catalog/tables 接入或移出已有数据表。运行中的工作结束后可以切换插件及目录配置。

字段、接口、插件示例和数据流见 [插件运行时与数据表接入](docs/architecture/插件运行时与数据表接入.md)。

## 原项目记录

main 分支提交 fdde3c2 和标签 baseline-before-request-refactor 保存改造前源码。新实现位于 refactor/request-task-model 分支。.env、虚拟环境和 runtime 被 Git 排除。PostgreSQL 改造前的源码由 before-postgres-only 标签保留。旧 SQLite 文件仅作为离线迁移来源与备份保存，运行时持久化使用 PostgreSQL。部署、迁移结果和恢复说明见 [PostgreSQL 部署与迁移](docs/operations/PostgreSQL部署与迁移.md)。

旧设计材料保存在 docs/archive/v1 和 docs/archive/v2。
