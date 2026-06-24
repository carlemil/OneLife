<script>
  // Illustrated world map with fog of war. Stacks the static PNG layers from
  // /worldmap (rendered by .claude/skills/worldmap/render_map.py): base parchment,
  // then only the REVEALED location/road/room tiles (positioned by their bbox from
  // world-map.meta.json), then the frame on top. The revealed set comes from
  // /api/worldmap (places you've visited; rollback-safe).
  import { api } from './api.js';

  let { onClose, isAdmin = false, onGo } = $props();
  let revealAll = $state(false);     // admin-only: temporarily un-fog the whole map
  const BASE = '/worldmap';
  const DISPLAY_W = 1180;            // on-screen width; everything scales from the meta

  let meta = $state(null);
  let locs = $state(new Set());
  let nodes = $state(new Set());
  let current = $state(null);
  let error = $state('');

  let scale = $derived(meta ? DISPLAY_W / meta.width : 1);
  const px = (v) => v * (meta ? DISPLAY_W / meta.width : 1);

  async function load() {
    try {
      const res = await fetch(`${BASE}/world-map.meta.json`, { cache: 'no-cache' });
      if (!res.ok) throw new Error('map not generated yet — run the worldmap skill');
      meta = await res.json();
      const r = await api.worldMap();
      locs = new Set(r.revealed_locations || []);
      nodes = new Set(r.revealed_nodes || []);
      current = r.current_location;
    } catch (e) { error = e.message; }
  }
  load();

  const locShown = (l) => revealAll || locs.has(l.id);
  const roadShown = (r) => revealAll || (locs.has(r.from) && locs.has(r.to));
  const regionShown = (reg) => revealAll || (reg.reveal_locations || []).some((id) => locs.has(id));
  const markerShown = (m) => revealAll || (m.reveal_node && nodes.has(m.reveal_node))
    || (m.reveal_location && locs.has(m.reveal_location));
  function here() {
    if (!meta || !current) return null;
    const l = meta.locations.find((x) => x.id === current);
    return l ? { x: l.x, y: l.y } : null;
  }

  // --- click-to-travel with a "walk the path" animation ---
  const WALK_MS = 1150;
  let walk = $state(null);       // { d, len } in display px while animating
  let walking = $state(false);

  // Shortest route from one place to another along the drawn roads (BFS), returned
  // as a list of [x,y] points in meta coords. Falls back to a straight line.
  function routePoints(fromId, toId) {
    if (!meta) return null;
    const L = {};
    for (const l of meta.locations) L[l.id] = l;
    if (!L[fromId] || !L[toId]) return null;
    const adj = {};
    for (const r of meta.roads || []) {
      const poly = (r.polygon && r.polygon.length >= 2)
        ? r.polygon
        : (L[r.from] && L[r.to] ? [[L[r.from].x, L[r.from].y], [L[r.to].x, L[r.to].y]] : null);
      if (!poly) continue;
      (adj[r.from] ||= []).push({ to: r.to, poly });
      (adj[r.to] ||= []).push({ to: r.from, poly: [...poly].reverse() });
    }
    const prev = { [fromId]: null };
    const q = [fromId];
    while (q.length) {
      const cur = q.shift();
      if (cur === toId) break;
      for (const e of (adj[cur] || [])) if (!(e.to in prev)) { prev[e.to] = { from: cur, poly: e.poly }; q.push(e.to); }
    }
    if (toId in prev && prev[toId]) {
      const segs = [];
      let n = toId;
      while (prev[n]) { segs.unshift(prev[n].poly); n = prev[n].from; }
      const pts = [];
      for (const s of segs) for (const p of s) pts.push(p);
      return pts;
    }
    return [[L[fromId].x, L[fromId].y], [L[toId].x, L[toId].y]];
  }

  async function travel(l) {
    if (walking || !onGo) return;
    walking = true;
    const pts = current && current !== l.id ? routePoints(current, l.id) : null;
    if (pts && pts.length >= 2) {
      const sc = pts.map(([x, y]) => [Math.round(px(x)), Math.round(px(y))]);
      const d = 'M ' + sc.map((p) => p.join(',')).join(' L ');
      let len = 0;
      for (let i = 1; i < sc.length; i++) len += Math.hypot(sc[i][0] - sc[i - 1][0], sc[i][1] - sc[i - 1][1]);
      walk = { d, len: Math.max(len, 1) };
      await new Promise((r) => setTimeout(r, WALK_MS));
    }
    await onGo(l.id);       // travels + closes the map (this component unmounts)
    walking = false; walk = null;
  }
