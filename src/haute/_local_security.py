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
from http.cookies import CookieError, SimpleCookie
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_TOKEN_ENV = "HAUTE_LOCAL_SESSION_TOKEN"
DISABLE_AUTH_ENV = "HAUTE_DISABLE_LOCAL_SESSION_AUTH"
TRUSTED_HOSTS_ENV = "HAUTE_TRUSTED_HOSTS"
SESSION_TOKEN_COOKIE = "haute_session"
SESSION_BOOTSTRAP_PATH = "/api/session/bootstrap"

_BOOT_SESSION_TOKEN = secrets.token_urlsafe(32)
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

OriginState = Literal["missing", "trusted", "untrusted"]


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


def _normalise_authority(value: str | None) -> tuple[str, int | None] | None:
    authority = (value or "").strip().lower()
    if not authority or "@" in authority or any(char.isspace() for char in authority):
        return None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing == -1:
            return None
        inner = authority[1:closing]
        try:
            address = ip_address(inner)
        except ValueError:
            return None
        if address.version != 6:
            return None
        remainder = authority[closing + 1 :]
        if not remainder:
            return address.compressed, None
        if not remainder.startswith(":"):
            return None
        port_text = remainder[1:]
        if not port_text.isdigit():
            return None
        ipv6_port = int(port_text)
        return (address.compressed, ipv6_port) if 0 < ipv6_port <= 65535 else None

    if authority.count(":") > 1:
        try:
            address = ip_address(authority)
        except ValueError:
            return None
        return (address.compressed, None) if address.version == 6 else None

    host = authority
    port: int | None = None
    if ":" in authority:
        host, port_text = authority.rsplit(":", 1)
        if not port_text.isdigit():
            return None
        port = int(port_text)
        if not 0 < port <= 65535:
            return None
    host = host.rstrip(".")
    if not host:
        return None
    try:
        host = ip_address(host).compressed
    except ValueError:
        if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for char in host):
            return None
    return host, port


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _origin_authority(value: str | None) -> tuple[str, str, int | None] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    authority = _normalise_authority(parsed.netloc)
    if authority is None:
        return None
    host, parsed_port = authority
    if port != parsed_port:
        return None
    return parsed.scheme, host, port


def _http_scheme(scope_scheme: str) -> str:
    if scope_scheme in {"https", "wss"}:
        return "https"
    return "http"


def _effective_port(scheme: str, port: int | None) -> int:
    if port is not None:
        return port
    return 443 if scheme == "https" else 80


def _origin_state(headers: Headers, *, scope_scheme: str) -> OriginState:
    origin_value = headers.get("origin")
    if origin_value is None:
        return "missing"
    origin = _origin_authority(origin_value)
    request_authority = _normalise_authority(headers.get("host"))
    if origin is None or request_authority is None:
        return "untrusted"
    origin_scheme, origin_host, origin_port = origin
    request_host, request_port = request_authority
    request_scheme = _http_scheme(scope_scheme)
    if origin_scheme != request_scheme:
        return "untrusted"
    if not _is_loopback_host(origin_host) or not _is_loopback_host(request_host):
        return "untrusted"
    if origin_host != request_host:
        return "untrusted"
    if _effective_port(origin_scheme, origin_port) != _effective_port(request_scheme, request_port):
        return "untrusted"
    return "trusted"


def _has_forwarded_metadata(headers: Headers) -> bool:
    return any(
        name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
        for name in headers.keys()
    )


def _cookie_token(headers: Headers) -> str | None:
    raw_cookie = headers.get("cookie")
    if not raw_cookie:
        return None
    parsed = SimpleCookie()
    try:
        parsed.load(raw_cookie)
    except CookieError:
        return None
    morsel = parsed.get(SESSION_TOKEN_COOKIE)
    return morsel.value if morsel is not None else None


def _request_token_matches(headers: Headers) -> bool:
    return _token_matches(_cookie_token(headers))


def _configured_local_hosts() -> list[str]:
    raw_hosts = os.environ.get(TRUSTED_HOSTS_ENV, "")
    configured = [host.strip() for host in raw_hosts.split(",") if host.strip()]
    return configured or ["localhost", "127.0.0.1", "::1"]


def _validate_local_host_configuration(hosts: Sequence[str]) -> None:
    invalid: list[str] = []
    for raw_host in hosts:
        authority = _normalise_authority(raw_host)
        if authority is None or not _is_loopback_host(authority[0]):
            invalid.append(raw_host)
    if invalid:
        raise ValueError("Haute is a local-only UI; trusted hosts must all be loopback addresses")


class LocalTrustedHostMiddleware:
    """Reject non-loopback and forwarded HTTP/WebSocket requests."""

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str] | None = None) -> None:
        self.app = app
        self.allowed_hosts = list(allowed_hosts or _configured_local_hosts())
        _validate_local_host_configuration(self.allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if _has_forwarded_metadata(headers):
            response = PlainTextResponse(
                "Forwarded headers are not supported by the local Haute UI",
                status_code=400,
            )
            await response(scope, receive, send)
            return

        authority = _normalise_authority(headers.get("host"))
        if authority is not None and _is_loopback_host(authority[0]):
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse("Invalid host header", status_code=400)
        await response(scope, receive, send)


def _token_matches(value: str | None) -> bool:
    supplied = (value or "").strip()
    if not supplied:
        return False
    try:
        return hmac.compare_digest(supplied, local_session_token())
    except TypeError:
        return False


class LocalSessionMiddleware(BaseHTTPMiddleware):
    """Require local Origin and session credentials for ``/api/*`` requests."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if local_session_auth_disabled() or not request.url.path.startswith("/api/"):
            return await call_next(request)

        origin_state = _origin_state(request.headers, scope_scheme=request.url.scheme)
        is_bootstrap = (
            request.url.path == SESSION_BOOTSTRAP_PATH and request.method.upper() == "POST"
        )
        if is_bootstrap:
            if origin_state != "trusted":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Origin is not trusted for the local Haute session"},
                )
            return await call_next(request)

        if request.method.upper() == "OPTIONS":
            if origin_state != "trusted":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Origin is not trusted for the local Haute session"},
                )
            return await call_next(request)

        if origin_state == "untrusted":
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin is not trusted for the local Haute session"},
            )

        token_matches = _request_token_matches(request.headers)
        if origin_state == "missing" and not token_matches:
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin or existing local session is required"},
            )
        if not token_matches:
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing or invalid Haute session token"},
            )

        return await call_next(request)


def websocket_rejection_reason(headers: Headers, *, scope_scheme: str = "ws") -> str | None:
    """Return a pre-accept WebSocket rejection reason, never a supplied secret."""
    if local_session_auth_disabled():
        return None
    if _origin_state(headers, scope_scheme=scope_scheme) != "trusted":
        return "Origin is not trusted for the local Haute session"
    if not _request_token_matches(headers):
        return "Missing or invalid Haute session token"
    return None
