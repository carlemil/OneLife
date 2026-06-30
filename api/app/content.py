"""Authoring pipeline: load YAML content, validate it (including the spine
reachability lint), and seed it into Postgres. See AUTHORING.md.

The lint guarantees a player can never get *truly* stuck: from the single entry
node an ending is always reachable, and no reachable non-death node is a trap
(unable to reach any ending). Reachability is optimistic — it ignores edge
conditions, assuming a player can eventually satisfy the flags by playing.
"""
import os
import glob
import json
from collections import defaultdict, deque

import asyncpg
import yaml

from . import gamestate

DEFAULT_DIR = os.environ.get("CONTENT_DIR", "/content")
_LIST_KEYS = ["arcs", "cells", "characters", "locations", "nodes", "gates", "puzzles", "clues", "edges"]
_ID_KEYS = ["nodes", "gates", "puzzles", "clues", "characters", "locations", "arcs", "cells"]


def load_dir(path: str | None = None):
    """Merge every *.yaml / *.yml file in `path` into one content dict. With no
    path, loads the currently-active game's data dir (see gamestate)."""
    if path is None:
        path = gamestate.active_dir()
    data = {k: [] for k in _LIST_KEYS}
    data["game"] = {}   # optional top-level metadata: title, subtitle, first_summary
    files = sorted(glob.glob(os.path.join(path, "*.yaml")) + glob.glob(os.path.join(path, "*.yml")))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for k in _LIST_KEYS:
            data[k].extend(doc.get(k, []) or [])
        if isinstance(doc.get("game"), dict):
            data["game"].update(doc["game"])
    return data, files


async def register_game(conn, game_id: str, meta: dict, sort_order: int = 0) -> None:
    """Upsert the games registry row for `game_id`. Title/subtitle/first_summary come
    from the YAML `game:` block when present; on conflict only non-empty incoming
    values overwrite (so a value set elsewhere — e.g. the migration's preserved first
    scene line — survives a metadata-less dataset)."""
    title = (meta.get("title") or "").strip()
    subtitle = (meta.get("subtitle") or "").strip()
    first_summary = (meta.get("first_summary") or "").strip()
    await conn.execute(
        """INSERT INTO games (id, title, subtitle, first_summary, sort_order)
           VALUES ($1, $2, $3, COALESCE(NULLIF($4,''), 'You wake.'), $5)
           ON CONFLICT (id) DO UPDATE SET
             title = COALESCE(NULLIF(EXCLUDED.title,''), games.title),
             subtitle = COALESCE(NULLIF(EXCLUDED.subtitle,''), games.subtitle),
             first_summary = CASE WHEN NULLIF($4,'') IS NOT NULL
                                  THEN EXCLUDED.first_summary ELSE games.first_summary END,
             sort_order = EXCLUDED.sort_order""",
        game_id, title or game_id, subtitle, first_summary, sort_order)


def all_edges(data: dict) -> list[dict]:
    """Embedded node edges + standalone edges, normalized with `from`/`sort_order`."""
    edges = []
    for n in data["nodes"]:
        for i, e in enumerate(n.get("edges", []) or []):
            e = dict(e)
            e.setdefault("from", n["id"])
            e.setdefault("sort_order", i)
            edges.append(e)
    for e in data["edges"]:
        e = dict(e)
        e.setdefault("sort_order", 0)
        edges.append(e)
    return edges


