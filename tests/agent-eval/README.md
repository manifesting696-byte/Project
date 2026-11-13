# tests/agent-eval —— 真实测试（会调用被测 Agent）

这个目录**只**放"跑起来会调 Agent"的东西。离线核对与判分器自测在 [`../preflight`](../preflight)，两者互不依赖：
删掉本目录，`preflight` 仍可独立完成"参考答案是否正确"的全部核对。

```
agent-eval/
├── promptfooconfig.yaml   Promptfoo 配置（模型、并发、判定模型）
├── promptfoo-tests.json   69 条测试（由 ../preflight/export_dataset.py 生成，勿手改）
├── provider.js            唯一调用 Agent 的文件：登录 → 重放 history → /ask → 抓 artifacts
├── asserts/grade.js       表格/图表确定性判定（被 promptfoo 调用）
├── fixtures/              真实响应样本（供 preflight/selftest.js 离线回归）
├── report.py              把 promptfoo 结果 JSON 汇成逐案红绿表
└── README.md              本文件
```

## 前置

1. 应用与数据库在跑（`docker compose up -d --wait`），`http://127.0.0.1:8000/health` 正常；
2. `promptfoo-tests.json` 是最新的：先跑 `python tests/preflight/export_dataset.py`；
3. 一个 analyst 账号（默认 `blackbox_runner`，密码在 `runtime/promptfoo-credentials.json`）；
4. 判定文字的 `llm-rubric` 需要判定模型的 key。

## 运行

```powershell
cd C:\Users\Lenovo\Desktop\LLM\Project\tests\agent-eval

$c = Get-Content ..\..\runtime\promptfoo-credentials.json -Raw | ConvertFrom-Json
$env:APP_URL      = "http://127.0.0.1:8000"
$env:APP_USER     = $c.username
$env:APP_PASSWORD = $c.password
$env:DEEPSEEK_API_KEY = "<.env 里的 LLM_API_KEY>"   # 仅 llm-rubric 需要

npx --yes promptfoo@latest validate -c promptfooconfig.yaml
npx --yes promptfoo@latest eval -c promptfooconfig.yaml --max-concurrency 2 --no-cache
npx --yes promptfoo@latest eval -c promptfooconfig.yaml --filter-pattern "^K05" --no-cache
npx --yes promptfoo@latest view
```

`--no-cache` 很重要：不加会复用上一轮 Agent 输出，等于没重跑。

汇总：

```powershell
.\.venv\Scripts\python.exe tests\agent-eval\report.py runtime\promptfoo-full.json --csv runtime\promptfoo-full.csv
```

## 判定方式（与 preflight 的约定一致）

| 模式 | 规则 |
|---|---|
| `ordered` | 行数相同、逐行逐列相等、行序一致；数值容差 0.01 |
| `set` | 行数相同、行多重集相等（顺序无关）；数值容差 0.01 |
| `chart` | 类型一致、`point_count` 一致、每类数值一致；饼图占比逐类一致且合计 100 |
| `llm_rubric` | LLM 判：`facts` 必须包含（允许同义/千分位/单位差异），`limitations` 不得矛盾，禁止编造数值 |

表格列对齐：先按列名（忽略大小写）→ 再按整列取值 → 列数相同按位置；**多余列忽略**；**行数必须一致**。
多交付轮次里，每个期望交付在未使用产物中找第一个能匹配的（容忍重复表等噪声）。

## 读结果

- 只有 `structured` 通过、`text` 失败 → 通常是文字表述或口径缺失，看 `reason`；
- `status=failed` 且内容是 `402 Payment Required` / `SSL EOF` / `peer closed` → 基础设施问题，不是 Agent 判定结论；
- `llm-rubric` 报 `API key is not set` → 判定模型没配 key，该行答案其实未被评价。

Windows 上结束时可能打印 `Assertion failed: ... UV_HANDLE_CLOSING`，属 Node 24 + libuv 收尾噪声，以表格/`view` 结果为准。
