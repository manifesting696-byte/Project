"""Failure taxonomy: the retry policy follows the error type, not the try nesting.

The graph currently inspects exception *classes* in a few places (``except
ValueError``); the taxonomy below states which class of failure each case is so
new code can raise the precise type instead of a bare ``ValueError``. Existing
call sites are unchanged in this commit.
"""


class AgentError(Exception):
    """Base class for expected business failures. Unhandled ones fail the turn."""


class RepairableError(AgentError, ValueError):
    """The model can fix the output: feed the message back and retry once.

    Inherits ``ValueError`` so existing ``except ValueError`` handlers keep
    catching the validation failures they already handle.
    """


class PermissionDenied(AgentError, PermissionError):
    """Security boundary: never retried, never explained further to the model."""


class VersionConflict(AgentError):
    """The request revision moved on; the saved graph scene is stale."""


class UpstreamPending(AgentError):
    """A dependency has not delivered yet; wait instead of failing."""
