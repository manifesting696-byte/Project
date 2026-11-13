# Agent 黑盒测试集

本测试集记录用户输入及用户可见的预期结果。标准答案直接由当前CSV、数据字典和已保存业务定义整理或计算。

共 52 个案例、69 轮输入。六类数量依次为基础问答6、企业知识8、基础数据查询16、结果交互6、多需求8、跨轮修改8。

基础数据查询额外覆盖收货数量、质检结果、生产用料成本及收货明细追溯。

## 使用与判定

- 每个案例在独立的新会话中开始，按案例内的轮次顺序输入。前置查询也属于案例，需先核对其结果。
- 单轮包含多项要求的案例归入多需求。跨轮修改案例可以包含前置多需求，最后一轮检查对既有要求的修改。
- 只比较最终可见的文字、表格、图表和报告。列名允许等义中文或英文，要求对应的数据值、对象、时间范围和排序一致。
- 单个数值或简短比较结果可用准确文字表达。多行明细应完整展示预期行数和要求的字段，允许等价的可读表格形式。
- 文字按列出的事实要点判断，允许同义表达。语义标签作为输出要求，多种要点可以合并在同一段文字中。表名允许采用对应的PostgreSQL小写视图名。
- limitations记录事实边界，回答涉及相应判断时须符合。reference_notes是核对者的参照注记，按题意输出正确数值即可，注记文字可省略。
- 整数与计数须精确相等。金额及指定小数保留两位，采用十进制四舍五入；数值比较容差为0.005。
- 日期边界包含题述开始日和结束日，半年为1月1日至6月30日，第二季度为4月1日至6月30日。每题指定的日期字段决定入期。
- 题目指定的并列排序键需生效；未要求顺序的表按行集合核对（顺序无关）。
- 分类与日期文本已统一（`YYYY-MM-DD`），实体名称比较忽略首尾空白，名称主体须一致。
- 饼图百分比采用0至100的百分数。JSON中的percentage=25.00对应25%。图表以实际图形、标签、顺序和数据对应关系判定。
- 空明细可展示空表与无匹配说明。缺失月份补0仅在题目明确要求时采用。候选指标状态及缺失数据按各题所列事实判断。
- JSON中的十进制数使用字符串保留精度，实际回答可以使用数值和千分位格式。字符串型分类标签保留其原始文本。

## 参照来源

数据快照指纹为 `4c21d53a83016f2e`，16个CSV合计62,077行。完整文件SHA-256与列名保存在 [cases.json](../../tests/preflight/cases.json) 的dataset.files中。

业务说明来自 [数据字典](../reference/电子供应链数据表说明.md)、[语义源](../../config/semantic/catalog.yaml) 和 [登记关系](../../config/relations.json)。表目录以本次16个CSV为准。

数据文件同时包含2024年和2025年的业务记录。库存文件的唯一时间为2025-12-31 18:00:00。金额币种及含税口径未提供。生产实际产量是否包含拒收量尚未确认。

当前语义源中的到货准时率、平均延期天数、来料拒收率、发货队列来料拒收率均为candidate。涉及原始数量或日期差的计算由题目明确字段和公式。

标准答案生成命令为 `python tests/preflight/build_cases.py`。生成器使用Python标准库、CSV、Decimal与日期计算，运行后重建本文件及cases.json。

数值参照交叉核对命令为 `python -m tests.blackbox.verify_reference`，使用DATABASE_URL对应的只读账号执行独立SQL，并核对CSV文件哈希、数据库表行数和标准结果。核对报告保存到runtime/diagnostics/blackbox_reference_validation.json。

## 案例目录

| 编号 | 分类 | 案例 | 轮数 |
| --- | --- | --- | --- |
| B01 | 基础问答 | 问析助手身份与能力 | 1 |
| B02 | 基础问答 | 用项目价格示例计算平均值 | 1 |
| B03 | 基础问答 | 电子产品BOM概念 | 1 |
| B04 | 基础问答 | 现存量与预留量概念 | 1 |
| B05 | 基础问答 | 关联后订单重复计数 | 1 |
| B06 | 基础问答 | 库存快照与时间趋势 | 1 |
| K01 | 企业知识 | 项目数据表目录 | 1 |
| K02 | 企业知识 | 产品表字段含义 | 1 |
| K03 | 企业知识 | 生产数量字段及口径限制 | 1 |
| K04 | 企业知识 | 采购订单到收货单关系 | 1 |
| K05 | 企业知识 | 供应商地域字段粒度 | 1 |
| K06 | 企业知识 | 候选到货准时率定义 | 1 |
| K07 | 企业知识 | 库存数据时间覆盖 | 1 |
| K08 | 企业知识 | 设备与销售数据范围 | 1 |
| D01 | 基础数据查询 | 产品记录总数 | 1 |
| D02 | 基础数据查询 | 产品明细与固定排序 | 1 |
| D03 | 基础数据查询 | 类别过滤与价格排序 | 1 |
| D04 | 基础数据查询 | 类别分组计数（取值已统一） | 1 |
| D05 | 基础数据查询 | 供应商订单排名 | 1 |
| D06 | 基础数据查询 | 月度订单数量 | 1 |
| D07 | 基础数据查询 | 产品产量排名与日期解析 | 1 |
| D08 | 基础数据查询 | 产品BOM明细 | 1 |
| D09 | 基础数据查询 | 采购明细金额汇总 | 1 |
| D10 | 基础数据查询 | 库存差值及低值排序 | 1 |
| D11 | 基础数据查询 | 日期比较与延期天数 | 1 |
| D12 | 基础数据查询 | 明确日期范围的空结果 | 1 |
| D13 | 基础数据查询 | 收货数量按月汇总 | 1 |
| D14 | 基础数据查询 | 检验结果分组统计 | 1 |
| D15 | 基础数据查询 | 生产用料成本多表汇总 | 1 |
| D16 | 基础数据查询 | 收货明细追溯采购行 | 1 |
| I01 | 结果交互 | 订单排名柱状图 | 2 |
| I02 | 结果交互 | 月度订单折线图 | 2 |
| I03 | 结果交互 | 基于结果制作派生表 | 2 |
| I04 | 结果交互 | 解读产量排名 | 2 |
| I05 | 结果交互 | 库存结果报告 | 2 |
| I06 | 结果交互 | 产品类别饼图与占比 | 2 |
| M01 | 多需求 | 概念解释与数据查询 | 1 |
| M02 | 多需求 | 两类实体的独立明细 | 1 |
| M03 | 多需求 | 企业关系解释与订单趋势表 | 1 |
| M04 | 多需求 | 查询类别并绘图和解释 | 1 |
| M05 | 多需求 | 不同供应商与年份的条件隔离 | 1 |
| M06 | 多需求 | 可回答需求与缺失数据并存 | 1 |
| M07 | 多需求 | 两套数据与两张图的对应 | 1 |
| M08 | 多需求 | 已有结果绘图与字段解释 | 2 |
| X01 | 跨轮修改 | 产品条数由5改2 | 2 |
| X02 | 跨轮修改 | 产量统计半年改季度 | 2 |
| X03 | 跨轮修改 | 供应商订单排名换年份 | 2 |
| X04 | 跨轮修改 | 仅修改图表方向 | 3 |
| X05 | 跨轮修改 | 库存查询切换仓库 | 2 |
| X06 | 跨轮修改 | 多项需求中的局部修改 | 2 |
| X07 | 跨轮修改 | 多轮排序与类别条件继承 | 3 |
| X08 | 跨轮修改 | 时间范围改为空数据并补零 | 2 |

