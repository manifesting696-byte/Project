"""Cross-Request Task scheduling, bounded concurrency and durable recovery."""

from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

from enterprise_agent.flow.application import Application
from tests.fixtures import FixtureModel
from tests.postgres_fixture import TestStores as Stores
from tests.support import AgentTestCase


class TablesModel(FixtureModel):
    names = ("A", "B")
    dependent = False
    replan = False

    def intent(self, context, tools):
        return {
            "operations": [
                self.operation(
                    name,
                    "create_task",
                    "result_interaction",
                    name,
                    [{"kind": "request", "id": "A", "output": "data"}]
                    if self.dependent and name == "B"
                    else [],
                )
                for name in self.names
            ]
        }

    @staticmethod
    def definition(context):
        op = context["operation"]
        return {
            "description": op["instruction"],
            "inputs": [{"slot": "source", "source": ref} for ref in op["refs"]],
            "outputs": [
                {"name": "data", "content_type": "table", "description": "展示表"}
            ],
        }

    def plan(self, context, tools):
        prior = [n for n in context["tasks"] if n["status"] == "completed"]
        if context["tasks"] and (not self.replan or len(prior) >= 2 or not prior):
            return {"reason": "保留当前计划", "tasks": []}
        name = "followup" if prior else "table"
        return {
            "reason": "生成独立展示表",
            "tasks": [
                self.node(
                    name,
                    "PresentationAgent",
                    context["spec"]["description"],
                    inputs=context["spec"]["inputs"],
                    outputs=["table"],
                    replan=self.replan and not prior,
                )
            ],
            "output_bindings": {
                "data": {"kind": "task", "id": name, "output": "table"}
            },
        }

    def run(self, role, instruction, context, output, tools=None, emit=None):
        if role != "PresentationAgent":
            return super().run(role, instruction, context, output, tools, emit)
        saved = tools["compose_table"].call(
            title=context["task"]["objective"],
            summary="测试数据",
            rows=[{"owner": context["task"]["request_id"]}],
            source_ids=[
                item["artifact_id"]
                for item in context["inputs"]
                if "artifact_id" in item
            ],
        )
        return output.model_validate(
            {
                "summary": "已生成",
                "produced_outputs": {
                    "table": {"kind": "artifact", "id": saved["artifact_id"]}
                },
            }
        )


