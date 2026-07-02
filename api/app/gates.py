"""Dialogue-gate turn handler — the Actor/Referee/Applier loop
(AI_DIALOGUE_GATES.md §2/§5). State is mutated only here, in code, only on a
validated verdict.

Two entry points, one shared body:
  * process_message  — single-phase (anthropic / stub providers): the server runs
    the LLM inline, then applies effects.
  * build_turn / apply_turn — two-phase (browser provider): build_turn returns the
    prompts for the browser to run (phase 1); apply_turn takes the raw completions
    back, parses them, re-derives `satisfied` authoritatively, and applies effects
    (phase 2). See the inference broker in main.py.
Both paths share _turn_context (setup), _say, and _finalize (effect application), so
the load-bearing logic exists once.
"""
import json
from . import llm, memory, security, i18n
from .engine import (apply_action, discover_clues, next_seq,
                     record_alignment, current_alignment, alignment_label)


def _identity(char) -> dict:
    """Identity facts the Actor needs to (not) introduce itself. A character whose
    reveal_name differs from their public name is hiding their true identity."""
    name = char["name"]
    reveal = char["reveal_name"]
    withholds = bool(reveal and reveal != name)
    return {"name": name, "true_name": reveal or name, "withholds": withholds}


async def _say(conn, player_id, gid, gate_id, seq, line: str):
    await conn.execute(
        """INSERT INTO gate_messages (player_id, game_id, gate_id, role, content, seq)
           VALUES ($1,$2,$3,'agent',$4,$5)""", player_id, gid, gate_id, line, seq)


async def _turn_context(conn, player_id, session, text: str) -> dict:
    """Shared setup for a gate turn: load the gate/spec/character, persist the
    player's message, rebuild the transcript, compute attempts/hint rung, retrieve
    NPC memory, and read the PRE-turn alignment standing. Does NOT judge alignment or
    touch gate_attempts — those happen in the caller / _finalize so both the single-
    and two-phase paths stay in step."""
    gid = session["game_id"]
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2",
                               gid, session["current_node"])
    gate_id = node["gate_id"]
    gate = await conn.fetchrow("SELECT * FROM dialogue_gates WHERE game_id=$1 AND id=$2",
                               gid, gate_id)
    spec = json.loads(gate["spec"])
    char = await conn.fetchrow(
        "SELECT name, reveal_name FROM characters WHERE game_id=$1 AND id=$2",
        gid, gate["character_id"])
    # Where this encounter physically happens — so the Actor stays put and describes the
    # real surroundings instead of inventing a place and wandering off. Prefer the node's
    # location, falling back to the gate's.
    place = await conn.fetchrow(
        """SELECT l.name, l.description, w.region
           FROM locations l LEFT JOIN world_cells w ON w.id=l.cell_id AND w.game_id=l.game_id
           WHERE l.game_id=$1 AND l.id=$2""",
        gid, node["location_id"] or gate["location_id"])

    ga = await conn.fetchrow(
        "SELECT * FROM gate_attempts WHERE player_id=$1 AND game_id=$2 AND gate_id=$3",
        player_id, gid, gate_id)
    if ga is None:
        await conn.execute(
            "INSERT INTO gate_attempts (player_id, game_id, gate_id) VALUES ($1,$2,$3)",
            player_id, gid, gate_id)
        ga = await conn.fetchrow(
            "SELECT * FROM gate_attempts WHERE player_id=$1 AND game_id=$2 AND gate_id=$3",
            player_id, gid, gate_id)
    cur_seq = max(await next_seq(conn, session["log_id"]) - 1, 0)

    # Record the player's message and rebuild the running transcript.
    await conn.execute(
        """INSERT INTO gate_messages (player_id, game_id, gate_id, role, content, seq)
           VALUES ($1,$2,$3,'player',$4,$5)""", player_id, gid, gate_id, text, cur_seq)
    history = [{"role": r["role"], "content": r["content"]} for r in await conn.fetch(
        """SELECT role, content FROM gate_messages
           WHERE player_id=$1 AND game_id=$2 AND gate_id=$3 ORDER BY seq, created_at""",
        player_id, gid, gate_id)]

    # Count this as a real attempt only if it's substantive. A trivial one/two-word
    # message or an exact duplicate of something already said does NOT advance the hint
    # ladder or the mercy counter — so a player can't spam "hi" to trip the auto-pass.
    norm = (text or "").strip().lower()
    prior_player = [m["content"].strip().lower() for m in history[:-1] if m["role"] == "player"]
    low_effort = (norm in prior_player) or (len(norm.split()) <= 2)
    attempts = ga["attempts"] + (0 if low_effort else 1)
    ladder = spec.get("hint_ladder", [])
    hint_level = min(max(attempts - 1, 0), max(len(ladder) - 1, 0))

    # Retrieve what this NPC remembers — own (this player) and leaked (others),
    # timeline-safe (MEMORY_AND_LEAKAGE.md §4).
    own_mem, leaked_mem = await memory.retrieve(
        conn, game_id=gid, character_id=gate["character_id"], query_text=text,
        player_id=player_id, story_time=session["story_time"])
    identity = _identity(char) if char else None

    # A concise recap of what this gate grants on success (its on_success log line) —
    # fed to the Actor so it discloses it clearly on the reveal turn and stays
    # consistent (it has already helped this person) on every turn after.
    recap = spec.get("on_success", {}).get("log")
    # When the gate discloses the NPC's name on success (whisper_name), that name is a
    # reward — the Actor must withhold it until the player gets through, not introduce
    # itself early.
    name_earned = bool(spec.get("on_success", {}).get("whisper_name"))

    # PRE-turn alignment standing — used by the two-phase (browser) path for the
    # Actor's tone, since this turn's own delta can't be recorded until phase 2.
    align_pre = alignment_label(*await current_alignment(conn, player_id, gid))

    return {"gid": gid, "node": node, "gate_id": gate_id, "gate": gate, "spec": spec,
            "char": char, "ga": ga, "cur_seq": cur_seq, "history": history,
            "attempts": attempts, "hint_level": hint_level, "ladder": ladder,
            "own_mem": own_mem, "leaked_mem": leaked_mem, "identity": identity,
            "recap": recap, "name_earned": name_earned, "align_pre": align_pre,
            "place": dict(place) if place else None}


