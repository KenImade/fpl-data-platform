"""The team defensive model.

Clean sheets, goals conceded and saves are not three models. They all fall out
of one team-level distribution over goals conceded: P(0) is the clean sheet, the
concession penalty is an expectation over that distribution, and saves scale off
the opponent's shot volume. Building them together is less work than building
them separately and keeps them mutually consistent — a keeper cannot have both a
high clean-sheet probability and a high expected concession.

MARGINAL, NOT BIVARIATE. The classic formulation is a bivariate Poisson with a
Dixon-Coles correction for low scores. That machinery exists to get JOINT
outcomes right, and nothing in e_points consumes a joint outcome — each club's
marginal distribution is all the scoring rules ever ask for. Two independent
marginals, one row per team-fixture, and the correlation structure disappears
along with the correction.

A GLM, NOT A GBM. This matrix is about 700 rows against ten features. The
minutes model has forty times as many. LightGBM on 700 rows would memorise the
twenty clubs, and walk-forward would not reliably catch it because the same
twenty clubs recur in every fold. A Poisson GLM with a log link and L2 is the
right amount of model for the data, and the Elo backbone is doing most of the
work regardless.

THIS MODULE EMITS A RATE AND NOTHING ELSE. The pmf is here because it is
arithmetic on that rate. The translation from a distribution to POINTS is not:
four points for a defender's clean sheet and minus one per two conceded are FPL
rules, they change when FPL changes them, and they belong in fpl_core alongside
the rules they follow. The test is whether a rule change would edit the code —
exp(-lambda) would not, multiplying by four would.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fpl_modelling.data import TEAM_DEFENCE_MATRIX, Split, prepare

log = logging.getLogger(__name__)

TARGET = "goals_against"

# Deliberately narrower than feature_columns would return.
#
# elo_diff is EXCLUDED even though it is the strongest single column on the
# spine, because it is exactly own_elo_current - opp_elo_current. Including all
# three makes the design matrix singular; including only the difference asserts
# that a strong defence facing a strong attack concedes the same as a weak one
# facing a weak attack, which is false — goals conceded depends on the two sides
# separately, not only on the gap between them.
#
# The home/away xG splits are excluded too. They are season-to-date averages
# over roughly nine matches each, and is_home plus the 5- and 10-fixture windows
# already carry what they would add. At this sample size a feature has to earn
# its degree of freedom.
FEATURES: tuple[str, ...] = (
    "is_home",
    "own_elo_current",
    "opp_elo_current",
    "own_xg_against_5",
    "own_xg_against_10",
    "opp_xg_for_5",
    "opp_xg_for_10",
    "matches_prior_14d",
    "days_rest",
    "has_congestion",
)

# The null model that matters: everything the feature layer adds beyond a
# rating both clubs already had before the season started.
ELO_ONLY: tuple[str, ...] = ("own_elo_current", "opp_elo_current")

# P(k > 10) is below 1e-6 at any rate this model can produce. Truncating there
# costs nothing and bounds every expectation computed from the pmf.
MAX_GOALS = 10

# L2 strength. Searched rather than assumed — at 700 rows the difference
# between 0.1 and 10 is not cosmetic.
ALPHA_GRID: tuple[float, ...] = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
DEFAULT_ALPHA = 1.0


# --- the distribution ----------------------------------------------------


def pmf(lam: np.ndarray, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Poisson pmf over 0..max_goals, one row per rate. Shape (n, max_goals+1).

    Built by the recurrence P(k) = P(k-1) * lambda / k rather than from the
    closed form, which avoids a factorial and avoids scipy. The package needs
    exactly one new dependency for this model and it is scikit-learn; adding a
    second for four lines of arithmetic would be a poor trade.
    """
    rates = np.asarray(lam, dtype=float)
    if np.any(rates <= 0):
        raise ValueError("rates must be positive; a log link should guarantee this")

    out = np.empty((rates.shape[0], max_goals + 1), dtype=float)
    out[:, 0] = np.exp(-rates)
    for k in range(1, max_goals + 1):
        out[:, k] = out[:, k - 1] * rates / k
    return out


def clean_sheet_prob(lam: np.ndarray) -> np.ndarray:
    """P(0 conceded). The pmf at zero, which is just exp(-lambda).

    This is a probability, not points. Multiplying it by four for a defender
    and by one for a midfielder happens in fpl_core, where the number four
    lives next to the rule that produced it.
    """
    return np.exp(-np.asarray(lam, dtype=float))


# --- the model -----------------------------------------------------------


def _design(df: pl.DataFrame, features: Sequence[str]) -> np.ndarray:
    """Feature frame to float matrix.

    prepare() casts booleans to integers and decimals to floats but leaves
    nulls alone, because LightGBM splits on missingness directly. A GLM cannot,
    so everything is widened to float64 here and the nulls become NaN for the
    imputer in the pipeline to handle. That is a deliberate departure from the
    house rule, made where the model that needs it can be seen.
    """
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ValueError(f"features absent from frame: {missing}")

    cast = prepare(df, list(features), TEAM_DEFENCE_MATRIX)
    return cast.select([pl.col(c).cast(pl.Float64) for c in features]).to_numpy()


