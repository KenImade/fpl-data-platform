"""Structured logging.

JSON in production so logs are queryable; human-readable
locally so they're readable. Both carry a request id, set
once per request and available anywhere via a container
so a log line written deep in a query helper can be
correlated with the response the client received, without
threading an id through ever function signature.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if request_id := request_id_var.get():
            payload["request_id"] = request_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Readable local output. Same fields, laid out for
    eyes not machines"""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if request_id := request_id_var.get():
            extras["request_id"] = request_id[:8]

        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())

        return base


def configure(level: str | None = None) -> None:
    """Call once at startup, before anything logs."""
    handler = logging.StreamHandler(sys.stdout)

    if os.environ.get("ENV") in ("production", "staging"):
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ConsoleFormatter("%(asctime)s %(levelname)-7s %(name)-24s %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))

    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
