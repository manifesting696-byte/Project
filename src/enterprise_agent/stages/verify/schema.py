"""Structured output owned by this stage."""

from typing import Literal

from pydantic import Field

from enterprise_agent.domain.contract import Model
from enterprise_agent.domain.refs import ResourceRef


class OutputCheck(Model):
    name: str
    status: Literal["completed", "unfulfilled", "needs_clarification"]
    refs: list[ResourceRef] = Field(default_factory=list)
    reason: str


class Completion(Model):
    outputs: list[OutputCheck]
    next_action: Literal["answer", "replan", "clarify"]
    reason: str
