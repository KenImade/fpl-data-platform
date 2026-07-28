"""Extract gameweek-level tables from the Core Insights tarball.

Some tables exist only inside the weekly repository snapshot, never as flat
master files. Paths inside the archive:

    FPL-Core-Insights-main/data/{season}/By Gameweek/GW{n}/{table}.csv
    FPL-Core-Insights-main/data/{season}/By Tournament/{comp}/GW{n}/{table}.csv

By Tournament is a **strict superset** of By Gameweek, verified by row count:

                            By Gameweek   By Tournament
    2025/26 matches                 525             526
    2026/27 matches                 380             479
    2025/26 playermatchstats     15,026          15,042

By Gameweek is not Premier League only — an early GW11 sample suggested it
was, but that gameweek fell in an international break with no European
fixtures. It carries Champions League, Europa League, Conference League and
EFL Cup; By Tournament adds friendlies, the Community Shield and the Uefa
Super Cup. Extracting both would double-count every match, so only the
tournament scope is built for `matches` and `playermatchstats`.

`player_gameweek_stats` is By Gameweek only and has no equivalent elsewhere.
It is the most valuable table here: discrete per-gameweek values, verified
against the differenced cumulative totals (303/303 match on minutes at GW11).
Using it removes the differencing step and the phantom events that retroactive
restatements produce — though not the grain problem, since it aggregates
double gameweeks. Per-match resolution lives in `playermatchstats`.

2026/27 contains 97 friendlies filed under numbered gameweeks, not GW0.
Anything computing form, fitness or fixture congestion must filter on
competition. That lives on `matches.tournament`; `playermatchstats` has no
competition column and joins via `match_id`.

`fixtures` is byte-identical to `matches` under By Gameweek: same file, two
names. Ignored.
"""

from __future__ import annotations

import io
import logging
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import polars as pl

from fpl_ingestion.storage import Store

log = logging.getLogger(__name__)

TARBALL_PREFIX = "raw/core-insights/_full/"

BY_GAMEWEEK = "By Gameweek"
BY_TOURNAMENT = "By Tournament"

Scope = Literal["gameweek", "tournament"]

# (table, scope) pairs to build.
BUILDS: tuple[tuple[str, Scope], ...] = (
    ("player_gameweek_stats", "gameweek"),  # doesn't exist elsewhere
    ("matches", "tournament"),  # superset of By Gameweek
    ("playermatchstats", "tournament"),
)


@dataclass(frozen=True, slots=True)
class Entry:
    """A gameweek-scoped CSV inside the archive."""

    season: str
    competition: str | None  # None for By Gameweek
    gameweek: int
    table: str

    @property
    def scope(self) -> Scope:
        return "tournament" if self.competition is not None else "gameweek"


def parse_member(name: str) -> Entry | None:
    """Parse an archive path. Returns None for anything not gameweek-scoped.

    Most of the ~1,900 entries are flat master files, README, or licence.
    """
    parts = name.split("/")

    # [root, "data", season, scope, ...]
    if len(parts) < 6 or parts[1] != "data":
        return None

    season, scope = parts[2], parts[3]
    if not parts[-1].endswith(".csv"):
        return None

    table = parts[-1].removesuffix(".csv")

    if scope == BY_GAMEWEEK and len(parts) == 6:
        gw_part, competition = parts[4], None
    elif scope == BY_TOURNAMENT and len(parts) == 7:
        competition, gw_part = parts[4], parts[5]
    else:
        return None

    if not gw_part.startswith("GW"):
        return None
    try:
        gameweek = int(gw_part.removeprefix("GW"))
    except ValueError:
        return None

    return Entry(season=season, competition=competition, gameweek=gameweek, table=table)


def latest_tarball(store: Store) -> str:
    """Newest archived snapshot. Keys are date-suffixed, so lexicographic
    order is chronological."""
    keys = store.list(TARBALL_PREFIX)
    if not keys:
        raise ValueError(f"no tarball under {TARBALL_PREFIX}")
    return keys[-1]


