<script>
  // Admin map editor for the overview map. Shows every location icon at its graph-
  // editor position on the parchment and draws the graph edges as roads. Icons are
  // DRAGGABLE — dropping one saves the node's position back into the YAML (the same
  // pos the story-graph editor uses). The "Redraw roads" button re-routes the roads
  // as splines that bend around icons and push apart, saved onto their edges.
  // Coordinates are normalized 0..1 against the parchment, matching the player map.
  import { api, contentAsset } from './api.js';
  import { roadPath } from './maputil.js';   // shared with the player map (MapOverlay)
  import './map.css';                        // shared .map-* styles (also used by MapOverlay)

  let { onClose } = $props();

  let block = $state(null);           // { image, nodes:[{id,title,icon,x,y,scale}], roads:[{from,to,points}] }
  let error = $state('');
  let msg = $state('');
  let busy = $state(false);
  let cw = $state(0), ch = $state(0); // rendered image size in px
  let imgOk = $state(true);
  let stageEl = $state(null);         // the parchment stage element (for drag coords)
  let drag = $state(null);            // { loc, sx, sy, moved } while pressing an icon
  let selected = $state(null);        // loc id of the icon whose scale is being edited
  let revealAll = $state(true);       // fog-of-war toggle: on = show every place (default)
  let drawFrom = $state(null);        // draw-edge mode: source place id (after a long-press)
  let cursor = $state(null);          // {x,y} px while rubber-banding to the pointer
  let pressTimer = null;              // long-press timer handle (non-reactive)
  const LONG_MS = 450;                // hold this long on an icon to start drawing an edge
  let hitDrag = $state(null);         // { id } while dragging a hotspot's resize handle
  const DEFAULT_HIT = 0.09;           // default hotspot side (fraction of map width)

  const nodes = $derived(block?.nodes ?? []);
  const roads = $derived(block?.roads ?? []);
  const byId = $derived(Object.fromEntries(nodes.map((n) => [n.id, n])));
  // With fog on (revealAll off) only the starting cell's places are visible, as a
  // fresh player would first see them.
  const visible = $derived(new Set(
    (revealAll ? nodes : nodes.filter((n) => n.cell === block?.start_cell)).map((n) => n.id)));
  let hovered = $state(null);   // loc id of the icon under the mouse (shows its big label)

  async function load() {
    try { block = await api.mapOverview(); }
    catch (e) { error = e.message; }
  }
  load();

  const cx = (n) => n.x * cw, cy = (n) => n.y * ch;

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

  // --- Drag an icon to reposition it; on drop, persist back to the YAML ----------
  // The icon shows a normalized 0..1 position; we invert it through the same bounds
  // the backend normalized with, then save the node's graph pixel pos (the single
  // source of truth that both this map and the story-graph editor read).
  function startDrag(e, n) {
    e.preventDefault();
    if (drawFrom) {                          // we're placing the edge's 2nd endpoint
      const src = drawFrom; cancelDraw();
      if (n.id !== src) addEdge(src, n.id);  // clicking the source again just cancels
      return;
    }
    drag = { loc: n.id, sx: e.clientX, sy: e.clientY, moved: false };
    stageEl?.setPointerCapture?.(e.pointerId);
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {          // held still long enough → draw-edge mode
      if (drag && !drag.moved) {
        const s = byId[drag.loc];
        drawFrom = drag.loc; selected = null;
        cursor = s ? { x: s.x * cw, y: s.y * ch } : null;
        drag = null;
        try { stageEl?.releasePointerCapture?.(e.pointerId); } catch (_) {}
      }
    }, LONG_MS);
  }
  function onMove(e) {
    if (hitDrag) {                           // resize the centred hotspot square
      if (!cw || !ch || !stageEl) return;
      const n = byId[hitDrag.id];
      if (!n) return;
      const r = stageEl.getBoundingClientRect();
      // The square is centred on the icon; its side is twice the larger of the
      // pointer's x/y distance from that centre (so the corner tracks the cursor).
      const half = Math.max(Math.abs((e.clientX - r.left) - n.x * cw),
                            Math.abs((e.clientY - r.top) - n.y * ch));
      n.hit = +Math.max(0.02, Math.min(0.5, (2 * half) / cw)).toFixed(4);
      block = { ...block, nodes: [...nodes] };
      return;
    }
    if (drawFrom) {                          // rubber-band the edge to the pointer
      if (!stageEl) return;
      const r = stageEl.getBoundingClientRect();
      cursor = { x: e.clientX - r.left, y: e.clientY - r.top };
      return;
    }
    if (!drag || !cw || !ch || !stageEl) return;
    if (!drag.moved) {   // ignore tiny jitter so a click stays a click (select), not a drag
      if (Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 4) return;
      drag.moved = true; clearTimeout(pressTimer);   // a real drag — not a long-press
    }
    const r = stageEl.getBoundingClientRect();
    const clamp = (v) => Math.min(0.98, Math.max(0.02, v));
    const n = byId[drag.loc];
    if (!n) return;
    n.x = +clamp((e.clientX - r.left) / cw).toFixed(4);
    n.y = +clamp((e.clientY - r.top) / ch).toFixed(4);
    block = { ...block, nodes: [...nodes] };   // nudge reactivity (icons + roads follow)
  }
  async function endDrag(e) {
    clearTimeout(pressTimer);
    if (hitDrag) {                   // finished resizing a hotspot → persist to YAML
      const n = byId[hitDrag.id];
      hitDrag = null;
      try { stageEl?.releasePointerCapture?.(e.pointerId); } catch (_) {}
      if (n) {
        try { await api.setNodeHit(n.node_id, n.hit); msg = `Saved “${n.title}” hotspot ${(n.hit).toFixed(3)}.`; }
        catch (err) { msg = `Hotspot save failed: ${err.message}`; }
      }
      return;
    }
    if (drawFrom) return;            // long-press armed draw mode; await the target click
    if (!drag) return;
    const n = byId[drag.loc], moved = drag.moved;
    drag = null;
    stageEl?.releasePointerCapture?.(e.pointerId);
    if (!moved) { selected = n?.id ?? null; return; }   // a click selects it (edit scale)
    const b = block?.bounds;
    if (!n || !b) { msg = 'No layout bounds — cannot save position.'; return; }
    const span = 1 - 2 * b.margin;
    const px = b.maxx === b.minx ? b.minx : b.minx + ((n.x - b.margin) / span) * (b.maxx - b.minx);
    const py = b.maxy === b.miny ? b.miny : b.miny + ((n.y - b.margin) / span) * (b.maxy - b.miny);
    try {
      await api.moveNode(n.node_id, Math.round(px), Math.round(py));
      msg = `Saved “${n.title}” position to the YAML.`;
    } catch (err) { msg = `Save failed: ${err.message}`; }
  }

  // --- Draw-edge mode: connect the long-pressed place to the next one clicked -------
  function cancelDraw() { drawFrom = null; cursor = null; }
  async function addEdge(fromLoc, toLoc) {
    const f = byId[fromLoc], t = byId[toLoc];
    if (!f || !t) return;
    const label = window.prompt(
      `Label for the edge between “${f.title}” and “${t.title}”\n(blank = auto “Go to …”; applied to both directions)`, '');
    if (label === null) return;     // cancelled
    busy = true; msg = '';
    try {
      const r = await api.addEdge(f.node_id, t.node_id, label.trim(), true);
      await load();                 // reload so the new road(s) show
      msg = r.created?.length
        ? `Added ${r.created.length} edge(s): ${f.title} ↔ ${t.title}.`
        : `Edge already existed between ${f.title} and ${t.title}.`;
    } catch (err) { msg = `Add edge failed: ${err.message}`; }
    finally { busy = false; }
  }

  // --- Hotspot: the centred square that governs hover/click on the player map ------
  function startHitDrag(e, n) {
    e.preventDefault(); e.stopPropagation();
    hitDrag = { id: n.id };
    stageEl?.setPointerCapture?.(e.pointerId);
  }
  async function addHotspot(n) {
    n.hit = DEFAULT_HIT; block = { ...block, nodes: [...nodes] };
    try { await api.setNodeHit(n.node_id, n.hit); msg = `Added “${n.title}” hotspot — drag its corner to size.`; }
    catch (e) { msg = `Hotspot failed: ${e.message}`; }
  }
  async function clearHotspot(n) {
    n.hit = null; block = { ...block, nodes: [...nodes] };
    try { await api.setNodeHit(n.node_id, null); msg = `Cleared “${n.title}” hotspot.`; }
    catch (e) { msg = `Hotspot failed: ${e.message}`; }
  }

  // --- Edit a selected icon's scale; live-resize on input, save to YAML on release ---
  function setScaleLive(n, v) { n.scale = v; block = { ...block, nodes: [...nodes] }; }
  async function saveScale(n, v) {
    setScaleLive(n, v);
    try {
      await api.setNodeScale(n.node_id, v);
      msg = `Saved “${n.title}” scale ${v.toFixed(2)}× to the YAML.`;
    } catch (err) { msg = `Scale save failed: ${err.message}`; }
  }
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && cancelDraw()} />

