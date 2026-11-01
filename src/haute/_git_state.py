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
_INTERRUPTED_FILE = "interrupted.json"
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


def _interrupted_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _INTERRUPTED_FILE


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


def restore_working_branch(project_root: Path, previous: str | None) -> None:
    """Record *previous* as the clone's working branch again, or none when it is ``None``:
    a rolled-back mutation's undo of the record it changed."""
    if previous is None:
        clear_working_branch(project_root)
    else:
        write_working_branch(project_root, previous)


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
# Trash tombstones — recovery metadata for deleted working pairs, one entry per
# deletion, keyed ``<branch>@<UTC stamp>`` so a name deleted, made again and
# deleted again keeps both. Each entry records the branch, the pair's tips and
# the refs pinning them (so gc cannot collect them), whether the pair was
# archived, and when it was deleted — so ``undelete_working_pair`` can rebuild
# the pair exactly. Per-clone (deletes are local ref surgery), also untracked,
# trimmed to the newest ``_TRASH_MAX_ENTRIES`` once a delete has succeeded
# (insertion order IS recency). An entry written before ids existed is keyed by
# the bare branch name, with its pins at ``refs/haute/trash/<name>`` and
# ``refs/haute/trash/<name>-save``.
# ---------------------------------------------------------------------------


def trash_entry_branch(entry_id: str, entry: dict[str, object]) -> str:
    """The branch a trash entry restores (a bare-name entry's key is its branch)."""
    branch = entry.get("branch")
    return branch if isinstance(branch, str) else entry_id


def trash_entry_pins(entry_id: str, entry: dict[str, object]) -> tuple[str, str]:
    """The refs pinning a trash entry's working and ledger tips."""
    working_pin = entry.get("working_pin")
    ledger_pin = entry.get("ledger_pin")
    if isinstance(working_pin, str) and isinstance(ledger_pin, str):
        return working_pin, ledger_pin
    return f"refs/haute/trash/{entry_id}", f"refs/haute/trash/{entry_id}-save"


def read_trash(project_root: Path) -> dict[str, dict[str, object]]:
    """Map of trash entry id → its recovery tombstone
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


def record_trash(project_root: Path, entry_id: str, entry: dict[str, object]) -> None:
    """Record a deletion's tombstone as the newest entry."""
    with repository_mutation(project_root):
        trash = read_trash(project_root)
        trash.pop(entry_id, None)  # re-insert at the back so recency is honest
        trash[entry_id] = entry
        _write_trash(project_root, trash)
    logger.info("git_trash_recorded", entry=entry_id)


def trim_trash(project_root: Path) -> list[tuple[str, dict[str, object]]]:
    """Drop the oldest tombstones beyond the cap and return them, for the caller to
    delete their pins: nothing could restore them any more."""
    with repository_mutation(project_root):
        trash = read_trash(project_root)
        dropped: list[tuple[str, dict[str, object]]] = []
        while len(trash) > _TRASH_MAX_ENTRIES:
            oldest = next(iter(trash))
            dropped.append((oldest, trash.pop(oldest)))
        if dropped:
            _write_trash(project_root, trash)
    return dropped


def remove_trash(project_root: Path, branch: str) -> None:
    """Forget a tombstone by its entry id (e.g. after a successful undelete)."""
    with repository_mutation(project_root):
        trash = read_trash(project_root)
        if trash.pop(branch, None) is not None:
            _write_trash(project_root, trash)


# ---------------------------------------------------------------------------
# An interrupted tree — files a git action could not write (Windows refuses to
# replace a file another program holds open) and the fence could not put back,
# so the working tree is out of step with HEAD. Readiness answers "interrupted"
# while it exists; Retry writes HEAD's version of the files and clears it.
# ---------------------------------------------------------------------------


def read_interrupted(project_root: Path) -> dict[str, object] | None:
    """The interrupted tree's record (``operation``, ``files``, ``at``), or None.

    A record that cannot be read is still an interruption: the files it would
    name are unknown, so it reads as one with no files rather than as healthy."""
    path = _interrupted_path(project_root)
    with repository_mutation(project_root):
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            logger.warning("git_interrupted_unreadable", path=str(path))
            return {"operation": "unknown", "files": [], "at": None}
    if not isinstance(raw, dict):
        return {"operation": "unknown", "files": [], "at": None}
    files = raw.get("files")
    return {
        "operation": raw.get("operation") if isinstance(raw.get("operation"), str) else "unknown",
        "files": [f for f in files if isinstance(f, str)] if isinstance(files, list) else [],
        "at": raw.get("at") if isinstance(raw.get("at"), str) else None,
    }


def record_interrupted(project_root: Path, operation: str, files: list[str]) -> None:
    """Record the working tree as out of step with HEAD over *files*."""
    from datetime import UTC, datetime

    path = _interrupted_path(project_root)
    with repository_mutation(project_root):
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"operation": operation, "files": files, "at": datetime.now(UTC).isoformat()}
        atomic_write_text(path, json.dumps(record, indent=2) + "\n")
    logger.warning("git_tree_interrupted", operation=operation, files=files)


def clear_interrupted(project_root: Path) -> None:
    """Forget the interruption once HEAD's version of its files is written."""
    with repository_mutation(project_root):
        _interrupted_path(project_root).unlink(missing_ok=True)


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
