from unittest.mock import Mock

import httpx
import pytest
from fpl_ingestion.client import (
    FetchError,
    RateLimited,
    Response,
    fetch,
    make_client,
)


def make_response(status=200, body=b"data"):
    return httpx.Response(status_code=status, content=body)


def test_fetch_success():
    client = Mock()
    client.get.return_value = make_response(200, b"hello")

    result = fetch(client, "https://example.com")

    assert result == Response(
        url="https://example.com",
        body=b"hello",
        status=200,
        duration_s=result.duration_s,
    )
    assert result.duration_s >= 0
    client.get.assert_called_once_with("https://example.com")


def test_fetch_retries_transport_error_then_succeeds():
    client = Mock()
    client.get.side_effect = [
        httpx.ConnectError("boom"),
        make_response(200, b"ok"),
    ]

    sleep = Mock()

    result = fetch(
        client,
        "https://example.com",
        backoffs=(1.0,),
        sleep=sleep,
    )

    assert result.status == 200
    assert result.body == b"ok"

    sleep.assert_called_once_with(1.0)
    assert client.get.call_count == 2


def test_fetch_retries_timeout_then_succeeds():
    client = Mock()
    client.get.side_effect = [
        httpx.ReadTimeout("timeout"),
        make_response(200),
    ]

    sleep = Mock()

    result = fetch(
        client,
        "https://example.com",
        backoffs=(2.0,),
        sleep=sleep,
    )

    assert result.status == 200
    sleep.assert_called_once_with(2.0)


def test_fetch_retries_on_server_errors():
    client = Mock()
    client.get.side_effect = [
        make_response(500),
        make_response(502),
        make_response(200, b"done"),
    ]

    sleep = Mock()

    result = fetch(
        client,
        "https://example.com",
        backoffs=(1.0, 2.0),
        sleep=sleep,
    )

    assert result.body == b"done"
    assert sleep.call_args_list == [((1.0,),), ((2.0,),)]


def test_fetch_raises_after_all_server_errors():
    client = Mock()
    client.get.return_value = make_response(503)

    sleep = Mock()

    with pytest.raises(FetchError, match="all attempts failed"):
        fetch(
            client,
            "https://example.com",
            backoffs=(1.0, 2.0),
            sleep=sleep,
        )

    assert client.get.call_count == 3
    assert sleep.call_args_list == [((1.0,),), ((2.0,),)]


def test_fetch_raises_after_all_transport_errors():
    client = Mock()
    client.get.side_effect = httpx.ConnectError("offline")

    sleep = Mock()

    with pytest.raises(FetchError, match="all attempts failed"):
        fetch(
            client,
            "https://example.com",
            backoffs=(1.0,),
            sleep=sleep,
        )

    assert client.get.call_count == 2
    sleep.assert_called_once_with(1.0)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_fetch_does_not_retry_client_errors(status):
    client = Mock()
    client.get.return_value = make_response(status)

    sleep = Mock()

    with pytest.raises(FetchError, match=str(status)):
        fetch(
            client,
            "https://example.com",
            backoffs=(1.0, 2.0),
            sleep=sleep,
        )

    client.get.assert_called_once()
    sleep.assert_not_called()


def test_fetch_raises_rate_limited():
    client = Mock()
    client.get.return_value = make_response(429)

    with pytest.raises(RateLimited):
        fetch(client, "https://example.com")


def test_make_client():
    client = make_client("my-agent", timeout=12.5)

    assert isinstance(client, httpx.Client)
    assert client.headers["User-Agent"] == "my-agent"
    assert client.follow_redirects is True

    # httpx stores timeout as a Timeout object
    assert client.timeout.connect == 12.5

    client.close()
