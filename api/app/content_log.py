"""Canonical authoring event-log for the admin content editor.

The authored world is canonical as: **baseline #0 (the YAML seed) + replay of the
event log up to `head`**. The content tables are a materialized cache, kept in sync
on every apply/undo/redo, and rebuildable by `replay()` on boot.

Each event is one committed authoring action (create | update | delete | move)
carrying `before`/`after` images so it can be inverted. `head` is the highest
applied seq; events with seq > head are redoable. A new action after an undo
truncates that redo tail (standard linear undo).

Scope is AUTHORING ONLY — player runtime (log_entries, gate_messages, memories)
is never touched here. A node-delete is a single compound event whose `before`
holds the node plus its incident edges, so one undo restores them together.
"""
import json

from . import content, content_edit

# DDL mirrored from db/01_schema.sql so a non-fresh volume gets the tables too.
_DDL = [
    """CREATE TABLE IF NOT EXISTS content_events (
        seq         BIGSERIAL PRIMARY KEY,
        op          TEXT NOT NULL,
        kind        TEXT NOT NULL,
        entity_id   TEXT NOT NULL,
        before      JSONB,
        after       JSONB,
        admin_email TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS content_log_state (
        id   INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        head BIGINT NOT NULL DEFAULT 0
    )""",
    "INSERT INTO content_log_state (id, head) VALUES (1, 0) ON CONFLICT (id) DO NOTHING",
    """CREATE TABLE IF NOT EXISTS node_positions (
        node_id TEXT PRIMARY KEY,
        x       DOUBLE PRECISION NOT NULL,
        y       DOUBLE PRECISION NOT NULL
    )""",
]


async def ensure_tables(conn):
    for stmt in _DDL:
        await conn.execute(stmt)


# ---------- HEAD pointer ----------
async def _head(conn) -> int:
    h = await conn.fetchval("SELECT head FROM content_log_state WHERE id=1")
    return int(h or 0)


async def _set_head(conn, h: int):
    await conn.execute("UPDATE content_log_state SET head=$1 WHERE id=1", h)


# ---------- reading current state ----------
async def current(conn) -> dict:
    """Full authored set plus the editor's persisted node positions."""
    data = await content.export_content(conn)
    pos = {}
    for r in await conn.fetch("SELECT node_id,x,y FROM node_positions"):
        pos[r["node_id"]] = {"x": r["x"], "y": r["y"]}
    data["positions"] = pos
    return data


async def find_entity(conn, kind: str, entity_id: str):
    """The current authored value of one entity (the before-image), or None."""
    data = await content.export_content(conn)
    for x in data.get(kind, []):
        if x.get("id") == entity_id:
            return x
    return None


async def node_delete_before(conn, node_id: str) -> dict:
    """Compound before-image for deleting a node: the node + its incident edges."""
    data = await content.export_content(conn)
    node = next((n for n in data["nodes"] if n["id"] == node_id), None)
    edges = [e for e in data["edges"] if e["from"] == node_id or e["to"] == node_id]
    return {"node": node, "edges": edges}


# ---------- materialization primitives (operate on the live cache) ----------
async def _upsert_entity(conn, kind: str, entity: dict):
    full = await content.export_content(conn)
    merged = content_edit.upsert_entity(full, kind, entity)
    await content.seed_content(conn, merged)


async def _delete_entity(conn, kind: str, entity_id: str):
    full = await content.export_content(conn)
    remaining = content_edit.remove_entity(full, kind, entity_id)
    await content.seed_content(conn, remaining)
    await content_edit.delete_one(conn, kind, entity_id)


async def _delete_node_compound(conn, node_id: str, edge_ids: list[str]):
    full = await content.export_content(conn)
    remaining = content_edit.remove_entity(full, "nodes", node_id)
    drop = set(edge_ids)
    remaining["edges"] = [e for e in remaining["edges"] if e["id"] not in drop]
    await content.seed_content(conn, remaining)          # re-seed without node/edges
    for eid in edge_ids:                                 # then physically remove them
        await content_edit.delete_one(conn, "edges", eid)
    await content_edit.delete_one(conn, "nodes", node_id)


async def _restore_node_compound(conn, node: dict, edges: list[dict]):
    full = await content.export_content(conn)
    merged = content_edit.upsert_entity(full, "nodes", node)
    for e in edges:
        merged = content_edit.upsert_entity(merged, "edges", e)
    await content.seed_content(conn, merged)


