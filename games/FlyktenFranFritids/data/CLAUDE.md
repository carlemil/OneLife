# CLAUDE.md — Flykten från fritids game data

This folder is a **OneLife game dataset**: authored content as YAML, no code. The engine
mounts it and seeds it into Postgres; it appears in the in-app lobby as its own game.

Authoring rules + the full field reference live in the engine docs at the repo root:
`../../../AUTHORING.md`, `../../../STORY_AND_PUZZLES.md`, `../../../AI_DIALOGUE_GATES.md`.
Per-game engine config (setting, name particles, onboarding, offline soundtrack) lives in
the `game:` block of `game.yaml` — see `api/app/gameconfig.py` for the keys + defaults.

## Layout (all *.yaml are merged; organized by type)
- `arcs.yaml` `cells.yaml` `locations.yaml` `characters.yaml`
- `nodes-*.yaml` — story beats by node type, edges embedded inline
- `edges.yaml` — standalone edges (explicit `from`) into nodes in other files
- `gates.yaml` `puzzles.yaml` `clues.yaml`
- `game.yaml` — title/subtitle/first_summary + per-game config

## After editing
Always `make lint` (or `docker compose run --rm --no-deps api python -m app.seed --lint`),
then `make seed` to load. The lint proves the graph is completable (one entry, ≥1 ending,
no traps) and every reference resolves.
