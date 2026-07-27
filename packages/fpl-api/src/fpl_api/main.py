import os
from datetime import UTC, datetime

from fastapi import FastAPI

app = FastAPI()

VERSION = "0.1.0"


@app.get("/health")
def health() -> dict[str, str]:
    data: dict[str, str] = {
        "status": "ok",
        "version": VERSION,
        "environment": os.environ.get("ENV", "development"),
        "sha": os.environ.get("GIT_SHA", "development"),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return data
