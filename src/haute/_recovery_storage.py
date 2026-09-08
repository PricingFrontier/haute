"""Private, bounded recovery evidence and durable records inside one project."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from haute._cache import canonical_json
from haute._config_io import is_windows_reserved_filename, reject_duplicate_keys_hook
from haute._pipeline_recovery import _recovery_artifacts
from haute._pipeline_repair import PipelineRepairError
from haute.discovery import discover_pipelines

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS = 512
MAX_DRAFTS = 200
_ID = re.compile(r"^[0-9a-f]{32}$")


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


def encode(raw: bytes | None) -> str | None:
    return None if raw is None else base64.b64encode(raw).decode("ascii")


def decode(raw: str | None) -> bytes | None:
    return None if raw is None else base64.b64decode(raw, validate=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot(root: Path, source_file: str) -> tuple[list[str], dict[str, str | None]]:
    """Capture only authored graph artifacts; never copy data, caches or credentials."""
    active = safe_path(root, source_file)
    pipelines = {active, *discover_pipelines(root)}
    roots = sorted(path.relative_to(root).as_posix() for path in pipelines)
    paths = {"haute.toml"}
    for relative in roots:
        source = safe_path(root, relative)
        for _role, path in _recovery_artifacts(source, root):
            paths.add(path.relative_to(root).as_posix())
    if len(paths) > MAX_ARTIFACTS:
        raise conflict("Recovery scope exceeds the 512 artifact limit; repair a smaller project.")
    result = {name: encode(read_artifact(root, name)) for name in sorted(paths)}
    if sum(len(decode(value) or b"") for value in result.values()) > MAX_SNAPSHOT_BYTES:
        raise conflict("Recovery evidence exceeds the 64 MiB limit.")
    return roots, result


def record_path(root: Path, draft_id: str) -> Path:
    if not _ID.fullmatch(draft_id):
        raise conflict("Invalid recovery draft identity.", "recovery_draft_not_found")
    return safe_path(root, f".haute/recovery/{draft_id}.json", private=True)


def load_record(root: Path, draft_id: str) -> dict[str, Any]:
    path = record_path(root, draft_id)
    if not path.exists():
        raise conflict("Recovery draft was not found.", "recovery_draft_not_found")
    if not path.is_file() or path.stat().st_size > MAX_SNAPSHOT_BYTES * 6:
        raise conflict("Recovery draft is corrupt or exceeds the record limit.")
    try:
        record = json.loads(path.read_bytes(), object_pairs_hook=reject_duplicate_keys_hook)
        if not isinstance(record, dict) or record.get("format") != 1:
            raise ValueError("record format")
        if record["draft"]["draft_id"] != draft_id:
            raise ValueError("record identity")
        return record
    except (ValueError, KeyError, TypeError) as exc:
        raise conflict("Recovery draft is corrupt; its evidence has been retained.") from exc


def save_record(root: Path, record: dict[str, Any]) -> None:
    path = record_path(root, record["draft"]["draft_id"])
    raw = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_SNAPSHOT_BYTES * 6:
        raise conflict("Recovery record exceeds the storage limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".recovery-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    # Persist the rename on platforms that support directory fsync.
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def record_ids(root: Path) -> list[str]:
    directory = safe_path(root, ".haute/recovery", private=True)
    if not directory.exists():
        return []
    paths = sorted(directory.glob("*.json"))
    if len(paths) > MAX_DRAFTS:
        raise conflict("Recovery history exceeds the 200 record limit; archive old records first.")
    return [path.stem for path in paths if _ID.fullmatch(path.stem)]
