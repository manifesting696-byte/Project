"""Strict communication skeletons shared by the five logical Agents and stores."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResourceRef(Model):
    kind: Literal["request", "subtask", "artifact", "event"]
    id: str = Field(min_length=1)
    output: str | None = None

    @model_validator(mode="after")
    def named_output_only(self):
        if self.output is not None and self.kind != "subtask":
            raise ValueError("output 仅用于引用 Subtask 的命名输出；Artifact 只使用 kind 和 id")
        return self


class InputBinding(Model):
    slot: str = Field(min_length=1)
    source: ResourceRef


class Requirement(Model):
    request_id: str = Field(min_length=1)
    type: Literal["data_analysis", "visualization", "report", "business_knowledge", "general_qa"]
    description: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[InputBinding] = Field(default_factory=list)




AgentName = Literal["SemanticAgent", "QueryAgent", "AnalysisAgent", "PresentationAgent"]


class PlannedSubtask(Model):
    subtask_id: str = Field(min_length=1)
    request_ids: list[str]
    objective: str = Field(min_length=1)
    assigned_agent: AgentName
    depends_on: list[str] = Field(default_factory=list)
    input_bindings: list[InputBinding] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    replan_after: bool = False


class Plan(Model):
    subtasks: list[PlannedSubtask] = Field(default_factory=list)
    cancel_subtask_ids: list[str] = Field(default_factory=list)
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


class EvidenceRef(ResourceRef):
    kind: Literal["artifact", "subtask"]


class RequirementCheck(Model):
    request_id: str
    status: Literal["completed", "unfulfilled", "needs_clarification"]
    refs: list[EvidenceRef] = Field(default_factory=list)
    reason: str


class Completion(Model):
    requirements: list[RequirementCheck]
    next_action: Literal["answer", "replan", "clarify"]
    reason: str


class AnswerBlock(Model):
    block_id: str = ""
    kind: Literal["text", "table", "chart", "report"]
    content: str | None = None
    refs: list[ResourceRef] = Field(default_factory=list)


class Answer(Model):
    blocks: list[AnswerBlock]


class Artifact(Model):
    artifact_id: str
    type: Literal["query_result", "analysis_result", "chart", "report"]
    task_id: str
    revision_id: str
    subtask_id: str
    request_ids: list[str]
    producer: AgentName
    title: str
    summary: str
    source_refs: list[ResourceRef]
    content_ref: str
    payload: dict[str, Any]
    created_at: str


class Session(Model):
    session_id: str
    user_id: str
    title: str
    last_active_task_id: str | None = None
    last_response_event_id: str | None = None
    last_seq: int = 0
    created_at: str
    updated_at: str


class SessionEvent(Model):
    event_id: str
    session_id: str
    seq: int
    turn_id: str
    event_type: Literal["user_message", "intent_resolved", "agent_output", "tool_call", "tool_result", "assistant_response"]
    task_id: str | None = None
    subtask_id: str | None = None
    actor: str
    payload: dict[str, Any]
    created_at: str

    @model_validator(mode="after")
    def validate_payload(self):
        fields = {"user_message": ("content",), "intent_resolved": ("intent",),
                  "agent_output": ("summary",), "tool_call": ("tool_call_id", "tool_name", "arguments"),
                  "tool_result": ("tool_call_id", "summary"), "assistant_response": ("blocks",)}
        if any(key not in self.payload for key in fields[self.event_type]):
            raise ValueError("事件内容缺少必要字段")
        if self.event_type == "assistant_response":
            for block in self.payload["blocks"]:
                AnswerBlock.model_validate(block)
        return self


class Task(Model):
    task_id: str
    origin_session_id: str
    created_by_turn_id: str
    goal: str
    current_revision_id: str | None = None
    plan_version: int
    status: Literal["active", "completed", "paused"]
    created_at: str
    updated_at: str
    current_turn_id: str | None = None
    current_intent: dict[str, Any] | None = None
    current_query: str | None = None
    completion: dict[str, Any] | None = None


class TaskRevision(Model):
    revision_id: str
    task_id: str
    parent_revision_id: str | None = None
    source_intent_event_id: str
    constraints: dict[str, Any]
    requirements: list[Requirement]
    created_at: str


class Subtask(PlannedSubtask):
    task_id: str
    revision_id: str
    created_by_turn_id: str
    produced_outputs: dict[str, ResourceRef]
    status: Literal["pending", "running", "completed", "failed", "needs_clarification", "skipped", "cancelled", "superseded"]
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    semantic_binding: SemanticBinding | None = None
    summary: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class SessionSummary(Model):
    summary_id: str
    session_id: str
    covered_to_seq: int
    content: str
    key_refs: list[ResourceRef]
    created_at: str
