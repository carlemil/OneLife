"""Agent memory + cross-player/-character leakage (MEMORY_AND_LEAKAGE.md).

Writes happen in code (never by the LLM). Retrieval is filtered per player and
timeline-safe. Cross-character propagation is implemented and dormant until a
second NPC exists.
"""
from . import embeddings, llm


async def write_memory(conn, *, character_id, content, location_id, story_time,
                       origin_player_id, seq, source="told"):
    emb = embeddings.to_pgvector(embeddings.embed(content))
    await conn.execute(
        """INSERT INTO agent_memories
             (character_id, content, embedding, location_id, story_time,
              source, origin_player_id, created_seq)
           VALUES ($1,$2,$3::vector,$4,$5,$6,$7,$8)""",
        character_id, content, emb, location_id, story_time, source,
        origin_player_id, seq)
    await propagate_to_other_characters(
        conn, source_character_id=character_id, content=content,
        location_id=location_id, story_time=story_time,
        origin_player_id=origin_player_id, seq=seq)


async def retrieve(conn, *, character_id, query_text, player_id, story_time, k=4):
    """Return (own, leaked) memory contents for this character, timeline-safe."""
    emb = embeddings.to_pgvector(embeddings.embed(query_text))
    rows = await conn.fetch(
        """SELECT content, origin_player_id
           FROM agent_memories
           WHERE character_id=$1 AND NOT voided AND story_time <= $3
           ORDER BY embedding <=> $2::vector
           LIMIT $4""",
        character_id, emb, story_time, k)
    own, leaked = [], []
    for r in rows:
        (own if r["origin_player_id"] == player_id else leaked).append(r["content"])
    return own, leaked


async def propagate_to_other_characters(conn, *, source_character_id, content,
                                        location_id, story_time,
                                        origin_player_id, seq):
    """Share a memory to OTHER characters with a believable in-world explanation
    of how they came to know it. Dormant until a second NPC exists."""
    others = await conn.fetch(
        "SELECT id, name FROM characters WHERE id <> $1", source_character_id)
    if not others:
        return
    src = await conn.fetchrow("SELECT name FROM characters WHERE id=$1", source_character_id)
    for other in others:
        # Dedupe: skip if this character already knows the fact (a still-live
        # memory whose content starts with the same base fact). Checked before
        # the explanation LLM call so duplicates cost nothing.
        already = await conn.fetchval(
            """SELECT 1 FROM agent_memories
               WHERE character_id=$1 AND NOT voided AND starts_with(content, $2)
               LIMIT 1""",
            other["id"], content)
        if already:
            continue
        explanation = await llm.generate_share_explanation(
            from_character=src["name"] if src else source_character_id,
            to_character=other["name"], fact=content)
        leaked = f"{content} ({explanation})"
        emb = embeddings.to_pgvector(embeddings.embed(leaked))
        await conn.execute(
            """INSERT INTO agent_memories
                 (character_id, content, embedding, location_id, story_time,
                  source, origin_player_id, created_seq)
               VALUES ($1,$2,$3::vector,$4,$5,'leaked',$6,$7)""",
            other["id"], leaked, emb, location_id, story_time,
            origin_player_id, seq)


async def void_after(conn, player_id, seq):
    """Rollback: a player's memories created after `seq` un-happen."""
    await conn.execute(
        "UPDATE agent_memories SET voided=TRUE WHERE origin_player_id=$1 AND created_seq>$2",
        player_id, seq)
