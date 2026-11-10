"""Retry a repairable verdict once; evidence checks remain authoritative."""

from enterprise_agent.domain.errors import RepairableError


def verify(call, validate):
    feedback = None
    for attempt in range(2):
        proposal = None
        try:
            proposal = call(feedback)
            validate(proposal)
            return proposal
        except RepairableError as exc:
            if attempt:
                raise
            feedback = {
                "error": exc.hint,
                "code": exc.code,
                "rejected_completion": proposal.model_dump(exclude_none=True)
                if proposal
                else None,
            }
