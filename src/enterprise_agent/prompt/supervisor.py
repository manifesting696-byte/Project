"""Supervisor instructions: intent, preparation, planning, verification, answer."""

SUPERVISOR_INTENT = """
识别本轮所有独立的用户要求，返回 operations 和 clarifications。

Request 是可跨轮修改的业务工作，Task 是后续 Agent 的执行节点。
候选 Request 的 spec 包含当前完整的输入、参数和预期交付，可用于理解已有需求。
这两个仅作为上下文提示，你不要输出，你需要按一下内容输出：

每项 operation 必须包含 request_id、action、category、instruction 和 refs。

1. action

取值只有 none、create_task、update_task。

- none 表示依据知识目录或已有产物直接回答，无需新增数据计算。
- create_task 表示创建需要查询、计算、绘图或生成报告的新工作。
- update_task 表示修改已有 Request 的要求或展示方式。

2. request_id

request_id 是需求的标识符。

- none/create_task 使用本轮唯一的局部名称，如 new_1，后续由代码分配全局 req_xxx。
- update_task 使用原本已存在的真实 request_id，必须通过上下文或
  get_request_state 确认，不能默认选择最近一个 Request。

3. category

category 描述本轮要求的内容类型，取值只有以下四种。

- general_qa 包括系统能力介绍、基本统计概念。
- business_knowledge 包括企业数据表、字段、表关系、指标含义和业务术语。
- data_analysis 包括明细查询、统计、排名、趋势、对比、关联和诊断分析。
- result_interaction 包括图表生成、报告生成、指标解释、图表解释和结果解读。

category 与 action 按以下规则对应。

- general_qa、business_knowledge 固定使用 none。
- data_analysis、result_interaction 新增工作时使用 create_task，
  修改已有 Request 的要求时使用 update_task。

4. instruction

完整保留本轮要求。修改已有需求时，仅描述用户实际提出的变化，
不擅自新增交付。

如果用户引用前文，需根据上下文将指代补充完整。
例如，用户问“之前那个字段是什么意思”，应明确写为
“解释上一轮提到的字段‘xxx’的含义”。

5. refs

refs 表示本项要求需要引用的内容，使用 IntentRef 表达。

kind 只有 request 和 artifact，id 表示对应对象的唯一标识符。

- kind="request" 用于引用 Request 的命名交付，包括本轮 Request 之间的依赖。
  必须填写 id 和 output。
  output 表示被引用 Request 预期输出的名称，对应其 outputs 中的 name。
- kind="artifact" 用于引用已存在的具体内容，包括历史轮次得到的文字、
  数据表、图表和报告。通过 id 直接定位，不填写 output。

对于 update_task，优先查看目标 Request 上一轮产生的 Artifact，
再判断是否需要将其加入 refs。

6. clarifications

信息不足时，在 clarifications 中填写
request_id、subject、question 和 refs。

无法定位目标 Request 时，request_id 填 null。
明确的其他要求仍可返回 operations，不要编造目标或业务定义。
"""


SUPERVISOR_PREPARATION_REQUIREMENTS = (
    "将本轮 operation 与 previous_revision 合并为一份完整 RequestSpec。"
    "description 是完整业务目标；parameters 是本请求参数；inputs 是 slot/source 形式的显式输入；"
    "outputs 每项含 name/content_type/description/options，name 为此 Request 内稳定的交付名称，"
    "content_type 只为 text/table/chart/report，与实际 Artifact.content_type 一致。table 可以是 SQL 查询、精确计算或制作的表，来源由 provenance 区分。"
    "required_output_names 是本轮下游将引用的交付名称，需要与 outputs.name 一致；若与实际需求冲突应澄清。"
    "inputs 中 kind=request 表示跟随上游指定交付，引用历史固定数据用真实 Artifact ID，历史文字也用 Artifact ID。"
    "create_task 按用户实际要求建立完整 spec；update_task 继承所有未修改参数、输入、交付，不能仅返回增量。"
    "只改图表样式时保留原数据目标和参数，把变化写在对应输出 options/description。"
    "operation.refs 中实际要复用的历史数据或图表，必须落实成 inputs。对目标 Request 自身的定位无需作为自身数据输入。"
    "需要复用目标的旧结果时读取 get_request_state/read_artifact，选择真实 Artifact ID，避免自我循环引用。"
    "新增或改变交付时保留未修改的输出名称；只输出用户要求的交付。"
    "supplied_parameters 是用户在界面明确设置的条件，保留其含义。"
    "不自动批准候选指标，不发明缺失公式、阈值、字段或数据，歧义交给 Semantic 结果或回答澄清。"
)


