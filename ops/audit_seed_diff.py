"""Audit a seed dataset change cell by cell (before/after directories).

    python -m ops.audit_seed_diff data/seed_raw data/seed [--out runtime/x.json]

Reports every changed cell with its physical line and primary key, grouped by
table.column, so a reviewer can approve the rewrite without diffing 62k rows.
"""

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

from enterprise_agent.settings import WORK


def _rows(path):
    raw = path.read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    return raw, rows[0], rows[1:]


def audit(before_dir, after_dir):
    before_dir, after_dir = Path(before_dir), Path(after_dir)
    names = sorted({p.name for p in before_dir.glob("*.csv")} | {p.name for p in after_dir.glob("*.csv")})
    report = {"before": str(before_dir), "after": str(after_dir), "tables": {}, "totals": {}}
    grand = Counter()
    for name in names:
        left, right = before_dir / name, after_dir / name
        if not (left.exists() and right.exists()):
            report["tables"][name] = {"status": "missing-side"}
            continue
        raw_b, header_b, body_b = _rows(left)
        raw_a, header_a, body_a = _rows(right)
        entry = {
            "sha256_before": hashlib.sha256(raw_b).hexdigest(),
            "sha256_after": hashlib.sha256(raw_a).hexdigest(),
            "rows_before": len(body_b),
            "rows_after": len(body_a),
            "header_equal": header_b == header_a,
            "columns": {},
        }
        if header_b != header_a:
            report["tables"][name] = {**entry, "status": "header-changed"}
            continue
        if len(body_b) != len(body_a):
            report["tables"][name] = {**entry, "status": "row-count-changed"}
            continue
        changed = 0
        for index, (row_b, row_a) in enumerate(zip(body_b, body_a)):
            if row_b[0] != row_a[0]:
                report["tables"][name] = {**entry, "status": "primary-key-order-changed"}
                break
            for position, column in enumerate(header_b):
                if row_b[position] == row_a[position]:
                    continue
                bucket = entry["columns"].setdefault(
                    column, {"changed": 0, "samples": []}
                )
                bucket["changed"] += 1
                if len(bucket["samples"]) < 3:
                    bucket["samples"].append(
                        {
                            "line": index + 2,
                            "primary_key": row_b[0],
                            "before": row_b[position],
                            "after": row_a[position],
                        }
                    )
                changed += 1
        entry["changed_cells"] = changed
        entry["status"] = "changed" if changed or raw_b != raw_a else "identical"
        report["tables"][name] = entry
        grand["changed_cells"] += changed
        grand["changed_tables"] += bool(changed)
    report["totals"] = dict(grand)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    report = audit(args.before, args.after)
    out = Path(args.out) if args.out else WORK / "seed-diff-audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, entry in report["tables"].items():
        if entry.get("status") != "changed":
            continue
        print(f"{name:28} cells={entry['changed_cells']}")
        for column, bucket in entry["columns"].items():
            sample = bucket["samples"][0]
            print(
                f"    {column:28} {bucket['changed']:>6}  e.g. line {sample['line']} "
                f"pk={sample['primary_key']} [{sample['before']}] -> [{sample['after']}]"
            )
    print(f"\ntotals={report['totals']} audit={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
