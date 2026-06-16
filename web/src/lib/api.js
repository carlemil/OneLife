const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

function token() {
  return localStorage.getItem('onelife_token');
}

async function req(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && token()) headers['Authorization'] = `Bearer ${token()}`;
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    // Stale/invalid session (e.g. the DB was reset) — drop it so the UI can
    // fall back to the name screen instead of getting stuck loading.
    localStorage.removeItem('onelife_token');
    const err = new Error('session expired');
    err.unauthorized = true;
    throw err;
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  hasToken: () => !!token(),
  async register(display_name) {
    const r = await req('/api/auth/register', { method: 'POST', body: { display_name }, auth: false });
    localStorage.setItem('onelife_token', r.token);
    return r;
  },
  logout() { localStorage.removeItem('onelife_token'); },
  state: () => req('/api/state'),
  log: () => req('/api/log'),
  leaderboard: () => req('/api/leaderboard'),
  takeEdge: (edge_id) => req('/api/edge', { method: 'POST', body: { edge_id } }),
  gate: (text) => req('/api/gate/message', { method: 'POST', body: { text } }),
  puzzle: (answer) => req('/api/puzzle/submit', { method: 'POST', body: { answer } }),
  rollback: (to_seq) => req('/api/rollback', { method: 'POST', body: { to_seq } }),
};
