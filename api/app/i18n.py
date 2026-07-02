"""Per-game translations of authored player-facing text.

English is the base language: the *.yaml under games/<id>/data/ is the single source of
truth and stays pure English. A translation is one row in `content_translations`
overriding one English *leaf* (a single string) for one language; a missing row falls
back to the English base at render time. Translations are authored as shape-mirroring
sidecars games/<id>/i18n/<lang>.yaml and seeded by content.seed_translations.

This module is the single source of truth for THREE things:
  1. FLATTENERS — which leaves of each entity are translatable, and their `field_path`
     (dotted + [i], e.g. "body_variants[0].body", "solution.set[1]"). Shared by the
     translate generator (translate.py), the sidecar loader, and the drift check.
  2. load_sidecar / discover_langs — read a game's sidecars into DB-ready rows.
  3. Translator — a per-(game, lang) lookup used by the render path, with English fallback.

Kept deliberately dependency-light (content + gamestate) and tolerant, mirroring
gameconfig.py: a missing/broken sidecar yields no translations rather than raising.
"""
import os
import re
import glob
import hashlib

import yaml

from . import content, gamestate

# --------------------------------------------------------------------------- #
#  Path addressing:  dotted keys + [i] indices into a nested dict/list.
# --------------------------------------------------------------------------- #
_TOK = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def get_path(obj, path):
    """Read a leaf from a nested structure by a 'a.b[0].c' path. None if absent."""
    cur = obj
    for m in _TOK.finditer(path):
        if cur is None:
            return None
        key, idx = m.group(1), m.group(2)
        if key is not None:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                return None
            cur = cur[i]
    return cur


def set_path(obj, path, value):
    """Write `value` into a nested dict/list at `path`, creating dicts and growing lists
    as needed. `obj` must be a dict. Used to build shape-mirroring sidecars."""
    toks = [(m.group(1), m.group(2)) for m in _TOK.finditer(path)]
    cur = obj
    for i, (key, idx) in enumerate(toks):
        last = i == len(toks) - 1
        nxt_is_index = (not last) and toks[i + 1][1] is not None
        if key is not None:
            if last:
                cur[key] = value
            else:
                cur = cur.setdefault(key, [] if nxt_is_index else {})
        else:
            j = int(idx)
            while len(cur) <= j:
                cur.append({} if not last else None)
            if last:
                cur[j] = value
            else:
                if cur[j] is None:
                    cur[j] = [] if nxt_is_index else {}
                cur = cur[j]


def sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
#  FLATTENERS — the translatable leaves of each entity, as (field_path, text).
#  Only NON-EMPTY, player-facing prose. Ids, flags, conditions, media hints and
#  proper names (reveal_name) are deliberately excluded.
# --------------------------------------------------------------------------- #
def _node_leaves(n):
    if n.get("title"):
        yield "title", n["title"]
    if n.get("body"):
        yield "body", n["body"]
    for i, v in enumerate(n.get("body_variants") or []):
        if isinstance(v, dict) and v.get("body"):
            yield f"body_variants[{i}].body", v["body"]


def _edge_leaves(e):
    if e.get("label"):
        yield "label", e["label"]
    log = (e.get("effects") or {}).get("log")
    if log:
        yield "effects.log", log


def _puzzle_leaves(p):
    if p.get("prompt"):
        yield "prompt", p["prompt"]
    for i, h in enumerate(p.get("hint_ladder") or []):
        if h:
            yield f"hint_ladder[{i}]", h
    log = (p.get("on_solve") or {}).get("log")
    if log:
        yield "on_solve.log", log
    sol = p.get("solution") or {}
    # Crossword accepted answers are locked to the interlocking grid — never translated.
    if p.get("type") != "crossword" and sol.get("kind") != "crossword":
        if sol.get("value"):
            yield "solution.value", sol["value"]
        for i, s in enumerate(sol.get("set") or []):
            if s:
                yield f"solution.set[{i}]", s


def _clue_leaves(c):
    if c.get("reveal_text"):
        yield "reveal_text", c["reveal_text"]


def _character_leaves(c):
    # Public/role name only ("The Janitor" -> "Vaktmästaren"). The proper reveal_name and
    # the LLM-facing persona are NOT translated.
    if c.get("name"):
        yield "name", c["name"]


def _location_leaves(l):
    if l.get("name"):
        yield "name", l["name"]
    if l.get("description"):
        yield "description", l["description"]


