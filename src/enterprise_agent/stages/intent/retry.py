"""One repair budget covers both inference and normalization."""

from enterprise_agent.domain.errors import RepairableError


def recognize(call, normalize):
    feedback = None
    for attempt in range(2):
        proposal = None
        try:
            proposal = call(feedback)
            ordered = normalize(proposal)
            return proposal, ordered
        except RepairableError as exc:
            if attempt:
                raise
            feedback = {
                "error": exc.hint,
                "code": exc.code,
                "rejected_intent": proposal.model_dump(exclude_none=True)
                if proposal
                else None,
            }
