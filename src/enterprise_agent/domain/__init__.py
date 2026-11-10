"""Domain contracts: data shapes and invariants only, no I/O.

Import from the specific module (``domain.request``, ``domain.task``, ...) in new
code; the re-exports below exist so callers can use ``from enterprise_agent.domain
import Request`` when a single import is clearer.
"""

from enterprise_agent.domain.answer import (
    AnswerDraft,
    AnswerDraftBlock,
    Completion,
    OutputCheck,
    Response,
    ResponseBlock,
)
from enterprise_agent.domain.artifact import Artifact
from enterprise_agent.domain.base import AgentName, Category, ContentType, Model
from enterprise_agent.domain.refs import InputBinding, IntentRef, OutputSpec, ResourceRef
from enterprise_agent.domain.request import (
    Request,
    RequestRecord,
    RequestRevision,
    RequestSpec,
)
from enterprise_agent.domain.session import Session, SessionEvent, SessionSummary
from enterprise_agent.domain.task import AgentResult, Plan, PlannedTask, SemanticBinding, Task

__all__ = [
    "AgentName",
    "AgentResult",
    "AnswerDraft",
    "AnswerDraftBlock",
    "Artifact",
    "Category",
    "Completion",
    "ContentType",
    "InputBinding",
    "IntentRef",
    "Model",
    "OutputCheck",
    "OutputSpec",
    "Plan",
    "PlannedTask",
    "Request",
    "RequestRecord",
    "RequestRevision",
    "RequestSpec",
    "ResourceRef",
    "Response",
    "ResponseBlock",
    "SemanticBinding",
    "Session",
    "SessionEvent",
    "SessionSummary",
    "Task",
]
