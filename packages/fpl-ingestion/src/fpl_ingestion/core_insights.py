"""Bronze layer for the mirrored Core Insights CSVs.

Three source shapes, handled here rather than in dbt because they are *path*
concerns:

  archive   raw/core-insights/2024-2025/{table}/2024-2025-final.csv.gz
            Nested layout, captured once. The season is finished.

  daily     raw/core-insights/{season}/{table}/{YYYY-MM-DD}.csv.gz
            Flat layout, one snapshot per day per active season.

  tarball   raw/core-insights/_full/{day}.tar.gz
            By Gameweek/ and By Tournament/ trees. Not handled here — see
            the note at the bottom of this module.

Column differences between seasons (playerstats is 58 columns in 2024/25 and
87 in 2025/26) are a *schema* concern and belong in dbt staging, where a
union with explicit null-filling reads naturally in SQL.

Everything lands as String. See read_csv() for why.
"""

from __future__ import annotations

import contextlib
import io
import logging
from datetime import UTC, date, datetime

import polars as pl

from fpl_ingestion.storage import ObjectMissing, Store

log = logging.getLogger(__name__)

ARCHIVE_SEASON = "2024-2025"
ACTIVE_SEASONS = ("2025-2026", "2026-2027")

# Available as flat master files, mirrored daily.
DAILY_TABLES = ("players", "teams", "playerstats", "gameweek_summaries")

# Available in the 2024/25 archive. Note `matches` and `playermatchstats`:
# for active seasons these exist only inside By Gameweek/, so the tarball is
# the only route to them.
ARCHIVE_TABLES = ("players", "teams", "playerstats", "matches", "playermatchstats")


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------


def daily_key(season: str, table: str, day: date) -> str:
    return f"raw/core-insights/{season}/{table}/{day:%Y-%m-%d}.csv.gz"


def archive_key(table: str) -> str:
    return f"raw/core-insights/{ARCHIVE_SEASON}/{table}/{ARCHIVE_SEASON}-final.csv.gz"


def bronze_key(table: str, suffix: str) -> str:
    return f"bronze/core-insights/{table}/{suffix}.parquet"


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def read_csv(store: Store, key: str) -> pl.DataFrame:
    """Read a mirrored CSV. Every column as String.

    Deliberate. CSV type inference is per-file, so a column that is entirely
    empty in one day's snapshot infers as Null and as Int64 the next — the
    same bug the Pydantic model fixed for bootstrap-static, but across five
    tables and several hundred columns, where declaring models is not worth
    the effort.

    Landing as text and casting in dbt staging is the standard bronze
    pattern and puts the type decisions somewhere they can be reviewed.
    """
    return pl.read_csv(io.BytesIO(store.get(key)), infer_schema_length=0)


def _stamp(df: pl.DataFrame, *, season: str, source_key: str) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(season).alias("_season"),
        pl.lit(source_key).alias("_source_key"),
        pl.lit(datetime.now(UTC)).alias("_ingested_at"),
    )


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def build_daily(store: Store, table: str, day: date) -> dict[str, object]:
    """One parquet per table per day, covering every active season.

    Missing seasons are skipped rather than raising: a season's file may not
    exist yet, and a partial partition is more useful than none.
    """
    frames: list[pl.DataFrame] = []
    columns_by_season: dict[str, int] = {}

    for season in ACTIVE_SEASONS:
        key = daily_key(season, table, day)
        try:
            df = read_csv(store, key)
        except ObjectMissing:
            log.info("no %s for %s on %s", table, season, day)
            continue

        columns_by_season[season] = df.width
        frames.append(_stamp(df, season=season, source_key=key))

    if not frames:
        raise ValueError(f"no {table} mirrored for {day} in any active season")

    # diagonal: union on column names, null-filling absences. Active seasons
    # usually agree, but a new season can gain fields mid-life.
    out = pl.concat(frames, how="diagonal")

    buf = io.BytesIO()
    out.write_parquet(buf)
    store.put(bronze_key(table, f"{day:%Y-%m-%d}"), buf.getvalue(), overwrite=True, compress=False)

    return {
        "rows": out.height,
        "columns": out.width,
        "seasons": len(frames),
        "columns_by_season": columns_by_season,
    }


def build_archive(store: Store, table: str) -> dict[str, object]:
    """The finished 2024/25 season. Captured once, so unpartitioned."""
    key = archive_key(table)
    df = _stamp(read_csv(store, key), season=ARCHIVE_SEASON, source_key=key)

    buf = io.BytesIO()
    df.write_parquet(buf)
    store.put(
        bronze_key(table, f"archive-{ARCHIVE_SEASON}"),
        buf.getvalue(),
        overwrite=True,
        compress=False,
    )

    return {"rows": df.height, "columns": df.width, "season": ARCHIVE_SEASON}


# ---------------------------------------------------------------------------
# feature availability — first-class metadata, not a footnote
# ---------------------------------------------------------------------------


def column_availability(store: Store, table: str, day: date) -> dict[str, list[str]]:
    """Which columns exist in which season.

    A model must never train on a column that was null-filled because the
    source didn't have it that season. 2024/25 playerstats has no `news`
    field at all, so a model fed the union would learn that injuries didn't
    happen that year.

    Feeds a dbt seed so the coverage window is queryable rather than tribal.
    """
    availability: dict[str, list[str]] = {}

    with contextlib.suppress(ObjectMissing):
        availability[ARCHIVE_SEASON] = read_csv(store, archive_key(table)).columns

    for season in ACTIVE_SEASONS:
        with contextlib.suppress(ObjectMissing):
            availability[season] = read_csv(store, daily_key(season, table, day)).columns

    return availability


# ---------------------------------------------------------------------------
# NOT HANDLED HERE
# ---------------------------------------------------------------------------
#
# `matches` and `playermatchstats` for 2025/26 and 2026/27 live only inside
# the weekly tarball, under By Gameweek/GW{n}/. Those are the tables carrying
# xG, xA and the CBIT components — the most valuable data in the source.
#
# Extracting them is step 29c: open the tarball from R2, walk the By Gameweek
# tree, concatenate with a `gameweek` column. Deferred because it is a
# different shape of problem and the daily masters are enough to build the
# dbt project against.
