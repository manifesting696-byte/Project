"""Real PostgreSQL migration, locking, identity and read-only integration checks."""

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from threading import Event, Thread

import psycopg

from enterprise_agent.server import identity
from ops.migrate.from_sqlite import import_snapshot
from enterprise_agent.query.database import ReadonlyDatabase
from .legacy_fixture import legacy_ledger
from .postgres_fixture import TestStores, cleanup, identity_scope


class PostgreSQL(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(cleanup)
        self.path = Path(self.directory.name)

    def test_offline_import_preserves_source_and_is_idempotent(self):
        source = self.path / "archive"
        legacy_ledger(source)
        before = hashlib.sha256((source / "agent.sqlite").read_bytes()).hexdigest()
        target = TestStores(self.path / "target")
        report = import_snapshot(source, target)
        self.assertGreater(report["tables"]["artifacts"], 0)
        with target.db() as db:
            kind = db.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='requests' AND column_name='body'",
                (target.persistence.schema,),
            ).fetchone()[0]
            self.assertEqual(kind, "jsonb")
        target.session(1, title="迁移后的新会话")
        self.assertEqual(import_snapshot(source, target), report)
        self.assertEqual(
            hashlib.sha256((source / "agent.sqlite").read_bytes()).hexdigest(), before
        )
        self.assertEqual(
            len(target.list("sessions", "user_id", 1)), report["tables"]["sessions"] + 1
        )

    def test_invalid_archive_does_not_write_partial_postgres_records(self):
        import json

        source = self.path / "invalid"
        legacy_ledger(source)
        with closing(sqlite3.connect(source / "agent.sqlite")) as db, db:
            revision = json.loads(
                db.execute("SELECT body FROM task_revisions").fetchone()[0]
            )
            revision["requirements"][0].pop("description")
            db.execute("UPDATE task_revisions SET body=?", (json.dumps(revision),))
        target = TestStores(self.path / "target")
        with self.assertRaises(KeyError):
            import_snapshot(source, target)
        with target.db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0], 0
            )

    def test_identity_import_keeps_password_and_login(self):
        source = self.path / "archive"
        legacy_ledger(source)
        auth = self.path / "old-auth.sqlite"
        password = "preserved-account-password"
        encoded = identity._password_hash(password)
        with closing(sqlite3.connect(auth)) as db, db:
            db.execute(
                "CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,password_hash TEXT,role TEXT,active INTEGER,created_at TEXT)"
            )
            db.execute(
                "INSERT INTO users VALUES(1,?,?,?,?,?)",
                ("original-admin", encoded, "admin", 1, "2026-11-01T00:00:00+00:00"),
            )
        with identity_scope(self.path):
            identity.setup()
            target = TestStores(self.path / "target")
            report = import_snapshot(
                source, target, auth_source=auth, identity_schema=identity.AUTH_SCHEMA
            )
            self.assertEqual(report["auth"]["users"], 1)
            logged_in = identity.login("original-admin", password)
            self.assertEqual(logged_in["user"]["role"], "admin")
            with identity.app_db() as db:
                self.assertEqual(
                    db.execute("SELECT password_hash FROM users WHERE id=1").fetchone()[
                        "password_hash"
                    ],
                    encoded,
                )

    def test_session_lock_coordinates_distinct_connections(self):
        first = TestStores(self.path / "shared")
        second = TestStores(self.path / "shared")
        entered, acquired = Event(), Event()

        def run():
            entered.set()
            with second.session_lock("same-session"):
                acquired.set()

        with first.session_lock("same-session"):
            worker = Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(timeout=5))
            self.assertFalse(acquired.wait(timeout=0.2))
        worker.join(timeout=5)
        self.assertTrue(acquired.is_set())

    def test_business_source_enforces_postgres_read_only(self):
        source = ReadonlyDatabase()
        self.addCleanup(source.close)
        with source.connection() as db:
            self.assertEqual(
                db.execute("SHOW transaction_read_only").fetchone()[
                    "transaction_read_only"
                ],
                "on",
            )
        with self.assertRaises(psycopg.Error):
            with source.connection() as db:
                db.execute("CREATE TABLE analysis.forbidden_test_write(id INTEGER)")
        rows, _ = source.execute("SELECT COUNT(*) AS n FROM products")
        self.assertEqual(rows[0]["n"], 16)
