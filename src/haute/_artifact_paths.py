"""Contained project-relative artifact paths shared by recovery and locking."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Literal

from haute._config_io import is_windows_reserved_filename
from haute._pipeline_repair import PipelineRepairError
from haute._sandbox import contained_path
from haute.errors import PathOutsideProjectError

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

#: Which rule a project-relative path breaks: not an ordinary relative name, a link or
#: junction on the way, a multiply linked file, something other than a file or folder, or a
#: path that leaves the root.
PathProblem = Literal["not_ordinary", "link", "multiply_linked", "not_a_file", "outside"]

_RECOVERY_REFUSALS: dict[PathProblem, str] = {
    "not_ordinary": "Recovery requires an ordinary project-relative artifact path.",
    "link": "Recovery cannot operate through a symlink or directory junction.",
    "multiply_linked": "Recovery cannot replace a multiply linked artifact.",
    "not_a_file": "Recovery artifacts must be ordinary files.",
    "outside": "Recovery artifact is outside the project.",
}


def conflict(message: str, code: str = "recovery_conflict") -> PipelineRepairError:
    return PipelineRepairError(code, message)


def ordinary_path_problem(
    root: Path, relative: str, *, private: bool = False
) -> tuple[Path, PathProblem | None]:
    """Resolve *relative* under *root* and report the first rule it breaks, if any.

    Rejects traversal and filesystem aliases, including Windows reparse points. Callers
    word the refusal themselves.
    """
    root = root.resolve()
    rel = Path(relative)
    if (
        not relative
        or "\x00" in relative
        or rel.is_absolute()
        or rel.drive
        or any(part == ".." or part.casefold() == ".git" for part in rel.parts)
        or any(
            part.endswith((".", " ")) or is_windows_reserved_filename(part) for part in rel.parts
        )
        or (not private and any(part.casefold() == ".haute" for part in rel.parts))
        or ":" in relative
    ):
        return root, "not_ordinary"
    current = root
    for part in rel.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return current, "link"
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            return current, "multiply_linked"
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            return current, "not_a_file"
    try:
        contained_path(root, current)
    except PathOutsideProjectError:
        return current, "outside"
    return current, None


def safe_path(root: Path, relative: str, *, private: bool = False) -> Path:
    """Reject traversal and filesystem aliases, including Windows reparse points."""
    path, problem = ordinary_path_problem(root, relative, private=private)
    if problem is not None:
        raise conflict(_RECOVERY_REFUSALS[problem])
    return path


def read_artifact(root: Path, relative: str) -> bytes | None:
    path = safe_path(root, relative)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise conflict("Recovery artifact is not a file or exceeds the 16 MiB limit.")
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise conflict("Recovery artifact exceeds the 16 MiB limit.")
    return raw
