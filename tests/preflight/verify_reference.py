"""Cross-check CSV reference answers using read-only PostgreSQL queries."""

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from enterprise_agent.settings import database_url


ROOT = Path(__file__).resolve().parents[2]
CORPUS = Path(__file__).with_name("cases.json")
HALF_YEAR = ("2025-01-01", "2025-07-01")
PRODUCT_FIELDS = (
    "product_id,product_code,product_name,product_category,standard_cost,selling_price"
)

SUPPLIER_COUNTS = """
SELECT p.supplier_id,s.supplier_name,COUNT(*) AS order_count
FROM purchaseorders p JOIN suppliers s ON s.supplier_id=p.supplier_id
WHERE p.order_date >= %s AND p.order_date < %s
GROUP BY p.supplier_id,s.supplier_name
ORDER BY order_count DESC,p.supplier_id LIMIT 5
"""
MONTHLY = """
SELECT to_char(month,'YYYY-MM') AS month,COUNT(p.po_id) AS order_count
FROM generate_series(%s::date,%s::date,interval '1 month') AS month
LEFT JOIN purchaseorders p ON p.order_date>=month AND p.order_date<month+interval '1 month'
GROUP BY month ORDER BY month
"""
PRODUCTION = """
SELECT p.product_id,r.product_name,SUM(p.production_quantity) AS production_quantity
FROM production p JOIN products r ON r.product_id=p.product_id
WHERE p.actual_end_date>=%s AND p.actual_end_date<%s
GROUP BY p.product_id,r.product_name
ORDER BY production_quantity DESC,p.product_id LIMIT 5
"""
STOCK = """
SELECT r.material_id,m.material_name,r.quantity_on_hand,r.quantity_reserved,
       ROUND(r.quantity_on_hand-r.quantity_reserved,2) AS available_quantity
FROM rawmaterialinventory r JOIN materials m ON m.material_id=r.material_id
WHERE r.warehouse_id=%s ORDER BY available_quantity,r.material_id LIMIT 5
"""


def normalize(value):
    if isinstance(value, date):
        return "text=" + value.isoformat()
    if isinstance(value, (int, Decimal)) or (
        isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value)
    ):
        return "number=" + str(Decimal(value).normalize())
    return "text=" + value


def assert_rows(expected, actual, name):
    fields = expected["columns"]
    assert all(set(row) == set(fields) for row in actual), name
    wanted = [
        tuple(normalize(row[field]) for field in fields) for row in expected["rows"]
    ]
    found = [tuple(normalize(row[field]) for field in fields) for row in actual]
    if not expected["ordered"]:
        wanted, found = sorted(wanted), sorted(found)
    assert wanted == found, name


