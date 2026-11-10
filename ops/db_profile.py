"""Profile the actual read-only database, not only its CSV source files."""

import json
import re
from datetime import datetime, timezone

from enterprise_agent.settings import database_url
from ops.profile_data import RELATIONS, WORK

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
SENSITIVE = {"contact_person", "phone", "email", "address"}


def _ident(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid database identifier")
    return '"' + value + '"'


def _profile_postgres() -> dict:
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        database_url("DATABASE_URL"), options="-c default_transaction_read_only=on"
    ) as db:
        definitions = db.execute("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns WHERE table_schema='analysis'
            ORDER BY table_name, ordinal_position
        """).fetchall()
        tables: dict[str, dict] = {}
        for table, name, kind, nullable in definitions:
            tables.setdefault(table, {"columns": {}, "database_constraints": []})[
                "columns"
            ][name] = {
                "type": kind,
                "nullable": nullable == "YES",
                "primary_key": False,
            }
        for table, entry in tables.items():
            target = sql.Identifier("analysis", table)
            entry["rows"] = db.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(target)
            ).fetchone()[0]
            for name, column in entry["columns"].items():
                kind = column["type"]
                field = sql.Identifier(name)
                missing, distinct = db.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FILTER (WHERE {} IS NULL), COUNT(DISTINCT {}) FROM {}"
                    ).format(field, field, target)
                ).fetchone()
                column.update({"null_count": missing, "distinct_count": distinct})
                if "date" in name.lower() or kind in (
                    "date",
                    "timestamp without time zone",
                    "timestamp with time zone",
                ):
                    lo, hi = db.execute(
                        sql.SQL("SELECT MIN({}), MAX({}) FROM {}").format(
                            field, field, target
                        )
                    ).fetchone()
                    column["range"] = [
                        lo.isoformat() if lo else None,
                        hi.isoformat() if hi else None,
                    ]
                elif (
                    kind in ("text", "character varying")
                    and 0 < distinct <= 20
                    and name.lower() not in SENSITIVE
                ):
                    column["common_values"] = [
                        dict(value=value, rows=rows)
                        for value, rows in db.execute(
                            sql.SQL(
                                "SELECT {}, COUNT(*) FROM {} GROUP BY {} ORDER BY COUNT(*) DESC LIMIT 5"
                            ).format(field, target, field)
                        )
                    ]
        relations = []
        for child, child_key, parent, parent_key in RELATIONS:
            if child.lower() not in tables or parent.lower() not in tables:
                continue
            child_table, parent_table = (
                sql.Identifier("analysis", child.lower()),
                sql.Identifier("analysis", parent.lower()),
            )
            ck, pk = sql.Identifier(child_key), sql.Identifier(parent_key)
            matched, total, max_fanout = db.execute(
                sql.SQL("""
                SELECT COUNT(*), (SELECT COUNT(*) FROM {} WHERE {} IS NOT NULL),
                       (SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM {} GROUP BY {}) grouped)
                FROM {} AS child JOIN {} AS parent ON child.{}=parent.{}
            """).format(
                    child_table, ck, child_table, ck, child_table, parent_table, ck, pk
                )
            ).fetchone()
            relations.append(
                {
                    "child": f"{child}.{child_key}",
                    "parent": f"{parent}.{parent_key}",
                    "matched_child_rows": matched,
                    "non_null_child_rows": total,
                    "coverage": matched / total if total else None,
                    "max_child_rows_per_key": max_fanout,
                    "status": "inferred_from_data",
                }
            )
    return {"backend": "postgresql", "tables": tables, "relations": relations}


def profile_database() -> dict:
    report = _profile_postgres()
    manifest = json.loads((WORK / "manifest.json").read_text(encoding="utf-8"))
    report.update(
        {
            "snapshot_id": manifest["snapshot_id"],
            "profiled_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return report


def main():
    report = profile_database()
    output = WORK / "db_profile.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "snapshot_id": report["snapshot_id"],
                "tables": len(report["tables"]),
                "relations": len(report["relations"]),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
