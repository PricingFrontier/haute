"""Process-owned runtime storage: disk budget, spill leases, parquet snapshots.

File-backed runtime snapshots live outside a cache generation so replacing or
clearing that generation cannot retarget an already-returned LazyFrame, and
orderly process exit removes every remaining private directory."""

from __future__ import annotations

import atexit
import hashlib
import math
import os
import shutil
import stat as stat_module
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from haute._env import int_env
from haute._execution_context import (
    current_execution_context,
)
from haute._json_shred import _publication, _source_proof
from haute._json_shred._publication import JsonCacheRecoveryError
from haute._json_shred._source_proof import _StrongFileRevision
from haute._logging import get_logger
from haute._process_memory import process_is_alive

logger = get_logger(component="json_shred")


_DIRECT_SPILL_DIRNAME = ".runtime-spills"


# Direct spill bundles are deliberately not cache artifacts.  An unmanaged
# LazyFrame can be cloned and retained after this function returns, so its
# bundle has the same process-exit lifetime rule as runtime snapshots.
_DIRECT_SPILL_PROCESS_ID = os.getpid()


_DIRECT_SPILL_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"


_DIRECT_SPILL_DIRS: set[Path] = set()


_DIRECT_SPILL_LOCK = threading.Lock()


_DIRECT_SPILL_ATEXIT_REGISTERED = False


# File-backed runtime snapshots live outside a cache generation so replacing or
# clearing that generation cannot retarget an already-returned LazyFrame. Repeated
# access to one artifact generation can share a private path across managed
# executions. Its verification-cache pin is independently bounded; unmanaged direct
# callers pin it for the process:
# a derived Polars plan can outlive the original LazyFrame, so there is no safe
# Python-object lifetime at which to reclaim its source. Orderly process exit
# removes every remaining private snapshot directory.
_RUNTIME_SNAPSHOT_DIRNAME = ".runtime-snapshots"


_RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX = 32


_RUNTIME_SNAPSHOT_PROCESS_ID = os.getpid()


_RUNTIME_SNAPSHOT_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"


_RUNTIME_SNAPSHOT_DIRS: set[Path] = set()


_RUNTIME_SNAPSHOT_REFERENCES: dict[Path, int] = {}


_RUNTIME_SNAPSHOT_PROCESS_PINS: set[Path] = set()


_RUNTIME_SNAPSHOT_LOCK = threading.Lock()


_RUNTIME_SNAPSHOT_ATEXIT_REGISTERED = False


_RUNTIME_OWNER_META_FILENAME = ".owner.json"


_RUNTIME_OWNER_FORMAT_VERSION = 1


_RUNTIME_STORAGE_BUDGET_DEFAULT_BYTES = 4 * 1024 * 1024 * 1024


_RUNTIME_STORAGE_ORPHAN_GRACE_DEFAULT_SECONDS = 60 * 60


_RUNTIME_STORAGE_RECOVERY_PROCESS_ID = os.getpid()


_RUNTIME_STORAGE_RECOVERED_ROOTS: set[Path] = set()


RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES = int_env("HAUTE_JSON_RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES", 64)


RUNTIME_SNAPSHOT_CACHE_MAX_BYTES = int_env(
    "HAUTE_JSON_RUNTIME_SNAPSHOT_CACHE_MAX_BYTES", 512 * 1024 * 1024
)


class JsonRuntimeDiskBudgetExceededError(RuntimeError):
    """A runtime snapshot/spill allocation exceeded the project disk budget."""

    def __init__(self, *, used_bytes: int, budget_bytes: int) -> None:
        super().__init__(
            f"JSON runtime storage requires {used_bytes} bytes, exceeding its "
            f"{budget_bytes} byte disk budget"
        )
        self.used_bytes = used_bytes
        self.budget_bytes = budget_bytes


class JsonRuntimeStorageIntegrityError(RuntimeError):
    """Runtime storage contains an entry whose size cannot be trusted."""

    def __init__(self, *, path: Path, reason: str) -> None:
        super().__init__(
            f"JSON runtime storage cannot be measured safely because {path} is {reason}"
        )
        self.path = path
        self.reason = reason


def _runtime_storage_root_for_cache(cache_dir: Path) -> Path:
    absolute = Path(os.path.abspath(cache_dir))
    for candidate in (absolute, *absolute.parents):
        if candidate.name == ".haute_cache":
            return candidate
    return absolute.parent


