// undefined (local dev, var unset) → talk to the API on localhost:8000.
// "" (production build behind the Caddy reverse proxy) → same-origin relative
// requests (/api/...), so a remote browser hits the server, not its own localhost.
// Note: `??` not `||` — an explicit empty string must survive as "relative".
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

// Absolute base for non-fetch asset URLs (e.g. <img src> for the map backgrounds,
// served by the API at /content-static/...). Empty string in prod = same-origin.
export const API_BASE = BASE;
// Build a URL for a content-repo static asset (path like "images/maps/lund.png").
export const contentAsset = (path) => `${BASE}/content-static/${path}`;

import * as webllm from './webllm.js';

function token() {
  return localStorage.getItem('onelife_token');
}

// Two-phase inference broker (browser provider). If a response carries a
// pending_inference, run it on the player's GPU and POST the raw completions to
// /api/llm/complete; merge its result over any data the phase-1 response already
// returned (edge/atmosphere return usable data immediately + a background pending).
async function withInference(p) {
  const res = await p;
  if (!res || !res.pending_inference) return res;
  const { pending_inference, ...rest } = res;
  const completions = await webllm.runInference(pending_inference);
  const final = await req('/api/llm/complete', {
    method: 'POST',
    body: { turn_token: pending_inference.turn_token, completions },
  });
  return { ...rest, ...final };
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
  // 409 on an authed request = no game selected yet → send the player to the lobby.
  if (res.status === 409 && auth) {
    const err = new Error(data.detail || 'pick a game');
    err.needGame = true;
    throw err;
  }
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

let _health = null;

export const api = {
  hasToken: () => !!token(),
  logout() { localStorage.removeItem('onelife_token'); },

  // Server config incl. which LLM provider is active (cached). In "browser" mode the
  // client boots WebLLM and runs inference on-device; otherwise the server does it.
  async health() {
    if (!_health) _health = await req('/api/health', { auth: false });
    return _health;
  },

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

  // lobby (pick which game to play; independent save per game)
  games: () => req('/api/games'),
  selectGame: (game_id, mode = 'continue') =>
    req('/api/games/select', { method: 'POST', body: { game_id, mode } }),
  leaveGame: () => req('/api/games/leave', { method: 'POST' }),

  // world & maps
  travel: (cell_id) => req('/api/travel', { method: 'POST', body: { cell_id } }),
  // map editor (admin): load every map's items, save one ellipse back to YAML
  mapAll: () => req('/api/admin/map/all'),
  saveMapPos: (kind, id, map) => req('/api/admin/map/save', { method: 'POST', body: { kind, id, map } }),
  mapOverview: () => req('/api/admin/map/overview'),
  saveRoads: (roads) => req('/api/admin/map/roads', { method: 'POST', body: { roads } }),
  setNodeScale: (id, scale) => req('/api/admin/map/scale', { method: 'POST', body: { id, scale } }),
  // Save an icon's stable overview position (normalized 0..1) — dragging a node persists
  // this and nothing else, so no other icon ever moves.
  moveNodeMap: (id, x, y) => req('/api/admin/map/pos', { method: 'POST', body: { id, x, y } }),
  addEdge: (from, to, label = '', bidirectional = true) =>
    req('/api/admin/map/edge', { method: 'POST', body: { from_node: from, to_node: to, label, bidirectional } }),

  // atmosphere
  atmosphere: (spotify) => withInference(req(`/api/atmosphere?spotify=${spotify ? 1 : 0}`)),
  spotifyConfig: () => req('/api/spotify/config', { auth: false }),

  // game
  state: () => req('/api/state'),
  alignment: () => req('/api/alignment'),
  saveClipboard: (text) => req('/api/clipboard', { method: 'POST', body: { text } }),
  log: () => req('/api/log'),
  leaderboard: ({ offset = 0, limit = 20, q = '', around = 0 } = {}) =>
    req(`/api/leaderboard?offset=${offset}&limit=${limit}&q=${encodeURIComponent(q)}&around=${around}`),
  takeEdge: (edge_id) => withInference(req('/api/edge', { method: 'POST', body: { edge_id } })),
  walkTo: (node_id) => req('/api/walk', { method: 'POST', body: { node_id } }),
  gate: (text) => withInference(req('/api/gate/message', { method: 'POST', body: { text } })),
  puzzle: (answer, entry = '') => req('/api/puzzle/submit', { method: 'POST', body: { answer, entry } }),
  puzzleHint: () => withInference(req('/api/puzzle/hint', { method: 'POST' })),
  rollback: (to_seq) => req('/api/rollback', { method: 'POST', body: { to_seq } }),

  // admin (export/import) — all require an allowlisted account
  adminMe: () => req('/api/admin/me'),
  adminGames: () => req('/api/admin/games'),
  switchGame: (game) => req('/api/admin/games/switch', { method: 'POST', body: { game } }),
  exportContent: () => req('/api/admin/content/export'),
  exportDb: () => req('/api/admin/db/export'),
  importDb: (data, confirm) => req('/api/admin/db/import', { method: 'POST', body: { data, confirm } }),
  listPlayers: () => req('/api/admin/players'),
  exportPlayer: (id) => req(`/api/admin/player/${id}/export`),
  importPlayer: (data, confirm) => req('/api/admin/player/import', { method: 'POST', body: { data, confirm } }),

  // admin read-only content graph + node positioning (positions are authored in YAML)
  contentAll: () => req('/api/admin/content/all'),
  moveNode: (id, x, y) => req('/api/admin/content/move', { method: 'POST', body: { id, x, y } }),
  layoutNodes: (positions) => req('/api/admin/content/layout', { method: 'POST', body: { positions } }),
};

// Trigger a browser download of a text body.
export function download(filename, body) {
  const url = URL.createObjectURL(new Blob([body], { type: 'application/octet-stream' }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