## B01 问析助手身份与能力

分类为基础问答。

参照来源为 项目能力说明。

计算或核对方式为依据项目功能说明及 stages/execute/agents/capabilities.py 中的能力描述。

### 第1轮

**输入**

> 请介绍你在这个供应链数据项目中的用途。

**预期结果**

**系统能力**

- 面向企业供应链数据问答与分析的智能助手。
- 支持查询数据、统计分析、表格或图表展示、报告生成以及基于已有结果继续提问。
- 能力涉及采购、供应商发货与收货、来料检验、生产和库存。

- 底层模型厂商和具体模型型号应以可确认的运行配置为依据。


## B02 用项目价格示例计算平均值

分类为基础问答。

参照来源为 data/seed/Products.csv。

计算或核对方式为取 product_id=1、2、3 的 selling_price，Decimal 求和除以3。

### 第1轮

**输入**

> 给定三个产品的售价数值 31644.01、25228.49、19753.84，这三个数的算术平均值是多少？保留两位小数。

**预期结果**

**计算结果**

| mean |
| --- |
| 25542.11 |


## B03 电子产品BOM概念

分类为基础问答。

参照来源为 data/seed/ProductBOM.csv、data/seed/Products.csv、data/seed/Materials.csv。

计算或核对方式为以真实产品、材料和 ProductBOM 的数量配置作为概念依据。

### 第1轮

**输入**

> 电子产品装配中的BOM是什么，它在物料需求分析中有什么作用？

**预期结果**

**BOM概念**

- BOM 是 Bill of Materials，即物料清单。
- 描述产品所需的材料或零部件及其配置数量。
- 可以结合计划生产数量计算按配置数量得到的物料需求。

- 本项目BOM文件未另列损耗率或版本生效条件。


## B04 现存量与预留量概念

分类为基础问答。

参照来源为 data/seed/RawMaterialInventory.csv。

计算或核对方式为依据库存字段含义，解释给定的示例计算约定。

### 第1轮

**输入**

> 库存分析中现存量与预留量有什么区别？如果暂按现存量减预留量表示可用量，应怎样理解这个计算？

**预期结果**

**库存概念**

- 现存量表示库存记录中现有的数量。
- 预留量表示记录中已预留的数量。
- 按题设约定，可用量等于现存量减预留量。

- 该差值是本题的计算约定，实际业务是否可直接出库还取决于已确认的规则。


## B05 关联后订单重复计数

分类为基础问答。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/PurchaseOrderDetails.csv。

计算或核对方式为核实 po_id=1 有3条明细，以一对多关联解释计数粒度。

### 第1轮

**输入**

> 一张采购订单有3条明细。关联订单表和明细表后，为什么不能直接把这3行算成3张订单？应该怎样统计订单数？

**预期结果**

**计数粒度**

- 一张订单关联3条明细后会展开成3行，行数属于明细粒度。
- 这仍然是1张订单、3条明细。
- 订单数可在订单单头表统计，或对订单标识去重计数。


## B06 库存快照与时间趋势

分类为基础问答。

参照来源为 data/seed/RawMaterialInventory.csv、data/seed/FinishedGoodsInventory.csv。

计算或核对方式为库存两个文件均只含2025-12-31 18:00:00这一更新时间。

### 第1轮

**输入**

> 只有某一时点的库存快照时，库存快照和逐月库存变化趋势有什么区别？

**预期结果**

**快照与趋势**

- 库存快照描述特定时点的库存状态。
- 逐月变化趋势需要多个时间点的数据。
- 单个时点的库存数量无法确定之前各月的库存变化。


## K01 项目数据表目录

分类为企业知识。

参照来源为 data/seed/FinishedGoodsInventory.csv、data/seed/GoodsReceiptDetails.csv、data/seed/GoodsReceipts.csv、data/seed/Materials.csv、data/seed/ProductBOM.csv、data/seed/Production.csv、data/seed/ProductionDetails.csv、data/seed/Products.csv、data/seed/PurchaseOrderDetails.csv、data/seed/PurchaseOrders.csv、data/seed/QualityInspections.csv、data/seed/RawMaterialInventory.csv、data/seed/SupplierShipmentDetails.csv、data/seed/SupplierShipments.csv、data/seed/Suppliers.csv、data/seed/Warehouses.csv。

计算或核对方式为逐一读取16个CSV文件名，按文件名去掉扩展名列出表。

### 第1轮

**输入**

> 这个项目当前有哪些数据表？请列出全部表名并简要说明业务范围。

**预期结果**

**完整表目录**

- 当前数据集包含16张表。
- 表名完整集合为 FinishedGoodsInventory、GoodsReceiptDetails、GoodsReceipts、Materials、ProductBOM、Production、ProductionDetails、Products、PurchaseOrderDetails、PurchaseOrders、QualityInspections、RawMaterialInventory、SupplierShipmentDetails、SupplierShipments、Suppliers、Warehouses。
- 范围覆盖基础资料、BOM、采购、发货、收货、质检、生产及库存。


## K02 产品表字段含义

分类为企业知识。

参照来源为 data/seed/Products.csv。

计算或核对方式为核对数据字典中 Products 八个字段的中文含义。

### 第1轮

**输入**

> 介绍Products表各字段的含义。

**预期结果**

**产品字段含义**

- product_id：产品标识
- product_code：产品编码
- product_name：产品名称
- product_category：产品类别
- uom：计量单位
- standard_cost：标准成本
- selling_price：销售价格
- status：状态

- 字段共 8 个，字段名与中文含义需一一对应。


## K03 生产数量字段及口径限制

分类为企业知识。

参照来源为 data/seed/Production.csv。

计算或核对方式为读取生产工单字段说明，不推导未确认的数量包含关系。

### 第1轮

**输入**

> Production里的planned_quantity、production_quantity和rejected_quantity分别表示什么？production_quantity是否已经扣除了rejected_quantity？

**预期结果**

**实际产量与计划量**

- planned_quantity 是生产工单计划数量。
- production_quantity 是生产工单记录的实际生产数量。

- 原文件未确认 production_quantity 是否包含 rejected_quantity，解释实际产量时需保留此口径限制。

**拒收数量与包含关系**

- rejected_quantity 是工单记录的拒收数量。
- 源文件未明确 production_quantity 是否包含 rejected_quantity，也未确认其是否已扣除拒收数量。


## K04 采购订单到收货单关系

分类为企业知识。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/SupplierShipments.csv、data/seed/GoodsReceipts.csv。

计算或核对方式为核对 config/relations.json 以及三个CSV的关系键。

### 第1轮

**输入**

> 从采购订单单头找到对应收货单，应该经过哪些表，使用哪些关联字段？

**预期结果**

**采购单头到收货单的关系**

- PurchaseOrders.po_id 关联 SupplierShipments.po_id。
- SupplierShipments.shipment_id 关联 GoodsReceipts.shipment_id。
- 从采购单头到收货单的路径为 PurchaseOrders → SupplierShipments → GoodsReceipts。
- GoodsReceipts 的单头标识为 gr_id，收货日期字段为 receipt_date。


## K05 供应商地域字段粒度

分类为企业知识。

参照来源为 data/seed/Suppliers.csv。

计算或核对方式为核对数据字典中 Suppliers.country、city、address 的字段含义与记录粒度。

### 第1轮

**输入**

> Suppliers表的country、city、address三个字段分别是什么粒度？如果按地域统计供应商分布，应该用哪个字段，为什么不能直接用address？

**预期结果**

**地域字段粒度**

