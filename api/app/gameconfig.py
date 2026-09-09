"""Per-game engine configuration read from the dataset's `game:` block.

The engine is data-agnostic: dataset-specific strings (the setting, name particles,
the onboarding manual/quiz, the offline soundtrack) live in the game data under the
top-level `game:` block (see games/SandbyMystery/data/game.yaml), NOT hardcoded here.
This module loads that block for a given game, merged over built-in DEFAULTS so a
minimal dataset with no `game:` block still runs. Title/subtitle/first_summary are
handled separately (seeded into the `games` table by content.register_game); this
module covers the richer runtime config that isn't a DB column.

Loading is tolerant (a missing/broken file falls back to DEFAULTS, never raises) and
cached per data-dir, so per-request callers (atmosphere, engine) stay cheap.
"""
import os
import glob

import yaml

from . import gamestate

# Built-in engine fallbacks — used for any key a dataset's `game:` block omits, and
# for datasets that ship no game block at all. Deliberately generic (no dataset
# content); the SandbyMystery dataset overrides these in its own game.yaml.
DEFAULTS = {
    "setting": {"region": "", "period": ""},
    # `map: false` turns the overview map off for a game (no map block in the state, no
    # map panel; every edge is offered as a button).
    "map": True,
    # In-game wall clock: {"start": "16:20"} makes the engine expose story_time (minutes)
    # as "HH:MM" in every state. Absent → no clock is shown.
    "clock": {},
    # Language the base YAML is written in. Content defaults to English; a game authored
    # directly in another language declares it here so selecting the game switches the
    # player's UI chrome to match (main.select_game).
    "language": "en",
    # Name particles/titles that are not the name itself (so "von Trapp" is known as
    # "Trapp"). A small cross-European default; datasets add their own honorifics.
    "name_particles": ["the", "von", "van", "der", "de", "la", "le"],
    "onboarding": {
        "pass_threshold": None,   # None → "must get all correct"
        "manual": (
            "HOW TO PLAY\n\n"
            "You act by choosing what to do and talking to the people and places you "
            "meet. The LOG records everything — your log is your progress. You can die "
            "or get stuck; CHEAT DEATH to rewind the log, at the cost of progress and "
            "leaderboard position. Read this, then prove you've got the basics."
        ),
        "quiz": [],
    },
    "offline": {
        "tracks": [
            {"artist": "Brian Eno", "title": "An Ending (Ascent)", "why": "ambient stillness"},
        ],
    },
}

_cache: dict[str, dict] = {}


def _deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` onto a copy of `base` (dicts merge; everything else,
    including lists, is replaced when present in `over`)."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _read_game_block(data_dir: str) -> dict:
    """Merge the `game:` block from every *.yaml/*.yml in `data_dir`. Lightweight —
    reads only the game block, not the full content graph. Tolerant of bad files."""
    block: dict = {}
    files = sorted(glob.glob(os.path.join(data_dir, "*.yaml"))
                   + glob.glob(os.path.join(data_dir, "*.yml")))
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
            if isinstance(doc.get("game"), dict):
                block.update(doc["game"])
        except Exception as e:  # noqa: BLE001
            print(f"[gameconfig] skipped {f}: {e}")
    return block


def for_dir(data_dir: str) -> dict:
    """The merged config for a game data dir (cached). DEFAULTS overlaid with the
    dataset's `game:` block."""
    if data_dir in _cache:
        return _cache[data_dir]
    try:
        block = _read_game_block(data_dir)
    except Exception as e:  # noqa: BLE001
        print(f"[gameconfig] load failed for {data_dir}: {e}")
        block = {}
    cfg = _deep_merge(DEFAULTS, block)
    _cache[data_dir] = cfg
    return cfg


def for_game(game_id: str) -> dict:
    """Config for a named game (resolves its data dir via gamestate)."""
    return for_dir(gamestate.data_dir_for(game_id))


def active() -> dict:
    """Config for the currently-active game."""
    return for_dir(gamestate.active_dir())


def invalidate(data_dir: str | None = None) -> None:
    """Drop the cache (all, or one dir) — call after content is re-seeded/switched."""
    if data_dir is None:
        _cache.clear()
    else:
        _cache.pop(data_dir, None)
