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
from ..core.models import AnswerDraft, ResourceRef
from ..core.transport import ChatResponse, OpenAICompatibleTransport, ToolCall
from ..intent.models import Intent, RequestPreparation
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
        self.assertEqual(
            set(self.engine.requests.outputs(work)), {"data", "chart", "report"}
        )
        self.assertEqual(result["blocks"][0]["request_id"], mean["request_id"])
        self.assertEqual(
            {item["content_type"] for item in result["artifacts"]},
            {"table", "text", "chart", "report"},
        )
        self.assertIn("UnifiedSupervisor.Preparation", self.model.calls)

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
            {
                self.engine.artifacts.inspect(block["artifact_id"])["content_type"]
                for block in second["blocks"]
            },
            {"text", "chart"},
        )
        chart_id = self.engine.requests.outputs(after)["chart"]["id"]
        chart = self.engine.artifacts.content(chart_id)
        self.assertEqual(chart["yAxis"]["type"], "category")
        self.assertEqual(
            chart["selection"]["source_id"],
            self.engine.requests.outputs(before)["data"]["id"],
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
            stored = json.loads(
                db.execute(
                    "SELECT body FROM requests WHERE id=?", (request["request_id"],)
                ).fetchone()[0]
            )
        self.assertNotIn("spec", stored)
        self.assertNotIn("results", stored)
        edited = deepcopy(request)
        edited["spec"]["parameters"]["limit"] = 100
        with self.assertRaises(ValueError):
            self.engine.stores.put("requests", edited)
        self.assertEqual(
            self.engine.stores.get("requests", request["request_id"]), request
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

    def test_multiple_text_outputs_bind_to_their_own_artifacts(self):
        class MultipleAnswers(FixtureModel):
            def intent(self, context, tools):
                return {
                    "operations": [
                        self.operation(
                            "explanation",
                            "create_task",
                            "result_interaction",
                            "解释平均值并给出例子",
                        )
                    ]
                }

            @staticmethod
            def definition(context):
                return {
                    "description": "解释平均值并给出例子",
                    "outputs": [
                        {
                            "name": "definition",
                            "content_type": "text",
                            "description": "定义",
                        },
                        {
                            "name": "example",
                            "content_type": "text",
                            "description": "例子",
                        },
                    ],
                }

            def plan(self, context, tools):
                return {"tasks": [], "reason": "已有知识可形成文字交付"}

            @staticmethod
            def answer(context):
                return {
                    "blocks": [
                        {
                            "output_name": "definition",
                            "content_type": "text",
                            "content": "总和除以数量。",
                        },
                        {
                            "output_name": "example",
                            "content_type": "text",
                            "content": "2和4的平均值是3。",
                        },
                    ]
                }

        self.model = MultipleAnswers()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        result = self.completed("解释平均值并给出例子")
        request_id = result["requests"][0]["request_id"]
        for name, content in (
            ("definition", "总和除以数量。"),
            ("example", "2和4的平均值是3。"),
        ):
            resolved = self.engine.requests.resolve(
                result["session_id"],
                {"kind": "request", "id": request_id, "output": name},
            )
            self.assertEqual(
                self.engine.artifacts.content(resolved["artifact_id"]), content
            )
        self.assertFalse(result["tasks"])
        self.assertEqual(len(result["blocks"]), 2)

    def test_previous_text_artifact_is_an_explicit_input(self):
        class AnswerHistoryModel(FixtureModel):
            def intent(self, context, tools):
                if context["query"] == "reuse-answer":
                    return {
                        "operations": [
                            self.operation(
                                "explanation",
                                "none",
                                "general_qa",
                                "reuse-answer",
                                [self.previous],
                            )
                        ]
                    }
                return super().intent(context, tools)

            def run(self, role, instruction, context, output, tools=None, emit=None):
                if (
                    role == "UnifiedSupervisor.Answer"
                    and context.get("operation", {}).get("instruction")
                    == "reuse-answer"
                ):
                    previous = tools["read_artifact"].call(
                        artifact_id=self.previous["id"]
                    )["content"]
                    return output.model_validate(
                        {
                            "blocks": [
                                {
                                    "content_type": "text",
                                    "content": previous,
                                    "refs": [self.previous],
                                }
                            ]
                        }
                    )
                return super().run(role, instruction, context, output, tools, emit)

        self.model = AnswerHistoryModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed("什么是平均值？")
        key = first["blocks"][0]["artifact_id"]
        self.model.previous = {"kind": "artifact", "id": key}
        second = self.completed("reuse-answer", first["session_id"])
        request = second["requests"][0]
        self.assertEqual(request["spec"]["inputs"][0]["source"], self.model.previous)
        actual = self.engine.artifacts.inspect(second["blocks"][0]["artifact_id"])
        self.assertEqual(actual["source_artifact_ids"], [key])
        self.assertEqual(
            self.engine.artifacts.content(actual["artifact_id"]),
            self.engine.artifacts.content(key),
        )
        resolved = self.engine.requests.resolve(
            second["session_id"],
            {"kind": "request", "id": request["request_id"], "output": "answer"},
        )
        self.assertEqual(resolved["artifact_id"], actual["artifact_id"])
        self.assertFalse(second["tasks"])

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

    def test_conflicting_legacy_deliveries_roll_back_migration(self):
        result = self.completed("分别列出前两个和前三个产品")
        with self.engine.stores.db() as db:
            for index, request in enumerate(result["requests"]):
                stored = {key: value for key, value in request.items() if key != "spec"}
                stored["results"] = result["deliveries"][request["request_id"]]
                if index:
                    stored["output_refs"] = {}
                db.execute(
                    "UPDATE requests SET body=? WHERE id=?",
                    (json.dumps(stored), request["request_id"]),
                )
            before = db.execute("SELECT id, body FROM requests ORDER BY id").fetchall()
            before = [tuple(row) for row in before]
        with self.assertRaises(ValueError):
            Stores(Path(self.directory.name))
        with self.engine.stores.db() as db:
            after = [
                tuple(row)
                for row in db.execute("SELECT id, body FROM requests ORDER BY id")
            ]
        self.assertEqual(after, before)

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

        engine = Application(Stores(Path(self.directory.name)), ReferencingModel())
        result = engine.ask(1, "解释平均值并识别库存偏低")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["blocks"]), 2)
        self.assertEqual(
            engine.artifacts.content(result["blocks"][-1]["artifact_id"]),
            "采用哪个库存阈值？",
        )
        self.assertEqual(
            engine.artifacts.inspect(result["blocks"][0]["artifact_id"])[
                "source_artifact_ids"
            ],
            [],
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
        rejected = [
            event for event in events if "rejected_preparation" in event["payload"]
        ]
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
        self.assertIn(
            "Products",
            self.engine.artifacts.content(result["blocks"][0]["artifact_id"]),
        )

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

    def test_request_specs_and_legacy_deliveries_preserve_existing_ledger(self):
        for field in ("results", "output_refs"):
            with self.subTest(field=field):
                result = self.completed()
                request = result["requests"][0]
                deliveries = self.engine.requests.outputs(request)
                artifact_id = deliveries["data"]["id"]
                original_content = self.engine.artifacts.content(artifact_id)
                previous = {
                    key: value for key, value in request.items() if key != "spec"
                }
                previous[field] = deliveries
                with self.engine.stores.db() as db:
                    db.execute(
                        "UPDATE requests SET body=? WHERE id=?",
                        (
                            json.dumps(previous),
                            request["request_id"],
                        ),
                    )
                    for row in db.execute(
                        "SELECT id, body FROM session_events WHERE session_id=?",
                        (request["session_id"],),
                    ).fetchall():
                        event = json.loads(row[1])
                        if event["event_type"] == "plan_updated":
                            event["event_type"] = "agent_output"
                            event["payload"]["summary"] = "Historical execution"
                            db.execute(
                                "UPDATE session_events SET body=? WHERE id=?",
                                (json.dumps(event), row[0]),
                            )
                original_events = self.engine.stores.list(
                    "session_events", "session_id", request["session_id"]
                )
                stores = Stores(Path(self.directory.name))
                self.assertEqual(stores.get("requests", request["request_id"]), request)
                with stores.db() as db:
                    persisted = json.loads(
                        db.execute(
                            "SELECT body FROM requests WHERE id=?",
                            (request["request_id"],),
                        ).fetchone()[0]
                    )
                self.assertNotIn("results", persisted)
                self.assertNotIn("output_refs", persisted)
                self.assertNotIn("spec", persisted)
                self.assertEqual(
                    stores.list("session_events", "session_id", request["session_id"])[
                        :-1
                    ],
                    original_events,
                )
                engine = Application(stores, self.model)
                self.assertEqual(
                    engine.artifacts.content(artifact_id), original_content
                )
                self.assertEqual(engine.requests.outputs(request), deliveries)
                self.assertEqual(
                    engine.requests.resolve(
                        request["session_id"],
                        {
                            "kind": "request",
                            "id": request["request_id"],
                            "output": "data",
                        },
                    ),
                    {"artifact_id": artifact_id},
                )
                self.assertEqual(
                    Stores(Path(self.directory.name)).get(
                        "requests", request["request_id"]
                    ),
                    request,
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
                self.assertIn("spec", request)
                self.assertNotIn("results", request)
                self.assertIn(request["request_id"], result["deliveries"])
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
                artifact = self.engine.requests.outputs(request)["data"]["id"]
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
                orphan = self.engine.responses.message(
                    result["session_id"], result["turn_id"], "请补充统计范围"
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{orphan.artifact_id}", headers=headers
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{orphan.artifact_id}/content", headers=headers
                    ).json()["content"],
                    "请补充统计范围",
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{orphan.artifact_id}/download", headers=headers
                    ).status_code,
                    200,
                )
                identity.create_user("other", "other-password-2026", "analyst")
                other = client.post(
                    "/auth/login",
                    json={"username": "other", "password": "other-password-2026"},
                )
                denied = client.get(
                    f"/artifacts/{orphan.artifact_id}",
                    headers={"Authorization": "Bearer " + other.json()["access_token"]},
                )
                self.assertEqual(denied.status_code, 403)
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
                restored = client.post(
                    f"/sessions/{result['session_id']}/turns/{result['turn_id']}/resume",
                    headers=headers,
                )
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertEqual(
                    restored.json()["response_event_id"], result["response_event_id"]
                )
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
        self.assertTrue(
            all(item["content_type"] == "text" for item in result["artifacts"])
        )

    def test_unified_response_blocks_and_direct_text(self):
        result = self.completed("什么是平均值？")
        block = result["blocks"][0]
        self.assertEqual(
            set(block), {"block_id", "artifact_id", "request_id", "revision_id"}
        )
        item = self.engine.artifacts.inspect(block["artifact_id"])
        self.assertEqual(item["content_type"], "text")
        self.assertEqual(item["producer"], "Supervisor")
        self.assertNotIn("task_id", item)
        self.assertEqual(item["session_id"], result["session_id"])
        self.assertEqual(item["created_by_turn_id"], result["turn_id"])
        event = self.engine.stores.get("session_events", result["response_event_id"])
        self.assertEqual(event["payload"]["schema_version"], 2)
        self.assertEqual(event["payload"]["status"], "completed")
        self.assertEqual(
            result["deliveries"][block["request_id"]]["answer"]["id"],
            item["artifact_id"],
        )

    def test_clarification_without_request_is_session_owned(self):
        class Clarifying(FixtureModel):
            def intent(self, context, tools):
                return {
                    "clarifications": [
                        {"subject": "目标", "question": "需要查询哪个产品？"}
                    ]
                }

        engine = Application(
            Stores(Path(self.directory.name) / "clarification"), Clarifying()
        )
        result = engine.ask(1, "查询一下")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertFalse(result["requests"])
        self.assertFalse(result["tasks"])
        block = result["blocks"][0]
        self.assertEqual(set(block), {"block_id", "artifact_id"})
        item = engine.artifacts.inspect(block["artifact_id"])
        self.assertNotIn("request_id", item)
        self.assertEqual(item["producer"], "System")
        self.assertEqual(item["session_id"], result["session_id"])
        engine.requests.validate_ref(
            result["session_id"], {"kind": "artifact", "id": item["artifact_id"]}
        )
        other = engine.stores.session(2)
        with self.assertRaises(PermissionError):
            engine.requests.validate_ref(
                other["session_id"], {"kind": "artifact", "id": item["artifact_id"]}
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

    def test_text_delivery_tracks_updated_request_inputs(self):
        class Following(FixtureModel):
            def intent(self, context, tools):
                if context["query"] == "follow":
                    return {
                        "operations": [
                            self.operation(
                                "explanation",
                                "none",
                                "general_qa",
                                "解释字段",
                                [
                                    {
                                        "kind": "request",
                                        "id": self.source,
                                        "output": "data",
                                    }
                                ],
                            )
                        ]
                    }
                return super().intent(context, tools)

        self.model = Following()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed()
        self.model.source = first["requests"][0]["request_id"]
        explanation = self.completed("follow", first["session_id"])
        owner = explanation["requests"][0]
        original = explanation["deliveries"][owner["request_id"]]["answer"]["id"]
        self.completed("只统计前两个产品", first["session_id"])
        updated = self.engine.stores.get("requests", owner["request_id"])
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(self.engine.requests.outputs(updated), {})
        resumed = self.engine.resume(1, owner["request_id"])
        self.assertEqual(resumed["status"], "completed")
        self.assertNotEqual(
            resumed["deliveries"][owner["request_id"]]["answer"]["id"], original
        )
        self.assertTrue(self.engine.artifacts.content(original))

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

    def test_composed_table_preserves_sources_and_content(self):
        from ..agents.tools import SpecialistToolbox

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
            sql=f'SELECT product_id AS id FROM "{key}" WHERE 0',
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

    def test_reused_artifact_keeps_producer_and_response_consumer(self):
        class ReuseModel(FixtureModel):
            def intent(self, context, tools):
                if context["query"] == "reuse-data":
                    return {
                        "operations": [
                            self.operation(
                                "copy",
                                "create_task",
                                "result_interaction",
                                "复用表格",
                                [{"kind": "artifact", "id": self.source}],
                            )
                        ]
                    }
                return super().intent(context, tools)

            def definition(self, context):
                if context["operation"]["instruction"] == "复用表格":
                    return {
                        "description": "复用表格",
                        "inputs": [self.binding("source", "artifact", self.source)],
                        "outputs": [
                            {
                                "name": "data",
                                "content_type": "table",
                                "description": "原表",
                            }
                        ],
                    }
                return FixtureModel.definition(context)

            def plan(self, context, tools):
                if context["spec"]["description"] == "复用表格":
                    return {
                        "tasks": [],
                        "output_bindings": {
                            "data": {"kind": "artifact", "id": self.source}
                        },
                        "reason": "复用明确的历史表",
                    }
                return super().plan(context, tools)

        self.model = ReuseModel()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        first = self.completed()
        self.model.source = first["deliveries"][first["requests"][0]["request_id"]][
            "data"
        ]["id"]
        producer = self.engine.artifacts.inspect(self.model.source)
        second = self.completed("reuse-data", first["session_id"])
        block = next(
            block
            for block in second["blocks"]
            if block["artifact_id"] == self.model.source
        )
        self.assertEqual(block["request_id"], second["requests"][0]["request_id"])
        self.assertNotEqual(block["request_id"], producer["request_id"])
        self.assertEqual(self.engine.artifacts.inspect(self.model.source), producer)
        self.assertIn(
            self.model.source, {item["artifact_id"] for item in second["artifacts"]}
        )

    def downgrade_text_ledger(self):
        result = self.completed("什么是平均值？")
        old_key = result["blocks"][0]["artifact_id"]
        with self.engine.stores.db() as db:
            events = [
                json.loads(row[0])
                for row in db.execute("SELECT body FROM session_events ORDER BY seq")
            ]
            event = next(
                item
                for item in events
                if item["event_type"] == "agent_output"
                and item["payload"].get("blocks")
            )
            event_id = event["event_id"]
            for item in events:
                payload = item["payload"]
                if "blocks" in payload:
                    payload.pop("schema_version", None)
                    payload.pop("status", None)
                    payload["blocks"] = [
                        {
                            "block_id": block["block_id"],
                            "request_id": block["request_id"],
                            "kind": "text",
                            "content": self.engine.artifacts.content(
                                block["artifact_id"]
                            ),
                            "refs": [],
                        }
                        for block in payload["blocks"]
                    ]
                if item["event_type"] == "plan_updated":
                    payload["plan"]["output_bindings"] = {
                        "answer": {"kind": "event", "id": event_id}
                    }
                db.execute(
                    "UPDATE session_events SET body=? WHERE id=?",
                    (json.dumps(item, ensure_ascii=False), item["event_id"]),
                )
            for row in db.execute("SELECT id,body FROM request_revisions").fetchall():
                revision = json.loads(row[1])
                for output in revision["spec"]["outputs"]:
                    output["kind"] = output.pop("content_type")
                db.execute(
                    "UPDATE request_revisions SET body=? WHERE id=?",
                    (json.dumps(revision), row[0]),
                )
            db.execute("DELETE FROM artifacts WHERE id=?", (old_key,))
            db.execute("PRAGMA user_version=3")
        return result, old_key, event_id

    def test_unified_migration_recovers_historical_text_and_bindings(self):
        result, old_key, event_id = self.downgrade_text_ledger()
        with self.engine.stores.db() as db:
            revision = json.loads(
                db.execute("SELECT body FROM request_revisions").fetchone()[0]
            )
            revision["spec"]["inputs"] = [
                {
                    "slot": "old_text",
                    "source": {
                        "kind": "event",
                        "id": event_id,
                        "block_id": "block_001",
                    },
                }
            ]
            db.execute("UPDATE request_revisions SET body=?", (json.dumps(revision),))
        original_file = self.engine.artifacts.root / old_key / "text.md"
        before = original_file.read_bytes()
        store = Stores(Path(self.directory.name))
        engine = Application(store, FixtureModel())
        request = store.get("requests", result["requests"][0]["request_id"])
        delivery = engine.requests.outputs(request)["answer"]
        self.assertEqual(request["spec"]["inputs"][0]["source"], delivery)
        self.assertEqual(delivery["kind"], "artifact")
        self.assertNotEqual(delivery["id"], old_key)
        self.assertEqual(
            engine.artifacts.content(delivery["id"]), "平均值是总和除以数量。"
        )
        self.assertEqual(original_file.read_bytes(), before)
        event = store.get("session_events", event_id)
        self.assertEqual(event["payload"]["schema_version"], 2)
        self.assertEqual(event["payload"]["blocks"][0]["artifact_id"], delivery["id"])
        self.assertEqual(
            event["payload"]["blocks"][0]["revision_id"], request["current_revision_id"]
        )
        with store.db() as db:
            count = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        reopened = Stores(Path(self.directory.name))
        with reopened.db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], count
            )
        self.assertTrue(
            (
                Path(self.directory.name) / "agent.before-unified-artifacts.sqlite"
            ).exists()
        )

    def test_unified_migration_failure_removes_new_content_and_can_retry(self):
        result, old_key, event_id = self.downgrade_text_ledger()
        root = Path(self.directory.name) / "content"
        before = set(root.iterdir())
        with self.engine.stores.db() as db:
            revision = json.loads(
                db.execute("SELECT body FROM request_revisions").fetchone()[0]
            )
            revision["spec"].pop("description")
            db.execute("UPDATE request_revisions SET body=?", (json.dumps(revision),))
        with self.assertRaises(ValidationError):
            Stores(Path(self.directory.name))
        self.assertEqual(set(root.iterdir()), before)
        with self.engine.stores.db() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0
            )
            revision["spec"]["description"] = "什么是平均值？"
            db.execute("UPDATE request_revisions SET body=?", (json.dumps(revision),))
        store = Stores(Path(self.directory.name))
        event = store.get("session_events", event_id)
        self.assertTrue(
            store.exists("artifacts", event["payload"]["blocks"][0]["artifact_id"])
        )

    def test_frontend_renders_only_response_artifacts_in_order(self):
        script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.className=''; this.textContent=''; this.classList={toggle(){},add(){},remove(){}} }
  append(...children) { this.children.push(...children) }
  replaceChildren(...children) { this.children=children }
}
const nodes={conversationLog:new Element('log'),resultEmpty:new Element('empty'),chatScroll:new Element('scroll')};
const calls=[];
const artifacts={
  result_001:{artifact_id:'result_001',content_type:'text',title:'说明',source_artifact_ids:['result_hidden']},
  result_002:{artifact_id:'result_002',content_type:'table',title:'真实数据',summary:'完整数据',payload:{row_count:1}},
  result_003:{artifact_id:'result_003',content_type:'report',title:'报告'},
};
const responses={
  '/sessions/s/events':[{event_type:'assistant_response',payload:{schema_version:2,blocks:[{block_id:'a',artifact_id:'result_001'},{block_id:'b',artifact_id:'result_002'},{block_id:'c',artifact_id:'result_003'}]}}],
  '/artifacts/result_001/content':{content:'平均值的解释'},
  '/artifacts/result_002/content?offset=0&limit=6':{rows:[{n:16}],total_rows:1,has_more:false},
  '/artifacts/result_003/content':{content:'# 报告正文'},
};
const context={document:{createElement:tag=>new Element(tag),getElementById:id=>nodes[id],addEventListener(){}},sessionStorage:{getItem(){return null}},requestAnimationFrame:fn=>fn(),fetch:async path=>{calls.push(path);const data=responses[path]||artifacts[path.split('/').pop()];assert.ok(data, path);return {ok:true,status:200,json:async()=>data}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
vm.runInContext("state.sessionId='s'; loadConversation()",context).then(()=>{
  const body=nodes.conversationLog.children[0].children[1];
  assert.equal(body.children.length,3);
  assert.equal(body.children[0].className,'message-text');
  assert.equal(body.children[1].className,'inline-artifact');
  assert.equal(body.children[2].className,'inline-artifact');
  assert.ok(!calls.some(path=>path.includes('result_hidden')));
  assert.ok(body.children[1].children.some(child=>child.tag==='div'&&child.className==='table-wrap'));
}).catch(error=>{console.error(error);process.exitCode=1});
"""
        subprocess.run(
            ["node", "-e", script, str(ROOT / "src/enterprise_agent/ui/app.js")],
            check=True,
            capture_output=True,
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

    def test_answer_binding_type_error_is_repaired_with_feedback(self):
        class MismatchedAnswer(FixtureModel):
            @staticmethod
            def answer(context):
                value = FixtureModel.answer(context)
                if not context.get("validation_feedback"):
                    value["blocks"][0]["output_name"] = "data"
                return value

        self.model = MismatchedAnswer()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        result = self.completed()
        self.assertEqual(self.model.calls.count("UnifiedSupervisor.Answer"), 2)
        feedback = next(
            context["validation_feedback"]
            for role, context in self.model.contexts
            if role == "UnifiedSupervisor.Answer" and context.get("validation_feedback")
        )
        self.assertIn("text", feedback["error"])
        self.assertEqual(
            [
                self.engine.artifacts.inspect(block["artifact_id"])["content_type"]
                for block in result["blocks"]
            ],
            ["text", "table"],
        )

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

    def test_state_changed_during_preparation_reloads_before_retry(self):
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
            result = self.completed("只统计前两个产品", first["session_id"])
        self.assertEqual(self.model.calls.count("UnifiedSupervisor.Preparation"), 3)
        self.assertEqual(
            result["requests"][0]["plan_version"],
            first["requests"][0]["plan_version"] + 2,
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

    def test_langgraph_preview_resumes_after_application_restart(self):
        preview = self.engine.ask(1, "统计产品并生成图表和报告", preview=True)
        request = preview["requests"][0]
        snapshot = self.engine.execution.snapshot(request)
        self.assertIn("wait", snapshot.next)
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        self.assertTrue(self.engine.checkpoints.path.exists())
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

    def test_source_syntax(self):
        for path in (ROOT / "src").rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        subprocess.run(
            ["node", "--check", str(ROOT / "src/enterprise_agent/ui/app.js")],
            check=True,
            capture_output=True,
        )

    def test_direct_answer_waits_for_its_request_input(self):
        class DependentAnswerModel(FixtureModel):
            def intent(self, context, tools):
                return {
                    "operations": [
                        self.operation(
                            "data", "create_task", "data_analysis", "列出前五个产品"
                        ),
                        self.operation(
                            "explanation",
                            "none",
                            "general_qa",
                            "解释上游数据",
                            [
                                {"kind": "request", "id": "data", "output": "data"},
                            ],
                        ),
                    ]
                }

        class MissingSemantics:
            def execute(self, context, allowed, emit):
                return {
                    "status": "needs_clarification",
                    "summary": "需要确认口径",
                    "semantic_binding": {
                        "status": "ambiguous",
                        "explanation": "需要确认口径",
                    },
                    "produced_outputs": {},
                }

        engine = Application(
            Stores(Path(self.directory.name) / "dependent-answer"),
            DependentAnswerModel(),
            specialist_overrides={"SemanticAgent": MissingSemantics()},
        )
        result = engine.ask(1, "query then explain")
        self.assertEqual(result["status"], "needs_clarification", result["blocks"])
        self.assertEqual(
            [item["status"] for item in result["requests"]],
            ["needs_clarification", "needs_clarification"],
        )
        self.assertTrue(
            all(item["content_type"] == "text" for item in result["artifacts"])
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
                return ChatResponse(
                    '{"blocks":[{"content_type":"text","content":"完成"}]}'
                )

        transport, events = ScriptedTransport(), []
        answer = ModelClient(transport).run(
            "TestAgent",
            "返回文字",
            {},
            AnswerDraft,
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
                return ChatResponse(
                    '{"blocks":[{"content_type":"text","content":"已处理"}]}'
                )

        transport = ScriptedTransport()
        answer = ModelClient(transport).run("TestAgent", "返回文字", {}, AnswerDraft)
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
        from .plugins import Plugins
        from .catalog import Catalog

        suite = unittest.TestSuite(
            [
                unittest.defaultTestLoader.loadTestsFromTestCase(Smoke),
                unittest.defaultTestLoader.loadTestsFromTestCase(Plugins),
                unittest.defaultTestLoader.loadTestsFromTestCase(Catalog),
            ]
        )
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
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
                item for item in result["artifacts"] if item["content_type"] == "table"
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