@dataclass
class DefenceModel:
    """A fitted rate model, and the feature list it was fitted on.

    The feature list is carried rather than recomputed for the same reason the
    minutes model carries one: the matrix gains columns over time, and a model
    applied to a wider frame than it was trained on must fail loudly rather
    than reorder columns and continue.
    """

    features: list[str]
    alpha: float
    pipeline: Pipeline | None = None

    @classmethod
    def fit(
        cls,
        train: pl.DataFrame,
        features: Sequence[str] = FEATURES,
        alpha: float = DEFAULT_ALPHA,
    ) -> DefenceModel:
        cols = list(features)
        pipeline = Pipeline(
            [
                # Median rather than mean: days_rest is bounded above at 14 and
                # piles up there, so the mean sits somewhere no fixture is.
                # keep_empty_features because a fold early in a season can have
                # a column that is null throughout, and silently dropping it
                # would change the matrix width between fit and predict.
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                # Standardising is not cosmetic here: L2 penalises coefficients
                # on their own scale, and Elo runs in the low thousands while
                # is_home is zero or one. Unscaled, the penalty would fall
                # almost entirely on the binary features.
                ("scale", StandardScaler()),
                ("glm", PoissonRegressor(alpha=alpha, max_iter=2000)),
            ]
        )
        pipeline.fit(_design(train, cols), train[TARGET].to_numpy())
        return cls(features=cols, alpha=alpha, pipeline=pipeline)

    def rate(self, df: pl.DataFrame) -> np.ndarray:
        """Expected goals conceded, per team-fixture. The model's only output."""
        if self.pipeline is None:
            raise RuntimeError("model is not fitted")
        return np.asarray(self.pipeline.predict(_design(df, self.features)), dtype=float)

    def coefficients(self) -> pl.DataFrame:
        """Standardised coefficients, largest absolute effect first.

        Read these against expectation. own_elo_current should be negative — a
        better defence concedes fewer — and opp_elo_current positive. If those
        signs are reversed, the two feat_team_strength joins in the spine have
        been swapped, which is an error that produces no exception anywhere.
        """
        if self.pipeline is None:
            raise RuntimeError("model is not fitted")
        glm: Any = self.pipeline.named_steps["glm"]
        return (
            pl.DataFrame({"feature": self.features, "coefficient": glm.coef_})
            .with_columns(pl.col("coefficient").abs().alias("magnitude"))
            .sort("magnitude", descending=True)
            .drop("magnitude")
        )


# --- evaluation ----------------------------------------------------------


def poisson_deviance(y: np.ndarray, mu: np.ndarray) -> float:
    """Mean unit deviance. The loss the model is actually fitted on.

    The y*log(y/mu) term is zero at y = 0 by convention and by limit, which
    matters here because a clean sheet is the single most common outcome.
    """
    y = np.asarray(y, dtype=float)
    mu = np.asarray(mu, dtype=float)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / mu), 0.0)
    return float(2.0 * np.mean(term - (y - mu)))


def dispersion(y: np.ndarray, mu: np.ndarray, n_features: int) -> float:
    """Pearson dispersion. Poisson assumes 1.

    Team-match goals usually land between 1.05 and 1.15, which is fine. Above
    roughly 1.3 the marginals are too thin in the tail, and the tail is exactly
    where the concession penalty lives — so that is the number that would send
    this model to a negative binomial rather than a judgement about fit
    elsewhere.
    """
    y = np.asarray(y, dtype=float)
    mu = np.asarray(mu, dtype=float)
    dof = max(len(y) - n_features - 1, 1)
    return float(np.sum((y - mu) ** 2 / mu) / dof)


def evaluate(y: np.ndarray, lam: np.ndarray, n_features: int) -> dict[str, float]:
    """Fit on the rate, and calibration on the clean sheet.

    Deviance is the loss, but nobody has an intuition for it. The numbers a
    manager feels are the clean-sheet ones — Brier score for sharpness, and the
    gap between mean predicted P(0) and the observed rate for bias. A model can
    have a respectable deviance while systematically over-predicting clean
    sheets, and only calibration_gap shows it.
    """
    y = np.asarray(y, dtype=float)
    p0 = clean_sheet_prob(lam)
    cs = (y == 0).astype(float)
    eps = 1e-7

    observed = float(cs.mean())
    predicted = float(p0.mean())

    return {
        "n": float(len(y)),
        "deviance": poisson_deviance(y, lam),
        "mae": float(np.mean(np.abs(y - lam))),
        "mean_rate": float(np.mean(lam)),
        "mean_actual": float(np.mean(y)),
        "brier_cs": float(np.mean((p0 - cs) ** 2)),
        "logloss_cs": float(
            -np.mean(cs * np.log(p0 + eps) + (1 - cs) * np.log(1 - p0 + eps))
        ),
        "cs_predicted": predicted,
        "cs_observed": observed,
        "calibration_gap": predicted - observed,
        "dispersion": dispersion(y, lam, n_features),
    }


