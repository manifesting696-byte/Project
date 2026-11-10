"""Execution plan, its tasks and the semantic binding a task carries."""

from typing import Any, Literal

from pydantic import Field

from enterprise_agent.domain.contract import AgentName, Model
from enterprise_agent.domain.refs import InputBinding, ResourceRef


class PlannedTask(Model):
    task_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    assigned_agent: AgentName = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    input_bindings: list[InputBinding] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    replan_after: bool = False


class Plan(Model):
    tasks: list[PlannedTask] = Field(default_factory=list)
    cancel_task_ids: list[str] = Field(default_factory=list)
    output_bindings: dict[str, ResourceRef] = Field(default_factory=dict)
    reason: str


class SemanticBinding(Model):
    status: Literal["bound", "ambiguous", "unavailable"]
    metrics: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time: dict[str, Any] = Field(default_factory=dict)
    grain: str | None = None
    explanation: str
    ambiguities: list[str] = Field(default_factory=list)


class AgentResult(Model):
    summary: str
    produced_outputs: dict[str, ResourceRef] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)


class Task(PlannedTask):
    request_id: str
    revision_id: str
    plan_version: int
    created_by_turn_id: str
    produced_outputs: dict[str, ResourceRef] = Field(default_factory=dict)
    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "needs_clarification",
        "skipped",
        "cancelled",
        "superseded",
    ]
    summary: str | None = None
    semantic_binding: SemanticBinding | None = None
    suggestions: list[str] = Field(default_factory=list)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
