/**
 * 测试A 的 provider：走应用自身的意图阶段（/bind 预览，不执行任何 Task/SQL）。
 *
 * 调用链：POST /auth/login → POST /bind {question} → GET /sessions/{id}/events
 * 取回 intent_resolved 事件里的 Intent JSON 作为被测输出。
 *
 * 注意：/bind 会跑「意图识别 + 需求整理」两次模型调用，但不会调度执行节点；
 * 若只想迭代提示词、完全不碰应用，请用 prompt-ab 那套（provider-llm.js）。
 *
 * Env：APP_URL / APP_USER / APP_PASSWORD / APP_TIMEOUT_MS
 */

const BASE = process.env.APP_URL || 'http://127.0.0.1:8000';
const USER = process.env.APP_USER || 'admin';
const PASSWORD = process.env.APP_PASSWORD || '';
const TIMEOUT_MS = Number(process.env.APP_TIMEOUT_MS || 600000);

let TOKEN = null;

async function call(path, { method = 'GET', body } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(TOKEN ? { authorization: `Bearer ${TOKEN}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    let payload;
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { raw: text };
    }
    if (!response.ok) throw new Error(`${method} ${path} -> ${response.status} ${text.slice(0, 300)}`);
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

async function login() {
  if (TOKEN) return TOKEN;
  if (!PASSWORD) throw new Error('缺少 APP_PASSWORD');
  const result = await call('/auth/login', {
    method: 'POST',
    body: { username: USER, password: PASSWORD },
  });
  TOKEN = result.access_token;
  if (!TOKEN) throw new Error('登录未返回 access_token');
  return TOKEN;
}

async function intentOf(sessionId) {
  const events = await call(`/sessions/${sessionId}/events`);
  const list = Array.isArray(events) ? events : events.events || [];
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const event = list[index];
    const payload = event.payload || {};
    const key = payload.workflow_key || '';
    if (event.event_type === 'intent_resolved' || key.endsWith(':intent')) {
      return { event, intent: payload.intent };
    }
  }
  return null;
}

module.exports = class IntentAppProvider {
  id() {
    return 'intent-via-app';
  }

  async callApi(_prompt, context) {
    const vars = (context && context.vars) || {};
    try {
      await login();
      const preview = await call('/bind', {
        method: 'POST',
        body: { question: vars.query },
      });
      const sessionId = preview.session_id;
      const found = await intentOf(sessionId);
      if (!found || !found.intent) {
        return { error: `未在会话 ${sessionId} 中找到 intent_resolved 事件（status=${preview.status}）` };
      }
      return {
        output: JSON.stringify({
          case_id: vars.case_id,
          status: preview.status,
          session_id: sessionId,
          intent: found.intent,
        }),
      };
    } catch (error) {
      return { error: String(error && error.message ? error.message : error) };
    }
  }
};
