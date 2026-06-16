-- OneLife vertical-slice schema (subset of DATA_MODEL.md / STORY_AND_PUZZLES.md).
-- pgvector / 2FA / object storage are deferred for the slice; the runtime tables
-- carry the rollback seq-stamp invariant from STORY_AND_PUZZLES.md §6.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------- Players & sessions (auth simplified for the slice: token only) ----------
CREATE TABLE players (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT UNIQUE NOT NULL,
    totp_secret   TEXT,                         -- 2FA shared secret
    totp_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    onboarded     BOOLEAN NOT NULL DEFAULT FALSE, -- passed manual + quiz
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One session row per player; token rotates on login, game state persists.
CREATE TABLE player_sessions (
    token        UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    player_id    UUID PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    current_node TEXT,
    story_time   BIGINT NOT NULL DEFAULT 0,
    log_id       UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- World ----------
CREATE TABLE locations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE characters (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    persona   TEXT NOT NULL
);

-- ---------- Story graph ----------
CREATE TABLE story_arcs (
    id       TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    is_spine BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE story_nodes (
    id          TEXT PRIMARY KEY,
    arc_id      TEXT NOT NULL REFERENCES story_arcs(id),
    type        TEXT NOT NULL,                 -- narration|choice|gate|puzzle|location|death|ending
    location_id TEXT REFERENCES locations(id),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    is_entry    BOOLEAN NOT NULL DEFAULT FALSE,
    is_death    BOOLEAN NOT NULL DEFAULT FALSE,
    gate_id     TEXT,
    puzzle_id   TEXT,
    media       JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE story_edges (
    id         TEXT PRIMARY KEY,
    from_node  TEXT NOT NULL REFERENCES story_nodes(id) ON DELETE CASCADE,
    to_node    TEXT NOT NULL REFERENCES story_nodes(id),
    label      TEXT NOT NULL,
    conditions JSONB NOT NULL DEFAULT '{"all":[]}',
    effects    JSONB NOT NULL DEFAULT '{}',
    danger     SMALLINT NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- ---------- Dialogue gates ----------
CREATE TABLE dialogue_gates (
    id            TEXT PRIMARY KEY,
    location_id   TEXT REFERENCES locations(id),
    character_id  TEXT REFERENCES characters(id),
    spec          JSONB NOT NULL                -- criteria, knowledge_boundary, hints, effects
);

-- ---------- Puzzles ----------
CREATE TABLE puzzles (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,               -- combination|riddle|assembly|semantic
    prompt         TEXT NOT NULL,
    solution       JSONB NOT NULL,              -- {kind, value|set|intent}
    required_clues TEXT[] NOT NULL DEFAULT '{}',
    hint_ladder    JSONB NOT NULL DEFAULT '[]',
    on_solve       JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE puzzle_clues (
    id                  TEXT PRIMARY KEY,
    puzzle_id           TEXT NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE,
    placement           JSONB NOT NULL,
    reveal_text         TEXT NOT NULL,
    discover_conditions JSONB NOT NULL DEFAULT '{"all":[]}'
);

-- ---------- Log & progress (the rollback spine) ----------
CREATE TABLE game_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE log_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id      UUID NOT NULL REFERENCES game_logs(id) ON DELETE CASCADE,
    seq         BIGINT NOT NULL,
    story_time  BIGINT NOT NULL DEFAULT 0,
    node_id     TEXT,
    summary     TEXT NOT NULL,
    rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (log_id, seq)
);

CREATE TABLE progress_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    seq          BIGINT NOT NULL,              -- log seq that created it (rollback voiding)
    kind         TEXT NOT NULL,                -- action|item|location|dialogue|puzzle
    points       INTEGER NOT NULL,
    voided       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Runtime player state (all seq-stamped for rollback) ----------
CREATE TABLE player_flags (
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    flag       TEXT NOT NULL,
    set_at_seq BIGINT NOT NULL,
    PRIMARY KEY (player_id, flag)
);

CREATE TABLE player_clues (
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    clue_id      TEXT NOT NULL REFERENCES puzzle_clues(id),
    found_at_seq BIGINT NOT NULL,
    PRIMARY KEY (player_id, clue_id)
);

CREATE TABLE puzzle_progress (
    player_id     UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    puzzle_id     TEXT NOT NULL REFERENCES puzzles(id),
    attempts      INTEGER NOT NULL DEFAULT 0,
    hint_level    INTEGER NOT NULL DEFAULT 0,
    solved        BOOLEAN NOT NULL DEFAULT FALSE,
    solved_at_seq BIGINT,
    PRIMARY KEY (player_id, puzzle_id)
);

CREATE TABLE gate_attempts (
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    gate_id      TEXT NOT NULL REFERENCES dialogue_gates(id),
    criteria_met JSONB NOT NULL DEFAULT '[]',
    attempts     INTEGER NOT NULL DEFAULT 0,
    hint_level   INTEGER NOT NULL DEFAULT 0,
    satisfied    BOOLEAN NOT NULL DEFAULT FALSE,
    passed_at_seq BIGINT,
    PRIMARY KEY (player_id, gate_id)
);

-- Conversation transcript for a gate (kept minimal for the slice)
CREATE TABLE gate_messages (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    gate_id    TEXT NOT NULL,
    role       TEXT NOT NULL,                  -- player|agent
    content    TEXT NOT NULL,
    seq        BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE VIEW leaderboard AS
SELECT p.id AS player_id,
       p.display_name,
       COALESCE(SUM(pe.points) FILTER (WHERE NOT pe.voided), 0)::int AS progress
FROM players p
LEFT JOIN progress_events pe ON pe.player_id = p.id
GROUP BY p.id, p.display_name
ORDER BY progress DESC, p.display_name;
