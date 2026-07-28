import io
import json
from datetime import UTC, date, datetime

import polars as pl
import pytest
from fpl_ingestion.bronze import build_bootstrap_bronze, parse_capture_time
from fpl_ingestion.storage import LocalStore, raw_key

BRONZE_KEY = "bronze/players/2026-07-27.parquet"


def seed(store, at: datetime, elements: list[dict]) -> None:
    payload = {"elements": elements, "teams": [], "events": []}
    store.put(raw_key("bootstrap-static", at), json.dumps(payload).encode())


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path)
    seed(s, datetime(2026, 7, 27, 2, 0, tzinfo=UTC), [{"id": 1, "web_name": "Raya"}])
    seed(s, datetime(2026, 7, 27, 23, 37, 20, tzinfo=UTC), [{"id": 1, "web_name": "Raya"}])
    return s


def read(store) -> pl.DataFrame:
    return pl.read_parquet(io.BytesIO(store.get(BRONZE_KEY, decompress=False)))


def test_idempotent(store):
    build_bootstrap_bronze(store, date(2026, 7, 27))
    first = store.get(BRONZE_KEY, decompress=False)
    build_bootstrap_bronze(store, date(2026, 7, 27))
    assert store.get(BRONZE_KEY, decompress=False) == first


def test_preserves_every_capture(store):
    build_bootstrap_bronze(store, date(2026, 7, 27))
    df = read(store)

    assert df.height == 2
    assert df["captured_at"].n_unique() == 2
    assert df["id"].to_list() == [1, 1]


def test_captures_are_ordered_oldest_first(store):
    build_bootstrap_bronze(store, date(2026, 7, 27))
    times = read(store)["captured_at"].to_list()

    assert times == sorted(times)


def test_returns_metadata(store):
    meta = build_bootstrap_bronze(store, date(2026, 7, 27))

    assert meta == {"rows": 2, "captures": 2}


def test_no_captures_raises(tmp_path):
    empty = LocalStore(tmp_path)

    with pytest.raises(ValueError, match="no captures"):
        build_bootstrap_bronze(empty, date(2026, 7, 27))


def test_ignores_other_endpoints(store):
    store.put(
        raw_key("fixtures", datetime(2026, 7, 27, 12, 0, tzinfo=UTC)),
        json.dumps([{"id": 99}]).encode(),
    )
    build_bootstrap_bronze(store, date(2026, 7, 27))

    assert read(store).height == 2


def test_ignores_other_days(store):
    seed(store, datetime(2026, 7, 28, 6, 0, tzinfo=UTC), [{"id": 2, "web_name": "Sels"}])
    build_bootstrap_bronze(store, date(2026, 7, 27))
    df = read(store)

    assert df.height == 2
    assert 2 not in df["id"].to_list()


def test_partitions_are_independent(store):
    seed(store, datetime(2026, 7, 28, 6, 0, tzinfo=UTC), [{"id": 2, "web_name": "Sels"}])
    build_bootstrap_bronze(store, date(2026, 7, 27))
    build_bootstrap_bronze(store, date(2026, 7, 28))

    assert read(store).height == 2  # 07-27 untouched by the 07-28 build
    later = pl.read_parquet(
        io.BytesIO(store.get("bronze/players/2026-07-28.parquet", decompress=False))
    )
    assert later.height == 1


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
def test_parse_capture_time(key, expected):
    assert parse_capture_time(key) == expected


def test_parse_capture_time_round_trips_raw_key():
    at = datetime(2026, 8, 21, 11, 30, 0, tzinfo=UTC)
    assert parse_capture_time(raw_key("bootstrap-static", at)) == at
