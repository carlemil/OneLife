// undefined (local dev, var unset) → talk to the API on localhost:8000.
// "" (production build behind the Caddy reverse proxy) → same-origin relative
// requests (/api/...), so a remote browser hits the server, not its own localhost.
// Note: `??` not `||` — an explicit empty string must survive as "relative".
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

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
  const data = await res.json().catch(() => ({}));
  // Only treat 401/403 specially for AUTHENTICATED requests (an expired token /
  // onboarding gate). For auth endpoints (login etc.) these mean bad credentials
  // or 2FA-not-set-up, so surface the real message instead.
  if (res.status === 401 && auth) {
    localStorage.removeItem('onelife_token');
    const err = new Error('session expired');
    err.unauthorized = true;
    throw err;
  }
  if (res.status === 403 && auth) {
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
  register: (email, password, display_name, two_factor = true) =>
    req('/api/auth/register', { method: 'POST', auth: false, body: { email, password, display_name, two_factor } }),
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

  // world & maps
  world: () => req('/api/world'),
  worldMap: () => req('/api/worldmap'),
  worldMapTravel: (location_id) => req('/api/worldmap/travel', { method: 'POST', body: { location_id } }),
  getWorldmapPositions: () => req('/api/admin/worldmap/positions'),
  saveWorldmapPositions: (positions) => req('/api/admin/worldmap/positions', { method: 'POST', body: { positions } }),
  travel: (cell_id) => req('/api/travel', { method: 'POST', body: { cell_id } }),

  // atmosphere
  atmosphere: (spotify) => req(`/api/atmosphere?spotify=${spotify ? 1 : 0}`),
  spotifyConfig: () => req('/api/spotify/config', { auth: false }),

  // game
  state: () => req('/api/state'),
  log: () => req('/api/log'),
  leaderboard: ({ offset = 0, limit = 20, q = '', around = 0 } = {}) =>
    req(`/api/leaderboard?offset=${offset}&limit=${limit}&q=${encodeURIComponent(q)}&around=${around}`),
  takeEdge: (edge_id) => req('/api/edge', { method: 'POST', body: { edge_id } }),
  gate: (text) => req('/api/gate/message', { method: 'POST', body: { text } }),
  puzzle: (answer) => req('/api/puzzle/submit', { method: 'POST', body: { answer } }),
  puzzleHint: () => req('/api/puzzle/hint', { method: 'POST' }),
  rollback: (to_seq) => req('/api/rollback', { method: 'POST', body: { to_seq } }),

  // admin (export/import) — all require an allowlisted account
  adminMe: () => req('/api/admin/me'),
  exportContent: () => req('/api/admin/content/export'),
  importContent: (text) => req('/api/admin/content/import', { method: 'POST', body: { text } }),
  exportDb: () => req('/api/admin/db/export'),
  importDb: (data, confirm) => req('/api/admin/db/import', { method: 'POST', body: { data, confirm } }),
  listPlayers: () => req('/api/admin/players'),
  exportPlayer: (id) => req(`/api/admin/player/${id}/export`),
  importPlayer: (data, confirm) => req('/api/admin/player/import', { method: 'POST', body: { data, confirm } }),

  // admin content editor (graph + canonical event log)
  contentAll: () => req('/api/admin/content/all'),
  saveEntity: (kind, entity) => req('/api/admin/content/entity', { method: 'POST', body: { kind, entity } }),
  deleteEntity: (kind, id) => req('/api/admin/content/delete', { method: 'POST', body: { kind, id } }),
  moveNode: (id, x, y) => req('/api/admin/content/move', { method: 'POST', body: { id, x, y } }),
  layoutNodes: (positions) => req('/api/admin/content/layout', { method: 'POST', body: { positions } }),
  contentLog: () => req('/api/admin/content/log'),
  contentUndo: () => req('/api/admin/content/undo', { method: 'POST' }),
  contentRedo: () => req('/api/admin/content/redo', { method: 'POST' }),
  contentUndoTo: (seq) => req('/api/admin/content/undo_to', { method: 'POST', body: { seq } }),
};

// Trigger a browser download of a text body.
export function download(filename, body) {
  const url = URL.createObjectURL(new Blob([body], { type: 'application/octet-stream' }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
