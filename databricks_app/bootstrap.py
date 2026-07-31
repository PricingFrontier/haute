"""Project preparation for the Databricks Apps container.

Runs before the server starts and decides what the session's project is:

* **Bound** (a storage binding exists) — the project is restored from its
  git remote. A failure here gates the boot rather than seeding a fresh
  project over durable work.
* **Unbound** — a volatile project is seeded (`haute init` scaffold plus a
  git repository), exactly as before durable storage existed. Work is
  lost when the container is replaced until the user binds a remote.

The project lives in its own directory (``~/haute-project`` by default),
NOT in the deployed source snapshot: the snapshot is replaced wholesale
on every deploy, and mixing project files into it also put the app's own
bundle under haute's file watcher.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_GIT_IDENTITY_NAME = "Haute (Databricks App)"
_GIT_IDENTITY_EMAIL = "haute-app@noreply.databricksapps.com"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=60)


def _seed_volatile_project(project_dir: Path) -> None:
    """Create the standard scaffold + a local repository in *project_dir*."""
    from haute.cli._init_cmd import InitConfig, handle_init

    if not (project_dir / "haute.toml").exists():
        handle_init(InitConfig(target="databricks", ci="github"))

    if shutil.which("git") is None:
        print("bootstrap: git binary unavailable; skipping repo seed", flush=True)
        return

    if not (project_dir / ".git").exists():
        _git("init", "-b", "main", cwd=project_dir)
        # Container-local identity: commits made through the haute UI need
        # one, and the app service principal has no git identity of its own.
        _git("config", "user.name", _GIT_IDENTITY_NAME, cwd=project_dir)
        _git("config", "user.email", _GIT_IDENTITY_EMAIL, cwd=project_dir)
        _git("add", "-A", cwd=project_dir)
        _git(
            "commit",
            "-m",
            f"Seed haute project for app {os.environ.get('DATABRICKS_APP_NAME', 'local')}",
            cwd=project_dir,
        )
        print("bootstrap: seeded volatile haute project and git repository", flush=True)


def ensure_project() -> Path:
    """Prepare the session's project directory and chdir into it.

    Returns the project directory. Raises when a bound project cannot be
    restored — the caller must not fall back to a fresh project, which
    would hide durable work behind an empty canvas.
    """
    from haute import _project_storage

    project_dir = _project_storage.resolve_project_dir()

    # Credentials must exist before any remote git command runs.
    _project_storage.configure_git_credentials(Path.home() / ".haute-runtime")

    outcome = _project_storage.restore_if_bound(project_dir)
    if outcome == "unbound":
        project_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(project_dir)
        _seed_volatile_project(project_dir)
    else:
        os.chdir(project_dir)
        print(f"bootstrap: project {outcome} from durable storage", flush=True)

    return project_dir
