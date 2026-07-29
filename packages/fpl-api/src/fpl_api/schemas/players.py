from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Player(BaseModel):
    season: str
    player_id: int
    # Stable across seasons, unlike player_id which FPL reassigns each
    # August. Use this for anything historical.
    player_code: int
    team_code: int | None = None
    team_name: str | None = None
    team_short: str | None = None

    web_name: str
    full_name: str
    position: str | None = None

    # Current state, from the most recent capture. Only meaningful for the
    # season in progress; for a finished season this is whatever was true at
    # the last capture.
    price: float | None = None
    selected_by_percent: float | None = None
    status: str | None = None
    news: str | None = None
    chance_of_playing_next: int | None = None
    ep_next: float | None = None
    state_as_of: datetime | None = None

    # League totals. Indicative for display — inherits a ~2% coverage gap
    # from the underlying per-fixture data.
    appearances: int
    minutes: int
    goals: int
    assists: int
    xg: float | None = None
    xa: float | None = None
    points: int
    bonus: int


class PlayerPage(BaseModel):
    items: list[Player]
    total: int
    limit: int
    offset: int
