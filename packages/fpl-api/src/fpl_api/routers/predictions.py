"""Predictions.

Model output rather than recorded fact, and the schema field descriptions say
so throughout. Two grains:

    /v1/predictions/gameweek/{gameweek}   one row per player  (the default)
    /v1/predictions/fixtures              one row per fixture (double gameweeks
                                          split out)

Gameweek grain is the default because a caller asking how many points someone
scores in GW26 wants one answer. Serving fixture grain by default would make
every consumer implement the aggregation, and most would forget — producing
tools that silently halve a projection in exactly the gameweeks where it
matters most.

ONE MODEL VERSION PER RESPONSE. The marts serve whichever version is active,
and it is returned alongside the items so a consumer can cache against it and
notice when it changes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fpl_api import db
from fpl_api.deps import authenticated
from fpl_api.schemas.predictions import (
    PlayerFixturePrediction,
    PlayerGameweekPrediction,
    PredictionPage,
)

router = APIRouter(
    prefix="/v1/predictions",
    tags=["predictions"],
    dependencies=[Depends(authenticated)],
)

_GW_COLUMNS = """
    season, gameweek, player_id, player_code,
    web_name, full_name, position, team_code, team_name, team_short, price,
    fixtures_in_gw, is_double_gw, opponents, avg_elo_diff,
    first_kickoff, last_kickoff,
    p_minutes_60, e_minutes,
    e_goals, e_assists, p_clean_sheet, e_saves, e_goals_conceded,
    p_defcon, e_bonus, e_cards, e_points,
    prior_appearances, is_cold_start,
    snapshot_id, model_version, predicted_at
"""

_GW_FROM = "from analytics_marts.mart_player_gameweek_predictions"

_FIXTURE_COLUMNS = """
    season, gameweek, player_id, player_code, match_id,
    web_name, position, team_code, team_name, price,
    opponent_code, opponent_name, is_home, kickoff_utc, elo_diff,
    p_minutes_0, p_minutes_1_59, p_minutes_60, e_minutes,
    e_goals, e_assists, p_clean_sheet, e_saves, e_goals_conceded,
    p_defcon, e_bonus, e_cards, e_points,
    prior_appearances, is_cold_start,
    snapshot_id, model_version, predicted_at
"""

_FIXTURE_FROM = "from analytics_marts.mart_player_fixture_predictions"

# Whitelist, because an ORDER BY cannot be parameterised and interpolating
# user input into one is an injection.
#
# e_points leads the list even though it is currently null everywhere: it is
# what callers will reach for once the remaining components exist, and sorting
# by it now returns a stable null-last ordering rather than a 422.
_SORTABLE = {
    "e_points": "e_points",
    "p_minutes_60": "p_minutes_60",
    "e_minutes": "e_minutes",
    "price": "price",
    "web_name": "web_name",
    "avg_elo_diff": "avg_elo_diff",
}


@router.get("/gameweek/{gameweek}", response_model=PredictionPage)
async def gameweek_predictions(
    gameweek: int,
    season: str = Query(..., examples=["2026-2027"]),
    team: int | None = Query(None, description="team_code, not team_id"),
    position: Literal["GKP", "DEF", "MID", "FWD"] | None = None,
    max_price: float | None = Query(
        None,
        description="Upper bound on current price in millions.",
        examples=[10.0],
    ),
    exclude_cold_start: bool = Query(
        False,
        description=(
            "Drop players with no prior league appearances this season. Their "
            "predictions rest on a positional prior rather than their own "
            "history, and an optimiser is usually better off without them."
        ),
    ),
    sort: str = Query("p_minutes_60", examples=["p_minutes_60"]),
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Predictions for one gameweek, one row per player.

    A double gameweek is aggregated into a single row — see the schema for how
    expectations and probabilities combine differently.

    Returns an empty page rather than a 404 for a gameweek with no predictions.
    That is a real state rather than an error: predictions only exist once the
    deadline is close enough for a point-in-time snapshot to be usable, which
    is roughly a week out.
    """
    if sort not in _SORTABLE:
        raise HTTPException(422, f"sort must be one of: {', '.join(sorted(_SORTABLE))}")

    where = ["season = $1", "gameweek = $2"]
    params: list[Any] = [season, gameweek]

    if team is not None:
        params.append(team)
        where.append(f"team_code = ${len(params)}")

    if position is not None:
        params.append(position)
        where.append(f"position = ${len(params)}")

    if max_price is not None:
        params.append(max_price)
        where.append(f"price <= ${len(params)}")

    if exclude_cold_start:
        where.append("not is_cold_start")

    clause = " and ".join(where)
    direction = "desc" if order == "desc" else "asc"

    total = await db.fetch_value(f"select count(*) {_GW_FROM} where {clause}", *params)

    # nulls last regardless of direction, so sorting by a component that has no
    # model yet returns a usable ordering rather than a page of nulls.
    items = await db.fetch(
        f"select {_GW_COLUMNS} {_GW_FROM} where {clause} "
        f"order by {_SORTABLE[sort]} {direction} nulls last, player_id "
        f"limit ${len(params) + 1} offset ${len(params) + 2}",
        *params,
        limit,
        offset,
    )

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        # One version per response by construction — the mart serves whichever
        # is active — so taking it from the first row is safe and saves a
        # second query.
        "model_version": items[0]["model_version"] if items else None,
    }


@router.get("/player/{player_id}", response_model=list[PlayerGameweekPrediction])
async def player_predictions(
    player_id: int,
    season: str = Query(..., examples=["2026-2027"]),
    gameweeks: Annotated[int, Query(ge=1, le=38)] = 6,
) -> list[dict[str, Any]]:
    """One player across upcoming gameweeks.

    Defaults to six, which is the usual planning horizon for a wildcard or a
    fixture swing. Later gameweeks are predicted from features that will be
    stale by the time they are played — team news in particular — so treat the
    far end as a fixture-difficulty guide rather than a forecast.

    Returns an empty list rather than a 404 when a player has no predictions;
    a player who exists but has no upcoming fixture is not an error.
    """
    return await db.fetch(
        f"select {_GW_COLUMNS} {_GW_FROM} "
        "where season = $1 and player_id = $2 "
        "order by gameweek limit $3",
        season,
        player_id,
        gameweeks,
    )


@router.get("/fixtures", response_model=list[PlayerFixturePrediction])
async def fixture_predictions(
    season: str = Query(..., examples=["2026-2027"]),
    gameweek: int = Query(..., ge=1, le=38),
    team: int | None = Query(None, description="team_code, not team_id"),
    player_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    """Predictions split by fixture.

    Use this when a double gameweek's fixtures need treating separately — a
    home tie against a promoted side and an away tie against the leaders are
    very different propositions, and the gameweek total hides that.

    Also the place to get per-fixture probabilities unaggregated. The gameweek
    endpoint's `p_minutes_60` combines fixtures under an independence
    assumption that slightly overstates; these are the raw figures.
    """
    where = ["season = $1", "gameweek = $2"]
    params: list[Any] = [season, gameweek]

    if team is not None:
        params.append(team)
        where.append(f"team_code = ${len(params)}")

    if player_id is not None:
        params.append(player_id)
        where.append(f"player_id = ${len(params)}")

    clause = " and ".join(where)

    return await db.fetch(
        f"select {_FIXTURE_COLUMNS} {_FIXTURE_FROM} where {clause} "
        f"order by kickoff_utc, p_minutes_60 desc nulls last, player_id "
        f"limit ${len(params) + 1}",
        *params,
        limit,
    )
