"""``haute serve`` command.

Split into:

* :class:`ServeConfig` — the typed bag of CLI inputs.
* :func:`handle_serve` — the pure function that does the work.
* :func:`serve` — the thin ``@click.command`` entry point.

Host-binding safety
-------------------
Haute is a local UI, so every serve path is loopback-only. Non-loopback
values fail before port probing or either frontend/backend process starts,
whether supplied on the CLI or through ``haute.toml``.
"""

from __future__ import annotations

import ipaddress
import os
import signal
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import click

from haute._local_security import (
    DEFAULT_TRUSTED_HOSTS,
    TRUSTED_HOSTS_ENV,
    ensure_local_session_token_env,
)
from haute._logging import get_logger
from haute.cli._helpers import _find_frontend_dir, _node_env, _npm, _open_browser

logger = get_logger(component="serve")

_BACKEND_READY_TIMEOUT_SECONDS = 30.0
_BACKEND_READY_POLL_INTERVAL_SECONDS = 0.1
_VITE_HOST = "127.0.0.1"
_VITE_PORT = 5173
# ``127.0.0.1`` is the canonical IPv4 loopback; ``::1`` is the IPv6
# loopback; ``localhost`` is the DNS name conventionally resolved to
# one of those.  Any of the three are treated as loopback-safe.  Every
# other host — including the wildcard ``0.0.0.0`` and ``::`` — is
# non-loopback and therefore rejected.
_LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost"})


class _Closable(Protocol):
    """Small socket-like protocol used by the readiness probe."""

    def close(self) -> None:
        """Release the connection."""


_Connect = Callable[[tuple[str, int], float], _Closable]
_Sleep = Callable[[float], None]
_Monotonic = Callable[[], float]


def _is_loopback_host(host: str) -> bool:
    """Return ``True`` iff *host* is a loopback-safe bind target.

    Accepts three forms:

    * The DNS name ``localhost`` (case-insensitive).
    * An IPv4 or IPv6 address that parses as a loopback address
      (``127.0.0.0/8`` and ``::1`` respectively).  Parsing via
      :mod:`ipaddress` covers the full loopback range, not just
      ``127.0.0.1`` — tools that bind to ``127.0.0.42`` for isolation
      should be treated as loopback too.

    Anything else (including the wildcards ``0.0.0.0`` and ``::``,
    public IPs, and off-loopback hostnames) returns ``False`` and is
    rejected.
    """
    normalised = host.strip().lower()
    if normalised in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(normalised).is_loopback
    except ValueError:
        # Not a valid IP literal — treat as a hostname that isn't
        # ``localhost``.  A user binding to a hostname other than
        # ``localhost`` almost certainly means a network-routable
        # address, so reject it.
        return False


def _http_url(config: ServeConfig) -> str:
    """Return a valid local HTTP URL, including brackets for IPv6 literals."""
    host = config.host.strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        authority_host = host
    else:
        authority_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{authority_host}:{config.port}"


def _load_toml_server_host(project_dir: Path) -> str | None:
    """Return ``[server].host`` from ``haute.toml`` in *project_dir*, if set.

    Returns ``None`` when the file is absent, missing the ``[server]``
    table, or when the ``host`` key is not a string.  An ``OSError``
    (permission denied, transient FS issue) is logged as a warning and
    treated as "no override" so ``haute serve`` can still start on the
    loopback default.

    A ``TOMLDecodeError``, by contrast, is raised as :class:`ConfigError`
    — a typo in ``[server] host = "0.0.0.0"`` silently falling back to
    127.0.0.1 would make the user believe they had exposed the server
    when they had not (or vice-versa).  Failing loudly here surfaces the
    typo immediately.
    """
    toml_path = project_dir / "haute.toml"
    if not toml_path.is_file():
        return None
    try:
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        logger.warning(
            "haute_toml_read_failed",
            path=str(toml_path),
            error_type=type(exc).__name__,
            reason=str(exc),
        )
        return None
    except tomllib.TOMLDecodeError as exc:
        from haute.errors import ConfigError

        raise ConfigError(
            "haute.toml is malformed and could not be parsed",
            path=str(toml_path),
            error=str(exc),
        ) from exc
    server = data.get("server")
    if not isinstance(server, dict):
        return None
    host = server.get("host")
    if not isinstance(host, str) or not host:
        return None
    return host


@dataclass
class ServeConfig:
    """Parsed inputs for the ``haute serve`` command."""

    port: int
    no_browser: bool
    host: str = "localhost"


def _socket_family_for_host(host: str) -> socket.AddressFamily:
    """Return the socket family needed to probe a host bind target."""
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return socket.AF_INET
    return socket.AF_INET6 if address.version == 6 else socket.AF_INET