def _cell_leaves(c):
    if c.get("name"):
        yield "name", c["name"]
    if c.get("region"):
        yield "region", c["region"]


def _gate_leaves(g):
    kb = g.get("knowledge_boundary") or {}
    if kb.get("tone"):
        yield "knowledge_boundary.tone", kb["tone"]
    for i, s in enumerate(kb.get("knows") or []):
        if s:
            yield f"knowledge_boundary.knows[{i}]", s
    for i, s in enumerate(kb.get("refuses") or []):
        if s:
            yield f"knowledge_boundary.refuses[{i}]", s
    for i, h in enumerate(g.get("hint_ladder") or []):
        if h:
            yield f"hint_ladder[{i}]", h
    osx = g.get("on_success") or {}
    if osx.get("log"):
        yield "on_success.log", osx["log"]
    if osx.get("announce"):
        yield "on_success.announce", osx["announce"]
    wm = osx.get("write_memory") or {}
    if wm.get("content"):
        yield "on_success.write_memory.content", wm["content"]


def _game_leaves(meta):
    if meta.get("title"):
        yield "title", meta["title"]
    if meta.get("subtitle"):
        yield "subtitle", meta["subtitle"]
    if meta.get("first_summary"):
        yield "first_summary", meta["first_summary"]
    ob = meta.get("onboarding") or {}
    if ob.get("manual"):
        yield "onboarding.manual", ob["manual"]
    for i, q in enumerate(ob.get("quiz") or []):
        if q.get("prompt"):
            yield f"onboarding.quiz[{i}].prompt", q["prompt"]
        for j, opt in enumerate(q.get("options") or []):
            if opt:
                yield f"onboarding.quiz[{i}].options[{j}]", opt


# entity_type -> (sidecar top-level key, per-entity leaf generator). "game" is special
# (a single entity keyed by the game_id, not a list). Order is the generator's batch order.
FLATTENERS = {
    "game": ("game", _game_leaves),
    "story_nodes": ("nodes", _node_leaves),
    "story_edges": ("edges", _edge_leaves),
    "puzzles": ("puzzles", _puzzle_leaves),
    "puzzle_clues": ("clues", _clue_leaves),
    "characters": ("characters", _character_leaves),
    "locations": ("locations", _location_leaves),
    "world_cells": ("cells", _cell_leaves),
    "dialogue_gates": ("gates", _gate_leaves),
}


def _entities(entity_type: str, data: dict, meta: dict):
    """Yield (entity_id, entity_dict) for one entity_type from loaded base content."""
    if entity_type == "game":
        yield None, meta            # entity_id filled in by caller with the game_id
        return
    if entity_type == "story_edges":
        for e in content.all_edges(data):
            yield e["id"], e
        return
    key = {
        "story_nodes": "nodes", "puzzles": "puzzles", "puzzle_clues": "clues",
        "characters": "characters", "locations": "locations", "world_cells": "cells",
        "dialogue_gates": "gates",
    }[entity_type]
    for row in data.get(key, []) or []:
        yield row["id"], row


def iter_leaves(game_id: str, data: dict, meta: dict):
    """Every translatable leaf of a game as (entity_type, entity_id, field_path, text).
    `data` is content.load_dir()'s dict; `meta` is its `game:` block."""
    for etype, (_key, leaves) in FLATTENERS.items():
        for eid, ent in _entities(etype, data, meta):
            if ent is None:
                continue
            rid = game_id if etype == "game" else eid
            for path, text in leaves(ent):
                yield etype, rid, path, text


# --------------------------------------------------------------------------- #
#  On-disk sidecars: games/<id>/i18n/<lang>.yaml
# --------------------------------------------------------------------------- #
def i18n_dir(game_id: str) -> str:
    """The i18n sidecar directory for a game (sibling of its data/ dir)."""
    data_dir = gamestate.data_dir_for(game_id)
    return os.path.join(os.path.dirname(data_dir.rstrip("/\\")), "i18n")


def sidecar_path(game_id: str, lang: str) -> str:
    return os.path.join(i18n_dir(game_id), f"{lang}.yaml")


def discover_langs(game_id: str) -> list[str]:
    """Language codes with a sidecar file, from games/<id>/i18n/*.yaml basenames."""
    d = i18n_dir(game_id)
    return sorted(os.path.splitext(os.path.basename(f))[0]
                  for f in glob.glob(os.path.join(d, "*.yaml")) + glob.glob(os.path.join(d, "*.yml")))


