from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Fixture(BaseModel):
    """A single match, from the home side's perspective.

    Underlying storage is per team per match — two rows per fixture — because
    almost everything at team level is asymmetric. This collapses that to one
    row with the natural home/away orientation.

    One consequence: European away ties where the home side is not a Premier
    League club have no row here, since we hold no identity for the opposition.
    A team's full schedule across all competitions is available per team
    rather than per fixture.
    """

    match_id: str = Field(
        description=(
            "Globally unique identifier, and a readable slug rather than a "
            "number: `25-26-prem-everton-vs-liverpool`. It encodes season and "
            "competition, so no season parameter is needed to resolve one."
        ),
        examples=["26-27-prem-liverpool-vs-bournemouth"],
    )

    season: str = Field(
        description="Season the fixture belongs to.",
        examples=["2026-2027"],
    )

    gameweek: int | None = Field(
        default=None,
        description=(
            "FPL gameweek, 1-38. Null for fixtures not assigned to one — some "
            "cup and European ties. Note that pre-season friendlies ARE "
            "assigned numbered gameweeks rather than gameweek 0, so filter on "
            "`competition` rather than assuming a gameweek implies a league "
            "match."
        ),
    )

    competition: str = Field(
        description=(
            "Which competition. `prem` for the Premier League; also "
            "`champions-league`, `europa-league`, `conference-league`, "
            "`efl-cup`, `community-shield`, `uefa-super-cup` and `friendly`. "
            "2026/27 contains 97 friendlies — filter on this for anything "
            "scoring-related."
        ),
        examples=["prem"],
    )

    kickoff_utc: datetime | None = Field(
        default=None,
        description=(
            "Kickoff time, UTC. Null where upstream has not published one: "
            "unscheduled knockout ties, and every league fixture in 2025/26 "
            "gameweeks 34-38, which carry results but no timestamp. See the "
            "data quality page."
        ),
    )

    home_team_code: int = Field(
        description=(
            "Stable club identifier — survives across seasons, unlike "
            "`team_id`. Always populated: a fixture with no Premier League "
            "home side does not appear here."
        ),
        examples=[14],
    )

    home_team_name: str | None = Field(
        default=None,
        description=(
            "Display name for the season in question. Not stable — 'Ipswich' "
            "in 2024/25 became 'Ipswich Town' in 2026/27, same club, same "
            "code. Group by `home_team_code`, not by name."
        ),
        examples=["Liverpool"],
    )

    away_team_code: int | None = Field(
        default=None,
        description=(
            "Stable club identifier for the away side. **Null means the "
            "opposition is not a Premier League club** — a European or cup "
            "tie against a foreign or lower-league side. That is information "
            "rather than missing data."
        ),
    )

    away_team_name: str | None = Field(
        default=None,
        description="Display name for the away side. Null for non-league opposition.",
    )

    home_score: int | None = Field(
        default=None,
        description="Goals scored by the home side. Null if the fixture has not been played.",
    )

    away_score: int | None = Field(
        default=None,
        description="Goals scored by the away side. Null if the fixture has not been played.",
    )

    home_elo: float | None = Field(
        default=None,
        description=(
            "ClubElo rating for the home side **at kickoff**, not an "
            "end-of-season figure. Being point-in-time, it carries no "
            "hindsight and is safe as a model prior — including for promoted "
            "clubs, whose rating derives from their Championship form and so "
            "exists before they have played a top-flight match. Null for "
            "fixtures not yet played, and for non-league opposition."
        ),
    )

    away_elo: float | None = Field(
        default=None,
        description="ClubElo rating for the away side at kickoff. See `home_elo`.",
    )
