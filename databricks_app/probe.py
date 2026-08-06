"""One-shot boot diagnostics for the Databricks Apps runtime.

Called from app.py before the server starts; prints a single JSON blob to
stdout so it lands in `databricks apps logs` as an [APP] line. Answers the
questions the docs don't: is git available, what does the container
filesystem look like, which env vars identify the runtime, what is
writable, and whether workspace/volume FUSE mounts exist.

Env var VALUES are only printed for a safe allowlist — the container also
carries the app service principal's OAuth client secret, which must never
be logged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SAFE_ENV_VALUES = (
    "DATABRICKS_APP_NAME",
    "DATABRICKS_APP_PORT",
    "DATABRICKS_HOST",
    "DATABRICKS_WORKSPACE_ID",
    "DATABRICKS_CLIENT_ID",
    "HOME",
    "PATH",
    "PWD",
    "PYTHONPATH",
)

_MOUNT_CANDIDATES = (
    "/",
    "/app",
    "/app/python",
    "/Workspace",
    "/Volumes",
    "/dbfs",
    "/databricks",
    "/tmp",
)


def _listing(path: str, limit: int = 20) -> list[str] | str:
    try:
        return sorted(os.listdir(path))[:limit]
    except OSError as exc:
        return f"<{type(exc).__name__}: {exc}>"


def _try_write(directory: Path) -> str:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / ".haute-probe"
        marker.write_text("probe")
        marker.unlink()
        return "writable"
    except OSError as exc:
        return f"<{type(exc).__name__}: {exc}>"


def run() -> None:
    info: dict[str, object] = {
        "python": sys.version,
        "executable": sys.executable,
        "cwd": str(Path.cwd()),
        "uid": os.getuid(),
        "gid": os.getgid(),
    }

    git = shutil.which("git")
    info["git_path"] = git
    if git:
        version = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=10)
        info["git_version"] = version.stdout.strip() or version.stderr.strip()

    info["env_keys"] = sorted(os.environ)
    info["env_values"] = {key: os.environ[key] for key in _SAFE_ENV_VALUES if key in os.environ}
    info["mounts"] = {path: _listing(path) for path in _MOUNT_CANDIDATES}
    info["write_checks"] = {
        "cwd/data": _try_write(Path.cwd() / "data"),
        "tmp": _try_write(Path("/tmp/haute-probe")),
        "home": _try_write(Path.home() / ".haute-probe-dir"),
    }
    usage = shutil.disk_usage(Path.cwd())
    info["disk_gb"] = {
        "total": round(usage.total / 1e9, 2),
        "free": round(usage.free / 1e9, 2),
    }

    print("HAUTE_DBX_PROBE " + json.dumps(info, indent=2), flush=True)