def _runtime_storage_parents(cache_root: Path) -> tuple[Path, ...]:
    return (
        cache_root / _RUNTIME_SNAPSHOT_DIRNAME,
        cache_root / _DIRECT_SPILL_DIRNAME,
        cache_root / "working" / _RUNTIME_SNAPSHOT_DIRNAME,
        cache_root / "committed" / _RUNTIME_SNAPSHOT_DIRNAME,
    )


def _runtime_owner_payload() -> dict[str, int | float]:  # pragma: no mutate
    return {
        "format_version": _RUNTIME_OWNER_FORMAT_VERSION,
        "pid": os.getpid(),
        "created_at": time.time(),
    }


def _ensure_runtime_owner_metadata(owner_dir: Path) -> None:
    meta_path = owner_dir / _RUNTIME_OWNER_META_FILENAME
    if meta_path.exists():
        return
    temp_path = owner_dir / f".{_RUNTIME_OWNER_META_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        temp_path.write_bytes(orjson.dumps(_runtime_owner_payload()))
        os.replace(temp_path, meta_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _remove_empty_runtime_owner_dir(owner_dir: Path) -> None:
    """Remove an owner directory only when no runtime artifacts remain."""
    try:
        children = tuple(owner_dir.iterdir())
    except FileNotFoundError:
        return
    if any(child.name != _RUNTIME_OWNER_META_FILENAME for child in children):
        return
    (owner_dir / _RUNTIME_OWNER_META_FILENAME).unlink(missing_ok=True)
    owner_dir.rmdir()


def _runtime_owner_record(owner_dir: Path) -> tuple[int, float] | None:  # pragma: no mutate
    try:
        payload = orjson.loads((owner_dir / _RUNTIME_OWNER_META_FILENAME).read_bytes())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or type(payload.get("format_version")) is not int
        or payload.get("format_version") != _RUNTIME_OWNER_FORMAT_VERSION
    ):
        return None
    pid = payload.get("pid")
    created_at = payload.get("created_at")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(created_at)
    ):
        return None
    return pid, float(created_at)


def _recover_runtime_storage_parent(
    runtime_parent: Path,
    *,  # pragma: no mutate
    now: float,
    grace_seconds: int,
) -> dict[str, int]:
    report = {"inspected": 0, "removed": 0, "preserved": 0}
    if not runtime_parent.exists() and not runtime_parent.is_symlink():
        return report
    try:
        _publication._plain_directory_stat(runtime_parent)
        children = tuple(runtime_parent.iterdir())
    except (OSError, JsonCacheRecoveryError) as exc:
        logger.warning(
            "json_runtime_storage_parent_preserved",
            path=str(runtime_parent),
            reason="non_plain_or_unreadable",
            error_type=type(exc).__name__,
        )
        report["preserved"] += 1
        return report

    for owner_dir in children:
        report["inspected"] += 1
        try:
            _publication._plain_directory_stat(owner_dir)
        except (OSError, JsonCacheRecoveryError) as exc:
            report["preserved"] += 1
            logger.warning(
                "json_runtime_storage_owner_preserved",
                path=str(owner_dir),
                reason="non_plain_or_unreadable",
                error_type=type(exc).__name__,
            )
            continue
        owner = _runtime_owner_record(owner_dir)
        if owner is None:
            report["preserved"] += 1
            logger.warning(
                "json_runtime_storage_owner_preserved",
                path=str(owner_dir),
                reason="malformed_owner_metadata",
            )
            continue
        pid, created_at = owner
        if now - created_at < grace_seconds:
            report["preserved"] += 1
            continue
        if process_is_alive(pid):
            report["preserved"] += 1
            continue
        _publication._remove_plain_cache_directory(owner_dir)
        report["removed"] += 1
        logger.info(
            "json_runtime_storage_owner_reaped",
            path=str(owner_dir),
            pid=pid,
        )
    try:
        runtime_parent.rmdir()
    except OSError:
        pass
    return report


