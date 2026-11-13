"""Author intent-only cases and expected field assertions without model calls."""

import csv
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name("cases.json")
DOCUMENT = ROOT / "docs" / "testing" / "意图识别测试集.md"
GROUPS = {
    "G1": {"name": "四个基础类别识别", "fields": ["category"]},
    "G2": {"name": "多需求识别", "fields": ["operation_count", "category"]},
    "G3": {
        "name": "新建与修改",
        "fields": ["category", "action", "refs", "request_id"],
    },
    "G4": {"name": "同轮依赖识别", "fields": ["category", "refs"]},
    "G5": {"name": "信息不足与澄清", "fields": ["clarifications"]},
}


def artifact_ref(key):
    return {"kind": "artifact", "id": key}


def request_ref(key, output):
    return {"kind": "request", "id": key, "output": output}


def operation(label, category, *, alias=None, action=None, request_id=None, refs=None):
    match = {"category": category}
    if action is not None:
        match["action"] = action
    if request_id is not None:
        match["request_id"] = request_id
    if refs is not None:
        match["refs"] = refs
    result = {"requirement": label, "match": match}
    if alias:
        result["alias"] = alias
    return result


def request(key, title, parameters, output, kind, description=None):
    return {
        "request_id": key,
        "title": title,
        "status": "completed",
        "current_revision_id": "rev_" + key[4:],
        "spec": {
            "description": description or title,
            "parameters": parameters,
            "inputs": [],
            "outputs": [
                {
                    "name": output,
                    "content_type": kind,
                    "description": title,
                    "options": {},
                }
            ],
        },
    }


def artifact(key, owner, kind, title, output, columns=(), sources=()):
    return {
        "artifact_id": key,
        "request_id": owner,
        "revision_id": "rev_" + owner[4:],
        "session_id": "session_intent_fixture",
        "content_type": kind,
        "title": title,
        "summary": title,
        "source_artifact_ids": list(sources),
        "payload": {"columns": [{"name": name} for name in columns]},
        "fixture_output_name": output,
    }


