"""Per-clone haute state — the working-branch association.

Lives at ``<project_root>/.haute/state.json``, deliberately untracked: the
file answers "which working branch does THIS clone serve", which is exactly
the thing HEAD cannot answer once HEAD lives on the save ledger. Everything
reads and writes through this module so a future relocation is a one-file
change.
"""

from __future__ import annotations

import json
from pathlib import Path

from haute._logging import get_logger

logger = get_logger(component="git_state")

_STATE_DIR = ".haute"
_STATE_FILE = "state.json"
_PREFS_FILE = "prefs.json"
_FORKS_FILE = "forks.json"
_WORKING_BRANCH_KEY = "workingBranch"


def _state_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _STATE_FILE


def _prefs_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _PREFS_FILE


def _forks_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _FORKS_FILE


def read_working_branch(project_root: Path) -> str | None:
    """The clone's recorded working branch, or None when unset.

    Unreadable or malformed state is treated as unset (the startup flow
    re-prompts) rather than an error — the file is reconstructable user
    preference, not data.
    """
    path = _state_path(project_root)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.warning("git_state_unreadable", path=str(path))
        return None
    branch = raw.get(_WORKING_BRANCH_KEY) if isinstance(raw, dict) else None
    if isinstance(branch, str) and branch.strip():
        return branch
    return None


def write_working_branch(project_root: Path, branch: str) -> None:
    """Record the clone's working branch (creates ``.haute/`` if needed)."""
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({_WORKING_BRANCH_KEY: branch}, indent=2) + "\n")
    logger.info("git_state_written", branch=branch)


def clear_working_branch(project_root: Path) -> None:
    """Forget the clone's working branch (e.g. after archiving/deleting it).

    Leaves the clone in the 'unset' state — the next save re-prompts the
    working-branch chooser (S5/S13). A no-op when no state file exists.
    """
    path = _state_path(project_root)
    if path.exists():
        path.unlink()
        logger.info("git_state_cleared")


# ---------------------------------------------------------------------------
# Local preferences — per-clone UI settings (e.g. "don't ask again" toggles).
# Kept in a sibling, also-untracked file so working-branch churn never disturbs
# them and vice versa. Reconstructable preference, so unreadable == defaults.
# ---------------------------------------------------------------------------


def read_prefs(project_root: Path) -> dict[str, object]:
    """All local preferences for this clone (empty dict when none/malformed)."""
    path = _prefs_path(project_root)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning("git_prefs_unreadable", path=str(path))
        return {}
    return raw if isinstance(raw, dict) else {}


def write_pref(project_root: Path, key: str, value: object) -> None:
    """Set one preference, preserving the others (creates ``.haute/`` if needed)."""
    prefs = read_prefs(project_root)
    prefs[key] = value
    path = _prefs_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs, indent=2) + "\n")
    logger.info("git_pref_written", key=key)


# ---------------------------------------------------------------------------
# Fork map — which commit each working branch was spawned from, so the history
# view can back-link a commit to the branch(es) it spawned (S38). Per-clone
# (forks are a local working-branch concept), also untracked.
# ---------------------------------------------------------------------------


def read_forks(project_root: Path) -> dict[str, str]:
    """Map of working-branch name → the fork-point commit it was spawned at."""
    path = _forks_path(project_root)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning("git_forks_unreadable", path=str(path))
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _write_forks(project_root: Path, forks: dict[str, str]) -> None:
    path = _forks_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forks, indent=2) + "\n")


def set_fork(project_root: Path, branch: str, sha: str) -> None:
    """Record *branch* as spawned from *sha*."""
    forks = read_forks(project_root)
    forks[branch] = sha
    _write_forks(project_root, forks)


def remove_fork(project_root: Path, branch: str) -> None:
    """Forget *branch*'s fork point (e.g. on delete)."""
    forks = read_forks(project_root)
    if forks.pop(branch, None) is not None:
        _write_forks(project_root, forks)


def rename_fork(project_root: Path, old: str, new: str) -> None:
    """Move a fork entry when a branch is renamed (archive/restore)."""
    forks = read_forks(project_root)
    if old in forks:
        forks[new] = forks.pop(old)
        _write_forks(project_root, forks)
