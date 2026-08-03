# OneLife

A web-based, browser-played text adventure presented as a stream of interactions
with AI-driven agents. See the design docs:

- [GAME_DESIGN.md](GAME_DESIGN.md) — concept & features
- [TECH_STACK.md](TECH_STACK.md) — stack choices
- [DATA_MODEL.md](DATA_MODEL.md) — database schema
- [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) — the "talk your way through" design
- [STORY_AND_PUZZLES.md](STORY_AND_PUZZLES.md) — story graph & puzzle system
- [MEMORY_AND_LEAKAGE.md](MEMORY_AND_LEAKAGE.md) — agent memory & cross-player/character leakage
- [AUTHORING.md](AUTHORING.md) — how to write game content (YAML → DB, with a spine lint)
- [ACCOUNTS_AND_ONBOARDING.md](ACCOUNTS_AND_ONBOARDING.md) — auth (email/password + TOTP 2FA) & onboarding
- [ATMOSPHERE.md](ATMOSPHERE.md) — per-location generated image + LLM-picked Spotify soundtrack
- [WORLD_AND_MAPS.md](WORLD_AND_MAPS.md) — the cell grid, world map, and travel
- [MEDIA_PROVIDERS.md](MEDIA_PROVIDERS.md) — real image generation + Spotify Web Playback SDK
- [ADMIN.md](ADMIN.md) — hidden admin export/import (content, full DB, player saves)

## Vertical slice (this repo, runnable)

A playable proof of the whole stack: the opening at **Killebäckskolan** —
wake → entrance hall → talk your way past the **janitor** (AI dialogue gate) →
solve the **boiler-room padlock** (woven 3-clue puzzle) → or force the door and
**die** → **roll back** the log (at a leaderboard cost).

### Run it

```bash
docker compose up --build
```

Then open **http://localhost:5173**.

- **API** is on http://localhost:8000 (`/api/health`, OpenAPI docs at `/docs`).
- **Postgres** initializes from `db/01_schema.sql` + `db/02_seed.sql` on first boot.

### LLM provider (optional)

The dialogue gate runs a **deterministic offline stub by default**, so it's fully
playable with no key. Pick a provider with `LLM_PROVIDER` in `.env`:

- **anthropic** — the real Actor/Referee loop against Claude. Set a key:
  ```bash
  cp .env.example .env
  # ANTHROPIC_API_KEY=sk-ant-...   (LLM_PROVIDER defaults to anthropic when a key is set)
  docker compose up --build
  ```
- **browser** — the model runs **in each player's browser** on their GPU (WebGPU/WebLLM),
  no key or server GPU needed. Set `LLM_PROVIDER=browser` and optionally `LLM_MODEL`
  (a prebuilt MLC id, default a small Llama-3.2-3B). On first play the browser downloads
  the model once (multi-GB, cached); a progress notice shows while it loads, and talking/
  choices/hints unlock when it's ready. Needs a recent desktop Chrome/Edge (WebGPU).
  Note: gate verdicts run client-side and are **not** cheat-proof — see AI_DIALOGUE_GATES.md §5b.
- **stub** — deterministic offline heuristics (`LLM_PROVIDER=stub` or no key).

`GET /api/health` reports `using_real_llm`, `llm_provider`, and `llm_model`.

### How to play the slice

1. Enter a name to begin.
2. **Get your bearings** → you're in the entrance hall.
3. **Examine the brass plaque** (a clue), then **approach the janitor**.
4. Talk to him — be kind / say you're lost, *and* ask how to get out. He'll reveal
   the boiler-room exit. (Try to talk your way through; after 6 tries he relents — the
   mercy rule, so you can never get stuck.)
5. Back in the hall, **open the PANNRUM door** → the boiler room.
6. Enter the 4-digit code. The three clues (locker graffiti, the janitor, the plaque)
   all point at the same year.
7. Or, before learning the exit, **force the door** — you die. Use **roll back here**
   in the log panel to escape; note your progress (and leaderboard rank) drops.

### Reset the world

```bash
docker compose down -v   # wipes the Postgres volume; schema+seed re-run on next up
```

### Expose it to the internet (HTTPS, behind a reverse proxy)

Local `docker compose up` binds the api/web/db to `127.0.0.1` only. To let remote
players in, use the production overlay, which adds a **Caddy** reverse proxy serving
one HTTPS origin (`/api/*` → api, everything else → the built web app) with an
automatic Let's Encrypt certificate:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Prerequisites (see `.env`): set `DOMAIN` (its DNS → this host's public IP — DuckDNS
works for a dynamic IP), set `WEB_ORIGIN=https://DOMAIN`, and harden secrets
(`ONELIFE_SECRET_KEY`, DB password). On the network: **forward only router TCP 80 +
443** to this host (port 80 is needed for the cert challenge) and allow them through
the firewall — do *not* forward 5173/8000/5432. For Spotify, register
`https://DOMAIN/` as the redirect URI. Music is streamed by Spotify directly to each
player's browser via their own Premium account; the server only resolves track
metadata, so non-Premium players get 30s previews + text.

### Surviving a reboot (Windows host)

The compose `restart: unless-stopped` policies only help once the Docker engine is
running — and on Windows, **Docker Desktop only runs inside a signed-in user
session**. So a reboot leaves the site down until someone logs in and starts Docker.
`scripts/` closes both gaps:

| File | What it does |
|---|---|
| `scripts/start-onelife.ps1` | Starts Docker Desktop, waits for the engine, `compose up -d` **with the prod overlay**, then checks `http://127.0.0.1:8082/api/health`. Idempotent — safe to run any time to bring the site back. |
| `scripts/lock-after-autologon.ps1` | Locks the workstation after an *automatic* sign-in (skipped if you signed in deliberately, i.e. >5 min after boot). |
| `scripts/install-autostart.ps1` | Registers both as at-logon scheduled tasks (`OneLife Autostart`, `OneLife Lock After Autologon`). Re-run to update them. |

For unattended reboots (nightly Windows Update), also enable automatic sign-in —
[Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon)
stores the password as an LSA secret rather than plaintext in the registry. The lock
task then puts the machine straight back behind the lock screen.

Logs land in `%LOCALAPPDATA%\OneLife\autostart.log`.

## Notes / deferred

This slice intentionally defers (see docs for the full design): 2FA + real auth,
pgvector agent-memory embeddings & cross-player leakage, image/audio generation,
Redis, MinIO, the onboarding manual/quiz, and the world/city maps. The pieces it
*does* prove: the story graph engine, the condition/effect DSL, the AI dialogue
gate (Actor/Referee/Applier), the woven-clue puzzle system, progress scoring, the
leaderboard, and the uniform seq-stamped **rollback** across all runtime state.

## Layout

```
db/        Postgres schema (DDL only; content comes from the data repo)
api/       FastAPI backend (story engine, gates, puzzles, memory, content loader)
web/       Svelte + Vite frontend (text-stream UI)
Makefile   up / down / reset / seed / lint shortcuts
*.md       Design docs
```

The **game data is a separate repo**: this is the data-agnostic engine + editor.
The YAML world lives in a sibling repo (default `../OneLife-KBK-mystery`), mounted
into the API at `/content`. Run a different dataset with `GAME_DATA_DIR` in `.env`.

## Authoring content

Story, NPCs, puzzles, and clues live as `*.yaml` in the **data repo**
(`../OneLife-KBK-mystery`), loaded by a validating seeder. `make lint` checks
references and proves the spine is completable; `make seed` loads it. See
[AUTHORING.md](AUTHORING.md). (The API also auto-seeds on startup, so
`docker compose up` is self-contained.)
