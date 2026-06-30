"""Puzzle submission handler (STORY_AND_PUZZLES.md §5). Validates the answer in
code; the 'semantic' kind would reuse the gate referee (deferred for the slice)."""
import json
from . import llm
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
    a = answer.strip().lower()
    if kind == "assembly":
        # auto-solved elsewhere; treat any submit as solved if clues complete
        return True
    if kind == "set":
        targets = [str(v).strip().lower() for v in solution.get("set", [])]
    else:  # 'exact' (and the deferred 'semantic' kind) — single value
        targets = [str(solution.get("value", "")).strip().lower()]
    for t in targets:
        if a == t:
            return True
        # Forgive a single typo on spellable WORD answers (shaddow -> shadow);
        # numbers, codes and times stay strict (1998 never matches 1999).
        if _is_wordlike(t) and _is_wordlike(a) and _within_one_edit(a, t):
            return True
    return False


async def submit(conn, player_id, session, answer: str) -> dict:
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

    ctx = await load_context(conn, player_id, gid, session["story_time"])
    ok = _check(json.loads(pz["solution"]), answer, ctx)

    attempts = pp["attempts"] + 1

    # Weave the riddle and the player's answer into the narration flow, the way NPC
    # gate dialogue is woven in — so the events list preserves the whole exchange,
    # not just the terse outcome line. The prompt is recorded once (deduped; also
    # shown on arrival at the node); every submitted answer is recorded. Both are
    # seq-stamped beats, so they roll back with the rest of the run.
    await narrate_puzzle_prompt(conn, session["log_id"], gid, node, session["story_time"])
    await narrate(conn, session["log_id"], gid, node_id=node["id"],
                  story_time=session["story_time"],
                  summary=f'You answer: "{answer.strip()}"', kind="puzzle")

    if ok:
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
               WHERE player_id=$2 AND game_id=$3""",
            story_time, player_id, gid)
        session["story_time"] = story_time
        await discover_clues(conn, player_id, gid, session["log_id"], story_time, node["id"])
        return {"solved": True, "message": on_solve.get("log", "It opens.")}

    # No auto-hint: only count the attempt. Hints are revealed solely when the
    # player asks (request_hint), and only after they've tried at least once.
    await conn.execute(
        "UPDATE puzzle_progress SET attempts=$4 WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
        player_id, gid, pz["id"], attempts)
    return {"solved": False, "message": "Nothing happens."}


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
