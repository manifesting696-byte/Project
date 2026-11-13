"""Acceptance tests for request identity, revisions, task execution and provenance."""

from pathlib import Path

from pydantic import ValidationError

from enterprise_agent.domain.refs import ResourceRef
from enterprise_agent.flow.application import Application
from enterprise_agent.query.validate import validate_readonly
from enterprise_agent.stages.intent.schema import Intent
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class VerifyTests(AgentTestCase):
    def test_derived_table_can_be_delivered_without_querying_business_db_again(self):
        class DerivedModel(FixtureModel):
            def intent(self, context, tools):
                if context["query"] == "保留刚才前两个产品":
                    request = context["candidate_requests"][-1]
                    return {
                        "operations": [
                            self.operation(
                                request["request_id"],
                                "update_task",
                                "result_interaction",
                                context["query"],
                                [
                                    tools["get_request_state"].call(
                                        request_id=request["request_id"]
                                    )["deliveries"]["data"]
                                ],
                            )
                        ],
                        "clarifications": [],
                    }
                return super().intent(context, tools)

            @staticmethod
            def definition(context):
                if context["operation"]["instruction"] == "保留刚才前两个产品":
                    return {
                        "description": "从已有结果保留前两个产品",
                        "parameters": {},
                        "inputs": [
                            {
                                "slot": "source_data",
                                "source": context["operation"]["refs"][0],
                            }
                        ],
                        "outputs": [
                            {
                                "name": "data",
                                "content_type": "table",
                                "description": "前两个产品",
                            }
                        ],
                    }
                return FixtureModel.definition(context)

            def plan(self, context, tools):
                if context["spec"]["description"] == "从已有结果保留前两个产品":
                    return {
                        "reason": "精确计算历史数据",
                        "tasks": [
                            self.node(
                                "filter",
                                "AnalysisAgent",
                                "保留前两行",
                                inputs=context["spec"]["inputs"],
                                outputs=["filtered"],
                                params={"filter_only": True},
                            )
                        ],
                        "output_bindings": {
                            "data": {
                                "kind": "task",
                                "id": "filter",
                                "output": "filtered",
                            }
                        },
                    }
                return super().plan(context, tools)

            def run(self, role, instruction, context, output, tools=None, emit=None):
                if role == "AnalysisAgent" and context["task"]["params"].get(
                    "filter_only"
                ):
                    self.calls.append(role)
                    key = context["inputs"][0]["artifact_id"]
                    saved = tools["compute_artifact"].call(
                        artifact_ids=[key],
                        sql=f'SELECT * FROM "{key}" ORDER BY product_id LIMIT 2',
                        title="前两个产品",
                        summary="精确筛选两行",
                    )
                    return output.model_validate(
                        {
                            "summary": "计算完成",
                            "produced_outputs": {
                                "filtered": {
                                    "kind": "artifact",
                                    "id": saved["artifact_id"],
                                }
                            },
                        }
                    )
                return super().run(role, instruction, context, output, tools, emit)

        self.model = DerivedModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed()
        second = self.completed("保留刚才前两个产品", first["session_id"])
        output = self.engine.artifacts.inspect(
            self.engine.requests.outputs(second["requests"][0])["data"]["id"]
        )
        self.assertEqual(self.model.calls.count("QueryAgent"), 1)
        self.assertEqual(output["payload"]["provenance"]["method"], "artifact_compute")
        self.assertEqual(len(self.engine.artifacts.content(output["artifact_id"])), 2)

    def test_provenance_distinguishes_business_sql_and_computation(self):
        result = self.completed("统计产品并生成图表和报告")
        data = [item for item in result["artifacts"] if item["content_type"] == "table"]
        self.assertEqual(
            {item["payload"]["provenance"]["method"] for item in data},
            {"business_sql", "artifact_compute"},
        )
        derived = next(
            item
            for item in data
            if item["payload"]["provenance"]["method"] == "artifact_compute"
        )
        self.assertTrue(derived["source_artifact_ids"])
        self.assertIn("COUNT", self.engine.artifacts.sql(derived["artifact_id"]))
        raw = next(
            item
            for item in data
            if item["payload"]["provenance"]["method"] == "business_sql"
        )
        self.assertEqual(
            raw["payload"]["provenance"]["snapshot_id"], result["snapshot_id"]
        )

    def test_contracts_ownership_and_readonly(self):
        first = self.completed()
        with self.assertRaises(PermissionError):
            self.engine.stores.owned_request(2, first["requests"][0]["request_id"])
        other = self.engine.ask(2, "什么是平均值？")
        with self.assertRaises(PermissionError):
            self.engine.requests.validate_ref(
                other["session_id"],
                first["deliveries"][first["requests"][0]["request_id"]]["data"],
            )
        with self.assertRaises(ValidationError):
            ResourceRef(kind="artifact", id="result_001", output="dataset")
        with self.assertRaises(ValidationError):
            ResourceRef(kind="task", id="task_001", block_id="block_001")
        with self.assertRaises(ValidationError):
            Intent.model_validate({"dialogue_action": "new_task"})
        with self.assertRaises(ValueError):
            validate_readonly("DELETE FROM Products", {"Products": ["product_id"]})
        with self.assertRaises(ValueError):
            validate_readonly(
                "SELECT invented FROM Products", {"Products": ["product_id"]}
            )
        with self.assertRaises(ValueError):
            self.engine.registry.metric("on_time_rate")
