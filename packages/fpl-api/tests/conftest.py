"""Shared fixtures.

These are integration tests against the local Compose Postgres, not unit
tests against a mocked db layer. That is deliberate: the thing worth testing
here is the SQL and the permissions, and mocking db.fetch would only assert
that routers call a function.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fpl_api import db
from fpl_api.auth import KeyType, generate
from fpl_api.main import app
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

SEASON = "2026-2027"


def _skip_without_db() -> None:
    if not os.environ.get("API_DATABASE_URL"):
        pytest.skip("API_DATABASE_URL unset")


@pytest_asyncio.fixture(scope="session")
async def _pool() -> AsyncIterator[None]:
    _skip_without_db()
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


@pytest_asyncio.fixture
async def client(_pool: None) -> AsyncIterator[AsyncClient]:
    """The app, without running its lifespan — the pool is already open.

    Running lifespan per test would open and close a pool for every case,
    which is slow and occasionally races on teardown.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def make_key(_pool: None) -> AsyncIterator:
    """Create API keys, and remove them afterwards.

    Cleanup runs even on failure — a leaked test key in a shared database
    would still authenticate, which is a small security hole and a confusing
    one to track down.
    """
    created: list[int] = []

    async def _make(
        key_type: KeyType = KeyType.PUBLISHABLE,
        *,
        origins: list[str] | None = None,
        revoked: bool = False,
    ) -> str:
        raw, key_hash, prefix = generate(key_type)
        row = await db.fetch_one(
            """
            insert into app.api_key
                (key_hash, key_prefix, key_type, name, allowed_origins, revoked_at)
            values ($1, $2, $3, $4, $5, case when $6 then now() end)
            returning id
            """,
            key_hash,
            prefix,
            key_type.value,
            "pytest",
            origins,
            revoked,
        )
        created.append(row["id"])
        return raw

    yield _make

    for key_id in created:
        await db.execute("delete from app.api_key where id = $1", key_id)


@pytest_asyncio.fixture
async def pk(make_key) -> str:
    return await make_key(KeyType.PUBLISHABLE)


@pytest_asyncio.fixture
async def sk(make_key) -> str:
    return await make_key(KeyType.SECRET)
