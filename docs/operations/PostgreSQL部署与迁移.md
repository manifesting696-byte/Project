# PostgreSQL 部署与迁移

## 存储与权限

运行时全部使用 PostgreSQL 16。业务数据位于 supply_chain，模型通过只读账号 supply_chain_reader 访问 analysis 中的表或视图。应用使用 enterprise_runtime 写入 agent、identity 和 agent_checkpoints，并以事务内临时表进行产物二次计算。

| 位置 | 内容 |
| --- | --- |
| agent | 会话、需求、版本、执行节点、产物元数据、事件、摘要、表目录和迁移记录 |
| identity | 账号、认证会话、登录限制、审计和反馈 |
| agent_checkpoints | LangGraph 现场、暂停与恢复记录 |
| project_pg_data 卷 | PostgreSQL 数据文件 |
| project_app_data 卷的 content | 产物正文、完整数据、实际 SQL、图表配置和报告 |

数据库和产物文件需要配套备份。当前应用采用单个 Uvicorn worker，会话同时受线程锁和 PostgreSQL advisory lock 保护。数据库连接池有上限，查询有只读限制、超时与行数预算。Schema 迁移记录在各自的 schema_migrations 表中。

## 启动配置

.env 由 Git 排除，包含 POSTGRES_ADMIN_PASSWORD、POSTGRES_RUNTIME_PASSWORD、POSTGRES_READONLY_PASSWORD、INITIAL_ADMIN_PASSWORD，以及 LLM_URL、LLM_API_KEY、LLM_MODEL。连接 URL 中的密码应使用 URL 安全字符。

```powershell
.\start-local.ps1
```

脚本启动数据库、构建应用、运行初始化服务，再启动应用。初始化检测既有业务快照，空库首次导入 CSV，已有库沿用当前快照。已有账号继续保留原密码。应用容器使用 UID 10001，运行时只获得应用与只读账号，数据库管理员凭据只供初始化服务使用。

界面位于 http://127.0.0.1:8000。GET /health 检查账本和业务数据源连接，并返回 persistence 为 postgresql。PostgreSQL 默认仅在本机 5433 端口开放。

本机开发需额外配置 APP_DATABASE_URL 与 DATABASE_URL，分别对应运行账号与只读账号。测试另配 TEST_DATABASE_URL，指向独立的 enterprise_agent_test 数据库。

```powershell
.\.venv\Scripts\python.exe -m enterprise_agent.application.provision_postgres --with-test-database
.\.venv\Scripts\python.exe evaluate.py
.\.venv\Scripts\python.exe evaluate.py --live-only
```

## 已有表接入

新增业务表后，先由数据库管理员在 analysis 中开放相应视图并授予 supply_chain_reader 查询权限，再通过目录管理接口接入。目录保存实际字段、说明、关系和修订号。移出目录会更新后续查询的可见范围，历史产物仍可按来源链读取。

默认 Profile 使用 source_id 为 supply-chain-analysis。本机和 Docker 使用相同逻辑标识，不同数据源需配置不同标识。管理员接口及插件契约见 [插件运行时与数据表接入](../architecture/插件运行时与数据表接入.md)。

## 本次迁移

迁移前备份保存在 runtime/backups/20261110-200044-before-postgres，包括 supply_chain.dump、原 SQLite 一致快照、产物目录，以及旧 Docker 应用完整运行目录。

本次保留本地项目 admin，其密码哈希和账号 ID 原样导入。旧 Docker 身份记录保存在备份及 agent.legacy_application_records 中。

| 记录 | 导入数量 |
| --- | --- |
| 会话 | 3 |
| 需求及版本 | 各 6 |
| 执行节点 | 30 |
| 产物 | 36 |
| 会话事件 | 306 |
| 用户 | 1 |
| 认证会话 | 3 |
| 审计 | 6 |
| 反馈 | 1 |

36 个产物包括从历史回答恢复的 13 个文字产物。旧 SQLite 文件归档保存，正常启动和执行只连接 PostgreSQL。SQLite 转换代码仅供离线迁移和迁移回归测试使用。

本次验收的 104 项测试全部通过，另完成 16 项迁移与表目录复验。新版 Docker 的真实模型聊天接口查询 Products 总数为 16，与实库一致，登录、会话事件与结果内容接口均已通过验证。36 个历史产物均可读取，本地 admin 的 ID 和密码哈希与迁移前一致。

显式离线迁移入口如下。来源目录应含 agent.sqlite 和配套 content，目标账本需为空。

```powershell
.\.venv\Scripts\python.exe -m enterprise_agent.persistence.migrate_postgres --source <备份目录> --auth-source <本地application.sqlite备份> --archive-auth <旧Docker账号备份>
```

工具校验文件冲突，在临时副本上完成旧格式转换，原子导入元数据，记录来源哈希以支持重复运行。原来源文件保持完整。

## 备份与恢复

先停止 app，让数据库与内容文件处于一致时间点，再执行 PostgreSQL 自定义格式备份，同时备份 project_app_data。完成后启动 app。仅恢复数据库无法恢复文件卷中的产物正文。

```powershell
docker compose stop app
docker compose exec -T postgres pg_dump -U supply_admin -d supply_chain -Fc -f /tmp/project-backup.dump
docker cp project-postgres-1:/tmp/project-backup.dump <本机备份路径>
```

恢复时先保存当前状态，在单独数据库中用 pg_restore 检验备份，再与配套应用卷切换。跨环境恢复需先建立相同账号及 Schema 权限。应用卷的内容应由 UID/GID 10001 读写。

源码回退标签 before-postgres-only 指向 PostgreSQL 改造前的版本。旧版回退应使用同一时间点的旧格式数据库与文件备份。
