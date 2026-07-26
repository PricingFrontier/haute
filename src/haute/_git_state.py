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
from threading import RLock

from haute._file_ops import atomic_write_text
from haute._git_lock import repository_mutation
from haute._logging import get_logger

logger = get_logger(component="git_state")

_STATE_DIR = ".haute"
_STATE_FILE = "state.json"
_PREFS_FILE = "prefs.json"
_PUSHED_FILE = "pushed.json"
_TRASH_FILE = "trash.json"
_WORKING_BRANCH_KEY = "workingBranch"

# Serialises pushed.json access within this process. Atomic replacement keeps
# the file complete, while the lock prevents Windows readers from holding the
# destination open during replacement and makes read/merge/replace one
# transaction for concurrent successful pushes. RLock lets the public reader
# remain safe if it is reused from another locked operation in future.
_pushed_state_lock = RLock()

# Most recent tombstones kept in trash.json — a recovery net, not an archive;
# the oldest entry drops when a new delete would exceed this.
_TRASH_MAX_ENTRIES = 20


def _state_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _STATE_FILE


def _prefs_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _PREFS_FILE


def _pushed_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _PUSHED_FILE


def _trash_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _TRASH_FILE


def read_working_branch(project_root: Path) -> str | None:
    """The clone's recorded working branch, or None when unset.

    Unreadable or malformed state is treated as unset (the startup flow
    re-prompts) rather than an error — the file is reconstructable user
    preference, not data.
    """
    path = _state_path(project_root)
    with repository_mutation(project_root):
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
    with repository_mutation(project_root):
        path = _state_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({_WORKING_BRANCH_KEY: branch}, indent=2) + "\n")
    logger.info("git_state_written", branch=branch)


def clear_working_branch(project_root: Path) -> None:
    """Forget the clone's working branch (e.g. after archiving/deleting it).

    Leaves the clone in the 'unset' state — the next save re-prompts the
    working-branch chooser (S5/S13). A no-op when no state file exists.
    """
    with repository_mutation(project_root):
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
    with repository_mutation(project_root):
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
    with repository_mutation(project_root):
        prefs = read_prefs(project_root)
        prefs[key] = value
        path = _prefs_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(prefs, indent=2) + "\n")
    logger.info("git_pref_written", key=key)


# ---------------------------------------------------------------------------
# Trash tombstones — recovery metadata for deleted working pairs, keyed by the
# deleted working-branch name. Each entry records the pair's tips (the objects
# themselves are pinned under ``refs/haute/trash/``), whether the pair was
# archived, and when it was deleted — so
# ``undelete_working_pair`` can rebuild the pair exactly. Per-clone (deletes
# are local ref surgery), also untracked, capped to the newest
# ``_TRASH_MAX_ENTRIES`` (insertion order IS recency: a re-recorded name moves
# to the back).
# ---------------------------------------------------------------------------


def read_trash(project_root: Path) -> dict[str, dict[str, object]]:
    """Map of deleted working-branch name → its recovery tombstone
    (oldest-first; empty when the file is missing/malformed)."""
    path = _trash_path(project_root)
    with repository_mutation(project_root):
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            logger.warning("git_trash_unreadable", path=str(path))
            return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _write_trash(project_root: Path, trash: dict[str, dict[str, object]]) -> None:
    path = _trash_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(trash, indent=2) + "\n")


def record_trash(project_root: Path, branch: str, entry: dict[str, object]) -> None:
    """Record *branch*'s tombstone as the newest entry, dropping the oldest
    beyond the cap (a re-deleted name replaces its old tombstone)."""
    with repository_mutation(project_root):
        trash = read_trash(project_root)
        trash.pop(branch, None)  # re-insert at the back so recency is honest
        trash[branch] = entry
        while len(trash) > _TRASH_MAX_ENTRIES:
            trash.pop(next(iter(trash)))
        _write_trash(project_root, trash)
    logger.info("git_trash_recorded", branch=branch)


def remove_trash(project_root: Path, branch: str) -> None:
    """Forget *branch*'s tombstone (e.g. after a successful undelete)."""
    with repository_mutation(project_root):
        trash = read_trash(project_root)
        if trash.pop(branch, None) is not None:
            _write_trash(project_root, trash)


# ---------------------------------------------------------------------------
# Last-pushed tips — the SHA each remote ref was at the last time THIS clone
# pushed it, keyed ``<remote>/<ref>`` (P7 §6.8). Lets rewrite detection (X3)
# survive a pruned reflog: if a remote tip is no longer a descendant of what we
# published, the upstream history was rewritten. Per-clone, also untracked.
# ---------------------------------------------------------------------------


def _read_pushed_shas_unlocked(project_root: Path) -> dict[str, str]:
    """Read pushed tips; caller must hold ``_pushed_state_lock``."""
    path = _pushed_path(project_root)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning("git_pushed_unreadable", path=str(path))
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def read_pushed_shas(project_root: Path) -> dict[str, str]:
    """Map of ``<remote>/<ref>`` → the SHA this clone last pushed it to."""
    with repository_mutation(project_root), _pushed_state_lock:
        return _read_pushed_shas_unlocked(project_root)


def record_pushed_shas(project_root: Path, pushed: dict[str, str]) -> None:
    """Merge *pushed* (``<remote>/<ref>`` → SHA) into the recorded last-pushed
    tips, preserving entries for other remotes/refs (creates ``.haute/`` if
    needed)."""
    if not pushed:
        return
    with repository_mutation(project_root), _pushed_state_lock:
        current = _read_pushed_shas_unlocked(project_root)
        current.update(pushed)
        path = _pushed_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(current, indent=2) + "\n")
    logger.info("git_pushed_recorded", refs=sorted(pushed))