def bronze_key(table: str, scope: Scope) -> str:
    return f"bronze/core-insights/{table}/{scope}s.parquet"


def _read(data: bytes) -> pl.DataFrame | None:
    """Guard against empty and unreadable files.

    No empty files have been observed in practice — every GW directory in the
    archive had content, including future gameweeks of the unstarted 2026/27
    season. The guard is defensive, not a description of normal behaviour.
    """
    if not data.strip():
        return None
    try:
        return pl.read_csv(io.BytesIO(data), infer_schema_length=0)
    except Exception:
        log.warning("unreadable CSV in tarball", exc_info=True)
        return None


def build_gameweek_table(
    store: Store,
    table: str,
    *,
    scope: Scope = "gameweek",
    tarball_key: str | None = None,
) -> dict[str, object]:
    """Concatenate every slice of one table at one scope into a single parquet.

    Everything lands as String, for the same reason as the daily masters:
    per-file CSV inference means an all-empty column types differently between
    gameweeks, and a diagonal concat of Null and String columns is a schema
    conflict. Casting happens in dbt staging.
    """
    key = tarball_key or latest_tarball(store)
    blob = store.get(key, decompress=False)

    frames: list[pl.DataFrame] = []
    seen: set[tuple[str, str | None, int]] = set()
    empty = 0

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue

            entry = parse_member(member.name)
            if entry is None or entry.table != table or entry.scope != scope:
                continue

            handle = tar.extractfile(member)
            if handle is None:
                continue

            df = _read(handle.read())
            if df is None or df.height == 0:
                empty += 1
                continue

            frames.append(
                df.with_columns(
                    pl.lit(entry.season).alias("_season"),
                    pl.lit(entry.competition, dtype=pl.String).alias("_competition"),
                    pl.lit(entry.gameweek).alias("_gameweek"),
                    pl.lit(key).alias("_source_key"),
                    pl.lit(datetime.now(UTC)).alias("_ingested_at"),
                )
            )
            seen.add((entry.season, entry.competition, entry.gameweek))

    if not frames:
        raise ValueError(f"no {table} slices at scope={scope!r} in {key}")

    out = pl.concat(frames, how="diagonal")

    # Upstream files a small number of fixtures under two gameweek directories
    # — a knockout placeholder populated with a real fixture's data. That
    # duplicates every player row for the match. Keep the lowest gameweek,
    # matching the dedupe in stg_matches.
    if "match_id" in out.columns:
        before = out.height
        out = out.sort("_gameweek").unique(
            subset=["match_id", "player_id"] if "player_id" in out.columns else ["match_id"],
            keep="first",
        )
        if out.height < before:
            log.warning("dropped %d duplicate rows in %s", before - out.height, table)

    buf = io.BytesIO()
    out.write_parquet(buf)
    store.put(bronze_key(table, scope), buf.getvalue(), overwrite=True, compress=False)

    seasons = sorted({s for s, _, _ in seen})
    return {
        "table": table,
        "scope": scope,
        "rows": out.height,
        "columns": out.width,
        "slices": len(frames),
        "empty_slices": empty,
        "seasons": seasons,
        "competitions": sorted({c for _, c, _ in seen if c}),
        "gameweeks_by_season": {s: len({gw for sn, _, gw in seen if sn == s}) for s in seasons},
        "tarball": key,
    }


def inspect_tarball(store: Store, tarball_key: str | None = None) -> pl.DataFrame:
    """What's actually in the archive, without extracting it."""
    key = tarball_key or latest_tarball(store)
    blob = store.get(key, decompress=False)

    rows = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            entry = parse_member(member.name)
            if entry is None:
                continue
            rows.append(
                {
                    "season": entry.season,
                    "competition": entry.competition,
                    "scope": entry.scope,
                    "gameweek": entry.gameweek,
                    "table": entry.table,
                    "bytes": member.size,
                }
            )

    return pl.DataFrame(
        rows,
        schema={
            "season": pl.String,
            "competition": pl.String,
            "scope": pl.String,
            "gameweek": pl.Int64,
            "table": pl.String,
            "bytes": pl.Int64,
        },
    )
