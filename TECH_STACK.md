# OneLife — Tech Stack Proposal

> First-pass recommendations. Each section gives a **recommendation** plus the reasoning and the main alternative, so we can revisit individual choices without re-deciding everything. Driven by the requirements in [GAME_DESIGN.md](GAME_DESIGN.md): browser play, 2FA, Dockerized + easy-to-setup backend, a DB for locations/characters/conversations, AI-driven dialogue, per-location image/audio generation, leaderboard, and cross-player memory leakage.

---

## Summary (the stack at a glance)

| Layer | Recommendation | Why |
|---|---|---|
| Frontend | **SvelteKit + TypeScript** | Text-stream UI is light; Svelte is small/fast and pleasant. (Alt: Next.js/React for bigger hiring pool.) |
| Backend | **Python + FastAPI** | First-class AI/embeddings ecosystem; async; clean OpenAPI. (Alt: Node/TS Fastify for one-language stack.) |
| Database | **PostgreSQL + pgvector** | Relational world data **and** vector search for agent memory in one engine. |
| Cache / queue | **Redis** | Sessions, rate-limits, leaderboard (sorted sets), background-job queue. |
| Object storage | **MinIO** (S3-compatible) | Stores generated images/audio; swappable for real S3 in prod. |
| AI dialogue | **Pluggable LLM provider** (`LLM_PROVIDER`): Claude API, in-browser **WebGPU/WebLLM**, or offline stub | Drives NPC/agent conversations. Browser mode runs the model on the player's own GPU (no server key/GPU); see AI_DIALOGUE_GATES.md §5b. |
| Image gen | Pluggable provider behind our own interface | Per-location art; cache aggressively (see design §8). |
| Audio gen | Pluggable provider + reuse library | Music/SFX reused across similar locations. |
| Auth | **FastAPI + TOTP 2FA** (`pyotp`) | Email/password + authenticator-app second factor. |
| Packaging | **Docker Compose** | One `docker compose up` brings up the whole stack. |

---

## Frontend

**Recommendation: SvelteKit + TypeScript.**

- The core UI is a **scrolling text stream** with choice buttons, a log/rollback panel, a map view, and a leaderboard popup — not a heavy SPA. Svelte keeps the bundle small and the code simple.
- Streamed AI responses map naturally to **server-sent events (SSE)** for the typewriter/text-stream feel.
- Map rendering (world grid + city maps) can start as simple SVG/Canvas.

*Alternative:* Next.js/React — choose this if we want the larger ecosystem/hiring pool or plan a much richer client later.

---

## Backend

**Recommendation: Python + FastAPI.**

- The hard part of this game is the **AI + memory** layer (prompting, embeddings, retrieval, cross-agent memory leakage). Python has the strongest ecosystem here.
- FastAPI is async (good for fan-out AI calls), auto-generates OpenAPI docs, and pairs well with Pydantic for validating the structured outputs we'll want from the model.
- Background work (image/audio generation, embedding, memory propagation) runs via a worker queue (**Celery/RQ + Redis**, or FastAPI background tasks to start).

*Alternative:* Node.js + TypeScript (Fastify) — pick this if a single language across front/back is the priority.

---

## Database

**Recommendation: PostgreSQL with the `pgvector` extension.**

- Relational tables hold the structured world (locations, characters, grid, logs, progress).
- `pgvector` gives **semantic memory retrieval** in the same DB — essential for agents recalling relevant memories and for the "believable shared-memory" feature (design §5).
- `JSONB` columns hold flexible, evolving blobs (e.g. agent state, choice metadata) without constant migrations.
- One engine = simpler Docker setup, which the design explicitly wants.

See [DATA_MODEL.md](DATA_MODEL.md) for the schema.

---

## AI / Generation layer

- **Dialogue:** provider-selectable via `LLM_PROVIDER` —
  - **anthropic** — Claude API, two-tier (`claude-haiku-4-5` routine, `claude-opus-4-8` for hard gate steps, design §2);
  - **browser** — the model runs in the player's browser on WebGPU (`@mlc-ai/web-llm`); the server builds prompts and the browser executes them (2-phase inference broker, AI_DIALOGUE_GATES.md §5b);
  - **stub** — deterministic offline heuristics (zero config).
- **Structured outputs:** the same JSON schema drives both Anthropic tool-use and WebLLM's grammar-constrained `response_format` json_schema, so AI results (e.g. "did the player satisfy this gate?", progress deltas) are machine-checkable and can't derail the authored story tree — and the server re-validates every verdict regardless of provider.
- **Image & audio:** wrap each provider behind our own `MediaGenerator` interface so providers are swappable. **Cache by a location/theme key** and reuse across similar locations (design §4, §8). Store artifacts in MinIO; store only the key + metadata in Postgres.

---

## Auth & Security

- Email/password + **TOTP 2FA** (`pyotp`), QR provisioning for authenticator apps.
- Sessions via secure, httpOnly cookies backed by Redis; CSRF protection on mutating routes.
- Rate-limit AI endpoints per player (cost control + abuse prevention).

---

## Local Dev / Deployment

- **Docker Compose** services: `web` (SvelteKit), `api` (FastAPI), `worker`, `postgres` (pgvector image), `redis`, `minio`.
- `.env.example` + a `make up` / `docker compose up` flow so a fresh clone runs end-to-end (design req: "easy to set up").
- DB migrations via **Alembic**.

---

## Open technical questions (carried from design §11)

- **Global timeline consistency** across concurrent sessions — needs a concrete model (event log + per-player view? authoritative world clock?). Likely an append-only `world_events` table feeding per-player visibility.
- **Memory-leak coherence** — how to prevent shared memories from contradicting a player's private timeline. Probably: only leak memories whose attached time ≤ the receiving player's current story time.
- **Cost ceiling** for per-location media generation at world scale — aggressive caching + reuse, and possibly lazy generation (generate on first visit).
