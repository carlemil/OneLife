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
    # D&D-style alignment: one append-only, seq-stamped row per judged action,
    # carrying the delta AND the resulting clamped coordinate (current = latest
    # surviving row; trail = the history). Rollback deletes rows with created_seq > N.
    """CREATE TABLE IF NOT EXISTS player_alignment_events (
        id              BIGSERIAL PRIMARY KEY,
        player_id       UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        created_seq     BIGINT NOT NULL,
        good_evil_delta DOUBLE PRECISION NOT NULL,
        law_chaos_delta DOUBLE PRECISION NOT NULL,
        good_evil       DOUBLE PRECISION NOT NULL,
        law_chaos       DOUBLE PRECISION NOT NULL,
        reason          TEXT NOT NULL DEFAULT '',
        kind            TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE INDEX IF NOT EXISTS player_alignment_events_player_idx
        ON player_alignment_events (player_id, id)""",
    # Static memo of an edge label's alignment shift, so a given authored choice is
    # judged by the LLM once (not on every traversal). Player-independent; never
    # rolled back.
    """CREATE TABLE IF NOT EXISTS edge_alignment_cache (
        edge_id         TEXT PRIMARY KEY,
        good_evil_delta DOUBLE PRECISION NOT NULL,
        law_chaos_delta DOUBLE PRECISION NOT NULL,
        reason          TEXT NOT NULL DEFAULT '',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "DROP TABLE IF EXISTS content_events",
    "DROP TABLE IF EXISTS content_log_state",
]


async def ensure_tables(conn):
    for stmt in _DDL:
        await conn.execute(stmt)


async def current(conn, game_id: str) -> dict:
    """One game's full authored set plus node positions — for the read-only graph view."""
    data = await content.export_content(conn, game_id)
    data["positions"] = {
        r["node_id"]: {"x": r["x"], "y": r["y"]}
        for r in await conn.fetch(
            "SELECT node_id,x,y FROM node_positions WHERE game_id=$1", game_id)
    }
    return data