def _port_is_available(host: str, port: int) -> bool:
    """Return ``True`` iff a TCP socket can bind to ``(host, port)``.

    Opens and immediately closes a socket so the port is released before
    uvicorn tries to take it.

    On Windows we set ``SO_EXCLUSIVEADDRUSE`` so that the OS reports a
    conflict even when the peer socket was bound with ``SO_REUSEADDR``;
    without this, two sockets can silently share the same port, defeating
    the pre-flight check. On POSIX, the default behaviour is already
    strict enough.
    """
    family = _socket_family_for_host(host)
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        # On Windows the default allows a second bind to succeed when the
        # first socket used SO_REUSEADDR. SO_EXCLUSIVEADDRUSE asks the OS to
        # reject any overlapping bind, matching what uvicorn will observe a
        # few milliseconds later.
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _backend_probe_host(bind_host: str) -> str:
    """Return a host that can be connected to for a uvicorn bind target.

    Uvicorn can bind to wildcard addresses such as ``0.0.0.0`` and
    ``::``, but those are not meaningful remote endpoints for a TCP
    readiness check.  Probe the corresponding loopback address instead
    while preserving explicit concrete hosts.
    """
    host = bind_host.strip()
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.is_unspecified:
        return "::1" if address.version == 6 else "127.0.0.1"
    return host


def _wait_for_tcp_ready(
    host: str,
    port: int,
    *,
    timeout: float = _BACKEND_READY_TIMEOUT_SECONDS,
    poll_interval: float = _BACKEND_READY_POLL_INTERVAL_SECONDS,
    connect: _Connect = socket.create_connection,
    sleep: _Sleep = time.sleep,
    monotonic: _Monotonic = time.monotonic,
) -> None:
    """Block until ``host:port`` accepts TCP connections or raise.

    Only connection-refused / timed-out attempts are treated as
    "backend not ready yet".  Unexpected socket errors, such as an
    unresolvable host, are allowed to propagate so the CLI does not
    paper over a broken configuration with a misleading timed open.
    """
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be greater than zero")

    deadline = monotonic() + timeout
    last_not_ready: BaseException | None = None
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            conn = connect((host, port), min(poll_interval, remaining))
        except (ConnectionRefusedError, TimeoutError) as exc:
            last_not_ready = exc
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(poll_interval, remaining))
            continue

        conn.close()
        return

    raise TimeoutError(
        f"Timed out waiting for backend at {host}:{port} to accept TCP connections"
    ) from last_not_ready


def _require_loopback_host(config: ServeConfig) -> None:
    """Reject every network-visible bind before any startup side effect."""
    if _is_loopback_host(config.host):
        return
    raise click.ClickException(
        "Haute only serves locally; --host must be localhost or a loopback IP address."
    )


def _configure_trusted_hosts(config: ServeConfig) -> None:
    """Trust canonical loopbacks plus the validated bind host for the child."""
    _require_loopback_host(config)
    trusted_hosts = dict.fromkeys((*DEFAULT_TRUSTED_HOSTS, config.host))
    os.environ[TRUSTED_HOSTS_ENV] = ",".join(trusted_hosts)


def _abort_if_port_in_use(config: ServeConfig) -> None:
    """Fail loudly with exit code 1 when the target port is already bound.

    Running the pre-flight probe here — rather than letting uvicorn
    attempt the bind and crash with a cryptic ``OSError`` — lets us
    surface a user-facing message that names the flag to try next.
    """
    if _port_is_available(config.host, config.port):
        return
    click.echo(
        f"Error: port {config.port} already in use. Use --port to choose another.",
        err=True,
    )
    raise SystemExit(1)


def _abort_if_vite_port_in_use() -> None:
    """Fail before dev startup when Vite's fixed local port is occupied."""
    if _port_is_available(_VITE_HOST, _VITE_PORT):
        return
    click.echo(
        f"Error: Vite port {_VITE_PORT} already in use. Stop the process using it and retry.",
        err=True,
    )
    raise SystemExit(1)


def _detect_dev_frontend_dir() -> Path | None:
    """Return the ``frontend/`` dir iff dev mode is viable, else ``None``.

    Prefer a frontend discovered from the working directory, then fall back
    to the frontend beside an editable Haute source checkout.  The latter is
    what lets ``haute serve`` run from a separate user project while still
    serving the local library's current TypeScript rather than a potentially
    older generated bundle.  Either frontend must have ``node_modules``
    installed; wheel installs have no source checkout and use packaged assets.
    """
    try:
        frontend_dir = _find_frontend_dir()
    except FileNotFoundError:
        frontend_dir = None
    if frontend_dir is not None and (frontend_dir / "node_modules").is_dir():
        return frontend_dir

    source_root = _source_checkout_root()
    if source_root is None:
        return None
    source_frontend = source_root / "frontend"
    if not (source_frontend / "node_modules").is_dir():
        return None
    return source_frontend


