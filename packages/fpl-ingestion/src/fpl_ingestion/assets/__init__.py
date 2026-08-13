"""Every asset, assembled.

Split by layer rather than by table: raw mirrors, bronze parquet, warehouse
loads, dbt, and modelling. Each module knows only the layer above it, so a
change to how bronze is built does not reach into how predictions are scored.
"""

from dagster import AssetsDefinition

from fpl_ingestion.assets.bronze import (
    bootstrap_bronze,
    captured_near_deadline,
    ci_archive_assets,
    ci_daily_assets,
    tarball_assets,
)
from fpl_ingestion.assets.modelling import minutes_model, minutes_predictions
from fpl_ingestion.assets.raw import ci_archive_raw, ci_masters_raw, ci_tarball_raw
from fpl_ingestion.assets.transform import dbt_models
from fpl_ingestion.assets.warehouse import load_assets

# Python assets only. dbt_models and the modelling assets are listed separately
# in Definitions because they are not AssetsDefinition lists in the same sense
# — dbt_models is a multi-asset, and the modelling pair are individually named.
INGESTION_ASSETS: list[AssetsDefinition] = [
    ci_masters_raw,
    ci_archive_raw,
    ci_tarball_raw,
    bootstrap_bronze,
    *ci_daily_assets,
    *ci_archive_assets,
    *tarball_assets,
    *load_assets,
]

MODELLING_ASSETS: list[AssetsDefinition] = [
    minutes_model,
    minutes_predictions,
]

__all__ = [
    "INGESTION_ASSETS",
    "MODELLING_ASSETS",
    "bootstrap_bronze",
    "captured_near_deadline",
    "ci_archive_assets",
    "ci_archive_raw",
    "ci_daily_assets",
    "ci_masters_raw",
    "ci_tarball_raw",
    "dbt_models",
    "load_assets",
    "minutes_model",
    "minutes_predictions",
    "tarball_assets",
]
