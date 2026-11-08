"""Intent IR belongs to Unified Supervisor; it describes this Turn only."""
from typing import Any, Literal
from pydantic import Field, model_validator
from ..core.models import Model, ResourceRef, InputBinding


class RequestChange(Model):
    action: Literal["add", "replace", "remove"]
    request_id: str = Field(min_length=1)
    type: Literal["data_analysis", "visualization", "report", "business_knowledge", "general_qa"] | None = None
    description: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[InputBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_change(self):
        if self.action != "remove" and (not self.type or not self.description):
            raise ValueError("添加或修改需求必须提供 type 和 description")
        return self


class Intent(Model):
    dialogue_action: Literal["new_task", "continue", "modify", "independent", "clarify"]
    target_task_id: str | None = None
    goal: str = ""
    constraints_patch: dict[str, Any] = Field(default_factory=dict)
    request_changes: list[RequestChange] = Field(default_factory=list)
    refs: list[ResourceRef] = Field(default_factory=list)
    clarification: str | None = None

