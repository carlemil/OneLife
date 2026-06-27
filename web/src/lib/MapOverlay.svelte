<script>
  // The game's single overview map: location icons placed on a parchment at their
  // story-graph editor positions. Clicking a reachable icon plays the walk animation,
  // then calls onPick(item) — the parent dispatches a walk (within the cell) or a
  // travel (to an adjacent cell) from the item's `action`/`target`. Coordinates are
  // normalized 0..1 against the parchment.
  //
  // This same component is ALSO the admin map editor: when `admin` is true an "✎ Edit"
  // toggle appears, and in edit mode the icons become draggable (saved back to the
  // YAML), long-press draws an edge, click sets per-icon scale, and "Redraw roads"
  // re-routes every road. Editor mode loads the richer admin block (api.mapOverview:
  // node_id + bounds + every place, no fog/reachability) and renders that; play mode
  // renders the `block` the parent passes in. One file, one set of functions, with the
  // authoring affordances simply toggled off for normal players.
  import { api, contentAsset } from './api.js';
  import { roadPath } from './maputil.js';   // shared spline geometry (player + editor)
  import { fade } from 'svelte/transition';
  import './map.css';                        // shared .map-* styles

  let {
    block,                 // play block: { image, nodes:[{id,title,icon,x,y,current,reachable,action,target,scale}], roads }
    onPick,                // (item) => go there (called after the walk animation)
    onClose,               // () => close the map
    busy = false,          // parent-driven (a navigation is in flight)
    admin = false,         // show the editor toggle
    startInEdit = false,   // open straight into edit mode (admin panel entry)
  } = $props();

  const WALK_MS = 1150;
  let stageEl = $state(null);           // the parchment stage (for pointer coords)
  let cw = $state(0), ch = $state(0);   // rendered parchment size in px
  let imgOk = $state(true);
  let walk = $state(null);              // { d } while animating
  let walking = $state(false);
  let hovered = $state(null);           // id of the nearest icon to the pointer

  // --- editor state (inert unless `edit`) -----------------------------------------
  let edit = $state(admin && startInEdit);
  let editBlock = $state(null);         // admin block (api.mapOverview), loaded on first edit
  let working = $state(false);          // an editor save is in flight
  let msg = $state('');
  let revealAll = $state(true);         // fog toggle: on = show every place (default)
  let drag = $state(null);              // { loc, sx, sy, moved } while pressing an icon
  let selected = $state(null);          // loc id whose scale is being edited
  let drawFrom = $state(null);          // draw-edge mode: source place id (after long-press)
  let cursor = $state(null);            // {x,y} px while rubber-banding to the pointer
  let pressTimer = null;                // long-press timer handle (non-reactive)
  const LONG_MS = 450;                  // hold this long on an icon to start an edge

  // The active data block: the admin overview while editing, else the play block.
  const view = $derived(edit ? editBlock : block);
  const nodes = $derived(view?.nodes ?? []);
  const roads = $derived(view?.roads ?? []);
  const byId = $derived(Object.fromEntries(nodes.map((n) => [n.id, n])));
  const cx = (n) => n.x * cw, cy = (n) => n.y * ch;
  const hoverReachable = $derived(!!(hovered && byId[hovered]?.reachable));

  // Fog preview is an editor-only affordance: with "Reveal all" off, only the starting
  // cell's places are visible (as a fresh player first sees them). Play mode and edit-
  // with-reveal show everything in the block.
  const visible = $derived(
    edit && !revealAll && editBlock
      ? new Set(editBlock.nodes.filter((n) => n.cell === editBlock.start_cell).map((n) => n.id))
      : new Set(nodes.map((n) => n.id)));

  // The icon whose centre is nearest the pointer is the selection (Voronoi-style; no
  // per-icon hitboxes — the whole map is one surface). Fog-hidden icons aren't
  // candidates. Identical algorithm for play (click to go) and edit (grab/select).
  function nearestNode(clientX, clientY) {
    if (!stageEl || !cw || !ch || !nodes.length) return null;
    const r = stageEl.getBoundingClientRect();
    const px = clientX - r.left, py = clientY - r.top;
    let best = null, bestD = Infinity;
    for (const n of nodes) {
      if (!visible.has(n.id)) continue;
      const dx = px - n.x * cw, dy = py - n.y * ch, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = n; }
    }
    return best;
  }

  // --- pointer handling (stage owns it; icons are inert) --------------------------
  function onMapMove(e) {
    hovered = nearestNode(e.clientX, e.clientY)?.id ?? null;
    if (!edit) return;
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
    editBlock = { ...editBlock, nodes: [...nodes] };   // nudge reactivity (icons + roads follow)
  }
  function onMapLeave() { if (!drag && !drawFrom) hovered = null; }
  function onMapDown(e) {
    if (!edit) return;
    if (e.target.closest?.('.mo-scale')) return;       // let the scale popup work
    const n = nearestNode(e.clientX, e.clientY);
    if (n) startDrag(e, n);
  }
  function onMapClick(e) {
    if (edit) return;                                  // edit uses pointerdown/up
    const n = nearestNode(e.clientX, e.clientY);
    if (n) pick(n);
  }

  async function pick(n) {
    if (walking || busy || !n.reachable) return;
    walking = true;
    const cur = nodes.find((x) => x.current);
    if (cur && cur.id !== n.id && cw && ch) {
      walk = { d: `M ${Math.round(cx(cur))},${Math.round(cy(cur))} L ${Math.round(cx(n))},${Math.round(cy(n))}` };
      await new Promise((r) => setTimeout(r, WALK_MS));
    }
    walking = false; walk = null;
    onPick?.(n);
  }

  // --- editor: enter/leave + load the admin block ---------------------------------
  // The admin block (node_id + bounds + every place) is fetched lazily the first time
  // edit mode is on — whether toggled here or opened straight into edit (startInEdit).
  async function loadEditBlock() {
    working = true; msg = '';
    try { editBlock = await api.mapOverview(); }
    catch (e) { msg = `Could not load the editor map: ${e.message}`; edit = false; }
    finally { working = false; }
  }
  $effect(() => { if (edit && !editBlock && !working) loadEditBlock(); });

  function toggleEdit() {
    if (edit) { edit = false; cancelDraw(); selected = null; return; }
    edit = true;   // the $effect above loads editBlock if it isn't cached yet
  }

  // --- editor: route every road (deterministic force sim, 0..1 space) --------------
  // Each interior point is repelled by icons it isn't an endpoint of (the spline bends
  // around them), pushed off other roads' points (roads don't stack), and lightly
  // sprung to its straight-line slot (so it doesn't wander).
  function redraw() {
    if (!nodes.length) return;
    const pos = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const segLen = (r) => Math.hypot(pos[r.to].x - pos[r.from].x, pos[r.to].y - pos[r.from].y);
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
    editBlock = { ...editBlock, roads: [...roads] };   // nudge reactivity
  }

  async function save() {
    working = true; msg = '';
    try {
      const r = await api.saveRoads(roads.map((rd) => ({ from: rd.from, to: rd.to, points: rd.points ?? [] })));
      msg = `Saved ${r.edges_written} edge road(s) to the YAML.`
        + (r.pairs_without_edge ? ` (${r.pairs_without_edge} cell lane(s) had no edge to store on.)` : '');
    } catch (e) { msg = `Save failed: ${e.message}`; } finally { working = false; }
  }
  async function redrawAndSave() { redraw(); await save(); }

  // --- editor: drag an icon to reposition it; on drop, persist back to the YAML ----
  function startDrag(e, n) {
    e.preventDefault();
    if (drawFrom) {                          // placing the edge's 2nd endpoint
      const src = drawFrom; cancelDraw();
      if (n.id !== src) addEdge(src, n.id);  // clicking the source again just cancels
      return;
    }
    drag = { loc: n.id, sx: e.clientX, sy: e.clientY, moved: false };
    stageEl?.setPointerCapture?.(e.pointerId);
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {           // held still long enough → draw-edge mode
      if (drag && !drag.moved) {
        const s = byId[drag.loc];
        drawFrom = drag.loc; selected = null;
        cursor = s ? { x: s.x * cw, y: s.y * ch } : null;
        drag = null;
        try { stageEl?.releasePointerCapture?.(e.pointerId); } catch (_) {}
      }
    }, LONG_MS);
  }
  async function endDrag(e) {
    if (!edit) return;
    clearTimeout(pressTimer);
    if (drawFrom) return;             // long-press armed draw mode; await the target click
    if (!drag) return;
    const n = byId[drag.loc], moved = drag.moved;
    drag = null;
    stageEl?.releasePointerCapture?.(e.pointerId);
    if (!moved) { selected = n?.id ?? null; return; }   // a click selects it (edit scale)
    if (!n) return;
    // Save the dropped position as the node's STABLE normalized map coord. No bbox
    // inversion, no touching graph pixels — so this icon stays exactly here and no other
    // icon moves on the next load.
    try {
      await api.moveNodeMap(n.node_id, n.x, n.y);
      msg = `Saved “${n.title}” position to the YAML.`;
    } catch (err) { msg = `Save failed: ${err.message}`; }
  }

  // --- editor: draw-edge mode -----------------------------------------------------
  function cancelDraw() { drawFrom = null; cursor = null; }
  async function addEdge(fromLoc, toLoc) {
    const f = byId[fromLoc], t = byId[toLoc];
    if (!f || !t) return;
    const label = window.prompt(
      `Label for the edge between “${f.title}” and “${t.title}”\n(blank = auto “Go to …”; applied to both directions)`, '');
    if (label === null) return;     // cancelled
    working = true; msg = '';
    try {
      const r = await api.addEdge(f.node_id, t.node_id, label.trim(), true);
      // Merge the new road locally instead of reloading — a reload re-normalizes EVERY
      // node against a fresh bounding box, so icons would jump. Nodes only move on drag.
      const exists = roads.some((rd) =>
        (rd.from === fromLoc && rd.to === toLoc) || (rd.from === toLoc && rd.to === fromLoc));
      if (!exists) editBlock = { ...editBlock, roads: [...roads, { from: fromLoc, to: toLoc, points: [] }] };
      msg = r.created?.length
        ? `Added ${r.created.length} edge(s): ${f.title} ↔ ${t.title}.`
        : `Edge already existed between ${f.title} and ${t.title}.`;
    } catch (err) { msg = `Add edge failed: ${err.message}`; }
    finally { working = false; }
  }

  // --- editor: per-icon scale; live-resize on input, save to YAML on release -------
  function setScaleLive(n, v) { n.scale = v; editBlock = { ...editBlock, nodes: [...nodes] }; }
  async function saveScale(n, v) {
    setScaleLive(n, v);
    try {
      await api.setNodeScale(n.node_id, v);
      msg = `Saved “${n.title}” scale ${v.toFixed(2)}× to the YAML.`;
    } catch (err) { msg = `Scale save failed: ${err.message}`; }
  }
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && drawFrom && cancelDraw()} />

