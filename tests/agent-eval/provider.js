/**
 * Promptfoo provider: replays a case prefix against the live Agent HTTP API and
 * returns one JSON string with the final turn's artifacts (text / table / chart /
 * report) so asserts/grade.js can compare them with the expected answer.
 *
 * Env: APP_URL (default http://127.0.0.1:8000), APP_USER, APP_PASSWORD.
 */

const BASE = process.env.APP_URL || 'http://127.0.0.1:8000';
const USER = process.env.APP_USER || 'admin';
const PASSWORD = process.env.APP_PASSWORD || '';
const TIMEOUT_MS = Number(process.env.APP_TIMEOUT_MS || 600000);

let TOKEN = null;

async function call(path, { method = 'GET', body, token } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
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
    if (!response.ok) {
      throw new Error(`${method} ${path} -> ${response.status} ${text.slice(0, 300)}`);
    }
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

async function artifact(token, artifactId) {
  const meta = await call(`/artifacts/${artifactId}`, { token });
  let content = null;
  if (meta.content_type === 'table') {
    const rows = [];
    let offset = 0;
    for (;;) {
      const page = await call(
        `/artifacts/${artifactId}/content?offset=${offset}&limit=100`,
        { token },
      );
      rows.push(...(page.rows || []));
      if (!page.has_more) break;
      offset += (page.rows || []).length;
      if (!page.rows || page.rows.length === 0) break;
    }
    content = { rows };
  } else {
    const payload = await call(`/artifacts/${artifactId}/content`, { token });
    content = payload.content ?? payload;
  }
  return {
    artifact_id: artifactId,
    content_type: meta.content_type,
    title: meta.title,
    summary: meta.summary,
    payload: meta.payload,
    source_artifact_ids: meta.source_artifact_ids,
    content,
  };
}

function collectText(artifacts) {
  return artifacts
    .filter((item) => item.content_type === 'text' || item.content_type === 'report')
    .map((item) =>
      typeof item.content === 'string'
        ? item.content
        : JSON.stringify(item.content ?? ''),
    )
    .join('\n\n---\n\n');
}

function toList(value) {
  // promptfoo 可能把数组序列化成字符串，直接展开会按字符拆分
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string' && item);
  if (typeof value !== 'string' || !value.trim()) return [];
  const text = value.trim();
  if (text.startsWith('[')) {
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed.filter((item) => typeof item === 'string' && item);
    } catch {
      return [text];
    }
  }
  return [text];
}

module.exports = class AppProvider {
  id() {
    return 'enterprise-agent';
  }

  async callApi(_prompt, context) {
    const vars = context.vars || {};
    const queries = [...toList(vars.history), vars.query];
    try {
      const token = await login();
      let sessionId = null;
      const trace = [];
      let last = null;
      for (const question of queries) {
        last = await call('/ask', {
          method: 'POST',
          token,
          body: { question, session_id: sessionId },
        });
        sessionId = last.session_id || sessionId;
        trace.push({ question, status: last.status });
      }
      const artifacts = [];
      for (const block of last.blocks || []) {
        artifacts.push(await artifact(token, block.artifact_id));
      }
      return {
        output: JSON.stringify({
          case_id: vars.case_id,
          turn: vars.turn,
          status: last.status,
          session_id: sessionId,
          snapshot_id: last.snapshot_id,
          semantic_version: last.semantic_version,
          trace,
          artifacts,
          text: collectText(artifacts),
        }),
      };
    } catch (error) {
      return { error: String(error && error.message ? error.message : error) };
    }
  }
};