def _sidecar_entity(sidecar: dict, etype: str, entity_id: str):
    """The nested translated entity dict for (etype, entity_id) in a loaded sidecar."""
    top = FLATTENERS[etype][0]
    if etype == "game":
        return sidecar.get("game") or {}
    return (sidecar.get(top) or {}).get(entity_id)


def load_sidecar(game_id: str, lang: str) -> list[tuple]:
    """Walk games/<id>/i18n/<lang>.yaml against the English base and produce DB rows
    (entity_type, entity_id, field_path, text, src_hash) for every translated leaf that
    is present and non-empty. Tolerant: a missing/broken file yields []."""
    path = sidecar_path(game_id, lang)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            sidecar = yaml.safe_load(fh) or {}
    except Exception as e:  # noqa: BLE001
        print(f"[i18n] skipped {path}: {e}")
        return []
    try:
        data, _ = content.load_dir(gamestate.data_dir_for(game_id))
    except Exception as e:  # noqa: BLE001
        print(f"[i18n] base load failed for {game_id}: {e}")
        return []
    meta = data.get("game", {}) or {}

    rows: list[tuple] = []
    for etype, eid, path_, base_text in iter_leaves(game_id, data, meta):
        lookup_id = eid  # for game, eid == game_id; sidecar uses the single "game" block
        ent = _sidecar_entity(sidecar, etype, lookup_id)
        if not ent:
            continue
        val = get_path(ent, path_)
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        rows.append((etype, eid, path_, str(val), sha1(base_text)))
    return rows


# --------------------------------------------------------------------------- #
#  Render-time lookup with English fallback.
# --------------------------------------------------------------------------- #
class Translator:
    """A loaded (game, lang) translation set. tr() returns the translation for a leaf or
    the English base. The empty translator (lang 'en' or no rows) is a pure passthrough."""

    __slots__ = ("lang", "_by")

    def __init__(self, lang: str, rows=()):
        self.lang = lang
        self._by: dict = {}
        for r in rows:
            self._by.setdefault((r["entity_type"], r["entity_id"]), {})[r["field_path"]] = r["text"]

    @property
    def active(self) -> bool:
        return bool(self._by)

    def tr(self, entity_type: str, entity_id: str, field_path: str, base):
        if not self._by:
            return base
        d = self._by.get((entity_type, entity_id))
        if not d:
            return base
        v = d.get(field_path)
        return v if v is not None else base

    def node_variants(self, node_id: str, variants: list) -> list:
        """Return `variants` with each [i].body swapped for its translation (a shallow
        copy per element so the DB row isn't mutated). Feeds resolve_body unchanged."""
        if not self._by or not variants:
            return variants
        out = []
        for i, v in enumerate(variants):
            if isinstance(v, dict) and "body" in v:
                v = dict(v)
                v["body"] = self.tr("story_nodes", node_id, f"body_variants[{i}].body", v["body"])
            out.append(v)
        return out


async def tr_one(conn, game_id: str, lang: str, entity_type: str, entity_id: str,
                 field_path: str, base):
    """Single-leaf translation lookup with English fallback — for the write path, where a
    log line is localized at the moment it's stored (one lookup, no bulk load)."""
    if not lang or lang == "en" or not game_id:
        return base
    v = await conn.fetchval(
        """SELECT text FROM content_translations WHERE game_id=$1 AND lang=$2
           AND entity_type=$3 AND entity_id=$4 AND field_path=$5""",
        game_id, lang, entity_type, entity_id, field_path)
    return v if v is not None else base


_EMPTY = Translator("en")


async def translator(conn, game_id: str, lang: str) -> Translator:
    """Load the (game, lang) translation set. 'en' / no rows -> the passthrough translator.
    One bulk query; called once per render so per-field lookups are in-memory."""
    if not lang or lang == "en" or not game_id:
        return _EMPTY
    rows = await conn.fetch(
        "SELECT entity_type, entity_id, field_path, text FROM content_translations "
        "WHERE game_id=$1 AND lang=$2", game_id, lang)
    # Carry the requested lang even with no rows, so state.language reflects the player's
    # choice while every leaf transparently falls back to English.
    return Translator(lang, rows)