<div class="mo-modal" role="dialog" aria-label="Map" onclick={(e) => e.target === e.currentTarget && onClose?.()}>
  <div class="mo-card" class:editing={edit}>
    {#if admin}
      <div class="mo-tools">
        <button class="mo-btn" onclick={toggleEdit} disabled={working}>
          {edit ? '✓ Done editing' : '✎ Edit map'}</button>
        {#if edit}
          <button class="mo-btn" onclick={redrawAndSave} disabled={working || !nodes.length}
            title="Re-route every road as a spline that bends around icons and away from other roads, then save">
            {working ? 'Working…' : '🛣 Redraw roads'}</button>
          <label class="mo-fog" title="Off shows only the starting cell's places, as a new player first sees the map">
            <input type="checkbox" bind:checked={revealAll} /> Reveal all</label>
          <span class="mo-sub">{nodes.length ? `${nodes.length} places · ${roads.length} roads · drag to move · click to set scale · long-press to draw an edge` : ''}</span>
        {/if}
        <button class="mo-x" onclick={onClose}>✕</button>
      </div>
    {/if}
    {#if msg}<p class="mo-msg">{msg}</p>{/if}

    {#if view}
    <div class="mo map-stage" bind:this={stageEl} bind:clientWidth={cw} bind:clientHeight={ch}
         class:canclick={!edit && hoverReachable} class:edit class:dragging={edit && drag?.moved}
         role="presentation"
         onpointermove={onMapMove} onpointerleave={onMapLeave}
         onpointerdown={onMapDown} onpointerup={endDrag} onclick={onMapClick}
         style={imgOk ? '' : 'width:min(900px,90vw); aspect-ratio:4/3'}>
      <img class="map-bg" src={contentAsset(view.image)} alt="" draggable="false"
           onload={() => (imgOk = true)} onerror={() => (imgOk = false)} />
      {#if !imgOk}
        <div class="mo-missing">map background missing<br /><code>{view.image}</code></div>
      {/if}

      {#if drawFrom && byId[drawFrom]}
        <div class="mo-hint">Drawing an edge from “{byId[drawFrom].title}” — click another place to connect, or Esc to cancel</div>
      {/if}

      {#if cw > 0 && ch > 0}
        <svg class="map-svg" width={cw} height={ch} viewBox={`0 0 ${cw} ${ch}`}>
          <defs>
            {#each roads as r, i}
              {#if byId[r.from] && byId[r.to] && visible.has(r.from) && visible.has(r.to)}
                <linearGradient id={`moroad${i}`} gradientUnits="userSpaceOnUse"
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
              <path class="map-road" fill="none" stroke={`url(#moroad${i})`} d={roadPath(byId, r, cw, ch)} />
            {/if}
          {/each}
          {#if drawFrom && byId[drawFrom] && cursor}
            <line class="mo-draw" x1={byId[drawFrom].x * cw} y1={byId[drawFrom].y * ch}
                  x2={cursor.x} y2={cursor.y} />
          {/if}
          {#if walk}
            <path class="mo-trail" d={walk.d} style={`--dur:${WALK_MS}ms`} pathLength="1" />
          {/if}
        </svg>

        {#each nodes as n}
          {#if visible.has(n.id)}
            <!-- Pure visual: pointer handling lives on the stage (nearest-icon), so the
                 icons themselves are inert. -->
            <div class="mo-node" class:reachable={n.reachable} class:current={n.current}
                 class:disabled={!edit && !n.reachable && !n.current} class:hovered={hovered === n.id}
                 class:dragging={edit && drag?.loc === n.id} class:drawsrc={drawFrom === n.id}
                 style={`left:${cx(n)}px; top:${cy(n)}px`}>
              <img class="map-icon" style={`--map-scale:${n.scale ?? 1}`}
                   src={contentAsset(n.icon)} alt={n.title} draggable="false" />
            </div>
          {/if}
        {/each}

        {#if hovered && byId[hovered] && visible.has(hovered)}
          <!-- Keyed on the title so a change from one location to another swaps the
               element, crossfading the old text out while the new fades in. -->
          {#key byId[hovered].title}
            <div class="map-label" in:fade|global={{ duration: 200 }} out:fade|global={{ duration: 200 }}>{byId[hovered].title}</div>
          {/key}
        {/if}

        {#if edit && selected && byId[selected] && visible.has(selected)}
          <div class="mo-scale" style={`left:${byId[selected].x * cw}px; top:${byId[selected].y * ch}px`}>
            <span class="mo-scale-t">{byId[selected].title}</span>
            <input type="range" min="0.5" max="3" step="0.05" value={byId[selected].scale ?? 1}
              oninput={(e) => setScaleLive(byId[selected], +e.target.value)}
              onchange={(e) => saveScale(byId[selected], +e.target.value)} />
            <span class="mo-scale-v">{(byId[selected].scale ?? 1).toFixed(2)}×</span>
            <button class="mo-scale-x" onclick={() => (selected = null)}>done</button>
          </div>
        {/if}

        {#if walk}
          <div class="mo-walker" style={`offset-path:path('${walk.d}'); --dur:${WALK_MS}ms`}></div>
        {/if}
      {/if}
    </div>
    {/if}
  </div>
</div>

<style>
  /* A layer above the rest of the game UI, with a thin transparent border. */
  .mo-modal { position:fixed; inset:0; z-index:50; display:flex; align-items:center;
    justify-content:center; background:rgba(0,0,0,.72); padding:1.5rem; }
  /* The card shrinks to the parchment (flex item → content size). The parchment stage
     (.map-stage), backdrop (.map-bg) and roads (.map-svg) are sized in the shared
     ./map.css to the SAME rule the editor uses, so the map is the same size in both. */
  .mo-card { position:relative; max-width:var(--map-max-w); max-height:var(--map-max-h);
    overflow:hidden; border-radius:10px; line-height:0; display:flex; flex-direction:column; }
  /* In edit mode the card carries a toolbar; give it room and a frame. */
  .mo-card.editing { max-width:90vw; max-height:90vh; background:#15171f;
    border:1px solid #2a2e3e; padding:.5rem; line-height:1.2; gap:.4rem; }
  .mo-missing { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
    justify-content:center; text-align:center; color:#9a9ab0; font-size:.9rem; line-height:1.4; }

  /* Editor toolbar (admins only). */
  .mo-tools { display:flex; align-items:center; gap:.7rem; flex-wrap:wrap; line-height:1.2; }
  .mo-btn { background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a; border-radius:6px;
    padding:.3rem .8rem; cursor:pointer; }
  .mo-btn:hover:not(:disabled) { background:#34416a; color:#cdbb9a; }
  .mo-fog { display:flex; align-items:center; gap:.35rem; color:#cdbb9a; font-size:.85rem;
    cursor:pointer; user-select:none; }
  .mo-fog input { cursor:pointer; }
  .mo-sub { color:#8a8fa3; font-size:.8rem; }
  .mo-x { margin-left:auto; background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a;
    border-radius:6px; padding:.3rem .6rem; cursor:pointer; }
  .mo-msg { margin:0; color:#9fd29f; font-size:.85rem; line-height:1.2; }

  /* The stage is one pointer surface. Play: pointer cursor when the nearest icon is
     reachable. Edit: grab/grabbing. */
  .mo.canclick { cursor:pointer; }
  .mo.edit { cursor:grab; touch-action:none; }
  .mo.edit.dragging { cursor:grabbing; }

  /* Icons are pure visuals — inert; the stage handles all pointer input. */
  .mo-node { position:absolute; transform:translate(-50%, -50%); pointer-events:none; }
  .mo-node.disabled { opacity:.45; filter:grayscale(.5); }
  /* Hover-grow the click target: in play only a reachable icon, in edit any icon
     (the nearest is the drag/long-press target). */
  .mo-node.reachable.hovered .map-icon,
  .mo.edit .mo-node.hovered .map-icon { transform:scale(1.05); filter:drop-shadow(0 3px 6px rgba(0,0,0,.6)); }
  .mo-node.dragging { z-index:5; }
  .mo-node.drawsrc .map-icon { filter:drop-shadow(0 0 0 #c0563a) drop-shadow(0 0 7px rgba(192,86,58,.95)); }
  .mo-node.current .map-icon { filter:drop-shadow(0 0 0 #c0563a) drop-shadow(0 0 6px rgba(192,86,58,.9));
    animation:mopulse 1.8s infinite; }
  @keyframes mopulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.08)} }

  .mo-trail { fill:none; stroke:#b34a2e; stroke-width:4; stroke-linecap:round; opacity:.9;
    stroke-dasharray:1; stroke-dashoffset:1; animation:mo-draw var(--dur) ease-out forwards; }
  @keyframes mo-draw { to { stroke-dashoffset:0; } }
  .mo-walker { position:absolute; left:0; top:0; width:16px; height:16px; border-radius:50%;
    background:#b34a2e; box-shadow:0 0 8px #000, 0 0 0 4px rgba(179,74,46,.3); pointer-events:none;
    offset-distance:0%; offset-anchor:center; animation:mo-walk var(--dur) ease-out forwards; }
  @keyframes mo-walk { to { offset-distance:100%; } }

  /* Editor: rubber-band line while drawing an edge, the hint banner, and the scale popup. */
  .mo-draw { stroke:#c0563a; stroke-width:2; stroke-dasharray:6 4; opacity:.95; pointer-events:none; }
  .mo-hint { position:absolute; left:50%; top:8px; transform:translateX(-50%); z-index:20;
    background:rgba(192,86,58,.92); color:#fff; font:600 12px Georgia, serif; padding:.3rem .7rem;
    border-radius:6px; pointer-events:none; white-space:nowrap; box-shadow:0 2px 8px rgba(0,0,0,.4); }
  .mo-scale { position:absolute; transform:translate(-50%, -135%); z-index:10; display:flex;
    align-items:center; gap:.4rem; background:rgba(18,20,28,.96); border:1px solid #3a456a;
    border-radius:6px; padding:.25rem .5rem; white-space:nowrap; pointer-events:auto;
    box-shadow:0 4px 12px rgba(0,0,0,.5); }
  .mo-scale-t { color:#cdbb9a; font:600 11px Georgia, serif; max-width:120px; overflow:hidden;
    text-overflow:ellipsis; }
  .mo-scale input { width:120px; cursor:pointer; }
  .mo-scale-v { color:#e8e6df; font-size:.8rem; min-width:3.2ch; text-align:right; }
  .mo-scale-x { background:#2a2e3e; color:#e8e6df; border:1px solid #3a456a; border-radius:4px;
    cursor:pointer; font-size:.75rem; padding:.1rem .45rem; }
  .mo-scale-x:hover { background:#34416a; }
</style>
