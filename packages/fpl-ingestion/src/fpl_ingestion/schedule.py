from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

NORMAL_INTERVAL = timedelta(hours=3)
TIGHT_INERVAL = timedelta(minutes=15)
TIGHT_WINDOW = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class Decision:
    capture: bool
    reason: str
    interval: timedelta


def next_deadline(now: datetime, deadlines: list[datetime]) -> datetime | None:
    future = [d for d in deadlines if d >= now]
    return min(future) if future else None


def decide(
    now: datetime,
    last_capture: datetime | None,
    deadlines: list[datetime],
) -> Decision:
    """Capture if enough time has elasped since the last one.

    Gated on ELAPSED TIME, never wall-clock minute. A delayed tick captures
    late rather than not at all.
    """
    deadline = next_deadline(now, deadlines)

    if deadline is not None and (deadline - now) <= TIGHT_WINDOW:
        interval = TIGHT_INERVAL
        window = "deadline"
    else:
        interval = NORMAL_INTERVAL
        window = "normal"

    if last_capture is None:
        return Decision(True, "no previous capture", interval)

    elapsed = now - last_capture
    if elapsed >= interval:
        return Decision(True, f"{window}: {elapsed} since last capture", interval)

    return Decision(False, f"{window}: {interval - elapsed} until next capture", interval)
