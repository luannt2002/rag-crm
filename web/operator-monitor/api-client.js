// api-client.js — thin fetch wrapper for ragbot operator-monitor.
// Domain-neutral. No hardcoded brand / tenant. No credential.
// Configure endpoint via window.RAGBOT_API_BASE (set inline in index.html
// or via URL query ?api_base=...). Token cached in localStorage.

const DEFAULT_API_BASE = 'http://localhost:3004';
const TOKEN_PATH = '/api/ragbot/test/tokens/self';
const HEALTH_PATH = '/health';
const PASS_RATE_PATH = '/api/ragbot/admin/analytics/bots/pass-rate';
const COST_PATH = '/api/ragbot/admin/analytics/bots/cost';
const LATENCY_PATH = '/api/ragbot/admin/analytics/bots/latency';
const SYNC_DOCS_PATH = '/api/ragbot/sync/documents';

const TOKEN_LS_KEY = 'ragbot_self_token';

export function apiBase() {
  return (window.RAGBOT_API_BASE || DEFAULT_API_BASE).replace(/\/+$/, '');
}

async function _fetch(path, { method = 'GET', token, query, headers } = {}) {
  const url = new URL(apiBase() + path);
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v));
      }
    });
  }
  const h = new Headers(headers || {});
  if (token) h.set('Authorization', `Bearer ${token}`);
  h.set('Accept', 'application/json');
  const res = await fetch(url.toString(), { method, headers: h });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch (_e) { data = { raw: text }; }
  if (!res.ok) {
    const err = new Error(`${method} ${path} → ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export async function getToken({ force = false } = {}) {
  if (!force) {
    const cached = localStorage.getItem(TOKEN_LS_KEY);
    if (cached) return cached;
  }
  const data = await _fetch(TOKEN_PATH);
  const tok = data.token || data.access_token || '';
  if (tok) localStorage.setItem(TOKEN_LS_KEY, tok);
  return tok;
}

export async function fetchHealth() {
  return _fetch(HEALTH_PATH);
}

export async function fetchPassRate({ token, since, until } = {}) {
  return _fetch(PASS_RATE_PATH, { token, query: { since, until } });
}

export async function fetchCost({ token, since, until } = {}) {
  return _fetch(COST_PATH, { token, query: { since, until } });
}

export async function fetchLatency({ token, since, until } = {}) {
  return _fetch(LATENCY_PATH, { token, query: { since, until } });
}

export async function fetchDocuments({ token, tenantId, botId, channelType, limit }) {
  if (!tenantId || !botId || !channelType) {
    throw new Error('3-key required: tenant_id + bot_id + channel_type');
  }
  return _fetch(SYNC_DOCS_PATH, {
    token,
    query: {
      tenant_id: tenantId,
      bot_id: botId,
      channel_type: channelType,
      limit,
    },
  });
}

export function clearToken() {
  localStorage.removeItem(TOKEN_LS_KEY);
}
