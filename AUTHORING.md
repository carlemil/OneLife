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

The API also **auto-seeds on startup** (reconciles the DB to the YAML), so a fresh
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
- **Alignment conditions** gate content on the player's D&D-style alignment (two
  running coordinates in `[-1, 1]` that drift as an AI judge scores the player's
  words and choices): `good_at_least: 0.4`, `evil_at_least: 0.4`,
  `lawful_at_least: 0.4`, `chaotic_at_least: 0.4` (each true when the player has
  drifted at least that far toward the named pole). E.g. an edge only a chaotic
  player ever sees: `conditions: {chaotic_at_least: 0.5}`.
- A gate's fields (everything except `id`/`location`/`character`) become its
  stored `spec` (criteria, `knowledge_boundary`, `hint_ladder`,
  `mercy_after_attempts`, `on_success`) — see [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §3.

### State-reactive descriptions (`body_variants`)

A node's description can change with the player's state — most usefully with the
**state of the gates that affect it**, so a room or character reads differently
once something has happened there. Add `body_variants`: an ordered list of
`{when: <condition>, body: <text>}`. At render time the engine uses the **first
variant whose `when` holds**, falling back to the base `body` if none match.
`when` is the same condition DSL as edges (`gate_passed`, `flag_set`,
`puzzle_solved`, `node_visited`, `clue_found`, `all`/`any`/`not`), so this works
for any node — a `location` room *or* a `gate` (character) scene.

```yaml
nodes:
  - id: kbk-entrance-hall
    type: location
    body: "…an old janitor works a rag over a brass plaque."        # fallback
    body_variants:
      - when: {all: [{gate_passed: kbk-janitor-find-the-exit}]}     # after he talks
        body: "…the door marked PANNRUM holds your eye now — he as good as told you it's the way out."
```

Order matters (first match wins, most-specific first); a variant with no `when`
is a catch-all. Reactive text is a read-time view only — it never changes game
state, so it's automatically rollback-safe.

See `content/killebackskolan.yaml` and `content/marta.yaml` for full examples.

---

## Clock-driven games, minute costs and other per-game switches

`story_time` is a plain integer the engine carries per save; edges (and gate/puzzle
effects) add to it with `advance_story_time: N`, and conditions read it with
`story_time_gte: N` (combine with `not:` for "before"). A game can present it as a wall
clock and charge minutes for mistakes — `games/FlyktenFranFritids` is the worked example:

```yaml
game:
  clock: {start: "16:20"}   # state.clock = "HH:MM" = start + story_time minutes (header shows it)
  language: sv              # base YAML is Swedish → selecting the game switches the UI chrome
  map: false                # no overview map: no map block, every edge stays a button

puzzles:
  - id: ratt-nyckel
    on_solve: {set_flag: have_backpacks, advance_story_time: 1, log: "…"}
    on_fail:  {advance_story_time: 1, log: "Fel nyckel. En minut borta."}   # every wrong answer

nodes:
  - id: cykelstallen
    edges:
      - {id: e-grind, to: cykelvagen, label: Gå ut genom grinden,
         conditions: {any: [{not: {story_time_gte: 10}}, {flag_set: bosse_helped}]},
         effects: {advance_story_time: 1, set_flag: [left_school, out_in_time]}}
```

- `on_fail` uses the same effect keys as `on_solve` and is applied on every wrong answer
  (crosswords excluded); its `log` line is what the player sees.
- `set_flag` / `clear_flag` accept a single flag **or a list**.
- Without `clock`, `story_time` still advances but is never shown. Without `language`, the
  base is English (translations live in `i18n/<lang>.yaml` sidecars). Without `map: false`,
  the overview map is drawn from `map: {x, y}` node coordinates and `images/maps/icons/`.

---

## Crossword puzzles

A `puzzle` node can host a **crossword**: an interlocking grid of clued across/down
words the player answers **one whole word at a time**. Correct words lock into the grid
and reveal the letters they share with crossing words, so solving one clue helps crack
its neighbours. Authoring is just coordinates + answers — the engine derives the grid,
the numbering, and the interlocks.

```yaml
puzzles:
  - id: town-crossword
    type: crossword
    prompt: "The parish notice-board holds a half-finished crossword."
    solution:
      kind: crossword
      entries:
        - {id: a1, dir: across, row: 0, col: 0, clue: "Capital of Sweden", answer: STOCKHOLM}
        - {id: d1, dir: down,   row: 0, col: 2, clue: "Frozen water",      answer: ICE}
    hint_ladder: ["Start with the longest word."]   # optional, whole-puzzle
    on_solve: {progress_points: 60, set_flag: solved_crossword, log: "The grid is complete."}
```

- `row`/`col` are 0-indexed from the top-left; `dir` is `across` or `down`; answers are
  letters only (matched exact, case-insensitively — no typo forgiveness, so interlocks
  stay precise).
- Interlocks are implicit: two entries that cross a cell **must agree** on that letter, or
  `make lint` fails.
- Each solved entry is a seq-stamped player flag, so partial progress persists and rolls
  back with the rest of the run; the whole puzzle solves (firing `on_solve`) once every
  entry is filled. `hint_ladder` is whole-puzzle (per-entry hints are the clues themselves).

---

## What the lint guarantees

`make lint` fails the build (errors) on:

- Duplicate ids; references to unknown locations/arcs/gates/puzzles/characters/clues;
  edges pointing at unknown nodes.
- Not exactly one `entry` node, or no `ending` node.
- **Traps** — a node reachable from the entry that *cannot reach any ending*
  (death nodes are exempt; rollback is their escape).
- **Broken crosswords** — a `crossword` puzzle with a malformed grid (duplicate entry
  ids, bad `dir`, negative/missing `row`/`col`, empty or non-letter answers) or a
  **crossing-letter conflict** between two entries.

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

- The YAML files are the **single source of truth**. Seeding **reconciles** the DB
  to them: it upserts every authored row and then **prunes** authored rows no longer
  present in the YAML, so a deletion in a file propagates on the next seed. A row that
  a live player still references (a non-cascading FK) is **kept** with a printed
  warning instead of aborting the seed.
- Node graph-editor positions are authored too, as `pos: {x, y}` on each node, and
  seeded into the `node_positions` cache the map reads. The in-app graph is **read-only**
  except for drag-to-reposition, which writes the `pos:` line back into the YAML.
- Runtime tables (players, sessions, memories, image cache, progress) are never
  touched by seeding.
