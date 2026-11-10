"""Shared model/tool dependencies; stage implementations own the behavior."""

from enterprise_agent.stages.answer.inference import answer
from enterprise_agent.stages.execute.agents.invocation import ModelAgent
from enterprise_agent.stages.execute.tools.supervisor import supervisor_tools
from enterprise_agent.stages.intent.inference import intent
from enterprise_agent.stages.prepare.inference import plan, prepare
from enterprise_agent.stages.verify.inference import check


class Supervisor(ModelAgent):
    intent = intent
    prepare = prepare
    plan = plan
    check = check
    answer = answer

    def __init__(self, model, context, stores, artifacts, tool_registry=None):
        super().__init__(model)
        self.context, self.stores, self.artifacts = context, stores, artifacts
        self.tool_registry = tool_registry

    def tools(self, session_id, drafts=None, confirmed_refs=None):
        if self.tool_registry is not None:
            return self.tool_registry.build(
                "Supervisor",
                {
                    "session_id": session_id,
                    "drafts": drafts,
                    "confirmed_refs": confirmed_refs,
                },
            )
        return supervisor_tools(
            session_id,
            self.context,
            self.stores,
            self.artifacts,
            drafts,
            confirmed_refs,
        )
