"""Normalize seed CSV values: case/whitespace variants and date formats.

Three declarative rules, all auditable:

1. ``CANONICAL``  — explicit enumeration rewrites (case / trailing space).
2. ``COLLAPSE``   — interior runs of spaces in descriptive text.
3. ``DATE_COLUMNS`` — every date/timestamp column rewritten to ISO 8601
   (``YYYY-MM-DD`` / ``YYYY-MM-DD HH:MM:SS``). A content-based guard fails loudly
   when an undeclared column starts looking like a date column.

Byte format (UTF-8 BOM, CRLF, no quoting, trailing CRLF) is preserved exactly and
a second run must be a no-op.

    python -m ops.normalize_seed --check   # report only, exit 1 when dirty
    python -m ops.normalize_seed --apply   # rewrite data/seed
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from enterprise_agent.settings import ROOT, WORK

DATASET = ROOT / "data" / "seed"
REPORT = WORK / "normalize-report.json"

# Explicit enumerations: only these raw spellings are rewritten, never a fuzzy match.
CANONICAL = {
    ("Materials", "material_category"): {
        "Display ": "Display",
        "display": "Display",
        "memory and storage": "Memory and Storage",
        "camera audio and sensors": "Camera Audio and Sensors",
        "Enclosure and Mechanical ": "Enclosure and Mechanical",
    },
    ("Suppliers", "city"): {
        "GHARBIA": "Gharbia",
    },
}

# Descriptive text columns where a separator artifact left two spaces behind.
COLLAPSE = {
    ("Suppliers", "address"),
    ("Warehouses", "location_description"),
}

# Canonical date/time columns: value is the expected kind.
DATE_COLUMNS = {
    ("FinishedGoodsInventory", "last_updated_at"): "timestamp",
    ("GoodsReceipts", "receipt_date"): "date",
    ("Production", "start_date"): "date",
    ("Production", "expected_end_date"): "date",
    ("Production", "actual_end_date"): "date",
    ("PurchaseOrders", "order_date"): "date",
    ("PurchaseOrders", "expected_delivery_date"): "date",
    ("PurchaseOrderDetails", "expected_delivery_date"): "date",
    ("QualityInspections", "inspection_date"): "date",
    ("RawMaterialInventory", "last_updated_at"): "timestamp",
    ("SupplierShipments", "ship_date"): "date",
    ("SupplierShipments", "expected_arrival_date"): "date",
    ("SupplierShipments", "actual_arrival_date"): "date",
}

DATE_SHAPE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
TS_SHAPE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}:\d{2}$")
UNSAFE = re.compile(r'[",\r\n]')


def _canonical_date(value, kind):
    """Parse the accepted shapes and emit zero-padded ISO 8601."""
    if kind == "date":
        for shape in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, shape).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError(f"无法解析日期：{value!r}")

    for shape in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value, shape).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"无法解析日期时间：{value!r}")


def _rewrite(table, column, value):
    rules = CANONICAL.get((table, column), {})
    if value in rules:
        value = rules[value]
    value = value.strip()
    if (table, column) in COLLAPSE:
        value = re.sub(r"\s{2,}", " ", value)
    kind = DATE_COLUMNS.get((table, column))
    if kind and value:
        value = _canonical_date(value, kind)
    return value


def _load(path):
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    if not raw.endswith(b"\r\n"):
        raise ValueError(f"{path.name}: 源文件不以 CRLF 结尾，拒绝改写以免破坏格式")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    return raw, bom, rows


def _dump(rows, bom):
    for row in rows:
        for field in row:
            if UNSAFE.search(field):
                raise ValueError(f"拒绝写出需要引号的字段：{field!r}")
    text = "\r\n".join(",".join(row) for row in rows) + "\r\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def _guard_date_columns(tables):
    """Fail loudly when a column starts looking like a date without being declared."""
    for table, (header, body) in tables.items():
        if not body:
            continue
        for position, column in enumerate(header):
            values = [row[position] for row in body if row[position]]
            if not values:
                continue
            declared = (table, column) in DATE_COLUMNS
            looks_date = all(DATE_SHAPE.match(v) for v in values)
            looks_ts = all(TS_SHAPE.match(v) for v in values)
            if declared:
                kind = DATE_COLUMNS[(table, column)]
                ok = looks_date if kind == "date" else looks_ts
                if not ok:
                    raise ValueError(
                        f"{table}.{column} 声明为 {kind}，但存在无法按该形状解析的值"
                    )
            elif looks_date or looks_ts:
                raise ValueError(
                    f"{table}.{column} 形似{'日期时间' if looks_ts else '日期'}列但未在 DATE_COLUMNS 声明"
                )


def scan():
    tables, files, total = {}, {}, 0
    for path in sorted(DATASET.glob("*.csv"), key=lambda item: item.name.casefold()):
        raw, bom, rows = _load(path)
        tables[path.stem] = (rows[0], rows[1:])
        files[path.name] = {
            "sha256_before": hashlib.sha256(raw).hexdigest(),
            "rows": len(rows) - 1,
        }
        total += len(rows) - 1
    _guard_date_columns(tables)

    cell_changes, date_changes = [], {}
    for path in sorted(DATASET.glob("*.csv"), key=lambda item: item.name.casefold()):
        table = path.stem
        raw, bom, rows = _load(path)
        header, body = rows[0], rows[1:]
        if len(body) != len({row[0] for row in body}):
            raise RuntimeError(f"{table}: 主键重复，拒绝清洗")
        for index, row in enumerate(body):
            for position, column in enumerate(header):
                value = row[position]
                fixed = _rewrite(table, column, value)
                if fixed == value:
                    continue
                if (table, column) in DATE_COLUMNS:
                    entry = date_changes.setdefault(
                        f"{table}.{column}",
                        {
                            "table": table,
                            "column": column,
                            "kind": DATE_COLUMNS[(table, column)],
                            "changed": 0,
                            "sample_before": value,
                            "sample_after": fixed,
                        },
                    )
                    entry["changed"] += 1
                else:
                    cell_changes.append(
                        {
                            "table": table,
                            "line": index + 2,
                            "primary_key": row[0],
                            "column": column,
                            "before": value,
                            "after": fixed,
                        }
                    )
                row[position] = fixed
        files[path.name]["sha256_after"] = hashlib.sha256(_dump(rows, bom)).hexdigest()
    return cell_changes, sorted(date_changes.values(), key=lambda i: i["table"]), files, total


def _write_atomic(path, payload, attempts=20):
    """Replace the file, retrying around editor/indexer locks.

    Some Windows watchers hold the target without FILE_SHARE_DELETE, so a rename
    fails while a plain in-place write still succeeds. Try rename first (keeps the
    write atomic), then fall back to truncate-and-write.
    """
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    for attempt in range(attempts):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                break
            time.sleep(1.0)
    for attempt in range(attempts):
        try:
            with path.open("r+b") as handle:
                handle.write(payload)
                handle.truncate()
            temp.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                temp.unlink(missing_ok=True)
                raise PermissionError(
                    f"{path.name} 被其他进程独占（编辑器索引/杀毒扫描），请关闭后重试"
                ) from None
            time.sleep(1.0)


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="写入清洗结果")
    group.add_argument("--check", action="store_true", help="只报告，有差异时退出码 1")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    cell_changes, date_changes, files, total = scan()
    date_cells = sum(item["changed"] for item in date_changes)
    report = {
        "dataset": str(DATASET),
        "rows_total": total,
        "cell_changes": len(cell_changes),
        "date_cells_changed": date_cells,
        "total_changes": len(cell_changes) + date_cells,
        "columns_touched": sorted(
            {f"{c['table']}.{c['column']}" for c in cell_changes}
            | {f"{d['table']}.{d['column']}" for d in date_changes}
        ),
        "changes": cell_changes,
        "date_columns": date_changes,
        "files": files,
    }
    WORK.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        for item in cell_changes:
            print(
                f"{item['table']}.csv:{item['line']}  pk={item['primary_key']}  "
                f"{item['column']}: [{item['before']}] -> [{item['after']}]"
            )
        for item in date_changes:
            print(
                f"{item['table']}.{item['column']}  {item['changed']} 个值: "
                f"[{item['sample_before']}] -> [{item['sample_after']}]"
            )
        print(f"\nrows={total} 文本改写={len(cell_changes)} 日期改写={date_cells} 报告={REPORT}")

    if not args.apply:
        return 1 if (cell_changes or date_changes) else 0

    for path in sorted(DATASET.glob("*.csv"), key=lambda item: item.name.casefold()):
        raw, bom, rows = _load(path)
        table = path.stem
        header, body = rows[0], rows[1:]
        for row in body:
            for position, column in enumerate(header):
                row[position] = _rewrite(table, column, row[position])
        payload = _dump(rows, bom)
        if payload != raw:
            _write_atomic(path, payload)
            print(f"written {path.name}")

    remaining, remaining_dates, _, total_after = scan()
    if total_after != total or remaining or remaining_dates:
        raise RuntimeError(
            f"回读校验失败：文本 {len(remaining)} 处、日期 {len(remaining_dates)} 列仍有差异"
        )
    print(f"已写入并回读校验通过：rows={total_after} remaining=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
