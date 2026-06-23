"""Story-graph engine: context snapshot, effect applier, clue discovery,
state rendering, and the uniform rollback (STORY_AND_PUZZLES.md §6/§7)."""
import json
import re
from .dsl import PlayerContext, evaluate
from . import memory

# Tokens that are titles/particles, not the name itself (so "Herr Dödblek" is
# "known" when an NPC says "Dödblek", not merely "Herr").
_NAME_NOISE = {"the", "herr", "fru", "greve", "von", "van", "der", "af", "de", "la", "le"}


def _name_tokens(full_name: str) -> list[str]:
    return [t for t in re.findall(r"[^\W\d_]+", (full_name or "").lower())
            if len(t) >= 3 and t not in _NAME_NOISE]


# --------------------------------------------------------------------------- #
#  Context snapshot
# --------------------------------------------------------------------------- #
async def load_context(conn, player_id, story_time: int = 0) -> PlayerContext:
    flags = await conn.fetch("SELECT flag FROM player_flags WHERE player_id=$1", player_id)
    visited = await conn.fetch(
        """SELECT DISTINCT le.node_id FROM log_entries le
           JOIN game_logs g ON g.id = le.log_id
           WHERE g.player_id=$1 AND NOT le.rolled_back AND le.node_id IS NOT NULL""",
        player_id)
    gates = await conn.fetch(
        "SELECT gate_id FROM gate_attempts WHERE player_id=$1 AND satisfied", player_id)
    puzzles = await conn.fetch(
        "SELECT puzzle_id FROM puzzle_progress WHERE player_id=$1 AND solved", player_id)
    clues = await conn.fetch("SELECT clue_id FROM player_clues WHERE player_id=$1", player_id)
    # A character's name is "known" once an NPC has actually spoken it to this
    # player — in that character's own gate, or another's (leaked memories surface
    # as dialogue). The player typing a name doesn't count (role='agent' only).
    # Match on any distinctive name token (first name OR surname), so e.g. "I'm
    # Vallmo" reveals "Kurt Vallmo".
    chars = await conn.fetch("SELECT id, name, reveal_name FROM characters")
    said = await conn.fetch(
        "SELECT content FROM gate_messages WHERE player_id=$1 AND role='agent'", player_id)
    spoken = "\n".join((r["content"] or "") for r in said).lower()
    known_names = set()
    for c in chars:
        toks = _name_tokens(c["reveal_name"] or c["name"])  # true name (reveal if withholding)
        if toks and any(re.search(r"\b" + re.escape(t) + r"\b", spoken) for t in toks):
            known_names.add(c["id"])
    return PlayerContext(
        flags={r["flag"] for r in flags},
        visited_nodes={r["node_id"] for r in visited},
        passed_gates={r["gate_id"] for r in gates},
        solved_puzzles={r["puzzle_id"] for r in puzzles},
        found_clues={r["clue_id"] for r in clues},
        known_names=known_names,
        story_time=story_time,
    )


