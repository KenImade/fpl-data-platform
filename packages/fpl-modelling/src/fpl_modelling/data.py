"""Reading a training matrix, and splitting it the only way that is honest.

Three jobs:

1. Describe a matrix — where it lives, which of its columns are identity, which
   are labels, and which exist only on some rows. That description is a
   MatrixSpec, and there is one per model family.

2. Load it and drop the columns that cannot be used for the season being
   trained on.

3. Split walk-forward. Random splits leak — the same fixture appears on both
   sides of the boundary through rolling features, and a model validated that
   way looks excellent and then disappoints. Every split here is a point in
   time, with everything before it training and one gameweek after it testing.

WHY A SPEC RATHER THAN MODULE CONSTANTS. This file used to hardcode the player
matrix: one table name, one identity tuple, one label tuple. The team defence
matrix has none of the same columns, and the attacking and bonus models will
have others again. A spec per matrix keeps the loading, splitting and column
selection identical across all of them while letting the one thing that
genuinely differs — the shape — differ in one place.

WHY TRAINING AND PREDICTION TABLES ARE NAMED SEPARATELY. For the player matrix
they are the same table read with mirror-image predicates: kickoff in the past
trains, kickoff in the future is scored. For the team matrix they are not. The
training set is a view already filtered to played fixtures, while scoring reads
the spine it was filtered from. Both arrangements are correct and the spec says
which is in use rather than leaving a reader to infer it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

log = logging.getLogger(__name__)

# dbt's +schema config APPENDS to the target schema, so the built table is
# <target>_features.feat_training_set rather than features.feat_training_set.
# Configurable because that prefix differs between dev and production, and a
# hardcoded one fails at deploy rather than at import.
FEATURES_SCHEMA = os.environ.get("FEATURES_SCHEMA", "analytics_features")


def as_int(value: Any) -> int:
    """Narrow a polars aggregate to int.

    polars scalars type as a wide union — mypy cannot know that .min() on an
    integer column returns an int rather than a date or a Decimal. Asserting
    it here rather than casting at each call site keeps the claim in one
    place, and raises on None rather than producing a confusing TypeError
    three frames later.
    """
    if value is None:
        raise ValueError("expected an integer, got None")
    return int(value)


def as_float(value: Any) -> float:
    """Narrow a polars aggregate to float. See as_int."""
    if value is None:
        raise ValueError("expected a float, got None")
    return float(value)


@dataclass(frozen=True)
class MatrixSpec:
    """The shape of one training matrix.

    ``identity`` is grain and bookkeeping: keys, timestamps, anything a model
    must not fit on but a caller needs to join back.

    ``labels`` are outcomes, known only after kickoff. Anything here appearing
    on the feature side is a leak, which is why they are named in one place and
    excluded by name rather than by convention.

    ``optional`` are columns present on some rows and structurally absent on
    others — the capture-sourced block on the player matrix, null for every
    reconstructed season. Excluded by default, because a model trained across
    both paths would learn their absence as a season indicator rather than as a
    fact about football.

    ``labelled_predicate`` is None where the source table is labelled by
    construction. That is not the same as an empty predicate: it records that
    the filtering happened upstream in dbt, so nobody goes looking here for a
    filter that is not missing.
    """

    name: str
    training_table: str
    identity: tuple[str, ...]
    labels: tuple[str, ...]
    prediction_table: str | None = None
    optional: tuple[str, ...] = ()
    categorical: tuple[str, ...] = ()
    labelled_predicate: str | None = None
    unlabelled_predicate: str | None = None
    order_by: str = ""

    def __post_init__(self) -> None:
        overlap = set(self.identity) & set(self.labels)
        if overlap:
            raise ValueError(
                f"{self.name}: {sorted(overlap)} declared as both identity and label. "
                "A column that is both will be excluded from features twice and "
                "reported in neither place."
            )

    @property
    def training_relation(self) -> str:
        return f"{FEATURES_SCHEMA}.{self.training_table}"

    @property
    def prediction_relation(self) -> str:
        return f"{FEATURES_SCHEMA}.{self.prediction_table or self.training_table}"

    def excluded(self, include_optional: bool = False) -> set[str]:
        out = set(self.identity) | set(self.labels)
        if not include_optional:
            out |= set(self.optional)
        return out


# --- the player matrix ---------------------------------------------------

PLAYER_MATRIX = MatrixSpec(
    name="player_fixture",
    training_table="feat_training_set",
    identity=(
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
        "last_appearance_at",
        "last_fixture_at",
        "club_last_fixture_at",
        "built_at",
    ),
    labels=(
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
    ),
    optional=(
        "price_tenths",
        "selected_by_percent",
        "status",
        "has_news",
        "chance_of_playing_next",
        "ep_next",
    ),
    categorical=("position",),
    # kickoff_utc in the past is the honest test rather than minutes_band being
    # non-null: an unplayed fixture yields did_appear = false and minutes = 0
    # from the left join, which is indistinguishable from a player who was
    # dropped. Only the clock separates them.
    labelled_predicate="kickoff_utc < now()",
    unlabelled_predicate="kickoff_utc >= now()",
    order_by="gameweek, player_id",
)


# --- the team defence matrix ---------------------------------------------

TEAM_DEFENCE_MATRIX = MatrixSpec(
    name="team_defence",
    training_table="feat_team_defence_training_set",
    prediction_table="feat_team_fixture_spine",
    identity=(
        "snapshot_id",
        "season",
        "gameweek",
        "deadline_utc",
        "match_id",
        "team_code",
        "opponent_code",
        "kickoff_utc",
        "built_at",
    ),
    labels=(
        "goals_against",
        "is_clean_sheet",
        "team_saves",
        "shots_on_target_faced",
        "goals_for",
        "is_played",
    ),
    # No optional block. Every feature here derives from fixtures and Elo, none
    # of it from a deadline capture, so the live and reconstructed seasons carry
    # identical columns. That is why there is one team spine and two player ones.
    optional=(),
    categorical=(),
    # NOT kickoff_utc < now(). The undated 2025/26 GW34-38 fixtures have a null
    # kickoff, and `null < now()` is null, so that predicate would silently drop
    # about an eighth of the only labelled season. The training table is a view
    # already filtered on is_played, so no predicate is needed here at all.
    labelled_predicate=None,
    unlabelled_predicate="not is_played",
    order_by="gameweek, team_code",
)


MATRICES = {spec.name: spec for spec in (PLAYER_MATRIX, TEAM_DEFENCE_MATRIX)}


@dataclass(frozen=True)
class Split:
    """One walk-forward fold.

    ``train`` is everything strictly before ``test_gameweek``; ``test`` is that
    gameweek alone. Both carry identity columns — the caller needs season and
    gameweek to report per-fold metrics, and the grain keys to join predictions
    back.
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


