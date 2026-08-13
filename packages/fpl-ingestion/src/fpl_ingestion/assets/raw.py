"""Raw mirrors of Core Insights.

Everything here fetches something that still exists upstream, which is what
makes these assets rather than sensor-driven ops: a lost mirror is
reconstructible, so it belongs in the graph. Compare capture, in sensors.py,
where a missed observation is gone permanently.
"""

from datetime import UTC, date, datetime

from dagster import AssetExecutionContext, asset

from fpl_ingestion.mirror import mirror_archive, mirror_masters, mirror_tarball
from fpl_ingestion.partitions import daily
from fpl_ingestion.resources import FplClientResource, StoreResource


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
    all — so a wiped or newly deployed environment recovers immediately rather
    than waiting up to seven days for Sunday.
    """
    with fpl.client() as client:
        result = mirror_tarball(client, store.build(), datetime.now(UTC).date())

    context.add_output_metadata(result)
    if result["failed"]:
        raise RuntimeError("tarball mirror failed")
