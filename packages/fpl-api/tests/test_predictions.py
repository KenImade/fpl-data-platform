"""Prediction endpoints, against real warehouse data.

WRITTEN TO PASS WITH NO PREDICTIONS. Predictions exist only once a deadline is
close enough for a point-in-time snapshot to be usable — roughly a week out —
so for most of the year these tables are legitimately empty. A test suite that
required rows would fail every summer and be ignored by August.

So the shape and contract assertions run unconditionally, and anything needing
actual predictions skips with a reason. The skips are the signal: if they are
still skipping in October, predictions have stopped being written and that is
worth knowing.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SEASON = "2026-2027"


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def _any_predictions(client, key: str) -> list[dict]:
    """Predictions for the earliest gameweek that has any.

    Scans rather than assuming gameweek 1, because by mid-season the early
    gameweeks have been played and dropped out of the prediction frame.
    """
    for gw in range(1, 39):
        r = await client.get(
            f"/v1/predictions/gameweek/{gw}?season={SEASON}&limit=200",
            headers=auth(key),
        )
        if r.status_code == 200 and r.json()["items"]:
            return r.json()["items"]
    return []


# ---------------------------------------------------------------------------
# contract — these hold whether or not predictions exist
# ---------------------------------------------------------------------------


async def test_gameweek_predictions_requires_season(client, pk) -> None:
    """Not defaulted, for the same reason the other endpoints don't default it:
    answering for a season the caller didn't name is worse than asking."""
    r = await client.get("/v1/predictions/gameweek/1", headers=auth(pk))
    assert r.status_code == 422


async def test_gameweek_predictions_requires_auth(client) -> None:
    r = await client.get(f"/v1/predictions/gameweek/1?season={SEASON}")
    assert r.status_code == 401


