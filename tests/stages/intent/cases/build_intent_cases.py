"""【离线】生成意图识别测试题库（含 instruction 校对项）与两套 Promptfoo 测试。

输入：tests/new/intent/cases.json（原有 52 条语料 + 9 个固定上下文）
输出：
  cases/intent-cases.json                     题库（补上 instruction 期望）
  shared/intent-schema.json                   Intent 的 JSON Schema（直接调模型用）
  prompt-ab/prompts/intent-v1.txt             当前线上提示词（从源码抽取，保证同步）
  prompt-ab/prompts/intent-v2.txt             改进版（已存在则不覆盖，供人工迭代）
  e2e/promptfoo-tests.json                    测试A：端到端意图（走 /bind 预览）
  prompt-ab/promptfoo-tests.json              测试B：提示词 A/B（直接调模型）
  docs/testing/意图识别测试集.md              追加“指令校对”章节

    python tests/stages/intent/cases/build_intent_cases.py
"""

import json
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "tests" / "new" / "intent" / "cases.json"
BASE = Path(__file__).resolve().parents[1]
PROMPT_SRC = ROOT / "src" / "enterprise_agent" / "stages" / "intent" / "prompt.py"
DOC = ROOT / "docs" / "testing" / "意图识别测试集.md"
DOC_BEGIN = "<!-- instruction-check:begin -->"
DOC_END = "<!-- instruction-check:end -->"

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{2,}")
# 9 份固定上下文里逐字节相同的大块，抽到 shared/context-base.json，只存一份
SHARED_KEYS = ("database_metadata", "available_capabilities")
STOP = {
    "top", "and", "the", "for", "with", "order", "by", "desc", "asc",
    "null", "none", "request", "artifact",
}
TIME_WORDS = ("上半年", "下半年", "第一季度", "第二季度", "第三季度", "第四季度",
              "本月", "上月", "今年", "去年", "季度", "月度", "年度")
ORDER_GROUPS = [
    ["降序", "由大到小", "倒序", "降序排列"],
    ["升序", "由小到大", "正序", "升序排列"],
    ["排名", "排序"],
]
TOP_N = re.compile(r"(?:前|top)\s*(\d+)", re.I)
DELIVERY_WORDS = ("柱状图", "折线图", "饼图", "散点图", "报告", "表格")
# 常见字段的中英写法，命中任一即可
SYNONYMS = {
    "product_id": ["product_id", "产品ID", "产品id", "产品编号"],
    "product_name": ["product_name", "产品名称"],
    "product_category": ["product_category", "产品类别"],
    "supplier_id": ["supplier_id", "供应商ID", "供应商id"],
    "supplier_name": ["supplier_name", "供应商名称"],
    "order_count": ["order_count", "订单数量", "订单数"],
    "production_quantity": ["production_quantity", "实际产量", "产量"],
    "planned_quantity": ["planned_quantity", "计划数量"],
    "rejected_quantity": ["rejected_quantity", "拒收数量"],
    "quantity_on_hand": ["quantity_on_hand", "现存量"],
    "quantity_reserved": ["quantity_reserved", "预留量"],
    "actual_end_date": ["actual_end_date", "实际完工日期"],
    "order_date": ["order_date", "下单日期"],
    "receipt_date": ["receipt_date", "收货日期"],
    "selling_price": ["selling_price", "销售价格", "售价"],
    "standard_cost": ["standard_cost", "标准成本"],
    "material_category": ["material_category", "原材料类别", "材料类别"],
    "warehouse_id": ["warehouse_id", "仓库ID", "仓库id"],
}


def normalise(text):
    """全角转半角、去空白、拉丁小写，用于要素命中判断。"""
    out = []
    for ch in str(text or ""):
        code = ord(ch)
        if code == 0x3000:
            continue
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        out.append(ch)
    return re.sub(r"\s+", "", "".join(out)).lower()


def db_fields(context):
    names = set()
    for table, entry in (context.get("database_metadata", {}).get("tables") or {}).items():
        names.add(table.lower())
        for column in entry.get("columns") or []:
            names.add(str(column).lower())
    return names


def identifiers(query, fields):
    found = []
    for raw in IDENT.findall(query):
        token = raw.strip(".")
        low = token.lower()
        if low in STOP:
            continue
        if "_" in token or "." in token or low in fields:
            if token not in found:
                found.append(token)
    return found


