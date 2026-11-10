"""Stage-owned implementation behind the application facade."""

from enterprise_agent.domain.errors import RepairableError


def materialize_answer(
    self,
    request,
    turn_id,
    answer,
    extra_refs=(),
    output_names=None,
    bind=False,
    workflow_key=None,
    answer_context=None,
):
    for attempt in range(2):
        try:
            return self.responses.materialize(
                request,
                turn_id,
                answer,
                extra_refs,
                output_names,
                bind,
                workflow_key,
            )
        except RepairableError as exc:
            if attempt:
                raise
            context = (
                dict(answer_context)
                if answer_context is not None
                else self.context.answer(
                    request,
                    request["spec"]["description"],
                    output_names=output_names,
                )
            )
            context["validation_feedback"] = {
                "error": str(exc),
                "rejected_answer": answer.model_dump(exclude_none=True),
            }
            answer = self.supervisor.answer(
                request["session_id"],
                context,
                self.emitter(request["session_id"], turn_id, request["request_id"]),
            )
