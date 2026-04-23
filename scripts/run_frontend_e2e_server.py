"""Start a deterministic backend + Vite pair for Playwright browser tests."""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path

import polars as pl
import uvicorn

from haute.cli._helpers import _node_env, _npm
from haute.cli._init_cmd import InitConfig, handle_init

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
E2E_PROJECT_DIR = REPO_ROOT / ".tmp-e2e-project"


def _assert_under_repo(path: Path) -> None:
    repo_root = REPO_ROOT.resolve()
    resolved = path.resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise RuntimeError(f"Refusing to operate outside repo root: {resolved}")


def _reset_project_dir() -> None:
    _assert_under_repo(E2E_PROJECT_DIR)
    if E2E_PROJECT_DIR.exists():
        def _retry_remove_readonly(func: object, path: str, _exc_info: object) -> None:
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(E2E_PROJECT_DIR, onerror=_retry_remove_readonly)
    E2E_PROJECT_DIR.mkdir(parents=True)


def _scaffold_e2e_project() -> None:
    _reset_project_dir()
    os.chdir(E2E_PROJECT_DIR)
    handle_init(InitConfig(target="databricks", ci="none", force=True))

    sample = pl.DataFrame(
        {
            "id": [1, 2, 3],
            "value": [11, 23, 37],
        }
    )
    data_dir = E2E_PROJECT_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    sample.write_parquet(data_dir / "sample.parquet")


def _run_git(*args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(E2E_PROJECT_DIR),
        check=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _init_git_repo() -> None:
    _run_git("init")
    _run_git("branch", "-M", "main")
    _run_git("config", "user.name", "Haute E2E")
    _run_git("config", "user.email", "haute-e2e@example.com")
    _run_git("add", "-A")
    _run_git("commit", "-m", "Initial scaffold")


def _start_vite() -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    node_env = _node_env()
    if node_env is not None:
        env.update(node_env)
    return subprocess.Popen(
        [
            _npm(),
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
            "--strictPort",
        ],
        cwd=str(FRONTEND_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
    )


def main() -> None:
    _scaffold_e2e_project()
    _init_git_repo()
    vite_proc = _start_vite()

    def _cleanup(*_: object) -> None:
        vite_proc.terminate()
        try:
            vite_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vite_proc.kill()

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    print("[e2e] Frontend -> http://127.0.0.1:5173")
    print("[e2e] Backend  -> http://127.0.0.1:8000")
    sys.stdout.flush()

    try:
        uvicorn.run(
            "haute.server:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
            log_level="warning",
        )
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
