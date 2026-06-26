<script>
  // The game's single overview map: location icons placed on a parchment at their
  // story-graph editor positions. Replaces the old per-cell + world maps. Clicking
  // a reachable icon plays the walk animation, then calls onPick(item) — the parent
  // dispatches a walk (within the cell) or a travel (to an adjacent cell) from the
  // item's `action`/`target`. Coordinates are normalized 0..1 against the parchment.
  import { contentAsset } from './api.js';
  import { roadPath } from './maputil.js';   // shared with the admin map editor

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
         style={imgOk ? '' : 'aspect-ratio:4/3'}>
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
              <path class="mo-road" fill="none" stroke={`url(#moroad${i})`} d={roadPath(byId, r, cw, ch)} />
            {/if}
          {/each}
          {#if walk}
            <path class="mo-trail" d={walk.d} style={`--dur:${WALK_MS}ms`} pathLength="1" />
          {/if}
        </svg>

        {#each nodes as n}
          <div class="mo-node" class:reachable={n.reachable} class:current={n.current}
               class:disabled={!n.reachable && !n.current}
               style={`left:${cx(n)}px; top:${cy(n)}px`}
               role="button" tabindex={n.reachable ? 0 : -1} aria-label={`Go to ${n.title}`}
               onclick={() => pick(n)} onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && pick(n)}
               onmouseenter={() => (hovered = n.id)} onmouseleave={() => (hovered === n.id && (hovered = null))}>
            <img class="mo-icon" style={`--mo-scale:${n.scale ?? 1}`}
                 src={contentAsset(n.icon)} alt={n.title} draggable="false" />
          </div>
        {/each}

        {#if hovered && byId[hovered]}
          <div class="mo-hover-lbl">{byId[hovered].title}</div>
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
  .mo-card { position:relative; width:min(1100px, 96vw); max-height:94vh; overflow:auto;
    border:1px solid transparent; border-radius:10px; line-height:0; }
  .mo { position:relative; width:100%; }
  .mo-bg { display:block; width:100%; height:auto; border-radius:10px; }
  .mo-missing { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
    justify-content:center; text-align:center; color:#9a9ab0; font-size:.9rem; line-height:1.4; }
  .mo-svg { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
  .mo-road { stroke-width:4; stroke-linecap:round; }

  .mo-node { position:absolute; transform:translate(-50%, -50%); }
  .mo-icon { width:calc(clamp(56px, 9.8vw, 101px) * var(--mo-scale, 1)); height:auto; display:block; line-height:0;
    filter:drop-shadow(0 2px 3px rgba(0,0,0,.5)); transition:transform .12s, filter .12s; }
  /* The hovered icon's name, shown large and centred ~10% down from the top —
     identical to the admin map editor. */
  .mo-hover-lbl { position:absolute; left:50%; top:10%; transform:translate(-50%, -50%);
    z-index:15; pointer-events:none; white-space:nowrap; font:700 clamp(20px, 3.2vw, 38px) Georgia, serif;
    color:#2e2114; text-shadow:0 1px 0 rgba(255,250,240,.9), 0 2px 10px rgba(255,248,235,.7); }
  .mo-node.disabled { opacity:.45; filter:grayscale(.5); }
  .mo-node.reachable { cursor:pointer; }
  .mo-node.reachable:hover .mo-icon { transform:scale(1.12); filter:drop-shadow(0 3px 6px rgba(0,0,0,.6)); }
  .mo-node.current .mo-icon { filter:drop-shadow(0 0 0 #c0563a) drop-shadow(0 0 6px rgba(192,86,58,.9));
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
