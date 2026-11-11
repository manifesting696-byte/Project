"""Checkpointed Request execution, replanning, and interrupt/resume control flow."""

from langgraph.types import Command

from enterprise_agent.domain.answer import Response
from enterprise_agent.domain.errors import UpstreamPending, VersionConflict
from enterprise_agent.flow.request_graph import build


class Execution:
    def __init__(self, application, checkpoints):
        self.app, self.checkpoints = application, checkpoints
        self.builder = build(self)

    @staticmethod
    def thread_id(request):
        return f"request:{request['request_id']}:{request['current_revision_id']}"

    def request(self, state):
        request = self.app.stores.get("requests", state["request_id"])
        if request["current_revision_id"] != state["revision_id"]:
            raise VersionConflict("执行现场对应的需求版本已失效，请使用当前版本")
        return request

    def snapshot(self, request):
        return self.checkpoints.snapshot(self.builder, self.thread_id(request))

    def active(self, request):
        snapshot = self.snapshot(request)
        return bool(snapshot and snapshot.next)

    def start(
        self, request, turn_id, query, pause=False, output_names=None, preview=False
    ):
        initial = self.initial(request, turn_id, query, pause, output_names, preview)
        result = self.checkpoints.invoke(
            self.builder, self.thread_id(request), initial, {"turn_id": turn_id}
        )
        return self.result(request, result)

    @staticmethod
    def initial(request, turn_id, query, pause=False, output_names=None, preview=False):
        return {
            "request_id": request["request_id"],
            "revision_id": request["current_revision_id"],
            "session_id": request["session_id"],
            "run_id": turn_id,
            "query": query,
            "pause": pause,
            "preview": preview,
            "output_names": output_names,
            "executions": 0,
            "completion": None,
            "reply": None,
            "new_results": [],
            "outcome": "pending",
            "wait_kind": "",
        }

    def advance(
        self,
        request,
        turn_id,
        query,
        pause=False,
        output_names=None,
        preview=False,
        schedule_result=None,
    ):
        """Advance control nodes to the shared Task boundary or a user-facing wait.

        Static execute boundaries persist in the same historical request thread.
        A normal single-request resume can also continue these checkpoints.
        """
        snapshot = self.snapshot(request)
        current = snapshot and snapshot.values.get("run_id") == turn_id
        if current:
            if not snapshot.next or any(task.interrupts for task in snapshot.tasks):
                return snapshot
            if snapshot.next == ("execute",) and schedule_result is None:
                return snapshot
            initial = None
        else:
            initial = self.initial(
                request, turn_id, query, pause, output_names, preview
            )
        context = {"turn_id": turn_id, "unified": True}
        if schedule_result is not None:
            context["schedule_result"] = schedule_result
        self.checkpoints.invoke(
            self.builder,
            self.thread_id(request),
            initial,
            context,
            interrupt_before=["execute"],
        )
        return self.snapshot(request)

    def resume(self, request, turn_id):
        snapshot = self.snapshot(request)
        if not snapshot or not snapshot.next:
            return self.start(request, turn_id, request["spec"]["description"])
        waiting = any(task.interrupts for task in snapshot.tasks)
        value = Command(resume=True) if waiting else None
        result = self.checkpoints.invoke(
            self.builder, self.thread_id(request), value, {"turn_id": turn_id}
        )
        return self.result(request, result)

    def result(self, request, state):
        request.update(self.app.stores.get("requests", request["request_id"]))
        return Response.model_validate(state["reply"]), state["outcome"]

    def key(self, state, phase):
        scope = f"request:{state['request_id']}:{state['revision_id']}"
        return f"{scope}:{state['run_id']}:{state['executions']}:{phase}"

    def cached(self, state, phase):
        key = self.key(state, phase)
        return self.app.stores.workflow_event(state["session_id"], key)

    def upstream_ready(self, request):
        try:
            for binding in request["spec"]["inputs"]:
                if binding["source"]["kind"] == "request":
                    self.app.requests.resolve(request["session_id"], binding["source"])
        except UpstreamPending:
            return False
        return True
