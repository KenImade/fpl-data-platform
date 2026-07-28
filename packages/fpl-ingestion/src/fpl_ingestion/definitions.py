import os
from datetime import UTC, date, datetime

from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    DailyPartitionsDefinition,
    Definitions,
    OpExecutionContext,
    RunRequest,
    SkipReason,
    asset,
    asset_check,
    job,
    op,
    sensor,
)

from fpl_ingestion.bronze import build_bootstrap_bronze
from fpl_ingestion.capture import capture
from fpl_ingestion.checks import check_captured_near_deadline
from fpl_ingestion.client import make_client
from fpl_ingestion.deadlines import read_deadlines
from fpl_ingestion.resources import build_store
from fpl_ingestion.schedule import decide

daily = DailyPartitionsDefinition(start_date="2026-07-27")


@op
def capture_op(context: OpExecutionContext) -> None:
    store = build_store()
    with make_client(os.environ["USER_AGENT"]) as client:
        result = capture(client, store)

    for name, key in result.stored.items():
        context.log.info("stored %s -> %s", name, key)

    if not result.ok:
        raise RuntimeError(f"capture incomplete: {result.failed}")


@job
def capture_job() -> None:
    capture_op()


@sensor(job=capture_job, minimum_interval_seconds=60)
def fpl_capture_sensor(context):
    store = build_store()
    now = datetime.now(UTC)

    last = datetime.fromisoformat(context.cursor) if context.cursor else None
    decision = decide(now, last, read_deadlines(store))

    if not decision.capture:
        return SkipReason(decision.reason)

    context.update_cursor(now.isoformat())
    return RunRequest(run_key=now.strftime("%Y%m%dT%H%M%S"))


@asset(partitions_def=daily)
def bootstrap_bronze(context) -> None:
    meta = build_bootstrap_bronze(build_store(), date.fromisoformat(context.partition_key))
    context.add_output_metadata(meta)


@asset_check(asset=bootstrap_bronze, blocking=False)
def captured_near_deadline(context) -> AssetCheckResult:
    store = build_store()
    day = date.fromisoformat(context.partition_key)
    r = check_captured_near_deadline(store, day, read_deadlines(store))
    return AssetCheckResult(passed=r.passed, severity=AssetCheckSeverity.ERROR, metadata=r.metadata)


defs = Definitions(
    jobs=[capture_job],
    sensors=[fpl_capture_sensor],
    assets=[bootstrap_bronze],
    asset_checks=[captured_near_deadline],
)
