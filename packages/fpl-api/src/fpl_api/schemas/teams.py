from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Team(BaseModel):
    season: str
    team_code: int
    team_id: int
    team_name: str
    team_short: str

    strength: int | None = None
    strength_overall_home: int | None = None
    strength_overall_away: int | None = None
    strength_attack_home: int | None = None
    strength_attack_away: int | None = None
    strength_defence_home: int | None = None
    strength_defence_away: int | None = None

    latest_elo: float | None = None
    latest_match_at: datetime | None = None

    # NULL rather than zero before a ball is kicked, so a pre-season club is
    # distinguishable from one that has played and lost everything.
    matches_played: int | None = None
    wins: int | None = None
    draws: int | None = None
    losses: int | None = None
    goals_for: int | None = None
    goals_against: int | None = None
    goal_difference: int | None = None
