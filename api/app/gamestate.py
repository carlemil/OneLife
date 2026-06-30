"""Multi-game support: discover the games available under /games and switch the
active dataset at runtime (admin-only).

The "active game" is the folder whose *.yaml is currently seeded into the DB and
served to players. The whole `games/` tree is mounted at /games (see
docker-compose.yml) so the API can read any game's data without a restart; the
default game also lives at /content (the original bind mount). The chosen game is
persisted in the `app_settings` table, so it survives a container restart.

All path-resolving subsystems (content.load_dir, map_write, the /content-static
image route) read `active_dir()` so a switch takes effect immediately, live.
"""
import os
import glob

GAMES_DIR = os.environ.get("GAMES_DIR", "/games")
CONTENT_DIR = os.environ.get("CONTENT_DIR", "/content")


def _has_yaml(d: str) -> bool:
    return bool(glob.glob(os.path.join(d, "*.yaml")) or glob.glob(os.path.join(d, "*.yml")))


def default_game() -> str:
    """Name of the game wired up via GAME_DATA_DIR / the /content mount."""
    p = os.environ.get("GAME_DATA_DIR") or CONTENT_DIR or "/content"
    parts = [x for x in p.replace("\\", "/").split("/") if x not in ("", ".", "..")]
    if len(parts) >= 2 and parts[-1].lower() == "data":
        return parts[-2]
    return parts[-1] if parts else "game"


# Runtime-mutable active game, initialised to the default (the /content mount).
# main.py overrides this from app_settings on startup if a choice was persisted.
_active_game = default_game()


def data_dir_for(name: str) -> str:
    """The data dir for a named game. The default game is preferentially served
    from /content (guaranteed mounted even if /games isn't), everything else from
    /games/<name>/data."""
    if name == default_game() and _has_yaml(CONTENT_DIR):
        return CONTENT_DIR
    return os.path.join(GAMES_DIR, name, "data")


def active_game() -> str:
    return _active_game


def active_dir() -> str:
    return data_dir_for(_active_game)


def set_active_game(name: str) -> None:
    global _active_game
    _active_game = name


def is_valid_game(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and _has_yaml(data_dir_for(name))


def list_games() -> list[str]:
    """Every game folder under /games that holds at least one *.yaml, plus the
    default game (from the /content mount), sorted by name."""
    names = set()
    if os.path.isdir(GAMES_DIR):
        for name in os.listdir(GAMES_DIR):
            if _has_yaml(os.path.join(GAMES_DIR, name, "data")):
                names.add(name)
    if _has_yaml(CONTENT_DIR):
        names.add(default_game())
    return sorted(names)


def all_games_with_data() -> list[tuple[str, str]]:
    """(game_id, data_dir) for every game with content — drives the startup seed loop."""
    return [(name, data_dir_for(name)) for name in list_games()]
