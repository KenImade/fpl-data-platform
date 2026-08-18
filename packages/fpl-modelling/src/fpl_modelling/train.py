"""Fitting the model that gets served, and recording enough to reproduce it.

walk_forward_evaluate answers "is this model any good". This answers "which
model is running, fitted on what, and can we rebuild it". Different questions,
and conflating them is how a warehouse ends up serving numbers nobody can
explain six months later.

WHAT A VERSION IS. A model version names three things together: the code that
fitted it, the data it saw, and the features it saw. Change any one and the
predictions change, so all three go into the hash. The alternative — a
monotonic counter, or a timestamp — tells you a model is different without
telling you how, which is exactly the information you want when a prediction
looks wrong.

WHAT IS PERSISTED. Two boosters, the feature list, the training bounds, and the
walk-forward metrics that justified shipping it. The metrics travel with the
artifact rather than living in a notebook, because "we thought it was better"
is not a claim you can check later.

THE ARTIFACT DOES NOT GO IN POSTGRES. Boosters are megabytes and immutable;
object storage handles that and the database should not. What goes in Postgres
is the version string on every prediction row, which is the pointer.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import polars as pl

from fpl_modelling import minutes as minutes_model
from fpl_modelling.data import (
    as_float,
    as_int,
    feature_columns,
    load_training_set,
    walk_forward_splits,
)

log = logging.getLogger(__name__)

ARTIFACT_PREFIX = "models/minutes"


@dataclass(frozen=True)
class TrainingManifest:
    """Everything needed to say what this model is.

    Written alongside the boosters and read back at prediction time. The
    feature list in particular is load-bearing: feat_player_form is generated
    from a Jinja loop, so adding a window silently widens the matrix. A model
    scored against a wider frame than it was fitted on must fail rather than
    quietly reorder columns.
    """

    model_version: str
    model_name: str
    trained_at: str
    seasons: list[str]
    train_rows: int
    train_gameweek_min: int
    train_gameweek_max: int
    features: list[str]
    feature_count: int
    params: dict[str, Any]
    best_iterations: dict[str, int]
    metrics: dict[str, float]
    code_version: str


def _code_version() -> str:
    """Hash of the modelling source that produced this artifact.

    Not the git SHA — the working tree may be dirty, and a model fitted from
    uncommitted code is exactly the one you most need to identify. Hashing the
    files that define the model catches that; a commit hash does not.
    """
    here = Path(__file__).parent
    h = hashlib.sha256()
    for name in sorted(("data.py", "minutes.py", "train.py")):
        path = here / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _data_version(df: pl.DataFrame, features: list[str]) -> str:
    """Hash of what the model saw.

    Row count, season and gameweek bounds, and the feature list. Not the data
    itself — hashing 27k rows on every run is wasteful, and the warehouse is
    rebuilt deterministically from immutable inputs anyway, so the bounds
    identify it.

    The feature list is included because a model fitted on the same rows with a
    different feature set is a different model, and the row bounds alone would
    call them identical.
    """
    payload = json.dumps(
        {
            "rows": len(df),
            "seasons": sorted(df["season"].unique().to_list()),
            "gw_min": as_int(df["gameweek"].min()),
            "gw_max": as_int(df["gameweek"].max()),
            "features": sorted(features),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def make_version(code: str, data: str) -> str:
    """`minutes-<code>-<data>`. Readable, sortable enough, and diagnostic.

    Given two versions you can see at a glance whether the code changed, the
    data changed, or both — which is the first question when predictions move.
    """
    return f"minutes-{code}-{data}"


def _store() -> Any:
    """Object storage client.

    S3-compatible, so MinIO locally and whatever the deployment uses in
    production without a code change. Imported lazily because the training path
    is useful without it — evaluation and inspection need no storage at all.
    """
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin"),
    )


def _bucket() -> str:
    return os.environ.get("MODEL_BUCKET", "fpl-models")


def save(model: minutes_model.MinutesModel, manifest: TrainingManifest) -> str:
    """Write the artifact to object storage, return its key.

    One tarball rather than several objects: the boosters and the manifest are
    only meaningful together, and a partial read that returns a model without
    its feature list is worse than a failed read.
    """
    if model.played is None or model.played_60 is None:
        # Saving an unfitted model would write a well-formed artifact that
        # fails only when something tries to predict with it.
        raise RuntimeError("cannot save an unfitted model")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        model.played.save_model(str(tmp_path / "played.txt"))
        model.played_60.save_model(str(tmp_path / "played_60.txt"))
        (tmp_path / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2))

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("played.txt", "played_60.txt", "manifest.json"):
                tar.add(tmp_path / name, arcname=name)
        buf.seek(0)

        key = f"{ARTIFACT_PREFIX}/{manifest.model_version}.tar.gz"
        _store().put_object(Bucket=_bucket(), Key=key, Body=buf.getvalue())

    log.info("saved %s (%d features)", key, manifest.feature_count)
    return key


def load(model_version: str) -> tuple[minutes_model.MinutesModel, TrainingManifest]:
    """Read an artifact back.

    The prediction path calls this rather than refitting. A prediction that
    retrains is not reproducible and is also slow at exactly the moment it
    needs not to be.
    """
    key = f"{ARTIFACT_PREFIX}/{model_version}.tar.gz"
    obj = _store().get_object(Bucket=_bucket(), Key=key)
    buf = io.BytesIO(obj["Body"].read())

    with tarfile.open(fileobj=buf, mode="r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        tar.extractall(tmp, filter="data")
        tmp_path = Path(tmp)
        manifest = TrainingManifest(**json.loads((tmp_path / "manifest.json").read_text()))
        model = minutes_model.MinutesModel(
            features=manifest.features,
            played=lgb.Booster(model_file=str(tmp_path / "played.txt")),
            played_60=lgb.Booster(model_file=str(tmp_path / "played_60.txt")),
            best_iterations=manifest.best_iterations,
        )

    log.info("loaded %s trained %s", model_version, manifest.trained_at)
    return model, manifest


def train(
    seasons: list[str] | None = None,
    holdout_gameweeks: int = 2,
    evaluate: bool = True,
    persist: bool = True,
) -> tuple[minutes_model.MinutesModel, TrainingManifest]:
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

    manifest = TrainingManifest(
        model_version=make_version(_code_version(), _data_version(df, features)),
        model_name="minutes_ordinal",
        trained_at=datetime.now(UTC).isoformat(),
        seasons=sorted(df["season"].unique().to_list()),
        train_rows=len(df),
        train_gameweek_min=as_int(df["gameweek"].min()),
        train_gameweek_max=as_int(df["gameweek"].max()),
        features=features,
        feature_count=len(features),
        params=dict(minutes_model.PARAMS),
        best_iterations=model.best_iterations,
        metrics=metrics,
        code_version=_code_version(),
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
