"""Dialogue-gate turn handler — the Actor/Referee/Applier loop
(AI_DIALOGUE_GATES.md §2/§5). State is mutated only here, in code, only on a
validated verdict."""
import json
from . import llm, memory
from .engine import (apply_action, discover_clues, next_seq,
                     record_alignment, current_alignment, alignment_label)


def _identity(char) -> dict:
    """Identity facts the Actor needs to (not) introduce itself. A character whose
    reveal_name differs from their public name is hiding their true identity."""
    name = char["name"]
    reveal = char["reveal_name"]
    withholds = bool(reveal and reveal != name)
    return {"name": name, "true_name": reveal or name, "withholds": withholds}


async def process_message(conn, player_id, session, text: str) -> dict:
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE id=$1",
                               session["current_node"])
    gate_id = node["gate_id"]
    gate = await conn.fetchrow("SELECT * FROM dialogue_gates WHERE id=$1", gate_id)
    spec = json.loads(gate["spec"])
    char = await conn.fetchrow(
        "SELECT name, reveal_name FROM characters WHERE id=$1",
        gate["character_id"])

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
    cur_seq = max(await next_seq(conn, session["log_id"]) - 1, 0)

    # Record the player's message and rebuild the running transcript.
    await conn.execute(
        """INSERT INTO gate_messages (player_id, gate_id, role, content, seq)
           VALUES ($1,$2,'player',$3,$4)""", player_id, gate_id, text, cur_seq)
    history = [{"role": r["role"], "content": r["content"]} for r in await conn.fetch(
        """SELECT role, content FROM gate_messages
           WHERE player_id=$1 AND gate_id=$2 ORDER BY seq, created_at""",
        player_id, gate_id)]

    # Alignment: every thing the player SAYS shifts their alignment (whether or not
    # it passes the gate). Stamp with cur_seq, the same seq the message rolls back
    # with. Then read the (possibly shifted) standing so the NPC's tone reacts to it.
    verdict_a = await llm.judge_alignment(
        text, context=f"Talking to {char['name'] if char else 'someone'}.")
    await record_alignment(conn, player_id, cur_seq, verdict_a["good_evil_delta"],
                           verdict_a["law_chaos_delta"], verdict_a["reason"], kind="gate")
    align_str = alignment_label(*await current_alignment(conn, player_id))

    attempts = ga["attempts"] + 1
    ladder = spec.get("hint_ladder", [])
    hint_level = min(attempts - 1, max(len(ladder) - 1, 0))

    # Retrieve what this NPC remembers — own (this player) and leaked (others),
    # timeline-safe (MEMORY_AND_LEAKAGE.md §4).
    own_mem, leaked_mem = await memory.retrieve(
        conn, character_id=gate["character_id"], query_text=text,
        player_id=player_id, story_time=session["story_time"])
    identity = _identity(char) if char else None

    async def _say(line: str):
        await conn.execute(
            """INSERT INTO gate_messages (player_id, gate_id, role, content, seq)
               VALUES ($1,$2,'agent',$3,$4)""", player_id, gate_id, line, cur_seq)

    # Already convinced on an earlier turn: keep chatting in character, but never
    # re-judge or re-apply effects. The player is free to keep talking, or take an
    # edge to move on — we never force them out of the conversation.
    if ga["satisfied"]:
        reply = await llm.actor_reply(spec, history, hint_level, own_mem, leaked_mem,
                                      identity=identity, alignment=align_str)
        await _say(reply)
        return {"reply": reply, "satisfied": True, "transitioned": False}

    # Judge BEFORE the actor speaks this turn, so that on the turn the gate passes
    # the NPC can relent in character — explaining why it now trusts the player and
    # disclosing the way forward — instead of staying coy because the hint ladder
    # (indexed by failed attempts) hasn't reached its reveal rung yet.
    verdict = await llm.referee_verdict(spec, history, json.loads(ga["criteria_met"]))
    met = verdict["criteria_met"]
    mercy = spec.get("mercy_after_attempts")
    satisfied = llm.success_rule_met(spec, met) or (mercy is not None and attempts >= mercy)

    reply = await llm.actor_reply(spec, history, hint_level, own_mem, leaked_mem,
                                  identity=identity, reveal=satisfied, alignment=align_str)

    await conn.execute(
        """UPDATE gate_attempts SET criteria_met=$3::jsonb, attempts=$4, hint_level=$5
           WHERE player_id=$1 AND gate_id=$2""",
        player_id, gate_id, json.dumps(met), attempts, hint_level)

    on_success = spec.get("on_success", {}) if satisfied else {}
    # Guaranteed name reveal, FIRST: some encounters must hand the player the NPC's
    # name the instant they get through (here it's the answer to a puzzle). It can't
    # be left to the Actor's phrasing or ordering — name_known is derived from what
    # the NPC actually says — so on the success turn we deterministically whisper the
    # true name BEFORE the relenting reply, making it the first thing she discloses.
    # Same seq as this turn's messages, so it rolls back with them.
    if on_success.get("whisper_name") and char:
        true_name = _identity(char)["true_name"]
        if true_name:
            await _say(f'Before anything else, almost too quiet to hear, a name: "{true_name}."')
    await _say(reply)
    # Guaranteed success beat: a fixed, unmissable line on the turn the gate passes —
    # used when something concrete must happen (e.g. the NPC physically hands over a key
    # item), which can't be left to the Actor's phrasing. Appended after the relenting
    # reply, same seq as the turn's messages so it rolls back with them.
    announce = on_success.get("announce")
    if announce:
        await _say(announce)

    if satisfied:
        # Mark passed BEFORE applying effects so clue discovery sees gate_passed.
        seq = await next_seq(conn, session["log_id"])
        await conn.execute(
            """UPDATE gate_attempts SET satisfied=TRUE, passed_at_seq=$3
               WHERE player_id=$1 AND gate_id=$2""", player_id, gate_id, seq)
        # Apply the rewards/flags/memory, but DON'T move the player. They stay with
        # the NPC and leave through an authored edge ("the way ahead has opened")
        # when ready, so a passing line never cuts the conversation off mid-flow.
        # (Onward routes are unlocked by the flags set here, e.g. has_boiler_handle.)
        # A narrative outcome beat ("Pippa told you what she saw…"), NOT spoken
        # dialogue. kind="gate" marks it as a gate-unlock event — the only point a
        # regular player may cheat-death-rollback to (the UI keys off this kind).
        seq, story_time = await apply_action(
            conn, player_id, session["log_id"], node_id=node["id"],
            effects=on_success, story_time=session["story_time"], kind="gate")
        await conn.execute(
            "UPDATE player_sessions SET story_time=$1 WHERE token=$2",
            story_time, session["token"])
        session["story_time"] = story_time
        await discover_clues(conn, player_id, session["log_id"], story_time, node["id"])

        # Write the NPC's memory of this interaction — becomes leakable to others.
        wm = on_success.get("write_memory")
        if wm:
            await memory.write_memory(
                conn, character_id=wm["owner"], content=wm["content"],
                location_id=gate["location_id"], story_time=story_time,
                origin_player_id=player_id, seq=seq, source="told")

    return {"reply": reply, "satisfied": satisfied, "transitioned": False}
