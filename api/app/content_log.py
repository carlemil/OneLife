"""Node positions + a read-only snapshot of the authored world.

The in-app content editor and its event log were retired: the YAML content files
(in the data repo) are the single source of truth, and git is the history. What
remains here is the `node_positions` cache (graph-editor X/Y, authored in YAML as
`pos:` and re-seeded from it) and `current()`, which the read-only admin graph view
loads once. The obsolete `content_events` / `content_log_state` tables are dropped
on startup.
"""
from . import content

# DDL for a non-fresh volume; also retire the old event-log tables if present.
_DDL = [
    """CREATE TABLE IF NOT EXISTS node_positions (
        node_id TEXT PRIMARY KEY,
        x       DOUBLE PRECISION NOT NULL,
        y       DOUBLE PRECISION NOT NULL
    )""",
    # Road spline geometry lives on each edge (story_edges.road); add it to
    # non-fresh volumes that predate the column.
    "ALTER TABLE story_edges ADD COLUMN IF NOT EXISTS road JSONB NOT NULL DEFAULT '[]'",
    "DROP TABLE IF EXISTS content_events",
    "DROP TABLE IF EXISTS content_log_state",
]


async def ensure_tables(conn):
    for stmt in _DDL:
        await conn.execute(stmt)


async def current(conn) -> dict:
    """The full authored set plus node positions — for the read-only graph view."""
    data = await content.export_content(conn)
    data["positions"] = {
        r["node_id"]: {"x": r["x"], "y": r["y"]}
        for r in await conn.fetch("SELECT node_id,x,y FROM node_positions")
    }
    return data
