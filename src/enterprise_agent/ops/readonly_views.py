"""Expose every imported business table in an existing PostgreSQL analysis schema."""

import os

import psycopg
from psycopg import sql

from enterprise_agent.ops.profile_data import profile


def main():
    dsn = os.environ.get("PG_ADMIN_DSN")
    if not dsn:
        raise SystemExit("PG_ADMIN_DSN is required")
    profiles, _ = profile()
    with psycopg.connect(dsn) as db:
        with db.transaction():
            for table, entry in profiles.items():
                view = table.lower()
                existing = [row[0] for row in db.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='analysis' AND table_name=%s ORDER BY ordinal_position
                """, (view,))]
                columns = existing + [name for name in entry["columns"] if name not in existing]
                if any(name not in entry["columns"] for name in existing):
                    raise RuntimeError(f"Existing view has unexpected columns: {view}")
                db.execute(sql.SQL("CREATE OR REPLACE VIEW {} AS SELECT {} FROM {}").format(
                    sql.Identifier("analysis", view),
                    sql.SQL(",").join(sql.Identifier(name) for name in columns),
                    sql.Identifier("supply_chain", table)))
            db.execute("GRANT USAGE ON SCHEMA analysis TO supply_chain_reader")
            db.execute("GRANT SELECT ON ALL TABLES IN SCHEMA analysis TO supply_chain_reader")
            count = db.execute("SELECT COUNT(*) FROM information_schema.views WHERE table_schema='analysis'").fetchone()[0]
            if count < len(profiles):
                raise RuntimeError("Not all business tables are exposed")
    print(f"analysis schema exposes {len(profiles)} business tables")


if __name__ == "__main__":
    main()
