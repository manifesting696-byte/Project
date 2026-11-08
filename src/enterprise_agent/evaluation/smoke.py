"""One concise acceptance pass; fixtures are never imported by runtime Agents."""
import ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from ..application.service import Application
from ..core.config import ROOT
from ..core.model import STRING, ModelClient, tool
from ..core.models import Answer, Plan, ResourceRef
from ..core.transport import ChatResponse, OpenAICompatibleTransport, ToolCall
from ..intent.models import Intent
from ..persistence.stores import Stores
from ..persistence.summaries import compress
from ..query.validation import validate_readonly


def ref(key):
    return {"kind": "artifact", "id": key}


class FixtureModel:
    def __init__(self):
        self.calls = []

    def run(self, role, instruction, context, output, tools=None, emit=None):
        self.calls.append(role)
        tools = tools or {}
        if role == "UnifiedSupervisor.Intent":
            if context["query"] == "什么是平均值？":
                data = {"dialogue_action": "independent"}
            elif context["query"] == "将刚才的图改为横向条形图":
                task_id = context["candidate_tasks"][-1]["task_id"]
                state = tools["get_task_state"].call(task_id=task_id)
                chart = next(r for r in state["revision"]["requirements"] if r["type"] == "visualization")
                data = {"dialogue_action": "modify", "target_task_id": task_id, "goal": "产品汇总和展示",
                        "request_changes": [{"action": "replace", "request_id": chart["request_id"], "type": "visualization", "description": "横向图"}]}
            else:
                data = {"dialogue_action": "new_task", "goal": "产品汇总和展示", "request_changes": [
                    {"action": "add", "request_id": name, "type": kind, "description": name}
                    for name, kind in (("data", "data_analysis"), ("chart", "visualization"), ("report", "report"))]}
        elif role == "UnifiedSupervisor.Planning":
            requests = {r["type"]: r["request_id"] for r in context["revision"]["requirements"]}
            def node(key, agent, request, deps=(), inputs=(), outputs=(), **extra):
                return {"subtask_id": key, "assigned_agent": agent, "objective": key, "request_ids": [requests[request]],
                        "depends_on": list(deps), "input_bindings": list(inputs), "expected_outputs": list(outputs), **extra}
            def source(key, name=None):
                return {"slot": name or "semantics", "source": {"kind": "subtask", "id": key, **({"output": name} if name else {})}}
            nodes = context["subtasks"]
            if not nodes:
                additions = [node("bind", "SemanticAgent", "data_analysis"),
                    node("rows", "QueryAgent", "data_analysis", ["bind"], [source("bind")], ["dataset"]),
                    node("analysis", "AnalysisAgent", "data_analysis", ["rows"], [source("rows", "dataset")], ["analysis"], replan_after=True),
                    node("chart", "PresentationAgent", "visualization", ["rows"], [source("rows", "dataset")], ["chart"], params={"format": "chart"})]
            else:
                analysis = next(n for n in nodes if n["assigned_agent"] == "AnalysisAgent")
                if context["intent"]["dialogue_action"] == "modify":
                    original = next(n for n in nodes if n["assigned_agent"] == "QueryAgent")
                    additions = [node("changed_chart", "PresentationAgent", "visualization", [original["subtask_id"]],
                        [source(original["subtask_id"], "dataset")], ["chart"], params={"format": "chart", "horizontal": True})]
                    chart_id = "changed_chart"
                else:
                    additions = []
                    chart_id = next(n["subtask_id"] for n in nodes if n.get("params", {}).get("format") == "chart")
                additions.append(node("new_report", "PresentationAgent", "report", [analysis["subtask_id"], chart_id],
                    [source(analysis["subtask_id"], "analysis"), source(chart_id, "chart")], ["report"], params={"format": "report"}))
            data = {"reason": "按当前证据与用户需求动态规划", "subtasks": additions}
        elif role == "SemanticAgent":
            tools["search_semantic_registry"].call(query="Products")
            data = {"status": "bound", "tables": ["Products"], "fields": ["Products.product_id", "Products.product_name"], "explanation": "真实产品字段"}
        elif role == "QueryAgent":
            tools["get_bound_semantics"].call()
            saved = tools["execute_readonly_sql"].call(sql="SELECT product_id, product_name, 1 AS record_count FROM Products ORDER BY product_id LIMIT 5", title="产品数据", summary="真实产品行", entity_id_column="product_id")
            data = {"summary": "查询完成", "produced_outputs": {"dataset": ref(saved["artifact_id"])}}
        elif role == "AnalysisAgent":
            key = context["inputs"][0]["artifact_id"]
            computed = tools["compute_artifact"].call(artifact_ids=[key], sql=f'SELECT COUNT(*) AS n FROM "{key}"', title="行数", summary="精确计数")
            count = computed["data"]["rows"][0]["n"]
            saved = tools["save_artifact"].call(title="分析", summary=f"记录数 {count}", body=f"实际有 {count} 行。", source_ids=[computed["artifact_id"]])
            data = {"summary": "分析完成", "produced_outputs": {"analysis": ref(saved["artifact_id"])}}
        elif role == "PresentationAgent":
            params = context["subtask"]["params"]
            if params["format"] == "chart":
                saved = tools["render_chart"].call(source_id=context["inputs"][0]["artifact_id"], chart_type="bar", x="product_name",
                    series=[{"field": "record_count", "name": "记录数", "color": "#2879ad"}], title="产品记录", horizontal=params.get("horizontal", False))
                name = "chart"
            else:
                saved = tools["render_report"].call(title="报告", body="# 产品报告", source_ids=[item["artifact_id"] for item in context["inputs"]])
                name = "report"
            data = {"summary": "展示完成", "produced_outputs": {name: ref(saved["artifact_id"])}}
        elif role == "UnifiedSupervisor.Completion":
            state = tools["get_task_state"].call(task_id=context["task"]["task_id"])
            checks = []
            for request in context["revision"]["requirements"]:
                node = next(n for n in reversed(state["subtasks"]) if n["status"] == "completed" and request["request_id"] in n["request_ids"] and n["produced_outputs"])
                checks.append({"request_id": request["request_id"], "status": "completed", "reason": "真实产物已交付", "refs": list(node["produced_outputs"].values())})
            data = {"requirements": checks, "next_action": "answer", "reason": "逐项完成"}
        elif role == "UnifiedSupervisor.Answer":
            data = {"blocks": [{"kind": "text", "content": "平均值是总和除以数量。" if context["stage"] == "initial" else "已完成查询、分析和展示。"}]}
            for item in context.get("results", []):
                if item["type"] in ("chart", "report"):
                    data["blocks"].append({"kind": item["type"], "refs": [ref(item["artifact_id"])]})
        elif role == "SessionSummary":
            data = {"content": "此前讨论了产品汇总。", "key_refs": []}
        else:
            raise AssertionError(role)
        return output.model_validate(data)


