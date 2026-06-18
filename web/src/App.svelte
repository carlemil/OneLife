<script>
  import { onMount, tick } from 'svelte';
  import { api, download } from './lib/api.js';
  import * as spotify from './lib/spotify.js';
  import { SvelteFlow, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import StoryNode from './lib/StoryNode.svelte';

  let phase = $state('loading');        // loading | auth | twofa | onboarding | game
  let authMode = $state('login');       // login | register
  let busy = $state(false);
  let error = $state('');
  let notice = $state('');

  // auth form
  let email = $state('');
  let password = $state('');
  let displayName = $state('');
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
  let myWindow = $state([]);
  let gateInput = $state('');
  let puzzleInput = $state('');
  let gateEl = $state(null);
  let puzzleEl = $state(null);

  // Newest-on-top with "More" pagination, for the dialog and the log.
  const GATE_PAGE = 12;
  const LOG_PAGE = 10;
  let gateShown = $state(GATE_PAGE);
  let logShown = $state(LOG_PAGE);
  let gateMsgs = $derived(game?.gate?.messages ? [...game.gate.messages].reverse() : []);
  let logRev = $derived(logEntries ? [...logEntries].reverse() : []);
  // Show the NPC's name once it has been revealed/guessed in the conversation.
  let npcLabel = $derived.by(() => {
    const g = game?.gate;
    const rn = g?.reveal_name;
    if (rn && (g.messages || []).some((m) => (m.content || '').toLowerCase().includes(rn.toLowerCase()))) return rn;
    return 'NPC';
  });

  // world map
  let world = $state(null);
  let showMap = $state(false);

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
  let showAdmin = $state(false);
  let adminMsg = $state('');
  let adminPlayers = $state([]);
  let selPlayer = $state('');
  let dbConfirm = $state('');
  let savConfirm = $state('');

  // admin content editor (structured CRUD)
  let showEditor = $state(false);
  let content = $state(null);       // full authored set, loaded once
  let editKind = $state('nodes');
  let selId = $state(null);         // null = creating a new entity
  let form = $state({});            // working copy; json fields held as strings
  let jsonErrors = $state({});      // field key -> parse error message
  let editorMsg = $state('');
  let canSave = $derived(Object.keys(jsonErrors).length === 0 && !!String(form?.id ?? '').trim());
  // graph view + canonical event log
  let editorView = $state('list');  // 'list' | 'graph'
  let flowNodes = $state.raw([]);
  let flowEdges = $state.raw([]);
  let hover = $state(null);         // {kind:'node'|'edge', rec, x, y}
  let logRows = $state([]);
  let logHead = $state(0);
  let canRedo = $derived(logRows.some((e) => e.seq > logHead));
  const nodeTypes = { story: StoryNode };

  // atmosphere (image + Spotify soundtrack)
  let atmo = $state(null);
  let spotifyOn = $state(false);
  let spConfig = $state(null);    // {client_id, configured}
  let spConnected = $state(false); // Web Playback SDK connected (full tracks)
  let _players = [];
  let _active = 0;
  let _curUrl = '';
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
      next.volume = 0.7 * k; cur.volume = fromVol * (1 - k);
      if (k < 1) requestAnimationFrame(step); else cur.pause();
    }
    requestAnimationFrame(step);
  }
  function stopAudio() { _players.forEach((p) => { try { p.pause(); } catch {} }); _curUrl = ''; }

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
        crossfade(prev.preview_url);   // 30s-preview fallback
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
    if (phase === 'game' && id && id !== _lastNode) { _lastNode = id; gateShown = GATE_PAGE; loadAtmosphere(); }
  });

  onMount(async () => {
    spotifyOn = localStorage.getItem('onelife_spotify') === '1';
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
      const r = await api.register(email.trim(), password, displayName.trim());
      qrSvg = r.qr_svg; secret = r.secret;
      phase = 'twofa';
      notice = 'Scan the QR with an authenticator app, then enter a code to finish setup.';
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
    api.logout(); game = null; phase = 'auth'; authMode = 'login';
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
    try { isAdmin = (await api.adminMe()).is_admin; } catch { isAdmin = false; }
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
  async function importContentFile(ev) {
    const f = ev.target.files?.[0]; if (!f) return;
    try { const r = await api.importContent(await f.text()); adminMsg = `Content imported (${r.counts.nodes} nodes).`; }
    catch (e) { adminMsg = e.message; } finally { ev.target.value = ''; }
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

  // ---------- admin content editor ----------
  const KINDS = ['nodes', 'edges', 'gates', 'puzzles', 'clues', 'characters', 'locations', 'cells', 'arcs'];
  // t: text | longtext | number | bool | select | json. ref -> select from content[ref].
  const FIELDS = {
    arcs: [{ key: 'id', t: 'text' }, { key: 'title', t: 'text' }, { key: 'is_spine', t: 'bool' }],
    cells: [{ key: 'id', t: 'text' }, { key: 'grid_x', t: 'number' }, { key: 'grid_y', t: 'number' },
            { key: 'name', t: 'text' }, { key: 'kind', t: 'select', options: ['city', 'town', 'village', 'wilderness'] },
            { key: 'region', t: 'text' }, { key: 'arrival_node', t: 'select', ref: 'nodes' }],
    characters: [{ key: 'id', t: 'text' }, { key: 'name', t: 'text' }, { key: 'persona', t: 'longtext' }, { key: 'reveal_name', t: 'text' }],
    locations: [{ key: 'id', t: 'text' }, { key: 'name', t: 'text' }, { key: 'description', t: 'longtext' }, { key: 'cell', t: 'select', ref: 'cells' }],
    puzzles: [{ key: 'id', t: 'text' }, { key: 'type', t: 'select', options: ['combination', 'riddle', 'assembly', 'semantic'] },
              { key: 'prompt', t: 'longtext' }, { key: 'solution', t: 'json' }, { key: 'required_clues', t: 'json' },
              { key: 'hint_ladder', t: 'json' }, { key: 'on_solve', t: 'json' }],
    clues: [{ key: 'id', t: 'text' }, { key: 'puzzle', t: 'select', ref: 'puzzles' }, { key: 'placement', t: 'json' },
            { key: 'reveal_text', t: 'longtext' }, { key: 'discover_conditions', t: 'json' }],
    nodes: [{ key: 'id', t: 'text' }, { key: 'arc', t: 'select', ref: 'arcs' },
            { key: 'type', t: 'select', options: ['narration', 'choice', 'gate', 'puzzle', 'location', 'death', 'ending'] },
            { key: 'location', t: 'select', ref: 'locations' }, { key: 'title', t: 'text' }, { key: 'body', t: 'longtext' },
            { key: 'entry', t: 'bool' }, { key: 'is_death', t: 'bool' }, { key: 'world_access', t: 'bool' },
            { key: 'gate', t: 'select', ref: 'gates' }, { key: 'puzzle', t: 'select', ref: 'puzzles' }, { key: 'media', t: 'json' }],
    gates: [{ key: 'id', t: 'text' }, { key: 'location', t: 'select', ref: 'locations' },
            { key: 'character', t: 'select', ref: 'characters' }, { key: 'spec', t: 'json' }],
    edges: [{ key: 'id', t: 'text' }, { key: 'from', t: 'select', ref: 'nodes' }, { key: 'to', t: 'select', ref: 'nodes' },
            { key: 'label', t: 'text' }, { key: 'conditions', t: 'json' }, { key: 'effects', t: 'json' },
            { key: 'danger', t: 'number' }, { key: 'sort_order', t: 'number' }],
  };
  // Blank starting entities. Gates are kept FLAT (spec fields at top level) — the
  // backend collapses non-id/location/character keys into `spec`, and loadForm()
  // gathers them back into the single `spec` JSON editor.
  const DEFAULTS = {
    arcs: { id: '', title: '', is_spine: false },
    cells: { id: '', grid_x: 0, grid_y: 0, name: '', kind: 'town', region: '', arrival_node: '' },
    characters: { id: '', name: '', persona: '', reveal_name: '' },
    locations: { id: '', name: '', description: '', cell: '' },
    puzzles: { id: '', type: 'riddle', prompt: '', solution: {}, required_clues: [], hint_ladder: [], on_solve: {} },
    clues: { id: '', puzzle: '', placement: {}, reveal_text: '', discover_conditions: { all: [] } },
    nodes: { id: '', arc: 'main', type: 'narration', location: '', title: '', body: '', entry: false, is_death: false, world_access: false, gate: '', puzzle: '', media: {} },
    gates: { id: '', location: '', character: '', intent: '', criteria: [], success_rule: '', knowledge_boundary: { knows: [], refuses: [], tone: '' }, hint_ladder: [], mercy_after_attempts: 3, on_success: {} },
    edges: { id: '', from: '', to: '', label: '', conditions: { all: [] }, effects: {}, danger: 0, sort_order: 0 },
  };
  const singular = (k) => k.replace(/s$/, '');
  const optionsFor = (f) => f.options ?? (content?.[f.ref] ?? []).map((x) => x.id);

  function loadForm(ent) {
    const k = editKind, f = {};
    if (k === 'gates') {
      const spec = {};
      for (const [key, val] of Object.entries(ent || {}))
        if (!['id', 'location', 'character'].includes(key)) spec[key] = val;
      f.id = ent?.id ?? ''; f.location = ent?.location ?? ''; f.character = ent?.character ?? '';
      f.spec = JSON.stringify(spec, null, 2);
    } else {
      for (const fld of FIELDS[k]) {
        let v = ent ? ent[fld.key] : undefined;
        if (fld.t === 'json') f[fld.key] = JSON.stringify(v ?? DEFAULTS[k][fld.key] ?? {}, null, 2);
        else if (fld.t === 'bool') f[fld.key] = !!v;
        else if (fld.t === 'number') f[fld.key] = v ?? 0;
        else f[fld.key] = v ?? '';
      }
    }
    form = f; jsonErrors = {};
  }
  function buildEntity() {
    const k = editKind, e = {};
    for (const fld of FIELDS[k]) {
      if (fld.t === 'json') e[fld.key] = JSON.parse(form[fld.key]);
      else if (fld.t === 'number') e[fld.key] = Number(form[fld.key]) || 0;
      else if (fld.t === 'bool') e[fld.key] = !!form[fld.key];
      else {
        const v = (form[fld.key] ?? '').trim?.() ?? form[fld.key];
        if (fld.ref && v === '') continue;   // drop empty optional reference
        e[fld.key] = v;
      }
    }
    if (k === 'gates') { const spec = e.spec || {}; delete e.spec; Object.assign(e, spec); }
    return e;
  }
  function setJson(key, val) {
    form[key] = val;
    try { JSON.parse(val); delete jsonErrors[key]; } catch (err) { jsonErrors[key] = err.message; }
    jsonErrors = { ...jsonErrors };
  }
  function newEntity() {
    selId = null; editorMsg = '';
    loadForm(structuredClone(DEFAULTS[editKind]));
  }
  function selectKind(k) { editKind = k; newEntity(); }
  function pickEntity(id) {
    selId = id; editorMsg = '';
    loadForm(content[editKind].find((x) => x.id === id));
  }
  async function openEditor() {
    editorMsg = ''; editorView = 'list'; showEditor = true;
    try { content = await api.contentAll(); editKind = 'nodes'; newEntity(); await loadLog(); }
    catch (e) { editorMsg = e.message; }
  }
  async function saveEntity() {
    if (!canSave) return;
    let entity;
    try { entity = buildEntity(); } catch (e) { editorMsg = 'JSON error: ' + e.message; return; }
    busy = true; editorMsg = '';
    try {
      const r = await api.saveEntity(editKind, entity);
      await afterMutation(r.head);
      selId = entity.id;
      editorMsg = 'Saved.' + (r.warnings?.length ? ` ${r.warnings.length} warning(s).` : '');
    } catch (e) { editorMsg = e.message; } finally { busy = false; }
  }
  // No confirmation dialog: delete applies immediately; integrity is enforced
  // server-side (validate + node→edge cascade) and undo is the recovery net.
  async function deleteCurrent() {
    if (selId === null) return;
    busy = true; editorMsg = '';
    try {
      const r = await api.deleteEntity(editKind, selId);
      await afterMutation(r.head);
      newEntity();
      editorMsg = 'Deleted (undo to restore).';
    } catch (e) { editorMsg = e.message; } finally { busy = false; }
  }

  // ---------- event log: undo / redo / history ----------
  async function loadLog() {
    try { const r = await api.contentLog(); logRows = r.events; logHead = r.head; }
    catch (e) { editorMsg = e.message; }
  }
  async function afterMutation(head) {
    content = await api.contentAll();
    if (head != null) logHead = head;
    await loadLog();
    if (editorView === 'graph') buildFlow();
  }
  async function doUndo() {
    busy = true; editorMsg = '';
    try { const r = await api.contentUndo(); await afterMutation(r.head); afterHistoryJump(); }
    catch (e) { editorMsg = e.message; } finally { busy = false; }
  }
  async function doRedo() {
    busy = true; editorMsg = '';
    try { const r = await api.contentRedo(); await afterMutation(r.head); afterHistoryJump(); }
    catch (e) { editorMsg = e.message; } finally { busy = false; }
  }
  async function gotoSeq(seq) {
    busy = true; editorMsg = '';
    try { const r = await api.contentUndoTo(seq); await afterMutation(r.head); afterHistoryJump(); }
    catch (e) { editorMsg = e.message; } finally { busy = false; }
  }
  // After moving HEAD, the selected entity may no longer exist — refresh the form.
  function afterHistoryJump() {
    if (selId !== null && !(content?.[editKind] ?? []).some((x) => x.id === selId)) newEntity();
    else if (selId !== null) loadForm(content[editKind].find((x) => x.id === selId));
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
    flowEdges = content.edges.map((e) => ({
      id: e.id, source: e.from, target: e.to, label: e.label || '',
      markerEnd: { type: 'arrowclosed' },
      style: e.danger ? 'stroke:#c0563a;stroke-width:2' : '',
      data: { rec: e },
    }));
  }
  async function setGraphView() {
    editorView = 'graph';
    if (!content) { try { content = await api.contentAll(); } catch (e) { editorMsg = e.message; return; } }
    buildFlow();
  }
  function onNodeClick({ node }) { editKind = 'nodes'; pickEntity(node.id); }
  function onEdgeClick({ edge }) { editKind = 'edges'; pickEntity(edge.id); }
  async function onNodeDragStop({ targetNode }) {
    if (!targetNode) return;
    try { const r = await api.moveNode(targetNode.id, targetNode.position.x, targetNode.position.y); logHead = r.head; await loadLog(); }
    catch (e) { editorMsg = e.message; }
  }
  async function onConnect(conn) {
    if (!conn.source || !conn.target) return;
    let id = `e-${conn.source}-${conn.target}`;
    const existing = new Set((content?.edges ?? []).map((e) => e.id));
    if (existing.has(id)) { let i = 2; while (existing.has(`${id}-${i}`)) i++; id = `${id}-${i}`; }
    const entity = { id, from: conn.source, to: conn.target, label: '', conditions: { all: [] }, effects: {}, danger: 0, sort_order: 0 };
    busy = true; editorMsg = '';
    try {
      const r = await api.saveEntity('edges', entity);
      await afterMutation(r.head);
      editKind = 'edges'; pickEntity(id);
      editorMsg = 'Edge created — set its label/conditions.';
    } catch (e) { editorMsg = e.message; } finally { busy = false; }
  }
  async function addNode() {
    const ids = new Set((content?.nodes ?? []).map((n) => n.id));
    let n = 1; while (ids.has(`node-${n}`)) n++;
    const id = `node-${n}`;
    const entity = { ...structuredClone(DEFAULTS.nodes), id };
    busy = true; editorMsg = '';
    try {
      const r = await api.saveEntity('nodes', entity);
      await api.moveNode(id, 60, 60).catch(() => {});
      await afterMutation(r.head);
      editKind = 'nodes'; pickEntity(id);
      editorMsg = 'Node created.';
    } catch (e) { editorMsg = e.message; } finally { busy = false; }
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

  async function openMap() {
    try { world = await api.world(); showMap = true; }
    catch (e) { error = e.message; }
  }
  async function onTravel(cellId) {
    busy = true;
    try {
      game = await api.travel(cellId);
      logEntries = (await api.log()).entries;
      showMap = false;
    } catch (e) { error = e.message; } finally { busy = false; }
  }

  async function onEdge(id) {
    busy = true;
    try { game = await api.takeEdge(id); logEntries = (await api.log()).entries; }
    catch (e) { error = e.message; } finally { busy = false; }
  }
  async function onGate() {
    if (!gateInput.trim()) return; busy = true;
    try {
      const r = await api.gate(gateInput.trim());
      gateInput = ''; game = r.state; logEntries = (await api.log()).entries;
    }
    catch (e) { error = e.message; }
    finally { busy = false; await tick(); gateEl?.focus(); }   // refocus after re-enable
  }
  async function onPuzzle() {
    if (!puzzleInput.trim()) return; busy = true;
    try {
      const r = await api.puzzle(puzzleInput.trim());
      game = r.state; logEntries = (await api.log()).entries;
      error = (!r.result.solved && r.result.hint) ? `Hint: ${r.result.hint}` : '';
      puzzleInput = '';
    } catch (e) { error = e.message; }
    finally { busy = false; await tick(); puzzleEl?.focus(); }
  }
  async function onRollback(seq) {
    if (!confirm(`Roll the log back to step ${seq}? You will lose all progress after it.`)) return;
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
        <button class="primary" onclick={doRegister} disabled={busy}>Create account</button>
        <p class="switch">Already have an account? <button class="link" onclick={() => { authMode='login'; error=''; }}>Log in</button></p>
      {:else}
        <input bind:value={code} placeholder="Authenticator code (or a recovery code)" onkeydown={(e) => e.key === 'Enter' && doLogin()} />
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
      {#if isAdmin}<button class="link" title="Admin" onclick={openAdmin}>⚙</button>{/if}
      <button class="link" onclick={confirmLogout}>log out</button>
    </div>
    <div class="layout">
      <section class="story">
        {#if atmo?.image_url}
          <div class="banner"><img src={atmo.image_url} alt="" /><span class="setting">{atmo.setting}</span></div>
        {:else if atmo?.image_svg}
          <div class="banner">{@html atmo.image_svg}<span class="setting">{atmo.setting}</span></div>
        {/if}
        <h2>{game.node.title}</h2>
        <p class="body">{game.node.body}</p>
        <p class="media">🎨 {game.node.media.image_theme} &nbsp; 🎵 {game.node.media.music_theme}</p>

        {#if game.node.type === 'gate' && game.gate && !game.gate.satisfied}
          <form class="row" onsubmit={(e) => { e.preventDefault(); onGate(); }}>
            <input bind:this={gateEl} bind:value={gateInput} placeholder="Say something..." disabled={busy} />
            <button type="submit" disabled={busy}>{#if busy}<span class="spinner"></span>{:else}Say{/if}</button>
          </form>
        {/if}

        {#if game.node.type === 'puzzle' && game.puzzle && !game.puzzle.solved}
          <form class="row" onsubmit={(e) => { e.preventDefault(); onPuzzle(); }}>
            <input bind:this={puzzleEl} bind:value={puzzleInput} placeholder="Enter the code..." disabled={busy} />
            <button type="submit" disabled={busy}>{#if busy}<span class="spinner"></span>{:else}Try{/if}</button>
          </form>
        {/if}

        <div class="edges">
          {#each game.edges as e}
            <button class:danger={e.danger > 0} onclick={() => onEdge(e.id)} disabled={busy}>
              {e.label}{#if e.danger > 0} ⚠{/if}
            </button>
          {/each}
          {#if game.edges.length === 0 && game.node.is_death}
            <p class="dead">You are dead. Roll the log back from the panel on the right to escape — at a cost.</p>
          {/if}
          {#if game.node.world_access}
            <button class="primary" onclick={openMap} disabled={busy}>🗺 Open the world map</button>
          {/if}
        </div>

        {#if game.node.type === 'gate' && game.gate}
          <div class="chat">
            {#each gateMsgs.slice(0, gateShown) as m}
              <p class={m.role === 'player' ? 'me' : 'npc'}><b>{m.role === 'player' ? 'You' : npcLabel}:</b> {m.content}</p>
            {/each}
            {#if gateMsgs.length > gateShown}
              <button class="link more" onclick={() => (gateShown += GATE_PAGE)}>more ({gateMsgs.length - gateShown} earlier)</button>
            {/if}
          </div>
        {/if}
      </section>

      <aside class="side">
        <div class="panel">
          <h3>Atmosphere</h3>
          <button class="primary" onclick={toggleSpotify}>🎵 Spotify: {spotifyOn ? 'on' : 'off'}</button>
          {#if spotifyOn}
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
        </div>
        <div class="panel">
          <h3><button class="link paneltitle" onclick={openLeaderboard}>Leaderboard ↗</button></h3>
          {#if myWindow.length}
            <ul class="lbside">{#each myWindow as r}<li class:me={r.is_me}><span class="rank">#{r.rank}</span><span class="nm">{r.display_name}</span><span class="score">{r.progress}</span></li>{/each}</ul>
          {:else}
            <p class="sub">no ranking yet</p>
          {/if}
        </div>
        {#if game.notes.length}
          <div class="panel">
            <h3>Notes</h3>
            <ul class="notelist">{#each game.notes as n}<li>{n}</li>{/each}</ul>
          </div>
        {/if}
        <div class="panel">
          <h3>Log <span class="sub">(your progress)</span></h3>
          <ul class="log">
            {#each logRev.slice(0, logShown) as l}
              <li><span class="seq">#{l.seq}</span> {l.summary || '…'}
                {#if l.seq > 0}<button class="link rollback" title="Roll back to here" aria-label="Roll back to here" onclick={() => onRollback(l.seq)}>↩</button>{/if}
              </li>
            {/each}
          </ul>
          {#if logRev.length > logShown}
            <button class="link more" onclick={() => (logShown += LOG_PAGE)}>more ({logRev.length - logShown} earlier)</button>
          {/if}
        </div>
      </aside>
    </div>
  {/if}

  {#if showMap && world}
    <div class="modal" onclick={() => (showMap = false)}>
      <div class="modal-card" onclick={(e) => e.stopPropagation()}>
        <h2>🗺 World map <span class="sub">— southern Sweden</span></h2>
        <div class="worldgrid">
          {#each world.cells as c}
            <button class="cell {c.kind}" class:current={c.id === world.current_cell_id}
              style={`grid-column:${c.grid_x};grid-row:${c.grid_y}`}
              disabled={busy || !c.reachable}
              onclick={() => onTravel(c.id)}>
              <b>{c.name}</b><br><span class="sub">{c.kind}</span>
              {#if c.id === world.current_cell_id}<br><span class="here">you are here</span>
              {:else if !c.reachable}<br><span class="sub">too far</span>{/if}
            </button>
          {/each}
        </div>
        <button onclick={() => (showMap = false)}>Close</button>
      </div>
    </div>
  {/if}

  {#if showLb}
    <div class="modal" onclick={() => (showLb = false)}>
      <div class="modal-card lb" onclick={(e) => e.stopPropagation()}>
        <h2>🏆 Leaderboard <span class="sub">— {lbTotal} players</span></h2>
        {#if lbMe}
          <p>You are <b>#{lbMe.rank}</b> — {lbMe.progress} <button class="link" onclick={jumpToMe}>jump to me</button></p>
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
            <li class:me={r.is_me}><span class="rank">#{r.rank}</span><span class="nm">{r.display_name}</span><span class="score">{r.progress}</span></li>
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
        <h2>⚙ Admin</h2>
        {#if adminMsg}<div class="notice">{adminMsg}</div>{/if}

        <div class="admin-sec">
          <h3>Authored content</h3>
          <p class="sub">World, NPCs, story, puzzles. Import is validated + non-destructive (upsert).</p>
          <button onclick={exportContent}>Export YAML</button>
          <label class="filebtn">Import YAML/JSON<input type="file" accept=".yaml,.yml,.json" onchange={importContentFile} /></label>
          <button onclick={openEditor}>Edit content…</button>
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

  {#snippet formFields()}
    {#each FIELDS[editKind] as f (f.key)}
      <div class="field">
        <label>{f.key}{#if f.ref} <span class="sub">→ {f.ref}</span>{/if}</label>
        {#if f.t === 'bool'}
          <input type="checkbox" class="chk" bind:checked={form[f.key]} />
        {:else if f.t === 'number'}
          <input type="number" bind:value={form[f.key]} />
        {:else if f.t === 'longtext'}
          <textarea rows="4" bind:value={form[f.key]}></textarea>
        {:else if f.t === 'json'}
          <textarea rows="5" class="json" value={form[f.key]} oninput={(e) => setJson(f.key, e.target.value)}></textarea>
          {#if jsonErrors[f.key]}<div class="json-err">{jsonErrors[f.key]}</div>{/if}
        {:else if f.t === 'select'}
          <select bind:value={form[f.key]}>
            {#if f.ref}<option value="">—</option>{/if}
            {#each optionsFor(f) as o}<option value={o}>{o}</option>{/each}
          </select>
        {:else}
          <input type="text" bind:value={form[f.key]} readonly={f.key === 'id' && selId !== null} />
        {/if}
      </div>
    {/each}
    <div class="row">
      <button class="primary" onclick={saveEntity} disabled={!canSave || busy}>Save</button>
      {#if selId !== null}<button class="danger" onclick={deleteCurrent} disabled={busy}>Delete</button>{/if}
    </div>
  {/snippet}

  {#if showEditor}
    <div class="modal" onclick={() => (showEditor = false)}>
      <div class="modal-card editor" onclick={(e) => e.stopPropagation()}>
        <div class="editor-head">
          <h2>⚙ Edit content</h2>
          <div class="viewtabs">
            <button class:active={editorView === 'list'} onclick={() => (editorView = 'list')}>List</button>
            <button class:active={editorView === 'graph'} onclick={setGraphView}>Graph</button>
          </div>
          <div class="undobar">
            <button onclick={doUndo} disabled={logHead <= 0 || busy} title="Undo">↶</button>
            <span class="sub">#{logHead}</span>
            <button onclick={doRedo} disabled={!canRedo || busy} title="Redo">↷</button>
          </div>
        </div>
        {#if editorMsg}<div class="notice">{editorMsg}</div>{/if}

        {#if editorView === 'list'}
          <div class="kindtabs">
            {#each KINDS as k}
              <button class="link" class:active={k === editKind} onclick={() => selectKind(k)}>{k}</button>
            {/each}
          </div>
          <div class="editor-grid">
            <div class="idlist">
              <button class:sel={selId === null} onclick={newEntity}>+ New {singular(editKind)}</button>
              {#each (content?.[editKind] ?? []) as x}
                <button class:sel={x.id === selId} onclick={() => pickEntity(x.id)}>{x.id}</button>
              {/each}
            </div>
            <div class="form">{@render formFields()}</div>
          </div>
        {:else}
          <div class="graphwrap">
            <div class="canvas">
              <SvelteFlow bind:nodes={flowNodes} bind:edges={flowEdges} {nodeTypes} fitView
                onnodeclick={onNodeClick} onedgeclick={onEdgeClick}
                onconnect={onConnect} onnodedragstop={onNodeDragStop}
                onnodepointerenter={({ node, event }) => showHover('node', node.data.rec, event)}
                onnodepointerleave={() => (hover = null)}
                onedgepointerenter={({ edge, event }) => showHover('edge', edge.data.rec, event)}
                onedgepointerleave={() => (hover = null)}>
                <Background />
                <Controls />
              </SvelteFlow>
              <div class="graphtools">
                <button onclick={addNode} disabled={busy}>+ Node</button>
                {#if selId !== null}<button class="danger" onclick={deleteCurrent} disabled={busy}>Delete {editKind === 'edges' ? 'edge' : 'node'}</button>{/if}
                <span class="sub">drag a node to move · drag handle→node to connect · click to edit</span>
              </div>
            </div>
            <div class="graphside">
              {#if selId !== null}
                <div class="sidehd">{editKind === 'edges' ? 'Edge' : 'Node'}: <code>{selId}</code></div>
                {@render formFields()}
              {:else}
                <p class="sub">Select a node or edge to edit it, or use “+ Node”.</p>
              {/if}
            </div>
          </div>
        {/if}

        <details class="history">
          <summary>History — {logRows.length} action(s), at #{logHead}</summary>
          <ul>
            <li><button class="link" class:athead={logHead === 0} onclick={() => gotoSeq(0)}>#0 baseline (current world)</button></li>
            {#each logRows as e (e.seq)}
              <li><button class="link" class:athead={e.seq === logHead} class:undone={!e.applied} onclick={() => gotoSeq(e.seq)}>#{e.seq} {e.op} {e.kind} {e.entity_id}</button></li>
            {/each}
          </ul>
        </details>

        <button onclick={() => (showEditor = false)}>Close</button>
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
  main { max-width: 1000px; margin: 0 auto; padding: 1.5rem; }
  h1 { font-weight: normal; } .sub { color:#6b6b80; font-size:.7em; }
  .layout { display:grid; grid-template-columns: 1fr 300px; gap:1.5rem; }
  .topbar { text-align:right; margin-bottom:.5rem; }
  .body { font-size:1.15rem; line-height:1.6; }
  .media { color:#5a5a72; font-size:.85rem; }
  .banner { position:relative; border-radius:8px; overflow:hidden; margin-bottom:1rem; border:1px solid #2a2e3e; }
  .banner :global(svg), .banner img { display:block; width:100%; height:140px; object-fit:cover; }
  .banner .setting { position:absolute; bottom:.4rem; right:.6rem; font-size:.75rem; color:#cdbb9a; background:rgba(0,0,0,.45); padding:.1rem .45rem; border-radius:4px; }
  .tracks { list-style:none; padding:0; font-size:.8rem; margin:.6rem 0 0; }
  .tracks li { display:flex; gap:.5rem; margin:.55rem 0; align-items:center; }
  .tracks img { width:42px; height:42px; border-radius:4px; flex-shrink:0; }
  .panel, .notes { background:#1a1d28; border:1px solid #2a2e3e; border-radius:8px; padding:1rem; margin-bottom:1rem; }
  .narrow { max-width:420px; }
  .tabs { display:flex; gap:.5rem; margin-bottom:1rem; }
  .tabs button { flex:1; background:#15171f; }
  .tabs button.active { background:#34416a; }
  .switch { margin-top:.7rem; font-size:.85rem; color:#9aa; }
  .chat { background:#15171f; border-radius:8px; padding:.75rem; margin:.5rem 0; }
  .chat .me { color:#9fd3ff; } .chat .npc { color:#cdbb9a; }
  .row { display:flex; gap:.5rem; margin:.5rem 0; }
  input { display:block; width:100%; box-sizing:border-box; background:#0d0e14; border:1px solid #2a2e3e; color:#e8e8f0; padding:.5rem; border-radius:6px; margin:.4rem 0; }
  .row input { margin:0; flex:1; }
  .qr { background:#fff; padding:.5rem; border-radius:6px; width:max-content; }
  .qr :global(svg) { display:block; width:180px; height:180px; }
  .manual { white-space:pre-wrap; background:#15171f; border:1px solid #2a2e3e; border-radius:8px; padding:1rem; font-family:Georgia,serif; line-height:1.5; }
  .quiz-q { margin:.8rem 0; } .quiz-q .q { font-weight:bold; margin-bottom:.3rem; }
  .opt { display:block; cursor:pointer; padding:.15rem 0; }
  .opt input { display:inline; width:auto; margin-right:.5rem; }
  .codes { list-style:none; padding:0; display:grid; grid-template-columns:1fr 1fr; gap:.4rem; }
  .codes code { background:#0d0e14; padding:.35rem .5rem; border-radius:6px; display:block; text-align:center; letter-spacing:1px; }
  .edges { display:flex; flex-direction:column; gap:.5rem; margin-top:1rem; }
  .notes { margin-top:1.5rem; }
  button { background:#2a3550; color:#e8e8f0; border:1px solid #3a456a; padding:.55rem .8rem; border-radius:6px; cursor:pointer; text-align:left; }
  button:hover { background:#34416a; }
  button.primary { background:#34416a; text-align:center; width:100%; }
  button.danger { background:#4a2330; border-color:#6a3346; }
  button.link { background:none; border:none; color:#7fa8d8; padding:0 0 0 .4rem; width:auto; cursor:pointer; font-size:.8rem; }
  .dead { color:#c98; }
  .log { list-style:none; padding:0; font-size:.85rem; } .log .seq { color:#5a5a72; }
  .more { display:inline-block; margin-top:.5rem; }
  .rollback { font-size:1rem; line-height:1; }
  .notelist { list-style:disc; padding-left:1.1rem; margin:.3rem 0 0; font-size:.82rem; color:#cdbb9a; }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid rgba(255,255,255,.3); border-top-color:#e8e8f0; border-radius:50%; animation:spin .6s linear infinite; vertical-align:middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { background:#3a2330; border:1px solid #6a3346; padding:.5rem .8rem; border-radius:6px; margin-bottom:1rem; }
  .notice { background:#23323a; border:1px solid #356a5a; padding:.5rem .8rem; border-radius:6px; margin-bottom:1rem; }
  .modal { position:fixed; inset:0; background:rgba(0,0,0,.7); display:flex; align-items:center; justify-content:center; }
  .modal-card { background:#1a1d28; border:1px solid #3a456a; border-radius:10px; padding:1.5rem 2rem; min-width:300px; }
  .modal-card.admin { width:480px; max-width:90vw; max-height:85vh; overflow:auto; }
  .modal-card.help { width:560px; max-width:92vw; max-height:85vh; overflow:auto; }
  .modal-card.lb { width:420px; max-width:92vw; max-height:85vh; overflow:auto; }
  .modal-card.editor { width:1080px; max-width:96vw; max-height:92vh; overflow:auto; }
  .editor-head { display:flex; align-items:center; gap:1rem; }
  .editor-head h2 { flex:1; margin:.2rem 0; }
  .viewtabs { display:flex; gap:.2rem; }
  .viewtabs button { padding:.25rem .7rem; font-size:.85rem; }
  .viewtabs button.active { background:#34416a; color:#cdbb9a; }
  .undobar { display:flex; align-items:center; gap:.4rem; }
  .undobar button { padding:.25rem .55rem; }
  .kindtabs { display:flex; flex-wrap:wrap; gap:.2rem; border-bottom:1px solid #2a2e3e; padding-bottom:.5rem; margin:.4rem 0 .6rem; }
  .kindtabs .link { padding:.2rem .5rem; }
  .kindtabs .link.active { color:#cdbb9a; font-weight:bold; }
  .editor-grid { display:grid; grid-template-columns:220px 1fr; gap:1rem; }
  .idlist { max-height:62vh; overflow:auto; display:flex; flex-direction:column; gap:.15rem; }
  .idlist button { width:100%; font-size:.8rem; padding:.3rem .5rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .idlist button.sel { background:#34416a; color:#cdbb9a; }
  .form .field { margin-bottom:.5rem; }
  .form .field label { display:block; font-size:.78rem; color:#9a9ab0; margin-bottom:.1rem; }
  .form textarea, .form select { width:100%; box-sizing:border-box; background:#0d0e14; border:1px solid #2a2e3e; color:#e8e8f0; padding:.5rem; border-radius:6px; font:inherit; }
  .form textarea.json { font-family:monospace; font-size:.8rem; }
  .form input[readonly] { opacity:.55; }
  .form .chk { width:auto; display:inline-block; }
  .json-err { color:#e06c75; font-size:.78rem; margin-top:.15rem; }
  /* graph view */
  .graphwrap { display:grid; grid-template-columns:1fr 300px; gap:1rem; }
  .canvas { position:relative; height:66vh; border:1px solid #2a2e3e; border-radius:8px; overflow:hidden; background:#0d0e14; }
  .canvas :global(.svelte-flow) { background:#0d0e14; }
  .graphtools { position:absolute; left:.5rem; top:.5rem; z-index:5; display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; }
  .graphtools button { padding:.3rem .6rem; font-size:.82rem; }
  .graphside { max-height:66vh; overflow:auto; }
  .graphside .sidehd { margin-bottom:.4rem; font-size:.9rem; }
  .history { margin-top:.8rem; border-top:1px solid #2a2e3e; padding-top:.4rem; }
  .history summary { cursor:pointer; color:#9a9ab0; font-size:.85rem; }
  .history ul { list-style:none; padding:.3rem 0 0; max-height:24vh; overflow:auto; }
  .history .link { font-size:.8rem; font-family:monospace; }
  .history .athead { color:#cdbb9a; font-weight:bold; }
  .history .undone { opacity:.45; text-decoration:line-through; }
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
