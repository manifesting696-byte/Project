# 企业数据智能问析系统

当前实现采用包含完整 spec 的 Request 读取对象、不可覆盖的 RequestRevision、执行 Task 和可追溯 Artifact。当前规格由版本表统一加载，实际交付通过执行记录按需解析。Supervisor 负责意图、一次生成完整要求与初始计划、动态重规划、验收与回答，四个专业 Agent 负责语义、查询、分析和展示。

## 启动

在 PowerShell 中执行下列命令。

```powershell
.\start-local.ps1
```

浏览器打开 http://127.0.0.1:8000。模型使用 .env 或环境中的 LLM_URL、LLM_API_KEY、LLM_MODEL。首次安装设置 INITIAL_ADMIN_PASSWORD，已有账号继续使用。

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
- pipeline/checkpoints.py 将运行现场保存到 runtime/langgraph.sqlite。
- application/responses.py 将文字草稿保存为 Artifact，组装只含产物引用的 Response 块。前端据此展示正文和结果卡片。
- persistence/migration.py 自动备份并事务迁移旧账本。
- persistence/unified_migration.py 统一历史 Artifact 类型和回答结构，保留原数据与 SQL。
- agents/prompt.py 集中说明角色和字段取值。

## 验证

```powershell
.\.venv\Scripts\python.exe evaluate.py
.\.venv\Scripts\python.exe evaluate.py --live-only
```

第二条命令通过真实模型查询 Products 产品数，并与业务快照核对。测试覆盖并行调度、重启恢复、产物写入后中断、计划与回复保存后的重放。

## 原项目记录

main 分支提交 fdde3c2 和标签 baseline-before-request-refactor 保存改造前源码。新实现位于 refactor/request-task-model 分支。.env、虚拟环境和 runtime 被 Git 排除。旧账本首次迁移前会生成 runtime/agent.before-request-model.sqlite。统一 Artifact/Response 升级前会另存 runtime/agent.before-unified-artifacts.sqlite。

旧设计材料保存在 docs/第一版设计 和 docs/第二版设计。
