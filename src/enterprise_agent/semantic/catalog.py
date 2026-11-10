"""Durable table enrollment, isolated from source tables and business results."""

import json
import sqlite3
from contextlib import closing


class TableCatalog:
    def __init__(self, directory, source_id):
        self.path = directory / "catalog.sqlite"
        self.source_id = source_id
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS table_catalog (source_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)"
            )

    def read(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            row = db.execute(
                "SELECT revision, body FROM table_catalog WHERE source_id=?",
                (self.source_id,),
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else (0, {})

    def save(self, revision, entries):
        body = json.dumps(entries, ensure_ascii=False, sort_keys=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM table_catalog WHERE source_id=?",
                (self.source_id,),
            ).fetchone()
            if (row[0] if row else 0) != revision:
                raise ValueError("问析目录已被更新，请刷新后重试")
            db.execute(
                "INSERT INTO table_catalog VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET revision=excluded.revision, body=excluded.body",
                (self.source_id, revision + 1, body),
            )
        return revision + 1
