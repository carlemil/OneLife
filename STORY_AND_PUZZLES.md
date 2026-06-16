# OneLife — Story Tree & Puzzle System

> Models the authored narrative structure (the "story tree" of [GAME_DESIGN.md](GAME_DESIGN.md) §2) and the woven puzzle system (§3, *Fool's Errand*-style). This is what the AI dialogue gates ([AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md)) transition *between* — every gate's `story_node_next` points at a node defined here.

---

## 1. Concepts & shape

It's called a "story tree," but the design also wants a **"jungle of story arcs"** (§1). So the real shape is a **directed graph organized into arcs**, with an authored **main spine** plus branching side arcs that can rejoin. A strict tree can't express rejoining or side-quests; a graph can.

Four building blocks:

| Block | What it is |
|---|---|
| **Arc** | A named narrative thread. One `main` arc is the spine; many side arcs form the jungle. |
| **Node** | A single authored *beat* — narration, a choice point, a dialogue gate, a puzzle, a death, an ending. Anchored to a location. |
| **Edge (choice)** | A directed link `node → node` with a player-facing label, **availability conditions**, and **effects** when taken. "Many possible choices at each step" (§2) = many edges out of a node. |
| **Player state** | Per-player flags, visited nodes, puzzle progress, gate progress — all **reconstructable from / voidable with the log** (rollback). |

> The **static story between steps** (§2) lives in node narration text. The **branching** lives in edges. The **AI** only ever operates inside *gate* nodes, and even there it can only pick among authored edges (see [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §2). The authored graph is the rail.

---

## 2. Node types

```
narration   Static authored text. Mood-setting; usually 1+ choices out. (Déjà vu opening.)
choice      Explicit fork: several labeled edges, each with conditions.
gate        Hands off to a dialogue_gate (talk-your-way-through). On success → story_node_next.
puzzle      Hands off to a puzzle. On solve → an edge unlocks.
location    Exploration hub: "what/who is here" interactions (design §4) surface edges.
death       Terminal-ish. World is dangerous (§1). Rollback is the escape (real cost, §7).
ending      Terminal. An arc resolves.
```

A single physical location (e.g. Killebäckskolan) typically contains **many nodes** over time — the location is spatial, the nodes are narrative beats that play out there.

---

## 3. Node definition (authored)

```json
{
  "node_id": "kbk-entrance-hall",
  "arc_id": "main",
  "type": "location",
  "location_id": "killebackskolan",
  "title": "The Entrance Hall",
  "body": "You come to in the entrance hall of Killebäckskolan. Rain ticks the high windows. You don't remember how you got here. A mop bucket sits abandoned by a door marked PANNRUM.",
  "is_entry": false,
  "is_death": false,
  "media": { "image_theme": "school-hallway-dusk", "music_theme": "uneasy-quiet" },
  "edges": [
    {
      "edge_id": "to-janitor",
      "label": "Approach the old janitor by the lockers",
      "target": "kbk-janitor-gate",
      "conditions": { "all": [] },
      "effects": { "progress_points": 5 }
    },
    {
      "edge_id": "try-pannrum-door",
      "label": "Try the PANNRUM (boiler room) door",
      "target": "kbk-boiler-room",
      "conditions": { "all": [ { "flag_set": "knows_boiler_exit" } ] },
      "effects": { "progress_points": 10 }
    },
    {
      "edge_id": "force-pannrum-door",
      "label": "Force the PANNRUM door open",
      "target": "kbk-caught-by-something",
      "conditions": { "all": [ { "not": { "flag_set": "knows_boiler_exit" } } ] },
      "effects": {},
      "danger": 2
    }
  ]
}
```

- **Edges are filtered by `conditions` at render time** — the player only sees choices currently available. This is how "many choices" stays coherent: the graph is large, but only valid edges show.
- An edge can be **foreshadowed as dangerous** (`danger`) so the UI/text can hint risk (supports low-fail-risk by warning, not by removing agency).

---

## 4. Conditions & effects DSL (shared, code-evaluated)

A tiny JSON expression language, evaluated in plain code (never by an LLM). Reused by edges, gate criteria visibility, puzzle clue discovery, and media.

**Conditions** (boolean tree):
```
{ "all": [ ... ] }                     all must hold
{ "any": [ ... ] }                     at least one
{ "not": { ... } }                     negation
{ "flag_set": "knows_boiler_exit" }    player flag is true
{ "node_visited": "kbk-janitor-gate" }
{ "gate_passed": "kbk-janitor-find-the-exit" }
{ "puzzle_solved": "boiler-cabinet-code" }
{ "clue_found": "year-1998" }
{ "story_time_gte": 1200 }             timeline cursor (cross-player consistency, §1)
```

**Effects** (applied by the Applier, in code, on edge-take / gate-success / puzzle-solve):
```
{ "progress_points": 40 }              award progress (design §7)
{ "set_flag": "knows_boiler_exit" }
{ "clear_flag": "..." }
{ "write_memory": { "owner": "the-janitor", "content": "...", "leakable": true } }
{ "advance_story_time": 30 }
{ "log": "human-readable summary of what happened" }
```

Every effect that mutates runtime state is **stamped with the current log sequence number** (see §6) so it can be cleanly voided on rollback.

---

## 5. Puzzle system (Fool's Errand style)

The design wants **story puzzles woven into the world, with hints embedded in texts and characters** (§3). The model splits a puzzle from its scattered **clues**.

### Puzzle definition
```json
{
  "puzzle_id": "boiler-cabinet-code",
  "type": "combination",                 // combination | riddle | assembly | semantic
  "prompt": "A rusted cabinet in the boiler room has a 4-digit padlock.",
  "solution": { "kind": "exact", "value": "1998" },
  "required_clues": ["year-1998-graffiti", "janitor-said-98", "plaque-1998"],
  "hint_ladder": [
    "The number feels like a year.",
    "The janitor, the graffiti, and the memorial plaque all point at the same year.",
    "Try 1998."
  ],
  "on_solve": {
    "progress_points": 60,
    "set_flag": "opened_boiler_cabinet",
    "log": "You opened the cabinet — inside, a child's drawing you somehow recognize."
  }
}
```

Solution `kind`s:
- **exact** — string/number match (codes, combinations).
- **set** — answer must be a member of an accepted set (synonyms, multiple right answers → supports "many choices").
- **assembly** — solved automatically once all `required_clues` are discovered (pure clue-hunt puzzles).
- **semantic** — judged by a **referee call** (reuse the gate referee from [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §2) against an authored intent. For "name the thing you've forgotten" style puzzles where exact text varies.

### Clues — the "embedded hints" (design §3)
A clue is a fragment placed somewhere in the world. The same authored fact (e.g. the year 1998) is seeded into **multiple** location texts and NPC lines, so attentive players assemble it.

```json
{
  "clue_id": "janitor-said-98",
  "puzzle_id": "boiler-cabinet-code",
  "placement": { "kind": "dialogue", "character_id": "the-janitor" },
  "reveal_text": "'That door's been broke since '98.'",
  "discover_conditions": { "all": [ { "gate_passed": "kbk-janitor-find-the-exit" } ] }
}
```
- Placement kinds: `location_text`, `dialogue`, `interaction`, `gate_reward`, `puzzle_reward` — anywhere a hint can be woven.
- A clue is **discovered** when its `discover_conditions` first hold (e.g. you examined the plaque, or passed the janitor gate). Discovery is logged → rollback-safe.
- Discovering a clue can itself raise a puzzle's available hint level, so the world *teaches* the puzzle (low-fail-risk, §2).

### Solving & low-fail-risk
- Players submit an answer at a `puzzle` node (or it auto-solves for `assembly`).
- **Hint ladder** escalates with attempts and with clues found.
- **No hard dead-ends:** puzzles gate side rewards/branches, not the only path forward, wherever possible; and the spine always has a clue-fed route to its hint floor. (Authoring rule, not engine-enforced — flagged in §9.)

---

## 6. Player state & the rollback invariant

There is no inventory (§2). "State" = **flags + visited nodes + clue discoveries + puzzle solves + gate progress**, all per-player. The log is the spine of progress, so:

> **Rollback invariant:** every runtime state row is stamped with the `log_entries.seq` that created it. Rolling the log back to seq *N* **voids every runtime row with `seq > N`** (flags, clue discoveries, puzzle solves, gate progress, progress points) and restores the player to the node recorded at seq *N*. State is always exactly reconstructable from the surviving log.

This makes rollback a single, consistent operation across *all* systems, and makes its leaderboard cost (§7) precise: voided `progress_events` simply stop counting.

---

## 7. How a turn moves through the graph

1. Player is **at a node**. Render `body` + media + the **edges whose conditions currently hold**.
2. Player picks an edge **or** (at gate/puzzle nodes) interacts via dialogue/answer.
3. **Applier (code):**
   - For a normal edge: apply effects (stamped with new log seq), append a `log_entry`, move to `target`.
   - For a **gate** node: run the Actor/Referee loop; on success take the gate's `story_node_next` as the edge.
   - For a **puzzle** node: validate the answer (exact/set/assembly/semantic); on solve apply `on_solve` and unlock the gated edge.
4. Repeat. **Death/ending** nodes terminate the active line; death offers rollback.

The AI is confined to step 3's gate/semantic-puzzle evaluation, and can only ever resolve to **authored targets**. The graph cannot grow new branches at runtime.

---

## 8. Worked example — the opening (ties the whole stack together)

Arc `main`, at `killebackskolan`, reusing the janitor gate from [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §3:

```
[kbk-wake]  narration (is_entry)
   "You come to. Déjà vu. You shouldn't be here."
        │ edge: "Get your bearings"
        ▼
[kbk-entrance-hall]  location           ← §3 example above
   ├─ edge to-janitor ───────────────► [kbk-janitor-gate]  gate
   │                                       gate = kbk-janitor-find-the-exit
   │                                       on_success: set_flag knows_boiler_exit,
   │                                                   +40 pts, → kbk-entrance-hall
   │                                       (also discovers clue 'janitor-said-98')
   ├─ edge try-pannrum-door ──────────► [kbk-boiler-room]  location
   │     (needs flag knows_boiler_exit)     puzzle here: boiler-cabinet-code
   │                                        clues: year-1998-graffiti (hallway text),
   │                                               janitor-said-98 (gate reward),
   │                                               plaque-1998 (examine plaque)
   │                                        on_solve: +60, opened_boiler_cabinet
   │                                              └─► [kbk-the-drawing] narration → next arc
   └─ edge force-pannrum-door ────────► [kbk-caught-by-something]  death
         (only when NOT knows_boiler_exit)     "Something in the dark was waiting."
                                               → rollback (costs leaderboard, §7)
```

This single slice exercises: narration, a location hub, an **AI gate**, a **woven 3-clue puzzle** with embedded hints, a **dangerous choice → death → rollback**, progress scoring, a flag, and a leakable memory — i.e. it's the vertical-slice prototype from [AI_DIALOGUE_GATES.md](AI_DIALOGUE_GATES.md) §10, now with authored nodes.

---

## 9. Schema additions (fills the DATA_MODEL.md gap)

Story-node ids are **TEXT slugs** to match `dialogue_gates.story_node_id` and stay authorable.

```sql
CREATE TABLE story_arcs (
    id          TEXT PRIMARY KEY,              -- 'main', 'sandby-haunting', ...
    title       TEXT NOT NULL,
    is_spine    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE story_nodes (
    id          TEXT PRIMARY KEY,              -- 'kbk-entrance-hall'
    arc_id      TEXT NOT NULL REFERENCES story_arcs(id),
    type        TEXT NOT NULL,                 -- narration|choice|gate|puzzle|location|death|ending
    location_id UUID REFERENCES locations(id),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    is_entry    BOOLEAN NOT NULL DEFAULT FALSE,
    is_death    BOOLEAN NOT NULL DEFAULT FALSE,
    gate_id     TEXT REFERENCES dialogue_gates(id),   -- for type='gate'
    puzzle_id   TEXT,                                 -- for type='puzzle'
    media       JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE story_edges (
    id          TEXT PRIMARY KEY,
    from_node   TEXT NOT NULL REFERENCES story_nodes(id) ON DELETE CASCADE,
    to_node     TEXT NOT NULL REFERENCES story_nodes(id),
    label       TEXT NOT NULL,
    conditions  JSONB NOT NULL DEFAULT '{"all":[]}',
    effects     JSONB NOT NULL DEFAULT '{}',
    danger      SMALLINT NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE puzzles (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,              -- combination|riddle|assembly|semantic
    prompt         TEXT NOT NULL,
    solution       JSONB NOT NULL,            -- {kind, value|set|intent}
    required_clues TEXT[] NOT NULL DEFAULT '{}',
    hint_ladder    JSONB NOT NULL DEFAULT '[]',
    on_solve       JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE puzzle_clues (
    id                 TEXT PRIMARY KEY,
    puzzle_id          TEXT NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE,
    placement          JSONB NOT NULL,        -- {kind, location_id|character_id|...}
    reveal_text        TEXT NOT NULL,
    discover_conditions JSONB NOT NULL DEFAULT '{"all":[]}'
);

-- Runtime, per-player, all rollback-stamped with the creating log seq (§6)
CREATE TABLE player_flags (
    player_id   UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    flag        TEXT NOT NULL,
    set_at_seq  BIGINT NOT NULL,               -- log_entries.seq that set it
    PRIMARY KEY (player_id, flag)
);

CREATE TABLE puzzle_progress (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id       UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    puzzle_id       TEXT NOT NULL REFERENCES puzzles(id),
    clues_found     TEXT[] NOT NULL DEFAULT '{}',
    attempts        INTEGER NOT NULL DEFAULT 0,
    hint_level      INTEGER NOT NULL DEFAULT 0,
    solved          BOOLEAN NOT NULL DEFAULT FALSE,
    solved_at_seq   BIGINT,                     -- for rollback voiding
    UNIQUE (player_id, puzzle_id)
);
```

Also: extend `log_entries.detail` to record the `node_id` at each step, so the current node (and `node_visited` checks) reconstructs from the log.

---

## 10. Open questions / next

- **Spine-reachability guarantee.** Enforce (a lint over the authored graph) that the main spine is always completable from any reachable node given the mercy rules — so the player can never be *truly* stuck. Currently an authoring discipline; worth a CI check.
- **Cross-arc rejoin & shared state.** How side-arc flags affect the main spine (and how `story_time` keeps multi-arc play consistent with other players, §1).
- **Authoring format & tooling.** Author nodes/edges/puzzles in YAML/JSON files seeded into these tables; likely want a small validator + graph visualizer.
- **Difficulty tuning of puzzles** vs. the "very low risk of failure" rule — how aggressive the hint ladder should be by default.

---

### Where this leaves us
The authored graph now exists for gates to transition between, puzzles have a home with embedded clues, and rollback is one uniform, provable operation across story + puzzle + gate state. The natural next step is the **vertical-slice scaffold** (the opening above, running against Postgres), or an **authoring/seed format + validator** so content can be written at scale.
