"""Typed failures shared by stages, persistence and HTTP error mapping."""


class AgentError(Exception):
    """Expected business failure."""


class RepairableError(AgentError, ValueError):
    """Invalid model proposal; the owning stage may retry once with this hint."""

    def __init__(self, hint: str, *, code: str):
        self.hint, self.code = hint, code
        super().__init__(hint)


class PermissionDenied(AgentError, PermissionError):
    """Authorization failure; never returned to the model for repair."""


class VersionConflict(AgentError):
    """The persisted revision or plan changed while work was in flight."""


class UpstreamPending(AgentError):
    """Declared upstream work has no usable delivery yet; wait, do not retry."""


def public_message(error):
    if isinstance(error, PermissionError):
        return "无权访问所请求的资源"
    return str(error)[:600]
