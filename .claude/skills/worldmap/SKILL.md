---
name: worldmap
description: Render the OneLife world as a vintage Tolkien-style fantasy map — layered PNGs (parchment, ink terrain, hand-drawn icons, compass, legend) plus a coordinate metadata file (pixel positions of every place + road polygons), built for fog-of-war reveal. Use when asked to (re)generate, render, or update the world map / fantasy map / overworld map. Local to the OneLife repo.
---

# Render the OneLife world map

A deterministic renderer that turns the game's content (places + roads) into an
illustrated **vintage fantasy map** delivered as a **stack of PNG layers** plus a
**`world-map.meta.json`** with exact pixel coordinates — so the game can reveal it
**place-by-place (fog of war)**.

It composes the map itself (so coordinates are exact) and uses the free
**Pollinations** image provider only for the small **pen-and-ink icons** (cached).

## Run it

From the repo root:

```bash
python .claude/skills/worldmap/render_map.py
```

- Needs `python` + `pyyaml` + `pillow`. No DB, no running stack.
- Finds game-data automatically: `$GAME_DATA_DIR` → `$CONTENT_DIR` → sibling
  `../OneLife-KBK-mystery`. Override: `--content D:/source/OneLife-KBK-mystery`.
- Output defaults to **`web/public/worldmap/`** (so the web app serves it). Override
  with `--out DIR`.
- Flags:
  - `--no-ai` — skip Pollinations; use built-in procedural icons (offline, instant).
  - `--size WxH` — canvas size (default `3200x2400`).
- Icons are cached in `<out>/iconcache/*.png` keyed by concept — delete a file to
  regenerate just that icon, or delete the folder to redo them all.

## What it produces (in `--out`)

| File | Role |
|---|---|
| `base.png` | always-on parchment canvas (the undiscovered/“fog” backdrop) |
| `loc-<id>.png` | one transparent tile per location — terrain patch + ink icon + label |
| `road-<id>.png` | one transparent tile per road (a hand-inked wobbly line) |
| `region-<cell>.png` | a ribbon banner per region |
| `marker-*.png` | a skull at death spots, an X at the ending |
| `frame.png` | border + compass rose + title cartouche + legend (always on top) |
| `preview.png` | everything flattened — **open this to eyeball the result** |
| `thumb.jpg` | small flattened thumbnail for the in-game “Map” button |
| `world-map.meta.json` | the coordinate metadata (see below) |

`world-map.meta.json`:
- `width,height,base,frame`
- `locations[]` — `{id,name,cell,x,y,concept,tile,bbox:[x,y,w,h],reveal_nodes:[…]}`
- `roads[]` — `{id,from,to,label,tile,bbox,polygon:[[x,y]…]}` (the polygon is the
  road's hand-drawn centreline)
- `regions[]` — ribbon banners; `markers[]` — skull/X; `legend[]`

## Fog of war (how the layers are meant to be used)

Stack `base.png`, then only the **revealed** `loc-/road-/room-` tiles (positioned by
their `bbox`), then `frame.png` on top. A place is revealed once the player has
**visited a node there** (the in-game view derives the revealed set from the
player's log → `story_nodes.location`; rolling back re-fogs them).

## Tuning the look

- **Icons** are matched to each place by description keywords in `CONCEPTS` (top of
  `render_map.py`). Add a keyword/concept there to change what a place draws.
- **Terrain** per place (forest/water/fields) is in `terrain_patch()`; palette
  constants (`PARCH`, `INK`, `WATER`, `FOREST`, …) are at the top.
- Re-run after editing; check `preview.png`. The coordinates in `world-map.meta.json`
  always match the rendered tiles, so the in-game fog-of-war view stays in sync.

## After rendering
Report what was written (counts of locations/roads), and tell the user to open
`web/public/worldmap/preview.png`. If the game's content (locations/edges) changed,
re-run so the map + metadata match.
