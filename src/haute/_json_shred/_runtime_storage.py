"""Process-owned runtime storage: the disk budget and direct-spill leases.

Generated standalone code shreds a structured source into a private spill
directory below the process's cache root; orderly process exit removes every
remaining spill, and start-up recovery reaps those of dead processes."""

from __future__ import annotations

import atexit
import math
import os
import shutil
import stat as stat_module
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import orjson

from haute import _file_lock
from haute._env import int_env
from haute._file_lock import UnsafeCachePathError
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


_RUNTIME_OWNER_META_FILENAME = ".owner.json"


_RUNTIME_OWNER_FORMAT_VERSION = 1


_RUNTIME_STORAGE_BUDGET_DEFAULT_BYTES = 4 * 1024 * 1024 * 1024


_RUNTIME_STORAGE_ORPHAN_GRACE_DEFAULT_SECONDS = 60 * 60


_RUNTIME_STORAGE_RECOVERY_PROCESS_ID = os.getpid()


_RUNTIME_STORAGE_RECOVERED_ROOTS: set[Path] = set()


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
    return (cache_root / _DIRECT_SPILL_DIRNAME,)


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
        _file_lock._plain_directory_stat(runtime_parent)
        children = tuple(runtime_parent.iterdir())
    except (OSError, UnsafeCachePathError) as exc:
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
            _file_lock._plain_directory_stat(owner_dir)
        except (OSError, UnsafeCachePathError) as exc:
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
        _file_lock._remove_plain_cache_directory(owner_dir)
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
            _file_lock._plain_directory_stat(root)
        except (OSError, UnsafeCachePathError) as exc:
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
            _file_lock._plain_directory_stat(directory)
            children = tuple(directory.iterdir())
        except FileNotFoundError:
            return
        except (OSError, UnsafeCachePathError) as exc:
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
            if stat_module.S_ISDIR(child_stat.st_mode) and not _file_lock._is_reparse_point(
                child_stat
            ):
                _visit(child)
                continue
            if (
                not stat_module.S_ISREG(child_stat.st_mode)
                or stat_module.S_ISLNK(child_stat.st_mode)
                or _file_lock._is_reparse_point(child_stat)
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
    with _file_lock.file_lock_for(root / ".runtime-storage-budget.lock"):
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
