"""Players.

Paginated because a season has 500-850 players and returning all of them
unfiltered is the kind of default that looks fine locally and hurts on a
mobile connection.

Offset pagination rather than cursor. Cursor is better for large or shifting
datasets; this is under a thousand rows per season and stable within one, so
offset is adequate and simpler for consumers to reason about.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fpl_api import db
from fpl_api.deps import authenticated
from fpl_api.schemas.players import Player, PlayerPage

router = APIRouter(
    prefix="/v1/players",
    tags=["players"],
    dependencies=[Depends(authenticated)],
)

_COLUMNS = """
    season, player_id, player_code, team_code, team_name, team_short,
    web_name, full_name, position,
    price, selected_by_percent, status, news,
    chance_of_playing_next, ep_next, state_as_of,
    appearances, minutes, goals, assists, xg, xa, points, bonus
"""

_FROM = "from analytics_marts.dim_player"

# Whitelist, because an ORDER BY cannot be parameterised and interpolating
# user input into one is an injection.
_SORTABLE = {
    "web_name": "web_name",
    "price": "price",
    "points": "points",
    "minutes": "minutes",
    "goals": "goals",
    "assists": "assists",
    "selected_by_percent": "selected_by_percent",
    "ep_next": "ep_next",
}


@router.get("", response_model=PlayerPage)
async def list_players(
    season: str = Query(..., examples=["2026-2027"]),
    team: int | None = Query(None, description="team_code, not team_id"),
    position: Literal["GKP", "DEF", "MID", "FWD"] | None = None,
    sort: str = Query("points", examples=["points"]),
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    if sort not in _SORTABLE:
        raise HTTPException(422, f"sort must be one of: {', '.join(sorted(_SORTABLE))}")

    where = ["season = $1"]
    params: list[Any] = [season]

    if team is not None:
        params.append(team)
        where.append(f"team_code = ${len(params)}")

    if position is not None:
        params.append(position)
        where.append(f"position = ${len(params)}")

    clause = " and ".join(where)
    direction = "desc" if order == "desc" else "asc"

    total = await db.fetch_value(f"select count(*) {_FROM} where {clause}", *params)

    # nulls last regardless of direction: a player with no price sorts to the
    # end either way, rather than leading a descending list.
    items = await db.fetch(
        f"select {_COLUMNS} {_FROM} where {clause} "
        f"order by {_SORTABLE[sort]} {direction} nulls last, player_id "
        f"limit ${len(params) + 1} offset ${len(params) + 2}",
        *params,
        limit,
        offset,
    )

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{player_id}", response_model=Player)
async def get_player(
    player_id: int,
    season: str = Query(..., examples=["2026-2027"]),
) -> dict[str, Any]:
    """By season-scoped player_id.

    Note this is NOT stable across seasons — FPL reassigns element IDs each
    August, and id 3 has belonged to three different people in three seasons.
    For a player's history, resolve player_code first.
    """
    row = await db.fetch_one(
        f"select {_COLUMNS} {_FROM} where season = $1 and player_id = $2",
        season,
        player_id,
    )
    if row is None:
        raise HTTPException(404, "player not found")
    return row
