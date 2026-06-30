"""CLI for the authoring pipeline.

  python -m app.seed          validate + load content into Postgres
  python -m app.seed --lint   validate only (no DB) — incl. spine reachability
"""
import asyncio
import sys

from . import content, db, gamestate


async def run(lint_only: bool) -> int:
    games = gamestate.all_games_with_data()
    total_errors = 0
    loaded = []   # (game_id, data) for the seeding pass
    for game_id, data_dir in games:
        data, files = content.load_dir(data_dir)
        errors, warnings = content.validate(data)
        for w in warnings:
            print(f"WARN  [{game_id}] {w}")
        for e in errors:
            print(f"ERROR [{game_id}] {e}")
        print(f"[{game_id}] {len(files)} file(s): "
              f"{len(data['nodes'])} nodes, {len(data['gates'])} gates, "
              f"{len(data['puzzles'])} puzzles, {len(data['clues'])} clues, "
              f"{len(data['characters'])} characters.")
        total_errors += len(errors)
        if not errors:
            loaded.append((game_id, data))

    if total_errors:
        print(f"{total_errors} error(s) across {len(games)} game(s) — not seeding.")
        return 1
    if lint_only:
        print("Lint OK — every game's spine is completable, no traps, no broken references.")
        return 0

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        for i, (game_id, data) in enumerate(loaded):
            await content.register_game(conn, game_id, data.get("game", {}), sort_order=i)
            skipped = await content.seed_content(conn, data, game_id)
            for s in skipped or []:
                print(f"KEPT  [{game_id}] {s}")
            print(f"Seeded {game_id}." +
                  (f" ({len(skipped)} row(s) kept — in use by players)" if skipped else ""))
    await db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(lint_only="--lint" in sys.argv)))
