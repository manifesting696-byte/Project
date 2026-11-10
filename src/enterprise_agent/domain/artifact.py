"""Result artifact metadata; the body lives under runtime/content/<id>."""

from typing import Any

from pydantic import Field

from enterprise_agent.domain.contract import ContentType, Model


class Artifact(Model):
    artifact_id: str
    content_type: ContentType
    session_id: str
    created_by_turn_id: str
    request_id: str | None = None
    revision_id: str | None = None
    task_id: str | None = None
    producer: str = Field(min_length=1)
    title: str
    summary: str
    source_artifact_ids: list[str] = Field(default_factory=list)
    content_ref: str
    payload: dict[str, Any]
    created_at: str
