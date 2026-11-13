# 意图识别测试（阶段级）

两套 Promptfoo 测试，都只覆盖**意图识别阶段**，不会执行 Task、不查业务库、不生成回答：

| 目录 | 是什么 | 会碰什么 | 每条成本 |
|---|---|---|---|
| `e2e/` | **测试A 意图识别**：走应用自身的意图阶段 | `POST /bind`（预览）+ 读 `/sessions/{id}/events` 的 `intent_resolved` | 约 2 次模型调用（intent + prepare） |
| `prompt-ab/` | **测试B 提示词改进**：同一语料对比 `intent-v1` / `intent-v2` | 直连模型，不碰应用、不建会话 | 1 次模型调用 × 变体数 |

```
tests/stages/intent/
├── cases/
│   ├── build_intent_cases.py     题库生成（读 tests/new/intent/cases.json）
│   └── intent-cases.json         52 条：query + 固定上下文 + 期望字段 + instruction 要素
├── shared/
│   ├── assert-intent.js          两个测试共用的判定（字段 + instruction）
│   └── intent-schema.json        Intent 的 JSON Schema（直连模型时注入）
├── e2e/
│   ├── promptfooconfig.yaml      测试A 配置
│   ├── provider-app.js           调 /bind，取 intent 事件
│   └── promptfoo-tests.json      52 条测试（生成物）
├── prompt-ab/
│   ├── promptfooconfig.yaml      测试B 配置（prompts: v1 / v2）
│   ├── provider-llm.js           直连模型，按线上同样的 system 包装
│   ├── prompts/intent-v1.txt     当前线上提示词（从源码抽取，勿手改）
│   ├── prompts/intent-v2.txt     候选提示词（人工迭代这一份）
│   └── promptfoo-tests.json      同一套 52 条（生成物）
├── selftest.js                   判分器离线自测（8 条，不调模型）
└── README.md
```

## 相比原测试集补了什么

原 `docs/testing/意图识别测试集.md` 只评价 `category / action / refs / request_id / clarifications`，
**拆分出的 `instruction` 内容没有校对**。现在每条期望操作都带 `instruction` 校对项：

1. **确定性要素**：标识符（表名/字段名）、年份、时间短语（上半年/季度…）、排序与取数（升序/降序/前5/排名）、交付形态（柱状图/报告…）必须出现在实际 `instruction` 中；允许中文别名（`production_quantity` ↔ `实际产量` ↔ `产量`）；`instruction` 长度不得显著短于 query（≥45%，仅对 ≥15 字的 query 生效）。
2. **语义要点**（`llm-rubric`）：是否保留业务对象与口径、是否补全指代、`update_task` 是否只描述本次变化且不新增交付、引用来源是否与 refs 一致。

规则与逐例清单（66 个操作中 57 个有硬性要素）已追加到
[docs/testing/意图识别测试集.md](../../../docs/testing/意图识别测试集.md) 的「指令校对（instruction）」一节，由生成器维护，重复执行不会重复插入。

## 重建题库（离线，不调模型）

```powershell
cd C:\Users\Lenovo\Desktop\LLM\Project; $env:PYTHONPATH="src"
.\.venv\Scripts\python.exe tests\stages\intent\cases\build_intent_cases.py

node tests\stages\intent\selftest.js      # 判分器自测：8/8
```

## 跑测试A：意图识别（端到端）

```powershell
cd C:\Users\Lenovo\Desktop\LLM\Project\tests\stages\intent\e2e
$c = Get-Content ..\..\..\..\runtime\promptfoo-credentials.json -Raw | ConvertFrom-Json
$env:APP_URL="http://127.0.0.1:8000"; $env:APP_USER=$c.username; $env:APP_PASSWORD=$c.password
$env:DEEPSEEK_API_KEY="<.env 的 LLM_API_KEY>"     # 仅 instruction 语义判定需要

npx --yes promptfoo@latest eval -c promptfooconfig.yaml --max-concurrency 2 --no-cache
npx --yes promptfoo@latest eval -c promptfooconfig.yaml --filter-pattern "^G3" --no-cache
npx --yes promptfoo@latest view
```

## 跑测试B：提示词改进（A/B）

```powershell
cd C:\Users\Lenovo\Desktop\LLM\Project\tests\stages\intent\prompt-ab
$env:LLM_URL="https://api.deepseek.com"; $env:LLM_API_KEY="<key>"; $env:LLM_MODEL="deepseek-chat"
$env:DEEPSEEK_API_KEY=$env:LLM_API_KEY

npx --yes promptfoo@latest eval -c promptfooconfig.yaml --max-concurrency 3 --no-cache
npx --yes promptfoo@latest view      # 视图按 v1-线上 / v2-候选 两列对比通过率
```

迭代流程：改 `prompts/intent-v2.txt` → 重跑 → 看 v2 是否在 `G1~G5` 上全面不低于 v1（尤其 `instruction` 一列的失败原因）→ 满意后再同步回 `src/enterprise_agent/stages/intent/prompt.py`，并重新生成 `intent-v1.txt` 保持“线上基线”与源码一致。

