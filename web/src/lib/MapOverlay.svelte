<script>
  // The game's single overview map: location icons placed on a parchment at their
  // story-graph editor positions. Replaces the old per-cell + world maps. Clicking
  // a reachable icon plays the walk animation, then calls onPick(item) — the parent
  // dispatches a walk (within the cell) or a travel (to an adjacent cell) from the
  // item's `action`/`target`. Coordinates are normalized 0..1 against the parchment.
  import { contentAsset } from './api.js';
  import { roadPath } from './maputil.js';   // shared with the admin map editor
  import { fade } from 'svelte/transition';
  import './map.css';                        // shared .map-* styles (also used by MapEditor)

  let {
    block,                 // { image, nodes:[{id,title,icon,x,y,current,reachable,action,target}], roads:[{from,to}] }
    onPick,                // (item) => go there (called after the walk animation)
    onClose,               // () => close the map
    busy = false,
  } = $props();

  const WALK_MS = 1150;
  let stageEl = $state(null);           // the parchment stage (for pointer coords)
  let cw = $state(0), ch = $state(0);   // rendered parchment size in px
  let imgOk = $state(true);
  let walk = $state(null);              // { d } while animating
  let walking = $state(false);
  let hovered = $state(null);           // id of the nearest icon to the pointer

  const nodes = $derived(block?.nodes ?? []);
  const roads = $derived(block?.roads ?? []);
  const byId = $derived(Object.fromEntries(nodes.map((n) => [n.id, n])));
  const cx = (n) => n.x * cw, cy = (n) => n.y * ch;
  const hoverReachable = $derived(!!(hovered && byId[hovered]?.reachable));

  // No per-icon hitboxes: the whole map is one surface, and the icon whose centre is
  // nearest the pointer is the selection (Voronoi-style). Highlight any nearest icon;
  // clicking navigates only if it's reachable (pick() guards that).
  function nearestNode(clientX, clientY) {
    if (!stageEl || !cw || !ch || !nodes.length) return null;
    const r = stageEl.getBoundingClientRect();
    const px = clientX - r.left, py = clientY - r.top;
    let best = null, bestD = Infinity;
    for (const n of nodes) {
      const dx = px - n.x * cw, dy = py - n.y * ch, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = n; }
    }
    return best;
  }
  function onMapMove(e) { hovered = nearestNode(e.clientX, e.clientY)?.id ?? null; }
  function onMapLeave() { hovered = null; }
  function onMapClick(e) { const n = nearestNode(e.clientX, e.clientY); if (n) pick(n); }

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
</script>

<div class="mo-modal" role="dialog" aria-label="Map" onclick={(e) => e.target === e.currentTarget && onClose?.()}>
  <div class="mo-card">
    <div class="mo map-stage" bind:this={stageEl} bind:clientWidth={cw} bind:clientHeight={ch}
         class:canclick={hoverReachable} role="presentation"
         onpointermove={onMapMove} onpointerleave={onMapLeave} onclick={onMapClick}
         style={imgOk ? '' : 'width:min(900px,90vw); aspect-ratio:4/3'}>
      <img class="map-bg" src={contentAsset(block.image)} alt="" draggable="false"
           onload={() => (imgOk = true)} onerror={() => (imgOk = false)} />
      {#if !imgOk}
        <div class="mo-missing">map background missing<br /><code>{block.image}</code></div>
      {/if}

      {#if cw > 0 && ch > 0}
        <svg class="map-svg" width={cw} height={ch} viewBox={`0 0 ${cw} ${ch}`}>
          <defs>
            {#each roads as r, i}
              {#if byId[r.from] && byId[r.to]}
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
            {#if byId[r.from] && byId[r.to]}
              <path class="map-road" fill="none" stroke={`url(#moroad${i})`} d={roadPath(byId, r, cw, ch)} />
            {/if}
          {/each}
          {#if walk}
            <path class="mo-trail" d={walk.d} style={`--dur:${WALK_MS}ms`} pathLength="1" />
          {/if}
        </svg>

        {#each nodes as n}
          <!-- Pure visual: pointer handling lives on the stage (nearest-icon), so the
               icons themselves are inert. -->
          <div class="mo-node" class:reachable={n.reachable} class:current={n.current}
               class:disabled={!n.reachable && !n.current} class:hovered={hovered === n.id}
               style={`left:${cx(n)}px; top:${cy(n)}px`}>
            <img class="map-icon" style={`--map-scale:${n.scale ?? 1}`}
                 src={contentAsset(n.icon)} alt={n.title} draggable="false" />
          </div>
        {/each}

        {#if hovered && byId[hovered]}
          <!-- Keyed on the title so a change from one location to another swaps the
               element, crossfading the old text out while the new fades in (both
               absolutely positioned, so they overlap). -->
          {#key byId[hovered].title}
            <!-- |global so the fade still plays when the surrounding {#if hovered}
                 toggles (moving between icons briefly clears `hovered`); a plain
                 local transition would be suppressed by that ancestor change. -->
            <div class="map-label" in:fade|global={{ duration: 200 }} out:fade|global={{ duration: 200 }}>{byId[hovered].title}</div>
          {/key}
        {/if}

        {#if walk}
          <div class="mo-walker" style={`offset-path:path('${walk.d}'); --dur:${WALK_MS}ms`}></div>
        {/if}
      {/if}
    </div>
  </div>
</div>

<style>
  /* A layer above the rest of the game UI, with a thin transparent border. */
  .mo-modal { position:fixed; inset:0; z-index:50; display:flex; align-items:center;
    justify-content:center; background:rgba(0,0,0,.72); padding:1.5rem; }
  /* The card shrinks to the parchment (flex item → content size). The parchment stage
     (.map-stage), backdrop (.map-bg) and roads (.map-svg) are sized in the shared
     ./map.css to the SAME rule the editor uses, so the map is the same size in both.
     overflow:hidden guarantees no scrollbar (kills the appear/disappear loop). */
  .mo-card { position:relative; max-width:var(--map-max-w); max-height:var(--map-max-h);
    overflow:hidden; border-radius:10px; line-height:0; display:flex; }
  .mo-missing { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
    justify-content:center; text-align:center; color:#9a9ab0; font-size:.9rem; line-height:1.4; }
  /* The stage is one pointer surface (nearest-icon selection): a pointer cursor when
     the nearest icon is reachable. */
  .mo.canclick { cursor:pointer; }

  /* Icons are pure visuals — inert; the stage handles all pointer input. */
  .mo-node { position:absolute; transform:translate(-50%, -50%); pointer-events:none; }
  .mo-node.disabled { opacity:.45; filter:grayscale(.5); }
  /* The nearest reachable icon grows 5% (animated via .map-icon's transition). */
  .mo-node.reachable.hovered .map-icon { transform:scale(1.05); filter:drop-shadow(0 3px 6px rgba(0,0,0,.6)); }
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
</style>
