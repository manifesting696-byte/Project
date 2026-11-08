CREATE TABLE "FinishedGoodsInventory" (
  "finished_goods_inventory_id" INTEGER PRIMARY KEY,
  "warehouse_id" INTEGER,
  "product_id" INTEGER,
  "quantity_on_hand" INTEGER,
  "quantity_reserved" INTEGER,
  "reorder_point" INTEGER,
  "safety_stock_level" INTEGER,
  "last_updated_at" TIMESTAMP
);

CREATE TABLE "GoodsReceiptDetails" (
  "gr_detail_id" INTEGER PRIMARY KEY,
  "gr_id" INTEGER,
  "shipment_detail_id" INTEGER,
  "material_id" INTEGER,
  "received_quantity" INTEGER,
  "accepted_quantity" INTEGER,
  "rejected_quantity" INTEGER,
  "inspection_status" TEXT,
  "batch_number" TEXT
);

CREATE TABLE "GoodsReceipts" (
  "gr_id" INTEGER PRIMARY KEY,
  "gr_code" TEXT,
  "shipment_id" INTEGER,
  "receipt_date" DATE,
  "received_by" TEXT,
  "quality_check_status" TEXT,
  "total_received_value" NUMERIC
);

CREATE TABLE "Materials" (
  "material_id" INTEGER PRIMARY KEY,
  "material_code" TEXT,
  "material_name" TEXT,
  "material_category" TEXT,
  "material_type" TEXT,
  "uom" TEXT,
  "unit_weight" NUMERIC,
  "status" TEXT
);

CREATE TABLE "ProductBOM" (
  "bom_id" INTEGER PRIMARY KEY,
  "product_id" INTEGER,
  "material_id" INTEGER,
  "quantity_required" INTEGER,
  "uom" TEXT,
  "status" TEXT
);

CREATE TABLE "Production" (
  "production_id" INTEGER PRIMARY KEY,
  "production_order_code" TEXT,
  "product_id" INTEGER,
  "planned_quantity" INTEGER,
  "start_date" DATE,
  "expected_end_date" DATE,
  "actual_end_date" DATE,
  "production_quantity" INTEGER,
  "rejected_quantity" INTEGER,
  "production_status" TEXT
);

CREATE TABLE "ProductionDetails" (
  "production_detail_id" INTEGER PRIMARY KEY,
  "production_id" INTEGER,
  "bom_id" INTEGER,
  "raw_inventory_id" INTEGER,
  "actual_consumed_quantity" NUMERIC,
  "return_quantity" INTEGER,
  "scrap_quantity" NUMERIC,
  "unit_cost" NUMERIC,
  "total_material_cost" NUMERIC
);

CREATE TABLE "Products" (
  "product_id" INTEGER PRIMARY KEY,
  "product_code" TEXT,
  "product_name" TEXT,
  "product_category" TEXT,
  "uom" TEXT,
  "standard_cost" NUMERIC,
  "selling_price" NUMERIC,
  "status" TEXT
);

CREATE TABLE "PurchaseOrderDetails" (
  "po_detail_id" INTEGER PRIMARY KEY,
  "po_id" INTEGER,
  "pr_detail_id" INTEGER,
  "material_id" INTEGER,
  "ordered_quantity" INTEGER,
  "unit_price" NUMERIC,
  "discount_percentage" INTEGER,
  "expected_delivery_date" DATE,
  "total_amount" NUMERIC,
  "line_status" TEXT
);

CREATE TABLE "PurchaseOrders" (
  "po_id" INTEGER PRIMARY KEY,
  "po_code" TEXT,
  "supplier_id" INTEGER,
  "pr_id" INTEGER,
  "order_date" DATE,
  "expected_delivery_date" DATE,
  "payment_terms" TEXT,
  "status" TEXT
);

CREATE TABLE "QualityInspections" (
  "inspection_id" INTEGER PRIMARY KEY,
  "gr_detail_id" INTEGER,
  "inspection_date" DATE,
  "inspection_type" TEXT,
  "inspected_quantity" INTEGER,
  "passed_quantity" INTEGER,
  "inspection_result" TEXT,
  "defect_quantity" INTEGER,
  "defect_reason" TEXT,
  "corrective_action" TEXT,
  "reinspection_required" INTEGER,
  "status" TEXT
);

CREATE TABLE "RawMaterialInventory" (
  "raw_inventory_id" INTEGER PRIMARY KEY,
  "warehouse_id" INTEGER,
  "material_id" INTEGER,
  "quantity_on_hand" NUMERIC,
  "quantity_reserved" NUMERIC,
  "reorder_point" INTEGER,
  "safety_stock_level" INTEGER,
  "max_inventory_level" INTEGER,
  "unit_cost" NUMERIC,
  "last_updated_at" TIMESTAMP
);

CREATE TABLE "Suppliers" (
  "supplier_id" INTEGER PRIMARY KEY,
  "supplier_code" TEXT,
  "supplier_name" TEXT,
  "supplier_type" TEXT,
  "supplier_category" TEXT,
  "country" TEXT,
  "city" TEXT,
  "address" TEXT,
  "contact_person" TEXT,
  "phone" INTEGER,
  "email" TEXT,
  "payment_terms" TEXT,
  "status" TEXT
);

CREATE TABLE "SupplierShipmentDetails" (
  "shipment_detail_id" INTEGER PRIMARY KEY,
  "shipment_id" INTEGER,
  "po_detail_id" INTEGER,
  "material_id" INTEGER,
  "received_quantity" INTEGER,
  "accepted_quantity" INTEGER,
  "rejected_quantity" INTEGER
);

CREATE TABLE "SupplierShipments" (
  "shipment_id" INTEGER PRIMARY KEY,
  "shipment_code" TEXT,
  "po_id" INTEGER,
  "supplier_id" INTEGER,
  "supplier_reference" TEXT,
  "ship_date" DATE,
  "expected_arrival_date" DATE,
  "actual_arrival_date" DATE,
  "total_shipment_value" NUMERIC,
  "shipping_cost" NUMERIC
);

CREATE TABLE "Warehouses" (
  "warehouse_id" INTEGER PRIMARY KEY,
  "warehouse_code" TEXT,
  "warehouse_name" TEXT,
  "warehouse_type" TEXT,
  "country" TEXT,
  "city" TEXT,
  "location_description" TEXT,
  "status" TEXT
);
