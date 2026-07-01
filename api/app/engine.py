"""Story-graph engine: context snapshot, effect applier, clue discovery,
state rendering, and the uniform rollback (STORY_AND_PUZZLES.md §6/§7)."""
import json
import math
import re
from collections import deque
from .dsl import PlayerContext, evaluate
from . import memory, gameconfig


def _name_tokens(full_name: str, particles: set) -> list[str]:
    """Distinctive name tokens (so "Herr Dödblek" is "known" from "Dödblek", not just
    "Herr"). `particles` (titles/honorifics that aren't the name) is per-game config."""
    return [t for t in re.findall(r"[^\W\d_]+", (full_name or "").lower())
            if len(t) >= 3 and t not in particles]


# --------------------------------------------------------------------------- #
#  Context snapshot
# --------------------------------------------------------------------------- #
async def load_context(conn, player_id, game_id, story_time: int = 0) -> PlayerContext:
    flags = await conn.fetch(
        "SELECT flag FROM player_flags WHERE player_id=$1 AND game_id=$2", player_id, game_id)
    visited = await conn.fetch(
        """SELECT DISTINCT le.node_id FROM log_entries le
           JOIN game_logs g ON g.id = le.log_id
           WHERE g.player_id=$1 AND g.game_id=$2 AND NOT le.rolled_back
             AND le.node_id IS NOT NULL""",
        player_id, game_id)
    gates = await conn.fetch(
        "SELECT gate_id FROM gate_attempts WHERE player_id=$1 AND game_id=$2 AND satisfied",
        player_id, game_id)
    puzzles = await conn.fetch(
        "SELECT puzzle_id FROM puzzle_progress WHERE player_id=$1 AND game_id=$2 AND solved",
        player_id, game_id)
    clues = await conn.fetch(
        "SELECT clue_id FROM player_clues WHERE player_id=$1 AND game_id=$2", player_id, game_id)
    # A character's name is "known" once an NPC has actually spoken it to this
    # player — in that character's own gate, or another's (leaked memories surface
    # as dialogue). The player typing a name doesn't count (role='agent' only).
    # Match on any distinctive name token (first name OR surname), so e.g. "I'm
    # Vallmo" reveals "Kurt Vallmo".
    chars = await conn.fetch("SELECT id, name, reveal_name FROM characters WHERE game_id=$1", game_id)
    said = await conn.fetch(
        "SELECT content FROM gate_messages WHERE player_id=$1 AND game_id=$2 AND role='agent'",
        player_id, game_id)
    spoken = "\n".join((r["content"] or "") for r in said).lower()
    particles = set(gameconfig.for_game(game_id).get("name_particles") or [])
    known_names = set()
    for c in chars:
        toks = _name_tokens(c["reveal_name"] or c["name"], particles)  # true name (reveal if withholding)
        if toks and any(re.search(r"\b" + re.escape(t) + r"\b", spoken) for t in toks):
            known_names.add(c["id"])
    ge, lc = await current_alignment(conn, player_id, game_id)
    return PlayerContext(
        flags={r["flag"] for r in flags},
        visited_nodes={r["node_id"] for r in visited},
        passed_gates={r["gate_id"] for r in gates},
        solved_puzzles={r["puzzle_id"] for r in puzzles},
        found_clues={r["clue_id"] for r in clues},
        known_names=known_names,
        story_time=story_time,
        good_evil=ge,
        law_chaos=lc,
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


async def narrate(conn, log_id, game_id, *, node_id, story_time, summary, kind) -> int:
    """Append a standalone narration beat to the flow (a log entry with no side
    effects). Used for world changes that aren't actions in their own right —
    discovering a clue, the map opening up — so they show in the running story."""
    seq = await next_seq(conn, log_id)
    await conn.execute(
        """INSERT INTO log_entries (log_id, game_id, seq, story_time, node_id, summary, kind)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        log_id, game_id, seq, story_time, node_id, summary, kind)
    return seq


async def reveal_cells_around(conn, player_id, game_id, node_id, seq, log_id, story_time,
                              *, neighbors: bool = True) -> None:
    """Fog-of-war: discover the cell `node_id` sits in. With `neighbors=True` (the
    default) also discover the orthogonally adjacent cells and narrate the newly
    revealed ones as one 'travel' beat ("the map opens"). With `neighbors=False`
    only the node's own cell is discovered, silently — used at game start so the
    neighbours aren't pre-revealed before the player reaches a world-access node.
    Stamps each discovery with `seq` (the beat's seq for fresh neighbours) so
    rollback voids it. Deduped against already-discovered cells, so it's safe to
    call on every world-access arrival — the beat fires only once."""
    cell = await conn.fetchrow(
        """SELECT w.id, w.grid_x, w.grid_y FROM story_nodes n
           JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
           JOIN world_cells w ON w.id = l.cell_id AND w.game_id = l.game_id
           WHERE n.game_id=$1 AND n.id=$2""", game_id, node_id)
    if cell is None:
        return
    if not neighbors:
        await conn.execute(
            """INSERT INTO player_cells (player_id, game_id, cell_id, found_at_seq)
               VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING""",
            player_id, game_id, cell["id"], seq)
        return
    near = await conn.fetch(
        """SELECT id, name FROM world_cells
           WHERE game_id=$1 AND abs(grid_x-$2)+abs(grid_y-$3) <= 1""",
        game_id, cell["grid_x"], cell["grid_y"])
    have = {r["cell_id"] for r in await conn.fetch(
        "SELECT cell_id FROM player_cells WHERE player_id=$1 AND game_id=$2", player_id, game_id)}
    fresh = [c for c in near if c["id"] not in have and c["id"] != cell["id"]]
    beat_seq = seq
    if fresh:
        names = ", ".join(c["name"] for c in fresh)
        beat_seq = await narrate(
            conn, log_id, game_id, node_id=node_id, story_time=story_time,
            summary=f"New paths open on your map: {names}.", kind="travel")
    for c in near:
        if c["id"] in have:
            continue
        stamp = beat_seq if c["id"] != cell["id"] else seq
        await conn.execute(
            """INSERT INTO player_cells (player_id, game_id, cell_id, found_at_seq)
               VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING""",
            player_id, game_id, c["id"], stamp)


# --------------------------------------------------------------------------- #
#  Apply one action: a single log entry + its effects, all stamped with seq.
# --------------------------------------------------------------------------- #
async def apply_action(conn, player_id, game_id, log_id, *, node_id: str | None,
                       effects: dict, story_time: int,
                       kind: str = "action", summary_override: str | None = None):
    seq = await next_seq(conn, log_id)
    story_time = story_time + int(effects.get("advance_story_time", 0))
    summary = summary_override or effects.get("log", "") \
        or _FALLBACK_SUMMARY.get(kind, "Something shifts.")

    await conn.execute(
        """INSERT INTO log_entries (log_id, game_id, seq, story_time, node_id, summary, kind)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        log_id, game_id, seq, story_time, node_id, summary, kind)

    pts = int(effects.get("progress_points", 0))
    if pts:
        await conn.execute(
            """INSERT INTO progress_events (player_id, game_id, seq, kind, points)
               VALUES ($1,$2,$3,$4,$5)""", player_id, game_id, seq, kind, pts)

    if "set_flag" in effects:
        await conn.execute(
            """INSERT INTO player_flags (player_id, game_id, flag, set_at_seq)
               VALUES ($1,$2,$3,$4) ON CONFLICT (player_id, game_id, flag) DO NOTHING""",
            player_id, game_id, effects["set_flag"], seq)
    if "clear_flag" in effects:
        await conn.execute(
            "DELETE FROM player_flags WHERE player_id=$1 AND game_id=$2 AND flag=$3",
            player_id, game_id, effects["clear_flag"])

    # write_memory is recorded in the log summary for the slice; the full
    # agent_memories/pgvector path is deferred (see DATA_MODEL.md).
    return seq, story_time


# --------------------------------------------------------------------------- #
#  Alignment (D&D-style): running coordinate on two axes, seq-stamped per the
#  rollback invariant. good_evil = +good/-evil; law_chaos = +lawful/-chaotic.
# --------------------------------------------------------------------------- #
def _clamp_unit(v: float) -> float:
    return -1.0 if v < -1.0 else 1.0 if v > 1.0 else v


async def current_alignment(conn, player_id, game_id) -> tuple[float, float]:
    """The player's latest surviving (good_evil, law_chaos) in this game; (0.0, 0.0)
    if none. Rollback deletes events, so the newest remaining row is always current."""
    row = await conn.fetchrow(
        """SELECT good_evil, law_chaos FROM player_alignment_events
           WHERE player_id=$1 AND game_id=$2 ORDER BY id DESC LIMIT 1""", player_id, game_id)
    return (float(row["good_evil"]), float(row["law_chaos"])) if row else (0.0, 0.0)


async def record_alignment(conn, player_id, game_id, seq: int, ge_delta: float,
                           lc_delta: float, reason: str, kind: str) -> None:
    """Append one judged shift, stamped with the log `seq` it happened at so it
    rolls back with that beat. The stored coordinate is cumulative + clamped to
    [-1,1]; the raw delta is kept too. No-op for a ~zero shift (neutral actions
    don't clutter the trail). Must run inside the caller's transaction."""
    if abs(ge_delta) < 1e-6 and abs(lc_delta) < 1e-6:
        return
    ge0, lc0 = await current_alignment(conn, player_id, game_id)
    ge, lc = _clamp_unit(ge0 + ge_delta), _clamp_unit(lc0 + lc_delta)
    await conn.execute(
        """INSERT INTO player_alignment_events
           (player_id, game_id, created_seq, good_evil_delta, law_chaos_delta,
            good_evil, law_chaos, reason, kind)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        player_id, game_id, seq, ge_delta, lc_delta, ge, lc, reason or "", kind)


def alignment_label(ge: float, lc: float) -> str:
    """A D&D label for a coordinate, e.g. 'chaotic-good', 'lawful neutral',
    'true neutral' (the 3x3 grid; ±0.33 thresholds)."""
    g = "good" if ge >= 0.33 else "evil" if ge <= -0.33 else "neutral"
    l = "lawful" if lc >= 0.33 else "chaotic" if lc <= -0.33 else "neutral"
    if g == "neutral" and l == "neutral":
        return "true neutral"
    if g == "neutral":
        return f"{l} neutral"
    if l == "neutral":
        return f"neutral {g}"
    return f"{l}-{g}"


def alignment_short(ge: float, lc: float) -> str:
    """Two-letter D&D alignment code for a coordinate, e.g. 'CE' (chaotic evil),
    'LG' (lawful good), 'TN' (true neutral), 'NG' (neutral good). First letter is
    the law/chaos axis (L/C/N), second the good/evil axis (G/E/N); same ±0.33
    thresholds as alignment_label."""
    g = "G" if ge >= 0.33 else "E" if ge <= -0.33 else "N"
    l = "L" if lc >= 0.33 else "C" if lc <= -0.33 else "N"
    return "TN" if g == "N" and l == "N" else f"{l}{g}"


async def narrate_puzzle_prompt(conn, log_id, game_id, node, story_time):
    """Record a puzzle's prompt as a beat the first time the player reaches it, so
    the events flow shows what was actually asked — not just that a riddle happened.
    Deduped by the prompt text (re-entering the node won't repeat it); rolls back
    with the rest since it's a seq-stamped beat."""
    if not node["puzzle_id"]:
        return
    prompt = await conn.fetchval("SELECT prompt FROM puzzles WHERE game_id=$1 AND id=$2",
                                 game_id, node["puzzle_id"])
    if not prompt:
        return
    exists = await conn.fetchval(
        """SELECT 1 FROM log_entries
           WHERE log_id=$1 AND node_id=$2 AND summary=$3 AND NOT rolled_back""",
        log_id, node["id"], prompt)
    if not exists:
        await narrate(conn, log_id, game_id, node_id=node["id"], story_time=story_time,
                      summary=prompt, kind="puzzle")


async def discover_clues(conn, player_id, game_id, log_id, story_time, node_id=None):
    """After a state change, discover any clues whose conditions now hold. Each
    newly found clue writes its own 'clue' beat into the narration flow, and the
    clue is stamped with that beat's seq so the two roll back together."""
    ctx = await load_context(conn, player_id, game_id, story_time)
    rows = await conn.fetch(
        "SELECT id, reveal_text, discover_conditions FROM puzzle_clues WHERE game_id=$1", game_id)
    for r in rows:
        if r["id"] in ctx.found_clues:
            continue
        if evaluate(json.loads(r["discover_conditions"]), ctx):
            seq = await narrate(
                conn, log_id, game_id, node_id=node_id, story_time=story_time,
                summary=f"You notice: {r['reveal_text']}", kind="clue")
            await conn.execute(
                """INSERT INTO player_clues (player_id, game_id, clue_id, found_at_seq)
                   VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING""",
                player_id, game_id, r["id"], seq)
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


# --------------------------------------------------------------------------- #
#  Cell map: place a cell's nodes as ellipse hotspots, edges as roads
# --------------------------------------------------------------------------- #
# A node shows on its cell map if it has an explicit `map` (author-placed in the
# editor) or, as a fallback so navigation works before anything is placed, if it
# is a "place"/NPC/ending node. Puzzle, narration and death nodes stay OFF the
# map (reached via the few remaining in-place buttons).
_PLACE_TYPES = ("location", "gate", "ending")

# The overview-map parchment backdrop, served from /content. Both the player map and
# the admin editor use this same image so their layouts line up.
MAP_BACKGROUND = "images/maps/parchment2.png"


def _jmap(v) -> dict:
    if v is None:
        return {}
    return (json.loads(v) if isinstance(v, str) else v) or {}


def _auto_layout(ids: list[str]) -> dict:
    """Deterministic grid placement (normalized 0..1) for nodes lacking an
    explicit `map`, so every place is clickable before the author positions it."""
    ids = sorted(ids)
    n = len(ids)
    cols = max(1, math.ceil(math.sqrt(n)))
    out = {}
    for i, nid in enumerate(ids):
        c, r = i % cols, i // cols
        rows = max(1, math.ceil(n / cols))
        x = 0.10 + ((c + 0.5) / cols) * 0.80
        y = 0.12 + ((r + 0.5) / rows) * 0.76
        out[nid] = {"x": round(x, 4), "y": round(y, 4), "rx": 0.05, "ry": 0.045}
    return out


async def map_block(conn, node) -> tuple[dict | None, set]:
    """Build the cell-map view for the player's current node: the background
    image, every mapped node as an ellipse, intra-cell edges as roads, and the
    world-exit hotspot. Returns (block, mapped_node_ids)."""
    if not node["location_id"]:
        return None, set()
    gid = node["game_id"]
    cell = await conn.fetchrow(
        """SELECT w.id, w.map_image, w.world_exit FROM locations l
           JOIN world_cells w ON w.id = l.cell_id AND w.game_id = l.game_id
           WHERE l.game_id=$1 AND l.id=$2""", gid, node["location_id"])
    if cell is None:
        return None, set()
    rows = await conn.fetch(
        """SELECT n.id, n.title, n.type, n.map FROM story_nodes n
           JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
           WHERE n.game_id=$1 AND l.cell_id=$2""", gid, cell["id"])
    mapped, explicit, auto_ids = {}, {}, []
    for r in rows:
        if r["type"] == "death":
            continue
        m = _jmap(r["map"])
        if m.get("x") is None and r["type"] not in _PLACE_TYPES:
            continue
        mapped[r["id"]] = {"id": r["id"], "title": r["title"], "type": r["type"]}
        if m.get("x") is not None:
            explicit[r["id"]] = {"x": float(m["x"]), "y": float(m["y"]),
                                 "rx": float(m.get("rx", 0.05)), "ry": float(m.get("ry", 0.045))}
        else:
            auto_ids.append(r["id"])
    auto = _auto_layout(auto_ids)
    for nid, info in mapped.items():
        info.update(explicit.get(nid) or auto[nid])
    roads = []
    if mapped:
        ids = list(mapped)
        erows = await conn.fetch(
            """SELECT from_node, to_node FROM story_edges
               WHERE game_id=$2 AND from_node = ANY($1::text[]) AND to_node = ANY($1::text[])""",
            ids, gid)
        seen = set()
        for e in erows:
            if e["from_node"] == e["to_node"]:
                continue
            k = tuple(sorted((e["from_node"], e["to_node"])))
            if k not in seen:
                seen.add(k)
                roads.append({"from": k[0], "to": k[1]})
    we = _jmap(cell["world_exit"])
    if we.get("x") is None:
        we = {"x": 0.92, "y": 0.92, "rx": 0.06, "ry": 0.05}
    block = {
        "cell": cell["id"],
        "image": cell["map_image"] or f"images/maps/{cell['id']}.png",
        "nodes": list(mapped.values()),
        "roads": roads,
        "world_exit": {**we, "available": bool(node["world_access"])},
        "current": node["id"] if node["id"] in mapped else None,
    }
    return block, set(mapped)


def _map_xy(nm: dict, fallback):
    """A node's stable overview position: its saved normalized `map: {x, y}` when both
    are present, else the graph-layout-derived `fallback` (x, y). Returns (x, y)."""
    mx, my = nm.get("x"), nm.get("y")
    if mx is not None and my is not None:
        try:
            return float(mx), float(my)
        except (TypeError, ValueError):
            pass
    return fallback


def _normalize_positions(pts: dict, margin: float = 0.2) -> dict:
    """Map raw graph-editor pixel positions {id: (x, y)} into normalized 0..1
    map coordinates, preserving the layout's shape, inset by `margin` so icons
    and their labels never touch the parchment edge. The whole set is normalized
    together (not just discovered ids) so the layout is stable as fog lifts."""
    if not pts:
        return {}
    xs = [p[0] for p in pts.values()]
    ys = [p[1] for p in pts.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = 1.0 - 2 * margin
    out = {}
    for k, (x, y) in pts.items():
        nx = 0.5 if maxx == minx else (x - minx) / (maxx - minx)
        ny = 0.5 if maxy == miny else (y - miny) / (maxy - miny)
        out[k] = (round(margin + nx * span, 4), round(margin + ny * span, 4))
    return out


async def unified_map_block(conn, player_id, node, story_time: int) -> tuple[dict, set]:
    """The single overview map that replaces both the per-cell and world maps: one
    icon per *location* (place) across all discovered cells, laid out from the live
    story-graph editor positions (`node_positions`) on the parchment background.

    A reachable icon is clickable: places in the current cell `walk` there, the
    arrival place of an orthogonally-adjacent discovered cell `travel`s there (only
    when the current node is world-access). Returns (block, on_map_node_ids) — the
    second value is the set of current-cell anchor nodes the map now handles, so the
    caller can drop their edges from the text choices (they become map clicks)."""
    gid = node["game_id"]
    # One anchor node per location: the `location`-typed node when a place has one
    # (a hub like the school), else its sole gate/ending place node.
    rows = await conn.fetch(
        """SELECT DISTINCT ON (l.id)
                  l.id AS loc_id, l.cell_id, n.id AS node_id, n.title, n.type, n.map AS nmap,
                  w.arrival_node, w.grid_x, w.grid_y, np.x AS px, np.y AS py
           FROM story_nodes n
           JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
           JOIN world_cells w ON w.id = l.cell_id AND w.game_id = l.game_id
           LEFT JOIN node_positions np ON np.node_id = n.id AND np.game_id = n.game_id
           WHERE n.game_id = $1 AND n.type IN ('location', 'gate', 'ending')
           ORDER BY l.id, (n.type = 'location') DESC, n.id""", gid)

    # Normalize every place's position together (stable layout), filling any place
    # missing an editor position with a deterministic grid slot so it still shows.
    placed = {r["loc_id"]: (float(r["px"]), float(r["py"]))
              for r in rows if r["px"] is not None}
    norm = _normalize_positions(placed)
    missing = [r["loc_id"] for r in rows if r["px"] is None]
    for nid, auto in _auto_layout(missing).items():
        norm[nid] = (auto["x"], auto["y"])

    discovered = {r["cell_id"] for r in await conn.fetch(
        "SELECT cell_id FROM player_cells WHERE player_id=$1 AND game_id=$2", player_id, gid)}
    cur_cell = await conn.fetchrow(
        """SELECT w.id, w.grid_x, w.grid_y FROM locations l
           JOIN world_cells w ON w.id = l.cell_id AND w.game_id = l.game_id
           WHERE l.game_id=$1 AND l.id=$2""",
        gid, node["location_id"]) if node["location_id"] else None
    walk_paths = await cell_walk_paths(conn, player_id, node, story_time)
    ctx = await load_context(conn, player_id, gid, story_time)

    nodes, on_map = [], set()
    for r in rows:
        if r["cell_id"] not in discovered:
            continue                                   # fog-of-war
        loc = r["loc_id"]
        is_current = r["loc_id"] == node["location_id"]   # the place you're in
        action, target, reachable = None, None, False
        if cur_cell is not None and r["cell_id"] == cur_cell["id"]:
            if r["node_id"] in walk_paths:             # walk within the cell
                action, target, reachable = "walk", r["node_id"], True
                on_map.add(r["node_id"])
        elif (node["world_access"] and cur_cell is not None
              and r["cell_id"] in discovered
              and r["node_id"] == r["arrival_node"]
              and abs(r["grid_x"] - cur_cell["grid_x"])
                  + abs(r["grid_y"] - cur_cell["grid_y"]) == 1):
            action, target, reachable = "travel", r["cell_id"], True
        # Per-icon scale factor, stored alongside the node's saved map position
        # (story_nodes.map.scale); defaults to 1.0 when absent.
        nm = r["nmap"]
        nm = json.loads(nm) if isinstance(nm, str) else (nm or {})
        try:
            scale = float(nm.get("scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        # Stable overview position from `map: {x, y}` (player + editor share it), with the
        # graph-layout spot as the fallback for never-positioned places.
        x_y = _map_xy(nm, norm[loc])
        nodes.append({"id": loc, "title": r["title"],
                      "icon": f"images/maps/icons/{loc}.png",
                      "x": x_y[0], "y": x_y[1], "current": bool(is_current),
                      "reachable": reachable, "action": action, "target": target,
                      "visited": bool(r["node_id"] in ctx.visited_nodes),
                      "scale": scale})

    # Roads: edges between shown anchors, keyed by location id to match the icons.
    shown = {r["loc_id"]: r for r in rows if r["cell_id"] in discovered}
    node_to_loc = {r["node_id"]: r["loc_id"] for r in shown.values()}
    roads, seen = [], {}
    if node_to_loc:
        erows = await conn.fetch(
            """SELECT from_node, to_node, road FROM story_edges
               WHERE game_id=$2 AND from_node = ANY($1::text[]) AND to_node = ANY($1::text[])""",
            list(node_to_loc), gid)
        for e in erows:
            a, b = node_to_loc.get(e["from_node"]), node_to_loc.get(e["to_node"])
            if not a or not b or a == b:
                continue
            k = tuple(sorted((a, b)))
            pts = e["road"] if isinstance(e["road"], list) else json.loads(e["road"] or "[]")
            if a == k[1]:                       # edge runs k1->k0; flip to the road's k0->k1
                pts = list(reversed(pts))
            if k not in seen:
                rd = {"from": k[0], "to": k[1], "points": pts}
                seen[k] = rd; roads.append(rd)
            elif pts and not seen[k].get("points"):
                seen[k]["points"] = pts          # a sibling edge carries the geometry

    block = {"image": MAP_BACKGROUND, "nodes": nodes, "roads": roads}
    return block, on_map


_ANCHOR_QUERY = """
    SELECT DISTINCT ON (l.id)
           l.id AS loc_id, l.cell_id AS cell, n.id AS node_id, n.title,
           n.map AS nmap, np.x AS px, np.y AS py
    FROM story_nodes n
    JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
    JOIN world_cells w ON w.id = l.cell_id AND w.game_id = l.game_id
    LEFT JOIN node_positions np ON np.node_id = n.id AND np.game_id = n.game_id
    WHERE n.game_id = $1 AND n.type IN ('location', 'gate', 'ending')
    ORDER BY l.id, (n.type = 'location') DESC, n.id"""


def _edge_points(road) -> list:
    return road if isinstance(road, list) else json.loads(road or "[]")


async def map_overview(conn, game_id) -> dict:
    """The whole overview map for the admin road editor: every location's icon at its
    normalized position (no fog, no reachability) + every road with its saved spline
    points. Same layout/normalization the player map uses, so routes computed here
    line up there."""
    rows = await conn.fetch(_ANCHOR_QUERY, game_id)
    placed = {r["loc_id"]: (float(r["px"]), float(r["py"]))
              for r in rows if r["px"] is not None}
    norm = _normalize_positions(placed)
    for nid, auto in _auto_layout([r["loc_id"] for r in rows if r["px"] is None]).items():
        norm[nid] = (auto["x"], auto["y"])
    node_to_loc = {r["node_id"]: r["loc_id"] for r in rows}

    # Normalization bounds, so a drag in 0..1 map space can be inverted back to the
    # node's graph-editor pixel position (matches _normalize_positions: margin 0.2).
    pxs = [p[0] for p in placed.values()]
    pys = [p[1] for p in placed.values()]
    bounds = {"minx": min(pxs), "maxx": max(pxs), "miny": min(pys), "maxy": max(pys),
              "margin": 0.2} if placed else None

    nodes = []
    for r in rows:
        nm = r["nmap"]
        nm = json.loads(nm) if isinstance(nm, str) else (nm or {})
        try:
            scale = float(nm.get("scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        # A node's saved `map: {x, y}` is the STABLE overview position (normalized 0..1)
        # — used verbatim, so a dragged icon stays exactly where it was dropped and no
        # other icon moves. Only places never positioned on the map fall back to the
        # graph-layout-derived spot (`norm`).
        x, y = _map_xy(nm, norm.get(r["loc_id"]))
        nodes.append({"id": r["loc_id"], "node_id": r["node_id"], "cell": r["cell"],
                      "title": r["title"],
                      "icon": f"images/maps/icons/{r['loc_id']}.png",
                      "x": x, "y": y, "scale": scale})

    roads, seen = [], {}
    erows = await conn.fetch(
        """SELECT from_node, to_node, road FROM story_edges
           WHERE game_id=$2 AND from_node = ANY($1::text[]) AND to_node = ANY($1::text[])""",
        list(node_to_loc), game_id)
    for e in erows:
        a, b = node_to_loc.get(e["from_node"]), node_to_loc.get(e["to_node"])
        if not a or not b or a == b:
            continue
        k = tuple(sorted((a, b)))
        pts = _edge_points(e["road"])
        if a == k[1]:
            pts = list(reversed(pts))
        if k not in seen:
            rd = {"from": k[0], "to": k[1], "points": pts}
            seen[k] = rd; roads.append(rd)
        elif pts and not seen[k]["points"]:
            seen[k]["points"] = pts

    # The cell a fresh player starts in (entry node's cell) — the editor's fog
    # preview shows only this cell's places.
    start_cell = await conn.fetchval(
        """SELECT l.cell_id FROM story_nodes n
           JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
           WHERE n.game_id=$1 AND n.is_entry LIMIT 1""", game_id)
    return {"image": MAP_BACKGROUND, "nodes": nodes, "roads": roads,
            "bounds": bounds, "start_cell": start_cell}


async def map_road_anchors(conn, game_id) -> dict:
    """loc_id -> its anchor node id (the node a road between two places connects)."""
    rows = await conn.fetch(_ANCHOR_QUERY, game_id)
    return {r["loc_id"]: r["node_id"] for r in rows}


async def traverse_edge(conn, sess, edge) -> int:
    """Apply a single story edge for the player: log the move, advance the
    current node + story_time, narrate any puzzle prompt, and discover clues.
    Mutates `sess` (current_node/story_time) in place. Returns the move's log seq
    (so the /api/edge handler can stamp an alignment event onto it). The caller
    must have already validated the edge is available and its conditions pass."""
    gid = sess["game_id"]
    effects = json.loads(edge["effects"])
    dest = await conn.fetchrow(
        "SELECT title, is_death, world_access FROM story_nodes WHERE game_id=$1 AND id=$2",
        gid, edge["to_node"])
    kind = "death" if (dest and dest["is_death"]) else "action"
    # Name the place when the edge has no authored line, so every move reads as a
    # beat in the flow rather than a blank step.
    override = None if effects.get("log") else (
        f"You go to {dest['title']}." if dest else None)
    seq, story_time = await apply_action(
        conn, sess["player_id"], gid, sess["log_id"], node_id=edge["to_node"],
        effects=effects, story_time=sess["story_time"], kind=kind,
        summary_override=override)
    await conn.execute(
        """UPDATE player_games SET current_node=$1, story_time=$2, last_played_at=now()
           WHERE player_id=$3 AND game_id=$4""",
        edge["to_node"], story_time, sess["player_id"], gid)
    sess["current_node"] = edge["to_node"]
    sess["story_time"] = story_time
    # Reaching a world-access node is the moment "the map opens" — reveal this cell's
    # neighbours then (not pre-revealed at game start). Deduped, so revisiting is a no-op.
    if dest and dest["world_access"]:
        await reveal_cells_around(
            conn, sess["player_id"], gid, sess["current_node"], seq, sess["log_id"], story_time)
    # Arriving at a puzzle node records the riddle/prompt in the flow, so the
    # events list shows what was actually asked (deduped — see the helper).
    dest_node = await conn.fetchrow(
        "SELECT id, puzzle_id FROM story_nodes WHERE game_id=$1 AND id=$2", gid, sess["current_node"])
    await narrate_puzzle_prompt(conn, sess["log_id"], gid, dest_node, story_time)
    await discover_clues(
        conn, sess["player_id"], gid, sess["log_id"], story_time, sess["current_node"])
    return seq


# Nodes you may pass *through* while walking the cell map. A gate/puzzle node can
# be a destination but never a waypoint — we never route a walk through an NPC
# encounter or a riddle.
_WALK_THROUGH = ("location", "ending")


async def cell_walk_paths(conn, player_id, node, story_time: int) -> dict:
    """Shortest edge-path from the player's current node to every other node in
    the same cell, returned as {node_id: [edge_id, …]}. Movement walks *through*
    place nodes (locations) only; gate/puzzle nodes can be a destination but not a
    waypoint. Powers one-click "walk anywhere in the cell" on the cell map, so a
    place reachable via the square hub is clickable even without a direct edge."""
    if not node["location_id"]:
        return {}
    gid = node["game_id"]
    cell = await conn.fetchrow(
        "SELECT cell_id FROM locations WHERE game_id=$1 AND id=$2", gid, node["location_id"])
    if cell is None:
        return {}
    rows = await conn.fetch(
        """SELECT n.id, n.type FROM story_nodes n
           JOIN locations l ON l.id=n.location_id AND l.game_id=n.game_id
           WHERE n.game_id=$1 AND l.cell_id=$2""", gid, cell["cell_id"])
    types = {r["id"]: r["type"] for r in rows}
    cell_ids = list(types)
    erows = await conn.fetch(
        """SELECT id, from_node, to_node, conditions FROM story_edges
           WHERE game_id=$2 AND from_node = ANY($1::text[]) AND to_node = ANY($1::text[])
           ORDER BY sort_order""", cell_ids, gid)
    ctx = await load_context(conn, player_id, gid, story_time)
    adj: dict = {}
    for e in erows:
        if not evaluate(json.loads(e["conditions"]), ctx):
            continue
        adj.setdefault(e["from_node"], []).append((e["to_node"], e["id"]))
    start = node["id"]
    prev = {start: None}          # node_id -> (came_from, edge_id)
    q = deque([start])
    while q:
        u = q.popleft()
        # Expand the start always; otherwise only walk on through a place node.
        if u != start and types.get(u) not in _WALK_THROUGH:
            continue
        for v, eid in adj.get(u, []):
            if v not in prev:
                prev[v] = (u, eid)
                q.append(v)
    paths: dict = {}
    for t in cell_ids:
        if t == start or t not in prev:
            continue
        path, cur = [], t
        while prev[cur] is not None:
            came, eid = prev[cur]
            path.append(eid)
            cur = came
        path.reverse()
        paths[t] = path
    return paths


async def render_state(conn, player_id, session) -> dict:
    gid = session["game_id"]
    node = await conn.fetchrow(
        "SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2", gid, session["current_node"])
    ctx = await load_context(conn, player_id, gid, session["story_time"])

    edges = await conn.fetch(
        "SELECT * FROM story_edges WHERE game_id=$1 AND from_node=$2 ORDER BY sort_order",
        gid, node["id"])
    # Look up each choice's destination so we can hide alternatives already completed.
    targets = {e["to_node"] for e in edges}
    tinfo = {}
    if targets:
        trows = await conn.fetch(
            """SELECT id, type, gate_id, puzzle_id FROM story_nodes
               WHERE game_id=$2 AND id = ANY($1::text[])""",
            list(targets), gid)
        tinfo = {r["id"]: r for r in trows}
    # Edges whose conditions hold, tagged with whether they lead into an
    # already-finished interaction (a passed gate or a solved puzzle).
    passing = []
    for e in edges:
        if not evaluate(json.loads(e["conditions"]), ctx):
            continue
        eff = json.loads(e["effects"]) if e["effects"] else {}
        t = tinfo.get(e["to_node"])
        # `keep_when_finished` opts an edge out of the declutter-hiding below: use it for
        # a hub gate that still has sub-content reached through it (e.g. the classroom,
        # a passed gate, is the only way back to the chalk letters), so passing the gate
        # never strands what lies beyond it.
        finished = (not eff.get("keep_when_finished")) and t is not None and (
            (t["type"] == "gate" and t["gate_id"] in ctx.passed_gates)
            or (t["type"] == "puzzle" and t["puzzle_id"] in ctx.solved_puzzles))
        passing.append((e, finished))
    # Hide finished interactions to keep the menu clean — but NEVER hide the only
    # way out. A gate node doubles as a location hub, so sub-nodes whose return
    # edge points back at a now-passed gate would otherwise be soft-locked. If
    # hiding leaves nothing, fall back to every condition-met edge.
    shown = [e for e, fin in passing if not fin] or [e for e, _ in passing]

    # Overview map: one icon per place. Travel happens by clicking an icon, so an
    # edge leading to a place the map can walk to in this cell is "on the map" and
    # is NOT offered as a button — only in-place actions (gate/puzzle entry,
    # examine, the risky force-door) stay as buttons.
    block, on_map_ids = await unified_map_block(
        conn, player_id, node, session["story_time"])
    visible = [{"id": e["id"], "label": e["label"], "danger": e["danger"],
                "to": e["to_node"], "on_map": e["to_node"] in on_map_ids} for e in shown]

    found = await conn.fetch(
        """SELECT pc.reveal_text FROM player_clues p
           JOIN puzzle_clues pc ON pc.id = p.clue_id AND pc.game_id = p.game_id
           WHERE p.player_id=$1 AND p.game_id=$2 ORDER BY p.found_at_seq""", player_id, gid)

    clipboard = await conn.fetchval("SELECT clipboard FROM players WHERE id=$1", player_id)
    state = {
        "node": {
            "id": node["id"], "type": node["type"], "title": node["title"],
            "body": resolve_body(node, ctx), "is_death": node["is_death"],
            "world_access": node["world_access"],
            "media": json.loads(node["media"]),
        },
        "edges": visible,
        "map": block,
        "notes": [r["reveal_text"] for r in found],
        "clipboard": clipboard or "",
        "story_time": session["story_time"],
        # Current alignment coordinate (the full drift history is on /api/alignment).
        "alignment": {"good_evil": ctx.good_evil, "law_chaos": ctx.law_chaos,
                      "label": alignment_label(ctx.good_evil, ctx.law_chaos)},
    }

    if node["type"] == "gate":
        ga = await conn.fetchrow(
            "SELECT * FROM gate_attempts WHERE player_id=$1 AND game_id=$2 AND gate_id=$3",
            player_id, gid, node["gate_id"])
        msgs = await conn.fetch(
            """SELECT role, content FROM gate_messages
               WHERE player_id=$1 AND game_id=$2 AND gate_id=$3 ORDER BY seq, created_at""",
            player_id, gid, node["gate_id"])
        char = await conn.fetchrow(
            """SELECT c.id, c.name, c.reveal_name FROM dialogue_gates g
               JOIN characters c ON c.id = g.character_id AND c.game_id = g.game_id
               WHERE g.game_id=$1 AND g.id=$2""",
            gid, node["gate_id"])
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
        pz = await conn.fetchrow("SELECT * FROM puzzles WHERE game_id=$1 AND id=$2",
                                 gid, node["puzzle_id"])
        pp = await conn.fetchrow(
            "SELECT * FROM puzzle_progress WHERE player_id=$1 AND game_id=$2 AND puzzle_id=$3",
            player_id, gid, node["puzzle_id"])
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
    gid = session["game_id"]
    target = await conn.fetchrow(
        "SELECT node_id, story_time FROM log_entries WHERE log_id=$1 AND seq=$2",
        log_id, to_seq)
    if target is None:
        raise ValueError("That point in your history is no longer available to go back to.")

    # seq is per-log, so every void/delete below is scoped to THIS (player, game) —
    # rolling back one save must never touch the player's other games.
    await conn.execute(
        "UPDATE log_entries SET rolled_back=TRUE WHERE log_id=$1 AND seq>$2",
        log_id, to_seq)
    await conn.execute(
        "UPDATE progress_events SET voided=TRUE WHERE player_id=$1 AND game_id=$2 AND seq>$3",
        player_id, gid, to_seq)
    await conn.execute(
        "DELETE FROM player_flags WHERE player_id=$1 AND game_id=$2 AND set_at_seq>$3",
        player_id, gid, to_seq)
    await conn.execute(
        "DELETE FROM player_clues WHERE player_id=$1 AND game_id=$2 AND found_at_seq>$3",
        player_id, gid, to_seq)
    await conn.execute(
        "DELETE FROM player_cells WHERE player_id=$1 AND game_id=$2 AND found_at_seq>$3",
        player_id, gid, to_seq)
    await conn.execute(
        "DELETE FROM player_alignment_events WHERE player_id=$1 AND game_id=$2 AND created_seq>$3",
        player_id, gid, to_seq)
    await conn.execute(
        """UPDATE puzzle_progress SET solved=FALSE, solved_at_seq=NULL
           WHERE player_id=$1 AND game_id=$2 AND solved_at_seq>$3""", player_id, gid, to_seq)
    await conn.execute(
        """UPDATE gate_attempts SET satisfied=FALSE, passed_at_seq=NULL,
           criteria_met='[]'::jsonb WHERE player_id=$1 AND game_id=$2 AND passed_at_seq>$3""",
        player_id, gid, to_seq)
    await conn.execute(
        "DELETE FROM gate_messages WHERE player_id=$1 AND game_id=$2 AND seq>$3",
        player_id, gid, to_seq)
    await memory.void_after(conn, player_id, gid, to_seq)

    await conn.execute(
        """UPDATE player_games SET current_node=$1, story_time=$2, last_played_at=now()
           WHERE player_id=$3 AND game_id=$4""",
        target["node_id"], target["story_time"], player_id, gid)
    session = dict(session)
    session["current_node"] = target["node_id"]
    session["story_time"] = target["story_time"]
    return session
