"""Auto-repair authored content so a single edit never leaves the world in a
state the spine lint rejects — instead of blocking the save, generate the minimal
nodes/edges needed and let the caller fold them into the same event (so one undo
reverts the edit and its scaffolding together).

Only the fixable cases are handled:
  * no ending node  -> create one
  * trap nodes (reachable, can't reach an ending) -> add an edge to an ending
Cases that can't be synthesized safely (e.g. zero or multiple entry nodes) are
left alone; the caller re-validates and surfaces the error inline.
"""
from . import content


def _free_id(existing: set, base: str) -> str:
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def auto_repair(data: dict):
    """Return (repaired_copy, added) where added = [{kind, entity}] in dependency
    order (any new ending node before edges that point to it)."""
    data = {k: list(v) for k, v in data.items()}
    added = []
    node_ids = {n["id"] for n in data["nodes"]}
    arc_id = data["arcs"][0]["id"] if data["arcs"] else "main"

    endings = [n for n in data["nodes"] if n.get("type") == "ending"]
    if not endings:
        eid = _free_id(node_ids, "auto-ending")
        end = {"id": eid, "arc": arc_id, "type": "ending",
               "title": "The End", "body": "(auto-generated ending)"}
        data["nodes"].append(end)
        node_ids.add(eid)
        added.append({"kind": "nodes", "entity": end})
        endings = [end]
    end_id = endings[0]["id"]

    # Trap edges only make sense once the spine has exactly one entry.
    if sum(1 for n in data["nodes"] if n.get("entry")) != 1:
        return data, added

    edge_ids = {e["id"] for e in data["edges"]}
    for _ in range(2000):  # converges: every new edge points at an ending
        traps = content.find_traps(data)
        if not traps:
            break
        for t in traps:
            eid = _free_id(edge_ids, f"auto-{t}-end")
            edge = {"id": eid, "from": t, "to": end_id, "label": "(auto)",
                    "conditions": {"all": []}, "effects": {}, "danger": 0, "sort_order": 99}
            data["edges"].append(edge)
            edge_ids.add(eid)
            added.append({"kind": "edges", "entity": edge})

    return data, added
