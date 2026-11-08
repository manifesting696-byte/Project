# 企业数据智能问析 Agent：Context Manager 五阶段装配设计方案

> **文档性质**：架构设计讨论稿  
> **适用范围**：已确定的 `Query → 初始 Context → Unified Supervisor（Intent Parsing）→ Intent IR → 规划 Context → Unified Supervisor（Planning）→ Subtask DAG → Scheduler → Subtask Context → 专业 Agent → Artifact / Subtask 更新 → 重规划 Context → Supervisor 判断 → 回答 Context → 最终回答` 流程。  
> **设计前提**：Intent Parsing 与 Supervisor 在物理实现上统一，逻辑职责分离；Session Log、Task Store、Artifact Store、Summary Store 仍是四个既定存储模块。本文只规定 **Context 在哪些阶段装配、从哪里读取以及装配什么**；具体检索、Token 阈值、压缩、卸载与工具实现后续讨论。  
> **示例说明**：所有供应商数据、Artifact ID 和统计发现都是示意，不是真实查询结果。

## 一、总体思路

Context Manager 采用 **五阶段按需装配**，而不是在用户发送 Query 后，把四个 Store 的所有数据一次性塞进 Prompt。

五个阶段使用同一个 Context Manager，但面向不同的决策与执行角色，形成不同的运行时 Context 视图。

| 阶段 | 触发时机 | 目标 | 使用方 |
| --- | --- | --- | --- |
| ① 初始 Context | 收到用户 Query 后、Intent 解析前 | 理解历史对话、识别用户引用和任务关系 | Intent Parsing |
| ② 规划 Context | 已得到 Intent IR、准备规划时 | 合并完整需求，识别已有工作与可复用结果，规划 Subtask DAG | Supervisor Planning |
| ③ Subtask Context | Scheduler 调度某个 Subtask 执行前 | 提供当前 Subtask 的目标、条件和真实输入引用 | 专业 Agent |
| ④ 重规划 Context | 执行产生中间结果并需要 Supervisor 决策时 | 根据新发现、任务进度和未完成需求继续规划或结束 | Supervisor Replanning |
| ⑤ 回答 Context | Supervisor 判断可以结束后 | 汇总用户要求、交付产物和可靠证据，形成完整回答 | 回答生成模块 |

**重要原则**：五个装配阶段不代表五次固定的模型调用，也不代表五套新的 Store。只有需要规划判断、指代消歧或深入读取时才做相应的额外工作。

### 1.1 四个 Store 的权威职责

| Store | 保存什么 | 在 Context 中主要提供什么 |
| --- | --- | --- |
| **Session Log** | 原始用户消息、Intent IR 事件、Agent/工具执行事件、最终用户可见回答 | 对话历史、最近回答、当前轮原始需求、历史引用 |
| **Task Store** | Task、Task Revision（完整需求快照）、Subtask DAG、依赖与执行状态 | 任务目标、完整条件、当前计划、已完成/待执行工作 |
| **Artifact Store** | 查询数据、分析结果、图表、报告，及其元数据、完整内容位置和来源 | 可复用业务结果、数据概览、可定位的真实内容、证据 |
| **Summary Store** | 已压缩的会话历史及重要引用 | 远期对话背景，不替代 Task 或 Artifact 的权威状态 |

### 1.2 全局数据约定

- Artifact 统一命名为 `result_001`、`result_002`、`result_003` 等；通过 `type` 区分 `query_result`、`analysis_result`、`chart`、`report`，不再用 `analysis_001`、`chart_001` 等前缀混用。
- 用户输入的 Query 保存在 `SessionEvent(event_type="user_message")` 中；本轮解析结果保存在 `intent_resolved` 中；跨轮合并后的完整需求保存在当前 `TaskRevision` 中。
- Artifact Store 遵循 **Metadata + Content Reference + Full Content**：模型优先看到可检索概览，需要精确信息时通过工具按需读取 `content_ref` 指向的数据。
- 下文 JSON 是 **运行时装配内容示例**，不表示需要为每阶段新增一张表，也不要求逐字段照搬为数据库 Schema。
- Context Manager 只负责信息查找、读取和装配；业务推理、任务规划由 Supervisor/专业 Agent 完成。

