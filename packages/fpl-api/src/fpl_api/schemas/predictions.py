"""Predictions.

Everything here is a model output rather than a record of what happened, and
the schema says so in each field. That distinction matters more than usual for
this endpoint: a consumer building a transfer tool will treat these numbers as
facts unless told otherwise, and some of them rest on far more evidence than
others.

Three things a caller should read before using any of it:

- Most components are null. Only the minutes model exists so far; the fields
  awaiting a model are present and empty rather than absent, so a response
  shape built against today keeps working when they arrive.
- `is_cold_start` marks a prediction made without history. It is a positional
  prior wearing an estimate's clothing, not an estimate.
- `snapshot_id` and `model_version` together identify exactly what produced the
  number. Quote them in any bug report and the prediction can be reproduced.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PlayerGameweekPrediction(BaseModel):
    """One player, one gameweek.

    Aggregated across fixtures, so a double gameweek is a single row. That is
    what most callers want — asking how many points someone scores in GW26 has
    one answer — but it means the aggregation rules matter:

    Expectations SUM across fixtures. Two matches is two chances to score, and
    the arithmetic holds whether or not the fixtures are related.

    Probabilities DO NOT sum. `p_minutes_60` is the probability of clearing
    sixty minutes in AT LEAST ONE fixture, computed as 1 - prod(1 - p) under
    independence. Independence is not quite right — a player injured in the
    first fixture misses the second — so double-gameweek probabilities are
    slightly optimistic. Use the fixture-grain endpoint for the unaggregated
    figures.
    """

    season: str = Field(examples=["2026-2027"])

    gameweek: int = Field(
        description="The gameweek this prediction covers.",
        examples=[1],
    )

    player_id: int = Field(
        description=(
            "Season-scoped identifier, reassigned every August. Fine within a "
            "season; use `player_code` for anything crossing one."
        ),
        examples=[427],
    )

    player_code: int | None = Field(
        default=None,
        description="Permanent identifier. The correct key for historical joins.",
    )

    web_name: str = Field(examples=["Saka"])
    full_name: str | None = Field(default=None, examples=["Bukayo Saka"])

    position: str | None = Field(
        default=None,
        description=(
            "`GKP`, `DEF`, `MID` or `FWD`. Determines scoring, so it is what "
            "makes the component predictions interpretable."
        ),
        examples=["MID"],
    )

    team_code: int | None = Field(default=None)
    team_name: str | None = Field(default=None)
    team_short: str | None = Field(default=None, examples=["ARS"])

    price: float | None = Field(
        default=None,
        description="Current price in millions, from the most recent capture.",
        examples=[10.1],
    )

    # -- fixtures -----------------------------------------------------------

    fixtures_in_gw: int = Field(
        description=(
            "League fixtures this player's club plays in this gameweek. 2 marks a double."
        ),
        examples=[1],
    )

    is_double_gw: bool = Field(
        description="Shorthand for `fixtures_in_gw > 1`.",
    )

    opponents: str | None = Field(
        default=None,
        description=(
            "Opponents with home or away, in kickoff order. A double gameweek lists both."
        ),
        examples=["Liverpool (H), Everton (A)"],
    )

    avg_elo_diff: float | None = Field(
        default=None,
        description=(
            "This club's Elo minus the opponent's, averaged across fixtures. "
            "Positive means the stronger side. A rough fixture-difficulty "
            "figure that does not depend on FPL's own ratings."
        ),
    )

    first_kickoff: datetime | None = Field(default=None)
    last_kickoff: datetime | None = Field(default=None)

    # -- minutes ------------------------------------------------------------

    p_minutes_60: float = Field(
        description=(
            "Probability of playing sixty minutes or more, in at least one "
            "fixture.\n\n"
            "The most load-bearing number here: the second appearance point "
            "and clean-sheet eligibility both hang off this threshold, so most "
            "of the variance in a player's score is decided by it rather than "
            "by how well they play."
        ),
        examples=[0.87],
    )

    e_minutes: float = Field(
        description=(
            "Expected minutes, summed across fixtures — so a double gameweek "
            "can exceed 90. Derived from band midpoints rather than a "
            "regression, so treat it as a scaling factor for per-90 rates "
            "rather than a precise forecast."
        ),
        examples=[74.2],
    )

    # -- awaiting a model ---------------------------------------------------

    e_goals: float | None = Field(
        default=None,
        description=(
            "Expected goals. **Null: no model yet.** A null here means the "
            "component has not been built, not that the player is expected to "
            "score zero."
        ),
    )

    e_assists: float | None = Field(default=None, description="**Null: no model yet.**")

    p_clean_sheet: float | None = Field(
        default=None,
        description=(
            "Probability of a clean sheet in at least one fixture. **Null: no model yet.**"
        ),
    )

    e_saves: float | None = Field(default=None, description="**Null: no model yet.**")

    e_goals_conceded: float | None = Field(default=None, description="**Null: no model yet.**")

    p_defcon: float | None = Field(
        default=None,
        description=(
            "Probability of meeting the defensive contribution threshold — 10 "
            "CBIT for defenders, 12 including recoveries for midfielders and "
            "forwards. **Null: no model yet.**"
        ),
    )

    e_bonus: float | None = Field(default=None, description="**Null: no model yet.**")
    e_cards: float | None = Field(default=None, description="**Null: no model yet.**")

    e_points: float | None = Field(
        default=None,
        description=(
            "Expected FPL points, recombined from the components through the "
            "scoring rules.\n\n"
            "**Null until enough components exist to make it meaningful.** "
            "Only the minutes model is built, and appearance points alone "
            "would be a misleading total."
        ),
    )

    # -- confidence and provenance ------------------------------------------

    prior_appearances: int | None = Field(
        default=None,
        description=(
            "League appearances before this gameweek's deadline. The single "
            "best guide to how much evidence sits behind the prediction: "
            "appearance rates rise steeply from roughly 5% at zero prior "
            "appearances to over 70% at ten."
        ),
    )

    is_cold_start: bool = Field(
        description=(
            "No prior league appearances this season — a new signing, a "
            "promoted club's squad player, or the opening gameweek.\n\n"
            "The prediction is a positional prior rather than an estimate from "
            "this player's own history. Worth surfacing differently in a UI, "
            "and worth discounting in an optimiser."
        ),
    )

    snapshot_id: str = Field(
        description=(
            "The point-in-time feature state this prediction was made from. "
            "Every input predates the gameweek deadline by construction, so "
            "the number reflects only what a manager could have known."
        ),
        examples=["2026-2027-gw01"],
    )

    model_version: str = Field(
        description=(
            "Identifies the code and the training data together. Quote it in a "
            "bug report — with `snapshot_id` it is enough to reproduce the "
            "prediction exactly."
        ),
        examples=["minutes-c2db730c135f-07dc1a6f4d09"],
    )

    predicted_at: datetime = Field(
        description=(
            "When this was scored. Predictions are refreshed as the deadline "
            "approaches and team news moves, so a stale timestamp means stale "
            "availability information."
        ),
    )


class PredictionPage(BaseModel):
    """A page of predictions."""

    items: list[PlayerGameweekPrediction]

    total: int = Field(
        description="Total matching the filters, ignoring pagination.",
    )

    limit: int = Field(description="Page size as requested. Maximum 200.", examples=[50])
    offset: int = Field(description="Rows skipped.", examples=[0])

    model_version: str | None = Field(
        default=None,
        description=(
            "The version serving this page. One per response — a page never "
            "mixes versions, so a consumer can cache against it."
        ),
    )


class PlayerFixturePrediction(BaseModel):
    """One player, one fixture.

    The unaggregated form. Use it when a double gameweek's fixtures need
    treating separately — comparing a home tie against a strong side with an
    away tie against a weak one, say, where the gameweek total hides the
    difference.
    """

    season: str
    gameweek: int
    player_id: int
    player_code: int | None = None
    match_id: str = Field(description="Identifies the fixture.")

    web_name: str
    position: str | None = None
    team_code: int | None = None
    team_name: str | None = None
    price: float | None = None

    opponent_code: int | None = None
    opponent_name: str | None = None
    is_home: bool | None = None
    kickoff_utc: datetime | None = None
    elo_diff: float | None = Field(
        default=None,
        description="This club's Elo at kickoff minus the opponent's.",
    )

    p_minutes_0: float = Field(description="Probability of not appearing.")
    p_minutes_1_59: float = Field(
        description="Probability of appearing but not reaching sixty minutes."
    )
    p_minutes_60: float = Field(
        description="Probability of sixty minutes or more, in this fixture."
    )
    e_minutes: float

    e_goals: float | None = Field(default=None, description="**Null: no model yet.**")
    e_assists: float | None = Field(default=None, description="**Null: no model yet.**")
    p_clean_sheet: float | None = Field(default=None, description="**Null: no model yet.**")
    e_saves: float | None = Field(default=None, description="**Null: no model yet.**")
    e_goals_conceded: float | None = Field(default=None, description="**Null: no model yet.**")
    p_defcon: float | None = Field(default=None, description="**Null: no model yet.**")
    e_bonus: float | None = Field(default=None, description="**Null: no model yet.**")
    e_cards: float | None = Field(default=None, description="**Null: no model yet.**")
    e_points: float | None = Field(default=None, description="**Null: no model yet.**")

    prior_appearances: int | None = None
    is_cold_start: bool
    snapshot_id: str
    model_version: str
    predicted_at: datetime
