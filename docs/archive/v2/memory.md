# 企业数据智能问析 Agent：四存储模块精简设计方案（v2 讨论稿）

> **定位**：在已经确定的架构上精简字段、统一通信协议；不是重新设计 Agent 架构，也不是数据库最终建表脚本。
>
> **范围**：Session Log、Task Store、Artifact Store、Summary Store。**暂不讨论 Context Manager 的加载流程**。
>
> **示例约定**：所有 Artifact 的 ID 统一为 `result_001`、`result_002`……；以 `type` 区分 `query_result`、`analysis_result`、`chart` 和 `report`。示例中的业务结论均为演示内容，不是真实数据库结果。

## 0. 本版设计原则

保留原架构：**用户 Query → 上下文装配 → 同一个 Supervisor 内的 Intent Parsing（逻辑独立）→ Task Planning → Subtask DAG → 专业 Agent 执行 → Artifact → 最终回答**。

四个存储模块职责不变：

| 模块 | 保存的权威信息 | 不负责什么 |
|---|---|---|
| Session Log | 用户原话、每轮 Intent、重要执行事件、实际展示给用户的回答 | 不保存大批量 SQL 结果 |
| Task Store | 当前 Task、跨轮需求快照、具体 Subtask DAG 与执行状态 | 不复制完整分析结果 |
| Artifact Store | 查询结果、分析结果、图表、报告；内容地址和来源 | 不负责管理会话历史 |
| Summary Store | 历史事件的压缩摘要与重要结果引用 | 不充当当前 Task 或业务数据的权威来源 |

**本次相对于旧版主要做减法：**

1. **删除**每条记录里的 `schema_version`；需要时由数据库迁移和 API 版本管理。
2. Request 的 `operation`、`metric`、`entity_type` **不作为通用必填字段**；以自然语言 `description` 保留完整目标，已确定的条件才进入 `params`。
3. Analysis **不强制拆分** `finding_id`、`hypothesis_id`、`finding_type`、`evidence_status`、假设之间的关系等。
4. 跨模块仍使用简洁的 `ResourceRef`；保留 DAG 的 `depends_on`、`input_bindings` 和 `produced_outputs`，这些直接影响执行正确性。
5. **为所有 Artifact 统一设置 `content_ref`**，定位完整内容；特别补齐 `analysis_result` 的完整分析内容地址。
6. **不重复保存同一个事实**：例如不在 Session Event 中复制查询明细，不在 `produced_outputs` 再复制一遍分析正文。

---

## 1. 共享约定：ID、引用、数据输入输出

### 1.1 ID 命名

| 对象 | ID 示例 | 约定 |
|---|---|---|
| Session | `session_001` | 同一聊天会话 |
| Turn | `turn_001` | 一次用户请求以及由它引发的执行 |
| Event | `event_001` | Session Log 的一条记录 |
| Task | `task_001` | 一个可跨 Turn 的业务目标 |
| Task Revision | `rev_001` | 当前任务需求的一版完整快照 |
| Requirement | `req_001` | 一条用户需求，同一需求跨 Revision 保持 ID 稳定 |
| Subtask | `subtask_001` | DAG 的执行节点 |
| Artifact | **`result_001`** | **统一编号**，不因类型不同而采用 `analysis_001`、`chart_001` |
| Summary | `summary_001` | 历史摘要 |

示例 ID 在对应对象类型内全局唯一；生产中可统一由后端生成 UUID/ULID。**不能在同一 Artifact Store 中让查询结果和分析结果同时使用 `result_001`**。

### 1.2 ResourceRef：只负责引用，不保存内容

首版使用简单格式：

```json
{"kind": "artifact", "id": "result_002"}
```

| 字段 | 是否必填 | 说明 |
|---|---|---|
| `kind` | 是 | `request` / `subtask` / `artifact` / `event` |
| `id` | 是 | 被引用对象 ID |
| `output` | 否 | 首版只在引用上游 Subtask 的**某个命名输出**时使用；Request 默认引用整体需求 |

