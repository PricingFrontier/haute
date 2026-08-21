"""Fresh-install smoke: wheel -> fresh venv -> ``haute init`` -> serve -> endpoint.

Proves the end-user install path end to end, on the OS it runs on:

1. Build the wheel with ``HAUTE_BUILD_FRONTEND=1`` (never editable — editable
   installs return early from ``hatch_build.py``'s frontend hook, so only a
   real wheel build exercises static-asset packaging).
2. Install the wheel into a fresh venv with fresh dependency resolution
   (``uv pip install <wheel>`` resolves against the published floor/cap
   specifiers, not the repo lockfile — exactly what an end user gets).
3. Verify the installed wheel's ``python -m haute`` package entry point and
   its generated ``haute`` console entry point.
4. ``haute init`` in an empty scratch directory outside the repo.
5. ``haute serve`` headless from the scratch project root; the server must
   fall back to the packaged static frontend (no dev frontend present).
6. Bootstrap the canonical local-session cookie, drive the file-listing
   endpoint (AGENTS ``/api/files`` recipe), and confirm auth rejects
   pre-bootstrap calls.
7. Terminate the server and require a clean exit.

Stdlib-only; shells out to ``uv`` (and transitively ``npm`` for the frontend
build). Run directly (``python scripts/init_smoke.py``) or via
``scripts/preflight.sh --init-smoke`` / ``scripts/preflight.ps1 -InitSmoke``.
"""

from __future__ import annotations

import argparse
import http.cookiejar
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

# Per-step budgets; their sum must stay under the CI job's timeout-minutes so
# a hang dies here (with the server-log dump) rather than in a runner cancel.
BUILD_TIMEOUT_SECONDS = 12 * 60
INSTALL_TIMEOUT_SECONDS = 8 * 60
INIT_TIMEOUT_SECONDS = 2 * 60
SERVER_READY_TIMEOUT_SECONDS = 90
SERVER_SHUTDOWN_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
SHUTDOWN_MARKER_TIMEOUT_SECONDS = 10

