# 公开 Agent 架构与持久 Request 对照

核对日期为 2026-11-09。本文依据官方文档、A2A 1.0.0 规范与 Dify 公开源码，比较跨轮身份、执行身份和产物引用。本项目语义以本目录的《字段与数据流》为准。Dify 源码固定到提交 `2626e944ecd655c68cec9b9b0e8aaa21102262be`。闭源服务的结论限于其公开契约。

## 1. 对问题的直接回答

在本次查阅的 OpenAI、LangGraph/DeepAgents、Google ADK、Dify 与 A2A 中，跨轮延续普遍有稳定身份，但承担的职责不同。公开模型主要使用 Session、Conversation、Thread 保存上下文，使用 Turn、Run、Invocation 或有生命周期的 Task 标识执行。本次查阅未发现它们共同约定本项目这种 `Request + 完整 spec + RequestRevision + 命名交付` 的业务模型。该结论只覆盖这些资料，无法据此断言其他产品或闭源内部没有类似对象。各项目证据见下文。

本项目的 `Request` 可以理解为应用层的一项持续业务工作。一次用户输入能修改多个 Request，一项 Request 也能跨多轮修订。它在语义上覆盖了这些通用运行框架之上的业务需求管理。当前 `Task` 则是一项工作计划中的 Agent 执行步骤。上述解释来自本项目现有设计。[本项目字段与数据流](字段与数据流.md)

## 2. 先分开四种延续

| 行为 | 需要回答的问题 | 本项目对应对象 | 查阅资料中的代表机制 |
| --- | --- | --- | --- |
| 跨轮继续对话 | 下一轮从哪里获得上下文 | Session 与历史事件 | OpenAI Session/Conversation、LangGraph Thread、ADK Session、Dify Conversation |
| 恢复未完成执行 | 从哪一个执行位置继续 | 执行 Task 状态与重新规划策略 | LangGraph checkpoint、ADK invocation resume、A2A 非终态 Task |
| 引用既有结果 | 使用哪一份具体数据或文件 | Artifact、event、历史输出引用 | ADK artifact 版本、DeepAgents 文件后端、Dify run/node outputs、A2A Artifact |
| 修改同一业务要求 | 修改哪个对象，哪版规格生效，哪些结果需要更新 | Request、RequestRevision、当前 spec、交付绑定 | 所查通用机制需要应用层进一步定义这套业务语义 |

前三项机制的事实依据分别见下面的项目小节。第四项是对公开模型与本项目字段的对照判断。能够读取聊天历史、恢复运行或加载旧文件，仍需进一步定义完整要求、版本归属和交付有效性，才能形成本项目的业务要求版本管理。

## 3. OpenAI

