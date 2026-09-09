-- OneLife vertical-slice schema (subset of DATA_MODEL.md / STORY_AND_PUZZLES.md).
-- pgvector / 2FA / object storage are deferred for the slice; the runtime tables
-- carry the rollback seq-stamp invariant from STORY_AND_PUZZLES.md §6.
--
-- MULTI-GAME: the server hosts several games concurrently. Every authored content
-- row and every per-player runtime row is stamped with a `game_id` (FK -> games).
-- Content tables use a COMPOSITE primary key (game_id, id) so two games can reuse
-- the same human-readable slug (e.g. both have a node 'entry') without colliding,
-- and inter-content FKs carry game_id so a reference can never cross games. A
-- player has an independent, resumable save per game (player_games), and the
-- rollback seq-stamp invariant is scoped per (player, game). See migrations.py for
-- the in-place migration that brings an already-provisioned volume up to this shape.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------- Games (one row per games/<id>/data dataset) ----------
CREATE TABLE games (
    id            TEXT PRIMARY KEY,              -- folder name under games/<id>/data
    title         TEXT NOT NULL DEFAULT '',
    subtitle      TEXT NOT NULL DEFAULT '',
    first_summary TEXT NOT NULL DEFAULT 'You wake.',  -- first log line at the entry node
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-game translations of authored player-facing text. English is the base language
-- (the *.yaml under games/<id>/data/); a row here overrides one English leaf for one
-- language. Missing row = fall back to the English base column. entity_type/entity_id
-- name the content row; field_path addresses the (possibly nested JSONB) leaf, e.g.
-- 'body', 'body_variants[0].body', 'knowledge_boundary.tone', 'solution.set[1]'. Authored
-- as games/<id>/i18n/<lang>.yaml sidecars and seeded by content.seed_translations. Pure
-- display data (no player FK), so reseeding just upserts + prunes. See i18n.py.
CREATE TABLE content_translations (
    game_id     TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    lang        TEXT NOT NULL,                 -- BCP-47: 'sv', 'sv-FI', 'de'
    entity_type TEXT NOT NULL,                 -- 'game'|'games'|'story_nodes'|'story_edges'|'characters'|'locations'|'world_cells'|'puzzles'|'puzzle_clues'|'dialogue_gates'
    entity_id   TEXT NOT NULL,                 -- content slug (the game_id itself for 'game'/'games')
    field_path  TEXT NOT NULL,                 -- dotted + [i] path to the leaf
    text        TEXT NOT NULL,
    src_hash    TEXT NOT NULL DEFAULT '',      -- sha1 of the English leaf at translate time (drift detection)
    PRIMARY KEY (game_id, lang, entity_type, entity_id, field_path)
);
CREATE INDEX content_translations_lookup
    ON content_translations (game_id, lang, entity_type, entity_id);

-- ---------- Players & sessions (auth simplified for the slice: token only) ----------
CREATE TABLE players (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email          TEXT UNIQUE NOT NULL,
    password_hash  TEXT NOT NULL,
    display_name   TEXT UNIQUE NOT NULL,
    totp_secret    TEXT,                         -- 2FA shared secret
    totp_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
    totp_last_step BIGINT,                       -- last spent TOTP step (blocks replay)
    onboarded      BOOLEAN NOT NULL DEFAULT FALSE, -- passed manual + quiz (account-level)
    clipboard      TEXT NOT NULL DEFAULT '',       -- player's free-form clipboard (never auto-edited)
    active_game_id TEXT REFERENCES games(id),      -- which save is selected; NULL = in the lobby
    language       TEXT NOT NULL DEFAULT 'en',     -- account-level play language (BCP-47; 'en' = base)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One session row per player; token rotates on login. Auth ONLY — the game
-- position lives per-game in player_games (a player has many saves, one login).
-- Only the SHA-256 of the bearer token is stored, so a dump of this table (an
-- admin export, a backup) contains no usable session.
CREATE TABLE player_sessions (
    token_hash   TEXT UNIQUE NOT NULL,
    player_id    UUID PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    expires_at   TIMESTAMPTZ,                 -- session TTL; null = never (legacy)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The per-(player, game) save slot: where this player is in this game. Independent
-- and resumable — selecting a game in the lobby points players.active_game_id here.
CREATE TABLE player_games (
    player_id      UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id        TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    current_node   TEXT,
    story_time     BIGINT NOT NULL DEFAULT 0,
    log_id         UUID,
    language       TEXT,          -- optional per-(player,game) language override (NULL = use players.language)
    clipboard      TEXT NOT NULL DEFAULT '',  -- this game's free-form notes (per-game; never leaks between games)
    last_played_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, game_id)
);

-- One-time 2FA backup codes (bcrypt-hashed).
CREATE TABLE recovery_codes (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- World ----------
-- A grid of cells (villages, cities, wilderness). Locations live inside a cell;
-- traveling the world map moves between cells, arriving at the cell's arrival_node.
CREATE TABLE world_cells (
    game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    grid_x       INTEGER NOT NULL,
    grid_y       INTEGER NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'town',  -- city | town | village | wilderness
    region       TEXT NOT NULL DEFAULT '',      -- used by atmosphere ("<place> · <region> · 1992")
    arrival_node TEXT,                           -- node you arrive at when traveling here
    map          JSONB NOT NULL DEFAULT '{}',    -- this cell's ellipse on the WORLD map {x,y,rx,ry} (normalized 0..1)
    map_image    TEXT,                           -- this cell's own map background (path under the content repo)
    world_exit   JSONB NOT NULL DEFAULT '{}',    -- the "leave town" hotspot on this cell's map {x,y,rx,ry}
    PRIMARY KEY (game_id, id)
);

CREATE TABLE locations (
    game_id     TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id          TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cell_id     TEXT,
    PRIMARY KEY (game_id, id),
    FOREIGN KEY (game_id, cell_id) REFERENCES world_cells(game_id, id)
);

CREATE TABLE characters (
    game_id     TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id          TEXT NOT NULL,
    name        TEXT NOT NULL,
    persona     TEXT NOT NULL,
    reveal_name TEXT,          -- personal name shown once revealed/guessed in chat
    PRIMARY KEY (game_id, id)
);

-- ---------- Story graph ----------
CREATE TABLE story_arcs (
    game_id  TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id       TEXT NOT NULL,
    title    TEXT NOT NULL,
    is_spine BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (game_id, id)
);

CREATE TABLE story_nodes (
    game_id     TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id          TEXT NOT NULL,
    arc_id      TEXT NOT NULL,
    type        TEXT NOT NULL,                 -- narration|choice|gate|puzzle|location|death|ending
    location_id TEXT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,                  -- fallback description text
    body_variants JSONB NOT NULL DEFAULT '[]',  -- [{when:<condition>, body:<text>}]; first match wins
    is_entry    BOOLEAN NOT NULL DEFAULT FALSE,
    is_death    BOOLEAN NOT NULL DEFAULT FALSE,
    world_access BOOLEAN NOT NULL DEFAULT FALSE, -- can open the world map from here
    gate_id     TEXT,
    puzzle_id   TEXT,
    media       JSONB NOT NULL DEFAULT '{}',
    map         JSONB NOT NULL DEFAULT '{}',     -- this node's ellipse on its cell map {x,y,rx,ry} (normalized 0..1)
    PRIMARY KEY (game_id, id),
    FOREIGN KEY (game_id, arc_id) REFERENCES story_arcs(game_id, id),
    FOREIGN KEY (game_id, location_id) REFERENCES locations(game_id, id)
);

CREATE TABLE story_edges (
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id         TEXT NOT NULL,
    from_node  TEXT NOT NULL,
    to_node    TEXT NOT NULL,
    label      TEXT NOT NULL,
    conditions JSONB NOT NULL DEFAULT '{"all":[]}',
    effects    JSONB NOT NULL DEFAULT '{}',
    danger     SMALLINT NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    road       JSONB NOT NULL DEFAULT '[]',  -- map spline waypoints [[x,y],…] (normalized)
    PRIMARY KEY (game_id, id),
    FOREIGN KEY (game_id, from_node) REFERENCES story_nodes(game_id, id) ON DELETE CASCADE,
    FOREIGN KEY (game_id, to_node) REFERENCES story_nodes(game_id, id)
);

-- ---------- Dialogue gates ----------
CREATE TABLE dialogue_gates (
    game_id       TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    location_id   TEXT,
    character_id  TEXT,
    spec          JSONB NOT NULL,                -- criteria, knowledge_boundary, hints, effects
    PRIMARY KEY (game_id, id),
    FOREIGN KEY (game_id, location_id) REFERENCES locations(game_id, id),
    FOREIGN KEY (game_id, character_id) REFERENCES characters(game_id, id)
);

-- ---------- Puzzles ----------
CREATE TABLE puzzles (
    game_id        TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id             TEXT NOT NULL,
    type           TEXT NOT NULL,               -- combination|riddle|assembly|semantic
    prompt         TEXT NOT NULL,
    solution       JSONB NOT NULL,              -- {kind, value|set|intent}
    required_clues TEXT[] NOT NULL DEFAULT '{}',
    hint_ladder    JSONB NOT NULL DEFAULT '[]',
    on_solve       JSONB NOT NULL DEFAULT '{}',
    on_fail        JSONB NOT NULL DEFAULT '{}',   -- effects applied on a wrong answer (e.g. advance_story_time)
    PRIMARY KEY (game_id, id)
);

CREATE TABLE puzzle_clues (
    game_id             TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id                  TEXT NOT NULL,
    puzzle_id           TEXT NOT NULL,
    placement           JSONB NOT NULL,
    reveal_text         TEXT NOT NULL,
    discover_conditions JSONB NOT NULL DEFAULT '{"all":[]}',
    PRIMARY KEY (game_id, id),
    FOREIGN KEY (game_id, puzzle_id) REFERENCES puzzles(game_id, id) ON DELETE CASCADE
);

-- ---------- Log & progress (the rollback spine) ----------
CREATE TABLE game_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE log_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id      UUID NOT NULL REFERENCES game_logs(id) ON DELETE CASCADE,
    game_id     TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seq         BIGINT NOT NULL,
    story_time  BIGINT NOT NULL DEFAULT 0,
    node_id     TEXT,
    summary     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'action',  -- action|scene|dialogue|puzzle|clue|travel|death|system
    rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (log_id, seq)
);

CREATE TABLE progress_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seq          BIGINT NOT NULL,              -- log seq that created it (rollback voiding)
    kind         TEXT NOT NULL,                -- action|item|location|dialogue|puzzle
    points       INTEGER NOT NULL,
    voided       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Runtime player state (all seq-stamped for rollback, scoped per game) ----------
CREATE TABLE player_flags (
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    flag       TEXT NOT NULL,
    set_at_seq BIGINT NOT NULL,
    PRIMARY KEY (player_id, game_id, flag)
);

CREATE TABLE player_clues (
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    clue_id      TEXT NOT NULL,
    found_at_seq BIGINT NOT NULL,
    PRIMARY KEY (player_id, game_id, clue_id),
    FOREIGN KEY (game_id, clue_id) REFERENCES puzzle_clues(game_id, id)
);

-- Fog-of-war: which world cells a player has discovered (rollback-safe).
CREATE TABLE player_cells (
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    cell_id      TEXT NOT NULL,
    found_at_seq BIGINT NOT NULL,
    PRIMARY KEY (player_id, game_id, cell_id),
    FOREIGN KEY (game_id, cell_id) REFERENCES world_cells(game_id, id)
);

CREATE TABLE puzzle_progress (
    player_id     UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id       TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    puzzle_id     TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    hint_level    INTEGER NOT NULL DEFAULT 0,
    solved        BOOLEAN NOT NULL DEFAULT FALSE,
    solved_at_seq BIGINT,
    PRIMARY KEY (player_id, game_id, puzzle_id),
    FOREIGN KEY (game_id, puzzle_id) REFERENCES puzzles(game_id, id)
);

CREATE TABLE gate_attempts (
    player_id    UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    gate_id      TEXT NOT NULL,
    criteria_met JSONB NOT NULL DEFAULT '[]',
    attempts     INTEGER NOT NULL DEFAULT 0,
    hint_level   INTEGER NOT NULL DEFAULT 0,
    satisfied    BOOLEAN NOT NULL DEFAULT FALSE,
    passed_at_seq BIGINT,
    PRIMARY KEY (player_id, game_id, gate_id),
    FOREIGN KEY (game_id, gate_id) REFERENCES dialogue_gates(game_id, id)
);

-- Conversation transcript for a gate (kept minimal for the slice)
CREATE TABLE gate_messages (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id  UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    gate_id    TEXT NOT NULL,
    role       TEXT NOT NULL,                  -- player|agent
    content    TEXT NOT NULL,
    seq        BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- D&D-style alignment drift: one append-only, seq-stamped row per judged action,
-- carrying the delta AND the resulting clamped coordinate. Current = latest
-- surviving row; trail = the history. Rollback deletes rows with created_seq > N.
CREATE TABLE player_alignment_events (
    id              BIGSERIAL PRIMARY KEY,
    player_id       UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id         TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    created_seq     BIGINT NOT NULL,            -- log seq; DELETE rows with created_seq > target on rollback
    good_evil_delta DOUBLE PRECISION NOT NULL,  -- this action's shift, +good / -evil
    law_chaos_delta DOUBLE PRECISION NOT NULL,  -- this action's shift, +lawful / -chaotic
    good_evil       DOUBLE PRECISION NOT NULL,  -- cumulative, clamped [-1,1]
    law_chaos       DOUBLE PRECISION NOT NULL,  -- cumulative, clamped [-1,1]
    reason          TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,              -- gate | edge
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX player_alignment_events_player_idx ON player_alignment_events (player_id, game_id, id);

-- Static memo of an edge label's alignment shift (judged by the LLM once, reused on
-- every traversal). Player-independent; never rolled back. Scoped per game.
CREATE TABLE edge_alignment_cache (
    game_id         TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    edge_id         TEXT NOT NULL,
    good_evil_delta DOUBLE PRECISION NOT NULL,
    law_chaos_delta DOUBLE PRECISION NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, edge_id)
);

-- ---------- Authoring event log (admin content editor) ----------
-- The authored world is canonical as: baseline #0 (the YAML seed) + replay of
-- these events up to `head`. The content tables above are a materialized cache
-- kept in sync on every apply/undo/redo. See the admin content editor.
CREATE TABLE IF NOT EXISTS content_events (
    seq         BIGSERIAL PRIMARY KEY,
    op          TEXT NOT NULL,                 -- create | update | delete | move
    kind        TEXT NOT NULL,                 -- nodes | edges | gates | ... (content kind)
    entity_id   TEXT NOT NULL,
    before      JSONB,                         -- prior value (for undo); null for create
    after       JSONB,                         -- new value; null for delete
    extra       JSONB,                         -- auto-generated entities applied/reverted with this event
    admin_email TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Single-row HEAD pointer: events with seq <= head are applied, > head are redoable.
CREATE TABLE IF NOT EXISTS content_log_state (
    id   INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    head BIGINT NOT NULL DEFAULT 0
);
INSERT INTO content_log_state (id, head) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

-- Editor layout for the graph view. NOT content, NOT exported to YAML. Scoped per game.
CREATE TABLE IF NOT EXISTS node_positions (
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    x       DOUBLE PRECISION NOT NULL,
    y       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (game_id, node_id)
);

-- Per-(player, game) standing: one row per save, with live (non-voided) progress.
-- The /api/leaderboard endpoint ranks within a single game_id.
CREATE VIEW leaderboard AS
SELECT pg.player_id,
       p.display_name,
       pg.game_id,
       COALESCE(SUM(pe.points) FILTER (WHERE NOT pe.voided), 0)::int AS progress
FROM player_games pg
JOIN players p ON p.id = pg.player_id
LEFT JOIN progress_events pe ON pe.player_id = pg.player_id AND pe.game_id = pg.game_id
GROUP BY pg.player_id, p.display_name, pg.game_id;
