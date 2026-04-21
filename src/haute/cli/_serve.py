"""``haute serve`` command.

Split into:

* :class:`ServeConfig` — the typed bag of CLI inputs.
* :func:`handle_serve` — the pure function that does the work.
* :func:`serve` — the thin ``@click.command`` entry point.

Host-binding safety (Wave 10A #118)
-----------------------------------
Haute is a dev-only tool with no authentication.  The default bind has
to be loopback-only (``127.0.0.1``) so a user running ``haute serve``
on a corporate LAN does not accidentally expose an unauthenticated
Polars execution endpoint and file browser to every peer on the
network.  Any explicit non-loopback bind (``0.0.0.0``, a public IP, a
hostname that resolves off-loopback, …) is honoured but logs a loud
structured warning via structlog so the choice is auditable in server
logs.  The same policy applies whether the host was supplied on the
CLI (``--host ...``) or via ``[server] host = "..."`` in
``haute.toml``.
"""

from __future__ import annotations

import ipaddress
import signal
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import click

from haute._logging import get_logger
from haute.cli._helpers import _find_frontend_dir, _node_env, _npm, _open_browser

logger = get_logger(component="serve")


# ``127.0.0.1`` is the canonical IPv4 loopback; ``::1`` is the IPv6
# loopback; ``localhost`` is the DNS name conventionally resolved to
# one of those.  Any of the three are treated as loopback-safe.  Every
# other host — including the wildcard ``0.0.0.0`` and ``::`` — is
# non-loopback and therefore triggers the exposure warning.
_LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost"})


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
    subject to the exposure warning.
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
        # address, so warn.
        return False


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
    """Parsed inputs for the ``haute serve`` command.

    ``host`` defaults to ``127.0.0.1`` so every non-CLI caller
    (programmatic tests, future alternative frontends) inherits the
    same safe default as the Click wrapper — there is no path through
    which an uninitialised ``ServeConfig`` binds to the wider network.
    """

    port: int
    no_browser: bool
    host: str = "127.0.0.1"


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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
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


def _warn_if_non_loopback(config: ServeConfig) -> None:
    """Emit the structured exposure warning when binding off-loopback.

    Fires before port probing and frontend lookup so the warning
    reaches the operator even if a later step bails out. The contract
    with callers / tests is "any non-loopback host triggers a
    structured ``warning``-level event whose payload mentions
    'exposing beyond localhost'"; the event name and field layout are
    implementation details.
    """
    if _is_loopback_host(config.host):
        return
    logger.warning(
        "server_bind_non_loopback",
        host=config.host,
        port=config.port,
        hint=(
            "Binding to a non-loopback host is exposing beyond "
            "localhost — every peer that can reach this machine "
            "on the network can now hit the unauthenticated "
            "Haute API. Pass --host 127.0.0.1 to revert to the "
            "safe default."
        ),
    )


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


def _detect_dev_frontend_dir() -> Path | None:
    """Return the ``frontend/`` dir iff dev mode is viable, else ``None``.

    Dev mode requires both a discoverable ``frontend/`` directory and
    its ``node_modules`` to be installed. When ``_find_frontend_dir``
    raises (e.g. running from a wheel install with no checkout), we
    swallow the ``FileNotFoundError`` and signal "fall through to prod
    mode" by returning ``None``.
    """
    try:
        frontend_dir = _find_frontend_dir()
    except FileNotFoundError:
        return None
    if not (frontend_dir / "node_modules").exists():
        return None
    return frontend_dir


def _schedule_browser_open(url: str, delay: float) -> None:
    """Open *url* in the default browser after *delay* seconds.

    Deferred so the server has time to start accepting connections
    before the browser races to fetch the page.
    """
    import threading

    threading.Timer(delay, _open_browser, args=(url,)).start()


def _start_vite_subprocess(frontend_dir: Path) -> subprocess.Popen[bytes]:
    """Launch ``npm run dev`` in *frontend_dir* and wire signals for cleanup.

    Registers ``SIGINT`` / ``SIGTERM`` handlers that terminate the
    Vite child before exiting, so a Ctrl-C on the parent doesn't
    orphan the dev server.
    """
    vite_proc = subprocess.Popen(
        [_npm(), "run", "dev"],
        cwd=str(frontend_dir),
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=_node_env(),
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
    click.echo("  Frontend -> http://localhost:5173  (open this)")
    click.echo(f"  Backend  -> http://{config.host}:{config.port}   (API only)")
    click.echo("")

    vite_proc = _start_vite_subprocess(frontend_dir)
    if not config.no_browser:
        _schedule_browser_open("http://localhost:5173", delay=2.0)
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


def _run_prod_mode(config: ServeConfig) -> None:
    """Serve the pre-built static frontend from uvicorn.

    Fails loudly when no ``STATIC_DIR`` is present — the user needs to
    either build the frontend or install ``node_modules`` for dev
    mode, and a silent fallback would be worse than an actionable
    error.
    """
    import uvicorn

    from haute.server import STATIC_DIR

    if not STATIC_DIR.exists():
        click.echo(
            "Error: No built frontend found. "
            "Run 'npm run build' in frontend/ first, or "
            "install node_modules for dev mode.",
            err=True,
        )
        raise SystemExit(1)

    if not config.no_browser:
        _schedule_browser_open(f"http://{config.host}:{config.port}", delay=1.5)
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

    ``handle_serve`` is the single point where the host-exposure
    warning fires (see the module docstring). Every code path that
    starts the server — the Click wrapper, programmatic callers in
    tests, future alternative frontends — goes through this function,
    so the warning cannot be bypassed by skipping the CLI layer.
    """
    _warn_if_non_loopback(config)
    _abort_if_port_in_use(config)
    frontend_dir = _detect_dev_frontend_dir()
    if frontend_dir is not None:
        _run_dev_mode(config, frontend_dir)
    else:
        _run_prod_mode(config)


@click.command()
@click.option(
    "--host",
    default=None,
    help=(
        "Host to bind to. Defaults to ``haute.toml``'s ``[server] host`` "
        "if set, otherwise 127.0.0.1 (loopback-only). Non-loopback hosts "
        "trigger a structured warning."
    ),
)
@click.option("--port", default=8000, type=int, help="Backend API port.")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically.")
def serve(host: str | None, port: int, no_browser: bool) -> None:
    """Start the Haute UI server.

    Host resolution precedence (first match wins):

    1. ``--host`` passed on the CLI.
    2. ``[server] host = "..."`` in ``haute.toml`` at the current
       working directory.
    3. ``127.0.0.1`` (loopback-only default).

    Whichever source wins, any non-loopback value triggers a
    structured warning so the exposure is auditable in server logs.
    """
    if host is None:
        host = _load_toml_server_host(Path.cwd()) or "127.0.0.1"
    config = ServeConfig(host=host, port=port, no_browser=no_browser)
    handle_serve(config)