---

## 二、① 初始 Context 装配：Query → Intent Parsing

### 2.1 目标

帮助 Intent Parser 判断当前 Query 属于新任务、继续原任务、修改条件、引用历史结果，还是独立知识问答；尤其解决“这五家”“上次那张图”“刚才第二点”等指代。

**此时还没有完成 Intent 识别，因此不能直接将 `last_active_task_id` 当作当前 Query 的归属任务。** 它只是一个候选线索。

### 2.2 四个 Store 需要读取什么

| Store | 关键字段或内容 | 装配目的 |
| --- | --- | --- |
| Session Log | `sessions.last_active_task_id`、`last_response_event_id`；最近若干条 `user_message.payload.content` 和 `assistant_response.payload.blocks` | 理解最近对话、识别历史指代 |
| Task Store | 相关候选 Task 的 `task_id`、`goal`、`status` | 判断当前 Query 可能关联的历史 Task |
| Artifact Store | 相关产物的 `artifact_id`、`type`、`title`、`summary`；必要时数据规模、列信息和其他概览 | 知道历史产物代表什么，定位被引用的结果 |
| Summary Store | `summary.conversation_brief`、`summary.key_refs`（历史较长或确有必要时） | 补充更早的会话背景 |

不应默认加载完整 Subtask DAG、所有 Artifact 明细以及完整历史会话；但也**不能将“只加载索引”写成绝对限制**。若概览不足以识别引用，应允许按需查询具体成员或原始回答。

### 2.3 示例

上一轮用户说：“查询2025年上半年交付最差的五家供应商。”本轮用户说：“那这五家供应商的来料质量怎么样？”

```json
{
  "query": "那这五家供应商的来料质量怎么样？",
  "recent_messages": [
    {"role": "user", "content": "查询2025年上半年交付最差的五家供应商"},
    {"role": "assistant", "content": "已筛选出交付表现最差的五家供应商"}
  ],
  "candidate_tasks": [
    {"task_id": "task_001", "goal": "分析供应商交付表现"}
  ],
  "candidate_artifacts": [
    {
      "artifact_id": "result_001",
      "type": "query_result",
      "summary": "2025年上半年交付准时率最低的五家供应商"
    }
  ]
}
```

Intent Parser 可以据此将“这五家”关联到 `result_001`；具体供应商 ID 可在需要使用时从 Artifact 的真实数据中读取。

**本阶段重点：理解用户与历史结果的关系，不预先读取全部业务数据。**

---

## 三、② 规划 Context 装配：Intent IR → Supervisor Planning

### 3.1 目标

在 Intent IR 已形成的前提下，Supervisor 需要知道用户**完整要求**、当前 Task 已做到哪里、已有结果是否能复用，以及如何拆分 Subtask 和安排依赖。

本轮 Intent IR 与 Task Revision 的职责不同：

- **Intent IR**：当前 Turn 提出或修改了哪些需求。
- **Task Revision**：合并此前多轮条件后的完整当前需求快照。

### 3.2 四个 Store 需要读取什么

| Store | 关键字段或内容 | 装配目的 |
| --- | --- | --- |
| Session Log | 当前轮 `intent_resolved.payload.intent`，必要时 `user_message.payload.content` | 获得本轮真实需求变化及原话 |
| Task Store | `Task.goal`、`current_revision_id`、`TaskRevision.requirements`、`global_constraints`；有关 `subtasks` 的目标、状态、依赖、输出 | 获得完整需求和已执行工作 |
| Artifact Store | 与需求相关的结果 `artifact_id`、`type`、`summary`、`source_refs`、必要的 Payload 概览 | 判断已有结果能否复用，是否需要重新执行 |
| Summary Store | 一般不需要；历史背景不完整时补充 | 辅助理解远期任务背景 |

规划所需的 Agent/Tool 可用能力和业务语义注册信息，来自既有能力注册与语义服务，并不属于四个 Store。

### 3.3 示例

用户第二轮提出：“再分析这五家的来料质量，并生成柱状图。”

