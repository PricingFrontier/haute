"""First-boot project seeding for the Databricks Apps container.

The deployed source snapshot contains only the app bundle, so haute boots
into an empty project ("haute_toml_missing", bare topbar, no git). This
seeds the standard `haute init` scaffold (target=databricks) plus a git
repository with an initial commit so the UI is fully functional on first
visit.

The container filesystem is ephemeral per deployment: this runs on every
cold start and the seeded state (including git history) lasts only until
the next redeploy/restart. Durable projects need external storage — see
LEARNINGS.md.
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


def ensure_project() -> None:
    """Seed a haute project + git repo in cwd if none exists yet."""
    project_dir = Path.cwd()

    if not (project_dir / "haute.toml").exists():
        from haute.cli._init_cmd import InitConfig, handle_init

        handle_init(InitConfig(target="databricks", ci="github"))

    if shutil.which("git") is None:
        print("bootstrap: git binary unavailable; skipping repo seed", flush=True)
        return

    if not (project_dir / ".git").exists():
        _git("init", "-b", "main", cwd=project_dir)
        # Container-local identity: commits made through the haute UI need
        # one, and the app SP has no git identity of its own.
        _git("config", "user.name", _GIT_IDENTITY_NAME, cwd=project_dir)
        _git("config", "user.email", _GIT_IDENTITY_EMAIL, cwd=project_dir)
        _git("add", "-A", cwd=project_dir)
        _git(
            "commit",
            "-m",
            f"Seed haute project for app {os.environ.get('DATABRICKS_APP_NAME', 'local')}",
            cwd=project_dir,
        )
        print("bootstrap: seeded haute project and git repository", flush=True)
