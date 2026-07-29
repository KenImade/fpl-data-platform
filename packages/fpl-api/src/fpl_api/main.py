from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fpl_api import VERSION, db, errors
from fpl_api.logging import configure as configure_logging
from fpl_api.routers import gameweeks, health, teams

configure_logging()


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

errors.register(app)
app.include_router(gameweeks.router)
app.include_router(health.router)
app.include_router(teams.router)
