from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from dagster import (
    AssetCheckExecutionContext,
    AssetCheckResult,
    AssetCheckSeverity,
    AssetDep,
    AssetExecutionContext,
    AssetsDefinition,
    AssetSelection,
    ConfigurableResource,
    DailyPartitionsDefinition,
    DefaultScheduleStatus,
    DefaultSensorStatus,
    Definitions,
    EnvVar,
    FreshnessPolicy,
    LastPartitionMapping,
    OpExecutionContext,
    RunFailureSensorContext,
    RunRequest,
    ScheduleDefinition,
    ScheduleEvaluationContext,
    SensorEvaluationContext,
    SkipReason,
    asset,
    asset_check,
    define_asset_job,
    job,
    op,
    run_failure_sensor,
    schedule,
    sensor,
)
from dagster_dbt import DbtCliResource, dbt_assets

from fpl_ingestion.alerting import Severity, failure_severity
from fpl_ingestion.bronze import build_bootstrap_bronze
from fpl_ingestion.capture import capture
from fpl_ingestion.checks import check_captured_near_deadline
from fpl_ingestion.client import make_client
from fpl_ingestion.core_insights import (
    ARCHIVE_TABLES,
    DAILY_TABLES,
    build_archive,
    build_daily,
)
from fpl_ingestion.dbt import FplDbtTranslator, dbt_project
from fpl_ingestion.deadlines import read_deadlines
from fpl_ingestion.heartbeat import ping
from fpl_ingestion.load import SPECS, LoadSpec, ensure_schema, load_table
from fpl_ingestion.mirror import mirror_archive, mirror_masters, mirror_tarball
from fpl_ingestion.resources import POSTGRES, STORE, PostgresResource, StoreResource
from fpl_ingestion.schedule import decide
from fpl_ingestion.tarball import BUILDS, Scope, build_gameweek_table

# end_offset=1 includes the in-progress day. Without it the newest partition
# is always yesterday's, so nothing can be materialised on the day it lands —
# which makes a cold-start rebuild impossible to verify.
daily = DailyPartitionsDefinition(start_date="2026-07-30", end_offset=1)


# ---------------------------------------------------------------------------
# resources
# ---------------------------------------------------------------------------


class FplClientResource(ConfigurableResource):
    """HTTP identity for outbound requests.

    Declared rather than read from os.environ inside each op, so a missing
    User-Agent fails at code-location load instead of on the first run.
    """

    user_agent: str

    def client(self):
        return make_client(self.user_agent)


FPL_CLIENT = FplClientResource(user_agent=EnvVar("USER_AGENT"))


# ---------------------------------------------------------------------------
# capture — the only imperative path
# ---------------------------------------------------------------------------
#
# A capture is an OBSERVATION, not derivable state. The FPL API serves only
# the current moment, so a snapshot missed at 17:25 on a deadline day cannot
# be reconstructed later at any price. That makes it genuinely event-driven
# and the one thing here that belongs to a sensor rather than the asset graph.
#
# Everything else is reconstructible from an upstream that still exists, and
# is therefore an asset.


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


# ---------------------------------------------------------------------------
# raw — Core Insights mirrors
# ---------------------------------------------------------------------------


@asset(partitions_def=daily, group_name="raw_core_insights")
def ci_masters_raw(
    context: AssetExecutionContext, store: StoreResource, fpl: FplClientResource
) -> None:
    """Daily master CSVs. Idempotent by key existence."""
    day = date.fromisoformat(context.partition_key)
    with fpl.client() as client:
        result = mirror_masters(client, store.build(), day)

    context.add_output_metadata(result)
    if result["failed"]:
        context.log.warning("%d files failed to mirror", result["failed"])


@asset(group_name="raw_core_insights")
def ci_archive_raw(
    context: AssetExecutionContext, store: StoreResource, fpl: FplClientResource
) -> None:
    """The finished 2024/25 season.

    Static, but declarative rather than a manual script: an empty bucket
    recovers on materialisation instead of requiring someone to remember a
    command that isn't in any schedule.
    """
    with fpl.client() as client:
        result = mirror_archive(client, store.build())

    context.add_output_metadata(result)
    if result["failed"]:
        context.log.warning("%d archive files failed", result["failed"])


