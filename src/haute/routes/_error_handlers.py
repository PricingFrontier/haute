"""Application exception handlers: each error family is translated once.

A route raises and these handlers answer, the same way for every route. A
route keeps its own ``except`` clause only where it genuinely maps an error
differently (a preview embeds a schema mismatch in its node result, a trace
turns a row mismatch into a 409). Anything no handler claims is an unexpected
failure: ``_RequestIdMiddleware`` in ``haute.server`` logs it with its
traceback and answers 500 with ``_INTERNAL_ERROR_DETAIL``.
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.responses import Response

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionMemoryLimitExceededError
from haute._git import GitError
from haute._logging import get_logger
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
    contract_error_payload,
    memory_limit_http_exception,
)
from haute.routes.git import git_error_http_exception

logger = get_logger(component="server")


async def _respond(request: Request, exc: Exception, http_exc: HTTPException) -> Response:
    # Starlette also routes WebSocket failures through these handlers, and an
    # HTTP response cannot answer a WebSocket; let the connection fail as it
    # would with no handler registered.
    if request.scope["type"] != "http":
        raise exc
    return await http_exception_handler(request, http_exc)


async def _public_contract_error_handler(request: Request, exc: Exception) -> Response:
    logger.warning("public_contract_error", **contract_error_payload(exc))
    return await _respond(request, exc, contract_error_http_exception(exc))


async def _memory_limit_error_handler(request: Request, exc: Exception) -> Response:
    # Registered only for these two types, so the narrowing always holds.
    memory_exc = cast(ExecutionAdmissionError | ExecutionMemoryLimitExceededError, exc)
    return await _respond(request, exc, memory_limit_http_exception(memory_exc))


async def _git_error_handler(request: Request, exc: Exception) -> Response:
    return await _respond(request, exc, git_error_http_exception(cast(GitError, exc)))


def install_exception_handlers(app: FastAPI) -> None:
    """Register the application's exception handlers on *app*."""
    for error_type in PUBLIC_CONTRACT_ERROR_TYPES:
        app.add_exception_handler(error_type, _public_contract_error_handler)
    for memory_error_type in (ExecutionAdmissionError, ExecutionMemoryLimitExceededError):
        app.add_exception_handler(memory_error_type, _memory_limit_error_handler)
    app.add_exception_handler(GitError, _git_error_handler)
