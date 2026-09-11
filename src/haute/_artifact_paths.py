"""Contained project-relative artifact paths shared by recovery and locking."""

from __future__ import annotations

import stat
from pathlib import Path

from haute._config_io import is_windows_reserved_filename
from haute._pipeline_repair import PipelineRepairError

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def conflict(message: str, code: str = "recovery_conflict") -> PipelineRepairError:
    return PipelineRepairError(code, message)


def safe_path(root: Path, relative: str, *, private: bool = False) -> Path:
    """Reject traversal and filesystem aliases, including Windows reparse points."""
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
        raise conflict("Recovery requires an ordinary project-relative artifact path.")
    current = root
    for part in rel.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise conflict("Recovery cannot operate through a symlink or directory junction.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise conflict("Recovery cannot replace a multiply linked artifact.")
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise conflict("Recovery artifacts must be ordinary files.")
    if not current.resolve().is_relative_to(root):
        raise conflict("Recovery artifact is outside the project.")
    return current


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
