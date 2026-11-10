"""Authorized tools for each Agent role; orchestration only receives tool maps."""

from ..core.model import ARRAY, OBJECT, STRING, tool


def supervisor_tools(
    session_id, context, stores, artifacts, drafts=None, confirmed_refs=None
):
    def request_state(request_id):
        if request_id in (drafts or {}):
            if confirmed_refs is not None:
                confirmed_refs.update(
                    ("request", request_id, item["name"])
                    for item in drafts[request_id]["spec"]["outputs"]
                )
            return {"request": drafts[request_id], "deliveries": {}, "tasks": []}
        context.requests.validate_ref(session_id, {"kind": "request", "id": request_id})
        request = stores.get("requests", request_id)
        if confirmed_refs is not None:
            confirmed_refs.update(
                ("request", request_id, item["name"])
                for item in request["spec"]["outputs"]
            )
        return {
            "request": request,
            "deliveries": context.requests.outputs(request),
            "tasks": stores.list("tasks", "request_id", request_id),
        }

    def task_state(task_id):
        context.requests.validate_ref(session_id, {"kind": "task", "id": task_id})
        return stores.get("tasks", task_id)

    def inspect(artifact_id):
        context.requests.validate_ref(
            session_id, {"kind": "artifact", "id": artifact_id}
        )
        if confirmed_refs is not None:
            confirmed_refs.add(("artifact", artifact_id, None))
        return context.overview(artifacts.inspect(artifact_id))

    def read(artifact_id, selector=None):
        inspect(artifact_id)
        return artifacts.read(artifact_id, selector)

    def event(event_id, block_id=None):
        ref = {"kind": "event", "id": event_id}
        if block_id:
            ref["block_id"] = block_id
        context.requests.validate_ref(session_id, ref)
        return context.requests.resolve(session_id, ref)["event"]

    return {
        "get_request_state": tool(
            "读取候选 Request 的完整当前要求、交付和执行节点",
            request_state,
            request_id=STRING,
        ),
        "get_task_state": tool(
            "读取一个实际执行 Task 的状态和输出", task_state, task_id=STRING
        ),
        "get_artifact_overview": tool(
            "读取产物概览及来源", inspect, artifact_id=STRING
        ),
        "read_artifact": tool(
            "按需读取精确切片/正文；selector.part=sql 可读取原查询 SQL 和绑定口径，不根据摘要猜测",
            read,
            artifact_id=STRING,
            selector=OBJECT,
        ),
        "read_response_event": tool(
            "读取历史事件或具体回答块，解析刚才第二点等引用",
            event,
            event_id=STRING,
            block_id=STRING,
        ),
        "get_available_capabilities": tool(
            "读取当前已启用 Agent 的职责", lambda: dict(context.capabilities)
        ),
        **semantic_tools(context.registry),
    }


def semantic_tools(registry):
    return {
        "search_semantic_registry": tool(
            "检索真实表字段、已发布指标与未批准指标状态", registry.search, query=STRING
        ),
        "get_metric_definition": tool(
            "读取已发布指标口径；未发布明确拒绝绑定", registry.metric, metric_id=STRING
        ),
        "get_relation_paths": tool(
            "读取登记的物理数据关系，不发明关联",
            lambda: {"relations": registry.relations},
        ),
    }