- country 是国家或地区级字段，粒度最粗，用于跨国家汇总。
- city 是城市或行政区级字段，每个供应商一个城市，可按城市分组统计供应商数量。
- address 是街道级地址文本，粒度细于 city，同一城市对应多条不同地址，只适合定位与核对。
- 按地域统计供应商分布应使用 city：country 粒度过粗，address 会把同一城市的供应商拆散。

- address 不是规范化分组键，地域统计口径以 city 为准。


## K06 候选到货准时率定义

分类为企业知识。

参照来源为 data/seed/SupplierShipments.csv。

计算或核对方式为核对config/semantic/catalog.yaml的on_time_rate定义与candidate状态。

### 第1轮

**输入**

> 项目中的到货准时率已经有正式发布的口径了吗？如果还处于候选阶段，请说明候选定义的记录粒度、入期日期和判定条件。

**预期结果**

**指标状态和候选口径**

- on_time_rate / 到货准时率当前状态为 candidate，演示口径待业务确认。
- 候选粒度为 SupplierShipments.shipment_id，即发货单。
- 按 expected_arrival_date 预计到货日入期。
- 候选准时条件为 actual_arrival_date 不晚于 expected_arrival_date。
- 候选分母为有效发货单数。

- 该候选定义的发布状态仍待业务审核确认。


## K07 库存数据时间覆盖

分类为企业知识。

参照来源为 data/seed/RawMaterialInventory.csv、data/seed/FinishedGoodsInventory.csv。

计算或核对方式为提取两个库存文件的last_updated_at去重集合。

### 第1轮

**输入**

> 原料库存和成品库存是什么时间的数据？目前能直接得到2025年每个月的库存快照吗？

**预期结果**

**库存时间**

- 两个库存文件的全部更新时间均为2025-12-31 18:00:00。
- 当前文件提供该时点的库存状态。
- 数据集缺少2025年逐月库存快照。


## K08 设备与销售数据范围

分类为企业知识。

参照来源为 data/seed/FinishedGoodsInventory.csv、data/seed/GoodsReceiptDetails.csv、data/seed/GoodsReceipts.csv、data/seed/Materials.csv、data/seed/ProductBOM.csv、data/seed/Production.csv、data/seed/ProductionDetails.csv、data/seed/Products.csv、data/seed/PurchaseOrderDetails.csv、data/seed/PurchaseOrders.csv、data/seed/QualityInspections.csv、data/seed/RawMaterialInventory.csv、data/seed/SupplierShipmentDetails.csv、data/seed/SupplierShipments.csv、data/seed/Suppliers.csv、data/seed/Warehouses.csv。

计算或核对方式为核对16个CSV表头集合与semantic catalog的limitations。

### 第1轮

**输入**

> 项目当前是否包含设备故障、产线、工序和销售订单数据？能否据此直接分析设备故障导致生产延误的原因？

**预期结果**

**数据范围**

- 当前数据集缺少设备故障或停机、产线、工序和销售订单数据。
- 可以读取生产日期和数量，但当前证据不足以认定设备故障导致了生产延误。


## D01 产品记录总数

分类为基础数据查询。

参照来源为 data/seed/Products.csv。

计算或核对方式为对CSV数据行计数。

### 第1轮

**输入**

> 查询Products表的产品记录总数。

**预期结果**

**产品总数**

| product_count |
| --- |
| 16 |


## D02 产品明细与固定排序

分类为基础数据查询。

参照来源为 data/seed/Products.csv。

计算或核对方式为按整数product_id升序取前5条。

### 第1轮

**输入**

> 按 product_id 升序列出前5个产品，显示产品ID、编码、名称、类别、标准成本和销售价格。

**预期结果**

**前5个产品**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone | Smartphone | 23614.93 | 31644.01 |
| 2 | PRD-002 | Nile X1 Pro Phone | Smartphone | 18827.23 | 25228.49 |
| 3 | PRD-003 | Canal S2 Phone | Smartphone | 14741.67 | 19753.84 |
| 4 | PRD-004 | Canal S2 Max Phone | Smartphone | 29386.99 | 39378.57 |
| 5 | PRD-005 | Sina Air 14 Laptop | Laptop | 22462.35 | 28751.81 |


## D03 类别过滤与价格排序

分类为基础数据查询。

参照来源为 data/seed/Products.csv。

计算或核对方式为类别原文精确匹配Smartphone，按Decimal售价、整数ID排序。

### 第1轮

**输入**

> 列出product_category为Smartphone的全部产品，按selling_price升序、并列时product_id升序，显示产品ID、名称、类别和售价。

**预期结果**

**手机产品**

| product_id | product_name | product_category | selling_price |
| --- | --- | --- | --- |
| 3 | Canal S2 Phone | Smartphone | 19753.84 |
| 2 | Nile X1 Pro Phone | Smartphone | 25228.49 |
| 1 | Nile X1 Phone | Smartphone | 31644.01 |
| 4 | Canal S2 Max Phone | Smartphone | 39378.57 |


## D04 类别分组计数（取值已统一）

分类为基础数据查询。

参照来源为 data/seed/Materials.csv。

计算或核对方式为Counter按原始字符串分组，全部记录计数合计80。

### 第1轮

**输入**

> 按Materials.material_category原始文本分组统计材料记录数，列出全部类别及各自记录数。

**预期结果**

**材料原始类别计数**

| material_category | material_count |
| --- | --- |
| Battery and Power | 10 |
| Camera Audio and Sensors | 10 |
| Display | 10 |
| Enclosure and Mechanical | 10 |
| Memory and Storage | 10 |
| PCB and Connectivity | 10 |
| Packaging and Accessories | 10 |
| Processing | 10 |

判定要求

- 8种取值各10条
- 记录数合计80


## D05 供应商订单排名

分类为基础数据查询。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/Suppliers.csv。

计算或核对方式为在订单单头按order_date过滤后按supplier_id计数，再关联供应商名称。

### 第1轮

**输入**

> 按 PurchaseOrders.order_date 统计2025年上半年各供应商的采购订单数量，数量降序、并列时 supplier_id 升序，取前5名，显示供应商ID、名称和订单数量。

**预期结果**

**供应商采购订单数量排名**

| supplier_id | supplier_name | order_count |
| --- | --- | --- |
| 19 | New Cairo Device Parts | 44 |
| 23 | October Electronic Distribution | 37 |
| 2 | Misr Advanced Circuits | 35 |
| 4 | Canal Microelectronics | 32 |
| 12 | Giza Smart Components | 32 |


## D06 月度订单数量

分类为基础数据查询。

参照来源为 data/seed/PurchaseOrders.csv。

计算或核对方式为按order_date的YYYY-MM分组，并补齐1至6月。

### 第1轮

**输入**

> 按下单日期统计2025年上半年每月采购订单数量，显示月份和订单数量，月份用 YYYY-MM 表示并升序排列，缺失月份补0。

**预期结果**

**上半年每月采购订单数量**

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |


## D07 产品产量排名与日期解析

分类为基础数据查询。

参照来源为 data/seed/Production.csv、data/seed/Products.csv。

计算或核对方式为按actual_end_date（ISO日期）入期，汇总production_quantity。

### 第1轮

**输入**

> 以 Production.actual_end_date 为时间条件，统计2025年上半年各产品的 production_quantity 合计，按合计降序、并列时 product_id 升序取前5名，显示产品ID、名称和实际产量合计。

**预期结果**

**实际产量前5名产品**