def recover_json_runtime_storage(
    cache_root: str | Path | None = None,  # pragma: no mutate
    *,  # pragma: no mutate
    now: float | None = None,  # pragma: no mutate
) -> dict[str, int]:
    """Reap only old, plain, ownership-marked directories from dead processes."""
    root = (
        Path(os.path.abspath(Path.cwd() / ".haute_cache"))
        if cache_root is None
        else Path(os.path.abspath(cache_root))
    )
    grace_seconds = int_env(
        "HAUTE_JSON_RUNTIME_ORPHAN_GRACE_SECONDS",
        _RUNTIME_STORAGE_ORPHAN_GRACE_DEFAULT_SECONDS,
    )
    current_time = time.time() if now is None else now
    if (
        isinstance(current_time, bool)
        or not isinstance(current_time, (int, float))
        or not math.isfinite(current_time)
    ):
        raise ValueError("now must be finite")
    aggregate = {"inspected": 0, "removed": 0, "preserved": 0}
    if root.exists() or root.is_symlink():
        try:
            _publication._plain_directory_stat(root)
        except (OSError, JsonCacheRecoveryError) as exc:
            logger.warning(
                "json_runtime_storage_root_preserved",
                path=str(root),
                reason="non_plain_or_unreadable",
                error_type=type(exc).__name__,
            )
            aggregate["preserved"] = 1
            return aggregate
    for runtime_parent in _runtime_storage_parents(root):
        report = _recover_runtime_storage_parent(
            runtime_parent,
            now=current_time,
            grace_seconds=grace_seconds,
        )
        for key, value in report.items():
            aggregate[key] += value
    return aggregate


def _runtime_file_identity(path: Path, path_stat: os.stat_result) -> tuple[object, ...]:
    if path_stat.st_ino:
        return ("inode", path_stat.st_dev, path_stat.st_ino)
    return ("path", os.path.normcase(str(path.resolve())))


def _runtime_storage_usage_bytes(cache_root: Path) -> int:
    identities: set[tuple[object, ...]] = set()
    total = 0

    def _visit(directory: Path) -> None:
        nonlocal total
        try:
            _publication._plain_directory_stat(directory)
            children = tuple(directory.iterdir())
        except FileNotFoundError:
            return
        except (OSError, JsonCacheRecoveryError) as exc:
            raise JsonRuntimeStorageIntegrityError(
                path=directory,
                reason="a non-plain or unreadable directory",
            ) from exc
        for child in children:
            try:
                child_stat = child.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise JsonRuntimeStorageIntegrityError(
                    path=child,
                    reason="unreadable",
                ) from exc
            if stat_module.S_ISDIR(child_stat.st_mode) and not _publication._is_reparse_point(
                child_stat
            ):
                _visit(child)
                continue
            if (
                not stat_module.S_ISREG(child_stat.st_mode)
                or stat_module.S_ISLNK(child_stat.st_mode)
                or _publication._is_reparse_point(child_stat)
            ):
                raise JsonRuntimeStorageIntegrityError(
                    path=child,
                    reason="not a plain file or directory",
                )
            try:
                identity = _runtime_file_identity(child, child_stat)
            except OSError as exc:
                raise JsonRuntimeStorageIntegrityError(
                    path=child,
                    reason="unreadable while resolving its file identity",
                ) from exc
            if identity in identities:
                continue
            identities.add(identity)
            total += child_stat.st_size

    for runtime_parent in _runtime_storage_parents(cache_root):
        _visit(runtime_parent)
    return total


def _recover_runtime_storage_once(cache_root: Path) -> None:
    global _RUNTIME_STORAGE_RECOVERY_PROCESS_ID, _RUNTIME_STORAGE_RECOVERED_ROOTS
    if os.getpid() != _RUNTIME_STORAGE_RECOVERY_PROCESS_ID:
        _RUNTIME_STORAGE_RECOVERY_PROCESS_ID = os.getpid()
        _RUNTIME_STORAGE_RECOVERED_ROOTS = set()
    if cache_root in _RUNTIME_STORAGE_RECOVERED_ROOTS:
        return
    recover_json_runtime_storage(cache_root)
    _RUNTIME_STORAGE_RECOVERED_ROOTS.add(cache_root)


@contextmanager
def _runtime_disk_budget_transaction(
    cache_root: Path,
    *,  # pragma: no mutate
    allow_existing_excess: bool = False,
) -> Iterator[None]:
    root = Path(os.path.abspath(cache_root))
    budget_bytes = int_env(
        "HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES",
        _RUNTIME_STORAGE_BUDGET_DEFAULT_BYTES,
    )
    with _publication._build_lock_for(root / ".runtime-storage-budget"):
        _recover_runtime_storage_once(root)
        used_before = _runtime_storage_usage_bytes(root)
        if used_before > budget_bytes and not allow_existing_excess:
            raise JsonRuntimeDiskBudgetExceededError(
                used_bytes=used_before,
                budget_bytes=budget_bytes,
            )
        yield
        used_after = _runtime_storage_usage_bytes(root)
        if used_after > budget_bytes:
            raise JsonRuntimeDiskBudgetExceededError(
                used_bytes=used_after,
                budget_bytes=budget_bytes,
            )


@dataclass(frozen=True, slots=True)
class _VerifiedRuntimeSnapshot:
    revision: _StrongFileRevision
    snapshot_path: Path
    size: int


