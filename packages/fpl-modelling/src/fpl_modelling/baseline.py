"""The baseline the minutes model has to beat.

A model is only worth its complexity if it beats the obvious thing. For minutes
the obvious thing is: assume a player does what they did last time. That sounds
trivially weak and is not — football squads are stable week to week, and a
recency baseline clears 70% accuracy on the three-band problem. Published FPL
models routinely fail to beat it, usually because nobody checked.

Two baselines here, both computed from columns already in the matrix:

- ``modal_band``: the player's most common band across their last five
  appearances, via starts_5 and appearances_5. Pure persistence.
- ``prior_rate``: the base rate for players with the same prior_appearances
  count. Captures the "hasn't played yet" effect and nothing else.

Anything the real model adds has to show up as an improvement over these.
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl

log = logging.getLogger(__name__)

BANDS = (0, 1, 2)


def modal_band_baseline(df: pl.DataFrame) -> pl.DataFrame:
    """Predict the band implied by recent starts.

    starts_5 counts appearances of 60+ minutes in the last five; appearances_5
    counts appearances at all. Between them:

        started most recent five        -> band 2 (60+)
        appeared but rarely started     -> band 1 (1-59)
        no appearances in the window    -> band 0 (did not play)

    The thresholds are deliberately crude. A baseline that needs tuning is not
    a baseline.
    """
    return df.with_columns(
        pl.when(pl.col("appearances_5").is_null() | (pl.col("appearances_5") == 0))
        .then(0)
        .when(pl.col("starts_5") >= 3)
        .then(2)
        .when(pl.col("appearances_5") >= 3)
        .then(1)
        .when(pl.col("starts_5") >= 1)
        .then(2)
        .otherwise(1)
        .alias("baseline_band")
    )


def prior_rate_baseline(train: pl.DataFrame, test: pl.DataFrame) -> pl.DataFrame:
    """Predict the modal band for players with the same prior_appearances.

    Fitted on train, applied to test — so it is a real model, just a very small
    one. It exists to isolate how much of the signal is simply "established
    players play": if the LightGBM model barely beats this, the fixture and
    form features are not earning their place.
    """
    lookup = (
        train.group_by(["prior_appearances", "minutes_band"])
        .agg(pl.len().alias("n"))
        .sort(["prior_appearances", "n"], descending=[False, True])
        .group_by("prior_appearances")
        .first()
        .select(["prior_appearances", pl.col("minutes_band").alias("baseline_band")])
    )

    out = test.join(lookup, on="prior_appearances", how="left")
    # A prior_appearances value unseen in training — rare, and only at the tail.
    # Band 0 is the majority class overall, so it is the safe fallback.
    return out.with_columns(pl.col("baseline_band").fill_null(0))


def evaluate_bands(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    label: str = "",
) -> dict[str, float]:
    """Accuracy overall and per band, plus the two that actually matter.

    Overall accuracy is dominated by band 0 — most players do not play in most
    gameweeks, so predicting 0 everywhere scores well and is useless. The
    numbers to watch are:

    - ``recall_60``: of players who played 60+, how many were predicted to.
      Under-predicting these is what makes a points model too conservative.
    - ``precision_60``: of players predicted to play 60+, how many did. This is
      what a manager feels when a captain gets benched.
    """
    metrics: dict[str, float] = {
        "n": float(len(y_true)),
        "accuracy": float((y_true == y_pred).mean()),
    }

    for band in BANDS:
        actual = y_true == band
        predicted = y_pred == band
        support = int(actual.sum())
        metrics[f"support_{band}"] = float(support)
        metrics[f"recall_{band}"] = (
            float((actual & predicted).sum() / support) if support else float("nan")
        )
        n_predicted = int(predicted.sum())
        metrics[f"precision_{band}"] = (
            float((actual & predicted).sum() / n_predicted) if n_predicted else float("nan")
        )

    metrics["recall_60"] = metrics["recall_2"]
    metrics["precision_60"] = metrics["precision_2"]

    if label:
        log.info(
            "%s: n=%d acc=%.3f recall_60=%.3f precision_60=%.3f",
            label,
            metrics["n"],
            metrics["accuracy"],
            metrics["recall_60"],
            metrics["precision_60"],
        )
    return metrics


def run_baselines(df: pl.DataFrame, splits) -> pl.DataFrame:
    """Score both baselines across every walk-forward fold.

    Returns one row per fold per baseline. Aggregate with care: a simple mean
    across folds weights a 300-row gameweek the same as a 900-row one. Weight
    by n, or report the distribution.
    """
    rows = []

    for split in splits:
        modal = modal_band_baseline(split.test)
        rows.append(
            {
                "season": split.season,
                "gameweek": split.test_gameweek,
                "baseline": "modal_band",
                **evaluate_bands(
                    modal["minutes_band"].to_numpy(),
                    modal["baseline_band"].to_numpy(),
                ),
            }
        )

        prior = prior_rate_baseline(split.train, split.test)
        rows.append(
            {
                "season": split.season,
                "gameweek": split.test_gameweek,
                "baseline": "prior_rate",
                **evaluate_bands(
                    prior["minutes_band"].to_numpy(),
                    prior["baseline_band"].to_numpy(),
                ),
            }
        )

    return pl.DataFrame(rows)


def summarise(results: pl.DataFrame) -> pl.DataFrame:
    """Weighted aggregate across folds.

    Weighted by n, because gameweek sizes vary — a blank gameweek has far fewer
    rows and should not count equally.
    """
    return (
        results.group_by("baseline")
        .agg(
            [
                pl.len().alias("folds"),
                pl.col("n").sum().alias("rows"),
                (pl.col("accuracy") * pl.col("n")).sum().alias("_acc"),
                (pl.col("recall_60") * pl.col("n")).sum().alias("_rec"),
                (pl.col("precision_60") * pl.col("n")).sum().alias("_prec"),
            ]
        )
        .with_columns(
            [
                (pl.col("_acc") / pl.col("rows")).alias("accuracy"),
                (pl.col("_rec") / pl.col("rows")).alias("recall_60"),
                (pl.col("_prec") / pl.col("rows")).alias("precision_60"),
            ]
        )
        .drop(["_acc", "_rec", "_prec"])
    )