示例：

```json
[
  {"kind": "request", "id": "req_001"},
  {"kind": "subtask", "id": "subtask_001", "output": "dataset"},
  {"kind": "artifact", "id": "result_001"},
  {"kind": "event", "id": "event_010"}
]
```

**首版不引入通用的 `member_type/member_id` 体系**，不要求 Finding、Hypothesis、Series、Section 都各有 ID。

两个必要的精确定位问题用简单方式处理：

- **“这五家供应商”**：引用 `result_001` 对应的五行查询结果，由其 `entity_id_column`（例如 `supplier_id`）取出精确实体 ID；不一定要额外构造一个 `entity_set` 成员对象。
- **“刚才回答的第二点”**：Session `assistant_response` 中保留有序 `blocks`。在需要显式定位时使用 `event_id + block_id`，不必把通用 ResourceRef 扩展出一套内部成员体系。

如未来确实出现一个 Artifact 内多个无法区分的子结果，再按真实需求引入可选选择器，而不是现在对所有对象强制设计。

### 1.3 InputBinding 与 Subtask 输出

**一个输入绑定**表示“当前节点的一个输入，来自哪个上游对象”：

```json
{
  "slot": "supplier_data",
  "source": {"kind": "subtask", "id": "subtask_001", "output": "dataset"}
}
```

- `slot`：当前 Subtask 的输入名称；由被调用 Agent 的能力契约或具体执行器解释。
- `source`：来源，既可以是尚未执行的上游 Subtask，也可以是已存在的 Artifact。
- `output`：上游 Subtask 有多个命名输出时用来选择；无需虚构完整业务字段。

**Subtask 规划时**：

```json
{
  "expected_outputs": ["dataset"],
  "produced_outputs": {}
}
```

**执行完成后**：

```json
{
  "expected_outputs": ["dataset"],
  "produced_outputs": {
    "dataset": {"kind": "artifact", "id": "result_001"}
  }
}
```

第一版不重复持久化 `resolved_inputs`：执行器可依据 `input_bindings` + 上游 `produced_outputs` 解析真实 Artifact。若以后要追溯特定一次执行的输入快照，可在执行事件中记录解析后的引用。

**注意**：`depends_on` 管执行先后，`input_bindings` 管数据来源。即使两个 Subtask 只有顺序关系而无数据传递，也可能需要 `depends_on`。

### 1.4 基本字段规则

- 字段统一 `snake_case`；时间用带时区的 ISO 8601，后端统一 UTC。
- 可选且未知的信息直接省略或用 `null`；不要猜测 `entity_type`、物理表名、指标 ID。
- 数组 `[]` / 对象 `{}` 表示已知当前为空；不是“留着等模型随意填充”。
- `ResourceRef` 指向的对象是否存在、是否有权限访问，由服务层验证。
- **Pydantic 严格校验通信骨架**；`params`、业务分析文本可相对灵活，但不能成为没有目标和来源的任意数据。

---

## 2. Session Log

**核心表**：`sessions`、`session_events`。

### 2.1 Session

```json
{
  "session_id": "session_001",
  "user_id": "user_001",
  "title": "供应商交付与质量分析",
  "last_active_task_id": "task_001",
  "last_response_event_id": "event_010",
  "last_seq": 10,
  "created_at": "2026-11-05T08:00:00Z",
  "updated_at": "2026-11-05T08:15:00Z"
}
```

| 字段 | 说明 |
|---|---|
| `session_id`、`user_id` | 会话及所属用户 |
| `title` | 会话标题 |
| `last_active_task_id` | 最近活跃的 Task **历史指针**；不预判下一轮就是此 Task |
| `last_response_event_id` | 最近用户可见回答的 Event ID |
| `last_seq` | 会话内最新事件序号 |
| `created_at`、`updated_at` | 时间 |

### 2.2 SessionEvent