class _VerifiedRuntimeSnapshotLoadGate:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.participants = 0


class _VerifiedRuntimeSnapshotCache:
    """Bounded, fork-safe LRU of already verified private parquet snapshots."""

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        for name, value in (("max_entries", max_entries), ("max_bytes", max_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, int, str], _VerifiedRuntimeSnapshot] = OrderedDict()
        self._path_counts: dict[Path, int] = {}
        self._path_sizes: dict[Path, int] = {}
        self._bytes = 0
        self._gates: dict[tuple[str, int, str], _VerifiedRuntimeSnapshotLoadGate] = {}
        self._warnings: OrderedDict[str, None] = OrderedDict()

    def _ensure_current_process(self) -> None:
        if os.getpid() == self._process_id:
            return
        # Do not acquire an inherited lock after fork: a vanished parent thread
        # may have owned it. These are child-local copies of all bookkeeping.
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._path_counts = {}
        self._path_sizes = {}
        self._bytes = 0
        self._gates = {}
        self._warnings = OrderedDict()

    def reset_after_fork(self) -> None:
        """Forget inherited state without touching parent-owned files."""
        self._ensure_current_process()
        with self._lock:
            self._entries.clear()
            self._path_counts.clear()
            self._path_sizes.clear()
            self._bytes = 0
            self._gates = {key: gate for key, gate in self._gates.items() if gate.participants}
            self._warnings.clear()

    def warn_revision_unavailable_once(self, path_key: str, path: Path) -> None:
        self._ensure_current_process()
        with self._lock:
            if path_key in self._warnings:
                self._warnings.move_to_end(path_key)
                return
            self._warnings[path_key] = None
            while len(self._warnings) > self._max_entries:
                self._warnings.popitem(last=False)
        logger.warning(
            "json_cache_artifact_revision_unavailable",
            parquet_path=str(path),
            action="full_artifact_hash_per_operation",
        )

    def begin(self, key: tuple[str, int, str]) -> _VerifiedRuntimeSnapshotLoadGate:
        self._ensure_current_process()
        with self._lock:
            gate = self._gates.setdefault(key, _VerifiedRuntimeSnapshotLoadGate())
            gate.participants += 1
            return gate

    def finish(self, key: tuple[str, int, str], gate: _VerifiedRuntimeSnapshotLoadGate) -> None:
        self._ensure_current_process()
        with self._lock:
            gate.participants -= 1
            if gate.participants == 0 and self._gates.get(key) is gate and key not in self._entries:
                del self._gates[key]

    def _drop_entry_locked(self, key: tuple[str, int, str]) -> list[Path]:
        entry = self._entries.pop(key)
        path = entry.snapshot_path
        count = self._path_counts[path] - 1
        if count:
            self._path_counts[path] = count
            return []
        del self._path_counts[path]
        self._bytes -= self._path_sizes.pop(path)
        return [path]

    def get(
        self, key: tuple[str, int, str], revision: _StrongFileRevision
    ) -> tuple[Path | None, list[Path]]:  # pragma: no mutate
        self._ensure_current_process()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None, []
            if entry.revision != revision or not entry.snapshot_path.exists():
                return None, self._drop_entry_locked(key)
            self._entries.move_to_end(key)
            return entry.snapshot_path, []

    def store(
        self,
        key: tuple[str, int, str],
        revision: _StrongFileRevision,
        snapshot_path: Path,
        size: int,
    ) -> tuple[bool, list[Path]]:
        """Pin a verified snapshot if it fits, returning cache-pin evictions."""
        self._ensure_current_process()
        if size > self._max_bytes:
            return False, []
        evicted: list[Path] = []
        with self._lock:
            if key in self._entries:
                evicted.extend(self._drop_entry_locked(key))
            self._entries[key] = _VerifiedRuntimeSnapshot(revision, snapshot_path, size)
            self._entries.move_to_end(key)
            if snapshot_path in self._path_counts:
                self._path_counts[snapshot_path] += 1
            else:
                self._path_counts[snapshot_path] = 1
                self._path_sizes[snapshot_path] = size
                self._bytes += size
            while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                old_key = next(iter(self._entries))
                evicted.extend(self._drop_entry_locked(old_key))
            self._gates = {
                gate_key: gate
                for gate_key, gate in self._gates.items()
                if gate.participants or gate_key in self._entries
            }
            retained = key in self._entries
        return retained, evicted

    def is_pinned(self, snapshot_path: Path) -> bool:
        self._ensure_current_process()
        with self._lock:
            return snapshot_path in self._path_counts

    def clear(self) -> None:
        self.reset_after_fork()

    def stats(self) -> dict[str, int]:
        self._ensure_current_process()
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "inflight": sum(gate.participants > 0 for gate in self._gates.values()),
            }


