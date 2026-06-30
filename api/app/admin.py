"""Admin export/import for the full database and individual player saves
(see the admin UI). Content export/import lives in content.py.

Uses Postgres `json_agg` (export) and `json_populate_recordset` (import) so every
column round-trips through its own type (jsonb, uuid, timestamptz, text[]). The
pgvector `embedding` column is the one exception — exported as text and cast back
to ::vector by json_populate_recordset's type coercion.
"""
import json

# Full dependency order: parents before children (FK-safe for insert). `games` is
# the new root every content/runtime row hangs off; player_games is the save slot.
ALL_TABLES = [
    "games",
    "story_arcs", "world_cells", "characters", "locations", "puzzles",
    "story_nodes", "dialogue_gates", "story_edges", "puzzle_clues", "generated_images",
    "players", "recovery_codes", "player_games", "game_logs", "log_entries",
    "progress_events", "player_flags", "player_clues", "player_cells",
    "puzzle_progress", "gate_attempts", "gate_messages", "agent_memories",
]

# Per-player runtime tables, in FK-safe insert order (save slot first).
PLAYER_TABLES = [
    "player_games", "game_logs", "log_entries", "progress_events", "player_flags",
    "player_clues", "player_cells", "puzzle_progress", "gate_attempts",
    "gate_messages", "agent_memories",
]


async def _dump(conn, table: str, where: str = "", *args) -> list:
    """json_agg a table (optionally filtered). agent_memories.embedding → text."""
    if table == "agent_memories":
        cols = ("id, game_id, character_id, content, embedding::text AS embedding, location_id, "
                "story_time, source, origin_player_id, created_seq, voided, created_at")
        sql = f"SELECT coalesce(json_agg(r), '[]') FROM (SELECT {cols} FROM agent_memories t {where}) r"
    else:
        sql = f"SELECT coalesce(json_agg(t), '[]') FROM {table} t {where}"
    return json.loads(await conn.fetchval(sql, *args))


async def _restore(conn, table: str, rows: list):
    if not rows:
        return
    await conn.execute(
        f"INSERT INTO {table} SELECT * FROM json_populate_recordset(null::{table}, $1::json)",
        json.dumps(rows))


# --------------------------------------------------------------------------- #
#  Full database
# --------------------------------------------------------------------------- #
async def export_all(conn) -> dict:
    return {t: await _dump(conn, t) for t in ALL_TABLES}


async def import_all(conn, data: dict):
    """Destructive: truncate everything, then restore. One transaction."""
    async with conn.transaction():
        await conn.execute("TRUNCATE " + ", ".join(ALL_TABLES) + " RESTART IDENTITY CASCADE")
        for t in ALL_TABLES:
            await _restore(conn, t, data.get(t) or [])


# --------------------------------------------------------------------------- #
#  Single player save
# --------------------------------------------------------------------------- #
async def export_player(conn, player_id: str) -> dict:
    """A player's whole account: every per-game save slot + all runtime, across all
    games they've played."""
    p = await conn.fetchrow(
        "SELECT id, display_name, onboarded, active_game_id FROM players WHERE id=$1", player_id)
    if p is None:
        return {}
    save = {"player": {"id": str(p["id"]), "display_name": p["display_name"],
                       "onboarded": p["onboarded"], "active_game_id": p["active_game_id"]}}
    save["player_games"] = await _dump(conn, "player_games", "WHERE t.player_id=$1", player_id)
    save["game_logs"] = await _dump(conn, "game_logs", "WHERE t.player_id=$1", player_id)
    save["log_entries"] = await _dump(
        conn, "log_entries", "WHERE t.log_id IN (SELECT id FROM game_logs WHERE player_id=$1)", player_id)
    for t in ["progress_events", "player_flags", "player_clues", "player_cells",
              "puzzle_progress", "gate_attempts", "gate_messages"]:
        save[t] = await _dump(conn, t, "WHERE t.player_id=$1", player_id)
    save["agent_memories"] = await _dump(conn, "agent_memories", "WHERE t.origin_player_id=$1", player_id)
    return save


async def import_player(conn, data: dict):
    """Destructive for THIS player only: replace all their save slots + runtime. The
    player_id must already exist (no cross-account remapping). One transaction."""
    p = data.get("player") or {}
    pid = p.get("id")
    if not pid:
        raise ValueError("save has no player id")
    async with conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM players WHERE id=$1", pid):
            raise ValueError("player not found on this server")
        # wipe existing runtime for this player (all games)
        await conn.execute("DELETE FROM agent_memories WHERE origin_player_id=$1", pid)
        for t in ["gate_messages", "gate_attempts", "puzzle_progress", "player_cells",
                  "player_clues", "player_flags", "progress_events"]:
            await conn.execute(f"DELETE FROM {t} WHERE player_id=$1", pid)
        await conn.execute(
            "DELETE FROM log_entries WHERE log_id IN (SELECT id FROM game_logs WHERE player_id=$1)", pid)
        await conn.execute("DELETE FROM game_logs WHERE player_id=$1", pid)
        await conn.execute("DELETE FROM player_games WHERE player_id=$1", pid)
        # restore
        for t in PLAYER_TABLES:
            await _restore(conn, t, data.get(t) or [])
        await conn.execute("UPDATE players SET onboarded=$2, active_game_id=$3 WHERE id=$1",
                           pid, bool(p.get("onboarded", True)), p.get("active_game_id"))


async def list_players(conn) -> list:
    rows = await conn.fetch("SELECT id, display_name FROM players ORDER BY display_name")
    return [{"id": str(r["id"]), "display_name": r["display_name"]} for r in rows]


# --------------------------------------------------------------------------- #
#  Restart ONE game for ONE player (the lobby's "New game" on an existing save)
# --------------------------------------------------------------------------- #
# Runtime tables in FK-safe DELETE order, all scoped to (player_id, game_id) so a
# restart never touches the player's other games or other players.
_RESET_TABLES = [
    "gate_messages", "gate_attempts", "puzzle_progress", "player_cells",
    "player_clues", "player_flags", "progress_events", "player_alignment_events",
    "agent_memories",
]


async def reset_player_game(conn, player_id, game_id):
    """Wipe one player's progress in ONE game so they can start it over. Other games
    and other players are untouched; the player_games slot is blanked so _start_game
    re-bootstraps it. Runs in one transaction. agent_memories filters on
    origin_player_id (whose-memory), not player_id."""
    async with conn.transaction():
        for t in _RESET_TABLES:
            col = "origin_player_id" if t == "agent_memories" else "player_id"
            await conn.execute(
                f"DELETE FROM {t} WHERE {col}=$1 AND game_id=$2", player_id, game_id)
        await conn.execute(
            """DELETE FROM log_entries
               WHERE log_id IN (SELECT id FROM game_logs WHERE player_id=$1 AND game_id=$2)""",
            player_id, game_id)
        await conn.execute(
            "DELETE FROM game_logs WHERE player_id=$1 AND game_id=$2", player_id, game_id)
        await conn.execute(
            """UPDATE player_games SET current_node=NULL, log_id=NULL, story_time=0
               WHERE player_id=$1 AND game_id=$2""", player_id, game_id)
