"""Database access for the API.

Read-only by construction. The connection string points at the `fpl_api`
role, which holds SELECT on analytics_marts and nothing else - no write
grants anywhere, and no access to bronze, staging, or the Dagster schemas.

That distinction is the reason this module reads API_DATABASE_URL rather
than DATABASE_URL. The latter is the warehouse owner, used by Dagster and
dbt; pointing the API at it would make the role separation decorative.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    """Open the pool. Called once at application startup."""
    global _pool

    url = os.environ["API_DATABASE_URL"]

    _pool = await asyncpg.create_pool(
        url,
        min_size=2,
        max_size=10,
        command_timeout=10,
        statement_cache_size=0,
    )


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised")
    return _pool


async def fetch(query: str, *args: Any) -> list[dict[str, Any]]:
    """Rows as dicts, ready for a Pydantic response model."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(r) for r in rows]


async def fetch_one(query: str, *args: Any) -> dict[str, Any] | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(query, *args)
    return dict(row) if row else None


async def fetch_value(query: str, *args: Any) -> Any:
    async with pool().acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with pool().acquire() as conn:
        return await conn.execute(query, *args)
