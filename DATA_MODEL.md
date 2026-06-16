# OneLife — Data Model / DB Schema (draft)

> First-pass relational schema for **PostgreSQL + pgvector**, derived from [GAME_DESIGN.md](GAME_DESIGN.md). Names and columns are illustrative; this is meant to make the design concrete and surface gaps, not to be migrated as-is. SQL is written for clarity over optimization.

---

## Entity overview

```
players ──< player_sessions
players ──< game_logs >── log_entries        (the log = your progress; rollback truncates it)
players ──< progress_events                  (points for actions/items/locations/dialogue)
players ──> leaderboard (derived/materialized view)

world_cells (grid) ──< locations ──< location_interactions
locations >── assets (image/audio, reusable)

characters (agents) ──< agent_memories       (vector + time + location attached)
characters ──< conversations >── messages    (saved; can leak between agents)

world_events (append-only global timeline) ── feeds memory leakage & cross-player spread
```

Three "agent" kinds are modeled today: **characters (NPCs)** and **locations**; the schema leaves room for "possibly more" (design §5) via a generic `agents` view if needed later.

---

## Core tables

### players & auth

```sql
CREATE TABLE players (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           CITEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    totp_secret     TEXT,                       -- 2FA (design §9)
    totp_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    display_name    TEXT UNIQUE NOT NULL,
    onboarded       BOOLEAN NOT NULL DEFAULT FALSE,   -- passed manual + quiz (design §6)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE player_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id       UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    -- the player's current position in world + story
    location_id     UUID REFERENCES locations(id),
    story_time      BIGINT NOT NULL DEFAULT 0,  -- in-game timeline cursor (see world_events)
    active_log_id   UUID REFERENCES game_logs(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### world: grid, locations, interactions

```sql
-- The grid over the world: cities + wilderness (design §4)
CREATE TABLE world_cells (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grid_x      INTEGER NOT NULL,
    grid_y      INTEGER NOT NULL,
    kind        TEXT NOT NULL,                  -- 'city' | 'town' | 'village' | 'wilderness'
    name        TEXT,                           -- e.g. 'Södra Sandby'
    population  INTEGER,                        -- drives location count (10M≈100, village≈10)
    UNIQUE (grid_x, grid_y)
);

CREATE TABLE locations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cell_id     UUID NOT NULL REFERENCES world_cells(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,                  -- e.g. 'Killebäckskolan'
    description TEXT NOT NULL,                  -- authored static text
    is_start    BOOLEAN NOT NULL DEFAULT FALSE, -- the Killebäckskolan opening (design §3)
    image_asset_id UUID REFERENCES assets(id),
    music_asset_id UUID REFERENCES assets(id),
    sfx_asset_id   UUID REFERENCES assets(id),
    theme_key   TEXT,                           -- groups similar locations for asset reuse (§8)
    danger      SMALLINT NOT NULL DEFAULT 0     -- world is "dangerous" (§1)
);

-- What/who is here and what you can do (design §4)
CREATE TABLE location_interactions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id UUID NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                  -- 'examine' | 'talk' | 'move' | 'puzzle' | ...
    label       TEXT NOT NULL,
    character_id UUID REFERENCES characters(id),-- if it's a "who"
    payload     JSONB NOT NULL DEFAULT '{}'     -- hints, puzzle refs, gated AI step config
);
```

### assets (reusable images / audio)

```sql
CREATE TABLE assets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        TEXT NOT NULL,                  -- 'image' | 'music' | 'sfx'
    storage_key TEXT NOT NULL,                  -- MinIO/S3 object key
    theme_key   TEXT,                           -- enables reuse across similar locations (§8)
    prompt      TEXT,                           -- generation prompt, for regeneration/audit
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### characters (NPC agents) & memory

