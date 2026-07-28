"""Tests for fpl_ingestion.load.

Key selection is where the bugs live, so it gets most of the coverage. It is
pure logic over a Store and testable without a database.

The Postgres write itself is exercised in one integration test against the
local Compose instance, marked so it can be skipped. Mocking `write_database`
would only assert that we called polars the way we think we did, which is the
least interesting thing that could go wrong.
"""

from __future__ import annotations

import io
import os

import polars as pl
import pytest
from fpl_ingestion.load import (
    SCHEMA,
    LoadSpec,
    load_table,
    read_frames,
    select_keys,
)
from fpl_ingestion.storage import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


def put_parquet(store, key: str, df: pl.DataFrame) -> None:
    buf = io.BytesIO()
    df.write_parquet(buf)
    store.put(key, buf.getvalue(), overwrite=True, compress=False)


def frame(**cols) -> pl.DataFrame:
    return pl.DataFrame(cols)


# ---------------------------------------------------------------------------
# select_keys — selection="all"
# ---------------------------------------------------------------------------


def test_all_returns_every_parquet_sorted(store) -> None:
    for day in ("2026-07-29", "2026-07-27", "2026-07-28"):
        put_parquet(store, f"bronze/players/{day}.parquet", frame(id=[1]))

    spec = LoadSpec("fpl_players", "bronze/players/", selection="all")

    assert select_keys(store, spec) == [
        "bronze/players/2026-07-27.parquet",
        "bronze/players/2026-07-28.parquet",
        "bronze/players/2026-07-29.parquet",
    ]


def test_all_includes_the_archive(store) -> None:
    """2024/25 is half the rich training data. It must not be skipped."""
    put_parquet(store, "bronze/core-insights/playerstats/2026-07-28.parquet", frame(id=[1]))
    put_parquet(store, "bronze/core-insights/playerstats/archive-2024-2025.parquet", frame(id=[2]))

    keys = select_keys(store, LoadSpec("ci_playerstats", "bronze/core-insights/playerstats/"))

    assert len(keys) == 2
    assert any("archive-" in k for k in keys)


def test_all_ignores_non_parquet(store) -> None:
    put_parquet(store, "bronze/players/2026-07-28.parquet", frame(id=[1]))
    store.put("bronze/players/notes.txt", b"ignore me")

    keys = select_keys(store, LoadSpec("fpl_players", "bronze/players/"))

    assert keys == ["bronze/players/2026-07-28.parquet"]


def test_empty_prefix_returns_empty(store) -> None:
    assert select_keys(store, LoadSpec("fpl_players", "bronze/players/")) == []


# ---------------------------------------------------------------------------
# select_keys — selection="latest"
# ---------------------------------------------------------------------------


def test_latest_returns_newest_dated_key(store) -> None:
    for day in ("2026-07-27", "2026-07-29", "2026-07-28"):
        put_parquet(store, f"bronze/core-insights/teams/{day}.parquet", frame(id=[1]))

    spec = LoadSpec("ci_teams", "bronze/core-insights/teams/", selection="latest")

    assert select_keys(store, spec) == ["bronze/core-insights/teams/2026-07-29.parquet"]


def test_latest_prefers_dated_over_archive(store) -> None:
    """`archive-2024-2025` sorts before any `2026-...` key, so a naive max()
    would pin this table to a finished season forever."""
    put_parquet(store, "bronze/core-insights/teams/archive-2024-2025.parquet", frame(id=[1]))
    put_parquet(store, "bronze/core-insights/teams/2026-07-28.parquet", frame(id=[2]))

    spec = LoadSpec("ci_teams", "bronze/core-insights/teams/", selection="latest")

    assert select_keys(store, spec) == ["bronze/core-insights/teams/2026-07-28.parquet"]


def test_latest_falls_back_to_archive_when_only_option(store) -> None:
    put_parquet(store, "bronze/core-insights/teams/archive-2024-2025.parquet", frame(id=[1]))

    spec = LoadSpec("ci_teams", "bronze/core-insights/teams/", selection="latest")

    assert select_keys(store, spec) == ["bronze/core-insights/teams/archive-2024-2025.parquet"]


# ---------------------------------------------------------------------------
# select_keys — single-object specs
# ---------------------------------------------------------------------------


def test_single_object_spec_returns_it(store) -> None:
    key = "bronze/core-insights/matches/tournaments.parquet"
    put_parquet(store, key, frame(id=[1]))

    assert select_keys(store, LoadSpec("ci_matches", key)) == [key]


