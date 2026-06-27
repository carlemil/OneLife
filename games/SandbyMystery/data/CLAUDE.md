# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this folder is

This is the **Sandby Mystery game-data set** for the OneLife engine — a dark text
adventure set in 1992 southern Sweden (Skåne), opening at Killebäckskolan in Södra
Sandby. It contains **no code**, only authored content as YAML.

It lives **in-repo** at `games/SandbyMystery/data/` within the OneLife engine repo
(repo root on this machine: `D:\source\OneLife`). The engine is data-agnostic: it
mounts this folder at `/content` and seeds it into Postgres. This is the **default
dataset** (`GAME_DATA_DIR=./games/SandbyMystery/data`); point the engine at another
dataset by overriding `GAME_DATA_DIR` in the repo-root `.env`.

Authoring rules and the full field reference live in the engine docs at the repo root:
`../../../AUTHORING.md`, `../../../STORY_AND_PUZZLES.md`, `../../../AI_DIALOGUE_GATES.md`.

## Commands

There is no build step here. Validate and load content **from the repo root**
(`../../..`, i.e. `D:\source\OneLife`):

```bash
make lint     # validate this data — schema, references, spine reachability (no DB)
make seed     # validate + load into Postgres (idempotent upsert)
make up       # build + start everything (also auto-seeds on API startup)
make reset    # wipe the DB volume and start fresh
```

On Windows/PowerShell without `make`, use the underlying commands (run from the repo root):

```bash
docker compose run --rm --no-deps api python -m app.seed --lint   # lint
docker compose run --rm api python -m app.seed                    # seed
```

**Always `make lint` after editing YAML** — it is the only check this folder has.

## How the content model works

Every `*.yaml` file may contain any subset of the top-level lists below, and **all
files are merged** — so the file an entity lives in is purely organizational. This
dataset is organized **by type**, one file per kind:

| File(s) | Top-level list | Defines |
|---|---|---|
| `arcs.yaml` | `arcs` | Narrative threads; `main` is `is_spine: true` |
| `cells.yaml` | `cells` | The travel grid (one cell per place, with `arrival_node`) |
| `locations.yaml` | `locations` | Places within cells |
| `characters.yaml` | `characters` | NPCs — `id`, `name`, optional `reveal_name`, `persona` |
| `nodes-{narration,location,gate,puzzle,death,ending}.yaml` | `nodes` | Story beats by node type, with **edges embedded inline** |
| `gates.yaml` | `gates` | AI-dialogue specs referenced by `gate` nodes |
| `puzzles.yaml` | `puzzles` | Puzzle specs referenced by `puzzle` nodes |
| `clues.yaml` | `clues` | Woven clue fragments that feed a puzzle's `required_clues` |
| `edges.yaml` | `edges` | **Standalone** edges (explicit `from`) attaching routes to nodes in other files |

The `old/` directory holds the **previous by-area layout** (`killebackskolan.yaml`,
`lund.yaml`, `malmo.yaml`, `sandby.yaml`, `marta.yaml`, `npcs.yaml`, …) kept for
reference; it is **not** seeded. `images/maps/` holds generated map art.

### Nodes and edges

- `node.type` is one of `narration | location | gate | puzzle | death | ending`.
- A `gate` node sets `gate: <gate-id>`; a `puzzle` node sets `puzzle: <puzzle-id>`.
- Edges normally live **inside** their source node (`from` is implied). To add a
  route from a node defined in another file, use a top-level `edges:` entry in
  `edges.yaml` with explicit `from`.
- `media: {image_theme, music_theme, real_place, reference_image}` drives generated
  art and the atmosphere soundtrack. `world_access: true` marks travel-hub nodes.
- `body_variants` swap a node's body text based on a condition (`when: {all: [...]}`).
- `sort_order` orders choices in the UI; "leave/back" edges conventionally use a
  high value (e.g. `9`) so they sort last.
- `pos: {x, y}` is the node's position in the story-graph editor (pixel coords);
  dragging a node in the read-only graph editor writes this line back.
- `map: {x, y, rx, ry, scale}` places a node/cell/location on the hand-drawn overview
  map (normalized 0–1 coords).

### The condition / effect DSL (same one the engine evaluates)

Edge `conditions` use `{all: [...]}` / `{any: [...]}` / `{not: {...}}` over predicates:
`{flag_set: X}`, `{node_visited: X}`, `{gate_passed: X}`, `{puzzle_solved: X}`.
`effects` include `progress_points: N`, `set_flag: X`, `log: "..."`, and on gates/puzzles
`story_node_next`, `write_memory: {owner, content, leakable}`. Edges may carry
`danger: N` (death risk that the UI foreshadows).

### Gates (AI dialogue) and puzzles

- A **gate** specifies `intent`, `criteria` (named checks), a `success_rule`
  (boolean over criterion ids), a `knowledge_boundary` (`knows` / `refuses` / `tone`),
  a `hint_ladder`, `mercy_after_attempts`, and `on_success` effects. `write_memory`
  with `leakable: true` enables cross-character memory leakage.
- A **puzzle** has `type` (combination/riddle/wordlock/anagram…), a `solution`
  (`{kind: exact, value}` for case-insensitive match, or `{kind: set, set: [...]}`
  for any accepted answer), an escalating `hint_ladder` (last rung ≈ giveaway), and
  optional `required_clues` resolved against `clues` entries.

## What the lint enforces (and doesn't)

Errors (fail the build): duplicate ids; references to unknown
locations/arcs/gates/puzzles/characters/clues; edges to unknown nodes; not exactly
**one `entry` node**; **no `ending` node**; and **traps** — a node reachable from
entry that cannot reach any ending (`death` nodes are exempt because rollback is
their escape).

Warnings (non-blocking): a gate with no `mercy_after_attempts` (soft-lock risk);
a puzzle with no `hint_ladder`; unreachable nodes; unresolved `required_clues`.

**Reachability is optimistic** — it ignores edge `conditions`, so it proves the
*graph* is completable, not that every condition is satisfiable.

## Authoring conventions in this world

- Setting is fixed at 1992; **travel changes place, never the year**.
- NPCs in `characters.yaml` are each a real character (book/film/TV/real life) given a
  Swedish play-on-words name, documented in a `# ←` comment above the entry (e.g.
  Hannibal Lecter → "Hannibal Läcker"; *läcker* = "delicious"). The `persona` is the
  second-person brief the AI Actor reads; `reveal_name` is the name revealed in play.
- These YAML files are the **single source of truth** for all world data; the database
  holds only player state and runtime/derived data (NPC memories, the image cache).
  Seeding **reconciles** the DB to the files — it upserts every row and prunes authored
  rows you've removed from YAML (a row a live player still references is kept, with a
  warning). The in-app admin content editor edits the **DB cache**, not these files —
  it is a separate path; to change the canonical world, edit the YAML and re-seed.
