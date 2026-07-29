"""Authentication.

The highest-value tests in the suite, because every failure mode here is
silent. An API that stops rejecting invalid keys returns 200s and looks
perfectly healthy.
"""

from __future__ import annotations

import pytest
from fpl_api.auth import KeyType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ENDPOINT = "/v1/teams?season=2026-2027"


async def test_no_key_is_rejected(client) -> None:
    r = await client.get(ENDPOINT)
    assert r.status_code == 401


async def test_invalid_key_is_rejected(client) -> None:
    r = await client.get(ENDPOINT, headers={"X-API-Key": "pk_not_a_real_key"})
    assert r.status_code == 401


async def test_revoked_key_is_rejected(client, make_key) -> None:
    key = await make_key(KeyType.PUBLISHABLE, revoked=True)
    r = await client.get(ENDPOINT, headers={"X-API-Key": key})
    assert r.status_code == 401


async def test_missing_and_invalid_are_indistinguishable(client) -> None:
    """Different messages would tell an attacker which half to work on:
    whether a key exists at all, or whether theirs is merely wrong."""
    missing = await client.get(ENDPOINT)
    invalid = await client.get(ENDPOINT, headers={"X-API-Key": "pk_wrong"})

    assert missing.status_code == invalid.status_code == 401
    assert "WWW-Authenticate" in missing.headers
    assert "WWW-Authenticate" in invalid.headers


async def test_publishable_key_is_accepted(client, pk) -> None:
    r = await client.get(ENDPOINT, headers={"X-API-Key": pk})
    assert r.status_code == 200


async def test_bearer_header_works(client, pk) -> None:
    r = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {pk}"})
    assert r.status_code == 200


async def test_bearer_is_case_insensitive(client, pk) -> None:
    r = await client.get(ENDPOINT, headers={"Authorization": f"bearer {pk}"})
    assert r.status_code == 200


async def test_secret_key_works_server_side(client, sk) -> None:
    """No Origin header — the shape of a server-to-server call."""
    r = await client.get(ENDPOINT, headers={"X-API-Key": sk})
    assert r.status_code == 200


async def test_secret_key_from_browser_is_rejected(client, sk) -> None:
    """A secret key arriving with an Origin header is in frontend code, and
    therefore already public. Failing loudly costs a developer an afternoon;
    failing silently costs them their key.

    This only ever fires on someone else's mistake, which is exactly why it
    needs a test — nothing in normal use would reveal a regression.
    """
    r = await client.get(
        ENDPOINT,
        headers={"X-API-Key": sk, "Origin": "https://example.com"},
    )

    assert r.status_code == 403
    assert "client-side" in r.json()["title"]


async def test_secret_key_rejection_explains_the_fix(client, sk) -> None:
    """An error that says only 'forbidden' leaves the developer guessing."""
    r = await client.get(ENDPOINT, headers={"X-API-Key": sk, "Origin": "https://example.com"})
    assert "pk_" in r.json()["title"]


async def test_publishable_key_honours_allowlist(client, make_key) -> None:
    key = await make_key(KeyType.PUBLISHABLE, origins=["https://allowed.example"])

    ok = await client.get(ENDPOINT, headers={"X-API-Key": key, "Origin": "https://allowed.example"})
    assert ok.status_code == 200

    blocked = await client.get(
        ENDPOINT, headers={"X-API-Key": key, "Origin": "https://evil.example"}
    )
    assert blocked.status_code == 403


async def test_allowlist_blocks_missing_origin(client, make_key) -> None:
    """A key restricted to an origin should not work without one — otherwise
    the restriction is trivially bypassed by omitting the header."""
    key = await make_key(KeyType.PUBLISHABLE, origins=["https://allowed.example"])
    r = await client.get(ENDPOINT, headers={"X-API-Key": key})
    assert r.status_code == 403


async def test_no_allowlist_permits_any_origin(client, pk) -> None:
    r = await client.get(ENDPOINT, headers={"X-API-Key": pk, "Origin": "https://anywhere.example"})
    assert r.status_code == 200


async def test_health_needs_no_key(client) -> None:
    """A load balancer cannot authenticate."""
    r = await client.get("/health")
    assert r.status_code == 200
