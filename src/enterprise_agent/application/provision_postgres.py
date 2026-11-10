"""Provision dedicated runtime and read-only roles without replacing business data."""

import os

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ..core.config import setting


def main(include_test_database=False):
    dsn = os.getenv("PG_ADMIN_DSN")
    if not dsn:
        dsn = make_conninfo(
            host="127.0.0.1",
            port=setting("POSTGRES_PORT", "5433"),
            dbname="supply_chain",
            user="supply_admin",
            password=setting("POSTGRES_ADMIN_PASSWORD"),
        )
    runtime_password = setting("POSTGRES_RUNTIME_PASSWORD")
    reader_password = setting("POSTGRES_READONLY_PASSWORD")
    if not runtime_password or not reader_password:
        raise ValueError("请配置 PostgreSQL 应用与只读账号密码")
    with psycopg.connect(dsn, autocommit=True) as db:
        with db.transaction():
            for name, password in (
                ("enterprise_runtime", runtime_password),
                ("supply_chain_reader", reader_password),
            ):
                if not db.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)
                ).fetchone():
                    db.execute(
                        sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name))
                    )
                db.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
                    ).format(sql.Identifier(name), sql.Literal(password))
                )
            database = db.execute("SELECT current_database()").fetchone()[0]
            db.execute(
                sql.SQL(
                    "GRANT CONNECT,TEMPORARY ON DATABASE {} TO enterprise_runtime"
                ).format(sql.Identifier(database))
            )
            for schema in ("agent", "identity", "agent_checkpoints"):
                db.execute(
                    sql.SQL(
                        "CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION enterprise_runtime"
                    ).format(sql.Identifier(schema))
                )
            db.execute(
                "ALTER ROLE supply_chain_reader SET default_transaction_read_only=on"
            )
            db.execute("GRANT USAGE ON SCHEMA analysis TO supply_chain_reader")
            db.execute(
                "GRANT SELECT ON ALL TABLES IN SCHEMA analysis TO supply_chain_reader"
            )
            for role in ("enterprise_runtime", "supply_chain_reader"):
                db.execute(
                    sql.SQL("REVOKE ALL ON SCHEMA supply_chain FROM {}").format(
                        sql.Identifier(role)
                    )
                )
                db.execute(
                    sql.SQL(
                        "REVOKE ALL ON ALL TABLES IN SCHEMA supply_chain FROM {}"
                    ).format(sql.Identifier(role))
                )
        if (
            include_test_database
            and not db.execute(
                "SELECT 1 FROM pg_database WHERE datname='enterprise_agent_test'"
            ).fetchone()
        ):
            db.execute("CREATE DATABASE enterprise_agent_test OWNER enterprise_runtime")
    print("PostgreSQL 应用权限和隔离测试库已就绪")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--with-test-database", action="store_true")
    main(parser.parse_args().with_test_database)
