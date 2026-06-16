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
    // Stale/invalid session — drop it so the UI can fall back to the login screen.
    localStorage.removeItem('onelife_token');
    const err = new Error('session expired');
    err.unauthorized = true;
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 403) {
    const err = new Error(data.detail || 'forbidden');
    err.forbidden = true;
    throw err;
  }
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

export const api = {
  hasToken: () => !!token(),
  logout() { localStorage.removeItem('onelife_token'); },

  // auth
  register: (email, password, display_name) =>
    req('/api/auth/register', { method: 'POST', auth: false, body: { email, password, display_name } }),
  enableTotp: (email, password, code) =>
    req('/api/auth/totp/enable', { method: 'POST', auth: false, body: { email, password, code } }),
  async login(email, password, code) {
    const r = await req('/api/auth/login', { method: 'POST', auth: false, body: { email, password, code } });
    localStorage.setItem('onelife_token', r.token);
    return r;
  },

  // onboarding
  onboarding: () => req('/api/onboarding'),
  submitOnboarding: (answers) => req('/api/onboarding/submit', { method: 'POST', body: { answers } }),

  // atmosphere
  atmosphere: (spotify) => req(`/api/atmosphere?spotify=${spotify ? 1 : 0}`),

  // game
  state: () => req('/api/state'),
  log: () => req('/api/log'),
  leaderboard: () => req('/api/leaderboard'),
  takeEdge: (edge_id) => req('/api/edge', { method: 'POST', body: { edge_id } }),
  gate: (text) => req('/api/gate/message', { method: 'POST', body: { text } }),
  puzzle: (answer) => req('/api/puzzle/submit', { method: 'POST', body: { answer } }),
  rollback: (to_seq) => req('/api/rollback', { method: 'POST', body: { to_seq } }),
};
