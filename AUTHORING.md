# OneLife — Authoring Content

Game content (story nodes, choices, dialogue gates, puzzles, clues, NPCs,
locations) is authored in **YAML files in the separate game-data repo**
(default `../OneLife-KBK-mystery`, mounted into the API at `/content`; override
with `GAME_DATA_DIR`) and loaded into Postgres by a validating seed pipeline.
This engine repo is data-agnostic — you no longer hand-write SQL, and you edit
the data repo, not this one, to change the world.

This realizes the authoring step flagged in [STORY_AND_PUZZLES.md](STORY_AND_PUZZLES.md) §10
and gives the design's "jungle of story arcs" a way to actually grow.

---

## Workflow

```bash
make lint      # validate content/ — schema, references, and spine reachability (no DB)
make seed      # validate + load content/ into Postgres
make up        # build + start everything (also auto-seeds on API startup)
make reset     # wipe the DB volume and start fresh
```

No `make` (Windows/PowerShell)? Use the underlying commands:

```bash
docker compose run --rm --no-deps api python -m app.seed --lint    # lint
docker compose run --rm api python -m app.seed                     # seed
docker compose up --build -d                                       # up
```

The API also **auto-seeds on startup** (idempotent upsert), so a fresh
`docker compose up` always yields a playable game. `make seed` is for reloading
after edits without a full restart.

---

## File format

Drop any number of `*.yaml` files in the data repo; they're merged. Each file may
contain any subset of these top-level lists:

| Key | What it defines |
|---|---|
| `arcs` | Narrative threads (`main` is the spine) |
| `characters` | NPCs (`id`, `name`, `persona`) |
| `locations` | Places (`id`, `name`, `description`) |
| `nodes` | Story beats (with **embedded `edges`**) |
| `gates` | Dialogue-gate specs (referenced by `gate` nodes) |
| `puzzles` | Puzzle specs (referenced by `puzzle` nodes) |
| `clues` | Woven clue fragments for puzzles |
| `edges` | **Standalone** edges — attach a route to a node defined in any file |

### A minimal node with a choice

```yaml
nodes:
  - id: kbk-wake
    arc: main
    type: narration          # narration|location|gate|puzzle|death|ending
    location: killebackskolan
    title: "Déjà Vu"
    entry: true              # exactly one node across all files must be the entry
    media: {image_theme: school-hallway-dusk, music_theme: uneasy-quiet}
    body: "You come to..."
    edges:
      - id: e-wake-bearings
        to: kbk-entrance-hall
        label: Get your bearings
        conditions: {all: []}                 # the shared condition DSL
        effects: {progress_points: 5, log: "You got up."}
        danger: 0
```

- **gate** nodes set `gate: <gate-id>`; **puzzle** nodes set `puzzle: <puzzle-id>`.
- **edges** live inside their source node (`from` is implied). To add a route
  *from a node defined in another file*, use a standalone `edges:` entry with an
  explicit `from`.
- Condition/effect DSL is the same one the engine evaluates — see
  [STORY_AND_PUZZLES.md](STORY_AND_PUZZLES.md) §4.
- A gate's fields (everything except `id`/`location`/`character`) become its
  stored `spec` (criteria, `knowledge_boundary`, `hint_ladder`,
  `mercy_after_attempts`, `on_success`) — see [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §3.

See `content/killebackskolan.yaml` and `content/marta.yaml` for full examples.

---

## What the lint guarantees

`make lint` fails the build (errors) on:

- Duplicate ids; references to unknown locations/arcs/gates/puzzles/characters/clues;
  edges pointing at unknown nodes.
- Not exactly one `entry` node, or no `ending` node.
- **Traps** — a node reachable from the entry that *cannot reach any ending*
  (death nodes are exempt; rollback is their escape).

It warns (non-blocking) on:

- Gate nodes whose gate has no `mercy_after_attempts` (soft-lock risk).
- Puzzle nodes whose puzzle has no `hint_ladder`.
- Unreachable nodes (dead content).
- `required_clues` that don't resolve.

**Reachability is optimistic** — it ignores edge `conditions`, assuming a player
can eventually satisfy the flags by playing. So the lint proves the *graph* is
completable; it does not prove every condition is satisfiable. That deeper check
(constraint planning over flags) is future work.

---

## Notes & limits

- Seeding is an **upsert**: it updates authored rows in place and adds new ones,
  but does not delete content removed from YAML (except edges, which are rewritten
  per source node). For a clean slate, `make reset`.
- Runtime tables (players, sessions, memories, progress) are never touched by
  seeding.
