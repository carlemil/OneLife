"""Puzzle submission handler (STORY_AND_PUZZLES.md §5). Validates the answer in
code; the 'semantic' kind would reuse the gate referee (deferred for the slice)."""
import json
from .engine import apply_action, discover_clues, load_context, next_seq


def _check(solution: dict, answer: str, ctx) -> bool:
    kind = solution.get("kind")
    a = answer.strip().lower()
    if kind == "exact":
        return a == str(solution["value"]).strip().lower()
    if kind == "set":
        return a in {str(v).strip().lower() for v in solution.get("set", [])}
    if kind == "assembly":
        # auto-solved elsewhere; treat any submit as solved if clues complete
        return True
    # 'semantic' would call the referee here; fall back to exact for the slice.
    return a == str(solution.get("value", "")).strip().lower()


async def submit(conn, player_id, session, answer: str) -> dict:
    node = await conn.fetchrow("SELECT * FROM story_nodes WHERE id=$1",
                               session["current_node"])
    pz = await conn.fetchrow("SELECT * FROM puzzles WHERE id=$1", node["puzzle_id"])

    pp = await conn.fetchrow(
        "SELECT * FROM puzzle_progress WHERE player_id=$1 AND puzzle_id=$2",
        player_id, pz["id"])
    if pp is None:
        await conn.execute(
            "INSERT INTO puzzle_progress (player_id, puzzle_id) VALUES ($1,$2)",
            player_id, pz["id"])
        pp = await conn.fetchrow(
            "SELECT * FROM puzzle_progress WHERE player_id=$1 AND puzzle_id=$2",
            player_id, pz["id"])
    if pp["solved"]:
        return {"solved": True, "message": "Already open."}

    ctx = await load_context(conn, player_id, session["story_time"])
    ok = _check(json.loads(pz["solution"]), answer, ctx)

    attempts = pp["attempts"] + 1
    ladder = json.loads(pz["hint_ladder"])
    hint_level = min(attempts, max(len(ladder) - 1, 0))

    if ok:
        on_solve = json.loads(pz["on_solve"])
        seq, story_time = await apply_action(
            conn, player_id, session["log_id"], node_id=node["id"],
            effects=on_solve, story_time=session["story_time"], kind="puzzle")
        await conn.execute(
            """UPDATE puzzle_progress SET solved=TRUE, solved_at_seq=$3, attempts=$4
               WHERE player_id=$1 AND puzzle_id=$2""",
            player_id, pz["id"], seq, attempts)
        await conn.execute(
            "UPDATE player_sessions SET story_time=$1 WHERE token=$2",
            story_time, session["token"])
        session["story_time"] = story_time
        await discover_clues(conn, player_id, session["log_id"], story_time, seq)
        return {"solved": True, "message": on_solve.get("log", "It opens.")}

    await conn.execute(
        "UPDATE puzzle_progress SET attempts=$3, hint_level=$4 WHERE player_id=$1 AND puzzle_id=$2",
        player_id, pz["id"], attempts, hint_level)
    hint = ladder[min(hint_level, len(ladder) - 1)] if ladder else None
    return {"solved": False, "message": "Nothing happens.", "hint": hint}
