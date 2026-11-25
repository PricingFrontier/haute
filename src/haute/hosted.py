"""Hosted-mode support: run haute behind a platform SSO reverse proxy.

Haute's default posture is a local single-user UI: loopback-only trusted
hosts and rejection of any proxied traffic (see ``_local_security``). A
hosted platform such as Databricks Apps inverts that boundary — a
workspace-SSO reverse proxy authenticates every request and forwards it
with ``X-Forwarded-*`` metadata and the platform's public host name.

This module owns the trust decision for that arrangement, in one
inspectable place:

* :func:`databricks_app_environment` — detect the platform contract from
  the environment, loudly rejecting a partial contract.
* :class:`PlatformProxyBoundary` — refuse a page of another site, then present
  platform-proxied traffic to the local security middleware as the loopback
  traffic it was written for, after recording the forwarded user identity for
  attribution.
* :func:`create_app` — the deployment entry point tying both together and
  delegating request authentication to the platform proxy.

Design: specs/hosted-databricks-app/high-level.md.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from haute._local_security import DISABLE_AUTH_ENV, http_scheme, web_origin
from haute._logging import get_logger

logger = get_logger(component="hosted")

#: The Databricks Apps environment contract. All three are set inside an
#: app container; none are set anywhere else haute supports.
DATABRICKS_APP_ENV_VARS = (
    "DATABRICKS_APP_NAME",
    "DATABRICKS_APP_URL",
    "DATABRICKS_WORKSPACE_ID",
)

#: ASGI scope key under which :class:`PlatformProxyBoundary` records the
#: platform-authenticated user (from ``X-Forwarded-Email``), for log
#: attribution and future per-user features.
FORWARDED_USER_SCOPE_KEY = "haute_forwarded_user"

_FORWARDED = b"forwarded"
_FORWARDED_PREFIX = b"x-forwarded-"
_FORWARDED_EMAIL = b"x-forwarded-email"
_HOST = b"host"
_ORIGIN = b"origin"
_SEC_FETCH_SITE = b"sec-fetch-site"

#: The methods that change nothing; every other method, and every WebSocket handshake,
#: is refused when a page of another site sends it.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: The ``Sec-Fetch-Site`` values of the app's own page and of a request no page made.
_OWN_FETCH_SITES = frozenset({b"same-origin", b"none"})
_CROSS_SITE_WRITE = "A change to this Haute app comes from the app's own page."
_CROSS_SITE_SOCKET = "Live sync connects from the app's own page."


@dataclass(frozen=True)
class DatabricksAppEnvironment:
    """The detected Databricks Apps runtime contract."""

    app_name: str
    app_url: str
    workspace_id: str


def databricks_app_environment(
    environ: Mapping[str, str] | None = None,
) -> DatabricksAppEnvironment | None:
    """Return the Databricks Apps contract, or ``None`` outside a container.

    Hosted mode must never be guessed: a partial contract (some variables
    set, others absent) means an environment this module was not designed
    for, and raises rather than silently picking a posture.
    """
    env = os.environ if environ is None else environ
    values = {name: (env.get(name) or "").strip() for name in DATABRICKS_APP_ENV_VARS}
    present = sorted(name for name, value in values.items() if value)
    if not present:
        return None
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise RuntimeError(
            "Partial Databricks Apps environment contract: "
            f"{', '.join(present)} set but {', '.join(missing)} missing. "
            "Refusing to guess the hosting posture."
        )
    return DatabricksAppEnvironment(
        app_name=values["DATABRICKS_APP_NAME"],
        app_url=values["DATABRICKS_APP_URL"],
        workspace_id=values["DATABRICKS_WORKSPACE_ID"],
    )


def app_origin(app_url: str) -> tuple[str, str, int]:
    """The origin of the app's own page, from ``DATABRICKS_APP_URL``; a URL that is not
    an ``http``/``https`` origin stops the app, since no write could then be told from
    another site's."""
    origin = web_origin(app_url)
    if origin is None:
        raise RuntimeError(
            "DATABRICKS_APP_URL is not the app's origin (an http or https URL with no "
            "path): without it the app cannot tell its own page's changes from another "
            "site's."
        )
    return origin


