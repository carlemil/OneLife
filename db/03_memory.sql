-- OneLife agent-memory + cross-player leakage (MEMORY_AND_LEAKAGE.md).
-- Requires the pgvector extension (the db service uses the pgvector image).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_memories (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id     TEXT NOT NULL REFERENCES characters(id),
    content          TEXT NOT NULL,
    embedding        vector(256),
    location_id      TEXT REFERENCES locations(id),
    story_time       BIGINT NOT NULL DEFAULT 0,
    source           TEXT NOT NULL DEFAULT 'told',   -- observed | told | leaked
    -- SET NULL so deleting a player doesn't fail; their leaked memories persist
    -- as anonymous "world gossip".
    origin_player_id UUID REFERENCES players(id) ON DELETE SET NULL,
    created_seq      BIGINT NOT NULL DEFAULT 0,      -- creating player's log seq (rollback)
    voided           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Small data in the slice → sequential scan is fine; add an hnsw index for scale.
CREATE INDEX agent_memories_char_idx ON agent_memories (character_id);

-- Cache of generated location images, keyed by media theme (reused across
-- similar locations). image_url may be a remote URL or a data: URI.
CREATE TABLE generated_images (
    theme      TEXT PRIMARY KEY,
    image_url  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
