from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from haute._json_shred import _publication
from haute._json_shred._publication import JsonCacheRecoveryError, _build_lock_for, _cache_lock_path
from haute._worker_isolation import IsolatedWorkerStoppedError, IsolatedWorkerTimeoutError


def _hold_cache_lock(
    cache_dir: str,
    acquired: Any,
    release: Any,
) -> None:
    with _build_lock_for(Path(cache_dir)):
        acquired.put(os.getpid())
        if not release.wait(10):
            raise TimeoutError("test did not release cache lock")


def _finish_process(process: Any, release: Any) -> None:
    release.set()
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)


def _blocking_cache_worker(
    data_path: str,
    v2_config: dict[str, Any],
    cache_dir: str,
    staging_dir: str,
    budget: Any,
) -> Any:
    """Stand-in child: prove it is alive through *data_path*, leave a partial
    generation in the parent-chosen staging directory, then wait to be killed."""
    del v2_config, cache_dir, budget
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "partial.parquet").write_bytes(b"partial")
    Path(data_path).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(120)
    raise AssertionError("the worker outlived its parent's cancellation or timeout")


def _probe_cache_lock(
    cache_dir: str,
    child_pid: int,
    acquired: Any,
    release: Any,
) -> None:
    """Contend for the build lock from a separate process and report what the
    world looked like at the instant it was granted: whether the build child
    was still alive, and which staging siblings were still on disk. Both must
    already be settled — the transaction cleans up INSIDE the lock."""
    cache = Path(cache_dir)
    with _build_lock_for(cache):
        leftover = sorted(p.name for p in cache.parent.glob(f"{cache.name}.build-tmp-*"))
        acquired.put((os.getpid(), _pid_alive(child_pid), leftover))
        if not release.wait(10):
            raise TimeoutError("test did not release cache lock")


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0 == exited
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_cache_build_lock_serializes_independent_processes(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    first_acquired = ctx.Queue(maxsize=1)
    second_acquired = ctx.Queue(maxsize=1)
    release_first = ctx.Event()
    release_second = ctx.Event()
    cache_dir = tmp_path / "json_identity"
    first = ctx.Process(
        target=_hold_cache_lock,
        args=(str(cache_dir), first_acquired, release_first),
    )
    second = ctx.Process(
        target=_hold_cache_lock,
        args=(str(cache_dir), second_acquired, release_second),
    )
    try:
        first.start()
        assert first_acquired.get(timeout=5) == first.pid
        second.start()
        with pytest.raises(queue.Empty):
            second_acquired.get(timeout=0.2)
        release_first.set()
        assert second_acquired.get(timeout=5) == second.pid
    finally:
        _finish_process(first, release_first)
        _finish_process(second, release_second)

    assert first.exitcode == 0
    assert second.exitcode == 0


def test_outermost_lock_recovers_newest_backup_and_removes_crash_stages(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "json_identity"
    old_first = tmp_path / "json_identity.build-old-first"
    old_latest = tmp_path / "json_identity.build-old-latest"
    staged = tmp_path / "json_identity.build-tmp-stage"
    old_first.mkdir()
    (tmp_path / "json_identity.build-old-first" / "value.txt").write_text("old", encoding="utf-8")
    old_latest.mkdir()
    (tmp_path / "json_identity.build-old-latest" / "value.txt").write_text(
        "latest", encoding="utf-8"
    )
    staged.mkdir()
    (tmp_path / "json_identity.build-tmp-stage" / "value.txt").write_text(
        "partial", encoding="utf-8"
    )
    os.utime(old_first, ns=(100, 100))
    os.utime(old_latest, ns=(200, 200))

    with _build_lock_for(cache_dir):
        assert (cache_dir / "value.txt").read_text(encoding="utf-8") == "latest"

    assert not old_first.exists()
    assert not old_latest.exists()
    assert not staged.exists()


def test_recovery_removes_superseded_siblings_beside_published_generation(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "json_identity"
    old_generation = tmp_path / "json_identity.build-old-crashed"
    staged_generation = tmp_path / "json_identity.build-tmp-crashed"
    cache_dir.mkdir()
    (cache_dir / "current.txt").write_text("current", encoding="utf-8")
    old_generation.mkdir()
    staged_generation.mkdir()

    _publication._recover_cache_publication(cache_dir)

    assert (cache_dir / "current.txt").read_text(encoding="utf-8") == "current"
    assert not old_generation.exists()
    assert not staged_generation.exists()


def test_publication_sibling_scan_accepts_missing_parent(tmp_path: Path) -> None:
    assert _publication._publication_siblings(tmp_path / "missing" / "cache", "old") == []


def test_cache_recovery_fails_closed_on_ambiguous_or_non_plain_siblings(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "json_identity"
    first = tmp_path / "json_identity.build-old-first"
    second = tmp_path / "json_identity.build-old-second"
    first.mkdir()
    second.mkdir()
    os.utime(first, ns=(100, 100))
    os.utime(second, ns=(100, 100))

    with pytest.raises(JsonCacheRecoveryError, match="ambiguous"):
        with _build_lock_for(cache_dir):
            pass
    assert first.is_dir() and second.is_dir()

    first.rmdir()
    second.rmdir()
    hostile = tmp_path / "json_identity.build-tmp-hostile"
    hostile.write_text("not a directory", encoding="utf-8")
    with pytest.raises(JsonCacheRecoveryError, match="non-plain"):
        with _build_lock_for(cache_dir):
            pass
    assert hostile.read_text(encoding="utf-8") == "not a directory"


def test_reentrant_lock_does_not_recover_active_outer_staging_directory(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "json_identity"
    staged = tmp_path / "json_identity.build-tmp-active"

    with _build_lock_for(cache_dir):
        staged.mkdir()
        with _build_lock_for(cache_dir):
            assert staged.is_dir()
        assert staged.is_dir()

    with _build_lock_for(cache_dir):
        assert not staged.exists()


def test_cache_build_lock_rejects_a_non_plain_lock_path(tmp_path: Path) -> None:
    cache_dir = tmp_path / "json_identity"
    lock_path = _cache_lock_path(cache_dir)
    lock_path.mkdir()

    with pytest.raises(JsonCacheRecoveryError, match="lock path"):
        with _build_lock_for(cache_dir):
            pytest.fail("a non-plain lock path must never be trusted")

    assert lock_path.is_dir()


def test_open_cache_lock_file_fails_closed_on_open_error_and_inode_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "lock"

    def reject_open(*_args: Any, **_kwargs: Any) -> int:
        raise OSError("no")

    monkeypatch.setattr(os, "open", reject_open)
    with pytest.raises(JsonCacheRecoveryError, match="could not be opened safely"):
        _publication._open_cache_lock_file(lock_path)

    monkeypatch.undo()
    lock_path.write_bytes(b"x")
    original_fstat = os.fstat

    def different_inode(fd: int) -> Any:
        result = original_fstat(fd)
        return SimpleNamespace(
            st_mode=result.st_mode,
            st_file_attributes=getattr(result, "st_file_attributes", 0),
            st_dev=result.st_dev + 1,
            st_ino=result.st_ino + 1,
        )

    monkeypatch.setattr(os, "fstat", different_inode)
    with pytest.raises(JsonCacheRecoveryError, match="identity"):
        _publication._open_cache_lock_file(lock_path)


def test_open_cache_lock_file_closes_descriptor_when_path_disappears_mid_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "lock"
    lock_path.write_bytes(b"x")
    original_lstat = Path.lstat
    calls = 0

    def disappears(path: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        if path == lock_path:
            calls += 1
            if calls == 2:
                raise OSError("raced")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappears)
    with pytest.raises(JsonCacheRecoveryError, match="changed while"):
        _publication._open_cache_lock_file(lock_path)


def test_cache_path_ancestor_scan_handles_path_without_cache_root(tmp_path: Path) -> None:
    # A path named .haute_cache has no descendant cache-root boundary, so the
    # upward scan must stop safely at the filesystem root.
    _publication._assert_cache_path_ancestors_plain(tmp_path / ".haute_cache")


def test_cache_build_lock_nonblocking_timeout_and_release_error_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    lock = _publication._CacheBuildLock(cache_dir)
    handles: list[Any] = []

    class Handle:
        def __init__(self) -> None:
            self.closed = False

        def seek(self, *_args: Any) -> None:
            return None

        def tell(self) -> int:
            return 1

        def close(self) -> None:
            self.closed = True

    def open_handle(_path: Path) -> Handle:
        handle = Handle()
        handles.append(handle)
        return handle

    monkeypatch.setattr(_publication, "_open_cache_lock_file", open_handle)
    monkeypatch.setattr(_publication, "_assert_cache_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_publication, "_recover_cache_publication", lambda _path: None)
    monkeypatch.setattr(_publication, "_acquire_file_lock", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(_publication, "_release_file_lock", lambda _handle: None)
    assert lock.acquire(blocking=False) is False
    assert handles[-1].closed
    assert lock._depth == 0 and lock._owner_thread_id is None
    with pytest.raises(ValueError, match="timeout"):
        lock.acquire(blocking=False, timeout=0)

    monkeypatch.setattr(_publication, "_acquire_file_lock", lambda *_args, **_kwargs: True)
    with lock:
        assert lock.acquire() is True
        lock.release()
        assert lock._depth == 1
    assert handles[-1].closed
    with pytest.raises(RuntimeError, match="un-acquired"):
        lock.release()

    assert lock.acquire()
    monkeypatch.setattr(
        _publication, "_release_file_lock", lambda _handle: (_ for _ in ()).throw(OSError("unlock"))
    )
    with pytest.raises(OSError, match="unlock"):
        lock.release()
    assert lock._depth == 0 and lock._owner_thread_id is None


def test_file_lock_polling_handles_nonblocking_and_finite_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handle:
        def fileno(self) -> int:
            return 7

        def seek(self, *_args: Any) -> None:
            return None

    calls: list[object] = []
    if os.name == "nt":
        module = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)

        def contention(*_args: Any) -> None:
            calls.append(1)
            raise OSError(13, "busy", None, 33)

        module.locking = contention
        monkeypatch.setitem(sys.modules, "msvcrt", module)
    else:
        module = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)

        def contention(*_args: Any) -> None:
            calls.append(1)
            raise OSError(11, "busy")

        module.flock = contention
        monkeypatch.setitem(sys.modules, "fcntl", module)

    assert _publication._acquire_file_lock(Handle(), blocking=False) is False
    ticks = iter((0.0, 0.0, 0.02))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    assert _publication._acquire_file_lock(Handle(), timeout_seconds=0.01) is False
    assert len(calls) >= 2


def test_file_lock_posix_release_and_unexpected_error_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    module = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)

    def flock(fd: int, operation: int) -> None:
        calls.append((fd, operation))
        if operation != module.LOCK_UN:
            raise OSError(5, "disk failure")

    module.flock = flock
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "fcntl", module)

    class Handle:
        def fileno(self) -> int:
            return 11

    with pytest.raises(OSError, match="disk failure"):
        _publication._acquire_file_lock(Handle(), blocking=False)
    _publication._release_file_lock(Handle())
    assert calls[-1] == (11, module.LOCK_UN)


def test_file_lock_posix_success_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    operations: list[tuple[int, int]] = []
    module = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)
    module.flock = lambda fd, operation: operations.append((fd, operation))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "fcntl", module)

    class Handle:
        def fileno(self) -> int:
            return 12

    assert _publication._acquire_file_lock(Handle()) is True
    assert _publication._acquire_file_lock(Handle(), blocking=False) is True
    assert operations == [(12, module.LOCK_EX), (12, module.LOCK_EX | module.LOCK_NB)]


def test_file_lock_posix_contention_times_out_without_waiting_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)
    module.flock = lambda *_args: (_ for _ in ()).throw(OSError(11, "busy"))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "fcntl", module)
    ticks = iter((0.0, 0.0, 0.02))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    class Handle:
        def fileno(self) -> int:
            return 13

    assert _publication._acquire_file_lock(Handle(), timeout_seconds=0.01) is False


