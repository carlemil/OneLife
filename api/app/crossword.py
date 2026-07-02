"""Crossword geometry & validation (STORY_AND_PUZZLES.md §5).

A crossword `solution` is `{kind: "crossword", entries: [...]}`, where each entry is
`{id, dir: across|down, row, col, clue, answer}` (0-indexed row/col from the top-left).
This module is the single source of truth for grid geometry, standard crossword
numbering, and structural/interlock validation — shared by `content.py` (lint),
`puzzles.py` (submit), and `engine.py` (render_state). Pure stdlib, no I/O; the grid and
numbering are derived, never authored.
"""


def entry_flag(puzzle_id: str, entry_id: str) -> str:
    """Per-entry solved marker (a player flag). The `__xword__` prefix can't collide
    with authored flags, so crossword progress reuses the seq-stamped flag/rollback
    path for free."""
    return f"__xword__{puzzle_id}__{entry_id}"


def _entries(solution: dict) -> list:
    return solution.get("entries", []) or []


def cells_of(entry: dict) -> list:
    """The (row, col) cells an entry covers, in answer order."""
    r, c = int(entry["row"]), int(entry["col"])
    n = len(str(entry.get("answer", "")))
    if entry.get("dir") == "down":
        return [(r + i, c) for i in range(n)]
    return [(r, c + i) for i in range(n)]


def entry_ids(solution: dict) -> list:
    return [str(e["id"]) for e in _entries(solution)]


def answer_of(solution: dict, entry_id: str) -> str:
    """The UPPERCASE answer for one entry id, or "" if unknown."""
    for e in _entries(solution):
        if str(e.get("id")) == str(entry_id):
            return str(e.get("answer", "")).upper()
    return ""


def parse(solution: dict) -> dict:
    """Grid geometry + standard crossword numbering. Returns
    `{rows, cols, entries:[{id,dir,row,col,len,clue,number}], numbers:{(r,c):n}}`.
    Numbering: every word start-cell is numbered row-major from 1; an across and a
    down sharing a start cell share the number."""
    entries = _entries(solution)
    rows = cols = 0
    starts = set()
    for e in entries:
        for (r, c) in cells_of(e):
            rows = max(rows, r + 1)
            cols = max(cols, c + 1)
        starts.add((int(e["row"]), int(e["col"])))
    numbers = {}
    for i, rc in enumerate(sorted(starts), start=1):
        numbers[rc] = i
    out = []
    for e in entries:
        r, c = int(e["row"]), int(e["col"])
        out.append({
            "id": str(e["id"]),
            "dir": e.get("dir"),
            "row": r, "col": c,
            "len": len(str(e.get("answer", ""))),
            "clue": e.get("clue", ""),
            "number": numbers.get((r, c)),
        })
    return {"rows": rows, "cols": cols, "entries": out, "numbers": numbers}


def known_letters(solution: dict, solved_entry_ids) -> dict:
    """`{(r,c): "X"}` for every cell filled by a solved entry — a cell is revealed if
    *any* covering solved entry fills it (the interlock helper)."""
    solved = {str(x) for x in solved_entry_ids}
    out = {}
    for e in _entries(solution):
        if str(e.get("id")) not in solved:
            continue
        ans = str(e.get("answer", "")).upper()
        for i, rc in enumerate(cells_of(e)):
            if i < len(ans):
                out[rc] = ans[i]
    return out


def validate(solution: dict) -> list:
    """Structural checks (unique ids; valid dir; non-negative row/col; non-empty
    letters-only answer) plus interlock consistency (crossing letters must agree).
    Returns a list of error strings (empty = valid)."""
    errors = []
    entries = _entries(solution)
    if not entries:
        return ["crossword has no entries"]
    seen = set()
    cell_letters = {}  # (r,c) -> (letter, entry_id) — first writer wins, rest must match
    for e in entries:
        eid = str(e.get("id", ""))
        if not eid:
            errors.append("crossword entry is missing an id")
            continue
        if eid in seen:
            errors.append(f"crossword duplicate entry id {eid}")
        seen.add(eid)
        good_dir = e.get("dir") in ("across", "down")
        if not good_dir:
            errors.append(f"crossword entry {eid} has invalid dir {e.get('dir')!r} (across|down)")
        try:
            r, c = int(e["row"]), int(e["col"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"crossword entry {eid} has invalid or missing row/col")
            continue
        if r < 0 or c < 0:
            errors.append(f"crossword entry {eid} has negative row/col")
        ans = str(e.get("answer", "")).upper()
        if not ans:
            errors.append(f"crossword entry {eid} has an empty answer")
            continue
        if not ans.isalpha():
            errors.append(f"crossword entry {eid} answer must be letters only")
        if not good_dir:
            continue
        for i, rc in enumerate(cells_of(e)):
            if i >= len(ans):
                break
            letter = ans[i]
            prev = cell_letters.get(rc)
            if prev is None:
                cell_letters[rc] = (letter, eid)
            elif prev[0] != letter:
                errors.append(
                    f"crossword interlock mismatch at row {rc[0]}, col {rc[1]}: "
                    f"{prev[1]}='{prev[0]}' vs {eid}='{letter}'")
    return errors
