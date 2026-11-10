"""Stage-owned implementation behind the application facade."""

from enterprise_agent.domain.errors import RepairableError


def apply_plan(self, request, turn_id, plan, workflow_key=None):
    for attempt in range(2):
        try:
            self.tasks.apply_plan(request, turn_id, plan, workflow_key=workflow_key)
            return
        except RepairableError as exc:
            self.stores.event(
                request["session_id"],
                turn_id,
                "agent_output",
                "Supervisor",
                {
                    "summary": "计划校验失败：" + str(exc),
                    "rejected_plan": plan.model_dump(exclude_none=True),
                },
                request_id=request["request_id"],
            )
            if attempt:
                raise
            plan = self.supervisor.plan(
                request,
                self.emitter(request["session_id"], turn_id, request["request_id"]),
                [
                    {
                        "plan_validation_error": str(exc),
                        "rejected_proposal": plan.model_dump(exclude_none=True),
                    }
                ],
            )