class UnifiedScheduleTests(AgentTestCase):
    def use_model(self, model):
        self.engine.close()
        self.model = model
        self.engine = Application(Stores(Path(self.directory.name)), model)

    def parallel_probe(self, parties=2):
        delegate = self.engine.specialists.agents["PresentationAgent"]
        barrier, lock = Barrier(parties, timeout=15), Lock()
        probe = {"active": 0, "peak": 0, "started": [], "finished": []}

        class Probe:
            def execute(inner, context, allowed, emit):
                node = context["task"]
                with lock:
                    probe["active"] += 1
                    probe["peak"] = max(probe["peak"], probe["active"])
                    probe["started"].append((node["request_id"], node["task_id"]))
                try:
                    barrier.wait()
                    return delegate.execute(context, allowed, emit)
                finally:
                    with lock:
                        probe["active"] -= 1
                        probe["finished"].append(node["request_id"])

        self.engine.specialists.agents["PresentationAgent"] = Probe()
        return probe

    def test_independent_requests_share_one_parallel_task_wave(self):
        self.use_model(TablesModel())
        probe = self.parallel_probe()
        result = self.completed("two independent tables")
        self.assertEqual(probe["peak"], 2)
        self.assertEqual(len({owner for owner, _ in probe["started"]}), 2)
        self.assertEqual(len(result["tasks"]), 2)
        self.assertEqual([r["title"] for r in result["requests"]], ["A", "B"])
        for request in result["requests"]:
            artifact = self.engine.artifacts.inspect(
                result["deliveries"][request["request_id"]]["data"]["id"]
            )
            self.assertEqual(artifact["request_id"], request["request_id"])

    def test_global_task_limit_is_not_multiplied_by_request_count(self):
        model = TablesModel()
        model.names = tuple("ABCDEF")
        self.use_model(model)
        probe = self.parallel_probe()
        with (
            patch("enterprise_agent.stages.execute.scheduler.MAX_PARALLEL", 2),
            patch("enterprise_agent.stages.execute.task_nodes.MAX_PARALLEL", 2),
        ):
            result = self.completed("six independent tables")
        self.assertEqual(len(result["tasks"]), 6)
        self.assertEqual(probe["peak"], 2)

    def test_replanned_tasks_reenter_the_shared_scheduler(self):
        model = TablesModel()
        model.replan = True
        self.use_model(model)
        probe = self.parallel_probe()
        result = self.completed("two independently replanned tables")
        self.assertEqual(len(result["tasks"]), 4)
        self.assertEqual(len(probe["started"]), 4)
        self.assertEqual({r["plan_version"] for r in result["requests"]}, {2})

    def test_dependency_waits_for_actual_upstream_artifact(self):
        model = TablesModel()
        model.dependent = True
        self.use_model(model)
        delegate = self.engine.specialists.agents["PresentationAgent"]
        order = []

        class Probe:
            def execute(inner, context, allowed, emit):
                title = context["task"]["objective"]
                if title == "B":
                    self.assertEqual(order, ["A"])
                    self.assertTrue(context["inputs"][0].get("artifact_id"))
                value = delegate.execute(context, allowed, emit)
                order.append(title)
                return value

        self.engine.specialists.agents["PresentationAgent"] = Probe()
        self.completed("A then B")
        self.assertEqual(order, ["A", "B"])

    def test_downstream_does_not_consume_a_result_being_replanned(self):
        model = TablesModel()
        model.dependent = True
        model.replan = True
        self.use_model(model)
        result = self.completed("replan upstream before consuming")
        source, consumer = result["requests"]
        source_id = result["deliveries"][source["request_id"]]["data"]["id"]
        consumer_tasks = [
            t for t in result["tasks"] if t["request_id"] == consumer["request_id"]
        ]
        self.assertTrue(consumer_tasks)
        for task in consumer_tasks:
            self.assertEqual(
                task["input_bindings"][0]["source"],
                {"kind": "artifact", "id": source_id},
            )

    def test_failed_source_blocks_its_consumer_but_not_independent_tasks(self):
        model = TablesModel()
        model.names = ("A", "B", "C")
        model.dependent = True
        self.use_model(model)
        delegate = self.engine.specialists.agents["PresentationAgent"]
        executed = []

        class Probe:
            def execute(inner, context, allowed, emit):
                name = context["task"]["objective"]
                executed.append(name)
                if name == "A":
                    raise ValueError("source failed")
                return delegate.execute(context, allowed, emit)

        self.engine.specialists.agents["PresentationAgent"] = Probe()
        result = self.engine.ask(1, "one failure with independent work")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(set(executed), {"A", "C"})
        self.assertEqual(
            {r["title"]: r["status"] for r in result["requests"]}["C"], "completed"
        )

    def test_crash_after_shared_dispatch_reuses_saved_artifacts(self):
        self.use_model(TablesModel())
        original = self.engine.scheduler.run_many

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("shared tasks saved, request checkpoint not advanced")

        with patch.object(self.engine.scheduler, "run_many", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.engine.ask(1, "crash after tasks")
        session = self.engine.stores.list("sessions", "user_id", 1)[0]
        events = self.engine.stores.list(
            "session_events", "session_id", session["session_id"]
        )
        turn = next(e["turn_id"] for e in events if e["event_type"] == "user_message")
        before = {
            a["artifact_id"]
            for a in self.engine.stores.list(
                "artifacts", "session_id", session["session_id"]
            )
        }
        self.engine.close()
        self.engine = Application(Stores(Path(self.directory.name)), TablesModel())
        with patch.object(
            self.engine.specialists,
            "execute",
            side_effect=AssertionError("must not repeat saved tasks"),
        ):
            result = self.engine.resume_turn(1, session["session_id"], turn)
        self.assertEqual(result["status"], "completed")
        delivered = {refs["data"]["id"] for refs in result["deliveries"].values()}
        self.assertEqual(delivered, before)

    def test_preview_and_pause_remain_owned_by_each_request(self):
        self.use_model(TablesModel())
        preview = self.engine.ask(1, "preview", preview=True)
        self.assertTrue(all(t["status"] == "pending" for t in preview["tasks"]))
        for request in preview["requests"]:
            self.assertIn("wait", self.engine.execution.snapshot(request).next)
            resumed = self.engine.resume(1, request["request_id"])
            self.assertEqual(resumed["status"], "completed")
        paused = self.engine.ask(1, "pause", pause=True)
        self.assertEqual(paused["status"], "waiting_input")
        for request in paused["requests"]:
            self.assertIn("wait", self.engine.execution.snapshot(request).next)

    def test_stale_revision_does_not_execute_or_block_another_owner(self):
        self.use_model(TablesModel())
        preview = self.engine.ask(1, "preview", preview=True)
        first, second = preview["requests"]
        scopes = [
            {
                "request_id": r["request_id"],
                "revision_id": r["current_revision_id"],
                "execution_key": None,
                "pause": False,
            }
            for r in preview["requests"]
        ]
        scopes[0]["revision_id"] = "rev_stale"
        outcomes = self.engine.scheduler.run_many(
            preview["session_id"], preview["turn_id"], scopes
        )
        self.assertEqual(
            outcomes[first["request_id"]]["error"]["kind"], "version_conflict"
        )
        self.assertEqual(
            outcomes[second["request_id"]]["new_results"][0]["status"], "completed"
        )
        self.assertEqual(
            self.engine.stores.list("tasks", "request_id", first["request_id"])[0][
                "status"
            ],
            "pending",
        )