def test_single_object_spec_missing_returns_empty(store) -> None:
    key = "bronze/core-insights/matches/tournaments.parquet"

    assert select_keys(store, LoadSpec("ci_matches", key)) == []


def test_single_object_spec_does_not_prefix_match(store) -> None:
    """A .parquet suffix means an exact object, not a prefix — otherwise
    `matches/tournaments.parquet` would also match a longer key."""
    put_parquet(store, "bronze/core-insights/matches/tournaments.parquet.bak", frame(id=[1]))

    spec = LoadSpec("ci_matches", "bronze/core-insights/matches/tournaments.parquet")

    assert select_keys(store, spec) == []


# ---------------------------------------------------------------------------
# read_frames
# ---------------------------------------------------------------------------


def test_concatenates_in_key_order(store) -> None:
    put_parquet(store, "a.parquet", frame(id=[1, 2]))
    put_parquet(store, "b.parquet", frame(id=[3]))

    df = read_frames(store, ["a.parquet", "b.parquet"])

    assert df["id"].to_list() == [1, 2, 3]


def test_diagonal_concat_unions_differing_columns(store) -> None:
    """2024/25 playerstats has 58 columns, 2025/26 has 87. The union must
    null-fill rather than raise — that difference is real data about which
    season had which fields."""
    put_parquet(store, "old.parquet", frame(id=[1], minutes=["90"]))
    put_parquet(store, "new.parquet", frame(id=[2], minutes=["45"], news=["knock"]))

    df = read_frames(store, ["old.parquet", "new.parquet"])

    assert set(df.columns) == {"id", "minutes", "news"}
    assert df.height == 2
    assert df["news"].to_list() == [None, "knock"]


def test_column_order_does_not_matter(store) -> None:
    put_parquet(store, "a.parquet", frame(id=[1], minutes=["90"]))
    put_parquet(store, "b.parquet", frame(minutes=["45"], id=[2]))

    df = read_frames(store, ["a.parquet", "b.parquet"])

    assert df.height == 2
    assert df["id"].to_list() == [1, 2]


# ---------------------------------------------------------------------------
# load_table
# ---------------------------------------------------------------------------


def test_load_table_raises_when_nothing_to_load(store) -> None:
    with pytest.raises(ValueError, match="no parquet found"):
        load_table(store, "postgres://unused", LoadSpec("ci_teams", "bronze/missing/"))


@pytest.mark.integration
def test_load_table_writes_to_postgres(store) -> None:
    """Against the local Compose Postgres. Skipped without DATABASE_URL.

    Mocking write_database would only assert we called polars the way we
    think we did — which is not where this goes wrong.
    """
    conn_str = os.environ.get("DATABASE_URL")
    if not conn_str:
        pytest.skip("DATABASE_URL unset")

    import adbc_driver_postgresql.dbapi as pg
    from fpl_ingestion.load import ensure_schema

    ensure_schema(conn_str)
    put_parquet(store, "bronze/t/2026-07-28.parquet", frame(id=["1", "2"], name=["a", "b"]))

    spec = LoadSpec("test_load_target", "bronze/t/")
    result = load_table(store, conn_str, spec)

    assert result["rows"] == 2
    assert result["files"] == 1

    with pg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.test_load_target")
        assert cur.fetchone()[0] == 2
        cur.execute(f"DROP TABLE {SCHEMA}.test_load_target")
        conn.commit()


@pytest.mark.integration
def test_load_table_replaces_rather_than_appends(store) -> None:
    """A reload must not double the table. This is the behaviour that makes
    `just rebuild-from-bronze` safe to run twice."""
    conn_str = os.environ.get("DATABASE_URL")
    if not conn_str:
        pytest.skip("DATABASE_URL unset")

    import adbc_driver_postgresql.dbapi as pg
    from fpl_ingestion.load import ensure_schema

    ensure_schema(conn_str)
    put_parquet(store, "bronze/t2/2026-07-28.parquet", frame(id=["1", "2"]))

    spec = LoadSpec("test_replace_target", "bronze/t2/")
    load_table(store, conn_str, spec)
    load_table(store, conn_str, spec)

    with pg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.test_replace_target")
        assert cur.fetchone()[0] == 2
        cur.execute(f"DROP TABLE {SCHEMA}.test_replace_target")
        conn.commit()
