"""Training and prediction, as assets.

TWO ASSETS, TWO CADENCES, and the separation is the point. Training is
expensive and only improves when new outcomes land — refitting on the same
completed gameweeks produces the same model. Prediction is cheap and must run
before every deadline, because that is when price and team news move.
Conflating them means you cannot reprice a gameweek without retraining, nor
retrain without waiting for a deadline.

HOW THE ORDERING IS EXPRESSED. minutes_predictions carries the asset key that
FplDbtTranslator assigns to the `predictions.player_gameweek` SOURCE — which is
AssetKey("player_gameweek"), because the translator flattens sources to their
bare name. dbt already knows mart_player_fixture_predictions reads that source,
so declaring the key here completes the graph:

    feat_training_set -> player_gameweek -> mart_player_*_predictions

No schedule ordering, no sleeps, and a manual backfill gets it right for the
same reason a scheduled run does.

That key must match the translator exactly. It is not checkable at import —
a deps entry naming a key nothing produces is legal and simply dangles — so a
mismatch shows up as prediction marts floating free of the scoring asset in the
UI rather than as an error. Worth a glance at the graph after any change to
FplDbtTranslator.get_asset_key.

PROMOTION IS NOT AUTOMATED. minutes_model writes an artifact and reports its
version; it does not touch predictions.active_model. A model that promotes
itself ships its own regressions, and the reason versions coexist in the
predictions table is so a challenger can be compared before it serves anyone.
"""

from datetime import UTC, datetime

import psycopg
from dagster import (
    AssetExecutionContext,
    AssetKey,
    MaterializeResult,
    MetadataValue,
    asset,
)

from fpl_ingestion.resources import PostgresResource

MODEL_NAME = "minutes_ordinal"

# FplDbtTranslator maps sources to AssetKey(name) — the bare table name, with
# no schema prefix. Getting this wrong disconnects the prediction marts
# silently, so it is stated once here rather than inlined.
PREDICTIONS_KEY = AssetKey(["player_gameweek"])

# The dbt model the prediction frame comes from. Depending on this rather than
# on the whole dbt run means a features rebuild triggers a rescore and a
# marts-only rebuild does not.
TRAINING_SET_KEY = AssetKey(["feat_training_set"])


@asset(
    name="minutes_model",
    group_name="modelling",
    deps=[TRAINING_SET_KEY],
    compute_kind="lightgbm",
    description=(
        "Fits the minutes model on completed gameweeks and writes a versioned "
        "artifact to object storage. Does not promote it."
    ),
)
def minutes_model(context: AssetExecutionContext) -> MaterializeResult:
    """Refit and persist.

    Runs the full walk-forward before fitting, roughly doubling runtime. Worth
    it: the metrics in the manifest are the only evidence this version beats
    the one it replaces, and metrics computed after the fact are metrics that
    stop being computed.

    Returns metadata, not data. The artifact lives in object storage and the
    version string is the pointer — passing boosters through an IO manager
    would move megabytes for no benefit.
    """
    from fpl_modelling.train import train

    try:
        _, manifest = train(persist=True)
    except ValueError as exc:
        # Pre-season, or before the first data_checked gameweek, there are no
        # labels. Expected rather than broken, so it surfaces as a skip with a
        # readable reason rather than a failure that pages someone in July.
        context.log.warning("training skipped: %s", exc)
        return MaterializeResult(
            metadata={
                "status": "skipped",
                "reason": str(exc),
                "checked_at": MetadataValue.text(datetime.now(UTC).isoformat()),
            }
        )

    m = manifest.metrics
    context.log.info(
        "trained %s: acc=%.3f precision_60=%.3f",
        manifest.model_version,
        m.get("accuracy", float("nan")),
        m.get("precision_60", float("nan")),
    )

    return MaterializeResult(
        metadata={
            "model_version": MetadataValue.text(manifest.model_version),
            "seasons": MetadataValue.text(", ".join(manifest.seasons)),
            "train_rows": manifest.train_rows,
            "feature_count": manifest.feature_count,
            "accuracy": m.get("accuracy"),
            "recall_60": m.get("recall_60"),
            "precision_60": m.get("precision_60"),
            "logloss_60": m.get("logloss_60"),
            "folds": m.get("folds_total"),
            # Surfaced so the promotion decision can be made from the UI:
            # compare against the active version's numbers, then promote.
            "promoted": MetadataValue.text("no - promote deliberately in predictions.active_model"),
        }
    )


@asset(
    key=PREDICTIONS_KEY,
    group_name="modelling",
    deps=[TRAINING_SET_KEY],
    compute_kind="python",
    description=(
        "Scores upcoming fixtures with the ACTIVE model version and writes to "
        "predictions.player_gameweek. Carries the dbt source key, so the "
        "prediction marts depend on it automatically."
    ),
)
def minutes_predictions(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> MaterializeResult:
    """Score and persist.

    Depends on the feature matrix, NOT on minutes_model. Deliberate: the frame
    comes from feat_training_set and the model comes from whichever version is
    ACTIVE — which may be older than the most recently trained one, and should
    be until someone promotes.

    The write is an upsert on (snapshot_id, player_id, match_id,
    model_version), so a retry or a double-fired sensor is harmless. Rescoring
    with a different version inserts alongside rather than replacing, which is
    what makes challenger comparison possible.
    """
    from fpl_modelling.data import as_float
    from fpl_modelling.predict import load_prediction_frame, predict, write

    dsn = postgres.connection_string()

    version = _active_version(dsn)
    if version is None:
        # A legitimate state: before the first promotion, or deliberately after
        # rolling one back. Serving nothing beats serving something arbitrary.
        context.log.warning(
            "no active version for %s - promote one in predictions.active_model",
            MODEL_NAME,
        )
        return MaterializeResult(
            metadata={"status": "skipped", "reason": "no active model version"}
        )

    season = _current_season(dsn)
    frame = load_prediction_frame(season=season)

    if len(frame) == 0:
        # Either every remaining fixture has kicked off, or no usable deadline
        # snapshot exists yet. The second is the normal state more than seven
        # days out from a deadline, and is not a failure.
        context.log.info("no upcoming fixtures with usable features")
        return MaterializeResult(
            metadata={
                "status": "skipped",
                "reason": "empty prediction frame",
                "season": MetadataValue.text(season),
            }
        )

    predictions = predict(frame, version)
    n = write(predictions)
    gameweeks = sorted(frame["gameweek"].unique().to_list())

    return MaterializeResult(
        metadata={
            "model_version": MetadataValue.text(version),
            "season": MetadataValue.text(season),
            "rows_written": n,
            "gameweeks": MetadataValue.text(f"{min(gameweeks)}-{max(gameweeks)}"),
            "mean_p_60": as_float(predictions["p_minutes_60"].mean()),
            "cold_start_rows": int(predictions["is_cold_start"].sum()),
        }
    )


def _active_version(dsn: str) -> str | None:
    """The version the marts serve.

    Read at prediction time rather than passed in, so a promotion takes effect
    on the next run with no redeploy.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select model_version from predictions.active_model where model_name = %s",
            (MODEL_NAME,),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def _current_season(dsn: str) -> str:
    """The season holding the most recent deadline.

    Derived rather than configured. A hardcoded season is the kind of thing
    that keeps silently working against last year's data every August.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select season from analytics_marts.dim_gameweek order by deadline_utc desc limit 1"
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("dim_gameweek is empty - has the warehouse been built?")
    return str(row[0])
