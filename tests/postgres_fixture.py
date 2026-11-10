"""Integration-test schemas live in a separate PostgreSQL database."""

import hashlib
from pathlib import Path

import psycopg
from psycopg import sql

from enterprise_agent.settings import setting
from enterprise_agent.ledger.postgres import Postgres
from enterprise_agent.ledger.ledger import Stores

_schemas = set()


def test_dsn():
    value = setting("TEST_DATABASE_URL")
    if not value:
        raise ValueError("运行验收需要独立的 TEST_DATABASE_URL")
    return value


def namespace(directory, suffix=""):
    name = (
        "test_"
        + hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:18]
        + suffix
    )
    _schemas.add((test_dsn(), name))
    _schemas.add((test_dsn(), name + "_checkpoints"))
    return name


class TestStores(Stores):
    def __init__(self, directory):
        super().__init__(directory, dsn=test_dsn(), schema=namespace(directory))


def identity_scope(directory):
    from enterprise_agent.server import auth as identity
    from unittest.mock import patch

    return patch.multiple(
        identity, AUTH_DSN=test_dsn(), AUTH_SCHEMA=namespace(directory, "_auth")
    )


def cleanup():
    from enterprise_agent.server import auth as identity

    schemas = _schemas | {
        (instance.dsn, instance.schema)
        for instance in Postgres.instances
        if instance.schema.startswith("test_")
    }
    identity.close()
    for instance in tuple(Postgres.instances):
        instance.close()
    for dsn, schema in schemas:
        with psycopg.connect(dsn) as db:
            db.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
    _schemas.clear()


class LegacyTestStores:
    """Exercise the offline archive upgrader with PostgreSQL execution infrastructure."""

    def __new__(cls, directory):
        from ops.migrate.legacy_stores import LegacyStores

        instance = LegacyStores(Path(directory))
        instance.persistence = Postgres(test_dsn(), namespace(directory))
        instance.persistence.setup(1, [])
        instance.session_lock = lambda session_id: instance.persistence.advisory_lock(
            "legacy-fixture:" + session_id
        )
        instance.close = instance.persistence.close
        return instance
