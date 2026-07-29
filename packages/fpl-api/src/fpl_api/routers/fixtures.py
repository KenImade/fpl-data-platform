"""Fixtures.

fct_team_fixture is grained per TEAM per match, so two rows per fixture.
Filtering to is_home collapses that to one row per match with the natural
home/away orientation.

That filter costs European away ties where the home side is not a Premier
League club — those rows were dropped upstream, so the fixture has no
is_home row. Acceptable here because this endpoint serves league fixtures;
a team's full schedule across competitions belongs on a team-scoped endpoint
where the orientation doesn't matter.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fpl_api import db
from fpl_api.deps import authenticated
from fpl_api.schemas.fixtures import Fixture

router = APIRouter(
    prefix="/v1/fixtures",
    tags=["fixtures"],
    dependencies=[Depends(authenticated)],
)

_SELECT = """
    select
        match_id, season, gameweek, competition, kickoff_utc,
        team_code        as home_team_code,
        team_name        as home_team_name,
        opponent_code    as away_team_code,
        opponent_name    as away_team_name,
        goals_for        as home_score,
        goals_against    as away_score,
        elo              as home_elo,
        opponent_elo     as away_elo
    from analytics_marts.fct_team_fixture
"""


@router.get("", response_model=list[Fixture])
async def list_fixtures(
    season: str = Query(..., examples=["2026-2027"]),
    gameweek: int | None = None,
    team: int | None = Query(None, description="team_code; matches either side"),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    where = ["season = $1", "is_home", "is_league"]
    params: list[Any] = [season]

    if gameweek is not None:
        params.append(gameweek)
        where.append(f"gameweek = ${len(params)}")

    if team is not None:
        params.append(team)
        where.append(f"(team_code = ${len(params)} or opponent_code = ${len(params)})")

    return await db.fetch(
        f"{_SELECT} where {' and '.join(where)} "
        "order by kickoff_utc nulls last, match_id "
        f"limit ${len(params) + 1}",
        *params,
        limit,
    )


@router.get("/{match_id}", response_model=Fixture)
async def get_fixture(match_id: str) -> dict[str, Any]:
    """match_id is a slug: 25-26-prem-everton-vs-liverpool.

    Globally unique — it encodes season and competition — so no season
    parameter is needed.
    """
    row = await db.fetch_one(f"{_SELECT} where match_id = $1 and is_home", match_id)
    if row is None:
        raise HTTPException(404, "fixture not found")
    return row
