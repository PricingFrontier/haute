"""Safe ownership markers and bounded cleanup for crash-surviving artifacts."""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from haute._logging import get_logger

logger = get_logger(component="artifact_housekeeping")

_MARKER_FILENAME = ".haute-artifact.json"
_SCHEMA_VERSION = 1


def create_owned_artifact_directory(root: Path, prefix: str, owner: str) -> Path:
    """Create one marked direct child of *root*, removing it if marking fails."""
    _validate_owner(owner)
    _validate_prefix(prefix)
    if root.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    try:
        if directory.resolve().parent != root.resolve():
            raise RuntimeError("artifact directory must be a direct child of its root")
        created_at = time.time()
        if not math.isfinite(created_at) or created_at < 0:
            raise RuntimeError("system clock produced an invalid artifact creation time")
        (directory / _MARKER_FILENAME).write_text(
            json.dumps(
                {"schema_version": _SCHEMA_VERSION, "owner": owner, "created_at": created_at},
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
    if root.is_symlink() or not root.is_dir():
        return report
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        logger.warning(
            "artifact_reap_root_resolution_failed",
            root=str(root),
            error=str(exc),
            exc_info=True,
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
            exc_info=True,
        )
        report["failed"] += 1
        return report

    for child in children:
        report["inspected"] += 1
        if child.is_symlink() or not child.is_dir():
            report["skipped"] += 1
            continue
        try:
            if child.resolve(strict=True).parent != resolved_root:
                report["skipped"] += 1
                continue
        except OSError as exc:
            logger.warning(
                "artifact_reap_child_resolution_failed",
                path=str(child),
                error=str(exc),
                exc_info=True,
            )
            report["failed"] += 1
            continue
        marker = _read_valid_marker(child / _MARKER_FILENAME)
        if marker is None or marker["owner"] != owner or marker["created_at"] > cutoff:
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
                exc_info=True,
            )
            report["failed"] += 1
            continue
        report["removed"] += 1
        report["reclaimed_bytes"] += reclaimed_bytes
    return report


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
    return {"owner": owner, "created_at": created_at}


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
