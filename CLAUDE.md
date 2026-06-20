# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

OneLife is a browser-based text-adventure: a stream of interactions with AI-driven
agents (NPCs/locations), set in a dark southern-Sweden world. Stack: SvelteKit-less
**Svelte + Vite** frontend ↔ **FastAPI** backend ↔ **Postgres + pgvector**, all via Docker Compose.

**This repo is the data-agnostic engine + editor only.** The game *data* (all
`*.yaml`) lives in a SEPARATE sibling repo — by default `../OneLife-KBK-mystery`
(the "KBK mystery" dataset) — mounted into the API container at `/content`. Point at
a different dataset with `GAME_DATA_DIR` in `.env`. Nothing in this repo hard-codes
KBK content; to edit the game's world, edit the data repo, not this one.

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
  - The game data (`*.yaml` in the **separate** `../OneLife-KBK-mystery` repo) is a **mounted volume** → after editing, just `make seed` (no rebuild). The API also auto-seeds (idempotent upsert) on startup.
  - `api/app/*.py` is **baked into the image** → rebuild: `docker compose up -d --build api`.
  - `web/src/*` → rebuild: `docker compose up -d --build web`.
- **`db/*.sql` only runs on a fresh volume.** Any schema change (new column/table) requires `make reset` (down -v) to take effect — upsert seeding never adds columns. A reset wipes all players/runtime data.
- Python syntax sanity check (no test framework — verification is end-to-end via the API / curl):
  `python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('api/app/*.py')]"`

### Configuration (`.env`, gitignored; see `.env.example`)
All optional — without them the app still boots using deterministic stubs/fallbacks:
- `GAME_DATA_DIR` — host path to the game-data repo mounted at `/content` (default `../OneLife-KBK-mystery`). Set this to run a different dataset.
- `ANTHROPIC_API_KEY` — real Claude for dialogue gates / music director; unset → offline keyword stub.
- `IMAGE_API_KEY` (+ `IMAGE_API_BASE`/`IMAGE_MODEL`) — real location images (OpenAI-compatible); unset → procedural SVG.
- `SPOTIFY_CLIENT_ID`/`SECRET`/`REDIRECT_URI` — track resolution + Web Playback SDK; unset → text-only picks.
- `ONELIFE_SECRET_KEY` — encrypts TOTP secrets at rest (dev default + warning if unset).
- `WEB_ORIGIN` — comma-separated CORS allow-list (default allows both `localhost` and `127.0.0.1` on :5173). **The browser origin must be in this list and, for Spotify, match the registered redirect URI exactly.**

### Testing auth flows by hand
2FA codes: register returns the plaintext secret; compute a TOTP via the api container —
`docker compose exec -T api python -c "import pyotp; print(pyotp.TOTP('<secret>').now())"`.
Stored `totp_secret` is Fernet-encrypted; decrypt with `from app import security; security.decrypt(<value>)`.

## Architecture

### Content is data, not code
All game content — world cells, locations, characters, story nodes & edges, dialogue
gates, puzzles, clues — lives as **`*.yaml` in the separate data repo**
(`../OneLife-KBK-mystery`, mounted to `/content`) and is loaded by `content.py`
(merge files → `validate()` → `seed_content()` upsert). **`db/*.sql` is DDL only.** To add
or change content, edit YAML in the data repo and `make seed`; never hand-write content SQL. The
`validate()` step is a real gate (run via `make lint`): it checks references and a
**spine-reachability lint** — exactly one entry node, ≥1 ending, and no "traps"
(a reachable non-death node that can't reach any ending). Reachability is travel-aware
(world-access nodes reach every cell's arrival node) and optimistic (ignores edge conditions).
See `AUTHORING.md`.

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
and can only transition to authored nodes. `llm.py` uses Claude when configured, else a
deterministic stub, so the whole loop runs offline. **Preserve this invariant** for any new
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
  mandatory TOTP 2FA (QR via segno, PKCE Web Playback flow on the client) + one-time recovery
  codes; session = one row per player, token rotates on login (7-day TTL). Rate-limit + lockout
  + TOTP encryption at rest in `security.py`. A forced manual + quiz gates play — **game
  endpoints return 403 until `onboarded`**.
- **World/maps**: `world_cells` grid; `locations.cell_id`; nodes flagged `world_access` open the
  map. Travel is adjacency-gated with per-player fog-of-war (`player_cells`). See `WORLD_AND_MAPS.md`.
- **Atmosphere** (`atmosphere.py`, `imagegen.py`): per-location image (procedural SVG or real
  provider, cached per theme) + LLM "music director" picks resolved to Spotify tracks. See
  `ATMOSPHERE.md`, `MEDIA_PROVIDERS.md`.

### Frontend (`web/src`)
A single Svelte 5 component `App.svelte` (runes: `$state`/`$derived`/`$effect`) drives phases
`auth → twofa → recovery → onboarding → game`. `lib/api.js` is the typed-ish fetch client
(401 on an authed request → drop token to login; 403 → onboarding). `lib/spotify.js` is the
PKCE + Web Playback SDK client.

## Design docs
Deeper rationale lives in the root `*.md` docs — start with `README.md`, then the per-subsystem
docs referenced above (`DATA_MODEL.md`, `STORY_AND_PUZZLES.md`, `TECH_STACK.md`, etc.). Update
the relevant doc when changing a subsystem's behavior.
