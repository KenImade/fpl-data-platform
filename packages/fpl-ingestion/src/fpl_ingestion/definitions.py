import os
from datetime import UTC, date, datetime, timedelta

from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    DailyPartitionsDefinition,
    DefaultSensorStatus,
    Definitions,
    FreshnessPolicy,
    OpExecutionContext,
    RunFailureSensorContext,
    RunRequest,
    ScheduleEvaluationContext,
    SkipReason,
    asset,
    asset_check,
    job,
    op,
    run_failure_sensor,
    schedule,
    sensor,
)

from fpl_ingestion.alerting import Severity, failure_severity
from fpl_ingestion.bronze import build_bootstrap_bronze
from fpl_ingestion.capture import capture
from fpl_ingestion.checks import check_captured_near_deadline
from fpl_ingestion.client import make_client
from fpl_ingestion.deadlines import read_deadlines
from fpl_ingestion.heartbeat import ping
from fpl_ingestion.mirror import mirror_masters, mirror_tarball
from fpl_ingestion.resources import STORE, StoreResource
from fpl_ingestion.schedule import decide

daily = DailyPartitionsDefinition(start_date="2026-07-27")


@op
def capture_op(context: OpExecutionContext, store: StoreResource) -> None:
    with make_client(os.environ["USER_AGENT"]) as client:
        result = capture(client, store.build())

    for name, key in result.stored.items():
        context.log.info("stored %s -> %s", name, key)

    if not result.ok:
        ping("/fail")
        raise RuntimeError(f"capture incomplete: {result.failed}")

    ping()


@op
def mirror_masters_op(context: OpExecutionContext, store: StoreResource) -> None:
    day = date.fromisoformat(context.run.tags.get("mirror/day", str(datetime.now(UTC).date())))

    with make_client(os.environ["USER_AGENT"]) as client:
        result = mirror_masters(client, store.build(), day)
        context.log.info("mirror %s: %s", day, result)
        context.add_output_metadata(result)

    if result["failed"]:
        context.log.warning("%d files failed to mirror", result["failed"])


@op
def mirror_tarball_op(context: OpExecutionContext, store: StoreResource) -> None:
    day = datetime.now(UTC).date()

    with make_client(os.environ["USER_AGENT"]) as client:
        result = mirror_tarball(client, store.build(), day)
        context.log.info("tarball %s: %s", day, result)
        context.add_output_metadata(result)


@job
def mirror_tarball_job() -> None:
    mirror_tarball_op()


@job
def mirror_masters_job() -> None:
    mirror_masters_op()


@job
def capture_job() -> None:
    capture_op()


@schedule(job=mirror_tarball_job, cron_schedule="0 20 * * 0", execution_timezone="UTC")
def mirror_tarball_schedule():
    """Weekly rather than daily, the tarball is large and
    past gameweek snapshots don't change."""
    return {}


@schedule(
    job=mirror_masters_job,
    cron_schedule="0 19 * * *",
    execution_timezone="UTC",
)
def mirror_masters_schedule(context: ScheduleEvaluationContext):
    """Daily. Deliberately separate from the FPL capture sensor.

    Sharing that cadence would mean 15-minute pulls during deadline
    windows, roughly760 requests and several GB of downloads a day,
    for a source that refreshes twice.
    """
    day = context.scheduled_execution_time.astimezone(UTC).date()
    return {"tags": {"mirror/day": str(day)}}


@sensor(job=capture_job, minimum_interval_seconds=60)
def fpl_capture_sensor(context, store: StoreResource):
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
    """Route run failures by proximity to the next deadline"""
    now = datetime.now(UTC)
    severity = failure_severity(now, read_deadlines(store.build()))

    job_name = context.dagster_run.job_name
    message = (
        f"[{severity.upper()}] {job_name} failed\n"
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


@asset(
    partitions_def=daily,
    freshness_policy=FreshnessPolicy.cron(
        deadline_cron="0 6 * * *",  # fresh by 06:00 UTC daily
        lower_bound_delta=timedelta(hours=24),
    ),
)
def bootstrap_bronze(context, store: StoreResource) -> None:
    meta = build_bootstrap_bronze(store.build(), date.fromisoformat(context.partition_key))
    context.add_output_metadata(meta)


@asset_check(asset=bootstrap_bronze, blocking=False)
def captured_near_deadline(context, store: StoreResource) -> AssetCheckResult:
    s = store.build()
    day = date.fromisoformat(context.partition_key)
    r = check_captured_near_deadline(s, day, read_deadlines(s))
    return AssetCheckResult(passed=r.passed, severity=AssetCheckSeverity.ERROR, metadata=r.metadata)


defs = Definitions(
    resources={"store": STORE},
    jobs=[capture_job, mirror_masters_job, mirror_tarball_job],
    sensors=[fpl_capture_sensor, failure_alert_sensor],
    schedules=[mirror_masters_schedule, mirror_tarball_schedule],
    assets=[bootstrap_bronze],
    asset_checks=[captured_near_deadline],
)
