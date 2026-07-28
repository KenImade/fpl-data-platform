"""One-off mirror of the 2024/25 Core Insights season.

Not a schedule. 2024/25 is finished — the files won't change, so this runs
once and the objects are archival.

Two things differ from the daily mirror:

1. Layout. 2024/25 predates the flat structure and nests each table in its
   own directory: data/2024-2025/{table}/{table}.csv. Later seasons use
   data/{season}/{table}.csv.

2. Key suffix. Daily mirrors are keyed by capture date because the upstream
   file changes. This is a one-time archive of a completed season, so the
   key says so rather than implying a snapshot of a moving target.

Run from the repo root:
    uv run python scripts/mirror_2024_25.py
"""

from __future__ import annotations

import logging
import os
import sys

from fpl_ingestion.client import make_client
from fpl_ingestion.mirror import CORE_INSIGHTS_RAW
from fpl_ingestion.resources import build_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("mirror-2024-25")

SEASON = "2024-2025"

# Nested layout. playermatchstats carries the CBIT components that make the
# defensive-contribution reconstruction possible (see ADR 0005), so it is the
# most valuable file here — the aggregate field doesn't exist this season.
TABLES = (
    "players",
    "teams",
    "playerstats",
    "matches",
    "playermatchstats",
)


def main() -> int:
    store = build_store()
    stored = skipped = failed = 0

    with make_client(os.environ["USER_AGENT"]) as client:
        for name in TABLES:
            key = f"raw/core-insights/{SEASON}/{name}/{SEASON}-final.csv.gz"

            if store.exists(key):
                log.info("skip %-18s already archived", name)
                skipped += 1
                continue

            url = f"{CORE_INSIGHTS_RAW}/{SEASON}/{name}/{name}.csv"
            try:
                body = fetch_with_log(client, url, name)
            except Exception:
                log.warning("fail %-18s %s", name, url, exc_info=True)
                failed += 1
                continue

            store.put(key, body)
            stored += 1

    log.info("stored=%d skipped=%d failed=%d", stored, skipped, failed)

    if failed:
        log.error("some files failed — rerun to fill the gaps (existing keys are skipped)")
        return 1
    return 0


def fetch_with_log(client, url: str, name: str) -> bytes:
    from fpl_ingestion.client import fetch

    body = fetch(client, url).body
    log.info("ok   %-18s %8d bytes", name, len(body))
    return body


if __name__ == "__main__":
    sys.exit(main())