class PlatformProxyBoundary:
    """Present platform-proxied traffic to haute as local loopback traffic.

    The platform's proxy signs users in and never looks at ``Origin``, and the
    hosted trust decision turns the local session gate off, so this boundary is
    the hosted editor's cross-site rule: a request that changes state, or a
    WebSocket handshake, whose ``Origin`` is not the app's own URL or whose
    ``Sec-Fetch-Site`` is not ``same-origin``/``none`` is refused before the
    server sees it. A request with neither header (not a browser) passes.

    The local security middleware rejects forwarded metadata and foreign
    hosts by design. Behind an authenticating platform proxy those
    signals are expected, so this boundary — and only this boundary —
    removes them: ``Forwarded``/``X-Forwarded-*`` headers are stripped
    (after recording ``X-Forwarded-Email`` in the scope for attribution),
    ``Host`` is rewritten to the loopback authority of the bound server, and
    the app's own ``Origin`` to that authority's loopback origin. Non-HTTP
    scopes (lifespan) pass through untouched.
    """

    def __init__(self, app: ASGIApp, *, own_origin: tuple[str, str, int]) -> None:
        self._app = app
        self._own_origin = own_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        server = scope.get("server") or ("127.0.0.1", 8000)
        forwarded_user: str | None = None
        origins: list[bytes] = []
        fetch_sites: list[bytes] = []
        headers: list[tuple[bytes, bytes]] = []
        for name, value in scope["headers"]:
            if name == _FORWARDED_EMAIL:
                cleaned = value.decode("latin-1").strip()
                forwarded_user = cleaned if cleaned else None
            elif name == _ORIGIN:
                origins.append(value)
                continue
            elif name == _SEC_FETCH_SITE:
                fetch_sites.append(value)
            if name == _FORWARDED or name == _HOST or name.startswith(_FORWARDED_PREFIX):
                continue
            headers.append((name, value))
        own_page = all(web_origin(value.decode("latin-1")) == self._own_origin for value in origins)
        if scope["type"] == "websocket" or scope["method"] not in _SAFE_METHODS:
            deciding: str | None = None
            if not own_page:
                deciding = "origin"
            elif any(site not in _OWN_FETCH_SITES for site in fetch_sites):
                deciding = "sec-fetch-site"
            if deciding is not None:
                await self._refuse(scope, receive, send, deciding)
                return
        authority = f"127.0.0.1:{server[1]}"
        headers.append((_HOST, authority.encode("ascii")))
        if origins and own_page:
            loopback = f"{http_scheme(scope.get('scheme', 'http'))}://{authority}"
            headers.append((_ORIGIN, loopback.encode("ascii")))
        else:
            headers.extend((_ORIGIN, value) for value in origins)
        scope = dict(scope, headers=headers)
        if forwarded_user is not None:
            scope[FORWARDED_USER_SCOPE_KEY] = forwarded_user
        await self._app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, deciding: str) -> None:
        logger.warning(
            "cross_site_request_refused",
            scope_type=scope["type"],
            method=scope.get("method", "GET"),
            header=deciding,
        )
        if scope["type"] == "websocket":
            await WebSocketClose(code=1008, reason=_CROSS_SITE_SOCKET)(scope, receive, send)
            return
        await JSONResponse(status_code=403, content={"detail": _CROSS_SITE_WRITE})(
            scope, receive, send
        )


def create_app(project_dir: Path) -> ASGIApp:
    """Build the ASGI app serving the project in *project_dir* for a recognised hosted
    environment.

    Raises when called outside one — hosted mode is an explicit
    deployment decision, never a fallback. Within one, request
    authentication is delegated to the platform proxy: the local session
    gate is disabled process-wide (the documented hosted trust model)
    and the server app is wrapped in :class:`PlatformProxyBoundary`, which
    refuses another site's page against the app's URL. The
    project is *project_dir* itself, the folder the bootstrap restored or
    seeded.
    """
    environment = databricks_app_environment()
    if environment is None:
        raise RuntimeError(
            "haute.hosted.create_app() called outside a recognised hosted "
            "environment (Databricks Apps contract absent). Use `haute serve` "
            "for local sessions."
        )
    # Read first, so a URL that is not an origin stops the app before the trust decision.
    own_origin = app_origin(environment.app_url)
    os.environ[DISABLE_AUTH_ENV] = "1"
    # Imported here so the trust decision above is already recorded when
    # the server module (and its middleware stack) initialises.
    from haute._project_context import ProjectContext
    from haute.server import create_app as create_server_app

    server_app = create_server_app(ProjectContext.at(project_dir))

    logger.info(
        "hosted_mode_enabled",
        platform="databricks-apps",
        app_name=environment.app_name,
        workspace_id=environment.workspace_id,
    )
    return PlatformProxyBoundary(server_app, own_origin=own_origin)
