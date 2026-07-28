from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 10.0


def ping(suffix: str = "") -> None:
    """Signal liveness to an external monitor.

    The only alert that catches the daemon being dead. Every other check
    fires when something fails; if nothing runs, nothing fails, and the
    silence is indistinguishable from health from the inside.

    Never raises. Monitoring must not be able to break the work it monitors.
    """
    url = os.environ.get("HEARTBEAT_URL")
    if not url:
        log.debug("HEARTBEAT_URL unset, skipping ping")
        return

    try:
        httpx.get(f"{url}{suffix}", timeout=TIMEOUT)
    except Exception:
        log.warning("heartbeat ping failed", exc_info=True)
