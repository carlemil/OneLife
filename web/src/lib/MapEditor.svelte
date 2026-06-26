<script>
  // Admin road editor for the overview map. Shows every location icon at its (read-
  // only) graph-editor position on the parchment, draws the graph edges as roads, and
  // the "Redraw roads" button re-routes them as splines that bend around icons and
  // push apart from one another. The routes are saved onto their edges in the YAML.
  // Coordinates are normalized 0..1 against the parchment, matching the player map.
  import { api, contentAsset } from './api.js';

  let { onClose } = $props();

  let block = $state(null);           // { image, nodes:[{id,title,icon,x,y,scale}], roads:[{from,to,points}] }
  let error = $state('');
  let msg = $state('');
  let busy = $state(false);
  let cw = $state(0), ch = $state(0); // rendered image size in px
  let imgOk = $state(true);

  const nodes = $derived(block?.nodes ?? []);
  const roads = $derived(block?.roads ?? []);
  const byId = $derived(Object.fromEntries(nodes.map((n) => [n.id, n])));

  async function load() {
    try { block = await api.mapOverview(); }
    catch (e) { error = e.message; }
  }
  load();

  // --- Catmull-Rom spline through the endpoints + interior waypoints (px) ---
  function pathFor(r) {
    const a = byId[r.from], b = byId[r.to];
    if (!a || !b) return '';
    const pts = [[a.x, a.y], ...(r.points ?? []), [b.x, b.y]].map(([x, y]) => [x * cw, y * ch]);
    if (pts.length === 2) return `M ${pts[0]} L ${pts[1]}`;
    let d = `M ${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] ?? pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] ?? p2;
      const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
      const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += ` C ${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
    }
    return d;
  }

  // --- Route every road: interior control points relaxed by a deterministic force
  // sim. Each point is repelled by icons it isn't an endpoint of (so the spline
  // bends around them), pushed away from other roads' points (so roads don't stack),
  // and lightly sprung to its straight-line slot (so it doesn't wander). 0..1 space.
  function redraw() {
    if (!nodes.length) return;
    const pos = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const segLen = (r) => Math.hypot(pos[r.to].x - pos[r.from].x, pos[r.to].y - pos[r.from].y);
    // interior count scales with length (2..5)
    const lerp = (a, b, t) => [a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t];
    const ICON_R = 0.075, SPRING = 0.06, ROAD_R = 0.05;
    const items = roads.filter((r) => pos[r.from] && pos[r.to] && r.from !== r.to).map((r) => {
      const m = Math.max(2, Math.min(5, Math.round(segLen(r) / 0.18)));
      const slots = Array.from({ length: m }, (_, i) => lerp(pos[r.from], pos[r.to], (i + 1) / (m + 1)));
      return { r, ends: [r.from, r.to], cps: slots.map((s) => [...s]), slots };
    });
    const clamp = (v) => Math.min(0.98, Math.max(0.02, v));
    for (let iter = 0; iter < 160; iter++) {
      const F = items.map((it) => it.cps.map(() => [0, 0]));
      // icon repulsion + straight-line spring
      items.forEach((it, ii) => it.cps.forEach((p, pi) => {
        for (const n of nodes) {
          if (n.id === it.ends[0] || n.id === it.ends[1]) continue;
          const dx = p[0] - n.x, dy = p[1] - n.y, d = Math.hypot(dx, dy) || 1e-4;
          const R = ICON_R * (n.scale || 1);
          if (d < R) { const f = ((R - d) / R) * 0.012 / d; F[ii][pi][0] += dx * f; F[ii][pi][1] += dy * f; }
        }
        const s = it.slots[pi];
        F[ii][pi][0] += (s[0] - p[0]) * SPRING; F[ii][pi][1] += (s[1] - p[1]) * SPRING;
      }));
      // road-vs-road point repulsion (separate overlapping/crossing roads)
      for (let a = 0; a < items.length; a++) for (let b = a + 1; b < items.length; b++) {
        for (let pa = 0; pa < items[a].cps.length; pa++) for (let pb = 0; pb < items[b].cps.length; pb++) {
          const A = items[a].cps[pa], B = items[b].cps[pb];
          const dx = A[0] - B[0], dy = A[1] - B[1], d = Math.hypot(dx, dy) || 1e-4;
          if (d < ROAD_R) {
            const f = ((ROAD_R - d) / ROAD_R) * 0.006 / d;
            F[a][pa][0] += dx * f; F[a][pa][1] += dy * f; F[b][pb][0] -= dx * f; F[b][pb][1] -= dy * f;
          }
        }
      }
      items.forEach((it, ii) => it.cps.forEach((p, pi) => {
        p[0] = clamp(p[0] + Math.max(-0.04, Math.min(0.04, F[ii][pi][0])));
        p[1] = clamp(p[1] + Math.max(-0.04, Math.min(0.04, F[ii][pi][1])));
      }));
    }
    for (const it of items) it.r.points = it.cps.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]);
    block = { ...block, roads: [...roads] };   // nudge reactivity
  }

  async function save() {
    busy = true; msg = '';
    try {
      const r = await api.saveRoads(roads.map((rd) => ({ from: rd.from, to: rd.to, points: rd.points ?? [] })));
      msg = `Saved ${r.edges_written} edge road(s) to the YAML.`
        + (r.pairs_without_edge ? ` (${r.pairs_without_edge} cell lane(s) had no edge to store on.)` : '');
    } catch (e) { msg = `Save failed: ${e.message}`; } finally { busy = false; }
  }
  async function redrawAndSave() { redraw(); await save(); }