async def _set_pos(conn, node_id: str, xy):
    if xy is None:
        await conn.execute("DELETE FROM node_positions WHERE node_id=$1", node_id)
    else:
        await conn.execute(
            """INSERT INTO node_positions (node_id,x,y) VALUES ($1,$2,$3)
               ON CONFLICT (node_id) DO UPDATE SET x=EXCLUDED.x, y=EXCLUDED.y""",
            node_id, float(xy["x"]), float(xy["y"]))


def _is_node_compound(before) -> bool:
    return isinstance(before, dict) and "node" in before and "edges" in before


# ---------- forward / inverse application of one event ----------
async def apply_forward(conn, ev: dict):
    op, kind, eid = ev["op"], ev["kind"], ev["entity_id"]
    if op in ("create", "update"):
        await _upsert_entity(conn, kind, ev["after"])
    elif op == "delete":
        if kind == "nodes" and _is_node_compound(ev["before"]):
            await _delete_node_compound(conn, eid, [e["id"] for e in ev["before"]["edges"]])
        else:
            await _delete_entity(conn, kind, eid)
    elif op == "move":
        await _set_pos(conn, eid, ev["after"])


async def apply_inverse(conn, ev: dict):
    op, kind, eid = ev["op"], ev["kind"], ev["entity_id"]
    if op == "create":
        await _delete_entity(conn, kind, eid)            # LIFO: no incident deps left
    elif op == "update":
        await _upsert_entity(conn, kind, ev["before"])
    elif op == "delete":
        if kind == "nodes" and _is_node_compound(ev["before"]):
            await _restore_node_compound(conn, ev["before"]["node"], ev["before"]["edges"])
        else:
            await _upsert_entity(conn, kind, ev["before"])
    elif op == "move":
        await _set_pos(conn, eid, ev["before"])


# ---------- the log ----------
async def _event(conn, seq: int):
    r = await conn.fetchrow(
        "SELECT seq,op,kind,entity_id,before,after FROM content_events WHERE seq=$1", seq)
    if not r:
        return None
    return {
        "seq": r["seq"], "op": r["op"], "kind": r["kind"], "entity_id": r["entity_id"],
        "before": json.loads(r["before"]) if r["before"] else None,
        "after": json.loads(r["after"]) if r["after"] else None,
    }


async def record(conn, op: str, kind: str, entity_id: str, before, after, admin_email=None) -> int:
    """Append one event at head+1 (truncating any redo tail), materialize it, bump head."""
    async with conn.transaction():
        head = await _head(conn)
        await conn.execute("DELETE FROM content_events WHERE seq > $1", head)
        seq = head + 1
        await conn.execute(
            """INSERT INTO content_events (seq,op,kind,entity_id,before,after,admin_email)
               VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7)""",
            seq, op, kind, entity_id,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
            admin_email)
        await apply_forward(conn, {"op": op, "kind": kind, "entity_id": entity_id,
                                   "before": before, "after": after})
        await _set_head(conn, seq)
    return seq


async def undo(conn) -> dict:
    async with conn.transaction():
        head = await _head(conn)
        if head <= 0:
            return {"ok": False, "head": 0, "reason": "already at baseline"}
        ev = await _event(conn, head)
        await apply_inverse(conn, ev)
        await _set_head(conn, head - 1)
    return {"ok": True, "head": head - 1}


async def redo(conn) -> dict:
    async with conn.transaction():
        head = await _head(conn)
        nxt = await _event(conn, head + 1)
        if not nxt:
            return {"ok": False, "head": head, "reason": "nothing to redo"}
        await apply_forward(conn, nxt)
        await _set_head(conn, head + 1)
    return {"ok": True, "head": head + 1}


async def goto(conn, seq: int) -> dict:
    """Move HEAD to `seq` (>=0) by redoing forward or undoing back, one step at a time."""
    seq = max(0, seq)
    while await _head(conn) < seq:
        if not (await redo(conn))["ok"]:
            break
    while await _head(conn) > seq:
        if not (await undo(conn))["ok"]:
            break
    return {"ok": True, "head": await _head(conn)}


async def log(conn) -> dict:
    head = await _head(conn)
    rows = await conn.fetch(
        "SELECT seq,op,kind,entity_id,created_at FROM content_events ORDER BY seq")
    return {"head": head, "events": [
        {"seq": r["seq"], "op": r["op"], "kind": r["kind"], "entity_id": r["entity_id"],
         "applied": r["seq"] <= head, "created_at": r["created_at"].isoformat()}
        for r in rows]}


async def replay(conn):
    """Boot: apply events 1..head over the freshly-seeded baseline to reach HEAD."""
    head = await _head(conn)
    for seq in range(1, head + 1):
        ev = await _event(conn, seq)
        if ev:
            await apply_forward(conn, ev)
