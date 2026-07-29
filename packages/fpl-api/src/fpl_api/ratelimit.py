"""Per-key rate limiting.

Fixed window: a counter per key per minute, incremented on each request and
expiring with the window. Simpler than a sliding window and adequate here —
its one flaw is that a client can send 2x the limit across a window boundary,
which for a read-only data API is not worth the complexity of fixing.

FAILS OPEN. If Redis is unreachable the request is allowed through. That is a
deliberate trade: unlimited requests for the duration of a Redis outage costs
some database load, while failing closed would take the entire API down
because a cache is unavailable. Rate limiting is a protection, not a
correctness requirement, and it should not be able to cause the outage it
exists to prevent.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import redis.asyncio as redis
from fastapi import HTTPException, Request, Response, status
from redis.commands.core import AsyncScript

from fpl_api.auth import ApiKey

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60

_INCREMENT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {current, redis.call('TTL', KEYS[1])}
"""

_client: redis.Redis | None = None
_script: AsyncScript | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    limit: int
    remaining: int
    reset_in: int

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_in),
        }


async def connect() -> None:
    """Open the connection. Called at startup; failure is non-fatal"""
    global _client, _script

    url = os.environ.get("REDIS_URL")
    if not url:
        log.warning("REDIS_URL unset, rate limiting disabled")
        return

    try:
        _client = redis.from_url(url, decode_responses=True)

        await _client.ping()
        _script = _client.register_script(_INCREMENT)
        await _script(keys=["ratelimit:selftest"], args=[1])
        log.info("rate limiting enabled")
    except Exception:
        log.warning("could not reach Redis, rate limiting disabled", exc_info=True)
        _client = None


async def disconnect() -> None:
    global _client, _script
    if _client is not None:
        await _client.aclose()
        _client = None
        _script = None


async def check(key: ApiKey) -> Verdict | None:
    """Consume one request from the key's allowance.

    Return None when rate limiting is unavailable, which
    callers treat as permission to proceed.
    """
    if _client is None or _script is None:
        return None

    window = int(time.time()) // WINDOW_SECONDS
    redis_key = f"ratelimit:{key.id}:{window}"

    try:
        count, ttl = await _script(keys=[redis_key], args=[WINDOW_SECONDS])
    except Exception:
        log.warning("rate limit check failed, allowing request", exc_info=True)
        return None

    return Verdict(
        allowed=count <= key.rate_limit_per_minute,
        limit=key.rate_limit_per_minute,
        remaining=key.rate_limit_per_minute - count,
        reset_in=ttl if ttl > 0 else WINDOW_SECONDS,
    )


async def enforce(request: Request, response: Response, key: ApiKey) -> None:
    """Raise 429 if the allowance is exhausted, otherwise annotate the
    response with the client's remaining budget.

    The headers matter as much as the enforcement: a consumer that can see
    its remaining allowances can back off gracefully, whereas one that only
    learns by being rejected will retry into the wall.
    """
    verdict = await check(key)
    if verdict is None:
        return

    if not verdict.allowed:
        log.info(
            "rate limit exceeded",
            extra={"key_id": key.id, "limit": verdict.limit},
        )

        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit of {verdict.limit} requests per minute exceeded. "
            f"Retry in {verdict.reset_in}s.",
            headers={**verdict.headers(), "Retry-After": str(verdict.reset_in)},
        )

    response.headers.update(verdict.headers())