_VERIFIED_RUNTIME_SNAPSHOT_CACHE = _VerifiedRuntimeSnapshotCache(
    RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES,
    RUNTIME_SNAPSHOT_CACHE_MAX_BYTES,
)


def _cleanup_runtime_snapshot_dirs() -> None:
    """Remove every private parquet snapshot directory owned by this process."""
    with _RUNTIME_SNAPSHOT_LOCK:
        # A forked child inherits Python globals and atexit callbacks but must
        # never remove the parent's still-live generation snapshots.
        if os.getpid() != _RUNTIME_SNAPSHOT_PROCESS_ID:
            return
        snapshot_dirs = tuple(_RUNTIME_SNAPSHOT_DIRS)
        _RUNTIME_SNAPSHOT_DIRS.clear()
        _RUNTIME_SNAPSHOT_REFERENCES.clear()
        _RUNTIME_SNAPSHOT_PROCESS_PINS.clear()
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.clear()
    for snapshot_dir in snapshot_dirs:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    for parent in {snapshot_dir.parent for snapshot_dir in snapshot_dirs}:
        try:
            parent.rmdir()
        except OSError:
            # Another live process token, or a crash-left directory, still owns
            # entries beneath the shared snapshot parent.
            pass


def _ensure_runtime_snapshot_process_state() -> None:
    """Discard snapshot ownership inherited from another process."""
    global _RUNTIME_SNAPSHOT_PROCESS_ID, _RUNTIME_SNAPSHOT_PROCESS_TOKEN, _RUNTIME_SNAPSHOT_LOCK

    # This check deliberately precedes acquiring the lock. A forked child can
    # inherit a lock held by a parent thread which no longer exists in the child.
    if os.getpid() != _RUNTIME_SNAPSHOT_PROCESS_ID:
        _RUNTIME_SNAPSHOT_PROCESS_ID = os.getpid()
        _RUNTIME_SNAPSHOT_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
        _RUNTIME_SNAPSHOT_LOCK = threading.Lock()
        _RUNTIME_SNAPSHOT_DIRS.clear()
        _RUNTIME_SNAPSHOT_REFERENCES.clear()
        _RUNTIME_SNAPSHOT_PROCESS_PINS.clear()
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.reset_after_fork()


def _runtime_snapshot_dir(cache_dir: Path) -> Path:
    """Return this process's private snapshot directory beside *cache_dir*."""
    global _RUNTIME_SNAPSHOT_ATEXIT_REGISTERED

    _ensure_runtime_snapshot_process_state()
    snapshot_dir = cache_dir.parent / _RUNTIME_SNAPSHOT_DIRNAME / _RUNTIME_SNAPSHOT_PROCESS_TOKEN
    cache_root = _runtime_storage_root_for_cache(cache_dir)
    try:
        with _runtime_disk_budget_transaction(cache_root):
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            _ensure_runtime_owner_metadata(snapshot_dir)
    except BaseException:
        try:
            _remove_empty_runtime_owner_dir(snapshot_dir)
        except OSError:
            pass
        raise
    with _RUNTIME_SNAPSHOT_LOCK:
        _RUNTIME_SNAPSHOT_DIRS.add(snapshot_dir)
        if not _RUNTIME_SNAPSHOT_ATEXIT_REGISTERED:
            atexit.register(_cleanup_runtime_snapshot_dirs)
            _RUNTIME_SNAPSHOT_ATEXIT_REGISTERED = True
    return snapshot_dir


def _stream_copy_with_signature(source: Path, target: Path) -> tuple[int, str]:
    """Copy *source* to exclusive *target* with bounded memory and one hash pass."""
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as source_file, target.open("xb") as target_file:
            for chunk in iter(lambda: source_file.read(1 << 20), b""):
                target_file.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def _release_runtime_snapshot(snapshot_path: Path) -> None:
    """Release one transient or execution-owned reference to *snapshot_path*."""
    with _RUNTIME_SNAPSHOT_LOCK:
        references = _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0)
        if references <= 0:
            raise RuntimeError(f"runtime parquet snapshot was released twice: {snapshot_path}")
        if references > 1:
            _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = references - 1
            return
        del _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path]
        if (
            snapshot_path in _RUNTIME_SNAPSHOT_PROCESS_PINS
            or _VERIFIED_RUNTIME_SNAPSHOT_CACHE.is_pinned(snapshot_path)
        ):
            return
        try:
            snapshot_path.unlink()
        except FileNotFoundError:
            pass
        try:
            _remove_empty_runtime_owner_dir(snapshot_path.parent)
        except OSError:
            # Another content snapshot in this process directory is still live.
            pass


