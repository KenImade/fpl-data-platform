"""Endpoint behaviour against real warehouse data."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SEASON = "2026-2027"


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------


async def test_list_teams(client, pk) -> None:
    r = await client.get(f"/v1/teams?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200

    teams = r.json()
    assert len(teams) == 20
    assert all(t["season"] == SEASON for t in teams)
    # Ordered by name, so a stable first element rather than whatever the
    # planner returned.
    assert teams == sorted(teams, key=lambda t: t["team_name"])


async def test_get_team(client, pk) -> None:
    r = await client.get(f"/v1/teams/3?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200
    assert r.json()["team_short"] == "ARS"


async def test_unknown_team_is_404(client, pk) -> None:
    r = await client.get(f"/v1/teams/9999?season={SEASON}", headers=auth(pk))
    assert r.status_code == 404


async def test_season_is_required(client, pk) -> None:
    """Not defaulted. A club's strength, id and even name are season-scoped,
    so answering for a season the caller didn't name is worse than asking."""
    r = await client.get("/v1/teams", headers=auth(pk))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# gameweeks
# ---------------------------------------------------------------------------


async def test_list_gameweeks(client, pk) -> None:
    r = await client.get(f"/v1/gameweeks?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200

    gws = r.json()
    assert len(gws) == 38
    assert [g["gameweek"] for g in gws] == list(range(1, 39))


async def test_next_gameweek(client, pk) -> None:
    r = await client.get(f"/v1/gameweeks/next?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200
    assert r.json()["deadline_utc"] is not None


async def test_gameweek_reports_snapshot_state(client, pk) -> None:
    """has_usable_snapshot tells a consumer whether point-in-time data exists
    for a deadline. Before a gameweek it is false; after one, false means the
    capture sensor missed and features for it are unreliable."""
    r = await client.get(f"/v1/gameweeks/1?season={SEASON}", headers=auth(pk))
    assert "has_usable_snapshot" in r.json()


# ---------------------------------------------------------------------------
# players
# ---------------------------------------------------------------------------


async def test_list_players_is_paginated(client, pk) -> None:
    r = await client.get(f"/v1/players?season={SEASON}&limit=5", headers=auth(pk))
    assert r.status_code == 200

    body = r.json()
    assert len(body["items"]) == 5
    assert body["limit"] == 5
    assert body["total"] > 5


async def test_player_filters_combine(client, pk) -> None:
    r = await client.get(f"/v1/players?season={SEASON}&position=GKP&team=3", headers=auth(pk))
    items = r.json()["items"]
    assert items
    assert all(p["position"] == "GKP" and p["team_code"] == 3 for p in items)


async def test_sort_whitelist_rejects_injection(client, pk) -> None:
    """ORDER BY cannot be parameterised, so an unvalidated sort column is an
    injection. The whitelist must reject anything not on it."""
    r = await client.get(
        f"/v1/players?season={SEASON}&sort=name;drop table app.api_key",
        headers=auth(pk),
    )
    assert r.status_code == 422
    assert "sort must be one of" in r.json()["title"]


async def test_api_key_table_survived_that(client, pk) -> None:
    """Paranoid, but the cost of being wrong is the whole auth system."""
    r = await client.get(f"/v1/teams?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200


async def test_limit_is_capped(client, pk) -> None:
    r = await client.get(f"/v1/players?season={SEASON}&limit=5000", headers=auth(pk))
    assert r.status_code == 422


async def test_unknown_player_is_404(client, pk) -> None:
    r = await client.get(f"/v1/players/999999?season={SEASON}", headers=auth(pk))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


async def test_list_fixtures_is_match_grain(client, pk) -> None:
    """fct_team_fixture holds two rows per match. The endpoint must collapse
    that to one, or every fixture appears twice with the sides swapped."""
    r = await client.get(f"/v1/fixtures?season={SEASON}&gameweek=1", headers=auth(pk))
    assert r.status_code == 200

    fixtures = r.json()
    assert len(fixtures) == 10
    assert len({f["match_id"] for f in fixtures}) == 10


async def test_fixture_team_filter_matches_either_side(client, pk) -> None:
    r = await client.get(f"/v1/fixtures?season={SEASON}&team=3", headers=auth(pk))
    fixtures = r.json()
    assert fixtures
    assert all(f["home_team_code"] == 3 or f["away_team_code"] == 3 for f in fixtures)