class Smoke(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.model = FixtureModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)

    def tearDown(self):
        self.directory.cleanup()

    def test_dynamic_dag_five_agents_and_revision_reuse(self):
        first = self.engine.ask(1, "汇总产品并生成图表和报告")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["task"]["plan_version"], 2)
        self.assertEqual({a["type"] for a in first["artifacts"]}, {"query_result", "analysis_result", "chart", "report"})
        second = self.engine.ask(1, "将刚才的图改为横向条形图", first["session_id"])
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["revision"]["parent_revision_id"], first["revision"]["revision_id"])
        self.assertEqual(self.model.calls.count("QueryAgent"), 1)
        chart = [a for a in second["artifacts"] if a["type"] == "chart"][-1]
        config = self.engine.artifacts.content(chart["artifact_id"])
        self.assertEqual(config["yAxis"]["type"], "category")
        self.assertEqual(config["series"][0]["itemStyle"]["color"], "#2879ad")
        events = self.engine.stores.list("session_events", "session_id", first["session_id"])
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events)+1)))

    def test_knowledge_summary_contracts_and_access(self):
        result = self.engine.ask(1, "什么是平均值？")
        self.assertIsNone(result["task"])
        with self.assertRaises(PermissionError):
            self.engine.stores.session(2, result["session_id"])
        with self.assertRaises(ValidationError):
            ResourceRef(kind="artifact", id="result_001", schema_version=1)
        with self.assertRaises(ValueError):
            validate_readonly("DELETE FROM Products", {"Products": ["product_id"]})
        with self.assertRaises(ValueError):
            validate_readonly("SELECT invented FROM Products", {"Products": ["product_id"]})
        with self.assertRaises(ValueError):
            self.engine.registry.metric("on_time_rate")
        for _ in range(110):
            self.engine.stores.event(result["session_id"], "turn_fixture", "user_message", "User", {"content": "历史背景"})
        compress(self.engine.stores, self.model, result["session_id"], self.engine.tasks.validate_ref)
        self.assertTrue(self.engine.stores.list("session_summaries", "session_id", result["session_id"]))

    def test_http_adapter(self):
        from fastapi.testclient import TestClient

        from ..api import app as adapter
        from ..api import identity
        with patch.object(identity, "AUTH_DB", Path(self.directory.name) / "auth.sqlite"), patch.object(adapter, "application", lambda: self.engine):
            identity.create_user("smoke", "smoke-password-2026", "analyst")
            with TestClient(adapter.app) as client:
                login = client.post("/auth/login", json={"username": "smoke", "password": "smoke-password-2026"})
                headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                response = client.post("/ask", json={"question": "什么是平均值？"}, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get(f"/sessions/{response.json()['session_id']}/events", headers=headers).status_code, 200)
                self.assertEqual(client.get("/health").json()["agents"], 5)
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/chat.css").status_code, 200)

    def test_http_answer_can_reference_current_request(self):
        from fastapi.testclient import TestClient

        from ..api import app as adapter
        from ..api import identity

        class RequestRefModel(FixtureModel):
            def run(self, role, instruction, context, output, tools=None, emit=None):
                answer = super().run(role, instruction, context, output, tools, emit)
                if role == "UnifiedSupervisor.Answer" and context.get("stage") == "answer":
                    request_id = context["revision"]["requirements"][0]["request_id"]
                    answer.blocks[0].refs.append(ResourceRef(kind="request", id=request_id))
                return answer

        engine = Application(Stores(Path(self.directory.name)), RequestRefModel())
        with patch.object(identity, "AUTH_DB", Path(self.directory.name) / "auth.sqlite"), patch.object(adapter, "application", lambda: engine):
            identity.create_user("request-ref", "request-ref-password-2026", "analyst")
            with TestClient(adapter.app) as client:
                login = client.post("/auth/login", json={"username": "request-ref", "password": "request-ref-password-2026"})
                headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                response = client.post("/ask", json={"question": "汇总产品并生成图表和报告"}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["blocks"][0]["refs"][0]["kind"], "request")
                invalid = Answer.model_validate({"blocks": [{"kind": "text", "refs": [{"kind": "request", "id": "req_missing"}]}]})
                with self.assertRaises(ValueError):
                    engine.respond(response.json()["session_id"], response.json()["turn_id"], invalid, response.json()["task"])

    def test_plan_accepts_semantic_binding_reference(self):
        stores = self.engine.stores
        session = stores.session(1)
        turn_id = stores.new_id("turn")
        intent = Intent.model_validate({"dialogue_action": "new_task", "goal": "按产品统计",
            "request_changes": [{"action": "add", "request_id": "local", "type": "data_analysis", "description": "按产品统计"}]})
        self.engine.tasks.canonicalize(intent)
        event = stores.event(session["session_id"], turn_id, "intent_resolved", "Supervisor",
                             {"intent": intent.model_dump(exclude_none=True)})
        task, revision = self.engine.tasks.revise(session["session_id"], turn_id, intent, event["event_id"])
        request_id = revision["requirements"][0]["request_id"]
        plan = Plan.model_validate({"reason": "先绑定语义，再查询", "subtasks": [
            {"subtask_id": "semantic", "request_ids": [request_id], "objective": "绑定产品字段", "assigned_agent": "SemanticAgent"},
            {"subtask_id": "query", "request_ids": [request_id], "objective": "查询产品", "assigned_agent": "QueryAgent",
             "depends_on": ["semantic"], "input_bindings": [{"slot": "semantics",
                "source": {"kind": "subtask", "id": "semantic", "output": "semantic_binding"}}], "expected_outputs": ["rows"]}]})
        self.engine.tasks.apply_plan(task, turn_id, plan)
        self.assertEqual(task["plan_version"], 1)
        invalid = plan.model_copy(deep=True)
        invalid.subtasks[1].input_bindings[0].source.output = "unknown_output"
        with self.assertRaises(ValueError):
            self.engine.tasks.apply_plan(task, turn_id, invalid)
        self.assertEqual(task["plan_version"], 1)

    def test_source_syntax(self):
        for path in (ROOT / "src").rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        result = subprocess.run(["node", "--check", str(ROOT / "src/enterprise_agent/ui/app.js")],
                                check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_transport_tool_round_trip(self):
        class ScriptedTransport:
            def __init__(self):
                self.requests = []

            def complete(self, messages, tools):
                self.requests.append((list(messages), list(tools)))
                if len(self.requests) == 1:
                    return ChatResponse(None, (ToolCall("call_1", "echo", '{"value":"hello"}'),))
                return ChatResponse('{"blocks":[{"kind":"text","content":"hello"}]}')

        transport = ScriptedTransport()
        events = []
        answer = ModelClient(transport).run("TestAgent", "调用工具", {}, Answer,
            {"echo": tool("回显输入", lambda value: {"value": value}, value=STRING)},
            lambda kind, payload: events.append((kind, payload)))
        self.assertEqual(answer.blocks[0].content, "hello")
        self.assertEqual(transport.requests[1][0][-1]["role"], "tool")
        self.assertIn('"value": "hello"', transport.requests[1][0][-1]["content"])
        self.assertEqual([kind for kind, _ in events], ["tool_call", "tool_result"])

    def test_model_rejects_unknown_tool_and_repairs_json(self):
        class ScriptedTransport:
            def __init__(self):
                self.requests = []

            def complete(self, messages, tools):
                self.requests.append(list(messages))
                if len(self.requests) == 1:
                    return ChatResponse(None, (ToolCall("call_1", "unregistered", "{}"),))
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
            return httpx.Response(200, json={"choices": [{"message": {"content": None,
                "tool_calls": [{"id": "call_1", "function": {"name": "echo", "arguments": "{}"}}]}}]})

        adapter = OpenAICompatibleTransport(
            settings=lambda: ("https://example.invalid/chat/completions", "test-key", "test-model"),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)))
        response = adapter.complete([{"role": "user", "content": "hello"}], [])
        self.assertEqual(captured[0]["model"], "test-model")
        self.assertEqual(response.tool_calls[0].name, "echo")

    def test_specialist_override_keeps_query_flow(self):
        class SemanticOverride:
            calls = 0

            def execute(self, context, allowed, emit):
                self.calls += 1
                return {"status": "completed", "summary": "真实产品字段",
                    "semantic_binding": {"status": "bound", "metrics": [], "tables": ["Products"],
                        "fields": ["Products.product_id", "Products.product_name"], "relations": [],
                        "dimensions": [], "time": {}, "explanation": "真实产品字段", "ambiguities": []},
                    "produced_outputs": {}, "suggestions": []}

        replacement = SemanticOverride()
        engine = Application(Stores(Path(self.directory.name) / "replacement"), FixtureModel(),
                             specialist_overrides={"SemanticAgent": replacement})
        result = engine.ask(1, "汇总产品并生成图表和报告")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(replacement.calls, 1)
        self.assertTrue(any(item["type"] == "query_result" for item in result["artifacts"]))


