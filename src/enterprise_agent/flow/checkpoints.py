"""LangGraph PostgreSQL checkpoints, isolated from ledger transactions."""

from contextlib import contextmanager
from contextvars import Context

from langgraph.checkpoint.postgres import PostgresSaver

from enterprise_agent.ledger.postgres import Postgres


class Checkpoints:
    backend = "postgres"

    def __init__(self, stores):
        self.persistence = Postgres(
            stores.persistence.dsn,
            stores.persistence.schema + "_checkpoints",
            max_size=4,
            autocommit=True,
        )
        try:
            self.persistence.setup(1, [])
            self.saver = PostgresSaver(self.persistence.pool)
            with self.persistence.advisory_lock(
                "enterprise-checkpoint-setup:" + self.persistence.schema
            ):
                self.saver.setup()
        except BaseException:
            self.persistence.close()
            raise

    @contextmanager
    def graph(self, builder, interrupt_before=None):
        yield builder.compile(
            checkpointer=self.saver, interrupt_before=interrupt_before
        )

    def invoke(self, builder, thread_id, value, context=None, *, interrupt_before=None):
        with self.graph(builder, interrupt_before) as graph:
            config = {
                "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
                "recursion_limit": 1000,
            }
            return Context().run(graph.invoke, value, config, context=context)

    def snapshot(self, builder, thread_id):
        with self.graph(builder) as graph:
            return graph.get_state(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            )

    def close(self):
        self.persistence.close()