```json
{
  "event_id": "event_004",
  "session_id": "session_001",
  "seq": 4,
  "turn_id": "turn_001",
  "event_type": "tool_result",
  "task_id": "task_001",
  "subtask_id": "subtask_001",
  "actor": "SQLTool",
  "payload": {
    "tool_call_id": "call_001",
    "summary": "查询返回5行",
    "refs": [{"kind": "artifact", "id": "result_001"}]
  },
  "created_at": "2026-11-05T08:05:00Z"
}
```

| 字段 | 说明 |
|---|---|
| `event_id`、`session_id`、`seq` | 标识及会话内顺序；`(session_id, seq)` 唯一 |
| `turn_id` | 同轮用户请求共享 |
| `event_type` | 事件类型 |
| `task_id`、`subtask_id` | 可为空；一般知识问答无需建立 Task |
| `actor` | User、Supervisor、Agent、Tool 等 |
| `payload` | 依事件类型使用简单的类型化内容 |
| `created_at` | 时间 |

首版保留六类事件：

| `event_type` | Payload 最少字段 |
|---|---|
| `user_message` | `content`：用户原话 |
| `intent_resolved` | `intent`：本轮结构化需求变更 |
| `agent_output` | `summary`、可选 `refs`：计划或 Agent 关键结果 |
| `tool_call` | `tool_call_id`、`tool_name`、`arguments` |
| `tool_result` | `tool_call_id`、`summary`、可选 `refs` |
| `assistant_response` | `blocks`：真实展示给用户的有序内容 |

`tool_call` / `tool_result` 通过 `tool_call_id` 关联。原始数据位于 Artifact Store，不放进 Event。

### 2.3 Intent Event：保留本轮变化，不强制细分 operation

```json
{
  "intent": {
    "dialogue_action": "new_task",
    "target_task_id": null,
    "goal": "分析供应商交付与来料质量",
    "constraints_patch": {"period": "2025-H1"},
    "request_changes": [
      {
        "action": "add",
        "request_id": "req_001",
        "type": "data_analysis",
        "description": "找出2025年上半年交付准时率最低的五家供应商",
        "params": {"top_k": 5},
        "inputs": []
      },
      {
        "action": "add",
        "request_id": "req_002",
        "type": "data_analysis",
        "description": "比较上述五家供应商的来料拒收率",
        "params": {},
        "inputs": [
          {"slot": "suppliers", "source": {"kind": "request", "id": "req_001"}}
        ]
      },
      {
        "action": "add",
        "request_id": "req_003",
        "type": "visualization",
        "description": "将五家供应商的拒收率生成柱状图",
        "params": {"chart_type": "bar"},
        "inputs": [
          {"slot": "data", "source": {"kind": "request", "id": "req_002"}}
        ]
      }
    ]
  }
}
```

注意以下几点：

- `description` 是**必填的业务需求原意**，不能因为 `params` 不完整而省略需求。
- `type` 使用宽泛类型，如 `data_analysis`、`visualization`、`report`、`business_knowledge`、`general_qa`；**`operation` 可不出现**。
- `params` 只填写从用户原话/明确历史条件能够确定的内容；如标准指标 ID、数据库字段不确定，后续由 Semantic Agent 绑定。
- `request_changes` 的 `action` 只需要 `add` / `replace` / `remove`。`remove` 时只需要 `request_id`；不用再内嵌一个 `request` 对象。
- `constraints_patch` 是**本轮条件变化**：未提供表示继承，显式 `null` 表示清除；不等于完整条件快照。
- 同一个物理 Supervisor 可以先输出 Intent，再进入 Planning；这里逻辑上仍保留两阶段的边界。

### 2.4 Assistant Response：保留用户看到的顺序

```json
{
  "blocks": [
    {
      "block_id": "block_001",
      "kind": "text",
      "content": "已筛选出交付准时率最低的五家供应商。",
      "refs": [{"kind": "artifact", "id": "result_001"}]
    },
    {
      "block_id": "block_002",
      "kind": "text",
      "content": "质量比较发现部分供应商拒收率偏高。",
      "refs": [{"kind": "artifact", "id": "result_003"}]
    },
    {
      "block_id": "block_003",
      "kind": "chart",
      "content": null,
      "refs": [{"kind": "artifact", "id": "result_004"}]
    }
  ]
}
```

