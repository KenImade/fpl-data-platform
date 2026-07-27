# tests/test_capture.py

from datetime import UTC, datetime
from unittest.mock import Mock, call, patch

from fpl_ingestion.capture import CaptureResult, capture
from fpl_ingestion.client import ENDPOINTS, RateLimited

NOW = datetime(2025, 8, 17, 14, 5, 9, tzinfo=UTC)


def response(body: bytes):
    r = Mock()
    r.body = body
    return r


def test_capture_all_endpoints():
    client = Mock()
    store = Mock()

    with (
        patch("fpl_ingestion.capture.fetch") as fetch,
        patch("fpl_ingestion.capture.raw_key") as raw_key,
    ):
        fetch.side_effect = [
            response(b"bootstrap"),
            response(b"fixtures"),
            response(b"events"),
        ]

        raw_key.side_effect = [
            "k1",
            "k2",
            "k3",
        ]

        result = capture(client, store, now=NOW)

    assert result.ok
    assert result.failed == {}
    assert result.stored == {
        "bootstrap-static": "k1",
        "fixtures": "k2",
        "event-status": "k3",
    }

    assert fetch.call_args_list == [
        call(client, ENDPOINTS["bootstrap-static"]),
        call(client, ENDPOINTS["fixtures"]),
        call(client, ENDPOINTS["event-status"]),
    ]

    assert store.put.call_args_list == [
        call("k1", b"bootstrap"),
        call("k2", b"fixtures"),
        call("k3", b"events"),
    ]


def test_capture_continues_after_non_rate_limit_error():
    client = Mock()
    store = Mock()

    with (
        patch("fpl_ingestion.capture.fetch") as fetch,
        patch("fpl_ingestion.capture.raw_key") as raw_key,
    ):
        fetch.side_effect = [
            RuntimeError("boom"),
            response(b"fixtures"),
            response(b"events"),
        ]

        raw_key.side_effect = ["k2", "k3"]

        result = capture(client, store, now=NOW)

    assert not result.ok
    assert result.failed == {
        "bootstrap-static": "boom",
    }

    assert result.stored == {
        "fixtures": "k2",
        "event-status": "k3",
    }


def test_capture_stops_after_rate_limit():
    client = Mock()
    store = Mock()

    with patch("fpl_ingestion.capture.fetch") as fetch:
        fetch.side_effect = RateLimited("429")

        result = capture(client, store, now=NOW)

    assert not result.ok
    assert result.failed == {
        "bootstrap-static": "rate limited",
    }

    fetch.assert_called_once()
    store.put.assert_not_called()


def test_capture_result_ok_property():
    result = CaptureResult(
        at=NOW,
        stored={"a": "b"},
        failed={},
    )

    assert result.ok is True

    result = CaptureResult(
        at=NOW,
        stored={},
        failed={"bootstrap-static": "boom"},
    )

    assert result.ok is False


def test_capture_uses_supplied_timestamp():
    client = Mock()
    store = Mock()

    with (
        patch("fpl_ingestion.capture.fetch") as fetch,
        patch("fpl_ingestion.capture.raw_key") as raw_key,
    ):
        fetch.side_effect = [
            response(b"a"),
            response(b"b"),
            response(b"c"),
        ]

        raw_key.side_effect = ["k1", "k2", "k3"]

        result = capture(client, store, now=NOW)

    assert result.at is NOW

    raw_key.assert_has_calls(
        [
            call("bootstrap-static", NOW),
            call("fixtures", NOW),
            call("event-status", NOW),
        ]
    )
