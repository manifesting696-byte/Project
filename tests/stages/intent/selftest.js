/**
 * 离线自测 assert-intent.js（不调用模型、不访问应用）。
 *
 *   node tests/stages/intent/selftest.js
 *
 * 用题库里的真实期望构造"正确答案"，再做定向篡改，确认判定会放对、会抓错。
 */

const fs = require('fs');
const path = require('path');
const assertIntent = require('./shared/assert-intent.js');

const corpus = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'cases', 'intent-cases.json'), 'utf8'),
);
const byId = new Map(corpus.cases.map((item) => [item.case_id, item]));

function instructionFor(op) {
  const spec = op.instruction || {};
  const parts = [op.requirement];
  parts.push(...(spec.must_include_all || []));
  for (const group of spec.must_include_any || []) parts.push(group[0]);
  const text = parts.join('，');
  const query = norm(op.requirement);
  return text.length > query.length ? text : `${text}，按原要求执行，范围与口径不变`;
}

function norm(value) {
  return String(value || '').replace(/\s+/g, '');
}

function fabricate(caseItem, mutate) {
  const aliases = new Map();
  const operations = (caseItem.expected.operations || []).map((op, index) => {
    const match = op.match || {};
    const localId = op.alias && String(op.alias).startsWith('$') ? `new_${index + 1}` : match.request_id;
    if (op.alias && String(op.alias).startsWith('$')) aliases.set(op.alias, localId);
    return {
      request_id: localId,
      action: match.action || 'create_task',
      category: match.category || 'data_analysis',
      instruction: instructionFor(op),
      refs: (match.refs || []).map((ref) => ({
        kind: ref.kind,
        id: String(ref.id).startsWith('$') ? aliases.get(ref.id) || ref.id : ref.id,
        ...(ref.output ? { output: ref.output } : {}),
      })),
    };
  });
  const output = {
    operations,
    clarifications: (caseItem.expected.clarifications || []).map((item) => ({
      request_id: item.request_id ?? null,
      subject: item.subject,
      question: (item.question_must_resolve || []).join('、') + '？',
      refs: [],
    })),
  };
  if (mutate) mutate(output, aliases);
  return output;
}

async function check(name, caseId, output, wantPass) {
  const caseItem = byId.get(caseId);
  const vars = {
    case_id: caseItem.case_id,
    group: caseItem.group,
    query: caseItem.query,
    context: caseItem.context,
    expected: caseItem.expected,
  };
  const verdict = await assertIntent(JSON.stringify(output), { vars });
  const ok = Boolean(verdict.pass) === wantPass;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ->  ${verdict.pass ? 'pass' : 'fail'}`);
  console.log(`      ${String(verdict.reason).slice(0, 240)}`);
  return ok;
}

(async () => {
  const results = [];

  // 1) G3-01 完全正确
  results.push(await check('G3-01 正确意图', 'G3-01', fabricate(byId.get('G3-01')), true));

  // 2) G3-01 instruction 丢掉年份 → 必须失败
  results.push(
    await check(
      'G3-01 instruction 缺年份',
      'G3-01',
      fabricate(byId.get('G3-01'), (out) => {
        out.operations[0].instruction = '查询各产品的实际产量，按 actual_end_date 入期。';
      }),
      false,
    ),
  );

  // 3) G1-04 用中文写法（别名）→ 应通过
  results.push(
    await check(
      'G1-04 instruction 用中文别名',
      'G1-04',
      fabricate(byId.get('G1-04'), (out) => {
        out.operations[0].instruction = '解释 RawMaterialInventory 中「预留量」的含义。';
      }),
      true,
    ),
  );

  // 4) G4-01 同轮依赖 refs 正确（别名解析）
  results.push(await check('G4-01 refs 指向上游', 'G4-01', fabricate(byId.get('G4-01')), true));

  // 5) G4-01 refs 指错 id → 必须失败
  results.push(
    await check(
      'G4-01 refs 指错 id',
      'G4-01',
      fabricate(byId.get('G4-01'), (out) => {
        out.operations[1].refs = [{ kind: 'request', id: 'new_9', output: 'product_totals' }];
      }),
      false,
    ),
  );

  // 6) G5-01 澄清 request_id 为 null → 通过
  results.push(await check('G5-01 澄清正确', 'G5-01', fabricate(byId.get('G5-01')), true));

  // 7) G5-01 澄清挂到具体 request_id → 必须失败
  results.push(
    await check(
      'G5-01 澄清错挂 request_id',
      'G5-01',
      fabricate(byId.get('G5-01'), (out) => {
        out.clarifications[0].request_id = 'req_101';
      }),
      false,
    ),
  );

  // 8) G2-01 少拆一项 → 必须失败
  results.push(
    await check(
      'G2-01 少拆一项',
      'G2-01',
      fabricate(byId.get('G2-01'), (out) => {
        out.operations = out.operations.slice(0, 1);
      }),
      false,
    ),
  );

  const failed = results.filter((item) => !item).length;
  console.log(`\n${results.length - failed}/${results.length} self-tests passed`);
  process.exit(failed ? 1 : 0);
})();
