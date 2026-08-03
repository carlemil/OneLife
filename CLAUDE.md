# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

OneLife is a browser-based text-adventure: a stream of interactions with AI-driven
agents (NPCs/locations), set in a dark southern-Sweden world. Stack: SvelteKit-less
**Svelte + Vite** frontend ↔ **FastAPI** backend ↔ **Postgres + pgvector**, all via Docker Compose.

**The engine is data-agnostic; game *data* (all `*.yaml`) lives in-repo under
`games/<Game>/data/`** — by default `games/SandbyMystery/data` (the Sandby Mystery
dataset) — mounted into the API container at `/content`. Point at a different dataset
with `GAME_DATA_DIR` in `.env`. Nothing in the engine code hard-codes a dataset; to
edit the game's world, edit the YAML under `games/SandbyMystery/data/`.

## Running & developing

Everything runs in Docker. The host is Windows/PowerShell; `make` may be absent, so
each Make target's underlying `docker compose` command is given.

| Task | Make | Raw command |
|---|---|---|
| Build + start | `make up` | `docker compose up --build -d` |
| Stop (keep DB) | `make down` | `docker compose down` |
| **Reset DB** + start | `make reset` | `docker compose down -v && docker compose up --build -d` |
| Load content | `make seed` | `docker compose run --rm api python -m app.seed` |
| Lint content | `make lint` | `docker compose run --rm --no-deps api python -m app.seed --lint` |
| API logs | `make logs` | `docker compose logs -f api` |

- Web: http://localhost:5173 · API: http://localhost:8000 (`/docs`, `/api/health`).
- **Apply changes correctly:**
  - The game data (`*.yaml` under `games/SandbyMystery/data/`) is a **mounted volume** → after editing, just `make seed` (no rebuild). The API also auto-seeds (idempotent upsert) on startup.
  - `api/app/*.py` is **baked into the image** → rebuild: `docker compose up -d --build api`.
  - `web/src/*` → rebuild: `docker compose up -d --build web`.
- **`db/*.sql` only runs on a fresh volume.** Any schema change (new column/table) requires `make reset` (down -v) to take effect — upsert seeding never adds columns. A reset wipes all players/runtime data.
- Python syntax sanity check (no test framework — verification is end-to-end via the API / curl):
  `python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('api/app/*.py')]"`

### Configuration (`.env`, gitignored; see `.env.example`)
All optional — without them the app still boots using deterministic stubs/fallbacks:
- `GAME_DATA_DIR` — host path to the game-data folder mounted at `/content` (default `./games/SandbyMystery/data`). Set this to run a different dataset.
- `ANTHROPIC_API_KEY` — real Claude for dialogue gates / music director; unset → offline keyword stub.
- `IMAGE_PROVIDER` — `pollinations` (free, keyless; `POLLINATIONS_TOKEN` adds real-photo image-to-image) or `openai` (needs `IMAGE_API_KEY`+`IMAGE_API_BASE`/`IMAGE_MODEL`). Unset/neither → procedural SVG.
- `SPOTIFY_CLIENT_ID`/`SECRET`/`REDIRECT_URI` — track resolution + Web Playback SDK; unset → text-only picks.
- `ONELIFE_SECRET_KEY` — encrypts TOTP secrets at rest (dev default + warning if unset;
  **fatal** if unset when `ONELIFE_ENV=prod`).
- `ONELIFE_ENV` — `dev` (default) or `prod`. `docker-compose.prod.yml` sets `prod`, which
  turns off the FastAPI auto-docs and makes the dev-key fallback above fail fast. Every
  prod-only difference hangs off `security.IS_PROD`.
- `ONELIFE_ADMIN_EMAILS` — comma-separated emails allowed into the in-UI content editor (the ⚙ panel).
- `WEB_ORIGIN` — comma-separated CORS allow-list (default allows both `localhost` and `127.0.0.1` on :5173). **The browser origin must be in this list and, for Spotify, match the registered redirect URI exactly.**

