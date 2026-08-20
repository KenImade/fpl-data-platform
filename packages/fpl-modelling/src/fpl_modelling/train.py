"""Fitting the minutes model that gets served, and recording enough to
reproduce it.

walk_forward_evaluate answers "is this model any good". This answers "which
model is running, fitted on what, and can we rebuild it". Different questions,
and conflating them is how a warehouse ends up serving numbers nobody can
explain six months later.

The versioning and storage that used to live here now live in artifacts.py,
because none of it was ever specific to minutes. What remains is the part that
is: which files define this model, how a booster becomes bytes, and the
holdout arrangement the ordinal decomposition needs.

THE METRICS TRAVEL WITH THE ARTIFACT rather than living in a notebook, because
"we thought it was better" is not a claim you can check later.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime

import lightgbm as lgb
import polars as pl

from fpl_modelling import artifacts
from fpl_modelling import minutes as minutes_model
from fpl_modelling.artifacts import Manifest
from fpl_modelling.data import (
    as_float,
    as_int,
    feature_columns,
    load_training_set,
    walk_forward_splits,
)

log = logging.getLogger(__name__)

MODEL_NAME = "minutes"

# The source files that define this model. artifacts.code_version raises if any
# is missing, so a rename that is not reflected here fails at training time
# rather than producing a version string that ignores the model.
SOURCE_FILES = ("artifacts.py", "data.py", "minutes.py", "train.py")

# Tar member names. Fixed rather than derived, because load must find them in
# an artifact written by an older revision of this file.
PLAYED_MEMBER = "played.txt"
PLAYED_60_MEMBER = "played_60.txt"


def _serialise(model: minutes_model.MinutesModel) -> dict[str, bytes]:
    """Two boosters as bytes.

    model_to_string rather than save_model, so no temporary directory is
    involved and the artifact layer never sees a path.
    """
    if model.played is None or model.played_60 is None:
        # Saving an unfitted model would write a well-formed artifact that
        # fails only when something tries to predict with it.
        raise RuntimeError("cannot save an unfitted model")

    return {
        PLAYED_MEMBER: model.played.model_to_string().encode(),
        PLAYED_60_MEMBER: model.played_60.model_to_string().encode(),
    }


def save(model: minutes_model.MinutesModel, manifest: Manifest) -> str:
    """Write the artifact, return its key."""
    return artifacts.save(manifest, _serialise(model))


def load(model_version: str) -> tuple[minutes_model.MinutesModel, Manifest]:
    """Read an artifact back as a fitted model.

    The feature list comes from the manifest rather than being recomputed, so a
    matrix that has gained columns since training fails in predict_cumulative
    rather than being silently reordered.
    """
    members, manifest = artifacts.load(MODEL_NAME, model_version)

    missing = [m for m in (PLAYED_MEMBER, PLAYED_60_MEMBER) if m not in members]
    if missing:
        raise ValueError(f"{model_version}: artifact is missing {missing}")

    model = minutes_model.MinutesModel(
        features=manifest.features,
        played=lgb.Booster(model_str=members[PLAYED_MEMBER].decode()),
        played_60=lgb.Booster(model_str=members[PLAYED_60_MEMBER].decode()),
        best_iterations=manifest.extras.get("best_iterations", {}),
    )
    return model, manifest


def train(
    seasons: list[str] | None = None,
    holdout_gameweeks: int = 2,
    evaluate: bool = True,
    persist: bool = True,
) -> tuple[minutes_model.MinutesModel, Manifest]:
    """Fit on everything available, after checking it is worth shipping.

    ``evaluate`` runs the full walk-forward before fitting the final model. It
    roughly doubles the runtime and is worth it: the metrics recorded in the
    manifest are the only evidence that this version is better than the one it
    replaces, and computing them after the fact is how they stop getting
    computed.

    The FINAL model is fitted on everything, including the gameweeks used as
    walk-forward test folds. That is correct — those folds measured
    generalisation, and having measured it there is no reason to discard the
    data. It does mean the metrics describe a model fitted on slightly less
    than the one being shipped, which is the conventional and slightly
    uncomfortable arrangement.
    """
    df = load_training_set(seasons=seasons)
    if len(df) == 0:
        raise ValueError(
            "no labelled rows. Before the first gameweek of a season there is "
            "nothing to train on — this is expected pre-season, not a bug."
        )

    features = feature_columns(df)
    log.info(
        "training on %d rows, %d features, seasons %s",
        len(df),
        len(features),
        sorted(df["season"].unique().to_list()),
    )

    metrics: dict[str, float] = {}
    if evaluate:
        results = minutes_model.walk_forward_evaluate(
            walk_forward_splits(df),
            features=features,
            holdout_gameweeks=holdout_gameweeks,
        )
        # Weighted by fold size — a blank gameweek has far fewer rows and
        # should not count equally. NaN folds are dropped rather than
        # propagated: a fold where the model predicted no band-2 rows has an
        # undefined precision, and letting that poison the aggregate hides
        # every other fold's number.
        total = results["n"].sum()
        for col in ("accuracy", "recall_60", "precision_60", "logloss_play", "logloss_60"):
            valid = results.filter(pl.col(col).is_not_nan() & pl.col(col).is_not_null())
            if len(valid) == 0:
                continue
            metrics[col] = as_float((valid[col] * valid["n"]).sum()) / as_float(valid["n"].sum())
            metrics[f"{col}_folds"] = float(len(valid))
        metrics["folds_total"] = float(len(results))
        metrics["eval_rows"] = float(total)
        log.info(
            "walk-forward: acc=%.3f recall_60=%.3f precision_60=%.3f over %d folds",
            metrics.get("accuracy", float("nan")),
            metrics.get("recall_60", float("nan")),
            metrics.get("precision_60", float("nan")),
            len(results),
        )

    # Hold out the last gameweeks for early stopping, same as each fold does.
    # Fitting the final model with a fixed iteration count taken from the folds
    # would be defensible too, but the folds disagree with each other by a
    # factor of two, so there is no single count to take.
    gws = sorted(df["gameweek"].unique())
    cutoff = gws[-holdout_gameweeks] if len(gws) > holdout_gameweeks + 1 else None
    fit_df = df.filter(pl.col("gameweek") < cutoff) if cutoff else df
    valid_df = df.filter(pl.col("gameweek") >= cutoff) if cutoff else None

    model = minutes_model.fit(fit_df, features=features, valid=valid_df)

    code = artifacts.code_version(SOURCE_FILES)
    data = artifacts.data_version(df, features)

    manifest = Manifest(
        model_name=MODEL_NAME,
        model_version=artifacts.make_version(MODEL_NAME, code, data),
        trained_at=datetime.now(UTC).isoformat(),
        seasons=sorted(df["season"].unique().to_list()),
        train_rows=len(df),
        train_gameweek_min=as_int(df["gameweek"].min()),
        train_gameweek_max=as_int(df["gameweek"].max()),
        features=features,
        feature_count=len(features),
        params=dict(minutes_model.PARAMS),
        metrics=metrics,
        code_version=code,
        data_version=data,
        extras={
            "formulation": "ordinal_decomposition",
            "best_iterations": model.best_iterations,
            "holdout_gameweeks": holdout_gameweeks,
        },
    )

    if persist:
        save(model, manifest)

    return model, manifest


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _, m = train()
    print(json.dumps({k: v for k, v in asdict(m).items() if k != "features"}, indent=2))
