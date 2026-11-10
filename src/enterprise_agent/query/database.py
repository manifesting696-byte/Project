"""Read-only source execution and metadata inspection share one database provider."""

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..profile.data import DB


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class ReadonlyDatabase:
    def __init__(self, sqlite_path=None):
        self.dsn = os.getenv("DATABASE_URL")
        self.path = Path(sqlite_path or DB).resolve()
        self.backend = "postgres" if self.dsn else "sqlite"
        if self.dsn:
            from psycopg.conninfo import conninfo_to_dict

            connection = conninfo_to_dict(self.dsn)
            identity = {
                key: connection.get(key) for key in ("host", "port", "dbname", "user")
            }
        else:
            identity = {"path": str(self.path)}
        self.source_id = hashlib.sha256(
            json.dumps({"backend": self.backend, **identity}, sort_keys=True).encode()
        ).hexdigest()[:20]

    @contextmanager
    def connection(self):
        if self.dsn:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(
                self.dsn,
                row_factory=dict_row,
                options="-c default_transaction_read_only=on -c search_path=analysis",
            ) as db:
                db.execute(
                    "SELECT set_config('statement_timeout', %s, true)", ("15000",)
                )
                yield db
        else:
            with closing(
                sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            ) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 15
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                yield db

    def available(self):
        with self.connection() as db:
            if self.dsn:
                rows = db.execute("""
                    SELECT table_name AS name, table_type AS kind
                    FROM information_schema.tables
                    WHERE table_schema='analysis'
                      AND has_table_privilege(format('%I.%I', table_schema, table_name), 'SELECT')
                    ORDER BY table_name
                """).fetchall()
            else:
                rows = db.execute("""
                    SELECT name, type AS kind FROM sqlite_master
                    WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name
                """).fetchall()
        return [dict(row) for row in rows]

    def inspect(self, name):
        available = {item["name"] for item in self.available()}
        if name not in available:
            raise ValueError(f"数据库中没有可接入的表或视图：{name}")
        target = quote_identifier(name)
        with self.connection() as db:
            if self.dsn:
                definitions = db.execute(
                    """
                    SELECT column_name AS name, data_type AS type, is_nullable='YES' AS nullable
                    FROM information_schema.columns WHERE table_schema='analysis' AND table_name=%s
                    ORDER BY ordinal_position
                """,
                    (name,),
                ).fetchall()
                primary = db.execute(
                    """
                    SELECT key.column_name FROM information_schema.table_constraints AS constraint_info
                    JOIN information_schema.key_column_usage AS key
                      USING (constraint_catalog, constraint_schema, constraint_name)
                    WHERE constraint_info.table_schema='analysis'
                      AND constraint_info.table_name=%s AND constraint_type='PRIMARY KEY'
                    ORDER BY key.ordinal_position
                """,
                    (name,),
                ).fetchall()
                columns = {
                    item["name"]: {"type": item["type"], "nullable": item["nullable"]}
                    for item in definitions
                }
                primary_keys = [item["column_name"] for item in primary]
                relations = db.execute(
                    """
                    SELECT child.column_name AS child_column, parent.table_name AS parent_table,
                           parent.column_name AS parent_column
                    FROM information_schema.referential_constraints AS relation
                    JOIN information_schema.key_column_usage AS child
                      ON child.constraint_catalog=relation.constraint_catalog
                     AND child.constraint_schema=relation.constraint_schema
                     AND child.constraint_name=relation.constraint_name
                    JOIN information_schema.key_column_usage AS parent
                      ON parent.constraint_catalog=relation.unique_constraint_catalog
                     AND parent.constraint_schema=relation.unique_constraint_schema
                     AND parent.constraint_name=relation.unique_constraint_name
                     AND parent.ordinal_position=child.position_in_unique_constraint
                    WHERE child.table_schema='analysis' AND child.table_name=%s
                      AND parent.table_schema='analysis'
                """,
                    (name,),
                ).fetchall()
                foreign_keys = [
                    {
                        "from": f"{name}.{item['child_column']}",
                        "to": f"{item['parent_table']}.{item['parent_column']}",
                    }
                    for item in relations
                ]
                target = '"analysis".' + target
            else:
                definitions = db.execute(f"PRAGMA table_info({target})").fetchall()
                columns = {
                    item["name"]: {
                        "type": item["type"],
                        "nullable": not bool(item["notnull"]),
                    }
                    for item in definitions
                }
                primary_keys = [
                    item["name"]
                    for item in sorted(definitions, key=lambda item: item["pk"])
                    if item["pk"]
                ]
                foreign_keys = [
                    {
                        "from": f"{name}.{item['from']}",
                        "to": f"{item['table']}.{item['to']}",
                    }
                    for item in db.execute(f"PRAGMA foreign_key_list({target})")
                    if item["to"]
                ]
            row_count = db.execute(f"SELECT COUNT(*) AS n FROM {target}").fetchone()[
                "n"
            ]
        return {
            "rows": row_count,
            "columns": columns,
            "primary_key": primary_keys[0] if len(primary_keys) == 1 else None,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
        }

    def execute(self, statement, limit=100000):
        with self.connection() as db:
            if self.dsn:
                plan = db.execute("EXPLAIN (FORMAT JSON) " + statement).fetchone()[
                    "QUERY PLAN"
                ][0]["Plan"]
                pending = [plan]
                while pending:
                    node = pending.pop()
                    if (
                        node.get("Total Cost", 0) > 100000
                        or node.get("Plan Rows", 0) > 1000000
                    ):
                        raise ValueError("查询超过资源预算，请缩小范围或聚合")
                    pending.extend(node.get("Plans", []))
            cursor = db.execute(statement)
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchmany(limit + 1)
        if len(rows) > limit:
            raise ValueError("结果超过行数预算，请改用聚合查询")
        return [
            {
                key: float(value)
                if isinstance(value, Decimal)
                else value.isoformat()
                if isinstance(value, date)
                else value
                for key, value in dict(row).items()
            }
            for row in rows
        ], columns
