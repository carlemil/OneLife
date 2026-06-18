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


async def delete_one(conn, kind: str, id_: str):
    """Physically delete the row by id. seed_content() is upsert-only (it never
    deletes a removed entity), and its per-from_node edge rewrite skips a node once
    its last edge is gone — so an explicit DELETE is needed for every kind.

    Call inside the same transaction as the re-seed of the remaining set.
    """
    await conn.execute(f"DELETE FROM {EDIT_KINDS[kind]} WHERE id=$1", id_)
