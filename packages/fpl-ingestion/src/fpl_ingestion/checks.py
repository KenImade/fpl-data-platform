from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fpl_ingestion.bronze import list_captures, parse_capture_time
from fpl_ingestion.storage import Store


@dataclass(frozen=True, slots=True)
class CheckResult:
    passed: bool
    metadata: dict


def check_captured_near_deadline(store: Store, day: date, deadlines: list[datetime]) -> CheckResult:
    todays = [d for d in deadlines if d.date() == day]
    if not todays:
        return CheckResult(True, {"deadlines_today": 0})

    captures = [
        parse_capture_time(k)
        for d in (day - timedelta(days=1), day)
        for k in list_captures(store, "bootstrap-static", d)
    ]
    missed = [
        d for d in todays if not any(timedelta(0) <= d - c <= timedelta(hours=6) for c in captures)
    ]
    return CheckResult(
        passed=not missed,
        metadata={"deadlines_today": len(todays), "missed": [str(d) for d in missed]},
    )