- **会话与执行分层。** Agents API 的 Session 保留配置、对话与已保存工作。向同一个 Session 发送后续输入可以延续工作。Session 空闲时输入开启新 Turn，活跃时输入可以 steer 当前 Turn。这里跨轮保留的是 Session 身份，Turn 标识执行活动。[Agents API Sessions](https://developers.openai.com/api/docs/guides/agents-api/sessions)
- **SDK Run 有明确边界。** Agents SDK 文档将一次 SDK run 定义为一次应用层 turn，其内部可以包含多次模型调用、工具执行与 Agent 交接。跨轮可使用 session、`conversationId` 或 `previousResponseId` 维护上下文。[Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)
- **历史容器与业务规格分别建模。** Conversations API 提供持久对话容器，items 包括消息、工具调用与工具输出。基于这一公开结构，可以确认对话状态的保存方式。将其用于本项目时，`RequestRevision.spec` 的审计语义仍需应用定义。[Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)

## 4. LangGraph 与 DeepAgents

- **Thread 保存多次执行的累积状态。** LangGraph 用 `thread_id` 定位 checkpoint。Agent Server 的 Run 是一次 assistant invocation，可有状态或无状态，多次 Run 可作用于同一 Thread。因此 Thread 与 Run 分别承担状态容器和执行身份。[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) [Runs](https://docs.langchain.com/langsmith/runs)
- **恢复执行有独立调用语义。** 在同一 Thread 上提交新输入会从入口开展新工作。失败恢复、回答 interrupt、从历史 checkpoint 重放有各自入口。checkpoint 保存执行状态，按 super-step 形成快照，节点级 task writes 用于故障恢复。这些快照的粒度与本项目的完整业务规格修订不同。[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- **跨工作数据可独立持久化。** Checkpointer 面向单 Thread，Store 面向跨 Thread 的应用数据。DeepAgents 默认文件后端存于 LangGraph state，通过 checkpointer 在同 Thread 的多轮之间保留，也可选择持久 Store 或本地文件后端。文件的可复用范围取决于后端配置。[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) [DeepAgents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- **执行步骤可以委派。** DeepAgents 用 `task` 工具委派子 Agent，子 Agent 可隔离上下文并返回结果。这提供计划执行能力，公开接口没有因此规定统一的业务 `RequestSpec`。[DeepAgents Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)

## 5. Google ADK

- **Session 是会话身份。** `SessionService` 管理 Session，Runner 读取其 state 和 events，保存新 Event 并更新状态，然后进入下一轮。`InvocationContext` 另有 `invocation_id`、当前 agent 与初始用户输入，能够区分一次调用与其所属会话。[Sessions](https://adk.dev/sessions/session/) [Agent context](https://adk.dev/context/)
- **恢复与回退分别提供。** 配置 resumability 后，可使用历史中的 `invocation_id` 恢复中断工作流。Rewind 则按指定 invocation 回退 Session，并撤销该次及其后调用带来的受管变化。文档中的 request 在这里可指一次 invocation。[Resume Agents](https://adk.dev/runtime/resume/) [Rewind sessions](https://adk.dev/sessions/session/rewind/)
- **Artifact 有独立版本。** ArtifactService 按文件名保存产物并产生版本号，加载时可以选择最新或指定版本，事件通过 `artifact_delta` 关联产物变化。这与本项目引用实际产物、保留历史版本的做法存在可对照之处。[Artifacts](https://adk.dev/artifacts/)
- **业务要求需要应用定义。** ADK 暴露可扩展 state、events 和 artifacts。上述公开模型足以承载应用自定义需求信息，但本次所查文档没有统一规定 `description/parameters/inputs/outputs` 的业务要求快照，或其修订与执行失效规则。[Agent context](https://adk.dev/context/) [Sessions](https://adk.dev/sessions/session/)

## 6. Dify

- **持久会话和单轮记录分开。** 公开源码有 `Conversation` 和 `Message` 两个模型。Message 保存 `conversation_id`、query、answer、inputs，并可带 `workflow_run_id`，显式连接会话、交互记录与工作流执行。[model.py，Conversation 与 Message](https://github.com/langgenius/dify/blob/2626e944ecd655c68cec9b9b0e8aaa21102262be/api/models/model.py#L1045-L1086) [Message 字段](https://github.com/langgenius/dify/blob/2626e944ecd655c68cec9b9b0e8aaa21102262be/api/models/model.py#L1363-L1432)
- **工作流定义和运行分开。** `Workflow` 保存图配置及定义版本。`WorkflowRun` 保存一次执行的 ID、workflow_id、version、graph、inputs、status、outputs 和时间信息。该 version 对应工作流配置快照，不能仅因存在 version 字段就解释成用户业务要求的修订。[Workflow 定义](https://github.com/langgenius/dify/blob/2626e944ecd655c68cec9b9b0e8aaa21102262be/api/models/workflow.py#L158-L186) [WorkflowRun](https://github.com/langgenius/dify/blob/2626e944ecd655c68cec9b9b0e8aaa21102262be/api/models/workflow.py#L721-L805)
- **节点执行有自己的记录。** `WorkflowNodeExecutionModel` 关联 `workflow_run_id` 与 `node_id`，保留 inputs、process_data、outputs 和 status。Run 输出与节点输出可独立追踪，这与本项目分离整体工作和内部执行步骤的方向相符。[节点执行模型](https://github.com/langgenius/dify/blob/2626e944ecd655c68cec9b9b0e8aaa21102262be/api/models/workflow.py#L886-L931)
- **历史执行与暂停可留存。** `WorkflowRun` 还关联 `WorkflowPause`。此处可以确认持久执行状态与暂停记录的分层。本次检查的这些模型未规定本项目式的跨轮业务 Request、完整规格修订和 `request/id/output` 引用契约，这一判断限定于所检查的公开模型。[WorkflowRun 与暂停关联](https://github.com/langgenius/dify/blob/2626e944ecd655c68cec9b9b0e8aaa21102262be/api/models/workflow.py#L770-L805)

## 7. A2A 1.0.0

- **Task 是有生命周期的工作单位。** 规范明确 Task 有 ID、status、artifacts 和可选 history。它可经历多轮交互，`input-required` 后可用同一 `taskId` 补充输入。它比单次模型调用更接近一项可追踪工作。[规范 3.4 与 4.1.1](https://a2a-protocol.org/v1.0.0/specification/#34-multi-turn-interactions)
- **跨任务上下文另有身份。** `contextId` 分组相关 Task 和 Message。消息可以用 `referenceTaskIds` 指向相关工作，也可在原 context 中创建新 Task。Artifact 有 task 内唯一的 `artifactId`，内容由 Parts 承载。[规范 3.4 与 4.1](https://a2a-protocol.org/v1.0.0/specification/#341-context-identifier-semantics)
- **终态约束决定后续修改方式。** 已 completed、failed、canceled 或 rejected 的 Task 进入终态，不能再接收消息。后续 refinement 可以创建关联的新 Task。因此把同一业务对象持续修订为多个 RequestRevision，是本项目额外定义的生命周期语义。[规范 Send Message 与 TaskState](https://a2a-protocol.org/v1.0.0/specification/#311-send-message) [Life of a Task](https://a2a-protocol.org/v1.0.0/topics/life-of-a-task/)
- **规范没有统一业务 spec。** 核心 Task 字段为身份、上下文、状态、产物、历史和 metadata，协议允许扩展。`RequestSpec` 的字段与完整修订规则需要应用自行约定。[规范 4.1.1 Task](https://a2a-protocol.org/v1.0.0/specification/#411-task)

## 8. 对本项目设计的解释

以下是基于上述对照的设计判断。

1. **保留持久 Request 有明确业务作用。** 它让“把去年改成今年”“给刚才的统计再加一个图”定位到同一工作，并形成新的完整要求版本。Session 则容纳多个这样的工作。[本项目字段与数据流](字段与数据流.md)
2. **RequestRevision 管理要求，执行记录管理运行。** checkpoint、invocation history、workflow run version 分别服务运行恢复、交互历史或工作流配置。项目需要按自身规则把“当时完整要求”与“按此要求执行的步骤、最终交付”关联起来。
3. **逻辑输出与历史产物继续区分。** `source.kind=request` 加输出名表达业务依赖，实际消费时再绑定具体 Artifact。历史复用通过 Artifact 或 event 固定来源。这样可以分别表达“上游工作当前交付”和“此前那份结果”。[本项目字段与数据流](字段与数据流.md)
4. **名称可以按语义解释。** 文档可将 Request 标注为“业务工作/需求”，将当前 Task 标注为“执行步骤”。当前 Task 在这里承担执行步骤的职责。一次完整执行的身份目前由 turn_id、request_id、revision_id 与 plan_version 等信息共同表达。与独立 Run/Invocation 模型相比，同一规格多次重跑的归属还可以进一步集中表达。这一项是本项目与公开实现的设计对照判断。

可直接用于设计说明的表述为“本系统在会话层之下增加持久业务 Request，用 RequestRevision 记录完整要求，用执行 Task 分解工作，用 Artifact 记录真实结果。它借鉴公开 Agent 系统对会话、执行和产物的分层，并为跨轮修改需求与跨请求输出依赖定义应用层语义。”