@asset(group_name="raw_core_insights")
def ci_tarball_raw(
    context: AssetExecutionContext, store: StoreResource, fpl: FplClientResource
) -> None:
    """Full repository snapshot.

    Weekly by schedule, but fetches unconditionally when no tarball exists at
    all — so a wiped or newly deployed environment recovers immediately
    rather than waiting up to seven days for Sunday.
    """
    with fpl.client() as client:
        result = mirror_tarball(client, store.build(), datetime.now(UTC).date())

    context.add_output_metadata(result)
    if result["failed"]:
        raise RuntimeError("tarball mirror failed")


# ---------------------------------------------------------------------------
# bronze
# ---------------------------------------------------------------------------


@asset(
    partitions_def=daily,
    group_name="bronze_fpl",
    freshness_policy=FreshnessPolicy.cron(
        deadline_cron="0 6 * * *",
        lower_bound_delta=timedelta(hours=24),
    ),
)
def bootstrap_bronze(context: AssetExecutionContext, store: StoreResource) -> None:
    meta = build_bootstrap_bronze(store.build(), date.fromisoformat(context.partition_key))
    context.add_output_metadata(meta)


@asset_check(asset=bootstrap_bronze, blocking=False)
def captured_near_deadline(
    context: AssetCheckExecutionContext, store: StoreResource
) -> AssetCheckResult:
    """The check that catches success-with-no-data.

    Everything else fires when something fails. This fires when the system
    ran, reported success, and captured nothing useful — the only failure
    mode whose cost is permanent.
    """
    s = store.build()
    day = date.fromisoformat(context.partition_key)
    r = check_captured_near_deadline(s, day, read_deadlines(s))
    return AssetCheckResult(passed=r.passed, severity=AssetCheckSeverity.ERROR, metadata=r.metadata)


def _ci_daily_asset(table: str) -> AssetsDefinition:
    """One asset per Core Insights daily master table.

    `table` is a parameter of this factory, so each call binds its own. Note
    that adding a bound default argument here would make Dagster treat it as
    an upstream asset input rather than a closure.
    """

    @asset(
        name=f"ci_{table}_daily",
        partitions_def=daily,
        group_name="bronze_core_insights",
        deps=[ci_masters_raw],
    )
    def _asset(context: AssetExecutionContext, store: StoreResource) -> None:
        meta = build_daily(store.build(), table, date.fromisoformat(context.partition_key))
        context.add_output_metadata(meta)

    return _asset


def _ci_archive_asset(table: str) -> AssetsDefinition:
    @asset(
        name=f"ci_{table}_archive",
        group_name="bronze_core_insights",
        deps=[ci_archive_raw],
    )
    def _asset(context: AssetExecutionContext, store: StoreResource) -> None:
        context.add_output_metadata(build_archive(store.build(), table))

    return _asset


def _tarball_asset(table: str, scope: Scope) -> AssetsDefinition:
    @asset(
        name=f"ci_{table}_{scope}s",
        group_name="bronze_core_insights",
        deps=[ci_tarball_raw],
    )
    def _asset(context: AssetExecutionContext, store: StoreResource) -> None:
        context.add_output_metadata(build_gameweek_table(store.build(), table, scope=scope))

    return _asset


def _load_asset(spec: LoadSpec, deps: list) -> AssetsDefinition:
    """Bronze parquet -> bronze.{table} in Postgres.

    The boundary between Dagster and dbt. Everything above this line is
    Dagster's; everything below reads `bronze` and writes its own schemas.

    Deps are passed in explicitly rather than inferred: these assets resolve
    their inputs from object storage rather than from an upstream asset's
    output, so Dagster cannot work the graph out on its own.
    """

    @asset(name=spec.table, group_name="warehouse_bronze", deps=deps)
    def _asset(
        context: AssetExecutionContext,
        store: StoreResource,
        postgres: PostgresResource,
    ) -> None:
        conn = postgres.connection_string()
        ensure_schema(conn)
        context.add_output_metadata(load_table(store.build(), conn, spec))

    return _asset