| product_id | product_name | production_quantity |
| --- | --- | --- |
| 16 | Delta Sound Pro | 747731 |
| 15 | Canal Pods | 737762 |
| 13 | Nile Buds Lite | 602901 |
| 14 | Nile Buds Pro | 503491 |
| 12 | Sina Health Watch | 445806 |


## D08 产品BOM明细

分类为基础数据查询。

参照来源为 data/seed/ProductBOM.csv、data/seed/Materials.csv、data/seed/Products.csv。

计算或核对方式为过滤product_id=1，以material_id关联材料，按material_id升序。

### 第1轮

**输入**

> 查询产品PRD-001（Nile X1 Phone）的BOM，按material_id升序显示材料ID、材料名称、quantity_required和uom。

**预期结果**

**PRD-001物料清单**

| material_id | material_name | quantity_required | uom |
| --- | --- | --- | --- |
| 4 | Laptop Processor B | 1 | Piece |
| 16 | Laptop OLED Display 14 | 1 | Piece |
| 28 | SSD Module 512GB | 1 | Piece |
| 29 | SSD Module 1TB | 1 | Piece |
| 40 | Antenna Module | 1 | Piece |
| 42 | Phone Battery 5000mAh | 1 | Piece |
| 52 | Wide Camera Module 12MP | 2 | Piece |
| 54 | Laptop Webcam Module | 2 | Piece |
| 66 | Smartwatch Strap | 1 | Piece |
| 78 | Protective Packaging Insert | 1 | Piece |


## D09 采购明细金额汇总

分类为基础数据查询。

参照来源为 data/seed/PurchaseOrderDetails.csv、data/seed/PurchaseOrders.csv、data/seed/Suppliers.csv。

计算或核对方式为按订单下单日过滤，直接汇总明细total_amount，最终保留2位小数。

### 第1轮

**输入**

> 按订单下单日期统计2025年上半年各供应商采购明细total_amount合计，金额降序、并列时supplier_id升序取前三名，显示供应商ID、名称和金额合计，金额保留两位小数。

**预期结果**

**采购明细金额前三名**

| supplier_id | supplier_name | total_amount |
| --- | --- | --- |
| 11 | Al Sharqia Industrial Electronics | 23822313504.61 |
| 4 | Canal Microelectronics | 17258198901.81 |
| 19 | New Cairo Device Parts | 12173290662.86 |

参照注记

- 金额的币种和含税口径未由源文件提供。


## D10 库存差值及低值排序

分类为基础数据查询。

参照来源为 data/seed/RawMaterialInventory.csv、data/seed/Materials.csv。

计算或核对方式为按题设定义用Decimal逐行相减，过滤仓库后排序。

### 第1轮

**输入**

> 按库存快照查询 warehouse_id=1 的原材料，暂将可用数量定义为 quantity_on_hand 减 quantity_reserved，按可用数量升序、并列时 material_id 升序取5项，显示材料ID、名称、现存量、预留量和可用数量，数量保留两位小数。

**预期结果**

**仓库可用数量最低的5种材料**

| material_id | material_name | quantity_on_hand | quantity_reserved | available_quantity |
| --- | --- | --- | --- | --- |
| 31 | Main PCB Phone | 41311.56 | 1652.26 | 39659.30 |
| 16 | Laptop OLED Display 14 | 46554.05 | 1861.96 | 44692.09 |
| 79 | Warranty Card | 49158.56 | 1966.18 | 47192.38 |
| 76 | Laptop Power Cable | 56547.81 | 2262.07 | 54285.74 |
| 43 | Laptop Battery 50Wh | 60546.15 | 2422.05 | 58124.10 |


## D11 日期比较与延期天数

分类为基础数据查询。

参照来源为 data/seed/SupplierShipments.csv、data/seed/Suppliers.csv。

计算或核对方式为逐发货单计算actual_arrival_date减expected_arrival_date的日数，先过滤正差值再排序。

### 第1轮

**输入**

> 列出预计到货日在2025年上半年、实际到货日晚于预计到货日的发货单，按两日期相差的天数降序、并列时shipment_id升序取5张，显示发货单ID、编码、供应商名称、预计到货日、实际到货日及相差天数。

**预期结果**

**到货日期差值前5单**

| shipment_id | shipment_code | supplier_name | expected_arrival_date | actual_arrival_date | delay_days |
| --- | --- | --- | --- | --- | --- |
| 1119 | SSH-01119 | New Cairo Device Parts | 2025-01-20 | 2025-01-31 | 11 |
| 1744 | SSH-01744 | October Electronic Distribution | 2025-04-24 | 2025-05-05 | 11 |
| 2037 | SSH-02037 | October Electronic Distribution | 2025-03-30 | 2025-04-10 | 11 |
| 2197 | SSH-02197 | October Electronic Distribution | 2025-01-14 | 2025-01-25 | 11 |
| 1267 | SSH-01267 | October Electronic Distribution | 2025-05-19 | 2025-05-29 | 10 |


## D12 明确日期范围的空结果

分类为基础数据查询。

参照来源为 data/seed/Production.csv。

计算或核对方式为按actual_end_date筛选2026年1月，匹配行数为0。

### 第1轮

**输入**

> 查询实际完工日期在2026年1月的生产工单，按production_id升序列出工单ID、工单编码、产品ID和实际产量，最多5条。

**预期结果**

**2026年1月完工工单**

| production_id | production_order_code | product_id | production_quantity |
| --- | --- | --- | --- |

预期行数为0。

**空结果说明**

- 该日期条件下没有匹配的生产工单。


## D13 收货数量按月汇总

分类为基础数据查询。

参照来源为 data/seed/GoodsReceipts.csv、data/seed/GoodsReceiptDetails.csv。

计算或核对方式为按收货单receipt_date入期，将每条收货明细只计入一次。

### 第1轮

**输入**

> 按GoodsReceipts.receipt_date统计2025年上半年各月收货明细的received_quantity、accepted_quantity、rejected_quantity合计，显示月份和三种数量，按月份升序。

**预期结果**

**每月收货数量**

| month | received_quantity | accepted_quantity | rejected_quantity |
| --- | --- | --- | --- |
| 2025-01 | 15303027 | 15180151 | 122876 |
| 2025-02 | 13303610 | 13113955 | 189655 |
| 2025-03 | 13512250 | 13409583 | 102667 |
| 2025-04 | 14410635 | 14289469 | 121166 |
| 2025-05 | 13304709 | 13224379 | 80330 |
| 2025-06 | 11399310 | 11307180 | 92130 |


## D14 检验结果分组统计

分类为基础数据查询。

参照来源为 data/seed/QualityInspections.csv。

计算或核对方式为按inspection_date筛选后分组计数并求和defect_quantity。

### 第1轮

**输入**

> 按inspection_date筛选2025年上半年的来料检验记录，按inspection_result分组，显示检验结果、记录数和defect_quantity合计，按检验结果英文名升序。

**预期结果**

**检验结果统计**

| inspection_result | inspection_count | defect_quantity |
| --- | --- | --- |
| Partial Acceptance | 393 | 708824 |
| Passed | 1641 | 0 |


## D15 生产用料成本多表汇总

分类为基础数据查询。

参照来源为 data/seed/Production.csv、data/seed/ProductionDetails.csv、data/seed/ProductBOM.csv、data/seed/Materials.csv。

计算或核对方式为按工单实际完工日和product_id=1过滤，再经bom_id确定材料，汇总用料明细total_material_cost。

### 第1轮

**输入**

