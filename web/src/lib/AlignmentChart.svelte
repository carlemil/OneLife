<script>
  // D&D-style alignment chart: a 3×3 grid (Lawful/Neutral/Chaotic × Good/Neutral/
  // Evil) showing the player's current position and a fading trail of where they've
  // drifted over time. Pure SVG, no deps. The `compare` prop (a second {current,
  // history}) is accepted for a future "compare against another player" overlay.
  let { current = null, history = [], size = 220, compare = null } = $props();

  // Plot maths: value v∈[-1,1] → pixel. law_chaos is x (right = Lawful), good_evil
  // is y (up = Good, so y is inverted). Grid spans 5..95 inside a 100×100 box.
  const clamp = (v) => (v < -1 ? -1 : v > 1 ? 1 : v);
  const px = (lc) => 50 + clamp(lc ?? 0) * 45;
  const py = (ge) => 50 - clamp(ge ?? 0) * 45;
  const TH = 50 - 0.333 * 45;        // ±0.33 threshold line (≈35 / 65)

  // Older positions fade; cap the rendered trail so long games stay light.
  const trail = $derived((history ?? []).slice(-40));
  const pts = $derived(trail.map((h, i) => ({
    x: px(h.law_chaos), y: py(h.good_evil),
    o: 0.18 + 0.82 * ((i + 1) / Math.max(trail.length, 1)),
  })));
  const poly = $derived(pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '));

  const cmpTrail = $derived((compare?.history ?? []).slice(-40));
  const cmpPoly = $derived(cmpTrail.map(
    (h) => `${px(h.law_chaos).toFixed(1)},${py(h.good_evil).toFixed(1)}`).join(' '));

  // 3×3 cell labels, by (column = law axis, row = good axis).
  const CELLS = [
    ['CG', 'NG', 'LG'],   // top row = Good
    ['CN', 'TN', 'LN'],   // middle row = Neutral
    ['CE', 'NE', 'LE'],   // bottom row = Evil
  ];
  const COLX = [17.5, 50, 82.5];
  const ROWY = [17.5, 50, 82.5];
</script>

<div class="ac" style={`width:${size}px`}>
  <svg viewBox="-14 -12 128 130" role="img" aria-label="Alignment chart">
    <!-- grid -->
    <rect class="frame" x="0" y="0" width="100" height="100" rx="2" />
    <line class="grid" x1={TH} y1="0" x2={TH} y2="100" />
    <line class="grid" x1={100 - TH} y1="0" x2={100 - TH} y2="100" />
    <line class="grid" x1="0" y1={TH} x2="100" y2={TH} />
    <line class="grid" x1="0" y1={100 - TH} x2="100" y2={100 - TH} />

    <!-- cell names -->
    {#each CELLS as row, r}
      {#each row as name, c}
        <text class="cell" x={COLX[c]} y={ROWY[r] + 2} text-anchor="middle">{name}</text>
      {/each}
    {/each}

    <!-- axis labels -->
    <text class="axis" x="50" y="-4" text-anchor="middle">Good</text>
    <text class="axis" x="50" y="110" text-anchor="middle">Evil</text>
    <text class="axis" x="-6" y="50" text-anchor="middle" transform="rotate(-90 -6 50)">Chaotic</text>
    <text class="axis" x="106" y="50" text-anchor="middle" transform="rotate(90 106 50)">Lawful</text>

    <!-- compare trail (future) -->
    {#if cmpTrail.length}
      <polyline class="cmpline" points={cmpPoly} />
      {#if compare?.current}
        <circle class="cmpmark" cx={px(compare.current.law_chaos)} cy={py(compare.current.good_evil)} r="3" />
      {/if}
    {/if}

    <!-- drift trail -->
    {#if pts.length}
      <polyline class="trail" points={poly} />
      {#each pts as p}
        <circle class="dot" cx={p.x} cy={p.y} r="1.6" style={`opacity:${p.o}`} />
      {/each}
    {/if}

    <!-- current marker -->
    {#if current}
      <circle class="here" cx={px(current.law_chaos)} cy={py(current.good_evil)} r="3.6" />
    {/if}
  </svg>
  {#if current}
    <p class="lbl">{current.label}</p>
  {/if}
</div>

<style>
  .ac { margin:.2rem auto; max-width:100%; }
  svg { width:100%; height:auto; display:block; overflow:visible; }
  .frame { fill:rgba(20,22,30,.5); stroke:#3a3f52; stroke-width:.8; }
  .grid { stroke:#2f3447; stroke-width:.6; }
  .cell { fill:#5a6075; font:600 5px Georgia, serif; opacity:.5; }
  .axis { fill:#cdbb9a; font:600 6px Georgia, serif; }
  .trail { fill:none; stroke:#7a7ad0; stroke-width:1; opacity:.55; stroke-linejoin:round; }
  .dot { fill:#9a9ae0; }
  .here { fill:#c0563a; stroke:#fff2e8; stroke-width:1; }
  .cmpline { fill:none; stroke:#5a9a6a; stroke-width:1; opacity:.5; stroke-dasharray:2 2; }
  .cmpmark { fill:#5a9a6a; stroke:#eafaea; stroke-width:.8; }
  .lbl { margin:.25rem 0 0; text-align:center; color:#e8e6df; font:600 .85rem Georgia, serif;
    text-transform:capitalize; }
</style>
