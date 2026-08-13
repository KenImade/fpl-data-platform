"""Jobs: named selections of the asset graph.

Two partitioned jobs rather than one, because a job carries a single partitions
definition and the two sources start on different days.
"""

from dagster import AssetSelection, define_asset_job

from fpl_ingestion.assets.bronze import (
    bootstrap_bronze,
    ci_archive_assets,
    ci_daily_assets,
    tarball_assets,
)
from fpl_ingestion.assets.modelling import PREDICTIONS_KEY, minutes_model
from fpl_ingestion.assets.raw import ci_archive_raw, ci_masters_raw, ci_tarball_raw
from fpl_ingestion.assets.warehouse import load_assets
from fpl_ingestion.partitions import daily

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

# Loads and everything downstream: staging views, marts, features, and their
# tests. `.downstream()` rather than an explicit list so a new dbt model is
# picked up without the selection needing to change.
#
# Note this now also pulls in the prediction marts, which sit downstream of the
# scoring asset. On a load run they will rebuild from whatever predictions
# exist, which is correct — they are a view over the predictions table, not a
# trigger to produce new ones.
load_job = define_asset_job(
    "load_job",
    selection=AssetSelection.assets(*load_assets).downstream(),
)

train_job = define_asset_job("train_minutes_job", selection=[minutes_model])

# The scoring asset plus everything downstream of it — the two prediction marts
# and their tests — so one run covers score-then-publish rather than leaving the
# marts to catch up on the next load tick.
predict_job = define_asset_job(
    "predict_minutes_job",
    selection=AssetSelection.assets(PREDICTIONS_KEY).downstream(),
)