```json
{
  "task": {
    "task_id": "task_001",
    "goal": "分析供应商交付与来料质量",
    "global_constraints": {"period": "2025-H1"}
  },
  "requirements": [
    {"request_id": "req_001", "description": "筛选交付准时率最低的五家供应商"},
    {"request_id": "req_002", "description": "分析这五家的来料质量"},
    {"request_id": "req_003", "description": "生成柱状图"}
  ],
  "existing_results": [
    {
      "artifact_id": "result_001",
      "type": "query_result",
      "summary": "已得到五家供应商的交付排名"
    }
  ]
}
```

Supervisor 可以据此复用 `result_001`，规划后续质量查询与图表 Subtask，并依赖真实结果配置串行/并行关系。

**本阶段重点：完整需求 + 当前进度 + 可复用结果 + 可用能力。**

---

## 四、③ Subtask Context 装配：Scheduler → 专业 Agent

### 4.1 目标

让 Query Agent、Analysis Agent、Presentation Agent 等只获得完成**当前 Subtask** 所需的信息，而不是整个会话或全部 DAG 结果。

### 4.2 四个 Store 需要读取什么

| Store | 关键字段或内容 | 装配目的 |
| --- | --- | --- |
| Session Log | 默认无需；必要时按原始 Query 补充歧义信息 | 防止错误解释用户原意 |
| Task Store | 当前 `subtask_id`、`objective`、`assigned_agent`、`input_bindings`、`expected_outputs`、相关的 Task Revision 约束 | 确定任务目标、输入输出契约与业务条件 |
| Artifact Store | 上游 Artifact 的 `artifact_id`、`type`、`summary`、`content_ref`、数据概况及执行必需的数据切片 | 获取真实输入；必要时通过工具读取/筛选/聚合 |
| Summary Store | 通常不需要 | 避免不相关历史占用上下文 |

Scheduler 先根据上游 Subtask 的 `produced_outputs` 解析逻辑 `input_bindings`，定位实际 Artifact。**定位成功并不意味着把整个 Artifact 全量发送给模型。**

### 4.3 示例

```json
{
  "subtask_id": "subtask_003",
  "objective": "比较五家供应商的来料拒收率",
  "constraints": {"period": "2025-H1"},
  "inputs": [
    {
      "slot": "data",
      "artifact_id": "result_002",
      "type": "query_result",
      "summary": "五家供应商的来料拒收率数据",
      "row_count": 50000,
      "columns": ["supplier_id", "inspection_date", "rejected_quantity"]
    }
  ]
}
```

此处 50,000 行是假设数据量。对于只有数行的小结果，可以直接提供精确值；对于大结果，应提供可定位的引用与数据结构，允许专业 Agent 调用 Artifact 工具进行筛选、统计、聚合或按需读取。

Context 可以分为：

1. **执行前固定部分**：Subtask 目标、适用约束、真实输入引用、预期输出。
2. **执行中按需部分**：专业 Agent 为完成任务请求的详细数据、计算结果或分析证据。

**本阶段重点：当前 Subtask 目标 + 输入引用 + 必要的真实数据或访问能力。**

---

## 五、④ 重规划 Context 装配：Artifact / Subtask 更新 → Supervisor 判断

### 5.1 目标

当某一批 Subtask 产生新结果、需要继续决策时，Supervisor 检查当前任务进展、新发现、尚未满足的用户要求，决定追加 Subtask、继续现有 DAG 或进入最终回答。

不是每完成一个 Subtask 都必须再次调用 LLM。**Scheduler 可以直接运行已经准备好的下游节点；只有需要基于中间结果决策时才进行重规划。**

### 5.2 四个 Store 需要读取什么

| Store | 关键字段或内容 | 装配目的 |
| --- | --- | --- |
| Session Log | 当前轮 Intent IR、必要的任务变更事件 | 防止偏离本轮用户意图 |
| Task Store | 当前 Task Revision；有关 Subtask 的 `status`、`depends_on`、`produced_outputs`；尚未完成的用户需求 | 获得准确的执行进度与待办 |
| Artifact Store | 新产物的 `artifact_id`、`type`、`summary`、`payload` 中的关键发现、`source_refs` | 判断新结果是否支持扩展分析 |
| Summary Store | 通常不需要 | 当前 Task 状态以 Task Store 为准 |

