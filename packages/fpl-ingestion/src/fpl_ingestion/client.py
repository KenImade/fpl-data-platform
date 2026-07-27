from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

BASE = "https://fantasy.premierleague.com/api"

ENDPOINTS = {
    "bootstrap-static": f"{BASE}/bootstrap-static/",
    "fixtures": f"{BASE}/fixtures",
    "event-status": f"{BASE}/event-status",
}


class FetchError(RuntimeError):
    """All retries exhausted"""


class RateLimited(FetchError):
    """429 from upstream. Back off hard and alert."""


@dataclass(frozen=True, slots=True)
class Response:
    url: str
    body: bytes
    status: int
    duration_s: float


def fetch(
    client: httpx.Client,
    url: str,
    *,
    backoffs: tuple[float, ...] = (1.0, 4.0, 16.0),
    sleep=time.sleep,
) -> Response:
    last: Exception | None = None

    for attempt, delay in enumerate((0.0, *backoffs)):
        if delay:
            sleep(delay)
        started = time.monotonic()
        try:
            r = client.get(url)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            log.warning("attempt %d transport error for %s: %s", attempt + 1, url, exc)
            continue

        elapsed = time.monotonic() - started

        if r.status_code == 429:
            log.error("429 from %s", url)
            raise RateLimited(url)

        if r.status_code >= 500:
            last = FetchError(f"{r.status_code} from {url}")
            log.warning("attempt %d got %d for %s", attempt + 1, r.status_code, url)
            continue

        if r.status_code >= 400:
            raise FetchError(f"{r.status_code} from {url}")

        return Response(url=url, body=r.content, status=r.status_code, duration_s=elapsed)

    raise FetchError(f"{url}: all attempts failed") from last


def make_client(user_agent: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=timeout,
        follow_redirects=True,
    )
