"""Shared Agent execution contracts; subclasses own only role-specific behavior."""

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from ..core.model import ModelRunner
from ..core.models import AgentResult
from .tools import SpecialistToolbox


class ModelAgent:
    def __init__(self, model: ModelRunner):
        self.model = model

    def invoke(
        self,
        role: str,
        instruction: str,
        context: dict,
        output: type[BaseModel],
        tools: dict,
        emit,
    ):
        return self.model.run(role, instruction, context, output, tools, emit)


class SpecialistAgent(ModelAgent, ABC):
    @abstractmethod
    def execute(self, context: dict, allowed: set[str], emit) -> dict:
        """Run one execution Task using only its authorized inputs."""


class ArtifactAgent(SpecialistAgent, ABC):
    role: ClassVar[str]
    instruction: ClassVar[str]
    output_types: ClassVar[frozenset[str]]

    def __init__(self, model: ModelRunner, registry, artifacts, gateway):
        super().__init__(model)
        self.registry, self.artifacts, self.gateway = registry, artifacts, gateway

    @abstractmethod
    def role_tools(self, toolbox: SpecialistToolbox, context: dict) -> dict:
        """Register tools granted to this role in addition to artifact reads."""

    def execute(self, context: dict, allowed: set[str], emit) -> dict:
        node = context["task"]
        toolbox = SpecialistToolbox(
            node, allowed, self.registry, self.artifacts, self.gateway
        )
        tools = {**toolbox.common_tools(), **self.role_tools(toolbox, context)}
        result = self.invoke(
            self.role, self.instruction, context, AgentResult, tools, emit
        )
        if set(result.produced_outputs) != set(node["expected_outputs"]):
            raise ValueError("执行输出与声明的输出名称不一致")
        for ref in result.produced_outputs.values():
            if ref.kind != "artifact" or ref.id not in toolbox.created:
                raise ValueError("输出必须引用当前 Agent 实际生成的产物")
            if self.artifacts.inspect(ref.id)["type"] not in self.output_types:
                raise ValueError("产物类型不符合 Agent 的职责")
        return {"status": "completed", **result.model_dump(exclude_none=True)}