```sql
CREATE TABLE characters (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    persona      TEXT NOT NULL,                 -- system-prompt seed for the agent
    home_location_id UUID REFERENCES locations(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Agent memory: vector-searchable, with time + location attached (design §5)
CREATE TABLE agent_memories (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,                 -- what is remembered
    embedding    VECTOR(1536),                  -- pgvector, for relevance retrieval
    location_id  UUID REFERENCES locations(id), -- where it "happened"
    story_time   BIGINT NOT NULL,               -- when it happened (for timeline-safe leaks)
    source       TEXT NOT NULL,                 -- 'observed' | 'told' | 'leaked'
    origin_player_id UUID REFERENCES players(id),-- whose story it came from (cross-player spread)
    confidence   REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX ON agent_memories USING hnsw (embedding vector_cosine_ops);

-- Which memories an agent is allowed to "know" from another agent, plus the
-- believable in-world explanation generated for how they came to know it (§5).
CREATE TABLE memory_shares (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id     UUID NOT NULL REFERENCES agent_memories(id) ON DELETE CASCADE,
    to_character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    explanation   TEXT NOT NULL,                -- generated: how they know it
    via_location_id UUID REFERENCES locations(id),
    via_story_time  BIGINT NOT NULL
);
```

### conversations (saved; leak between agents)

```sql
CREATE TABLE conversations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    character_id UUID NOT NULL REFERENCES characters(id),
    location_id  UUID REFERENCES locations(id),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,              -- 'player' | 'agent' | 'system'
    content         TEXT NOT NULL,
    story_time      BIGINT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Progress, log, and rollback

The **log is the player's progress**, and **rollback truncates the log** at a cost (design §2, §7).

```sql
CREATE TABLE game_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only ordered steps. Rollback = mark entries above a seq as rolled_back
-- (kept for audit) and recompute progress to that point.
CREATE TABLE log_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id      UUID NOT NULL REFERENCES game_logs(id) ON DELETE CASCADE,
    seq         BIGINT NOT NULL,               -- 0,1,2,... ordering within the log
    story_time  BIGINT NOT NULL,
    location_id UUID REFERENCES locations(id),
    summary     TEXT NOT NULL,                 -- human-readable "what happened"
    detail      JSONB NOT NULL DEFAULT '{}',
    rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (log_id, seq)
);

-- Progress points for actions/items/locations/dialogue outcomes (design §7)
CREATE TABLE progress_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    log_entry_id UUID REFERENCES log_entries(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL,                -- 'action' | 'item' | 'location' | 'dialogue'
    points       INTEGER NOT NULL,
    voided       BOOLEAN NOT NULL DEFAULT FALSE, -- set TRUE when rolled back past
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Leaderboard** (design §7) is a sum of non-voided progress, cheap to read as a materialized view (or kept hot in a Redis sorted set):

```sql
CREATE MATERIALIZED VIEW leaderboard AS
SELECT p.id AS player_id,
       p.display_name,
       COALESCE(SUM(pe.points) FILTER (WHERE NOT pe.voided), 0) AS progress
FROM players p
LEFT JOIN progress_events pe ON pe.player_id = p.id
GROUP BY p.id, p.display_name
ORDER BY progress DESC;
```

---

## Global timeline & cross-player spread

An append-only event log is the source of truth for the shared world clock and for what may leak between players (design §1, §5, §11).

```sql
CREATE TABLE world_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    story_time  BIGINT NOT NULL,               -- authoritative ordering
    player_id   UUID REFERENCES players(id),   -- originating player (nullable for authored events)
    location_id UUID REFERENCES locations(id),
    character_id UUID REFERENCES characters(id),
    kind        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON world_events (story_time);
```

**Timeline-safe leakage rule (proposed):** a memory/event may only leak into another player's game if its `story_time` ≤ that player's current `player_sessions.story_time`. This keeps shared stories from contradicting a player's private progression (the §11 open question).

---

## Authored content vs. runtime state

- **Authored (seeded) :** `world_cells`, `locations`, `location_interactions`, base `characters`, base `assets`, authored `world_events`, static story text.
- **Runtime (per player):** `player_sessions`, `game_logs`/`log_entries`, `progress_events`, `conversations`/`messages`, runtime `agent_memories`, `memory_shares`, player-originated `world_events`.

This split matters for the **story tree**: the static between-step story lives in authored tables, while choices and AI dialogue produce runtime rows — keeping the authored spine intact even as the world reacts.

---

## Gaps to resolve next

- **Story-tree representation:** model the authored nodes/edges (a `story_nodes` + `story_choices` pair, or keep it in `location_interactions.payload`?). Needs its own pass.
- **Puzzles:** where puzzle definitions and embedded-hint links live (design §3).
- **AI gate steps:** schema for "talk your way through" gates — success criteria, attempt limits, low-fail-risk tuning (design §2, §11).
- **Embedding dimension** (1536 above) depends on the chosen embedding model.
