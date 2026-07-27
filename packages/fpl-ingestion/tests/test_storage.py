from __future__ import annotations

import gzip
from datetime import UTC, datetime
from io import BytesIO

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


def test_raw_key_returns_expected_path():
    ts = datetime(2025, 8, 17, 14, 5, 9, tzinfo=UTC)

    assert raw_key("bootstrap-static", ts) == (
        "raw/fpl/bootstrap-static/2025-08-17/14-05-09Z.json.gz"
    )


def test_raw_key_requires_timezone_aware_datetime():
    ts = datetime(2025, 8, 17, 14, 5, 9)

    with pytest.raises(ValueError, match="timezone-aware"):
        raw_key("fixtures", ts)


def test_latest_bootstrap_constant():
    assert LATEST_BOOTSTRAP == "state/latest-bootstrap.json.gz"


# ---------------------------------------------------------------------------
# LocalStore
# ---------------------------------------------------------------------------


def test_local_store_round_trip(tmp_path):
    store = LocalStore(tmp_path)

    key = "raw/test.json.gz"
    body = b'{"hello":"world"}'

    store.put(key, body)

    assert store.exists(key)
    assert store.get(key) == body


def test_local_store_refuses_overwrite(tmp_path):
    store = LocalStore(tmp_path)

    key = "raw/test.json.gz"

    store.put(key, b"first")

    with pytest.raises(ObjectExists):
        store.put(key, b"second")


def test_local_store_allows_explicit_overwrite(tmp_path):
    store = LocalStore(tmp_path)

    store.put(LATEST_BOOTSTRAP, b"old")
    store.put(LATEST_BOOTSTRAP, b"new", overwrite=True)

    assert store.get(LATEST_BOOTSTRAP) == b"new"


def test_local_store_missing_object(tmp_path):
    store = LocalStore(tmp_path)

    with pytest.raises(ObjectMissing):
        store.get("missing")


def test_local_store_writes_gzip(tmp_path):
    store = LocalStore(tmp_path)

    key = "raw/test.json.gz"
    body = bytes(range(256))

    store.put(key, body)

    raw = (tmp_path / key).read_bytes()

    assert gzip.decompress(raw) == body


# ---------------------------------------------------------------------------
# S3Store
# ---------------------------------------------------------------------------


def test_s3_store_round_trip():
    client = pytest.Mock() if hasattr(pytest, "Mock") else None
    from unittest.mock import Mock

    client = Mock()

    body = b"payload"

    client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

    client.get_object.return_value = {
        "Body": BytesIO(gzip.compress(body)),
    }

    store = S3Store(client, "bucket")

    store.put("key", body)

    client.put_object.assert_called_once()
    assert store.get("key") == body


def test_s3_store_refuses_overwrite():
    from unittest.mock import Mock

    client = Mock()
    client.head_object.return_value = {}

    store = S3Store(client, "bucket")

    with pytest.raises(ObjectExists):
        store.put("key", b"data")


def test_s3_store_allows_explicit_overwrite():
    from unittest.mock import Mock

    client = Mock()

    store = S3Store(client, "bucket")

    store.put("key", b"data", overwrite=True)

    client.put_object.assert_called_once()


def test_s3_store_missing_object():
    from unittest.mock import Mock

    client = Mock()

    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}},
        "GetObject",
    )

    store = S3Store(client, "bucket")

    with pytest.raises(ObjectMissing):
        store.get("missing")


def test_s3_store_exists():
    from unittest.mock import Mock

    client = Mock()
    client.head_object.return_value = {}

    store = S3Store(client, "bucket")

    assert store.exists("key")


def test_s3_store_not_exists():
    from unittest.mock import Mock

    client = Mock()

    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404"}},
        "HeadObject",
    )

    store = S3Store(client, "bucket")

    assert not store.exists("key")
