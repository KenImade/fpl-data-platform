"""Health

Provides information about the state of the API
and database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter
from fpl_api import VERSION, db

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": VERSION,
        "environment": os.environ.get("ENV", "development"),
        "sha": os.environ.get("GIT_SHA", "development"),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/health/db")
async def health_db() -> dict[str, object]:
    """Separate from /health deliberately.

    /health answers "is the process alive" which is the right
    check for a load balancer, and it must not fail because a
    database is briefly unreachable.

    /health/db answers "can we server requests", which is what
    monitoring should alert on.
    """
    marts = await db.fetch_value(
        "SELECT COUNT(*) FROM information_schema.tables " "WHERE table_schema = 'analytics_marts'"
    )
    return {"status": "ok", "marts": marts}