async def _finalize(conn, player_id, session, ctx, met, satisfied, reply) -> dict:
    """Apply a judged turn: persist attempts/criteria, speak the reply (with the
    guaranteed whisper_name/announce beats on success), and on success apply effects,
    discover clues, and write leakable memory. Shared by both providers."""
    gid, gate_id, node, spec, char = (ctx["gid"], ctx["gate_id"], ctx["node"],
                                      ctx["spec"], ctx["char"])
    cur_seq = ctx["cur_seq"]

    await conn.execute(
        """UPDATE gate_attempts SET criteria_met=$4::jsonb, attempts=$5, hint_level=$6
           WHERE player_id=$1 AND game_id=$2 AND gate_id=$3""",
        player_id, gid, gate_id, json.dumps(met), ctx["attempts"], ctx["hint_level"])

    on_success = spec.get("on_success", {}) if satisfied else {}
    # Localize the scripted success beats (the announce + log lines) at write time.
    lang = session.get("language", "en")
    if lang != "en" and on_success:
        on_success = dict(on_success)
        for fld in ("announce", "log"):
            if on_success.get(fld):
                on_success[fld] = await i18n.tr_one(
                    conn, gid, lang, "dialogue_gates", gate_id, f"on_success.{fld}",
                    on_success[fld])
    # Guaranteed name reveal, FIRST: some encounters must hand the player the NPC's
    # name the instant they get through (here it's the answer to a puzzle). It can't
    # be left to the Actor's phrasing or ordering — name_known is derived from what
    # the NPC actually says — so on the success turn we deterministically whisper the
    # true name BEFORE the relenting reply, making it the first thing she discloses.
    # Same seq as this turn's messages, so it rolls back with them.
    if on_success.get("whisper_name") and char:
        true_name = _identity(char)["true_name"]
        if true_name:
            await _say(conn, player_id, gid, gate_id, cur_seq,
                       f'Before anything else, almost too quiet to hear, a name: "{true_name}."')
    await _say(conn, player_id, gid, gate_id, cur_seq, reply)
    # Guaranteed success beat: a fixed, unmissable line on the turn the gate passes —
    # used when something concrete must happen (e.g. the NPC physically hands over a key
    # item), which can't be left to the Actor's phrasing. Appended after the relenting
    # reply, same seq as the turn's messages so it rolls back with them.
    announce = on_success.get("announce")
    if announce:
        await _say(conn, player_id, gid, gate_id, cur_seq, announce)

    if satisfied:
        # Mark passed BEFORE applying effects so clue discovery sees gate_passed.
        seq = await next_seq(conn, session["log_id"])
        await conn.execute(
            """UPDATE gate_attempts SET satisfied=TRUE, passed_at_seq=$4
               WHERE player_id=$1 AND game_id=$2 AND gate_id=$3""",
            player_id, gid, gate_id, seq)
        # Apply the rewards/flags/memory, but DON'T move the player. They stay with
        # the NPC and leave through an authored edge ("the way ahead has opened")
        # when ready, so a passing line never cuts the conversation off mid-flow.
        seq, story_time = await apply_action(
            conn, player_id, gid, session["log_id"], node_id=node["id"],
            effects=on_success, story_time=session["story_time"], kind="gate")
        await conn.execute(
            """UPDATE player_games SET story_time=$1, last_played_at=now()
               WHERE player_id=$2 AND game_id=$3""",
            story_time, player_id, gid)
        session["story_time"] = story_time
        await discover_clues(conn, player_id, gid, session["log_id"], story_time, node["id"], lang=session.get("language", "en"))

        # Write the NPC's memory of this interaction — becomes leakable to others.
        wm = on_success.get("write_memory")
        if wm:
            await memory.write_memory(
                conn, game_id=gid, character_id=wm["owner"], content=wm["content"],
                location_id=ctx["gate"]["location_id"], story_time=story_time,
                origin_player_id=player_id, seq=seq, source="told")

    return {"reply": reply, "satisfied": satisfied, "transitioned": False}


