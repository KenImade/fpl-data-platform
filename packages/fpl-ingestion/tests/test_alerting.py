from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fpl_ingestion.alerting import Severity, failure_severity

GW1 = datetime(2026, 8, 21, 17, 30, tzinfo=UTC)  # the real one
GW2 = datetime(2026, 8, 28, 17, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hours_before", "expected"),
    [
        (0.5, Severity.PAGE),
        (5.9, Severity.PAGE),
        (6.0, Severity.PAGE),  # boundary is inclusive
        (6.1, Severity.NOTIFY),
        (23.9, Severity.NOTIFY),
        (24.0, Severity.NOTIFY),  # boundary is inclusive
        (24.1, Severity.DIGEST),
        (168.0, Severity.DIGEST),  # a week out
    ],
)
def test_severity_by_proximity(hours_before: float, expected: Severity) -> None:
    now = GW1 - timedelta(hours=hours_before)
    assert failure_severity(now, [GW1, GW2]) == expected


def test_pages_during_the_gw1_window() -> None:
    """14:00 on 21 August. The failure that costs data permanently."""
    now = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
    assert failure_severity(now, [GW1, GW2]) is Severity.PAGE


def test_uses_nearest_future_deadline_not_the_first() -> None:
    """Past deadlines must not suppress an imminent one."""
    now = GW2 - timedelta(hours=2)
    assert failure_severity(now, [GW1, GW2]) is Severity.PAGE


def test_no_deadlines_is_notify_not_digest() -> None:
    """Not knowing when the next deadline is, is itself mildly concerning."""
    assert failure_severity(datetime(2026, 7, 28, tzinfo=UTC), []) is Severity.NOTIFY


def test_all_deadlines_past_is_notify() -> None:
    """End of season, or a stale bootstrap that stopped updating."""
    now = datetime(2027, 6, 1, tzinfo=UTC)
    assert failure_severity(now, [GW1, GW2]) is Severity.NOTIFY


def test_exact_deadline_instant_pages() -> None:
    assert failure_severity(GW1, [GW1, GW2]) is Severity.PAGE