def _remove_unpinned_runtime_snapshot(snapshot_path: Path) -> None:
    """Drop an evicted cache pin once no execution/process lease remains."""
    with _RUNTIME_SNAPSHOT_LOCK:
        if (
            _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0) > 0
            or snapshot_path in _RUNTIME_SNAPSHOT_PROCESS_PINS
            or _VERIFIED_RUNTIME_SNAPSHOT_CACHE.is_pinned(snapshot_path)
        ):
            return
        try:
            snapshot_path.unlink()
        except FileNotFoundError:
            pass
        try:
            _remove_empty_runtime_owner_dir(snapshot_path.parent)
        except OSError:
            pass


def _retain_runtime_snapshot(snapshot_path: Path) -> None:
    """Convert a transient snapshot reference into its execution lifetime."""
    execution_context = current_execution_context()
    if execution_context is None:
        # Outside a managed execution there is no sound lifetime boundary for
        # arbitrary derived LazyFrames. Pin once for this process and consume the
        # transient reference without deleting the content-addressed path.
        with _RUNTIME_SNAPSHOT_LOCK:
            references = _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0)
            if references <= 0:
                raise RuntimeError(
                    f"runtime parquet snapshot has no transient owner: {snapshot_path}"
                )
            _RUNTIME_SNAPSHOT_PROCESS_PINS.add(snapshot_path)
            if references == 1:
                del _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path]
            else:
                _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = references - 1
        return

    # Keep the transient reference as this context's lease. The execution
    # context releases resources only after its collection and cleanup finish.
    try:
        execution_context.add_cleanup(lambda: _release_runtime_snapshot(snapshot_path))
    except BaseException:
        _release_runtime_snapshot(snapshot_path)
        raise


