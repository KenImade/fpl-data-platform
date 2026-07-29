import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dagster import AssetKey
from dagster_dbt import DagsterDbtTranslator, DbtProject

DBT_PROJECT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", Path(__file__).parents[4] / "transform"))

dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR)
dbt_project.prepare_if_dev()


class FplDbtTranslator(DagsterDbtTranslator):
    """Map dbt sources onto the load assets that produce them.

    Without this, dbt's sources become their own asset keys and the
    graph splits into two disconnected islands. Dagster wouldn't know
    that stg_playerstats depends on the ci_playerstats load.
    """

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> AssetKey:
        if dbt_resource_props["resource_type"] == "source":
            return AssetKey(dbt_resource_props["name"])
        return super().get_asset_key(dbt_resource_props)
