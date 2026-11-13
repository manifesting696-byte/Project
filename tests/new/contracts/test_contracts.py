"""Persisted/public contracts captured from commit 348c5ff, before stage extraction."""

import importlib
import json
import unittest
from pathlib import Path

from enterprise_agent.flow.request_runs import Execution
from enterprise_agent.flow.task_graph import build as task_graph
from enterprise_agent.flow.turn_runs import Turns
from enterprise_agent.main import create_app


class FrozenContractsTests(unittest.TestCase):
    def test_public_and_persisted_schemas_are_unchanged(self):
        modules = [
            importlib.import_module("enterprise_agent." + name)
            for name in (
                "domain.request",
                "domain.task",
                "domain.artifact",
                "domain.refs",
                "domain.answer",
                "domain.session",
                "stages.intent.schema",
                "stages.prepare.schema",
                "stages.verify.schema",
            )
        ]
        expected = json.loads(
            Path(__file__).with_name("schemas.json").read_text(encoding="utf-8")
        )
        for name, schema in expected.items():
            with self.subTest(contract=name):
                contract = next(
                    getattr(module, name) for module in modules if hasattr(module, name)
                )
                self.assertEqual(contract.model_json_schema(), schema)

    def test_route_paths_and_methods_are_unchanged(self):
        routes = []
        for route in create_app().routes:
            # FastAPI's lazy included routers do not expose path/methods directly.
            effective = (
                route.effective_route_contexts()
                if hasattr(route, "effective_route_contexts")
                else [route]
            )
            routes.extend(
                (item.path, sorted(item.methods))
                for item in effective
                if hasattr(item, "methods")
            )
        routes.sort()
        expected = json.loads(Path(__file__).with_name("routes.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(routes)), expected)

    def test_checkpoint_node_names_and_keys_are_unchanged(self):
        turns, requests = Turns(None, None), Execution(None, None)
        self.assertEqual(
            set(turns.builder.nodes),
            {"intent", "prepare", "commit", "process", "respond"},
        )
        self.assertEqual(
            set(requests.builder.nodes),
            {
                "begin",
                "execute",
                "check",
                "replan",
                "finalize",
                "answer",
                "waiting_answer",
                "wait",
            },
        )
        self.assertEqual(
            set(task_graph(None).nodes), {"dispatch", "execute_task", "collect"}
        )
        self.assertEqual(turns.thread_id("s", "t"), "turn:s:t")
        self.assertEqual(
            requests.thread_id({"request_id": "r", "current_revision_id": "v"}),
            "request:r:v",
        )
        self.assertEqual(
            requests.key(
                {"request_id": "r", "revision_id": "v", "run_id": "t", "executions": 2},
                "answer",
            ),
            "request:r:v:t:2:answer",
        )

    def test_graph_files_are_small_wiring_modules(self):
        from enterprise_agent.settings import ROOT

        for name in ("turn_graph.py", "request_graph.py", "task_graph.py"):
            source = (ROOT / "src/enterprise_agent/flow" / name).read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 80, name)
            self.assertNotIn(".stores.", source)
