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
    import uvicorn

    from haute.server import STATIC_DIR

    # Emit the non-loopback exposure warning before doing anything
    # else. Firing it early means the operator sees the warning even
    # if the port probe or frontend lookup below bails out. The
    # contract with callers / tests is "any non-loopback host
    # triggers a structured ``warning``-level event whose payload
    # mentions 'exposing beyond localhost'"; the event name and
    # precise field layout are implementation details.
    if not _is_loopback_host(config.host):
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

    # Pre-flight: fail loudly if the port is already taken rather than
    # letting uvicorn crash with a cryptic OSError.
    if not _port_is_available(config.host, config.port):
        click.echo(
            f"Error: port {config.port} already in use. Use --port to choose another.",
            err=True,
        )
        raise SystemExit(1)

    # Dev mode requires a frontend/ directory with node_modules already
    # installed. When no frontend/ is checked out (e.g. running from a
    # wheel install), _find_frontend_dir raises — we catch that here and
    # fall through to production mode (serve built static files).
    try:
        frontend_dir: Path | None = _find_frontend_dir()
    except FileNotFoundError:
        frontend_dir = None
    dev_mode = frontend_dir is not None and (frontend_dir / "node_modules").exists()

    if dev_mode:
        assert frontend_dir is not None  # narrowed by dev_mode guard
        click.echo("[dev] Dev mode: starting Vite dev server + FastAPI backend")
        click.echo("  Frontend -> http://localhost:5173  (open this)")
        click.echo(f"  Backend  -> http://{config.host}:{config.port}   (API only)")
        click.echo("")
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

        if not config.no_browser:
            import threading

            threading.Timer(2.0, _open_browser, args=("http://localhost:5173",)).start()

        # Resolve the haute package directory so uvicorn only reloads on
        # server source changes, not on user pipeline file writes.
        import haute as _haute_pkg

        _haute_src_dir = str(Path(_haute_pkg.__file__).resolve().parent)

        try:
            uvicorn.run(
                "haute.server:app",
                host=config.host,
                port=config.port,
                reload=True,
                reload_dirs=[_haute_src_dir],
                log_level="warning",
            )
        finally:
            vite_proc.terminate()
    else:
        if not STATIC_DIR.exists():
            click.echo(
                "Error: No built frontend found. "
                "Run 'npm run build' in frontend/ first, or "
                "install node_modules for dev mode.",
                err=True,
            )
            raise SystemExit(1)

        if not config.no_browser:
            import threading

            threading.Timer(
                1.5,
                _open_browser,
                args=(f"http://{config.host}:{config.port}",),
            ).start()

        uvicorn.run(
            "haute.server:app",
            host=config.host,
            port=config.port,
        )


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