def main():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in corpus["cases"]}
    sources = corpus["dataset"]["files"] + corpus["dataset"]["references"]
    for source in sources:
        assert (
            hashlib.sha256((ROOT / source["file"]).read_bytes()).hexdigest()
            == source["sha256"]
        ), source["file"]

    checks = [
        ("D01", 1, 0, "SELECT COUNT(*) AS product_count FROM products", ()),
        (
            "D02",
            1,
            0,
            f"SELECT {PRODUCT_FIELDS} FROM products ORDER BY product_id LIMIT 5",
            (),
        ),
        (
            "D03",
            1,
            0,
            "SELECT product_id,product_name,product_category,selling_price FROM products WHERE product_category='Smartphone' ORDER BY selling_price,product_id",
            (),
        ),
        (
            "D04",
            1,
            0,
            "SELECT material_category,COUNT(*) AS material_count FROM materials GROUP BY material_category",
            (),
        ),
        ("D05", 1, 0, SUPPLIER_COUNTS, HALF_YEAR),
        ("D06", 1, 0, MONTHLY, ("2025-01-01", "2025-06-01")),
        ("D07", 1, 0, PRODUCTION, HALF_YEAR),
        (
            "D08",
            1,
            0,
            "SELECT b.material_id,m.material_name,b.quantity_required,b.uom FROM productbom b JOIN materials m ON m.material_id=b.material_id WHERE b.product_id=1 ORDER BY b.material_id",
            (),
        ),
        (
            "D09",
            1,
            0,
            """
            SELECT p.supplier_id,s.supplier_name,ROUND(SUM(d.total_amount),2) AS total_amount
            FROM purchaseorderdetails d JOIN purchaseorders p ON p.po_id=d.po_id
            JOIN suppliers s ON s.supplier_id=p.supplier_id
            WHERE p.order_date>=%s AND p.order_date<%s
            GROUP BY p.supplier_id,s.supplier_name ORDER BY total_amount DESC,p.supplier_id LIMIT 3
        """,
            HALF_YEAR,
        ),
        ("D10", 1, 0, STOCK, (1,)),
        (
            "D11",
            1,
            0,
            """
            SELECT p.shipment_id,p.shipment_code,s.supplier_name,
                   p.expected_arrival_date,p.actual_arrival_date,
                   p.actual_arrival_date-p.expected_arrival_date AS delay_days
            FROM suppliershipments p JOIN suppliers s ON s.supplier_id=p.supplier_id
            WHERE p.expected_arrival_date>=%s AND p.expected_arrival_date<%s
              AND p.actual_arrival_date>p.expected_arrival_date
            ORDER BY delay_days DESC,p.shipment_id LIMIT 5
        """,
            HALF_YEAR,
        ),
        (
            "D12",
            1,
            0,
            "SELECT production_id,production_order_code,product_id,production_quantity FROM production WHERE actual_end_date>='2026-01-01' AND actual_end_date<'2026-02-01' ORDER BY production_id LIMIT 5",
            (),
        ),
        (
            "D13",
            1,
            0,
            """
            SELECT to_char(g.receipt_date,'YYYY-MM') AS month,
                   SUM(d.received_quantity) AS received_quantity,
                   SUM(d.accepted_quantity) AS accepted_quantity,
                   SUM(d.rejected_quantity) AS rejected_quantity
            FROM goodsreceiptdetails d JOIN goodsreceipts g ON g.gr_id=d.gr_id
            WHERE g.receipt_date>=%s AND g.receipt_date<%s GROUP BY month ORDER BY month
        """,
            HALF_YEAR,
        ),
        (
            "D14",
            1,
            0,
            """
            SELECT inspection_result,COUNT(*) AS inspection_count,SUM(defect_quantity) AS defect_quantity
            FROM qualityinspections WHERE inspection_date>=%s AND inspection_date<%s
            GROUP BY inspection_result ORDER BY inspection_result
        """,
            HALF_YEAR,
        ),
        (
            "D15",
            1,
            0,
            """
            SELECT b.material_id,m.material_name,ROUND(SUM(d.total_material_cost),2) AS total_material_cost
            FROM productiondetails d JOIN production p ON p.production_id=d.production_id
            JOIN productbom b ON b.bom_id=d.bom_id JOIN materials m ON m.material_id=b.material_id
            WHERE p.product_id=1 AND p.actual_end_date>=%s AND p.actual_end_date<%s
            GROUP BY b.material_id,m.material_name ORDER BY total_material_cost DESC,b.material_id LIMIT 5
        """,
            HALF_YEAR,
        ),
        (
            "D16",
            1,
            0,
            """
            SELECT d.gr_detail_id,s.po_id,sd.po_detail_id,d.material_id,m.material_name,d.received_quantity
            FROM goodsreceiptdetails d JOIN goodsreceipts g ON g.gr_id=d.gr_id
            JOIN suppliershipments s ON s.shipment_id=g.shipment_id
            JOIN suppliershipmentdetails sd ON sd.shipment_detail_id=d.shipment_detail_id
            JOIN materials m ON m.material_id=d.material_id
            WHERE d.gr_id=1 ORDER BY d.gr_detail_id
        """,
            (),
        ),
        (
            "I03",
            2,
            0,
            """
            WITH first_five AS (SELECT * FROM products ORDER BY product_id LIMIT 5)
            SELECT product_name,standard_cost,selling_price,
                   ROUND(selling_price-standard_cost,2) AS price_cost_difference
            FROM first_five ORDER BY price_cost_difference DESC,product_name
        """,
            (),
        ),
        (
            "I04",
            2,
            0,
            """
            WITH totals AS (
                SELECT p.product_id,r.product_name,SUM(p.production_quantity) AS production_quantity
                FROM production p JOIN products r ON r.product_id=p.product_id
                WHERE p.actual_end_date>=%s AND p.actual_end_date<%s
                GROUP BY p.product_id,r.product_name
            ), ranked AS (
                SELECT *,ROW_NUMBER() OVER(ORDER BY production_quantity DESC,product_id) AS rank FROM totals
            )
            SELECT first.product_name AS first_product,first.production_quantity AS first_quantity,
                   fifth.product_name AS fifth_product,fifth.production_quantity AS fifth_quantity,
                   first.production_quantity-fifth.production_quantity AS difference
            FROM ranked first CROSS JOIN ranked fifth WHERE first.rank=1 AND fifth.rank=5
        """,
            HALF_YEAR,
        ),
        (
            "I06",
            1,
            0,
            "SELECT product_category,COUNT(*) AS product_count FROM products GROUP BY product_category ORDER BY product_category",
            (),
        ),
        (
            "M02",
            1,
            0,
            "SELECT product_id,product_code,product_name FROM products ORDER BY product_id LIMIT 2",
            (),
        ),
        (
            "M02",
            1,
            1,
            "SELECT supplier_id,supplier_code,supplier_name FROM suppliers ORDER BY supplier_id LIMIT 3",
            (),
        ),
        (
            "M05",
            1,
            0,
            """
            SELECT supplier_id,2024 AS year,COUNT(*) AS order_count FROM purchaseorders
            WHERE supplier_id=1 AND order_date>='2024-01-01' AND order_date<'2025-01-01' GROUP BY supplier_id
            UNION ALL
            SELECT supplier_id,2025 AS year,COUNT(*) AS order_count FROM purchaseorders
            WHERE supplier_id=2 AND order_date>='2025-01-01' AND order_date<'2026-01-01' GROUP BY supplier_id
        """,
            (),
        ),
        (
            "X01",
            2,
            0,
            f"SELECT {PRODUCT_FIELDS} FROM products ORDER BY product_id LIMIT 2",
            (),
        ),
        ("X02", 2, 0, PRODUCTION, ("2025-04-01", "2025-07-01")),
        ("X03", 2, 0, SUPPLIER_COUNTS, ("2024-01-01", "2024-07-01")),
        ("X05", 2, 0, STOCK, (2,)),
        (
            "X06",
            2,
            0,
            "SELECT product_id,product_code,product_name FROM products ORDER BY product_id LIMIT 3",
            (),
        ),
        (
            "X07",
            2,
            0,
            f"SELECT {PRODUCT_FIELDS} FROM products ORDER BY selling_price DESC,product_id LIMIT 5",
            (),
        ),
        (
            "X07",
            3,
            0,
            f"SELECT {PRODUCT_FIELDS} FROM products WHERE product_category='Laptop' ORDER BY selling_price DESC,product_id LIMIT 3",
            (),
        ),
        ("X08", 2, 0, MONTHLY, ("2026-01-01", "2026-06-01")),
        (
            "B02",
            1,
            0,
            "SELECT ROUND(AVG(selling_price),2) AS mean FROM products WHERE product_id IN(1,2,3)",
            (),
        ),
        # 跨轮/多需求/结果交互的首轮查询：与 D 组共用同一口径
        ("I01", 1, 0, SUPPLIER_COUNTS, HALF_YEAR),
        ("I02", 1, 0, MONTHLY, ("2025-01-01", "2025-06-01")),
        ("I03", 1, 0, f"SELECT {PRODUCT_FIELDS} FROM products ORDER BY product_id LIMIT 5", ()),
        ("I04", 1, 0, PRODUCTION, HALF_YEAR),
        ("I05", 1, 0, STOCK, (1,)),
        ("M01", 1, 1, "SELECT COUNT(*) AS product_count FROM products", ()),
        ("M03", 1, 1, MONTHLY, ("2025-01-01", "2025-06-01")),
        ("M04", 1, 0, "SELECT product_category,COUNT(*) AS product_count FROM products GROUP BY product_category ORDER BY product_category", ()),
        ("M06", 1, 0, "SELECT product_id,product_code,product_name FROM products ORDER BY product_id LIMIT 3", ()),
        ("M07", 1, 0, MONTHLY, ("2025-01-01", "2025-06-01")),
        ("M07", 1, 2, "SELECT product_category,COUNT(*) AS product_count FROM products GROUP BY product_category ORDER BY product_category", ()),
        ("M08", 1, 0, PRODUCTION, HALF_YEAR),
        ("X01", 1, 0, f"SELECT {PRODUCT_FIELDS} FROM products ORDER BY product_id LIMIT 5", ()),
        ("X02", 1, 0, PRODUCTION, HALF_YEAR),
        ("X03", 1, 0, SUPPLIER_COUNTS, HALF_YEAR),
        ("X04", 1, 0, SUPPLIER_COUNTS, HALF_YEAR),
        ("X05", 1, 0, STOCK, (1,)),
        ("X06", 1, 0, "SELECT product_id,product_code,product_name FROM products ORDER BY product_id LIMIT 5", ()),
        ("X06", 1, 1, "SELECT supplier_id,supplier_code,supplier_name FROM suppliers ORDER BY supplier_id LIMIT 3", ()),
        ("X07", 1, 0, f"SELECT {PRODUCT_FIELDS} FROM products ORDER BY product_id LIMIT 5", ()),
        ("X08", 1, 0, MONTHLY, ("2025-01-01", "2025-06-01")),
    ]
    count = 0
    with psycopg.connect(
        database_url("DATABASE_URL"),
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c search_path=analysis,pg_catalog -c statement_timeout=15000",
        connect_timeout=5,
    ) as database:
        assert (
            database.execute("SHOW transaction_read_only").fetchone()[
                "transaction_read_only"
            ]
            == "on"
        )
        for source in corpus["dataset"]["files"]:
            name = Path(source["file"]).stem.lower()
            actual = database.execute(
                psycopg.sql.SQL("SELECT COUNT(*) AS n FROM {}").format(
                    psycopg.sql.Identifier(name)
                )
            ).fetchone()["n"]
            assert actual == source["rows"], name
        for case_id, index, output_index, statement, parameters in checks:
            expected = cases[case_id]["turns"][index - 1]["expected"]["outputs"][
                output_index
            ]
            actual = database.execute(statement, parameters).fetchall()
            assert_rows(
                expected, actual, f"{case_id} turn={index} output={output_index}"
            )
            count += len(actual)

    for case in cases.values():
        for item in case["turns"]:
            for output in item["expected"]["outputs"]:
                if output["type"] == "table":
                    assert output["row_count"] == len(output["rows"])
                    assert all(
                        set(row) == set(output["columns"]) for row in output["rows"]
                    )
                if output["type"] == "chart":
                    assert output["point_count"] == len(output["data"])
                    assert (
                        len({row[output["category_field"]] for row in output["data"]})
                        == output["point_count"]
                    )
                    if output["chart_type"] == "pie":
                        assert sum(
                            Decimal(row["percentage"]) for row in output["percentages"]
                        ) == Decimal(100)

    report = {
        "cases": len(cases),
        "turns": sum(len(case["turns"]) for case in cases.values()),
        "source_tables_checked": 16,
        "sql_result_sets_checked": len(checks),
        "sql_result_rows_checked": count,
        "cases_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "result": "passed",
    }
    path = ROOT / "runtime" / "diagnostics" / "blackbox_reference_validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
