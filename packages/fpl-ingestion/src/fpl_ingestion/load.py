"""Load bronze parquet from object storage into Postgres.

The bridge between Dagster and dbt. Dagster owns extract and load; dbt owns
transform. Everything here lands in the `bronze` schema and is read-only to
dbt from that point on.

Truncate-and-replace rather than incremental merge. The whole warehouse is a
few hundred thousand rows, so merge logic buys no meaningful time and costs a
class of bug where a partial failure leaves the table in a state no one can
reason about. A replace either succeeds completely or leaves the previous
table untouched.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Literal

import polars as pl

from fpl_ingestion.storage import Store

log = logging.getLogger(__name__)

SCHEMA = "bronze"

Selection = Literal["all", "latest"]


@dataclass(frozen=True, slots=True)
class LoadSpec:
    """One Postgres table, and where its parquet comes from.

    `selection` matters more than it looks. Daily snapshots accumulate one
    parquet per day, and for most tables that history IS the point — price
    moves and injury-news changes are the reason for capturing eight times a
    day. But `players` and `teams` are near-static, and 365 daily copies of
    the same 800 rows is storage and query cost for nothing.
    """

    table: str
    prefix: str
    selection: Selection = "all"


SPECS: tuple[LoadSpec, ...] = (
    # FPL bootstrap elements. Every snapshot: this is the price and
    # injury-news history, and losing it defeats the capture cadence.
    LoadSpec("fpl_players", "bronze/players/", selection="all"),
    # Core Insights daily masters.
    LoadSpec("ci_playerstats", "bronze/core-insights/playerstats/", selection="latest"),
    LoadSpec(
        "ci_gameweek_summaries", "bronze/core-insights/gameweek_summaries/", selection="latest"
    ),
    LoadSpec("ci_players", "bronze/core-insights/players/", selection="latest"),
    LoadSpec("ci_teams", "bronze/core-insights/teams/", selection="latest"),
    # Extracted from the weekly tarball. Single files, rebuilt each time.
    LoadSpec(
        "ci_player_gameweek_stats", "bronze/core-insights/player_gameweek_stats/gameweeks.parquet"
    ),
    LoadSpec("ci_matches", "bronze/core-insights/matches/"),
    LoadSpec("ci_playermatchstats", "bronze/core-insights/playermatchstats/"),
)


def select_keys(store: Store, spec: LoadSpec) -> list[str]:
    """Resolve a spec to the parquet keys it should load.

    A prefix ending in `.parquet` is a single object rather than a prefix.
    """
    if spec.prefix.endswith(".parquet"):
        return [spec.prefix] if store.exists(spec.prefix) else []

    keys = [k for k in store.list(spec.prefix) if k.endswith(".parquet")]
    if not keys:
        return []

    if spec.selection == "latest":
        # The daily snapshot carries every ACTIVE season, so one is enough —
        # 365 copies of the same 800 rows is waste. But the archive is a
        # different season that appears nowhere else, so it always comes too.
        dated = [k for k in keys if "archive-" not in k]
        archives = [k for k in keys if "archive-" in k]
        return ([max(dated)] if dated else []) + archives

    return sorted(keys)


def read_frames(store: Store, keys: list[str]) -> pl.DataFrame:
    """Read and concatenate. Diagonal, so seasons with different column sets
    union with null-filling rather than raising.

    Everything in bronze is String (see core_insights.read_csv), so there are
    no type conflicts to resolve here — only presence and absence.
    """
    frames = [pl.read_parquet(io.BytesIO(store.get(k, decompress=False))) for k in keys]
    return pl.concat(frames, how="diagonal")


def load_table(store: Store, conn_str: str, spec: LoadSpec) -> dict[str, object]:
    """Load one spec into `bronze.{table}`.

    Truncate-and-append rather than replace. `replace` issues DROP TABLE,
    which Postgres refuses once a dbt view depends on the table — and
    dropping with CASCADE would silently delete the dbt models.

    Truncating keeps the table object (and the views pointing at it) intact
    while still giving replace semantics for the data.
    """
    keys = select_keys(store, spec)
    if not keys:
        raise ValueError(f"no parquet found for {spec.table} under {spec.prefix}")

    df = read_frames(store, keys)
    table = f"{SCHEMA}.{spec.table}"

    import adbc_driver_postgresql.dbapi as pg

    with pg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT to_regclass('{table}')")
        exists = cur.fetchone()[0] is not None

        if exists:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = $2",
                (SCHEMA, spec.table),
            )
            existing = {r[0] for r in cur.fetchall()}

            if existing != set(df.columns):
                # Bronze gains columns as sources evolve — a season's daily
                # masters carry fields its archive never had. append cannot
                # widen a table, so recreate.
                #
                # CASCADE drops the dbt views reading this table. That is
                # safe: they are declarative and rebuilt by the dbt models
                # downstream in this same job.
                log.info(
                    "schema change on %s: %d -> %d columns, recreating",
                    table,
                    len(existing),
                    len(df.columns),
                )
                cur.execute(f"DROP TABLE {table} CASCADE")
                exists = False
            else:
                cur.execute(f"TRUNCATE TABLE {table}")

        conn.commit()

    df.write_database(
        table,
        conn_str,
        if_table_exists="append" if exists else "replace",
        engine="adbc",
    )

    log.info("loaded %s rows=%d from %d file(s)", table, df.height, len(keys))

    return {
        "table": spec.table,
        "rows": df.height,
        "columns": df.width,
        "files": len(keys),
        "selection": spec.selection,
        "first_key": keys[0],
        "last_key": keys[-1],
    }


def load_all(store: Store, conn_str: str) -> list[dict[str, object]]:
    """Every spec. Failures are collected rather than aborting, so one
    missing table doesn't block the rest of the warehouse."""
    results: list[dict[str, object]] = []
    for spec in SPECS:
        try:
            results.append(load_table(store, conn_str, spec))
        except Exception as exc:
            log.exception("load failed for %s", spec.table)
            results.append({"table": spec.table, "error": str(exc)})
    return results


def ensure_schema(conn_str: str) -> None:
    """Create the bronze schema if absent.

    Kept here rather than in a migration because bronze is Dagster-owned and
    entirely rebuildable — it has no migration history worth tracking. The
    dbt-owned schemas are a different matter.
    """
    import adbc_driver_postgresql.dbapi as pg

    with pg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.commit()
