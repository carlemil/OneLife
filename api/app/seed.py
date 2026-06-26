"""CLI for the authoring pipeline.

  python -m app.seed          validate + load content into Postgres
  python -m app.seed --lint   validate only (no DB) — incl. spine reachability
"""
import asyncio
import sys

from . import content, db


async def run(lint_only: bool) -> int:
    data, files = content.load_dir()
    errors, warnings = content.validate(data)

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"Loaded {len(files)} file(s): "
          f"{len(data['nodes'])} nodes, {len(data['gates'])} gates, "
          f"{len(data['puzzles'])} puzzles, {len(data['clues'])} clues, "
          f"{len(data['characters'])} characters.")

    if errors:
        print(f"{len(errors)} error(s) — not seeding.")
        return 1
    if lint_only:
        print("Lint OK — spine is completable, no traps, no broken references.")
        return 0

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        skipped = await content.seed_content(conn, data)
    await db.close_pool()
    for s in skipped or []:
        print(f"KEPT  {s}")
    print("Seeded OK." + (f" ({len(skipped)} row(s) kept — in use by players)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(lint_only="--lint" in sys.argv)))