> 查询产品ID为1、实际完工日在2025年上半年的工单，按材料汇总ProductionDetails.total_material_cost，金额降序、并列时material_id升序取前5名，显示材料ID、名称和成本合计，保留两位小数。

**预期结果**

**产品1用料成本前5名**

| material_id | material_name | total_material_cost |
| --- | --- | --- |
| 29 | SSD Module 1TB | 1236025142.50 |
| 52 | Wide Camera Module 12MP | 1234699907.38 |
| 16 | Laptop OLED Display 14 | 1162863483.18 |
| 28 | SSD Module 512GB | 1076214602.99 |
| 54 | Laptop Webcam Module | 940580063.99 |

参照注记

- 金额的币种和含税口径未由源文件提供。


## D16 收货明细追溯采购行

分类为基础数据查询。

参照来源为 data/seed/GoodsReceiptDetails.csv、data/seed/GoodsReceipts.csv、data/seed/SupplierShipmentDetails.csv、data/seed/SupplierShipments.csv、data/seed/Materials.csv。

计算或核对方式为从gr_id=1的明细分别通过shipment_detail_id和收货单shipment_id追溯采购行及采购单。

### 第1轮

**输入**

> 追溯gr_id=1的收货明细，按gr_detail_id升序显示收货明细ID、采购单ID、采购明细ID、材料ID、材料名称以及GoodsReceiptDetails.received_quantity。

**预期结果**

**收货单1采购追溯**

| gr_detail_id | po_id | po_detail_id | material_id | material_name | received_quantity |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1 | 1 | Mobile Processor A | 72300 |
| 2 | 1 | 2 | 6 | Smartwatch Controller B | 100950 |
| 3 | 1 | 3 | 11 | OLED Phone Display 6.1 | 156830 |


## I01 订单排名柱状图

分类为结果交互。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/Suppliers.csv。

计算或核对方式为图表沿用前一轮标准表的5个名称和数量。

### 第1轮

**输入**

> 按 PurchaseOrders.order_date 统计2025年上半年各供应商的采购订单数量，数量降序、并列时 supplier_id 升序，取前5名，显示供应商ID、名称和订单数量。

**预期结果**

**供应商采购订单数量排名**

| supplier_id | supplier_name | order_count |
| --- | --- | --- |
| 19 | New Cairo Device Parts | 44 |
| 23 | October Electronic Distribution | 37 |
| 2 | Misr Advanced Circuits | 35 |
| 4 | Canal Microelectronics | 32 |
| 12 | Giza Smart Components | 32 |

### 第2轮

**输入**

> 把刚才的前5名结果画成竖向柱状图，横轴为供应商名称，纵轴为订单数量，顺序沿用刚才的排名。

**预期结果**

**供应商订单数量柱状图**

预期展示竖向条形图，共5个数据点。类别为 supplier_name，数值为 order_count。

| supplier_name | order_count |
| --- | --- |
| New Cairo Device Parts | 44 |
| October Electronic Distribution | 37 |
| Misr Advanced Circuits | 35 |
| Canal Microelectronics | 32 |
| Giza Smart Components | 32 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级


## I02 月度订单折线图

分类为结果交互。

参照来源为 data/seed/PurchaseOrders.csv。

计算或核对方式为折线图依次使用1至6月的6个数量。

### 第1轮

**输入**

> 按下单日期统计2025年上半年每月采购订单数量，显示月份和订单数量，月份用 YYYY-MM 表示并升序排列，缺失月份补0。

**预期结果**

**上半年每月采购订单数量**

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |

### 第2轮

**输入**

> 将刚才的每月采购订单数量画成折线图，横轴月份升序，纵轴订单数量，展示全部6个月。

**预期结果**

**月度订单趋势**

预期展示折线图，共6个数据点。类别为 month，数值为 order_count。

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级


## I03 基于结果制作派生表

分类为结果交互。

参照来源为 data/seed/Products.csv。

计算或核对方式为仅对前一轮5条产品数据逐行计算售价减标准成本并排序。

### 第1轮

**输入**

> 按 product_id 升序列出前5个产品，显示产品ID、编码、名称、类别、标准成本和销售价格。

**预期结果**

**前5个产品**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone | Smartphone | 23614.93 | 31644.01 |
| 2 | PRD-002 | Nile X1 Pro Phone | Smartphone | 18827.23 | 25228.49 |
| 3 | PRD-003 | Canal S2 Phone | Smartphone | 14741.67 | 19753.84 |
| 4 | PRD-004 | Canal S2 Max Phone | Smartphone | 29386.99 | 39378.57 |
| 5 | PRD-005 | Sina Air 14 Laptop | Laptop | 22462.35 | 28751.81 |

### 第2轮

**输入**

> 仅基于刚才这5个产品制作一张新表，显示产品名称、标准成本、售价和售价减标准成本的差额，按差额降序，金额保留两位小数。

**预期结果**

**五个产品售价成本差额**

| product_name | standard_cost | selling_price | price_cost_difference |
| --- | --- | --- | --- |
| Canal S2 Max Phone | 29386.99 | 39378.57 | 9991.58 |
| Nile X1 Phone | 23614.93 | 31644.01 | 8029.08 |
| Nile X1 Pro Phone | 18827.23 | 25228.49 | 6401.26 |
| Sina Air 14 Laptop | 22462.35 | 28751.81 | 6289.46 |
| Canal S2 Phone | 14741.67 | 19753.84 | 5012.17 |

参照注记

- 金额的币种和含税口径未由源文件提供。
- 本题差额按售价减标准成本定义，源文件没有提供净利润口径。


## I04 解读产量排名

分类为结果交互。

参照来源为 data/seed/Production.csv、data/seed/Products.csv。

计算或核对方式为读取前5名表的首行与末行，直接相减。

### 第1轮

**输入**

> 以 Production.actual_end_date 为时间条件，统计2025年上半年各产品的 production_quantity 合计，按合计降序、并列时 product_id 升序取前5名，显示产品ID、名称和实际产量合计。

**预期结果**

**实际产量前5名产品**

| product_id | product_name | production_quantity |
| --- | --- | --- |
| 16 | Delta Sound Pro | 747731 |
| 15 | Canal Pods | 737762 |
| 13 | Nile Buds Lite | 602901 |
| 14 | Nile Buds Pro | 503491 |
| 12 | Sina Health Watch | 445806 |

### 第2轮

**输入**

> 解读刚才这份前5名结果，说明第一名和第五名分别是谁、产量各是多少，以及第一名比第五名多多少。

**预期结果**

**前5名首尾比较**

| first_product | first_quantity | fifth_product | fifth_quantity | difference |
| --- | --- | --- | --- | --- |
| Delta Sound Pro | 747731 | Sina Health Watch | 445806 | 301925 |

判定要求

- 比较对象为这份前5名表的第一名与第五名


## I05 库存结果报告

分类为结果交互。

参照来源为 data/seed/RawMaterialInventory.csv、data/seed/Materials.csv。

计算或核对方式为报告表格与前轮5项一致，最低值由表首行给出。

### 第1轮

**输入**

> 按库存快照查询 warehouse_id=1 的原材料，暂将可用数量定义为 quantity_on_hand 减 quantity_reserved，按可用数量升序、并列时 material_id 升序取5项，显示材料ID、名称、现存量、预留量和可用数量，数量保留两位小数。

**预期结果**

**仓库可用数量最低的5种材料**

