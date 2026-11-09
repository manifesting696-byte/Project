"""Deterministic end-to-end fixtures kept outside production imports."""

from copy import deepcopy


def artifact_ref(key):
    return {"kind": "artifact", "id": key}


class FixtureModel:
    def __init__(self):
        self.calls = []
        self.contexts = []

    def run(self, role, instruction, context, output, tools=None, emit=None):
        self.calls.append(role)
        self.contexts.append((role, deepcopy(context)))
        tools = tools or {}
        if role == "UnifiedSupervisor.Intent":
            data = self.intent(context, tools)
        elif role == "UnifiedSupervisor.Definition":
            data = self.definition(context)
        elif role == "UnifiedSupervisor.Planning":
            data = self.plan(context, tools)
        elif role == "SemanticAgent":
            tools["search_semantic_registry"].call(query="Products")
            data = {
                "status": "bound",
                "tables": ["Products"],
                "fields": ["Products.product_id", "Products.product_name"],
                "explanation": "真实产品字段",
            }
        elif role == "QueryAgent":
            tools["get_bound_semantics"].call()
            limit = int(context["parameters"].get("limit", 5))
            saved = tools["execute_readonly_sql"].call(
                sql=f"SELECT product_id, product_name, 1 AS record_count FROM Products ORDER BY product_id LIMIT {limit}",
                title="产品数据",
                summary="真实产品行",
                entity_id_column="product_id",
            )
            data = {
                "summary": "查询完成",
                "produced_outputs": {"dataset": artifact_ref(saved["artifact_id"])},
            }
        elif role == "AnalysisAgent":
            key = context["inputs"][0]["artifact_id"]
            computed = tools["compute_artifact"].call(
                artifact_ids=[key],
                sql=f'SELECT COUNT(*) AS n FROM "{key}"',
                title="行数",
                summary="精确计数",
            )
            count = computed["data"]["rows"][0]["n"]
            saved = tools["save_artifact"].call(
                title="分析",
                summary=f"记录数 {count}",
                body=f"实际有 {count} 行。",
                source_ids=[computed["artifact_id"]],
            )
            data = {
                "summary": "分析完成",
                "produced_outputs": {"analysis": artifact_ref(saved["artifact_id"])},
            }
        elif role == "PresentationAgent":
            if context["task"]["params"]["format"] == "chart":
                saved = tools["render_chart"].call(
                    source_id=context["inputs"][0]["artifact_id"],
                    chart_type="bar",
                    x="product_name",
                    series=[
                        {"field": "record_count", "name": "记录数", "color": "#202020"}
                    ],
                    title="产品记录",
                    horizontal=context["task"]["params"].get("horizontal", False),
                )
                name = "chart"
            else:
                saved = tools["render_report"].call(
                    title="报告",
                    body="# 产品报告",
                    source_ids=[
                        value["artifact_id"]
                        for value in context["inputs"]
                        if value.get("artifact_id")
                    ],
                )
                name = "report"
            data = {
                "summary": "展示完成",
                "produced_outputs": {name: artifact_ref(saved["artifact_id"])},
            }
        elif role == "UnifiedSupervisor.Completion":
            data = self.completion(context)
        elif role == "UnifiedSupervisor.Answer":
            data = self.answer(context)
        elif role == "SessionSummary":
            data = {"content": "此前讨论了产品分析和结果交互。", "key_refs": []}
        else:
            raise AssertionError(role)
        return output.model_validate(data)

    @staticmethod
    def operation(key, action, category, text, refs=None):
        return {
            "request_id": key,
            "action": action,
            "category": category,
            "instruction": text,
            "refs": refs or [],
        }

    def intent(self, context, tools):
        query = context["query"]
        if query == "什么是平均值？":
            operations = [self.operation("question", "none", "general_qa", query)]
        elif query == "有哪些产品表？":
            operations = [
                self.operation("knowledge", "none", "business_knowledge", query)
            ]
        elif query == "先给统计结果画图，再列出前五个产品":
            operations = [
                self.operation(
                    "chart",
                    "create_task",
                    "result_interaction",
                    "根据上游绘图",
                    [{"kind": "request", "id": "data", "output": "data"}],
                ),
                self.operation(
                    "data", "create_task", "data_analysis", "列出前五个产品"
                ),
            ]
        elif query == "分别列出前两个和前三个产品":
            operations = [
                self.operation(
                    "first", "create_task", "data_analysis", "列出前两个产品"
                ),
                self.operation(
                    "second", "create_task", "data_analysis", "列出前三个产品"
                ),
            ]
        elif "横向" in query or query == "只统计前两个产品":
            candidates = context["candidate_requests"]
            candidate = next(
                item for item in reversed(candidates) if "产品" in item["title"]
            )
            state = tools["get_request_state"].call(request_id=candidate["request_id"])
            refs = []
            if "横向" in query:
                refs = [state["deliveries"]["chart"]]
            operations = [
                self.operation(
                    candidate["request_id"],
                    "update_task",
                    "result_interaction" if refs else "data_analysis",
                    query,
                    refs,
                )
            ]
        else:
            operations = []
            if query.startswith("解释平均值，再"):
                operations.append(
                    self.operation("mean", "none", "general_qa", "解释平均值")
                )
            operations.append(
                self.operation("products", "create_task", "data_analysis", query)
            )
        return {"operations": operations, "clarifications": []}

    @staticmethod
    def definition(context):
        operation = context["operation"]
        text = operation["instruction"]
        if context["previous_revision"]:
            spec = deepcopy(context["previous_revision"]["spec"])
            if "横向" in text:
                next(item for item in spec["outputs"] if item["name"] == "chart")[
                    "options"
                ]["horizontal"] = True
                spec["inputs"].append(
                    {"slot": "previous_chart", "source": operation["refs"][0]}
                )
            else:
                spec["description"] = text
                spec["parameters"]["limit"] = 2
            return spec
        if text == "根据上游绘图":
            return {
                "description": "绘制产品图",
                "parameters": {},
                "inputs": [{"slot": "data", "source": operation["refs"][0]}],
                "outputs": [
                    {"name": "chart", "kind": "chart", "description": "产品条形图"}
                ],
            }
        limit = 2 if "两个" in text else 3 if "三个" in text else 5
        outputs = [{"name": "data", "kind": "table", "description": "产品数据"}]
        if "图表" in text:
            outputs.append(
                {"name": "chart", "kind": "chart", "description": "产品图表"}
            )
        if "报告" in text:
            outputs.append(
                {"name": "report", "kind": "report", "description": "产品报告"}
            )
        return {
            "description": text,
            "parameters": {"limit": limit},
            "inputs": [],
            "outputs": outputs,
        }

    @staticmethod
    def node(
        key,
        agent,
        objective,
        deps=None,
        inputs=None,
        outputs=None,
        params=None,
        replan=False,
    ):
        return {
            "task_id": key,
            "assigned_agent": agent,
            "objective": objective,
            "depends_on": deps or [],
            "input_bindings": inputs or [],
            "expected_outputs": outputs or [],
            "params": params or {},
            "replan_after": replan,
        }

    @staticmethod
    def binding(slot, kind, key, output=None):
        return {
            "slot": slot,
            "source": {
                "kind": kind,
                "id": key,
                **({"output": output} if output else {}),
            },
        }

    def plan(self, context, tools):
        spec = context["spec"]
        outputs = {item["name"]: item for item in spec["outputs"]}
        nodes = context["tasks"]
        semantic_gap = next(
            (node for node in nodes if node["status"] == "needs_clarification"), None
        )
        if semantic_gap:
            return {
                "tasks": [],
                "cancel_task_ids": [
                    node["task_id"] for node in nodes if node["status"] == "pending"
                ],
                "reason": "等待用户确认语义",
            }
        if spec["description"] == "绘制产品图":
            source = spec["inputs"][0]["source"]
            task = self.node(
                "chart",
                "PresentationAgent",
                "绘图",
                inputs=[{"slot": "data", "source": source}],
                outputs=["chart"],
                params={"format": "chart"},
            )
            return {
                "tasks": [task],
                "output_bindings": {
                    "chart": {"kind": "task", "id": "chart", "output": "chart"}
                },
                "reason": "读取本轮上游请求的真实结果",
            }
        query = next(
            (
                node
                for node in reversed(nodes)
                if node["assigned_agent"] == "QueryAgent"
                and node["status"] == "completed"
            ),
            None,
        )
        horizontal = (
            outputs.get("chart", {}).get("options", {}).get("horizontal", False)
        )
        additions, bindings = [], {}
        if horizontal:
            chart_ref = next(
                item["source"]
                for item in spec["inputs"]
                if item["slot"] == "previous_chart"
            )
            chart = tools["read_artifact"].call(artifact_id=chart_ref["id"])["content"]
            data_id = chart["selection"]["source_id"]
            additions.append(
                self.node(
                    "changed_chart",
                    "PresentationAgent",
                    "横向图",
                    inputs=[self.binding("data", "artifact", data_id)],
                    outputs=["chart"],
                    params={"format": "chart", "horizontal": True},
                )
            )
            if "data" in outputs:
                bindings["data"] = artifact_ref(data_id)
            bindings["chart"] = {
                "kind": "task",
                "id": "changed_chart",
                "output": "chart",
            }
            if "report" in outputs:
                analysis = next(
                    node
                    for node in reversed(nodes)
                    if node["assigned_agent"] == "AnalysisAgent"
                    and node["status"] == "completed"
                )
                additions.append(
                    self.node(
                        "changed_report",
                        "PresentationAgent",
                        "同步报告",
                        deps=["changed_chart"],
                        inputs=[
                            self.binding(
                                "analysis",
                                "artifact",
                                analysis["produced_outputs"]["analysis"]["id"],
                            ),
                            self.binding("chart", "task", "changed_chart", "chart"),
                        ],
                        outputs=["report"],
                        params={"format": "report"},
                    )
                )
                bindings["report"] = {
                    "kind": "task",
                    "id": "changed_report",
                    "output": "report",
                }
        elif query is None:
            additions = [
                self.node("semantic", "SemanticAgent", "绑定产品字段"),
                self.node(
                    "query",
                    "QueryAgent",
                    "读取产品",
                    deps=["semantic"],
                    inputs=[
                        self.binding(
                            "semantics", "task", "semantic", "semantic_binding"
                        )
                    ],
                    outputs=["dataset"],
                ),
            ]
            bindings["data"] = {"kind": "task", "id": "query", "output": "dataset"}
            if "report" in outputs:
                additions.append(
                    self.node(
                        "analysis",
                        "AnalysisAgent",
                        "精确分析",
                        deps=["query"],
                        inputs=[self.binding("data", "task", "query", "dataset")],
                        outputs=["analysis"],
                        replan=True,
                    )
                )
            if "chart" in outputs:
                additions.append(
                    self.node(
                        "chart",
                        "PresentationAgent",
                        "绘图",
                        deps=["query"],
                        inputs=[self.binding("data", "task", "query", "dataset")],
                        outputs=["chart"],
                        params={"format": "chart"},
                    )
                )
                bindings["chart"] = {"kind": "task", "id": "chart", "output": "chart"}
        elif "report" in outputs and "report" not in context["deliveries"]:
            analysis = next(
                node
                for node in nodes
                if node["assigned_agent"] == "AnalysisAgent"
                and node["status"] == "completed"
            )
            chart = next(
                (
                    node
                    for node in nodes
                    if node["assigned_agent"] == "PresentationAgent"
                    and node["status"] == "completed"
                ),
                None,
            )
            deps = [analysis["task_id"]]
            inputs = [self.binding("analysis", "task", analysis["task_id"], "analysis")]
            if chart:
                deps.append(chart["task_id"])
                inputs.append(self.binding("chart", "task", chart["task_id"], "chart"))
            additions.append(
                self.node(
                    "report",
                    "PresentationAgent",
                    "报告",
                    deps=deps,
                    inputs=inputs,
                    outputs=["report"],
                    params={"format": "report"},
                )
            )
            bindings["report"] = {"kind": "task", "id": "report", "output": "report"}
        return {
            "tasks": additions,
            "output_bindings": bindings,
            "reason": "根据当前需求与真实证据规划",
        }

    @staticmethod
    def completion(context):
        refs = context["deliveries"]
        checks = []
        for item in context["spec"]["outputs"]:
            completed = item["name"] in refs
            checks.append(
                {
                    "name": item["name"],
                    "status": "completed" if completed else "needs_clarification",
                    "reason": "真实交付" if completed else "等待确认",
                    "refs": [refs[item["name"]]] if completed else [],
                }
            )
        return {
            "outputs": checks,
            "next_action": "answer"
            if all(check["status"] == "completed" for check in checks)
            else "clarify",
            "reason": "按当前交付要求检查",
        }

    @staticmethod
    def answer(context):
        request_id = context["request"]["request_id"]
        if context["stage"] == "initial":
            category = context["operation"]["category"]
            text = (
                "平均值是总和除以数量。"
                if category == "general_qa"
                else "Products 表包含产品编号和名称等字段。"
            )
            return {
                "blocks": [{"request_id": request_id, "kind": "text", "content": text}]
            }
        blocks = [
            {"request_id": request_id, "kind": "text", "content": "已处理当前请求。"}
        ]
        visible = context.get("response_output_names")
        for item in context["spec"]["outputs"]:
            name = item["name"]
            if visible is not None and name not in visible:
                continue
            ref = context["deliveries"].get(name)
            if ref and item["kind"] != "text":
                blocks.append(
                    {"request_id": request_id, "kind": item["kind"], "refs": [ref]}
                )
        return {"blocks": blocks}