def instruction_expect(query, requirement, match, fields):
    """为一条期望操作生成 instruction 的可判定要素。"""
    all_terms, any_groups, none_terms = [], [], []
    for token in identifiers(query, fields):
        low = token.split(".")[-1].lower()
        alias = SYNONYMS.get(low)
        if alias:
            any_groups.append(list(dict.fromkeys(alias)))
        else:
            all_terms.append(token)
    # 中文写法也要命中：query 里出现「实际产量」时，instruction 用 production_quantity 或「实际产量」都算保留
    covered = {alias.lower() for group in any_groups for alias in group}
    for _, aliases in SYNONYMS.items():
        hit = [alias for alias in aliases if normalise(alias) in normalise(query)]
        if not hit:
            continue
        group = [alias for alias in dict.fromkeys(aliases) if alias.lower() not in covered]
        if group:
            any_groups.append(group)
            covered.update(alias.lower() for alias in group)
    for year in dict.fromkeys(re.findall(r"20\d{2}", query)):
        all_terms.append(year)
    for word in TIME_WORDS:
        if word in query:
            all_terms.append(word)
    for group in ORDER_GROUPS:
        if any(word in query for word in group):
            any_groups.append(group)
    for number in dict.fromkeys(TOP_N.findall(query)):
        any_groups.append([f"前{number}", f"top{number}", f"前 {number}", f"{number}名", f"{number}个"])
    # 去掉被更长词包含的短词（如「第二季度」已含「季度」）
    all_terms = [term for term in all_terms
                 if not any(term != other and term in other for other in all_terms)]
    if match.get("category") == "result_interaction":
        for word in DELIVERY_WORDS:
            if word in query:
                all_terms.append(word)
    elements = [f"保留业务对象与要求：{requirement}"]
    if any(y in query for y in re.findall(r"20\d{2}", query)):
        elements.append("保留时间条件：" + "、".join(dict.fromkeys(re.findall(r"20\d{2}", query))))
    if any(any(word in query for word in group) for group in ORDER_GROUPS) or TOP_N.search(query):
        parts = [w for group in ORDER_GROUPS for w in group if w in query]
        parts += [f"前{n}" for n in TOP_N.findall(query)]
        elements.append("保留排序或取数要求：" + "、".join(dict.fromkeys(parts)))
    if match.get("category") == "result_interaction" and any(w in query for w in DELIVERY_WORDS):
        elements.append("保留交付形态：" + "、".join(w for w in DELIVERY_WORDS if w in query))
    if match.get("action") == "update_task":
        elements.append("只描述本次实际变化，不擅自新增交付")
    if match.get("refs"):
        elements.append("引用来源与 refs 一致（已有结果或上游命名交付）")
    return {
        "must_include_all": list(dict.fromkeys(all_terms)),
        "must_include_any": any_groups,
        "must_not_include": list(none_terms),
        "min_length_ratio": 0.45 if len(query) >= 15 else 0.0,
        "elements": elements,
    }



def online_context_keys():
    """从 model/context.py 的 initial() 里解析线上上下文字段，用于契约校验。"""
    source = (ROOT / "src" / "enterprise_agent" / "model" / "context.py").read_text(encoding="utf-8")
    block = source.split("def initial(")[1].split("\n    def ")[0]
    return set(re.findall(r'^\s{12}"([a-z_]+)":', block, re.M))


def capabilities():
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from enterprise_agent.stages.execute.agents.capabilities import CAPABILITIES

    return dict(CAPABILITIES)



def registry_overview():
    """从应用侧生成真实 database_metadata，而不是手写简化版。"""
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from enterprise_agent.catalog.registry import Registry

    return Registry(None).overview()


OVERVIEW_KEYS = {
    "backend", "source_id", "snapshot_id", "catalog_version", "catalog_revision",
    "unavailable_tables", "semantic_version", "tables", "relations",
    "metrics", "unapproved_metrics", "limitations",
}


def assert_overview_shape(overview):
    """防止线上 overview 结构变化后，夹具悄悄退化成简化版。"""
    missing = OVERVIEW_KEYS - set(overview)
    extra = set(overview) - OVERVIEW_KEYS
    if missing or extra:
        raise RuntimeError(f"database_metadata 与 Registry.overview 不一致：缺 {sorted(missing)}，多 {sorted(extra)}")
    for name, table in overview["tables"].items():
        if set(table) != {"rows", "primary_key", "columns"}:
            raise RuntimeError(f"{name} 表结构应为 rows/primary_key/columns，实际 {sorted(table)}")
        sample = next(iter(table["columns"].values()))
        if set(sample) != {"type", "meaning"}:
            raise RuntimeError(f"{name}.columns 应为 列名→{{type,meaning}}，实际 {sorted(sample)}")
    if not isinstance(overview["relations"], list) or not overview["relations"]:
        raise RuntimeError("relations 不能为空")
    for item in overview["relations"]:
        if set(item) != {"from", "to"}:
            raise RuntimeError(f"relation 结构异常：{sorted(item)}")


