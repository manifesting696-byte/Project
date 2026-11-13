"""Acceptance tests for request identity, revisions, task execution and provenance."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from enterprise_agent.flow.application import Application
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class FlowTests(AgentTestCase):
    def test_interrupted_execution_can_be_recovered_without_overwriting_artifacts(self):
        result = self.completed()
        request = result["requests"][0]
        task = next(
            node for node in result["tasks"] if node["assigned_agent"] == "QueryAgent"
        )
        artifact_id = self.engine.requests.outputs(request)["data"]["id"]
        before = self.engine.artifacts.content(artifact_id)
        request["status"], task["status"] = "running", "running"
        self.engine.stores.save_many([("requests", request), ("tasks", task)])
        self.engine.stores.recover_interrupted()
        self.assertEqual(
            self.engine.stores.get("requests", request["request_id"])["status"],
            "pending",
        )
        self.assertEqual(
            self.engine.stores.get("tasks", task["task_id"])["status"], "failed"
        )
        self.assertEqual(self.engine.artifacts.content(artifact_id), before)

    def test_resume_failure_is_published_as_text_artifact(self):
        result = self.completed()
        request = result["requests"][0]
        with patch.object(
            self.engine.supervisor, "answer", side_effect=ValueError("回复生成失败")
        ):
            resumed = self.engine.resume(
                1, request["request_id"], continue_analysis=False
            )
        self.assertEqual(resumed["status"], "failed")
        block = resumed["blocks"][0]
        item = self.engine.artifacts.inspect(block["artifact_id"])
        self.assertEqual(item["content_type"], "text")
        self.assertEqual(item["producer"], "System")
        self.assertIn(
            "回复生成失败", self.engine.artifacts.content(item["artifact_id"])
        )

    def test_langgraph_preview_resumes_after_application_restart(self):
        preview = self.engine.ask(1, "统计产品并生成图表和报告", preview=True)
        request = preview["requests"][0]
        snapshot = self.engine.execution.snapshot(request)
        self.assertIn("wait", snapshot.next)
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        self.assertTrue(self.engine.checkpoints.backend == "postgres")
        model = FixtureModel()
        restarted = Application(Stores(Path(self.directory.name)), model)
        result = restarted.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(
            result["requests"][0]["current_revision_id"], request["current_revision_id"]
        )
        self.assertEqual(model.calls.count("QueryAgent"), 1)
        self.assertFalse(restarted.execution.snapshot(result["requests"][0]).next)

    def test_langgraph_send_executes_independent_tasks_in_parallel(self):
        from threading import Barrier

        class ParallelModel(FixtureModel):
            @staticmethod
            def definition(context):
                return {
                    "description": "并行查询",
                    "parameters": {"limit": 2},
                    "outputs": [
                        {"name": name, "content_type": "table", "description": name}
                        for name in ("first", "second")
                    ],
                }

            def plan(self, context, tools):
                nodes = [self.node("semantic", "SemanticAgent", "确认字段")]
                for name in ("first", "second"):
                    nodes.append(
                        self.node(
                            name,
                            "QueryAgent",
                            name,
                            deps=["semantic"],
                            inputs=[
                                self.binding(
                                    "semantics", "task", "semantic", "semantic_binding"
                                )
                            ],
                            outputs=["dataset"],
                        )
                    )
                return {
                    "tasks": nodes,
                    "output_bindings": {
                        name: {"kind": "task", "id": name, "output": "dataset"}
                        for name in ("first", "second")
                    },
                    "reason": "独立查询并行",
                }

        self.model = ParallelModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        delegate = self.engine.specialists.agents["QueryAgent"]
        barrier = Barrier(2)

        class ParallelQuery:
            def execute(self, context, allowed, emit):
                barrier.wait(timeout=15)
                return delegate.execute(context, allowed, emit)

        self.engine.specialists.agents["QueryAgent"] = ParallelQuery()
        result = self.completed("并行查询")
        self.assertEqual(self.model.calls.count("QueryAgent"), 2)
        self.assertEqual(
            set(result["deliveries"][result["requests"][0]["request_id"]]),
            {"first", "second"},
        )

    def test_langgraph_batch_pause_continues_without_repeating_completed_tasks(self):
        first = self.engine.ask(1, "统计产品并生成图表和报告", pause=True)
        self.assertEqual(first["status"], "waiting_input")
        request = first["requests"][0]
        self.assertEqual(self.model.calls.count("SemanticAgent"), 1)
        result = self.engine.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(self.model.calls.count("SemanticAgent"), 1)
        self.assertEqual(self.model.calls.count("QueryAgent"), 1)

    def test_langgraph_crash_replays_inflight_task_and_reuses_saved_artifact(self):
        delegate = self.engine.specialists.agents["QueryAgent"]

        class CrashingQuery:
            def execute(self, context, allowed, emit):
                delegate.execute(context, allowed, emit)
                raise SystemExit("模拟进程在产物保存后退出")

        self.engine.specialists.agents["QueryAgent"] = CrashingQuery()
        with self.assertRaises(SystemExit):
            self.engine.ask(1, "列出前五个产品")
        sessions = self.engine.stores.list("sessions", "user_id", 1)
        request = self.engine.stores.list(
            "requests", "session_id", sessions[0]["session_id"]
        )[0]
        original = next(
            item
            for item in self.engine.stores.list(
                "artifacts", "request_id", request["request_id"]
            )
            if item["content_type"] == "table"
        )
        before = self.engine.artifacts.content(original["artifact_id"])
        model = FixtureModel()
        restarted = Application(Stores(Path(self.directory.name)), model)
        restarted.recover_interrupted()
        task = next(
            item
            for item in restarted.stores.list(
                "tasks", "request_id", request["request_id"]
            )
            if item["assigned_agent"] == "QueryAgent"
        )
        self.assertEqual(task["status"], "running")
        result = restarted.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(
            result["deliveries"][request["request_id"]]["data"]["id"],
            original["artifact_id"],
        )
        self.assertEqual(restarted.artifacts.content(original["artifact_id"]), before)
        tables = [
            item for item in result["artifacts"] if item["content_type"] == "table"
        ]
        self.assertEqual(len(tables), 1)
        self.assertEqual(model.calls.count("SemanticAgent"), 0)

    def test_langgraph_recovery_keeps_completed_task_replanning_evidence(self):
        original = self.engine.stores.save_many

        def crash_after_task(*args, **kwargs):
            result = original(*args, **kwargs)
            event = kwargs.get("event") or {}
            if event.get("actor") == "AnalysisAgent" and event["payload"].get(
                "execution_key"
            ):
                raise SystemExit("模拟 Task 完成后主图尚未保存")
            return result

        with patch.object(
            self.engine.stores, "save_many", side_effect=crash_after_task
        ):
            with self.assertRaises(SystemExit):
                self.engine.ask(1, "统计产品并生成图表和报告")
        session = self.engine.stores.list("sessions", "user_id", 1)[0]
        request = self.engine.stores.list(
            "requests", "session_id", session["session_id"]
        )[0]
        model = FixtureModel()
        restarted = Application(Stores(Path(self.directory.name)), model)
        with patch.object(
            restarted.supervisor, "plan", wraps=restarted.supervisor.plan
        ) as planner:
            result = restarted.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(model.calls.count("AnalysisAgent"), 0)
        evidence = planner.call_args_list[0].args[2]
        self.assertTrue(any(item.get("replan_after") for item in evidence))
        self.assertTrue(any(item.get("produced_outputs") for item in evidence))

    def test_langgraph_replan_commit_is_reused_after_checkpoint_gap(self):
        original = self.engine.apply_plan

        def crash_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("模拟重规划保存后中断")

        with patch.object(self.engine, "apply_plan", side_effect=crash_after_commit):
            first = self.engine.ask(1, "统计产品并生成图表和报告")
        self.assertEqual(first["status"], "failed")
        request = first["requests"][0]
        count = len(first["tasks"])
        version = request["plan_version"]
        model = FixtureModel()
        restarted = Application(Stores(Path(self.directory.name)), model)
        result = restarted.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(len(result["tasks"]), count)
        self.assertEqual(result["requests"][0]["plan_version"], version)
        self.assertNotIn("UnifiedSupervisor.Planning", model.calls)

    def test_langgraph_saved_answer_is_reused_after_checkpoint_gap(self):
        original = self.engine.finish_operation

        def crash_after_answer(*args, **kwargs):
            result = original(*args, **kwargs)
            if (kwargs.get("workflow_key") or "").endswith(":answer"):
                raise RuntimeError("模拟回答保存后中断")
            return result

        with patch.object(
            self.engine, "finish_operation", side_effect=crash_after_answer
        ):
            first = self.engine.ask(1, "列出前五个产品")
        self.assertEqual(first["status"], "failed")
        request = first["requests"][0]
        count = len(first["artifacts"])
        model = FixtureModel()
        restarted = Application(Stores(Path(self.directory.name)), model)
        result = restarted.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requests"][0]["status"], "completed")
        self.assertEqual(len(result["artifacts"]), count)
        self.assertNotIn("UnifiedSupervisor.Answer", model.calls)

    def test_langgraph_turn_recovery_does_not_publish_duplicate_response(self):
        result = self.completed()
        before = self.engine.stores.list(
            "session_events", "session_id", result["session_id"]
        )
        restored = self.engine.resume_turn(1, result["session_id"], result["turn_id"])
        self.assertEqual(restored["response_event_id"], result["response_event_id"])
        self.assertEqual(
            self.engine.stores.list(
                "session_events", "session_id", result["session_id"]
            ),
            before,
        )
        with self.assertRaises(PermissionError):
            self.engine.resume_turn(2, result["session_id"], result["turn_id"])

    def test_langgraph_turn_recovers_after_request_commit_before_checkpoint(self):
        original = self.engine.preparation.commit
        crashed = False

        def crash_after_commit(bundle):
            nonlocal crashed
            result = original(bundle)
            if not crashed:
                crashed = True
                raise SystemExit("模拟主图在提交后退出")
            return result

        with patch.object(
            self.engine.preparation, "commit", side_effect=crash_after_commit
        ):
            with self.assertRaises(SystemExit):
                self.engine.ask(1, "分别列出前两个和前三个产品")
        session = self.engine.stores.list("sessions", "user_id", 1)[0]
        events = self.engine.stores.list(
            "session_events", "session_id", session["session_id"]
        )
        turn_id = events[0]["turn_id"]
        model = FixtureModel()
        restarted = Application(Stores(Path(self.directory.name)), model)
        result = restarted.resume_turn(1, session["session_id"], turn_id)
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(len(result["tasks"]), 4)
        self.assertNotIn("UnifiedSupervisor.Preparation", model.calls)
        for request in result["requests"]:
            self.assertEqual(
                len(
                    restarted.stores.list(
                        "request_revisions", "request_id", request["request_id"]
                    )
                ),
                1,
            )

    def test_langgraph_turn_reuses_response_published_before_checkpoint(self):
        original = self.engine.respond

        def crash_after_response(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("模拟回复发布后退出")

        with patch.object(self.engine, "respond", side_effect=crash_after_response):
            with self.assertRaises(SystemExit):
                self.engine.ask(1, "列出前五个产品")
        session = self.engine.stores.list("sessions", "user_id", 1)[0]
        events = self.engine.stores.list(
            "session_events", "session_id", session["session_id"]
        )
        response = next(
            event for event in events if event["event_type"] == "assistant_response"
        )
        restarted = Application(Stores(Path(self.directory.name)), FixtureModel())
        result = restarted.resume_turn(1, session["session_id"], response["turn_id"])
        self.assertEqual(result["response_event_id"], response["event_id"])
        after = restarted.stores.list(
            "session_events", "session_id", session["session_id"]
        )
        self.assertEqual(
            sum(event["event_type"] == "assistant_response" for event in after), 1
        )

    def test_langgraph_clarification_answer_creates_a_new_revision(self):
        class ConfirmingModel(FixtureModel):
            @staticmethod
            def definition(context):
                if context["operation"]["instruction"] == "确认使用产品字段":
                    value = deepcopy(context["previous_revision"]["spec"])
                    value["parameters"]["confirmed"] = True
                    return value
                return FixtureModel.definition(context)

        self.model = ConfirmingModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        delegate = self.engine.specialists.agents["SemanticAgent"]

        class ConfirmingSemantic:
            def execute(self, context, allowed, emit):
                if context["parameters"].get("confirmed"):
                    return delegate.execute(context, allowed, emit)
                return {
                    "status": "needs_clarification",
                    "summary": "请确认字段",
                    "semantic_binding": {
                        "status": "ambiguous",
                        "explanation": "请确认字段",
                        "ambiguities": ["字段"],
                    },
                    "produced_outputs": {},
                    "suggestions": ["请确认字段"],
                }

        self.engine.specialists.agents["SemanticAgent"] = ConfirmingSemantic()
        first = self.engine.ask(1, "列出前五个产品")
        self.assertEqual(first["status"], "needs_clarification")
        request = first["requests"][0]
        self.assertIn("wait", self.engine.execution.snapshot(request).next)
        result = self.engine.resume(1, request["request_id"], answer="确认使用产品字段")
        self.assertEqual(result["status"], "completed", result["blocks"])
        current = result["requests"][0]
        self.assertEqual(current["request_id"], request["request_id"])
        self.assertNotEqual(
            current["current_revision_id"], request["current_revision_id"]
        )
        self.assertTrue(current["spec"]["parameters"]["confirmed"])

    def test_langgraph_failed_dependency_skips_all_descendants(self):
        class Chain(FixtureModel):
            def plan(self, context, tools):
                value = super().plan(context, tools)
                value["tasks"].append(
                    self.node(
                        "downstream",
                        "AnalysisAgent",
                        "后续分析",
                        deps=["query"],
                        inputs=[self.binding("data", "task", "query", "dataset")],
                        outputs=["analysis"],
                    )
                )
                return value

        class Ambiguous:
            def execute(self, context, allowed, emit):
                return {
                    "status": "needs_clarification",
                    "summary": "等待口径",
                    "semantic_binding": {
                        "status": "ambiguous",
                        "explanation": "等待口径",
                    },
                    "produced_outputs": {},
                }

        engine = Application(
            Stores(Path(self.directory.name) / "chain"),
            Chain(),
            specialist_overrides={"SemanticAgent": Ambiguous()},
        )
        result = engine.ask(1, "列出前五个产品")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(
            [node["status"] for node in result["tasks"]],
            ["needs_clarification", "skipped", "skipped"],
        )

    def test_langgraph_error_reply_recovery_reuses_the_original_artifact(self):
        class InvalidIntent(FixtureModel):
            def intent(self, context, tools):
                raise ValueError("意图识别失败")

        engine = Application(
            Stores(Path(self.directory.name) / "reply"), InvalidIntent()
        )
        original = engine.respond

        def crash_after_response(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("模拟错误回复发布后退出")

        with patch.object(engine, "respond", side_effect=crash_after_response):
            with self.assertRaises(SystemExit):
                engine.ask(1, "列出产品")
        session = engine.stores.list("sessions", "user_id", 1)[0]
        events = engine.stores.list(
            "session_events", "session_id", session["session_id"]
        )
        response = next(
            event for event in events if event["event_type"] == "assistant_response"
        )
        restored = engine.resume_turn(1, session["session_id"], response["turn_id"])
        self.assertEqual(restored["status"], "failed")
        self.assertEqual(restored["blocks"], response["payload"]["blocks"])
        self.assertEqual(
            [item["artifact_id"] for item in restored["artifacts"]],
            [response["payload"]["blocks"][0]["artifact_id"]],
        )
        self.assertEqual(
            len(engine.stores.list("artifacts", "session_id", session["session_id"])), 1
        )
