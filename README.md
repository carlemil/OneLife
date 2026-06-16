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

### Claude API (optional)

The dialogue gate runs a **deterministic offline stub by default**, so it's fully
playable with no key. To use the real Actor/Referee loop against Claude:

```bash
cp .env.example .env
# put your key in .env:  ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

`GET /api/health` reports `using_real_llm: true/false`.

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

## Notes / deferred

This slice intentionally defers (see docs for the full design): 2FA + real auth,
pgvector agent-memory embeddings & cross-player leakage, image/audio generation,
Redis, MinIO, the onboarding manual/quiz, and the world/city maps. The pieces it
*does* prove: the story graph engine, the condition/effect DSL, the AI dialogue
gate (Actor/Referee/Applier), the woven-clue puzzle system, progress scoring, the
leaderboard, and the uniform seq-stamped **rollback** across all runtime state.

## Layout

```
content/   Game content authored in YAML (loaded into Postgres; see AUTHORING.md)
db/        Postgres schema (DDL only; content comes from content/)
api/       FastAPI backend (story engine, gates, puzzles, memory, content loader)
web/       Svelte + Vite frontend (text-stream UI)
Makefile   up / down / reset / seed / lint shortcuts
*.md       Design docs
```

## Authoring content

Story, NPCs, puzzles, and clues live in `content/*.yaml`, loaded by a validating
seeder. `make lint` checks references and proves the spine is completable;
`make seed` loads it. See [AUTHORING.md](AUTHORING.md). (The API also auto-seeds
on startup, so `docker compose up` is self-contained.)
