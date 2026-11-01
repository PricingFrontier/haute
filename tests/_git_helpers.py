"""Shared git helpers for test_git.py and test_git_routes.py."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_run(cwd: Path, *args: str, check: bool = True) -> str:
    """Run a raw git command for test setup and return its stripped stdout.

    A failure raises at the call with git's stderr, so a broken ``init`` or
    ``commit`` in fixture setup is not first seen as an unrelated assertion.
    ``check=False`` is for a query whose non-zero exit is its answer, such as
    ``rev-parse --verify`` of a missing ref.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd} (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def init_repo(path: Path, *, user: str = "Test User") -> Path:
    """Create a fresh git repo with an initial commit on 'main'."""
    git_run(path, "init", "-b", "main")
    git_run(path, "config", "user.name", user)
    git_run(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("# Test\n")
    git_run(path, "add", ".")
    git_run(path, "commit", "-m", "Initial commit")
    return path


# A pre-commit hook in the shape of the one `haute init` scaffolds: it rewrites each staged
# Python file on disk and stages it again, so the commit holds the hook's bytes.
REWRITING_PRE_COMMIT_HOOK = """#!/bin/sh
for f in $(git diff --cached --name-only --diff-filter=ACM -- '*.py'); do
  printf '# formatted\\n' >> "$f"
  git add "$f"
done
"""


def install_hook(repository: Path, name: str, script: str) -> Path:
    """Install *script* as the repository's *name* hook (``pre-commit``, ``commit-msg``)."""
    hook = repository / ".git" / "hooks" / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(script, encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    return hook
