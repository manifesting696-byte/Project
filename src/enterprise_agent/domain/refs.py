"""References between requests, tasks, artifacts and events."""

from typing import Any, Literal

from pydantic import Field, model_validator

from enterprise_agent.domain.contract import ContentType, Model


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


class IntentRef(Model):
    kind: Literal["request", "artifact"]
    id: str = Field(min_length=1)
    output: str | None = None

    @model_validator(mode="after")
    def select_output(self):
        if self.kind == "request" and not self.output:
            raise ValueError("Request 引用必须指定 output")
        if self.kind == "artifact" and self.output is not None:
            raise ValueError("Artifact 直接通过 id 定位")
        return self


class InputBinding(Model):
    slot: str = Field(min_length=1)
    source: ResourceRef


class OutputSpec(Model):
    name: str = Field(min_length=1)
    content_type: ContentType
    description: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)