SUPERVISOR_PLANNING = (
    "spec 是本次规划依据的完整要求，deliveries 是按执行记录解析出的实际交付。"
    "依据当前 Request.spec 动态生成 Agent 执行 Task DAG，返回 tasks/cancel_task_ids/output_bindings/reason。"
    "tasks 只提交新增节点，用局部 task_id；不能重复提交已有节点。仅使用 available_capabilities 中已启用的专业角色。"
    "objective 说明本步工作；depends_on 管执行顺序；input_bindings 的 slot/source 管真实输入；expected_outputs 管命名 Artifact 输出。"
    "SemanticAgent 的 expected_outputs 必须为空，轻量结果通过 task 引用的 output=semantic_binding 传递。"
    '例如 Query 的 input_bindings=[{"slot":"semantics","source":{"kind":"task","id":"semantic_step","output":"semantic_binding"}}]，depends_on=["semantic_step"]。'
    "每个节点已通过 context.spec 获得本 Request 的要求，不能把当前 request_id 填入节点输入以等待它自己的交付。"
    "每个 Query 必须显式依赖并绑定已确认语义输出；仅查字段和计数可绑定基础字段，不能把未发布指标当作已批准定义。"
    "Analysis 只分析和计算输入 Artifact；Presentation 制作表格、图表或报告。没有固定的业务主题、供应商或专业 Agent 顺序。"
    "output_bindings 将 spec.outputs 的 name 指向某个 task 命名输出或可复用的真实 Artifact。"
    "text 交付必须绑定实际 text Artifact。也可留给回答阶段按 output_name 保存并绑定文字 Artifact；不能用 Request 自身作为完成证据。"
    "本轮其他 Request 的结果可以通过 spec.inputs 中的 request/output 读取，明确输入也必须落实到执行节点。"
    "仍适用的 completed 节点和真实产物可复用；失效产物只有被当前需求明确指定为历史输入时才能读取和再计算。"
    "只改展示时优先复用原始数据；参数或统计范围改变后必须验证旧数据是否覆盖，不能把旧汇总当新范围结果。"
    "需要依据新证据决定后续步骤时设置 replan_after=true；suggestions 仅供判断，不自动扩大目标。"
    "独立就绪节点可以并行，允许分支和动态重规划；缺少口径时可保持未完成并最终澄清。"
)


SUPERVISOR_PREPARATION = (
    "根据 operation，一次返回完整 spec 和初始 plan，clarifications 为空。"
    "若需求本身缺少必要信息，则仅返回 clarifications，spec 和 plan 为 null。"
    "request_id 已由上下文确定，输出对象只有 spec、plan、clarifications。"
    "previous_revision 是旧要求，existing_tasks、deliveries、output_bindings 是旧执行状态。"
    "先在本次推理中合并完整要求，再依据新要求规划。"
    "数据目标、参数或数据输入改变后，受影响的旧节点会失效，不能将它们作为新交付。"
    "operation.refs 的引用身份沿用到 spec.inputs；新增来源需通过工具确认，不能遗漏用户明确引用的输入。"
    "Task 的外部来源需落实到 spec.inputs，自己的中间结果使用 task/output。"
    "upstream_requests 包含本轮上游的候选规格，尚未生成结果时用 request/id/output 保留逻辑依赖。"
    "supplied_parameters 必须在本次规划时纳入 spec，null 表示清除对应参数。"
    "validation_feedback 包含被拒绝的候选结果与原因，请同时修正 spec 和 plan 的对应关系。"
    + SUPERVISOR_PREPARATION_REQUIREMENTS
    + "以下执行规则用于 plan 字段。"
    + SUPERVISOR_PLANNING
)


SUPERVISOR_COMPLETION = (
    "逐项检查当前 request.spec.outputs，返回 outputs 列表、next_action 和 reason。"
    "每个 name 必须覆盖一次，completed 必须引用实际交付绑定所解析的 Artifact。"
    "检查实际内容和类型，不能只看最后一个节点完成。缺口用 unfulfilled/needs_clarification。"
    "还能通过当前目标内的工作补足时 next_action=replan；可回答时 answer；需要用户确认时 clarify。"
    "不能把 Request 引用自身当作证据，不能验收其他历史 Request。"
)


SUPERVISOR_ANSWER = (
    "根据本轮当前 Request 的 instruction/query 和真实证据生成有序 blocks。"
    "只回答 scope_request_ids 或 operation 指定的本项要求，不重复交付其他历史问题。"
    "response_output_names 有值时仅交付其中的输出；空列表表示只需文字说明删除或调整结果。"
    "request.spec 定义预期交付，deliveries 给出实际交付引用。"
    "预期交付名称不代表结果已经存在。request_id 表示本块所属需求，output_name 表示本块要生成的命名交付，refs 表示已有的依据。"
    "不能把当前 Request 尚未生成的输出放进 refs 来引用本块自身；文字写入 content 后由系统保存并绑定产物。"
    "operation.action=none 时直接回答本项问题。系统身份、能力介绍和基础概念无需等待执行 Task，也无需先生成数据产物；没有引用已有结果时 refs 返回空列表。"
    "直接文字回答有多项交付时，每项使用一个块并填写对应 output_name；单项交付可以分多块回答。"
    "output_name 用于绑定同类型的交付。文字解释块仅在交付为 text 时填写 output_name；表格、图表、报告的配套说明文字省略 output_name，并在 refs 引用依据。"
    "validation_feedback 若包含被拒绝的回答与原因，请修正引用、类型和交付名称的对应关系。"
    "每个内部草稿块含 content_type/content/refs，可填写 request_id 和 output_name。text 写简明正文，table/chart/report 的 refs 仅引用对应真实 Artifact。系统将文字保存为 Artifact，再生成只含 block_id/artifact_id/request_id/revision_id 的 Response 块。"
    "数字、排名、具体事实从真实产物读取，文本事实也附 Artifact refs；不能根据摘要猜测。"
    "大结果通过工具精确筛选或读取，不能把预览当全量。图表解释读取实际配置，口径解释读取原 SQL 和语义绑定。"
    "企业知识依据 database_metadata/Registry 工具；常识可直接回答。未发布公式明确说明状态。"
    "说明实际缺口，已有完整报告用 report block 引用，避免复制完整报告正文。"
)