def build_cases(raw):
    fields = db_fields(raw["contexts"]["empty"]["context"])
    caps = capabilities()
    expected_keys = online_context_keys() | {"query"}
    shared_context = {}
    overview = registry_overview()
    assert_overview_shape(overview)
    cases = []
    for case in raw["cases"]:
        context = json.loads(json.dumps(raw["contexts"][case["context_id"]]["context"]))
        context["query"] = case["query"]
        # 冻结上下文必须与线上 context.initial 同构，否则测试B 与线上不等价
        context.setdefault("available_capabilities", caps)
        # 语料里的 database_metadata 是手写简化版，一律用应用侧真实 overview 覆盖
        context["database_metadata"] = overview
        shared_context.setdefault("database_metadata", overview)
        shared_context.setdefault("available_capabilities", context["available_capabilities"])
        operations = []
        for op in case["expected"].get("operations", []):
            operations.append({
                "requirement": op["requirement"],
                "alias": op.get("alias"),
                "match": op.get("match", {}),
                "instruction": instruction_expect(
                    case["query"], op["requirement"], op.get("match", {}), fields
                ),
            })
        clarifications = case["expected"].get("clarifications", [])
        rubric = instruction_rubric(case, operations, clarifications)
        cases.append({
            "case_id": case["case_id"],
            "group": case["group"],
            "title": case["title"],
            "query": case["query"],
            "context_id": case["context_id"],
            "fields": case["evaluation_fields"],
            "context": context,
            "expected": {"operations": operations, "clarifications": clarifications},
            "instruction_rubric": rubric,
            "judging_notes": case.get("judging_notes", []),
        })
    # 契约校验：shared + 用例差异 合并后必须与线上 context.initial 同构
    for case in cases:
        merged = {**shared_context, **case["context"]}
        case["context"] = {k: v for k, v in case["context"].items() if k not in SHARED_KEYS}
        missing = expected_keys - set(merged)
        extra = set(merged) - expected_keys
        if missing or extra:
            raise RuntimeError(
                f"{case['case_id']} 上下文与 context.initial 不一致：缺少 {sorted(missing)}，多出 {sorted(extra)}"
            )
    return cases, shared_context


def instruction_rubric(case, operations, clarifications):
    lines = [
        f"请判断被测回答里每项 operation 的 instruction 是否忠实表达了用户本轮要求（问题：{case['query']}）。",
        "判定要点：",
    ]
    for index, op in enumerate(operations, 1):
        lines.append(f"- 第{index}项（{op['requirement']}）需满足：")
        for element in op["instruction"]["elements"]:
            lines.append(f"  · {element}")
    if clarifications:
        lines.append("- 澄清问题需补足下列信息缺口，且不得重复询问上下文已知信息：")
        for item in clarifications:
            lines.append("  · 缺口：" + "；".join(item.get("question_must_resolve", [])))
            for known in item.get("already_known", []):
                lines.append(f"  · 已知，不得重复询问：{known}")
    lines.append("instruction 可以改写措辞、合并同义表达，但不得丢失上述要素、不得新增用户没提的交付或条件。")
    return "\n".join(lines)


def pf_tests(cases, assert_path, include_context=True):
    tests = []
    for case in cases:
        asserts = [{
            "type": "javascript",
            "value": assert_path,
            "threshold": 1,
            "metric": "intent",
        }]
        if case["instruction_rubric"]:
            asserts.append({
                "type": "llm-rubric",
                "value": case["instruction_rubric"],
                "threshold": 0.8,
                "metric": "instruction",
            })
        tests.append({
            "description": f"{case['case_id']} {case['group']} {case['title']}",
            "vars": {
                "case_id": case["case_id"],
                "group": case["group"],
                "query": case["query"],
                **({"context": case["context"]} if include_context else {}),
                "expected": case["expected"],
            },
            "assert": asserts,
            "metadata": {"case_id": case["case_id"], "group": case["group"]},
        })
    return tests


def extract_prompt():
    text = PROMPT_SRC.read_text(encoding="utf-8")
    match = re.search(r'SUPERVISOR_INTENT\s*=\s*"""(.*?)"""', text, re.S)
    if not match:
        raise RuntimeError("未能从源码中抽取 SUPERVISOR_INTENT")
    return match.group(1).strip() + "\n"


