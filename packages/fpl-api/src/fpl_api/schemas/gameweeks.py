from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Gameweek(BaseModel):
    """One FPL gameweek: its deadline, its fixtures, and how it turned out.

    The deadline is the field everything else hangs off. It is the instant
    teams lock, and it is the boundary that makes a feature either legitimate
    or leaked.
    """

    season: str = Field(
        description="Season the gameweek belongs to.",
        examples=["2026-2027"],
    )

    gameweek: int = Field(
        description="Gameweek number, 1-38.",
        examples=[1],
    )

    gameweek_name: str = Field(
        description="FPL's display name.",
        examples=["Gameweek 1"],
    )

    deadline_utc: datetime = Field(
        description=(
            "Transfer deadline, UTC — roughly 90 minutes before the first "
            "kickoff. Teams lock at this instant, so it is the cutoff for any "
            "information a manager could have acted on. Verified against an "
            "independent epoch field on every build, so a timezone misparse "
            "cannot go unnoticed."
        ),
        examples=["2026-08-21T17:30:00Z"],
    )

    is_finished: bool | None = Field(
        default=None,
        description="Whether every fixture in the gameweek has been played.",
    )

    is_data_checked: bool | None = Field(
        default=None,
        description=(
            "Whether FPL has finalised scoring for the gameweek. Bonus points "
            "are provisional until this is true, and points can still change."
        ),
    )

    fixture_count: int | None = Field(
        default=None,
        description=(
            "League matches in this gameweek. **Not always 10.** Rounds get "
            "rescheduled around cup and European commitments, so 7 and 13 both "
            "occur legitimately in a normal season. A count above 10 usually "
            "means fixtures were moved into this week; below, moved out."
        ),
        examples=[10],
    )

    first_kickoff: datetime | None = Field(
        default=None,
        description="Earliest league kickoff. Null where no fixture carries a time.",
    )

    last_kickoff: datetime | None = Field(
        default=None,
        description=(
            "Latest league kickoff. The gameweek is not settled until this "
            "match ends, which is the relevant window for anything live."
        ),
    )

    average_score: float | None = Field(
        default=None,
        description=(
            "Mean score across all FPL managers. Null until the gameweek is "
            "played. Useful as a baseline: beating the average is the "
            "minimum bar for a strategy to be worth anything."
        ),
    )

    highest_score: int | None = Field(
        default=None,
        description="Best single-manager score in the gameweek.",
    )

    most_selected_player_id: int | None = Field(
        default=None,
        description=(
            "Most-owned player at the deadline, as a season-scoped "
            "`player_id`. Resolve via `/v1/players/{player_id}` with the same "
            "season — this id is reassigned each August and is not comparable "
            "across seasons."
        ),
    )

    most_captained_player_id: int | None = Field(
        default=None,
        description=(
            "Most-captained player, season-scoped `player_id`. Captaincy is "
            "a stronger ownership signal than selection, since it is a single "
            "concentrated bet rather than a squad slot."
        ),
    )

    transfers_made: int | None = Field(
        default=None,
        description="Total transfers made by all managers ahead of this gameweek.",
    )

    has_usable_snapshot: bool = Field(
        description=(
            "Whether a point-in-time capture exists close enough to this "
            "deadline to be meaningful.\n\n"
            "Before a gameweek, false is expected — there is nothing to "
            "capture yet. **After one, false means the capture missed**, and "
            "any feature built for that gameweek is unreliable. Published "
            "rather than hidden because a silently stale snapshot is worse "
            "than an obviously absent one."
        ),
    )

    snapshot_at: datetime | None = Field(
        default=None,
        description=(
            "When the authoritative capture for this deadline was taken. "
            "Always strictly before `deadline_utc` — a capture at the deadline "
            "instant is already too late."
        ),
    )

    hours_before_deadline: float | None = Field(
        default=None,
        description=(
            "How stale the snapshot was. Captures run every three hours, "
            "tightening to every fifteen minutes in the six hours before a "
            "deadline, so a healthy value is **under 0.25**.\n\n"
            "Larger means the cadence did not hold for that gameweek. The "
            "data is real but staler than intended, and injury news in "
            "particular moves fast enough that three hours matters."
        ),
    )