def validate(data: dict):
    """Return (errors, warnings). Errors block seeding; warnings don't."""
    errors, warnings = [], []
    ids = {k: {x["id"] for x in data[k]} for k in _ID_KEYS}

    for k in _ID_KEYS:
        seen = set()
        for x in data[k]:
            if x["id"] in seen:
                errors.append(f"duplicate {k} id: {x['id']}")
            seen.add(x["id"])

    nodes = {n["id"]: n for n in data["nodes"]}
    gates = {g["id"]: g for g in data["gates"]}
    puzzles = {p["id"]: p for p in data["puzzles"]}

    entries = [n["id"] for n in data["nodes"] if n.get("entry")]
    if len(entries) != 1:
        errors.append(f"expected exactly one entry node, found {len(entries)}: {entries}")
    endings = [n["id"] for n in data["nodes"] if n.get("type") == "ending"]
    if not endings:
        errors.append("no ending node defined — the spine can never resolve")

    for n in data["nodes"]:
        if n.get("location") and n["location"] not in ids["locations"]:
            errors.append(f"node {n['id']} references unknown location {n['location']}")
        if n.get("arc") and n["arc"] not in ids["arcs"]:
            errors.append(f"node {n['id']} references unknown arc {n['arc']}")
        if n.get("type") == "gate":
            if not n.get("gate") or n["gate"] not in gates:
                errors.append(f"gate node {n['id']} references unknown gate {n.get('gate')}")
            elif "mercy_after_attempts" not in gates[n["gate"]]:
                warnings.append(f"gate {n['gate']} has no mercy_after_attempts (soft-lock risk)")
        if n.get("type") == "puzzle":
            if not n.get("puzzle") or n["puzzle"] not in puzzles:
                errors.append(f"puzzle node {n['id']} references unknown puzzle {n.get('puzzle')}")
            elif not puzzles[n["puzzle"]].get("hint_ladder"):
                warnings.append(f"puzzle {n['puzzle']} has no hint_ladder (difficulty risk)")

    for l in data["locations"]:
        if l.get("cell") and l["cell"] not in ids["cells"]:
            errors.append(f"location {l['id']} references unknown cell {l['cell']}")
    for c in data["cells"]:
        if c.get("arrival_node") and c["arrival_node"] not in nodes:
            errors.append(f"cell {c['id']} arrival_node references unknown node {c['arrival_node']}")
    for g in data["gates"]:
        if g.get("character") and g["character"] not in ids["characters"]:
            errors.append(f"gate {g['id']} references unknown character {g['character']}")
        if g.get("location") and g["location"] not in ids["locations"]:
            errors.append(f"gate {g['id']} references unknown location {g['location']}")
    for p in data["puzzles"]:
        for c in p.get("required_clues", []) or []:
            if c not in ids["clues"]:
                warnings.append(f"puzzle {p['id']} requires unknown clue {c}")
    for c in data["clues"]:
        if c.get("puzzle") and c["puzzle"] not in ids["puzzles"]:
            errors.append(f"clue {c['id']} references unknown puzzle {c['puzzle']}")

    edges = all_edges(data)
    for e in edges:
        if e.get("from") not in nodes:
            errors.append(f"edge {e.get('id','?')} from unknown node {e.get('from')}")
        if e.get("to") not in nodes:
            errors.append(f"edge {e.get('id','?')} to unknown node {e.get('to')}")

    # Spine reachability — only meaningful with one entry and at least one ending.
    if len(entries) == 1 and endings:
        adj, radj = defaultdict(list), defaultdict(list)
        for e in edges:
            if e.get("from") in nodes and e.get("to") in nodes:
                adj[e["from"]].append(e["to"])
                radj[e["to"]].append(e["from"])
        # Travel: a world-access node can reach every cell's arrival node, and
        # from any arrival node you can travel onward — model that so the lint
        # sees cross-cell content as reachable and trap-free.
        arrivals = [c["arrival_node"] for c in data["cells"]
                    if c.get("arrival_node") in nodes]
        for wa in [n["id"] for n in data["nodes"] if n.get("world_access")]:
            for an in arrivals:
                adj[wa].append(an)
                radj[an].append(wa)

        def bfs(starts, graph):
            seen, dq = set(), deque(starts)
            while dq:
                x = dq.popleft()
                if x in seen:
                    continue
                seen.add(x)
                dq.extend(graph[x])
            return seen

        reachable = bfs([entries[0]], adj)
        can_end = bfs(endings, radj)
        for nid, n in nodes.items():
            if nid in reachable and nid not in can_end \
                    and not n.get("is_death") and n.get("type") not in ("ending", "death"):
                errors.append(f"TRAP: node {nid} is reachable but cannot reach any ending")
            if nid not in reachable:
                warnings.append(f"unreachable node (dead content): {nid}")

    return errors, warnings


