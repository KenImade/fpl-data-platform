"""Reading the training matrix, and splitting it the only way that is honest.

Two jobs:

1. Load ``features.feat_training_set`` and drop the columns that cannot be used
   for the season being trained on.

2. Split walk-forward. Random splits leak — the same fixture appears on both
   sides of the boundary through rolling features, and a model validated that
   way looks excellent and then disappoints. Every split here is a point in
   time, with everything before it training and one gameweek after it testing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass

import polars as pl

log = logging.getLogger(__name__)

FEATURES_SCHEMA = os.environ.get("FEATURES_SCHEMA", "analytics_features")
TRAINING_SET = f"{FEATURES_SCHEMA}.feat_training_set"

# Columns that exist only where a deadline capture does. On reconstructed rows
# they are null for the whole season, so a model trained across both paths
# would learn "no status" as a season indicator rather than as a fact about
# football. Dropped unless training exclusively on live rows.
CAPTURE_SOURCED = (
    "price_tenths",
    "selected_by_percent",
    "status",
    "has_news",
    "chance_of_playing_next",
    "ep_next",
)

# Not features. Identity, grain, and bookkeeping.
IDENTITY = (
    "snapshot_id",
    "season",
    "gameweek",
    "deadline_utc",
    "player_id",
    "player_code",
    "match_id",
    "kickoff_utc",
    "team_code",
    "opponent_code",
    # Raw timestamps. The model wants elapsed time, not an instant — an
    # absolute datetime would let trees split on "before March", which is a
    # fact about the calendar rather than about football.
    "club_last_fixture_at",
    "last_appearance_at",
    "last_fixture_at",
    "built_at",
)

# Outcomes. Known only after kickoff — anything here on the feature side is a
# leak, so they are named in one place and excluded by name rather than by
# convention.
LABELS = (
    "did_appear",
    "minutes",
    "played_60",
    "minutes_band",
    "goals",
    "assists",
    "xg",
    "xa",
    "saves",
    "goals_conceded_on_pitch",
    "defensive_actions",
    "gw_clean_sheets",
    "gw_points",
    "gw_bonus",
    "gw_bps",
)

CATEGORICAL = ("position",)


@dataclass(frozen=True)
class Split:
    """One walk-forward fold.

    ``train`` is everything strictly before ``test_gameweek``; ``test`` is that
    gameweek alone. Both carry identity columns — the caller needs season and
    gameweek to report per-fold metrics, and player_id to join predictions back.
    """

    season: str
    test_gameweek: int
    train: pl.DataFrame
    test: pl.DataFrame

    def __repr__(self) -> str:
        return (
            f"Split({self.season} gw{self.test_gameweek:02d}: "
            f"train={len(self.train)}, test={len(self.test)})"
        )


def _dsn() -> str:
    """Connection string for the modelling role.

    Read-only on features and marts, writes only to predictions. Falls back to
    the dbt role in local development, where the split does not exist.
    """
    return os.environ.get(
        "MODELLING_DB_DSN",
        "postgresql://fpl:fpl@localhost:5432/fpl",
    )


def load_training_set(
    seasons: list[str] | None = None,
    labelled_only: bool = True,
) -> pl.DataFrame:
    """Read the matrix.

    ``labelled_only`` drops rows whose fixture has not been played. Those exist
    for every future gameweek in the current season — the spine is built from
    the fixture list, so a row exists long before there is an outcome to learn
    from. They are what the prediction path reads; they are not training data.

    A fixture is played if it has a minutes_band, which is non-null only where
    the spine found a matching row or established there was none. Before
    kickoff there is neither.
    """
    where = ["1 = 1"]
    if seasons:
        quoted = ", ".join(f"'{s}'" for s in seasons)
        where.append(f"season in ({quoted})")
    if labelled_only:
        # kickoff_utc in the past is the honest test rather than minutes_band
        # being non-null: an unplayed fixture yields did_appear = false and
        # minutes = 0 from the left join, which is indistinguishable from a
        # player who was dropped. Only the clock separates them.
        where.append("kickoff_utc < now()")

    query = f"select * from {TRAINING_SET} where {' and '.join(where)}"
    df = pl.read_database_uri(query=query, uri=_dsn())
    log.info("loaded %d rows from %s", len(df), TRAINING_SET)
    return df


def feature_columns(
    df: pl.DataFrame,
    include_capture_sourced: bool = False,
) -> list[str]:
    """Everything usable as a predictor.

    Excludes identity, labels, and — unless asked otherwise — the capture
    sourced block. ``is_reconstructed`` is deliberately KEPT: if the matrix
    spans both paths it is the column that stops the model attributing the
    capture block's absence to something else.
    """
    excluded = set(IDENTITY) | set(LABELS)
    if not include_capture_sourced:
        excluded |= set(CAPTURE_SOURCED)

    cols = [c for c in df.columns if c not in excluded]

    missing = [c for c in CAPTURE_SOURCED if c not in df.columns]
    if include_capture_sourced and missing:
        raise ValueError(
            f"capture-sourced columns requested but absent: {missing}. "
            f"The matrix probably contains only reconstructed rows."
        )
    return cols


def walk_forward_splits(
    df: pl.DataFrame,
    min_train_gameweeks: int = 6,
) -> Iterator[Split]:
    """Train on gameweeks 1..k, test on k+1, roll forward.

    ``min_train_gameweeks`` exists because the first few folds are not worth
    scoring. Rolling features are mostly null at GW2 and the positional priors
    in feat_player_form are computed off one round of fixtures, so early folds
    measure the cold-start path rather than the model.

    Splits do not cross seasons. A season boundary resets every rolling window
    and the squads change wholesale, so training on the end of one season to
    predict the start of the next is a different problem — one worth solving,
    but not by pretending the gameweeks are contiguous.
    """
    for season in sorted(df["season"].unique()):
        season_df = df.filter(pl.col("season") == season)
        gameweeks = sorted(season_df["gameweek"].unique())

        for gw in gameweeks:
            if gw <= min_train_gameweeks:
                continue

            train = season_df.filter(pl.col("gameweek") < gw)
            test = season_df.filter(pl.col("gameweek") == gw)

            if len(test) == 0 or len(train) == 0:
                continue

            yield Split(
                season=season,
                test_gameweek=int(gw),
                train=train,
                test=test,
            )


def prepare(df: pl.DataFrame, features: list[str]) -> pl.DataFrame:
    """Cast for LightGBM.

    Postgres `numeric` arrives as polars Decimal, which becomes a pandas object
    column and which LightGBM rejects. Every rate, ratio and Elo figure in the
    matrix is numeric, so this is most of the feature set — cast to Float64.
    Precision loss is irrelevant here: these are estimates to eight decimal
    places of quantities known to about one.

    Booleans become integers. Nulls are left alone: LightGBM splits on
    missingness directly, and imputing would destroy the distinction the
    feature layer works to preserve — a null rolling rate means no prior
    appearances, which is not a rate of zero.
    """
    exprs = []
    for col in features:
        dtype = df[col].dtype
        if col in CATEGORICAL:
            exprs.append(pl.col(col).cast(pl.Categorical))
        elif dtype == pl.Boolean:
            exprs.append(pl.col(col).cast(pl.Int8))
        elif dtype == pl.Decimal:
            exprs.append(pl.col(col).cast(pl.Float64))
        else:
            exprs.append(pl.col(col))
    return df.with_columns(exprs)
