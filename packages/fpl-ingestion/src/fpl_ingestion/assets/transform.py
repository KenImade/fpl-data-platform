"""The dbt project as Dagster assets.

One function covers every dbt model. dagster-dbt subsets automatically when a
run selects part of the graph, so `dbt build` here does not mean "always build
everything" — it means "build what was selected".

The translator in fpl_ingestion.dbt maps dbt SOURCES onto the load assets that
produce them, which is what keeps the graph one connected piece rather than two
islands.
"""

from collections.abc import Iterator
from typing import Any

from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets

from fpl_ingestion.dbt import FplDbtTranslator, dbt_project


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=FplDbtTranslator(),
)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    yield from dbt.cli(["build"], context=context).stream()
