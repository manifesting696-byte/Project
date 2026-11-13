"""【离线】把 cases.json 导出成 (a) query/answer JSON 与 (b) agent-eval 用的 promptfoo-tests.json.

    python tests/preflight/export_dataset.py

Outputs:
    dataset.json          lean rows: {"id","query","history","answer","judge"}
    promptfoo-tests.json  promptfoo test array (vars + asserts) built from the same rows

Judging modes:
    set          unordered multiset equality on the given columns, numeric tolerance 0.01
    ordered      row order and values must match, numeric tolerance 0.01
    chart        chart type + label/value pairs (tolerance) + percentage sum
    llm_rubric   free-form text/report graded by an LLM judge on the required facts
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = Path(__file__).with_name("cases.json")
DATASET = Path(__file__).with_name("dataset.json")
TESTS = Path(__file__).resolve().parents[1] / "agent-eval" / "promptfoo-tests.json"

RUBRIC = (
    "请判断被测回答是否正确回答了用户问题「{query}」。\n"
    "必须包含的事实（允许同义表述与不同排版；数字允许千分位、百分比与单位差异，但必须一致）：\n"
    "{facts}\n"
    "不得与之矛盾的限制（不要求逐字复述；违反即判定不通过）：\n"
    "{limits}\n"
    "不得出现数据中不存在的指标数值或编造的口径。"
)


def judge_for(output):
    kind = output["type"]
    if kind == "table":
        return {
            "mode": "ordered" if output.get("ordered") else "set",
            "tolerance": 0.01,
            "columns": output["columns"],
        }
    if kind == "chart":
        return {
            "mode": "chart",
            "tolerance": 0.01,
            "chart_type": output.get("chart_type"),
        }
    return {
        "mode": "llm_rubric",
        "facts": output.get("required_facts", []),
    }


def answer_for(output):
    kind = output["type"]
    if kind == "table":
        return {
            "type": "table",
            "ordered": bool(output.get("ordered")),
            "columns": output["columns"],
            "rows": output["rows"],
        }
    if kind == "chart":
        answer = {
            "type": "chart",
            "chart_type": output.get("chart_type"),
            "category_field": output.get("category_field"),
            "value_field": output.get("value_field"),
            "data": output.get("data", []),
        }
        if output.get("percentages"):
            answer["percentages"] = output["percentages"]
        return answer
    return {
        "type": "text" if kind == "text" else kind,
        "facts": output.get("required_facts", []),
        "limitations": output.get("limitations", []),
    }


def rubric_text(query, outputs):
    facts, limits = [], []
    for output in outputs:
        facts.extend(output.get("required_facts", []))
        limits.extend(output.get("limitations", []))
    if not facts:
        facts = ["回答与问题相关，且不编造数据中不存在的数值或口径。"]
    if not limits:
        limits = ["回答不得与已给出的数据事实冲突。"]
    return RUBRIC.format(
        query=query,
        facts="\n".join(f"- {item}" for item in facts),
        limits="\n".join(f"- {item}" for item in limits),
    )


def build(corpus):
    rows, tests = [], []
    for case in corpus["cases"]:
        history = []
        for turn_index, turn in enumerate(case["turns"], 1):
            outputs = turn["expected"].get("outputs", [])
            judges = [judge_for(item) for item in outputs]
            row_id = f"{case['case_id']}-T{turn_index}"
            row = {
                "id": row_id,
                "case_id": case["case_id"],
                "category": case["category"],
                "turn": turn_index,
                "query": turn["input"],
                "history": list(history),
                "answer": [answer_for(item) for item in outputs],
                "judge": judges,
                "outcome": turn["expected"].get("outcome"),
            }
            rows.append(row)

            asserts = [
                {
                    "type": "javascript",
                    "value": "file://asserts/grade.js",
                    "threshold": 1,
                    "metric": "structured",
                }
            ]
            if any(item["type"] in ("text", "report") for item in outputs):
                asserts.append(
                    {
                        "type": "llm-rubric",
                        "value": rubric_text(turn["input"], outputs),
                        "threshold": 0.8,
                        "metric": "text",
                    }
                )
            tests.append(
                {
                    "description": f"{row_id} {case['title']}",
                    "vars": {
                        "case_id": case["case_id"],
                        "category": case["category"],
                        "turn": turn_index,
                        "query": turn["input"],
                        "history": list(history),
                        "expected": {
                            "outcome": turn["expected"].get("outcome"),
                            "outputs": outputs,
                        },
                        "judge": judges,
                    },
                    "assert": asserts,
                    "metadata": {"case_id": case["case_id"], "turn": turn_index},
                }
            )
            history.append(turn["input"])
    return rows, tests


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--tests", default=str(TESTS))
    args = parser.parse_args(argv)

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    rows, tests = build(corpus)
    modes = {}
    for row in rows:
        for judge in row["judge"]:
            modes[judge["mode"]] = modes.get(judge["mode"], 0) + 1

    dataset = {
        "metadata": {
            "snapshot_id": corpus["dataset"]["snapshot_id"],
            "source": "tests/preflight/cases.json",
            "rows": len(rows),
            "modes": modes,
            "judging": {
                "set": "无序行集合（多重集）完全相等，列由 columns 指定，数值容差 0.01",
                "ordered": "行序与数值均需一致，数值容差 0.01",
                "column_alignment": "判定锚点是每列在各行的值：先按列名（忽略大小写），再按整列取值匹配；多余列不参与判定；列数一致时可按位置对齐；行数必须一致",
                "empty_result": "期望空表时，实际必须为 0 行",
                "chart": "图表类型一致、每个类别的数值一致（容差 0.01），饼图占比合计 100",
                "llm_rubric": "由 LLM 依 facts 判定，允许同义表述，禁止编造数值",
            },
        },
        "rows": rows,
    }
    Path(args.dataset).write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    Path(args.tests).write_text(
        json.dumps(tests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"rows": len(rows), "tests": len(tests), "modes": modes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
