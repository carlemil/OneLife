// Spotify Web Playback SDK + PKCE auth (MEDIA_PROVIDERS.md).
// Full-track playback for Premium users who connect their account. Degrades to
// the 30s-preview crossfade in App.svelte when not connected.
//
// Untested headlessly: needs a Spotify Premium login, SPOTIFY_CLIENT_ID, and the
// app's URL registered as a redirect URI in the Spotify developer dashboard.

const SCOPES = 'streaming user-read-email user-read-private user-modify-playback-state';
const AUTH = 'https://accounts.spotify.com/authorize';
const TOKEN = 'https://accounts.spotify.com/api/token';

// Use the server-configured redirect URI when set (so it can match exactly what
// Spotify accepts); otherwise derive it from where the app is running.
function redirectUri(override) {
  return (override && override.trim()) ? override.trim() : location.origin + location.pathname;
}
function rand(n) {
  const a = new Uint8Array(n); crypto.getRandomValues(a);
  return Array.from(a, (b) => ('0' + b.toString(16)).slice(-2)).join('');
}
async function s256(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return btoa(String.fromCharCode(...new Uint8Array(d)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function store(j) {
  if (j.access_token) localStorage.setItem('sp_token', j.access_token);
  if (j.refresh_token) localStorage.setItem('sp_refresh', j.refresh_token);
  if (j.expires_in) localStorage.setItem('sp_exp', String(Date.now() + j.expires_in * 1000 - 60000));
}

export function isConnected() {
  return !!(localStorage.getItem('sp_token') || localStorage.getItem('sp_refresh'));
}
export function disconnect() {
  ['sp_token', 'sp_refresh', 'sp_exp'].forEach((k) => localStorage.removeItem(k));
  try { _player && _player.pause(); } catch {}
}

export async function connect(clientId, redirect) {
  const verifier = rand(48);
  sessionStorage.setItem('sp_verifier', verifier);
  const challenge = await s256(verifier);
  const p = new URLSearchParams({
    response_type: 'code', client_id: clientId, scope: SCOPES,
    code_challenge_method: 'S256', code_challenge: challenge, redirect_uri: redirectUri(redirect),
  });
  location.href = `${AUTH}?${p}`;
}

// Call on load — if we're returning from Spotify with ?code, exchange it.
export async function handleRedirect(clientId, redirect) {
  const u = new URL(location.href);
  const code = u.searchParams.get('code');
  if (!code) return false;
  const verifier = sessionStorage.getItem('sp_verifier');
  const body = new URLSearchParams({
    grant_type: 'authorization_code', code, redirect_uri: redirectUri(redirect),
    client_id: clientId, code_verifier: verifier || '',
  });
  const r = await fetch(TOKEN, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
  // Strip the ?code from the URL without forcing the configured redirect path.
  history.replaceState({}, '', location.origin + location.pathname);
  if (!r.ok) return false;
  store(await r.json());
  return true;
}

export async function token(clientId) {
  const exp = +(localStorage.getItem('sp_exp') || 0);
  const t = localStorage.getItem('sp_token');
  if (t && Date.now() < exp) return t;
  const refresh = localStorage.getItem('sp_refresh');
  if (!refresh) return t || null;
  const body = new URLSearchParams({ grant_type: 'refresh_token', refresh_token: refresh, client_id: clientId });
  const r = await fetch(TOKEN, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
  if (!r.ok) return null;
  const j = await r.json(); store(j); return j.access_token;
}

let _player = null, _deviceId = null, _curUri = '';

function loadSdk() {
  return new Promise((res, rej) => {
    if (document.getElementById('sp-sdk')) return res();
    const s = document.createElement('script');
    s.id = 'sp-sdk'; s.src = 'https://sdk.scdn.co/spotify-player.js';
    s.onload = res; s.onerror = rej; document.head.appendChild(s);
  });
}

async function ensurePlayer(getToken) {
  if (_deviceId) return _deviceId;
  await loadSdk();
  await new Promise((res) => { if (window.Spotify) res(); else window.onSpotifyWebPlaybackSDKReady = res; });
  if (!_player) {
    _player = new window.Spotify.Player({
      name: 'OneLife', volume: 0.0,
      getOAuthToken: async (cb) => cb(await getToken()),
    });
    _player.addListener('ready', ({ device_id }) => { _deviceId = device_id; });
    await _player.connect();
  }
  for (let i = 0; i < 24 && !_deviceId; i++) await new Promise((r) => setTimeout(r, 250));
  return _deviceId;
}

async function fade(to, ms) {
  if (!_player) return;
  const steps = 12;
  for (let i = 1; i <= steps; i++) {
    await _player.setVolume(Math.max(0, Math.min(1, to * i / steps))).catch(() => {});
    await new Promise((r) => setTimeout(r, ms / steps));
  }
}

// Fade out, switch to `uri`, fade in (a crossfade-style transition on one player).
export async function playWithFade(uri, getToken) {
  if (uri === _curUri) return true;
  const dev = await ensurePlayer(getToken);
  const t = await getToken();
  if (!dev || !t) return false;
  await fade(0, 400);
  _curUri = uri;
  const r = await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${dev}`, {
    method: 'PUT', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ uris: [uri] }),
  });
  await fade(0.7, 900);
  return r.ok;
}

export async function pause() { try { await _player?.pause(); } catch {} _curUri = ''; }
