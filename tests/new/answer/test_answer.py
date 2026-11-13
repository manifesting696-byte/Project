"""Acceptance tests for request identity, revisions, task execution and provenance."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from enterprise_agent.flow.application import Application
from enterprise_agent.ledger.summaries import compress
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.support import AgentTestCase


class AnswerTests(AgentTestCase):
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

    def test_direct_answer_self_reference_is_repaired_with_original_context(self):
        class SelfReferencingAnswer(FixtureModel):
            @staticmethod
            def intent(context, tools):
                return {
                    "operations": [
                        {
                            "request_id": "introduction",
                            "action": "none",
                            "category": "general_qa",
                            "instruction": "你是什么AI",
                            "refs": [],
                        }
                    ],
                    "clarifications": [],
                }

            @staticmethod
            def answer(context):
                request_id = context["request"]["request_id"]
                if not context.get("validation_feedback") or "operation" not in context:
                    return {
                        "blocks": [
                            {
                                "request_id": request_id,
                                "content_type": "text",
                                "content": "我是企业数据问析助手。",
                                "refs": [
                                    {
                                        "kind": "request",
                                        "id": request_id,
                                        "output": "answer",
                                    }
                                ],
                            }
                        ]
                    }
                assert context["operation"]["action"] == "none"
                assert context["operation"]["category"] == "general_qa"
                assert "database_metadata" in context
                assert "available_capabilities" in context
                assert "当前 Request" in context["validation_feedback"]["error"]
                return {
                    "blocks": [
                        {
                            "request_id": request_id,
                            "content_type": "text",
                            "content": "我是企业数据问析助手。",
                            "refs": [],
                        }
                    ]
                }

        self.model = SelfReferencingAnswer()
        self.engine.close()
        self.engine = Application(Stores(Path(self.directory.name)), self.model)
        result = self.engine.ask(1, "你是什么AI")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["tasks"], [])
        self.assertEqual(self.model.calls.count("UnifiedSupervisor.Answer"), 2)
        self.assertEqual(
            self.engine.artifacts.content(result["blocks"][0]["artifact_id"]),
            "我是企业数据问析助手。",
        )

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