def _schedule_browser_open(url: str, delay: float) -> None:
    """Open *url* in the default browser after *delay* seconds.

    Deferred so the server has time to start accepting connections
    before the browser races to fetch the page.
    """
    import threading

    threading.Timer(delay, _open_browser, args=(url,)).start()


def _wait_for_backend_then_open_browser(
    browser_url: str,
    backend_host: str,
    backend_port: int,
) -> None:
    """Open the browser once the backend socket is accepting connections."""
    probe_host = _backend_probe_host(backend_host)
    try:
        _wait_for_tcp_ready(probe_host, backend_port)
    except TimeoutError as exc:
        logger.warning(
            "browser_open_backend_ready_timeout",
            host=probe_host,
            port=backend_port,
            frontend_url=browser_url,
            reason=str(exc),
        )
        click.echo(
            "Backend did not become ready in time; the browser was not opened "
            f"automatically. Open {browser_url} once the backend is ready.",
            err=True,
        )
        return
    _open_browser(browser_url)


def _wait_for_servers_then_open_browser(
    browser_url: str, backend_host: str, backend_port: int
) -> None:
    """Open the browser only once both the API and Vite accept connections."""
    try:
        _wait_for_tcp_ready(_backend_probe_host(backend_host), backend_port)
        _wait_for_tcp_ready(_VITE_HOST, _VITE_PORT)
    except TimeoutError as exc:
        logger.warning(
            "browser_open_server_ready_timeout",
            frontend_url=browser_url,
            reason=str(exc),
        )
        click.echo(
            "Servers did not become ready in time; the browser was not opened automatically. "
            f"Open {browser_url} once they are ready.",
            err=True,
        )
        return
    _open_browser(browser_url)


def _open_browser_after_backend_ready(
    browser_url: str,
    backend_host: str,
    backend_port: int,
) -> None:
    """Start a background readiness wait before opening *browser_url*."""
    import threading

    threading.Thread(
        target=_wait_for_backend_then_open_browser,
        args=(browser_url, backend_host, backend_port),
        name="haute-open-browser-after-backend-ready",
        daemon=True,
    ).start()


def _open_browser_after_servers_ready(
    browser_url: str,
    backend_host: str,
    backend_port: int,
) -> None:
    """Start a background readiness wait for the backend and Vite."""
    import threading

    threading.Thread(
        target=_wait_for_servers_then_open_browser,
        args=(browser_url, backend_host, backend_port),
        name="haute-open-browser-after-servers-ready",
        daemon=True,
    ).start()


def _start_vite_subprocess(
    frontend_dir: Path,
    config: ServeConfig,
) -> subprocess.Popen[bytes]:
    """Launch ``npm run dev`` in *frontend_dir* and wire signals for cleanup.

    Registers ``SIGINT`` / ``SIGTERM`` handlers that terminate the
    Vite child before exiting, so a Ctrl-C on the parent doesn't
    orphan the dev server.
    """
    _node_env()
    env = os.environ.copy()
    ensure_local_session_token_env()
    env.pop("VITE_HAUTE_SESSION_TOKEN", None)
    env["HAUTE_BACKEND_URL"] = _http_url(config)

    vite_proc = subprocess.Popen(
        [_npm(), "run", "dev"],
        cwd=str(frontend_dir),
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
    )

    def _cleanup(signum: int, frame: object) -> None:
        vite_proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)
    return vite_proc


def _haute_package_dir() -> str:
    """Return the on-disk directory of the installed ``haute`` package.

    Used as uvicorn's ``reload_dirs`` so the autoreloader only watches
    server source files — not user pipeline file writes under the
    working directory, which would otherwise trigger constant reloads.
    """
    import haute as _haute_pkg

    return str(Path(_haute_pkg.__file__).resolve().parent)


