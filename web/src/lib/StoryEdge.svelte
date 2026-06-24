<script>
  import { BaseEdge, getBezierPath } from '@xyflow/svelte';
  // data carries { rec, pairIndex, pairCount, sign } — see buildFlow in App.svelte.
  let {
    sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
    markerEnd, style, label, data,
  } = $props();

  const bezier = $derived(getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  }));

  // Parallel/antiparallel edges (same node pair) share a midpoint, so their labels
  // stack exactly. Spread the group's labels to distinct points ALONG the edge —
  // these labels are long, so an along-edge offset separates them where a small
  // perpendicular nudge wouldn't. `sign` keeps the offset consistent for a pair's
  // reversed twin so the two never land on the same spot. Single edges keep their
  // natural bezier midpoint.
  const lbl = $derived.by(() => {
    const cnt = data?.pairCount ?? 1;
    if (cnt < 2) return { x: bezier[1], y: bezier[2] };
    const idx = data?.pairIndex ?? 0, sign = data?.sign ?? 1;
    let f = 0.5 + (idx - (cnt - 1) / 2) * 0.2;          // fraction from the canonical source
    f = Math.min(0.88, Math.max(0.12, f));
    const own = sign > 0 ? f : 1 - f;                  // fraction along this edge's own direction
    return { x: sourceX + (targetX - sourceX) * own, y: sourceY + (targetY - sourceY) * own };
  });
</script>

<BaseEdge path={bezier[0]} {label} labelX={lbl.x} labelY={lbl.y} {markerEnd} {style} />
