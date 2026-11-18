"""The HTTP edge of every failure answer (``haute._failure_answers``), for both applications.

The editor's server and the underwriter app each install it with :func:`install_failure_answers`.
A route raises and :class:`RequestFailureMiddleware` answers: whatever reaches it is put into
words by :func:`haute._failure_answers.answer_failure`, a failure nothing names with an
incident id and its traceback logged under it, so no route answers a bare 500, a traceback or
"check the server logs". This module is the only place a failure becomes an HTTP response;
routes and services raise Haute's refusals (``RequestRefusedError`` and its kinds), never an
HTTP type (``tests/test_failure_boundaries.py``).

The middleware also binds each request's id, logs the request with its timing and sets the
``x-request-id`` header on every answer. A request its models refuse keeps FastAPI's 422 list
(``request_validation_error_handler``), registered as an exception handler.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from haute._failure_answers import FailureAnswer, answer_failure
from haute._logging import get_logger
from haute.hosted import FORWARDED_USER_SCOPE_KEY
from haute.routes._validation_errors import request_validation_error_handler

logger = get_logger(component="server")

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)


def select_request_id(value: str | None) -> tuple[str, dict[str, str | int] | None]:
    """Return a safe correlation token plus bounded rejection metadata."""
    if value is None:
        return uuid.uuid4().hex[:12], None
    length = len(value)
    if length > 64:
        return uuid.uuid4().hex[:12], {"reason": "too_long", "length": length}
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return uuid.uuid4().hex[:12], {"reason": "non_ascii", "length": length}
    if _REQUEST_ID_RE.fullmatch(value) is None:
        return uuid.uuid4().hex[:12], {"reason": "invalid_format", "length": length}
    return value, None


def failure_response(answer: FailureAnswer) -> JSONResponse:
    """*answer* as an HTTP response: its status, its detail under ``detail``, its headers."""
    return JSONResponse(
        status_code=answer.status, content={"detail": answer.detail}, headers=dict(answer.headers)
    )


class RequestFailureMiddleware(BaseHTTPMiddleware):
    """Bind the request's id, log the request with its timing, and answer every failure a
    route raises through :func:`haute._failure_answers.answer_failure`."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        rid, rejection = select_request_id(request.headers.get("x-request-id"))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        if rejection is not None:
            logger.warning("request_id_rejected", **rejection)

        method = request.method
        path = request.url.path
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            response = failure_response(answer_failure(exc, operation=f"{method} {path}"))
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        status = response.status_code
        response.headers["x-request-id"] = rid

        kw: dict[str, Any] = dict(method=method, path=path, status=status, duration_ms=duration_ms)
        forwarded_user = request.scope.get(FORWARDED_USER_SCOPE_KEY)
        if forwarded_user is not None:
            kw["user"] = forwarded_user
        if status >= 500:
            logger.error("request_error", **kw)
        elif status >= 400:
            logger.warning("request_client_error", **kw)
        elif path.startswith("/api/"):
            logger.info("request_ok", **kw)
        return response


def install_failure_answers(app: FastAPI) -> None:
    """Answer *app*'s failures here: FastAPI's 422 for a request its models refuse, and
    :class:`RequestFailureMiddleware` for everything else. Middleware added after this wraps
    it, so whatever it answers itself (a host or a session refused) is answered before the
    request is let in."""
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_middleware(RequestFailureMiddleware)
