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
    origin_player_id UUID REFERENCES players(id),
    created_seq      BIGINT NOT NULL DEFAULT 0,      -- creating player's log seq (rollback)
    voided           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Small data in the slice → sequential scan is fine; add an hnsw index for scale.
CREATE INDEX agent_memories_char_idx ON agent_memories (character_id);