</script>

<div class="me-overlay">
  <div class="me-card">
    <div class="me-head">
      <b>🗺 Road editor</b>
      <button onclick={redrawAndSave} disabled={busy || !nodes.length}
        title="Re-route every road as a spline that bends around icons and away from other roads, then save">
        {busy ? 'Working…' : '🛣 Redraw roads'}</button>
      <span class="sub">{nodes.length ? `${nodes.length} places · ${roads.length} roads · positions are set in the story graph` : ''}</span>
      <button class="me-x" onclick={onClose}>✕</button>
    </div>
    {#if msg}<p class="me-msg">{msg}</p>{/if}
    {#if error}<p class="me-err">{error}</p>{/if}

    {#if block}
      <div class="me-scroll">
        <div class="me-stage" bind:clientWidth={cw} bind:clientHeight={ch}
             style={imgOk ? '' : 'aspect-ratio:4/3'}>
          <img class="me-bg" src={contentAsset(block.image)} alt="" draggable="false"
               onload={() => (imgOk = true)} onerror={() => (imgOk = false)} />
          {#if !imgOk}<div class="me-missing">map background missing: <code>{block.image}</code></div>{/if}
          {#if cw > 0 && ch > 0}
            <svg class="me-svg" width={cw} height={ch}>
              {#each roads as r}
                {#if byId[r.from] && byId[r.to]}
                  <path class="me-road" d={pathFor(r)} />
                {/if}
              {/each}
            </svg>
            {#each nodes as n}
              <div class="me-icon" style={`left:${n.x * cw}px; top:${n.y * ch}px; --s:${n.scale ?? 1}`}>
                <img src={contentAsset(n.icon)} alt={n.title} draggable="false" />
                <span>{n.title}</span>
              </div>
            {/each}
          {/if}
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
  .me-head { display:flex; align-items:center; gap:.7rem; margin-bottom:.5rem; flex-wrap:wrap; }
  .me-head button { background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a; border-radius:6px;
    padding:.3rem .8rem; cursor:pointer; }
  .me-head button:hover:not(:disabled) { background:#34416a; color:#cdbb9a; }
  .me-x { margin-left:auto; }
  .me-msg { margin:.2rem 0; color:#9fd29f; font-size:.85rem; }
  .me-err { margin:.2rem 0; color:#e0a; font-size:.85rem; }
  .me-scroll { overflow:auto; border:1px solid #2a2e3e; border-radius:8px; background:#0d0e14; }
  .me-stage { position:relative; line-height:0; width:1100px; max-width:88vw; }
  .me-bg { display:block; width:100%; height:auto; }
  .me-missing { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    color:#9a9ab0; font-size:.9rem; }
  .me-svg { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
  .me-road { fill:none; stroke:#7a5230; stroke-width:3; stroke-linecap:round; opacity:.85; }
  .me-icon { position:absolute; transform:translate(-50%, -50%); display:flex; flex-direction:column;
    align-items:center; gap:1px; pointer-events:none; }
  .me-icon img { width:calc(clamp(40px, 6vw, 76px) * var(--s, 1)); height:auto; display:block;
    filter:drop-shadow(0 2px 3px rgba(0,0,0,.5)); }
  .me-icon span { font:600 11px Georgia, serif; color:#3a2a16; white-space:nowrap;
    text-shadow:0 1px 0 rgba(255,250,240,.6); }
</style>
