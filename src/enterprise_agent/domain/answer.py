"""Verification verdicts, answer drafts and the published response."""

from typing import Literal

from pydantic import Field

from enterprise_agent.domain.base import ContentType, Model
from enterprise_agent.domain.refs import IntentRef, ResourceRef


class OutputCheck(Model):
    name: str
    status: Literal["completed", "unfulfilled", "needs_clarification"]
    refs: list[ResourceRef] = Field(default_factory=list)
    reason: str


class Completion(Model):
    outputs: list[OutputCheck]
    next_action: Literal["answer", "replan", "clarify"]
    reason: str


class AnswerDraftBlock(Model):
    """Transient model output, materialized before publishing a response."""

    request_id: str | None = None
    output_name: str | None = None
    content_type: ContentType
    content: str | None = None
    refs: list[IntentRef] = Field(default_factory=list)


class AnswerDraft(Model):
    blocks: list[AnswerDraftBlock]


class ResponseBlock(Model):
    block_id: str
    artifact_id: str
    request_id: str | None = None
    revision_id: str | None = None


class Response(Model):
    blocks: list[ResponseBlock]
