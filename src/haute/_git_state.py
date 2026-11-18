"""Per-clone haute state — the working-branch association.

Lives at ``<project_root>/.haute/state.json``, deliberately untracked: the
file answers "which working branch does THIS clone serve", which is exactly
the thing HEAD cannot answer once HEAD lives on the save ledger. Everything
reads and writes through this module so a future relocation is a one-file
change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
_OPERATION_FILE = "operation.json"
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


def _operation_path(project_root: Path) -> Path:
    return project_root / _STATE_DIR / _OPERATION_FILE


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


def trash_overflow(
    trash: dict[str, dict[str, object]], adding: str
) -> list[tuple[str, dict[str, object]]]:
    """The oldest tombstones of *trash* the cap drops once the entry *adding* is the
    newest: nothing could restore them any more, so their pins go too."""
    kept = [entry_id for entry_id in trash if entry_id != adding] + [adding]
    over = len(kept) - _TRASH_MAX_ENTRIES
    return [(entry_id, trash[entry_id]) for entry_id in kept[: max(over, 0)]]


def remove_trash(project_root: Path, branch: str) -> None:
    """Forget a tombstone by its entry id (e.g. after a successful undelete)."""
    with repository_mutation(project_root):
        trash = read_trash(project_root)
        if trash.pop(branch, None) is not None:
            _write_trash(project_root, trash)


# ---------------------------------------------------------------------------
# The record of a git operation in progress (``_git_operation``): written before
# its first step and removed after its last, so one still there is an operation
# that stopped part-way. Readiness answers "interrupted" while it exists, and
# every git action but Retry refuses.
# ---------------------------------------------------------------------------

#: What the analyst reads for each operation a record can name.
_OPERATION_WORDS = {
    "restore": "Restoring a version",
    "catch-up": "Catching up",
    "select-branch": "Choosing a branch",
    "open-ledger": "Opening the working branch",
    "seed": "Making the first version",
    "create-branch": "Creating a branch",
    "spin-off": "Spinning off a copy",
    "archive": "Archiving a branch",
    "delete": "Deleting a branch",
    "undelete": "Restoring a deleted branch",
    "unarchive": "Restoring an archived branch",
    "repair": "Repairing a branch",
    "milestone": "Committing a milestone",
    "milestone-hooks": "Running the project's commit hooks",
    "adopt-clone": "Opening the stored project",
}

#: The operations a record can name.
OPERATIONS = frozenset(_OPERATION_WORDS)

# How many files a message names before counting the rest.
_FILES_NAMED = 5


def name_files(files: list[str]) -> str:
    """The first files of *files* by name and the rest counted, for a message."""
    more = len(files) - _FILES_NAMED
    return ", ".join(files[:_FILES_NAMED]) + (f" and {more} more" if more > 0 else "")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """An operation's record as ``operation.json`` holds it.

    *initial* maps each slot the operation changes, and HEAD and every branch HEAD
    names in any of its states, to its value before the first step. Each of *steps*
    is the slots that step sets, and *kinds* says what each step is, so the state
    after step k is *initial* overlaid with the first k steps, and the steps whose
    kind writes files say which commit the files hold. A record that cannot be read
    is still an interruption: *readable* is False and the operation ``unknown``."""

    operation: str
    at: str | None
    initial: dict[str, object]
    steps: list[dict[str, object]]
    kinds: list[str]
    commit_point: int
    files: list[str]
    wipes_caches: bool
    readable: bool = True

    @property
    def words(self) -> str:
        return operation_words(self.operation)

    def to_json(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "at": self.at,
            "initial": self.initial,
            "steps": [
                {"kind": kind, "sets": sets}
                for kind, sets in zip(self.kinds, self.steps, strict=True)
            ],
            "commit_point": self.commit_point,
            "files": self.files,
            "wipes_caches": self.wipes_caches,
        }


def operation_words(operation: str) -> str:
    """The analyst's words for *operation* ("Restoring a version")."""
    return _OPERATION_WORDS.get(operation, "A git action")


def interruption_message(record: OperationRecord) -> str:
    """Readiness's line for an interrupted clone: the operation and its files."""
    if record.files:
        return f"{record.words} stopped part-way: {name_files(record.files)}."
    return f"{record.words} stopped part-way."


def _unreadable_operation() -> OperationRecord:
    return OperationRecord("unknown", None, {}, [], [], 0, [], False, readable=False)


def _parse_operation(raw: object) -> OperationRecord | None:
    if not isinstance(raw, dict):
        return None
    operation, at, initial, steps = (
        raw.get("operation"),
        raw.get("at"),
        raw.get("initial"),
        raw.get("steps"),
    )
    commit_point, files, wipes = raw.get("commit_point"), raw.get("files"), raw.get("wipes_caches")
    if not (
        operation in OPERATIONS
        and isinstance(at, str)
        and isinstance(initial, dict)
        and all(isinstance(slot, str) for slot in initial)
        and isinstance(steps, list)
        and all(_is_step(step) for step in steps)
        and isinstance(commit_point, int)
        and not isinstance(commit_point, bool)
        and 0 <= commit_point <= len(steps)
        and isinstance(files, list)
        and all(isinstance(path, str) for path in files)
        and isinstance(wipes, bool)
    ):
        return None
    assert isinstance(operation, str)
    kinds = [step["kind"] for step in steps]
    sets = [step["sets"] for step in steps]
    return OperationRecord(operation, at, initial, sets, kinds, commit_point, files, wipes)


#: What a recorded step is: a ref transaction, a checkout, a fast-forward, a write of the
#: files from HEAD, HEAD alone, the recorded working branch, a tombstone, or a call.
STEP_KINDS = frozenset(
    {"refs", "checkout", "fast-forward", "files", "head", "branch", "trash", "call"}
)


def _is_step(step: object) -> bool:
    if not isinstance(step, dict) or set(step) != {"kind", "sets"}:
        return False
    sets = step["sets"]
    return (
        step["kind"] in STEP_KINDS
        and isinstance(sets, dict)
        and all(isinstance(slot, str) for slot in sets)
    )


def read_operation(project_root: Path) -> OperationRecord | None:
    """The record of the operation in progress or stopped part-way, or None."""
    path = _operation_path(project_root)
    with repository_mutation(project_root):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            logger.warning("git_operation_unreadable", path=str(path))
            return _unreadable_operation()
    record = _parse_operation(raw)
    if record is None:
        logger.warning("git_operation_unreadable", path=str(path))
        return _unreadable_operation()
    return record


def write_operation(project_root: Path, record: OperationRecord) -> None:
    """Record an operation before its first step (creates ``.haute/`` if needed)."""
    path = _operation_path(project_root)
    with repository_mutation(project_root):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(record.to_json(), indent=2) + "\n")


def clear_operation(project_root: Path) -> None:
    """Forget an operation once it has finished or been put back."""
    with repository_mutation(project_root):
        _operation_path(project_root).unlink(missing_ok=True)


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
