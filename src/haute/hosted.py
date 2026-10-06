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
* :class:`PlatformProxyBoundary` — present platform-proxied traffic to the
  local security middleware as the loopback traffic it was written for,
  after recording the forwarded user identity for attribution.
* :func:`create_app` — the deployment entry point tying both together and
  delegating request authentication to the platform proxy.

Design: specs/hosted-databricks-app/high-level.md.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from haute._local_security import DISABLE_AUTH_ENV
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


class PlatformProxyBoundary:
    """Present platform-proxied traffic to haute as local loopback traffic.

    The local security middleware rejects forwarded metadata and foreign
    hosts by design. Behind an authenticating platform proxy those
    signals are expected, so this boundary — and only this boundary —
    removes them: ``Forwarded``/``X-Forwarded-*`` headers are stripped
    (after recording ``X-Forwarded-Email`` in the scope for attribution)
    and ``Host`` is rewritten to the loopback authority of the bound
    server. Non-HTTP scopes (lifespan) pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            server = scope.get("server") or ("127.0.0.1", 8000)
            forwarded_user: str | None = None
            headers: list[tuple[bytes, bytes]] = []
            for name, value in scope["headers"]:
                if name == _FORWARDED_EMAIL:
                    cleaned = value.decode("latin-1").strip()
                    forwarded_user = cleaned if cleaned else None
                if name == _FORWARDED or name == _HOST or name.startswith(_FORWARDED_PREFIX):
                    continue
                headers.append((name, value))
            headers.append((_HOST, f"127.0.0.1:{server[1]}".encode("ascii")))
            scope = dict(scope, headers=headers)
            if forwarded_user is not None:
                scope[FORWARDED_USER_SCOPE_KEY] = forwarded_user
        await self._app(scope, receive, send)


def create_app() -> ASGIApp:
    """Build the ASGI app for a recognised hosted environment.

    Raises when called outside one — hosted mode is an explicit
    deployment decision, never a fallback. Within one, request
    authentication is delegated to the platform proxy: the local session
    gate is disabled process-wide (the documented hosted trust model)
    and the server app is wrapped in :class:`PlatformProxyBoundary`.
    """
    environment = databricks_app_environment()
    if environment is None:
        raise RuntimeError(
            "haute.hosted.create_app() called outside a recognised hosted "
            "environment (Databricks Apps contract absent). Use `haute serve` "
            "for local sessions."
        )
    os.environ[DISABLE_AUTH_ENV] = "1"
    # Imported here so the trust decision above is already recorded when
    # the server module (and its middleware stack) initialises — and, in
    # deployments that restore a bound project, after the working
    # directory is already the restored clone.
    from haute.server import app as server_app

    logger.info(
        "hosted_mode_enabled",
        platform="databricks-apps",
        app_name=environment.app_name,
        workspace_id=environment.workspace_id,
    )
    return PlatformProxyBoundary(server_app)
