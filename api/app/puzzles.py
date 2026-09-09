"""Puzzle submission handler (STORY_AND_PUZZLES.md §5). Validates the answer in
code; the 'semantic' kind would reuse the gate referee (deferred for the slice)."""
import json
from . import llm, security, crossword, i18n
from .strings import M, t
from .engine import (apply_action, discover_clues, load_context, narrate,
                     narrate_puzzle_prompt, next_seq)


def _is_wordlike(s: str) -> bool:
    """A spellable word (letters only, length >= 4) — eligible for typo tolerance.
    Numbers, codes, times and short tokens stay strict (so 1998 never matches 1999)."""
    return len(s) >= 4 and s.isalpha()


def _within_one_edit(a: str, b: str) -> bool:
    """True if `a` is within one edit (insert/delete/substitute) of `b`."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:                                    # one substitution
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:                                     # make `a` the shorter one
        a, b, la, lb = b, a, lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:                        # one insertion/deletion
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def _check(solution: dict, answer: str, ctx) -> bool:
    kind = solution.get("kind")
    a = answer.strip().casefold()   # casefold (not lower) so non-ASCII answers fold correctly
    if kind == "assembly":
        # auto-solved elsewhere; treat any submit as solved if clues complete
        return True
    if kind == "set":
        targets = [str(v).strip().casefold() for v in solution.get("set", [])]
    else:  # 'exact' (and the deferred 'semantic' kind) — single value
        targets = [str(solution.get("value", "")).strip().casefold()]
    for t in targets:
        if a == t:
            return True
        # Forgive a single typo on spellable WORD answers (shaddow -> shadow);
        # numbers, codes and times stay strict (1998 never matches 1999).
        if _is_wordlike(t) and _is_wordlike(a) and _within_one_edit(a, t):
            return True
    return False


async def submit(conn, player_id, session, answer: str, entry: str = "") -> dict:
    gid = session["game_id"]
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2",
                               gid, session["current_node"])
    pz = await conn.fetchrow("SELECT * FROM puzzles WHERE game_id=$1 AND id=$2",
                             gid, node["puzzle_id"])

    pp = await conn.fetchrow(
        "SELECT * FROM puzzle_progress WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"])
    if pp is None:
        await conn.execute(
            "INSERT INTO puzzle_progress (player_id, game_id, puzzle_id) VALUES ($1,$2,$3)",
            player_id, gid, pz["id"])
        pp = await conn.fetchrow(
            "SELECT * FROM puzzle_progress WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
            player_id, gid, pz["id"])
    if pp["solved"]:
        return {"solved": True, "message": "Already open."}

    solution = json.loads(pz["solution"])
    if solution.get("kind") == "crossword":
        return await _submit_crossword(
            conn, player_id, gid, session, node, pz, pp, solution, answer, entry)

    ctx = await load_context(conn, player_id, gid, session["story_time"])
    # In a translated game, accept the UNION of the base English answers and the
    # target-language answers (English never stops working).
    lang = session.get("language", "en")
    if lang and lang != "en" and solution.get("kind") != "crossword":
        extra = [r["text"] for r in await conn.fetch(
            """SELECT text FROM content_translations
               WHERE game_id=$1 AND lang=$2 AND entity_type='puzzles' AND entity_id=$3
                 AND (field_path='solution.value' OR field_path LIKE 'solution.set[%')""",
            gid, lang, pz["id"])]
        if extra:
            base = (list(solution.get("set", [])) if solution.get("kind") == "set"
                    else ([solution["value"]] if solution.get("value") else []))
            solution = {**solution, "kind": "set", "set": base + extra}
    ok = _check(solution, answer, ctx)

    attempts = pp["attempts"] + 1

    # Weave the riddle and the player's answer into the narration flow, the way NPC
    # gate dialogue is woven in — so the events list preserves the whole exchange,
    # not just the terse outcome line. The prompt is recorded once (deduped; also
    # shown on arrival at the node); every submitted answer is recorded. Both are
    # seq-stamped beats, so they roll back with the rest of the run.
    await narrate_puzzle_prompt(conn, session["log_id"], gid, node, session["story_time"], lang=session.get("language", "en"))
    await narrate(conn, session["log_id"], gid, node_id=node["id"],
                  story_time=session["story_time"],
                  summary=t("you_answer", lang, answer=answer.strip()), kind="puzzle")

    if ok:
        on_solve = json.loads(pz["on_solve"])
        # Localize the solve's log line at write time (English base fallback).
        if lang != "en" and on_solve.get("log"):
            on_solve = {**on_solve, "log": await i18n.tr_one(
                conn, gid, lang, "puzzles", pz["id"], "on_solve.log", on_solve["log"])}
        seq, story_time = await apply_action(
            conn, player_id, gid, session["log_id"], node_id=node["id"],
            effects=on_solve, story_time=session["story_time"], kind="puzzle")
        await conn.execute(
            """UPDATE puzzle_progress SET solved=TRUE, solved_at_seq=$4, attempts=$5
               WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3""",
            player_id, gid, pz["id"], seq, attempts)
        await conn.execute(
            """UPDATE player_games SET story_time=$1, last_played_at=now()
               WHERE player_id=$2 AND game_id=$3""",
            story_time, player_id, gid)
        session["story_time"] = story_time
        await discover_clues(conn, player_id, gid, session["log_id"], story_time, node["id"], lang=session.get("language", "en"))
        return {"solved": True, "message": on_solve.get("log", "It opens.")}

    # No auto-hint: only count the attempt. Hints are revealed solely when the
    # player asks (request_hint), and only after they've tried at least once.
    await conn.execute(
        "UPDATE puzzle_progress SET attempts=$4 WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"], attempts)
    # A wrong answer may cost something (`on_fail`, same effect DSL as on_solve —
    # typically `advance_story_time` in a clock-driven game, plus a `log` beat).
    on_fail = json.loads(pz["on_fail"]) if ("on_fail" in pz.keys() and pz["on_fail"]) else {}
    if on_fail:
        if lang != "en" and on_fail.get("log"):
            on_fail = {**on_fail, "log": await i18n.tr_one(
                conn, gid, lang, "puzzles", pz["id"], "on_fail.log", on_fail["log"])}
        _seq, story_time = await apply_action(
            conn, player_id, gid, session["log_id"], node_id=node["id"],
            effects=on_fail, story_time=session["story_time"], kind="puzzle")
        await conn.execute(
            """UPDATE player_games SET story_time=$1, last_played_at=now()
               WHERE player_id=$2 AND game_id=$3""",
            story_time, player_id, gid)
        session["story_time"] = story_time
    return {"solved": False, "message": on_fail.get("log") or "Nothing happens."}


async def _submit_crossword(conn, player_id, gid, session, node, pz, pp,
                            solution, answer, entry) -> dict:
    """One clue at a time: validate the whole typed word (exact, case-insensitive —
    no typo tolerance, letters must be precise for interlocks). A correct word is
    recorded as a seq-stamped per-entry flag (so it rolls back); when every entry's
    flag is set, the shared solve tail fires."""
    parsed = crossword.parse(solution)
    emap = {e["id"]: e for e in parsed["entries"]}
    eid = str(entry or "").strip()

    # A bad/stale entry id is a client glitch, not a real attempt — don't count or
    # narrate it.
    if eid not in emap:
        return {"solved": False, "entry": eid, "correct": False,
                "message": M.XWORD_NO_ENTRY}

    flag = crossword.entry_flag(pz["id"], eid)
    flags = {r["flag"] for r in await conn.fetch(
        "SELECT flag FROM player_flags WHERE player_id=$1 AND game_id=$2", player_id, gid)}
    if flag in flags:
        return {"solved": False, "entry": eid, "correct": False,
                "message": M.XWORD_ALREADY}

    # Record the exchange in the flow (prompt once, deduped; the attempt always), so
    # it reads as a beat and rolls back with the run.
    await narrate_puzzle_prompt(conn, session["log_id"], gid, node, session["story_time"], lang=session.get("language", "en"))
    await narrate(conn, session["log_id"], gid, node_id=node["id"],
                  story_time=session["story_time"],
                  summary=f'You try "{answer.strip().upper()}" for {eid}.', kind="puzzle")

    attempts = pp["attempts"] + 1
    correct = answer.strip().upper() == crossword.answer_of(solution, eid)
    if not correct:
        await conn.execute(
            "UPDATE puzzle_progress SET attempts=$4 WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
            player_id, gid, pz["id"], attempts)
        return {"solved": False, "entry": eid, "correct": False, "message": M.XWORD_WRONG}

    ent = emap[eid]
    d = "A" if ent["dir"] == "across" else "D"
    fill_log = f"You fill in {ent['number']}{d}: {crossword.answer_of(solution, eid)}."
    seq, story_time = await apply_action(
        conn, player_id, gid, session["log_id"], node_id=node["id"],
        effects={"set_flag": flag, "log": fill_log}, story_time=session["story_time"],
        kind="puzzle")
    session["story_time"] = story_time
    await conn.execute(
        """UPDATE player_games SET story_time=$1, last_played_at=now()
           WHERE player_id=$2 AND game_id=$3""", story_time, player_id, gid)

    flags.add(flag)
    done = all(crossword.entry_flag(pz["id"], i) in flags for i in crossword.entry_ids(solution))
    if not done:
        await conn.execute(
            "UPDATE puzzle_progress SET attempts=$4 WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
            player_id, gid, pz["id"], attempts)
        return {"solved": False, "entry": eid, "correct": True, "message": fill_log}

    # Last entry filled — run the same solve tail as a single-answer puzzle.
    on_solve = json.loads(pz["on_solve"])
    seq, story_time = await apply_action(
        conn, player_id, gid, session["log_id"], node_id=node["id"],
        effects=on_solve, story_time=session["story_time"], kind="puzzle")
    await conn.execute(
        """UPDATE puzzle_progress SET solved=TRUE, solved_at_seq=$4, attempts=$5
           WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3""",
        player_id, gid, pz["id"], seq, attempts)
    await conn.execute(
        """UPDATE player_games SET story_time=$1, last_played_at=now()
           WHERE player_id=$2 AND game_id=$3""", story_time, player_id, gid)
    session["story_time"] = story_time
    await discover_clues(conn, player_id, gid, session["log_id"], story_time, node["id"], lang=session.get("language", "en"))
    return {"solved": True, "entry": eid, "correct": True,
            "message": on_solve.get("log", "The grid is complete.")}


async def request_hint(conn, player_id, session) -> dict:
    """Reveal the next hint for the puzzle the player is on — but only after they
    have actually tried and failed. Each ask climbs one rung of the hint ladder."""
    gid = session["game_id"]
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2",
                               gid, session["current_node"])
    if not node or not node["puzzle_id"]:
        return {"hint": None}
    pz = await conn.fetchrow("SELECT * FROM puzzles WHERE game_id=$1 AND id=$2",
                             gid, node["puzzle_id"])
    ladder = json.loads(pz["hint_ladder"]) if pz else []
    pp = await conn.fetchrow(
        "SELECT * FROM puzzle_progress WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"])
    if not ladder or pp is None or pp["solved"]:
        return {"hint": None}
    if pp["attempts"] == 0:
        return {"hint": None, "message": "Try an answer first."}
    new_level = min(pp["hint_level"] + 1, len(ladder))
    await conn.execute(
        "UPDATE puzzle_progress SET hint_level=$4 WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"], new_level)
    # Voice the hint as whoever posed the riddle, and record it in the flow like the
    # rest of the conversation (so it persists and can be re-read).
    spoken = await llm.hint_in_character(pz["prompt"], ladder[new_level - 1])
    await narrate(conn, session["log_id"], gid, node_id=node["id"],
                  story_time=session["story_time"], summary=spoken, kind="puzzle")
    return {"hint": spoken}


# --------------------------------------------------------------------------- #
#  Two-phase (browser mode) hint: build_hint_request checks preconditions and
#  either returns a terminal result or a pending_inference; apply_hint narrates
#  the browser-run completion.
# --------------------------------------------------------------------------- #
async def _hint_preconditions(conn, player_id, session):
    """Shared checks. Returns (terminal_result | None, node, pz, ladder, new_level)."""
    gid = session["game_id"]
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2",
                               gid, session["current_node"])
    if not node or not node["puzzle_id"]:
        return {"hint": None}, None, None, None, None
    pz = await conn.fetchrow("SELECT * FROM puzzles WHERE game_id=$1 AND id=$2",
                             gid, node["puzzle_id"])
    ladder = json.loads(pz["hint_ladder"]) if pz else []
    pp = await conn.fetchrow(
        "SELECT * FROM puzzle_progress WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"])
    if not ladder or pp is None or pp["solved"]:
        return {"hint": None}, None, None, None, None
    if pp["attempts"] == 0:
        return {"hint": None, "message": "Try an answer first."}, None, None, None, None
    new_level = min(pp["hint_level"] + 1, len(ladder))
    return None, node, pz, ladder, new_level


async def build_hint_request(conn, player_id, session) -> dict:
    """Phase 1: either {"result": {...}} for the terminal (no-hint) cases, or
    {"pending_inference": {...}} for the browser to voice the next rung."""
    terminal, node, pz, ladder, new_level = await _hint_preconditions(
        conn, player_id, session)
    if terminal is not None:
        return {"result": terminal}
    gid = session["game_id"]
    req = llm.build_hint(pz["prompt"], ladder[new_level - 1])
    token = security.sign({"kind": "hint", "gid": gid, "node_id": node["id"],
                           "puzzle_id": pz["id"], "new_level": new_level})
    return {"pending_inference": {
        "turn_token": token, "requests": [llm.to_browser_request(req)]}}


async def apply_hint(conn, player_id, session, tok: dict, completions: dict) -> dict:
    """Phase 2: persist the new hint rung and narrate the browser's completion."""
    gid = tok["gid"]
    pz = await conn.fetchrow("SELECT * FROM puzzles WHERE game_id=$1 AND id=$2",
                             gid, tok["puzzle_id"])
    ladder = json.loads(pz["hint_ladder"]) if pz else []
    new_level = tok["new_level"]
    fallback = ladder[new_level - 1] if 0 < new_level <= len(ladder) else ""
    await conn.execute(
        "UPDATE puzzle_progress SET hint_level=$4 WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"], new_level)
    spoken = llm.parse_hint(completions.get("hint", ""), fallback)
    await narrate(conn, session["log_id"], gid, node_id=tok["node_id"],
                  story_time=session["story_time"], summary=spoken, kind="puzzle")
    return {"hint": spoken}
