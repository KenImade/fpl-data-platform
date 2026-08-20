"""Scoring a gameweek and writing the result where the warehouse can see it.

This is the serving path. It loads a fitted artifact — it never trains. A
prediction step that retrains is not reproducible, and it is slow at exactly the
moment it must not be: the deadline window, where the whole point of the capture
cadence is that state moves in the final hours.

WHAT IT SCORES. Rows whose fixture has not yet kicked off, read through the
same MatrixSpec the training path used. That is what makes train/serve skew
structurally unavailable rather than merely avoided: the labelled and unlabelled
predicates are declared side by side in one place, so there is no second code
path computing features a different way and no way to widen one predicate
without noticing it no longer mirrors the other.

WHAT IT WRITES. One row per player per fixture, stamped with the model version
and the snapshot it was made from. Components separately rather than only a
total, because a wrong total is not diagnosable and a wrong component is.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import polars as pl

from fpl_modelling.data import PLAYER_MATRIX, MatrixSpec, _dsn, as_float, load_matrix
from fpl_modelling.train import load

log = logging.getLogger(__name__)

PREDICTIONS_TABLE = "predictions.player_gameweek"

# Written on every row. Kept in one place because the writer and any backfill
# must agree, and a column list that drifts between them fails as a constraint
# violation at the least convenient time.
OUTPUT_COLUMNS = (
    "snapshot_id",
    "season",
    "gameweek",
    "player_id",
    "player_code",
    "match_id",
    "model_version",
    "predicted_at",
    "p_minutes_0",
    "p_minutes_1_59",
    "p_minutes_60",
    "e_minutes",
    "prior_appearances",
    "is_cold_start",
)


def load_prediction_frame(
    season: str,
    gameweek: int | None = None,
    spec: MatrixSpec = PLAYER_MATRIX,
) -> pl.DataFrame:
    """Rows to score: fixtures that have not kicked off.

    The filter is the spec's unlabelled predicate, the mirror of the labelled
    one the training path reads. Between them every row is either trainable or
    predictable and never both, which is the property that keeps an outcome
    from being scored as if it were unknown. Declaring the pair together in
    MatrixSpec is what makes that checkable rather than a convention two files
    happen to share.

    The predicate is NOT universally ``kickoff_utc >= now()``. On the team
    matrix it is ``not is_played``, because the undated 2025/26 fixtures have a
    null kickoff and a null comparison is null rather than true — a fixture
    that would be scored by neither path. Hardcoding the player predicate here
    is what made that invisible.

    Without ``gameweek`` this returns every remaining fixture in the season.
    That is useful — a fixture-ticker endpoint wants six gameweeks ahead — but
    the far ones are predicted from features that will be stale by the time
    they are played, and the API should present them as such.
    """
    df = load_matrix(
        spec,
        seasons=[season],
        gameweeks=[gameweek] if gameweek is not None else None,
        labelled=False,
    )
    log.info(
        "prediction frame: %d rows, gameweeks %s",
        len(df),
        sorted(df["gameweek"].unique().to_list()) if len(df) else [],
    )
    return df


def predict(df: pl.DataFrame, model_version: str) -> pl.DataFrame:
    """Score a frame and shape the result for the predictions table.

    The model's own feature list drives the scoring, not the frame's columns.
    If the matrix has gained a column since training, the model ignores it; if
    it has lost one, MinutesModel raises. That asymmetry is deliberate — a
    wider frame is a feature added and not yet used, a narrower one is a
    feature the model needs and cannot get.
    """
    if len(df) == 0:
        log.warning("nothing to score")
        return pl.DataFrame(schema={c: pl.Utf8 for c in OUTPUT_COLUMNS})

    model, manifest = load(model_version)

    bands = model.predict_bands(df)
    e_minutes = model.predict_expected_minutes(df)

    out = df.select(
        [
            "snapshot_id",
            "season",
            "gameweek",
            "player_id",
            "player_code",
            "match_id",
            "prior_appearances",
        ]
    ).with_columns(
        [
            pl.lit(manifest.model_version).alias("model_version"),
            pl.lit(datetime.now(UTC)).alias("predicted_at"),
            pl.Series("p_minutes_0", bands[:, 0]),
            pl.Series("p_minutes_1_59", bands[:, 1]),
            pl.Series("p_minutes_60", bands[:, 2]),
            pl.Series("e_minutes", e_minutes),
            # Cold start is worth surfacing rather than inferring downstream: a
            # prediction for a player with no history is a prior dressed as an
            # estimate, and the API should be able to say which it is serving.
            (pl.col("prior_appearances") == 0).alias("is_cold_start"),
        ]
    )

    log.info(
        "scored %d rows with %s: mean p_60=%.3f, mean e_minutes=%.1f",
        len(out),
        manifest.model_version,
        as_float(out["p_minutes_60"].mean()),
        as_float(out["e_minutes"].mean()),
    )
    return out.select(OUTPUT_COLUMNS)


def write(predictions: pl.DataFrame) -> int:
    """Upsert into predictions.player_gameweek.

    Upsert rather than insert because rescoring the same snapshot with the same
    version must be idempotent — the deadline sensor can fire twice, a Dagster
    retry can replay the asset, and neither should double the rows or fail on a
    constraint. Rescoring with a DIFFERENT version inserts alongside rather than
    replacing, which is the whole reason model_version is in the key.
    """
    if len(predictions) == 0:
        return 0

    import psycopg
    from psycopg import sql

    cols = list(predictions.columns)
    updatable = [
        c for c in cols if c not in ("snapshot_id", "player_id", "match_id", "model_version")
    ]

    statement = sql.SQL(
        "insert into {table} ({cols}) values ({placeholders}) "
        "on conflict (snapshot_id, player_id, match_id, model_version) "
        "do update set {updates}"
    ).format(
        table=sql.SQL("predictions.player_gameweek"),
        cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        updates=sql.SQL(", ").join(
            sql.SQL("{0} = excluded.{0}").format(sql.Identifier(c)) for c in updatable
        ),
    )

    rows = [tuple(r) for r in predictions.iter_rows()]
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.executemany(statement, rows)
        conn.commit()

    log.info("wrote %d rows to %s", len(rows), PREDICTIONS_TABLE)
    return len(rows)


def run(
    season: str,
    model_version: str,
    gameweek: int | None = None,
    spec: MatrixSpec = PLAYER_MATRIX,
) -> int:
    """Score and persist. The whole serving path in one call."""
    frame = load_prediction_frame(season=season, gameweek=gameweek, spec=spec)
    return write(predict(frame, model_version))


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Score a gameweek.")
    parser.add_argument("--season", default=os.environ.get("SEASON", "2026-2027"))
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--gameweek", type=int, default=None)
    args = parser.parse_args()

    n = run(
        season=args.season,
        model_version=args.model_version,
        gameweek=args.gameweek,
    )
    print(f"wrote {n} rows")