def _run_dev_mode(config: ServeConfig, frontend_dir: Path) -> None:
    """Run Vite dev server + uvicorn with autoreload.

    The Vite process is terminated in the ``finally`` block so that
    any uvicorn exit path — clean shutdown, KeyboardInterrupt, crash —
    leaves no orphaned child.
    """
    import uvicorn

    click.echo("[dev] Dev mode: starting Vite dev server + FastAPI backend")
    click.echo(f"  Frontend -> http://{_VITE_HOST}:{_VITE_PORT}  (open this)")
    click.echo(f"  Backend  -> {_http_url(config)}   (API only)")
    click.echo("")

    vite_proc = _start_vite_subprocess(frontend_dir, config)
    if not config.no_browser:
        _open_browser_after_servers_ready(
            f"http://{_VITE_HOST}:{_VITE_PORT}",
            config.host,
            config.port,
        )
    try:
        uvicorn.run(
            "haute.server:app",
            host=config.host,
            port=config.port,
            reload=True,
            reload_dirs=[_haute_package_dir()],
            log_level="warning",
        )
    finally:
        vite_proc.terminate()


def _source_checkout_root() -> Path | None:
    """Return the repo root when haute is running from a source checkout.

    A source checkout (editable install, fresh git worktree) keeps the
    package at ``<root>/src/haute`` with ``frontend/package.json`` two
    levels up.  A wheel install lives in ``site-packages`` with no such
    sibling, so PyPI users are never told to build the frontend — their
    assets ship prebuilt inside the package.
    """
    import haute

    root = Path(haute.__file__).resolve().parent.parent.parent
    if (root / "frontend" / "package.json").is_file():
        return root
    return None


def _missing_static_message(static_dir: Path) -> str:
    """Build the fail-fast error for a missing/incomplete frontend build."""
    root = _source_checkout_root()
    if root is None:
        return (
            f"Error: no built frontend found at {static_dir}.\n"
            "The installed haute package is missing its bundled UI assets — "
            "try reinstalling haute."
        )
    return (
        f"Error: no built frontend found at {static_dir}.\n"
        "This is a haute source checkout (fresh worktree?) whose UI has not "
        "been built yet. Build it with:\n"
        "\n"
        f"    cd {root / 'frontend'} && npm install && npm run build\n"
        "\n"
        "or run the idempotent provisioning script:\n"
        "\n"
        f"    {root / 'scripts' / 'setup-worktree.sh'}\n"
        "\n"
        "(Once frontend/node_modules exists, 'haute serve' starts dev mode "
        "instead and serves the UI via Vite without a build.)"
    )


def _run_prod_mode(config: ServeConfig) -> None:
    """Serve the pre-built static frontend from uvicorn.

    Fails loudly when ``STATIC_DIR`` is missing or holds an incomplete
    build — the user needs to either build the frontend or install
    ``node_modules`` for dev mode, and a silent fallback (or the opaque
    ``RuntimeError`` uvicorn raises mounting an empty ``assets/``)
    would be worse than an actionable error.
    """
    import uvicorn

    from haute.server import STATIC_DIR, static_build_ready

    ensure_local_session_token_env()

    if not static_build_ready(STATIC_DIR):
        click.echo(_missing_static_message(STATIC_DIR), err=True)
        raise SystemExit(1)

    if not config.no_browser:
        _schedule_browser_open(_http_url(config), delay=1.5)
    uvicorn.run(
        "haute.server:app",
        host=config.host,
        port=config.port,
    )


def handle_serve(config: ServeConfig) -> None:
    """Start the Haute UI server.

    Picks dev mode when a ``frontend/`` directory with ``node_modules``
    exists; otherwise falls through to production mode (serving built
    static files).  Fails loudly when neither is available.

    ``handle_serve`` is the single local-only bind gate. Every code path
    that starts the server goes through this function.
    """
    _require_loopback_host(config)
    _configure_trusted_hosts(config)
    frontend_dir = _detect_dev_frontend_dir()
    if frontend_dir is not None:
        _abort_if_port_in_use(config)
        _abort_if_vite_port_in_use()
        _run_dev_mode(config, frontend_dir)
    else:
        _abort_if_port_in_use(config)
        _run_prod_mode(config)


@click.command()
@click.option(
    "--host",
    default=None,
    help=(
        "Host to bind to. Defaults to ``haute.toml``'s ``[server] host`` "
        "if set, otherwise localhost. Only loopback hosts are accepted."
    ),
)
@click.option("--port", default=8000, type=int, help="Backend API port.", show_default=True)
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically.")
def serve(host: str | None, port: int, no_browser: bool) -> None:
    """Start the Haute UI server.

    Host resolution precedence (first match wins):

    1. ``--host`` passed on the CLI.
    2. ``[server] host = "..."`` in ``haute.toml`` at the current
       working directory.
    3. ``localhost`` (loopback-only default).

    Whichever source wins, non-loopback values are rejected before startup.
    """
    if host is None:
        host = _load_toml_server_host(Path.cwd()) or "localhost"
    config = ServeConfig(host=host, port=port, no_browser=no_browser)
    handle_serve(config)
