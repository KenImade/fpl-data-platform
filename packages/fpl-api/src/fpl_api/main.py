import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from fpl_api import db
from fpl_api.routers import teams

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(
    title="Premierlytics API",
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(teams.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": VERSION,
        "environment": os.environ.get("ENV", "development"),
        "sha": os.environ.get("GIT_SHA", "development"),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/health/db")
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
