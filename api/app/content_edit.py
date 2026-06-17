"""In-UI content editing: upsert/delete a single authored entity through the same
validate() + seed_content() pipeline the YAML import uses, plus a coupling check
that guards deletes against dangling content references and live player state.

content.py stays pure (no live-player knowledge) — that lives here.
"""
from .content import _LIST_KEYS  # noqa: F401  (kept for callers that introspect)

# Each editable kind -> its physical table (for the explicit DELETE on remove;
# seed_content() only upserts, so it never deletes a removed row except edges).
EDIT_KINDS = {
    "arcs": "story_arcs",
    "cells": "world_cells",
    "characters": "characters",
    "locations": "locations",
    "nodes": "story_nodes",
    "gates": "dialogue_gates",
    "puzzles": "puzzles",
    "clues": "puzzle_clues",
    "edges": "story_edges",
}


def upsert_entity(full: dict, kind: str, entity: dict) -> dict:
    """Return a copy of `full` with `entity` inserted/replaced in full[kind] by id.

    `entity` is in the same authored shape export_content() produces (edges carry
    top-level from/to; gates carry the flat spec-spread) — exactly what validate()
    and seed_content() consume, so no per-kind translation is needed.
    """
    if kind not in EDIT_KINDS:
        raise ValueError(f"unknown content kind: {kind}")
    eid = entity.get("id")
    if not isinstance(eid, str) or not eid.strip():
        raise ValueError("entity needs a non-empty string id")
    out = {k: list(v) for k, v in full.items()}
    lst = out.setdefault(kind, [])
    for i, x in enumerate(lst):
        if x.get("id") == eid:
            lst[i] = entity
            break
    else:
        lst.append(entity)
    return out


def remove_entity(full: dict, kind: str, id_: str) -> dict:
    """Return a copy of `full` with the entity `id_` removed from full[kind]."""
    if kind not in EDIT_KINDS:
        raise ValueError(f"unknown content kind: {kind}")
    out = {k: list(v) for k, v in full.items()}
    out[kind] = [x for x in out.get(kind, []) if x.get("id") != id_]
    return out


# Coupling rules per kind. Each entry: (label, sql, bucket) where bucket is one of
# "content" (other authored rows would dangle -> always blocks; force can't help
# since the remaining set would fail validate anyway) or "live" (live-player rows
# with no cascade FK -> blocks a hard delete unless force) or "cascade" (Postgres
# auto-deletes/nulls these; informational only). $1 is the entity id.
_COUPLING = {
    "arcs": [
        ("story nodes in this arc", "SELECT count(*) FROM story_nodes WHERE arc_id=$1", "content"),
    ],
    "cells": [
        ("locations in this cell", "SELECT count(*) FROM locations WHERE cell_id=$1", "content"),
        ("players who discovered this cell", "SELECT count(*) FROM player_cells WHERE cell_id=$1", "live"),
    ],
    "characters": [
        ("gates using this character", "SELECT count(*) FROM dialogue_gates WHERE character_id=$1", "content"),
        ("agent memories (will be erased)", "SELECT count(*) FROM agent_memories WHERE character_id=$1", "cascade"),
    ],
    "locations": [
        ("nodes at this location", "SELECT count(*) FROM story_nodes WHERE location_id=$1", "content"),
        ("gates at this location", "SELECT count(*) FROM dialogue_gates WHERE location_id=$1", "content"),
        ("agent memories (location will be cleared)", "SELECT count(*) FROM agent_memories WHERE location_id=$1", "cascade"),
    ],
    "nodes": [
        ("edges pointing to this node", "SELECT count(*) FROM story_edges WHERE to_node=$1", "content"),
        ("cells whose arrival is this node", "SELECT count(*) FROM world_cells WHERE arrival_node=$1", "content"),
        ("players currently on this node", "SELECT count(*) FROM player_sessions WHERE current_node=$1", "live"),
        ("log entries on this node", "SELECT count(*) FROM log_entries WHERE node_id=$1", "live"),
        ("outgoing edges (will be removed)", "SELECT count(*) FROM story_edges WHERE from_node=$1", "cascade"),
    ],
    "gates": [
        ("nodes using this gate", "SELECT count(*) FROM story_nodes WHERE gate_id=$1", "content"),
        ("player gate attempts", "SELECT count(*) FROM gate_attempts WHERE gate_id=$1", "live"),
    ],
    "puzzles": [
        ("nodes using this puzzle", "SELECT count(*) FROM story_nodes WHERE puzzle_id=$1", "content"),
        ("player puzzle progress", "SELECT count(*) FROM puzzle_progress WHERE puzzle_id=$1", "live"),
        ("clues for this puzzle (will be removed)", "SELECT count(*) FROM puzzle_clues WHERE puzzle_id=$1", "cascade"),
    ],
    "clues": [
        ("puzzles requiring this clue", "SELECT count(*) FROM puzzles WHERE $1 = ANY(required_clues)", "content"),
        ("players who found this clue", "SELECT count(*) FROM player_clues WHERE clue_id=$1", "live"),
    ],
    "edges": [],
}


async def coupling_check(conn, kind: str, id_: str) -> dict:
    """Count rows that reference `id_`. Returns content_refs / live_refs / cascade
    lists (each {label,count}, only non-zero) and a `blocked` flag.

    NOTE: story_nodes.gate_id/puzzle_id and player_sessions.current_node /
    log_entries.node_id are plain TEXT with no DB FK — Postgres will NOT block
    those deletes, so this check is the only guard.
    """
    if kind not in EDIT_KINDS:
        raise ValueError(f"unknown content kind: {kind}")
    content_refs, live_refs, cascade = [], [], []
    for label, sql, bucket in _COUPLING.get(kind, []):
        n = await conn.fetchval(sql, id_)
        if not n:
            continue
        item = {"label": label, "count": int(n)}
        if bucket == "content":
            content_refs.append(item)
        elif bucket == "live":
            live_refs.append(item)
        else:
            cascade.append(item)
    return {
        "content_refs": content_refs,
        "live_refs": live_refs,
        "cascade": cascade,
        "blocked": bool(content_refs) or bool(live_refs),
    }


async def delete_one(conn, kind: str, id_: str):
    """Physically delete the row by id. seed_content() is upsert-only (it never
    deletes a removed entity), and its per-from_node edge rewrite skips a node once
    its last edge is gone — so an explicit DELETE is needed for every kind.

    Call inside the same transaction as the re-seed of the remaining set.
    """
    await conn.execute(f"DELETE FROM {EDIT_KINDS[kind]} WHERE id=$1", id_)
