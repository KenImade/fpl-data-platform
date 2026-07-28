from __future__ import annotations

import logging
from datetime import date

import httpx

from fpl_ingestion.client import fetch
from fpl_ingestion.storage import Store

log = logging.getLogger(__name__)

CORE_INSIGHTS_RAW = "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data"
CORE_INSIGHTS_TARBALL = (
    "https://codeload.github.com/olbauday/FPL-Core-Insights/tar.gz/refs/heads/main"
)
ACTIVE_SEASONS = ("2025-2026", "2026-2027")
MASTER_FILES = ("players", "teams", "playerstats", "gameweek_summaries")


def mirror_masters(client: httpx.Client, store: Store, day: date) -> dict[str, int]:
    """Mirror Core Insights master CSVs into bronze.

    Idempotent by key existence rather than a cursor — a re-run within the
    same day is a no-op and it survives a daemon restart without state.

    Failures are non-fatal. This is a backup of a third-party source, not the
    primary capture path, so a missing or moved file is logged and skipped.
    Some files legitimately don't exist yet in pre-season.
    """
    stored = skipped = failed = 0

    for season in ACTIVE_SEASONS:
        for name in MASTER_FILES:
            key = f"raw/core-insights/{season}/{name}/{day:%Y-%m-%d}.csv.gz"
            if store.exists(key):
                skipped += 1
                continue

            try:
                body = fetch(client, f"{CORE_INSIGHTS_RAW}/{season}/{name}.csv").body
            except Exception:
                log.warning("mirror failed: %s/%s", season, name, exc_info=True)
                failed += 1
                continue

            store.put(key, body)
            stored += 1

    return {"stored": stored, "skipped": skipped, "failed": failed}


def mirror_tarball(client: httpx.Client, store: Store, day: date) -> dict[str, int]:
    """Full repository snapshot, for the By Gameweek/ and By Tournament/ trees.

    Weekly rather than daily: the tarball is large and past-gameweek snapshots
    don't change. The master files in mirror_masters() cover what moves.
    """
    key = f"raw/core-insights/_full/{day:%Y-%m-%d}.tar.gz"
    if store.exists(key):
        return {"stored": 0, "skipped": 1, "failed": 0}

    try:
        body = fetch(client, CORE_INSIGHTS_TARBALL).body
    except Exception:
        log.warning("tarball mirror failed", exc_info=True)
        return {"stored": 0, "skipped": 0, "failed": 1}

    # Already gzipped by codeload — don't double-compress.
    store.put(key, body, compress=False)
    return {"stored": 1, "skipped": 0, "failed": 0, "bytes": len(body)}