`block_id` 在一次回答中唯一，由系统生成。保留它是为了支持“你刚才第二点是什么意思”这样的精确追问；**不要求 Analysis 内部每条 Finding 另设 ID**。不再额外存一份可能与 `blocks` 不一致的回答全文。

---

## 3. Task Store

**核心表**：`tasks`、`task_revisions`、`subtasks`。

### 3.1 Task：跨 Turn 的整体目标

```json
{
  "task_id": "task_001",
  "origin_session_id": "session_001",
  "created_by_turn_id": "turn_001",
  "goal": "分析供应商交付与来料质量",
  "current_revision_id": "rev_001",
  "plan_version": 1,
  "status": "active",
  "created_at": "2026-11-05T08:00:00Z",
  "updated_at": "2026-11-05T08:05:00Z"
}
```

- `goal`：**整体任务的简要目标**，不是完整 Query，也不是单个 Subtask 的任务描述。
- `current_revision_id`：指向完整需求快照。
- `plan_version`：Supervisor 根据中间结果扩展计划时更新；与用户修改需求而产生的 Revision 不同。
- `status`：`active` / `completed` / `paused`。暂不设计复杂恢复状态机。

### 3.2 TaskRevision：合并历史后的完整需求

```json
{
  "revision_id": "rev_001",
  "task_id": "task_001",
  "parent_revision_id": null,
  "source_intent_event_id": "event_002",
  "constraints": {"period": "2025-H1"},
  "requirements": [
    {
      "request_id": "req_001",
      "type": "data_analysis",
      "description": "找出2025年上半年交付准时率最低的五家供应商",
      "params": {"top_k": 5},
      "inputs": []
    },
    {
      "request_id": "req_002",
      "type": "data_analysis",
      "description": "比较上述五家供应商的来料拒收率",
      "params": {},
      "inputs": [
        {"slot": "suppliers", "source": {"kind": "request", "id": "req_001"}}
      ]
    },
    {
      "request_id": "req_003",
      "type": "visualization",
      "description": "将五家供应商拒收率绘制为柱状图",
      "params": {"chart_type": "bar"},
      "inputs": [
        {"slot": "data", "source": {"kind": "request", "id": "req_002"}}
      ]
    }
  ],
  "created_at": "2026-11-05T08:01:00Z"
}
```

| 字段 | 含义 |
|---|---|
| `revision_id`、`task_id` | 版本及所属任务 |
| `parent_revision_id` | 前一版需求快照，可为空 |
| `source_intent_event_id` | 哪一轮 Intent 触发了该版本 |
| `constraints` | **完整、合并后的**约束，区别于本轮 `constraints_patch` |
| `requirements` | 该版本全部用户需求，不是仅本轮新增的需求 |
| `created_at` | 时间 |

`Requirement` 统一保留 `request_id`、`type`、`description`、`params`、`inputs`；**不必有 `operation`**，也不要求预先识别所有业务字段。`TaskRevision` 不重复存 `goal`；当前任务概括看 `Task.goal`，历史完整需求以 Revision 为准。

下一轮用户说“只看第二季度，柱状图改折线图”，生成 `rev_002`，更新相关 Request 和 `constraints`，旧 `rev_001` 不覆盖。

### 3.3 Subtask：实际执行节点

**规划刚完成时：**

```json
{
  "subtask_id": "subtask_002",
  "task_id": "task_001",
  "revision_id": "rev_001",
  "created_by_turn_id": "turn_001",
  "request_ids": ["req_002"],
  "objective": "查询五家供应商的来料拒收率",
  "assigned_agent": "QueryAgent",
  "depends_on": ["subtask_001"],
  "input_bindings": [
    {
      "slot": "supplier_data",
      "source": {"kind": "subtask", "id": "subtask_001", "output": "dataset"}
    }
  ],
  "expected_outputs": ["dataset"],
  "produced_outputs": {},
  "status": "pending",
  "created_at": "2026-11-05T08:02:00Z"
}
```

