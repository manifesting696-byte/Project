"""Summarize a Promptfoo eval JSON into a per-case table for the blackbox suite.

    python tests/agent-eval/report.py runtime/promptfoo-full.json [--csv out.csv]
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def component(results, metric):
    for item in results or []:
        assertion = item.get("assertion") or {}
        if assertion.get("metric") == metric:
            return item
    return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--csv")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    raw = payload.get("results", payload)
    rows = raw.get("results", raw if isinstance(raw, list) else [])

    table = []
    for row in rows:
        case = (row.get("testCase") or {})
        vars_ = case.get("vars") or {}
        grading = row.get("gradingResult") or {}
        components = grading.get("componentResults") or []
        structured = component(components, "structured")
        text = component(components, "text")
        output = (row.get("response") or {})
        status = None
        try:
            status = json.loads(output.get("output") or "{}").get("status")
        except (json.JSONDecodeError, TypeError):
            status = None
        reason = ""
        failed = [item for item in components if not item.get("pass")]
        if failed:
            reason = failed[0].get("reason") or ""
        table.append(
            {
                "case": vars_.get("case_id"),
                "turn": vars_.get("turn"),
                "category": vars_.get("category"),
                "pass": bool(row.get("success")),
                "structured": None if structured is None else bool(structured.get("pass")),
                "text": None if text is None else bool(text.get("pass")),
                "status": status,
                "latency_s": round((row.get("latencyMs") or 0) / 1000),
                "reason": reason[:400],
                "error": (row.get("error") or "")[:200],
            }
        )

    by_case = defaultdict(list)
    for item in table:
        by_case[item["case"]].append(item)
    categories = Counter()
    category_pass = Counter()
    for item in table:
        categories[item["category"]] += 1
        category_pass[item["category"]] += int(item["pass"])

    print(f"rows={len(table)} passed={sum(1 for i in table if i['pass'])} "
          f"failed={sum(1 for i in table if not i['pass'])}")
    print("\n按类别：")
    for name, total in categories.most_common():
        print(f"  {name:6} {category_pass[name]}/{total}")
    print("\n按案例：")
    for case in sorted(by_case):
        turns = by_case[case]
        marks = "".join("✓" if item["pass"] else "✗" for item in turns)
        print(f"  {case:4} {marks:4} ({sum(1 for i in turns if i['pass'])}/{len(turns)})")
    failures = [item for item in table if not item["pass"]]
    if failures:
        print("\n失败明细：")
        for item in failures:
            print(f"  {item['case']}T{item['turn']} [{item['category']}] status={item['status']} "
                  f"structured={item['structured']} text={item['text']}")
            print(f"      {item['reason'] or item['error']}")

    if args.csv:
        import csv as csv_module

        with Path(args.csv).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv_module.DictWriter(handle, fieldnames=list(table[0].keys()))
            writer.writeheader()
            writer.writerows(table)
        print(f"\ncsv: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
