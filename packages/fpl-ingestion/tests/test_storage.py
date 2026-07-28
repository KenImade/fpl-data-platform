"""Tests for fpl_ingestion.storage.

Structure:
  - Pure functions (raw_key) tested directly.
  - Behaviour common to both Store implementations tested once, parametrised
    over both. The Store protocol only means something if the implementations
    are interchangeable, so the contract is asserted rather than assumed.
  - S3-specific wiring (pagination, error-code mapping) tested against a mock.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from fpl_ingestion.storage import (
    LATEST_BOOTSTRAP,
    LocalStore,
    ObjectExists,
    ObjectMissing,
    S3Store,
    raw_key,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def client_error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, op)


class FakeS3:
    """In-memory stand-in for the boto3 S3 client.

    Behaves like S3 rather than like our code: prefix matching over whole
    keys, paginated listing, ClientError on missing keys. Lets the contract
    tests run against S3Store without a network or a container.
    """

    PAGE_SIZE = 1000

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise client_error("NoSuchKey", "GetObject")
        return {"Body": BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise client_error("404", "HeadObject")
        return {}

    def get_paginator(self, name: str) -> FakeS3:
        assert name == "list_objects_v2"
        return self

    def paginate(self, *, Bucket: str, Prefix: str):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if not keys:
            yield {}
            return
        for i in range(0, len(keys), self.PAGE_SIZE):
            yield {"Contents": [{"Key": k} for k in keys[i : i + self.PAGE_SIZE]]}


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    """Both Store implementations, for contract tests."""
    if request.param == "local":
        return LocalStore(tmp_path)
    return S3Store(FakeS3(), "bucket")


# ---------------------------------------------------------------------------
# raw_key
# ---------------------------------------------------------------------------


def test_raw_key_returns_expected_path() -> None:
    ts = datetime(2025, 8, 17, 14, 5, 9, tzinfo=UTC)
    assert raw_key("bootstrap-static", ts) == (
        "raw/fpl/bootstrap-static/2025-08-17/14-05-09Z.json.gz"
    )


def test_raw_key_zero_pads() -> None:
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert raw_key("fixtures", ts) == "raw/fpl/fixtures/2026-01-02/03-04-05Z.json.gz"


def test_raw_key_requires_timezone_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        raw_key("fixtures", datetime(2025, 8, 17, 14, 5, 9))


def test_raw_key_uses_utc_not_local_offset() -> None:
    """A key built from a non-UTC offset would misdate the partition."""
    from datetime import timedelta, timezone

    wat = timezone(timedelta(hours=1))
    ts = datetime(2026, 7, 28, 0, 22, 47, tzinfo=wat)  # 23:22:47Z the day before
    key = raw_key("bootstrap-static", ts.astimezone(UTC))
    assert "2026-07-27/23-22-47Z" in key


def test_latest_bootstrap_constant() -> None:
    assert LATEST_BOOTSTRAP == "state/latest-bootstrap.json.gz"


# ---------------------------------------------------------------------------
# Store contract — must hold for every implementation
# ---------------------------------------------------------------------------


class TestStoreContract:
    def test_round_trip(self, store) -> None:
        body = b'{"hello":"world"}'
        store.put("raw/test.json.gz", body)
        assert store.exists("raw/test.json.gz")
        assert store.get("raw/test.json.gz") == body

    def test_round_trip_binary(self, store) -> None:
        """Full byte range, not just ASCII."""
        body = bytes(range(256))
        store.put("raw/bin", body)
        assert store.get("raw/bin") == body

    def test_round_trip_empty_body(self, store) -> None:
        store.put("raw/empty", b"")
        assert store.get("raw/empty") == b""

    def test_refuses_overwrite_by_default(self, store) -> None:
        store.put("raw/test", b"first")
        with pytest.raises(ObjectExists):
            store.put("raw/test", b"second")

    def test_original_survives_refused_overwrite(self, store) -> None:
        store.put("raw/test", b"first")
        with pytest.raises(ObjectExists):
            store.put("raw/test", b"second")
        assert store.get("raw/test") == b"first"

    def test_allows_explicit_overwrite(self, store) -> None:
        store.put(LATEST_BOOTSTRAP, b"old")
        store.put(LATEST_BOOTSTRAP, b"new", overwrite=True)
        assert store.get(LATEST_BOOTSTRAP) == b"new"

    def test_missing_object_raises(self, store) -> None:
        with pytest.raises(ObjectMissing):
            store.get("missing")

    def test_exists_false_for_missing(self, store) -> None:
        assert not store.exists("missing")

    def test_uncompressed_round_trip(self, store) -> None:
        """Bronze parquet is already compressed; gzipping it again wastes CPU
        and makes the object unreadable by anything that doesn't know to
        unwrap it."""
        body = b"PAR1fake-parquet-bytes"
        store.put("bronze/x.parquet", body, compress=False)
        assert store.get("bronze/x.parquet", decompress=False) == body

    def test_list_returns_sorted_keys(self, store) -> None:
        for key in ("raw/c", "raw/a", "raw/b"):
            store.put(key, b"x")
        assert store.list("raw/") == ["raw/a", "raw/b", "raw/c"]

    def test_list_empty_prefix_returns_empty(self, store) -> None:
        store.put("raw/a", b"x")
        assert store.list("nothing/") == []

    def test_list_matches_partial_path_component(self, store) -> None:
        """S3 has no directories. 'raw/fpl/boot' is a valid prefix that
        matches 'raw/fpl/bootstrap-static/...'. A directory-traversal
        implementation fails this and diverges from production."""
        store.put("raw/fpl/bootstrap-static/2026-07-27/01.json.gz", b"a")
        store.put("raw/fpl/bootstrap-static/2026-07-27/02.json.gz", b"b")
        store.put("raw/fpl/fixtures/2026-07-27/01.json.gz", b"c")

        assert len(store.list("raw/fpl/boot")) == 2
        assert len(store.list("raw/fpl/")) == 3

    def test_list_ordering_is_chronological_for_raw_keys(self, store) -> None:
        """Bronze relies on lexicographic order equalling capture order."""
        ts = [
            datetime(2026, 7, 27, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 27, 11, 30, 0, tzinfo=UTC),
            datetime(2026, 7, 27, 23, 22, 47, tzinfo=UTC),
        ]
        for t in reversed(ts):
            store.put(raw_key("bootstrap-static", t), b"x")

        listed = store.list("raw/fpl/bootstrap-static/2026-07-27/")
        assert listed == [raw_key("bootstrap-static", t) for t in ts]


# ---------------------------------------------------------------------------
# LocalStore specifics
# ---------------------------------------------------------------------------


def test_local_store_writes_gzip_to_disk(tmp_path) -> None:
    store = LocalStore(tmp_path)
    body = bytes(range(256))
    store.put("raw/test.json.gz", body)
    assert gzip.decompress((tmp_path / "raw/test.json.gz").read_bytes()) == body


def test_local_store_writes_raw_bytes_when_uncompressed(tmp_path) -> None:
    store = LocalStore(tmp_path)
    store.put("bronze/x.parquet", b"PAR1", compress=False)
    assert (tmp_path / "bronze/x.parquet").read_bytes() == b"PAR1"


def test_local_store_list_on_missing_root(tmp_path) -> None:
    assert LocalStore(tmp_path / "does-not-exist").list("raw/") == []


# ---------------------------------------------------------------------------
# S3Store specifics — boto3 wiring
# ---------------------------------------------------------------------------


def test_s3_list_paginates_beyond_one_page() -> None:
    """list_objects_v2 truncates at 1000 keys and returns them
    lexicographically, so an unpaginated call silently loses everything after
    the first thousand — and loses the NEWEST keys, since ours sort by time.
    """
    client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": f"raw/a/{i:05d}"} for i in range(1000)]},
        {"Contents": [{"Key": f"raw/a/{i:05d}"} for i in range(1000, 1500)]},
    ]
    client.get_paginator.return_value = paginator

    keys = S3Store(client, "bucket").list("raw/a/")

    assert len(keys) == 1500
    assert keys == sorted(keys)


def test_s3_list_handles_page_without_contents() -> None:
    client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [{}]
    client.get_paginator.return_value = paginator

    assert S3Store(client, "bucket").list("nothing/") == []


def test_s3_put_skips_head_when_overwriting() -> None:
    """Overwrite shouldn't pay for an existence check."""
    client = Mock()
    S3Store(client, "bucket").put("key", b"data", overwrite=True)
    client.head_object.assert_not_called()
    client.put_object.assert_called_once()


@pytest.mark.parametrize("code", ["NoSuchKey", "404"])
def test_s3_get_maps_missing_codes(code: str) -> None:
    client = Mock()
    client.get_object.side_effect = client_error(code, "GetObject")
    with pytest.raises(ObjectMissing):
        S3Store(client, "bucket").get("missing")


@pytest.mark.parametrize("code", ["NoSuchKey", "NotFound", "404"])
def test_s3_exists_maps_missing_codes(code: str) -> None:
    client = Mock()
    client.head_object.side_effect = client_error(code, "HeadObject")
    assert not S3Store(client, "bucket").exists("key")


def test_s3_get_propagates_unexpected_errors() -> None:
    """AccessDenied must not be silently reported as a missing object."""
    client = Mock()
    client.get_object.side_effect = client_error("AccessDenied", "GetObject")
    with pytest.raises(ClientError):
        S3Store(client, "bucket").get("key")


def test_s3_exists_propagates_unexpected_errors() -> None:
    client = Mock()
    client.head_object.side_effect = client_error("AccessDenied", "HeadObject")
    with pytest.raises(ClientError):
        S3Store(client, "bucket").exists("key")
