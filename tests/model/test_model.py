"""Acceptance tests for request identity, revisions, task execution and provenance."""

import json
from copy import deepcopy

import httpx

from enterprise_agent.domain.answer import AnswerDraft
from enterprise_agent.model.client import ModelClient
from enterprise_agent.model.tool_call import STRING, tool
from enterprise_agent.model.transport import (
    ChatResponse,
    OpenAICompatibleTransport,
    ToolCall,
)
from tests.support import AgentTestCase


class ModelTests(AgentTestCase):
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
