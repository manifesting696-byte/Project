/**
 * 意图识别判定（两个测试共用）：字段 + instruction 校对。
 *
 * 输入：provider 返回的 JSON 字符串，形如 {"intent": {...}} 或直接是 Intent。
 * vars：case_id / group / query / context / expected
 *
 * 判定顺序：
 *   1) 结构（operations / clarifications 必须是数组）
 *   2) 按 category/action/instruction 重合度为期望操作匹配实际操作
 *   3) 分组字段：G1/G2 数量与类别；G3 类别/动作/request_id/refs；G4 类别/refs；G5 澄清
 *   4) instruction：标识符、年份、时间短语、排序取数、交付形态必须出现；
 *      允许中文别名；长度不得显著短于 query
 *
 * 语义层（是否补全指代、update 是否只写变化）由同一测试里的 llm-rubric 负责。
 */

function norm(value) {
  return String(value ?? '')
    .replace(/[\uFF01-\uFF5E]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/\u3000/g, '')
    .replace(/\s+/g, '')
    .toLowerCase();
}

function parseOutput(output) {
  let parsed;
  try {
    parsed = JSON.parse(output);
  } catch {
    const start = String(output).indexOf('{');
    const end = String(output).lastIndexOf('}');
    if (start < 0 || end <= start) throw new Error('输出不是合法 JSON');
    parsed = JSON.parse(String(output).slice(start, end + 1));
  }
  const intent = parsed.intent && typeof parsed.intent === 'object' ? parsed.intent : parsed;
  if (!Array.isArray(intent.operations)) throw new Error('缺少 operations 数组');
  if (!Array.isArray(intent.clarifications)) throw new Error('缺少 clarifications 数组');
  return intent;
}

function refKey(kind, id, output) {
  return `${kind}:${id}${output ? `#${output}` : ''}`;
}

function pairScore(expectedOp, actualOp) {
  const match = expectedOp.match || {};
  let score = 0;
  if (match.category && actualOp.category === match.category) score += 3;
  if (match.action && actualOp.action === match.action) score += 2;
  const tokens = [
    ...(expectedOp.instruction?.must_include_all || []),
    ...(expectedOp.instruction?.must_include_any || []).flat(),
  ];
  const text = norm(actualOp.instruction);
  score += tokens.filter((token) => text.includes(norm(token))).length * 0.5;
  return score;
}

function matchOperations(expectedOps, actualOps) {
  const used = new Set();
  const pairs = [];
  for (const expectedOp of expectedOps) {
    let best = null;
    actualOps.forEach((actualOp, index) => {
      if (used.has(index)) return;
      const wanted = (expectedOp.match || {}).category;
      if (wanted && actualOp.category !== wanted) return;
      const score = pairScore(expectedOp, actualOp);
      if (!best || score > best.score) best = { index, actualOp, score };
    });
    if (!best) return { pairs, missing: expectedOp };
    used.add(best.index);
    pairs.push({ expected: expectedOp, actual: best.actualOp, index: best.index });
  }
  return { pairs, missing: null, unused: actualOps.length - used.size };
}

function checkInstruction(expected, actualInstruction, query) {
  const spec = expected.instruction || {};
  const text = norm(actualInstruction);
  const missing = [];
  for (const term of spec.must_include_all || []) {
    if (!text.includes(norm(term))) missing.push(term);
  }
  for (const group of spec.must_include_any || []) {
    if (!group.some((term) => text.includes(norm(term)))) missing.push(`(${group.join('|')})`);
  }
  const forbidden = (spec.must_not_include || []).filter((term) => text.includes(norm(term)));
  const ratio = norm(query).length ? text.length / norm(query).length : 1;
  const tooShort = spec.min_length_ratio ? ratio < spec.min_length_ratio : false;
  return {
    pass: missing.length === 0 && forbidden.length === 0 && !tooShort,
    missing,
    forbidden,
    ratio: Number(ratio.toFixed(2)),
    tooShort,
  };
}

