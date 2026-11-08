"""Acceptance tests for request identity, revisions, task execution and provenance."""

import ast
import json
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from ..application.service import Application
from ..core.config import ROOT
from ..core.model import STRING, ModelClient, tool
from ..core.models import Answer, ResourceRef
from ..core.transport import ChatResponse, OpenAICompatibleTransport, ToolCall
from ..intent.models import Intent
from ..persistence.stores import RECORD_MODELS, Stores
from ..persistence.summaries import compress
from ..query.validation import validate_readonly
from .fixtures import FixtureModel
from .legacy_fixture import legacy_ledger


class Smoke(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.model = FixtureModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)

    def tearDown(self):
        self.directory.cleanup()

    def completed(self, question="列出前五个产品", session_id=None):
        result = self.engine.ask(1, question, session_id)
        self.assertEqual(result["status"], "completed", result["blocks"])
        return result

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
        self.assertEqual(set(work["output_refs"]), {"data", "chart", "report"})
        self.assertEqual(result["blocks"][0]["request_id"], mean["request_id"])
        self.assertEqual(
            {item["type"] for item in result["artifacts"]},
            {"query_result", "analysis_result", "chart", "report"},
        )
        self.assertIn("UnifiedSupervisor.Definition", self.model.calls)

    def test_update_chart_reuses_data_and_only_answers_changed_output(self):
        first = self.completed("统计产品并生成图表和报告")
        before = first["requests"][0]
        original = {
            item["artifact_id"]: self.engine.artifacts.content(item["artifact_id"])
            for item in first["artifacts"]
        }
        second = self.completed("将刚才的图改为横向条形图", first["session_id"])
        after = second["requests"][0]
        self.assertEqual(before["request_id"], after["request_id"])
        self.assertEqual(
            second["revisions"][0]["parent_revision_id"],
            first["revisions"][0]["revision_id"],
        )
        self.assertEqual(self.model.calls.count("QueryAgent"), 1)
        self.assertEqual(
            {block["kind"] for block in second["blocks"]}, {"text", "chart"}
        )
        chart_id = after["output_refs"]["chart"]["id"]
        chart = self.engine.artifacts.content(chart_id)
        self.assertEqual(chart["yAxis"]["type"], "category")
        self.assertEqual(
            chart["selection"]["source_id"], before["output_refs"]["data"]["id"]
        )
        for key, content in original.items():
            self.assertEqual(self.engine.artifacts.content(key), content)
        revision = second["revisions"][0]
        with self.assertRaises(ValueError):
            self.engine.stores.put(
                "request_revisions",
                {**revision, "spec": {**revision["spec"], "description": "覆盖"}},
            )

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
                    second["requests"][0]["output_refs"]["data"]["id"]
                )
            ),
            2,
        )
        self.assertTrue(any(node["status"] == "superseded" for node in second["tasks"]))
        self.assertEqual(
            len(
                self.engine.artifacts.content(
                    first["requests"][0]["output_refs"]["data"]["id"]
                )
            ),
            5,
        )

    def test_same_turn_request_dependency_is_ordered_and_materialized(self):
        result = self.completed("先给统计结果画图，再列出前五个产品")
        requests = {item["title"]: item for item in result["requests"]}
        chart_request = requests["绘制产品图"]
        data_request = requests["列出前五个产品"]
        self.assertEqual(result["blocks"][0]["request_id"], chart_request["request_id"])
        task = next(
            node
            for node in result["tasks"]
            if node["assigned_agent"] == "PresentationAgent"
        )
        self.assertEqual(
            task["input_bindings"][0]["source"], data_request["output_refs"]["data"]
        )
        chart = self.engine.artifacts.inspect(
            chart_request["output_refs"]["chart"]["id"]
        )
        self.assertEqual(chart["source_refs"], [data_request["output_refs"]["data"]])

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
        self.assertEqual(dependent["output_refs"], {})
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
                len(self.engine.artifacts.content(item["output_refs"]["data"]["id"]))
                for item in result["requests"]
            ],
            [2, 3],
        )

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
                                [request["output_refs"]["data"]],
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
                                "kind": "table",
                                "description": "前两个产品",
                            }
                        ],
                    }
                return FixtureModel.definition(context)

            def plan(self, context, tools):
                if (
                    context["revision"]["spec"]["description"]
                    == "从已有结果保留前两个产品"
                ):
                    return {
                        "reason": "精确计算历史数据",
                        "tasks": [
                            self.node(
                                "filter",
                                "AnalysisAgent",
                                "保留前两行",
                                inputs=context["revision"]["spec"]["inputs"],
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
            second["requests"][0]["output_refs"]["data"]["id"]
        )
        self.assertEqual(self.model.calls.count("QueryAgent"), 1)
        self.assertEqual(output["payload"]["provenance"]["method"], "artifact_compute")
        self.assertEqual(len(self.engine.artifacts.content(output["artifact_id"])), 2)

    def test_request_refs_in_answer_and_partial_clarification(self):
        class ReferencingModel(FixtureModel):
            def intent(self, context, tools):
                if context["query"] == "解释平均值并识别库存偏低":
                    return {
                        "operations": [
                            self.operation("mean", "none", "general_qa", "解释平均值")
                        ],
                        "clarifications": [
                            {"subject": "库存偏低", "question": "采用哪个库存阈值？"}
                        ],
                    }
                return super().intent(context, tools)

            @staticmethod
            def answer(context):
                answer = FixtureModel.answer(context)
                answer["blocks"][0]["refs"] = [
                    {"kind": "request", "id": context["request"]["request_id"]}
                ]
                return answer

        engine = Application(Stores(Path(self.directory.name)), ReferencingModel())
        result = engine.ask(1, "解释平均值并识别库存偏低")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["blocks"]), 2)
        self.assertEqual(result["blocks"][-1]["content"], "采用哪个库存阈值？")
        self.assertEqual(
            engine.requests.artifact_scope([result["blocks"][0]["refs"][0]]), set()
        )

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
        rejected = [event for event in events if "rejected_plan" in event["payload"]]
        self.assertEqual(len(rejected), 1)
        self.assertIn("不能把本 Request", rejected[0]["payload"]["summary"])

    def test_failed_migration_rolls_back_and_keeps_backup(self):
        directory = Path(self.directory.name) / "corrupt-legacy"
        legacy_ledger(directory)
        db = sqlite3.connect(directory / "agent.sqlite")
        try:
            revision = json.loads(
                db.execute("SELECT body FROM task_revisions").fetchone()[0]
            )
            revision["requirements"][0].pop("description")
            db.execute("UPDATE task_revisions SET body=?", (json.dumps(revision),))
            db.commit()
        finally:
            db.close()
        with self.assertRaises(KeyError):
            Stores(directory)
        db = sqlite3.connect(directory / "agent.sqlite")
        try:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("subtasks", tables)
            self.assertNotIn("requests", tables)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 2
            )
        finally:
            db.close()
        self.assertTrue((directory / "agent.before-request-model.sqlite").exists())

    def test_none_business_knowledge_has_metadata_and_no_tasks(self):
        result = self.completed("有哪些产品表？")
        self.assertEqual(result["tasks"], [])
        self.assertNotIn("SemanticAgent", self.model.calls)
        initial = next(
            context
            for role, context in self.model.contexts
            if role == "UnifiedSupervisor.Intent"
        )
        self.assertIn("Products", initial["database_metadata"]["tables"])
        self.assertIn("Products", result["blocks"][0]["content"])

    def test_provenance_distinguishes_business_sql_and_computation(self):
        result = self.completed("统计产品并生成图表和报告")
        data = [item for item in result["artifacts"] if item["type"] == "query_result"]
        self.assertEqual(
            {item["payload"]["provenance"]["method"] for item in data},
            {"business_sql", "artifact_compute"},
        )
        derived = next(
            item
            for item in data
            if item["payload"]["provenance"]["method"] == "artifact_compute"
        )
        self.assertTrue(derived["source_refs"])
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
                other["session_id"], first["requests"][0]["output_refs"]["data"]
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

    def test_atomic_revision_compare_and_swap(self):
        result = self.completed()
        request = result["requests"][0]
        revision = deepcopy(result["revisions"][0])
        revision["revision_id"] = "rev_conflict"
        changed = {**request, "current_revision_id": "rev_conflict"}
        with self.assertRaises(ValueError):
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

    def test_interrupted_execution_can_be_recovered_without_overwriting_artifacts(self):
        result = self.completed()
        request = result["requests"][0]
        task = next(
            node for node in result["tasks"] if node["assigned_agent"] == "QueryAgent"
        )
        artifact_id = request["output_refs"]["data"]["id"]
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

    def test_event_sequence_and_summary(self):
        result = self.completed("什么是平均值？")
        session_id = result["session_id"]

        def event(index):
            self.engine.stores.event(
                session_id,
                "turn_parallel",
                "user_message",
                "User",
                {"content": str(index)},
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(event, range(110)))
        events = self.engine.stores.list("session_events", "session_id", session_id)
        self.assertEqual(
            [item["seq"] for item in events], list(range(1, len(events) + 1))
        )
        compress(
            self.engine.stores,
            self.model,
            session_id,
            self.engine.requests.validate_ref,
        )
        self.assertTrue(
            self.engine.stores.list("session_summaries", "session_id", session_id)
        )

    def test_migration_preserves_history_artifacts_and_update(self):
        directory = Path(self.directory.name) / "legacy"
        legacy_ledger(directory)
        before = (directory / "content/result_001/data.json").read_bytes()
        stores = Stores(directory)
        self.assertTrue((directory / "agent.before-request-model.sqlite").exists())
        self.assertEqual(len(stores.list("requests", "session_id", "session_001")), 2)
        for table, model in RECORD_MODELS.items():
            with stores.db() as db:
                values = [
                    json.loads(row[0])
                    for row in db.execute(f"SELECT body FROM {table}")
                ]
            for value in values:
                model.model_validate(value)
        engine = Application(stores, FixtureModel())
        result = engine.ask(1, "将刚才的图改为横向条形图", "session_001")
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(result["requests"][0]["request_id"], "req_002")
        self.assertNotIn("QueryAgent", engine.model.calls)
        self.assertEqual(
            (directory / "content/result_001/data.json").read_bytes(), before
        )
        self.assertIn("SELECT", engine.artifacts.sql("result_001"))
        self.assertEqual(
            Stores(directory).get("artifacts", "result_001")["artifact_id"],
            "result_001",
        )

    def test_http_requests_revisions_artifacts_and_resume(self):
        from fastapi.testclient import TestClient
        from ..api import app as adapter
        from ..api import identity

        with (
            patch.object(
                identity, "AUTH_DB", Path(self.directory.name) / "auth.sqlite"
            ),
            patch.object(adapter, "application", lambda: self.engine),
        ):
            identity.create_user("smoke", "smoke-password-2026", "analyst")
            with TestClient(adapter.app) as client:
                login = client.post(
                    "/auth/login",
                    json={"username": "smoke", "password": "smoke-password-2026"},
                )
                headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                response = client.post(
                    "/ask",
                    json={"question": "解释平均值，再统计产品并生成图表和报告"},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "completed", result["blocks"])
                request = result["requests"][-1]
                self.assertEqual(
                    len(
                        client.get(
                            "/requests",
                            params={"session_id": result["session_id"]},
                            headers=headers,
                        ).json()
                    ),
                    2,
                )
                self.assertEqual(
                    client.get(
                        f"/requests/{request['request_id']}", headers=headers
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    len(
                        client.get(
                            f"/requests/{request['request_id']}/revisions",
                            headers=headers,
                        ).json()
                    ),
                    1,
                )
                self.assertEqual(
                    client.get(
                        f"/tasks/{result['tasks'][0]['task_id']}", headers=headers
                    ).status_code,
                    200,
                )
                artifact = request["output_refs"]["data"]["id"]
                self.assertEqual(
                    client.get(f"/artifacts/{artifact}/sql", headers=headers).json()[
                        "provenance"
                    ]["method"],
                    "business_sql",
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{artifact}/content?limit=2", headers=headers
                    ).json()["returned_rows"],
                    2,
                )
                preview = client.post(
                    "/bind",
                    json={
                        "question": "列出前五个产品",
                        "session_id": result["session_id"],
                    },
                    headers=headers,
                )
                self.assertEqual(
                    preview.json()["status"], "waiting_input", preview.text
                )
                resume_id = preview.json()["requests"][0]["request_id"]
                resumed = client.post(
                    f"/requests/{resume_id}/resume", json={}, headers=headers
                )
                self.assertEqual(resumed.json()["status"], "completed", resumed.text)
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/chat.css").status_code, 200)

    def test_semantic_gap_returns_scoped_clarification(self):
        class Ambiguous:
            def execute(self, context, allowed, emit):
                return {
                    "status": "needs_clarification",
                    "summary": "请确认统计口径",
                    "semantic_binding": {
                        "status": "ambiguous",
                        "explanation": "需要确认",
                        "ambiguities": ["口径"],
                    },
                    "produced_outputs": {},
                    "suggestions": ["请确认统计口径"],
                }

        engine = Application(
            Stores(Path(self.directory.name) / "ambiguous"),
            FixtureModel(),
            specialist_overrides={"SemanticAgent": Ambiguous()},
        )
        result = engine.ask(1, "列出前五个产品")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertFalse(result["artifacts"])

    def test_source_syntax(self):
        for path in (ROOT / "src").rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        subprocess.run(
            ["node", "--check", str(ROOT / "src/enterprise_agent/ui/app.js")],
            check=True,
            capture_output=True,
        )

    def test_model_transport_tool_round_trip(self):
        class ScriptedTransport:
            def __init__(self):
                self.requests = []

            def complete(self, messages, tools):
                self.requests.append((deepcopy(messages), tools))
                if len(self.requests) == 1:
                    return ChatResponse(
                        None, (ToolCall("call_1", "echo", '{"value":"hello"}'),)
                    )
                return ChatResponse('{"blocks":[{"kind":"text","content":"完成"}]}')

        transport, events = ScriptedTransport(), []
        answer = ModelClient(transport).run(
            "TestAgent",
            "返回文字",
            {},
            Answer,
            {"echo": tool("echo", lambda value: {"value": value}, value=STRING)},
            lambda kind, value: events.append((kind, value)),
        )
        self.assertEqual(answer.blocks[0].content, "完成")
        self.assertEqual(transport.requests[1][0][-1]["role"], "tool")
        self.assertEqual([kind for kind, _ in events], ["tool_call", "tool_result"])

    def test_model_rejects_unknown_tool_and_repairs_json(self):
        class ScriptedTransport:
            def __init__(self):
                self.requests = []

            def complete(self, messages, tools):
                self.requests.append(deepcopy(messages))
                if len(self.requests) == 1:
                    return ChatResponse(
                        None, (ToolCall("call_1", "unregistered", "{}"),)
                    )
                if len(self.requests) == 2:
                    return ChatResponse('{"blocks":"oops"}')
                return ChatResponse('{"blocks":[{"kind":"text","content":"已处理"}]}')

        transport = ScriptedTransport()
        answer = ModelClient(transport).run("TestAgent", "返回文字", {}, Answer)
        self.assertEqual(answer.blocks[0].content, "已处理")
        self.assertIn("工具未授权", transport.requests[1][-1]["content"])
        self.assertEqual(len(transport.requests), 3)

    def test_openai_compatible_adapter_translation(self):
        captured = []

        def handle(request):
            captured.append(json.loads(request.content))
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "function": {"name": "echo", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )

        adapter = OpenAICompatibleTransport(
            settings=lambda: (
                "https://example.invalid/chat/completions",
                "test-key",
                "test-model",
            ),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        )
        response = adapter.complete([{"role": "user", "content": "hello"}], [])
        self.assertEqual(captured[0]["model"], "test-model")
        self.assertEqual(response.tool_calls[0].name, "echo")


