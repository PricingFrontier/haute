"""Safe ownership markers and bounded cleanup for crash-surviving artifacts."""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil

from haute._logging import get_logger

logger = get_logger(component="artifact_housekeeping")

_MARKER_FILENAME = ".haute-artifact.json"
_SCHEMA_VERSION = 1
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


def create_owned_artifact_directory(root: Path, prefix: str, owner: str) -> Path:
    """Create one marked direct child of *root*, removing it if marking fails."""
    _validate_owner(owner)
    _validate_prefix(prefix)
    if root.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse_point(root.lstat()):
        raise ValueError("artifact root must not be a symlink or reparse point")
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    try:
        if directory.resolve().parent != root.resolve():
            raise RuntimeError("artifact directory must be a direct child of its root")
        created_at = time.time()
        if not math.isfinite(created_at) or created_at < 0:
            raise RuntimeError("system clock produced an invalid artifact creation time")
        (directory / _MARKER_FILENAME).write_text(
            json.dumps(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "owner": owner,
                    "created_at": created_at,
                    # The creating process: a live one's directories are never reaped.
                    "pid": os.getpid(),
                    "process_started_at": _CURRENT_PROCESS_STARTED_AT,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory


def reap_stale_artifact_directories(
    root: Path,
    owner: str,
    stale_after_seconds: int | float,
    *,
    now: float | None = None,
) -> dict[str, int]:
    """Remove only marked, owned, stale direct descendants of an existing root."""
    _validate_owner(owner)
    if (
        isinstance(stale_after_seconds, bool)
        or not isinstance(stale_after_seconds, (int, float))
        or not math.isfinite(stale_after_seconds)
        or stale_after_seconds < 0
    ):
        raise ValueError("stale_after_seconds must be a finite non-negative number")
    current_time = time.time() if now is None else now
    if (
        isinstance(current_time, bool)
        or not isinstance(current_time, (int, float))
        or not math.isfinite(current_time)
        or current_time < 0
    ):
        raise ValueError("now must be a finite non-negative number")
    report = {"inspected": 0, "removed": 0, "skipped": 0, "failed": 0, "reclaimed_bytes": 0}
    try:
        root_metadata = root.lstat()
    except FileNotFoundError:
        return report
    except OSError as exc:
        logger.warning(
            "artifact_reap_root_inspection_failed",
            root=str(root),
            error=str(exc),
        )
        report["failed"] += 1
        return report
    if _is_link_or_reparse_point(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        return report
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        logger.warning(
            "artifact_reap_root_resolution_failed",
            root=str(root),
            error=str(exc),
        )
        report["failed"] += 1
        return report

    cutoff = current_time - stale_after_seconds
    try:
        children = list(root.iterdir())
    except OSError as exc:
        logger.warning(
            "artifact_reap_root_scan_failed",
            root=str(root),
            error=str(exc),
        )
        report["failed"] += 1
        return report

    for child in children:
        report["inspected"] += 1
        try:
            child_metadata = child.lstat()
            resolved_parent = child.parent.resolve(strict=True)
        except OSError as exc:
            logger.warning(
                "artifact_reap_child_inspection_failed",
                path=str(child),
                error=str(exc),
            )
            report["failed"] += 1
            continue
        if (
            _is_link_or_reparse_point(child_metadata)
            or not stat.S_ISDIR(child_metadata.st_mode)
            or resolved_parent != resolved_root
        ):
            report["skipped"] += 1
            continue
        marker = _read_valid_marker(child / _MARKER_FILENAME)
        if (
            marker is None
            or marker["owner"] != owner
            or marker["created_at"] > cutoff
            or _creator_is_running(marker)
        ):
            report["skipped"] += 1
            continue
        reclaimed_bytes = _directory_size_bytes(child)
        try:
            shutil.rmtree(child)
        except OSError as exc:
            logger.warning(
                "artifact_reap_cleanup_failed",
                path=str(child),
                error=str(exc),
            )
            report["failed"] += 1
            continue
        report["removed"] += 1
        report["reclaimed_bytes"] += reclaimed_bytes
    return report


def _is_link_or_reparse_point(metadata: os.stat_result) -> bool:
    """Return whether non-following metadata identifies a link-like entry."""
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _validate_owner(owner: object) -> None:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("artifact owner must be a non-empty string")


def _validate_prefix(prefix: object) -> None:
    if (
        not isinstance(prefix, str)
        or not prefix
        or prefix in {".", ".."}
        or "/" in prefix
        or "\\" in prefix
        or Path(prefix).is_absolute()
    ):
        raise ValueError("artifact prefix must be a non-empty path component")


def _read_valid_marker(path: Path) -> dict[str, Any] | None:
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(marker, dict) or marker.get("schema_version") != _SCHEMA_VERSION:
        return None
    owner = marker.get("owner")
    created_at = marker.get("created_at")
    if not isinstance(owner, str) or not owner.strip():
        return None
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
        return None
    if not math.isfinite(created_at) or created_at < 0:
        return None
    pid = marker.get("pid")
    started_at = marker.get("process_started_at")
    if pid is None and started_at is None:
        # A marker written before creators were recorded: age alone decides.
        return {"owner": owner, "created_at": created_at, "pid": None, "started_at": None}
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        return None
    if not math.isfinite(started_at):
        return None
    return {"owner": owner, "created_at": created_at, "pid": pid, "started_at": started_at}


def _process_started_at(pid: int) -> float:
    return float(psutil.Process(pid).create_time())


_CURRENT_PROCESS_STARTED_AT = _process_started_at(os.getpid())
# Process start times are reported to the clock tick, so compare with a margin.
_START_TIME_TOLERANCE_SECONDS = 1.0


def _creator_is_running(marker: dict[str, Any]) -> bool:
    """Whether the process that created a marked directory is still running.

    A recorded start time tells the creator apart from a later process that
    reused its pid. A process whose start time cannot be read is treated as
    running: a live server's files are never removed on a guess.
    """
    pid = marker["pid"]
    if not isinstance(pid, int):
        return False
    recorded_started_at = float(marker["started_at"])
    try:
        started_at = _process_started_at(pid)
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True
    return abs(started_at - recorded_started_at) <= _START_TIME_TOLERANCE_SECONDS


def _directory_size_bytes(directory: Path) -> int:
    total = 0
    try:
        paths: Iterator[Path] = directory.rglob("*")
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total