async def test_empty_gameweek_is_a_page_not_a_404(client, pk) -> None:
    """A gameweek with no predictions is a real state, not an error.

    Predictions appear only once the deadline is within about a week, so a
    consumer polling GW20 in August should get an empty page telling them the
    data is not ready — not a 404 suggesting the endpoint is wrong.
    """
    r = await client.get(f"/v1/predictions/gameweek/38?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200

    body = r.json()
    assert body["total"] >= 0
    assert isinstance(body["items"], list)


async def test_unsortable_field_is_422(client, pk) -> None:
    """Sort columns are whitelisted because an ORDER BY cannot be
    parameterised. The rejection is what stops the interpolation being an
    injection."""
    r = await client.get(
        f"/v1/predictions/gameweek/1?season={SEASON}&sort=price;drop+table",
        headers=auth(pk),
    )
    assert r.status_code == 422


async def test_e_points_is_sortable_before_it_exists(client, pk) -> None:
    """Sorting by a component with no model yet returns a null-last ordering
    rather than an error.

    Deliberate: a consumer can build against e_points now and keep working when
    the goals and bonus models land, rather than having their code start
    422-ing the day the field becomes real.
    """
    r = await client.get(
        f"/v1/predictions/gameweek/1?season={SEASON}&sort=e_points",
        headers=auth(pk),
    )
    assert r.status_code == 200


async def test_limit_is_bounded(client, pk) -> None:
    r = await client.get(f"/v1/predictions/gameweek/1?season={SEASON}&limit=500", headers=auth(pk))
    assert r.status_code == 422


async def test_unknown_player_returns_empty_list(client, pk) -> None:
    """Not a 404. A player who exists but has no upcoming fixture is not an
    error, and distinguishing that from a bad id would need a second query for
    no benefit to the caller."""
    r = await client.get(f"/v1/predictions/player/999999?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# behaviour — skipped when nothing has been scored yet
# ---------------------------------------------------------------------------


async def test_predictions_carry_provenance(client, pk) -> None:
    """snapshot_id and model_version together must identify the prediction.

    Without both, a wrong number cannot be reproduced — which makes a bug
    report unanswerable and the point-in-time layer decorative.
    """
    items = await _any_predictions(client, pk)
    if not items:
        pytest.skip("no predictions scored yet")

    for item in items:
        assert item["snapshot_id"]
        assert item["model_version"]
        assert item["predicted_at"]


async def test_one_model_version_per_page(client, pk) -> None:
    """A page never mixes versions, so a consumer can cache against the one
    returned alongside the items."""
    r = await client.get(f"/v1/predictions/gameweek/1?season={SEASON}&limit=200", headers=auth(pk))
    body = r.json()
    if not body["items"]:
        pytest.skip("no predictions scored yet")

    versions = {i["model_version"] for i in body["items"]}
    assert len(versions) == 1
    assert body["model_version"] == versions.pop()


async def test_probabilities_are_probabilities(client, pk) -> None:
    items = await _any_predictions(client, pk)
    if not items:
        pytest.skip("no predictions scored yet")

    for item in items:
        assert 0.0 <= item["p_minutes_60"] <= 1.0
        # Summed across fixtures, so a double gameweek exceeds 90 legitimately.
        assert 0.0 <= item["e_minutes"] <= 180.0


async def test_fixture_probabilities_sum_to_one(client, pk) -> None:
    """The three bands partition the outcome space.

    This is what the ordinal decomposition guarantees after clipping and
    renormalising — if it fails, the two stages have crossed and the
    correction is not being applied.
    """
    for gw in range(1, 39):
        r = await client.get(
            f"/v1/predictions/fixtures?season={SEASON}&gameweek={gw}",
            headers=auth(pk),
        )
        items = r.json()
        if items:
            break
    else:
        pytest.skip("no predictions scored yet")

    for item in items:
        total = item["p_minutes_0"] + item["p_minutes_1_59"] + item["p_minutes_60"]
        assert abs(total - 1.0) < 1e-6


async def test_double_gameweek_probability_exceeds_either_fixture(client, pk) -> None:
    """The aggregation's defining property.

    P(60+ in at least one) is computed as 1 - prod(1 - p), so a double gameweek
    must come out strictly higher than either fixture alone. A range check
    would not catch the aggregation being wrong; this does.
    """
    items = await _any_predictions(client, pk)
    doubles = [i for i in items if i["is_double_gw"]] if items else []
    if not doubles:
        pytest.skip("no double gameweek in the scored range")

    player = doubles[0]
    r = await client.get(
        f"/v1/predictions/fixtures?season={SEASON}"
        f"&gameweek={player['gameweek']}&player_id={player['player_id']}",
        headers=auth(pk),
    )
    fixtures = r.json()
    assert len(fixtures) == 2

    assert player["p_minutes_60"] > max(f["p_minutes_60"] for f in fixtures)
    assert player["p_minutes_60"] <= 1.0


async def test_gameweek_grain_collapses_fixtures(client, pk) -> None:
    """One row per player at gameweek grain, however many fixtures they play.

    The whole reason this endpoint is the default: a consumer who got two rows
    for one player and summed neither would silently halve a projection.
    """
    items = await _any_predictions(client, pk)
    if not items:
        pytest.skip("no predictions scored yet")

    ids = [i["player_id"] for i in items]
    assert len(ids) == len(set(ids))


async def test_cold_start_filter_removes_them(client, pk) -> None:
    """Cold-start predictions rest on a positional prior rather than the
    player's own history, and an optimiser is usually better off without."""
    r = await client.get(
        f"/v1/predictions/gameweek/1?season={SEASON}&exclude_cold_start=true&limit=200",
        headers=auth(pk),
    )
    items = r.json()["items"]
    if not items:
        pytest.skip("no non-cold-start predictions yet")

    assert not any(i["is_cold_start"] for i in items)


async def test_unmodelled_components_are_null_not_zero(client, pk) -> None:
    """A null means no model exists for that component — not a prediction of
    zero.

    The distinction matters: a consumer summing components would otherwise
    treat "we haven't built the goals model" as "this striker scores nothing".
    When the goals model lands this test should be updated, not deleted.
    """
    items = await _any_predictions(client, pk)
    if not items:
        pytest.skip("no predictions scored yet")

    assert all(i["e_goals"] is None for i in items)
    assert all(i["e_points"] is None for i in items)


async def test_every_prediction_has_a_player(client, pk) -> None:
    """The marts join dim_player for display fields, so a player missing from
    that dimension drops silently rather than erroring.

    Worth asserting on a public endpoint: a disappeared player is a much
    quieter failure than a wrong number.
    """
    items = await _any_predictions(client, pk)
    if not items:
        pytest.skip("no predictions scored yet")

    for item in items:
        assert item["web_name"]
        assert item["position"] in {"GKP", "DEF", "MID", "FWD"}


async def test_player_horizon_is_bounded(client, pk) -> None:
    """Defaults to six gameweeks — the usual wildcard planning horizon.

    Later gameweeks are predicted from features that will be stale by the time
    they are played, so the far end is a fixture-difficulty guide rather than a
    forecast.
    """
    items = await _any_predictions(client, pk)
    if not items:
        pytest.skip("no predictions scored yet")

    player_id = items[0]["player_id"]
    r = await client.get(f"/v1/predictions/player/{player_id}?season={SEASON}", headers=auth(pk))
    assert r.status_code == 200

    rows = r.json()
    assert len(rows) <= 6
    assert [x["gameweek"] for x in rows] == sorted(x["gameweek"] for x in rows)
