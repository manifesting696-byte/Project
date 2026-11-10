"""Session ledger records: session, ordered events and compressed summaries."""

from typing import Any, Literal

from pydantic import model_validator

from enterprise_agent.domain.answer import ResponseBlock
from enterprise_agent.domain.base import Model
from enterprise_agent.domain.refs import ResourceRef


class Session(Model):
    session_id: str
    user_id: str
    title: str
    last_active_request_id: str | None = None
    last_response_event_id: str | None = None
    last_seq: int = 0
    created_at: str
    updated_at: str


class SessionEvent(Model):
    event_id: str
    session_id: str
    seq: int
    turn_id: str
    event_type: Literal[
        "user_message",
        "intent_resolved",
        "request_revised",
        "plan_updated",
        "agent_output",
        "tool_call",
        "tool_result",
        "assistant_response",
    ]
    request_id: str | None = None
    task_id: str | None = None
    actor: str
    payload: dict[str, Any]
    created_at: str

    @model_validator(mode="after")
    def validate_payload(self):
        required = {
            "user_message": ("content",),
            "intent_resolved": ("intent",),
            "request_revised": ("revision_id",),
            "plan_updated": ("plan",),
            "agent_output": ("summary",),
            "tool_call": ("tool_call_id", "tool_name", "arguments"),
            "tool_result": ("tool_call_id", "summary"),
            "assistant_response": ("blocks",),
        }
        if any(key not in self.payload for key in required[self.event_type]):
            raise ValueError("事件缺少必要字段")
        if "blocks" in self.payload and self.payload.get("schema_version") == 2:
            for block in self.payload["blocks"]:
                ResponseBlock.model_validate(block)
        return self


class SessionSummary(Model):
    summary_id: str
    session_id: str
    covered_to_seq: int
    content: str
    key_refs: list[ResourceRef]
    created_at: str
