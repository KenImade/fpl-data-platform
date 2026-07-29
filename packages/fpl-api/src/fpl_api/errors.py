"""Error responses and request logging.

Every error the client sees is an RFC 9457 problem document, so a consumer
can parse failures the same way regardless of what went wrong. Third-party
developers judge an API on its errors more than its happy path.

Two rules the handlers exist to enforce:

  1. Internal detail never reaches the client. A database error message can
     name tables, columns and sometimes values. The client gets a request id;
     the detail goes to the log.

  2. Every response carries that request id, in the body and the header. A
     user reporting "request abc123 failed" can then be traced to the exact
     log line, which is otherwise near-impossible on a busy service.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from fpl_api.logging import request_id_var

log = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
REQUEST_ID_HEADER = "X-Request-ID"


class Problem(BaseModel):
    """RFC 9457 problem details.

    `type` is a URI identifying the problem class, clients
    switch on it.

    `detail` is human-readable and may change; `type` is the
    contract.
    """

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None


def _problem_response(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str | None = None,
    problem_type: str = "about:blank",
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_var.get()

    body = Problem(
        type=problem_type,
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path,
        request_id=request_id,
    ).model_dump(exclude_none=True)

    if extra:
        body.update(extra)

    response_headers = dict(headers or {})
    if request_id:
        response_headers[REQUEST_ID_HEADER] = request_id

    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=response_headers or None,
    )


def register(app: FastAPI) -> None:
    """Attach middleware and exception handlers. Call once at startup"""

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Assign a request id, time the request, log the outcome.

        An inbound X-Request-ID is honoured so a caller can correlate
        across their own system; otherwise one is generated.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request_id_var.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            log.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms),
                },
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path not in ("/health", "/health/db"):
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                },
            )

        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Deliberate 4xx from route code. Safe to surface:
        these message are written for client."""
        return _problem_response(
            request,
            status_code=exc.status_code,
            title=exc.detail if isinstance(exc.detail, str) else "Request failed",
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Bad input. The field-level errors are the useful part,
        without them a 422 tells a developer nothing about what to
        fix."""

        return _problem_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Invalid request",
            detail="One or more parameters failed validation",
            problem_type="https://premierlytics.com/problems/validation",
            extra={
                "errors": [
                    {
                        "field": ".".join(str(p) for p in e["loc"][1:]),
                        "message": e["msg"],
                    }
                    for e in exc.errors()
                ]
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Anything unanticipated.

        The exception is already logged with a traceback by the middleware.
        What goes to the client is deliberately bare: a database error can
        name table, columns and occassionally values, and none of that is
        the caller's business. They get a request id which is enough for
        support to find the real error.
        """
        return _problem_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal server error",
            detail="An unexpected error occurred. Quote the request id if reporting this.",
        )
