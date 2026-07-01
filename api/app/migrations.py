"""In-place migration: single-game schema → concurrent multi-game.

`db/*.sql` only runs on a FRESH volume, so an already-provisioned database keeps the
old single-game shape (bare TEXT primary keys, the game position stored on
player_sessions, no `games`/`player_games`, no `game_id` anywhere). This module
brings such a volume up to the multi-game schema in ONE transaction, idempotently:

  * adds the `games` registry (+ the existing dataset as the first game),
  * adds `game_id` to every authored-content and per-player runtime table and
    backfills it to the existing game,
  * rebuilds content primary keys as composite (game_id, id) and rewrites every
    inter-content / runtime→content foreign key to carry game_id,
  * splits player_sessions (auth) from the new per-(player,game) save slot
    `player_games`, migrating the one in-flight position across so existing players
    auto-resume exactly where they were.

Because no id VALUES change (only a game_id column is added and keys are re-homed),
the live save's current_node / log_id / node_id / clue / gate / puzzle references
stay byte-identical and valid. Guarded by a sentinel (story_nodes.game_id) so it is
a no-op once applied, and fully transactional so a failure leaves the old schema
intact. Keep this in sync with db/01_schema.sql and db/03_memory.sql.
"""
import re

from . import gamestate


# Content tables: PK (id) → (game_id, id).
_CONTENT_TABLES = [
    "world_cells", "locations", "characters", "story_arcs", "story_nodes",
    "story_edges", "dialogue_gates", "puzzles", "puzzle_clues",
]

# Per-player runtime tables whose composite PK gains game_id.
_RUNTIME_COMPOSITE_PK = {
    "player_flags": "player_id, game_id, flag",
    "player_clues": "player_id, game_id, clue_id",
    "player_cells": "player_id, game_id, cell_id",
    "puzzle_progress": "player_id, game_id, puzzle_id",
    "gate_attempts": "player_id, game_id, gate_id",
}

# Caches whose single-column PK gains game_id.
_CACHE_PK = {
    "edge_alignment_cache": "game_id, edge_id",
    "node_positions": "game_id, node_id",
    "generated_images": "game_id, theme",
}

# Every table that needs a game_id column + a game_id → games(id) FK. Content tables
# and the caches above are included; the scope-only runtime tables are listed too.
_ALL_GAME_ID_TABLES = _CONTENT_TABLES + list(_RUNTIME_COMPOSITE_PK) + list(_CACHE_PK) + [
    "game_logs", "log_entries", "progress_events", "player_alignment_events",
    "gate_messages", "agent_memories",
]

# Old single-column FKs to drop before swapping content PKs (default constraint names).
_OLD_FKS = [
    ("locations", "locations_cell_id_fkey"),
    ("characters", None),
    ("story_nodes", "story_nodes_arc_id_fkey"),
    ("story_nodes", "story_nodes_location_id_fkey"),
    ("story_edges", "story_edges_from_node_fkey"),
    ("story_edges", "story_edges_to_node_fkey"),
    ("dialogue_gates", "dialogue_gates_location_id_fkey"),
    ("dialogue_gates", "dialogue_gates_character_id_fkey"),
    ("puzzle_clues", "puzzle_clues_puzzle_id_fkey"),
    ("player_clues", "player_clues_clue_id_fkey"),
    ("player_cells", "player_cells_cell_id_fkey"),
    ("puzzle_progress", "puzzle_progress_puzzle_id_fkey"),
    ("gate_attempts", "gate_attempts_gate_id_fkey"),
    ("agent_memories", "agent_memories_character_id_fkey"),
    ("agent_memories", "agent_memories_location_id_fkey"),
]


