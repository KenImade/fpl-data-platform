from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Team(BaseModel):
    """One club in one season.

    Season-scoped because clubs are: strength ratings change, the numeric id
    is reassigned, and even the name is not stable. `team_code` is the only
    thing that survives.

    Two independent measures of strength are carried. FPL's own ratings drive
    its fixture difficulty display; ClubElo is a rating system computed from
    results. They disagree often, and the disagreement is sometimes the
    interesting part.
    """

    season: str = Field(
        description="Season this row describes.",
        examples=["2026-2027"],
    )

    team_code: int = Field(
        description=(
            "Permanent club identifier. Survives across seasons and through "
            "relegation, so it is the correct key for anything historical. "
            "Fixture data joins on this rather than `team_id`."
        ),
        examples=[3],
    )

    team_id: int = Field(
        description=(
            "Season-scoped identifier, 1-20, assigned alphabetically each "
            "August. Reassigned as clubs are promoted and relegated, so it is "
            "not comparable across seasons."
        ),
        examples=[1],
    )

    team_name: str = Field(
        description=(
            "Club name as FPL renders it for this season. **Not stable** — "
            "'Ipswich' in 2024/25 became 'Ipswich Town' in 2026/27, same club, "
            "same code. Grouping by name splits one club in two."
        ),
        examples=["Arsenal"],
    )

    team_short: str = Field(
        description="Three-letter abbreviation.",
        examples=["ARS"],
    )

    # -- FPL's own ratings --------------------------------------------------

    strength: int | None = Field(
        default=None,
        description=(
            "FPL's overall strength rating on a 1-5 scale. Coarse, and set by "
            "FPL rather than derived from results — but it is what their own "
            "fixture difficulty ratings are built from, so it is worth having "
            "when comparing against `ep_next`."
        ),
    )

    strength_overall_home: int | None = Field(
        default=None,
        description=(
            "FPL's home strength rating, on a larger scale than `strength` — "
            "typically 1000-1400. Higher is stronger."
        ),
    )

    strength_overall_away: int | None = Field(
        default=None,
        description="FPL's away strength rating. Usually lower than the home figure.",
    )

    strength_attack_home: int | None = Field(
        default=None,
        description="FPL's attacking strength at home.",
    )

    strength_attack_away: int | None = Field(
        default=None,
        description="FPL's attacking strength away.",
    )

    strength_defence_home: int | None = Field(
        default=None,
        description="FPL's defensive strength at home.",
    )

    strength_defence_away: int | None = Field(
        default=None,
        description="FPL's defensive strength away.",
    )

    # -- ClubElo ------------------------------------------------------------

    latest_elo: float | None = Field(
        default=None,
        description=(
            "ClubElo rating after this club's most recent completed match. A "
            "current-strength figure, suitable for display or comparison.\n\n"
            "For modelling, use the per-fixture `home_elo` and `away_elo` on "
            "the fixtures endpoint instead — those are the rating **at "
            "kickoff** and carry no hindsight, whereas this one reflects "
            "everything that has happened since."
        ),
        examples=[2064.0],
    )

    latest_match_at: datetime | None = Field(
        default=None,
        description=(
            "Kickoff of the match `latest_elo` reflects. Null before a club "
            "has played, or where that fixture carries no timestamp."
        ),
    )

    # -- league record ------------------------------------------------------

    matches_played: int | None = Field(
        default=None,
        description=(
            "League matches played. **Null rather than zero before a season "
            "starts**, so a club yet to play is distinguishable from one that "
            "has played and lost everything. Excludes European and cup "
            "fixtures."
        ),
    )

    wins: int | None = Field(
        default=None, description="League wins. Null before the season starts."
    )

    draws: int | None = Field(
        default=None, description="League draws. Null before the season starts."
    )

    losses: int | None = Field(
        default=None, description="League defeats. Null before the season starts."
    )

    goals_for: int | None = Field(
        default=None,
        description="League goals scored. Null before the season starts.",
    )

    goals_against: int | None = Field(
        default=None,
        description="League goals conceded. Null before the season starts.",
    )

    goal_difference: int | None = Field(
        default=None,
        description="`goals_for` minus `goals_against`. Null before the season starts.",
    )