def _turn_satisfied(spec: dict, met: list[str], attempts: int) -> bool:
    """Authoritative pass decision, shared by the server and mirrored in the browser.
    Mercy is a soft-lock safety net: it only fires after enough SUBSTANTIVE attempts
    AND once the player has met at least one criterion (genuine engagement)."""
    mercy = spec.get("mercy_after_attempts")
    return llm.success_rule_met(spec, met) or (
        mercy is not None and attempts >= mercy and len(met) >= 1)


async def process_message(conn, player_id, session, text: str) -> dict:
    """Single-phase path (anthropic / stub): run the LLM inline and apply effects."""
    ctx = await _turn_context(conn, player_id, session, text)
    gid, gate_id, cur_seq = ctx["gid"], ctx["gate_id"], ctx["cur_seq"]
    char = ctx["char"]

    # Alignment: every thing the player SAYS shifts their alignment (whether or not
    # it passes the gate). Stamp with cur_seq, the same seq the message rolls back
    # with. Then read the (possibly shifted) standing so the NPC's tone reacts to it.
    verdict_a = await llm.judge_alignment(
        text, context=f"Talking to {char['name'] if char else 'someone'}.")
    await record_alignment(conn, player_id, gid, cur_seq, verdict_a["good_evil_delta"],
                           verdict_a["law_chaos_delta"], verdict_a["reason"], kind="gate")
    align_str = alignment_label(*await current_alignment(conn, player_id, gid))

    # Already convinced on an earlier turn: keep chatting in character, but never
    # re-judge or re-apply effects. The player is free to keep talking, or take an
    # edge to move on — we never force them out of the conversation.
    if ctx["ga"]["satisfied"]:
        reply = await llm.actor_reply(ctx["spec"], ctx["history"], ctx["hint_level"],
                                      ctx["own_mem"], ctx["leaked_mem"],
                                      identity=ctx["identity"], alignment=align_str,
                                      recap=ctx["recap"], already_helped=True,
                                      name_earned=ctx["name_earned"],
                                      language=session.get("language", "en"),
                                      place=ctx["place"])
        await _say(conn, player_id, gid, gate_id, cur_seq, reply)
        return {"reply": reply, "satisfied": True, "transitioned": False}

    # Judge BEFORE the actor speaks this turn, so that on the turn the gate passes
    # the NPC can relent in character — explaining why it now trusts the player and
    # disclosing the way forward — instead of staying coy because the hint ladder
    # (indexed by failed attempts) hasn't reached its reveal rung yet.
    verdict = await llm.referee_verdict(ctx["spec"], ctx["history"],
                                        json.loads(ctx["ga"]["criteria_met"]))
    met = verdict["criteria_met"]
    satisfied = _turn_satisfied(ctx["spec"], met, ctx["attempts"])

    reply = await llm.actor_reply(ctx["spec"], ctx["history"], ctx["hint_level"],
                                  ctx["own_mem"], ctx["leaked_mem"],
                                  identity=ctx["identity"], reveal=satisfied,
                                  alignment=align_str,
                                  recap=(ctx["recap"] if satisfied else None),
                                  name_earned=ctx["name_earned"],
                                  language=session.get("language", "en"),
                                  place=ctx["place"])
    return await _finalize(conn, player_id, session, ctx, met, satisfied, reply)


# --------------------------------------------------------------------------- #
#  Two-phase (browser) path. build_turn returns the prompts; apply_turn takes
#  the raw completions back and applies effects.
# --------------------------------------------------------------------------- #
def _gate_token(ctx: dict) -> str:
    return security.sign({
        "kind": "gate", "gid": ctx["gid"], "gate_id": ctx["gate_id"],
        "node_id": ctx["node"]["id"], "cur_seq": ctx["cur_seq"],
        "attempts": ctx["attempts"], "hint_level": ctx["hint_level"]})


