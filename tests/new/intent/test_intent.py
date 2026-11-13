"""Acceptance tests for request identity, revisions, task execution and provenance."""

from pathlib import Path

from pydantic import ValidationError

from enterprise_agent.flow.application import Application
from enterprise_agent.stages.intent.schema import Intent
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class IntentTests(AgentTestCase):
    def test_invalid_self_dependency_is_repaired_with_feedback(self):
        class RepairModel(FixtureModel):
            def plan(self, context, tools):
                if context["stage"] == "planning":
                    return {
                        "reason": "无效自引用",
                        "tasks": [
                            self.node(
                                "query",
                                "QueryAgent",
                                "查询产品",
                                inputs=[
                                    self.binding(
                                        "data",
                                        "request",
                                        context["request"]["request_id"],
                                        "data",
                                    )
                                ],
                                outputs=["dataset"],
                            )
                        ],
                    }
                return super().plan(context, tools)

        engine = Application(Stores(Path(self.directory.name)), RepairModel())
        result = engine.ask(1, "列出前五个产品")
        self.assertEqual(result["status"], "completed", result["blocks"])
        events = engine.stores.list(
            "session_events", "session_id", result["session_id"]
        )
        rejected = [
            event for event in events if "rejected_preparation" in event["payload"]
        ]
        self.assertEqual(len(rejected), 1)
        self.assertIn("不能把本 Request", rejected[0]["payload"]["summary"])

    def test_none_business_knowledge_has_metadata_and_no_tasks(self):
        result = self.completed("有哪些产品表？")
        self.assertEqual(result["tasks"], [])
        self.assertNotIn("SemanticAgent", self.model.calls)
        initial = next(
            context
            for role, context in self.model.contexts
            if role == "UnifiedSupervisor.Intent"
        )
        self.assertIn("products", initial["database_metadata"]["tables"])
        self.assertIn(
            "Products",
            self.engine.artifacts.content(result["blocks"][0]["artifact_id"]),
        )

    def test_request_cycles_rejected_before_business_records(self):
        intent = Intent.model_validate(
            {
                "operations": [
                    self.model.operation(
                        "a",
                        "create_task",
                        "data_analysis",
                        "A",
                        [{"kind": "request", "id": "b", "output": "data"}],
                    ),
                    self.model.operation(
                        "b",
                        "create_task",
                        "data_analysis",
                        "B",
                        [{"kind": "request", "id": "a", "output": "data"}],
                    ),
                ]
            }
        )
        session = self.engine.stores.session(1)
        with self.assertRaises(ValueError):
            self.engine.requests.normalize(session["session_id"], intent)
        self.assertEqual(
            self.engine.stores.list("requests", "session_id", session["session_id"]), []
        )

    def test_intent_failure_is_a_session_owned_text_response(self):
        class Failing(FixtureModel):
            def intent(self, context, tools):
                raise ValueError("暂时无法识别意图")

        engine = Application(
            Stores(Path(self.directory.name) / "intent-error"), Failing()
        )
        result = engine.ask(1, "查询产品")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["requests"])
        self.assertIn(
            "暂时无法识别意图",
            engine.artifacts.content(result["blocks"][0]["artifact_id"]),
        )
        self.assertEqual(result["artifacts"][0]["producer"], "System")

    def test_intent_routes_and_reference_boundaries(self):
        for category in ("general_qa", "business_knowledge"):
            Intent.model_validate(
                {"operations": [self.model.operation("new", "none", category, "问题")]}
            )
            with self.assertRaises(ValidationError):
                Intent.model_validate(
                    {
                        "operations": [
                            self.model.operation("new", "create_task", category, "问题")
                        ]
                    }
                )
        for category in ("data_analysis", "result_interaction"):
            with self.assertRaises(ValidationError):
                Intent.model_validate(
                    {
                        "operations": [
                            self.model.operation("new", "none", category, "问题")
                        ]
                    }
                )
        for ref in (
            {"kind": "event", "id": "event_001"},
            {"kind": "task", "id": "task_001"},
            {"kind": "request", "id": "req_001"},
        ):
            with self.assertRaises(ValidationError):
                Intent.model_validate(
                    {
                        "operations": [
                            self.model.operation(
                                "new", "none", "general_qa", "问题", [ref]
                            )
                        ]
                    }
                )

    def test_missing_intent_reference_is_rejected_before_persistence(self):
        class Omitting(FixtureModel):
            @staticmethod
            def definition(context):
                spec = FixtureModel.definition(context)
                if context["operation"]["instruction"] == "根据上游绘图":
                    spec["inputs"] = []
                return spec

        engine = Application(Stores(Path(self.directory.name) / "omit"), Omitting())
        result = engine.ask(1, "先给统计结果画图，再列出前五个产品")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["title"], "列出前五个产品")