**执行完成后，更新同一条记录中的状态与输出：**

```json
{
  "produced_outputs": {
    "dataset": {"kind": "artifact", "id": "result_002"}
  },
  "status": "completed",
  "completed_at": "2026-11-05T08:07:00Z"
}
```

| 字段 | 含义 | 是否必要 |
|---|---|---|
| `subtask_id`、`task_id`、`revision_id` | 子任务身份与归属 | 必须 |
| `created_by_turn_id` | 哪一轮用户请求触发该 Subtask；跨轮继续执行时尤其有用 | 必须 |
| `request_ids` | 此节点服务于哪些用户需求 | 必须，可多个 |
| `objective` | 当前节点要做的事 | 必须 |
| `assigned_agent` | 执行的 Agent/能力 | 必须 |
| `depends_on` | DAG 前置节点 | 必须，可空数组 |
| `input_bindings` | 需要哪个上游逻辑输出/Artifact | 必须，可空数组 |
| `expected_outputs` | 规划阶段声明的输出名 | 必须，可空数组 |
| `produced_outputs` | 完成后，输出名到真实 `result_xxx` 的映射 | 必须，可空对象 |
| `status`、时间 | 运行状态及时间 | 必须/按阶段出现 |

`expected_outputs` 保留简单的名称列表即可。若某能力需要明确校验产物类型，可由其能力配置或执行器检查 `Artifact.type`，不必在每个 Subtask 内重复写一份类型表。

`depends_on` 定义串并行；**中间结果驱动的动态规划**通过增加新 Subtask 并更新 Task 的 `plan_version` 实现。首版先不强制 `trigger_ref`、`resolved_inputs` 等补充字段；重要计划变更可由 Session Event 记录。

---

## 4. Artifact Store：一套 Artifact ID、四种类型

**核心表**：`artifacts`。

### 4.1 通用 Artifact：必须能找到完整内容

```json
{
  "artifact_id": "result_003",
  "type": "analysis_result",
  "task_id": "task_001",
  "revision_id": "rev_001",
  "subtask_id": "subtask_003",
  "request_ids": ["req_002"],
  "producer": "AnalysisAgent",
  "title": "五家供应商来料质量分析",
  "summary": "五家供应商的来料质量存在差异",
  "source_refs": [
    {"kind": "artifact", "id": "result_001"},
    {"kind": "artifact", "id": "result_002"}
  ],
  "content_ref": "storage://artifacts/result_003/analysis.md",
  "payload": {
    "findings": [
      {
        "text": "示例：供应商A的拒收率高于其余候选供应商",
        "refs": [{"kind": "artifact", "id": "result_002"}]
      }
    ],
    "conclusion": "示例：建议重点检查拒收率较高的供应商"
  },
  "created_at": "2026-11-05T08:10:00Z"
}
```

**这里专门补上你提出的 Analysis“地址”问题：**

- `artifact_id = result_003`：此分析产物自己的 ID。
- `content_ref = storage://artifacts/result_003/analysis.md`：**完整分析内容**的位置，可以是 Markdown、JSON 或其他内部受管存储对象。
- `summary`：供检索/预览的短摘要，**不代替完整分析正文**。
- `payload`：适量结构化内容，如关键发现与结论；**不是必须把长篇分析全文放在这里**。
- `source_refs`：分析实际依赖的上游产物；从其 `content_ref` 可进一步读取真实数据。

通用字段说明：