def find_traps(data: dict) -> list[str]:
    """Reachable non-death nodes that cannot reach any ending — mirrors the TRAP
    rule in validate()'s spine lint (travel-aware, optimistic). Empty unless there
    is exactly one entry node and at least one ending."""
    nodes = {n["id"]: n for n in data["nodes"]}
    entries = [n["id"] for n in data["nodes"] if n.get("entry")]
    endings = [n["id"] for n in data["nodes"] if n.get("type") == "ending"]
    if len(entries) != 1 or not endings:
        return []
    edges = all_edges(data)
    adj, radj = defaultdict(list), defaultdict(list)
    for e in edges:
        if e.get("from") in nodes and e.get("to") in nodes:
            adj[e["from"]].append(e["to"])
            radj[e["to"]].append(e["from"])
    arrivals = [c["arrival_node"] for c in data["cells"] if c.get("arrival_node") in nodes]
    for wa in [n["id"] for n in data["nodes"] if n.get("world_access")]:
        for an in arrivals:
            adj[wa].append(an)
            radj[an].append(wa)

    def bfs(starts, graph):
        seen, dq = set(), deque(starts)
        while dq:
            x = dq.popleft()
            if x in seen:
                continue
            seen.add(x)
            dq.extend(graph[x])
        return seen

    reachable = bfs([entries[0]], adj)
    can_end = bfs(endings, radj)
    return [nid for nid, n in nodes.items()
            if nid in reachable and nid not in can_end
            and not n.get("is_death") and n.get("type") not in ("ending", "death")]


