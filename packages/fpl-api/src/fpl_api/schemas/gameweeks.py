from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Gameweek(BaseModel):
    season: str
    gameweek: int
    gameweek_name: str
    deadline_utc: datetime
    is_finished: bool | None = None
    is_data_checked: bool | None = None

    # League matches. Not always 10 — rounds get rescheduled around cup and
    # European commitments, so 7 and 13 both occur legitimately.
    fixture_count: int | None = None
    first_kickoff: datetime | None = None
    last_kickoff: datetime | None = None

    average_score: float | None = None
    highest_score: int | None = None
    most_selected_player_id: int | None = None
    most_captained_player_id: int | None = None
    transfers_made: int | None = None

    # Whether a point-in-time capture exists for this deadline. Before a
    # gameweek, false is expected. After it, false means the capture sensor
    # missed and any feature built for that gameweek is unreliable.
    has_usable_snapshot: bool
    snapshot_at: datetime | None = None
    hours_before_deadline: float | None = None
