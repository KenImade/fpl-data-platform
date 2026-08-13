"""Application entry point"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fpl_api import VERSION, db, errors, ratelimit
from fpl_api.logging import configure as configure_logging
from fpl_api.routers import (
    fixtures,
    gameweeks,
    health,
    players,
    predictions,
    teams,
)

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await db.connect()
    await ratelimit.connect()
    try:
        yield
    finally:
        await ratelimit.disconnect()
        await db.disconnect()


app = FastAPI(
    title="Premierlytics API",
    version=VERSION,
    description=(
        "Fantasy Premier League data. Authenticat with a publishable key "
        "(pk_) from the browser or a secret key (sk_) server side."
    ),
    lifespan=lifespan,
)

errors.register(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "X-Request-ID", "Content-Type"],
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ],
    max_age=3600,
)


for r in (
    health.router,
    teams.router,
    gameweeks.router,
    players.router,
    fixtures.router,
    predictions.router,
):
    app.include_router(r)