### Testing auth flows by hand
2FA codes: register returns the plaintext secret; compute a TOTP via the api container —
`docker compose exec -T api python -c "import pyotp; print(pyotp.TOTP('<secret>').now())"`.
Stored `totp_secret` is Fernet-encrypted; decrypt with `from app import security; security.decrypt(<value>)`.

## Architecture

### Content is data, not code
All game content — world cells, locations, characters, story nodes & edges, dialogue
gates, puzzles, clues — lives as **`*.yaml` under `games/SandbyMystery/data/`**
(mounted to `/content`) and is loaded by `content.py`
(merge files → `validate()` → `seed_content()` upsert). **`db/*.sql` is DDL only.** To add
or change content, edit YAML in the data repo and `make seed`; never hand-write content SQL. The
`validate()` step is a real gate (run via `make lint`): it checks references and a
**spine-reachability lint** — exactly one entry node, ≥1 ending, and no "traps"
(a reachable non-death node that can't reach any ending). Reachability is travel-aware
(world-access nodes reach every cell's arrival node) and optimistic (ignores edge conditions).
See `AUTHORING.md`.

### Concurrent multi-game (`gamestate.py`, `migrations.py`)
The server hosts **several games at once** — every folder under `games/<id>/data` is its
own game, seeded into the DB at startup (`gamestate.all_games_with_data()`). Every authored
content row and every per-player runtime row carries a **`game_id`**; content tables use a
**composite primary key `(game_id, id)`** so two games can reuse the same slug, and
inter-content FKs carry `game_id`. A player has an **independent, resumable save per game**
(`player_games`, keyed `(player_id, game_id)`); `players.active_game_id` is the selected one
(NULL = in the lobby). The login token lives on `player_sessions` (auth only). Players pick a
game in the **in-app lobby** (`/api/games`, `/api/games/select`, `/api/games/leave`);
leaderboards and NPC memory are per-game. The rollback seq-stamp invariant is scoped per
`(player, game)` — `seq` is per-log, so every void/delete in `engine.rollback` filters
`game_id`. `migrations.ensure_multigame` brings an existing single-game volume up to this
shape **in place** (additive backfill, no data loss); `db/01_schema.sql` is the fresh-volume
source of truth — keep them in sync. The admin "switch game" control now only changes which
dataset the **authoring** editors act on, independent of what players play.

### Story engine + the rollback invariant (`engine.py`, `dsl.py`)
The game is a directed graph: `story_nodes` joined by `story_edges`, navigated through a
small shared **condition/effect DSL** (`dsl.py`) evaluated in plain code. Player progress
is an append-only **log** (`log_entries`), and a single invariant governs all runtime state:
**every runtime row is stamped with the `log_entries.seq` that created it; rollback to seq N
voids everything with `seq > N`** (flags, clue/cell discoveries, puzzle solves, gate progress,
progress points). Rolling back costs leaderboard position. Keep new runtime state seq-stamped
and voided in `engine.rollback`. IDs for authored entities are TEXT slugs; runtime tables use UUIDs.

### AI dialogue gates — the Actor/Referee/Applier split (`gates.py`, `llm.py`)
The load-bearing AI pattern. When a player talks to a gated NPC: the **Actor** (LLM) produces
in-character dialogue and the **Referee** (separate LLM call, out-of-band) returns a typed
verdict on whether authored criteria are met — but **only code (the Applier) mutates game state,
and only on a validated verdict.** This keeps the LLM on-rails: it influences words, never state,
and can only transition to authored nodes. `llm.py` selects a provider via `LLM_PROVIDER`:
**anthropic** (real Claude), **browser** (the model runs in the player's browser via WebGPU/WebLLM),
or **stub** (deterministic offline). Every provider shares one source of truth: `build_*()` constructs
the prompt/JSON-schema, `parse_*()` reads the completion back — so the prompt engineering isn't
duplicated. In **browser** mode the server never calls a model: endpoints return a `pending_inference`
envelope, the browser runs it and POSTs raw completions to `/api/llm/complete` (a 2-phase "inference
broker"; see `gates.build_turn`/`apply_turn`), and **gate verdicts become client-trusted** — the
Applier still re-validates every verdict (criteria-id filter, ±0.3 delta clamp, `success_rule`), so the
blast radius is a player cheating their own save. **Preserve the Applier invariant** for any new
AI-driven feature (the music director and memory-explanation generators follow it). See `AI_DIALOGUE_GATES.md`.

### Agent memory & cross-player/character leakage (`memory.py`, `embeddings.py`)
NPC memories are stored with vector embeddings (pgvector). Written on gate success, retrieved
into the Actor's context split into *own* (this player) vs *leaked* (others), so one player's
interactions surface in another's game through shared NPCs, and across NPCs via
`propagate_to_other_characters()` (with an LLM-generated believable explanation, deduped).
Timeline-safe and rollback-voided. Embeddings are a pluggable local hashed stand-in (no
embeddings provider wired). See `MEMORY_AND_LEAKAGE.md`.

### Other subsystems
- **Auth/onboarding** (`auth.py`, `security.py`, `onboarding.py`): email/password (bcrypt) +
  optional TOTP 2FA (on by default at registration, opt out or skip; required for admins;
  a code is only demanded once `totp_enabled`) (QR via segno, PKCE Web Playback flow) + one-time recovery
  codes; session = one row per player, token rotates on login (7-day TTL) and is stored
  **only as a SHA-256 digest**. Rate-limit + lockout + TOTP encryption at rest in
  `security.py`; TOTP codes are single-use (`players.totp_last_step` blocks replay inside
  the ~90s validity window). A forced manual + quiz gates play — **game endpoints return
  403 until `onboarded`**.
- **World/maps**: `world_cells` grid; `locations.cell_id`; nodes flagged `world_access` open the
  map. Travel is adjacency-gated with per-player fog-of-war (`player_cells`). See `WORLD_AND_MAPS.md`.
- **Atmosphere** (`atmosphere.py`, `imagegen.py`, `gen_images.py`): per-location banner image +
  LLM "music director" picks resolved to Spotify tracks. Images are cached in `generated_images`
  keyed by the node's `media.image_theme`; the prompt blends `media.real_place` + the node body
  text in a house sepia style (with `POLLINATIONS_TOKEN`, a real reference photo is restyled via
  image-to-image, fetched server-side → data URI). Warm every theme up front:
  `docker compose run --rm api python -m app.gen_images [--force]`. See `ATMOSPHERE.md`, `MEDIA_PROVIDERS.md`.
- **Admin panel** (`admin.py`, `content.py`, `content_log.py`, `map_write.py`, `/api/admin/*` in `main.py`,
  ⚙ UI in `App.svelte`): admins-only (`ONELIFE_ADMIN_EMAILS`). **The DB-mutating in-UI content editor —
  with its content-event log, undo/redo and auto-repair — was retired.** The YAML files are now the single
  source of truth and git is the history; the in-app story-graph is a **read-only** view. The only writes
  it makes go *back into the YAML*: dragging a node persists `pos:` and the map editor persists `map`/
  `world_exit` placements, both via `map_write.py` as **surgical, byte-preserving text edits** (so the
  `/content` mount must be **read-write**). `admin.py` is the hidden export/import panel (authored content
  is **export-only**; full-DB and per-player saves round-trip via `json_agg`/`json_populate_recordset`,
  destructive imports require typing `REPLACE`). See `ADMIN.md`.

### Frontend (`web/src`)
A single Svelte 5 component `App.svelte` (runes: `$state`/`$derived`/`$effect`) drives phases
`auth → twofa → recovery → onboarding → game`. `lib/api.js` is the typed-ish fetch client
(401 on an authed request → drop token to login; 403 → onboarding). `lib/spotify.js` is the
PKCE + Web Playback SDK client.

## Design docs
Deeper rationale lives in the root `*.md` docs — start with `README.md`, then the per-subsystem
docs referenced above (`DATA_MODEL.md`, `STORY_AND_PUZZLES.md`, `TECH_STACK.md`, etc.). Update
the relevant doc when changing a subsystem's behavior.