def main():
    import sys
    if "--live-only" not in sys.argv:
        outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Smoke))
        if not outcome.wasSuccessful():
            raise SystemExit(1)
    if "--live" in sys.argv or "--live-only" in sys.argv:
        with tempfile.TemporaryDirectory() as directory:
            engine = Application(Stores(Path(directory)))
            try:
                result = engine.ask("live-smoke", "查询 Products 表中的产品总数，只返回实际行数，不使用业务指标。")
            except Exception:
                diagnostics = {"sessions": engine.stores.list("sessions", "user_id", "live-smoke")}
                diagnostics["tasks"] = [task for session in diagnostics["sessions"] for task in engine.stores.list("tasks", "session_id", session["session_id"])]
                diagnostics["subtasks"] = [node for task in diagnostics["tasks"] for node in engine.stores.list("subtasks", "task_id", task["task_id"])]
                (ROOT / "acceptance" / "rebuild-live-diagnostic.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
                raise
            evidence = {"live_status": result["status"], "agents": [n["assigned_agent"] for n in result["subtasks"]],
                        "blocks": result["blocks"], "queries": [{"artifact_id": a["artifact_id"],
                            "sql": engine.artifacts.sql(a["artifact_id"]), "rows": engine.artifacts.content(a["artifact_id"])}
                            for a in result["artifacts"] if a["type"] == "query_result"]}
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            (ROOT / "acceptance" / "rebuild-live.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            if result["status"] != "completed" or not any(a["type"] == "query_result" for a in result["artifacts"]):
                raise SystemExit("真实模型尚未完成本轮查询")


if __name__ == "__main__":
    main()
