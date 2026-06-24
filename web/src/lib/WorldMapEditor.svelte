<script>
  // Admin-only drag-and-drop editor for world-map node positions. It overlays the
  // rendered base image with draggable markers + the road edges, so you can move
  // places around (and watch the crossing count). Save writes
  // world-map.positions.json, which render_map.py reads to place nodes by hand —
  // re-run the worldmap skill afterwards to regenerate the pretty map.
  import { api } from './api.js';

  let { onClose } = $props();
  const BASE = '/worldmap';
  const DISPLAY_W = 1200;

  let meta = $state(null);
  let error = $state('');
  let msg = $state('');
  let positions = $state({});            // { id: {x, y} } in canvas pixels
  let dragId = null;
  let stageEl;

  const scale = $derived(meta ? DISPLAY_W / meta.width : 1);
  const sx = (v) => v * (meta ? DISPLAY_W / meta.width : 1);

  async function load() {
    try {
      meta = await (await fetch(`${BASE}/world-map.meta.json`, { cache: 'no-cache' })).json();
      const cur = {};
      for (const l of meta.locations) cur[l.id] = { x: l.x, y: l.y };
      const r = await api.getWorldmapPositions();
      for (const [id, xy] of Object.entries(r.positions || {})) {
        if (cur[id]) cur[id] = { x: xy.x, y: xy.y };
      }
      positions = cur;
    } catch (e) { error = e.message; }
  }
  load();

  const edges = $derived(meta ? meta.roads.filter((r) => positions[r.from] && positions[r.to]) : []);

  function crossings() {
    const ccw = (a, b, c) => (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x);
    let n = 0;
    for (let i = 0; i < edges.length; i++) {
      for (let j = i + 1; j < edges.length; j++) {
        const r1 = edges[i], r2 = edges[j];
        if (r1.from === r2.from || r1.from === r2.to || r1.to === r2.from || r1.to === r2.to) continue;
        const a = positions[r1.from], b = positions[r1.to], c = positions[r2.from], d = positions[r2.to];
        if (ccw(a, c, d) !== ccw(b, c, d) && ccw(a, b, c) !== ccw(a, b, d)) n++;
      }
    }
    return n;
  }

  function down(e, id) {
    dragId = id;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  }
  function move(e) {
    if (!dragId || !stageEl) return;
    const rect = stageEl.getBoundingClientRect();
    const x = Math.max(0, Math.min(meta.width, (e.clientX - rect.left) / scale));
    const y = Math.max(0, Math.min(meta.height, (e.clientY - rect.top) / scale));
    positions = { ...positions, [dragId]: { x, y } };
  }
  function up() { dragId = null; }

  async function save() {
    try {
      const r = await api.saveWorldmapPositions(positions);
      msg = `Saved ${r.count} position(s). Re-run the worldmap skill to regenerate the map.`;
    } catch (e) { msg = 'Save failed: ' + e.message; }
  }
  async function resetAuto() {
    const cur = {};
    for (const l of meta.locations) cur[l.id] = { x: l.x, y: l.y };
    positions = cur;
    try {
      await api.saveWorldmapPositions({});
      msg = 'Cleared manual positions. Re-run the skill to get the automatic layout.';
    } catch (e) { msg = 'Reset failed: ' + e.message; }
  }
</script>

<div class="me-overlay" onpointermove={move} onpointerup={up}>
  <div class="me-card">
    <div class="me-head">
      <b>🗺 Map editor</b>
      <span class="sub">drag places · edges: {edges.length} · crossings: {crossings()}</span>
      <button class="me-btn" onclick={save}>Save</button>
      <button class="me-btn" onclick={resetAuto} title="Discard manual positions">Reset to auto</button>
      <button class="me-btn me-x" onclick={onClose}>✕</button>
    </div>
    {#if msg}<p class="me-msg">{msg}</p>{/if}
    {#if error}<p class="me-err">{error}</p>{/if}
    {#if meta}
      <div class="me-scroll">
        <div class="me-stage" bind:this={stageEl}
             style="width:{sx(meta.width)}px; height:{sx(meta.height)}px">
          <img class="me-bg" src="{BASE}/{meta.base}" alt="" draggable="false" />
          <svg class="me-svg" width={sx(meta.width)} height={sx(meta.height)}>
            {#each edges as r}
              <line x1={sx(positions[r.from].x)} y1={sx(positions[r.from].y)}
                    x2={sx(positions[r.to].x)} y2={sx(positions[r.to].y)}
                    stroke="#b34a2e" stroke-width="2" stroke-opacity="0.8" />
            {/each}
          </svg>
          {#each meta.locations as l}
            {#if positions[l.id]}
              <button class="me-node" onpointerdown={(e) => down(e, l.id)}
                      style="left:{sx(positions[l.id].x)}px; top:{sx(positions[l.id].y)}px">
                <span class="me-dot"></span>
                <span class="me-lbl">{l.name}</span>
              </button>
            {/if}
          {/each}
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .me-overlay { position:fixed; inset:0; z-index:2100; background:rgba(8,9,14,.9);
    display:flex; align-items:center; justify-content:center; padding:1rem; }
  .me-card { background:#15171f; border:1px solid #2a2e3e; border-radius:10px; padding:.8rem;
    max-width:97vw; max-height:97vh; display:flex; flex-direction:column; }
  .me-head { display:flex; align-items:center; gap:.7rem; margin-bottom:.5rem; }
  .me-btn { background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a; border-radius:6px;
    padding:.25rem .7rem; cursor:pointer; }
  .me-btn:hover { background:#3a456a; }
  .me-x { margin-left:auto; }
  .me-msg { margin:.2rem 0; color:#9fd29f; font-size:.85rem; }
  .me-err { margin:.2rem 0; color:#e0a; font-size:.85rem; }
  .me-scroll { overflow:auto; border:1px solid #2a2e3e; border-radius:8px; background:#0d0e14; }
  .me-stage { position:relative; touch-action:none; user-select:none; }
  .me-bg { position:absolute; left:0; top:0; width:100%; height:100%; opacity:.7; }
  .me-svg { position:absolute; left:0; top:0; pointer-events:none; }
  .me-node { position:absolute; transform:translate(-50%,-50%); display:flex; align-items:center;
    gap:.25rem; background:transparent; border:0; padding:.3rem; cursor:grab; white-space:nowrap; }
  .me-node:active { cursor:grabbing; }
  .me-dot { width:14px; height:14px; border-radius:50%; background:#e8b24a; border:2px solid #1a1d28;
    box-shadow:0 0 0 1px #e8b24a; flex:0 0 auto; }
  .me-lbl { font-size:.7rem; color:#f3e9d2; text-shadow:0 1px 3px #000, 0 0 5px #000; }
</style>