def build_contexts():
    tables = {}
    for path in sorted((ROOT / "data" / "seed").glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
        tables[path.stem.lower()] = {"columns": reader.fieldnames, "rows": len(rows)}
    assert len(tables) == 16
    base = {
        "stage": "initial",
        "session_hints": {
            "last_active_request_id": None,
            "last_response_event_id": None,
        },
        "recent_messages": [],
        "candidate_requests": [],
        "candidate_artifacts": [],
        "history_summary": None,
        "database_metadata": {
            "tables": tables,
            "terminology": {
                "现存量": "RawMaterialInventory.quantity_on_hand 与 FinishedGoodsInventory.quantity_on_hand",
                "预留量": "RawMaterialInventory.quantity_reserved 与 FinishedGoodsInventory.quantity_reserved",
                "实际产量": "Production.production_quantity",
                "生产开始日期": "Production.start_date",
                "实际完工日期": "Production.actual_end_date",
            },
            "limitations": [
                "库存只有2025-12-31快照",
                "缺少设备故障、产线、工序及销售订单数据",
                "到货准时率等指标处于candidate状态",
            ],
        },
    }
    requests = {
        "req_101": request(
            "req_101",
            "2025年上半年产品实际产量前5名",
            {
                "date_field": "actual_end_date",
                "start": "2025-01-01",
                "end": "2025-06-30",
                "limit": 5,
                "order": "production_quantity DESC, product_id ASC",
            },
            "product_totals",
            "table",
        ),
        "req_102": request(
            "req_102",
            "产品产量前5名竖向柱状图",
            {"chart_type": "bar", "orientation": "vertical"},
            "production_chart",
            "chart",
        ),
        "req_103": request(
            "req_103",
            "2025年上半年供应商采购订单数量前5名",
            {
                "date_field": "order_date",
                "start": "2025-01-01",
                "end": "2025-06-30",
                "limit": 5,
            },
            "supplier_counts",
            "table",
        ),
        "req_104": request(
            "req_104", "产品产量分析报告", {}, "production_report", "report"
        ),
        "req_105": request(
            "req_105",
            "供应商采购订单数量柱状图",
            {"chart_type": "bar"},
            "supplier_chart",
            "chart",
        ),
        "req_107": request(
            "req_107",
            "前2个产品的基础资料",
            {"limit": 2, "order": "product_id ASC"},
            "product_rows",
            "table",
        ),
        "req_109": request(
            "req_109", "项目库存字段含义说明", {}, "field_explanation", "text"
        ),
    }
    artifacts = {
        "result_101": artifact(
            "result_101",
            "req_101",
            "table",
            "2025上半年产品实际产量前5名",
            "product_totals",
            ["product_id", "product_name", "production_quantity"],
        ),
        "result_102": artifact(
            "result_102",
            "req_107",
            "table",
            "product_id为1和2的产品资料",
            "product_rows",
            [
                "product_id",
                "product_code",
                "product_name",
                "standard_cost",
                "selling_price",
            ],
        ),
        "result_201": artifact(
            "result_201",
            "req_102",
            "chart",
            "产品产量前5名竖向柱状图",
            "production_chart",
            sources=["result_101"],
        ),
        "result_202": artifact(
            "result_202",
            "req_105",
            "chart",
            "供应商采购订单数量柱状图",
            "supplier_chart",
            sources=["result_301"],
        ),
        "result_301": artifact(
            "result_301",
            "req_103",
            "table",
            "2025上半年供应商采购订单数量前5名",
            "supplier_counts",
            ["supplier_id", "supplier_name", "order_count"],
        ),
        "result_401": artifact(
            "result_401",
            "req_104",
            "report",
            "产品产量分析报告",
            "production_report",
            sources=["result_101"],
        ),
        "result_501": artifact(
            "result_501",
            "req_109",
            "text",
            "quantity_on_hand与quantity_reserved字段说明",
            "field_explanation",
        ),
    }
    requests["req_102"]["spec"]["inputs"] = [
        {"slot": "data", "source": artifact_ref("result_101")}
    ]
    requests["req_104"]["spec"]["inputs"] = [
        {"slot": "data", "source": artifact_ref("result_101")}
    ]
    requests["req_105"]["spec"]["inputs"] = [
        {"slot": "data", "source": artifact_ref("result_301")}
    ]

    def history(request_ids, artifact_ids, note):
        context = deepcopy(base)
        context["candidate_requests"] = [deepcopy(requests[key]) for key in request_ids]
        context["candidate_artifacts"] = [
            deepcopy(artifacts[key]) for key in artifact_ids
        ]
        context["session_hints"] = {
            "last_active_request_id": request_ids[-1],
            "last_response_event_id": "event_fixture_answer",
        }
        context["recent_messages"] = [
            {
                "event_id": "event_fixture_user",
                "event_type": "user_message",
                "payload": {"content": note},
            },
            {
                "event_id": "event_fixture_answer",
                "event_type": "assistant_response",
                "payload": {
                    "blocks": [
                        {
                            "block_id": "block_" + str(index + 1),
                            "artifact_id": key,
                            "request_id": artifacts[key]["request_id"],
                        }
                        for index, key in enumerate(artifact_ids)
                    ]
                },
            },
        ]
        return context

    return {
        "empty": {
            "description": "新会话，已有项目目录，尚无历史工作或产物。",
            "context": base,
        },
        "product": {
            "description": "上轮已完成产品产量查询。",
            "context": history(
                ["req_101"],
                ["result_101"],
                "按实际完工日期统计2025上半年产品实际产量前5名。",
            ),
        },
        "product_and_supplier": {
            "description": "产品产量与供应商订单两项工作都已完成，最近活跃项是供应商统计。",
            "context": history(
                ["req_101", "req_103"],
                ["result_101", "result_301"],
                "分别查询产品产量前5名和供应商订单数前5名。",
            ),
        },
        "product_chart": {
            "description": "唯一一张图为产品产量图，可追溯到产品统计表。",
            "context": history(
                ["req_101", "req_102"],
                ["result_101", "result_201"],
                "给产品产量前5名生成竖向柱状图。",
            ),
        },
        "two_charts": {
            "description": "上一条回答同时展示产品产量图和供应商订单图，当前用户没有选中任何一张图。",
            "context": history(
                ["req_101", "req_102", "req_103", "req_105"],
                ["result_101", "result_201", "result_301", "result_202"],
                "请同时展示产品产量图和供应商订单数图。",
            ),
        },
        "report": {
            "description": "上轮已完成产品产量报告，报告有明确产物。",
            "context": history(
                ["req_101", "req_104"],
                ["result_101", "result_401"],
                "根据产品产量查询结果生成报告。",
            ),
        },
        "product_rows": {
            "description": "上轮返回product_id为1和2的两个产品资料。",
            "context": history(
                ["req_107"], ["result_102"], "按product_id升序列出前2个产品的基础资料。"
            ),
        },
        "product_with_selection": {
            "description": "同时存在产品产量汇总及两个产品的基础资料。",
            "context": history(
                ["req_101", "req_107"],
                ["result_101", "result_102"],
                "产品产量汇总已完成，另外列出了产品ID为1和2的资料。",
            ),
        },
        "field_text": {
            "description": "上轮解释过两个库存字段，说明文字已保存。",
            "context": history(
                ["req_109"],
                ["result_501"],
                "解释本项目quantity_on_hand与quantity_reserved字段。",
            ),
        },
    }


def build_cases():
    cases = []

    def add(
        key, title, query, operations, context="empty", notes=(), clarifications=None
    ):
        group = key.split("-")[0]
        expected = {"operations": operations}
        if group == "G2":
            expected["operation_count"] = len(operations)
        if group == "G5":
            expected["clarifications"] = clarifications or []
        elif group == "G3":
            expected["clarifications"] = []
        cases.append(
            {
                "case_id": key,
                "group": group,
                "title": title,
                "query": query,
                "context_id": context,
                "evaluation_fields": GROUPS[group]["fields"],
                "expected": expected,
                "judging_notes": list(notes),
            }
        )

    simple = [
        ("系统能力介绍", "介绍你在这个供应链数据项目中的用途。", "general_qa", "empty"),
        (
            "通用统计概念",
            "一般统计分析中，平均值与中位数有什么区别？",
            "general_qa",
            "empty",
        ),
        (
            "明确通用的库存概念",
            "在一般库存管理中，现存量与预留量通常有什么区别？",
            "general_qa",
            "empty",
        ),
        (
            "项目库存字段",
            "本项目RawMaterialInventory中的quantity_reserved表示什么？",
            "business_knowledge",
            "empty",
        ),
        (
            "项目真实表关系",
            "本项目采购订单与收货单通过哪些表和字段关联？",
            "business_knowledge",
            "empty",
        ),
        (
            "省略范围的项目术语",
            "现存量与预留量有什么区别？",
            "business_knowledge",
            "empty",
        ),
        ("产品数量查询", "查询Products中的产品记录总数。", "data_analysis", "empty"),
        (
            "供应商订单排名",
            "按下单日期统计2025年上半年各供应商的采购订单数，降序取前5名。",
            "data_analysis",
            "empty",
        ),
        (
            "产品实际产量统计",
            "按实际完工日期统计2025年第二季度各产品的实际产量合计。",
            "data_analysis",
            "empty",
        ),
        (
            "已有图表解读",
            "解释刚才这张产品产量图表达了什么。",
            "result_interaction",
            "product_chart",
        ),
        (
            "根据已有表绘图",
            "把刚才的产品产量结果画成柱状图。",
            "result_interaction",
            "product",
        ),
        (
            "根据已有表生成报告",
            "根据刚才的产品产量结果生成一份简短报告。",
            "result_interaction",
            "product",
        ),
        (
            "通用相关性概念",
            "一般数据分析中，相关性与因果关系有什么区别？",
            "general_qa",
            "empty",
        ),
        (
            "项目指标定义及发布状态",
            "介绍本项目到货准时率的定义及当前发布状态。",
            "business_knowledge",
            "empty",
        ),
        (
            "按明确公式计算库存差值",
            "查询仓库1每种原材料的现存量减预留量，按这个差值升序列出前5项。",
            "data_analysis",
            "empty",
        ),
        (
            "解读已有结果中的差异",
            "解读刚才产品产量前5名结果中，第一名与第五名的产量差异。",
            "result_interaction",
            "product",
        ),
    ]
    for index, (title, query, category, context) in enumerate(simple, 1):
        notes = ["按项目术语的默认判定约定标注为企业知识。"] if index == 6 else []
        add(
            f"G1-{index:02d}",
            title,
            query,
            [operation(query, category)],
            context,
            notes,
        )

    add(
        "G2-01",
        "基础问答加数据查询",
        "先解释算术平均值，再查询产品总数。",
        [
            operation("解释算术平均值", "general_qa"),
            operation("查询产品总数", "data_analysis"),
        ],
    )
    add(
        "G2-02",
        "企业知识加数据查询",
        "先说明本项目po_id和po_detail_id的区别，再查询采购订单总数。",
        [
            operation("解释两个采购标识", "business_knowledge"),
            operation("查询采购订单总数", "data_analysis"),
        ],
    )
    add(
        "G2-03",
        "两个独立数据查询",
        "分别查询Products的产品总数和Materials的材料总数。",
        [
            operation("查询产品总数", "data_analysis"),
            operation("查询材料总数", "data_analysis"),
        ],
        notes=["保留两个相同类别，操作数为2。"],
    )
    add(
        "G2-04",
        "两个独立基础问答",
        "分两项回答，第一项解释算术平均值，第二项解释异常值的含义。",
        [
            operation("解释算术平均值", "general_qa"),
            operation("解释异常值", "general_qa"),
        ],
    )
    add(
        "G2-05",
        "两个独立企业知识问题",
        "分别介绍Products表和Warehouses表的字段含义，各自给出说明。",
        [
            operation("Products字段说明", "business_knowledge"),
            operation("Warehouses字段说明", "business_knowledge"),
        ],
    )
    add(
        "G2-06",
        "同一结果的两项交付",
        "请分别为刚才的产品产量结果生成一张柱状图和一份简短报告。",
        [
            operation("生成柱状图", "result_interaction"),
            operation("生成报告", "result_interaction"),
        ],
        "product",
    )
    add(
        "G2-07",
        "四种类别同时出现",
        "请分四项完成这些工作，分别为解释平均值、解释本项目production_quantity字段、查询2025年上半年各产品实际产量，以及根据查询结果生成报告。",
        [
            operation("解释平均值", "general_qa"),
            operation("解释项目实际产量字段", "business_knowledge"),
            operation("查询各产品实际产量", "data_analysis"),
            operation("生成报告", "result_interaction"),
        ],
    )
    add(
        "G2-08",
        "单个复杂查询的对照",
        "筛选2025年上半年的采购订单，按供应商分组统计订单数，按数量降序取前5名。",
        [operation("供应商订单数前5名查询", "data_analysis")],
        notes=["筛选、分组和排序共同定义一项查询。"],
    )

    def full(label, category, action, key, refs):
        return operation(
            label,
            category,
            action=action,
            request_id=key,
            refs=refs,
            alias=key if key.startswith("$") else None,
        )

    add(
        "G3-01",
        "新会话创建查询",
        "查询2025年上半年各产品的实际产量，按实际完工日期入期。",
        [full("新产品产量查询", "data_analysis", "create_task", "$new_query", [])],
    )
    add(
        "G3-02",
        "已有相近工作时明确另建",
        "另外新建一项独立查询，统计2024年上半年各产品实际产量，按实际完工日期入期。",
        [full("独立的2024产品查询", "data_analysis", "create_task", "$new_query", [])],
        "product",
        ["局部ID与已有req_101不同。"],
    )
    add(
        "G3-03",
        "修改唯一的既有查询",
        "把刚才产品产量统计的年份改为2024年，其他要求沿用。",
        [full("修改产品产量年份", "data_analysis", "update_task", "req_101", [])],
        "product",
        ["旧结果用于识别目标，年份修改需按新条件求值，未要求消费旧结果。"],
    )
    add(
        "G3-04",
        "定位较早的产品工作",
        "只把产品产量统计改为2024年上半年，供应商订单统计保留原要求。",
        [full("修改较早的产品统计", "data_analysis", "update_task", "req_101", [])],
        "product_and_supplier",
        ["最近活跃req_103指向供应商工作，本题目标为req_101。"],
    )
    add(
        "G3-05",
        "使用指定旧图修改样式",
        "以产品产量图result_201为修改依据，把这张图改为横向条形图，沿用原来的数据和排序。",
        [
            full(
                "修改产品图方向",
                "result_interaction",
                "update_task",
                "req_102",
                [artifact_ref("result_201")],
            )
        ],
        "two_charts",
    )
    add(
        "G3-06",
        "使用指定旧报告修改表述",
        "修改原来的产品产量报告，以result_401为依据，将文字压缩为三段，保留已有数据和结论。",
        [
            full(
                "压缩已有报告",
                "result_interaction",
                "update_task",
                "req_104",
                [artifact_ref("result_401")],
            )
        ],
        "report",
    )
    add(
        "G3-07",
        "从固定历史数据另建报告",
        "固定使用result_101这份历史数据，另外创建一份报告，原统计工作保留。",
        [
            full(
                "基于历史表另建报告",
                "result_interaction",
                "create_task",
                "$new_report",
                [artifact_ref("result_101")],
            )
        ],
        "product",
    )
    add(
        "G3-08",
        "已有图表的新解读工作",
        "新建一项解读工作，解释图表result_201中各产品产量的差异。",
        [
            full(
                "解读指定图表",
                "result_interaction",
                "create_task",
                "$new_explanation",
                [artifact_ref("result_201")],
            )
        ],
        "product_chart",
    )
    add(
        "G3-09",
        "历史产品集合作为新查询输入",
        "以result_102列出的产品ID为范围，另外查询这些产品2024年上半年的实际产量，按实际完工日期入期。",
        [
            full(
                "对历史产品集合新取数",
                "data_analysis",
                "create_task",
                "$new_query",
                [artifact_ref("result_102")],
            )
        ],
        "product_rows",
    )
    add(
        "G3-10",
        "引用历史文字进行知识问答",
        "依据上轮说明文字result_501，解释其中quantity_reserved字段的项目含义。",
        [
            full(
                "解释历史文字中的字段",
                "business_knowledge",
                "none",
                "$new_answer",
                [artifact_ref("result_501")],
            )
        ],
        "field_text",
    )
    add(
        "G3-11",
        "修改查询并引入固定产品集合",
        "修改原产品产量统计req_101，产品范围以result_102列出的ID为准，年份改为2024年，其他条件沿用。",
        [
            full(
                "修改产品范围和年份",
                "data_analysis",
                "update_task",
                "req_101",
                [artifact_ref("result_102")],
            )
        ],
        "product_with_selection",
    )
    add(
        "G3-12",
        "引用已有Request的命名交付",
        "新建一张柱状图，数据跟随req_101当前版本的product_totals命名交付。",
        [
            full(
                "跟随当前交付创建图表",
                "result_interaction",
                "create_task",
                "$new_chart",
                [request_ref("req_101", "product_totals")],
            )
        ],
        "product",
        ["题目明确引用Request当前交付，应保留request/output引用含义。"],
    )

    def dependency(label, category, alias, refs=()):
        return operation(label, category, alias=alias, refs=list(refs))

    add(
        "G4-01",
        "查询后绘图",
        "先查询各产品实际产量合计，将结果命名为product_totals。再基于product_totals生成柱状图。",
        [
            dependency("查询产品产量合计", "data_analysis", "$totals"),
            dependency(
                "根据合计绘图",
                "result_interaction",
                "$chart",
                [request_ref("$totals", "product_totals")],
            ),
        ],
    )
    add(
        "G4-02",
        "查询后生成报告",
        "先查询2025年上半年各供应商采购订单数，将结果命名为supplier_counts。再基于supplier_counts生成报告。",
        [
            dependency("查询供应商订单数", "data_analysis", "$counts"),
            dependency(
                "根据供应商结果生成报告",
                "result_interaction",
                "$report",
                [request_ref("$counts", "supplier_counts")],
            ),
        ],
    )
    add(
        "G4-03",
        "两项分析之间的依赖",
        "请分两项交付：第一项查询2025年上半年采购明细，命名为purchase_rows。第二项仅根据purchase_rows计算每月采购金额合计，命名为monthly_amounts。",
        [
            dependency("查询采购明细", "data_analysis", "$rows"),
            dependency(
                "基于明细计算月合计",
                "data_analysis",
                "$amounts",
                [request_ref("$rows", "purchase_rows")],
            ),
        ],
    )
    add(
        "G4-04",
        "一个上游供两项交付使用",
        "查询各产品实际产量合计，命名为product_totals。基于product_totals分别创建柱状图和简短报告。",
        [
            dependency("查询产品产量合计", "data_analysis", "$totals"),
            dependency(
                "创建产量图",
                "result_interaction",
                "$chart",
                [request_ref("$totals", "product_totals")],
            ),
            dependency(
                "创建产量报告",
                "result_interaction",
                "$report",
                [request_ref("$totals", "product_totals")],
            ),
        ],
    )
    add(
        "G4-05",
        "独立问答与查询绘图并存",
        "解释平均值。另外查询每月采购订单数，结果命名为monthly_counts。再基于monthly_counts绘制折线图。",
        [
            dependency("解释平均值", "general_qa", "$answer"),
            dependency("查询月度订单数", "data_analysis", "$counts"),
            dependency(
                "绘制月度折线图",
                "result_interaction",
                "$chart",
                [request_ref("$counts", "monthly_counts")],
            ),
        ],
    )
    add(
        "G4-06",
        "两套查询分别供两张图使用",
        "查询各产品类别数量，命名为category_counts。查询每月采购订单数，命名为monthly_counts。分别基于category_counts画饼图、基于monthly_counts画折线图。",
        [
            dependency("查询产品类别数量", "data_analysis", "$categories"),
            dependency("查询月度订单数", "data_analysis", "$months"),
            dependency(
                "类别饼图",
                "result_interaction",
                "$pie",
                [request_ref("$categories", "category_counts")],
            ),
            dependency(
                "月度折线图",
                "result_interaction",
                "$line",
                [request_ref("$months", "monthly_counts")],
            ),
        ],
    )
    add(
        "G4-07",
        "三级结果依赖",
        "第一项查询产品名称、售价和标准成本，命名为product_prices。第二项基于product_prices另做售价减标准成本的差额表，命名为difference_table。第三项只根据difference_table生成报告。",
        [
            dependency("查询产品价格", "data_analysis", "$prices"),
            dependency(
                "制作价格差额表",
                "result_interaction",
                "$differences",
                [request_ref("$prices", "product_prices")],
            ),
            dependency(
                "根据差额表生成报告",
                "result_interaction",
                "$report",
                [request_ref("$differences", "difference_table")],
            ),
        ],
    )
    add(
        "G4-08",
        "引用在后文声明的上游",
        "请创建一张基于supplier_counts的供应商订单数柱状图。其中supplier_counts由本轮另一项工作提供，这项工作查询2025年上半年各供应商采购订单数量。",
        [
            dependency(
                "生成供应商订单图",
                "result_interaction",
                "$chart",
                [request_ref("$counts", "supplier_counts")],
            ),
            dependency("查询供应商订单数", "data_analysis", "$counts"),
        ],
        notes=["允许operations按依赖顺序输出，需求与引用关系正确即可。"],
    )

    def clarify(target, subject, question, required, known=()):
        return {
            "request_id": target,
            "subject": subject,
            "question_example": question,
            "question_must_resolve": list(required),
            "already_known": list(known),
        }

    add(
        "G5-01",
        "没有历史工作时缺少修改目标",
        "改成2024年的。",
        [],
        clarifications=[
            clarify(
                None,
                "修改目标",
                "请说明要把哪项查询或分析改为2024年？",
                ["待修改的工作或查询对象"],
                ["年份已指定为2024年"],
            )
        ],
    )
    add(
        "G5-02",
        "两张图同时存在时指代不明",
        "把那张图改成折线图。",
        [],
        "two_charts",
        clarifications=[
            clarify(
                None,
                "待修改图表",
                "要修改产品产量图result_201，还是供应商订单图result_202？",
                ["在两张候选图中确认目标"],
                ["目标图形已指定为折线图"],
            )
        ],
    )
    add(
        "G5-03",
        "目标明确但缺少新时间范围",
        "把刚才产品产量统计的时间范围改一下。",
        [],
        "product",
        clarifications=[
            clarify(
                "req_101",
                "新的统计时间范围",
                "要将产品产量统计改为哪个时间范围？",
                ["新的年份、日期区间或季度"],
                ["目标为产品统计req_101"],
            )
        ],
    )
    add(
        "G5-04",
        "明确问答与含糊修改并存",
        "先解释平均值，再把刚才那张产品产量图改一种样式。",
        [],
        "product_chart",
        notes=["本组只评价clarifications，基础问答操作不计入评分。"],
        clarifications=[
            clarify(
                "req_102",
                "新的图表样式",
                "希望将产品产量图改成哪种图形或样式？",
                ["希望采用的新图形类型或样式"],
                ["目标为产品产量图req_102/result_201"],
            )
        ],
    )
    add(
        "G5-05",
        "查询对象明确留待补充",
        "查询指定供应商在2025年上半年的采购订单数量，供应商我还没有选定。",
        [],
        clarifications=[
            clarify(
                None,
                "供应商选择",
                "请提供要查询的供应商名称或supplier_id。",
                ["供应商名称或ID"],
                ["时间为2025年上半年", "查询内容为采购订单数量"],
            )
        ],
    )
    add(
        "G5-06",
        "两个统计工作之间目标不明",
        "把统计改为按月展示。",
        [],
        "product_and_supplier",
        clarifications=[
            clarify(
                None,
                "待修改统计",
                "要修改产品产量统计，还是供应商采购订单统计？",
                ["在两个已有统计工作中确定目标"],
                ["新分组方式为按月"],
            )
        ],
    )
    add(
        "G5-07",
        "信息充分的修改对照",
        "将产品产量统计年份改成2024年，其他条件沿用。",
        [],
        "product",
        notes=["预期clarifications为空，现有上下文已提供修改目标。"],
        clarifications=[],
    )
    add(
        "G5-08",
        "信息充分的基础问答对照",
        "解释算术平均值的含义。",
        [],
        notes=["预期clarifications为空，通用概念问答已具备必要信息。"],
        clarifications=[],
    )
    assert Counter(case["group"] for case in cases) == {
        "G1": 16,
        "G2": 8,
        "G3": 12,
        "G4": 8,
        "G5": 8,
    }
    assert len(cases) == len({case["case_id"] for case in cases}) == 52
    for case in cases:
        if case["group"] == "G5":
            del case["expected"]["operations"]
    return cases


def validate(corpus):
    categories = {
        "general_qa",
        "business_knowledge",
        "data_analysis",
        "result_interaction",
    }
    for case in corpus["cases"]:
        assert case["context_id"] in corpus["contexts"]
        expected = case["expected"]
        context = corpus["contexts"][case["context_id"]]["context"]
        existing = {item["request_id"]: item for item in context["candidate_requests"]}
        artifacts = {
            item["artifact_id"]: item for item in context["candidate_artifacts"]
        }
        operations = expected.get("operations", [])
        aliases = {item["alias"] for item in operations if "alias" in item}
        assert len(aliases) == sum("alias" in item for item in operations)
        for item in operations:
            match = item["match"]
            assert match["category"] in categories
            if case["group"] in ("G1", "G2"):
                assert set(match) == {"category"}
            elif case["group"] == "G3":
                assert set(match) == {"category", "action", "refs", "request_id"}
            elif case["group"] == "G4":
                assert set(match) == {"category", "refs"}
            if "action" in match:
                assert (match["action"] == "none") == (
                    match["category"] in {"general_qa", "business_knowledge"}
                )
                key = match["request_id"]
                if match["action"] == "update_task":
                    assert key in existing
                else:
                    assert key in aliases and key not in existing
            for ref in match.get("refs", []):
                if ref["kind"] == "artifact":
                    assert ref["id"] in artifacts and "output" not in ref
                else:
                    assert ref["kind"] == "request" and ref.get("output")
                    if ref["id"].startswith("$"):
                        assert ref["id"] in aliases and ref["id"] != item.get("alias")
                        assert ref["output"] in case["query"]
                    else:
                        assert ref["id"] in existing
                        assert ref["output"] in {
                            out["name"]
                            for out in existing[ref["id"]]["spec"]["outputs"]
                        }
        for clarification in expected.get("clarifications", []):
            assert (
                clarification["request_id"] is None
                or clarification["request_id"] in existing
            )
            assert (
                clarification["question_must_resolve"]
                and clarification["question_example"]
            )


def render(corpus):
    lines = [
        "# 意图识别测试集",
        "",
        "共52个用例，按已确定的五个测试组组织。每个用例给出输入、固定前置上下文、评价字段和预期字段约束。",
        "",
        "## 分组与评分范围",
        "",
        "| 测试组 | 用例数 | 评价字段 |",
        "| --- | --- | --- |",
    ]
    counts = Counter(case["group"] for case in corpus["cases"])
    for group, details in GROUPS.items():
        lines.append(
            f"| {group} {details['name']} | {counts[group]} | {'、'.join(details['fields'])} |"
        )
    lines += [
        "",
        "## 判定约定",
        "",
        "- 每个用例独立开始，将context_id对应的上下文提供给意图识别阶段，并将query作为本轮输入。完整上下文保存在 [cases.json](../../tests/intent/cases.json)。",
        "- 仅评价各组指定的字段。G1和G2的预期对象只列category，G4只列category和refs，其他字段由模型正常输出。",
        "- match是用于核对的字段片段，requirement和alias为用例标注。模型按项目Schema输出完整Intent，验收者再提取本组字段。",
        "- 如调用get_request_state等工具，测试工具应读取同一固定快照并返回对应对象。上下文ID与工具可读取的对象需保持一致。",
        "- G2按拆出的独立要求数量及各项类别判断，重复类别保留。筛选、分组、排序可以共同定义一项查询，G2-08用于核对这条边界。",
        "- operations可按有效依赖顺序排列。通过requirement的含义对齐各项要求后比较，指令措辞与非评价字段不计入本组分数。",
        "- $开头的名称是测试用的局部操作别名。模型可自由选择唯一局部request_id，验收时建立别名映射，再核对引用指向。",
        "- G3中update_task的request_id就是目标ID，需精确等于上下文中的已有Request ID。新建或none使用新局部ID。",
        "- req_101等身份及result_101等产物均为测试专用上下文中的固定ID。实际接入测试时需加载这些快照或等价映射后的身份。",
        "- refs按引用集合比较，顺序可变。artifact引用通过id定位，request引用还需output命名交付。上下文中的fixture_output_name用于说明产物与命名交付的对应。",
        "- G3的查询条件修改用例通过request_id定位工作。只有明确消费已有产物或上游交付时，预期refs才包含相应来源。",
        "- G4中的命名交付由query明确给出。将本轮上游操作的实际局部ID映射到$别名后核对依赖，尚待生成的本轮结果使用request引用。",
        "- G5只评价clarifications。question_example给出一种合格问法，按question_must_resolve中的信息缺口判定，允许同义表达与合理合并提问。",
        "- G5中subject采用同义表达即可，目标已知时request_id应指向该工作，目标尚未确定时为null。clarifications为空的对照用例用于检查过度澄清。",
        "- already_known列出上下文已经提供的信息。合格澄清应补足缺口，避免重复询问这些信息。",
        "- 基础问答与企业知识以适用范围区分。明确通用语境采用general_qa，明确项目字段或业务语义采用business_knowledge。",
        "- 本测试集默认将省略范围、且命中当前项目术语的问法标注为business_knowledge。G1-03、G1-04、G1-06形成相关对照。",
        "- 业务指标的公式审核与数据可用性由后续语义处理支持。G5集中覆盖目标、指代、修改值和明确缺少的查询对象。",
        "",
        "## 固定上下文",
        "",
    ]
    for key, fixture in corpus["contexts"].items():
        context = fixture["context"]
        lines += [f"### {key}", "", fixture["description"], ""]
        if context["candidate_requests"]:
            lines += ["| Request ID | 当前工作 | 命名交付 |", "| --- | --- | --- |"]
            for item in context["candidate_requests"]:
                outputs = "、".join(out["name"] for out in item["spec"]["outputs"])
                lines.append(f"| {item['request_id']} | {item['title']} | {outputs} |")
            lines += [
                "",
                "| Artifact ID | 类型 | 所属Request | 内容 |",
                "| --- | --- | --- | --- |",
            ]
            for item in context["candidate_artifacts"]:
                lines.append(
                    f"| {item['artifact_id']} | {item['content_type']} | {item['request_id']} | {item['summary']} |"
                )
            lines.append("")
    lines += ["## 用例目录", "", "| 编号 | 组别 | 用例 |", "| --- | --- | --- |"]
    lines += [
        f"| {case['case_id']} | {GROUPS[case['group']]['name']} | {case['title']} |"
        for case in corpus["cases"]
    ]
    for case in corpus["cases"]:
        lines += [
            "",
            f"## {case['case_id']} {case['title']}",
            "",
            f"前置上下文为 `{case['context_id']}`。",
            "",
            "评价字段为 " + "、".join(case["evaluation_fields"]) + "。",
            "",
            "**输入**",
            "",
            "> " + case["query"],
            "",
            "**预期结果**",
            "",
        ]
        if case["group"] == "G5":
            clarifications = case["expected"]["clarifications"]
            if not clarifications:
                lines += ["`clarifications = []`。", ""]
            for item in clarifications:
                target = item["request_id"] or "null"
                lines += [
                    f"- request_id 为 `{target}`。",
                    f"- subject表达“{item['subject']}”。",
                    "- 需要补足的信息为"
                    + "、".join(item["question_must_resolve"])
                    + "。",
                    "- 合格问法示例为“" + item["question_example"] + "”。",
                ]
                if item["already_known"]:
                    lines += [
                        "- 已知信息包括" + "、".join(item["already_known"]) + "。"
                    ]
                lines.append("")
        else:
            if case["group"] == "G2":
                lines += [f"识别出{case['expected']['operation_count']}项要求。", ""]
            for index, operation in enumerate(case["expected"]["operations"], 1):
                lines += [f"第{index}项为“{operation['requirement']}”。", ""]
                if "alias" in operation:
                    lines += [f"本项测试别名为 `{operation['alias']}`。", ""]
                lines += [
                    "```json",
                    json.dumps(operation["match"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
        if case["judging_notes"]:
            lines += [
                "判定补充",
                "",
                *["- " + note for note in case["judging_notes"]],
                "",
            ]
    lines += [
        "## 文件使用",
        "",
        "- cases.json中的contexts提供固定输入上下文，cases提供query和分组评价约束。",
        "- `python tests/intent/build_cases.py`可重新生成本文件和JSON，并校验引用可达性、目标归属、字段评分范围及组内数量。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def main():
    sources = [
        "config/semantic/catalog.yaml",
        "config/relations.json",
        "docs/reference/电子供应链数据表说明.md",
    ]
    sources += [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "data" / "seed").glob("*.csv"))
    ]
    corpus = {
        "schema_version": 1,
        "purpose": "意图识别分组测试的输入与期望字段",
        "category_values": [
            "general_qa",
            "business_knowledge",
            "data_analysis",
            "result_interaction",
        ],
        "groups": GROUPS,
        "conventions": {
            "unspecified_project_terms": "business_knowledge",
            "target_field": "operations[].request_id when action=update_task",
            "new_ids": "$别名允许映射为任意唯一的新局部ID",
            "refs": "集合比较，request引用需output，artifact仅用id",
            "clarification_question": "按信息缺口及问题含义判断，示例措辞可变",
            "fixture_ids": "上下文专用模拟身份，供模型定位，未写入业务账本",
        },
        "source_manifest": [
            {
                "file": name,
                "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest(),
            }
            for name in sources
        ],
        "contexts": build_contexts(),
        "cases": build_cases(),
    }
    validate(corpus)
    OUTPUT.write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENT.write_text(render(corpus), encoding="utf-8")
    print(
        json.dumps(
            {
                "cases": len(corpus["cases"]),
                "groups": dict(Counter(case["group"] for case in corpus["cases"])),
                "contexts": len(corpus["contexts"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
