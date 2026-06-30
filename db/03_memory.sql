-- OneLife agent-memory + cross-player leakage (MEMORY_AND_LEAKAGE.md).
-- Requires the pgvector extension (the db service uses the pgvector image).
-- Multi-game: memories are scoped per game so an NPC in one game never recalls
-- (or leaks) anything from another game's world.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_memories (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id          TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    character_id     TEXT NOT NULL,
    content          TEXT NOT NULL,
    embedding        vector(256),
    location_id      TEXT,                          -- soft reference (no FK; informational)
    story_time       BIGINT NOT NULL DEFAULT 0,
    source           TEXT NOT NULL DEFAULT 'told',   -- observed | told | leaked
    -- SET NULL so deleting a player doesn't fail; their leaked memories persist
    -- as anonymous "world gossip".
    origin_player_id UUID REFERENCES players(id) ON DELETE SET NULL,
    created_seq      BIGINT NOT NULL DEFAULT 0,      -- creating player's log seq (rollback)
    voided           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (game_id, character_id) REFERENCES characters(game_id, id) ON DELETE CASCADE
);

-- Small data in the slice → sequential scan is fine; add an hnsw index for scale.
CREATE INDEX agent_memories_char_idx ON agent_memories (game_id, character_id);

-- Cache of generated location images, keyed by (game, media theme) (reused across
-- similar locations within a game). image_url may be a remote URL or a data: URI.
CREATE TABLE generated_images (
    game_id    TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    theme      TEXT NOT NULL,
    image_url  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, theme)
);