| material_id | material_name | quantity_on_hand | quantity_reserved | available_quantity |
| --- | --- | --- | --- | --- |
| 31 | Main PCB Phone | 41311.56 | 1652.26 | 39659.30 |
| 16 | Laptop OLED Display 14 | 46554.05 | 1861.96 | 44692.09 |
| 79 | Warranty Card | 49158.56 | 1966.18 | 47192.38 |
| 76 | Laptop Power Cable | 56547.81 | 2262.07 | 54285.74 |
| 43 | Laptop Battery 50Wh | 60546.15 | 2422.05 | 58124.10 |

### 第2轮

**输入**

> 把刚才5项结果整理为一份简短Markdown报告，包含统计时点、可用数量公式、完整5行表格和最低可用数量的材料说明。

**预期结果**

**仓库1低可用量材料报告**

- 库存快照时点为2025-12-31 18:00:00。
- 可用数量按现存量减预留量计算。
- 最低项为Main PCB Phone，可用数量39659.30。

| material_id | material_name | quantity_on_hand | quantity_reserved | available_quantity |
| --- | --- | --- | --- | --- |
| 31 | Main PCB Phone | 41311.56 | 1652.26 | 39659.30 |
| 16 | Laptop OLED Display 14 | 46554.05 | 1861.96 | 44692.09 |
| 79 | Warranty Card | 49158.56 | 1966.18 | 47192.38 |
| 76 | Laptop Power Cable | 56547.81 | 2262.07 | 54285.74 |
| 43 | Laptop Battery 50Wh | 60546.15 | 2422.05 | 58124.10 |

- 当前报告使用单时点快照。


## I06 产品类别饼图与占比

分类为结果交互。

参照来源为 data/seed/Products.csv。

计算或核对方式为四类各4条，以16为分母计算占比。

### 第1轮

**输入**

> 统计每个 product_category 的产品数量，按类别英文名升序，显示类别和产品数量。

**预期结果**

**产品类别数量**

| product_category | product_count |
| --- | --- |
| Earbuds | 4 |
| Laptop | 4 |
| Smartphone | 4 |
| Smartwatch | 4 |

### 第2轮

**输入**

> 根据刚才的类别数量制作饼图，标出类别名、数量和百分比。

**预期结果**

**产品类别占比**

预期展示饼图，共4个数据点。类别为 product_category，数值为 product_count。

| product_category | product_count |
| --- | --- |
| Earbuds | 4 |
| Laptop | 4 |
| Smartphone | 4 |
| Smartwatch | 4 |

各类别百分比

| product_category | percentage |
| --- | --- |
| Earbuds | 25.00 |
| Laptop | 25.00 |
| Smartphone | 25.00 |
| Smartwatch | 25.00 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级


## M01 概念解释与数据查询

分类为多需求。

参照来源为 data/seed/Products.csv。

计算或核对方式为概念按定义判断，产品数独立计数。

### 第1轮

**输入**

> 先解释算术平均值，再查询Products中的产品记录总数。

**预期结果**

**平均值解释**

- 算术平均值等于数值总和除以数值个数。

**产品总数**

| product_count |
| --- |
| 16 |

判定要求

- 概念解释和产品总数两项均有答案


## M02 两类实体的独立明细

分类为多需求。

参照来源为 data/seed/Products.csv、data/seed/Suppliers.csv。

计算或核对方式为分别按各自的整数ID排序取指定数量。

### 第1轮

**输入**

> 分别列出product_id最小的前2个产品，以及supplier_id最小的前3个供应商，两张表都显示ID、编码和名称。

**预期结果**

**前2个产品**

| product_id | product_code | product_name |
| --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone |
| 2 | PRD-002 | Nile X1 Pro Phone |

**前3个供应商**

| supplier_id | supplier_code | supplier_name |
| --- | --- | --- |
| 1 | SUP-001 | Al Nile Electronic Components |
| 2 | SUP-002 | Misr Advanced Circuits |
| 3 | SUP-003 | Suez Tech Supplies |

判定要求

- 产品表2行，供应商表3行，数量条件分别生效


## M03 企业关系解释与订单趋势表

分类为多需求。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/SupplierShipments.csv、data/seed/GoodsReceipts.csv。

计算或核对方式为关系来自登记关系，月度数量来自订单单头日期计数。

### 第1轮

**输入**

> 先解释采购订单单头经过哪些表关联到收货单，再按下单日期统计2025年上半年每月采购订单数量，月份升序，缺失月份补0。

**预期结果**

**采购单头到收货单的关系**

- PurchaseOrders.po_id 关联 SupplierShipments.po_id。
- SupplierShipments.shipment_id 关联 GoodsReceipts.shipment_id。
- 从采购单头到收货单的路径为 PurchaseOrders → SupplierShipments → GoodsReceipts。
- GoodsReceipts 的单头标识为 gr_id，收货日期字段为 receipt_date。

**上半年每月采购订单数量**

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |


## M04 查询类别并绘图和解释

分类为多需求。

参照来源为 data/seed/Products.csv。

计算或核对方式为一次计数得到四类数量，图与解释使用同一结果。

### 第1轮

**输入**

> 统计每个产品类别的数量，按类别英文名升序列出结果，再根据这份结果画饼图，并解释各类别占全部产品的比例。

**预期结果**

**产品类别数量**

| product_category | product_count |
| --- | --- |
| Earbuds | 4 |
| Laptop | 4 |
| Smartphone | 4 |
| Smartwatch | 4 |

**产品类别占比**

预期展示饼图，共4个数据点。类别为 product_category，数值为 product_count。

| product_category | product_count |
| --- | --- |
| Earbuds | 4 |
| Laptop | 4 |
| Smartphone | 4 |
| Smartwatch | 4 |

各类别百分比

| product_category | percentage |
| --- | --- |
| Earbuds | 25.00 |
| Laptop | 25.00 |
| Smartphone | 25.00 |
| Smartwatch | 25.00 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级

**类别占比解释**

- 共有4个类别，每类4个产品。
- 总计16个产品，每个类别占25%。


## M05 不同供应商与年份的条件隔离

分类为多需求。

参照来源为 data/seed/PurchaseOrders.csv。

计算或核对方式为按两个独立的supplier_id和order_date范围分别计数。

### 第1轮

**输入**

> 分别统计supplier_id=1在2024年下单的采购订单数量，以及supplier_id=2在2025年下单的采购订单数量，明确标出各自供应商ID、年份和订单数。

**预期结果**

**独立条件统计**

| supplier_id | year | order_count |
| --- | --- | --- |
| 1 | 2024 | 51 |
| 2 | 2025 | 35 |


## M06 可回答需求与缺失数据并存

分类为多需求。

参照来源为 data/seed/Products.csv。

计算或核对方式为产品前3条可确定，设备故障数据缺失由表集合与limitations确认。

### 第1轮

**输入**

> 列出product_id最小的前3个产品的ID、编码和名称，同时统计2025年上半年每台设备的故障次数。

**预期结果**

**前3个产品**

| product_id | product_code | product_name |
| --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone |
| 2 | PRD-002 | Nile X1 Pro Phone |
| 3 | PRD-003 | Canal S2 Phone |

**设备故障部分**

- 当前项目缺少设备及设备故障记录，无法从当前数据确定每台设备的故障次数。
- 完成故障统计需要补充设备标识、故障时间等设备故障数据。

判定要求

- 产品列表正常交付
- 设备故障部分明确说明数据缺口


## M07 两套数据与两张图的对应

分类为多需求。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/Products.csv。

计算或核对方式为分别计算月度订单数量与产品类别数量，逐图核对对应数据。

### 第1轮

**输入**

> 按下单日期统计2025年上半年每月采购订单数量并画折线图，缺失月份补0；同时统计各产品类别的产品数量并画柱状图，类别按英文名升序。请同时展示两份数据表和两张图。