class SpecialistToolbox:
    """Per-execution-task artifact access and role-scoped tool registration."""

    def __init__(self, node, allowed, registry, artifacts, gateway):
        self.node = node
        self.allowed = set(allowed)
        self.created = set()
        self.registry = registry
        self.artifacts = artifacts
        self.gateway = gateway

    def authorize(self, artifact_id):
        if artifact_id not in self.allowed | self.created:
            raise ValueError("仅允许访问当前输入及其来源产物")

    def inspect(self, artifact_id):
        self.authorize(artifact_id)
        return self.artifacts.inspect(artifact_id)

    def read(self, artifact_id, selector=None):
        self.authorize(artifact_id)
        return self.artifacts.read(artifact_id, selector)

    def save(self, kind, title, summary, content, source_ids, payload=None, sql=None):
        for key in source_ids:
            self.authorize(key)
        result = self.artifacts.save(
            self.node,
            kind,
            title,
            summary,
            content,
            list(dict.fromkeys(source_ids)),
            payload,
            sql,
        )
        self.created.add(result["artifact_id"])
        return {**result, "overview": self.artifacts.inspect(result["artifact_id"])}

    def common_tools(self):
        return {
            "inspect_artifact": tool(
                "读取输入产物的概况与完整内容地址", self.inspect, artifact_id=STRING
            ),
            "read_artifact": tool(
                "精确读取输入产物正文或切片；selector 支持 columns/filters/order_by/descending/offset/limit，part=sql 可回读原查询 SQL 和口径",
                self.read,
                artifact_id=STRING,
                selector=OBJECT,
            ),
        }

    def query_tools(self, context):
        bindings = [
            item["semantic_binding"]
            for item in context["inputs"]
            if "semantic_binding" in item
        ]
        if not bindings or any(item["status"] != "bound" for item in bindings):
            raise ValueError("QueryAgent 必须获得明确的已确认语义输入")
        binding = {
            "status": "bound",
            "metrics": [],
            "tables": [],
            "fields": [],
            "relations": [],
        }
        for item in bindings:
            for key in ("metrics", "tables", "fields", "relations"):
                for value in item[key]:
                    if value not in binding[key]:
                        binding[key].append(value)

        def bound_semantics():
            names = {value.lower() for value in binding["tables"]}
            return {
                "binding": bindings,
                "metadata": {
                    name: entry
                    for name, entry in self.registry.tables.items()
                    if name.lower() in names
                },
                "metrics": {
                    key: self.registry.metric(key) for key in binding["metrics"]
                },
                "backend": self.registry.backend,
            }

        def validate(sql):
            return {"sql": self.gateway.validate(sql, binding)}

        def execute(sql, title, summary, entity_id_column=None):
            statement, rows, columns = self.gateway.execute(sql, binding)
            payload = {
                "columns": columns,
                "semantic_binding": bindings,
                "snapshot_id": self.registry.snapshot_id,
                "source_id": self.registry.source_id,
                "semantic_version": self.registry.catalog["version"],
                "catalog_version": self.registry.catalog_version,
                "provenance": {
                    "method": "business_sql",
                    "snapshot_id": self.registry.snapshot_id,
                    "source_id": self.registry.source_id,
                    "semantic_version": self.registry.catalog["version"],
                    "catalog_version": self.registry.catalog_version,
                },
            }
            if entity_id_column:
                if entity_id_column not in columns:
                    raise ValueError("实体 ID 列不存在")
                payload["entity_id_column"] = entity_id_column
            result = self.save(
                "table",
                title,
                summary,
                rows,
                sorted(self.allowed),
                payload,
                statement,
            )
            result["data"] = self.artifacts.read(result["artifact_id"], {"limit": 20})
            return result

        return {
            "get_bound_semantics": tool(
                "读取已绑定的指标定义与查询范围", bound_semantics
            ),
            "validate_sql": tool(
                "按字段、授权表、只读和登记关系校验 SQL", validate, sql=STRING
            ),
            "execute_readonly_sql": tool(
                "执行已绑定语义下的只读 SQL 并持久化 table Artifact；大结果只返回概览与切片",
                execute,
                sql=STRING,
                title=STRING,
                summary=STRING,
                entity_id_column=STRING,
            ),
        }

    def analysis_tools(self):
        def compute(artifact_ids, sql, title, summary):
            for key in artifact_ids:
                self.authorize(key)
            rows, columns = self.artifacts.compute(artifact_ids, sql, with_columns=True)
            result = self.save(
                "table",
                title,
                summary,
                rows,
                artifact_ids,
                {
                    "columns": columns,
                    "computation_sql": sql,
                    "provenance": {"method": "artifact_compute"},
                },
                sql,
            )
            result["data"] = self.artifacts.read(result["artifact_id"], {"limit": 30})
            return result

        def save_analysis(
            title,
            summary,
            body,
            source_ids,
            findings=None,
            conclusion=None,
            next_steps=None,
        ):
            for finding in findings or []:
                for ref in finding.get("refs", []):
                    if ref["kind"] != "artifact":
                        raise ValueError("分析证据需引用输入产物")
                    self.authorize(ref["id"])
            payload = {"findings": findings or []}
            if conclusion:
                payload["conclusion"] = conclusion
            if next_steps:
                payload["next_steps"] = next_steps
            return self.save("text", title, summary, body, source_ids, payload)

        return {
            "compute_artifact": tool(
                "对完整输入数据执行精确 PostgreSQL SELECT；表名为 result_xxx，不连接业务库；派生数据另存产物",
                compute,
                artifact_ids=ARRAY,
                sql=STRING,
                title=STRING,
                summary=STRING,
            ),
            "save_artifact": tool(
                "保存完整分析正文、轻量发现和实际来源，返回 text Artifact 引用",
                save_analysis,
                title=STRING,
                summary=STRING,
                body=STRING,
                source_ids=ARRAY,
                findings={"type": "array", "items": OBJECT},
                conclusion=STRING,
                next_steps=ARRAY,
            ),
        }

    def presentation_tools(self):
        def render_chart(
            source_id, chart_type, x, series, title, horizontal=False, selector=None
        ):
            self.authorize(source_id)
            if chart_type not in ("bar", "line", "scatter"):
                raise ValueError("支持 bar/line/scatter 图表")
            source = self.inspect(source_id)
            if source["content_type"] != "table":
                raise ValueError("图表数据必须来自查询产物")
            columns = {col["name"] for col in source["payload"]["columns"]}
            if (
                x not in columns
                or not series
                or any(item.get("field") not in columns for item in series)
            ):
                raise ValueError("图表字段必须存在于真实数据")
            selection = self.artifacts.read(source_id, selector)
            if selection["has_more"] and not (selector or {}).get("limit"):
                raise ValueError(
                    "数据超过图表展示范围，请明确筛选或先聚合，不能把预览当全体"
                )
            rows = selection["rows"]
            colors = ["#202020", "#666666", "#a0a0a0", "#454545"]
            plotted = []
            for index, item in enumerate(series):
                name, field = item.get("name", item["field"]), item["field"]
                color = item.get("color", colors[index % len(colors)])
                plotted.append(
                    {
                        "name": name,
                        "type": chart_type,
                        "encode": {
                            "x": field if horizontal else x,
                            "y": x if horizontal else field,
                        },
                        "itemStyle": {"color": color},
                        "lineStyle": {"color": color},
                    }
                )
            config = {
                "title": {"text": title},
                "legend": {"data": [item["name"] for item in plotted]},
                "tooltip": {"trigger": "axis"},
                "dataset": {"source": rows},
                "xAxis": {
                    "type": "value"
                    if horizontal or chart_type == "scatter"
                    else "category"
                },
                "yAxis": {"type": "category" if horizontal else "value"},
                "series": plotted,
                "selection": {
                    "source_id": source_id,
                    "selector": selector or {},
                    "total_rows": selection["total_rows"],
                    "displayed_rows": len(rows),
                },
            }
            return self.save(
                "chart",
                title,
                title,
                config,
                [source_id],
                {"chart_type": chart_type, "title": title},
            )

        def render_report(title, body, source_ids):
            return self.save(
                "report",
                title,
                title,
                body,
                source_ids,
                {"title": title, "format": "markdown"},
            )

        def compose_table(title, summary, rows, source_ids, columns=None):
            return self.save(
                "table",
                title,
                summary,
                rows,
                source_ids,
                {"columns": columns or [], "provenance": {"method": "table_compose"}},
            )

        return {
            "compose_table": tool(
                "制作用户要求的展示表，保留真实来源。数值运算交给 compute_artifact，不能编造数据",
                compose_table,
                title=STRING,
                summary=STRING,
                rows={"type": "array", "items": OBJECT},
                source_ids=ARRAY,
                columns=ARRAY,
            ),
            "render_chart": tool(
                "按真实查询列生成 ECharts 配置；保存数据、轴映射、系列名、颜色；支持横向图与多系列",
                render_chart,
                source_id=STRING,
                chart_type=STRING,
                x=STRING,
                series={"type": "array", "items": OBJECT},
                title=STRING,
                horizontal={"type": "boolean"},
                selector=OBJECT,
            ),
            "render_report": tool(
                "保存有实际数据/分析/图表来源引用的完整 Markdown 报告",
                render_report,
                title=STRING,
                body=STRING,
                source_ids=ARRAY,
            ),
        }