# Loopback requests must never route via a proxy: urllib's default opener
# honours HTTP(S)_PROXY env vars and, on macOS, the system proxy config —
# either would break the smoke (and leak the session token) on proxied hosts.
_COOKIE_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(_COOKIE_JAR),
)

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


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(  # noqa: S310 (loopback only)
        url,
        headers=headers or {},
        method=method,
    )
    try:
        with _OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    return _http_request(url, headers=headers)


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
    _run_step(
        "python -m haute entry point",
        [str(venv_python), "-m", "haute", "--version"],
        cwd=REPO_ROOT,
        timeout=INIT_TIMEOUT_SECONDS,
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


def _check_endpoints(base_url: str) -> None:
    status, body = _http_get(base_url + "/")
    if status != 200 or b"<!doctype html" not in body.lower():
        raise SmokeError(f"GET / did not serve the packaged index.html (HTTP {status})")
    _log("packaged frontend served at /")

    files_url = base_url + "/api/files?dir=data&extensions=.json"
    status, _ = _http_get(files_url)
    if status not in (401, 403):
        raise SmokeError(f"tokenless /api/files should be rejected, got HTTP {status}")
    _log(f"session auth active (tokenless request rejected with {status})")

    status, body = _http_request(
        base_url + "/api/session/bootstrap",
        method="POST",
        headers={"Origin": base_url},
    )
    if status != 200:
        raise SmokeError(f"session bootstrap failed: HTTP {status}: {body[:200]!r}")
    _log("local session cookie bootstrapped")

    status, body = _http_get(files_url)
    if status != 200:
        raise SmokeError(f"authed /api/files failed: HTTP {status}: {body[:200]!r}")
    names = [item.get("name") for item in json.loads(body).get("items", [])]
    if "init_smoke_probe.json" not in names:
        raise SmokeError(f"probe file missing from /api/files listing: {names}")
    _log("file-listing endpoint sees the probe file (config-to-data wiring ok)")


def _start_server(haute_exe: Path, project_dir: Path, port: int, token: str, log_path: Path):
    env = os.environ.copy()
    env["HAUTE_LOCAL_SESSION_TOKEN"] = token
    # Keep the smoke hermetic against ambient shell state: an inherited
    # auth-disable (a documented local perf workflow) would flip the
    # tokenless-rejection assertion into a confusing failure.
    env.pop("HAUTE_DISABLE_LOCAL_SESSION_AUTH", None)
    env.pop("HAUTE_TRUSTED_HOSTS", None)
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


def _kill_server_tree(server: subprocess.Popen[bytes]) -> None:
    """Hard-kill the server and its children.

    On Windows the ``haute`` entry point is a launcher exe whose python
    child would survive a plain ``Popen.kill()`` (TerminateProcess does not
    cascade), leaving a live server holding the port and the scratch dir —
    ``taskkill /T`` fells the whole tree. POSIX needs only the process.
    """
    if server.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(server.pid), "/T", "/F"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    else:
        server.kill()
    server.wait(timeout=10)


def _wait_for_shutdown_marker(log_path: Path) -> bool:
    """Poll for the graceful-shutdown marker.

    On Windows the launcher exe may report its exit before the python child
    finishes flushing the log, so a single immediate read would race the
    marker write.
    """
    deadline = time.monotonic() + SHUTDOWN_MARKER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _SHUTDOWN_MARKER in log_path.read_bytes():
            return True
        time.sleep(0.2)
    return False


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
        _kill_server_tree(server)
        raise SmokeError(
            f"server did not shut down within {SERVER_SHUTDOWN_TIMEOUT_SECONDS}s of the signal"
        ) from error
    if IS_WINDOWS:
        clean_code = returncode in _WINDOWS_CLEAN_EXIT_CODES
    else:
        clean_code = returncode in (0, -signal.SIGTERM)
    if not clean_code:
        raise SmokeError(f"server shutdown was not clean (exit code {returncode})")
    if not _wait_for_shutdown_marker(log_path):
        raise SmokeError("server log has no graceful-shutdown marker; shutdown was not orderly")
    _log(f"server shut down cleanly (exit code {returncode}, graceful-shutdown marker present)")


def _cleanup_scratch(scratch: Path) -> None:
    """Remove the scratch tree, retrying once for slow file-handle release.

    On Windows, antivirus/indexer holds on freshly-written venv files can
    defeat the first rmtree; a leaked scratch dir is a full venv, so warn
    rather than fail silently when even the retry loses.
    """
    shutil.rmtree(scratch, ignore_errors=True)
    if scratch.exists():
        time.sleep(2)
        shutil.rmtree(scratch, ignore_errors=True)
    if scratch.exists():
        _log(f"WARNING: could not fully remove scratch dir {scratch}")


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

    # The scratch project must live outside any checkout: `haute serve` walks
    # UP from cwd looking for a dev frontend/, and a TMPDIR nested inside a
    # repo would silently flip the smoke into Vite dev mode.
    #
    # `.resolve()` canonicalises to the long form, matching how a real user's
    # project path looks. On Windows `%TEMP%` is often an 8.3 short path
    # (`C:\Users\RUNNER~1\...`); serving from that unresolved short form is not
    # the realistic install scenario and, separately, trips a short-vs-long
    # path-normalisation bug in the file browser (tracked for its own fix).
    scratch = Path(tempfile.mkdtemp(prefix="haute-init-smoke-")).resolve()
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
            _check_endpoints(base_url)
            _stop_server(server, log_path)
        except Exception:
            _kill_server_tree(server)
            raise
        finally:
            log_file.close()
    except Exception as error:
        # Broad on purpose: whatever the failure class, the verdict is FAIL
        # and the server-log tail is the diagnostic that matters.
        _log(f"FAIL: {error!r}")
        _dump_server_log(log_path)
        if args.keep_scratch:
            _log(f"scratch kept at {scratch}")
        return 1
    finally:
        if not args.keep_scratch:
            _cleanup_scratch(scratch)

    _log(f"fresh-install smoke ok ({time.monotonic() - started:.1f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
