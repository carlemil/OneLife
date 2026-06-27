<script>
  import { onMount, tick } from 'svelte';
  import { api, download } from './lib/api.js';
  import * as spotify from './lib/spotify.js';
  import { SvelteFlow, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import StoryNode from './lib/StoryNode.svelte';
  import StoryEdge from './lib/StoryEdge.svelte';
  import MapOverlay from './lib/MapOverlay.svelte';
  import MapEditor from './lib/MapEditor.svelte';

  let phase = $state('loading');        // loading | auth | twofa | onboarding | game
  let authMode = $state('login');       // login | register
  let busy = $state(false);
  let error = $state('');
  let notice = $state('');

  // auth form
  let email = $state('');
  let password = $state('');
  let displayName = $state('');
  let twoFactor = $state(true);   // 2FA on by default; opt out at registration
  let code = $state('');

  // 2fa setup
  let qrSvg = $state('');
  let secret = $state('');
  let recoveryCodes = $state([]);

  // onboarding
  let manual = $state('');
  let questions = $state([]);
  let answers = $state({});
  let quizMsg = $state('');

  // game
  let game = $state(null);
  let logEntries = $state([]);
  // Player clipboard: free-form notes, saved with the profile. Adopted from the
  // server on first load; thereafter locally owned and saved (debounced) on edit.
  let clipboard = $state('');
  let clipLoaded = $state(false);
  let clipTimer;
  $effect(() => { if (game && !clipLoaded) { clipboard = game.clipboard ?? ''; clipLoaded = true; } });
  async function saveClip() {
    clearTimeout(clipTimer);
    try { await api.saveClipboard(clipboard); } catch (_) { /* keep local text */ }
  }
  function onClipInput() { clearTimeout(clipTimer); clipTimer = setTimeout(saveClip, 600); }
  let myWindow = $state([]);
  let gateInput = $state('');
  let gateReply = $state('');           // latest NPC line, echoed by the input for immediacy
  let gatePassed = $state(false);       // did the last turn pass the gate? (shows a note)
  let puzzleResult = $state('');        // latest solve message, echoed by the puzzle input
  let puzzleHintLine = $state('');      // the NPC's spoken hint, shown only after you ask
  // Side-panel accordions: per-panel expanded/collapsed state, persisted.
  let panelOpen = $state(loadPanels());
  function loadPanels() {
    const def = { atmosphere: true, leaderboard: true, notes: true, log: true };
    try { return { ...def, ...JSON.parse(localStorage.getItem('onelife_panels') || '{}') }; }
    catch { return def; }
  }
  function togglePanel(key) {
    panelOpen[key] = !panelOpen[key];
    localStorage.setItem('onelife_panels', JSON.stringify(panelOpen));
  }
  let puzzleInput = $state('');
  let gateEl = $state(null);
  let puzzleEl = $state(null);

  // The running story flow: every beat (log entries + the NPC dialogue woven in
  // by the backend) in chronological order, oldest first. Paged from the top so
  // a long game can't render thousands of beats at once.
  const FEED_PAGE = 40;
  let feedShown = $state(FEED_PAGE);
  let feed = $derived(logEntries || []);
  let sceneEl = $state(null);   // the live "current scene" — we scroll it into view
  let _seenBeats = 0;
  // Icon per beat kind; dialogue and scene lines carry none.
  const KIND_ICON = { action: '›', gate: '›', puzzle: '🧩', clue: '✦', travel: '🗺', death: '✝' };

  // Side "Log" panel: the progress beats (no NPC dialogue), newest first, paged.
  // The main-column flow is unaffected; this is the compact progress log + rollback.
  const LOG_PAGE = 10;
  let logShown = $state(LOG_PAGE);
  let logRev = $derived((logEntries || []).filter((l) => l.kind !== 'dialogue').slice().reverse());

  // map: a single icon-on-parchment overview (game.map) opened as a full-screen
  // overlay above the rest of the UI; clicking a place walks or travels there.
  let showMap = $state(false);
  let showMapEditor = $state(false);
  const gameMap = $derived(game?.map ?? null);

  // in-game help
  let showHelp = $state(false);
  let helpText = $state('');

  // leaderboard page
  const LB_LIMIT = 20;
  let showLb = $state(false);
  let lbRows = $state([]);
  let lbMe = $state(null);
  let lbTotal = $state(0);
  let lbOffset = $state(0);
  let lbQuery = $state('');
  let lbPos = $state('');

  // admin (hidden export/import)
  let isAdmin = $state(false);
  let adminGame = $state('');
  let showAdmin = $state(false);
  let adminMsg = $state('');
  let adminPlayers = $state([]);
  let selPlayer = $state('');
  let dbConfirm = $state('');
  let savConfirm = $state('');

  // admin: read-only story-graph view (authoring lives in the YAML files; the only
  // mutation here is dragging a node, which writes its position back into the YAML)
  let showEditor = $state(false);
  let content = $state(null);       // full authored set, loaded once (read-only)
  let graphMsg = $state('');
  let flowNodes = $state.raw([]);
  let flowEdges = $state.raw([]);
  let autoSpreadDone = false;        // auto-arrange once if no saved positions yet
  let hover = $state(null);         // {kind:'node'|'edge', rec, x, y}
  const nodeTypes = { story: StoryNode };
  const edgeTypes = { story: StoryEdge };

  // atmosphere (image + Spotify soundtrack)
  let atmo = $state(null);
  let spotifyOn = $state(false);
  let spConfig = $state(null);    // {client_id, configured}
  let spConnected = $state(false); // Web Playback SDK connected (full tracks)
  let spotifyVol = $state(0.7);    // shared soundtrack volume (0..1)
  let _players = [];
  let _active = 0;
  let _curUrl = '';
  let _previewTimer = null;
  let _lastNode = '';

  function _ensureAudio() {
    if (!_players.length) {
      _players = [new Audio(), new Audio()];
      _players.forEach((p) => { p.loop = true; p.volume = 0; });
    }
  }
  function crossfade(url) {
    _ensureAudio();
    if (url === _curUrl) return;
    _curUrl = url;
    const cur = _players[_active], next = _players[1 - _active];
    next.src = url; next.currentTime = 0; next.volume = 0;
    next.play().catch(() => {});
    _active = 1 - _active;
    const start = performance.now(), dur = 1500, fromVol = cur.volume;
    function step(t) {
      const k = Math.min(1, (t - start) / dur);
      next.volume = spotifyVol * k; cur.volume = fromVol * (1 - k);
      if (k < 1) requestAnimationFrame(step); else cur.pause();
    }
    requestAnimationFrame(step);
  }
  // Don't cut a preview off mid-clip (same rule as the full-track path):
  // >1 min left → crossfade now; ≤1 min left → wait for it to finish; else start now.
  function playPreview(url) {
    _ensureAudio();
    if (url === _curUrl) { _clearPreviewTimer(); return; }
    _clearPreviewTimer();
    const cur = _players[_active];
    const playing = cur && !cur.paused && cur.duration && _curUrl;
    const left = playing ? cur.duration - cur.currentTime : 0;  // seconds
    if (playing && left > 60) { crossfade(url); }                // plenty left → crossfade now
    else if (playing && left > 0) {                              // almost over → wait it out
      _previewTimer = setTimeout(() => { _previewTimer = null; crossfade(url); }, (left + 0.2) * 1000);
    } else { crossfade(url); }                                   // nothing playing → start now
  }
  function _clearPreviewTimer() { if (_previewTimer) { clearTimeout(_previewTimer); _previewTimer = null; } }
  function stopAudio() { _clearPreviewTimer(); _players.forEach((p) => { try { p.pause(); } catch {} }); _curUrl = ''; }
  function setSpotifyVol(v) {
    spotifyVol = Math.max(0, Math.min(1, Number(v) || 0));
    localStorage.setItem('onelife_spotify_vol', String(spotifyVol));
    spotify.setVolume(spotifyVol);                 // full-track SDK path
    if (_players[_active]) _players[_active].volume = spotifyVol;  // 30s-preview path
  }

  async function loadAtmosphere() {
    try {
      atmo = await api.atmosphere(spotifyOn);
      if (!spotifyOn) { stopAudio(); spotify.pause(); return; }
      const full = (atmo.tracks || []).find((x) => x.uri);
      const prev = (atmo.tracks || []).find((x) => x.preview_url);
      if (spConnected && full) {
        // Full-track playback via the Web Playback SDK (Premium).
        stopAudio();
        spotify.playWithFade(full.uri, () => spotify.token(spConfig.client_id));
      } else if (prev) {
        playPreview(prev.preview_url);   // 30s-preview fallback
      }
    } catch { /* atmosphere is non-critical */ }
  }
  function toggleSpotify() {
    spotifyOn = !spotifyOn;
    localStorage.setItem('onelife_spotify', spotifyOn ? '1' : '0');
    if (!spotifyOn) { stopAudio(); spotify.pause(); }
    loadAtmosphere();
  }
  function connectSpotify() { if (spConfig?.client_id) spotify.connect(spConfig.client_id, spConfig.redirect_uri); }
  function disconnectSpotify() { spotify.disconnect(); spConnected = false; }

  // Reload atmosphere whenever the location (node) changes.
  $effect(() => {
    const id = game?.node?.id;
    if (phase === 'game' && id && id !== _lastNode) { _lastNode = id; loadAtmosphere(); }
  });

  // After acting, keep the scene's controls in view (the log grows below them).
  $effect(() => {
    const n = feed.length;
    if (phase === 'game' && n !== _seenBeats) {
      _seenBeats = n;
      tick().then(() => sceneEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
    }
  });

  onMount(async () => {
    spotifyOn = localStorage.getItem('onelife_spotify') === '1';
    const savedVol = localStorage.getItem('onelife_spotify_vol');
    if (savedVol !== null) setSpotifyVol(savedVol);
    try {
      spConfig = await api.spotifyConfig();
      if (spConfig.configured) {
        const justReturned = await spotify.handleRedirect(spConfig.client_id, spConfig.redirect_uri);
        spConnected = justReturned || spotify.isConnected();
      }
    } catch { /* spotify is optional */ }
    if (!api.hasToken()) { phase = 'auth'; return; }
    try {
      await loadGame();
    } catch (e) {
      if (e.unauthorized) phase = 'auth';
      else if (e.forbidden) await loadOnboarding();
      else { error = e.message; phase = 'auth'; }
    }
  });

  // ---------- auth ----------
  async function doRegister() {
    error = ''; busy = true;
    try {
      const r = await api.register(email.trim(), password, displayName.trim(), twoFactor);
      if (r.two_factor) {
        qrSvg = r.qr_svg; secret = r.secret;
        phase = 'twofa';
        notice = 'Scan the QR with an authenticator app, then enter a code to finish setup.';
      } else {
        // Password-only account: log straight in, no code needed.
        notice = '';
        const lr = await api.login(email.trim(), password, '');
        if (lr.onboarded) await loadGame(); else await loadOnboarding();
      }
    } catch (e) { error = e.message; }
    finally { busy = false; }
  }

  async function doEnable() {
    error = ''; busy = true;
    try {
      const r = await api.enableTotp(email.trim(), password, code.trim());
      recoveryCodes = r.recovery_codes || [];
      code = '';
      phase = 'recovery';   // show backup codes once before continuing
    } catch (e) { error = e.message; }
    finally { busy = false; }
  }

  function recoveryDone() {
    recoveryCodes = [];
    notice = '2FA enabled. Log in with your authenticator code.';
    authMode = 'login'; phase = 'auth';
  }

  async function doLogin() {
    error = ''; busy = true;
    try {
      const r = await api.login(email.trim(), password, code.trim());
      code = '';
      if (r.onboarded) await loadGame(); else await loadOnboarding();
    } catch (e) { error = e.message; }
    finally { busy = false; }
  }

  function authSubmit() { if (authMode === 'register') doRegister(); else doLogin(); }

  function confirmLogout() { if (confirm('Log out? Your progress is saved — log back in any time.')) logout(); }

  function logout() {
    api.logout(); game = null; clipLoaded = false; clipboard = ''; phase = 'auth'; authMode = 'login';
    email = ''; password = ''; code = ''; notice = '';
  }

  // ---------- onboarding ----------
  async function loadOnboarding() {
    const r = await api.onboarding();
    manual = r.manual; questions = r.questions; answers = {}; quizMsg = '';
    phase = 'onboarding';
  }

  function pick(qid, i) { answers = { ...answers, [qid]: i }; }

  async function submitQuiz() {
    error = ''; busy = true;
    try {
      const r = await api.submitOnboarding(answers);
      if (r.passed) await loadGame();
      else quizMsg = `You got ${r.score}/${r.total}. Read the manual again and retry.`;
    } catch (e) { error = e.message; }
    finally { busy = false; }
  }

  // ---------- game ----------
  async function loadGame() {
    game = await api.state();
    logEntries = (await api.log()).entries;
    myWindow = (await api.leaderboard({ around: 1 })).rows;
    error = ''; notice = ''; phase = 'game';
    try { const me = await api.adminMe(); isAdmin = me.is_admin; adminGame = me.game || ''; }
    catch { isAdmin = false; }
  }

  // ---------- leaderboard page ----------
  async function loadLb() {
    try {
      const r = await api.leaderboard({ offset: lbOffset, limit: LB_LIMIT, q: lbQuery });
      lbRows = r.rows; lbMe = r.me; lbTotal = r.total;
    } catch (e) { error = e.message; }
  }
  async function openLeaderboard() { lbQuery = ''; lbOffset = 0; lbPos = ''; await loadLb(); showLb = true; }
  function searchLb() { lbOffset = 0; loadLb(); }
  function clearSearch() { lbQuery = ''; lbOffset = 0; loadLb(); }
  function jumpToMe() { if (lbMe) { lbQuery = ''; lbOffset = Math.max(0, lbMe.rank - Math.ceil(LB_LIMIT / 2)); loadLb(); } }
  function goToPos() { const p = parseInt(lbPos, 10); if (p > 0) { lbQuery = ''; lbOffset = Math.max(0, p - 1); loadLb(); } }
  function lbPrev() { lbOffset = Math.max(0, lbOffset - LB_LIMIT); loadLb(); }
  function lbNext() { if (lbOffset + LB_LIMIT < lbTotal) { lbOffset += LB_LIMIT; loadLb(); } }

  async function openHelp() {
    if (!helpText) {
      try { helpText = (await api.onboarding()).manual; } catch { helpText = 'Help is unavailable right now.'; }
    }
    showHelp = true;
  }

  // ---------- admin ----------
  async function openAdmin() {
    adminMsg = ''; showAdmin = true;
    try { adminPlayers = (await api.listPlayers()).players; } catch (e) { adminMsg = e.message; }
  }
  async function readJson(ev) {
    const f = ev.target.files?.[0]; if (!f) return null;
    return JSON.parse(await f.text());
  }
  async function exportContent() {
    try { const r = await api.exportContent(); download(r.filename, r.body); }
    catch (e) { adminMsg = e.message; }
  }
  async function exportDb() {
    try { const r = await api.exportDb(); download(r.filename, r.body); }
    catch (e) { adminMsg = e.message; }
  }
  async function importDbFile(ev) {
    try { const data = await readJson(ev); await api.importDb(data, dbConfirm); adminMsg = 'Database replaced.'; dbConfirm = ''; }
    catch (e) { adminMsg = e.message; } finally { ev.target.value = ''; }
  }
  async function exportPlayer() {
    if (!selPlayer) return;
    try { const r = await api.exportPlayer(selPlayer); download(r.filename, r.body); }
    catch (e) { adminMsg = e.message; }
  }
  async function importPlayerFile(ev) {
    try { const data = await readJson(ev); await api.importPlayer(data, savConfirm); adminMsg = 'Player save restored.'; savConfirm = ''; }
    catch (e) { adminMsg = e.message; } finally { ev.target.value = ''; }
  }

  // ---------- admin: read-only story-graph view ----------
  // Authoring happens in the YAML files. This view just renders the graph and lets
  // an admin drag nodes to reposition them (persisted back into the YAML).
  async function openGraph() {
    graphMsg = ''; showEditor = true;
    try { content = await api.contentAll(); } catch (e) { graphMsg = e.message; return; }
    await setGraphView();
  }

  // ---------- graph view ----------
  function autoLayout(nodes, edges) {
    // Layered by BFS depth from the entry node; fallback grid for the rest.
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const adj = {};
    for (const e of edges) (adj[e.from] ??= []).push(e.to);
    const entry = nodes.find((n) => n.entry)?.id ?? nodes[0]?.id;
    const depth = {};
    const q = entry ? [entry] : [];
    if (entry) depth[entry] = 0;
    while (q.length) {
      const x = q.shift();
      for (const t of adj[x] ?? []) if (byId[t] && depth[t] === undefined) { depth[t] = depth[x] + 1; q.push(t); }
    }
    let orphan = 0;
    const rows = {};
    const pos = {};
    for (const n of nodes) {
      const d = depth[n.id] ?? (8 + orphan++ % 1);   // unreached: push to a far column
      const col = depth[n.id] !== undefined ? d : 8;
      rows[col] = (rows[col] ?? 0) + 1;
      pos[n.id] = { x: col * 230, y: (rows[col] - 1) * 95 };
    }
    return pos;
  }
  function buildFlow() {
    if (!content) return;
    const auto = autoLayout(content.nodes, content.edges);
    const stored = content.positions ?? {};
    flowNodes = content.nodes.map((n) => ({
      id: n.id, type: 'story',
      position: stored[n.id] ?? auto[n.id] ?? { x: 0, y: 0 },
      data: { ntype: n.type, rec: n },
    }));
    // Group edges by unordered endpoint pair so parallel/antiparallel siblings can
    // fan their labels apart (see StoryEdge). `sign` keeps the fan consistent for a
    // pair's reversed twin (canonical source = the lexically smaller node id).
    const pairKey = (e) => (e.from < e.to ? `${e.from}|${e.to}` : `${e.to}|${e.from}`);
    const groups = {};
    for (const e of content.edges) (groups[pairKey(e)] ??= []).push(e);
    const pairMeta = {};
    for (const k in groups) {
      const arr = groups[k], canon = k.split('|')[0];
      arr.forEach((e, i) => { pairMeta[e.id] = { pairIndex: i, pairCount: arr.length, sign: e.from === canon ? 1 : -1 }; });
    }
    flowEdges = content.edges.map((e) => ({
      id: e.id, source: e.from, target: e.to, type: 'story', label: e.label || '',
      markerEnd: { type: 'arrowclosed' },
      style: e.danger ? 'stroke:#c0563a;stroke-width:2' : '',
      data: { rec: e, ...pairMeta[e.id] },
    }));
  }
  async function setGraphView() {
    if (!content) { try { content = await api.contentAll(); } catch (e) { graphMsg = e.message; return; } }
    buildFlow();
    // First open ever on this server (no positions saved yet) → arrange once.
    // Spreading persists positions, so it never auto-runs again.
    if (!autoSpreadDone && !Object.keys(content.positions ?? {}).length) {
      autoSpreadDone = true;
      await spreadOut();
    }
  }
  async function onNodeDragStop({ targetNode }) {
    if (!targetNode) return;
    try { await api.moveNode(targetNode.id, targetNode.position.x, targetNode.position.y); }
    catch (e) { graphMsg = e.message; }
  }
  // Force-directed layout of ONE connected cluster: repel every node, pull
  // connected nodes toward an ideal edge length K, repel overlapping EDGE LABELS
  // (so the graph opens up to give labels room), then resolve node-box overlaps.
  // edgeList entries are [fromId, toId, own] where `own` is the label's fraction
  // along the edge (matches StoryEdge). Deterministic. Returns {id:{x,y}}.
  function _simulate(nodeIds, edgeList, K) {
    const N = nodeIds.length;
    const idx = Object.fromEntries(nodeIds.map((id, i) => [id, i]));
    const px = new Array(N), py = new Array(N);
    const R = K * Math.max(1, Math.sqrt(N) / 1.6);
    for (let i = 0; i < N; i++) { const a = (i / N) * Math.PI * 2; px[i] = Math.cos(a) * R; py[i] = Math.sin(a) * R; }
    const E = edgeList.map(([a, b, own]) => [idx[a], idx[b], own ?? 0.5]).filter(([a, b]) => a !== undefined && b !== undefined);
    const LW = 160, LH = 48;                          // edge-label box (wrapped labels)
    let temp = K;
    for (let it = 0; it < 420; it++) {
      const dx = new Array(N).fill(0), dy = new Array(N).fill(0);
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {     // node repulsion
        let vx = px[i] - px[j], vy = py[i] - py[j], d2 = vx * vx + vy * vy;
        if (d2 < 0.01) { vx = (i - j) || 1; vy = 1; d2 = vx * vx + vy * vy; }
        const d = Math.sqrt(d2), f = (K * K) / d, ux = vx / d, uy = vy / d;
        dx[i] += ux * f; dy[i] += uy * f; dx[j] -= ux * f; dy[j] -= uy * f;
      }
      for (const [a, b] of E) {                                        // edge springs
        let vx = px[a] - px[b], vy = py[a] - py[b];
        const d = Math.hypot(vx, vy) || 0.01, f = (d * d) / K, ux = vx / d, uy = vy / d;
        dx[a] -= ux * f; dy[a] -= uy * f; dx[b] += ux * f; dy[b] += uy * f;
      }
      // Edge-label repulsion: when two labels' boxes overlap, push their edges
      // (both endpoints) apart, opening up room for the labels.
      for (let i = 0; i < E.length; i++) {
        const [ai, bi, oi] = E[i], lix = px[ai] + (px[bi] - px[ai]) * oi, liy = py[ai] + (py[bi] - py[ai]) * oi;
        for (let j = i + 1; j < E.length; j++) {
          const [aj, bj, oj] = E[j], ljx = px[aj] + (px[bj] - px[aj]) * oj, ljy = py[aj] + (py[bj] - py[aj]) * oj;
          const vx = lix - ljx, vy = liy - ljy, ox = LW - Math.abs(vx), oy = LH - Math.abs(vy);
          if (ox > 0 && oy > 0) {                                      // label boxes overlap
            const len = Math.hypot(vx, vy) || 0.01, ux = vx / len, uy = vy / len, f = Math.min(ox, oy) * 0.5;
            dx[ai] += ux * f; dy[ai] += uy * f; dx[bi] += ux * f; dy[bi] += uy * f;
            dx[aj] -= ux * f; dy[aj] -= uy * f; dx[bj] -= ux * f; dy[bj] -= uy * f;
          }
        }
      }
      for (let i = 0; i < N; i++) {                                    // integrate, capped by temperature
        const d = Math.hypot(dx[i], dy[i]) || 0.01;
        px[i] += (dx[i] / d) * Math.min(d, temp); py[i] += (dy[i] / d) * Math.min(d, temp);
      }
      temp = Math.max(temp * 0.985, K * 0.05);
    }
    const NW = 220, NH = 96;                          // node footprint + margin
    for (let pass = 0; pass < 90; pass++) {           // resolve any remaining node-box overlaps
      let hit = false;
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        const vx = px[j] - px[i], vy = py[j] - py[i], ox = NW - Math.abs(vx), oy = NH - Math.abs(vy);
        if (ox > 0 && oy > 0) {
          if (ox <= oy) { const s = (ox / 2 + 0.5) * (vx < 0 ? -1 : 1); px[i] -= s; px[j] += s; }
          else { const s = (oy / 2 + 0.5) * (vy < 0 ? -1 : 1); py[i] -= s; py[j] += s; }
          hit = true;
        }
      }
      if (!hit) break;
    }
    const out = {};
    for (let i = 0; i < N; i++) out[nodeIds[i]] = { x: px[i], y: py[i] };
    return out;
  }
  // The story graph splits into disconnected clusters (cells joined only by
  // implicit travel, not edges). Lay each cluster out on its own, then pack them
  // close together — gap = 2x the average neighbour distance — so no cluster drifts
  // off where you can't find it. Deterministic.
  function forceLayout(fnodes, fedges) {
    const ids = fnodes.map((n) => n.id);
    const idset = new Set(ids);
    const ownOf = (e) => {                                     // label's fraction along the edge (matches StoryEdge)
      const c = e.data?.pairCount ?? 1; if (c < 2) return 0.5;
      const i = e.data?.pairIndex ?? 0, s = e.data?.sign ?? 1;
      const f = Math.min(0.88, Math.max(0.12, 0.5 + (i - (c - 1) / 2) * 0.2));
      return s > 0 ? f : 1 - f;
    };
    const edges = fedges.filter((e) => idset.has(e.source) && idset.has(e.target)).map((e) => [e.source, e.target, ownOf(e)]);
    const adj = {}; ids.forEach((id) => (adj[id] = []));
    for (const [a, b] of edges) { adj[a].push(b); adj[b].push(a); }
    const compOf = {}; const comps = [];                       // connected components (BFS)
    for (const id of ids) {
      if (compOf[id] !== undefined) continue;
      const ci = comps.length, group = [], q = [id]; compOf[id] = ci;
      while (q.length) { const x = q.shift(); group.push(x); for (const t of adj[x]) if (compOf[t] === undefined) { compOf[t] = ci; q.push(t); } }
      comps.push(group);
    }
    const K = 200;   // ideal edge length / spacing (tightened 1.5x from 300)
    const blocks = comps.map((group, ci) => {
      const pos = _simulate(group, edges.filter(([a]) => compOf[a] === ci), K);
      let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
      for (const id of group) { const p = pos[id]; if (p.x < minx) minx = p.x; if (p.y < miny) miny = p.y; if (p.x > maxx) maxx = p.x; if (p.y > maxy) maxy = p.y; }
      return { group, pos, minx, miny, w: maxx - minx, h: maxy - miny };
    });
    let sum = 0, cnt = 0;                                       // average neighbour (edge) distance
    for (const [a, b] of edges) { const pa = blocks[compOf[a]].pos[a], pb = blocks[compOf[b]].pos[b]; sum += Math.hypot(pa.x - pb.x, pa.y - pb.y); cnt++; }
    const GAP = 2 * (cnt ? sum / cnt : K);
    const order = blocks.map((b, i) => i).sort((i, j) => blocks[j].h - blocks[i].h);  // shelf-pack, tallest first
    const rowW = Math.max(Math.sqrt(blocks.reduce((s, b) => s + (b.w + GAP) * (b.h + GAP), 0)), ...blocks.map((b) => b.w));
    const out = {};
    let curX = 0, curY = 0, rowH = 0;
    for (const i of order) {
      const b = blocks[i];
      if (curX > 0 && curX + b.w > rowW) { curX = 0; curY += rowH + GAP; rowH = 0; }
      for (const id of b.group) out[id] = { x: Math.round(curX + (b.pos[id].x - b.minx) + 40), y: Math.round(curY + (b.pos[id].y - b.miny) + 40) };
      curX += b.w + GAP; rowH = Math.max(rowH, b.h);
    }
    return out;
  }
  async function spreadOut() {
    if (!flowNodes.length) return;
    busy = true; graphMsg = '';
    try {
      const pos = forceLayout(flowNodes, flowEdges);
      flowNodes = flowNodes.map((n) => ({ ...n, position: pos[n.id] }));   // instant feedback
      await api.layoutNodes(pos);                                          // writes pos to YAML
      graphMsg = 'Spread the nodes out and saved their positions to the YAML files.';
    } catch (e) { graphMsg = e.message; } finally { busy = false; }
  }
  function showHover(kind, rec, ev) { hover = { kind, rec, x: ev.clientX, y: ev.clientY }; }

  async function refresh() {
    try {
      game = await api.state();
      logEntries = (await api.log()).entries;
      myWindow = (await api.leaderboard({ around: 1 })).rows;
    } catch (e) {
      if (e.unauthorized) logout(); else error = e.message;
    }
  }

  // Click a place on the overview map → walk (within the cell) or travel (to an
  // adjacent cell), per the item's action. The backend finds the shortest path
  // within the cell, so any reachable place is one click. (MapOverlay already
  // played the walk animation before calling back.) Close the map on success.
  async function mapNav(item) {
    if (!item?.reachable || !item.action) return;
    busy = true;
    gateReply = ''; gatePassed = false; puzzleResult = ''; puzzleHintLine = '';
    try {
      game = item.action === 'travel'
        ? await api.travel(item.target) : await api.walkTo(item.target);
      logEntries = (await api.log()).entries;
      showMap = false;
    } catch (e) { error = e.message; } finally { busy = false; }
  }

  async function onEdge(id) {
    busy = true;
    gateReply = ''; gatePassed = false; puzzleResult = ''; puzzleHintLine = '';   // a fresh scene clears the last reply
    try { game = await api.takeEdge(id); logEntries = (await api.log()).entries; }
    catch (e) { error = e.message; } finally { busy = false; }
  }
  async function onGate() {
    if (!gateInput.trim()) return; busy = true;
    try {
      const r = await api.gate(gateInput.trim());
      gateInput = '';
      gateReply = r.result?.reply || '';     // echo the NPC's line by the input
      gatePassed = !!r.result?.satisfied;    // …and whether that opened the gate
      game = r.state; logEntries = (await api.log()).entries;
    }
    catch (e) { error = e.message; }
    finally { busy = false; await tick(); gateEl?.focus(); }   // refocus after re-enable
  }
  async function onPuzzle() {
    if (!puzzleInput.trim()) return; busy = true;
    try {
      const r = await api.puzzle(puzzleInput.trim());
      game = r.state; logEntries = (await api.log()).entries;
      // Echo the outcome by the input. A wrong guess says so but volunteers no
      // hint — the player must ask for one (askHint), and only after trying.
      if (r.result.solved) {
        puzzleResult = r.result.message || ''; error = '';
      } else {
        puzzleResult = ''; error = r.result.message || 'Nothing happens.';
      }
      puzzleInput = '';
    } catch (e) { error = e.message; }
    finally { busy = false; await tick(); puzzleEl?.focus(); }
  }
  async function askHint() {
    busy = true;
    try {
      const r = await api.puzzleHint();   // the NPC speaks the next hint, only on ask
      game = r.state;
      puzzleHintLine = r.result?.hint || '';
      error = (!r.result?.hint && r.result?.message) ? r.result.message : '';
      logEntries = (await api.log()).entries;   // the spoken hint is also a beat
    } catch (e) { error = e.message; } finally { busy = false; }
  }
  async function onRollback(seq) {
    if (!confirm(`Cheat death — return to step ${seq}? You'll lose all progress made after it.`)) return;
    busy = true;
    try { game = await api.rollback(seq); logEntries = (await api.log()).entries; myWindow = (await api.leaderboard({ around: 1 })).rows; }
    catch (e) { error = e.message; } finally { busy = false; }
  }
</script>

<main>
  <h1>OneLife <span class="sub">— prototype slice</span></h1>
  {#if error}<div class="error">{error}</div>{/if}
  {#if notice}<div class="notice">{notice}</div>{/if}

  {#if phase === 'loading'}
    <p>Loading…</p>

  {:else if phase === 'auth'}
    <div class="panel narrow">
      <h2>{authMode === 'login' ? 'Log in' : 'Create account'}</h2>
      <input type="email" bind:value={email} placeholder="Email" onkeydown={(e) => e.key === 'Enter' && authSubmit()} />
      <input type="password" bind:value={password} placeholder="Password (min 8 chars)" onkeydown={(e) => e.key === 'Enter' && authSubmit()} />
      {#if authMode==='register'}
        <input bind:value={displayName} placeholder="Display name" onkeydown={(e) => e.key === 'Enter' && doRegister()} />
        <label class="opt toggle"><input type="checkbox" bind:checked={twoFactor} /> Protect this account with two-factor auth (recommended)</label>
        <button class="primary" onclick={doRegister} disabled={busy}>Create account</button>
        <p class="switch">Already have an account? <button class="link" onclick={() => { authMode='login'; error=''; }}>Log in</button></p>
      {:else}
        <input bind:value={code} placeholder="Authenticator code (blank if 2FA is off)" onkeydown={(e) => e.key === 'Enter' && doLogin()} />
        <button class="primary" onclick={doLogin} disabled={busy}>Log in</button>
        <p class="switch">No account yet? <button class="link" onclick={() => { authMode='register'; error=''; }}>Register</button></p>
      {/if}
    </div>

  {:else if phase === 'twofa'}
    <div class="panel narrow">
      <h2>Set up two-factor auth</h2>
      <p>Scan this with Google Authenticator, Authy, 1Password, etc. — then enter a code to confirm.</p>
      <div class="qr">{@html qrSvg}</div>
      <p class="sub">Can't scan? Secret: <code>{secret}</code></p>
      <input bind:value={code} placeholder="6-digit code" inputmode="numeric" onkeydown={(e) => e.key === 'Enter' && doEnable()} />
      <button class="primary" onclick={doEnable} disabled={busy}>Enable 2FA &amp; continue</button>
    </div>

  {:else if phase === 'recovery'}
    <div class="panel narrow">
      <h2>Save your recovery codes</h2>
      <p>If you lose your authenticator, each code below logs you in <b>once</b>. Store them somewhere safe — they won't be shown again.</p>
      <ul class="codes">{#each recoveryCodes as c}<li><code>{c}</code></li>{/each}</ul>
      <button class="primary" onclick={recoveryDone}>I've saved them — continue</button>
    </div>

  {:else if phase === 'onboarding'}
    <div class="panel">
      <h2>Before you begin</h2>
      <pre class="manual">{manual}</pre>
      <h3>Quick check</h3>
      {#each questions as q}
        <div class="quiz-q">
          <p class="q">{q.prompt}</p>
          {#each q.options as opt, i}
            <label class="opt">
              <input type="radio" name={q.id} checked={answers[q.id]===i} onchange={() => pick(q.id, i)} />
              {opt}
            </label>
          {/each}
        </div>
      {/each}
      {#if quizMsg}<div class="error">{quizMsg}</div>{/if}
      <button class="primary" onclick={submitQuiz} disabled={busy}>Begin</button>
    </div>

  {:else if phase === 'game' && game}
    <div class="topbar">
      <button class="link" title="How to play" onclick={openHelp}>❓</button>
      {#if isAdmin}<button class="link" title="Admin & settings" onclick={openAdmin}>⚙</button>{/if}
      <button class="link" title="Log out" onclick={confirmLogout}>🚪</button>
    </div>
    <div class="layout">
      <section class="story">
        <!-- The current moment: where the player acts next. -->
        <div class="scene" bind:this={sceneEl}>
          <div class="bannerrow">
            {#if atmo?.image_url}
              <div class="banner"><img src={atmo.image_url} alt="" onerror={() => { if (atmo) atmo.image_url = null; }} /><span class="setting">{atmo.setting}</span></div>
            {:else if atmo?.image_svg}
              <div class="banner">{@html atmo.image_svg}<span class="setting">{atmo.setting}</span></div>
            {/if}
            {#if gameMap && gameMap.nodes.length}
              <button class="mapthumb" onclick={() => (showMap = true)} disabled={busy}
                      aria-label="Open map" title="Open map">
                <img src="/map-button.png" alt="Map" draggable="false" />
              </button>
            {/if}
          </div>
          <h2>{game.node.title}</h2>
          <p class="body">{game.node.body}</p>
          <p class="media">🎨 {game.node.media.image_theme} &nbsp; 🎵 {game.node.media.music_theme}</p>

          {#if game.node.type === 'gate' && game.gate}
            <form class="row" onsubmit={(e) => { e.preventDefault(); onGate(); }}>
              <input bind:this={gateEl} bind:value={gateInput} placeholder={game.gate.satisfied ? 'Keep talking, or choose a way onward below…' : 'Say something...'} disabled={busy} />
              <button type="submit" disabled={busy}>{#if busy}<span class="spinner"></span>{:else}Say{/if}</button>
            </form>
            {#if gateReply}<p class="gate-reply">{gateReply}</p>{/if}
            {#if gatePassed}<p class="gate-passed">✓ You got through to them — the way ahead has opened.</p>{/if}
          {/if}

          {#if game.node.type === 'puzzle' && game.puzzle}
            <p class="puzzle-prompt">{game.puzzle.prompt}</p>
            {#if puzzleHintLine}<p class="puzzle-hint">💡 {puzzleHintLine}</p>{/if}
            {#if !game.puzzle.solved}
              <form class="row" onsubmit={(e) => { e.preventDefault(); onPuzzle(); }}>
                <input bind:this={puzzleEl} bind:value={puzzleInput} placeholder="Enter your answer..." disabled={busy} />
                <button type="submit" disabled={busy}>{#if busy}<span class="spinner"></span>{:else}Try{/if}</button>
              </form>
              {#if game.puzzle.attempts > 0}
                <button class="link askhint" onclick={askHint} disabled={busy}>{puzzleHintLine ? 'Ask for another hint 💡' : 'Stuck? Ask for a hint 💡'}</button>
              {/if}
            {/if}
            {#if puzzleResult}<p class="gate-reply">{puzzleResult}</p>{/if}
          {/if}

          <div class="edges">
            {#each game.edges.filter((e) => !e.on_map) as e}
              <button class:danger={e.danger > 0} onclick={() => onEdge(e.id)} disabled={busy}>
                {e.label}{#if e.danger > 0} ⚠{/if}
              </button>
            {/each}
            {#if game.edges.length === 0 && game.node.is_death}
              <p class="dead">You are dead. In the <b>Log</b> panel (right), use ↩ on an earlier <b>gate</b> you talked your way through to cheat death and return there — at a cost.</p>
            {/if}
          </div>
        </div>

        <!-- The running log of everything that has happened, kept at the bottom. -->
        <div class="transcript">
          <h3 class="flow-head">The log so far <span class="sub">— newest first</span></h3>
          {#each feed.slice(Math.max(0, feed.length - feedShown)).reverse() as l}
            {#if l.kind === 'dialogue'}
              <p class="beat dialogue {l.speaker === 'You' ? 'me' : 'npc'}"><b class="who">{l.speaker}:</b> {l.summary}</p>
            {:else}
              <p class="beat beat-{l.kind}">
                {#if KIND_ICON[l.kind]}<span class="ic">{KIND_ICON[l.kind]}</span> {/if}{l.summary || '…'}
              </p>
            {/if}
          {/each}
          {#if feed.length > feedShown}
            <button class="link more earlier" onclick={() => (feedShown += FEED_PAGE)}>↓ earlier ({feed.length - feedShown})</button>
          {/if}
        </div>
      </section>

      <aside class="side">
        <div class="panel" class:collapsed={!panelOpen.atmosphere}>
          <h3 class="acc-head">
            <button class="paneltoggle" aria-expanded={panelOpen.atmosphere} onclick={() => togglePanel('atmosphere')}>
              <span class="chev">{panelOpen.atmosphere ? '▲' : '▼'}</span> Atmosphere
            </button>
          </h3>
          {#if panelOpen.atmosphere}
          <button class="primary" onclick={toggleSpotify}>🎵 Spotify: {spotifyOn ? 'on' : 'off'}</button>
          {#if spotifyOn}
            <label class="vol">🔈
              <input type="range" min="0" max="1" step="0.01" value={spotifyVol}
                oninput={(e) => setSpotifyVol(e.currentTarget.value)} />
              🔊 <span class="vol-pct">{Math.round(spotifyVol * 100)}%</span>
            </label>
            {#if spConfig?.configured}
              {#if spConnected}
                <p class="sub">▶ Full tracks via your Spotify (Premium). <button class="link" onclick={disconnectSpotify}>disconnect</button></p>
              {:else}
                <button class="primary" onclick={connectSpotify}>Connect Spotify for full tracks</button>
                <p class="sub">Otherwise you'll hear 30s previews.</p>
              {/if}
            {/if}
            {#if atmo && !atmo.spotify_configured}<p class="sub">No Spotify keys set — these are the game's picks (no playback).</p>{/if}
            <ul class="tracks">
              {#each atmo?.tracks || [] as t}
                <li>
                  {#if t.album_art}<img src={t.album_art} alt="" />{/if}
                  <div><b>{t.matched || (t.artist + ' — ' + t.title)}</b><br>
                    <span class="sub">{t.why}</span>
                    {#if t.spotify_url}&nbsp;<a href={t.spotify_url} target="_blank" rel="noopener">open ↗</a>{/if}
                  </div>
                </li>
              {/each}
            </ul>
            {#if atmo?.tracks?.length && !atmo.tracks.some((x) => x.preview_url) && atmo.tracks[0].spotify_id}
              <iframe title="spotify" src={"https://open.spotify.com/embed/track/" + atmo.tracks[0].spotify_id}
                width="100%" height="80" style="border:0;border-radius:8px" allow="autoplay; encrypted-media"></iframe>
            {/if}
          {/if}
          {/if}
        </div>
        <div class="panel" class:collapsed={!panelOpen.leaderboard}>
          <h3 class="acc-head">
            <button class="paneltoggle" aria-expanded={panelOpen.leaderboard} onclick={() => togglePanel('leaderboard')}>
              <span class="chev">{panelOpen.leaderboard ? '▲' : '▼'}</span> Leaderboard
            </button>
            <button class="link paneltitle" title="Open full leaderboard" aria-label="Open full leaderboard" onclick={openLeaderboard}>↗</button>
          </h3>
          {#if panelOpen.leaderboard}
          {#if myWindow.length}
            <ul class="lbside">{#each myWindow as r}<li class:me={r.is_me}><span class="rank">#{r.rank}</span><span class="nm">{#if r.completed}<span class="done" title="Completed the game">★</span> {/if}{r.display_name}</span><span class="score">{r.progress}</span></li>{/each}</ul>
          {:else}
            <p class="sub">no ranking yet</p>
          {/if}
          {/if}
        </div>
        <div class="panel" class:collapsed={!panelOpen.notes}>
          <h3 class="acc-head">
            <button class="paneltoggle" aria-expanded={panelOpen.notes} onclick={() => togglePanel('notes')}>
              <span class="chev">{panelOpen.notes ? '▲' : '▼'}</span> Notes
            </button>
          </h3>
          {#if panelOpen.notes}
          <textarea class="clipboard" bind:value={clipboard} oninput={onClipInput} onblur={saveClip}
            placeholder="Your private clipboard — type or paste anything. Saved with your profile."></textarea>
          {/if}
        </div>
        <div class="panel" class:collapsed={!panelOpen.log}>
          <h3 class="acc-head">
            <button class="paneltoggle" aria-expanded={panelOpen.log} onclick={() => togglePanel('log')}>
              <span class="chev">{panelOpen.log ? '▲' : '▼'}</span> Log <span class="sub">(your progress)</span>
            </button>
          </h3>
          {#if panelOpen.log}
          <ul class="log">
            {#each logRev.slice(0, logShown) as l, i}
              <li><span class="seq">#{l.seq}</span> {l.summary || '…'}
                {#if l.seq > 0 && i > 0}
                  {#if l.kind === 'gate'}<button class="link rollback" title="Cheat death — return to here" aria-label="Cheat death — return to here" onclick={() => onRollback(l.seq)}>↩</button>{/if}
                  {#if isAdmin}<button class="link rollback adminrb" title="Admin: roll back to this point" aria-label="Admin: roll back to this point" onclick={() => onRollback(l.seq)}>↺</button>{/if}
                {/if}
              </li>
            {/each}
          </ul>
          {#if logRev.length > logShown}
            <button class="link more" onclick={() => (logShown += LOG_PAGE)}>more ({logRev.length - logShown} earlier)</button>
          {/if}
          {/if}
        </div>
      </aside>
    </div>
  {/if}

  {#if showMap && gameMap}
    <MapOverlay block={gameMap} onPick={mapNav} onClose={() => (showMap = false)} {busy} />
  {/if}

  {#if showMapEditor}
    <MapEditor onClose={() => (showMapEditor = false)} />
  {/if}

  {#if showLb}
    <div class="modal" onclick={() => (showLb = false)}>
      <div class="modal-card lb" onclick={(e) => e.stopPropagation()}>
        <h2>🏆 Leaderboard <span class="sub">— {lbTotal} players</span></h2>
        {#if lbMe}
          <p>You are <b>#{lbMe.rank}</b> — {lbMe.progress}{#if lbMe.completed} <span class="done" title="You completed the game">★ completed</span>{/if} <button class="link" onclick={jumpToMe}>jump to me</button></p>
        {/if}
        <div class="row">
          <input bind:value={lbQuery} placeholder="search a name…" onkeydown={(e) => e.key === 'Enter' && searchLb()} />
          <button onclick={searchLb}>Search</button>
          {#if lbQuery}<button class="link" onclick={clearSearch}>clear</button>{/if}
        </div>
        <div class="row">
          <input bind:value={lbPos} placeholder="go to position #" inputmode="numeric" onkeydown={(e) => e.key === 'Enter' && goToPos()} />
          <button onclick={goToPos}>Go</button>
        </div>
        <ul class="lblist">
          {#each lbRows as r}
            <li class:me={r.is_me}><span class="rank">#{r.rank}</span><span class="nm">{#if r.completed}<span class="done" title="Completed the game">★</span> {/if}{r.display_name}</span><span class="score">{r.progress}</span></li>
          {/each}
          {#if lbRows.length === 0}<li class="empty sub">no matches</li>{/if}
        </ul>
        {#if !lbQuery}
          <div class="row lbnav">
            <button onclick={lbPrev} disabled={lbOffset === 0}>← Prev</button>
            <span class="sub">{lbTotal ? lbOffset + 1 : 0}–{Math.min(lbOffset + LB_LIMIT, lbTotal)} of {lbTotal}</span>
            <button onclick={lbNext} disabled={lbOffset + LB_LIMIT >= lbTotal}>Next →</button>
          </div>
        {/if}
        <button onclick={() => (showLb = false)}>Close</button>
      </div>
    </div>
  {/if}

  {#if showHelp}
    <div class="modal" onclick={() => (showHelp = false)}>
      <div class="modal-card help" onclick={(e) => e.stopPropagation()}>
        <h2>How to play</h2>
        <pre class="manual">{helpText}</pre>
        <button onclick={() => (showHelp = false)}>Close</button>
      </div>
    </div>
  {/if}

  {#if showAdmin}
    <div class="modal" onclick={() => (showAdmin = false)}>
      <div class="modal-card admin" onclick={(e) => e.stopPropagation()}>
        <h2>⚙ Admin &amp; settings</h2>
        <p class="sub">Running game: <b>{adminGame || '—'}</b> <span class="sub">(games/{adminGame}/data)</span></p>
        {#if adminMsg}<div class="notice">{adminMsg}</div>{/if}

        <div class="admin-sec">
          <h3>Editors</h3>
          <p class="sub">Authoring lives in the YAML files; these editors write changes back to them.</p>
          <button onclick={() => { showAdmin = false; openGraph(); }}>Story graph…</button>
          <button onclick={() => { showAdmin = false; showMapEditor = true; }}>Map editor…</button>
        </div>

        <div class="admin-sec">
          <h3>Authored content</h3>
          <p class="sub">World, NPCs, story, puzzles — authored in the YAML files (the single source of truth). Seeding reconciles the DB to the files on every load.</p>
          <button onclick={exportContent}>Export YAML</button>
        </div>

        <div class="admin-sec">
          <h3>Full database <span class="sub">(sensitive · destructive)</span></h3>
          <p class="sub">Every table incl. accounts. Import <b>replaces everything</b>.</p>
          <button onclick={exportDb}>Export JSON</button>
          <div class="row">
            <input bind:value={dbConfirm} placeholder="type REPLACE to import" />
            <label class="filebtn" class:disabled={dbConfirm !== 'REPLACE'}>Import<input type="file" accept=".json" disabled={dbConfirm !== 'REPLACE'} onchange={importDbFile} /></label>
          </div>
        </div>

        <div class="admin-sec">
          <h3>Player save <span class="sub">(destructive)</span></h3>
          <p class="sub">One player's progress. Import overwrites that player's state.</p>
          <div class="row">
            <select bind:value={selPlayer}>
              <option value="">— pick a player —</option>
              {#each adminPlayers as p}<option value={p.id}>{p.display_name}</option>{/each}
            </select>
            <button onclick={exportPlayer} disabled={!selPlayer}>Export</button>
          </div>
          <div class="row">
            <input bind:value={savConfirm} placeholder="type REPLACE to import" />
            <label class="filebtn" class:disabled={savConfirm !== 'REPLACE'}>Import<input type="file" accept=".json" disabled={savConfirm !== 'REPLACE'} onchange={importPlayerFile} /></label>
          </div>
        </div>

        <button onclick={() => (showAdmin = false)}>Close</button>
      </div>
    </div>
  {/if}

  {#if showEditor}
    <div class="modal" onclick={() => (showEditor = false)}>
      <div class="modal-card editor" onclick={(e) => e.stopPropagation()}>
        <div class="editor-head">
          <h2>🕸 Story graph <span class="sub">— read-only; authoring lives in the YAML files</span></h2>
        </div>
        {#if graphMsg}<div class="notice">{graphMsg}</div>{/if}

        <div class="graphwrap">
          <div class="canvas">
            <SvelteFlow bind:nodes={flowNodes} bind:edges={flowEdges} {nodeTypes} {edgeTypes} fitView
              minZoom={0.25} fitViewOptions={{ padding: 0.2, minZoom: 0.02, maxZoom: 1.5 }}
              nodesConnectable={false} elementsSelectable={false}
              onnodedragstop={onNodeDragStop}
              onnodepointerenter={({ node, event }) => showHover('node', node.data.rec, event)}
              onnodepointerleave={() => (hover = null)}
              onedgepointerenter={({ edge, event }) => showHover('edge', edge.data.rec, event)}
              onedgepointerleave={() => (hover = null)}>
              <Background />
              <Controls fitViewOptions={{ padding: 0.2, minZoom: 0.02, maxZoom: 1.5 }} />
            </SvelteFlow>
            <div class="graphtools">
              <button onclick={spreadOut} disabled={busy} title="Spread the nodes out: even spacing, similar edge lengths, no overlap">⤢ Spread out</button>
              <span class="sub">drag a node to reposition it (saved to YAML) · edit content in the YAML files</span>
            </div>
          </div>
        </div>

        <button class="editor-close" onclick={() => (showEditor = false)}>Close</button>
      </div>
    </div>
    {#if hover}
      <div class="gtip" style={`left:${hover.x + 14}px; top:${hover.y + 12}px`}>
        {#if hover.kind === 'node'}
          <b>{hover.rec.id}</b> <span class="sub">{hover.rec.type}</span>
          {#if hover.rec.title}<div>{hover.rec.title}</div>{/if}
          {#if hover.rec.location}<div class="sub">location: {hover.rec.location}</div>{/if}
          {#if hover.rec.gate}<div class="sub">gate: {hover.rec.gate}</div>{/if}
          {#if hover.rec.puzzle}<div class="sub">puzzle: {hover.rec.puzzle}</div>{/if}
          {#if hover.rec.entry}<div class="sub">· entry</div>{/if}
          {#if hover.rec.is_death}<div class="sub">· death</div>{/if}
          {#if hover.rec.world_access}<div class="sub">· world access</div>{/if}
        {:else}
          <b>{hover.rec.id}</b>
          <div>{hover.rec.from} → {hover.rec.to}</div>
          {#if hover.rec.label}<div class="sub">label: {hover.rec.label}</div>{/if}
          {#if hover.rec.danger}<div class="sub">danger: {hover.rec.danger}</div>{/if}
          <div class="sub">conditions: {JSON.stringify(hover.rec.conditions)}</div>
          {#if hover.rec.effects && Object.keys(hover.rec.effects).length}<div class="sub">effects: {JSON.stringify(hover.rec.effects)}</div>{/if}
        {/if}
      </div>
    {/if}
  {/if}

</main>

<style>
  :global(body) { background:#11131a; color:#d8d8e0; font-family: Georgia, serif; margin:0; }
  main { max-width: 1340px; margin: 0 auto; padding: 1.5rem; }
  h1 { font-weight: normal; } .sub { color:#6b6b80; font-size:.7em; }
  .layout { display:grid; grid-template-columns: 1fr 300px; gap:1.5rem; }
  .topbar { text-align:right; margin-bottom:.5rem; }
  .topbar .link { font-size:1.6rem; padding-left:.8rem; vertical-align:middle; line-height:1; }
  .body { font-size:1.15rem; line-height:1.6; }
  .media { color:#5a5a72; font-size:.85rem; }
  .gate-passed { color:#9ad29a; font-size:.95rem; margin:.25rem 0 1rem; }
  .puzzle-prompt { font-size:1.1rem; line-height:1.6; color:#e8e8f0; background:#15171f; border:1px solid #2a2e3e; border-radius:8px; padding:.7rem .9rem; margin:1rem 0 .5rem; }
  .puzzle-hint { color:#d8c89a; font-size:.95rem; margin:.25rem 0 .5rem; }
  .bannerrow { display:flex; gap:.6rem; align-items:stretch; margin-bottom:1rem; }
  .banner { position:relative; flex:1 1 auto; min-width:0; border-radius:8px; overflow:hidden; border:1px solid #2a2e3e; }
  .banner :global(svg), .banner img { display:block; width:100%; height:140px; object-fit:cover; }
  /* The map-access button: the word MAP is baked into the aged parchment image,
     sized to match the location banner's height. */
  .mapthumb { flex:0 0 auto; width:186px; height:140px; padding:0; cursor:pointer;
    border:none; border-radius:8px; overflow:hidden; background:transparent; }
  /* Scale the parchment up so it fills the button: the source PNG has transparent
     margins around the art, which overflow:hidden then crops away. */
  .mapthumb img { display:block; width:100%; height:100%; object-fit:cover; transform:scale(1.2);
    transition:filter .15s, transform .15s; }
  .mapthumb:hover img { filter:brightness(1.06); transform:scale(1.24); }
  .mapthumb:disabled { opacity:.5; cursor:default; }
  .mapthumb:disabled:hover img { filter:none; transform:scale(1.2); }
  .banner .setting { position:absolute; bottom:.4rem; right:.6rem; font-size:.75rem; color:#cdbb9a; background:rgba(0,0,0,.45); padding:.1rem .45rem; border-radius:4px; }
  .vol { display:flex; align-items:center; gap:.5rem; margin:.6rem 0 0; font-size:.9rem; color:#9a9ab0; }
  .vol input[type=range] { flex:1; accent-color:#7a7ad0; cursor:pointer; }
  .vol-pct { min-width:2.6em; text-align:right; color:#6b6b80; font-size:.8rem; }
  .tracks { list-style:none; padding:0; font-size:.8rem; margin:.6rem 0 0; }
  .tracks li { display:flex; gap:.5rem; margin:.55rem 0; align-items:center; }
  .tracks img { width:42px; height:42px; border-radius:4px; flex-shrink:0; }
  .panel, .notes { background:#1a1d28; border:1px solid #2a2e3e; border-radius:8px; padding:1rem; margin-bottom:1rem; }
  .acc-head { display:flex; align-items:center; gap:.4rem; margin:0; }
  .panel:not(.collapsed) .acc-head { margin-bottom:.7rem; }
  .paneltoggle { flex:1; display:flex; align-items:center; gap:.5rem; min-width:0; background:none; border:none; color:inherit; font:inherit; padding:0; cursor:pointer; text-align:left; }
  .paneltoggle:hover { color:#7fa8d8; }
  .paneltoggle .chev { font-size:.7rem; color:#7a7a90; width:.9em; flex-shrink:0; }
  .narrow { max-width:420px; }
  .tabs { display:flex; gap:.5rem; margin-bottom:1rem; }
  .tabs button { flex:1; background:#15171f; }
  .tabs button.active { background:#34416a; }
  .switch { margin-top:.7rem; font-size:.85rem; color:#9aa; }
  .gate-reply { font-size:1.1rem; line-height:1.6; font-style:italic; color:#cdd0e6; border-left:3px solid #3a3f57; padding-left:.9rem; margin:1rem 0; }
  /* The running log, kept below the current scene. */
  .transcript { margin-top:1.5rem; border-top:1px solid #2a2e3e; padding-top:1rem; }
  .flow-head { margin:0 0 .6rem; font-size:.95rem; font-weight:normal; color:#6f7290; }
  .beat { font-size:1.05rem; line-height:1.55; margin:.45rem 0; color:#cfd0dc; }
  .beat .ic { color:#6f7290; margin-right:.15rem; }
  .beat .rollback { opacity:0; transition:opacity .12s; }
  .beat:hover .rollback { opacity:1; }
  .beat-clue { color:#cdbb9a; }
  .beat-travel { color:#8fb8c8; }
  .beat-death { color:#d98a8a; font-weight:bold; }
  .beat-puzzle { color:#cdd0e6; }
  .beat.dialogue { font-style:italic; border-left:2px solid #2a2e3e; padding-left:.7rem; }
  .beat.dialogue .who { font-style:normal; }
  .beat.dialogue.me { color:#9fd3ff; } .beat.dialogue.npc { color:#cdbb9a; }
  /* Side "Log" panel (compact progress beats). */
  .log { list-style:none; padding:0; font-size:.85rem; } .log .seq { color:#5a5a72; }
  .log li { margin:.3rem 0; }
  .earlier { display:block; margin:.8rem 0 0; color:#6f7290; }
  .row { display:flex; gap:.5rem; margin:.5rem 0; }
  input { display:block; width:100%; box-sizing:border-box; background:#0d0e14; border:1px solid #2a2e3e; color:#e8e8f0; padding:.5rem; border-radius:6px; margin:.4rem 0; }
  .row input { margin:0; flex:1; }
  .qr { background:#fff; padding:.5rem; border-radius:6px; width:max-content; }
  .qr :global(svg) { display:block; width:180px; height:180px; }
  .manual { white-space:pre-wrap; background:#15171f; border:1px solid #2a2e3e; border-radius:8px; padding:1rem; font-family:Georgia,serif; line-height:1.5; }
  .quiz-q { margin:.8rem 0; } .quiz-q .q { font-weight:bold; margin-bottom:.3rem; }
  .opt { display:block; cursor:pointer; padding:.15rem 0; }
  .opt.toggle { font-size:.9rem; color:#9aa; margin:.3rem 0 .6rem; }
  .opt input { display:inline; width:auto; margin-right:.5rem; }
  .codes { list-style:none; padding:0; display:grid; grid-template-columns:1fr 1fr; gap:.4rem; }
  .codes code { background:#0d0e14; padding:.35rem .5rem; border-radius:6px; display:block; text-align:center; letter-spacing:1px; }
  .maphint { margin:.4rem 0 0; text-align:center; }
  .modal-card.worldmap { width:min(960px,94vw); max-height:92vh; overflow:auto; }
  .edges { display:flex; flex-direction:column; gap:.5rem; margin-top:1rem; }
  .notes { margin-top:1.5rem; }
  button { background:#2a3550; color:#e8e8f0; border:1px solid #3a456a; padding:.55rem .8rem; border-radius:6px; cursor:pointer; text-align:left; }
  button:hover { background:#34416a; }
  button.primary { background:#34416a; text-align:center; width:100%; }
  button.danger { background:#4a2330; border-color:#6a3346; }
  button.link { background:none; border:none; color:#7fa8d8; padding:0 0 0 .4rem; width:auto; cursor:pointer; font-size:.8rem; }
  .dead { color:#c98; }
  .more { display:inline-block; margin-top:.5rem; }
  .rollback { font-size:1rem; line-height:1; }
  .rollback.adminrb { color:#7da7d0; margin-left:.15rem; }   /* admin: roll back to ANY beat (distinct from the player ↩) */
  .notelist { list-style:disc; padding-left:1.1rem; margin:.3rem 0 0; font-size:.82rem; color:#cdbb9a; }
  .clipboard { width:100%; min-height:9rem; margin-top:.4rem; box-sizing:border-box; resize:vertical;
    background:#11131b; color:#e8e6df; border:1px solid #2a2e3e; border-radius:6px; padding:.55rem .65rem;
    font:inherit; font-size:.85rem; line-height:1.45; }
  .clipboard:focus { outline:none; border-color:#3a456a; }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid rgba(255,255,255,.3); border-top-color:#e8e8f0; border-radius:50%; animation:spin .6s linear infinite; vertical-align:middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { background:#3a2330; border:1px solid #6a3346; padding:.5rem .8rem; border-radius:6px; margin-bottom:1rem; }
  .notice { background:#23323a; border:1px solid #356a5a; padding:.5rem .8rem; border-radius:6px; margin-bottom:1rem; }
  .modal { position:fixed; inset:0; background:rgba(0,0,0,.7); display:flex; align-items:center; justify-content:center; }
  .modal-card { background:#1a1d28; border:1px solid #3a456a; border-radius:10px; padding:1.5rem 2rem; min-width:300px; }
  .modal-card.admin { width:480px; max-width:90vw; max-height:85vh; overflow:auto; }
  .modal-card.help { width:560px; max-width:92vw; max-height:85vh; overflow:auto; }
  .modal-card.lb { width:420px; max-width:92vw; max-height:85vh; overflow:auto; }
  .modal-card.editor { width:100vw; height:100vh; max-width:100vw; max-height:100vh;
    border-radius:0; overflow:hidden; display:flex; flex-direction:column; padding:1rem 1.5rem; }
  .editor-head { display:flex; align-items:center; gap:1rem; }
  .editor-head h2 { flex:1; margin:.2rem 0; }
  /* graph view */
  .graphwrap { display:grid; grid-template-columns:minmax(0,1fr); gap:1rem; flex:1 1 auto; min-height:0; }
  .canvas { position:relative; height:100%; border:1px solid #2a2e3e; border-radius:8px; overflow:hidden; background:#0d0e14; }
  .canvas :global(.svelte-flow) { background:#0d0e14; }
  /* Zoom / fit / interactivity controls: dark buttons with bright icons (the
     default near-white-on-white made the icons almost invisible on this theme). */
  .canvas :global(.svelte-flow__controls) { box-shadow:0 0 0 1px #2a2e3e; border-radius:6px; overflow:hidden; }
  .canvas :global(.svelte-flow__controls-button) { background:#1a1d28; border-bottom:1px solid #2a2e3e; width:26px; height:26px; }
  .canvas :global(.svelte-flow__controls-button:hover) { background:#2e3450; }
  .canvas :global(.svelte-flow__controls-button svg) { fill:#e8e8f0; max-width:15px; max-height:15px; }
  .canvas :global(.svelte-flow__controls-button:hover svg) { fill:#fff; }
  /* Edge labels are HTML pills whose colour is var-driven; the dark theme default
     renders them light-on-dark. They're portaled out of .canvas, so target them
     unscoped and force black text on a light pill. */
  :global(.svelte-flow__edge-label) {
    color:#000 !important; background:#e6e8f0 !important; font-weight:600;
    padding:2px 6px; border-radius:4px;
    max-width:150px; white-space:normal; overflow-wrap:break-word;
    text-align:center; line-height:1.2; font-size:11px; cursor:pointer;
  }
  .graphtools { position:absolute; left:.5rem; top:.5rem; z-index:5; display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; }
  .graphtools button { padding:.3rem .6rem; font-size:.82rem; }
  .editor-close { align-self:flex-end; margin-top:.5rem; padding:.7rem .8rem; font-size:.8rem; line-height:1.4; text-align:center; }
  .gtip { position:fixed; z-index:1000; pointer-events:none; max-width:320px; background:#1a1d28; border:1px solid #3a456a;
          border-radius:6px; padding:.4rem .6rem; font-size:.8rem; box-shadow:0 2px 10px rgba(0,0,0,.5); word-break:break-word; }
  .paneltitle { background:none; border:none; color:#e8e8f0; font:inherit; padding:0; cursor:pointer; }
  .paneltitle:hover { color:#7fa8d8; }
  .lblist, .lbside { list-style:none; padding:0; }
  .lblist li, .lbside li { display:grid; grid-template-columns:2.6rem 1fr auto; gap:.5rem; align-items:baseline; padding:.2rem .45rem; border-radius:4px; }
  .lblist li:nth-child(even), .lbside li:nth-child(even) { background:#15171f; }
  .lblist .rank, .lbside .rank { color:#5a5a72; text-align:right; }
  .lblist .nm, .lbside .nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .lblist .score, .lbside .score { text-align:right; font-weight:bold; font-variant-numeric:tabular-nums; }
  .lblist li.me, .lbside li.me { color:#cdbb9a; background:#2a3550; }
  .done { color:#e8c060; font-weight:bold; }
  .lblist li.empty, .lbside li.empty { display:block; background:none; }
  .lbnav { align-items:center; justify-content:space-between; }
  .admin-sec { border-top:1px solid #2a2e3e; padding:.8rem 0; }
  .admin-sec h3 { margin:.2rem 0; }
  .admin-sec button, .admin-sec .filebtn, .admin-sec select { margin:.2rem .4rem .2rem 0; }
  .filebtn { display:inline-block; background:#2a3550; border:1px solid #3a456a; padding:.5rem .8rem; border-radius:6px; cursor:pointer; }
  .filebtn input { display:none; }
  .filebtn.disabled { opacity:.4; pointer-events:none; }
  .worldgrid { display:grid; gap:.6rem; margin:1rem 0; }
  .cell { text-align:center; min-width:110px; min-height:64px; border-radius:8px; }
  .cell.city { background:#2c2f4a; } .cell.village { background:#2a3a2f; }
  .cell.wilderness { background:#2a2620; } .cell.town { background:#2a3550; }
  .cell.current { outline:2px solid #cdbb9a; }
  .cell .here { color:#cdbb9a; font-size:.7rem; }
  .cell:disabled { cursor:default; opacity:.85; }
  code { color:#cdbb9a; }
</style>