def intent_schema():
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from enterprise_agent.stages.intent.schema import Intent

    return Intent.model_json_schema()


def write_doc_block(cases):
    lines = [
        DOC_BEGIN,
        "",
        "## 指令校对（instruction）",
        "",
        "原有五组只评价 category / action / refs / request_id / clarifications，**未校对拆分出的 request 里 instruction 的具体内容**。",
        "本节为补充：每条用例在原有字段之外，额外比较「期望 instruction 要素」与「实际输出的 instruction」。",
        "",
        "判定分两层：",
        "",
        "1. **确定性要素**（可机械判定）：表/字段等标识符、年份、时间短语（上半年/季度…）、排序与取数（升序/降序/前N）、交付形态（柱状图/报告…）必须出现在 instruction 中；允许中文别名（如 `product_id` ↔ `产品ID`）；instruction 长度不得显著短于 query（≥45%，仅对 ≥15 字的 query 生效）。",
        "2. **语义要点**（LLM 判定）：是否保留业务对象与口径、是否补全指代、`update_task` 是否只描述本次变化且不新增交付、引用来源是否与 refs 一致。",
        "",
        "| 用例 | 组 | 类型 | instruction 必须出现 | 语义要点 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        for index, op in enumerate(case["expected"]["operations"], 1):
            expect = op["instruction"]
            must = []
            if expect["must_include_all"]:
                must.append("、".join(f"`{item}`" for item in expect["must_include_all"]))
            for group in expect["must_include_any"]:
                must.append(" 或 ".join(f"`{item}`" for item in group))
            match = op["match"]
            kind = f"{match.get('category', '-')}/{match.get('action', '-')}"
            label = f"{case['case_id']}#{index}" if index > 1 else case["case_id"]
            lines.append(
                f"| {label} | {case['group']} | {kind} | {'；'.join(must) or '（无硬性要素，转语义判定）'} "
                f"| {'；'.join(expect['elements'])} |"
            )
    lines += ["", DOC_END, ""]
    block = "\n".join(lines)

    doc = DOC.read_text(encoding="utf-8")
    if DOC_BEGIN in doc and DOC_END in doc:
        head, rest = doc.split(DOC_BEGIN, 1)
        _, tail = rest.split(DOC_END, 1)
        doc = head + block + tail
    else:
        doc = doc.rstrip() + "\n\n" + block
    DOC.write_text(doc, encoding="utf-8")
    return block.count("\n")


def main():
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases, shared_context = build_cases(raw)

    (BASE / "cases").mkdir(parents=True, exist_ok=True)
    (BASE / "shared").mkdir(parents=True, exist_ok=True)
    (BASE / "e2e").mkdir(parents=True, exist_ok=True)
    (BASE / "prompt-ab" / "prompts").mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 1,
        "purpose": "意图识别阶段测试：字段 + instruction 校对",
        "source": "tests/new/intent/cases.json",
        "groups": raw["groups"],
        "case_count": len(cases),
        "shared_context_keys": list(SHARED_KEYS),
        "shared_context_ref": "shared/context-base.json",
        "cases": cases,
    }
    (BASE / "cases" / "intent-cases.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    (BASE / "shared" / "context-base.json").write_text(
        json.dumps(shared_context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (BASE / "shared" / "intent-schema.json").write_text(
        json.dumps(intent_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    prompt_file = BASE / "prompt-ab" / "prompts" / "intent-v1.txt"
    prompt_file.write_text(extract_prompt(), encoding="utf-8")
    v2 = BASE / "prompt-ab" / "prompts" / "intent-v2.txt"
    if not v2.exists():
        v2.write_text(prompt_file.read_text(encoding="utf-8"), encoding="utf-8")

    # e2e 由应用自己造上下文，用例里不带 context；prompt-ab 直连模型，需要带
    for folder, assert_path, include_context in (
        ("e2e", "file://../shared/assert-intent.js", False),
        ("prompt-ab", "file://../shared/assert-intent.js", True),
    ):
        target = BASE / folder / "promptfoo-tests.json"
        target.write_text(
            json.dumps(pf_tests(cases, assert_path, include_context), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

    doc_lines = write_doc_block(cases)

    stats = {
        "cases": len(cases),
        "operations": sum(len(case["expected"]["operations"]) for case in cases),
        "with_hard_terms": sum(
            1 for case in cases for op in case["expected"]["operations"]
            if op["instruction"]["must_include_all"] or op["instruction"]["must_include_any"]
        ),
        "clarification_cases": sum(1 for case in cases if case["expected"]["clarifications"]),
        "doc_block_lines": doc_lines,
    }
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
