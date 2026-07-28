"""Severity routing.

Kept separate from Dagster so the branches can be tested
exhaustively. The `page` branch cannot be observerd before
21 August 2026, so its tests are the only evidence it works.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from fpl_ingestion.schedule import next_deadline

PAGE_WINDOW = timedelta(hours=6)
NOTIFY_WINDOW = timedelta(hours=24)


class Severity(StrEnum):
    PAGE = "page"
    NOTIFY = "notify"
    DIGEST = "digest"


def failure_severity(now: datetime, deadlines: list[datetime]) -> Severity:
    """Same failure, different urgency.

    A failed capture on a Tuesday costs one observation out of eight.
    The same failure at 14:00 on 21 August costs the pre-deadline state
    permanently, no source can reconstruct it afterwards.

    No deadline in view means NOTIFY rather than DIGEST: not knowing when
    the next deadline is, is itself mildy concerning.
    """
    nxt = next_deadline(now, deadlines)
    if nxt is None:
        return Severity.NOTIFY

    until = nxt - now
    if until <= PAGE_WINDOW:
        return Severity.PAGE
    if until <= NOTIFY_WINDOW:
        return Severity.NOTIFY
    return Severity.DIGEST
