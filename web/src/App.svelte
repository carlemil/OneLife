<script>
  import { onMount, tick } from 'svelte';
  import { api, download } from './lib/api.js';
  import * as spotify from './lib/spotify.js';

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
  let board = $state([]);
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
    board = (await api.leaderboard()).rows;
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

  async function refresh() {
    try {
      game = await api.state();
      logEntries = (await api.log()).entries;
      board = (await api.leaderboard()).rows;
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
    try { game = await api.rollback(seq); logEntries = (await api.log()).entries; board = (await api.leaderboard()).rows; }
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
          <ul class="lbside">{#each board as r}<li class:me={r.is_me}><span class="rank">#{r.rank}</span> {r.display_name} — <b>{r.progress}</b></li>{/each}</ul>
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
            <li class:me={r.is_me}><span class="rank">#{r.rank}</span> {r.display_name} <b>{r.progress}</b></li>
          {/each}
          {#if lbRows.length === 0}<li class="sub">no matches</li>{/if}
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
  .paneltitle { background:none; border:none; color:#e8e8f0; font:inherit; padding:0; cursor:pointer; }
  .paneltitle:hover { color:#7fa8d8; }
  .lblist, .lbside { list-style:none; padding:0; }
  .lblist li, .lbside li { padding:.12rem 0; }
  .lblist .rank, .lbside .rank { color:#5a5a72; }
  .lblist li.me, .lbside li.me { color:#cdbb9a; }
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