**预期结果**

**上半年每月采购订单数量**

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |

**每月采购订单**

预期展示折线图，共6个数据点。类别为 month，数值为 order_count。

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级

**产品类别数量**

| product_category | product_count |
| --- | --- |
| Earbuds | 4 |
| Laptop | 4 |
| Smartphone | 4 |
| Smartwatch | 4 |

**各类别产品数量**

预期展示竖向条形图，共4个数据点。类别为 product_category，数值为 product_count。

| product_category | product_count |
| --- | --- |
| Earbuds | 4 |
| Laptop | 4 |
| Smartphone | 4 |
| Smartwatch | 4 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级

判定要求

- 月度数据对应折线图，产品类别数据对应柱状图


## M08 已有结果绘图与字段解释

分类为多需求。

参照来源为 data/seed/Production.csv、data/seed/Products.csv。

计算或核对方式为先固定实际产量前5名，再绘图并按字段字典解释数量。

### 第1轮

**输入**

> 以 Production.actual_end_date 为时间条件，统计2025年上半年各产品的 production_quantity 合计，按合计降序、并列时 product_id 升序取前5名，显示产品ID、名称和实际产量合计。

**预期结果**

**实际产量前5名产品**

| product_id | product_name | production_quantity |
| --- | --- | --- |
| 16 | Delta Sound Pro | 747731 |
| 15 | Canal Pods | 737762 |
| 13 | Nile Buds Lite | 602901 |
| 14 | Nile Buds Pro | 503491 |
| 12 | Sina Health Watch | 445806 |

### 第2轮

**输入**

> 把刚才的前5名画成横向条形图，顺序沿用排名，再解释Production中的实际产量与计划量有什么区别。

**预期结果**

**产量前5名横向图**

预期展示横向条形图，共5个数据点。类别为 product_name，数值为 production_quantity。

| product_name | production_quantity |
| --- | --- |
| Delta Sound Pro | 747731 |
| Canal Pods | 737762 |
| Nile Buds Lite | 602901 |
| Nile Buds Pro | 503491 |
| Sina Health Watch | 445806 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级

**实际产量与计划量**

- planned_quantity 是生产工单计划数量。
- production_quantity 是生产工单记录的实际生产数量。

- 原文件未确认 production_quantity 是否包含 rejected_quantity，解释实际产量时需保留此口径限制。


## X01 产品条数由5改2

分类为跨轮修改。

参照来源为 data/seed/Products.csv。

计算或核对方式为第二轮只改变limit，保留全部输出字段和ID升序。

### 第1轮

**输入**

> 按 product_id 升序列出前5个产品，显示产品ID、编码、名称、类别、标准成本和销售价格。

**预期结果**

**前5个产品**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone | Smartphone | 23614.93 | 31644.01 |
| 2 | PRD-002 | Nile X1 Pro Phone | Smartphone | 18827.23 | 25228.49 |
| 3 | PRD-003 | Canal S2 Phone | Smartphone | 14741.67 | 19753.84 |
| 4 | PRD-004 | Canal S2 Max Phone | Smartphone | 29386.99 | 39378.57 |
| 5 | PRD-005 | Sina Air 14 Laptop | Laptop | 22462.35 | 28751.81 |

### 第2轮

**输入**

> 产品只列前2个，其他要求沿用。

**预期结果**

**前2个产品**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone | Smartphone | 23614.93 | 31644.01 |
| 2 | PRD-002 | Nile X1 Pro Phone | Smartphone | 18827.23 | 25228.49 |

判定要求

- 本轮产品明细为2行，字段和排序沿用前轮


## X02 产量统计半年改季度

分类为跨轮修改。

参照来源为 data/seed/Production.csv、data/seed/Products.csv。

计算或核对方式为将actual_end_date范围改为2025-04-01至2025-07-01，重算产品合计排名。

### 第1轮

**输入**

> 以 Production.actual_end_date 为时间条件，统计2025年上半年各产品的 production_quantity 合计，按合计降序、并列时 product_id 升序取前5名，显示产品ID、名称和实际产量合计。

**预期结果**

**实际产量前5名产品**

| product_id | product_name | production_quantity |
| --- | --- | --- |
| 16 | Delta Sound Pro | 747731 |
| 15 | Canal Pods | 737762 |
| 13 | Nile Buds Lite | 602901 |
| 14 | Nile Buds Pro | 503491 |
| 12 | Sina Health Watch | 445806 |

### 第2轮

**输入**

> 将统计时间改为2025年第二季度，仍按实际完工日期筛选，其他要求沿用。

**预期结果**

**实际产量前5名产品**

| product_id | product_name | production_quantity |
| --- | --- | --- |
| 16 | Delta Sound Pro | 418640 |
| 13 | Nile Buds Lite | 377210 |
| 15 | Canal Pods | 351213 |
| 14 | Nile Buds Pro | 224299 |
| 2 | Nile X1 Pro Phone | 215944 |


## X03 供应商订单排名换年份

分类为跨轮修改。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/Suppliers.csv。

计算或核对方式为时间范围由2025上半年改为2024上半年，保留order_date与排名规则。

### 第1轮

**输入**

> 按 PurchaseOrders.order_date 统计2025年上半年各供应商的采购订单数量，数量降序、并列时 supplier_id 升序，取前5名，显示供应商ID、名称和订单数量。

**预期结果**

**供应商采购订单数量排名**

| supplier_id | supplier_name | order_count |
| --- | --- | --- |
| 19 | New Cairo Device Parts | 44 |
| 23 | October Electronic Distribution | 37 |
| 2 | Misr Advanced Circuits | 35 |
| 4 | Canal Microelectronics | 32 |
| 12 | Giza Smart Components | 32 |

### 第2轮

**输入**

> 改查2024年上半年，其他要求沿用。

**预期结果**

**供应商采购订单数量排名**

| supplier_id | supplier_name | order_count |
| --- | --- | --- |
| 6 | Cairo Battery Systems | 45 |
| 9 | Sinai Semiconductor Distribution | 44 |
| 3 | Suez Tech Supplies | 40 |
| 16 | Tanta Packaging Industries | 40 |
| 5 | Delta Display Solutions | 39 |


## X04 仅修改图表方向

分类为跨轮修改。

参照来源为 data/seed/PurchaseOrders.csv、data/seed/Suppliers.csv。

计算或核对方式为三轮均使用2025上半年同一份5行排名，仅第三轮改变方向。

### 第1轮

**输入**

> 按 PurchaseOrders.order_date 统计2025年上半年各供应商的采购订单数量，数量降序、并列时 supplier_id 升序，取前5名，显示供应商ID、名称和订单数量。

**预期结果**

**供应商采购订单数量排名**

| supplier_id | supplier_name | order_count |
| --- | --- | --- |
| 19 | New Cairo Device Parts | 44 |
| 23 | October Electronic Distribution | 37 |
| 2 | Misr Advanced Circuits | 35 |
| 4 | Canal Microelectronics | 32 |
| 12 | Giza Smart Components | 32 |

### 第2轮

**输入**

> 把刚才结果画成竖向柱状图，横轴供应商名称，纵轴订单数量，顺序沿用排名。

**预期结果**

**供应商排名竖向图**

预期展示竖向条形图，共5个数据点。类别为 supplier_name，数值为 order_count。

| supplier_name | order_count |
| --- | --- |
| New Cairo Device Parts | 44 |
| October Electronic Distribution | 37 |
| Misr Advanced Circuits | 35 |
| Canal Microelectronics | 32 |
| Giza Smart Components | 32 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级

