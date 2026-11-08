import csv
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import ROOT, WORK

DATASET = ROOT / "dataset"
DB = WORK / "supply_chain.sqlite"
DATE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")


def connection():
    conn = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


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
        return datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date().isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date().isoformat() if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value) else datetime.strptime(value, "%Y/%m/%d").date().isoformat()
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
        bad_dates = {c: sum(1 for r in rows if r[c] and _date_bad(r[c], kind)) for c, kind in kinds.items() if kind in ("DATE", "TIMESTAMP")}
        profiles[file.stem] = {"file": file.name, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows), "columns": cols, "types": kinds, "primary_key": first, "duplicate_pk": len(ids) - len(set(ids)), "nulls": {c: sum(not r[c] for r in rows) for c in cols}, "date_parse_failures": bad_dates}
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
            if dialect == "sqlite" and kind in ("DATE", "TIMESTAMP"):
                kind = "TEXT"
            if dialect == "postgres" and kind == "NUMERIC":
                kind = "NUMERIC"
            cols.append(f'"{col}" {kind}' + (" PRIMARY KEY" if col == p["primary_key"] else ""))
        statements.append(f'CREATE TABLE "{table}" (\n  ' + ",\n  ".join(cols) + "\n);")
    return "\n\n".join(statements) + "\n"


RELATIONS = [tuple(item['from'].split('.') + item['to'].split('.'))
             for item in json.loads((ROOT / 'config' / 'relations.json').read_text(encoding='utf-8'))]


def import_data():
    profiles, total = profile()
    if not profiles:
        raise RuntimeError("No CSV data sources found")
    if any(p["duplicate_pk"] or any(p["date_parse_failures"].values()) for p in profiles.values()):
        raise RuntimeError("Primary key or date validation failed")
    WORK.mkdir(exist_ok=True)
    (ROOT / "schema.sql").write_text(_schema(profiles, "postgres"), encoding="utf-8")
    temp = WORK / "importing.sqlite"
    if temp.exists():
        temp.unlink()
    conn = sqlite3.connect(temp)
    try:
        conn.executescript(_schema(profiles, "sqlite"))
        for table, p in profiles.items():
            with (DATASET / p["file"]).open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            columns = p["columns"]
            placeholders = ",".join("?" for _ in columns)
            conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', [tuple(_normalize(row[c], p["types"][c]) for c in columns) for row in rows])
        joins = []
        for child, key, parent, parent_key in RELATIONS:
            orphan = conn.execute(f'SELECT COUNT(*) FROM "{child}" c LEFT JOIN "{parent}" p ON c."{key}"=p."{parent_key}" WHERE c."{key}" IS NOT NULL AND p."{parent_key}" IS NULL').fetchone()[0]
            joins.append({"child": child, "parent": parent, "key": key, "orphan_rows": orphan})
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in profiles}
        if counts != {t: p["rows"] for t, p in profiles.items()}:
            raise RuntimeError("Import counts do not match source")
        conn.commit()
    finally:
        conn.close()
    os.replace(temp, DB)
    report = {"imported_at_utc": datetime.now(timezone.utc).isoformat(), "snapshot_id": hashlib.sha256("".join(p["sha256"] for p in profiles.values()).encode()).hexdigest()[:16], "source_total_rows": total,  "tables": profiles, "relations": joins}
    (WORK / "data_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (WORK / "manifest.json").write_text(json.dumps({k: report[k] for k in ("imported_at_utc", "snapshot_id", "source_total_rows")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(import_data(), ensure_ascii=False, indent=2))
