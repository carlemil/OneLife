// Shared road geometry for the map (MapOverlay) — used identically by its player view
// and its admin edit mode, so the editor is a faithful preview of the player map.

// A road is a Catmull-Rom spline through its endpoints + any saved waypoints
// (`r.points`, set by the editor's "Redraw roads"); with no waypoints it's a
// straight line. Inputs are normalized 0..1; `cw`/`ch` scale to pixels.
export function roadPath(byId, r, cw, ch) {
  const a = byId[r.from], b = byId[r.to];
  if (!a || !b) return '';
  const pts = [[a.x, a.y], ...(r.points ?? []), [b.x, b.y]].map(([x, y]) => [x * cw, y * ch]);
  if (pts.length === 2) return `M ${pts[0][0]},${pts[0][1]} L ${pts[1][0]},${pts[1][1]}`;
  let d = `M ${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}
