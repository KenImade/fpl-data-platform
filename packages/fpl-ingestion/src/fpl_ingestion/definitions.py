"""The code location: everything assembled, nothing defined.
    partitions.py        the daily partition, shared by assets and schedules
    resources.py         store, warehouse, HTTP identity
    assets/raw.py        Core Insights mirrors
    assets/bronze.py     raw files -> typed parquet
    assets/warehouse.py  parquet -> bronze.{table}; the Dagster/dbt boundary
    assets/transform.py  the dbt project
    assets/modelling.py  training and prediction
    sensors.py           capture, and failure routing
    jobs.py              named selections
    schedules.py         when each job runs

The one thing worth knowing that no single file states: the graph is connected
end to end, from a capture through bronze, the warehouse, dbt's staging, marts
and features, into the prediction assets and back out through the prediction
marts. Two translations make that work — FplDbtTranslator mapping dbt sources
onto the load assets that produce them, and assets/modelling.py carrying the
`player_gameweek` source key. Break either and the graph splits into islands
that still run, in the wrong order.
"""

from dagster import Definitions
from dagster_dbt import DbtCliResource

from fpl_ingestion.assets import (
    INGESTION_ASSETS,
    MODELLING_ASSETS,
    captured_near_deadline,
    dbt_models,
)
from fpl_ingestion.dagster_schedules import (
    ci_daily_schedule,
    ci_snapshot_schedule,
    fpl_bronze_schedule,
    load_schedule,
    prediction_schedule,
    training_schedule,
)
from fpl_ingestion.dbt import dbt_project
from fpl_ingestion.jobs import (
    ci_daily_job,
    ci_snapshot_job,
    fpl_bronze_job,
    load_job,
    predict_job,
    train_job,
)
from fpl_ingestion.resources import FPL_CLIENT, POSTGRES, STORE
from fpl_ingestion.sensors import capture_job, failure_alert_sensor, fpl_capture_sensor

defs = Definitions(
    resources={
        "store": STORE,
        "postgres": POSTGRES,
        "fpl": FPL_CLIENT,
        "dbt": DbtCliResource(project_dir=dbt_project),
    },
    assets=[*INGESTION_ASSETS, *MODELLING_ASSETS, dbt_models],
    asset_checks=[captured_near_deadline],
    jobs=[
        capture_job,
        fpl_bronze_job,
        ci_daily_job,
        ci_snapshot_job,
        load_job,
        train_job,
        predict_job,
    ],
    schedules=[
        fpl_bronze_schedule,
        ci_daily_schedule,
        ci_snapshot_schedule,
        load_schedule,
        training_schedule,
        prediction_schedule,
    ],
    sensors=[fpl_capture_sensor, failure_alert_sensor],
)
