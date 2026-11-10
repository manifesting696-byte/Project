"""Offline import of an explicitly selected legacy snapshot into PostgreSQL."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from enterprise_agent.server import identity
from enterprise_agent.domain.request import RequestRecord
from enterprise_agent.ledger.migrate.legacy_stores import LegacyStores
from enterprise_agent.ledger.ledger import RECORD_MODELS, TABLES, Stores


def prepare_snapshot(source, target):
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with (
        closing(
            sqlite3.connect((source / "agent.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as old,
        closing(sqlite3.connect(target / "agent.sqlite")) as new,
    ):
        old.backup(new)
    if (source / "content").exists():
        shutil.copytree(source / "content", target / "content", dirs_exist_ok=True)
    return LegacyStores(target)


def import_snapshot(
    source, stores, auth_source=None, archive_source=None, identity_schema="identity"
):
    source = Path(source).resolve()
    source_hash = hashlib.sha256((source / "agent.sqlite").read_bytes()).hexdigest()
    report = {
        "source_hash": source_hash,
        "tables": {},
        "auth": {},
        "archived_records": 0,
    }
    stores.persistence.setup(
        3,
        [
            "CREATE TABLE IF NOT EXISTS import_runs(source_hash TEXT PRIMARY KEY,report JSONB NOT NULL,completed_at TIMESTAMPTZ NOT NULL DEFAULT now())",
            "CREATE TABLE IF NOT EXISTS legacy_application_records(source TEXT NOT NULL,table_name TEXT NOT NULL,record_key TEXT NOT NULL,body JSONB NOT NULL,PRIMARY KEY(source,table_name,record_key))",
        ],
    )
    with stores.db() as db:
        cached = db.execute(
            "SELECT report FROM import_runs WHERE source_hash=%s", (source_hash,)
        ).fetchone()
        if cached:
            return cached[0]
        if any(
            db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in TABLES
        ):
            raise ValueError("目标账本已有数据，请先选择空的迁移目标")
    created = []
    with tempfile.TemporaryDirectory(prefix="postgres-import-") as temporary:
        legacy = prepare_snapshot(source, Path(temporary))
        records = {}
        with legacy.db() as db:
            for table in TABLES:
                records[table] = [
                    json.loads(row[0])
                    for row in db.execute(f"SELECT body FROM {table} ORDER BY rowid")
                ]
                model = RequestRecord if table == "requests" else RECORD_MODELS[table]
                records[table] = [
                    model.model_validate(item).model_dump(exclude_none=True)
                    for item in records[table]
                ]
            sequences = db.execute("SELECT kind,value FROM sequences").fetchall()
            aliases = {}
            for table in ("legacy_tasks", "legacy_task_revisions"):
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    aliases[table] = db.execute(
                        f"SELECT id,body FROM {table}"
                    ).fetchall()
        # Copy files before committing their metadata; remove only newly created files on failure.
        content = stores.directory / "content"
        content.mkdir(exist_ok=True)
        try:
            for artifact in records["artifacts"]:
                origin = Path(temporary) / "content" / artifact["artifact_id"]
                destination = content / artifact["artifact_id"]
                if not origin.is_dir():
                    raise ValueError(f"历史产物文件缺失：{artifact['artifact_id']}")
                destination.mkdir(exist_ok=True)
                for path in origin.iterdir():
                    target = destination / path.name
                    if target.exists():
                        if target.read_bytes() != path.read_bytes():
                            raise ValueError(
                                f"产物文件冲突：{artifact['artifact_id']}/{path.name}"
                            )
                    else:
                        shutil.copy2(path, target)
                        created.append(target)
            with stores.db() as db:
                db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("enterprise-import:" + stores.persistence.schema,),
                )
                if db.execute(
                    "SELECT 1 FROM import_runs WHERE source_hash=%s", (source_hash,)
                ).fetchone():
                    return db.execute(
                        "SELECT report FROM import_runs WHERE source_hash=%s",
                        (source_hash,),
                    ).fetchone()[0]
                if any(
                    db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    for table in TABLES
                ):
                    raise ValueError("迁移目标已被写入，已终止本次导入")
                for table in (
                    "sessions",
                    "request_revisions",
                    "requests",
                    "tasks",
                    "artifacts",
                    "session_summaries",
                    "session_events",
                ):
                    for item in records[table]:
                        stores._put(db, table, item)
                    report["tables"][table] = len(records[table])
                for kind, value in sequences:
                    db.execute(
                        "INSERT INTO sequences VALUES(%s,%s) ON CONFLICT(kind) DO UPDATE SET value=GREATEST(sequences.value,excluded.value)",
                        (kind, value),
                    )
                for table, rows in aliases.items():
                    for key, body in rows:
                        db.execute(
                            f"INSERT INTO {table}(id,body) VALUES(%s,%s)",
                            (key, Jsonb(json.loads(body))),
                        )
                if auth_source:
                    report["auth"] = import_identity(
                        db, Path(auth_source), identity_schema
                    )
                if archive_source:
                    report["archived_records"] = archive_identity(
                        db, Path(archive_source)
                    )
                for table, count in report["tables"].items():
                    if (
                        db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        != count
                    ):
                        raise ValueError("迁移行数核对失败")
                db.execute(
                    "INSERT INTO import_runs(source_hash,report) VALUES(%s,%s)",
                    (source_hash, Jsonb(report)),
                )
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
                if not any(path.parent.iterdir()):
                    path.parent.rmdir()
            raise
    return report


def import_identity(db, source, schema):
    counts = {}
    allowed = {
        "users": ["id", "username", "password_hash", "role", "active", "created_at"],
        "auth_sessions": ["token_hash", "user_id", "expires_at", "created_at"],
        "login_failures": ["username", "attempts", "first_at"],
        "audit": [
            "id",
            "at",
            "user_id",
            "action",
            "status",
            "question_hash",
            "sql_hash",
            "snapshot_id",
            "metric_version",
            "duration_ms",
            "detail",
        ],
        "feedback": ["id", "at", "user_id", "audit_id", "rating", "comment"],
    }
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        old.row_factory = sqlite3.Row
        existing = {
            row[0]
            for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, columns in allowed.items():
            target = sql.Identifier(schema, table)
            if db.execute(
                sql.SQL("SELECT 1 FROM {} LIMIT 1").format(target)
            ).fetchone():
                raise ValueError("目标账号数据库已有数据，请先核对迁移来源")
            rows = (
                [dict(row) for row in old.execute(f'SELECT * FROM "{table}"')]
                if table in existing
                else []
            )
            for row in rows:
                if table == "users":
                    row["active"] = bool(row["active"])
                values = [row.get(name) for name in columns]
                db.execute(
                    sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        target,
                        sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns),
                    ),
                    values,
                )
            counts[table] = len(rows)
            if "id" in columns:
                qualified = f"{schema}.{table}"
                db.execute(
                    sql.SQL(
                        "SELECT setval(pg_get_serial_sequence(%s,'id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM {}"
                    ).format(target),
                    (qualified,),
                )
    return counts


def archive_identity(db, source):
    count = 0
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        old.row_factory = sqlite3.Row
        for table in (
            row[0]
            for row in old.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ):
            for index, row in enumerate(old.execute(f'SELECT * FROM "{table}"')):
                value = dict(row)
                key = str(value.get("id", value.get("token_hash", index)))
                db.execute(
                    "INSERT INTO legacy_application_records VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    ("docker-original", table, key, Jsonb(value)),
                )
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--auth-source", type=Path)
    parser.add_argument("--archive-auth", type=Path)
    args = parser.parse_args()
    identity.setup()
    stores = Stores()
    try:
        report = import_snapshot(
            args.source, stores, args.auth_source, args.archive_auth
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        stores.close()
        identity.close()


if __name__ == "__main__":
    main()