async def ensure_multigame(conn) -> bool:
    """Run the migration if the DB is still single-game. Returns True if it ran."""
    already = await conn.fetchval(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name='story_nodes' AND column_name='game_id'""")
    if already:
        return False
    if not await conn.fetchval("SELECT to_regclass('public.story_nodes')"):
        return False  # fresh/empty volume — db/*.sql already built the new shape

    g = gamestate.default_game()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", g):
        g = "SandbyMystery"
    # Generic placeholder for the legacy volume's games row; the real first-scene line
    # comes from the dataset's `game:` block (first_summary) via register_game on seed.
    first_summary = "You wake."

    sql = _build_sql(g, first_summary)
    async with conn.transaction():
        await conn.execute(sql)
    return True


def _build_sql(g: str, first_summary: str) -> str:
    parts: list[str] = []
    A = parts.append

    # 1. games registry + the existing dataset.
    A("""CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            subtitle TEXT NOT NULL DEFAULT '',
            first_summary TEXT NOT NULL DEFAULT 'You wake.',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now());""")
    A(f"INSERT INTO games (id, title, first_summary) "
      f"VALUES ('{g}', '{g}', '{first_summary.replace(chr(39), chr(39) * 2)}') "
      f"ON CONFLICT (id) DO NOTHING;")

    # 2. players.active_game_id + the per-(player,game) save slot.
    A("ALTER TABLE players ADD COLUMN IF NOT EXISTS active_game_id TEXT;")
    A("""CREATE TABLE IF NOT EXISTS player_games (
            player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            current_node TEXT, story_time BIGINT NOT NULL DEFAULT 0, log_id UUID,
            last_played_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (player_id, game_id));""")

    # 3. Add + backfill game_id on every content/runtime table.
    for t in _ALL_GAME_ID_TABLES:
        A(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS game_id TEXT;")
        A(f"UPDATE {t} SET game_id='{g}' WHERE game_id IS NULL;")

    # 4. Drop the leaderboard view (regrouped per game at the end) and the old FKs.
    A("DROP VIEW IF EXISTS leaderboard;")
    for table, name in _OLD_FKS:
        if name:
            A(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name};")

    # 5. Swap content PKs → composite (game_id, id). CASCADE is a safety net: it drops
    #    any inter-content / runtime→content FK still pointing at the old single-column
    #    PK (even one named non-defaultly that step 4 missed) — all such FKs are re-added
    #    composite in step 8 (location_id on agent_memories is intentionally not).
    for t in _CONTENT_TABLES:
        A(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey CASCADE;")
        A(f"ALTER TABLE {t} ADD PRIMARY KEY (game_id, id);")

    # 6. Swap runtime / cache PKs → composite.
    for t, cols in {**_RUNTIME_COMPOSITE_PK, **_CACHE_PK}.items():
        A(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey;")
        A(f"ALTER TABLE {t} ADD PRIMARY KEY ({cols});")

    # 7. NOT NULL on every game_id + a game_id → games(id) FK.
    for t in _ALL_GAME_ID_TABLES:
        A(f"ALTER TABLE {t} ALTER COLUMN game_id SET NOT NULL;")
        A(f"ALTER TABLE {t} ADD CONSTRAINT {t}_game_id_fkey "
          f"FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE;")

    # 8. Re-add inter-content + runtime→content FKs as composite (carry game_id).
    composite_fks = [
        ("locations", "(game_id, cell_id)", "world_cells(game_id, id)", ""),
        ("story_nodes", "(game_id, arc_id)", "story_arcs(game_id, id)", ""),
        ("story_nodes", "(game_id, location_id)", "locations(game_id, id)", ""),
        ("story_edges", "(game_id, from_node)", "story_nodes(game_id, id)", " ON DELETE CASCADE"),
        ("story_edges", "(game_id, to_node)", "story_nodes(game_id, id)", ""),
        ("dialogue_gates", "(game_id, location_id)", "locations(game_id, id)", ""),
        ("dialogue_gates", "(game_id, character_id)", "characters(game_id, id)", ""),
        ("puzzle_clues", "(game_id, puzzle_id)", "puzzles(game_id, id)", " ON DELETE CASCADE"),
        ("player_clues", "(game_id, clue_id)", "puzzle_clues(game_id, id)", ""),
        ("player_cells", "(game_id, cell_id)", "world_cells(game_id, id)", ""),
        ("puzzle_progress", "(game_id, puzzle_id)", "puzzles(game_id, id)", ""),
        ("gate_attempts", "(game_id, gate_id)", "dialogue_gates(game_id, id)", ""),
        ("agent_memories", "(game_id, character_id)", "characters(game_id, id)", " ON DELETE CASCADE"),
    ]
    for i, (table, cols, ref, extra) in enumerate(composite_fks):
        A(f"ALTER TABLE {table} ADD CONSTRAINT {table}_fk_mg_{i} "
          f"FOREIGN KEY {cols} REFERENCES {ref}{extra};")

    # 9. Re-home the memory index per game.
    A("DROP INDEX IF EXISTS agent_memories_char_idx;")
    A("CREATE INDEX agent_memories_char_idx ON agent_memories (game_id, character_id);")
    A("DROP INDEX IF EXISTS player_alignment_events_player_idx;")
    A("CREATE INDEX player_alignment_events_player_idx "
      "ON player_alignment_events (player_id, game_id, id);")

    # 10. Split sessions: move the in-flight position into player_games, point every
    #     existing player at this game (auto-resume), drop the old position columns.
    A(f"""INSERT INTO player_games (player_id, game_id, current_node, story_time, log_id)
            SELECT player_id, '{g}', current_node, story_time, log_id
            FROM player_sessions
            WHERE current_node IS NOT NULL OR log_id IS NOT NULL
            ON CONFLICT (player_id, game_id) DO NOTHING;""")
    A(f"UPDATE players SET active_game_id='{g}' WHERE active_game_id IS NULL;")
    A("ALTER TABLE players ADD CONSTRAINT players_active_game_id_fkey "
      "FOREIGN KEY (active_game_id) REFERENCES games(id);")
    A("ALTER TABLE player_sessions DROP COLUMN IF EXISTS current_node;")
    A("ALTER TABLE player_sessions DROP COLUMN IF EXISTS story_time;")
    A("ALTER TABLE player_sessions DROP COLUMN IF EXISTS log_id;")

    # 11. Recreate the leaderboard view, now per (player, game).
    A("""CREATE VIEW leaderboard AS
            SELECT pg.player_id, p.display_name, pg.game_id,
                   COALESCE(SUM(pe.points) FILTER (WHERE NOT pe.voided), 0)::int AS progress
            FROM player_games pg
            JOIN players p ON p.id = pg.player_id
            LEFT JOIN progress_events pe
                   ON pe.player_id = pg.player_id AND pe.game_id = pg.game_id
            GROUP BY pg.player_id, p.display_name, pg.game_id;""")

    return "\n".join(parts)