def _read(relation: str, where: Sequence[str], order_by: str) -> pl.DataFrame:
    clause = " and ".join(where) if where else "1 = 1"
    query = f"select * from {relation} where {clause}"
    if order_by:
        query += f" order by {order_by}"
    df = pl.read_database_uri(query=query, uri=_dsn())
    log.info("loaded %d rows from %s", len(df), relation)
    return df


def load_matrix(
    spec: MatrixSpec,
    seasons: list[str] | None = None,
    gameweeks: list[int] | None = None,
    labelled: bool = True,
) -> pl.DataFrame:
    """Read a matrix, either the trainable rows or the scorable ones.

    ``labelled`` selects which side of the boundary to read. The two predicates
    are mirror images where a spec defines both, which is the property that
    keeps an outcome from being scored as if it were unknown — no row can be
    read by both calls.

    ``gameweeks`` narrows to specific rounds, which the serving path wants and
    the training path does not. It lives here rather than as a raw predicate
    passed in by callers, because a caller that can inject SQL can also inject
    a predicate that crosses the labelled boundary — which is the one thing
    this function exists to prevent. Values are coerced to int for the same
    reason.
    """
    relation = spec.training_relation if labelled else spec.prediction_relation
    predicate = spec.labelled_predicate if labelled else spec.unlabelled_predicate

    where: list[str] = []
    if seasons:
        quoted = ", ".join(f"'{s}'" for s in seasons)
        where.append(f"season in ({quoted})")
    if gameweeks:
        listed = ", ".join(str(int(gw)) for gw in gameweeks)
        where.append(f"gameweek in ({listed})")
    if predicate:
        where.append(predicate)
    elif labelled:
        log.debug("%s: labelled by construction, no predicate applied", spec.name)

    return _read(relation, where, spec.order_by)


