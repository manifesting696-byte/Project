"""Bounded PostgreSQL pools with explicit schema ownership and transaction scope."""

import re
from contextlib import contextmanager

from psycopg import sql
from psycopg.rows import tuple_row
from psycopg_pool import ConnectionPool

from enterprise_agent.settings import database_url, setting


def schema_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("无效 PostgreSQL Schema 名称")
    return value


class Postgres:
    instances = set()

    def __init__(
        self, dsn=None, schema="agent", max_size=None, autocommit=False, read_only=False
    ):
        self.dsn = dsn or database_url()
        self.schema = schema_name(schema)
        self.pool = ConnectionPool(
            conninfo=self.dsn,
            min_size=0,
            max_size=max_size or int(setting("DATABASE_POOL_SIZE", "16")),
            timeout=30,
            max_lifetime=1800,
            max_idle=60,
            open=False,
            kwargs={
                "autocommit": autocommit,
                "connect_timeout": 5,
                "options": f"-c search_path={self.schema},pg_catalog -c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000"
                + (" -c default_transaction_read_only=on" if read_only else ""),
            },
            check=ConnectionPool.check_connection,
        )
        self.pool.open(wait=True)
        self.instances.add(self)

    @contextmanager
    def connection(self):
        with self.pool.connection() as connection:
            connection.row_factory = tuple_row
            try:
                with connection.transaction():
                    yield connection
            finally:
                connection.row_factory = tuple_row

    def setup(self, version, statements):
        with self.connection() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"enterprise-schema:{self.schema}",),
            )
            if not db.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (self.schema,)
            ).fetchone():
                db.execute(
                    sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            if db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=%s", (version,)
            ).fetchone():
                return
            for statement in statements:
                db.execute(statement)
            db.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (version,))

    @contextmanager
    def advisory_lock(self, key):
        with self.pool.connection() as db:
            previous = db.autocommit
            db.autocommit = True
            try:
                db.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
                try:
                    yield
                finally:
                    db.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,)
                    )
            finally:
                db.autocommit = previous

    def close(self):
        self.pool.close()
        self.instances.discard(self)
