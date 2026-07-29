"""Rate limiting.

Tests the decision logic directly rather than by sending 61 requests — that
would be slow, and it would couple the test to the window boundary in a way
that makes it flaky near the top of a minute.
"""

from __future__ import annotations

import pytest
from fpl_api import ratelimit
from fpl_api.auth import ApiKey, KeyType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _key(key_id: int, limit: int = 3) -> ApiKey:
    return ApiKey(
        id=key_id,
        key_type=KeyType.PUBLISHABLE,
        name="pytest",
        allowed_origins=None,
        rate_limit_per_minute=limit,
        last_used_at=None,
    )


@pytest.fixture(autouse=True)
async def _redis():
    await ratelimit.connect()
    if ratelimit._client is None:
        pytest.skip("Redis unavailable")
    yield
    await ratelimit.disconnect()


async def test_allows_within_limit() -> None:
    key = _key(key_id=900_001, limit=3)

    for expected_remaining in (2, 1, 0):
        verdict = await ratelimit.check(key)
        assert verdict is not None
        assert verdict.allowed
        assert verdict.remaining == expected_remaining


async def test_blocks_beyond_limit() -> None:
    key = _key(key_id=900_002, limit=2)

    await ratelimit.check(key)
    await ratelimit.check(key)
    verdict = await ratelimit.check(key)

    assert verdict is not None
    assert not verdict.allowed


async def test_keys_have_independent_budgets() -> None:
    """A noisy client must not exhaust everyone else's allowance."""
    noisy = _key(key_id=900_003, limit=1)
    quiet = _key(key_id=900_004, limit=1)

    await ratelimit.check(noisy)
    exhausted = await ratelimit.check(noisy)
    assert not exhausted.allowed

    unaffected = await ratelimit.check(quiet)
    assert unaffected.allowed


async def test_reset_is_within_the_window() -> None:
    verdict = await ratelimit.check(_key(key_id=900_005))
    assert 0 < verdict.reset_in <= ratelimit.WINDOW_SECONDS


async def test_fails_open_when_redis_is_down() -> None:
    """A cache outage must not take the API with it. Unlimited requests for
    a few minutes is a far cheaper failure than total unavailability."""
    await ratelimit.disconnect()

    verdict = await ratelimit.check(_key(key_id=900_006))

    assert verdict is None  # callers treat None as permission to proceed