### 第3轮

**输入**

> 把刚才这张图改为横向条形图，数量和排名顺序沿用。

**预期结果**

**供应商排名横向图**

预期展示横向条形图，共5个数据点。类别为 supplier_name，数值为 order_count。

| supplier_name | order_count |
| --- | --- |
| New Cairo Device Parts | 44 |
| October Electronic Distribution | 37 |
| Misr Advanced Circuits | 35 |
| Canal Microelectronics | 32 |
| Giza Smart Components | 32 |

- 类别标签与数值逐项对应
- 展示全部预期数据点
- 数值轴表达真实量级


## X05 库存查询切换仓库

分类为跨轮修改。

参照来源为 data/seed/RawMaterialInventory.csv、data/seed/Materials.csv。

计算或核对方式为将warehouse_id由1改2，保留差值定义、字段、排序和5行限制。

### 第1轮

**输入**

> 按库存快照查询 warehouse_id=1 的原材料，暂将可用数量定义为 quantity_on_hand 减 quantity_reserved，按可用数量升序、并列时 material_id 升序取5项，显示材料ID、名称、现存量、预留量和可用数量，数量保留两位小数。

**预期结果**

**仓库可用数量最低的5种材料**

| material_id | material_name | quantity_on_hand | quantity_reserved | available_quantity |
| --- | --- | --- | --- | --- |
| 31 | Main PCB Phone | 41311.56 | 1652.26 | 39659.30 |
| 16 | Laptop OLED Display 14 | 46554.05 | 1861.96 | 44692.09 |
| 79 | Warranty Card | 49158.56 | 1966.18 | 47192.38 |
| 76 | Laptop Power Cable | 56547.81 | 2262.07 | 54285.74 |
| 43 | Laptop Battery 50Wh | 60546.15 | 2422.05 | 58124.10 |

### 第2轮

**输入**

> 将仓库改为warehouse_id=2，其他要求沿用。

**预期结果**

**仓库可用数量最低的5种材料**

| material_id | material_name | quantity_on_hand | quantity_reserved | available_quantity |
| --- | --- | --- | --- | --- |
| 31 | Main PCB Phone | 41339.38 | 1653.78 | 39685.60 |
| 16 | Laptop OLED Display 14 | 49235.60 | 1969.62 | 47265.98 |
| 79 | Warranty Card | 49562.37 | 1982.65 | 47579.72 |
| 76 | Laptop Power Cable | 55914.15 | 2236.41 | 53677.74 |
| 43 | Laptop Battery 50Wh | 60168.79 | 2406.55 | 57762.24 |


## X06 多项需求中的局部修改

分类为跨轮修改。

参照来源为 data/seed/Products.csv、data/seed/Suppliers.csv。

计算或核对方式为第一轮两张明细表，第二轮只更新产品列表的行数。

### 第1轮

**输入**

> 分别按ID升序列出前5个产品和前3个供应商，各显示ID、编码和名称。

**预期结果**

**前5个产品**

| product_id | product_code | product_name |
| --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone |
| 2 | PRD-002 | Nile X1 Pro Phone |
| 3 | PRD-003 | Canal S2 Phone |
| 4 | PRD-004 | Canal S2 Max Phone |
| 5 | PRD-005 | Sina Air 14 Laptop |

**前3个供应商**

| supplier_id | supplier_code | supplier_name |
| --- | --- | --- |
| 1 | SUP-001 | Al Nile Electronic Components |
| 2 | SUP-002 | Misr Advanced Circuits |
| 3 | SUP-003 | Suez Tech Supplies |

### 第2轮

**输入**

> 只把产品列表改成前3个，这轮只展示修改后的产品列表。

**预期结果**

**前3个产品**

| product_id | product_code | product_name |
| --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone |
| 2 | PRD-002 | Nile X1 Pro Phone |
| 3 | PRD-003 | Canal S2 Phone |

判定要求

- 本轮只展示3个产品的列表


## X07 多轮排序与类别条件继承

分类为跨轮修改。

参照来源为 data/seed/Products.csv。

计算或核对方式为先ID排序5行，再全产品售价降序5行，最后按Laptop过滤并保留售价降序取3行。

### 第1轮

**输入**

> 按 product_id 升序列出前5个产品，显示产品ID、编码、名称、类别、标准成本和销售价格。

**预期结果**

**前5个产品**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 1 | PRD-001 | Nile X1 Phone | Smartphone | 23614.93 | 31644.01 |
| 2 | PRD-002 | Nile X1 Pro Phone | Smartphone | 18827.23 | 25228.49 |
| 3 | PRD-003 | Canal S2 Phone | Smartphone | 14741.67 | 19753.84 |
| 4 | PRD-004 | Canal S2 Max Phone | Smartphone | 29386.99 | 39378.57 |
| 5 | PRD-005 | Sina Air 14 Laptop | Laptop | 22462.35 | 28751.81 |

### 第2轮

**输入**

> 改按售价降序，售价相同时按product_id升序，仍列前5个，显示字段沿用。

**预期结果**

**售价最高的5个产品**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 14 | PRD-014 | Nile Buds Pro | Earbuds | 28827.24 | 40934.68 |
| 4 | PRD-004 | Canal S2 Max Phone | Smartphone | 29386.99 | 39378.57 |
| 15 | PRD-015 | Canal Pods | Earbuds | 23094.78 | 32794.59 |
| 8 | PRD-008 | Delta Creator 16 Laptop | Laptop | 25526.58 | 32674.03 |
| 1 | PRD-001 | Nile X1 Phone | Smartphone | 23614.93 | 31644.01 |

### 第3轮

**输入**

> 只看Laptop类别，数量改为前3个，其他要求沿用。

**预期结果**

**售价最高的3个Laptop**

| product_id | product_code | product_name | product_category | standard_cost | selling_price |
| --- | --- | --- | --- | --- | --- |
| 8 | PRD-008 | Delta Creator 16 Laptop | Laptop | 25526.58 | 32674.03 |
| 5 | PRD-005 | Sina Air 14 Laptop | Laptop | 22462.35 | 28751.81 |
| 6 | PRD-006 | Sina Pro 15 Laptop | Laptop | 19782.23 | 25321.25 |

判定要求

- 从完整Laptop类别数据筛选后排序取3条


## X08 时间范围改为空数据并补零

分类为跨轮修改。

参照来源为 data/seed/PurchaseOrders.csv。

计算或核对方式为将月份范围整体改为2026上半年，沿用缺失月份补0的要求。

### 第1轮

**输入**

> 按下单日期统计2025年上半年每月采购订单数量，显示月份和订单数量，月份用 YYYY-MM 表示并升序排列，缺失月份补0。

**预期结果**

**上半年每月采购订单数量**

| month | order_count |
| --- | --- |
| 2025-01 | 127 |
| 2025-02 | 113 |
| 2025-03 | 103 |
| 2025-04 | 102 |
| 2025-05 | 110 |
| 2025-06 | 86 |

### 第2轮

**输入**

> 年份改成2026年，仍统计上半年，其他要求沿用。

**预期结果**

**上半年每月采购订单数量**

| month | order_count |
| --- | --- |
| 2026-01 | 0 |
| 2026-02 | 0 |
| 2026-03 | 0 |
| 2026-04 | 0 |
| 2026-05 | 0 |
| 2026-06 | 0 |

**数据范围说明**

- 2026年上半年没有匹配的采购订单记录，按补0约定展示1至6月各0单。
