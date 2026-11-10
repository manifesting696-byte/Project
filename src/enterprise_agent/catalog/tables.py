"""Versioned PostgreSQL enrollment, isolated by source identity."""

from psycopg.types.json import Jsonb


class TableCatalog:
    def __init__(self, persistence, source_id):
        self.persistence = persistence
        self.source_id = source_id
        self.persistence.setup(
            2,
            [
                "CREATE TABLE IF NOT EXISTS table_catalog(source_id TEXT PRIMARY KEY,revision BIGINT NOT NULL,body JSONB NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ],
        )

    def read(self):
        with self.persistence.connection() as db:
            row = db.execute(
                "SELECT revision,body FROM table_catalog WHERE source_id=%s",
                (self.source_id,),
            ).fetchone()
        return (row[0], row[1]) if row else (0, {})

    def save(self, revision, entries):
        with self.persistence.connection() as db:
            row = db.execute(
                "INSERT INTO table_catalog(source_id,revision,body) VALUES(%s,%s,%s) ON CONFLICT(source_id) DO UPDATE SET revision=table_catalog.revision+1,body=excluded.body,updated_at=now() WHERE table_catalog.revision=%s RETURNING revision",
                (self.source_id, revision + 1, Jsonb(entries), revision),
            ).fetchone()
            if not row:
                raise ValueError("问析目录已被更新，请刷新后重试")
            return row[0]