async def build_turn(conn, player_id, session, text: str) -> dict:
    """Phase 1 (browser mode): persist the player's message and return the prompts the
    browser must run — always the alignment judge, plus either the already-helped
    Actor (gate already passed) or the Referee + both Actor variants (coy / relenting)."""
    ctx = await _turn_context(conn, player_id, session, text)
    char = ctx["char"]
    reqs = [llm.build_align(
        text, context=f"Talking to {char['name'] if char else 'someone'}.")]

    lang = session.get("language", "en")
    place = ctx["place"]
    already = bool(ctx["ga"]["satisfied"])
    if already:
        req = llm.build_actor(ctx["spec"], ctx["history"], ctx["hint_level"],
                              ctx["own_mem"], ctx["leaked_mem"], identity=ctx["identity"],
                              alignment=ctx["align_pre"], recap=ctx["recap"],
                              already_helped=True, name_earned=ctx["name_earned"],
                              language=lang, place=place)
        req["id"] = "actor_helped"
        reqs.append(req)
    else:
        reqs.append(llm.build_referee(ctx["spec"], ctx["history"],
                                      json.loads(ctx["ga"]["criteria_met"])))
        reqs.append(llm.build_actor(ctx["spec"], ctx["history"], ctx["hint_level"],
                                    ctx["own_mem"], ctx["leaked_mem"], identity=ctx["identity"],
                                    reveal=False, alignment=ctx["align_pre"],
                                    recap=None, name_earned=ctx["name_earned"], language=lang,
                                    place=place))
        reqs.append(llm.build_actor(ctx["spec"], ctx["history"], ctx["hint_level"],
                                    ctx["own_mem"], ctx["leaked_mem"], identity=ctx["identity"],
                                    reveal=True, alignment=ctx["align_pre"],
                                    recap=ctx["recap"], name_earned=ctx["name_earned"], language=lang,
                                    place=place))

    decision = {"already_satisfied": already,
                "criteria": [c["id"] for c in ctx["spec"].get("criteria", [])],
                "success_rule": ctx["spec"].get("success_rule", ""),
                "mercy_after_attempts": ctx["spec"].get("mercy_after_attempts"),
                "attempts": ctx["attempts"],
                "already_met": json.loads(ctx["ga"]["criteria_met"])}
    return {"turn_token": _gate_token(ctx),
            "requests": [llm.to_browser_request(r) for r in reqs],
            "decision": decision}


async def apply_turn(conn, player_id, session, tok: dict, completions: dict) -> dict:
    """Phase 2 (browser mode): parse the raw completions, re-derive `satisfied`
    server-side, and apply effects. The verdict is client-run (accepted trade-off),
    but the server still records alignment within ±0.3, filters criteria to valid
    ids, and evaluates success_rule itself."""
    gid, gate_id = tok["gid"], tok["gate_id"]
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2",
                               gid, tok["node_id"])
    gate = await conn.fetchrow("SELECT * FROM dialogue_gates WHERE game_id=$1 AND id=$2",
                               gid, gate_id)
    spec = json.loads(gate["spec"])
    char = await conn.fetchrow(
        "SELECT name, reveal_name FROM characters WHERE game_id=$1 AND id=$2",
        gid, gate["character_id"])
    ga = await conn.fetchrow(
        "SELECT * FROM gate_attempts WHERE player_id=$1 AND game_id=$2 AND gate_id=$3",
        player_id, gid, gate_id)
    cur_seq = tok["cur_seq"]

    # Alignment (every turn), clamped ±0.3 by parse_align.
    va = llm.parse_align(completions.get("align", ""))
    await record_alignment(conn, player_id, gid, cur_seq, va["good_evil_delta"],
                           va["law_chaos_delta"], va["reason"], kind="gate")

    if ga["satisfied"]:
        reply = llm.parse_actor(completions.get("actor_helped", ""))
        await _say(conn, player_id, gid, gate_id, cur_seq, reply)
        return {"reply": reply, "satisfied": True, "transitioned": False}

    met = llm.parse_referee(completions.get("referee", ""),
                            spec.get("criteria", []), json.loads(ga["criteria_met"]))["criteria_met"]
    satisfied = _turn_satisfied(spec, met, tok["attempts"])
    reply = llm.parse_actor(completions.get("actor_reveal" if satisfied else "actor")
                            or completions.get("actor") or completions.get("actor_reveal") or "")

    ctx = {"gid": gid, "gate_id": gate_id, "node": node, "spec": spec, "char": char,
           "gate": gate, "cur_seq": cur_seq, "attempts": tok["attempts"],
           "hint_level": tok["hint_level"]}
    return await _finalize(conn, player_id, session, ctx, met, satisfied, reply)
