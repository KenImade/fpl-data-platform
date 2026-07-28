"""Mirror the Core Insights repository into raw storage.

Everything here is reconstructible from GitHub's current state, so all three
mirrors are declarative: materialise against an empty bucket and they fetch,
materialise again and they no-op. Nothing requires a manual script or a
particular day of the week to recover.

Contrast with fpl_ingestion.capture, which stays a job because a capture is
an *observation* — the FPL API only ever serves current state, so a snapshot
missed at 17:25 on a deadline day cannot be reconstructed later at any price.

Idempotency is by key existence rather than by cursor, so it survives a
daemon restart and a wiped instance without any stored state.
"""

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

ARCHIVE_SEASON = "2024-2025"
# 2024/25 predates the flat layout: data/{season}/{table}/{table}.csv.
# `matches` and `playermatchstats` are the valuable ones — for active seasons
# those tables exist only inside the tarball, so this is the only route to
# that season's per-match data and its CBIT components.
ARCHIVE_TABLES = ("players", "teams", "playerstats", "matches", "playermatchstats")

TARBALL_PREFIX = "raw/core-insights/_full/"


def master_key(season: str, table: str, day: date) -> str:
    return f"raw/core-insights/{season}/{table}/{day:%Y-%m-%d}.csv.gz"


def archive_key(table: str) -> str:
    """Fixed suffix, not a date. This is a one-time archive of a completed
    season rather than a snapshot of something still moving."""
    return f"raw/core-insights/{ARCHIVE_SEASON}/{table}/{ARCHIVE_SEASON}-final.csv.gz"


def tarball_key(day: date) -> str:
    return f"{TARBALL_PREFIX}{day:%Y-%m-%d}.tar.gz"


def mirror_masters(client: httpx.Client, store: Store, day: date) -> dict[str, int]:
    """Daily master CSVs for every active season.

    Failures are non-fatal. This is a backup of a third-party source, not the
    primary capture path, and some files legitimately don't exist yet in
    pre-season. A sustained non-zero `failed` count means Core Insights has
    stopped updating — which is the risk the mirror exists to cover.
    """
    stored = skipped = failed = 0

    for season in ACTIVE_SEASONS:
        for name in MASTER_FILES:
            key = master_key(season, name, day)
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


def mirror_archive(client: httpx.Client, store: Store) -> dict[str, int]:
    """The finished 2024/25 season.

    Static — the files will never change — but declarative rather than a
    manual script. An empty bucket recovers on the next materialisation
    instead of requiring someone to remember a command.
    """
    stored = skipped = failed = 0

    for name in ARCHIVE_TABLES:
        key = archive_key(name)
        if store.exists(key):
            skipped += 1
            continue

        url = f"{CORE_INSIGHTS_RAW}/{ARCHIVE_SEASON}/{name}/{name}.csv"
        try:
            body = fetch(client, url).body
        except Exception:
            log.warning("archive mirror failed: %s", name, exc_info=True)
            failed += 1
            continue

        store.put(key, body)
        stored += 1
        log.info("archived %s: %d bytes", name, len(body))

    return {"stored": stored, "skipped": skipped, "failed": failed}


def mirror_tarball(client: httpx.Client, store: Store, day: date) -> dict[str, object]:
    """Full repository snapshot, for the By Gameweek/ and By Tournament/ trees.

    Weekly by schedule, because the archive is large and past-gameweek
    snapshots don't change. But it fetches unconditionally when *no* tarball
    exists at all, so a wiped or newly deployed environment recovers on the
    first tick rather than waiting up to seven days for Sunday.
    """
    key = tarball_key(day)

    if store.exists(key):
        return {"stored": 0, "skipped": 1, "failed": 0, "reason": "already have today"}

    existing = store.list(TARBALL_PREFIX)
    reason = "scheduled" if existing else "bootstrap: no tarball present"

    try:
        body = fetch(client, CORE_INSIGHTS_TARBALL).body
    except Exception:
        log.warning("tarball mirror failed", exc_info=True)
        return {"stored": 0, "skipped": 0, "failed": 1, "reason": reason}

    # codeload returns a gzipped tarball; wrapping it in another gzip layer
    # would make it unreadable to `tar -xzf`.
    store.put(key, body, compress=False)

    return {
        "stored": 1,
        "skipped": 0,
        "failed": 0,
        "bytes": len(body),
        "reason": reason,
    }
