# 本轮统一 Task 调度

Request 管需求、Revision、验收和结果归属；Task 是执行单位。一个 Request 可以只有一个 Task，也可以有多个 Task，不强制一对一。

```text
Intent → 准备并提交本轮各 Request 的 spec 与计划
                  ↓
      Request A     Request B     Request C
      保存现场      保存现场      保存现场
                  ↓
       统一选择全部就绪 Task → 同一张 Task 图执行
                  ↓
       按所属 Request 汇总执行结果
        ├─需要重规划：生成新 Task，重新加入调度
        ├─需要用户输入/外部上游：保存挂起现场
        └─完成：各自验收、生成回复，最后按原提问顺序聚合
```

## 调度规则

- 只纳入当前 Turn 已成功准备的 Request；不会顺便执行历史会话里的其他待办。
- 就绪判断依据 Task.depends_on、input_bindings 和 Request 命名交付的实际产物。互不依赖的 Task 可以来自不同 Request。
- MAX_PARALLEL 是一次统一调度的总上限，选择时按 Request 轮流取就绪 Task，避免一个大型需求占满同一批名额。
- 同一批 Task 使用 LangGraph Send 并发执行。批次结束后重新查看依赖，继续调度。
- 某 Request 需要重规划或暂停时，只停止继续分派它的任务，其他独立任务仍能执行。重规划可能改变命名交付，因此下游在这段时间不能消费其暂时输出。
- 普通知识直接回答仍没有执行 Task；它在控制流程中处理，所引用的数据未就绪时等待。验收、回答和规划也没有包装成新的业务 Task。

## 实现位置

| 文件 | 作用 |
| --- | --- |
| stages/execute/turn_schedule.py | 推进各 Request 控制流程，汇集执行范围，分发结果并聚合回答 |
| flow/request_runs.py:Execution.advance | 推进到 execute 前的内部 checkpoint，或完成、用户可见挂起 |
| stages/execute/scheduler.py:Scheduler.run_many | 一个调度入口处理多个 Request 的 Task；旧 run 接口委托给它 |
| stages/execute/task_nodes.py | 跨 Request 选择就绪节点、版本校验、并发执行、停止需重规划的范围 |
| stages/execute/request_nodes.py | 消费本 Request 的调度结果，再执行验收、重规划或回答 |
| flow/checkpoints.py | 支持在指定节点前内部让出，沿用 PostgreSQL checkpoint |

执行范围包含 request_id、revision_id、execution_key、pause，仅作为调度状态传递，不新增业务数据库表。产物仍记录其真正的 request_id、revision_id、task_id。

## 恢复与一致性

内部让出保留原 Request thread ID 和节点名。execute 前的 checkpoint 不是“等待用户”：只有原 wait 节点的 interrupt 才表示用户可见挂起。

Task 结果继续使用原有 `request:{request_id}:{revision_id}:{run_id}:{executions}:schedule` 幂等范围。进程在任务结果保存后中断，恢复时从事件重建已完成任务结果，并复用原产物。每个 Task 的写入使用所属 Request 的版本检查，过时版本不能覆盖当前记录。

ResponseBlock 最终按 Intent 原始操作顺序输出，不按并发完成顺序输出。一个 Request 失败或需要澄清，不会撤销其他独立 Request 已完成的产物。

会话锁继续覆盖整轮，防止下一轮同时修改当前需求。锁由外层持有，同轮并行 Task 使用自己的数据库连接，因此不会被该锁串行化。

## 验证

`tests/execute/test_unified_schedule.py` 使用线程屏障验证真实并发，而非仅检查调用次数；覆盖并发总上限、跨需求依赖、动态重规划、暂时交付保护、失败隔离、过期版本、预览暂停和中断恢复。

历史图节点名、thread ID、业务 Schema 和接口路由保持不变。原单 Request 的恢复入口仍可独立继续执行。