ci_daily_assets: list[AssetsDefinition] = [_ci_daily_asset(t) for t in DAILY_TABLES]
ci_archive_assets: list[AssetsDefinition] = [_ci_archive_asset(t) for t in ARCHIVE_TABLES]
tarball_assets: list[AssetsDefinition] = [_tarball_asset(t, s) for t, s in BUILDS]

ci_daily_by_table: dict[str, AssetsDefinition] = dict(
    zip(DAILY_TABLES, ci_daily_assets, strict=True)
)
ci_archive_by_table: dict[str, AssetsDefinition] = dict(
    zip(ARCHIVE_TABLES, ci_archive_assets, strict=True)
)
tarball_by_key: dict[str, AssetsDefinition] = {
    f"{t}_{s}": a for (t, s), a in zip(BUILDS, tarball_assets, strict=True)
}


def _last(asset_def: AssetsDefinition) -> AssetDep:
    """Depend on the most recent partition, not every partition.

    Dagster's default for partitioned -> unpartitioned is AllPartitionMapping,
    which would declare a dependency on all 365 partitions of a year-old
    asset and leave staleness permanently red.
    """
    return AssetDep(asset_def, partition_mapping=LastPartitionMapping())


# Each load table and the bronze assets that produce its parquet. A spec with
# no entry raises KeyError at import, which is intended — a new load target
# should not silently acquire an empty dependency set.
LOAD_DEPS: dict[str, list] = {
    "fpl_players": [_last(bootstrap_bronze)],
    "ci_playerstats": [
        _last(ci_daily_by_table["playerstats"]),
        ci_archive_by_table["playerstats"],
    ],
    # No archive: 2024/25 used the nested layout and never published this file.
    "ci_gameweek_summaries": [_last(ci_daily_by_table["gameweek_summaries"])],
    "ci_players": [
        _last(ci_daily_by_table["players"]),
        ci_archive_by_table["players"],
    ],
    "ci_teams": [
        _last(ci_daily_by_table["teams"]),
        ci_archive_by_table["teams"],
    ],
    # Tarball only: no flat master exists for this in any season.
    "ci_player_gameweek_stats": [tarball_by_key["player_gameweek_stats_gameweek"]],
    # Tarball for active seasons, archive for 2024/25 — the only route to
    # that season's CBIT components, and so to the defensive contribution
    # reconstruction.
    "ci_matches": [
        tarball_by_key["matches_tournament"],
        ci_archive_by_table["matches"],
    ],
    "ci_playermatchstats": [
        tarball_by_key["playermatchstats_tournament"],
        ci_archive_by_table["playermatchstats"],
    ],
}

load_assets: list[AssetsDefinition] = [_load_asset(s, LOAD_DEPS[s.table]) for s in SPECS]

ALL_ASSETS: list[AssetsDefinition] = [
    ci_masters_raw,
    ci_archive_raw,
    ci_tarball_raw,
    bootstrap_bronze,
    *ci_daily_assets,
    *ci_archive_assets,
    *tarball_assets,
    *load_assets,
]


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

# Two partitioned jobs rather than one, because a job carries a single
# partitions definition and the two sources start on different days.
fpl_bronze_job = define_asset_job(
    "fpl_bronze_job",
    selection=[bootstrap_bronze],
    partitions_def=daily,
)

ci_daily_job = define_asset_job(
    "ci_daily_job",
    selection=[ci_masters_raw, *ci_daily_assets],
    partitions_def=daily,
)

ci_snapshot_job = define_asset_job(
    "ci_snapshot_job",
    selection=[
        ci_archive_raw,
        ci_tarball_raw,
        *ci_archive_assets,
        *tarball_assets,
    ],
)

load_job = define_asset_job(
    "load_job",
    selection=AssetSelection.assets(*load_assets).downstream(),
)

# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------
#
# All times UTC, sequenced so each layer runs after the one it reads from.
#
# default_status=RUNNING throughout: a fresh deploy or a wiped instance comes
# up automating itself rather than sitting idle until someone remembers to
# flip switches in the UI. Automation state belongs in the deployment, not in
# a database someone clicked once.
#
# ---------------------------------------------------------------------------
# On partition targeting
# ---------------------------------------------------------------------------
#
# The partitions definitions carry end_offset=1, which makes TODAY's partition
# addressable. That is necessary — without it the newest partition is always
# yesterday's, and nothing landing today could be materialised until tomorrow,
# which makes a cold-start rebuild impossible to verify.
#
# But it means build_schedule_from_partitioned_job would target today: a day
# still accumulating captures, built partial and never revisited. Every
# partition would ship incomplete.
#
# So these are hand-written and explicitly target YESTERDAY, running shortly
# after midnight UTC when that day is closed. The cost is that bronze lags the
# raw captures by up to 24 hours; the raw layer is authoritative and always
# current, so nothing is lost.


@schedule(
    job=ci_daily_job,
    cron_schedule="0 1 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)
def ci_daily_schedule(context: ScheduleEvaluationContext) -> RunRequest:
    """Mirror and build the Core Insights daily masters for the day just
    completed.

    The raw fetch is upstream of the bronze assets within the same job, so
    ordering is the asset graph's problem rather than the schedule's.
    """
    day = context.scheduled_execution_time.astimezone(UTC).date() - timedelta(days=1)
    return RunRequest(partition_key=str(day))


@schedule(
    job=fpl_bronze_job,
    cron_schedule="30 1 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)
def fpl_bronze_schedule(context: ScheduleEvaluationContext) -> RunRequest:
    """Build yesterday's FPL capture partition, once every capture for that
    day has landed.

    Captures run every three hours, tightening to fifteen minutes before a
    deadline, so a day is only complete after it ends.
    """
    day = context.scheduled_execution_time.astimezone(UTC).date() - timedelta(days=1)
    return RunRequest(partition_key=str(day))


ci_snapshot_schedule = ScheduleDefinition(
    job=ci_snapshot_job,
    # Sundays, after the weekly tarball mirror. The archive and tarball assets
    # are self-healing — they fetch when nothing exists at all — so a missed
    # week recovers on the next tick rather than needing intervention.
    cron_schedule="0 2 * * 0",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)


# Loads and everything downstream of them: staging views, marts, and their
# tests. `.downstream()` rather than an explicit list so a new dbt model is
# picked up without the selection needing to change.
load_schedule = ScheduleDefinition(
    job=load_job,
    # After both partitioned jobs above. Daily, though ci_snapshot_job runs
    # only on Sundays — so the tarball-derived tables reload identical parquet
    # six days a week. Harmless under replace semantics and cheap at this
    # volume.
    cron_schedule="0 3 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)

# ---------------------------------------------------------------------------
# alerting
# ---------------------------------------------------------------------------


@run_failure_sensor(
    monitor_all_code_locations=True,
    default_status=DefaultSensorStatus.RUNNING,
)
def failure_alert_sensor(context: RunFailureSensorContext, store: StoreResource) -> None:
    """Route run failures by proximity to the next deadline.

    A failed capture on a Tuesday costs one observation out of eight. The
    same failure at 14:00 on 21 August costs the pre-deadline state
    permanently.
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


# ---------------------------------------------------------------------------
@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=FplDbtTranslator(),
)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    yield from dbt.cli(["build"], context=context).stream()


# ---------------------------------------------------------------------------
defs = Definitions(
    resources={
        "store": STORE,
        "postgres": POSTGRES,
        "fpl": FPL_CLIENT,
        "dbt": DbtCliResource(project_dir=dbt_project),
    },
    assets=[*ALL_ASSETS, dbt_models],
    asset_checks=[captured_near_deadline],
    jobs=[
        capture_job,
        fpl_bronze_job,
        ci_daily_job,
        ci_snapshot_job,
        load_job,
    ],
    schedules=[
        fpl_bronze_schedule,
        ci_daily_schedule,
        ci_snapshot_schedule,
        load_schedule,
    ],
    sensors=[fpl_capture_sensor, failure_alert_sensor],
)