async def seed_content(conn, data: dict, game_id: str) -> list[str]:
    """Reconcile one game's authored content into Postgres: upsert everything in the
    YAML under `game_id`, then prune that game's authored rows no longer present (YAML
    is the single source of truth). Other games' content is never touched. Runtime
    tables (players, sessions, memories, image cache) are never touched. Returns a list
    of warnings for rows that couldn't be pruned because live player data still
    references them (those are kept). The `games` row for `game_id` must already exist."""
    async with conn.transaction():
        for a in data["arcs"]:
            await conn.execute(
                """INSERT INTO story_arcs (game_id,id,title,is_spine) VALUES ($1,$2,$3,$4)
                   ON CONFLICT (game_id,id) DO UPDATE SET title=EXCLUDED.title, is_spine=EXCLUDED.is_spine""",
                game_id, a["id"], a["title"], bool(a.get("is_spine", False)))
        for c in data["cells"]:
            await conn.execute(
                """INSERT INTO world_cells (game_id,id,grid_x,grid_y,name,kind,region,arrival_node,map,map_image,world_exit)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11::jsonb)
                   ON CONFLICT (game_id,id) DO UPDATE SET grid_x=EXCLUDED.grid_x,grid_y=EXCLUDED.grid_y,
                     name=EXCLUDED.name,kind=EXCLUDED.kind,region=EXCLUDED.region,
                     arrival_node=EXCLUDED.arrival_node,map=EXCLUDED.map,
                     map_image=EXCLUDED.map_image,world_exit=EXCLUDED.world_exit""",
                game_id, c["id"], int(c.get("grid_x", 0)), int(c.get("grid_y", 0)), c["name"],
                c.get("kind", "town"), c.get("region", ""), c.get("arrival_node"),
                json.dumps(c.get("map", {})), c.get("map_image"),
                json.dumps(c.get("world_exit", {})))
        for l in data["locations"]:
            await conn.execute(
                """INSERT INTO locations (game_id,id,name,description,cell_id) VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (game_id,id) DO UPDATE SET name=EXCLUDED.name,
                     description=EXCLUDED.description, cell_id=EXCLUDED.cell_id""",
                game_id, l["id"], l["name"], l.get("description", ""), l.get("cell"))
        for c in data["characters"]:
            await conn.execute(
                """INSERT INTO characters (game_id,id,name,persona,reveal_name) VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (game_id,id) DO UPDATE SET name=EXCLUDED.name,
                     persona=EXCLUDED.persona, reveal_name=EXCLUDED.reveal_name""",
                game_id, c["id"], c["name"], c["persona"], c.get("reveal_name"))
        for p in data["puzzles"]:
            await conn.execute(
                """INSERT INTO puzzles (game_id,id,type,prompt,solution,required_clues,hint_ladder,on_solve)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7::jsonb,$8::jsonb)
                   ON CONFLICT (game_id,id) DO UPDATE SET type=EXCLUDED.type,prompt=EXCLUDED.prompt,
                     solution=EXCLUDED.solution,required_clues=EXCLUDED.required_clues,
                     hint_ladder=EXCLUDED.hint_ladder,on_solve=EXCLUDED.on_solve""",
                game_id, p["id"], p["type"], p.get("prompt", ""), json.dumps(p.get("solution", {})),
                list(p.get("required_clues", []) or []), json.dumps(p.get("hint_ladder", [])),
                json.dumps(p.get("on_solve", {})))
        for n in data["nodes"]:
            await conn.execute(
                """INSERT INTO story_nodes (game_id,id,arc_id,type,location_id,title,body,body_variants,is_entry,is_death,world_access,gate_id,puzzle_id,media,map)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb)
                   ON CONFLICT (game_id,id) DO UPDATE SET arc_id=EXCLUDED.arc_id,type=EXCLUDED.type,
                     location_id=EXCLUDED.location_id,title=EXCLUDED.title,body=EXCLUDED.body,
                     body_variants=EXCLUDED.body_variants,
                     is_entry=EXCLUDED.is_entry,is_death=EXCLUDED.is_death,world_access=EXCLUDED.world_access,
                     gate_id=EXCLUDED.gate_id,puzzle_id=EXCLUDED.puzzle_id,media=EXCLUDED.media,map=EXCLUDED.map""",
                game_id, n["id"], n.get("arc", "main"), n["type"], n.get("location"), n.get("title", ""),
                n.get("body", ""), json.dumps(n.get("body_variants", []) or []),
                bool(n.get("entry", False)),
                bool(n.get("is_death", n.get("type") == "death")),
                bool(n.get("world_access", False)),
                n.get("gate"), n.get("puzzle"), json.dumps(n.get("media", {})),
                json.dumps(n.get("map", {})))
            pos = n.get("pos") or {}
            if pos.get("x") is not None and pos.get("y") is not None:
                await conn.execute(
                    """INSERT INTO node_positions (game_id,node_id,x,y) VALUES ($1,$2,$3,$4)
                       ON CONFLICT (game_id,node_id) DO UPDATE SET x=EXCLUDED.x, y=EXCLUDED.y""",
                    game_id, n["id"], float(pos["x"]), float(pos["y"]))
        for g in data["gates"]:
            spec = {k: v for k, v in g.items() if k not in ("id", "location", "character")}
            await conn.execute(
                """INSERT INTO dialogue_gates (game_id,id,location_id,character_id,spec)
                   VALUES ($1,$2,$3,$4,$5::jsonb)
                   ON CONFLICT (game_id,id) DO UPDATE SET location_id=EXCLUDED.location_id,
                     character_id=EXCLUDED.character_id, spec=EXCLUDED.spec""",
                game_id, g["id"], g.get("location"), g.get("character"), json.dumps(spec))

        # Edges: rewrite per source node so removed edges are pruned (scoped to this game).
        edges = all_edges(data)
        for fn in {e["from"] for e in edges if e.get("from")}:
            await conn.execute(
                "DELETE FROM story_edges WHERE game_id=$1 AND from_node=$2", game_id, fn)
        for e in edges:
            await conn.execute(
                """INSERT INTO story_edges (game_id,id,from_node,to_node,label,conditions,effects,danger,sort_order,road)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,$10::jsonb)
                   ON CONFLICT (game_id,id) DO UPDATE SET from_node=EXCLUDED.from_node,to_node=EXCLUDED.to_node,
                     label=EXCLUDED.label,conditions=EXCLUDED.conditions,effects=EXCLUDED.effects,
                     danger=EXCLUDED.danger,sort_order=EXCLUDED.sort_order,road=EXCLUDED.road""",
                game_id, e["id"], e["from"], e["to"], e.get("label", ""),
                json.dumps(e.get("conditions", {"all": []})), json.dumps(e.get("effects", {})),
                int(e.get("danger", 0)), int(e.get("sort_order", 0)),
                json.dumps(e.get("road", []) or []))

        for c in data["clues"]:
            await conn.execute(
                """INSERT INTO puzzle_clues (game_id,id,puzzle_id,placement,reveal_text,discover_conditions)
                   VALUES ($1,$2,$3,$4::jsonb,$5,$6::jsonb)
                   ON CONFLICT (game_id,id) DO UPDATE SET puzzle_id=EXCLUDED.puzzle_id,placement=EXCLUDED.placement,
                     reveal_text=EXCLUDED.reveal_text,discover_conditions=EXCLUDED.discover_conditions""",
                game_id, c["id"], c.get("puzzle"), json.dumps(c.get("placement", {})),
                c.get("reveal_text", ""), json.dumps(c.get("discover_conditions", {"all": []})))

        # ---- Authoritative reconcile: drop THIS GAME's authored rows no longer in the
        # YAML, in reverse-dependency order. Each delete rides its own savepoint, so a
        # row a live player still references (FK violation) is KEPT with a warning
        # instead of aborting the whole seed. Player-state tables are never in this list.
        keep = {
            "story_edges": {e["id"] for e in edges},
            "story_nodes": {n["id"] for n in data["nodes"]},
            "dialogue_gates": {g["id"] for g in data["gates"]},
            "puzzle_clues": {c["id"] for c in data["clues"]},
            "puzzles": {p["id"] for p in data["puzzles"]},
            "locations": {l["id"] for l in data["locations"]},
            "world_cells": {c["id"] for c in data["cells"]},
            "characters": {c["id"] for c in data["characters"]},
            "story_arcs": {a["id"] for a in data["arcs"]},
        }
        skipped: list[str] = []
        for table in ("story_edges", "story_nodes", "dialogue_gates", "puzzle_clues",
                      "puzzles", "locations", "world_cells", "characters", "story_arcs"):
            stale = await conn.fetch(
                f"SELECT id FROM {table} WHERE game_id=$1 AND NOT (id = ANY($2::text[]))",
                game_id, list(keep[table]))
            for r in stale:
                try:
                    async with conn.transaction():      # savepoint
                        await conn.execute(
                            f"DELETE FROM {table} WHERE game_id=$1 AND id=$2", game_id, r["id"])
                except asyncpg.exceptions.ForeignKeyViolationError:
                    skipped.append(f"kept {table} {r['id']} — referenced by live player data")
        # node_positions has no content FK; prune this game's orphans of vanished nodes.
        await conn.execute(
            "DELETE FROM node_positions WHERE game_id=$1 AND NOT (node_id = ANY($2::text[]))",
            game_id, list(keep["story_nodes"]))
    return skipped