| 字段 | 含义 |
|---|---|
| `artifact_id` | 统一 `result_xxx` ID，不区分类型前缀 |
| `type` | `query_result` / `analysis_result` / `chart` / `report` |
| `task_id`、`revision_id`、`subtask_id` | 属于哪个任务/版本、由哪个节点产生 |
| `request_ids` | 支撑哪些需求；可多个 |
| `producer` | 实际生成者 |
| `title`、`summary` | 简短可检索描述 |
| `source_refs` | 直接上游 Artifact 的引用，形成数据血缘；可为空 |
| **`content_ref`** | **完整产物内容的内部存储地址，四种类型共用** |
| `payload` | 与 `type` 对应的轻量结构化信息 |
| `created_at` | 时间 |

**存储一致性约定**：`content_ref` 指向完整内容的权威副本；`payload` 只放小体量的索引/结构化摘录。不要在两个地方维护互相独立、可能冲突的两份完整正文。`content_ref` 是给后端 Artifact Service 解析的内部地址，**不把任意物理路径或访问凭据交给 LLM**。

### 4.2 类型一：`query_result`

示例完整记录中的 `payload`：

```json
{
  "columns": [
    {"name": "supplier_id", "dtype": "integer"},
    {"name": "supplier_name", "dtype": "string"},
    {"name": "on_time_delivery_rate", "dtype": "float"}
  ],
  "row_count": 5,
  "entity_id_column": "supplier_id",
  "sql_ref": "storage://artifacts/result_001/query.sql"
}
```

对应 Artifact 的 `type` 为 `query_result`，`content_ref` 例如 `storage://artifacts/result_001/data.parquet`；**真实五行数据就在 `content_ref` 中**，不复制到 Event。`entity_id_column` 可选：只有结果确实表示一组可识别实体时才填写。`sql_ref` 可选：需要追溯实际查询 SQL 时保留。若业务含指标版本、查询快照等严格溯源需求，再在可选元数据中补充。

### 4.3 类型二：`analysis_result`

`payload` 仅保留实用内容：

```json
{
  "findings": [
    {
      "text": "示例：供应商A的拒收率最高",
      "refs": [{"kind": "artifact", "id": "result_002"}]
    },
    {
      "text": "示例：供应商B在第二季度拒收率升高",
      "refs": [{"kind": "artifact", "id": "result_002"}]
    }
  ],
  "conclusion": "示例：需要进一步检查异常批次",
  "next_steps": ["按批次、材料类别继续调查"]
}
```

- **不强制** `finding_id`、`hypothesis_id`、`finding_type`、`evidence_status` 等。
- `findings` 可以是空数组；`conclusion`、`next_steps` 有内容才出现。
- 每条 Finding 保留轻量 `refs`，用于核查来源，但不要求独立 ID。
- 对尚未验证的原因，用“可能、待验证”等明确措辞，不把相关性说成因果性。
- 完整分析正文统一通过此 Artifact 的 **`content_ref`** 取得，例如 `storage://artifacts/result_003/analysis.md`。

### 4.4 类型三：`chart`

```json
{
  "chart_type": "bar",
  "title": "五家供应商来料拒收率"
}
```

- 本图表 Artifact 的 `source_refs` 指向它实际使用的查询结果，例如 `result_002`。
- `content_ref` 指向完整 ECharts 配置，例如 `storage://artifacts/result_004/chart.json`。
- 颜色、系列、轴映射保存在这份**实际渲染配置**中；首版不用把全部 `series_id`、`x_field`、`y_field` 再拆成数据库必填字段。后续用户问“蓝线是什么”，可读取图表配置。

### 4.5 类型四：`report`

```json
{
  "title": "供应商交付与质量分析报告",
  "format": "markdown"
}
```

- `content_ref` 指向完整正文，例如 `storage://artifacts/result_005/report.md`。
- `source_refs` 指向报告实际引用的数据、分析和图表 Artifact。
- 首版不要求为每一章节生成 `section_id`；需要查看某章节时读取正文结构。

### 4.6 关于 Analysis 的“来源”和“地址”

这是两个不同问题，必须都保留：

```json
{
  "artifact_id": "result_003",
  "type": "analysis_result",
  "source_refs": [
    {"kind": "artifact", "id": "result_002"}
  ],
  "content_ref": "storage://artifacts/result_003/analysis.md"
}
```

