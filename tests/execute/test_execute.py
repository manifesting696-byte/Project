"""Acceptance tests for request identity, revisions, task execution and provenance."""

from pathlib import Path

from enterprise_agent.flow.application import Application
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class ExecuteTests(AgentTestCase):
    def test_multiple_operations_and_five_agents(self):
        result = self.completed("解释平均值，再统计产品并生成图表和报告")
        self.assertEqual(len(result["requests"]), 2)
        mean, work = result["requests"]
        self.assertFalse(
            self.engine.stores.list("tasks", "request_id", mean["request_id"])
        )
        self.assertEqual(
            {node["assigned_agent"] for node in result["tasks"]},
            {"SemanticAgent", "QueryAgent", "AnalysisAgent", "PresentationAgent"},
        )
        self.assertEqual(work["plan_version"], 2)
        self.assertEqual(
            set(self.engine.requests.outputs(work)), {"data", "chart", "report"}
        )
        self.assertEqual(result["blocks"][0]["request_id"], mean["request_id"])
        self.assertEqual(
            {item["content_type"] for item in result["artifacts"]},
            {"table", "text", "chart", "report"},
        )
        self.assertIn("UnifiedSupervisor.Preparation", self.model.calls)

    def test_update_data_keeps_identity_and_requeries(self):
        first = self.completed()
        second = self.completed("只统计前两个产品", first["session_id"])
        self.assertEqual(
            first["requests"][0]["request_id"], second["requests"][0]["request_id"]
        )
        self.assertEqual(self.model.calls.count("QueryAgent"), 2)
        self.assertEqual(
            len(
                self.engine.artifacts.content(
                    self.engine.requests.outputs(second["requests"][0])["data"]["id"]
                )
            ),
            2,
        )
        self.assertTrue(any(node["status"] == "superseded" for node in second["tasks"]))
        self.assertNotEqual(
            self.engine.requests.outputs(first["requests"][0]).get("data"),
            second["deliveries"][second["requests"][0]["request_id"]]["data"],
        )
        self.assertEqual(
            len(
                self.engine.artifacts.content(
                    first["deliveries"][first["requests"][0]["request_id"]]["data"][
                        "id"
                    ]
                )
            ),
            5,
        )

    def test_fixed_artifact_input_survives_upstream_update(self):
        class HistoryModel(FixtureModel):
            def intent(self, context, tools):
                if context["query"] == "use-history":
                    return {
                        "operations": [
                            self.operation(
                                "history",
                                "create_task",
                                "result_interaction",
                                "根据上游绘图",
                                [{"kind": "artifact", "id": self.historical_artifact}],
                            )
                        ]
                    }
                if context["query"] == "update-source":
                    return {
                        "operations": [
                            self.operation(
                                self.source_request,
                                "update_task",
                                "data_analysis",
                                "只统计前两个产品",
                            )
                        ]
                    }
                return super().intent(context, tools)

        self.model = HistoryModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed("先给统计结果画图，再列出前五个产品")
        source = next(
            item for item in first["requests"] if item["title"] == "列出前五个产品"
        )
        dynamic = next(
            item for item in first["requests"] if item["title"] == "绘制产品图"
        )
        self.model.source_request = source["request_id"]
        self.model.historical_artifact = first["deliveries"][source["request_id"]][
            "data"
        ]["id"]
        second = self.completed("use-history", first["session_id"])
        fixed = second["requests"][0]
        chart_id = second["deliveries"][fixed["request_id"]]["chart"]["id"]
        self.completed("update-source", first["session_id"])
        current_dynamic = self.engine.stores.get("requests", dynamic["request_id"])
        current_fixed = self.engine.stores.get("requests", fixed["request_id"])
        self.assertEqual(current_dynamic["status"], "pending")
        self.assertEqual(self.engine.requests.outputs(current_dynamic), {})
        self.assertEqual(current_fixed["status"], "completed")
        self.assertEqual(
            self.engine.requests.outputs(current_fixed)["chart"]["id"], chart_id
        )
        self.assertEqual(
            len(self.engine.artifacts.content(self.model.historical_artifact)), 5
        )
        self.assertEqual(
            len(self.engine.artifacts.content(chart_id)["dataset"]["source"]), 5
        )
        later = self.completed("use-history", first["session_id"])
        later_chart = later["deliveries"][later["requests"][0]["request_id"]]["chart"][
            "id"
        ]
        self.assertEqual(
            len(self.engine.artifacts.content(later_chart)["dataset"]["source"]), 5
        )
        self.assertEqual(self.model.calls.count("QueryAgent"), 2)

    def test_same_turn_request_dependency_is_ordered_and_materialized(self):
        result = self.completed("先给统计结果画图，再列出前五个产品")
        requests = {item["title"]: item for item in result["requests"]}
        chart_request = requests["绘制产品图"]
        data_request = requests["列出前五个产品"]
        self.assertEqual(
            chart_request["spec"]["inputs"][0]["source"],
            {
                "kind": "request",
                "id": data_request["request_id"],
                "output": "data",
            },
        )
        last_definition = max(
            index
            for index, role in enumerate(self.model.calls)
            if role == "UnifiedSupervisor.Preparation"
        )
        first_execution = min(
            index
            for index, role in enumerate(self.model.calls)
            if role in ("SemanticAgent", "QueryAgent", "PresentationAgent")
        )
        self.assertLess(last_definition, first_execution)
        self.assertEqual(result["blocks"][0]["request_id"], chart_request["request_id"])
        task = next(
            node
            for node in result["tasks"]
            if node["assigned_agent"] == "PresentationAgent"
        )
        self.assertEqual(
            task["input_bindings"][0]["source"],
            self.engine.requests.outputs(data_request)["data"],
        )
        chart = self.engine.artifacts.inspect(
            self.engine.requests.outputs(chart_request)["chart"]["id"]
        )
        self.assertEqual(
            chart["source_artifact_ids"],
            [self.engine.requests.outputs(data_request)["data"]["id"]],
        )

    def test_cross_request_update_invalidates_dependent_result(self):
        first = self.completed("先给统计结果画图，再列出前五个产品")
        data = next(
            item for item in first["requests"] if item["title"] == "列出前五个产品"
        )
        chart = next(
            item for item in first["requests"] if item["title"] == "绘制产品图"
        )
        # Select the source explicitly so the fixture updates it rather than the last chart.
        original_intent = self.model.intent

        def intent(context, tools):
            if context["query"] == "只统计前两个产品":
                return {
                    "operations": [
                        self.model.operation(
                            data["request_id"],
                            "update_task",
                            "data_analysis",
                            context["query"],
                        )
                    ],
                    "clarifications": [],
                }
            return original_intent(context, tools)

        self.model.intent = intent
        second = self.completed("只统计前两个产品", first["session_id"])
        dependent = self.engine.stores.get("requests", chart["request_id"])
        self.assertEqual(dependent["status"], "pending")
        self.assertEqual(self.engine.requests.outputs(dependent), {})
        self.assertTrue(
            any(
                node["status"] == "superseded"
                for node in self.engine.stores.list(
                    "tasks", "request_id", chart["request_id"]
                )
            )
        )
        self.assertEqual(
            {block["request_id"] for block in second["blocks"]}, {data["request_id"]}
        )

    def test_composed_table_preserves_sources_and_content(self):
        from enterprise_agent.stages.execute.tools.artifact import SpecialistToolbox

        result = self.completed()
        request = result["requests"][0]
        key = result["deliveries"][request["request_id"]]["data"]["id"]
        original = self.engine.artifacts.content(key)
        node = {**result["tasks"][-1], "assigned_agent": "PresentationAgent"}
        toolbox = SpecialistToolbox(
            node,
            {key},
            self.engine.registry,
            self.engine.artifacts,
            self.engine.query_gateway,
        )
        saved = toolbox.presentation_tools()["compose_table"].call(
            title="展示表",
            summary="重排字段",
            rows=[{"产品": row["product_id"]} for row in original],
            source_ids=[key],
        )
        item = self.engine.artifacts.inspect(saved["artifact_id"])
        self.assertEqual(item["content_type"], "table")
        self.assertEqual(item["payload"]["provenance"]["method"], "table_compose")
        self.assertEqual(item["source_artifact_ids"], [key])
        self.assertEqual(self.engine.artifacts.content(key), original)
        self.assertEqual(item["payload"]["row_count"], len(original))
        self.assertIsNone(self.engine.artifacts.sql(item["artifact_id"]))
        empty = toolbox.analysis_tools()["compute_artifact"].call(
            artifact_ids=[key],
            sql=f'SELECT product_id AS id FROM "{key}" WHERE FALSE',
            title="空表",
            summary="筛选无记录",
        )
        metadata = self.engine.artifacts.inspect(empty["artifact_id"])
        self.assertEqual(metadata["payload"]["row_count"], 0)
        self.assertEqual(
            [column["name"] for column in metadata["payload"]["columns"]], ["id"]
        )
        before = set(self.engine.artifacts.root.iterdir())
        with self.assertRaises(ValueError):
            toolbox.presentation_tools()["compose_table"].call(
                title="无效表",
                summary="无效",
                rows=[{"a": 1}, {"b": 2}],
                source_ids=[key],
            )
        self.assertEqual(set(self.engine.artifacts.root.iterdir()), before)

    def test_pending_request_inputs_are_saved_and_wait_before_execution(self):
        result = self.engine.ask(1, "先给统计结果画图，再列出前五个产品", preview=True)
        self.assertEqual(result["status"], "waiting_input")
        source = next(
            item for item in result["requests"] if item["title"] == "列出前五个产品"
        )
        consumer = next(
            item for item in result["requests"] if item["title"] == "绘制产品图"
        )
        task = next(
            node
            for node in result["tasks"]
            if node["request_id"] == consumer["request_id"]
        )
        self.assertEqual(
            task["input_bindings"][0]["source"],
            {"kind": "request", "id": source["request_id"], "output": "data"},
        )
        waiting = self.engine.resume(1, consumer["request_id"])
        self.assertEqual(waiting["status"], "needs_clarification")
        self.assertNotIn("PresentationAgent", self.model.calls)
        self.assertTrue(
            all(
                node["status"] == "pending"
                for node in self.engine.stores.list(
                    "tasks", "request_id", consumer["request_id"]
                )
            )
        )
        self.assertEqual(
            self.engine.resume(1, source["request_id"])["status"], "completed"
        )
        complete = self.engine.resume(1, consumer["request_id"])
        self.assertEqual(complete["status"], "completed")
        task = self.engine.stores.get("tasks", task["task_id"])
        self.assertEqual(task["input_bindings"][0]["source"]["kind"], "artifact")
