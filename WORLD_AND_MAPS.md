# OneLife — World & Maps

Implements the world layer from [GAME_DESIGN.md](GAME_DESIGN.md) §4: a grid of
places you travel between, on top of the per-location story graph.

---

## Model

- **Cells** (`world_cells`) form a grid. Each has coordinates, a `kind`
  (`city` / `town` / `village` / `wilderness`), a `name`, a `region` string, and
  an **`arrival_node`** — the node you land on when you travel there.
- **Locations** belong to a cell (`locations.cell_id`). A cell can hold many
  locations (the design's scaling idea: a city has more, a village fewer).
- **Two map levels:**
  - *World map* — travel **between cells**. Available from any node flagged
    `world_access` (exteriors). `GET /api/world` returns the grid; `POST /api/travel`
    moves you to a cell's arrival node.
  - *City map* — navigating **within** a cell is just the normal story-graph
    choices/edges between that cell's location nodes.

Traveling changes the **place**; the **year stays 1992** (see [ATMOSPHERE.md](ATMOSPHERE.md)).
The atmosphere endpoint reads the current location's cell `region`, so the image
and Spotify soundtrack shift as you move — `Cathedral Square · Lund, Skåne · 1992`
sounds different from `Killebäckskolan · Södra Sandby, Skåne · 1992`.

---

## Current world (content/world.yaml)

```
        x=1            x=2
 y=1                   Skåne Woods (wilderness)
 y=2    Lund (city)    Södra Sandby (village, the school)
```

- **Södra Sandby** — the school (Killebäckskolan) + Sandby Street (exterior,
  world-access). Step out the school's front doors to reach the street.
- **Lund** — Cathedral Square (world-access) + Lund Station (reached from the
  square; a second location, demonstrating intra-cell navigation).
- **Skåne Woods** — a single ominous Forest Path (world-access).

You leave the school → Sandby Street → open the world map → travel to Lund or the
woods → travel onward or back. The boiler-room ending is still reached the same
way (through the school), so the spine stays completable.

---

## Authoring

Cells are authored in YAML like everything else (see [AUTHORING.md](AUTHORING.md)):

```yaml
cells:
  - id: lund
    grid_x: 1
    grid_y: 2
    name: Lund
    kind: city
    region: "Lund, Skåne"
    arrival_node: lund-square
locations:
  - id: lund-square
    name: Cathedral Square
    cell: lund            # location belongs to a cell
nodes:
  - id: lund-square
    type: location
    location: lund-square
    world_access: true    # can open the world map here
```

The **spine lint is travel-aware**: it treats every `world_access` node as able
to reach every cell's arrival node, so cross-cell content isn't flagged as
unreachable or as a trap, while still proving an ending is always reachable.

---

## Deferred

- A richer **city map** UI (clickable locations within a cell) — today intra-cell
  movement uses the normal choice buttons.
- Travel **gating** (distance, time, conditions) — today any world-access node can
  reach any cell. Discovery/fog-of-war (only show visited/known cells).
- World scale: the design targets ~100 locations for a big city and ~10 for a
  village; the slice ships a handful to prove the structure.