### 5.3 示例

Analysis Agent 得到 `result_003`：

```json
{
  "artifact_id": "result_003",
  "type": "analysis_result",
  "summary": "发现部分供应商的拒收率在第二季度上升",
  "content_ref": "storage://artifacts/result_003/analysis.md",
  "payload": {
    "findings": [
      {
        "text": "供应商A第二季度拒收率上升",
        "refs": [{"kind": "artifact", "id": "result_002"}]
      }
    ],
    "suggestions": ["可以进一步按材料类别检查质量变化"]
  }
}
```

重规划 Context 可组织为：

```json
{
  "task_id": "task_001",
  "goal": "分析供应商交付与来料质量",
  "plan_version": 1,
  "subtask_progress": [
    {"subtask_id": "subtask_001", "status": "completed"},
    {"subtask_id": "subtask_002", "status": "completed"},
    {"subtask_id": "subtask_003", "status": "completed"}
  ],
  "new_results": [
    {
      "artifact_id": "result_003",
      "summary": "发现部分供应商第二季度拒收率上升",
      "suggestions": ["进一步按材料类别检查质量变化"]
    }
  ],
  "unfulfilled_requests": []
}
```

Supervisor 根据用户目标判断是否值得展开下一步。Artifact 中的 `suggestions` 只是建议，不能自动升级成必须执行的 Subtask；用户仅要求排名与图表时，不应无限追加额外分析。

**本阶段重点：当前任务进度 + 最新结果 + 尚未满足的需求。**

---

## 六、⑤ 回答 Context 装配：Supervisor 判定完成 → 回答生成

### 6.1 目标

把用户的原始要求、各 Request 实际完成情况、查询结果、分析结论、图表/报告和证据汇总给回答生成模块，确保答复不漏项、不编造数值。

### 6.2 四个 Store 需要读取什么

| Store | 关键字段或内容 | 装配目的 |
| --- | --- | --- |
| Session Log | 本轮 `user_message.payload.content`；必要的历史对话语境 | 对齐用户原始表达 |
| Task Store | `TaskRevision.requirements`、相关 Subtask 状态与输出、需求完成度 | 检查全部要求是否交付 |
| Artifact Store | 已交付结果的 `summary`、必要 Payload、真实数据、图表内容及 `source_refs` | 保证回答中的数值、结论和展示有证据 |
| Summary Store | 通常不需要 | 避免冗余历史干扰回答 |

### 6.3 示例

```json
{
  "query": "查询交付最差的五家供应商，分析来料质量，并生成柱状图",
  "requirements": [
    {"request_id": "req_001", "description": "筛选交付最差的五家供应商", "status": "completed"},
    {"request_id": "req_002", "description": "分析五家供应商的来料质量", "status": "completed"},
    {"request_id": "req_003", "description": "生成柱状图", "status": "completed"}
  ],
  "results": [
    {"artifact_id": "result_001", "type": "query_result", "summary": "已得到五家供应商的交付排名"},
    {"artifact_id": "result_003", "type": "analysis_result", "summary": "已完成来料质量比较"},
    {"artifact_id": "result_004", "type": "chart", "summary": "五家供应商的来料拒收率柱状图"}
  ]
}
```

以上只是基础概览。真正回答时，**还必须按需要取得支撑结论的具体数值、分析证据和可呈现的图表**，不能根据 `summary` 虚构数据。

最终回答仍保存到 Session Log 的 `assistant_response.payload.blocks`，例如：

```json
{
  "event_type": "assistant_response",
  "payload": {
    "blocks": [
      {
        "block_id": "block_001",
        "kind": "text",
        "content": "供应商交付表现与来料质量分析结果……",
        "refs": [
          {"kind": "artifact", "id": "result_001"},
          {"kind": "artifact", "id": "result_003"}
        ]
      },
      {
        "block_id": "block_002",
        "kind": "chart",
        "content": null,
        "refs": [{"kind": "artifact", "id": "result_004"}]
      }
    ]
  }
}
```

`block_id` 用于后续引用“刚才第二点”，但用户可见内容只在相应 Event 中保存一次。

