"""Provision a restricted PostgreSQL login after importing the snapshot."""

import os

import psycopg
from psycopg import sql


def main():
    dsn = os.environ.get("PG_ADMIN_DSN")
    password = os.environ.get("PG_READONLY_PASSWORD")
    if not dsn or not password:
        raise SystemExit("PG_ADMIN_DSN and PG_READONLY_PASSWORD are required")
    role = sql.Identifier("supply_chain_reader")
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname='supply_chain_reader'"
            ).fetchone()
            if not exists:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        role, sql.Literal(password)
                    )
                )
            else:
                conn.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        role, sql.Literal(password)
                    )
                )
            conn.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                    role
                )
            )
            conn.execute(
                sql.SQL("REVOKE ALL ON SCHEMA supply_chain FROM {}").format(role)
            )
            conn.execute(
                sql.SQL(
                    "REVOKE ALL ON ALL TABLES IN SCHEMA supply_chain FROM {}"
                ).format(role)
            )
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA analysis TO {}").format(role))
            conn.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA analysis TO {}").format(
                    role
                )
            )
    print("supply_chain_reader provisioned")


if __name__ == "__main__":
    main()
