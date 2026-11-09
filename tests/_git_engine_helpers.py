"""Shared temp repositories and plumbing helpers for the git engine suites.

The ``repo`` fixture, the working and ledger branch names and the helpers that read the commit
graph through plumbing are used by several ``test_git_*`` modules that test the working/ledger
branch-pair model, so each imports them from this one place.
"""

import subprocess
from pathlib import Path

import pytest

from haute._git import (
    commit_save,
)

WORKING = "pricing-dev"


LEDGER = "pricing-dev-save"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _parents(repo: Path, ref: str) -> list[str]:
    out = _git(repo, "rev-list", "--parents", "-n", "1", ref)
    return out.split()[1:]


def _tree(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", f"{ref}^{{tree}}")


def _write_and_save(
    repo: Path, working: str, files: dict[str, str], message: str | None = None
) -> str | None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return commit_save(list(files), working, cwd=repo, message=message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "pipeline_repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# pipeline\n")
    _git(root, "add", "rating.py")
    _git(root, "commit", "-m", "initial pipeline")
    _git(root, "checkout", "-b", WORKING)
    return root


@pytest.fixture
def unborn_repo(tmp_path: Path) -> Path:
    """A fresh git repo with no commits (unborn HEAD on main) but identity configured."""
    root = tmp_path / "unborn_repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    return root