| 字段 | 解决的问题 |
|---|---|
| `source_refs` | **分析依据什么数据**（上游产物是谁） |
| `content_ref` | **分析全文存放在哪里**（当前产物的详细内容在哪） |

二者不能合并；`content_ref` 也不意味着必须把分析全文放进 Prompt。

---

## 5. Summary Store

**核心表**：`session_summaries`。首版只保存一份随会话延伸的历史压缩视图，不再拆太多摘要字段。

```json
{
  "summary_id": "summary_001",
  "session_id": "session_001",
  "covered_to_seq": 10,
  "content": "此前讨论了2025年上半年供应商交付排名及来料质量；已完成排名、质量分析和柱状图。",
  "key_refs": [
    {"kind": "artifact", "id": "result_001"},
    {"kind": "artifact", "id": "result_003"},
    {"kind": "artifact", "id": "result_004"}
  ],
  "created_at": "2026-11-05T08:20:00Z"
}
```

| 字段 | 含义 |
|---|---|
| `summary_id`、`session_id` | 摘要身份及所属会话 |
| `covered_to_seq` | 压缩覆盖事件 `1..N`（首版默认从会话开头累计） |
| `content` | 简短自然语言摘要：历史话题、重要进展、未完成事项 |
| `key_refs` | 重要结果或事件的引用，可为空 |
| `created_at` | 时间 |

没有必要同时维护 `version`、`previous_summary_id`、`main_topic`、`completed_work`、`pending_questions` 等一整套冗余字段。需要新的摘要就追加一条，按 `covered_to_seq` 和创建时间选择最新摘要。**当前业务状态以 Task Store 为准，精确结果以 Artifact Store 为准**。

---

## 6. 四个模块如何实际通信：完整例子

用户：**“查询2025年上半年交付准时率最低的五家供应商，分析这五家的来料拒收率，并生成柱状图。”**

| 步骤 | 发生什么 | 存在哪个 Store |
|---|---|---|
| ① | `user_message` 保存原 Query；`intent_resolved` 保存 `req_001`～`req_003` | Session Log |
| ② | 创建 `task_001` 和 `rev_001`，保存完整需求 | Task Store |
| ③ | Supervisor 创建查询、质量分析、绘图等 Subtask，写入 DAG 依赖 | Task Store |
| ④ | 查询交付排名，生成 `result_001`（query_result） | Artifact Store |
| ⑤ | `subtask_002` 的输入指向 `subtask_001.dataset`，据此查询质量，生成 `result_002`（query_result） | Task Store / Artifact Store |
| ⑥ | Analysis Agent 从 `result_002` 生成 `result_003`（analysis_result），在 `content_ref` 保存完整分析 | Artifact Store |
| ⑦ | 图表 Agent 从 `result_002` 生成 `result_004`（chart），图表规格保存于 `content_ref` | Artifact Store |
| ⑧ | 最终回答的 `blocks` 分别引用 `result_001`、`result_003`、`result_004` | Session Log |
| ⑨ | 如需压缩历史，保存简短摘要及这些 `result_xxx` 引用 | Summary Store |

简化后的数据关系：

```text
User Query
   │
   ▼
Session Log: user_message / intent_resolved
   │
   ▼
Task Store: task_001 → rev_001 (完整需求)
   │
   ▼
Subtask DAG
   ├─ Subtask 001 ────────────→ result_001 (query_result)
   ├─ Subtask 002 ← result_001 → result_002 (query_result)
   ├─ Subtask 003 ← result_002 → result_003 (analysis_result)
   └─ Subtask 004 ← result_002 → result_004 (chart)
                                      │
                                      ▼
                          Session Log: 回答 blocks
                                      │
                                      ▼
                          Summary Store: 摘要 + refs
```

**并行说明**：若 Analysis 与 Chart 都只依赖 `result_002`，它们可在查询完成后并行；无需引入 `is_parallel` 字段。

