---
name: initGame
description: Scaffold a NEW OneLife game under games/<id>/data/ — a minimal, lint-valid, end-to-end playable starter (a cell, a location, an NPC dialogue gate, a puzzle + clue, a death node, an ending) plus a full game.yaml (title/subtitle/first_summary + per-game config: setting, name_particles, onboarding, offline soundtrack), organized by-type like games/SandbyMystery/data/. Then optionally theme it to a premise, lint, and seed so it appears in the in-app lobby. Use when asked to create/initialize/scaffold/start a new game or "/initGame". Local to the OneLife repo.
---

# Initialize a new OneLife game (initGame)

OneLife hosts several games at once — each folder under `games/<id>/data/` is its own
game, seeded into the DB and offered in the in-app lobby. This skill scaffolds a new one
that is **guaranteed to pass the spine lint** (exactly one entry, ≥1 ending, no traps, all
references resolve), so it seeds and is playable immediately. Theme it afterwards.

The engine is **data-agnostic**: a game's entire identity lives in its dataset. All
game-specific copy goes in the YAML — the `game:` block (`api/app/gameconfig.py` reads it)
plus node/gate/puzzle text. **Never** put game copy in the engine's `api/app/strings.py`
or `web/src/lib/strings.js` (those are game-agnostic chrome).

## 1. Pick an id and title

- `--id` is the folder name and DB id — a slug matching `[A-Za-z0-9_-]+` (no spaces).
  Slugify the user's name (e.g. "The Drowned Coast" → `DrownedCoast`).
- `--title` is the lobby title (defaults to a prettified id). `--subtitle` and
  `--first-summary` (the first log line) are optional. `--region`/`--period` fill
  `game.setting` — leave blank to stay place/time-agnostic.

## 2. Scaffold it

From the repo root:

```bash
python .claude/skills/initGame/init_game.py --id DrownedCoast \
    --title "The Drowned Coast" --subtitle "..." [--region "..."] [--period "..."]
```

- Run with `--dry-run` first to preview the file list + graph (writes nothing).
- It **refuses to overwrite** an existing `games/<id>/` unless you pass `--force`.
- Pure stdlib (no deps, no DB). It writes 16 files: by-type YAML (`arcs`, `cells`,
  `locations`, `characters`, `nodes-{narration,location,gate,puzzle,death,ending}`,
  `edges`, `gates`, `puzzles`, `clues`), `game.yaml`, and a `CLAUDE.md`.

The starter graph: `start`(entry, narration) → `square`(location hub) →
`elder`(gate) / `well`(puzzle) / `gate-out`(ending) / `peril`(death). The arch to the
ending opens once the player wins the elder over **or** solves the well riddle — so it's
always completable, even offline (the riddle answer is deterministic).

## 3. Theme it (optional, when a premise was given)

Edit the generated YAML to fit the user's premise, **keeping the graph shape** so it stays
lint-valid — rename/re-skin, don't restructure. Typical edits:

- `game.yaml` → `title`/`subtitle`/`first_summary`, `setting.region`/`period`,
  `name_particles` (locale honorifics), and optionally the `onboarding` manual and
  `offline` soundtrack. (`api/app/gameconfig.py` documents every key + its default.)
- `characters.yaml` → the NPC's `name`/`persona` (and `reveal_name` if the name is
  withheld). The SandbyMystery convention is a Swedish pun name with a `# ←` gloss — see
  `games/SandbyMystery/data/CLAUDE.md`; only follow it if the user wants that style.
- `gates.yaml` → the gate's `intent`, `criteria`, `knowledge_boundary`, `hint_ladder`.
- `puzzles.yaml` / `clues.yaml` → the riddle `prompt`, `solution`, hints, clue text.
- node `body`/`title`/`media` across `nodes-*.yaml`.

Field reference: `AUTHORING.md`, `AI_DIALOGUE_GATES.md`, `STORY_AND_PUZZLES.md`.
Onboarding is **account-level** (shown once, before the lobby), so a secondary game's
onboarding block rarely displays — it matters mainly if this becomes the primary game.

To grow the world (more cells/locations/NPCs), copy the patterns in
`games/SandbyMystery/data/`. Re-lint after every edit.

## 4. Lint — must be green before seeding

```bash
make lint      # or: docker compose run --rm --no-deps api python -m app.seed --lint
```

Lints **every** game; expect "Lint OK". Fix any ERROR (broken ref, trap, two entries, no
ending) before seeding.

## 5. Seed so it goes live

```bash
make seed      # or: docker compose run --rm api python -m app.seed
```

`app.seed` loops every game and is idempotent — it seeds the new game without touching the
others. It then appears in the lobby (`GET /api/games`); no restart needed (the lobby
reads the `games` table). **Caveat:** `gameconfig` caches the `game:` block per data-dir
in the running API process. A brand-new game loads fresh, but if you **re-edit** an
already-seeded game's `game:` block (setting / onboarding / soundtrack), restart the api
(`docker compose up -d api`) so the change is picked up.

## 6. Optional: art

A game plays fine with no art (missing images fall back gracefully). To add it later:
`/generateMap` (world map) and `/generateIcons` (per-location emblems).

## Report

Tell the user the new folder path, that lint is green and it's seeded, and that it's now
selectable in the in-app lobby (with Continue/New). If you themed it, summarize what you
set. If anything failed lint, report the finding and the fix.
