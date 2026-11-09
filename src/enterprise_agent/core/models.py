"""Strict contracts for requests, revisions, execution tasks and artifacts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Category = Literal[
    "general_qa", "business_knowledge", "data_analysis", "result_interaction"
]
AgentName = Literal["SemanticAgent", "QueryAgent", "AnalysisAgent", "PresentationAgent"]


class ResourceRef(Model):
    kind: Literal["request", "task", "artifact", "event"]
    id: str = Field(min_length=1)
    output: str | None = None
    block_id: str | None = None

    @model_validator(mode="after")
    def valid_selector(self):
        if self.output is not None and self.kind not in ("request", "task"):
            raise ValueError("output 仅用于 Request 或执行 Task 的命名输出")
        if self.block_id is not None and self.kind != "event":
            raise ValueError("block_id 仅用于会话事件中的回答块")
        return self


class InputBinding(Model):
    slot: str = Field(min_length=1)
    source: ResourceRef


class OutputSpec(Model):
    name: str = Field(min_length=1)
    kind: Literal["text", "table", "chart", "report"]
    description: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


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


class PlannedTask(Model):
    task_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    assigned_agent: AgentName
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


class OutputCheck(Model):
    name: str
    status: Literal["completed", "unfulfilled", "needs_clarification"]
    refs: list[ResourceRef] = Field(default_factory=list)
    reason: str


class Completion(Model):
    outputs: list[OutputCheck]
    next_action: Literal["answer", "replan", "clarify"]
    reason: str


class AnswerBlock(Model):
    block_id: str = ""
    request_id: str | None = None
    output_name: str | None = None
    kind: Literal["text", "table", "chart", "report"]
    content: str | None = None
    refs: list[ResourceRef] = Field(default_factory=list)


class Answer(Model):
    blocks: list[AnswerBlock]


class Artifact(Model):
    artifact_id: str
    type: Literal["query_result", "analysis_result", "chart", "report"]
    request_id: str
    revision_id: str
    task_id: str
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
        if "blocks" in self.payload:
            for block in self.payload["blocks"]:
                AnswerBlock.model_validate(block)
        return self


class SessionSummary(Model):
    summary_id: str
    session_id: str
    covered_to_seq: int
    content: str
    key_refs: list[ResourceRef]
    created_at: str
