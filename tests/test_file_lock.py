"""Focused mutation witnesses for the one cross-process file-lock helper."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from haute import _file_lock


def _windows_lock_error(winerror: int) -> OSError:
    error = OSError(5, "busy")
    error.winerror = winerror  # type: ignore[attr-defined]
    return error


def test_open_lock_file_uses_private_binary_descriptor_and_closes_on_wrap_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "build.lock"
    opened: list[tuple[Path, int, int]] = []
    closed: list[int] = []
    original_open, original_close = os.open, os.close

    def record_open(path: Path, flags: int, mode: int) -> int:
        opened.append((path, flags, mode))
        return original_open(path, flags, mode)

    monkeypatch.setattr(os, "open", record_open)
    handle = _file_lock._open_lock_file(lock_path)
    try:
        assert opened == [
            (
                lock_path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOINHERIT", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        ]
        assert handle.mode == "rb+"
    finally:
        handle.close()

    monkeypatch.setattr(
        os, "fdopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("wrap"))
    )
    monkeypatch.setattr(os, "close", lambda fd: closed.append(fd) or original_close(fd))
    with pytest.raises(OSError, match="wrap"):
        _file_lock._open_lock_file(lock_path)
    assert len(closed) == 1


@pytest.mark.parametrize("path_identity, descriptor_identity", [((1, 2), (2, 1)), ((2, 1), (1, 2))])
def test_open_lock_file_rejects_identity_mismatch_in_either_direction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_identity: tuple[int, int],
    descriptor_identity: tuple[int, int],
) -> None:
    lock_path = tmp_path / "build.lock"
    closed: list[int] = []
    monkeypatch.setattr(os, "open", lambda *_args: 91)
    monkeypatch.setattr(os, "close", closed.append)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: SimpleNamespace(
            st_mode=0o100600, st_dev=path_identity[0], st_ino=path_identity[1]
        ),
    )
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=0o100600, st_dev=descriptor_identity[0], st_ino=descriptor_identity[1]
        ),
    )

    with pytest.raises(_file_lock.UnsafeCachePathError, match="identity"):
        _file_lock._open_lock_file(lock_path)
    assert closed == [91]


def test_open_lock_file_wraps_raw_descriptor_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "build.lock"
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    handle = BytesIO()
    identity = SimpleNamespace(st_mode=0o100600, st_dev=1, st_ino=2)
    monkeypatch.setattr(os, "open", lambda *_args: 92)
    monkeypatch.setattr(Path, "lstat", lambda _path: identity)
    monkeypatch.setattr(os, "fstat", lambda _fd: identity)
    monkeypatch.setattr(
        os,
        "fdopen",
        lambda *args, **kwargs: calls.append((args, kwargs)) or handle,
    )

    assert _file_lock._open_lock_file(lock_path) is handle
    assert calls == [((92, "r+b"), {"buffering": 0})]


def test_file_lock_platform_contracts_and_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    class Handle:
        def __init__(self) -> None:
            self.seeks: list[tuple[int, int]] = []

        def fileno(self) -> int:
            return 41

        def seek(self, offset: int, whence: int = 0) -> None:
            self.seeks.append((offset, whence))

    posix_calls: list[tuple[int, int]] = []
    fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)
    fcntl.flock = lambda fd, mode: posix_calls.append((fd, mode))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "fcntl", fcntl)
    assert _file_lock._acquire_file_lock(Handle()) is True
    assert _file_lock._acquire_file_lock(Handle(), blocking=False) is True
    _file_lock._release_file_lock(Handle())
    assert posix_calls == [(41, 1), (41, 3), (41, 4)]

    attempts: list[int] = []

    def busy(*_args: Any) -> None:
        attempts.append(1)
        raise OSError(11, "busy")

    fcntl.flock = busy
    ticks = iter((10.0, 10.0, 10.02))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    assert _file_lock._acquire_file_lock(Handle(), timeout_seconds=0.01) is False
    assert attempts == [1, 1] and sleeps == [0.01]
    fcntl.flock = lambda *_args: (_ for _ in ()).throw(OSError(5, "bad"))
    with pytest.raises(OSError, match="bad"):
        _file_lock._acquire_file_lock(Handle(), blocking=False)

    windows_calls: list[tuple[int, int, int]] = []
    msvcrt = SimpleNamespace(LK_NBLCK=7, LK_UNLCK=8)
    msvcrt.locking = lambda *args: windows_calls.append(args)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    handle = Handle()
    assert _file_lock._acquire_file_lock(handle, blocking=False) is True
    _file_lock._release_file_lock(handle)
    assert handle.seeks == [(0, 0), (0, 0)]
    assert windows_calls == [(41, 7, 1), (41, 8, 1)]


def test_file_lock_timeout_at_deadline_does_not_sleep_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)
    attempts: list[int] = []
    fcntl.flock = lambda *_args: attempts.append(1) or (_ for _ in ()).throw(OSError(11, "busy"))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "fcntl", fcntl)
    monkeypatch.setattr(time, "monotonic", iter((5.0, 5.0, 5.01)).__next__)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    assert (
        _file_lock._acquire_file_lock(SimpleNamespace(fileno=lambda: 43), timeout_seconds=0.01)
        is False
    )
    assert attempts == [1, 1]
    assert sleeps == [0.01]


def test_file_lock_posix_treats_permission_denied_as_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)
    fcntl.flock = lambda *_args: (_ for _ in ()).throw(OSError(13, "busy"))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setitem(sys.modules, "fcntl", fcntl)

    assert (
        _file_lock._acquire_file_lock(SimpleNamespace(fileno=lambda: 43), blocking=False) is False
    )


@pytest.mark.parametrize(
    "error",
    [
        _windows_lock_error(33),
        _windows_lock_error(36),
        OSError(11, "busy"),
        OSError(13, "busy"),
        OSError(36, "busy"),
    ],
)
def test_file_lock_windows_contention_errors_are_retried_until_deadline(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    calls: list[tuple[int, int, int]] = []
    msvcrt = SimpleNamespace(LK_NBLCK=9, LK_UNLCK=10)

    def busy(*args: int) -> None:
        calls.append(args)
        raise error

    msvcrt.locking = busy
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(time, "monotonic", iter((2.0, 2.0, 2.01)).__next__)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    handle = SimpleNamespace(fileno=lambda: 44, seek=lambda *args: seeks.append(args))
    seeks: list[tuple[int, ...]] = []

    assert _file_lock._acquire_file_lock(handle, timeout_seconds=0.01) is False
    assert calls == [(44, 9, 1), (44, 9, 1)]
    assert seeks == [(0,), (0,)]
    assert sleeps == [0.01]


def test_file_lock_windows_timeout_stops_after_overshooting_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []
    msvcrt = SimpleNamespace(LK_NBLCK=9, LK_UNLCK=10)
    msvcrt.locking = lambda *_args: attempts.append(1) or (_ for _ in ()).throw(OSError(11, "busy"))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(time, "monotonic", iter((2.0, 2.02)).__next__)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    handle = SimpleNamespace(fileno=lambda: 44, seek=lambda *_args: None)

    assert _file_lock._acquire_file_lock(handle, timeout_seconds=0.01) is False
    assert attempts == [1]
    assert sleeps == []


def test_file_lock_windows_propagates_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    msvcrt = SimpleNamespace(LK_NBLCK=9, LK_UNLCK=10)
    msvcrt.locking = lambda *_args: (_ for _ in ()).throw(OSError(5, "broken"))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)

    with pytest.raises(OSError, match="broken"):
        _file_lock._acquire_file_lock(
            SimpleNamespace(fileno=lambda: 45, seek=lambda *_args: None), blocking=False
        )


def test_path_ancestors_and_reparse_points(tmp_path: Path) -> None:
    assert _file_lock._is_reparse_point(SimpleNamespace(st_file_attributes=0x400))
    assert not _file_lock._is_reparse_point(SimpleNamespace(st_file_attributes=0x200))
    assert not _file_lock._is_reparse_point(SimpleNamespace())

    cache_root = tmp_path / ".haute_cache"
    cache_root.mkdir()
    non_directory = cache_root / "file"
    non_directory.write_text("x")
    with pytest.raises(_file_lock.UnsafeCachePathError, match="non-plain"):
        _file_lock._assert_path_ancestors_plain(non_directory / "lock")
    missing = cache_root / "missing" / "lock"
    _file_lock._assert_path_ancestors_plain(missing)


def test_ancestor_validation_visits_only_the_cache_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root = tmp_path / ".haute_cache"
    nested = cache_root / "one" / "two"
    nested.mkdir(parents=True)
    checked: list[Path] = []
    monkeypatch.setattr(
        _file_lock,
        "_plain_directory_stat",
        lambda path, **_kwargs: checked.append(path) or SimpleNamespace(),
    )

    _file_lock._assert_path_ancestors_plain(nested / "lock")
    assert checked == [cache_root, nested.parent, nested]

    checked.clear()
    _file_lock._assert_path_ancestors_plain(cache_root / "missing" / "lock")
    assert checked == [cache_root]

    outside = tmp_path / "outside" / "leaf"
    outside.parent.mkdir()
    checked.clear()
    _file_lock._assert_path_ancestors_plain(outside)
    assert checked == [outside.parent]


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [(0x3FF, False), (0x400, True), (0x401, True), (0x800, False)],
)
def test_reparse_point_mask_boundaries(attributes: int, expected: bool) -> None:
    assert _file_lock._is_reparse_point(SimpleNamespace(st_file_attributes=attributes)) is expected


def test_file_lock_object_tracks_owner_depth_and_remaining_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _file_lock.FileLock(tmp_path / "cache")
    handle = SimpleNamespace(seek=lambda *_args: None, tell=lambda: 1, close=lambda: None)
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(_file_lock, "_assert_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_file_lock, "_open_lock_file", lambda _path: handle)
    monkeypatch.setattr(_file_lock, "_release_file_lock", lambda _handle: None)
    monkeypatch.setattr(
        _file_lock,
        "_acquire_file_lock",
        lambda _handle, **kwargs: observed.append(kwargs) or True,
    )
    monkeypatch.setattr(threading, "get_ident", lambda: 101)
    ticks = iter((10.0, 10.25, 10.25, 10.25, 10.25))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))

    assert lock.acquire(timeout=1.0) is True
    assert observed == [{"blocking": True, "timeout_seconds": pytest.approx(0.75)}]
    assert lock.owned_by_current_thread() and lock._depth == 1 and lock._owner_thread_id == 101
    assert lock.acquire() is True and lock._depth == 2
    lock.release()
    assert lock._depth == 1 and lock._owner_thread_id == 101
    monkeypatch.setattr(threading, "get_ident", lambda: 202)
    assert not lock.owned_by_current_thread()
    with pytest.raises(RuntimeError, match="unacquired"):
        lock.release()
    monkeypatch.setattr(threading, "get_ident", lambda: 101)
    lock.release()
    assert lock._depth == 0 and lock._handle is None and lock._owner_thread_id is None

    unbounded = _file_lock.FileLock(tmp_path / "unbounded")
    monkeypatch.setattr(_file_lock, "_open_lock_file", lambda _path: handle)
    assert unbounded.acquire(timeout=-1) is True
    assert observed[-1]["timeout_seconds"] is None
    unbounded.release()


def test_file_lock_object_owner_state_and_nonblocking_timeout_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _file_lock.FileLock(tmp_path / "cache")
    monkeypatch.setattr(threading, "get_ident", lambda: 100)
    lock._owner_thread_id = 100
    lock._depth = 0
    assert not lock.owned_by_current_thread()
    lock._depth = 1
    assert lock.owned_by_current_thread()
    monkeypatch.setattr(threading, "get_ident", lambda: 101)
    assert not lock.owned_by_current_thread()

    class ThreadLock:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def acquire(self, **kwargs: Any) -> bool:
            self.calls.append(kwargs)
            return False

        def release(self) -> None:
            return None

    thread_lock = ThreadLock()
    lock._thread_lock = thread_lock  # type: ignore[assignment]
    assert lock.acquire(blocking=False, timeout=-1) is False
    assert thread_lock.calls == [{"blocking": False}]
    with pytest.raises(ValueError, match="timeout"):
        lock.acquire(blocking=False, timeout=0)


@pytest.mark.parametrize("initial_position, expected_writes", [(0, [b"\0"]), (1, [])])
def test_file_lock_object_initialises_only_empty_lock_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_position: int,
    expected_writes: list[bytes],
) -> None:
    lock = _file_lock.FileLock(tmp_path / f"cache-{initial_position}")
    writes: list[bytes] = []
    flushes: list[None] = []
    handle = SimpleNamespace(
        seek=lambda *_args: None,
        tell=lambda: initial_position,
        write=writes.append,
        flush=lambda: flushes.append(None),
        close=lambda: None,
    )
    monkeypatch.setattr(_file_lock, "_assert_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_file_lock, "_open_lock_file", lambda _path: handle)
    monkeypatch.setattr(_file_lock, "_acquire_file_lock", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(_file_lock, "_release_file_lock", lambda _handle: None)

    assert lock.acquire()
    assert writes == expected_writes
    assert flushes == ([None] if expected_writes else [])
    lock.release()


def test_file_lock_object_process_contention_releases_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Handle:
        def __init__(self) -> None:
            self.closed = False

        def seek(self, *_args: Any) -> None:
            return None

        def tell(self) -> int:
            return 1

        def close(self) -> None:
            self.closed = True

    lock = _file_lock.FileLock(tmp_path / "contention")
    contention_handle = Handle()
    monkeypatch.setattr(_file_lock, "_assert_path_ancestors_plain", lambda _path: None)
    monkeypatch.setattr(_file_lock, "_open_lock_file", lambda _path: contention_handle)
    monkeypatch.setattr(_file_lock, "_acquire_file_lock", lambda *_args, **_kwargs: False)
    released: list[Handle] = []
    monkeypatch.setattr(_file_lock, "_release_file_lock", released.append)

    assert lock.acquire(blocking=False) is False
    assert released == []
    assert contention_handle.closed
    # The thread lock is re-entrant, so only another thread proves it was freed.
    taken: list[bool] = []

    def take_and_release() -> None:
        acquired = lock._thread_lock.acquire(blocking=False)
        taken.append(acquired)
        if acquired:
            lock._thread_lock.release()

    other = threading.Thread(target=take_and_release)
    other.start()
    other.join(timeout=5)
    assert taken == [True]


def _hold_lock(lock_path: str, acquired: Any, release: Any) -> None:
    with _file_lock.file_lock_for(Path(lock_path)):
        acquired.put(os.getpid())
        if not release.wait(10):
            raise TimeoutError("test did not release the lock")


def _finish(process: Any, release: Any) -> None:
    release.set()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)


def test_the_lock_serializes_independent_processes(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    first_acquired, second_acquired = ctx.Queue(maxsize=1), ctx.Queue(maxsize=1)
    release_first, release_second = ctx.Event(), ctx.Event()
    lock_path = str(tmp_path / "shared.lock")
    first = ctx.Process(target=_hold_lock, args=(lock_path, first_acquired, release_first))
    second = ctx.Process(target=_hold_lock, args=(lock_path, second_acquired, release_second))
    try:
        first.start()
        assert first_acquired.get(timeout=30) == first.pid
        second.start()
        with pytest.raises(queue.Empty):
            second_acquired.get(timeout=0.5)
        # This process cannot take it either while the first holds it.
        assert _file_lock.FileLock(Path(lock_path)).acquire(blocking=False) is False
        release_first.set()
        assert second_acquired.get(timeout=30) == second.pid
    finally:
        _finish(first, release_first)
        _finish(second, release_second)
    assert (first.exitcode, second.exitcode) == (0, 0)


def test_one_lock_per_canonical_path_and_a_fresh_registry_after_fork(tmp_path: Path) -> None:
    lock = _file_lock.file_lock_for(tmp_path / "a.lock")

    assert _file_lock.file_lock_for(tmp_path / "." / "a.lock") is lock
    assert _file_lock.file_lock_for(tmp_path / "b.lock") is not lock
    original_pid = _file_lock._LOCKS_PROCESS_ID
    try:
        _file_lock._LOCKS_PROCESS_ID = -1  # as a forked child observes it
        assert _file_lock.file_lock_for(tmp_path / "a.lock") is not lock
    finally:
        _file_lock._LOCKS_PROCESS_ID = original_pid


def test_a_directory_at_the_lock_path_is_refused(tmp_path: Path) -> None:
    (tmp_path / "held.lock").mkdir()

    with pytest.raises(_file_lock.UnsafeCachePathError, match="plain regular file"):
        _file_lock.FileLock(tmp_path / "held.lock").acquire()


def test_a_lock_file_that_cannot_be_opened_or_restated_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "held.lock"
    real_open = os.open

    monkeypatch.setattr(os, "open", lambda *_args: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(_file_lock.UnsafeCachePathError, match="could not be opened safely"):
        _file_lock._open_lock_file(lock_path)

    monkeypatch.setattr(os, "open", real_open)
    real_lstat = Path.lstat
    calls: list[Path] = []

    def lstat_vanishing_after_open(path: Path) -> Any:
        calls.append(path)
        if path == lock_path and len(calls) > 1:
            raise FileNotFoundError(path)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat_vanishing_after_open)
    with pytest.raises(_file_lock.UnsafeCachePathError, match="changed while it was being opened"):
        _file_lock._open_lock_file(lock_path)


class _Handle:
    def __init__(self) -> None:
        self.closed = False

    def seek(self, *_args: object) -> None:
        pass

    def tell(self) -> int:
        return 1

    def close(self) -> None:
        self.closed = True


def test_a_failed_acquisition_closes_its_handle_and_frees_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = _Handle()
    released: list[object] = []
    monkeypatch.setattr(_file_lock, "_open_lock_file", lambda _path: handle)
    monkeypatch.setattr(
        _file_lock,
        "_acquire_file_lock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    def release_failing(value: object) -> None:
        released.append(value)
        raise OSError("not locked")

    monkeypatch.setattr(_file_lock, "_release_file_lock", release_failing)
    lock = _file_lock.FileLock(tmp_path / "held.lock")

    with pytest.raises(KeyboardInterrupt):
        lock.acquire()

    assert released == [handle] and handle.closed
    assert lock._depth == 0 and lock._handle is None
    # The thread lock was released: another thread can take it.
    taken: list[bool] = []

    def take_and_release() -> None:
        acquired = lock._thread_lock.acquire(blocking=False)
        taken.append(acquired)
        if acquired:
            lock._thread_lock.release()

    other = threading.Thread(target=take_and_release)
    other.start()
    other.join(timeout=5)
    assert taken == [True]


def test_a_failed_release_is_raised_or_noted_on_the_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_file_lock, "_open_lock_file", lambda _path: _Handle())
    monkeypatch.setattr(_file_lock, "_acquire_file_lock", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        _file_lock,
        "_release_file_lock",
        lambda _handle: (_ for _ in ()).throw(OSError("unlock failed")),
    )
    lock = _file_lock.FileLock(tmp_path / "held.lock")

    lock.acquire()
    with pytest.raises(OSError, match="unlock failed"):
        lock.release()
    assert lock._handle is None and lock._depth == 0

    with pytest.raises(ValueError, match="primary") as raised:
        with lock:
            raise ValueError("primary")
    assert raised.value.__notes__ == ["file lock release failed: unlock failed"]


def test_a_lock_that_lost_its_handle_says_so(tmp_path: Path) -> None:
    lock = _file_lock.FileLock(tmp_path / "held.lock")
    lock.acquire()
    handle, lock._handle = lock._handle, None
    try:
        with pytest.raises(RuntimeError, match="lost its file handle"):
            lock.release()
    finally:
        _file_lock._release_file_lock(handle)
        handle.close()