def main():
    import sys

    if "--live-only" not in sys.argv:
        outcome = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Smoke)
        )
        if not outcome.wasSuccessful():
            raise SystemExit(1)
    if "--live" in sys.argv or "--live-only" in sys.argv:
        with tempfile.TemporaryDirectory() as directory:
            engine = Application(Stores(Path(directory)))
            result = engine.ask(
                "live-smoke",
                "查询 Products 表中的产品总数，只返回实际行数，不使用业务指标。",
            )
            data = [
                item for item in result["artifacts"] if item["type"] == "query_result"
            ]
            evidence = {
                "status": result["status"],
                "requests": len(result["requests"]),
                "tasks": len(result["tasks"]),
                "data": [
                    {
                        "artifact_id": item["artifact_id"],
                        "rows": engine.artifacts.content(item["artifact_id"]),
                    }
                    for item in data
                ],
                "blocks": result["blocks"],
            }
            events = engine.stores.list(
                "session_events", "session_id", result["session_id"]
            )
            diagnostics = {
                "evidence": evidence,
                "requests": result["requests"],
                "revisions": result["revisions"],
                "events": events,
            }
            (ROOT / "runtime" / "request-model-live-verification.json").write_text(
                json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(evidence, ensure_ascii=False))
            if result["status"] != "completed" or not data:
                raise SystemExit("真实模型尚未完成查询")
            expected = next(
                item["rows"]
                for name, item in engine.registry.tables.items()
                if name.lower() == "products"
            )
            if not any(
                len(item["rows"]) == 1 and expected in item["rows"][0].values()
                for item in evidence["data"]
            ):
                raise SystemExit("真实模型返回值与业务快照的产品数不一致")


if __name__ == "__main__":
    main()