<div class="me-overlay">
  <div class="me-card">
    <div class="me-head">
      <b>🗺 Road editor</b>
      <button onclick={redrawAndSave} disabled={busy || !nodes.length}
        title="Re-route every road as a spline that bends around icons and away from other roads, then save">
        {busy ? 'Working…' : '🛣 Redraw roads'}</button>
      <label class="me-fog" title="Off shows only the starting cell's places, as a new player first sees the map">
        <input type="checkbox" bind:checked={revealAll} /> Reveal all (no fog)</label>
      <span class="sub">{nodes.length ? `${nodes.length} places · ${roads.length} roads · drag to move · click to set scale / hotspot · long-press to draw an edge` : ''}</span>
      <button class="me-x" onclick={onClose}>✕</button>
    </div>
    {#if msg}<p class="me-msg">{msg}</p>{/if}
    {#if error}<p class="me-err">{error}</p>{/if}

    {#if block}
      <div class="me-scroll">
        <div class="me-stage" bind:this={stageEl} bind:clientWidth={cw} bind:clientHeight={ch}
             onpointermove={onMove} onpointerup={endDrag}
             style={imgOk ? '' : 'aspect-ratio:4/3'}>
          <img class="me-bg" src={contentAsset(block.image)} alt="" draggable="false"
               onload={() => (imgOk = true)} onerror={() => (imgOk = false)} />
          {#if !imgOk}<div class="me-missing">map background missing: <code>{block.image}</code></div>{/if}
          {#if drawFrom && byId[drawFrom]}
            <div class="me-hint">Drawing an edge from “{byId[drawFrom].title}” — click another place to connect, or Esc to cancel</div>
          {/if}
          {#if cw > 0 && ch > 0}
            <svg class="me-svg" width={cw} height={ch} viewBox={`0 0 ${cw} ${ch}`}>
              <defs>
                {#each roads as r, i}
                  {#if byId[r.from] && byId[r.to] && visible.has(r.from) && visible.has(r.to)}
                    <linearGradient id={`meroad${i}`} gradientUnits="userSpaceOnUse"
                      x1={cx(byId[r.from])} y1={cy(byId[r.from])} x2={cx(byId[r.to])} y2={cy(byId[r.to])}>
                      <stop offset="0" stop-color="#7a5230" stop-opacity="0" />
                      <stop offset="0.22" stop-color="#7a5230" stop-opacity="0.7" />
                      <stop offset="0.78" stop-color="#7a5230" stop-opacity="0.7" />
                      <stop offset="1" stop-color="#7a5230" stop-opacity="0" />
                    </linearGradient>
                  {/if}
                {/each}
              </defs>
              {#each roads as r, i}
                {#if byId[r.from] && byId[r.to] && visible.has(r.from) && visible.has(r.to)}
                  <path class="map-road" fill="none" stroke={`url(#meroad${i})`} d={roadPath(byId, r, cw, ch)} />
                {/if}
              {/each}
              {#if drawFrom && byId[drawFrom] && cursor}
                <line class="me-draw" x1={byId[drawFrom].x * cw} y1={byId[drawFrom].y * ch}
                      x2={cursor.x} y2={cursor.y} />
              {/if}
            </svg>
            {#each nodes as n}
              {#if visible.has(n.id)}
              <!-- clickbox: icons WITHOUT a hotspot click via their image bounds, so
                   outline that box. Icons WITH a hit get the .me-hit square instead. -->
              <div class="me-icon" class:dragging={drag?.loc === n.id} class:drawsrc={drawFrom === n.id}
                   class:clickbox={!n.hit}
                   style={`left:${n.x * cw}px; top:${n.y * ch}px; --map-scale:${n.scale ?? 1}`}
                   onpointerdown={(e) => startDrag(e, n)}
                   onmouseenter={() => (hovered = n.id)} onmouseleave={() => (hovered === n.id && (hovered = null))}>
                <img class="map-icon" src={contentAsset(n.icon)} alt={n.title} draggable="false" />
              </div>
              {/if}
            {/each}

            <!-- Hotspot squares: a faint outline for every icon that has one; the
                 selected icon also gets a corner handle to resize it. -->
            {#each nodes as n}
              {#if visible.has(n.id) && n.hit}
                <div class="me-hit" class:sel={selected === n.id}
                     style={`left:${n.x * cw}px; top:${n.y * ch}px; width:${n.hit * cw}px; height:${n.hit * cw}px`}>
                  {#if selected === n.id}
                    <div class="me-hit-handle" title="Drag to resize the hover/click square"
                         onpointerdown={(e) => startHitDrag(e, n)}></div>
                  {/if}
                </div>
              {/if}
            {/each}

            {#if hovered && byId[hovered] && visible.has(hovered)}
              <div class="map-label">{byId[hovered].title}</div>
            {/if}

            {#if selected && byId[selected] && visible.has(selected)}
              <div class="me-scale" style={`left:${byId[selected].x * cw}px; top:${byId[selected].y * ch}px`}>
                <span class="me-scale-t">{byId[selected].title}</span>
                <input type="range" min="0.5" max="3" step="0.05" value={byId[selected].scale ?? 1}
                  oninput={(e) => setScaleLive(byId[selected], +e.target.value)}
                  onchange={(e) => saveScale(byId[selected], +e.target.value)} />
                <span class="me-scale-v">{(byId[selected].scale ?? 1).toFixed(2)}×</span>
                {#if byId[selected].hit}
                  <button class="me-scale-x" title="Remove the hover/click square (falls back to the icon)"
                    onclick={() => clearHotspot(byId[selected])}>▢ clear</button>
                {:else}
                  <button class="me-scale-x" title="Add a centred hover/click square; drag its corner to size"
                    onclick={() => addHotspot(byId[selected])}>▢ hotspot</button>
                {/if}
                <button class="me-scale-x" onclick={() => (selected = null)}>done</button>
              </div>
            {/if}
          {/if}
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .me-overlay { position:fixed; inset:0; z-index:2100; background:rgba(8,9,14,.9);
    display:flex; align-items:center; justify-content:center; padding:0; }
  /* 5% margin around the editor; no internal padding. */
  .me-card { background:#15171f; border:1px solid #2a2e3e; border-radius:10px; padding:0;
    width:90vw; height:90vh; max-width:90vw; max-height:90vh; box-sizing:border-box;
    display:flex; flex-direction:column; }
  .me-head { display:flex; align-items:center; gap:.7rem; margin-bottom:.5rem; flex-wrap:wrap; }
  .me-head button { background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a; border-radius:6px;
    padding:.3rem .8rem; cursor:pointer; }
  .me-head button:hover:not(:disabled) { background:#34416a; color:#cdbb9a; }
  .me-fog { display:flex; align-items:center; gap:.35rem; color:#cdbb9a; font-size:.85rem;
    cursor:pointer; user-select:none; }
  .me-fog input { cursor:pointer; }
  .me-x { margin-left:auto; }
  .me-msg { margin:.2rem 0; color:#9fd29f; font-size:.85rem; }
  .me-err { margin:.2rem 0; color:#e0a; font-size:.85rem; }
  .me-scroll { overflow:auto; border:1px solid #2a2e3e; border-radius:8px; background:#0d0e14;
    flex:1 1 auto; min-height:0; display:flex; }
  /* Fill the card: as large a 4:3 map as fits the available height/width. */
  .me-stage { position:relative; line-height:0; width:100%;
    max-width:calc((90vh - 4rem) * 4 / 3); margin:auto; }
  .me-bg { display:block; width:100%; height:auto; }
  .me-missing { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    color:#9a9ab0; font-size:.9rem; }
  .me-svg { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
  /* Icon image (.map-icon), hover label (.map-label) and roads (.map-road) are styled
     in the shared ./map.css; only editor-specific affordances live here. */
  .me-icon { position:absolute; transform:translate(-50%, -50%); cursor:grab;
    touch-action:none; user-select:none; }
  .me-icon.dragging { cursor:grabbing; z-index:5; }
  /* A thin outline of each icon's fallback clickable box (icons with a hotspot show
     the .me-hit square instead). Outline doesn't grow the box, so centring holds. */
  .me-icon.clickbox { outline:1px dashed rgba(192,86,58,.45); outline-offset:0; border-radius:3px; }
  .me-icon.drawsrc img { filter:drop-shadow(0 0 0 #c0563a) drop-shadow(0 0 7px rgba(192,86,58,.95)); }
  /* The interactive hotspot square (centred on the icon). Faint until selected. */
  .me-hit { position:absolute; transform:translate(-50%, -50%); z-index:8; box-sizing:border-box;
    border:1.5px dashed rgba(192,86,58,.45); border-radius:3px; pointer-events:none; }
  .me-hit.sel { border-color:#c0563a; background:rgba(192,86,58,.10); }
  .me-hit-handle { position:absolute; right:-7px; bottom:-7px; width:14px; height:14px; border-radius:3px;
    background:#c0563a; border:2px solid #fff; cursor:nwse-resize; pointer-events:auto; touch-action:none;
    box-shadow:0 1px 4px rgba(0,0,0,.5); }
  /* Rubber-band line while drawing a new edge. */
  .me-draw { stroke:#c0563a; stroke-width:2; stroke-dasharray:6 4; opacity:.95; pointer-events:none; }
  .me-hint { position:absolute; left:50%; top:8px; transform:translateX(-50%); z-index:20;
    background:rgba(192,86,58,.92); color:#fff; font:600 12px Georgia, serif; padding:.3rem .7rem;
    border-radius:6px; pointer-events:none; white-space:nowrap; box-shadow:0 2px 8px rgba(0,0,0,.4); }
  .me-scale { position:absolute; transform:translate(-50%, -135%); z-index:10; display:flex;
    align-items:center; gap:.4rem; background:rgba(18,20,28,.96); border:1px solid #3a456a;
    border-radius:6px; padding:.25rem .5rem; white-space:nowrap; pointer-events:auto;
    box-shadow:0 4px 12px rgba(0,0,0,.5); }
  .me-scale-t { color:#cdbb9a; font:600 11px Georgia, serif; max-width:120px; overflow:hidden;
    text-overflow:ellipsis; }
  .me-scale input { width:120px; cursor:pointer; }
  .me-scale-v { color:#e8e6df; font-size:.8rem; min-width:3.2ch; text-align:right; }
  .me-scale-x { background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a; border-radius:4px;
    cursor:pointer; font-size:.75rem; padding:.1rem .45rem; }
  .me-scale-x:hover { background:#34416a; }
</style>
