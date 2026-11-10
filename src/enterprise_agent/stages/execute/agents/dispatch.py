"""The four specialist roles and their dispatch table."""

from enterprise_agent.stages.execute.agents.capabilities import CAPABILITIES


class Specialists:
    """Scheduler-facing adapter; role implementations can be replaced at composition."""

    def __init__(self, events=None):
        self.agents = {}
        self.capabilities = {"Supervisor": CAPABILITIES["Supervisor"]}
        self.events = events

    def register(self, role, agent, description):
        if role in self.agents or role == "Supervisor":
            raise ValueError(f"Agent 重复注册：{role}")
        if not role or not description or not callable(getattr(agent, "execute", None)):
            raise ValueError("Agent 必须提供名称、职责说明和 execute")
        self.agents[role] = agent
        self.capabilities[role] = description

        def dispose():
            self.agents.pop(role, None)
            self.capabilities.pop(role, None)

        return dispose

    def execute(self, context, allowed, emit):
        role = context["task"]["assigned_agent"]
        if role not in self.agents:
            raise ValueError(f"Agent 当前未启用：{role}")
        scope = {"role": role, "task_id": context["task"]["task_id"]}
        if self.events:
            self.events.emit("agent/before_execute", scope)
        result = self.agents[role].execute(context, allowed, emit)
        if self.events:
            self.events.notify(
                "agent/after_execute", {**scope, "status": result["status"]}
            )
        return result
