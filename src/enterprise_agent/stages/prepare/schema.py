"""Structured output owned by this stage."""

from pydantic import Field, model_validator

from enterprise_agent.domain.contract import Model
from enterprise_agent.domain.request import RequestSpec
from enterprise_agent.domain.task import Plan
from enterprise_agent.stages.intent.schema import Clarification


class RequestPreparation(Model):
    """One model proposal for a complete specification and initial execution plan."""

    spec: RequestSpec | None = None
    plan: Plan | None = None
    clarifications: list[Clarification] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_proposal(self):
        ready = (
            self.spec is not None and self.plan is not None and not self.clarifications
        )
        blocked = self.spec is None and self.plan is None and bool(self.clarifications)
        if not (ready or blocked):
            raise ValueError("返回完整 spec 与 plan，或仅返回具体 clarifications")
        return self