</script>

<div class="wm-overlay" onclick={onClose}>
  <div class="wm-card" onclick={(e) => e.stopPropagation()}>
    <div class="wm-head">
      <h2>🗺 World Map</h2>
      <span class="sub">{revealAll ? 'admin: revealing all' : `${locs.size} place(s) discovered`}</span>
      {#if isAdmin}
        <button class="link wm-toggle" class:on={revealAll} onclick={() => (revealAll = !revealAll)}
                title="Admin: temporarily reveal the whole map">👁 reveal all</button>
      {/if}
      <button class="link" onclick={() => load()} title="Refresh">⟳</button>
      <button class="link wm-x" onclick={onClose} title="Close">✕</button>
    </div>

    {#if error}
      <p class="wm-err">{error}</p>
    {:else if !meta}
      <p class="sub">Loading map…</p>
    {:else}
      <div class="wm-scroll">
        <div class="wm-stage" style="width:{px(meta.width)}px; height:{px(meta.height)}px">
          <img class="wm-full" src="{BASE}/{meta.base}" alt="" draggable="false" />

          {#each meta.roads as r}
            {#if roadShown(r)}
              <img class="wm-tile wm-anim" src="{BASE}/{r.tile}" alt="" draggable="false"
                   style="left:{px(r.bbox[0])}px; top:{px(r.bbox[1])}px; width:{px(r.bbox[2])}px; height:{px(r.bbox[3])}px" />
            {/if}
          {/each}

          {#each meta.locations as l}
            {#if locShown(l)}
              <img class="wm-tile wm-anim" src="{BASE}/{l.tile}" alt="" draggable="false"
                   style="left:{px(l.bbox[0])}px; top:{px(l.bbox[1])}px; width:{px(l.bbox[2])}px; height:{px(l.bbox[3])}px" />
              <button class="wm-hit" title={`Travel to ${l.name}`} aria-label={`Travel to ${l.name}`}
                   disabled={walking} onclick={() => travel(l)}
                   style="left:{px(l.x) - 26}px; top:{px(l.y) - 26}px"></button>
            {/if}
          {/each}

          {#each meta.regions || [] as reg}
            {#if regionShown(reg)}
              <img class="wm-tile wm-anim" src="{BASE}/{reg.tile}" alt="" draggable="false"
                   style="left:{px(reg.bbox[0])}px; top:{px(reg.bbox[1])}px; width:{px(reg.bbox[2])}px; height:{px(reg.bbox[3])}px" />
            {/if}
          {/each}

          {#each meta.markers || [] as m}
            {#if markerShown(m)}
              <img class="wm-tile wm-anim wm-mark" src="{BASE}/{m.tile}" alt="" draggable="false"
                   title={m.type === 'ending' ? 'Journey’s end' : 'Danger — death'}
                   style="left:{px(m.bbox[0])}px; top:{px(m.bbox[1])}px; width:{px(m.bbox[2])}px; height:{px(m.bbox[3])}px" />
            {/if}
          {/each}

          {#if walk}
            <svg class="wm-walk-svg" width={px(meta.width)} height={px(meta.height)}>
              <path class="wm-trail" d={walk.d} style="--len:{walk.len}; --dur:{WALK_MS}ms" />
            </svg>
            <div class="wm-walker" style="offset-path:path('{walk.d}'); --dur:{WALK_MS}ms"></div>
          {/if}

          <img class="wm-full wm-frame" src="{BASE}/{meta.frame}" alt="" draggable="false" />

          {#if here()}
            <div class="wm-here" style="left:{px(here().x)}px; top:{px(here().y)}px" title="You are here">✦</div>
          {/if}
        </div>
      </div>
      <p class="sub wm-foot">Click a place to travel there. Undiscovered places stay hidden — explore to reveal the map.</p>
    {/if}
  </div>
</div>

<style>
  .wm-overlay { position:fixed; inset:0; z-index:2000; background:rgba(8,9,14,.82);
    display:flex; align-items:center; justify-content:center; padding:1.2rem; }
  .wm-card { background:#1a1d28; border:1px solid #2a2e3e; border-radius:12px; padding:1rem;
    max-width:96vw; max-height:96vh; display:flex; flex-direction:column; }
  .wm-head { display:flex; align-items:center; gap:.7rem; margin-bottom:.6rem; }
  .wm-head h2 { margin:0; font-size:1.1rem; }
  .wm-x { margin-left:auto; }
  .wm-toggle { border:1px solid #2a2e3e; border-radius:6px; padding:.15rem .5rem; }
  .wm-toggle.on { color:#1a1d28; background:#cdbb9a; border-color:#cdbb9a; font-weight:600; }
  .wm-scroll { overflow:auto; border:1px solid #2a2e3e; border-radius:8px; background:#0d0e14; }
  .wm-stage { position:relative; }
  .wm-full { position:absolute; left:0; top:0; width:100%; height:100%; }
  .wm-frame { pointer-events:none; }
  .wm-tile { position:absolute; image-rendering:auto; }
  .wm-anim { animation:wm-reveal .6s ease both; transform-origin:center; }
  @keyframes wm-reveal { from { opacity:0; transform:scale(.6); filter:blur(2px); }
    to { opacity:1; transform:scale(1); filter:blur(0); } }
  .wm-mark { animation:wm-pop .7s cubic-bezier(.34,1.56,.64,1) both; }
  @keyframes wm-pop { 0% { opacity:0; transform:scale(0) rotate(-20deg); }
    100% { opacity:1; transform:scale(1) rotate(0); } }
  .wm-hit { position:absolute; width:52px; height:52px; border-radius:50%; border:0;
    background:transparent; cursor:pointer; }
  .wm-hit:hover { background:rgba(205,187,154,.18); }
  .wm-walk-svg { position:absolute; left:0; top:0; pointer-events:none; overflow:visible; z-index:3; }
  .wm-trail { fill:none; stroke:#b34a2e; stroke-width:5; stroke-linecap:round; stroke-linejoin:round;
    stroke-dasharray:var(--len); stroke-dashoffset:var(--len); opacity:.9;
    animation:wm-draw var(--dur) ease-out forwards; }
  @keyframes wm-draw { to { stroke-dashoffset:0; } }
  .wm-walker { position:absolute; left:0; top:0; width:18px; height:18px; border-radius:50%;
    background:#b34a2e; box-shadow:0 0 8px #000, 0 0 0 4px rgba(179,74,46,.3); pointer-events:none; z-index:4;
    offset-distance:0%; offset-anchor:center; animation:wm-walk var(--dur) ease-out forwards; }
  @keyframes wm-walk { to { offset-distance:100%; } }
  .wm-here { position:absolute; transform:translate(-50%,-50%); color:#c0563a;
    font-size:1.4rem; text-shadow:0 0 6px #000; animation:wmpulse 1.6s infinite; pointer-events:none; }
  @keyframes wmpulse { 0%,100%{opacity:.55} 50%{opacity:1} }
  .wm-sel { margin:.5rem 0 0; color:#cdbb9a; }
  .wm-foot { margin:.4rem 0 0; }
  .wm-err { color:#e0a; }
</style>
