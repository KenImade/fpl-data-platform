"""Gameweeks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fpl_api import db
from fpl_api.schemas.gameweeks import Gameweek

router = APIRouter(prefix="/v1/gameweeks", tags=["gameweeks"])

_COLUMNS = """
    season, gameweek, gameweek_name, deadline_utc,
    is_finished, is_data_checked,
    fixture_count, first_kickoff, last_kickoff,
    average_score, highest_score,
    most_selected_player_id, most_captained_player_id, transfers_made,
    has_usable_snapshot, snapshot_at, hours_before_deadline
"""

_FROM = "from analytics_marts.dim_gameweek"


@router.get("", response_model=list[Gameweek])
async def list_gameweeks(
    season: str = Query(..., examples=["2026-2027"]),
) -> list[dict[str, Any]]:
    return await db.fetch(
        f"select {_COLUMNS} {_FROM} where season = $1 order by gameweek",
        season,
    )


@router.get("/current", response_model=Gameweek)
async def current_gameweek(
    season: str = Query(..., examples=["2026-2027"]),
) -> dict[str, Any]:
    """The gameweek in progress: the most recent deadline that has passed.

    Distinct from /next, and both exist simultaneously between a deadline and
    the last whistle. A live-scores view wants this one; a transfer planner
    wants the other.
    """
    row = await db.fetch_one(
        f"select {_COLUMNS} {_FROM} "
        "where season = $1 and deadline_utc <= now() "
        "order by deadline_utc desc limit 1",
        season,
    )
    if row is None:
        raise HTTPException(404, "no gameweek has started in this season")
    return row


@router.get("/next", response_model=Gameweek)
async def next_gameweek(
    season: str = Query(..., examples=["2026-2027"]),
) -> dict[str, Any]:
    """The next deadline. What a transfer planner needs."""
    row = await db.fetch_one(
        f"select {_COLUMNS} {_FROM} "
        "where season = $1 and deadline_utc > now() "
        "order by deadline_utc asc limit 1",
        season,
    )
    if row is None:
        raise HTTPException(404, "season has no remaining deadlines")
    return row


@router.get("/{gameweek}", response_model=Gameweek)
async def get_gameweek(
    gameweek: int,
    season: str = Query(..., examples=["2026-2027"]),
) -> dict[str, Any]:
    row = await db.fetch_one(
        f"select {_COLUMNS} {_FROM} where season = $1 and gameweek = $2",
        season,
        gameweek,
    )
    if row is None:
        raise HTTPException(404, "gameweek not found")
    return row
