<script>
  import { onMount } from 'svelte';
  import { api } from './lib/api.js';

  let registered = $state(api.hasToken());
  let name = $state('');
  let game = $state(null);
  let logEntries = $state([]);
  let board = $state([]);
  let gateInput = $state('');
  let puzzleInput = $state('');
  let error = $state('');
  let busy = $state(false);
  let showBoard = $state(false);
  let actions = 0;

  onMount(() => { if (registered) refresh(); });

  async function refresh() {
    try {
      game = await api.state();
      logEntries = (await api.log()).entries;
      board = (await api.leaderboard()).rows;
      error = '';
    } catch (e) { error = e.message; }
  }

  async function doRegister() {
    if (!name.trim()) return;
    busy = true;
    try { await api.register(name.trim()); registered = true; await refresh(); }
    catch (e) { error = e.message; }
    finally { busy = false; }
  }

  function tick() {
    actions += 1;
    if (actions % 5 === 0) showBoard = true;   // periodic leaderboard focus (design §7)
  }

  async function onEdge(id) {
    busy = true;
    try { game = await api.takeEdge(id); logEntries = (await api.log()).entries; tick(); }
    catch (e) { error = e.message; }
    finally { busy = false; }
  }

  async function onGate() {
    if (!gateInput.trim()) return;
    busy = true;
    try {
      const r = await api.gate(gateInput.trim());
      gateInput = '';
      game = r.state;
      logEntries = (await api.log()).entries;
      tick();
    } catch (e) { error = e.message; }
    finally { busy = false; }
  }

  async function onPuzzle() {
    if (!puzzleInput.trim()) return;
    busy = true;
    try {
      const r = await api.puzzle(puzzleInput.trim());
      game = r.state;
      logEntries = (await api.log()).entries;
      if (!r.result.solved && r.result.hint) error = `Hint: ${r.result.hint}`;
      else error = '';
      puzzleInput = '';
      tick();
    } catch (e) { error = e.message; }
    finally { busy = false; }
  }

  async function onRollback(seq) {
    if (!confirm(`Roll the log back to step ${seq}? You will lose all progress after it.`)) return;
    busy = true;
    try { game = await api.rollback(seq); logEntries = (await api.log()).entries; board = (await api.leaderboard()).rows; }
    catch (e) { error = e.message; }
    finally { busy = false; }
  }
</script>

<main>
  <h1>OneLife <span class="sub">— prototype slice</span></h1>

  {#if error}<div class="error">{error}</div>{/if}

  {#if !registered}
    <div class="panel">
      <p>Enter a name to begin. (Auth is stubbed for the slice — no password yet.)</p>
      <input bind:value={name} placeholder="Your name" onkeydown={(e) => e.key === 'Enter' && doRegister()} />
      <button onclick={doRegister} disabled={busy}>Begin</button>
    </div>
  {:else if game}
    <div class="layout">
      <section class="story">
        <h2>{game.node.title}</h2>
        <p class="body">{game.node.body}</p>
        <p class="media">🎨 {game.node.media.image_theme} &nbsp; 🎵 {game.node.media.music_theme}</p>

        {#if game.node.type === 'gate' && game.gate}
          <div class="chat">
            {#each game.gate.messages as m}
              <p class={m.role === 'player' ? 'me' : 'npc'}><b>{m.role === 'player' ? 'You' : 'NPC'}:</b> {m.content}</p>
            {/each}
          </div>
          {#if !game.gate.satisfied}
            <div class="row">
              <input bind:value={gateInput} placeholder="Say something..." onkeydown={(e) => e.key === 'Enter' && onGate()} />
              <button onclick={onGate} disabled={busy}>Say</button>
            </div>
          {/if}
        {/if}

        {#if game.node.type === 'puzzle' && game.puzzle && !game.puzzle.solved}
          <div class="row">
            <input bind:value={puzzleInput} placeholder="Enter the code..." onkeydown={(e) => e.key === 'Enter' && onPuzzle()} />
            <button onclick={onPuzzle} disabled={busy}>Try</button>
          </div>
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
        </div>

        {#if game.notes.length}
          <div class="notes">
            <h3>Notes</h3>
            <ul>{#each game.notes as n}<li>{n}</li>{/each}</ul>
          </div>
        {/if}
      </section>

      <aside class="side">
        <div class="panel">
          <h3>Leaderboard</h3>
          <ol>{#each board as r}<li>{r.display_name} — <b>{r.progress}</b></li>{/each}</ol>
        </div>
        <div class="panel">
          <h3>Log <span class="sub">(your progress)</span></h3>
          <ul class="log">
            {#each logEntries as l}
              <li>
                <span class="seq">#{l.seq}</span> {l.summary || '…'}
                {#if l.seq > 0}<button class="link" onclick={() => onRollback(l.seq)}>roll back here</button>{/if}
              </li>
            {/each}
          </ul>
        </div>
      </aside>
    </div>
  {:else}
    <p>Loading…</p>
  {/if}

  {#if showBoard}
    <div class="modal" onclick={() => (showBoard = false)}>
      <div class="modal-card" onclick={(e) => e.stopPropagation()}>
        <h2>🏆 Leaderboard</h2>
        <ol>{#each board as r}<li>{r.display_name} — <b>{r.progress}</b></li>{/each}</ol>
        <button onclick={() => (showBoard = false)}>Back to the dark</button>
      </div>
    </div>
  {/if}
</main>

<style>
  :global(body) { background:#11131a; color:#d8d8e0; font-family: Georgia, serif; margin:0; }
  main { max-width: 1000px; margin: 0 auto; padding: 1.5rem; }
  h1 { font-weight: normal; } .sub { color:#6b6b80; font-size:.7em; }
  .layout { display:grid; grid-template-columns: 1fr 300px; gap:1.5rem; }
  .body { font-size:1.15rem; line-height:1.6; }
  .media { color:#5a5a72; font-size:.85rem; }
  .panel, .notes { background:#1a1d28; border:1px solid #2a2e3e; border-radius:8px; padding:1rem; margin-bottom:1rem; }
  .chat { background:#15171f; border-radius:8px; padding:.75rem; margin:.5rem 0; }
  .chat .me { color:#9fd3ff; } .chat .npc { color:#cdbb9a; }
  .row { display:flex; gap:.5rem; margin:.5rem 0; }
  input { flex:1; background:#0d0e14; border:1px solid #2a2e3e; color:#e8e8f0; padding:.5rem; border-radius:6px; }
  .edges { display:flex; flex-direction:column; gap:.5rem; margin-top:1rem; }
  button { background:#2a3550; color:#e8e8f0; border:1px solid #3a456a; padding:.55rem .8rem; border-radius:6px; cursor:pointer; text-align:left; }
  button:hover { background:#34416a; }
  button.danger { background:#4a2330; border-color:#6a3346; }
  button.link { background:none; border:none; color:#7fa8d8; padding:0 0 0 .4rem; width:auto; cursor:pointer; font-size:.8rem; }
  .dead { color:#c98; }
  .log { list-style:none; padding:0; font-size:.85rem; } .log .seq { color:#5a5a72; }
  .error { background:#3a2330; border:1px solid #6a3346; padding:.5rem .8rem; border-radius:6px; margin-bottom:1rem; }
  .modal { position:fixed; inset:0; background:rgba(0,0,0,.7); display:flex; align-items:center; justify-content:center; }
  .modal-card { background:#1a1d28; border:1px solid #3a456a; border-radius:10px; padding:1.5rem 2rem; min-width:300px; }
</style>
