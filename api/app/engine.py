"""Story-graph engine: context snapshot, effect applier, clue discovery,
state rendering, and the uniform rollback (STORY_AND_PUZZLES.md §6/§7)."""
import json
from .dsl import PlayerContext, evaluate
from . import memory


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
    return PlayerContext(
        flags={r["flag"] for r in flags},
        visited_nodes={r["node_id"] for r in visited},
        passed_gates={r["gate_id"] for r in gates},
        solved_puzzles={r["puzzle_id"] for r in puzzles},
        found_clues={r["clue_id"] for r in clues},
        story_time=story_time,
    )


async def next_seq(conn, log_id) -> int:
    row = await conn.fetchrow(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS s FROM log_entries WHERE log_id=$1", log_id)
    return row["s"]


# --------------------------------------------------------------------------- #
#  Apply one action: a single log entry + its effects, all stamped with seq.
# --------------------------------------------------------------------------- #
async def apply_action(conn, player_id, log_id, *, node_id: str | None,
                       effects: dict, story_time: int,
                       kind: str = "action", summary_override: str | None = None):
    seq = await next_seq(conn, log_id)
    story_time = story_time + int(effects.get("advance_story_time", 0))
    summary = summary_override or effects.get("log", "")

    await conn.execute(
        """INSERT INTO log_entries (log_id, seq, story_time, node_id, summary)
           VALUES ($1,$2,$3,$4,$5)""",
        log_id, seq, story_time, node_id, summary)

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


async def discover_clues(conn, player_id, log_id, story_time, seq: int):
    """After a state change, discover any clues whose conditions now hold."""
    ctx = await load_context(conn, player_id, story_time)
    rows = await conn.fetch("SELECT id, discover_conditions FROM puzzle_clues")
    for r in rows:
        if r["id"] in ctx.found_clues:
            continue
        if evaluate(json.loads(r["discover_conditions"]), ctx):
            await conn.execute(
                """INSERT INTO player_clues (player_id, clue_id, found_at_seq)
                   VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""",
                player_id, r["id"], seq)


# --------------------------------------------------------------------------- #
#  Render the player's current state for the client
# --------------------------------------------------------------------------- #
async def render_state(conn, player_id, session) -> dict:
    node = await conn.fetchrow(
        "SELECT * FROM story_nodes WHERE id=$1", session["current_node"])
    ctx = await load_context(conn, player_id, session["story_time"])

    edges = await conn.fetch(
        "SELECT * FROM story_edges WHERE from_node=$1 ORDER BY sort_order", node["id"])
    visible = []
    for e in edges:
        if evaluate(json.loads(e["conditions"]), ctx):
            visible.append({"id": e["id"], "label": e["label"],
                            "danger": e["danger"]})

    found = await conn.fetch(
        """SELECT pc.reveal_text FROM player_clues p
           JOIN puzzle_clues pc ON pc.id = p.clue_id
           WHERE p.player_id=$1 ORDER BY p.found_at_seq""", player_id)

    state = {
        "node": {
            "id": node["id"], "type": node["type"], "title": node["title"],
            "body": node["body"], "is_death": node["is_death"],
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
        state["gate"] = {
            "gate_id": node["gate_id"],
            "messages": [{"role": m["role"], "content": m["content"]} for m in msgs],
            "satisfied": bool(ga["satisfied"]) if ga else False,
        }

    if node["type"] == "puzzle":
        pz = await conn.fetchrow("SELECT * FROM puzzles WHERE id=$1", node["puzzle_id"])
        pp = await conn.fetchrow(
            "SELECT * FROM puzzle_progress WHERE player_id=$1 AND puzzle_id=$2",
            player_id, node["puzzle_id"])
        ladder = json.loads(pz["hint_ladder"])
        hint_level = pp["hint_level"] if pp else 0
        state["puzzle"] = {
            "puzzle_id": pz["id"], "prompt": pz["prompt"],
            "hint": ladder[min(hint_level, len(ladder) - 1)] if ladder and hint_level > 0 else None,
            "solved": bool(pp["solved"]) if pp else False,
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
