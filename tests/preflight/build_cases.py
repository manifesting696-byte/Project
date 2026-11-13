"""【离线】从 CSV 生成黑盒语料 cases.json 与题面文档，不调用 Agent。"""

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "seed"
CORPUS = Path(__file__).with_name("cases.json")
DOCUMENT = ROOT / "docs" / "testing" / "Agent黑盒测试集.md"


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def day(value):
    return datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date()


def read_tables():
    schema = (ROOT / "data" / "schema.sql").read_text(encoding="utf-8-sig")
    types = {}
    for name, body in re.findall(r'CREATE TABLE "(\w+)" \((.*?)\);', schema, re.S):
        types[name] = dict(
            re.findall(r'"(\w+)" (INTEGER|NUMERIC|TEXT|DATE|TIMESTAMP)', body)
        )
    tables, manifest = {}, []
    for path in sorted(SEED.glob("*.csv"), key=lambda item: item.name.casefold()):
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
            fields = reader.fieldnames
        assert rows and all(value != "" for row in rows for value in row.values())
        for row in rows:
            for field, value in row.items():
                kind = types[path.stem][field]
                if kind == "INTEGER":
                    row[field] = int(value)
                elif kind == "NUMERIC":
                    row[field] = Decimal(value)
                elif kind == "DATE":
                    row[field] = day(value)
        assert len({row[fields[0]] for row in rows}) == len(rows)
        tables[path.stem] = rows
        manifest.append(
            {
                "file": path.relative_to(ROOT).as_posix(),
                "rows": len(rows),
                "columns": fields,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    assert len(tables) == 16 and sum(len(rows) for rows in tables.values()) == 62077
    return tables, manifest


def table(label, rows, fields, ordered=True, notes=()):
    return {
        "type": "table",
        "label": label,
        "columns": fields,
        "row_count": len(rows),
        "ordered": ordered,
        "reference_notes": list(notes),
        "rows": [{field: row[field] for field in fields} for row in rows],
    }


def text(label, facts, limitations=()):
    return {
        "type": "text",
        "label": label,
        "required_facts": facts,
        "limitations": list(limitations),
    }


def chart(label, data, kind, category, value, orientation=None):
    result = {
        "type": "chart",
        "label": label,
        "chart_type": kind,
        "category_field": category,
        "value_field": value,
        "data": data["rows"],
        "point_count": data["row_count"],
        "checks": [
            "类别标签与数值逐项对应",
            "展示全部预期数据点",
            "数值轴表达真实量级",
        ],
    }
    if orientation:
        result["orientation"] = orientation
    if kind == "pie":
        total = sum(Decimal(str(row[value])) for row in data["rows"])
        result["percentages"] = [
            {
                category: row[category],
                "percentage": money(Decimal(str(row[value])) * 100 / total),
            }
            for row in data["rows"]
        ]
    return result


def turn(query, *outputs, checks=(), outcome="answer"):
    return {
        "input": query,
        "expected": {
            "outcome": outcome,
            "outputs": list(outputs),
            "checks": list(checks),
        },
    }


def build_cases(tables):
    semantic = (ROOT / "config" / "semantic" / "catalog.yaml").read_text(
        encoding="utf-8-sig"
    )
    for metric in (
        "on_time_rate",
        "average_delay_days",
        "rejection_rate",
        "shipment_cohort_rejection_rate",
    ):
        assert re.search(
            r"^  " + metric + r":\n    status: candidate$", semantic, re.M
        ), metric
    assert "grain: SupplierShipments.shipment_id" in semantic
    assert "date: SupplierShipments.expected_arrival_date" in semantic
    products = {row["product_id"]: row for row in tables["Products"]}
    materials = {row["material_id"]: row for row in tables["Materials"]}
    suppliers = {row["supplier_id"]: row for row in tables["Suppliers"]}
    orders = {row["po_id"]: row for row in tables["PurchaseOrders"]}
    productions = {row["production_id"]: row for row in tables["Production"]}
    boms = {row["bom_id"]: row for row in tables["ProductBOM"]}
    shipments = {row["shipment_id"]: row for row in tables["SupplierShipments"]}
    shipment_details = {
        row["shipment_detail_id"]: row for row in tables["SupplierShipmentDetails"]
    }
    receipts = {row["gr_id"]: row for row in tables["GoodsReceipts"]}
    start, end = date(2025, 1, 1), date(2025, 7, 1)
    cases = []

    def add(key, category, title, sources, method, *turns):
        cases.append(
            {
                "case_id": key,
                "category": category,
                "title": title,
                "source_files": ["data/seed/" + source + ".csv" for source in sources],
                "reference_method": method,
                "turns": list(turns),
            }
        )

    def product_rows(limit=5, category=None, descending_price=False):
        rows = list(products.values())
        if category:
            rows = [row for row in rows if row["product_category"] == category]
        rows.sort(
            key=(lambda row: (-row["selling_price"], row["product_id"]))
            if descending_price
            else (lambda row: row["product_id"])
        )
        return rows[:limit]

    product_fields = [
        "product_id",
        "product_code",
        "product_name",
        "product_category",
        "standard_cost",
        "selling_price",
    ]
    products_query = "按 product_id 升序列出前5个产品，显示产品ID、编码、名称、类别、标准成本和销售价格。"
    products_five = table("前5个产品", product_rows(), product_fields)
    supplier_fields = ["supplier_id", "supplier_code", "supplier_name"]
    suppliers_three = table(
        "前3个供应商",
        sorted(suppliers.values(), key=lambda row: row["supplier_id"])[:3],
        supplier_fields,
    )

    def supplier_counts(lower, upper, limit=5):
        counts = Counter(
            row["supplier_id"]
            for row in orders.values()
            if lower <= row["order_date"] < upper
        )
        rows = [
            {
                "supplier_id": key,
                "supplier_name": suppliers[key]["supplier_name"],
                "order_count": value,
            }
            for key, value in counts.items()
        ]
        rows.sort(key=lambda row: (-row["order_count"], row["supplier_id"]))
        return table(
            "供应商采购订单数量排名",
            rows[:limit],
            ["supplier_id", "supplier_name", "order_count"],
        )

    supplier_query = "按 PurchaseOrders.order_date 统计2025年上半年各供应商的采购订单数量，数量降序、并列时 supplier_id 升序，取前5名，显示供应商ID、名称和订单数量。"
    supplier_top = supplier_counts(start, end)

    def monthly_orders(year):
        counts = Counter(
            row["order_date"].strftime("%Y-%m")
            for row in orders.values()
            if date(year, 1, 1) <= row["order_date"] < date(year, 7, 1)
        )
        return table(
            "上半年每月采购订单数量",
            [
                {
                    "month": f"{year}-{month:02d}",
                    "order_count": counts[f"{year}-{month:02d}"],
                }
                for month in range(1, 7)
            ],
            ["month", "order_count"],
        )

    monthly_query = "按下单日期统计2025年上半年每月采购订单数量，显示月份和订单数量，月份用 YYYY-MM 表示并升序排列，缺失月份补0。"
    monthly = monthly_orders(2025)

    def production_totals(lower, upper):
        totals = defaultdict(int)
        for row in productions.values():
            if lower <= row["actual_end_date"] < upper:
                totals[row["product_id"]] += row["production_quantity"]
        rows = [
            {
                "product_id": key,
                "product_name": products[key]["product_name"],
                "production_quantity": value,
            }
            for key, value in totals.items()
        ]
        rows.sort(key=lambda row: (-row["production_quantity"], row["product_id"]))
        return table(
            "实际产量前5名产品",
            rows[:5],
            ["product_id", "product_name", "production_quantity"],
        )

    production_query = "以 Production.actual_end_date 为时间条件，统计2025年上半年各产品的 production_quantity 合计，按合计降序、并列时 product_id 升序取前5名，显示产品ID、名称和实际产量合计。"
    production_top = production_totals(start, end)

    def available_stock(warehouse_id):
        rows = [
            {
                "material_id": row["material_id"],
                "material_name": materials[row["material_id"]]["material_name"],
                "quantity_on_hand": money(row["quantity_on_hand"]),
                "quantity_reserved": money(row["quantity_reserved"]),
                "available_quantity": money(
                    row["quantity_on_hand"] - row["quantity_reserved"]
                ),
            }
            for row in tables["RawMaterialInventory"]
            if row["warehouse_id"] == warehouse_id
        ]
        rows.sort(key=lambda row: (row["available_quantity"], row["material_id"]))
        return table(
            "仓库可用数量最低的5种材料",
            rows[:5],
            [
                "material_id",
                "material_name",
                "quantity_on_hand",
                "quantity_reserved",
                "available_quantity",
            ],
        )

    stock_query = "按库存快照查询 warehouse_id=1 的原材料，暂将可用数量定义为 quantity_on_hand 减 quantity_reserved，按可用数量升序、并列时 material_id 升序取5项，显示材料ID、名称、现存量、预留量和可用数量，数量保留两位小数。"
    stock_one = available_stock(1)
    category_counts = Counter(row["product_category"] for row in products.values())
    categories = table(
        "产品类别数量",
        [
            {"product_category": key, "product_count": value}
            for key, value in sorted(category_counts.items())
        ],
        ["product_category", "product_count"],
    )
    category_query = (
        "统计每个 product_category 的产品数量，按类别英文名升序，显示类别和产品数量。"
    )
    average_definition = text(
        "平均值解释",
        ["算术平均值等于数值总和除以数值个数。"],
    )
    production_meanings = text(
        "实际产量与计划量",
        [
            "planned_quantity 是生产工单计划数量。",
            "production_quantity 是生产工单记录的实际生产数量。",
        ],
        [
            "原文件未确认 production_quantity 是否包含 rejected_quantity，解释实际产量时需保留此口径限制。"
        ],
    )
    currency = "金额的币种和含税口径未由源文件提供。"

    add(
        "B01",
        "基础问答",
        "问析助手身份与能力",
        [],
        "依据项目功能说明及 stages/execute/agents/capabilities.py 中的能力描述。",
        turn(
            "请介绍你在这个供应链数据项目中的用途。",
            text(
                "系统能力",
                [
                    "面向企业供应链数据问答与分析的智能助手。",
                    "支持查询数据、统计分析、表格或图表展示、报告生成以及基于已有结果继续提问。",
                    "能力涉及采购、供应商发货与收货、来料检验、生产和库存。",
                ],
                ["底层模型厂商和具体模型型号应以可确认的运行配置为依据。"],
            ),
        ),
    )
    first_prices = [products[key]["selling_price"] for key in (1, 2, 3)]
    add(
        "B02",
        "基础问答",
        "用项目价格示例计算平均值",
        ["Products"],
        "取 product_id=1、2、3 的 selling_price，Decimal 求和除以3。",
        turn(
            f"给定三个产品的售价数值 {first_prices[0]}、{first_prices[1]}、{first_prices[2]}，这三个数的算术平均值是多少？保留两位小数。",
            table(
                "计算结果",
                [
                    {
                        "mean": money(sum(first_prices) / 3),
                    }
                ],
                ["mean"],
            ),
        ),
    )
    add(
        "B03",
        "基础问答",
        "电子产品BOM概念",
        ["ProductBOM", "Products", "Materials"],
        "以真实产品、材料和 ProductBOM 的数量配置作为概念依据。",
        turn(
            "电子产品装配中的BOM是什么，它在物料需求分析中有什么作用？",
            text(
                "BOM概念",
                [
                    "BOM 是 Bill of Materials，即物料清单。",
                    "描述产品所需的材料或零部件及其配置数量。",
                    "可以结合计划生产数量计算按配置数量得到的物料需求。",
                ],
                ["本项目BOM文件未另列损耗率或版本生效条件。"],
            ),
        ),
    )
    add(
        "B04",
        "基础问答",
        "现存量与预留量概念",
        ["RawMaterialInventory"],
        "依据库存字段含义，解释给定的示例计算约定。",
        turn(
            "库存分析中现存量与预留量有什么区别？如果暂按现存量减预留量表示可用量，应怎样理解这个计算？",
            text(
                "库存概念",
                [
                    "现存量表示库存记录中现有的数量。",
                    "预留量表示记录中已预留的数量。",
                    "按题设约定，可用量等于现存量减预留量。",
                ],
                [
                    "该差值是本题的计算约定，实际业务是否可直接出库还取决于已确认的规则。"
                ],
            ),
        ),
    )
    order_one_lines = sum(row["po_id"] == 1 for row in tables["PurchaseOrderDetails"])
    assert order_one_lines == 3
    add(
        "B05",
        "基础问答",
        "关联后订单重复计数",
        ["PurchaseOrders", "PurchaseOrderDetails"],
        "核实 po_id=1 有3条明细，以一对多关联解释计数粒度。",
        turn(
            "一张采购订单有3条明细。关联订单表和明细表后，为什么不能直接把这3行算成3张订单？应该怎样统计订单数？",
            text(
                "计数粒度",
                [
                    "一张订单关联3条明细后会展开成3行，行数属于明细粒度。",
                    "这仍然是1张订单、3条明细。",
                    "订单数可在订单单头表统计，或对订单标识去重计数。",
                ],
            ),
        ),
    )
    add(
        "B06",
        "基础问答",
        "库存快照与时间趋势",
        ["RawMaterialInventory", "FinishedGoodsInventory"],
        "库存两个文件均只含2025-12-31 18:00:00这一更新时间。",
        turn(
            "只有某一时点的库存快照时，库存快照和逐月库存变化趋势有什么区别？",
            text(
                "快照与趋势",
                [
                    "库存快照描述特定时点的库存状态。",
                    "逐月变化趋势需要多个时间点的数据。",
                    "单个时点的库存数量无法确定之前各月的库存变化。",
                ],
            ),
        ),
    )

    all_names = sorted(tables)
    add(
        "K01",
        "企业知识",
        "项目数据表目录",
        all_names,
        "逐一读取16个CSV文件名，按文件名去掉扩展名列出表。",
        turn(
            "这个项目当前有哪些数据表？请列出全部表名并简要说明业务范围。",
            text(
                "完整表目录",
                [
                    "当前数据集包含16张表。",
                    "表名完整集合为 " + "、".join(all_names) + "。",
                    "范围覆盖基础资料、BOM、采购、发货、收货、质检、生产及库存。",
                ],
            ),
        ),
    )
    product_definitions = {
        "product_id": "产品标识",
        "product_code": "产品编码",
        "product_name": "产品名称",
        "product_category": "产品类别",
        "uom": "计量单位",
        "standard_cost": "标准成本",
        "selling_price": "销售价格",
        "status": "状态",
    }
    add(
        "K02",
        "企业知识",
        "产品表字段含义",
        ["Products"],
        "核对数据字典中 Products 八个字段的中文含义。",
        turn(
            "介绍Products表各字段的含义。",
            text(
                "产品字段含义",
                [f"{field}：{meaning}" for field, meaning in product_definitions.items()],
                ["字段共 8 个，字段名与中文含义需一一对应。"],
            ),
        ),
    )
    add(
        "K03",
        "企业知识",
        "生产数量字段及口径限制",
        ["Production"],
        "读取生产工单字段说明，不推导未确认的数量包含关系。",
        turn(
            "Production里的planned_quantity、production_quantity和rejected_quantity分别表示什么？production_quantity是否已经扣除了rejected_quantity？",
            production_meanings,
            text(
                "拒收数量与包含关系",
                [
                    "rejected_quantity 是工单记录的拒收数量。",
                    "源文件未明确 production_quantity 是否包含 rejected_quantity，也未确认其是否已扣除拒收数量。",
                ],
            ),
        ),
    )
    receipt_relation = text(
        "采购单头到收货单的关系",
        [
            "PurchaseOrders.po_id 关联 SupplierShipments.po_id。",
            "SupplierShipments.shipment_id 关联 GoodsReceipts.shipment_id。",
            "从采购单头到收货单的路径为 PurchaseOrders → SupplierShipments → GoodsReceipts。",
            "GoodsReceipts 的单头标识为 gr_id，收货日期字段为 receipt_date。",
        ],
    )
    add(
        "K04",
        "企业知识",
        "采购订单到收货单关系",
        ["PurchaseOrders", "SupplierShipments", "GoodsReceipts"],
        "核对 config/relations.json 以及三个CSV的关系键。",
        turn(
            "从采购订单单头找到对应收货单，应该经过哪些表，使用哪些关联字段？",
            receipt_relation,
        ),
    )
    add(
        "K05",
        "企业知识",
        "供应商地域字段粒度",
        ["Suppliers"],
        "核对数据字典中 Suppliers.country、city、address 的字段含义与记录粒度。",
        turn(
            "Suppliers表的country、city、address三个字段分别是什么粒度？如果按地域统计供应商分布，应该用哪个字段，为什么不能直接用address？",
            text(
                "地域字段粒度",
                [
                    "country 是国家或地区级字段，粒度最粗，用于跨国家汇总。",
                    "city 是城市或行政区级字段，每个供应商一个城市，可按城市分组统计供应商数量。",
                    "address 是街道级地址文本，粒度细于 city，同一城市对应多条不同地址，只适合定位与核对。",
                    "按地域统计供应商分布应使用 city：country 粒度过粗，address 会把同一城市的供应商拆散。",
                ],
                ["address 不是规范化分组键，地域统计口径以 city 为准。"],
            ),
        ),
    )
    add(
        "K06",
        "企业知识",
        "候选到货准时率定义",
        ["SupplierShipments"],
        "核对config/semantic/catalog.yaml的on_time_rate定义与candidate状态。",
        turn(
            "项目中的到货准时率已经有正式发布的口径了吗？如果还处于候选阶段，请说明候选定义的记录粒度、入期日期和判定条件。",
            text(
                "指标状态和候选口径",
                [
                    "on_time_rate / 到货准时率当前状态为 candidate，演示口径待业务确认。",
                    "候选粒度为 SupplierShipments.shipment_id，即发货单。",
                    "按 expected_arrival_date 预计到货日入期。",
                    "候选准时条件为 actual_arrival_date 不晚于 expected_arrival_date。",
                    "候选分母为有效发货单数。",
                ],
                ["该候选定义的发布状态仍待业务审核确认。"],
            ),
        ),
    )
    stock_times = sorted(
        {
            row["last_updated_at"]
            for name in ("RawMaterialInventory", "FinishedGoodsInventory")
            for row in tables[name]
        }
    )
    assert stock_times == ["2025-12-31 18:00:00"]
    add(
        "K07",
        "企业知识",
        "库存数据时间覆盖",
        ["RawMaterialInventory", "FinishedGoodsInventory"],
        "提取两个库存文件的last_updated_at去重集合。",
        turn(
            "原料库存和成品库存是什么时间的数据？目前能直接得到2025年每个月的库存快照吗？",
            text(
                "库存时间",
                [
                    "两个库存文件的全部更新时间均为2025-12-31 18:00:00。",
                    "当前文件提供该时点的库存状态。",
                    "数据集缺少2025年逐月库存快照。",
                ],
            ),
        ),
    )
    add(
        "K08",
        "企业知识",
        "设备与销售数据范围",
        all_names,
        "核对16个CSV表头集合与semantic catalog的limitations。",
        turn(
            "项目当前是否包含设备故障、产线、工序和销售订单数据？能否据此直接分析设备故障导致生产延误的原因？",
            text(
                "数据范围",
                [
                    "当前数据集缺少设备故障或停机、产线、工序和销售订单数据。",
                    "可以读取生产日期和数量，但当前证据不足以认定设备故障导致了生产延误。",
                ],
            ),
        ),
    )

    add(
        "D01",
        "基础数据查询",
        "产品记录总数",
        ["Products"],
        "对CSV数据行计数。",
        turn(
            "查询Products表的产品记录总数。",
            table("产品总数", [{"product_count": len(products)}], ["product_count"]),
        ),
    )
    add(
        "D02",
        "基础数据查询",
        "产品明细与固定排序",
        ["Products"],
        "按整数product_id升序取前5条。",
        turn(products_query, products_five),
    )
    phones = sorted(
        [row for row in products.values() if row["product_category"] == "Smartphone"],
        key=lambda row: (row["selling_price"], row["product_id"]),
    )
    add(
        "D03",
        "基础数据查询",
        "类别过滤与价格排序",
        ["Products"],
        "类别原文精确匹配Smartphone，按Decimal售价、整数ID排序。",
        turn(
            "列出product_category为Smartphone的全部产品，按selling_price升序、并列时product_id升序，显示产品ID、名称、类别和售价。",
            table(
                "手机产品",
                phones,
                ["product_id", "product_name", "product_category", "selling_price"],
            ),
        ),
    )
    material_counts = Counter(row["material_category"] for row in tables["Materials"])
    material_group = table(
        "材料原始类别计数",
        [
            {"material_category": key, "material_count": value}
            for key, value in sorted(material_counts.items())
        ],
        ["material_category", "material_count"],
        ordered=False,
    )
    add(
        "D04",
        "基础数据查询",
        "类别分组计数（取值已统一）",
        ["Materials"],
        "Counter按原始字符串分组，全部记录计数合计80。",
        turn(
            "按Materials.material_category原始文本分组统计材料记录数，列出全部类别及各自记录数。",
            material_group,
            checks=["8种取值各10条", "记录数合计80"],
        ),
    )
    add(
        "D05",
        "基础数据查询",
        "供应商订单排名",
        ["PurchaseOrders", "Suppliers"],
        "在订单单头按order_date过滤后按supplier_id计数，再关联供应商名称。",
        turn(supplier_query, supplier_top),
    )
    add(
        "D06",
        "基础数据查询",
        "月度订单数量",
        ["PurchaseOrders"],
        "按order_date的YYYY-MM分组，并补齐1至6月。",
        turn(monthly_query, monthly),
    )
    add(
        "D07",
        "基础数据查询",
        "产品产量排名与日期解析",
        ["Production", "Products"],
        "按actual_end_date（ISO日期）入期，汇总production_quantity。",
        turn(production_query, production_top),
    )
    bom_rows = [
        {
            "material_id": row["material_id"],
            "material_name": materials[row["material_id"]]["material_name"],
            "quantity_required": row["quantity_required"],
            "uom": row["uom"],
        }
        for row in tables["ProductBOM"]
        if row["product_id"] == 1
    ]
    bom_rows.sort(key=lambda row: row["material_id"])
    add(
        "D08",
        "基础数据查询",
        "产品BOM明细",
        ["ProductBOM", "Materials", "Products"],
        "过滤product_id=1，以material_id关联材料，按material_id升序。",
        turn(
            "查询产品PRD-001（Nile X1 Phone）的BOM，按material_id升序显示材料ID、材料名称、quantity_required和uom。",
            table(
                "PRD-001物料清单",
                bom_rows,
                ["material_id", "material_name", "quantity_required", "uom"],
            ),
        ),
    )
    amounts = defaultdict(Decimal)
    for detail in tables["PurchaseOrderDetails"]:
        order = orders[detail["po_id"]]
        if start <= order["order_date"] < end:
            amounts[order["supplier_id"]] += detail["total_amount"]
    amount_rows = [
        {
            "supplier_id": key,
            "supplier_name": suppliers[key]["supplier_name"],
            "total_amount": money(value),
        }
        for key, value in amounts.items()
    ]
    amount_rows.sort(key=lambda row: (-row["total_amount"], row["supplier_id"]))
    purchase_amount = table(
        "采购明细金额前三名",
        amount_rows[:3],
        ["supplier_id", "supplier_name", "total_amount"],
        notes=[currency],
    )
    add(
        "D09",
        "基础数据查询",
        "采购明细金额汇总",
        ["PurchaseOrderDetails", "PurchaseOrders", "Suppliers"],
        "按订单下单日过滤，直接汇总明细total_amount，最终保留2位小数。",
        turn(
            "按订单下单日期统计2025年上半年各供应商采购明细total_amount合计，金额降序、并列时supplier_id升序取前三名，显示供应商ID、名称和金额合计，金额保留两位小数。",
            purchase_amount,
        ),
    )
    add(
        "D10",
        "基础数据查询",
        "库存差值及低值排序",
        ["RawMaterialInventory", "Materials"],
        "按题设定义用Decimal逐行相减，过滤仓库后排序。",
        turn(stock_query, stock_one),
    )
    delay_rows = []
    for row in shipments.values():
        if (
            start <= row["expected_arrival_date"] < end
            and row["actual_arrival_date"] > row["expected_arrival_date"]
        ):
            delay_rows.append(
                {
                    "shipment_id": row["shipment_id"],
                    "shipment_code": row["shipment_code"],
                    "supplier_name": suppliers[row["supplier_id"]]["supplier_name"],
                    "expected_arrival_date": row["expected_arrival_date"],
                    "actual_arrival_date": row["actual_arrival_date"],
                    "delay_days": (
                        row["actual_arrival_date"] - row["expected_arrival_date"]
                    ).days,
                }
            )
    delay_rows.sort(key=lambda row: (-row["delay_days"], row["shipment_id"]))
    add(
        "D11",
        "基础数据查询",
        "日期比较与延期天数",
        ["SupplierShipments", "Suppliers"],
        "逐发货单计算actual_arrival_date减expected_arrival_date的日数，先过滤正差值再排序。",
        turn(
            "列出预计到货日在2025年上半年、实际到货日晚于预计到货日的发货单，按两日期相差的天数降序、并列时shipment_id升序取5张，显示发货单ID、编码、供应商名称、预计到货日、实际到货日及相差天数。",
            table(
                "到货日期差值前5单",
                delay_rows[:5],
                [
                    "shipment_id",
                    "shipment_code",
                    "supplier_name",
                    "expected_arrival_date",
                    "actual_arrival_date",
                    "delay_days",
                ],
            ),
        ),
    )
    empty_production = [
        row
        for row in productions.values()
        if date(2026, 1, 1) <= row["actual_end_date"] < date(2026, 2, 1)
    ]
    assert empty_production == []
    add(
        "D12",
        "基础数据查询",
        "明确日期范围的空结果",
        ["Production"],
        "按actual_end_date筛选2026年1月，匹配行数为0。",
        turn(
            "查询实际完工日期在2026年1月的生产工单，按production_id升序列出工单ID、工单编码、产品ID和实际产量，最多5条。",
            table(
                "2026年1月完工工单",
                [],
                [
                    "production_id",
                    "production_order_code",
                    "product_id",
                    "production_quantity",
                ],
            ),
            text("空结果说明", ["该日期条件下没有匹配的生产工单。"]),
            outcome="empty_result",
        ),
    )
    received = defaultdict(lambda: [0, 0, 0])
    for detail in tables["GoodsReceiptDetails"]:
        receipt = receipts[detail["gr_id"]]
        if start <= receipt["receipt_date"] < end:
            values = received[receipt["receipt_date"].strftime("%Y-%m")]
            for index, field in enumerate(
                ["received_quantity", "accepted_quantity", "rejected_quantity"]
            ):
                values[index] += detail[field]
    received_rows = [
        {
            "month": key,
            **dict(
                zip(
                    ["received_quantity", "accepted_quantity", "rejected_quantity"],
                    value,
                )
            ),
        }
        for key, value in sorted(received.items())
    ]
    add(
        "D13",
        "基础数据查询",
        "收货数量按月汇总",
        ["GoodsReceipts", "GoodsReceiptDetails"],
        "按收货单receipt_date入期，将每条收货明细只计入一次。",
        turn(
            "按GoodsReceipts.receipt_date统计2025年上半年各月收货明细的received_quantity、accepted_quantity、rejected_quantity合计，显示月份和三种数量，按月份升序。",
            table(
                "每月收货数量",
                received_rows,
                [
                    "month",
                    "received_quantity",
                    "accepted_quantity",
                    "rejected_quantity",
                ],
            ),
        ),
    )
    quality = defaultdict(lambda: [0, 0])
    for row in tables["QualityInspections"]:
        if start <= row["inspection_date"] < end:
            quality[row["inspection_result"]][0] += 1
            quality[row["inspection_result"]][1] += row["defect_quantity"]
    quality_rows = [
        {
            "inspection_result": key,
            "inspection_count": value[0],
            "defect_quantity": value[1],
        }
        for key, value in sorted(quality.items())
    ]
    add(
        "D14",
        "基础数据查询",
        "检验结果分组统计",
        ["QualityInspections"],
        "按inspection_date筛选后分组计数并求和defect_quantity。",
        turn(
            "按inspection_date筛选2025年上半年的来料检验记录，按inspection_result分组，显示检验结果、记录数和defect_quantity合计，按检验结果英文名升序。",
            table(
                "检验结果统计",
                quality_rows,
                ["inspection_result", "inspection_count", "defect_quantity"],
            ),
        ),
    )
    material_costs = defaultdict(Decimal)
    for detail in tables["ProductionDetails"]:
        production = productions[detail["production_id"]]
        if (
            production["product_id"] == 1
            and start <= production["actual_end_date"] < end
        ):
            material_costs[boms[detail["bom_id"]]["material_id"]] += detail[
                "total_material_cost"
            ]
    cost_rows = [
        {
            "material_id": key,
            "material_name": materials[key]["material_name"],
            "total_material_cost": money(value),
        }
        for key, value in material_costs.items()
    ]
    cost_rows.sort(key=lambda row: (-row["total_material_cost"], row["material_id"]))
    add(
        "D15",
        "基础数据查询",
        "生产用料成本多表汇总",
        ["Production", "ProductionDetails", "ProductBOM", "Materials"],
        "按工单实际完工日和product_id=1过滤，再经bom_id确定材料，汇总用料明细total_material_cost。",
        turn(
            "查询产品ID为1、实际完工日在2025年上半年的工单，按材料汇总ProductionDetails.total_material_cost，金额降序、并列时material_id升序取前5名，显示材料ID、名称和成本合计，保留两位小数。",
            table(
                "产品1用料成本前5名",
                cost_rows[:5],
                ["material_id", "material_name", "total_material_cost"],
                notes=[currency],
            ),
        ),
    )
    trace_rows = []
    for detail in tables["GoodsReceiptDetails"]:
        if detail["gr_id"] == 1:
            shipment_detail = shipment_details[detail["shipment_detail_id"]]
            shipment = shipments[receipts[1]["shipment_id"]]
            trace_rows.append(
                {
                    "gr_detail_id": detail["gr_detail_id"],
                    "po_id": shipment["po_id"],
                    "po_detail_id": shipment_detail["po_detail_id"],
                    "material_id": detail["material_id"],
                    "material_name": materials[detail["material_id"]]["material_name"],
                    "received_quantity": detail["received_quantity"],
                }
            )
    trace_rows.sort(key=lambda row: row["gr_detail_id"])
    add(
        "D16",
        "基础数据查询",
        "收货明细追溯采购行",
        [
            "GoodsReceiptDetails",
            "GoodsReceipts",
            "SupplierShipmentDetails",
            "SupplierShipments",
            "Materials",
        ],
        "从gr_id=1的明细分别通过shipment_detail_id和收货单shipment_id追溯采购行及采购单。",
        turn(
            "追溯gr_id=1的收货明细，按gr_detail_id升序显示收货明细ID、采购单ID、采购明细ID、材料ID、材料名称以及GoodsReceiptDetails.received_quantity。",
            table(
                "收货单1采购追溯",
                trace_rows,
                [
                    "gr_detail_id",
                    "po_id",
                    "po_detail_id",
                    "material_id",
                    "material_name",
                    "received_quantity",
                ],
            ),
        ),
    )

    add(
        "I01",
        "结果交互",
        "订单排名柱状图",
        ["PurchaseOrders", "Suppliers"],
        "图表沿用前一轮标准表的5个名称和数量。",
        turn(supplier_query, supplier_top),
        turn(
            "把刚才的前5名结果画成竖向柱状图，横轴为供应商名称，纵轴为订单数量，顺序沿用刚才的排名。",
            chart(
                "供应商订单数量柱状图",
                supplier_top,
                "bar",
                "supplier_name",
                "order_count",
                "vertical",
            ),
        ),
    )
    add(
        "I02",
        "结果交互",
        "月度订单折线图",
        ["PurchaseOrders"],
        "折线图依次使用1至6月的6个数量。",
        turn(monthly_query, monthly),
        turn(
            "将刚才的每月采购订单数量画成折线图，横轴月份升序，纵轴订单数量，展示全部6个月。",
            chart("月度订单趋势", monthly, "line", "month", "order_count"),
        ),
    )
    differences = [
        {
            "product_name": row["product_name"],
            "standard_cost": row["standard_cost"],
            "selling_price": row["selling_price"],
            "price_cost_difference": money(row["selling_price"] - row["standard_cost"]),
        }
        for row in product_rows()
    ]
    differences.sort(
        key=lambda row: (-row["price_cost_difference"], row["product_name"])
    )
    add(
        "I03",
        "结果交互",
        "基于结果制作派生表",
        ["Products"],
        "仅对前一轮5条产品数据逐行计算售价减标准成本并排序。",
        turn(products_query, products_five),
        turn(
            "仅基于刚才这5个产品制作一张新表，显示产品名称、标准成本、售价和售价减标准成本的差额，按差额降序，金额保留两位小数。",
            table(
                "五个产品售价成本差额",
                differences,
                [
                    "product_name",
                    "standard_cost",
                    "selling_price",
                    "price_cost_difference",
                ],
                notes=[
                    currency,
                    "本题差额按售价减标准成本定义，源文件没有提供净利润口径。",
                ],
            ),
        ),
    )
    best, fifth = production_top["rows"][0], production_top["rows"][-1]
    add(
        "I04",
        "结果交互",
        "解读产量排名",
        ["Production", "Products"],
        "读取前5名表的首行与末行，直接相减。",
        turn(production_query, production_top),
        turn(
            "解读刚才这份前5名结果，说明第一名和第五名分别是谁、产量各是多少，以及第一名比第五名多多少。",
            table(
                "前5名首尾比较",
                [
                    {
                        "first_product": best["product_name"],
                        "first_quantity": best["production_quantity"],
                        "fifth_product": fifth["product_name"],
                        "fifth_quantity": fifth["production_quantity"],
                        "difference": best["production_quantity"]
                        - fifth["production_quantity"],
                    }
                ],
                [
                    "first_product",
                    "first_quantity",
                    "fifth_product",
                    "fifth_quantity",
                    "difference",
                ],
            ),
            checks=["比较对象为这份前5名表的第一名与第五名"],
        ),
    )
    add(
        "I05",
        "结果交互",
        "库存结果报告",
        ["RawMaterialInventory", "Materials"],
        "报告表格与前轮5项一致，最低值由表首行给出。",
        turn(stock_query, stock_one),
        turn(
            "把刚才5项结果整理为一份简短Markdown报告，包含统计时点、可用数量公式、完整5行表格和最低可用数量的材料说明。",
            {
                "type": "report",
                "label": "仓库1低可用量材料报告",
                "required_facts": [
                    "库存快照时点为2025-12-31 18:00:00。",
                    "可用数量按现存量减预留量计算。",
                    f"最低项为{stock_one['rows'][0]['material_name']}，可用数量{stock_one['rows'][0]['available_quantity']}。",
                ],
                "tables": [stock_one],
                "limitations": ["当前报告使用单时点快照。"],
            },
        ),
    )
    add(
        "I06",
        "结果交互",
        "产品类别饼图与占比",
        ["Products"],
        "四类各4条，以16为分母计算占比。",
        turn(category_query, categories),
        turn(
            "根据刚才的类别数量制作饼图，标出类别名、数量和百分比。",
            chart(
                "产品类别占比", categories, "pie", "product_category", "product_count"
            ),
        ),
    )

    count_output = table(
        "产品总数", [{"product_count": len(products)}], ["product_count"]
    )
    add(
        "M01",
        "多需求",
        "概念解释与数据查询",
        ["Products"],
        "概念按定义判断，产品数独立计数。",
        turn(
            "先解释算术平均值，再查询Products中的产品记录总数。",
            average_definition,
            count_output,
            checks=["概念解释和产品总数两项均有答案"],
        ),
    )
    add(
        "M02",
        "多需求",
        "两类实体的独立明细",
        ["Products", "Suppliers"],
        "分别按各自的整数ID排序取指定数量。",
        turn(
            "分别列出product_id最小的前2个产品，以及supplier_id最小的前3个供应商，两张表都显示ID、编码和名称。",
            table(
                "前2个产品",
                product_rows(2),
                ["product_id", "product_code", "product_name"],
            ),
            suppliers_three,
            checks=["产品表2行，供应商表3行，数量条件分别生效"],
        ),
    )
    add(
        "M03",
        "多需求",
        "企业关系解释与订单趋势表",
        ["PurchaseOrders", "SupplierShipments", "GoodsReceipts"],
        "关系来自登记关系，月度数量来自订单单头日期计数。",
        turn(
            "先解释采购订单单头经过哪些表关联到收货单，再按下单日期统计2025年上半年每月采购订单数量，月份升序，缺失月份补0。",
            receipt_relation,
            monthly,
        ),
    )
    add(
        "M04",
        "多需求",
        "查询类别并绘图和解释",
        ["Products"],
        "一次计数得到四类数量，图与解释使用同一结果。",
        turn(
            "统计每个产品类别的数量，按类别英文名升序列出结果，再根据这份结果画饼图，并解释各类别占全部产品的比例。",
            categories,
            chart(
                "产品类别占比", categories, "pie", "product_category", "product_count"
            ),
            text(
                "类别占比解释",
                ["共有4个类别，每类4个产品。", "总计16个产品，每个类别占25%。"],
            ),
        ),
    )
    count_one_2024 = sum(
        row["supplier_id"] == 1
        and date(2024, 1, 1) <= row["order_date"] < date(2025, 1, 1)
        for row in orders.values()
    )
    count_two_2025 = sum(
        row["supplier_id"] == 2
        and date(2025, 1, 1) <= row["order_date"] < date(2026, 1, 1)
        for row in orders.values()
    )
    add(
        "M05",
        "多需求",
        "不同供应商与年份的条件隔离",
        ["PurchaseOrders"],
        "按两个独立的supplier_id和order_date范围分别计数。",
        turn(
            "分别统计supplier_id=1在2024年下单的采购订单数量，以及supplier_id=2在2025年下单的采购订单数量，明确标出各自供应商ID、年份和订单数。",
            table(
                "独立条件统计",
                [
                    {"supplier_id": 1, "year": 2024, "order_count": count_one_2024},
                    {"supplier_id": 2, "year": 2025, "order_count": count_two_2025},
                ],
                ["supplier_id", "year", "order_count"],
                ordered=False,
            ),
        ),
    )
    add(
        "M06",
        "多需求",
        "可回答需求与缺失数据并存",
        ["Products"],
        "产品前3条可确定，设备故障数据缺失由表集合与limitations确认。",
        turn(
            "列出product_id最小的前3个产品的ID、编码和名称，同时统计2025年上半年每台设备的故障次数。",
            table(
                "前3个产品",
                product_rows(3),
                ["product_id", "product_code", "product_name"],
            ),
            text(
                "设备故障部分",
                [
                    "当前项目缺少设备及设备故障记录，无法从当前数据确定每台设备的故障次数。",
                    "完成故障统计需要补充设备标识、故障时间等设备故障数据。",
                ],
            ),
            checks=["产品列表正常交付", "设备故障部分明确说明数据缺口"],
            outcome="partial",
        ),
    )
    add(
        "M07",
        "多需求",
        "两套数据与两张图的对应",
        ["PurchaseOrders", "Products"],
        "分别计算月度订单数量与产品类别数量，逐图核对对应数据。",
        turn(
            "按下单日期统计2025年上半年每月采购订单数量并画折线图，缺失月份补0；同时统计各产品类别的产品数量并画柱状图，类别按英文名升序。请同时展示两份数据表和两张图。",
            monthly,
            chart("每月采购订单", monthly, "line", "month", "order_count"),
            categories,
            chart(
                "各类别产品数量",
                categories,
                "bar",
                "product_category",
                "product_count",
                "vertical",
            ),
            checks=["月度数据对应折线图，产品类别数据对应柱状图"],
        ),
    )
    add(
        "M08",
        "多需求",
        "已有结果绘图与字段解释",
        ["Production", "Products"],
        "先固定实际产量前5名，再绘图并按字段字典解释数量。",
        turn(production_query, production_top),
        turn(
            "把刚才的前5名画成横向条形图，顺序沿用排名，再解释Production中的实际产量与计划量有什么区别。",
            chart(
                "产量前5名横向图",
                production_top,
                "bar",
                "product_name",
                "production_quantity",
                "horizontal",
            ),
            production_meanings,
        ),
    )

    add(
        "X01",
        "跨轮修改",
        "产品条数由5改2",
        ["Products"],
        "第二轮只改变limit，保留全部输出字段和ID升序。",
        turn(products_query, products_five),
        turn(
            "产品只列前2个，其他要求沿用。",
            table("前2个产品", product_rows(2), product_fields),
            checks=["本轮产品明细为2行，字段和排序沿用前轮"],
        ),
    )
    add(
        "X02",
        "跨轮修改",
        "产量统计半年改季度",
        ["Production", "Products"],
        "将actual_end_date范围改为2025-04-01至2025-07-01，重算产品合计排名。",
        turn(production_query, production_top),
        turn(
            "将统计时间改为2025年第二季度，仍按实际完工日期筛选，其他要求沿用。",
            production_totals(date(2025, 4, 1), end),
        ),
    )
    add(
        "X03",
        "跨轮修改",
        "供应商订单排名换年份",
        ["PurchaseOrders", "Suppliers"],
        "时间范围由2025上半年改为2024上半年，保留order_date与排名规则。",
        turn(supplier_query, supplier_top),
        turn(
            "改查2024年上半年，其他要求沿用。",
            supplier_counts(date(2024, 1, 1), date(2024, 7, 1)),
        ),
    )
    add(
        "X04",
        "跨轮修改",
        "仅修改图表方向",
        ["PurchaseOrders", "Suppliers"],
        "三轮均使用2025上半年同一份5行排名，仅第三轮改变方向。",
        turn(supplier_query, supplier_top),
        turn(
            "把刚才结果画成竖向柱状图，横轴供应商名称，纵轴订单数量，顺序沿用排名。",
            chart(
                "供应商排名竖向图",
                supplier_top,
                "bar",
                "supplier_name",
                "order_count",
                "vertical",
            ),
        ),
        turn(
            "把刚才这张图改为横向条形图，数量和排名顺序沿用。",
            chart(
                "供应商排名横向图",
                supplier_top,
                "bar",
                "supplier_name",
                "order_count",
                "horizontal",
            ),
        ),
    )
    add(
        "X05",
        "跨轮修改",
        "库存查询切换仓库",
        ["RawMaterialInventory", "Materials"],
        "将warehouse_id由1改2，保留差值定义、字段、排序和5行限制。",
        turn(stock_query, stock_one),
        turn("将仓库改为warehouse_id=2，其他要求沿用。", available_stock(2)),
    )
    add(
        "X06",
        "跨轮修改",
        "多项需求中的局部修改",
        ["Products", "Suppliers"],
        "第一轮两张明细表，第二轮只更新产品列表的行数。",
        turn(
            "分别按ID升序列出前5个产品和前3个供应商，各显示ID、编码和名称。",
            table(
                "前5个产品",
                product_rows(),
                ["product_id", "product_code", "product_name"],
            ),
            suppliers_three,
        ),
        turn(
            "只把产品列表改成前3个，这轮只展示修改后的产品列表。",
            table(
                "前3个产品",
                product_rows(3),
                ["product_id", "product_code", "product_name"],
            ),
            checks=["本轮只展示3个产品的列表"],
        ),
    )
    add(
        "X07",
        "跨轮修改",
        "多轮排序与类别条件继承",
        ["Products"],
        "先ID排序5行，再全产品售价降序5行，最后按Laptop过滤并保留售价降序取3行。",
        turn(products_query, products_five),
        turn(
            "改按售价降序，售价相同时按product_id升序，仍列前5个，显示字段沿用。",
            table(
                "售价最高的5个产品", product_rows(descending_price=True), product_fields
            ),
        ),
        turn(
            "只看Laptop类别，数量改为前3个，其他要求沿用。",
            table(
                "售价最高的3个Laptop",
                product_rows(3, category="Laptop", descending_price=True),
                product_fields,
            ),
            checks=["从完整Laptop类别数据筛选后排序取3条"],
        ),
    )
    add(
        "X08",
        "跨轮修改",
        "时间范围改为空数据并补零",
        ["PurchaseOrders"],
        "将月份范围整体改为2026上半年，沿用缺失月份补0的要求。",
        turn(monthly_query, monthly),
        turn(
            "年份改成2026年，仍统计上半年，其他要求沿用。",
            monthly_orders(2026),
            text(
                "数据范围说明",
                ["2026年上半年没有匹配的采购订单记录，按补0约定展示1至6月各0单。"],
            ),
        ),
    )
    assert Counter(item["category"] for item in cases) == {
        "基础问答": 6,
        "企业知识": 8,
        "基础数据查询": 16,
        "结果交互": 6,
        "多需求": 8,
        "跨轮修改": 8,
    }
    assert len(cases) == len({item["case_id"] for item in cases}) == 52
    return cases


def json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def cell(value):
    if isinstance(value, str):
        trailing = len(value) - len(value.rstrip(" "))
        if trailing:
            value = value.rstrip(" ") + "␠" * trailing
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_table(output):
    fields = output["columns"]
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    lines += [
        "| " + " | ".join(cell(row[field]) for field in fields) + " |"
        for row in output["rows"]
    ]
    if not output["rows"]:
        lines.append("\n预期行数为0。")
    return lines


def render_output(output):
    lines = [f"**{output['label']}**", ""]
    if output["type"] == "table":
        lines += render_table(output)
    elif output["type"] == "chart":
        names = {
            "bar": "条形图",
            "line": "折线图",
            "pie": "饼图",
            "vertical": "竖向",
            "horizontal": "横向",
        }
        kind = names.get(output.get("orientation"), "") + names[output["chart_type"]]
        lines += [
            f"预期展示{kind}，共{output['point_count']}个数据点。类别为 {output['category_field']}，数值为 {output['value_field']}。",
            "",
        ]
        fields = [output["category_field"], output["value_field"]]
        lines += render_table({"columns": fields, "rows": output["data"]})
        if "percentages" in output:
            lines += ["", "各类别百分比", ""]
            lines += render_table(
                {
                    "columns": [output["category_field"], "percentage"],
                    "rows": output["percentages"],
                }
            )
        lines += ["", *["- " + value for value in output["checks"]]]
    else:
        lines += ["- " + fact for fact in output["required_facts"]]
        for data in output.get("tables", []):
            lines += ["", *render_table(data)]
    if output.get("limitations"):
        lines += ["", *["- " + value for value in output["limitations"]]]
    if output.get("reference_notes"):
        lines += [
            "",
            "参照注记",
            "",
            *["- " + value for value in output["reference_notes"]],
        ]
    return lines


def render_document(corpus):
    lines = [
        "# Agent 黑盒测试集",
        "",
        "本测试集记录用户输入及用户可见的预期结果。标准答案直接由当前CSV、数据字典和已保存业务定义整理或计算。",
        "",
        f"共 {len(corpus['cases'])} 个案例、{sum(len(item['turns']) for item in corpus['cases'])} 轮输入。六类数量依次为基础问答6、企业知识8、基础数据查询16、结果交互6、多需求8、跨轮修改8。",
        "",
        "基础数据查询额外覆盖收货数量、质检结果、生产用料成本及收货明细追溯。",
        "",
        "## 使用与判定",
        "",
        "- 每个案例在独立的新会话中开始，按案例内的轮次顺序输入。前置查询也属于案例，需先核对其结果。",
        "- 单轮包含多项要求的案例归入多需求。跨轮修改案例可以包含前置多需求，最后一轮检查对既有要求的修改。",
        "- 只比较最终可见的文字、表格、图表和报告。列名允许等义中文或英文，要求对应的数据值、对象、时间范围和排序一致。",
        "- 单个数值或简短比较结果可用准确文字表达。多行明细应完整展示预期行数和要求的字段，允许等价的可读表格形式。",
        "- 文字按列出的事实要点判断，允许同义表达。语义标签作为输出要求，多种要点可以合并在同一段文字中。表名允许采用对应的PostgreSQL小写视图名。",
        "- limitations记录事实边界，回答涉及相应判断时须符合。reference_notes是核对者的参照注记，按题意输出正确数值即可，注记文字可省略。",
        "- 整数与计数须精确相等。金额及指定小数保留两位，采用十进制四舍五入；数值比较容差为0.005。",
        "- 日期边界包含题述开始日和结束日，半年为1月1日至6月30日，第二季度为4月1日至6月30日。每题指定的日期字段决定入期。",
        "- 题目指定的并列排序键需生效；未要求顺序的表按行集合核对（顺序无关）。",
        "- 分类与日期文本已统一（`YYYY-MM-DD`），实体名称比较忽略首尾空白，名称主体须一致。",
        "- 饼图百分比采用0至100的百分数。JSON中的percentage=25.00对应25%。图表以实际图形、标签、顺序和数据对应关系判定。",
        "- 空明细可展示空表与无匹配说明。缺失月份补0仅在题目明确要求时采用。候选指标状态及缺失数据按各题所列事实判断。",
        "- JSON中的十进制数使用字符串保留精度，实际回答可以使用数值和千分位格式。字符串型分类标签保留其原始文本。",
        "",
        "## 参照来源",
        "",
        f"数据快照指纹为 `{corpus['dataset']['snapshot_id']}`，16个CSV合计62,077行。完整文件SHA-256与列名保存在 [cases.json](../../tests/preflight/cases.json) 的dataset.files中。",
        "",
        "业务说明来自 [数据字典](../reference/电子供应链数据表说明.md)、[语义源](../../config/semantic/catalog.yaml) 和 [登记关系](../../config/relations.json)。表目录以本次16个CSV为准。",
        "",
        "数据文件同时包含2024年和2025年的业务记录。库存文件的唯一时间为2025-12-31 18:00:00。金额币种及含税口径未提供。生产实际产量是否包含拒收量尚未确认。",
        "",
        "当前语义源中的到货准时率、平均延期天数、来料拒收率、发货队列来料拒收率均为candidate。涉及原始数量或日期差的计算由题目明确字段和公式。",
        "",
        "标准答案生成命令为 `python tests/preflight/build_cases.py`。生成器使用Python标准库、CSV、Decimal与日期计算，运行后重建本文件及cases.json。",
        "",
        "数值参照交叉核对命令为 `python -m tests.blackbox.verify_reference`，使用DATABASE_URL对应的只读账号执行独立SQL，并核对CSV文件哈希、数据库表行数和标准结果。核对报告保存到runtime/diagnostics/blackbox_reference_validation.json。",
        "",
        "## 案例目录",
        "",
        "| 编号 | 分类 | 案例 | 轮数 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {case['case_id']} | {case['category']} | {case['title']} | {len(case['turns'])} |"
        for case in corpus["cases"]
    ]
    for case in corpus["cases"]:
        lines += [
            "",
            f"## {case['case_id']} {case['title']}",
            "",
            f"分类为{case['category']}。",
            "",
            "参照来源为 " + "、".join(case["source_files"] or ["项目能力说明"]) + "。",
            "",
            "计算或核对方式为" + case["reference_method"],
            "",
        ]
        for index, item in enumerate(case["turns"], 1):
            lines += [
                f"### 第{index}轮",
                "",
                "**输入**",
                "",
                "> " + item["input"],
                "",
                "**预期结果**",
                "",
            ]
            for output in item["expected"]["outputs"]:
                lines += render_output(output) + [""]
            if item["expected"]["checks"]:
                lines += [
                    "判定要求",
                    "",
                    *["- " + value for value in item["expected"]["checks"]],
                    "",
                ]
    return "\n".join(lines).rstrip() + "\n"


def main():
    tables, manifest = read_tables()
    snapshot_id = hashlib.sha256(
        "".join(item["sha256"] for item in manifest).encode()
    ).hexdigest()[:16]
    reference_files = [
        "data/schema.sql",
        "config/semantic/catalog.yaml",
        "config/relations.json",
        "docs/reference/电子供应链数据表说明.md",
        "src/enterprise_agent/stages/execute/agents/capabilities.py",
    ]
    corpus = {
        "schema_version": 1,
        "purpose": "用户输入及可见预期结果，用于后续黑盒验收",
        "dataset": {
            "snapshot_id": snapshot_id,
            "total_rows": sum(item["rows"] for item in manifest),
            "files": manifest,
            "references": [
                {
                    "file": name,
                    "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest(),
                }
                for name in reference_files
            ],
        },
        "comparison": {
            "integer_tolerance": 0,
            "decimal_absolute_tolerance": "0.005",
            "rounding": "ROUND_HALF_UP",
            "decimal_encoding": "string",
            "equivalent_column_labels": True,
            "table_identifiers_case_insensitive": True,
            "text": "核对事实要点和限定条件，接受同义表达",
            "case_isolation": "每个案例使用新的会话，案例内按轮次执行",
        },
        "cases": build_cases(tables),
    }
    canonical = (
        json.dumps(corpus, ensure_ascii=False, indent=2, default=json_value) + "\n"
    )
    CORPUS.write_text(canonical, encoding="utf-8")
    DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENT.write_text(render_document(json.loads(canonical)), encoding="utf-8")
    print(
        json.dumps(
            {
                "cases": len(corpus["cases"]),
                "turns": sum(len(item["turns"]) for item in corpus["cases"]),
                "snapshot_id": snapshot_id,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
