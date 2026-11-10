"""Provider seam for chat completion; the Agent loop never performs HTTP."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from enterprise_agent.settings import model_settings


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ChatResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()


class ChatTransport(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...


class OpenAICompatibleTransport:
    """Adapter for Chat Completions providers such as DeepSeek and OpenAI."""

    def __init__(
        self,
        settings: Callable[[], tuple[str, str, str]] = model_settings,
        client_factory: Callable[[], httpx.Client] | None = None,
    ):
        self.settings = settings
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(120, connect=10))
        )

    def complete(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        endpoint, key, model = self.settings()
        request = {
            "model": model,
            "temperature": 0,
            "max_tokens": 6000,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if tools:
            request["tools"] = tools
        with self.client_factory() as client:
            response = client.post(
                endpoint, headers={"Authorization": f"Bearer {key}"}, json=request
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
        return ChatResponse(
            content=message.get("content"),
            tool_calls=tuple(
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=call["function"]["arguments"],
                )
                for call in message.get("tool_calls") or ()
            ),
        )
