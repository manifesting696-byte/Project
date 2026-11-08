"""Turn intent selects request operations; code owns durable IDs and state."""

from typing import Literal

from pydantic import Field, model_validator

from ..core.models import Category, Model, ResourceRef


class RequestOperation(Model):
    request_id: str = Field(min_length=1)
    action: Literal["none", "create_task", "update_task"]
    category: Category
    instruction: str = Field(min_length=1)
    refs: list[ResourceRef] = Field(default_factory=list)


class Clarification(Model):
    request_id: str | None = None
    subject: str = Field(min_length=1)
    question: str = Field(min_length=1)
    refs: list[ResourceRef] = Field(default_factory=list)


class Intent(Model):
    operations: list[RequestOperation] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_turn(self):
        ids = [item.request_id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("本轮同一 Request 的修改必须合并为一项操作")
        if not self.operations and not self.clarifications:
            raise ValueError("必须给出请求操作或具体澄清问题")
        return self