**跨轮修改**：新 Query 产生另一条 `intent_resolved` 事件；Task Store 生成 `rev_002`，只重新执行受影响的 Subtask；旧 Artifact 保留，以便引用原有回答。

---

## 7. 推荐保留的七张核心表

| 模块 | 表名 | 首版主要字段 |
|---|---|---|
| Session Log | `sessions` | `session_id`, `user_id`, `title`, `last_active_task_id`, `last_response_event_id`, `last_seq`, 时间 |
| Session Log | `session_events` | `event_id`, `session_id`, `seq`, `turn_id`, `event_type`, `task_id`, `subtask_id`, `actor`, `payload`, `created_at` |
| Task Store | `tasks` | `task_id`, `origin_session_id`, `created_by_turn_id`, `goal`, `current_revision_id`, `plan_version`, `status`, 时间 |
| Task Store | `task_revisions` | `revision_id`, `task_id`, `parent_revision_id`, `source_intent_event_id`, `constraints`, `requirements`, `created_at` |
| Task Store | `subtasks` | `subtask_id`, `task_id`, `revision_id`, `created_by_turn_id`, `request_ids`, `objective`, `assigned_agent`, `depends_on`, `input_bindings`, `expected_outputs`, `produced_outputs`, `status`, 时间 |
| Artifact Store | `artifacts` | `artifact_id`, `type`, `task_id`, `revision_id`, `subtask_id`, `request_ids`, `producer`, `title`, `summary`, `source_refs`, **`content_ref`**, `payload`, `created_at` |
| Summary Store | `session_summaries` | `summary_id`, `session_id`, `covered_to_seq`, `content`, `key_refs`, `created_at` |

可以在 PostgreSQL 中把主键、状态、时间单独作为列，把 `payload`、`requirements`、`input_bindings`、`produced_outputs` 等作为 JSONB。这里仍是逻辑设计，不要求四个 Store 四个数据库。

---

## 8. 什么要严格校验，什么可以灵活？

| 类别 | 校验方式 | 示例 |
|---|---|---|
| **通信骨架** | 严格，Pydantic 类型校验 | `artifact_id`、`type`、`ResourceRef`、`input_bindings`、`produced_outputs` |
| **任务关系** | Service 层校验 | 引用 ID 存在、DAG 无环、产物确实属于相应 Task/Revision |
| **业务需求参数** | 允许缺省，已知才填写 | `metric`、`entity_type`、`operation`、排序字段、时间 |
| **分析内容** | 轻量结构化 + 自然语言 | `findings`、`conclusion`、`next_steps` |
| **完整数据/报告** | 存储服务管理与读取 | `content_ref` 对应的表格、分析 Markdown、图表配置、报告 |

### 首版建议暂不做的事

- 不给每条记录加 `schema_version`。
- 不强迫 Intent Parser 为每个复杂分析请求选择一个 `operation`。
- 不把未知业务字段伪装成已完成的语义绑定。
- 不建立 `Finding` / `Hypothesis` / `Series` / `Section` 的全套独立主键与引用体系。
- 不让同一份分析全文同时成为 `payload` 和 `content_ref` 下两份可独立修改的数据。
- 不把所有工具原始输出放进 Session Log。

### 保留的不可再删的关系

- **Request → Subtask**：知道哪些执行节点服务哪些用户要求。
- **Subtask → 上游 Subtask/Artifact**：知道依赖什么、传什么数据。
- **Subtask → Artifact**：知道执行后真实生成了什么。
- **Artifact → 上游 Artifact**：知道分析、图表、报告来自哪些证据。
- **Artifact → content_ref**：知道当前结果的完整数据/内容在哪里。
- **Answer Block → Artifact**：支持用户围绕历史回答继续追问。

> **结论**：精简字段，保留必需的数据关系。通信结构要统一、可验证；Intent 的复杂业务含义和 Analysis 的内容不必被大量预设字段锁死。尤其是 `analysis_result`，同时保留 `source_refs`（分析依据）和 `content_ref`（完整分析地址），才能既可追溯又可继续使用。
