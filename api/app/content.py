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

import yaml

DEFAULT_DIR = os.environ.get("CONTENT_DIR", "/content")
_LIST_KEYS = ["arcs", "cells", "characters", "locations", "nodes", "gates", "puzzles", "clues", "edges"]
_ID_KEYS = ["nodes", "gates", "puzzles", "clues", "characters", "locations", "arcs", "cells"]


def load_dir(path: str = DEFAULT_DIR):
    """Merge every *.yaml / *.yml file in `path` into one content dict."""
    data = {k: [] for k in _LIST_KEYS}
    files = sorted(glob.glob(os.path.join(path, "*.yaml")) + glob.glob(os.path.join(path, "*.yml")))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for k in _LIST_KEYS:
            data[k].extend(doc.get(k, []) or [])
    return data, files


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


async def seed_content(conn, data: dict):
    """Upsert authored content. Runtime tables (players, memories) are untouched."""
    async with conn.transaction():
        for a in data["arcs"]:
            await conn.execute(
                """INSERT INTO story_arcs (id,title,is_spine) VALUES ($1,$2,$3)
                   ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, is_spine=EXCLUDED.is_spine""",
                a["id"], a["title"], bool(a.get("is_spine", False)))
        for c in data["cells"]:
            await conn.execute(
                """INSERT INTO world_cells (id,grid_x,grid_y,name,kind,region,arrival_node)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)
                   ON CONFLICT (id) DO UPDATE SET grid_x=EXCLUDED.grid_x,grid_y=EXCLUDED.grid_y,
                     name=EXCLUDED.name,kind=EXCLUDED.kind,region=EXCLUDED.region,
                     arrival_node=EXCLUDED.arrival_node""",
                c["id"], int(c.get("grid_x", 0)), int(c.get("grid_y", 0)), c["name"],
                c.get("kind", "town"), c.get("region", ""), c.get("arrival_node"))
        for l in data["locations"]:
            await conn.execute(
                """INSERT INTO locations (id,name,description,cell_id) VALUES ($1,$2,$3,$4)
                   ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,
                     description=EXCLUDED.description, cell_id=EXCLUDED.cell_id""",
                l["id"], l["name"], l.get("description", ""), l.get("cell"))
        for c in data["characters"]:
            await conn.execute(
                """INSERT INTO characters (id,name,persona,reveal_name) VALUES ($1,$2,$3,$4)
                   ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,
                     persona=EXCLUDED.persona, reveal_name=EXCLUDED.reveal_name""",
                c["id"], c["name"], c["persona"], c.get("reveal_name"))
        for p in data["puzzles"]:
            await conn.execute(
                """INSERT INTO puzzles (id,type,prompt,solution,required_clues,hint_ladder,on_solve)
                   VALUES ($1,$2,$3,$4::jsonb,$5,$6::jsonb,$7::jsonb)
                   ON CONFLICT (id) DO UPDATE SET type=EXCLUDED.type,prompt=EXCLUDED.prompt,
                     solution=EXCLUDED.solution,required_clues=EXCLUDED.required_clues,
                     hint_ladder=EXCLUDED.hint_ladder,on_solve=EXCLUDED.on_solve""",
                p["id"], p["type"], p.get("prompt", ""), json.dumps(p.get("solution", {})),
                list(p.get("required_clues", []) or []), json.dumps(p.get("hint_ladder", [])),
                json.dumps(p.get("on_solve", {})))
        for n in data["nodes"]:
            await conn.execute(
                """INSERT INTO story_nodes (id,arc_id,type,location_id,title,body,is_entry,is_death,world_access,gate_id,puzzle_id,media)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                   ON CONFLICT (id) DO UPDATE SET arc_id=EXCLUDED.arc_id,type=EXCLUDED.type,
                     location_id=EXCLUDED.location_id,title=EXCLUDED.title,body=EXCLUDED.body,
                     is_entry=EXCLUDED.is_entry,is_death=EXCLUDED.is_death,world_access=EXCLUDED.world_access,
                     gate_id=EXCLUDED.gate_id,puzzle_id=EXCLUDED.puzzle_id,media=EXCLUDED.media""",
                n["id"], n.get("arc", "main"), n["type"], n.get("location"), n.get("title", ""),
                n.get("body", ""), bool(n.get("entry", False)),
                bool(n.get("is_death", n.get("type") == "death")),
                bool(n.get("world_access", False)),
                n.get("gate"), n.get("puzzle"), json.dumps(n.get("media", {})))
        for g in data["gates"]:
            spec = {k: v for k, v in g.items() if k not in ("id", "location", "character")}
            await conn.execute(
                """INSERT INTO dialogue_gates (id,location_id,character_id,spec)
                   VALUES ($1,$2,$3,$4::jsonb)
                   ON CONFLICT (id) DO UPDATE SET location_id=EXCLUDED.location_id,
                     character_id=EXCLUDED.character_id, spec=EXCLUDED.spec""",
                g["id"], g.get("location"), g.get("character"), json.dumps(spec))

        # Edges: rewrite per source node so removed edges are pruned.
        edges = all_edges(data)
        for fn in {e["from"] for e in edges if e.get("from")}:
            await conn.execute("DELETE FROM story_edges WHERE from_node=$1", fn)
        for e in edges:
            await conn.execute(
                """INSERT INTO story_edges (id,from_node,to_node,label,conditions,effects,danger,sort_order)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8)
                   ON CONFLICT (id) DO UPDATE SET from_node=EXCLUDED.from_node,to_node=EXCLUDED.to_node,
                     label=EXCLUDED.label,conditions=EXCLUDED.conditions,effects=EXCLUDED.effects,
                     danger=EXCLUDED.danger,sort_order=EXCLUDED.sort_order""",
                e["id"], e["from"], e["to"], e.get("label", ""),
                json.dumps(e.get("conditions", {"all": []})), json.dumps(e.get("effects", {})),
                int(e.get("danger", 0)), int(e.get("sort_order", 0)))

        for c in data["clues"]:
            await conn.execute(
                """INSERT INTO puzzle_clues (id,puzzle_id,placement,reveal_text,discover_conditions)
                   VALUES ($1,$2,$3::jsonb,$4,$5::jsonb)
                   ON CONFLICT (id) DO UPDATE SET puzzle_id=EXCLUDED.puzzle_id,placement=EXCLUDED.placement,
                     reveal_text=EXCLUDED.reveal_text,discover_conditions=EXCLUDED.discover_conditions""",
                c["id"], c.get("puzzle"), json.dumps(c.get("placement", {})),
                c.get("reveal_text", ""), json.dumps(c.get("discover_conditions", {"all": []})))
