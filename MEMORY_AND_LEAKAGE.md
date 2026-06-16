# OneLife — Agent Memory & Cross-Player Leakage

> Implements the most distinctive feature from [GAME_DESIGN.md](GAME_DESIGN.md) §1/§5:
> NPCs remember, and **the stories of other players spread into your game**.
> Builds on [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §7 and [DATA_MODEL.md](DATA_MODEL.md) §5.

---

## 1. What this layer adds

- **Agent memory** — NPCs accumulate memories of what was said, stored with a
  semantic **embedding**, a **location**, and a **story-time**. When an NPC
  speaks, the relevant memories are retrieved and injected into the Actor's
  context, so the NPC genuinely "remembers."
- **Cross-player leakage** — NPCs are shared world entities. The janitor talked
  to by Player A accumulates memories that, when Player B talks to him later,
  surface in B's game ("you're not the first to come asking…"). The pool of
  agent memories is shared; retrieval is filtered per player.
- **Timeline-safe rule** — a memory may surface to a player only if its
  `story_time ≤ that player's current story_time`, so leaked stories never
  contradict a player's private progression (the §11 open question, answered).
- **Rollback-safe** — memories carry the creating player's log `seq`; rolling
  back past that seq voids them (consistent with [STORY_AND_PUZZLES.md](STORY_AND_PUZZLES.md) §6).

---

## 2. Embeddings — provider choice

Anthropic's API is generation-only; it has **no embeddings endpoint**. Real
semantic embeddings would come from a dedicated provider (e.g. Voyage AI,
Anthropic's recommended partner). To keep the slice self-contained and bootable
with zero extra config, embeddings are **pluggable**:

- **Default (offline):** a deterministic **hashed bag-of-words** embedding
  (`api/app/embeddings.py`), 256-dim, L2-normalized. Cosine similarity then
  reflects lexical overlap — crude, but enough to prove retrieval and the whole
  pipeline end-to-end without a second API key.
- **Production seam:** swap `embed()` for a real provider call (Voyage
  `voyage-3` etc.). Only that one function and the `vector(N)` dimension change.

Storage and search use **pgvector**: `embedding vector(256)`, cosine distance
operator `<=>`.

---

## 3. Data model (added)

```sql
CREATE EXTENSION vector;

CREATE TABLE agent_memories (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id     TEXT NOT NULL REFERENCES characters(id),
    content          TEXT NOT NULL,           -- what is remembered
    embedding        vector(256),             -- semantic retrieval key
    location_id      TEXT REFERENCES locations(id),
    story_time       BIGINT NOT NULL,         -- when it happened (timeline-safe leak)
    source           TEXT NOT NULL,           -- observed | told | leaked
    origin_player_id UUID REFERENCES players(id),  -- whose story it came from
    created_seq      BIGINT NOT NULL,         -- creating player's log seq (rollback)
    voided           BOOLEAN NOT NULL DEFAULT FALSE
);
```

This is the slice subset of the full `agent_memories` in DATA_MODEL.md §5
(the `memory_shares` / believable-explanation path for **cross-character**
leakage is implemented in code as `propagate_to_other_characters()` and
`generate_share_explanation()`, ready for a second NPC — see §6).

---

## 4. Retrieval & injection (per NPC turn)

1. Player sends a message at a gate.
2. `memory.retrieve()` embeds the player's text and runs a cosine-nearest query
   over that character's memories, filtered: `NOT voided` **and**
   `story_time ≤ player.story_time` (timeline-safe).
3. Results are split into **own** (this player's past) and **leaked** (other
   players'), and injected into the Actor's system prompt under distinct
   headings — own as "you remember this person," leaked as "you remember other
   visitors (not the person in front of you), allude naturally if relevant."
4. The Actor (Claude or offline stub) weaves them in; the Referee is unchanged
   and still gates progress deterministically.

The LLM only ever *narrates* memories — it never writes them. Writes happen in
code on gate success (and would on other authored memory triggers).

---

## 5. Worked example — the janitor across two players

1. **Player A** talks the janitor into revealing the boiler-room exit → on
   success, code writes a janitor memory: *"Told the newcomer about the
   boiler-room exit,"* `origin_player = A`, embedded, stamped with A's seq.
2. **Player B** later approaches the janitor and says "how do I get out?"
3. Retrieval pulls A's memory (timeline-safe, not voided, lexically relevant) as
   a **leaked** memory; the Actor can now reference that *another* lost soul came
   through asking the same thing.
4. If **A rolls back** past the memory's seq, it's voided and stops surfacing to
   anyone — the event un-happens, consistently.

This is the design's headline claim made concrete: one player's interaction
spreads into another player's game, bounded by the timeline so it stays coherent.

---

## 6. Cross-character leakage (implemented, dormant until a 2nd NPC)

`propagate_to_other_characters()` takes a freshly written memory and shares it to
other characters as `source='leaked'`, attaching a generated **believable
explanation** of *how* they know it (`generate_share_explanation()`, a cheap
`claude-haiku-4-5` call, offline-stubbed). With only the janitor authored today
this is a no-op; adding a second NPC activates it with no further wiring.

---

## 7. Deferred (still)

- Real embeddings provider (Voyage) + ANN index (`hnsw`) for scale.
- `memory_shares` as a first-class table with `via_location`/`via_story_time`.
- Memory decay / salience weighting; summarization of long memory sets.
- Per-character memory-visibility policy (who may learn what from whom).
