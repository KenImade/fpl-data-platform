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
    """Map dbt sources onto the load assets that produce them, and group models
    by the layer they belong to.

    Without the source mapping, dbt's sources become their own asset keys and
    the graph splits into two disconnected islands — Dagster wouldn't know that
    stg_playerstats depends on the ci_playerstats load.

    NOTE THE ASYMMETRY in get_asset_key. Sources return a BARE name, because
    that is what the load assets are called; models fall through to super(),
    which prefixes with the schema — features/feat_training_set,
    marts/dim_player. Anything declaring a dep on a dbt model must use the
    prefixed form, and a dep naming a key nothing produces dangles silently
    rather than raising. That asymmetry has cost an afternoon once already.
    """

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> AssetKey:
        if dbt_resource_props["resource_type"] == "source":
            return AssetKey(dbt_resource_props["name"])
        return super().get_asset_key(dbt_resource_props)

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        """Group by dbt layer rather than leaving everything in `default`.

        dbt already encodes the layer twice — in the folder path and in the
        schema config — so deriving it here means the Dagster UI reflects the
        project's own structure instead of a flat list of eighty models.

        Taken from the folder rather than the schema, because the folder is
        where a reader looks first and the two are kept in step by
        dbt_project.yml. A model directly under models/ with no subfolder
        falls back to None, which Dagster renders as `default` — and which is
        a signal that the model is filed somewhere it should not be.
        """
        if dbt_resource_props["resource_type"] == "source":
            # Sources are produced by the load assets, which set their own
            # group. Returning one here would fight with that.
            return None

        path = dbt_resource_props.get("fqn", [])
        # fqn is [project_name, *folders, model_name]. The first folder is the
        # layer; anything deeper is organisation within it and not worth a
        # separate group.
        return path[1] if len(path) > 2 else None
