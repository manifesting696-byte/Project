"""Graph state shapes shared by the three LangGraph graphs.

Kept in one module so a reader can see every field a turn / request / task batch
carries without opening the graph builders.
"""

import operator
from typing import Annotated, TypedDict


class TurnState(TypedDict, total=False):
    session_id: str
    turn_id: str
    query: str
    constraints: dict
    pause: bool
    preview: bool
    forced_operation: dict | None
    intent: dict
    ordered: list[dict]
    intent_event_id: str
    bundles: dict
    proposals: dict
    errors: dict[str, str]
    previous: dict
    prepared: dict
    cursor: int
    answers: dict[str, list[dict]]
    states: dict[str, str]
    touched: list[str]
    error: str | None
    result: dict


class ExecutionContext(TypedDict):
    turn_id: str


class ExecutionState(TypedDict, total=False):
    request_id: str
    revision_id: str
    session_id: str
    run_id: str
    query: str
    output_names: list[str] | None
    pause: bool
    preview: bool
    executions: int
    completion: dict | None
    reply: dict | None
    new_results: list[dict]
    outcome: str
    wait_kind: str
    route: str


class ScheduleState(TypedDict, total=False):
    request_id: str
    session_id: str
    revision_id: str
    turn_id: str
    execution_key: str | None
    pause: bool
    ready: list[str]
    results: Annotated[dict[str, dict], operator.or_]
    decision_required: bool
    paused: bool
    waiting_for_requests: bool
    blocked: str | None
    route: str
    retry_dispatch: bool