async def next_seq(conn, log_id) -> int:
    row = await conn.fetchrow(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS s FROM log_entries WHERE log_id=$1", log_id)
    return row["s"]


# Every beat in the narration flow carries a `kind` so the UI can style it and so
# nothing meaningful is ever silent. When an effect/edge has no authored `log`
# line, fall back to a generic one keyed by kind rather than an empty summary.
_FALLBACK_SUMMARY = {
    "action": "You press on.",
    "scene": "You take in your surroundings.",
    "dialogue": "The conversation shifts.",
    "puzzle": "It gives way.",
    "death": "Everything goes dark.",
}


async def narrate(conn, log_id, *, node_id, story_time, summary, kind) -> int:
    """Append a standalone narration beat to the flow (a log entry with no side
    effects). Used for world changes that aren't actions in their own right —
    discovering a clue, the map opening up — so they show in the running story."""
    seq = await next_seq(conn, log_id)
    await conn.execute(
        """INSERT INTO log_entries (log_id, seq, story_time, node_id, summary, kind)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        log_id, seq, story_time, node_id, summary, kind)
    return seq


# --------------------------------------------------------------------------- #
#  Apply one action: a single log entry + its effects, all stamped with seq.
# --------------------------------------------------------------------------- #
async def apply_action(conn, player_id, log_id, *, node_id: str | None,
                       effects: dict, story_time: int,
                       kind: str = "action", summary_override: str | None = None):
    seq = await next_seq(conn, log_id)
    story_time = story_time + int(effects.get("advance_story_time", 0))
    summary = summary_override or effects.get("log", "") \
        or _FALLBACK_SUMMARY.get(kind, "Something shifts.")

    await conn.execute(
        """INSERT INTO log_entries (log_id, seq, story_time, node_id, summary, kind)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        log_id, seq, story_time, node_id, summary, kind)

    pts = int(effects.get("progress_points", 0))
    if pts:
        await conn.execute(
            """INSERT INTO progress_events (player_id, seq, kind, points)
               VALUES ($1,$2,$3,$4)""", player_id, seq, kind, pts)

    if "set_flag" in effects:
        await conn.execute(
            """INSERT INTO player_flags (player_id, flag, set_at_seq)
               VALUES ($1,$2,$3) ON CONFLICT (player_id, flag) DO NOTHING""",
            player_id, effects["set_flag"], seq)
    if "clear_flag" in effects:
        await conn.execute("DELETE FROM player_flags WHERE player_id=$1 AND flag=$2",
                           player_id, effects["clear_flag"])

    # write_memory is recorded in the log summary for the slice; the full
    # agent_memories/pgvector path is deferred (see DATA_MODEL.md).
    return seq, story_time


async def narrate_puzzle_prompt(conn, log_id, node, story_time):
    """Record a puzzle's prompt as a beat the first time the player reaches it, so
    the events flow shows what was actually asked — not just that a riddle happened.
    Deduped by the prompt text (re-entering the node won't repeat it); rolls back
    with the rest since it's a seq-stamped beat."""
    if not node["puzzle_id"]:
        return
    prompt = await conn.fetchval("SELECT prompt FROM puzzles WHERE id=$1",
                                 node["puzzle_id"])
    if not prompt:
        return
    exists = await conn.fetchval(
        """SELECT 1 FROM log_entries
           WHERE log_id=$1 AND node_id=$2 AND summary=$3 AND NOT rolled_back""",
        log_id, node["id"], prompt)
    if not exists:
        await narrate(conn, log_id, node_id=node["id"], story_time=story_time,
                      summary=prompt, kind="puzzle")


async def discover_clues(conn, player_id, log_id, story_time, node_id=None):
    """After a state change, discover any clues whose conditions now hold. Each
    newly found clue writes its own 'clue' beat into the narration flow, and the
    clue is stamped with that beat's seq so the two roll back together."""
    ctx = await load_context(conn, player_id, story_time)
    rows = await conn.fetch(
        "SELECT id, reveal_text, discover_conditions FROM puzzle_clues")
    for r in rows:
        if r["id"] in ctx.found_clues:
            continue
        if evaluate(json.loads(r["discover_conditions"]), ctx):
            seq = await narrate(
                conn, log_id, node_id=node_id, story_time=story_time,
                summary=f"You notice: {r['reveal_text']}", kind="clue")
            await conn.execute(
                """INSERT INTO player_clues (player_id, clue_id, found_at_seq)
                   VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""",
                player_id, r["id"], seq)
            # Let a clue unlocked this pass satisfy another clue's condition.
            ctx.found_clues.add(r["id"])


# --------------------------------------------------------------------------- #
#  Render the player's current state for the client
# --------------------------------------------------------------------------- #
def resolve_body(node, ctx: PlayerContext) -> str:
    """Pick a node's description for the current state: the first body_variant
    whose `when` condition holds (gate state, flags, puzzles, …), else the base
    `body`. Lets a room/character description react to the gates affecting it."""
    raw = node["body_variants"]
    variants = json.loads(raw) if isinstance(raw, str) else (raw or [])
    for v in variants:
        if evaluate(v.get("when"), ctx):
            return v.get("body", node["body"])
    return node["body"]


def _resolve_name(char, known: bool) -> tuple[str, str | None, bool]:
    """What to call this NPC in the UI. `known` is whether the player has learned
    the character's name (an NPC has spoken it — see load_context.known_names).
    Until then a name-withholding character (reveal_name != name) shows its public
    role descriptor (e.g. "The Janitor"); an open character, whose only name IS
    their real one, is simply "NPC" until they introduce themselves.
    Returns (display_name, true_name, name_known)."""
    if char is None:
        return "NPC", None, False
    name = char["name"]
    reveal = char["reveal_name"]
    withholds = bool(reveal and reveal != name)
    true_name = reveal or name
    if known:
        return true_name, true_name, True
    return (name if withholds else "NPC"), true_name, False


async def render_state(conn, player_id, session) -> dict:
    node = await conn.fetchrow(
        "SELECT * FROM story_nodes WHERE id=$1", session["current_node"])
    ctx = await load_context(conn, player_id, session["story_time"])

    edges = await conn.fetch(
        "SELECT * FROM story_edges WHERE from_node=$1 ORDER BY sort_order", node["id"])
    # Look up each choice's destination so we can hide alternatives already completed.
    targets = {e["to_node"] for e in edges}
    tinfo = {}
    if targets:
        trows = await conn.fetch(
            "SELECT id, type, gate_id, puzzle_id FROM story_nodes WHERE id = ANY($1::text[])",
            list(targets))
        tinfo = {r["id"]: r for r in trows}
    # Edges whose conditions hold, tagged with whether they lead into an
    # already-finished interaction (a passed gate or a solved puzzle).
    passing = []
    for e in edges:
        if not evaluate(json.loads(e["conditions"]), ctx):
            continue
        t = tinfo.get(e["to_node"])
        finished = t is not None and (
            (t["type"] == "gate" and t["gate_id"] in ctx.passed_gates)
            or (t["type"] == "puzzle" and t["puzzle_id"] in ctx.solved_puzzles))
        passing.append((e, finished))
    # Hide finished interactions to keep the menu clean — but NEVER hide the only
    # way out. A gate node doubles as a location hub, so sub-nodes whose return
    # edge points back at a now-passed gate would otherwise be soft-locked. If
    # hiding leaves nothing, fall back to every condition-met edge.
    shown = [e for e, fin in passing if not fin] or [e for e, _ in passing]
    visible = [{"id": e["id"], "label": e["label"], "danger": e["danger"]} for e in shown]

    found = await conn.fetch(
        """SELECT pc.reveal_text FROM player_clues p
           JOIN puzzle_clues pc ON pc.id = p.clue_id
           WHERE p.player_id=$1 ORDER BY p.found_at_seq""", player_id)

    state = {
        "node": {
            "id": node["id"], "type": node["type"], "title": node["title"],
            "body": resolve_body(node, ctx), "is_death": node["is_death"],
            "world_access": node["world_access"],
            "media": json.loads(node["media"]),
        },
        "edges": visible,
        "notes": [r["reveal_text"] for r in found],
        "story_time": session["story_time"],
    }

    if node["type"] == "gate":
        ga = await conn.fetchrow(
            "SELECT * FROM gate_attempts WHERE player_id=$1 AND gate_id=$2",
            player_id, node["gate_id"])
        msgs = await conn.fetch(
            """SELECT role, content FROM gate_messages
               WHERE player_id=$1 AND gate_id=$2 ORDER BY seq, created_at""",
            player_id, node["gate_id"])
        char = await conn.fetchrow(
            """SELECT c.id, c.name, c.reveal_name FROM dialogue_gates g
               JOIN characters c ON c.id = g.character_id WHERE g.id=$1""",
            node["gate_id"])
        known = bool(char and char["id"] in ctx.known_names)
        display_name, true_name, name_known = _resolve_name(char, known)
        state["gate"] = {
            "gate_id": node["gate_id"],
            "messages": [{"role": m["role"], "content": m["content"]} for m in msgs],
            "satisfied": bool(ga["satisfied"]) if ga else False,
            "character_name": char["name"] if char else "NPC",
            "reveal_name": true_name,
            "display_name": display_name,
            "name_known": name_known,
        }

    if node["type"] == "puzzle":
        pz = await conn.fetchrow("SELECT * FROM puzzles WHERE id=$1", node["puzzle_id"])
        pp = await conn.fetchrow(
            "SELECT * FROM puzzle_progress WHERE player_id=$1 AND puzzle_id=$2",
            player_id, node["puzzle_id"])
        # Hints are never auto-revealed: they're delivered in-character only when the
        # player asks (POST /api/puzzle/hint), as a spoken beat in the flow.
        state["puzzle"] = {
            "puzzle_id": pz["id"], "prompt": pz["prompt"],
            "solved": bool(pp["solved"]) if pp else False,
            "attempts": pp["attempts"] if pp else 0,
        }

    return state


# --------------------------------------------------------------------------- #
#  Rollback — one uniform operation across all runtime state (§6)
# --------------------------------------------------------------------------- #
async def rollback(conn, player_id, session, to_seq: int) -> dict:
    log_id = session["log_id"]
    target = await conn.fetchrow(
        "SELECT node_id, story_time FROM log_entries WHERE log_id=$1 AND seq=$2",
        log_id, to_seq)
    if target is None:
        raise ValueError("no such log step")

    await conn.execute(
        "UPDATE log_entries SET rolled_back=TRUE WHERE log_id=$1 AND seq>$2",
        log_id, to_seq)
    await conn.execute(
        "UPDATE progress_events SET voided=TRUE WHERE player_id=$1 AND seq>$2",
        player_id, to_seq)
    await conn.execute(
        "DELETE FROM player_flags WHERE player_id=$1 AND set_at_seq>$2", player_id, to_seq)
    await conn.execute(
        "DELETE FROM player_clues WHERE player_id=$1 AND found_at_seq>$2", player_id, to_seq)
    await conn.execute(
        "DELETE FROM player_cells WHERE player_id=$1 AND found_at_seq>$2", player_id, to_seq)
    await conn.execute(
        """UPDATE puzzle_progress SET solved=FALSE, solved_at_seq=NULL
           WHERE player_id=$1 AND solved_at_seq>$2""", player_id, to_seq)
    await conn.execute(
        """UPDATE gate_attempts SET satisfied=FALSE, passed_at_seq=NULL,
           criteria_met='[]'::jsonb WHERE player_id=$1 AND passed_at_seq>$2""",
        player_id, to_seq)
    await conn.execute(
        "DELETE FROM gate_messages WHERE player_id=$1 AND seq>$2", player_id, to_seq)
    await memory.void_after(conn, player_id, to_seq)

    await conn.execute(
        "UPDATE player_sessions SET current_node=$1, story_time=$2 WHERE token=$3",
        target["node_id"], target["story_time"], session["token"])
    session = dict(session)
    session["current_node"] = target["node_id"]
    session["story_time"] = target["story_time"]
    return session
