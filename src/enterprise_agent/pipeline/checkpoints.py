"""Short-lived SQLite checkpoint connections for restartable graph invocations."""

import sqlite3
from contextlib import closing, contextmanager
from contextvars import Context

from langgraph.checkpoint.sqlite import SqliteSaver


class Checkpoints:
    def __init__(self, directory):
        self.path = directory / "langgraph.sqlite"

    @contextmanager
    def graph(self, builder):
        with closing(
            sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        ) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            yield builder.compile(checkpointer=SqliteSaver(connection))

    def invoke(self, builder, thread_id, value, context=None):
        with self.graph(builder) as graph:
            config = {
                "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
                "recursion_limit": 1000,
            }
            # Nested Request graphs have their own checkpoint and interrupt scope.
            return Context().run(graph.invoke, value, config, context=context)

    def snapshot(self, builder, thread_id):
        if not self.path.exists():
            return None
        with self.graph(builder) as graph:
            return graph.get_state(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            )