def test_file_lock_windows_unexpected_error_is_not_treated_as_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)
    module.locking = lambda *_args: (_ for _ in ()).throw(OSError(5, "disk failure"))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", module)

    class Handle:
        def fileno(self) -> int:
            return 14

        def seek(self, *_args: Any) -> None:
            return None

    with pytest.raises(OSError, match="disk failure"):
        _publication._acquire_file_lock(Handle(), blocking=False)


def test_file_lock_windows_success_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int, int]] = []
    module = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)
    module.locking = lambda *args: calls.append(args)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", module)

    class Handle:
        def fileno(self) -> int:
            return 15

        def seek(self, *_args: Any) -> None:
            return None

    handle = Handle()
    assert _publication._acquire_file_lock(handle, blocking=False) is True
    _publication._release_file_lock(handle)
    assert calls == [(15, module.LK_NBLCK, 1), (15, module.LK_UNLCK, 1)]


def test_file_lock_windows_recognises_contention_for_nonblocking_and_retrying_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows sharing violations either refuse immediately or are retried."""
    attempts: list[int] = []
    module = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)

    def locking(*_args: Any) -> None:
        attempts.append(1)
        if len(attempts) != 3:
            raise OSError(13, "busy", None, 33)

    module.locking = locking
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", module)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    class Handle:
        def fileno(self) -> int:
            return 16

        def seek(self, *_args: Any) -> None:
            return None

    handle = Handle()
    assert _publication._acquire_file_lock(handle, blocking=False) is False
    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    assert _publication._acquire_file_lock(handle, timeout_seconds=0.01) is True
    assert len(attempts) == 3


def test_build_lock_registry_discards_inherited_locks_after_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_lock = _publication._build_lock_for(tmp_path / "cache")
    monkeypatch.setattr(_publication, "_BUILD_LOCKS_PROCESS_ID", os.getpid() + 1)

    child_lock = _publication._build_lock_for(tmp_path / "cache")

    assert child_lock is not old_lock
    assert _publication._BUILD_LOCKS_PROCESS_ID == os.getpid()


def test_cache_build_lock_uses_thread_timeout_and_preserves_primary_exception_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _publication._CacheBuildLock(tmp_path / "cache")

    class RefusingLock:
        def acquire(self, **kwargs: Any) -> bool:
            assert kwargs == {"timeout": 0.0}
            return False

    lock._thread_lock = RefusingLock()  # type: ignore[assignment]
    assert lock.acquire(timeout=0.0) is False

    lock = _publication._CacheBuildLock(tmp_path / "other")
    handle = SimpleNamespace(seek=lambda *_args: None, tell=lambda: 1, close=lambda: None)
    monkeypatch.setattr(_publication, "_assert_cache_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_publication, "_open_cache_lock_file", lambda _path: handle)
    monkeypatch.setattr(_publication, "_acquire_file_lock", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(_publication, "_recover_cache_publication", lambda _path: None)
    monkeypatch.setattr(
        _publication, "_release_file_lock", lambda _handle: (_ for _ in ()).throw(OSError("unlock"))
    )
    with pytest.raises(ValueError, match="primary") as raised:
        with lock:
            raise ValueError("primary")
    assert any("unlock" in note for note in getattr(raised.value, "__notes__", []))


def test_cache_build_lock_preserves_recovery_error_when_unlock_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _publication._CacheBuildLock(tmp_path / "cache")

    class Handle:
        closed = False

        def seek(self, *_args: Any) -> None:
            return None

        def tell(self) -> int:
            return 1

        def close(self) -> None:
            self.closed = True

    handle = Handle()
    monkeypatch.setattr(_publication, "_assert_cache_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_publication, "_open_cache_lock_file", lambda _path: handle)
    monkeypatch.setattr(_publication, "_acquire_file_lock", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        _publication,
        "_recover_cache_publication",
        lambda _path: (_ for _ in ()).throw(ValueError("recovery failed")),
    )
    monkeypatch.setattr(
        _publication,
        "_release_file_lock",
        lambda _handle: (_ for _ in ()).throw(OSError("unlock failed")),
    )

    with pytest.raises(ValueError, match="recovery failed"):
        lock.acquire()

    assert handle.closed
    assert lock._thread_lock.acquire(blocking=False)
    lock._thread_lock.release()


def test_cache_build_lock_passes_remaining_finite_timeout_and_detects_lost_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _publication._CacheBuildLock(tmp_path / "cache")
    handle = SimpleNamespace(seek=lambda *_args: None, tell=lambda: 1, close=lambda: None)
    observed: list[float | None] = []
    monkeypatch.setattr(_publication, "_assert_cache_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_publication, "_open_cache_lock_file", lambda _path: handle)
    monkeypatch.setattr(_publication, "_recover_cache_publication", lambda _path: None)
    monkeypatch.setattr(_publication, "_release_file_lock", lambda _handle: None)
    monkeypatch.setattr(
        _publication,
        "_acquire_file_lock",
        lambda _handle, **kwargs: observed.append(kwargs["timeout_seconds"]) or True,
    )
    ticks = iter((10.0, 10.25))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    assert lock.acquire(timeout=1.0)
    assert observed == [pytest.approx(0.75)]
    lock.release()

    assert lock._thread_lock.acquire()
    lock._depth = 1
    lock._owner_thread_id = threading.get_ident()
    with pytest.raises(RuntimeError, match="lost"):
        lock.release()


def _run_build_transaction_until(
    tmp_path: Path,
    *,
    cancel: bool,
    timeout_seconds: float | None,
) -> tuple[BaseException | None, int, Path]:
    """Run the real transaction with a real spawned child, interrupt it, and report
    (raised exception, child pid, cache_dir) after the transaction thread finished.
    Asserts, WHILE the child is alive, that a separate process cannot take the build lock."""
    from unittest.mock import patch

    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionProfile
    from haute.routes import json_cache
    from haute.routes._isolated_worker_async import WorkerCancellationGate

    cache_dir = tmp_path / "json_identity"
    started = tmp_path / "child-started"
    gate = WorkerCancellationGate()
    budget = IsolatedExecutionBudget(
        operation="test",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=8 * 1024**3,
        config_key="test",
        budget_policy="test",
    )
    result: list[BaseException | None] = []

    def run() -> None:
        try:
            json_cache._json_cache_build_transaction(
                str(started), {"type": "object", "fields": {}}, cache_dir, budget, gate
            )
        except BaseException as exc:  # noqa: BLE001 - the test inspects the exact type
            result.append(exc)
        else:
            result.append(None)

    patches = [patch.object(json_cache, "_prepare_json_cache_worker", _blocking_cache_worker)]
    if timeout_seconds is not None:
        patches.append(patch.object(json_cache, "_build_timeout", return_value=timeout_seconds))

    for p in patches:
        p.start()
    patches_active = True
    try:
        thread = threading.Thread(target=run)
        thread.start()

        deadline = time.monotonic() + 60.0
        child_pid: int | None = None
        while time.monotonic() < deadline:
            if started.exists():
                try:
                    text = started.read_text(encoding="utf-8").strip()
                except OSError:
                    text = ""
                if text:
                    child_pid = int(text)
                    break
            time.sleep(0.05)
        assert child_pid is not None, "Child process did not report start"
        assert _pid_alive(child_pid)

        staging_matches = list(cache_dir.parent.glob(f"{cache_dir.name}.build-tmp-*"))
        assert len(staging_matches) == 1
        assert (staging_matches[0] / "partial.parquet").is_file()

        ctx = mp.get_context("spawn")
        acquired = ctx.Queue(maxsize=1)
        release = ctx.Event()
        probe = ctx.Process(
            target=_probe_cache_lock,
            args=(str(cache_dir), child_pid, acquired, release),
        )
        probe.start()
        try:
            with pytest.raises(queue.Empty):
                acquired.get(timeout=1.0)

            if cancel:
                gate.request()

            thread.join(timeout=60)
            assert not thread.is_alive()
            for p in reversed(patches):
                p.stop()
            patches_active = False

            # Granted only once the transaction let go — and by then the child
            # was already dead and its staging generation already discarded.
            probe_pid, child_alive_at_grant, leftover_at_grant = acquired.get(timeout=10)
            assert probe_pid == probe.pid
            assert child_alive_at_grant is False
            assert leftover_at_grant == []
        finally:
            _finish_process(probe, release)
        assert probe.exitcode == 0
    finally:
        if patches_active:
            for p in reversed(patches):
                p.stop()

    assert len(result) == 1
    return (result[0], child_pid, cache_dir)


def test_http_build_cancellation_terminates_the_child_and_cleans_staging_before_releasing_the_lock(
    tmp_path: Path,
) -> None:
    exc, child_pid, cache_dir = _run_build_transaction_until(
        tmp_path, cancel=True, timeout_seconds=None
    )
    assert isinstance(exc, IsolatedWorkerStoppedError) and exc.terminal_reason == "cancelled"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _pid_alive(child_pid):
        time.sleep(0.05)
    assert not _pid_alive(child_pid)
    assert list(cache_dir.parent.glob(f"{cache_dir.name}.build-tmp-*")) == []
    assert not cache_dir.exists()


def test_http_build_timeout_terminates_the_child_and_cleans_staging_before_releasing_the_lock(
    tmp_path: Path,
) -> None:
    exc, child_pid, cache_dir = _run_build_transaction_until(
        tmp_path, cancel=False, timeout_seconds=6.0
    )
    assert isinstance(exc, IsolatedWorkerTimeoutError) and exc.timeout_seconds == 6.0
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _pid_alive(child_pid):
        time.sleep(0.05)
    assert not _pid_alive(child_pid)
    assert list(cache_dir.parent.glob(f"{cache_dir.name}.build-tmp-*")) == []
    assert not cache_dir.exists()
