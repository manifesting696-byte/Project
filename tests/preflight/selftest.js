/**
 * Offline self-test for asserts/grade.js — no Agent, no Docker, no LLM.
 *
 * Uses one captured real response (fixtures/D01-captured.json) plus synthetic
 * mutations so the matching rules stay honest:
 *   - single-column count table with a different column name must PASS
 *   - a wrong value must FAIL
 *   - a missing table artifact must FAIL
 *   - chart data points and a wrong point must FAIL
 *   - text output must be delegated to llm-rubric (pass + delegation note)
 *
 *   node selftest.js
 */

const fs = require('fs');
const path = require('path');
const grade = require('../agent-eval/asserts/grade.js');

const fixture = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'agent-eval', 'fixtures', 'D01-captured.json'), 'utf8'),
);
const tests = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'agent-eval', 'promptfoo-tests.json'), 'utf8'),
);
const byId = new Map(tests.map((item) => [`${item.vars.case_id}T${item.vars.turn}`, item]));

const clone = (value) => JSON.parse(JSON.stringify(value));
const results = [];

async function check(name, output, vars, wantPass) {
  const verdict = await grade(JSON.stringify(output), { vars });
  const ok = Boolean(verdict.pass) === wantPass;
  results.push({ name, wantPass, got: Boolean(verdict.pass), ok, reason: verdict.reason });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ->  ${verdict.pass ? 'pass' : 'fail'}`);
  console.log(`      ${String(verdict.reason).slice(0, 220)}`);
}

(async () => {
  const d01 = byId.get('D01T1');

  // 1) 真实抓取：row_count=16，期望列名 product_count，单列按位置对齐应通过
  await check('captured D01 (row_count vs product_count)', fixture.output, d01.vars, true);

  // 2) 数值错误必须失败
  const wrong = clone(fixture.output);
  wrong.artifacts[0].content.rows[0][Object.keys(wrong.artifacts[0].content.rows[0])[0]] = 17;
  await check('captured D01 with wrong value 17', wrong, d01.vars, false);

  // 3) 缺少表格产物必须失败
  const empty = clone(fixture.output);
  empty.artifacts = [];
  await check('captured D01 without table artifact', empty, d01.vars, false);

  // 4) 图表：按 I01 期望构造一个一致的图表产物
  const i01 = byId.get('I01T2');
  const expectedChart = i01.vars.expected.outputs.find((item) => item.type === 'chart');
  const chartRows = expectedChart.data.map((row) => ({
    [expectedChart.category_field]: row[expectedChart.category_field],
    [expectedChart.value_field]: row[expectedChart.value_field],
  }));
  const chartOutput = {
    status: 'completed',
    artifacts: [
      { content_type: 'table', content: { rows: chartRows }, payload: {} },
      {
        content_type: 'chart',
        payload: { chart_type: expectedChart.chart_type },
        content: {
          dataset: { source: chartRows },
          series: [
            {
              type: expectedChart.chart_type,
              encode: { x: expectedChart.category_field, y: expectedChart.value_field },
            },
          ],
        },
      },
    ],
  };
  // I01 第二轮只期望 chart，第一轮表格不在本轮输出里
  i01.vars.expected.outputs = i01.vars.expected.outputs.filter((item) => item.type === 'chart');
  await check('synthetic I01 chart matches reference', chartOutput, i01.vars, true);

  const badChart = clone(chartOutput);
  badChart.artifacts[1].content.dataset.source[0][expectedChart.value_field] += 1;
  await check('synthetic I01 chart with one wrong point', badChart, i01.vars, false);

  // 5) 文本：交由 llm-rubric，确定性部分应通过并标注委托
  const k05 = byId.get('K05T1');
  const textOutput = {
    status: 'completed',
    artifacts: [
      {
        content_type: 'text',
        payload: {},
        content:
          'country 是国家级，粒度最粗；city 是城市或行政区，可按城市分组；address 是街道级地址，只适合定位与核对。按地域统计应使用 city。',
      },
    ],
  };
  await check('K05 text delegated to llm-rubric', textOutput, k05.vars, true);

  // 6) 多交付轮次里的"重复表噪声"：M07 真实出现过同名月度表输出两次
  const m07 = byId.get('M07T1');
  const monthlyRows = m07.vars.expected.outputs[0].rows;
  const categoryRows = m07.vars.expected.outputs[2].rows;
  const noisyOutput = {
    status: 'completed',
    artifacts: [
      { content_type: 'table', payload: {}, content: { rows: monthlyRows } },
      { content_type: 'table', payload: {}, content: { rows: monthlyRows } },
      {
        content_type: 'chart',
        payload: { chart_type: 'line' },
        content: {
          dataset: { source: monthlyRows },
          series: [{ type: 'line', encode: { x: 'month', y: 'order_count' } }],
        },
      },
      { content_type: 'table', payload: {}, content: { rows: categoryRows } },
      {
        content_type: 'chart',
        payload: { chart_type: 'bar' },
        content: {
          dataset: { source: categoryRows },
          series: [{ type: 'bar', encode: { x: 'product_category', y: 'product_count' } }],
        },
      },
    ],
  };
  await check('M07 shape with duplicated monthly table', noisyOutput, m07.vars, true);

  const failed = results.filter((item) => !item.ok);
  console.log(`\n${results.length - failed.length}/${results.length} self-tests passed`);
  process.exit(failed.length ? 1 : 0);
})();
