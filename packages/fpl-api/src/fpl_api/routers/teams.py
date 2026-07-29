"""Teams.

Read analytics_marts. The `season` parameter is required rather than
defaulting to current: a club's identity, strength ratings and even
name are season-scoped, and silently answering for a season the caller
didn't ask about is worse than making them say.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fpl_api import db
from fpl_api.deps import authenticated
from fpl_api.schemas.teams import Team

router = APIRouter(
    prefix="/v1/teams",
    tags=["teams"],
    dependencies=[Depends(authenticated)],
)

_SELECT = """
    SELECT
        team_code, team_id, team_name, team_short,
        strength, strength_overall_home, strength_overall_away,
        strength_attack_home, strength_attack_away,
        strength_defence_home, strength_defence_away,
        season
    from analytics_marts.dim_team
"""


@router.get("", response_model=list[Team])
async def list_teams(season: str = Query(..., examples=["2026-2027"])) -> list[dict[str, Any]]:
    return await db.fetch(f"{_SELECT} WHERE season = $1 order by team_name", season)


@router.get("/{team_code}", response_model=Team)
async def get_team(team_code: int, season: str = Query(...)) -> dict[str, Any]:
    row = await db.fetch_one(f"{_SELECT} WHERE season = $1 AND team_code = $2", season, team_code)
    if row is None:
        raise HTTPException(status_code=404, detail="team not found")
    return row
