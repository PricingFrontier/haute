"""``haute serve`` command."""

import signal
import socket
import subprocess
import sys
from pathlib import Path

import click

from haute.cli._helpers import _find_frontend_dir, _node_env, _npm, _open_browser


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


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--port", default=8000, type=int, help="Backend API port.")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically.")
def serve(host: str, port: int, no_browser: bool) -> None:
    """Start the Haute UI server."""
    import uvicorn

    from haute.server import STATIC_DIR

    # Pre-flight: fail loudly if the port is already taken rather than
    # letting uvicorn crash with a cryptic OSError.
    if not _port_is_available(host, port):
        click.echo(
            f"Error: port {port} already in use. Use --port to choose another.",
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
        click.echo(f"  Backend  -> http://{host}:{port}   (API only)")
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

        if not no_browser:
            import threading

            threading.Timer(2.0, _open_browser, args=("http://localhost:5173",)).start()

        # Resolve the haute package directory so uvicorn only reloads on
        # server source changes, not on user pipeline file writes.
        import haute as _haute_pkg

        _haute_src_dir = str(Path(_haute_pkg.__file__).resolve().parent)

        try:
            uvicorn.run(
                "haute.server:app",
                host=host,
                port=port,
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

        if not no_browser:
            import threading

            threading.Timer(1.5, _open_browser, args=(f"http://{host}:{port}",)).start()

        uvicorn.run(
            "haute.server:app",
            host=host,
            port=port,
        )
