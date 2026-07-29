"""CORS.

Worth testing because the failure mode is invisible server-side: every
request succeeds, and only the browser refuses to hand the response to the
JavaScript that asked for it.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ORIGIN = "https://app.example"
ENDPOINT = "/v1/teams?season=2026-2027"


async def test_preflight_is_answered(client) -> None:
    """A browser sends OPTIONS before any request carrying an auth header.
    If that fails the real request is never sent at all."""
    r = await client.options(
        "/v1/teams",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )

    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"]
    assert "x-api-key" in r.headers["access-control-allow-headers"].lower()


async def test_preflight_is_cacheable(client) -> None:
    """Without max-age a browser preflights every request, doubling round
    trips on a mobile connection."""
    r = await client.options(
        "/v1/teams",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert int(r.headers["access-control-max-age"]) > 0


async def test_rate_limit_headers_are_readable_by_browsers(client, pk) -> None:
    """Non-simple response headers are hidden from JavaScript unless exposed.

    A client that cannot read its remaining budget cannot back off — it only
    learns the limit exists by being rejected, then retries into the wall.
    """
    r = await client.get(ENDPOINT, headers={"X-API-Key": pk, "Origin": ORIGIN})

    exposed = r.headers.get("access-control-expose-headers", "").lower()
    assert "x-ratelimit-remaining" in exposed
    assert "x-request-id" in exposed


async def test_errors_carry_cors_headers(client) -> None:
    """The reason CORS is the outermost middleware.

    If an error response lacks them, the browser reports a CORS failure and
    the developer debugs the wrong problem — the actual 401 is never visible
    to their code.
    """
    r = await client.get(ENDPOINT, headers={"Origin": ORIGIN})

    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"]
