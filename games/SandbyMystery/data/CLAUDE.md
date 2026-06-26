# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is the **game-data folder of the repo** for the [OneLife](../OneLife) engine — a dark text
adventure set in 1992 southern Sweden (Skåne), opening at Killebäckskolan in Södra
Sandby. It contains **no code**, only authored content as YAML. The engine is
data-agnostic: it mounts this repo at `/content` and seeds it into Postgres.

The engine lives in the sibling directory `../OneLife` (on this machine,
`D:\source\OneLife`). Authoring rules and the full field reference live in the
engine's `../OneLife/AUTHORING.md`, `STORY_AND_PUZZLES.md`, and `AI_DIALOGUE_GATES.md`.
The engine expects this repo as `../OneLife-KBK-mystery` by default; override with
`GAME_DATA_DIR` in the engine's `.env`.

## Commands

There is no build step here. Validate and load content **from the engine repo**
(`../OneLife`):

```bash
make lint     # validate this data — schema, references, spine reachability (no DB)
make seed     # validate + load into Postgres (idempotent upsert)
make up       # build + start everything (also auto-seeds on API startup)
make reset    # wipe the DB volume and start fresh
```

On Windows/PowerShell without `make`, use the underlying commands (run from `../OneLife`):

```bash
docker compose run --rm --no-deps api python -m app.seed --lint   # lint
docker compose run --rm api python -m app.seed                    # seed
```

**Always `make lint` after editing YAML** — it is the only check this repo has.

## How the content model works

Every `*.yaml` file may contain any subset of these top-level lists, and **all files
are merged**, so files are organized by area/feature (`killebackskolan.yaml`,
`lund.yaml`, `malmo.yaml`, `sandby.yaml`, `marta.yaml`), not by type. `npcs.yaml`
and `puzzles.yaml` are **inert libraries** — characters/puzzles not yet wired into a
node, so they don't affect the spine lint until referenced.

| List | Defines |
|---|---|
| `arcs` | Narrative threads; `main` is `is_spine: true` |
| `cells` / `locations` | The travel grid (cells) and places within them |
| `characters` | NPCs — `id`, `name`, optional `reveal_name`, and a `persona` (2nd-person brief the AI Actor reads) |
| `nodes` | Story beats, with **edges embedded inline** |
| `gates` | AI-dialogue specs referenced by `gate` nodes |
| `puzzles` | Puzzle specs referenced by `puzzle` nodes |
| `clues` | Woven clue fragments that feed a puzzle's `required_clues` |
| `edges` | **Standalone** edges (with explicit `from`) to attach a route to a node defined in *another* file |

### Nodes and edges

- `node.type` is one of `narration | location | gate | puzzle | death | ending`.
- A `gate` node sets `gate: <gate-id>`; a `puzzle` node sets `puzzle: <puzzle-id>`.
- Edges normally live **inside** their source node (`from` is implied). To add a
  route from a node defined elsewhere, use a top-level `edges:` entry with explicit
  `from` (see `marta.yaml` adding routes off `kbk-entrance-hall`).
- `media: {image_theme, music_theme, real_place, reference_image}` drives generated
  art and the atmosphere soundtrack. `world_access: true` marks travel-hub nodes.
- `sort_order` orders choices in the UI; "leave/back" edges conventionally use a
  high value (e.g. `9`) so they sort last.
- `pos: {x, y}` is the node's position in the story-graph editor (pixel coords) and,
  after normalization, where its location icon sits on the overview map. It is authored
  here in YAML; dragging a node in the read-only graph editor writes this line back.

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
- NPCs in `npcs.yaml` are each a real character (book/film/TV/real life) given a
  Swedish play-on-words name; the `persona` is always written in the second person.
- These YAML files are the **single source of truth** for all world data; the
  database holds only player state and runtime/derived data (NPC memories, the image
  cache). Seeding **reconciles** the DB to the files — it upserts every row and prunes
  authored rows you've removed from YAML (a row a live player still references is kept,
  with a warning). There is no in-app content editor: edit the YAML and re-seed (the
  story graph is a read-only view whose only edit is dragging a node, saved as `pos:`).
