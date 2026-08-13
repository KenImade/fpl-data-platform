"""Bronze: raw files flattened into typed parquet.

Three families, and they differ only in what produces their input — the FPL
captures, the Core Insights daily masters, or the weekly tarball. The
per-table assets are built by factories because the table lists live in the
modules that know them, and a hand-written asset per table would drift from
those lists silently.
"""

from datetime import date, timedelta

from dagster import (
    AssetCheckExecutionContext,
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    AssetsDefinition,
    FreshnessPolicy,
    asset,
    asset_check,
)

from fpl_ingestion.assets.raw import ci_archive_raw, ci_masters_raw, ci_tarball_raw
from fpl_ingestion.bronze import build_bootstrap_bronze
from fpl_ingestion.checks import check_captured_near_deadline
from fpl_ingestion.core_insights import (
    ARCHIVE_TABLES,
    DAILY_TABLES,
    build_archive,
    build_daily,
)
from fpl_ingestion.deadlines import read_deadlines
from fpl_ingestion.partitions import daily
from fpl_ingestion.resources import StoreResource
from fpl_ingestion.tarball import BUILDS, Scope, build_gameweek_table


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
    ran, reported success, and captured nothing useful — the only failure mode
    whose cost is permanent.
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


ci_daily_assets: list[AssetsDefinition] = [_ci_daily_asset(t) for t in DAILY_TABLES]
ci_archive_assets: list[AssetsDefinition] = [_ci_archive_asset(t) for t in ARCHIVE_TABLES]
tarball_assets: list[AssetsDefinition] = [_tarball_asset(t, s) for t, s in BUILDS]

# Lookups by table name, so warehouse.py can wire load dependencies without
# depending on list ordering.
ci_daily_by_table: dict[str, AssetsDefinition] = dict(
    zip(DAILY_TABLES, ci_daily_assets, strict=True)
)
ci_archive_by_table: dict[str, AssetsDefinition] = dict(
    zip(ARCHIVE_TABLES, ci_archive_assets, strict=True)
)
tarball_by_key: dict[str, AssetsDefinition] = {
    f"{t}_{s}": a for (t, s), a in zip(BUILDS, tarball_assets, strict=True)
}
