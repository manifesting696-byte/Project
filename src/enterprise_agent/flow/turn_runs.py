"""LangGraph routing for intent, preparation, Request processing, and publication."""

from enterprise_agent.flow.turn_graph import build


class Turns:
    def __init__(self, application, checkpoints):
        self.app, self.checkpoints = application, checkpoints
        self.builder = build(self)

    @staticmethod
    def thread_id(session_id, turn_id):
        return f"turn:{session_id}:{turn_id}"

    def run(
        self,
        session_id,
        turn_id,
        query,
        constraints,
        pause,
        preview,
        forced_operation=None,
    ):
        initial = {
            "session_id": session_id,
            "turn_id": turn_id,
            "query": query,
            "constraints": constraints,
            "pause": pause,
            "preview": preview,
            "forced_operation": forced_operation,
            "cursor": 0,
            "answers": {},
            "states": {},
            "touched": [],
            "error": None,
        }
        return self.checkpoints.invoke(
            self.builder, self.thread_id(session_id, turn_id), initial
        )["result"]

    def resume(self, session_id, turn_id):
        snapshot = self.checkpoints.snapshot(
            self.builder, self.thread_id(session_id, turn_id)
        )
        if not snapshot or not snapshot.values:
            raise ValueError("该轮没有可恢复的执行现场")
        if not snapshot.next:
            return snapshot.values["result"]
        return self.checkpoints.invoke(
            self.builder, self.thread_id(session_id, turn_id), None
        )["result"]
