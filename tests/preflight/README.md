# tests/preflight —— 离线前置核对（不调用 Agent、不调用模型）

这里回答一个问题：**"这套题库和参考答案，本身对不对？"**
全部命令只读 CSV、只查数据库、只跑本地 JS，不触发 Agent，不消耗模型额度。

```
preflight/
├── build_cases.py            ① 从 data/seed/*.csv 生成 cases.json 与 docs/testing/Agent黑盒测试集.md
├── cases.json                机器可读语料（52 案例 / 69 轮 / 83 个期望交付）
├── dataset.json              交付件：只有 query + answer + 判定方式（69 行）
├── export_dataset.py         ② cases.json → dataset.json + ../agent-eval/promptfoo-tests.json
├── verify_reference.py       ③ 用只读 SQL 重推参考答案（52 结果集 / 224 行）
├── verify_dataset.py         ④ CSV↔DB 逐格、结构事实、覆盖率、行归属、字典核对 → reference-verification.json
├── selftest.js               ⑤ 判分器离线自测（7 条，不需要 Agent）
├── reference-verification.json   最近一次核对报告（ok: true）
└── README.md
```

## 四条命令（按顺序）

```powershell
cd C:\Users\Lenovo\Desktop\LLM\Project
$env:PYTHONPATH="src"

# ① 改数据或改题后：重建语料与题面
.\.venv\Scripts\python.exe tests\preflight\build_cases.py

# ② 导出交付件与 agent-eval 的测试文件
.\.venv\Scripts\python.exe tests\preflight\export_dataset.py

# ③ 参考答案的 SQL 复核（需要数据库在跑）
.\.venv\Scripts\python.exe tests\preflight\verify_reference.py

# ④ 参考答答案的来源核对 + 覆盖率（需要数据库在跑）
.\.venv\Scripts\python.exe tests\preflight\verify_dataset.py

# ⑤ 判分器自测（纯本地，不需要数据库）
node tests\preflight\selftest.js
```

## 各项检查在验什么

| 检查 | 内容 | 当前结果 |
|---|---|---|
| ③ `verify_reference.py` | 用独立 SQL 重算每张表的期望值、逐行比对 | 52 个结果集 / 224 行全部一致 |
| ④ CSV↔DB | 16 张表按主键逐单元格比对 | 0 处不一致 |
| ④ 结构事实 | 类别 8 种各 10 条、country=1、city=14、address=17、粒度链、日期无斜杠 | 8/8 |
| ④ 覆盖率 | 83 个期望交付中 62 个有 SQL/字典级复核证据，21 个约定交 LLM 判定 | 无遗漏 |
| ④ 行归属 | 14 处"单表投影"的期望行逐行回查物理表 | 14/14 |
| ④ 字典 | K02 的 8 个字段名与释义对 `information_schema` 与数据字典 | 通过 |
| ⑤ `selftest.js` | 判分器对"正确/数值错/缺产物/重复表/图表单点错/文字委托"的判定 | 7/7 |

`83 = 52 表 + 9 图 + 21 文字（含 1 份报告）`；`62 = 52 表 + 9 图 + K02 字典表`。

## 数据改动后的重生成顺序

1. 清洗/替换 CSV（`ops/normalize_seed.py`、`ops/audit_seed_diff.py` 见 `ops/`）；
2. `build_cases.py` → `export_dataset.py`；
3. `verify_reference.py` + `verify_dataset.py` + `selftest.js` 全绿；
4. 才去 `../agent-eval` 跑真实测试。

## 边界

- 这里**永远不会**调用 Agent；`provider.js` 在 `../agent-eval`。
- 文字类答案的真值不在数据库里，只能用 `facts + limitations` 的约定约束，因此 21 个交付在跑测试时才由 LLM 判定。
- 若数据重建导致快照变化，`cases.json` 与 `dataset.json` 的 `snapshot_id` 会同步更新，`reference-verification.json` 需要重跑生成。
