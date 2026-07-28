"""Tests for fpl_ingestion.bronze.

The `element()` helper produces a payload that passes validation. Before the
Pydantic model existed these tests seeded two-field stubs, which validated
fine against nothing and would never have come from FPL — so keeping the
fixture realistic is part of what the model is for.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime

import polars as pl
import pytest
from fpl_ingestion.bronze import build_bootstrap_bronze, parse_capture_time
from fpl_ingestion.storage import LocalStore, raw_key
from pydantic import ValidationError

BRONZE_KEY = "bronze/players/2026-07-27.parquet"

GW1 = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
GW2 = datetime(2026, 7, 27, 23, 37, 20, tzinfo=UTC)


def element(**overrides) -> dict:
    """A valid bootstrap-static element. Override any field per test."""
    base = {
        "id": 1,
        "code": 223094,
        "web_name": "Raya",
        "first_name": "David",
        "second_name": "Raya Martin",
        "element_type": 1,
        "team": 1,
        "team_code": 3,
        "now_cost": 60,
        "status": "a",
        "news": "",
        "chance_of_playing_next_round": None,
        "chance_of_playing_this_round": None,
        "total_points": 0,
        "event_points": 0,
        "minutes": 0,
        "selected_by_percent": "12.3",
        "ep_next": "3.5",
        "ep_this": "3.5",
    }
    return base | overrides


def seed(store, at: datetime, elements: list[dict]) -> None:
    payload = {"elements": elements, "teams": [], "events": []}
    store.put(raw_key("bootstrap-static", at), json.dumps(payload).encode())


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path)
    seed(s, GW1, [element()])
    seed(s, GW2, [element()])
    return s


def read(store) -> pl.DataFrame:
    return pl.read_parquet(io.BytesIO(store.get(BRONZE_KEY, decompress=False)))


# ---------------------------------------------------------------------------
# core behaviour
# ---------------------------------------------------------------------------


def test_idempotent(store) -> None:
    """What makes rebuild-from-bronze verifiable by checksum."""
    build_bootstrap_bronze(store, date(2026, 7, 27))
    first = store.get(BRONZE_KEY, decompress=False)
    build_bootstrap_bronze(store, date(2026, 7, 27))
    assert store.get(BRONZE_KEY, decompress=False) == first


def test_preserves_every_capture(store) -> None:
    """Capturing eight times a day exists to record price and injury-news
    movement. Collapsing to one row per player would discard the point."""
    build_bootstrap_bronze(store, date(2026, 7, 27))
    df = read(store)

    assert df.height == 2
    assert df["captured_at"].n_unique() == 2
    assert df["id"].to_list() == [1, 1]


def test_captures_are_ordered_oldest_first(store) -> None:
    build_bootstrap_bronze(store, date(2026, 7, 27))
    times = read(store)["captured_at"].to_list()
    assert times == sorted(times)


def test_returns_metadata(store) -> None:
    meta = build_bootstrap_bronze(store, date(2026, 7, 27))
    assert meta == {"rows": 2, "captures": 2, "novel_fields": 0}


def test_no_captures_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="no captures"):
        build_bootstrap_bronze(LocalStore(tmp_path), date(2026, 7, 27))


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------


def test_ignores_other_endpoints(store) -> None:
    store.put(
        raw_key("fixtures", datetime(2026, 7, 27, 12, 0, tzinfo=UTC)),
        json.dumps([{"id": 99}]).encode(),
    )
    build_bootstrap_bronze(store, date(2026, 7, 27))
    assert read(store).height == 2


def test_ignores_other_days(store) -> None:
    seed(store, datetime(2026, 7, 28, 6, 0, tzinfo=UTC), [element(id=2, web_name="Sels")])
    build_bootstrap_bronze(store, date(2026, 7, 27))
    df = read(store)

    assert df.height == 2
    assert 2 not in df["id"].to_list()


def test_partitions_are_independent(store) -> None:
    """A prefix bug here would duplicate rows across every partition and only
    surface much later, when counts came out wrong."""
    seed(store, datetime(2026, 7, 28, 6, 0, tzinfo=UTC), [element(id=2, web_name="Sels")])
    build_bootstrap_bronze(store, date(2026, 7, 27))
    build_bootstrap_bronze(store, date(2026, 7, 28))

    assert read(store).height == 2
    later = pl.read_parquet(
        io.BytesIO(store.get("bronze/players/2026-07-28.parquet", decompress=False))
    )
    assert later.height == 1


# ---------------------------------------------------------------------------
# validation and schema
# ---------------------------------------------------------------------------


def test_missing_required_field_raises(tmp_path) -> None:
    """A field disappearing upstream must fail the partition loudly rather
    than propagating a null."""
    s = LocalStore(tmp_path)
    bad = element()
    del bad["now_cost"]
    seed(s, GW1, [bad])

    with pytest.raises(ValidationError):
        build_bootstrap_bronze(s, date(2026, 7, 27))


def test_novel_field_is_counted_not_fatal(tmp_path) -> None:
    """FPL adds fields mid-season. Losing the partition would be worse than
    not modelling the field — but it must be reported so it gets added
    deliberately rather than discovered months later."""
    s = LocalStore(tmp_path)
    seed(s, GW1, [element(brand_new_stat=42)])

    meta = build_bootstrap_bronze(s, date(2026, 7, 27))

    assert meta["rows"] == 1
    assert meta["novel_fields"] == 1
    assert "brand_new_stat" not in read(s).columns  # dropped by explicit schema


def test_null_and_populated_optional_share_a_schema(tmp_path) -> None:
    """chance_of_playing_next_round is null all pre-season and an integer once
    the season starts. Without a declared schema those partitions type
    differently and any union across them fails."""
    s = LocalStore(tmp_path)
    seed(s, GW1, [element(chance_of_playing_next_round=None)])
    seed(s, GW2, [element(chance_of_playing_next_round=75)])

    build_bootstrap_bronze(s, date(2026, 7, 27))

    assert read(s).schema["chance_of_playing_next_round"] == pl.Int64


# ---------------------------------------------------------------------------
# key parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "raw/fpl/bootstrap-static/2026-07-27/23-37-20Z.json.gz",
            datetime(2026, 7, 27, 23, 37, 20, tzinfo=UTC),
        ),
        (
            "raw/fpl/fixtures/2026-01-02/03-04-05Z.json.gz",
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        ),
    ],
)
def test_parse_capture_time(key: str, expected: datetime) -> None:
    assert parse_capture_time(key) == expected


def test_parse_capture_time_round_trips_raw_key() -> None:
    """raw_key and parse_capture_time live in different modules and will
    drift if either format changes."""
    at = datetime(2026, 8, 21, 11, 30, 0, tzinfo=UTC)
    assert parse_capture_time(raw_key("bootstrap-static", at)) == at
