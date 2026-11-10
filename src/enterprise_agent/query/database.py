"""PostgreSQL read-only source execution and live metadata inspection."""

import hashlib
import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from ..core.config import database_url
from ..persistence.postgres import Postgres, schema_name


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class ReadonlyDatabase:
    backend = "postgres"

    def __init__(self, dsn=None, schema="analysis", source_id=None):
        self.dsn = dsn or database_url("DATABASE_URL")
        self.schema = schema_name(schema)
        connection = conninfo_to_dict(self.dsn)
        identity = {
            key: connection.get(key) for key in ("host", "port", "dbname", "user")
        }
        self.source_id = (
            source_id
            or hashlib.sha256(
                json.dumps({"schema": schema, **identity}, sort_keys=True).encode()
            ).hexdigest()[:20]
        )
        self.persistence = Postgres(self.dsn, self.schema, max_size=8, read_only=True)

    @contextmanager
    def connection(self):
        with self.persistence.connection() as db:
            db.row_factory = dict_row
            db.execute("SET LOCAL statement_timeout='15s'")
            yield db

    def available(self):
        with self.connection() as db:
            rows = db.execute(
                "SELECT table_name AS name, CASE WHEN table_type='VIEW' THEN 'view' ELSE 'table' END AS kind FROM information_schema.tables WHERE table_schema=%s AND has_table_privilege(format('%%I.%%I', table_schema, table_name),'SELECT') ORDER BY table_name",
                (self.schema,),
            ).fetchall()
        return [dict(row) for row in rows]

    def inspect(self, name):
        target = quote_identifier(name)
        with self.connection() as db:
            definitions = db.execute(
                """
                SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,
                       NOT a.attnotnull AS nullable,c.relkind AS relation_kind
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
                WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped
                  AND has_table_privilege(c.oid,'SELECT') AND has_schema_privilege(n.oid,'USAGE')
                ORDER BY a.attnum
            """,
                (self.schema, name),
            ).fetchall()
            if not definitions:
                raise ValueError(f"数据库中没有可接入的表或视图：{name}")
            columns = {
                item["name"]: {"type": item["type"], "nullable": item["nullable"]}
                for item in definitions
            }
            primary, relations = [], []
            if definitions[0]["relation_kind"] in ("r", "p"):
                primary = db.execute(
                    """
                    SELECT key.column_name FROM information_schema.table_constraints AS constraint_info
                    JOIN information_schema.key_column_usage AS key
                      USING (constraint_catalog, constraint_schema, constraint_name)
                    WHERE constraint_info.table_schema=%s
                      AND constraint_info.table_name=%s AND constraint_type='PRIMARY KEY'
                    ORDER BY key.ordinal_position
                """,
                    (self.schema, name),
                ).fetchall()
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
                    WHERE child.table_schema=%s AND child.table_name=%s
                      AND parent.table_schema=%s
                """,
                    (self.schema, name, self.schema),
                ).fetchall()
            primary_keys = [item["column_name"] for item in primary]
            foreign_keys = [
                {
                    "from": f"{name}.{item['child_column']}",
                    "to": f"{item['parent_table']}.{item['parent_column']}",
                }
                for item in relations
            ]
            target = quote_identifier(self.schema) + "." + target
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

    def close(self):
        self.persistence.close()