def select_alpha(
    train: pl.DataFrame,
    features: Sequence[str] = FEATURES,
    grid: Sequence[float] = ALPHA_GRID,
    holdout_gameweeks: int = 4,
) -> float:
    """Pick L2 strength on a holdout INSIDE the training window.

    Never on the test fold. Tuning regularisation against the data being scored
    is a subtle leak and reliably flatters the model — the same reason the
    minutes model holds out its own gameweeks for early stopping rather than
    watching the test set.

    Falls back to DEFAULT_ALPHA when the window is too short to split, which
    happens in the earliest folds and is not worth failing over.
    """
    gws = sorted(train["gameweek"].unique())
    if len(gws) <= holdout_gameweeks + 1:
        return DEFAULT_ALPHA

    cutoff = gws[-holdout_gameweeks]
    inner_fit = train.filter(pl.col("gameweek") < cutoff)
    inner_valid = train.filter(pl.col("gameweek") >= cutoff)
    if len(inner_fit) == 0 or len(inner_valid) == 0:
        return DEFAULT_ALPHA

    best_alpha, best_deviance = DEFAULT_ALPHA, float("inf")
    for alpha in grid:
        model = DefenceModel.fit(inner_fit, features=features, alpha=alpha)
        deviance = poisson_deviance(inner_valid[TARGET].to_numpy(), model.rate(inner_valid))
        if deviance < best_deviance:
            best_alpha, best_deviance = alpha, deviance

    log.debug("alpha=%.3g (deviance=%.4f)", best_alpha, best_deviance)
    return best_alpha


def walk_forward_evaluate(
    splits: Iterable[Split],
    features: Sequence[str] = FEATURES,
    tune: bool = True,
) -> pl.DataFrame:
    """Fit and score one model per fold, against two baselines.

    ``constant`` predicts the training mean for every fixture — the league's
    average concession rate, ignoring who is playing whom. Beating it is the
    minimum bar.

    ``elo_only`` is the same GLM on nothing but the two Elo ratings. This is the
    baseline that matters: Elo is free, available before a ball is kicked, and
    needs none of the feature layer. If the full model does not clearly beat it,
    the rolling xG windows are not paying for themselves and the honest thing is
    to ship the two-feature version.
    """
    rows: list[dict[str, Any]] = []

    for split in splits:
        y_true = split.test[TARGET].to_numpy()
        y_train = split.train[TARGET].to_numpy()

        alpha = select_alpha(split.train, features=features) if tune else DEFAULT_ALPHA
        model = DefenceModel.fit(split.train, features=features, alpha=alpha)
        rows.append(
            {
                "season": split.season,
                "gameweek": split.test_gameweek,
                "model": "poisson_glm",
                "alpha": alpha,
                **evaluate(y_true, model.rate(split.test), len(features)),
            }
        )

        elo = DefenceModel.fit(split.train, features=ELO_ONLY, alpha=DEFAULT_ALPHA)
        rows.append(
            {
                "season": split.season,
                "gameweek": split.test_gameweek,
                "model": "elo_only",
                "alpha": DEFAULT_ALPHA,
                **evaluate(y_true, elo.rate(split.test), len(ELO_ONLY)),
            }
        )

        constant = np.full(len(y_true), float(y_train.mean()))
        rows.append(
            {
                "season": split.season,
                "gameweek": split.test_gameweek,
                "model": "constant",
                "alpha": float("nan"),
                **evaluate(y_true, constant, 0),
            }
        )

        log.info(
            "gw%02d deviance=%.4f brier_cs=%.4f gap=%+.3f",
            split.test_gameweek,
            rows[-3]["deviance"],
            rows[-3]["brier_cs"],
            rows[-3]["calibration_gap"],
        )

    return pl.DataFrame(rows)


def summarise(results: pl.DataFrame) -> pl.DataFrame:
    """Weighted aggregate across folds.

    Weighted by n, because gameweek sizes vary — a blank gameweek has fewer
    fixtures and should not count equally.
    """
    weighted = ("deviance", "mae", "brier_cs", "logloss_cs", "calibration_gap")
    return (
        results.group_by("model")
        .agg(
            [pl.len().alias("folds"), pl.col("n").sum().alias("rows")]
            + [(pl.col(c) * pl.col("n")).sum().alias(f"_{c}") for c in weighted]
        )
        .with_columns([(pl.col(f"_{c}") / pl.col("rows")).alias(c) for c in weighted])
        .drop([f"_{c}" for c in weighted])
        .sort("deviance")
    )