def _capture_runtime_snapshot(
    cache_dir: Path,
    parquet_path: Path,
    snapshot_dir: Path,
    expected_size: int,
    expected_digest: str,
    cache_key: tuple[str, int, str] | None,  # pragma: no mutate
    source_revision: _StrongFileRevision | None = None,  # pragma: no mutate
) -> Path | None:  # pragma: no mutate
    """Capture and verify one generation, retaining it only when proof permits."""
    # A stale-entry eviction can remove the now-empty process directory after
    # `_runtime_snapshot_dir` returned it for this operation.
    candidate = snapshot_dir / f".{uuid.uuid4().hex}.parquet.tmp"
    copied = False
    captured_revision: _StrongFileRevision | None = None  # pragma: no mutate
    try:
        cache_root = _runtime_storage_root_for_cache(cache_dir)
        with _runtime_disk_budget_transaction(cache_root):
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            _ensure_runtime_owner_metadata(snapshot_dir)
            try:
                os.link(parquet_path, candidate)
            except FileNotFoundError:
                raise
            except OSError as exc:
                copied = True
                logger.warning(
                    "json_shred_runtime_snapshot_copy_fallback",
                    cache_dir=str(cache_dir),
                    parquet_path=str(parquet_path),
                    error_type=type(exc).__name__,
                )
                # A regular Windows read handle blocks the rename-based publisher.
                # This fallback is serialized with same-process builders.
                with _publication._build_lock_for(cache_dir):
                    observed_size, observed_digest = _stream_copy_with_signature(
                        parquet_path, candidate
                    )
            else:
                # Linking itself may change inode metadata. Observe the captured
                # generation only after the link exists, then prove that revision
                # survived the complete verification hash below.
                captured_revision = _source_proof._strong_file_revision(candidate)
                observed = _source_proof._file_content_signature(candidate)
                observed_size = observed["size"]
                observed_digest = observed["sha256"]

        if (observed_size, observed_digest) != (expected_size, expected_digest):
            candidate.unlink(missing_ok=True)
            return None

        cacheable_revision: _StrongFileRevision | None = None  # pragma: no mutate
        if cache_key is not None and source_revision is not None:
            if copied:
                # Copying captures bytes rather than identity, so require the
                # visible generation to be unchanged over the copy interval.
                visible_after = _source_proof._strong_file_revision(parquet_path)
                if visible_after == source_revision:
                    cacheable_revision = source_revision
            else:
                captured_after = _source_proof._strong_file_revision(candidate)
                if captured_after != captured_revision:
                    candidate.unlink(missing_ok=True)
                    return None
                cacheable_revision = captured_after

        # The complete digest remains in the cache key and was verified above.
        # A fixed 128-bit filename address stays shorter than the temporary name,
        # avoiding a rename-only failure at the legacy Windows path boundary.
        snapshot_path = snapshot_dir / (
            f"{expected_digest[:_RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX]}.parquet"
        )
        # Publication, cache pinning, and the first execution lease are one
        # runtime-lock transaction. This establishes runtime-lock -> cache-lock
        # ordering and prevents another key's eviction from unlinking the file
        # between its cache admission and returned lease.
        with _RUNTIME_SNAPSHOT_LOCK:
            if snapshot_path.exists():
                if candidate.samefile(snapshot_path):
                    candidate.unlink(missing_ok=True)
                else:
                    # A content-address collision can be an independently
                    # captured inode (for example two source paths with equal
                    # bytes). Never substitute that file for the generation
                    # verified above: a later in-place edit through its source
                    # link must not corrupt this lease or its cached proof.
                    # Keep the collision name no longer than the already
                    # created candidate. Deep project paths can otherwise
                    # cross legacy Windows path-length handling during rename.
                    snapshot_path = snapshot_dir / f"{uuid.uuid4().hex}.parquet"
                    candidate.rename(snapshot_path)
            else:
                candidate.rename(snapshot_path)
            if cacheable_revision is not None:
                # The visible path must still name the stable captured inode at
                # admission time; a publisher race merely makes this call
                # transient rather than authorising reuse.
                visible_after = _source_proof._strong_file_revision(parquet_path)
                if copied:
                    if visible_after != cacheable_revision:
                        cacheable_revision = None
                elif (
                    visible_after is None
                    or visible_after.file_identity != cacheable_revision.file_identity
                    or visible_after.size != cacheable_revision.size
                ):
                    cacheable_revision = None
                else:
                    # The snapshot's native change token proves its own bytes
                    # were stable during hashing; the visible path's token is
                    # the generation gate for later cache hits.
                    cacheable_revision = visible_after
            evicted: list[Path] = []
            if cacheable_revision is not None:
                assert cache_key is not None
                _retained, evicted = _VERIFIED_RUNTIME_SNAPSHOT_CACHE.store(
                    cache_key, cacheable_revision, snapshot_path, expected_size
                )
            _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = (
                _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0) + 1
            )
        for evicted_path in evicted:
            _remove_unpinned_runtime_snapshot(evicted_path)
        return snapshot_path
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def _snapshot_cache_artifact(
    cache_dir: Path,
    parquet_path: Path,
    recorded_signature: Any,
) -> Path | None:  # pragma: no mutate
    with _publication._build_lock_for(cache_dir):
        return _snapshot_cache_artifact_locked(
            cache_dir,
            parquet_path,
            recorded_signature,
        )


def _snapshot_cache_artifact_locked(
    cache_dir: Path,
    parquet_path: Path,
    recorded_signature: Any,
) -> Path | None:  # pragma: no mutate
    """Pin and verify one cache artifact without materialising its payload.

    A hard link captures one rename-published generation atomically and does not
    duplicate its disk blocks. The link is hashed in bounded chunks, then moved
    to a content-addressed private path that lazy Polars plans can safely retain.
    Filesystems without hard-link support use an equivalently bounded copy while
    holding the same-process build lock; the copied bytes are hashed as written.
    ``None`` means the captured generation did not match its manifest signature.
    """
    # A child must discard inherited lock and cache state before touching any
    # cache-owned synchronization primitive. Runtime storage itself is allocated
    # only after the verified-generation lookup misses.
    _ensure_runtime_snapshot_process_state()
    expected = _source_proof._content_signature_parts(recorded_signature)
    assert expected is not None
    expected_size, expected_digest = expected
    visible_path = parquet_path.expanduser().resolve()
    path_key = os.path.normcase(str(visible_path))
    initial_revision = _source_proof._strong_file_revision(visible_path)
    if initial_revision is None:
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.warn_revision_unavailable_once(path_key, visible_path)
        snapshot_dir = _runtime_snapshot_dir(cache_dir)
        return _capture_runtime_snapshot(
            cache_dir, visible_path, snapshot_dir, expected_size, expected_digest, None
        )

    key = (path_key, expected_size, expected_digest)
    gate = _VERIFIED_RUNTIME_SNAPSHOT_CACHE.begin(key)
    try:
        with gate.lock:
            current_revision = _source_proof._strong_file_revision(visible_path)
            if current_revision is None:
                _VERIFIED_RUNTIME_SNAPSHOT_CACHE.warn_revision_unavailable_once(
                    path_key, visible_path
                )
                snapshot_dir = _runtime_snapshot_dir(cache_dir)
                return _capture_runtime_snapshot(
                    cache_dir, visible_path, snapshot_dir, expected_size, expected_digest, None
                )
            # Take the execution lease while the cache pin is still observed,
            # so a concurrent LRU eviction cannot unlink a just-hit snapshot.
            with _RUNTIME_SNAPSHOT_LOCK:
                hit, evicted = _VERIFIED_RUNTIME_SNAPSHOT_CACHE.get(key, current_revision)
                if hit is not None:
                    _RUNTIME_SNAPSHOT_REFERENCES[hit] = _RUNTIME_SNAPSHOT_REFERENCES.get(hit, 0) + 1
            for evicted_path in evicted:
                _remove_unpinned_runtime_snapshot(evicted_path)
            if hit is not None:
                return hit
            snapshot_dir = _runtime_snapshot_dir(cache_dir)
            return _capture_runtime_snapshot(
                cache_dir,
                visible_path,
                snapshot_dir,
                expected_size,
                expected_digest,
                key,
                current_revision,
            )
    finally:
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.finish(key, gate)