def load_training_set(
    seasons: list[str] | None = None,
    labelled_only: bool = True,
    spec: MatrixSpec = PLAYER_MATRIX,
) -> pl.DataFrame:
    """Read the trainable rows of a matrix.

    ``labelled_only`` false reads the scorable side instead — rows whose
    fixture has not been played. Those exist for every future gameweek in the
    current season, because the spine is built from the fixture list and a row
    exists long before there is an outcome to learn from. They are what the
    prediction path reads; they are not training data.
    """
    return load_matrix(spec, seasons=seasons, labelled=labelled_only)


def feature_columns(
    df: pl.DataFrame,
    spec: MatrixSpec = PLAYER_MATRIX,
    include_optional: bool = False,
) -> list[str]:
    """Everything in the frame usable as a predictor.

    Excludes identity, labels, and — unless asked otherwise — the optional
    block. ``is_reconstructed`` is deliberately KEPT on the player matrix: if
    the frame spans both paths it is the column that stops the model
    attributing the optional block's absence to something else.
    """
    excluded = spec.excluded(include_optional=include_optional)
    cols = [c for c in df.columns if c not in excluded]

    if include_optional:
        missing = [c for c in spec.optional if c not in df.columns]
        if missing:
            raise ValueError(
                f"{spec.name}: optional columns requested but absent: {missing}. "
                f"The matrix probably contains only reconstructed rows."
            )

    unknown_labels = [c for c in spec.labels if c not in df.columns]
    if unknown_labels:
        # Not fatal — a prediction frame legitimately lacks some labels — but
        # worth saying, because a label misspelled in the spec is excluded from
        # nothing and lands silently on the feature side.
        log.debug("%s: labels absent from frame: %s", spec.name, unknown_labels)

    return cols


def walk_forward_splits(
    df: pl.DataFrame,
    min_train_gameweeks: int = 6,
) -> Iterator[Split]:
    """Train on gameweeks 1..k, test on k+1, roll forward.

    ``min_train_gameweeks`` exists because the first few folds are not worth
    scoring. Rolling features are mostly null at GW2 and the positional priors
    in feat_player_form are computed off one round of fixtures, so early folds
    measure the cold-start path rather than the model. A matrix with far fewer
    rows per gameweek — the team matrix has about twenty — wants this set
    higher, since six gameweeks there is barely a hundred training rows.

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


def prepare(
    df: pl.DataFrame,
    features: list[str],
    spec: MatrixSpec = PLAYER_MATRIX,
) -> pl.DataFrame:
    """Cast for LightGBM.

    Categoricals become polars Categorical, which LightGBM handles natively —
    one-hot encoding position would lose the ordinal structure of nothing in
    particular but would also triple the column count for no gain.

    Booleans become integers. Nulls are left alone: LightGBM splits on
    missingness directly, and imputing would destroy the distinction the
    feature layer works hard to preserve — a null rolling rate means no prior
    appearances, which is not the same as a rate of zero.

    A model that CANNOT take nulls — the team defence GLM — must impute for
    itself rather than have this function do it, so that the imputation
    strategy lives next to the model it was chosen for.
    """
    exprs = []
    for col in features:
        dtype = df[col].dtype
        if col in spec.categorical:
            exprs.append(pl.col(col).cast(pl.Categorical))
        elif dtype == pl.Boolean:
            exprs.append(pl.col(col).cast(pl.Int8))
        elif dtype == pl.Decimal:
            exprs.append(pl.col(col).cast(pl.Float64))
        else:
            exprs.append(pl.col(col))
    return df.with_columns(exprs)


# --- backwards-compatible aliases ----------------------------------------
#
# minutes.py, baseline.py and predict.py import these by name. Kept as views
# onto PLAYER_MATRIX rather than as separate declarations, so there is one
# definition of the player matrix and these cannot drift from it.

TRAINING_SET = PLAYER_MATRIX.training_relation
IDENTITY = PLAYER_MATRIX.identity
LABELS = PLAYER_MATRIX.labels
CAPTURE_SOURCED = PLAYER_MATRIX.optional
CATEGORICAL = PLAYER_MATRIX.categorical
