"""Transactional PostgreSQL import. Requires PG_DSN and an existing database."""
import csv
import hashlib
import os
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from ops.profile_data import DATASET, RELATIONS, _schema, profile


def main():
    dsn = os.environ.get("PG_DSN")
    if not dsn:
        raise SystemExit("PG_DSN is required")
    profiles, _ = profile()
    with psycopg.connect(dsn) as conn:
        existing = conn.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('staging', 'supply_chain', 'analysis')").fetchall()
        if existing:
            raise RuntimeError("目标库已含分析 Schema；导入仅允许空库首次初始化，后续更新须使用已审核迁移")
        with conn.transaction():
            # A fresh database is required; never replace an existing business schema.
            conn.execute("CREATE SCHEMA staging")
            for table, p in profiles.items():
                cols = p["columns"]
                raw_table = sql.Identifier("staging", table)
                conn.execute(sql.SQL("CREATE TABLE {} ({})").format(raw_table, sql.SQL(",").join(sql.SQL("{} TEXT").format(sql.Identifier(c)) for c in cols)))
                with (DATASET / p["file"]).open(encoding="utf-8-sig", newline="") as source:
                    rows = list(csv.DictReader(source))
                with conn.cursor().copy(sql.SQL("COPY {} ({}) FROM STDIN").format(raw_table, sql.SQL(",").join(map(sql.Identifier, cols)))) as copy:
                    for row in rows:
                        copy.write_row(tuple(row[c] or None for c in cols))
                count = conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(raw_table)).fetchone()[0]
                if count != p["rows"]:
                    raise RuntimeError(f"Staging count mismatch: {table}")
            conn.execute("CREATE SCHEMA supply_chain")
            for statement in _schema(profiles, "postgres").split(";"):
                if statement.strip():
                    conn.execute(statement.replace('CREATE TABLE "', 'CREATE TABLE supply_chain."') + ";")
            for table, p in profiles.items():
                cols = p["columns"]
                expressions = []
                for c in cols:
                    col = sql.Identifier(c)
                    kind = p["types"][c]
                    if kind == "DATE":
                        expressions.append(sql.SQL("TO_DATE(REPLACE({}, '/', '-'), 'YYYY-MM-DD')").format(col))
                    elif kind == "TIMESTAMP":
                        expressions.append(sql.SQL("{}::TIMESTAMP").format(col))
                    elif kind in ("INTEGER", "NUMERIC"):
                        expressions.append(sql.SQL("{}::{}").format(col, sql.SQL(kind)))
                    else:
                        expressions.append(col)
                conn.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(sql.Identifier("supply_chain", table), sql.SQL(",").join(map(sql.Identifier, cols)), sql.SQL(",").join(expressions), sql.Identifier("staging", table)))
                count = conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("supply_chain", table))).fetchone()[0]
                if count != p["rows"]:
                    raise RuntimeError(f"Final count mismatch: {table}")
            for child, key, parent, parent_key in RELATIONS:
                orphan = conn.execute(sql.SQL("SELECT COUNT(*) FROM {} c LEFT JOIN {} p ON c.{}=p.{} WHERE c.{} IS NOT NULL AND p.{} IS NULL").format(sql.Identifier("supply_chain", child), sql.Identifier("supply_chain", parent), sql.Identifier(key), sql.Identifier(parent_key), sql.Identifier(key), sql.Identifier(parent_key))).fetchone()[0]
                if orphan:
                    raise RuntimeError(f"Orphan relation: {child}.{key} -> {parent}.{parent_key}: {orphan}")
                conn.execute(sql.SQL("ALTER TABLE {} ADD FOREIGN KEY ({}) REFERENCES {} ({})").format(sql.Identifier("supply_chain", child), sql.Identifier(key), sql.Identifier("supply_chain", parent), sql.Identifier(parent_key)))
                conn.execute(sql.SQL("CREATE INDEX {} ON {} ({})").format(sql.Identifier(f"ix_{child}_{key}"), sql.Identifier("supply_chain", child), sql.Identifier(key)))
            conn.execute("DROP SCHEMA staging CASCADE")
            conn.execute("CREATE SCHEMA analysis")
            for table, table_profile in profiles.items():
                columns = table_profile["columns"]
                conn.execute(sql.SQL("CREATE VIEW {} AS SELECT {} FROM {}").format(
                    sql.Identifier("analysis", table.lower()),
                    sql.SQL(",").join(sql.Identifier(c) for c in columns),
                    sql.Identifier("supply_chain", table)))
            conn.execute("REVOKE ALL ON SCHEMA supply_chain FROM PUBLIC")
            conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA supply_chain FROM PUBLIC")
            conn.execute("REVOKE ALL ON SCHEMA analysis FROM PUBLIC")
            conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA analysis FROM PUBLIC")
            snapshot_id = hashlib.sha256("".join(p["sha256"] for p in profiles.values()).encode()).hexdigest()[:16]
            conn.execute("CREATE TABLE supply_chain.snapshot_manifest (snapshot_id TEXT PRIMARY KEY, imported_at TIMESTAMPTZ NOT NULL)")
            conn.execute("INSERT INTO supply_chain.snapshot_manifest VALUES (%s,%s)", (snapshot_id, datetime.now(timezone.utc)))
    print(f"{len(profiles)} tables imported transactionally into supply_chain")


if __name__ == "__main__":
    main()
