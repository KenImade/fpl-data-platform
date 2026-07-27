from __future__ import annotations

import json
import logging
from datetime import datetime

from fpl_ingestion.storage import LATEST_BOOTSTRAP, ObjectMissing, Store

log = logging.getLogger(__name__)


def read_deadlines(store: Store) -> list[datetime]:
    """Deadlines from the latest record bootstrap. Never raises."""
    try:
        raw = store.get(LATEST_BOOTSTRAP)
    except ObjectMissing:
        log.warning("no bootstrap stored yet")
        return []
    except Exception:
        log.exception("failed reading %s", LATEST_BOOTSTRAP)
        return []

    try:
        events = json.loads(raw)["events"]
        return [
            datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
            for e in events
            if e.get("deadline_time")
        ]
    except Exception:
        log.exception("malformed bootstrap, treating as no deadline")
        return []
