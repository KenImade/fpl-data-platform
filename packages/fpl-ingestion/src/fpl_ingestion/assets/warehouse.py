"""Bronze parquet -> bronze.{table} in Postgres.

THE BOUNDARY BETWEEN DAGSTER AND DBT. Everything upstream of this module is
Dagster's; everything downstream reads `bronze` and writes its own schemas.
"""

from dagster import AssetDep, AssetExecutionContext, AssetsDefinition, LastPartitionMapping, asset

from fpl_ingestion.assets.bronze import (
    bootstrap_bronze,
    ci_archive_by_table,
    ci_daily_by_table,
    tarball_by_key,
)
from fpl_ingestion.load import SPECS, LoadSpec, ensure_schema, load_table
from fpl_ingestion.resources import PostgresResource, StoreResource


def _load_asset(spec: LoadSpec, deps: list) -> AssetsDefinition:
    """Deps are passed in explicitly rather than inferred.

    These assets resolve their inputs from object storage rather than from an
    upstream asset's output, so Dagster cannot work the graph out on its own.
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


def _last(asset_def: AssetsDefinition) -> AssetDep:
    """Depend on the most recent partition, not every partition.

    Dagster's default for partitioned -> unpartitioned is AllPartitionMapping,
    which would declare a dependency on all 365 partitions of a year-old asset
    and leave staleness permanently red.
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
    # Tarball for active seasons, archive for 2024/25 — the only route to that
    # season's CBIT components, and so to the defensive contribution
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
