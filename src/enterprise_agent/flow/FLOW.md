# 主流程一页纸

入口：`POST /ask` → `server/routes/ask.py` → `Application.ask`（会话锁）→ `Turns.run`。
先读三张 `*_graph.py`，它们只装配节点。节点实现在 `stages/`，checkpoint 调用与恢复入口在 `turn_runs.py`、`request_runs.py`。

```text
Turn：START → intent ─成功→ prepare → commit → process ─更多操作→ process
               │失败                    │无操作      │全部处理完
               └────────────→ respond ←┴───────────┘ → END
        prepare/commit 每项隔离：失败或澄清拦住依赖方，其他项仍执行

Request：START → begin ─就绪→ execute → check → finalize → answer → END
                  │           │         │         │
                  │           ├需要决策─┴需重规划→ replan ─→ execute
                  │           │                     超预算→ finalize(partial)
                  └需等待─────┴批暂停/上游未交付────┐
                                 finalize需澄清────┴→ waiting_answer → wait(interrupt)
        resume：澄清/本批需决策→ replan；其他→ execute

Task：START → dispatch ─就绪→ Send(execute_task × N) → collect ─继续→ dispatch
                  │无就绪                                └暂停/需决策→ END
                  ├刚传播依赖失效→ dispatch
                  └其他→ END（返回 waiting/blocked/结果）
```

| 阶段 | 实现入口与数据流 |
| --- | --- |
| 意图 | `intent/{graph_node,inference,prompt,schema,normalize,retry}.py`：Query → Intent → 全局 ID、有序操作 → intent_resolved |
| 准备 | `prepare/{graph_node,preparation,schema,prompt,inference,validate,plan,commit}.py`：操作 + 旧版本 → 完整 spec/plan → 原子保存 Revision、Task、事件；requests.py 管版本和依赖失效，bindings.py 解析逻辑交付绑定 |
| 执行 | `execute/{graph_node,operations,request_nodes,task_nodes,scheduler}.py`：Request → Task DAG → 专业 Agent → 授权工具 → Artifact；agents/ 和 tools/ 按职责拆分 |
| 验收 | `verify/{graph_node,inference,prompt,schema,retry,validate,evidence}.py`：实际交付 + 模型判定 → 完整性、真实证据、内容类型的代码裁决 |
| 回答 | `answer/{graph_node,inference,prompt,retry,materialize,publication}.py`：草稿/已有结果 → Artifact → 有序 ResponseBlock → assistant_response；none 直接回答时没有执行 Task |

| 失败或等待 | 处理及用户看到的结果 |
| --- | --- |
| RepairableError(hint, code) | 意图、准备、计划修复、验收、回答校验各有一次修复预算；再次失败发布本轮/本项失败文字 |
| PermissionDenied | 不重试、不回喂权限细节；对外只显示“无权访问所请求的资源” |
| VersionConflict | 不重试旧版本；HTTP 409 或本项失败，提示基于当前版本重新操作 |
| UpstreamPending | 调度入口转 waiting_answer(upstream)，缺少交付不当作模型格式错误 |
| preview / batch | 已保存计划 / 已保存本批结果，等待继续 |
| clarification | 先保存追问再挂起；带 answer 的 resume 创建新 Turn + Revision |
| 进程中断 | 同一 thread 恢复原节点；workflow_key 复用已保存的计划、操作和回答 |

冻结契约：`turn:{session_id}:{turn_id}`、`request:{request_id}:{revision_id}`；
`{turn_id}:intent`、`:commit:{request_id}`、`:operation:{request_id}`、`:direct:{request_id}`、`:response`；
`request:{request_id}:{revision_id}:{run_id}:{executions}:{phase}`。
三张图的全部节点名、现有 event_type、`runtime/content/result_xxx/`、`agent` / `agent_checkpoints` / `identity` Schema 均保持不变。
