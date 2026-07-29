"""Error envelope and request correlation."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_errors_are_problem_documents(client) -> None:
    """RFC 9457, so a consumer parses failures the same way regardless of
    what went wrong."""
    r = await client.get("/v1/teams?season=2026-2027")

    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert {"type", "title", "status", "instance"} <= body.keys()


async def test_every_response_carries_a_request_id(client, pk) -> None:
    r = await client.get("/v1/teams?season=2026-2027", headers={"X-API-Key": pk})
    assert r.headers["X-Request-ID"]


async def test_errors_carry_the_request_id_in_the_body(client) -> None:
    """A user reporting 'request abc123 failed' can then be traced to the log
    line, which is otherwise near-impossible on a busy service."""
    r = await client.get("/v1/teams?season=2026-2027")

    assert r.json()["request_id"] == r.headers["X-Request-ID"]


async def test_inbound_request_id_is_honoured(client, pk) -> None:
    """A caller correlating across their own systems should see their id
    preserved rather than replaced."""
    r = await client.get(
        "/v1/teams?season=2026-2027",
        headers={"X-API-Key": pk, "X-Request-ID": "caller-trace-123"},
    )
    assert r.headers["X-Request-ID"] == "caller-trace-123"


async def test_validation_errors_name_the_field(client, pk) -> None:
    """A 422 without field detail tells a developer nothing about what to
    fix."""
    r = await client.get("/v1/players?season=2026-2027&limit=notanumber", headers={"X-API-Key": pk})

    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "limit"
