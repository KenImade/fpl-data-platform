"""Sensors, and the one imperative path in the system.

A CAPTURE IS AN OBSERVATION, not derivable state. The FPL API serves only the
current moment, so a snapshot missed at 17:25 on a deadline day cannot be
reconstructed later at any price. That makes it genuinely event-driven and the
one thing here that belongs to a sensor rather than the asset graph.

Everything else is reconstructible from an upstream that still exists, and is
therefore an asset.
"""

from datetime import UTC, datetime

from dagster import (
    DefaultSensorStatus,
    OpExecutionContext,
    RunFailureSensorContext,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    job,
    op,
    run_failure_sensor,
    sensor,
)

from fpl_ingestion.alerting import Severity, failure_severity
from fpl_ingestion.capture import capture
from fpl_ingestion.deadlines import read_deadlines
from fpl_ingestion.heartbeat import ping
from fpl_ingestion.resources import FplClientResource, StoreResource
from fpl_ingestion.schedule import decide


@op
def capture_op(context: OpExecutionContext, store: StoreResource, fpl: FplClientResource) -> None:
    try:
        with fpl.client() as client:
            result = capture(client, store.build())
    except Exception:
        ping("/fail")
        raise

    for name, key in result.stored.items():
        context.log.info("stored %s -> %s", name, key)

    if not result.ok:
        # /fail alerts immediately rather than waiting out the grace period.
        ping("/fail")
        raise RuntimeError(f"capture incomplete: {result.failed}")

    # After storage, never before: a ping on job start would only prove the
    # scheduler works, not that data landed.
    ping()


@job
def capture_job() -> None:
    capture_op()


@sensor(
    job=capture_job,
    minimum_interval_seconds=60,
    default_status=DefaultSensorStatus.RUNNING,
)
def fpl_capture_sensor(
    context: SensorEvaluationContext, store: StoreResource
) -> RunRequest | SkipReason:
    """Gated on elapsed time since the last capture, never wall-clock minute.

    A delayed tick captures late rather than not at all. The cursor is
    Dagster-managed and survives restarts.
    """
    now = datetime.now(UTC)
    last = datetime.fromisoformat(context.cursor) if context.cursor else None
    decision = decide(now, last, read_deadlines(store.build()))

    if not decision.capture:
        return SkipReason(decision.reason)

    context.update_cursor(now.isoformat())
    return RunRequest(run_key=now.strftime("%Y%m%dT%H%M%S"))


@run_failure_sensor(
    monitor_all_code_locations=True,
    default_status=DefaultSensorStatus.RUNNING,
)
def failure_alert_sensor(context: RunFailureSensorContext, store: StoreResource) -> None:
    """Route run failures by proximity to the next deadline.

    A failed capture on a Tuesday costs one observation out of eight. The same
    failure at 14:00 on 21 August costs the pre-deadline state permanently.
    """
    now = datetime.now(UTC)
    severity = failure_severity(now, read_deadlines(store.build()))

    message = (
        f"[{severity.upper()}] {context.dagster_run.job_name} failed\n"
        f"{context.failure_event.message}\n"
        f"run: {context.dagster_run.run_id}"
    )

    if severity is Severity.PAGE:
        ping("/fail")
        context.log.error(message)
    elif severity is Severity.NOTIFY:
        context.log.warning(message)
    else:
        context.log.info(message)
