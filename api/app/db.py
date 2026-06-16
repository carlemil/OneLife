"""Async Postgres pool + small helpers."""
import os
import asyncio
import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        # Postgres may still be coming up on first boot; retry briefly.
        last = None
        for _ in range(30):
            try:
                _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
                break
            except Exception as e:  # noqa: BLE001
                last = e
                await asyncio.sleep(1)
        if _pool is None:
            raise RuntimeError(f"could not connect to database: {last}")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
