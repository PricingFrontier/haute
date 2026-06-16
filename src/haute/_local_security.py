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
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.datastructures import Headers, QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_TOKEN_ENV = "HAUTE_LOCAL_SESSION_TOKEN"
DISABLE_AUTH_ENV = "HAUTE_DISABLE_LOCAL_SESSION_AUTH"
TRUSTED_HOSTS_ENV = "HAUTE_TRUSTED_HOSTS"
SESSION_TOKEN_HEADER = "x-haute-session-token"
SESSION_TOKEN_QUERY_PARAM = "haute_session_token"

_BOOT_SESSION_TOKEN = secrets.token_urlsafe(32)
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_ipv6_literal(value: str) -> bool:
    try:
        return ip_address(value).version == 6
    except ValueError:
        return False


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


def _normalise_host_value(value: str | None) -> str:
    host = (value or "").strip().rstrip(".").lower()
    if not host:
        return ""
    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            return ""
        inner = host[1:closing]
        if not _is_ipv6_literal(inner):
            return ""
        remainder = host[closing + 1 :]
        if remainder:
            if not remainder.startswith(":"):
                return ""
            port = remainder[1:]
            if not port.isdigit():
                return ""
        return host[1:closing]
    if host.count(":") == 1:
        name, port = host.rsplit(":", 1)
        if not port.isdigit():
            return ""
        return name
    return host


def _origin_host(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    authority = parsed.netloc.rsplit("@", 1)[-1]
    return _normalise_host_value(authority)


def _configured_origin_hosts() -> set[str]:
    raw_hosts = os.environ.get(TRUSTED_HOSTS_ENV, "")
    hosts: set[str] = set()
    for host in raw_hosts.split(","):
        normalised = _normalise_host_value(host)
        if normalised and normalised != "*":
            hosts.add(normalised)
    return hosts


class LocalTrustedHostMiddleware:
    """Trusted-host middleware with correct bracketed IPv6 host parsing."""

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str] | None = None) -> None:
        self.app = app
        self.allowed_hosts = list(allowed_hosts or ["*"])
        self.allow_any = "*" in self.allowed_hosts
        self._normalised_allowed_hosts = [
            pattern if "*" in pattern else _normalise_host_value(pattern)
            for pattern in self.allowed_hosts
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.allow_any or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host = _normalise_host_value(Headers(scope=scope).get("host", ""))
        is_valid_host = bool(host) and any(
            host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))
            for pattern in self._normalised_allowed_hosts
        )
        if is_valid_host:
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse("Invalid host header", status_code=400)
        await response(scope, receive, send)


def _is_local_origin(headers: Headers) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True
    origin_host = _origin_host(origin)
    return origin_host in _LOCAL_ORIGIN_HOSTS or origin_host in _configured_origin_hosts()


def _token_matches(value: str | None) -> bool:
    supplied = (value or "").strip()
    if not supplied:
        return False
    try:
        return hmac.compare_digest(supplied, local_session_token())
    except TypeError:
        return False


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
        if local_session_auth_disabled() or not request.url.path.startswith("/api/"):
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
    if local_session_auth_disabled():
        return None
    if not _is_local_origin(headers):
        return "Origin is not trusted for the local Haute session"
    token = headers.get(SESSION_TOKEN_HEADER) or _query_token(query_params)
    if not _token_matches(token):
        return "Missing or invalid Haute session token"
    return None
