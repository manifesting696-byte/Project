"""【离线】核对参考答答案（不调用 Agent）：CSV↔DB、结构事实、覆盖率、行归属、字典。

Three independent checks:

1. CSV <-> database fidelity: every one of the 16 tables is compared cell by cell
   against data/seed/*.csv (this is what makes the imported snapshot trustworthy).
2. Structural checks for the knowledge cases that assert field grain / enum facts.
3. Reference-answer coverage: which expected outputs are additionally re-derived
   from SQL by tests/preflight/verify_reference.py, and which expected table rows
   can be matched directly against a physical table.

Run:  python tests/preflight/verify_dataset.py
"""

import csv
import io
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from enterprise_agent.settings import database_url  # noqa: E402

SEED = ROOT / "data" / "seed"
DATASET = Path(__file__).with_name("dataset.json")
REFERENCE = Path(__file__).with_name("verify_reference.py")
CORPUS = Path(__file__).with_name("cases.json")
OUT = Path(__file__).with_name("reference-verification.json")


def cell_text(value):
    """Canonical text for a CSV/DB cell so both sides compare equal."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return "0" if text in ("0", "-0") else text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip()


def csv_rows(path):
    raw = path.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw, newline=""))
    return list(reader), reader.fieldnames or []


def numeric_equal(left, right):
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return cell_text(left) == cell_text(right)


def fidelity(database):
    report = {}
    for path in sorted(SEED.glob("*.csv")):
        table = path.stem
        rows, columns = csv_rows(path)
        actual = database.execute(
            psycopg.sql.SQL("SELECT * FROM {}").format(
                psycopg.sql.Identifier(table.lower())
            )
        ).fetchall()
        actual_columns = list(actual[0].keys()) if actual else []
        problems = []
        if [c.lower() for c in columns] != [c.lower() for c in actual_columns]:
            problems.append(f"columns {columns} != {actual_columns}")
        if len(rows) != len(actual):
            problems.append(f"row count {len(rows)} != {len(actual)}")
        # 堆表顺序不保证与插入顺序一致，按主键对齐比较
        primary = columns[0]
        actual_by_key = {
            cell_text(item.get(primary.lower(), item.get(primary))): item for item in actual
        }
        if len(actual_by_key) != len(actual):
            problems.append("数据库主键不唯一")
        mismatched_cells = 0
        samples = []
        for csv_row in rows:
            key = cell_text(csv_row[primary])
            db_row = actual_by_key.get(key)
            if db_row is None:
                mismatched_cells += len(columns)
                if len(samples) < 3:
                    samples.append({"primary_key": key, "missing_in_db": True})
                continue
            for column in columns:
                left = csv_row[column]
                right = db_row.get(column.lower(), db_row.get(column))
                if isinstance(right, (Decimal, int, float)) and not isinstance(right, bool):
                    ok = numeric_equal(left, right)
                else:
                    ok = cell_text(left) == cell_text(right)
                if not ok:
                    mismatched_cells += 1
                    if len(samples) < 3:
                        samples.append(
                            {"primary_key": key, "column": column, "csv": left, "db": cell_text(right)}
                        )
        if mismatched_cells:
            problems.append(f"{mismatched_cells} cells differ")
        report[table] = {
            "rows": len(rows),
            "columns": len(columns),
            "mismatched_cells": mismatched_cells,
            "samples": samples,
            "ok": not problems,
            "problems": problems,
        }
    return report


def structural_checks(database):
    checks = []

    def one(label, statement, wanted):
        got = database.execute(statement).fetchone()["value"]
        checks.append(
            {
                "check": label,
                "sql": " ".join(statement.split()),
                "expected": wanted,
                "actual": int(got) if got is not None else None,
                "ok": (int(got) if got is not None else None) == wanted,
            }
        )

    one("Materials 类别数（8 种规范取值）",
        "SELECT COUNT(DISTINCT material_category) AS value FROM materials", 8)
    one("Materials 每类记录数一致（80/8=10）",
        "SELECT MIN(c) AS value FROM (SELECT COUNT(*) c FROM materials GROUP BY material_category) t", 10)
    one("Suppliers 国家取值数（country 无区分度）",
        "SELECT COUNT(DISTINCT country) AS value FROM suppliers", 1)
    one("Suppliers 城市取值数（city 可分组）",
        "SELECT COUNT(DISTINCT city) AS value FROM suppliers", 14)
    one("Suppliers 地址取值数（8 个地址各被 2 家共用）",
        "SELECT COUNT(DISTINCT address) AS value FROM suppliers", 17)
    one("Suppliers 粒度顺序 country < city < address < 行数",
        "SELECT CASE WHEN COUNT(DISTINCT country) < COUNT(DISTINCT city) "
        "AND COUNT(DISTINCT city) < COUNT(DISTINCT address) "
        "AND COUNT(DISTINCT address) <= COUNT(*) THEN 1 ELSE 0 END AS value FROM suppliers",
        1)
    one("Suppliers 供应商数",
        "SELECT COUNT(*) AS value FROM suppliers", 25)
    one("日期列全部为 ISO（无斜杠残留）",
        "SELECT COUNT(*) AS value FROM production WHERE start_date::text LIKE '%/%'", 0)
    return checks


def reference_coverage(cases, dataset, dictionary_ok=False):
    """Which expected outputs have an independent SQL check in verify_reference.py."""
    source = REFERENCE.read_text(encoding="utf-8")
    covered = set()
    for case_id, turn, index in re.findall(
        r'\(\s*"([A-Z]\d{2})",\s*(\d+),\s*(\d+),', source
    ):
        covered.add((case_id, int(turn), int(index)))
    outputs = []
    for case in cases:
        for turn_index, turn in enumerate(case["turns"], 1):
            for output_index, output in enumerate(turn["expected"].get("outputs", [])):
                key = (case["case_id"], turn_index, output_index)
                outputs.append(
                    {
                        "case": key[0],
                        "turn": turn_index,
                        "output": output_index,
                        "type": output["type"],
                        "sql_reverified": key in covered,
                    }
                )
    covered_tables = {
        (item["case"], item["turn"]) for item in outputs if item["sql_reverified"]
    }
    by_type = {}
    for item in outputs:
        bucket = by_type.setdefault(
            item["type"], {"total": 0, "sql_rederived": 0, "llm_judged": 0}
        )
        bucket["total"] += 1
        if item["case"] == "K02" and dictionary_ok:
            bucket["sql_rederived"] += 1
        elif item["sql_reverified"]:
            bucket["sql_rederived"] += 1
        elif item["type"] == "chart" and any(
            case == item["case"] and turn <= item["turn"]
            for case, turn in covered_tables
        ):
            # 图表数据点由同轮查询表派生，查询表已被 SQL 复核
            bucket["sql_rederived"] += 1
        elif item["type"] in ("text", "report"):
            bucket["llm_judged"] += 1
    # K02 是字段字典表格（非 SQL），单独对照 information_schema 与数据字典
    return {
        "total_outputs": len(outputs),
        "sql_rederived": sum(bucket["sql_rederived"] for bucket in by_type.values()),
        "llm_judged": sum(bucket["llm_judged"] for bucket in by_type.values()),
        "by_type": by_type,
        "pending": [
            f"{item['case']}T{item['turn']}#{item['output']}({item['type']})"
            for item in outputs
            if not item["sql_reverified"]
            and not (item["case"] == "K02" and dictionary_ok)
            and not (
                item["type"] == "chart"
                and any(
                    case == item["case"] and turn <= item["turn"]
                    for case, turn in covered_tables
                )
            )
            and item["type"] not in ("text", "report")
        ],
    }


def row_membership(database, dataset, cases):
    """Expected single-table rows that can be matched directly against a physical table."""
    tables = {}
    for row in database.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='analysis'"
    ).fetchall():
        tables.setdefault(row["table_name"], set()).add(row["column_name"])
    results = []
    for item in dataset["rows"]:
        for answer in item["answer"]:
            if answer["type"] != "table" or not answer.get("rows"):
                continue
            columns = {column.lower() for column in answer["columns"]}
            matches = [table for table, cols in tables.items() if columns <= cols]
            if len(matches) != 1:
                continue
            table = matches[0]
            missing = 0
            for expected in answer["rows"]:
                clauses = " AND ".join(f"{column.lower()} = %s" for column in answer["columns"])
                params = [expected[column] for column in answer["columns"]]
                found = database.execute(
                    f"SELECT 1 FROM {table} WHERE {clauses} LIMIT 1", params
                ).fetchone()
                if not found:
                    missing += 1
            results.append(
                {
                    "id": item["id"],
                    "table": table,
                    "rows": len(answer["rows"]),
                    "missing_rows": missing,
                    "ok": missing == 0,
                }
            )
    return results




def dictionary_check(database, cases):
    """K02: field names against information_schema, meanings against the dictionary."""
    dictionary = (ROOT / "docs" / "reference" / "电子供应链数据表说明.md").read_text(
        encoding="utf-8"
    )
    columns = [
        row["column_name"]
        for row in database.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='analysis' AND table_name='products' ORDER BY ordinal_position"
        ).fetchall()
    ]
    case = next(item for item in cases if item["case_id"] == "K02")
    output = case["turns"][0]["expected"]["outputs"][0]
    facts = output.get("required_facts") or [
        f"{row['field']}：{row['meaning']}" for row in output.get("rows", [])
    ]
    missing_field, missing_meaning = [], []
    for fact in facts:
        if "：" not in fact:
            missing_field.append(fact)
            continue
        field, meaning = fact.split("：", 1)
        if field not in columns:
            missing_field.append(field)
        if f"| `{field}` | {meaning} |" not in dictionary:
            missing_meaning.append(field)
    return {
        "case": "K02",
        "output_type": output["type"],
        "facts_checked": len(facts),
        "db_columns": len(columns),
        "missing_fields": missing_field,
        "missing_meanings": missing_meaning,
        "ok": not missing_field and not missing_meaning and len(columns) == len(facts),
    }


def main():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = corpus["cases"]

    with psycopg.connect(
        database_url("DATABASE_URL"),
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c search_path=analysis,pg_catalog",
    ) as database:
        fidelity_report = fidelity(database)
        structural = structural_checks(database)
        membership = row_membership(database, dataset, cases)
        dictionary = dictionary_check(database, cases)

    coverage = reference_coverage(cases, dataset, dictionary_ok=dictionary["ok"])
    report = {
        "snapshot_id": corpus["dataset"]["snapshot_id"],
        "dataset_rows": len(dataset["rows"]),
        "fidelity": {
            "tables": fidelity_report,
            "ok": all(item["ok"] for item in fidelity_report.values()),
            "mismatched_cells": sum(item["mismatched_cells"] for item in fidelity_report.values()),
        },
        "structural": {"checks": structural, "ok": all(item["ok"] for item in structural)},
        "reference_sql": coverage,
        "dictionary": dictionary,
        "row_membership": {
            "checked": len(membership),
            "ok": all(item["ok"] for item in membership),
            "details": membership,
        },
    }
    report["ok"] = (
        report["fidelity"]["ok"]
        and report["structural"]["ok"]
        and report["row_membership"]["ok"]
        and report["dictionary"]["ok"]
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": report["ok"],
        "fidelity_tables": len(fidelity_report),
        "fidelity_mismatched_cells": report["fidelity"]["mismatched_cells"],
        "structural_failed": [item["check"] for item in structural if not item["ok"]],
        "sql_rederived_outputs": f"{coverage['sql_rederived']}/{coverage['total_outputs']}",
        "llm_judged_outputs": coverage["llm_judged"],
        "pending_outputs": coverage["pending"],
        "row_membership_checked": len(membership),
        "row_membership_failed": [item["id"] for item in membership if not item["ok"]],
        "dictionary_ok": report["dictionary"]["ok"],
        "report": str(OUT),
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
