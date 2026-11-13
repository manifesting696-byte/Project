/**
 * Deterministic grader for the Agent blackbox corpus.
 *
 * Tables and charts are compared against the reference answer here; free-form text
 * and reports are delegated to promptfoo's `llm-rubric` assert (this file only
 * checks that the artifact exists and reports whether the reference numbers appear).
 *
 * Promptfoo calls this as a javascript assert: module.exports = (output, context).
 */

const TOLERANCE = 0.01;

function num(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const cleaned = String(value).replace(/[,\s]/g, '').replace(/%$/, '');
  if (cleaned === '') return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

function sameValue(expected, actual, tolerance) {
  const left = num(expected);
  const right = num(actual);
  if (left !== null && right !== null) {
    return Math.abs(left - right) <= Math.max(tolerance, Math.abs(left) * 1e-9);
  }
  if (expected === null || expected === undefined) return actual === null || actual === undefined;
  return String(expected).trim() === String(actual ?? '').trim();
}

function project(row, columns) {
  const out = {};
  for (const column of columns) out[column] = row[column];
  return out;
}

function rowKey(row, columns, tolerance) {
  return columns
    .map((column) => {
      const value = num(row[column]);
      if (value !== null) return String(Math.round(value / Math.max(tolerance, 1e-9)));
      return String(row[column] ?? '').trim();
    })
    .join('\u0001');
}

function columnVector(rows, name) {
  return rows.map((row) => row[name]);
}

function normaliseValue(value) {
  const number = num(value);
  if (number !== null) return `n:${Math.round(number / TOLERANCE)}`;
  return `s:${String(value ?? '').trim()}`;
}

function vectorEquals(left, right, sortMode) {
  if (left.length !== right.length) return false;
  const a = left.map(normaliseValue);
  const b = right.map(normaliseValue);
  if (sortMode) {
    a.sort();
    b.sort();
  }
  return a.every((value, index) => value === b[index]);
}

/**
 * 判定锚点是"每列在各行的值"，列名只是提示：
 *   1) 先按列名（忽略大小写）匹配；
 *   2) 否则按整列取值匹配（顺序模式按同序，集合模式按多重集）；
 *   3) 多余列不参与判定；行数必须一致。
 */
function mapColumns(expectedRows, wanted, rows, sortMode) {
  const actualColumns = Object.keys(rows[0] || {});
  const used = new Set();
  const mapping = {};
  for (const column of wanted) {
    const expectedVector = columnVector(expectedRows, column);
    let choice = actualColumns.find(
      (name) => !used.has(name) && name.toLowerCase() === String(column).toLowerCase(),
    );
    if (!choice) {
      choice = actualColumns.find(
        (name) =>
          !used.has(name) && vectorEquals(expectedVector, columnVector(rows, name), sortMode),
      );
    }
    if (!choice) return null;
    used.add(choice);
    mapping[column] = choice;
  }
  return mapping;
}

function compareTable(expected, rows, label) {
  const wanted = expected.columns || Object.keys(expected.rows[0] || {});
  if (!rows.length && expected.rows.length) {
    return { pass: false, reason: `${label}: 无表格产物` };
  }
  if (expected.rows.length === 0) {
    return rows.length === 0
      ? { pass: true, reason: `${label}: 空结果符合预期` }
      : { pass: false, reason: `${label}: 预期空表，实际 ${rows.length} 行` };
  }
  if (rows.length !== expected.rows.length) {
    return {
      pass: false,
      reason: `${label}: 行数 ${rows.length} != ${expected.rows.length}`,
    };
  }
  let mapping = mapColumns(expected.rows, wanted, rows, !expected.ordered);
  let positional = false;
  const actualColumns = Object.keys(rows[0] || {});
  if (!mapping && actualColumns.length === wanted.length) {
    // 列名与列值都不同但列数一致：按位置对齐，便于报出具体数值差异
    mapping = {};
    wanted.forEach((column, index) => {
      mapping[column] = actualColumns[index];
    });
    positional = true;
  }
  if (!mapping) {
    return {
      pass: false,
      reason:
        `${label}: 列无法对齐，期望列 ${wanted.join(',')} 无法在 ${actualColumns.join(',')} ` +
        '中找到列名或列值一致的列（列数也不同，无法按位置对齐）',
    };
  }
  const extra = actualColumns.filter((name) => !Object.values(mapping).includes(name));
  const note = positional
    ? `（列名不同，按位置对齐 ${wanted.join(',')} -> ${Object.values(mapping).join(',')}）`
    : extra.length
      ? `（忽略多余列 ${extra.join(',')}）`
      : '';
  const pairs = wanted.map((column) => [column, mapping[column]]);

  if (expected.ordered) {
    for (let i = 0; i < expected.rows.length; i += 1) {
      for (const [want, got] of pairs) {
        if (!sameValue(expected.rows[i][want], rows[i][got], TOLERANCE)) {
          return {
            pass: false,
            reason:
              `${label}: 第${i + 1}行 ${want} 期望 ${expected.rows[i][want]} ` +
              `实际 ${rows[i][got]}`,
          };
        }
      }
    }
    return { pass: true, reason: `${label}: ${rows.length} 行顺序与数值一致${note}` };
  }

  const key = (row, side) =>
    pairs
      .map(([want, got]) => normaliseValue(row[side === 'expected' ? want : got]))
      .join('\u0001');
  const want = expected.rows.map((row) => key(row, 'expected')).sort();
  const got = rows.map((row) => key(row, 'actual')).sort();
  for (let i = 0; i < want.length; i += 1) {
    if (want[i] !== got[i]) {
      return {
        pass: false,
        reason: `${label}: 行集合不一致，首个差异 ${JSON.stringify(expected.rows[i] ?? {})}`,
      };
    }
  }
  return { pass: true, reason: `${label}: ${rows.length} 行集合一致${note}` };
}

function compareChart(expected, artifact, label) {
  const config = artifact.content || {};
  const rows = (config.dataset && config.dataset.source) || [];
  const series = (config.series && config.series[0]) || {};
  const encode = series.encode || {};
  const chartType = (artifact.payload && artifact.payload.chart_type) || series.type;
  if (expected.chart_type && chartType && chartType !== expected.chart_type) {
    return { pass: false, reason: `${label}: 图表类型 ${chartType} != ${expected.chart_type}` };
  }
  const valueField =
    encode.y === expected.value_field || encode.x === expected.value_field
      ? expected.value_field
      : null;
  if (!valueField) {
    return {
      pass: false,
      reason: `${label}: 图表未使用数值字段 ${expected.value_field}（encode=${JSON.stringify(encode)}）`,
    };
  }
  const categoryField = encode.x === valueField ? encode.y : encode.x;
  if (!rows.length) return { pass: false, reason: `${label}: 图表无数据点` };
  if (expected.point_count !== undefined && rows.length !== expected.point_count) {
    return {
      pass: false,
      reason: `${label}: 数据点 ${rows.length} != ${expected.point_count}`,
    };
  }
  for (const row of expected.data || []) {
    const wanted = row[expected.category_field];
    const match = rows.find(
      (item) => String(item[categoryField] ?? '').trim() === String(wanted ?? '').trim(),
    );
    if (!match) {
      return { pass: false, reason: `${label}: 缺少类别 ${wanted}` };
    }
    if (!sameValue(row[expected.value_field], match[valueField], TOLERANCE)) {
      return {
        pass: false,
        reason: `${label}: 类别 ${wanted} 数值 期望 ${row[expected.value_field]} 实际 ${match[valueField]}`,
      };
    }
  }
  if (expected.percentages && expected.percentages.length) {
    const total = rows.reduce((sum, item) => sum + (num(item[valueField]) || 0), 0);
    if (total <= 0) return { pass: false, reason: `${label}: 占比分母为 0` };
    let sum = 0;
    for (const item of expected.percentages) {
      const match = rows.find(
        (row) =>
          String(row[categoryField] ?? '').trim() ===
          String(item[expected.category_field] ?? '').trim(),
      );
      if (!match) return { pass: false, reason: `${label}: 缺少类别 ${item[expected.category_field]}` };
      const share = ((num(match[valueField]) || 0) / total) * 100;
      sum += share;
      if (!sameValue(item.percentage, share, TOLERANCE)) {
        return {
          pass: false,
          reason: `${label}: 类别 ${item[expected.category_field]} 占比 期望 ${item.percentage} 实际 ${share.toFixed(2)}`,
        };
      }
    }
    if (Math.abs(sum - 100) > TOLERANCE) {
      return { pass: false, reason: `${label}: 占比合计 ${sum.toFixed(2)} != 100` };
    }
  }
  return { pass: true, reason: `${label}: ${rows.length} 个数据点与占比一致` };
}

function numberTokens(text) {
  const found = new Set();
  for (const match of String(text).matchAll(/-?\d[\d,]*(?:\.\d+)?/g)) {
    const value = num(match[0]);
    if (value !== null) found.add(value);
  }
  return found;
}

function textDelegated(expected, artifact, label, judge) {
  if (!artifact) {
    return { pass: false, reason: `${label}: 缺少 ${expected.type} 产物`, delegated: false };
  }
  const body = typeof artifact.content === 'string' ? artifact.content : JSON.stringify(artifact.content);
  const notes = [`${label}: 文本交由 llm-rubric 判定`];
  if (judge && judge.check_numbers) {
    const available = numberTokens(body);
    const missing = [];
    for (const fact of expected.required_facts || []) {
      for (const match of String(fact).matchAll(/-?\d[\d,]*(?:\.\d+)?/g)) {
        const value = num(match[0]);
        if (value === null) continue;
        const hit = [...available].some(
          (candidate) => Math.abs(candidate - value) <= Math.max(TOLERANCE, Math.abs(value) * 1e-9),
        );
        if (!hit) missing.push(match[0]);
      }
    }
    notes.push(
      missing.length ? `参考数字未出现: ${[...new Set(missing)].join(',')}` : '参考数字均出现',
    );
  }
  return { pass: true, reason: notes.join('；'), delegated: true };
}

/**
 * 多交付轮次里 Agent 可能多产出噪声（例如同名表重复一次），
 * 因此每个期望交付都在"未使用"的同类产物里找第一个能通过的，而不是按顺序硬取。
 */
function bestCandidate(pool, used, compare) {
  let fallback = null;
  for (const entry of pool) {
    if (used.has(entry.index)) continue;
    const verdict = compare(entry.item);
    if (verdict.pass) return { entry, verdict };
    if (!fallback) fallback = { entry, verdict };
  }
  return fallback;
}

module.exports = async (output, context) => {
  const vars = (context && context.vars) || {};
  const expected = vars.expected || { outputs: [] };
  const judges = vars.judge || [];
  let actual;
  try {
    actual = JSON.parse(output);
  } catch (error) {
    return { pass: false, score: 0, reason: `provider 输出不是 JSON：${String(error)}` };
  }
  const artifacts = actual.artifacts || [];
  const pools = {
    table: artifacts.map((item, index) => ({ item, index })).filter((entry) => entry.item.content_type === 'table'),
    chart: artifacts.map((item, index) => ({ item, index })).filter((entry) => entry.item.content_type === 'chart'),
    text: artifacts.map((item, index) => ({ item, index })).filter((entry) => entry.item.content_type === 'text'),
    report: artifacts.map((item, index) => ({ item, index })).filter((entry) => entry.item.content_type === 'report'),
  };
  const used = { table: new Set(), chart: new Set(), text: new Set(), report: new Set() };
  const results = [];
  let delegated = false;

  expected.outputs.forEach((want, index) => {
    const judge = judges[index] || {};
    const label = `${vars.case_id || '?'}T${vars.turn || '?'}#${index + 1}(${want.type})`;
    if (want.type === 'table') {
      const picked = bestCandidate(pools.table, used.table, (item) =>
        compareTable(want, (item.content || {}).rows || [], label),
      );
      if (!picked) {
        results.push({ pass: false, reason: `${label}: 无表格产物` });
        return;
      }
      if (picked.verdict.pass) used.table.add(picked.entry.index);
      results.push(picked.verdict);
      return;
    }
    if (want.type === 'chart') {
      const picked = bestCandidate(pools.chart, used.chart, (item) =>
        compareChart(want, item, label),
      );
      if (!picked) {
        results.push({ pass: false, reason: `${label}: 无图表产物` });
        return;
      }
      if (picked.verdict.pass) used.chart.add(picked.entry.index);
      results.push(picked.verdict);
      return;
    }
    const pool = want.type === 'report' ? pools.report : pools.text;
    const candidate = pool.find((entry) => !used[want.type].has(entry.index));
    if (candidate) used[want.type].add(candidate.index);
    const graded = textDelegated(want, candidate && candidate.item, label, judge);
    delegated = delegated || Boolean(graded.delegated);
    results.push(graded);
  });

  const failed = results.filter((item) => !item.pass);
  const score = results.length ? (results.length - failed.length) / results.length : 0;
  const summary = results.map((item) => item.reason).join(' | ');
  const statusNote = `status=${actual.status}`;
  if (failed.length) {
    return {
      pass: false,
      score,
      reason: `${statusNote}；${summary}`,
    };
  }
  if (delegated) {
    // Deterministic part passed; the llm-rubric assert carries the text verdict.
    return { pass: true, score: 1, reason: `${statusNote}；结构化产物一致；${summary}` };
  }
  return { pass: true, score: 1, reason: `${statusNote}；${summary}` };
};
