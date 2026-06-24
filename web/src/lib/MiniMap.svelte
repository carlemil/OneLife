<script>
  // Small live "minimap" shown beside the location image. It composites the SAME
  // layered tiles as the full world map (base + only the revealed location/road/
  // region/marker tiles + frame), at a small scale — so it always reflects the real
  // map's current fog-of-war state. `revealed` is refreshed by the parent whenever
  // the world state changes, which re-composites this. Click opens the full map.
  let { revealed, onOpen } = $props();
  const BASE = '/worldmap';
  const HEIGHT = 140;                    // matches the location banner height

  let meta = $state(null);
  (async () => {
    try { meta = await (await fetch(`${BASE}/world-map.meta.json`, { cache: 'no-cache' })).json(); }
    catch { /* map not generated yet */ }
  })();

  const s = (v) => v * (meta ? HEIGHT / meta.height : 1);
  const locShown = (l) => revealed?.locations?.has(l.id);
  const roadShown = (r) => revealed?.locations?.has(r.from) && revealed?.locations?.has(r.to);
  const regionShown = (reg) => (reg.reveal_locations || []).some((id) => revealed?.locations?.has(id));
  const markerShown = (m) => (m.reveal_node && revealed?.nodes?.has(m.reveal_node))
    || (m.reveal_location && revealed?.locations?.has(m.reveal_location));
</script>

<button class="mm" onclick={onOpen} title="Open the world map"
        style="height:{HEIGHT}px; width:{meta ? s(meta.width) : HEIGHT * 1.33}px">
  {#if meta}
    <img class="mm-l mm-full" src="{BASE}/{meta.base}" alt="" draggable="false" />
    {#each meta.roads as r}
      {#if roadShown(r)}
        <img class="mm-l" src="{BASE}/{r.tile}" alt="" draggable="false"
             style="left:{s(r.bbox[0])}px; top:{s(r.bbox[1])}px; width:{s(r.bbox[2])}px; height:{s(r.bbox[3])}px" />
      {/if}
    {/each}
    {#each meta.locations as l}
      {#if locShown(l)}
        <img class="mm-l" src="{BASE}/{l.tile}" alt="" draggable="false"
             style="left:{s(l.bbox[0])}px; top:{s(l.bbox[1])}px; width:{s(l.bbox[2])}px; height:{s(l.bbox[3])}px" />
      {/if}
    {/each}
    {#each meta.regions || [] as reg}
      {#if regionShown(reg)}
        <img class="mm-l" src="{BASE}/{reg.tile}" alt="" draggable="false"
             style="left:{s(reg.bbox[0])}px; top:{s(reg.bbox[1])}px; width:{s(reg.bbox[2])}px; height:{s(reg.bbox[3])}px" />
      {/if}
    {/each}
    {#each meta.markers || [] as m}
      {#if markerShown(m)}
        <img class="mm-l" src="{BASE}/{m.tile}" alt="" draggable="false"
             style="left:{s(m.bbox[0])}px; top:{s(m.bbox[1])}px; width:{s(m.bbox[2])}px; height:{s(m.bbox[3])}px" />
      {/if}
    {/each}
    <img class="mm-l mm-full" src="{BASE}/{meta.frame}" alt="" draggable="false" />
  {/if}
  <span class="mm-label">Map</span>
</button>

<style>
  .mm { position:relative; flex:0 0 auto; padding:0; border:1px solid #2a2e3e; border-radius:8px;
    overflow:hidden; background:#0d0e14; cursor:pointer; }
  .mm:hover { border-color:#cdbb9a; }
  .mm-l { position:absolute; }
  .mm-full { left:0; top:0; width:100%; height:100%; }
  .mm-label { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    font-size:1.1rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#f3e9d2;
    text-shadow:0 1px 4px #000, 0 0 12px #000; background:rgba(20,12,6,.18); transition:background .15s; }
  .mm:hover .mm-label { background:rgba(20,12,6,.04); }
</style>