module.exports = async (output, context) => {
  const vars = (context && context.vars) || {};
  const expected = vars.expected || { operations: [], clarifications: [] };
  const group = vars.group || '';
  const label = `${vars.case_id || '?'}[${group}]`;
  const problems = [];
  const notes = [];

  let intent;
  try {
    intent = parseOutput(output);
  } catch (error) {
    return { pass: false, score: 0, reason: `${label}: ${error.message}` };
  }

  const expectedOps = expected.operations || [];
  const actualOps = intent.operations;

  // ---------- 澄清类（G5）----------
  if (expected.clarifications && expected.clarifications.length) {
    if (!intent.clarifications.length) {
      problems.push('未给出澄清（clarifications 为空）');
    } else {
      for (const wanted of expected.clarifications) {
        const wantedId = wanted.request_id ?? null;
        const hit = intent.clarifications.find(
          (item) => (item.request_id ?? null) === wantedId || wantedId === null,
        );
        if (!hit) {
          problems.push(`澄清的 request_id 应为 ${wantedId ?? 'null'}，实际为 ` +
            `${intent.clarifications.map((item) => item.request_id ?? 'null').join(',')}`);
        } else if ((hit.request_id ?? null) !== wantedId) {
          problems.push(`澄清 request_id 期望 ${wantedId ?? 'null'} 实际 ${hit.request_id ?? 'null'}`);
        }
      }
      notes.push(`澄清 ${intent.clarifications.length} 条（语义由 llm-rubric 判定）`);
    }
  } else if (intent.clarifications.length && group !== 'G5') {
    problems.push(`不应澄清，实际给出 ${intent.clarifications.length} 条`);
  }

  // ---------- 操作类（G1-G4）----------
  if (group !== 'G5') {
    if (actualOps.length !== expectedOps.length) {
      problems.push(`operation 数量 期望 ${expectedOps.length} 实际 ${actualOps.length}`);
    }
    const { pairs, missing, unused } = matchOperations(expectedOps, actualOps);
    if (missing) {
      problems.push(`无法匹配期望操作「${missing.requirement}」`);
    }
    if (unused) notes.push(`多余操作 ${unused} 项`);

    // 第一遍：类别/动作/request_id，建立别名映射
    const aliases = new Map();
    const literalIds = new Set();
    for (const pair of pairs) {
      const match = pair.expected.match || {};
      const actual = pair.actual;
      if (match.category && actual.category !== match.category) {
        problems.push(`${pair.expected.requirement}: category 期望 ${match.category} 实际 ${actual.category}`);
      }
      if (match.action && actual.action !== match.action) {
        problems.push(`${pair.expected.requirement}: action 期望 ${match.action} 实际 ${actual.action}`);
      }
      // 题库里的 $别名（如 $totals）一律映射到实际操作 ID，供同轮依赖 refs 解析
      const alias = pair.expected.alias;
      if (alias && String(alias).startsWith('$')) {
        aliases.set(alias, actual.request_id);
      }
      if (match.request_id) {
        if (String(match.request_id).startsWith('$')) {
          aliases.set(match.request_id, actual.request_id);
        } else {
          literalIds.add(match.request_id);
          if (actual.request_id !== match.request_id) {
            problems.push(`${pair.expected.requirement}: request_id 期望 ${match.request_id} 实际 ${actual.request_id}`);
          }
        }
      }
      // 别名唯一性：不同期望操作不能指向同一个实际 request_id
      const seen = [...aliases.values()];
      if (seen.length !== new Set(seen).size) {
        problems.push('不同操作复用了同一个 request_id');
      }
    }

    // 第二遍：refs（别名此时已解析）
    const resolve = (ref) => {
      const id = String(ref.id);
      const mapped = id.startsWith('$') ? aliases.get(id) || id : id;
      return refKey(ref.kind, mapped, ref.output);
    };
    for (const pair of pairs) {
      const match = pair.expected.match || {};
      if (!Object.prototype.hasOwnProperty.call(match, 'refs')) continue;
      const wanted = (match.refs || []).map(resolve).sort();
      const actual = (pair.actual.refs || []).map((ref) => refKey(ref.kind, ref.id, ref.output)).sort();
      if (JSON.stringify(wanted) !== JSON.stringify(actual)) {
        problems.push(
          `${pair.expected.requirement}: refs 期望 [${wanted.join(', ')}] 实际 [${actual.join(', ')}]`,
        );
      }
    }

    // instruction 校对（所有组都做）
    for (const pair of pairs) {
      const verdict = checkInstruction(pair.expected, pair.actual.instruction, vars.query);
      const tag = `${pair.expected.requirement}`;
      if (!verdict.pass) {
        const detail = [];
        if (verdict.missing.length) detail.push(`缺少要素 ${verdict.missing.join('、')}`);
        if (verdict.forbidden.length) detail.push(`出现禁止词 ${verdict.forbidden.join('、')}`);
        if (verdict.tooShort) detail.push(`instruction 过短（长度比 ${verdict.ratio}）`);
        problems.push(`${tag}: instruction ${detail.join('；')}｜实际：「${String(pair.actual.instruction).slice(0, 80)}」`);
      } else {
        notes.push(`${tag}: instruction 要素齐全（长度比 ${verdict.ratio}）`);
      }
    }
  }

  const pass = problems.length === 0;
  const reason = pass
    ? `${label}: 字段与 instruction 要素均通过；${notes.join('；')}`
    : `${label}: ${problems.join(' | ')}`;
  return { pass, score: pass ? 1 : 0, reason };
};
