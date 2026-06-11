"""Local-session protection for the single-user Haute server.

The visual editor is a local development surface that can execute user
pipelines and write files. Binding to loopback is the first line of
defence; a per-process session token plus local Origin checks prevent a
random web page from driving that local server through the user's browser.
"""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from starlette.datastructures import Headers, QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SESSION_TOKEN_ENV = "HAUTE_LOCAL_SESSION_TOKEN"
DISABLE_AUTH_ENV = "HAUTE_DISABLE_LOCAL_SESSION_AUTH"
SESSION_TOKEN_HEADER = "x-haute-session-token"
SESSION_TOKEN_QUERY_PARAM = "haute_session_token"

_BOOT_SESSION_TOKEN = secrets.token_urlsafe(32)
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TESTCLIENT_HOST = "testserver"


def local_session_auth_disabled() -> bool:
    """Return True when the explicit local-auth escape hatch is enabled."""
    return os.environ.get(DISABLE_AUTH_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


def local_session_token() -> str:
    """Return this process' local UI session token."""
    configured = os.environ.get(SESSION_TOKEN_ENV, "").strip()
    return configured or _BOOT_SESSION_TOKEN


def ensure_local_session_token_env() -> str:
    """Populate the token env var so child dev servers inherit the same token."""
    if local_session_auth_disabled():
        return ""
    configured = os.environ.get(SESSION_TOKEN_ENV, "").strip()
    if configured:
        return configured
    os.environ[SESSION_TOKEN_ENV] = _BOOT_SESSION_TOKEN
    return _BOOT_SESSION_TOKEN


def _normalise_host_header(value: str | None) -> str:
    host = (value or "").strip().rstrip(".").lower()
    if not host:
        return ""
    if host.startswith("["):
        closing = host.find("]")
        return host[1:closing] if closing != -1 else host
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def _origin_host(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    return (parsed.hostname or "").strip().rstrip(".").lower()


def _is_local_origin(headers: Headers) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True
    return _origin_host(origin) in _LOCAL_ORIGIN_HOSTS


def _is_testclient_harness(headers: Headers) -> bool:
    return _normalise_host_header(headers.get("host")) == _TESTCLIENT_HOST and not headers.get(
        "origin"
    )


def _token_matches(value: str | None) -> bool:
    supplied = (value or "").strip()
    return bool(supplied) and hmac.compare_digest(supplied, local_session_token())


def _query_token(query_params: QueryParams) -> str | None:
    token = query_params.get(SESSION_TOKEN_QUERY_PARAM)
    return token if token else None


class LocalSessionMiddleware(BaseHTTPMiddleware):
    """Require the local session token for real ``/api/*`` HTTP requests."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            local_session_auth_disabled()
            or not request.url.path.startswith("/api/")
            or _is_testclient_harness(request.headers)
        ):
            return await call_next(request)

        if not _is_local_origin(request.headers):
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin is not trusted for the local Haute session"},
            )

        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        if not _token_matches(request.headers.get(SESSION_TOKEN_HEADER)):
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing or invalid Haute session token"},
            )

        return await call_next(request)


def websocket_rejection_reason(headers: Headers, query_params: QueryParams) -> str | None:
    """Return a WebSocket rejection reason, or None when the socket may open."""
    if local_session_auth_disabled() or _is_testclient_harness(headers):
        return None
    if not _is_local_origin(headers):
        return "Origin is not trusted for the local Haute session"
    token = headers.get(SESSION_TOKEN_HEADER) or _query_token(query_params)
    if not _token_matches(token):
        return "Missing or invalid Haute session token"
    return None
