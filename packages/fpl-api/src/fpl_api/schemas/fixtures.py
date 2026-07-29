from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Fixture(BaseModel):
    match_id: str
    season: str
    gameweek: int | None = None
    competition: str
    kickoff_utc: datetime | None = None

    home_team_code: int
    home_team_name: str | None = None
    away_team_code: int | None = None
    away_team_name: str | None = None

    home_score: int | None = None
    away_score: int | None = None

    # Elo at kickoff, not a season snapshot — so it carries no hindsight.
    home_elo: float | None = None
    away_elo: float | None = None
