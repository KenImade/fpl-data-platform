from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Player(BaseModel):
    """One player in one season.

    Three blocks, and they answer different questions:

    - **Identity** — who this is, and which of the two identifiers to use.
    - **Current state** — price, ownership, availability. What is true *now*,
      from the most recent capture.
    - **Season totals** — what has happened so far, derived from per-match
      data.

    The current-state block is the wrong source for historical features. It
    reflects today even for gameweeks long finished, so building a model on it
    means training on information nobody had at the time. Use the
    snapshot-scoped endpoints for that.
    """

    season: str = Field(
        description="Season this row describes.",
        examples=["2026-2027"],
    )

    player_id: int = Field(
        description=(
            "Season-scoped identifier. **Reassigned every August** — id 3 has "
            "belonged to three different people across three seasons. Fine "
            "for requests within one season; joining on it across seasons "
            "blends careers and returns no error."
        ),
        examples=[427],
    )

    player_code: int = Field(
        description=(
            "Permanent identifier. The same human keeps it year to year, so "
            "this is the correct key for any historical query, career "
            "aggregate, or cross-season join."
        ),
        examples=[232413],
    )

    team_code: int | None = Field(
        default=None,
        description=(
            "Stable club identifier — survives across seasons, unlike "
            "`team_id`. Fixture data joins on this."
        ),
    )

    team_name: str | None = Field(
        default=None,
        description=(
            "Club name for this season. Not stable across seasons: 'Ipswich' "
            "became 'Ipswich Town', same club, same code. Group by "
            "`team_code`."
        ),
    )

    team_short: str | None = Field(
        default=None,
        description="Three-letter club abbreviation.",
        examples=["ARS"],
    )

    web_name: str = Field(
        description=(
            "Short display name, as FPL shows it. Not unique — two players at "
            "the same club can share one."
        ),
        examples=["Saka"],
    )

    full_name: str = Field(
        description="First and second name combined.",
        examples=["Bukayo Saka"],
    )

    position: str | None = Field(
        default=None,
        description=(
            "`GKP`, `DEF`, `MID` or `FWD`. Determines scoring: goal values, "
            "clean sheet eligibility, and the defensive contribution "
            "threshold all vary by position.\n\n"
            "Null for 20 players in 2024/25 whose position the source "
            "recorded as unknown — all of whom played zero minutes."
        ),
        examples=["MID"],
    )

    # -- current state ------------------------------------------------------

    price: float | None = Field(
        default=None,
        description=(
            "Current price in millions, from the most recent capture. Only "
            "meaningful for the season in progress; for a finished season it "
            "is whatever was true when we last captured.\n\n"
            "Prices move nightly. For the price at a specific deadline, use "
            "the snapshot endpoints rather than this field."
        ),
        examples=[10.1],
    )

    selected_by_percent: float | None = Field(
        default=None,
        description=(
            "Share of FPL managers owning this player, as a percentage. Current, not point-in-time."
        ),
        examples=[42.7],
    )

    status: str | None = Field(
        default=None,
        description=(
            "Availability flag. `a` available, `d` doubtful, `i` injured, "
            "`s` suspended, `u` unavailable, `n` not in squad. Current state, "
            "so it reflects today rather than any past gameweek."
        ),
        examples=["a"],
    )

    news: str | None = Field(
        default=None,
        description=(
            "Free-text injury or availability note from FPL. Null when there "
            "is nothing to report — which is the common case and means the "
            "player is presumed fit.\n\n"
            "This field moves faster than any other: a press conference on "
            "Friday can change it hours before a deadline. It is the main "
            "reason captures tighten to every fifteen minutes before one. "
            "Absent entirely for 2024/25, where the source did not carry it."
        ),
    )

    chance_of_playing_next: int | None = Field(
        default=None,
        description=(
            "FPL's stated percentage chance of featuring in the next "
            "gameweek, 0-100. **Null means no flag** — the player is presumed "
            "available — rather than unknown."
        ),
    )

    ep_next: float | None = Field(
        default=None,
        description=(
            "FPL's own expected points projection for the next gameweek. "
            "Useful as a baseline: any model worth running should beat it on "
            "rank correlation.\n\n"
            "Flat before a season starts — every player shows the same value "
            "until matches have been played, so it carries no signal in the "
            "opening weeks."
        ),
    )

    state_as_of: datetime | None = Field(
        default=None,
        description=(
            "When the capture behind the current-state fields was taken. If "
            "this is hours old, so are the price and news above it."
        ),
    )

    # -- season totals ------------------------------------------------------

    appearances: int = Field(
        description=(
            "League matches with at least one minute played. Excludes European and cup fixtures."
        ),
    )

    minutes: int = Field(
        description="Total league minutes played.",
    )

    goals: int = Field(description="League goals scored.")

    assists: int = Field(
        description=(
            "League assists, using FPL's definition — which differs from the "
            "conventional football one and changed for the 2025/26 season."
        ),
    )

    xg: float | None = Field(
        default=None,
        description=(
            "Expected goals across league matches. A measure of chance "
            "quality rather than outcome, so it is more stable week to week "
            "than goals and generally a better predictor of future scoring."
        ),
    )

    xa: float | None = Field(
        default=None,
        description="Expected assists across league matches.",
    )

    points: int = Field(
        description=(
            "Total FPL points this season.\n\n"
            "Derived from per-match data, which is missing roughly 2% of "
            "scoring events, so this will not always equal FPL's official "
            "figure exactly. Indicative for display; see the data quality "
            "page before relying on it for anything precise."
        ),
    )

    bonus: int = Field(
        description=(
            "Bonus points awarded, on top of the base points already included in `points`."
        ),
    )


class PlayerPage(BaseModel):
    """A page of players.

    Offset pagination rather than cursor: a season has under a thousand
    players and the set is stable within one, so offsets do not shift
    underneath a paging client.
    """

    items: list[Player] = Field(description="Players on this page.")

    total: int = Field(
        description=(
            "Total matching the filters, ignoring pagination. Use it to "
            "decide whether more pages exist."
        ),
    )

    limit: int = Field(
        description="Page size as requested. Maximum 200.",
        examples=[50],
    )

    offset: int = Field(
        description="Rows skipped. Add `limit` to fetch the next page.",
        examples=[0],
    )
