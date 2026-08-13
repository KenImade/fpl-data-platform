from datetime import UTC, datetime, timedelta

import pytest
from fpl_ingestion.schedule import (
    NORMAL_INTERVAL,
    TIGHT_INTERVAL,
    TIGHT_WINDOW,
    decide,
    next_deadline,
)

BASE = datetime(2025, 8, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# next_deadline
# ---------------------------------------------------------------------------


def test_next_deadline_returns_next_future_deadline():
    deadlines = [
        BASE - timedelta(hours=1),
        BASE + timedelta(hours=5),
        BASE + timedelta(hours=2),
    ]

    assert next_deadline(BASE, deadlines) == BASE + timedelta(hours=2)


def test_next_deadline_ignores_past_deadlines():
    deadlines = [
        BASE - timedelta(seconds=1),
        BASE + timedelta(hours=1),
    ]

    assert next_deadline(BASE, deadlines) == BASE + timedelta(hours=1)


def test_next_deadline_returns_none_when_all_are_past():
    deadlines = [
        BASE - timedelta(hours=2),
        BASE - timedelta(seconds=1),
    ]

    assert next_deadline(BASE, deadlines) is None


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,last_capture,deadlines,capture,interval",
    [
        (
            "no cursor",
            None,
            [],
            True,
            NORMAL_INTERVAL,
        ),
        (
            "3h01m elapsed, no deadline near",
            BASE - timedelta(hours=3, minutes=1),
            [BASE + timedelta(hours=7)],
            True,
            NORMAL_INTERVAL,
        ),
        (
            "2h59m elapsed, no deadline near",
            BASE - timedelta(hours=2, minutes=59),
            [BASE + timedelta(hours=7)],
            False,
            NORMAL_INTERVAL,
        ),
        (
            "16m elapsed, deadline in 3h",
            BASE - timedelta(minutes=16),
            [BASE + timedelta(hours=3)],
            True,
            TIGHT_INTERVAL,
        ),
        (
            "14m elapsed, deadline in 3h",
            BASE - timedelta(minutes=14),
            [BASE + timedelta(hours=3)],
            False,
            TIGHT_INTERVAL,
        ),
        (
            "20m elapsed, deadline in 7h",
            BASE - timedelta(minutes=20),
            [BASE + timedelta(hours=7)],
            False,
            NORMAL_INTERVAL,
        ),
        (
            "deadline exactly 6h away",
            BASE - timedelta(minutes=16),
            [BASE + TIGHT_WINDOW],
            True,
            TIGHT_INTERVAL,
        ),
        (
            "deadline 1 second past, next one used",
            BASE - timedelta(minutes=16),
            [
                BASE - timedelta(seconds=1),
                BASE + timedelta(hours=5),
            ],
            True,
            TIGHT_INTERVAL,
        ),
        (
            "all deadlines in the past",
            BASE - timedelta(hours=3, minutes=1),
            [
                BASE - timedelta(hours=2),
                BASE - timedelta(minutes=1),
            ],
            True,
            NORMAL_INTERVAL,
        ),
        (
            "empty deadline list",
            BASE - timedelta(hours=3, minutes=1),
            [],
            True,
            NORMAL_INTERVAL,
        ),
        (
            "tick delayed by 45m",
            BASE - timedelta(hours=3, minutes=45),
            [],
            True,
            NORMAL_INTERVAL,
        ),
    ],
)
def test_decide(name, last_capture, deadlines, capture, interval):
    decision = decide(BASE, last_capture, deadlines)

    assert decision.capture is capture
    assert decision.interval == interval


def test_last_capture_in_future_clock_skew():
    """
    Decide what behavior you want here.

    Current implementation:
        elapsed = now - last_capture = negative
        => never captures until the clock catches up.
    """

    last_capture = BASE + timedelta(minutes=5)

    decision = decide(BASE, last_capture, [])

    assert decision.capture is False
    assert decision.interval == NORMAL_INTERVAL