def _cleanup_direct_spill_dirs() -> None:
    """Remove direct-spill bundles owned by this process only."""
    with _DIRECT_SPILL_LOCK:
        if os.getpid() != _DIRECT_SPILL_PROCESS_ID:
            return
        spill_dirs = tuple(_DIRECT_SPILL_DIRS)
        _DIRECT_SPILL_DIRS.clear()
    for spill_dir in spill_dirs:
        try:
            shutil.rmtree(spill_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "json_direct_spill_cleanup_failed",
                path=str(spill_dir),
                error=repr(exc),
            )
    for parent in {spill_dir.parent for spill_dir in spill_dirs}:
        try:
            shutil.rmtree(parent)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "json_direct_spill_owner_cleanup_failed",
                path=str(parent),
                error=repr(exc),
            )


def _new_direct_spill_dir(cache_dir: Path) -> Path:
    """Create a private direct-runtime bundle outside both cache layers."""
    global _DIRECT_SPILL_ATEXIT_REGISTERED
    global _DIRECT_SPILL_PROCESS_ID, _DIRECT_SPILL_PROCESS_TOKEN, _DIRECT_SPILL_LOCK
    if os.getpid() != _DIRECT_SPILL_PROCESS_ID:
        # A child must forget inherited ownership before it can register its
        # own cleanup; its atexit callback must never remove parent spills.
        _DIRECT_SPILL_PROCESS_ID = os.getpid()
        _DIRECT_SPILL_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
        _DIRECT_SPILL_LOCK = threading.Lock()
        _DIRECT_SPILL_DIRS.clear()
    cache_root = _runtime_storage_root_for_cache(cache_dir)
    with _DIRECT_SPILL_LOCK:
        owner_dir = cache_root / _DIRECT_SPILL_DIRNAME / _DIRECT_SPILL_PROCESS_TOKEN
        spill_dir = owner_dir / uuid.uuid4().hex
        spill_created = False
        try:
            with _runtime_disk_budget_transaction(cache_root):
                owner_dir.mkdir(parents=True, exist_ok=True)
                _ensure_runtime_owner_metadata(owner_dir)
                spill_dir.mkdir(exist_ok=False)
                spill_created = True
        except BaseException as exc:
            if spill_created:
                try:
                    shutil.rmtree(spill_dir)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    exc.add_note(f"direct spill staging cleanup failed: {cleanup_exc}")
            try:
                _remove_empty_runtime_owner_dir(owner_dir)
            except OSError as cleanup_exc:
                exc.add_note(f"direct spill owner cleanup failed: {cleanup_exc}")
            raise
        _DIRECT_SPILL_DIRS.add(spill_dir)
        if not _DIRECT_SPILL_ATEXIT_REGISTERED:
            atexit.register(_cleanup_direct_spill_dirs)
            _DIRECT_SPILL_ATEXIT_REGISTERED = True
    return spill_dir


def _release_direct_spill_dir(spill_dir: Path) -> None:
    """Release a managed direct-spill bundle once its execution has ended."""
    with _DIRECT_SPILL_LOCK:
        _DIRECT_SPILL_DIRS.discard(spill_dir)
    try:
        shutil.rmtree(spill_dir)
    except FileNotFoundError:
        pass
    with _DIRECT_SPILL_LOCK:
        owner_dir = spill_dir.parent
        try:
            _remove_empty_runtime_owner_dir(owner_dir)
        except FileNotFoundError:
            pass
