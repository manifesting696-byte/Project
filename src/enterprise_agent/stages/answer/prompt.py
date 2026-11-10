"""Model instructions owned by this stage."""

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
