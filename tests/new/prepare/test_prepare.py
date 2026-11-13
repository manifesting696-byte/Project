"""Acceptance tests for request identity, revisions, task execution and provenance."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from enterprise_agent.domain.errors import VersionConflict
from enterprise_agent.flow.application import Application
from enterprise_agent.stages.prepare.schema import RequestPreparation
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class PrepareTests(AgentTestCase):
    def test_request_spec_is_complete_and_has_one_stored_source(self):
        result = self.completed()
        request = result["requests"][0]
        self.assertNotIn("results", request)
        self.assertEqual(request["spec"], result["revisions"][0]["spec"])
        self.assertEqual(
            self.engine.stores.list("requests", "session_id", result["session_id"]),
            [request],
        )
        with self.engine.stores.db() as db:
            stored = db.execute(
                "SELECT body FROM requests WHERE id=%s", (request["request_id"],)
            ).fetchone()[0]
        self.assertNotIn("spec", stored)
        self.assertNotIn("results", stored)
        edited = deepcopy(request)
        edited["spec"]["parameters"]["limit"] = 100
        with self.assertRaises(ValueError):
            self.engine.stores.put("requests", edited)
        self.assertEqual(
            self.engine.stores.get("requests", request["request_id"]), request
        )

    def test_spec_dependency_cycles_are_rejected_before_execution(self):
        class CyclicSpecModel(FixtureModel):
            def intent(self, context, tools):
                return {
                    "operations": [
                        self.operation("a", "create_task", "data_analysis", "A"),
                        self.operation("b", "create_task", "data_analysis", "B"),
                    ]
                }

            @staticmethod
            def definition(context):
                other = (
                    "req_002"
                    if context["operation"]["instruction"] == "A"
                    else "req_001"
                )
                return {
                    "description": context["operation"]["instruction"],
                    "inputs": [
                        FixtureModel.binding("upstream", "request", other, "data")
                    ],
                    "outputs": [
                        {"name": "data", "content_type": "table", "description": "Data"}
                    ],
                }

        self.model = CyclicSpecModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        result = self.engine.ask(1, "create cyclic requirements")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["tasks"])
        self.assertTrue(
            all(item["content_type"] == "text" for item in result["artifacts"])
        )
        self.assertEqual(
            self.engine.stores.list("requests", "session_id", result["session_id"]), []
        )
        self.assertNotIn("QueryAgent", self.model.calls)

    def test_invalid_input_preserves_independent_request_execution(self):
        class PartialModel(FixtureModel):
            def intent(self, context, tools):
                return {
                    "operations": [
                        self.operation(
                            "invalid", "create_task", "data_analysis", "invalid-input"
                        ),
                        self.operation(
                            "valid", "create_task", "data_analysis", "列出前五个产品"
                        ),
                    ]
                }

            @staticmethod
            def definition(context):
                spec = FixtureModel.definition(context)
                if context["operation"]["instruction"] == "invalid-input":
                    spec["inputs"] = [
                        FixtureModel.binding(
                            "missing", "request", "req_missing", "data"
                        )
                    ]
                return spec

        self.model = PartialModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        result = self.engine.ask(1, "one invalid and one valid request")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["status"], "completed")
        self.assertEqual(self.model.calls.count("QueryAgent"), 1)

    def test_request_parameters_are_independent(self):
        result = self.completed("分别列出前两个和前三个产品")
        self.assertEqual(
            [
                revision["spec"]["parameters"]["limit"]
                for revision in result["revisions"]
            ],
            [2, 3],
        )
        self.assertEqual(
            [
                len(
                    self.engine.artifacts.content(
                        self.engine.requests.outputs(item)["data"]["id"]
                    )
                )
                for item in result["requests"]
            ],
            [2, 3],
        )

    def test_atomic_revision_compare_and_swap(self):
        result = self.completed()
        request = result["requests"][0]
        revision = deepcopy(result["revisions"][0])
        revision["revision_id"] = "rev_conflict"
        changed = {**request, "current_revision_id": "rev_conflict"}
        with self.assertRaises(VersionConflict):
            self.engine.stores.save_many(
                [("request_revisions", revision), ("requests", changed)],
                expected_revisions={request["request_id"]: "rev_missing"},
            )
        self.assertFalse(self.engine.stores.exists("request_revisions", "rev_conflict"))
        self.assertEqual(
            self.engine.stores.get("requests", request["request_id"])[
                "current_revision_id"
            ],
            request["current_revision_id"],
        )

    def test_initial_spec_and_plan_use_one_preparation_call(self):
        first = self.completed()
        self.assertEqual(self.model.calls.count("UnifiedSupervisor.Preparation"), 1)
        self.assertNotIn("UnifiedSupervisor.Definition", self.model.calls)
        self.assertNotIn("UnifiedSupervisor.Planning", self.model.calls)
        second = self.completed("只统计前两个产品", first["session_id"])
        self.assertEqual(self.model.calls.count("UnifiedSupervisor.Preparation"), 2)
        self.assertNotIn("UnifiedSupervisor.Planning", self.model.calls)
        self.assertEqual(
            first["requests"][0]["request_id"], second["requests"][0]["request_id"]
        )

    def test_preparation_model_requires_complete_result_or_clarifications(self):
        for value in (
            {},
            {
                "spec": {
                    "description": "Test",
                    "outputs": [
                        {"name": "data", "content_type": "table", "description": "Data"}
                    ],
                }
            },
            {"plan": {"reason": "Only plan"}},
        ):
            with self.assertRaises(ValidationError):
                RequestPreparation.model_validate(value)
        RequestPreparation.model_validate(
            {"clarifications": [{"subject": "范围", "question": "需要哪一年？"}]}
        )

    def test_invalid_initial_update_keeps_old_revision_and_tasks(self):
        class InvalidUpdate(FixtureModel):
            def plan(self, context, tools):
                if context["spec"]["parameters"].get("limit") == 2:
                    return {
                        "tasks": [],
                        "output_bindings": {
                            "data": {
                                "kind": "task",
                                "id": "missing_task",
                                "output": "dataset",
                            }
                        },
                        "reason": "无效计划",
                    }
                return super().plan(context, tools)

        self.model = InvalidUpdate()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed()
        before = deepcopy(first["requests"][0])
        tasks = deepcopy(first["tasks"])
        result = self.engine.ask(1, "只统计前两个产品", first["session_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            self.engine.stores.get("requests", before["request_id"]), before
        )
        self.assertEqual(
            self.engine.stores.list("tasks", "request_id", before["request_id"]), tasks
        )
        self.assertEqual(
            len(
                self.engine.stores.list(
                    "request_revisions", "request_id", before["request_id"]
                )
            ),
            1,
        )

    def test_revision_and_initial_plan_roll_back_together(self):
        original = self.engine.stores._append_event

        def reject_plan(db, *args, **kwargs):
            event_type = kwargs.get("event_type", args[2] if len(args) > 2 else None)
            if event_type == "plan_updated":
                raise ValueError("模拟计划事件写入失败")
            return original(db, *args, **kwargs)

        with patch.object(self.engine.stores, "_append_event", side_effect=reject_plan):
            result = self.engine.ask(1, "列出前五个产品")
        self.assertEqual(result["status"], "failed")
        with self.engine.stores.db() as db:
            for table in ("requests", "request_revisions", "tasks"):
                self.assertEqual(
                    db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
                )
        events = self.engine.stores.list(
            "session_events", "session_id", result["session_id"]
        )
        self.assertFalse(
            any(event["event_type"] == "request_revised" for event in events)
        )

    def test_preparation_clarification_preserves_existing_work(self):
        class Clarifying(FixtureModel):
            def prepare(self, context, tools):
                if context["previous_revision"]:
                    return {
                        "clarifications": [
                            {"subject": "统计范围", "question": "需要统计哪个年份？"}
                        ]
                    }
                return super().prepare(context, tools)

        self.model = Clarifying()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed()
        before = first["requests"][0]
        result = self.engine.ask(1, "只统计前两个产品", first["session_id"])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(
            self.engine.stores.get("requests", before["request_id"]), before
        )
        self.assertEqual(
            self.engine.artifacts.content(result["blocks"][0]["artifact_id"]),
            "需要统计哪个年份？",
        )

    def test_plan_version_conflict_prevents_initial_commit(self):
        first = self.completed()
        key = first["requests"][0]["request_id"]
        original = self.engine.preparation.commit

        def concurrent_change(bundle):
            request = self.engine.stores.get("requests", key)
            request["plan_version"] += 1
            self.engine.stores.put("requests", request)
            return original(bundle)

        with patch.object(
            self.engine.preparation, "commit", side_effect=concurrent_change
        ):
            result = self.engine.ask(1, "只统计前两个产品", first["session_id"])
        self.assertEqual(result["status"], "failed")
        current = self.engine.stores.get("requests", key)
        self.assertEqual(
            current["current_revision_id"], first["requests"][0]["current_revision_id"]
        )
        self.assertEqual(
            current["plan_version"], first["requests"][0]["plan_version"] + 1
        )
        self.assertEqual(current["status"], "completed")

    def test_failed_upstream_preparation_blocks_consumers_and_keeps_independent_work(
        self,
    ):
        class InvalidUpstream(FixtureModel):
            def intent(self, context, tools):
                value = super().intent(context, tools)
                value["operations"].append(
                    self.operation("mean", "none", "general_qa", "解释平均值")
                )
                return value

            def plan(self, context, tools):
                if context["spec"]["description"] == "列出前五个产品":
                    return {
                        "tasks": [],
                        "output_bindings": {
                            "data": {
                                "kind": "task",
                                "id": "missing",
                                "output": "dataset",
                            }
                        },
                        "reason": "无效上游计划",
                    }
                return super().plan(context, tools)

        engine = Application(
            Stores(Path(self.directory.name) / "blocked"), InvalidUpstream()
        )
        result = engine.ask(1, "先给统计结果画图，再列出前五个产品")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(result["requests"][0]["spec"]["description"], "解释平均值")
        self.assertFalse(result["tasks"])

    def test_state_changed_during_preparation_does_not_retry_stale_work(self):
        first = self.completed()
        key = first["requests"][0]["request_id"]
        original = self.engine.supervisor.prepare
        changed = False

        def concurrent_change(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            if not changed:
                request = self.engine.stores.get("requests", key)
                request["plan_version"] += 1
                self.engine.stores.put("requests", request)
                changed = True
            return result

        with patch.object(
            self.engine.supervisor, "prepare", side_effect=concurrent_change
        ):
            result = self.engine.ask(1, "只统计前两个产品", first["session_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.model.calls.count("UnifiedSupervisor.Preparation"), 2)
        self.assertEqual(
            result["requests"][0]["plan_version"],
            first["requests"][0]["plan_version"] + 1,
        )

    def test_failed_upstream_commit_does_not_save_dependent_plan(self):
        original = self.engine.stores._append_event

        def reject_plan(db, *args, **kwargs):
            event_type = kwargs.get("event_type", args[2] if len(args) > 2 else None)
            if event_type == "plan_updated":
                raise ValueError("模拟上游计划事件写入失败")
            return original(db, *args, **kwargs)

        with patch.object(self.engine.stores, "_append_event", side_effect=reject_plan):
            result = self.engine.ask(1, "先给统计结果画图，再列出前五个产品")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["requests"])
        self.assertFalse(result["tasks"])
        self.assertEqual(len(result["blocks"]), 2)
