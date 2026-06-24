<script>
  // Illustrated world map with fog of war. Stacks the static PNG layers from
  // /worldmap (rendered by .claude/skills/worldmap/render_map.py): base parchment,
  // then only the REVEALED location/road/room tiles (positioned by their bbox from
  // world-map.meta.json), then the frame on top. The revealed set comes from
  // /api/worldmap (places you've visited; rollback-safe).
  import { api } from './api.js';

  let { onClose } = $props();
  const BASE = '/worldmap';
  const DISPLAY_W = 1180;            // on-screen width; everything scales from the meta

  let meta = $state(null);
  let locs = $state(new Set());
  let nodes = $state(new Set());
  let current = $state(null);
  let error = $state('');
  let sel = $state(null);

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

  const roadShown = (r) => locs.has(r.from) && locs.has(r.to);
  const regionShown = (reg) => (reg.reveal_locations || []).some((id) => locs.has(id));
  const markerShown = (m) => (m.reveal_node && nodes.has(m.reveal_node))
    || (m.reveal_location && locs.has(m.reveal_location));
  const building = $derived(meta?.building);
  const schoolShown = $derived(building ? locs.has(building.id) : false);
  function here() {
    if (!meta || !current) return null;
    const l = meta.locations.find((x) => x.id === current);
    return l ? { x: l.x, y: l.y } : null;
  }
</script>

<div class="wm-overlay" onclick={onClose}>
  <div class="wm-card" onclick={(e) => e.stopPropagation()}>
    <div class="wm-head">
      <h2>🗺 World Map</h2>
      <span class="sub">{locs.size} place(s) discovered</span>
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
            {#if !l.is_building && locs.has(l.id)}
              <img class="wm-tile wm-anim" src="{BASE}/{l.tile}" alt="" draggable="false"
                   style="left:{px(l.bbox[0])}px; top:{px(l.bbox[1])}px; width:{px(l.bbox[2])}px; height:{px(l.bbox[3])}px" />
              <button class="wm-hit" title={l.name} aria-label={l.name} onclick={() => (sel = l)}
                   style="left:{px(l.x) - 26}px; top:{px(l.y) - 26}px"></button>
            {/if}
          {/each}

          {#if building && schoolShown}
            <img class="wm-tile wm-anim" src="{BASE}/{building.shell}" alt="" draggable="false"
                 style="left:{px(building.footprint[0])}px; top:{px(building.footprint[1])}px; width:{px(building.footprint[2])}px; height:{px(building.footprint[3])}px" />
            {#each building.rooms as rm}
              {#if nodes.has(rm.node_id)}
                <img class="wm-tile wm-anim" src="{BASE}/{rm.tile}" alt="" draggable="false"
                     style="left:{px(rm.bbox[0])}px; top:{px(rm.bbox[1])}px; width:{px(rm.bbox[2])}px; height:{px(rm.bbox[3])}px" />
              {/if}
            {/each}
          {/if}

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

          <img class="wm-full wm-frame" src="{BASE}/{meta.frame}" alt="" draggable="false" />

          {#if here()}
            <div class="wm-here" style="left:{px(here().x)}px; top:{px(here().y)}px" title="You are here">✦</div>
          {/if}
        </div>
      </div>
      {#if sel}<p class="wm-sel"><b>{sel.name}</b> <button class="link" onclick={() => (sel = null)}>×</button></p>{/if}
      <p class="sub wm-foot">Undiscovered places stay hidden — explore to reveal the map. The school reveals room by room.</p>
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
  .wm-here { position:absolute; transform:translate(-50%,-50%); color:#c0563a;
    font-size:1.4rem; text-shadow:0 0 6px #000; animation:wmpulse 1.6s infinite; pointer-events:none; }
  @keyframes wmpulse { 0%,100%{opacity:.55} 50%{opacity:1} }
  .wm-sel { margin:.5rem 0 0; color:#cdbb9a; }
  .wm-foot { margin:.4rem 0 0; }
  .wm-err { color:#e0a; }
</style>
