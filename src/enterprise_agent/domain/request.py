"""Request identity, its immutable revision and the full business specification."""

from typing import Any, Literal

from pydantic import Field, model_validator

from enterprise_agent.domain.base import Model
from enterprise_agent.domain.refs import InputBinding, OutputSpec


class RequestSpec(Model):
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    inputs: list[InputBinding] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_names(self):
        names = [item.name for item in self.outputs]
        slots = [item.slot for item in self.inputs]
        if len(names) != len(set(names)) or len(slots) != len(set(slots)):
            raise ValueError("交付名称和输入槽位必须各自唯一")
        return self


class RequestRecord(Model):
    """Stored identity and state; the specification lives in an immutable revision."""

    request_id: str
    session_id: str
    title: str
    created_by_turn_id: str
    current_revision_id: str
    plan_version: int = 0
    status: Literal[
        "pending", "running", "needs_clarification", "completed", "failed", "archived"
    ]
    created_at: str
    updated_at: str


class Request(RequestRecord):
    """Complete request returned to callers with its current specification."""

    spec: RequestSpec


class RequestRevision(Model):
    revision_id: str
    request_id: str
    parent_revision_id: str | None = None
    source_intent_event_id: str
    spec: RequestSpec
    created_at: str
