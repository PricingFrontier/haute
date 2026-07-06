"""Fresh-install smoke: wheel -> fresh venv -> ``haute init`` -> serve -> endpoint.

Proves the end-user install path end to end, on the OS it runs on:

1. Build the wheel with ``HAUTE_BUILD_FRONTEND=1`` (never editable — editable
   installs return early from ``hatch_build.py``'s frontend hook, so only a
   real wheel build exercises static-asset packaging).
2. Install the wheel into a fresh venv with fresh dependency resolution
   (``uv pip install <wheel>`` resolves against the published floor/cap
   specifiers, not the repo lockfile — exactly what an end user gets).
3. ``haute init`` in an empty scratch directory outside the repo.
4. ``haute serve`` headless from the scratch project root; the server must
   fall back to the packaged static frontend (no dev frontend present).
5. Drive the canonical file-listing endpoint (AGENTS ``/api/files`` recipe)
   with the local session token, and confirm auth rejects tokenless calls.
6. Terminate the server and require a clean exit.

Stdlib-only; shells out to ``uv`` (and transitively ``npm`` for the frontend
build). Run directly (``python scripts/init_smoke.py``) or via
``scripts/preflight.sh --init-smoke`` / ``scripts/preflight.ps1 -InitSmoke``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = sys.platform == "win32"

BUILD_TIMEOUT_SECONDS = 15 * 60
INSTALL_TIMEOUT_SECONDS = 10 * 60
INIT_TIMEOUT_SECONDS = 2 * 60
SERVER_READY_TIMEOUT_SECONDS = 90
SERVER_SHUTDOWN_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10

# Uvicorn shuts down gracefully on the signal, then re-raises it so the
# process reports termination-by-signal (unix convention). A clean stop is
# therefore: the graceful-shutdown marker in the server log, plus an exit
# code that is either 0 or the signal we sent. On Windows the console
# signal surfaces as 0, the CRT's SIGBREAK default (3), or
# STATUS_CONTROL_C_EXIT.
_WINDOWS_CLEAN_EXIT_CODES = frozenset({0, 3, 0xC000013A, 0xC000013A - (1 << 32)})
_SHUTDOWN_MARKER = b"Application shutdown complete"


class SmokeError(RuntimeError):
    """A smoke step failed; the message says which and why."""


def _log(message: str) -> None:
    print(f"[init-smoke] {message}", flush=True)


def _run_step(
    name: str,
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> None:
    _log(f"{name}: {' '.join(cmd)}")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    started = time.monotonic()
    result = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, check=False)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise SmokeError(f"{name} failed with exit code {result.returncode}")
    _log(f"{name}: ok ({elapsed:.1f}s)")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _venv_bin(venv_dir: Path, name: str) -> Path:
    if IS_WINDOWS:
        exe = venv_dir / "Scripts" / f"{name}.exe"
        if exe.exists():
            return exe
        return venv_dir / "Scripts" / name
    return venv_dir / "bin" / name


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers or {})  # noqa: S310 (loopback only)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _build_wheel(dist_dir: Path) -> Path:
    _run_step(
        "wheel build",
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=REPO_ROOT,
        timeout=BUILD_TIMEOUT_SECONDS,
        extra_env={"HAUTE_BUILD_FRONTEND": "1"},
    )
    wheels = sorted(dist_dir.glob("haute-*.whl"))
    if len(wheels) != 1:
        raise SmokeError(f"Expected exactly one wheel in {dist_dir}, found {wheels}")
    return wheels[0]


def _install_into_fresh_venv(wheel: Path, venv_dir: Path) -> Path:
    _run_step(
        "fresh venv",
        ["uv", "venv", str(venv_dir)],
        cwd=REPO_ROOT,
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    venv_python = _venv_bin(venv_dir, "python")
    _run_step(
        "wheel install (fresh dependency resolution)",
        ["uv", "pip", "install", "--python", str(venv_python), str(wheel)],
        cwd=REPO_ROOT,
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    haute_exe = _venv_bin(venv_dir, "haute")
    if not haute_exe.exists():
        raise SmokeError(f"haute entry point missing after install: {haute_exe}")
    return haute_exe


def _scaffold_project(haute_exe: Path, project_dir: Path) -> None:
    _run_step(
        "haute init",
        [str(haute_exe), "init"],
        cwd=project_dir,
        timeout=INIT_TIMEOUT_SECONDS,
    )
    for expected in ("haute.toml", "data", "rating/main.py"):
        if not (project_dir / expected).exists():
            raise SmokeError(f"haute init did not scaffold {expected}")
    probe = project_dir / "data" / "init_smoke_probe.json"
    probe.write_text('{"probe": true}\n', encoding="utf-8")


def _wait_until_ready(base_url: str, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
    last_error = "no response"
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SmokeError(f"server exited early with code {server.returncode}")
        try:
            status, _ = _http_get(base_url + "/")
        except OSError as error:
            last_error = str(error)
        else:
            if status == 200:
                return
            last_error = f"HTTP {status}"
        time.sleep(0.5)
    raise SmokeError(f"server not ready after {SERVER_READY_TIMEOUT_SECONDS}s ({last_error})")


def _check_endpoints(base_url: str, token: str) -> None:
    status, body = _http_get(base_url + "/")
    if status != 200 or b"<!doctype html" not in body.lower():
        raise SmokeError(f"GET / did not serve the packaged index.html (HTTP {status})")
    _log("packaged frontend served at /")

    files_url = base_url + "/api/files?dir=data&extensions=.json"
    status, _ = _http_get(files_url)
    if status not in (401, 403):
        raise SmokeError(f"tokenless /api/files should be rejected, got HTTP {status}")
    _log(f"session auth active (tokenless request rejected with {status})")

    status, body = _http_get(files_url, headers={"x-haute-session-token": token})
    if status != 200:
        raise SmokeError(f"authed /api/files failed: HTTP {status}: {body[:200]!r}")
    names = [item.get("name") for item in json.loads(body).get("items", [])]
    if "init_smoke_probe.json" not in names:
        raise SmokeError(f"probe file missing from /api/files listing: {names}")
    _log("file-listing endpoint sees the probe file (config-to-data wiring ok)")


def _start_server(haute_exe: Path, project_dir: Path, port: int, token: str, log_path: Path):
    env = os.environ.copy()
    env["HAUTE_LOCAL_SESSION_TOKEN"] = token
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    log_file = log_path.open("wb")
    server = subprocess.Popen(
        [str(haute_exe), "serve", "--no-browser", "--port", str(port)],
        cwd=project_dir,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    return server, log_file


def _stop_server(server: subprocess.Popen[bytes], log_path: Path) -> None:
    if server.poll() is not None:
        raise SmokeError(f"server died before shutdown (exit code {server.returncode})")
    if IS_WINDOWS:
        server.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        server.send_signal(signal.SIGTERM)
    try:
        returncode = server.wait(timeout=SERVER_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        server.kill()
        server.wait(timeout=10)
        raise SmokeError(
            f"server did not shut down within {SERVER_SHUTDOWN_TIMEOUT_SECONDS}s of the signal"
        ) from error
    if IS_WINDOWS:
        clean_code = returncode in _WINDOWS_CLEAN_EXIT_CODES
    else:
        clean_code = returncode in (0, -signal.SIGTERM)
    if not clean_code:
        raise SmokeError(f"server shutdown was not clean (exit code {returncode})")
    if _SHUTDOWN_MARKER not in log_path.read_bytes():
        raise SmokeError("server log has no graceful-shutdown marker; shutdown was not orderly")
    _log(f"server shut down cleanly (exit code {returncode}, graceful-shutdown marker present)")


def _dump_server_log(log_path: Path) -> None:
    if not log_path.exists():
        return
    tail = log_path.read_bytes()[-4000:]
    if tail:
        _log("server log tail:")
        print(tail.decode("utf-8", errors="replace"), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Reuse an already-built wheel instead of building one.",
    )
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help="Keep the scratch directory for post-mortem inspection.",
    )
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="haute-init-smoke-"))
    _log(f"scratch directory: {scratch}")
    project_dir = scratch / "project"
    project_dir.mkdir()
    log_path = scratch / "serve.log"
    started = time.monotonic()

    try:
        wheel = args.wheel.resolve() if args.wheel else _build_wheel(scratch / "dist")
        _log(f"wheel: {wheel.name}")
        haute_exe = _install_into_fresh_venv(wheel, scratch / "venv")
        _scaffold_project(haute_exe, project_dir)

        port = _free_port()
        token = secrets.token_urlsafe(24)
        base_url = f"http://127.0.0.1:{port}"
        _log(f"starting headless server on {base_url}")
        server, log_file = _start_server(haute_exe, project_dir, port, token, log_path)
        try:
            _wait_until_ready(base_url, server)
            _check_endpoints(base_url, token)
            _stop_server(server, log_path)
        except Exception:
            if server.poll() is None:
                server.kill()
                server.wait(timeout=10)
            raise
        finally:
            log_file.close()
    except (SmokeError, subprocess.TimeoutExpired) as error:
        _log(f"FAIL: {error}")
        _dump_server_log(log_path)
        if args.keep_scratch:
            _log(f"scratch kept at {scratch}")
        return 1
    finally:
        if not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    _log(f"fresh-install smoke ok ({time.monotonic() - started:.1f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
