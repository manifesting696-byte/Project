"""Model instructions owned by this stage."""

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
