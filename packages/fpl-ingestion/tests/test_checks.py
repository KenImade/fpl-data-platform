"""Tests for check_captured_near_deadline.

This check is the one that catches the system running successfully and
capturing nothing useful — no exception, no failed run, no alert, and the
data unrecoverable afterwards.

The first real opportunity to observe it is the GW1 deadline on 21 August
2026. These tests are the only evidence beforehand that it works, so the
failing cases matter more than the passing ones.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fpl_ingestion.checks import check_captured_near_deadline
from fpl_ingestion.storage import LocalStore, raw_key

DAY = date(2026, 8, 21)
DEADLINE = datetime(2026, 8, 21, 17, 30, tzinfo=UTC)  # GW1


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


def capture_at(store, *times: datetime) -> None:
    payload = json.dumps({"elements": [], "teams": [], "events": []}).encode()
    for t in times:
        store.put(raw_key("bootstrap-static", t), payload)


# ---------------------------------------------------------------------------
# failing cases — the reason this check exists
# ---------------------------------------------------------------------------


def test_fails_when_no_captures_at_all(store) -> None:
    result = check_captured_near_deadline(store, DAY, [DEADLINE])

    assert not result.passed
    assert result.metadata["missed"] == [str(DEADLINE)]


def test_fails_when_only_capture_is_outside_window(store) -> None:
    """7h before the deadline: prices and injury news have moved since."""
    capture_at(store, DEADLINE - timedelta(hours=7))

    result = check_captured_near_deadline(store, DAY, [DEADLINE])

    assert not result.passed


def test_fails_when_only_capture_is_after_the_deadline(store) -> None:
    """Post-deadline state is useless for prediction — teams are locked.

    Pins the direction of the inequality: easy to flip in a refactor, and
    the failure would be invisible.
    """
    capture_at(store, DEADLINE + timedelta(minutes=1))

    result = check_captured_near_deadline(store, DAY, [DEADLINE])

    assert not result.passed


def test_reports_only_the_uncovered_deadline(store) -> None:
    """A double gameweek can have two deadlines; covering one isn't enough."""
    second = datetime(2026, 8, 21, 23, 0, tzinfo=UTC)
    capture_at(store, DEADLINE - timedelta(hours=2))

    result = check_captured_near_deadline(store, DAY, [DEADLINE, second])

    assert not result.passed
    assert result.metadata["missed"] == [str(second)]
    assert result.metadata["deadlines_today"] == 2


# ---------------------------------------------------------------------------
# passing cases
# ---------------------------------------------------------------------------


def test_passes_with_capture_inside_window(store) -> None:
    capture_at(store, DEADLINE - timedelta(hours=3))

    assert check_captured_near_deadline(store, DAY, [DEADLINE]).passed


def test_passes_at_window_boundary(store) -> None:
    capture_at(store, DEADLINE - timedelta(hours=5, minutes=59))

    assert check_captured_near_deadline(store, DAY, [DEADLINE]).passed


def test_passes_at_exact_deadline_instant(store) -> None:
    capture_at(store, DEADLINE)

    assert check_captured_near_deadline(store, DAY, [DEADLINE]).passed


def test_passes_when_one_of_many_captures_covers(store) -> None:
    """Realistic day: 3-hourly captures, tightening near the deadline."""
    capture_at(
        store,
        datetime(2026, 8, 21, 2, 0, tzinfo=UTC),
        datetime(2026, 8, 21, 5, 0, tzinfo=UTC),
        datetime(2026, 8, 21, 8, 0, tzinfo=UTC),
        datetime(2026, 8, 21, 11, 30, tzinfo=UTC),
        datetime(2026, 8, 21, 11, 45, tzinfo=UTC),
        datetime(2026, 8, 21, 17, 15, tzinfo=UTC),
    )

    assert check_captured_near_deadline(store, DAY, [DEADLINE]).passed


def test_passes_when_no_deadline_that_day(store) -> None:
    """Most days. No captures required, nothing to miss."""
    result = check_captured_near_deadline(store, date(2026, 8, 19), [DEADLINE])

    assert result.passed
    assert result.metadata["deadlines_today"] == 0


def test_ignores_deadlines_on_other_days(store) -> None:
    other = datetime(2026, 8, 28, 17, 30, tzinfo=UTC)

    result = check_captured_near_deadline(store, DAY, [other])

    assert result.passed
    assert result.metadata["deadlines_today"] == 0


# ---------------------------------------------------------------------------
# the midnight case
# ---------------------------------------------------------------------------


def test_passes_when_window_spans_midnight(store) -> None:
    """A 02:00 deadline has its entire 6h window on the PREVIOUS UTC day.

    Reading only the deadline day's keys would report a false ERROR here —
    the worst kind, because it fires at exactly the moment you need to
    trust the alert.
    """
    early = datetime(2026, 12, 27, 2, 0, tzinfo=UTC)
    capture_at(store, datetime(2026, 12, 26, 23, 0, tzinfo=UTC))

    result = check_captured_near_deadline(store, date(2026, 12, 27), [early])

    assert result.passed


def test_previous_day_captures_outside_window_still_fail(store) -> None:
    """Reading two days of keys must not become a blanket pass."""
    early = datetime(2026, 12, 27, 2, 0, tzinfo=UTC)
    capture_at(store, datetime(2026, 12, 26, 14, 0, tzinfo=UTC))  # 12h before

    result = check_captured_near_deadline(store, date(2026, 12, 27), [early])

    assert not result.passed
