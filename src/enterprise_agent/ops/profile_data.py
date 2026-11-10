import csv
import hashlib
import json
import re
from datetime import datetime, timezone

from enterprise_agent.settings import ROOT, WORK

DATASET = ROOT / "data" / "seed"
DATE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")


def _type(values):
    values = [x for x in values if x != ""]
    if values and all(DATE.fullmatch(x) for x in values):
        return "DATE"
    if values and all(DATETIME.fullmatch(x) for x in values):
        return "TIMESTAMP"
    if values and all(INTEGER.fullmatch(x) for x in values):
        return "INTEGER"
    if values and all(DECIMAL.fullmatch(x) for x in values):
        return "NUMERIC"
    return "TEXT"


def _normalize(value, kind):
    if not value:
        return None
    if kind == "DATE":
        return (
            datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date().isoformat()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            else datetime.strptime(value.replace("/", "-"), "%Y-%m-%d")
            .date()
            .isoformat()
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value)
            else datetime.strptime(value, "%Y/%m/%d").date().isoformat()
        )
    return value


def profile():
    profiles = {}
    total = 0
    for file in sorted(DATASET.glob("*.csv"), key=lambda item: item.name.casefold()):
        raw = file.read_bytes()
        with file.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            cols = reader.fieldnames or []
        kinds = {c: _type([r[c] for r in rows]) for c in cols}
        first = cols[0]
        ids = [r[first] for r in rows]
        bad_dates = {
            c: sum(1 for r in rows if r[c] and _date_bad(r[c], kind))
            for c, kind in kinds.items()
            if kind in ("DATE", "TIMESTAMP")
        }
        profiles[file.stem] = {
            "file": file.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(rows),
            "columns": cols,
            "types": kinds,
            "primary_key": first,
            "duplicate_pk": len(ids) - len(set(ids)),
            "nulls": {c: sum(not r[c] for r in rows) for c in cols},
            "date_parse_failures": bad_dates,
        }
        total += len(rows)
    return profiles, total


def _date_bad(value, kind):
    try:
        if kind == "DATE":
            _normalize(value, kind)
        else:
            datetime.fromisoformat(value)
        return False
    except ValueError:
        return True


def _schema(profiles, dialect):
    statements = []
    for table, p in profiles.items():
        cols = []
        for col in p["columns"]:
            kind = p["types"][col]
            if dialect == "postgres" and kind == "NUMERIC":
                kind = "NUMERIC"
            cols.append(
                f'"{col}" {kind}' + (" PRIMARY KEY" if col == p["primary_key"] else "")
            )
        statements.append(f'CREATE TABLE "{table}" (\n  ' + ",\n  ".join(cols) + "\n);")
    return "\n\n".join(statements) + "\n"


RELATIONS = [
    tuple(item["from"].split(".") + item["to"].split("."))
    for item in json.loads(
        (ROOT / "config" / "relations.json").read_text(encoding="utf-8")
    )
]


def import_data():
    profiles, total = profile()
    if not profiles:
        raise RuntimeError("No CSV data sources found")
    if any(
        p["duplicate_pk"] or any(p["date_parse_failures"].values())
        for p in profiles.values()
    ):
        raise RuntimeError("Primary key or date validation failed")
    WORK.mkdir(exist_ok=True)
    (WORK / "source-schema.sql").write_text(
        _schema(profiles, "postgres"), encoding="utf-8"
    )
    report = {
        "imported_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": hashlib.sha256(
            "".join(p["sha256"] for p in profiles.values()).encode()
        ).hexdigest()[:16],
        "source_total_rows": total,
        "tables": profiles,
        "relations": [],
    }
    (WORK / "data_quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (WORK / "manifest.json").write_text(
        json.dumps(
            {
                k: report[k]
                for k in ("imported_at_utc", "snapshot_id", "source_total_rows")
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    print(json.dumps(import_data(), ensure_ascii=False, indent=2))