**本阶段重点：完整交付用户需求 + 精确结果 + 可检查的证据。**

---

## 七、四个 Store 在五阶段的使用矩阵

| Store | ① 初始 | ② 规划 | ③ Subtask 执行 | ④ 重规划 | ⑤ 回答 |
| --- | --- | --- | --- | --- | --- |
| **Session Log** | 主要：近期对话与指代 | 当前 Intent 事件 | 默认无需；必要时取原话 | 少量需求事件 | 当前 Query 和必要语境 |
| **Task Store** | 候选 Task 概览 | 完整 Revision / 已有 DAG | 当前 Subtask 和约束 | 相关执行进度 / 未满足需求 | 完成度和输出映射 |
| **Artifact Store** | 候选结果概览 | 可复用结果概览 | 真实输入与按需数据 | 最新发现与来源 | 结果、数值、图表、证据 |
| **Summary Store** | 长历史按需使用 | 偶尔补充 | 通常不需要 | 通常不需要 | 通常不需要 |

其中 Artifact Store 的读取目的随阶段变化：

| 阶段 | Artifact 的主要用途 |
| --- | --- |
| 初始 | **这份结果是什么？** |
| 规划 | **这份结果能否复用？** |
| 执行 | **当前 Subtask 真正需要什么数据？** |
| 重规划 | **新结果说明什么，是否值得继续？** |
| 回答 | **哪些数据与证据支持最终回答？** |

---

## 八、Context Manager 的两种逻辑视图

为避免为五个阶段开发五个相互独立的系统，推荐保留两种 Context 视图：

### 8.1 Conversation Context

主要支持阶段①：最近用户/助手对话、会话状态、远期摘要、历史 Task 候选、最近和相关 Artifact 的元数据，以及用户直接指向的结果。

### 8.2 Execution Context

主要支持阶段②至⑤：完整任务需求、当前执行计划、Subtask 输入输出引用、约束、真实执行结果、可复用产物、分析证据及最终交付状态。

两种视图从同样的四个 Store 读取；**不是新的权威存储模块**。

---

## 九、职责边界与后续待定事项

### 9.1 Context Manager 负责什么

- 定位相关 Session、Task、Artifact、Summary。
- 按阶段装配必要信息与统一引用。
- 对体积大的 Artifact 提供概览及可操作引用；必要时通过工具读取、筛选、聚合或截取。
- 避免给模型装入与当前阶段无关的历史数据。

### 9.2 Context Manager 不负责什么

- 不决定业务问题应分析哪些原因或扩展哪个研究方向；由 Supervisor/专业 Agent 决定。
- 不将 Summary 当作精确数据来源；Task 的当前状态来自 Task Store，业务数据来自 Artifact Store。
- 不要求所有 Agent 先读取完整 Artifact 再进行分析。
- 不在 Intent 尚未解析前，把上一个活跃 Task 直接认定为当前用户任务。

### 9.3 保持 Artifact 存储与访问原则

> **Artifact Store = Metadata + Content Reference + Full Content**  
> **Context Manager = Select Metadata + Expose Read Tools + Load Required Content**

Artifact 的元数据说明结果是什么，内容引用定位真实数据，完整内容可由工具访问。Agent 根据明确的业务目标决定是否需要深入读取，数据访问工具负责精确读取、筛选或计算。

### 9.4 暂不在本文确定

- 如何选取最近对话窗口或历史 Task 候选。
- 何时触发 Summary 压缩及其 Token 预算。
- Artifact 检索排序、渐进展开及数据卸载策略。
- Read / Inspect / Aggregate 等工具的具体接口与权限约束。

## 十、结论

**初始阶段理解用户 → 规划阶段获取完整任务 → 执行阶段获取必要数据 → 重规划阶段观察新结果 → 回答阶段汇总真实证据。**

五种 Context 装配共享四个 Store，但有不同的业务目标和数据粒度。在既定 Unified Supervisor（Intent/Planning 物理统一、逻辑隔离）与 Subtask DAG 架构下，这套规则可作为 Context Manager 的第一版总体设计，具体检索与压缩机制可在存储字段确定后再讨论。
