"""Dialogue-gate turn handler — the Actor/Referee/Applier loop
(AI_DIALOGUE_GATES.md §2/§5). State is mutated only here, in code, only on a
validated verdict."""
import json
from . import llm, memory
from .engine import apply_action, discover_clues, next_seq


async def process_message(conn, player_id, session, text: str) -> dict:
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE id=$1",
                               session["current_node"])
    gate_id = node["gate_id"]
    gate = await conn.fetchrow("SELECT * FROM dialogue_gates WHERE id=$1", gate_id)
    spec = json.loads(gate["spec"])

    ga = await conn.fetchrow(
        "SELECT * FROM gate_attempts WHERE player_id=$1 AND gate_id=$2",
        player_id, gate_id)
    if ga is None:
        await conn.execute(
            "INSERT INTO gate_attempts (player_id, gate_id) VALUES ($1,$2)",
            player_id, gate_id)
        ga = await conn.fetchrow(
            "SELECT * FROM gate_attempts WHERE player_id=$1 AND gate_id=$2",
            player_id, gate_id)
    if ga["satisfied"]:
        return {"reply": "(You have already convinced him.)", "satisfied": True}

    cur_seq = max(await next_seq(conn, session["log_id"]) - 1, 0)

    # Record the player's message.
    await conn.execute(
        """INSERT INTO gate_messages (player_id, gate_id, role, content, seq)
           VALUES ($1,$2,'player',$3,$4)""", player_id, gate_id, text, cur_seq)

    history = [{"role": r["role"], "content": r["content"]} for r in await conn.fetch(
        """SELECT role, content FROM gate_messages
           WHERE player_id=$1 AND gate_id=$2 ORDER BY seq, created_at""",
        player_id, gate_id)]

    attempts = ga["attempts"] + 1
    ladder = spec.get("hint_ladder", [])
    hint_level = min(attempts - 1, max(len(ladder) - 1, 0))

    # Retrieve what this NPC remembers — own (this player) and leaked (others),
    # timeline-safe (MEMORY_AND_LEAKAGE.md §4).
    own_mem, leaked_mem = await memory.retrieve(
        conn, character_id=gate["character_id"], query_text=text,
        player_id=player_id, story_time=session["story_time"])

    reply = await llm.actor_reply(spec, history, hint_level, own_mem, leaked_mem)
    verdict = await llm.referee_verdict(spec, history, json.loads(ga["criteria_met"]))
    met = verdict["criteria_met"]

    mercy = spec.get("mercy_after_attempts")
    satisfied = llm.success_rule_met(spec, met) or (mercy is not None and attempts >= mercy)

    await conn.execute(
        """UPDATE gate_attempts SET criteria_met=$3::jsonb, attempts=$4, hint_level=$5
           WHERE player_id=$1 AND gate_id=$2""",
        player_id, gate_id, json.dumps(met), attempts, hint_level)

    await conn.execute(
        """INSERT INTO gate_messages (player_id, gate_id, role, content, seq)
           VALUES ($1,$2,'agent',$3,$4)""", player_id, gate_id, reply, cur_seq)

    transitioned = False
    if satisfied:
        on_success = spec.get("on_success", {})
        next_node = on_success.get("story_node_next", session["current_node"])
        # Mark passed BEFORE applying effects so clue discovery sees gate_passed.
        seq = await next_seq(conn, session["log_id"])
        await conn.execute(
            """UPDATE gate_attempts SET satisfied=TRUE, passed_at_seq=$3
               WHERE player_id=$1 AND gate_id=$2""", player_id, gate_id, seq)
        seq, story_time = await apply_action(
            conn, player_id, session["log_id"], node_id=next_node,
            effects=on_success, story_time=session["story_time"], kind="dialogue")
        await conn.execute(
            "UPDATE player_sessions SET current_node=$1, story_time=$2 WHERE token=$3",
            next_node, story_time, session["token"])
        session["current_node"] = next_node
        session["story_time"] = story_time
        await discover_clues(conn, player_id, session["log_id"], story_time, seq)

        # Write the NPC's memory of this interaction — becomes leakable to others.
        wm = on_success.get("write_memory")
        if wm:
            await memory.write_memory(
                conn, character_id=wm["owner"], content=wm["content"],
                location_id=gate["location_id"], story_time=story_time,
                origin_player_id=player_id, seq=seq, source="told")
        transitioned = True

    return {"reply": reply, "satisfied": satisfied, "transitioned": transitioned}
