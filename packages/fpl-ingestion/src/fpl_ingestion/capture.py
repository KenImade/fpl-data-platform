from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from fpl_ingestion.client import ENDPOINTS, RateLimited, fetch
from fpl_ingestion.storage import LATEST_BOOTSTRAP, Store, raw_key

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    at: datetime
    stored: dict[str, str]
    failed: dict[str, str]

    @property
    def ok(self) -> bool:
        return not self.failed


def capture(client: httpx.Client, store: Store, *, now: datetime | None = None) -> CaptureResult:
    """Fetch every endpoint and store the raw bytes

    Each endpoint is independent: one failing must not prevent the others.
    Nothing is parsed here, validation belongs in the bronze assets.
    """
    at = now or datetime.now(UTC)
    stored: dict[str, str] = {}
    failed: dict[str, str] = {}

    for name, url in ENDPOINTS.items():
        try:
            response = fetch(client, url)
        except RateLimited:
            failed[name] = "rate limited"
            log.error("rate limited on %s aborting remaining endpoints", name)
            break
        except Exception as exc:
            failed[name] = str(exc)
            log.exception("capture failed for %s", name)
            continue

        key = raw_key(name, at)
        store.put(key, response.body)
        stored[name] = key

        if name == "bootstrap-static":
            store.put(LATEST_BOOTSTRAP, response.body, overwrite=True)

    return CaptureResult(at=at, stored=stored, failed=failed)
