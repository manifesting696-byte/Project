# 主流程一页纸

读这一页就能知道一次提问发生了什么、失败停在哪里、要改某件事该去哪个目录。
节点名、`thread_id`、`workflow_key`、事件类型、产物路径都是**冻结契约**，见第 6 节。

## 1. 一次提问的完整路径

```
POST /ask                                    server/routes/ask.py
  → Application.ask                          flow/application.py
    → 会话锁 + TurnGraph.invoke              flow/turn_graph.py
        intent ~ prepare ~ commit ~ process ⇄ ~ respond
      ↑ 逐项 Request
      ↳ RequestGraph.invoke                  flow/request_graph.py
           begin ~ execute(调度子图) ~ check ⇄ replan ~ finalize ~ answer
                          ↳ TaskGraph            flow/task_graph.py
                               dispatch ~ Send(execute_task ×n) ~ collect
                                   ↳ SpecialistAgent.execute
                                        stages/execute/agents/
```

## 2. 三张图

### 主图 `flow/turn_graph.py`

| 节点 | 做什么 | 代码 |
|---|---|---|
| `intent` | 识别本轮全部独立要求；校验失败把原因回喂模型重试 1 次 | 方法 `intent` |
| `prepare` | 一次调用产出完整 `RequestSpec` + 初始 `Plan` | 方法 `prepare` |
| `commit` | 单事务落 Revision + 失效标记 + 初始 Task + 事件 | 方法 `commit` |
| `process` | 按依赖顺序处理每个 Request（缓存键 `{turn}:operation:{req}`） | 方法 `process` |
| `respond` | 纯装配：按 operation 顺序输出 blocks，不调模型 | 方法 `respond` |

### Request 执行图 `flow/request_graph.py`

| 节点 | 做什么 |
|---|---|
| `begin` | 上游未交付→等；`preview=True`→等确认；否则进 `execute` |
| `execute` | 调调度子图跑一批 Task；有 `replan_after` 或暂停则转 `replan`/等 |
| `check` | **验收器**：模型逐项判定 + 代码硬校验（名称集合、证据指向、类型一致） |
| `replan` | 让 Supervisor 追加/取消 Task，沿用当前 Revision |
| `finalize` | 三种收尾：`completed` / `partial`（预算用完）/ `needs_clarification` |
| `answer` | 生成或复用回复，绑定文字交付 |
| `waiting_answer` → `wait` | 先把话写进 Artifact，再 `interrupt()` 挂起 |

### 调度子图 `flow/task_graph.py`

`dispatch` 依据 `depends_on` 与 Request 命名输入选出就绪 Task，`Send` 并发执行
（并发上限 `MAX_PARALLEL`），`collect` 用 reducer 按 `task_id` 合并。

## 3. 五个阶段与它们的代码

| 阶段 | 目录 | 关键文件 | 产出 |
|---|---|---|---|
| 意图 | `stages/intent/` | `schema.py` 契约、`normalize.py`？→ 目前在 `stages/prepare/requests.py` 的 `normalize` | `Intent` + 有序 operations |
| 准备 | `stages/prepare/` | `requests.py`（聚合根：ID 归一/规格校验/版本/绑定）、`preparation.py`（提议+校验+提交）、`plan.py`（DAG 校验） | `RequestRevision` + `Task[]` |
| 执行 | `stages/execute/` | `agents/{supervisor,specialists,base,capabilities}.py`、`toolbox.py`（工具与授权） | `Artifact` |
| 验收 | `stages/verify/`（提示词在 `prompt/supervisor.py`） | 判定逻辑在 `flow/request_graph.py:check`，硬校验在 `flow/application.py:validate_completion` | `Completion` |
| 回答 | `stages/answer/` | `responses.py`（文稿→Artifact→Response 块） | `Response` |

提示词全部集中在 `prompt/`：`supervisor.py`（6 段）、`specialists.py`（4 段），
按阶段分开，改口径规则只动这里。

## 4. 四种等待（`wait_kind`）

| 值 | 触发 | 用户看到 | 恢复方式 |
|---|---|---|---|
| `preview` | `POST /bind` | 已保存需求与计划，等待继续 | `POST /requests/{id}/resume`（不带 answer） |
| `batch` | `pause_after_batch=True` | 本批已保存，可以继续 | 同上 |
| `upstream` | 依赖的 Request 还没交付 | 请先补全上游需求 | 上游完成后再 resume |
| `clarification` | 验收要求澄清 | 模型的追问正文 | resume **带** `answer` → 新 Revision |

`resume` 带 `answer` 时不恢复旧图，而是以 `forced_operation=update_task` 开新一轮，
因此产生新的 Revision，旧执行现场按版本校验自动作废。

## 5. 失败停在哪

| 位置 | 表现 | 影响范围 |
|---|---|---|
| `intent` 抛非 ValueError | 整轮失败，一条文字 | 整轮 |
| `normalize` ValueError | 带反馈重试 1 次，再失败则整轮 | 整轮 |
| `prepare` 每个 Request 独立 try | 该 Request 记入 `errors` | 单项 |
| `commit` 每个 Request 独立事务 | 该 Request 失败，下游被拦住 | 单项 + 下游 |
| `process` 任意异常 | 该项 `failed` + 一条“本项未完成” | 单项 |
| `check` 校验失败 | 同上 | 单项 |
| `finalize` 预算用完 | `partial` + 明确说明缺口 | 单项 |
| `wait` | 挂起，不失败 | 单项 |

## 6. 冻结契约（重构不得改动）

| 契约 | 取值 |
|---|---|
| 主图 thread | `turn:{session_id}:{turn_id}` |
| Request 图 thread | `request:{request_id}:{revision_id}` |
| 工作流幂等键 | `{turn}:intent`、`{turn}:commit:{req}`、`{turn}:operation:{req}`、`{turn}:response`、`{turn}:direct:{req}`、`{request}:{rev}:{run}:{n}:{phase}` |
| 图节点名 | `intent/prepare/commit/process/respond`、`begin/execute/check/replan/finalize/answer/waiting_answer/wait`、`dispatch/execute_task/collect` |
| 事件类型 | `user_message/intent_resolved/request_revised/plan_updated/agent_output/tool_call/tool_result/assistant_response` |
| 产物正文 | `runtime/content/result_xxx/` |
| Schema | `agent`、`agent_checkpoints`、`identity`、`supply_chain`、`analysis` |

## 7. 和最初方案的三处偏离

1. `Requests` 聚合根放在 `stages/prepare/requests.py`：除交付解析外的操作都服务于准备，
   而交付解析依赖同一份状态，拆开会重复校验。
2. 节点方法仍在 `flow/*_graph.py` 里（尚未拆成 `stages/<阶段>/graph_node.py`）：图文件已是“节点名 → 一行方法”的可读形态；验收硬校验已独立到 `stages/verify/validate.py`。
3. 工具聚合在 `stages/execute/toolbox.py` 一个类里（`SpecialistToolbox`），没有按角色拆成
   5 个文件——它是一个共享授权边界的整体，拆开会把不变量打散。

