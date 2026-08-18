"""The minutes model.

Most of the variance in FPL points is "did he play", not "how well did he
play". A 12-point haul and a blank differ mostly by selection, and selection is
knowable in advance in a way that finishing is not. So this is the component
worth the most effort, and the one where public models are weakest — they
ingest only the FPL API and therefore cannot see that a player went ninety
minutes in Bergamo on Thursday.

ORDINAL, NOT MULTICLASS. The three bands are ordered: 0 (did not play),
1 (1-59), 2 (60+). Plain multiclass throws that away and will happily be more
confident in band 0 than band 1 for a player who is clearly starting. The
formulation here is the standard ordinal decomposition — two binary models,

    P(band >= 1) = P(played at all)
    P(band >= 2) = P(played 60+)

with band probabilities recovered by differencing. That is two well-posed
binary problems instead of one badly-posed three-way one, and it guarantees the
monotonicity a manager expects: nothing can be more likely to reach 60 minutes
than to appear at all.

The differencing can produce a small negative for band 1 when the two models
disagree at the margin. That is clipped and renormalised — see _bands_from_
cumulative.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from fpl_modelling.baseline import evaluate_bands
from fpl_modelling.data import CATEGORICAL, Split, feature_columns, prepare

log = logging.getLogger(__name__)

# Deliberately conservative. The training set is one season — roughly 27k rows
# once the early gameweeks are excluded, and heavily imbalanced toward
# non-appearance. Deep trees on that will memorise players rather than learn
# selection, and the walk-forward evaluation will not always catch it because
# the same players recur in every fold.
PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "num_threads": 0,
}

NUM_BOOST_ROUND = 400
EARLY_STOPPING = 50


@dataclass
class MinutesModel:
    """Two boosters and the feature list they were fitted on.

    The feature list is carried rather than recomputed because the matrix gains
    columns over time — feat_player_form is generated from a Jinja loop, so
    adding a window silently widens it. A model applied to a wider matrix than
    it was trained on must fail loudly, not reorder columns and continue.
    """

    features: list[str]
    played: lgb.Booster | None = None
    played_60: lgb.Booster | None = None
    best_iterations: dict[str, int] = field(default_factory=dict)

    def predict_cumulative(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """P(band >= 1) and P(band >= 2)."""
        if self.played is None or self.played_60 is None:
            raise RuntimeError("model is not fitted")

        missing = [c for c in self.features if c not in df.columns]
        if missing:
            raise ValueError(f"features absent from prediction frame: {missing}")

        x = prepare(df, self.features).select(self.features).to_pandas()
        for col in CATEGORICAL:
            if col in x.columns:
                x[col] = x[col].astype("category")

        # Booster.predict is typed as possibly returning a list. asarray is
        # a no-op for the ndarray case and correct for the other.
        return (
            np.asarray(
                self.played.predict(x, num_iteration=self.played.best_iteration),
                dtype=float,
            ),
            np.asarray(
                self.played_60.predict(x, num_iteration=self.played_60.best_iteration),
                dtype=float,
            ),
        )

    def predict_bands(self, df: pl.DataFrame) -> np.ndarray:
        """Per-row probabilities for bands 0, 1, 2. Shape (n, 3)."""
        p_play, p_60 = self.predict_cumulative(df)
        return _bands_from_cumulative(p_play, p_60)

    def predict_expected_minutes(self, df: pl.DataFrame) -> np.ndarray:
        """Expected minutes, for scaling the per-90 rates in other components.

        Band midpoints rather than band labels: a player predicted to play is
        not usefully described as "1", and the goals model needs a minutes
        figure to multiply an xG-per-90 rate by. 30 for band 1 is the midpoint
        of 1-59; 80 for band 2 reflects that most 60+ appearances are full
        matches but some are substitutions on the hour.
        """
        bands = self.predict_bands(df)
        return bands[:, 1] * 30.0 + bands[:, 2] * 80.0


def _bands_from_cumulative(
    p_play: np.ndarray,
    p_60: np.ndarray,
) -> np.ndarray:
    """Difference two cumulative probabilities into three band probabilities.

    P(0) = 1 - P(play)
    P(1) = P(play) - P(60)
    P(2) = P(60)

    The two models are fitted independently, so nothing forces P(60) <= P(play).
    Where they cross, P(1) goes negative. Clipping to zero and renormalising is
    the conventional fix and is honest about what happened: the models disagree,
    and the answer is somewhere between them.
    """
    p_60 = np.minimum(p_60, p_play)
    bands = np.column_stack([1.0 - p_play, p_play - p_60, p_60])
    bands = np.clip(bands, 0.0, None)
    normalised: np.ndarray = bands / bands.sum(axis=1, keepdims=True)
    return normalised


def _fit_binary(
    train: pl.DataFrame,
    valid: pl.DataFrame | None,
    features: list[str],
    target: np.ndarray,
    valid_target: np.ndarray | None,
    label: str,
) -> tuple[lgb.Booster, int]:
    x = prepare(train, features).select(features).to_pandas()
    for col in CATEGORICAL:
        if col in x.columns:
            x[col] = x[col].astype("category")

    dtrain = lgb.Dataset(x, label=target, free_raw_data=False)

    valid_sets: list[lgb.Dataset] = []
    callbacks: list[Callable[..., Any]] = []
    if valid is not None and valid_target is not None and len(valid) > 0:
        xv = prepare(valid, features).select(features).to_pandas()
        for col in CATEGORICAL:
            if col in xv.columns:
                xv[col] = xv[col].astype("category")
        valid_sets = [lgb.Dataset(xv, label=valid_target, reference=dtrain)]
        callbacks = [lgb.early_stopping(EARLY_STOPPING, verbose=False)]

    booster = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=valid_sets,
        callbacks=callbacks,
    )
    best = booster.best_iteration or NUM_BOOST_ROUND
    log.debug("%s fitted, best_iteration=%d", label, best)
    return booster, best


def fit(
    train: pl.DataFrame,
    features: list[str] | None = None,
    valid: pl.DataFrame | None = None,
) -> MinutesModel:
    """Fit both stages.

    ``valid`` enables early stopping. In walk-forward evaluation it must NOT be
    the test fold — that tunes iteration count on the data being scored, which
    is a subtle leak and reliably flatters the model by a point or two. Hold out
    the last gameweek of the training window instead, which is what
    walk_forward_evaluate does.
    """
    features = features or feature_columns(train)

    y_play = (train["minutes_band"].to_numpy() >= 1).astype(int)
    y_60 = (train["minutes_band"].to_numpy() >= 2).astype(int)

    vy_play = vy_60 = None
    if valid is not None and len(valid) > 0:
        vy_play = (valid["minutes_band"].to_numpy() >= 1).astype(int)
        vy_60 = (valid["minutes_band"].to_numpy() >= 2).astype(int)

    played, it_play = _fit_binary(train, valid, features, y_play, vy_play, "played")
    played_60, it_60 = _fit_binary(train, valid, features, y_60, vy_60, "played_60")

    return MinutesModel(
        features=features,
        played=played,
        played_60=played_60,
        best_iterations={"played": it_play, "played_60": it_60},
    )


def walk_forward_evaluate(
    splits: Iterable[Split],
    features: list[str] | None = None,
    holdout_gameweeks: int = 2,
) -> pl.DataFrame:
    """Fit and score one model per fold.

    The last ``holdout_gameweeks`` of each training window are held out for
    early stopping rather than used for fitting. That costs a little training
    data and buys an iteration count that was not chosen by looking at the
    answer.

    Refitting per fold is the honest thing and also the slow thing — 32 folds
    times two boosters. It is a few minutes on this data size, which is worth
    paying to avoid the alternative: fitting once on everything and scoring on
    a subset of what the model has already seen.
    """
    rows = []

    for split in splits:
        train = split.train
        gws = sorted(train["gameweek"].unique())

        if len(gws) > holdout_gameweeks + 1:
            cutoff = gws[-holdout_gameweeks]
            fit_df = train.filter(pl.col("gameweek") < cutoff)
            valid_df = train.filter(pl.col("gameweek") >= cutoff)
        else:
            fit_df, valid_df = train, None

        cols = features or feature_columns(fit_df)
        model = fit(fit_df, features=cols, valid=valid_df)

        bands = model.predict_bands(split.test)
        y_pred = bands.argmax(axis=1)
        y_true = split.test["minutes_band"].to_numpy()

        metrics = evaluate_bands(y_true, y_pred)

        # Log loss on the ordinal stages separately. Accuracy hides which stage
        # is failing; these do not.
        p_play, p_60 = model.predict_cumulative(split.test)
        eps = 1e-7
        ty_play = (y_true >= 1).astype(float)
        ty_60 = (y_true >= 2).astype(float)
        metrics["logloss_play"] = float(
            -np.mean(ty_play * np.log(p_play + eps) + (1 - ty_play) * np.log(1 - p_play + eps))
        )
        metrics["logloss_60"] = float(
            -np.mean(ty_60 * np.log(p_60 + eps) + (1 - ty_60) * np.log(1 - p_60 + eps))
        )

        rows.append(
            {
                "season": split.season,
                "gameweek": split.test_gameweek,
                "model": "minutes_ordinal",
                **metrics,
                **{f"iters_{k}": v for k, v in model.best_iterations.items()},
            }
        )
        log.info(
            "gw%02d acc=%.3f recall_60=%.3f precision_60=%.3f",
            split.test_gameweek,
            metrics["accuracy"],
            metrics["recall_60"],
            metrics["precision_60"],
        )

    return pl.DataFrame(rows)


def feature_importance(model: MinutesModel, stage: str = "played_60") -> pl.DataFrame:
    """Gain-based importance for one stage.

    Gain rather than split count: split count rewards high-cardinality columns
    for being splittable, which says nothing about whether the splits help.

    Read this against expectation. If prior_appearances dominates and the load
    and form features barely register, the model is mostly rediscovering the
    prior_rate baseline and the feature layer is not earning its keep.
    """
    booster = getattr(model, stage)
    if booster is None:
        raise RuntimeError(f"stage {stage} is not fitted")

    return pl.DataFrame(
        {
            "feature": booster.feature_name(),
            "gain": booster.feature_importance(importance_type="gain"),
        }
    ).sort("gain", descending=True)
