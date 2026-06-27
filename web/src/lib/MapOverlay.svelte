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
  let cw = $state(0), ch = $state(0);   // rendered parchment size in px
  let imgOk = $state(true);
  let walk = $state(null);              // { d } while animating
  let walking = $state(false);
  let hovered = $state(null);           // id of the icon under the mouse (big label)

  const nodes = $derived(block?.nodes ?? []);
  const roads = $derived(block?.roads ?? []);
  const byId = $derived(Object.fromEntries(nodes.map((n) => [n.id, n])));
  const cx = (n) => n.x * cw, cy = (n) => n.y * ch;

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
    <div class="mo" bind:clientWidth={cw} bind:clientHeight={ch}
         style={imgOk ? '' : 'width:min(900px,90vw); aspect-ratio:4/3'}>
      <img class="mo-bg" src={contentAsset(block.image)} alt="" draggable="false"
           onload={() => (imgOk = true)} onerror={() => (imgOk = false)} />
      {#if !imgOk}
        <div class="mo-missing">map background missing<br /><code>{block.image}</code></div>
      {/if}

      {#if cw > 0 && ch > 0}
        <svg class="mo-svg" width={cw} height={ch} viewBox={`0 0 ${cw} ${ch}`}>
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
          <!-- The icon's visual. When it has a `hit` hotspot the icon itself is
               inert (pointer-events:none, via .withhit) and the square below is the
               sole hover/click target; otherwise the icon is the target as before. -->
          <div class="mo-node" class:reachable={n.reachable} class:current={n.current}
               class:disabled={!n.reachable && !n.current} class:hovered={hovered === n.id}
               class:withhit={!!n.hit}
               style={`left:${cx(n)}px; top:${cy(n)}px`}
               role="button" tabindex={n.reachable && !n.hit ? 0 : -1} aria-label={`Go to ${n.title}`}
               onclick={() => pick(n)} onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && pick(n)}
               onmouseenter={() => (hovered = n.id)} onmouseleave={() => (hovered === n.id && (hovered = null))}>
            <img class="map-icon" style={`--map-scale:${n.scale ?? 1}`}
                 src={contentAsset(n.icon)} alt={n.title} draggable="false" />
          </div>
          {#if n.hit}
            <div class="mo-hit" class:reachable={n.reachable} class:disabled={!n.reachable && !n.current}
                 style={`left:${cx(n)}px; top:${cy(n)}px; width:${n.hit * cw}px; height:${n.hit * cw}px`}
                 role="button" tabindex={n.reachable ? 0 : -1} aria-label={`Go to ${n.title}`}
                 onclick={() => pick(n)} onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && pick(n)}
                 onmouseenter={() => (hovered = n.id)} onmouseleave={() => (hovered === n.id && (hovered = null))}></div>
          {/if}
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
  /* The card shrinks to the image (flex item → content size). The image is capped by
     BOTH width and height with width/height:auto, so it always fits the viewport with
     its aspect preserved — no width-driven height that could overflow. overflow:hidden
     guarantees no scrollbar, killing the appear/disappear feedback loop. */
  .mo-card { position:relative; max-width:min(1100px, 96vw); max-height:94vh; overflow:hidden;
    border-radius:10px; line-height:0; display:flex; }
  .mo { position:relative; }
  .mo-bg { display:block; width:auto; height:auto; max-width:min(1100px, 96vw); max-height:94vh;
    border-radius:10px; }
  .mo-missing { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
    justify-content:center; text-align:center; color:#9a9ab0; font-size:.9rem; line-height:1.4; }
  .mo-svg { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
  /* Icon image (.map-icon), hover label (.map-label) and roads (.map-road) are styled
     in the shared ./map.css; only player-specific behaviour lives here. */

  .mo-node { position:absolute; transform:translate(-50%, -50%); }
  .mo-node.disabled { opacity:.45; filter:grayscale(.5); }
  .mo-node.reachable { cursor:pointer; }
  /* When a hotspot square owns the pointer, the icon is inert; its hover-grow is
     then driven by the shared `hovered` state instead of the icon's own :hover. */
  .mo-node.withhit { pointer-events:none; }
  /* Hover grows the icon 5% (animated via .map-icon's transition:transform). */
  .mo-node.reachable:hover .map-icon,
  .mo-node.reachable.hovered .map-icon { transform:scale(1.05); filter:drop-shadow(0 3px 6px rgba(0,0,0,.6)); }
  /* The hotspot hit-area: invisible until hovered, then a faint ink outline. */
  .mo-hit { position:absolute; transform:translate(-50%, -50%); z-index:6; box-sizing:border-box;
    border:1.5px dashed transparent; border-radius:5px; transition:border-color .15s, background .15s; }
  .mo-hit.reachable { cursor:pointer; }
  .mo-hit:hover { border-color:rgba(46,33,20,.5); background:rgba(46,33,20,.06); }
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