def _j(v, default):
    """Parse a JSONB column (asyncpg returns it as a str) with a fallback."""
    if v is None:
        return default
    return json.loads(v) if isinstance(v, str) else v


async def export_content(conn, game_id: str) -> dict:
    """Inverse of seed_content: read one game's authored tables into the same dict
    shape that load_dir()/validate()/seed_content() consume (see AUTHORING.md). Edges
    are emitted as a top-level `edges` list (not embedded in nodes)."""
    data = {k: [] for k in _LIST_KEYS}
    positions = {r["node_id"]: r for r in
                 await conn.fetch("SELECT node_id,x,y FROM node_positions WHERE game_id=$1", game_id)}

    for a in await conn.fetch(
            "SELECT id,title,is_spine FROM story_arcs WHERE game_id=$1 ORDER BY id", game_id):
        data["arcs"].append({"id": a["id"], "title": a["title"], "is_spine": a["is_spine"]})

    for c in await conn.fetch(
            "SELECT id,grid_x,grid_y,name,kind,region,arrival_node,map,map_image,world_exit "
            "FROM world_cells WHERE game_id=$1 ORDER BY id", game_id):
        row = {"id": c["id"], "grid_x": c["grid_x"], "grid_y": c["grid_y"],
               "name": c["name"], "kind": c["kind"], "region": c["region"],
               "arrival_node": c["arrival_node"]}
        cmap = _j(c["map"], {})
        if cmap:
            row["map"] = cmap
        if c["map_image"]:
            row["map_image"] = c["map_image"]
        wexit = _j(c["world_exit"], {})
        if wexit:
            row["world_exit"] = wexit
        data["cells"].append(row)

    for c in await conn.fetch(
            "SELECT id,name,persona,reveal_name FROM characters WHERE game_id=$1 ORDER BY id", game_id):
        row = {"id": c["id"], "name": c["name"], "persona": c["persona"]}
        if c["reveal_name"]:
            row["reveal_name"] = c["reveal_name"]
        data["characters"].append(row)

    for l in await conn.fetch(
            "SELECT id,name,description,cell_id FROM locations WHERE game_id=$1 ORDER BY id", game_id):
        row = {"id": l["id"], "name": l["name"], "description": l["description"]}
        if l["cell_id"]:
            row["cell"] = l["cell_id"]
        data["locations"].append(row)

    for p in await conn.fetch(
            "SELECT id,type,prompt,solution,required_clues,hint_ladder,on_solve "
            "FROM puzzles WHERE game_id=$1 ORDER BY id", game_id):
        data["puzzles"].append({
            "id": p["id"], "type": p["type"], "prompt": p["prompt"],
            "solution": _j(p["solution"], {}),
            "required_clues": list(p["required_clues"] or []),
            "hint_ladder": _j(p["hint_ladder"], []),
            "on_solve": _j(p["on_solve"], {}),
        })

    for c in await conn.fetch(
            "SELECT id,puzzle_id,placement,reveal_text,discover_conditions "
            "FROM puzzle_clues WHERE game_id=$1 ORDER BY id", game_id):
        data["clues"].append({
            "id": c["id"], "puzzle": c["puzzle_id"],
            "placement": _j(c["placement"], {}), "reveal_text": c["reveal_text"],
            "discover_conditions": _j(c["discover_conditions"], {"all": []}),
        })

    for n in await conn.fetch("SELECT * FROM story_nodes WHERE game_id=$1 ORDER BY id", game_id):
        row = {"id": n["id"], "arc": n["arc_id"], "type": n["type"],
               "title": n["title"], "body": n["body"]}
        variants = _j(n["body_variants"], [])
        if variants:
            row["body_variants"] = variants
        if n["location_id"]:
            row["location"] = n["location_id"]
        if n["is_entry"]:
            row["entry"] = True
        if n["is_death"]:
            row["is_death"] = True
        if n["world_access"]:
            row["world_access"] = True
        if n["gate_id"]:
            row["gate"] = n["gate_id"]
        if n["puzzle_id"]:
            row["puzzle"] = n["puzzle_id"]
        media = _j(n["media"], {})
        if media:
            row["media"] = media
        nmap = _j(n["map"], {})
        if nmap:
            row["map"] = nmap
        p = positions.get(n["id"])
        if p is not None:
            row["pos"] = {"x": round(p["x"]), "y": round(p["y"])}
        data["nodes"].append(row)

    for g in await conn.fetch(
            "SELECT id,location_id,character_id,spec FROM dialogue_gates WHERE game_id=$1 ORDER BY id", game_id):
        row = {"id": g["id"]}
        if g["location_id"]:
            row["location"] = g["location_id"]
        if g["character_id"]:
            row["character"] = g["character_id"]
        row.update(_j(g["spec"], {}))   # criteria, knowledge_boundary, hint_ladder, on_success, …
        data["gates"].append(row)

    for e in await conn.fetch(
            "SELECT * FROM story_edges WHERE game_id=$1 ORDER BY from_node, sort_order, id", game_id):
        row = {
            "id": e["id"], "from": e["from_node"], "to": e["to_node"], "label": e["label"],
            "conditions": _j(e["conditions"], {"all": []}), "effects": _j(e["effects"], {}),
            "danger": e["danger"], "sort_order": e["sort_order"],
        }
        road = _j(e["road"], [])
        if road:
            row["road"] = road
        data["edges"].append(row)

    return data
