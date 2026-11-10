"""Initialize an empty deployment or attach to its existing business snapshot."""

import os

import psycopg
from psycopg.conninfo import make_conninfo

from ..core.config import setting
from ..profile import postgres_import, upgrade_analysis_views
from . import bootstrap, provision_postgres


def main():
    dsn = os.getenv("PG_ADMIN_DSN") or make_conninfo(
        host="127.0.0.1",
        port=setting("POSTGRES_PORT", "5433"),
        dbname="supply_chain",
        user="supply_admin",
        password=setting("POSTGRES_ADMIN_PASSWORD"),
    )
    with psycopg.connect(dsn) as db:
        existing = db.execute(
            "SELECT to_regclass('supply_chain.snapshot_manifest')"
        ).fetchone()[0]
    os.environ["PG_ADMIN_DSN"] = dsn
    os.environ["PG_DSN"] = dsn
    if not existing:
        postgres_import.main()
    provision_postgres.main()
    upgrade_analysis_views.main()
    bootstrap.main()


if __name__ == "__main__":
    main()