## 上下文从哪来（两个测试的区别）

| | 测试A `e2e/` | 测试B `prompt-ab/` |
|---|---|---|
| 上下文来源 | **应用自己造**：provider 只发 `{question}`，`context.initial()` 从会话账本 + 语义目录现造 | **题库里的固定上下文**：`shared/context-base.json` + 用例差异，由 provider 合并后注入 |
| 用例里是否带上下文 | 不带（`vars` 只有 case_id/group/query/expected） | 带（只带会话差异，见下） |
| 副作用 | 建会话、写事件、跑 intent + prepare 两次调用 | 无副作用，可 52×N 变体并发 |
| 为什么 | 验证真实链路，包括上下文组装本身 | 直连模型没有账本/目录，不注入等于换题；且必须可复现、可对比 v1/v2 |

题库共 **9 种**固定会话状态（`empty` 被 31 条用例复用，其余为 `product`、`product_chart`、
`two_charts`、`report`、`product_rows`、`product_with_selection`、`field_text`、`product_and_supplier`）。

其中 `database_metadata`（16 表/字段/术语/限制）与 `available_capabilities` 在 9 份上下文里**逐字节相同**，
所以只存一份在 [shared/context-base.json](shared/context-base.json)，用例只存会话差异
（`stage / session_hints / recent_messages / candidate_requests / candidate_artifacts / history_summary / query`），
provider 运行时用 `{...CONTEXT_BASE, ...vars.context}` 合并：

- 用例文件体积：620 KB → 226 KB；
- 测试A 用例彻底不带上下文：620 KB → 131 KB；
- 合并后的键集合仍与线上 `context.initial()` **逐键一致**（生成器里做契约校验，缺/多字段直接报错）。

### database_metadata 的来源（已修正）

早前夹具里的 `database_metadata` 是从归档语料搬来的**手写简化版**，与线上 `Registry.overview()` 不一致。现已改为
在生成阶段直接调用应用侧 `Registry(None).overview()`，并加形状校验，差异如下：

| 项 | 旧夹具 | 线上 / 现在 |
|---|---|---|
| 顶层键 | 3 个（tables/terminology/limitations） | **12 个**：backend、source_id、snapshot_id、catalog_version、catalog_revision、unavailable_tables、semantic_version、tables、relations、metrics、unapproved_metrics、limitations |
| tables 结构 | `{columns: [...], rows}` | `{rows, primary_key, columns: {列名: {type, meaning}}}` |
| relations | 无 | **23 条** `{from, to}` |
| metrics / unapproved_metrics | 无 | 0 / **4**（candidate） |
| terminology | 额外加了 5 条 | **不存在**（含义在 `columns[].meaning` 里） |
| limitations | 硬编码 3 条 | **5 条**，直接来自 `config/semantic/catalog.yaml` |

生成器还会校验：顶层键集合、每张表的 `rows/primary_key/columns`、列条目的 `type/meaning`、relations 非空且为 `{from,to}`；
任何一项不符直接报错，避免夹具再次退化成简化版。

**契约校验**：生成器解析 `src/enterprise_agent/model/context.py` 的 `context.initial()` 返回键，
与「shared + 用例差异」合并结果比对。`available_capabilities` 就是靠这道检查发现并补上的。

## 判定细节

| 组 | 评价字段 | 附加（本套新增） |
|---|---|---|
| G1 | category | 操作数、instruction 要素 |
| G2 | operation_count、category | 同上 |
| G3 | category、action、request_id、refs | 同上；`update_task` 的 request_id 必须等于上下文里的真实 ID |
| G4 | category、refs | 同上；`$别名` 会映射到本轮实际 request_id 后再比对 refs |
| G5 | clarifications | request_id 必须为 null 或指向目标工作；语义由 llm-rubric 判 |

判定实现要点（`shared/assert-intent.js`）：

- 期望操作与实际行动按「category + action + instruction 要素重合度」贪心配对，允许 operations 顺序不同；
- `refs` 按集合比较（顺序无关），request 引用需同时比对 `output`；
- 除 G5 外，多余操作会被记为问题（防止把一项要求拆成两项）；
- `instruction` 命中判定会做全角转半角、去空白、拉丁小写化。

## 已知边界

- `/bind` 会顺带跑「需求整理」阶段，因此测试A 每条比纯意图多一次模型调用；只想迭代提示词就用测试B。
- G1 里纯概念类问题（如 G1-02、G1-05）没有可机械校验的标识符，只能靠语义判定。
- 语义判定依赖 `DEEPSEEK_API_KEY`；缺 key 时 `javascript` 断言仍会跑，`llm-rubric` 会整行报错。
- 题库来自 `tests/new/intent/cases.json`（你归档的旧语料）；若以后要新增意图用例，改那份语料后重跑生成器即可。
