"""Mutation witnesses for the structured-source content proof.

Direct witnesses for the data-file signature (:func:`_data_file_signature`), its
memo, the native revisions behind it, and its content hash (:func:`_hash_file`).
The signature is an API-input table snapshot's source signature, so a mutation
that makes it wrongly report "unchanged" would serve stale rows silently; each
branch decision gets a discriminating witness.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from haute._json_shred import _source_proof
from haute._json_shred._source_proof import (
    _DATA_FILE_SIGNATURE_MEMO,
    _clear_data_file_signature_memo,
    _data_file_signature,
    _DataFileSignatureMemo,
    _hash_file,
)


@pytest.fixture(autouse=True)
def clear_data_file_signature_memo() -> Iterator[None]:
    """Keep global source-signature memo state out of unrelated witnesses."""
    _clear_data_file_signature_memo()
    yield
    _clear_data_file_signature_memo()


# ─── _hash_file — chunked content hash ─────────────────────────────


def test_hash_file_matches_sha256_of_content(tmp_path: Path) -> None:
    # A real, multi-byte file must hash to exactly sha256(content). Kills the
    # mutations that zero the read chunk size (``1 << 20`` -> ``1 // 20`` /
    # ``1 & 20`` / ``1 >> 20`` = 0 -> ``read(0)`` -> the iter sentinel fires
    # immediately -> empty hash) and the ZeroIterationForLoop (no chunks read).
    content = b"the quick brown fox jumps over the lazy dog\n" * 64
    p = tmp_path / "data.json"
    p.write_bytes(content)
    assert _hash_file(p) == hashlib.sha256(content).hexdigest()


def test_data_file_signature_rejects_a_file_changed_while_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw-data signatures use the same before/after stat guard as artifacts."""
    from haute._json_shred import _source_proof

    p = tmp_path / "data.json"
    p.write_bytes(b"[1]")
    real_hash_file = _source_proof._hash_file

    def racing_hash_file(path: Path) -> str:
        path.write_bytes(b"[1, 2]")
        return real_hash_file(path)

    monkeypatch.setattr(_source_proof, "_hash_file", racing_hash_file)

    with pytest.raises(OSError, match="changed while its signature was computed"):
        _data_file_signature(p)


# ─── _DataFileSignatureMemo — source-signature memo contract ───────


def test_data_file_signature_memoizes_unchanged_content_without_aliasing_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = _source_proof._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)
    first = _data_file_signature(path)
    second = _data_file_signature(path)
    assert first == second
    assert first is not second
    first["sha256"] = "poisoned"
    third = _data_file_signature(path)

    assert hashes == 1
    assert third["sha256"] == hashlib.sha256(b"[1]").hexdigest()
    assert third is not second
    assert len(_DATA_FILE_SIGNATURE_MEMO) == 1


def test_data_file_signature_rehashes_in_place_rewrite_with_restored_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"aaaa")
    original_stat = path.stat()
    real_hash_file = _source_proof._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)
    before = _data_file_signature(path)
    path.write_bytes(b"bbbb")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    after = _data_file_signature(path)

    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert hashes == 2
    assert after["sha256"] != before["sha256"]


def test_data_file_signature_rehashes_atomic_same_stat_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"aaaa")
    original_stat = path.stat()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"bbbb")
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    real_hash_file = _source_proof._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)
    before = _data_file_signature(path)
    os.replace(replacement, path)
    after = _data_file_signature(path)

    assert path.stat().st_size == original_stat.st_size
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert hashes == 2
    assert after["sha256"] != before["sha256"]


def test_data_file_signature_does_not_memoize_without_strong_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = _source_proof._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)

    assert _data_file_signature(path) == _data_file_signature(path)
    assert hashes == 2
    assert len(_DATA_FILE_SIGNATURE_MEMO) == 0


def test_data_file_signature_coalesces_simultaneous_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = _source_proof._hash_file
    hashing_started = threading.Event()
    allow_hash_to_finish = threading.Event()
    lock = threading.Lock()
    hashes = 0

    def blocking_hash_file(candidate: Path) -> str:
        nonlocal hashes
        with lock:
            hashes += 1
        hashing_started.set()
        assert allow_hash_to_finish.wait(timeout=5)
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_hash_file", blocking_hash_file)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_data_file_signature, path) for _ in range(8)]
        assert hashing_started.wait(timeout=5)
        allow_hash_to_finish.set()
        signatures = [future.result(timeout=5) for future in futures]

    assert hashes == 1
    assert all(signature == signatures[0] for signature in signatures)


def test_data_file_signature_does_not_cache_hashing_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = _source_proof._hash_file
    hashes = 0

    def flaky_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        if hashes == 1:
            raise OSError("temporary read failure")
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_hash_file", flaky_hash_file)
    with pytest.raises(OSError, match="temporary read failure"):
        _data_file_signature(path)

    assert _data_file_signature(path)["sha256"] == hashlib.sha256(b"[1]").hexdigest()
    assert hashes == 2


def test_data_file_signature_memo_is_bounded_lru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    paths: list[Path] = []
    for index in range(3):
        path = tmp_path / f"{index}.json"
        path.write_bytes(f"[{index}]".encode())
        paths.append(path)
    real_hash_file = _source_proof._hash_file
    hashes: list[Path] = []

    def counting_hash_file(candidate: Path) -> str:
        hashes.append(candidate)
        return real_hash_file(candidate)

    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)
    memo.get(paths[0])
    memo.get(paths[1])
    memo.get(paths[0])  # Refresh first, so second is the LRU entry.
    memo.get(paths[2])
    memo.get(paths[1])

    assert len(memo) == 2
    assert hashes == [paths[0], paths[1], paths[2], paths[1]]


def test_data_file_signature_memo_discards_entries_after_pid_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = _source_proof._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)
    memo.get(path)
    original_pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: original_pid + 1)
    memo.get(path)

    assert hashes == 2
    assert len(memo) == 1


@pytest.mark.parametrize("max_entries", [0, -1, True, 1.5, "2"])
def test_data_file_signature_memo_rejects_invalid_bounds(max_entries: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        _DataFileSignatureMemo(max_entries=max_entries)  # type: ignore[arg-type]


def test_posix_strong_file_revision_requires_regular_identified_file(tmp_path: Path) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = _source_proof._posix_strong_file_revision(path)

    assert revision is not None
    assert revision.file_identity[1] > 0
    assert revision.change_token > 0
    assert _source_proof._posix_strong_file_revision(tmp_path) is None

    actual = path.stat()
    no_inode = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_dev=actual.st_dev,
        st_ino=0,
        st_size=actual.st_size,
        st_mtime_ns=actual.st_mtime_ns,
        st_ctime_ns=actual.st_ctime_ns,
    )
    assert _source_proof._posix_strong_file_revision(SimpleNamespace(stat=lambda: no_inode)) is None


def test_posix_strong_file_revision_rejects_missing_ctime(tmp_path: Path) -> None:
    from haute._json_shred import _source_proof

    actual = tmp_path / "data.json"
    actual.write_bytes(b"[1]")
    observed = actual.stat()
    record = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=0,
    )

    assert _source_proof._posix_strong_file_revision(SimpleNamespace(stat=lambda: record)) is None


def test_strong_file_revision_dispatches_posix_without_constructing_windows_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    expected = SimpleNamespace(marker="posix")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(_source_proof, "_posix_strong_file_revision", lambda _path: expected)
    monkeypatch.setattr(
        _source_proof,
        "_windows_strong_file_revision",
        lambda _path: pytest.fail("Windows helper must not run"),
    )

    assert _source_proof._strong_file_revision(tmp_path / "data.json") is expected


def test_strong_file_revision_dispatches_windows_without_constructing_posix_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public revision gate selects the native Windows implementation."""
    from haute._json_shred import _source_proof

    expected = SimpleNamespace(marker="windows")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(_source_proof, "_windows_strong_file_revision", lambda _path: expected)
    monkeypatch.setattr(
        _source_proof,
        "_posix_strong_file_revision",
        lambda _path: pytest.fail("POSIX helper must not run"),
    )

    assert _source_proof._strong_file_revision(tmp_path / "data.json") is expected


class _NativeCallable:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)


def _windows_kernel32(
    *,
    handle: object = 1,
    fail_query: int | None = None,
    directory: int = 0,
    size: int = 4,
    change_time: int = 10,
    file_id: bytes = b"x" * 16,
    usn: int = 11,
    usn_major: int = 2,
    usn_record_length: int | None = None,
    usn_returned_length: int | None = None,
    fail_usn: bool = False,
) -> tuple[SimpleNamespace, list[object]]:
    closed: list[object] = []

    def get_information(_handle: object, info_class: int, target: object, _size: object) -> int:
        if info_class == fail_query:
            return 0
        if info_class == 0:
            info = ctypes.cast(target, ctypes.POINTER(_source_proof._WindowsFileBasicInfo)).contents
            info.LastWriteTime = _source_proof._WINDOWS_EPOCH_OFFSET_100NS + 2
            info.ChangeTime = change_time
        elif info_class == 1:
            info = ctypes.cast(
                target, ctypes.POINTER(_source_proof._WindowsFileStandardInfo)
            ).contents
            info.EndOfFile = size
            info.Directory = directory
        else:
            info = ctypes.cast(target, ctypes.POINTER(_source_proof._WindowsFileIdInfo)).contents
            info.VolumeSerialNumber = 7
            info.FileId.Identifier[:] = file_id
        return 1

    def device_io_control(
        _handle: object,
        _code: int,
        _input: object,
        _input_size: int,
        output: object,
        _output_size: int,
        returned: object,
        _overlapped: object,
    ) -> int:
        if fail_usn:
            return 0
        usn_offset = 24 if usn_major == 2 else 40 if usn_major == 3 else 24
        record_length = usn_record_length if usn_record_length is not None else usn_offset + 8
        buffer = ctypes.cast(output, ctypes.POINTER(ctypes.c_ubyte * 4096)).contents
        for index in range(len(buffer)):
            buffer[index] = 0
        for index, value in enumerate(record_length.to_bytes(4, "little", signed=False)):
            buffer[index] = value
        for index, value in enumerate(usn_major.to_bytes(2, "little", signed=False)):
            buffer[4 + index] = value
        if usn_offset + 8 <= len(buffer):
            for index, value in enumerate(usn.to_bytes(8, "little", signed=True)):
                buffer[usn_offset + index] = value
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = (
            usn_returned_length if usn_returned_length is not None else record_length
        )
        return 1

    return (
        SimpleNamespace(
            CreateFileW=_NativeCallable(lambda *_args: handle),
            GetFileInformationByHandleEx=_NativeCallable(get_information),
            DeviceIoControl=_NativeCallable(device_io_control),
            CloseHandle=_NativeCallable(lambda value: closed.append(value) or 1),
        ),
        closed,
    )


def test_windows_strong_file_revision_declines_unavailable_or_invalid_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    assert _source_proof._windows_strong_file_revision(tmp_path / "data.json") is None

    kernel32, _ = _windows_kernel32(handle=None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    assert _source_proof._windows_strong_file_revision(tmp_path / "data.json") is None


@pytest.mark.parametrize("failed_query", [0, 1, 18])
def test_windows_strong_file_revision_closes_handle_when_query_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_query: int
) -> None:
    from haute._json_shred import _source_proof

    kernel32, closed = _windows_kernel32(fail_query=failed_query)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert _source_proof._windows_strong_file_revision(tmp_path / "data.json") is None
    assert closed == [1]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: (_ for _ in ()).throw(OSError("no kernel32")),
        lambda: SimpleNamespace(),
    ],
)
def test_windows_strong_file_revision_declines_native_setup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: Any
) -> None:
    from haute._json_shred import _source_proof

    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: factory(),
        raising=False,
    )
    assert _source_proof._windows_strong_file_revision(tmp_path / "data.json") is None


@pytest.mark.parametrize(
    ("directory", "size", "file_id"),
    [
        (1, 4, b"x" * 16),
        (0, -1, b"x" * 16),
        (0, 4, b"\0" * 16),
    ],
)
def test_windows_strong_file_revision_rejects_invalid_native_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: int,
    size: int,
    file_id: bytes,
) -> None:
    from haute._json_shred import _source_proof

    kernel32, closed = _windows_kernel32(
        directory=directory,
        size=size,
        file_id=file_id,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert _source_proof._windows_strong_file_revision(tmp_path / "data.json") is None
    assert closed == [1]


def test_windows_strong_file_revision_returns_native_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    kernel32, closed = _windows_kernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    revision = _source_proof._windows_strong_file_revision(tmp_path / "data.json")
    assert revision is not None
    assert revision.file_identity == (7, b"x" * 16)
    assert revision.size == 4
    assert revision.mtime_ns == 200
    assert revision.change_token == 11
    assert closed == [1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fail_usn": True},
        {"usn_major": 1},
        {"usn": 0},
        {"usn": -1},
        {"usn_record_length": 4},
        {"usn_record_length": 32, "usn_returned_length": 8},
    ],
)
def test_windows_strong_file_revision_rejects_unavailable_or_malformed_usn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    from haute._json_shred import _source_proof

    kernel32, closed = _windows_kernel32(**kwargs)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert _source_proof._windows_strong_file_revision(tmp_path / "data.json") is None
    assert closed == [1]


def test_uncached_signature_rejects_hidden_identity_or_ctime_movement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._json_shred import _source_proof

    before = SimpleNamespace(st_dev=1, st_ino=2, st_size=4, st_mtime_ns=5, st_ctime_ns=6)
    for changed in (
        SimpleNamespace(st_dev=1, st_ino=3, st_size=4, st_mtime_ns=5, st_ctime_ns=6),
        SimpleNamespace(st_dev=1, st_ino=2, st_size=4, st_mtime_ns=5, st_ctime_ns=7),
    ):
        observations = iter((before, changed))
        path = SimpleNamespace(stat=lambda: next(observations))
        monkeypatch.setattr(_source_proof, "_hash_file", lambda _path: "digest")
        with pytest.raises(OSError, match="changed while its signature was computed"):
            _source_proof._uncached_data_file_signature(path)


def test_memo_falls_back_when_revision_disappears_inside_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = _source_proof._posix_strong_file_revision(path)
    assert revision is not None
    revisions = iter((revision, None))
    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: next(revisions))

    assert memo.get(path)["sha256"] == hashlib.sha256(b"[1]").hexdigest()
    assert len(memo) == 0


def test_unavailable_revision_warnings_are_once_per_bounded_retained_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    paths: list[Path] = []
    for index in range(3):
        path = tmp_path / f"{index}.json"
        path.write_bytes(b"[1]")
        paths.append(path)
    warnings: list[tuple[str, dict[str, object]]] = []
    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(
        _source_proof.logger,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )

    memo.get(paths[0])
    memo.get(paths[0])
    memo.get(paths[1])
    memo.get(paths[2])
    assert len(warnings) == 3
    assert {event for event, _fields in warnings} == {"json_source_signature_revision_unavailable"}
    assert {fields["action"] for _event, fields in warnings} == {"full_source_hash_per_operation"}
    assert len(memo._unavailable_warnings) == 2
    memo.get(paths[0])

    assert len(warnings) == 4
    assert len(memo._unavailable_warnings) == 2


def test_memo_clear_keeps_active_flight_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    memo = _DataFileSignatureMemo(max_entries=2)
    real_hash_file = _source_proof._hash_file
    started = threading.Event()
    release = threading.Event()
    hashes = 0

    def blocking_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        started.set()
        assert release.wait(timeout=5)
        return real_hash_file(candidate)

    monkeypatch.setattr(_source_proof, "_hash_file", blocking_hash_file)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(memo.get, path)
        assert started.wait(timeout=5)
        memo.clear()
        assert memo._load_gates
        release.set()
        signature = future.result(timeout=5)

    assert memo.get(path) == signature
    assert hashes == 1


def test_eviction_retains_active_stale_generation_gate_until_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    third = tmp_path / "third.json"
    first.write_bytes(b"[1]")
    second.write_bytes(b"[1]")
    third.write_bytes(b"[1]")
    first_revision = _source_proof._posix_strong_file_revision(first)
    second_revision = _source_proof._posix_strong_file_revision(second)
    third_revision = _source_proof._posix_strong_file_revision(third)
    assert first_revision is not None and second_revision is not None and third_revision is not None
    changed_first = _source_proof._StrongFileRevision(
        file_identity=first_revision.file_identity,
        size=first_revision.size,
        mtime_ns=first_revision.mtime_ns,
        change_token=first_revision.change_token + 1,
    )
    first_calls = 0

    def revisions(candidate: Path) -> object:
        nonlocal first_calls
        if candidate == first.resolve():
            first_calls += 1
            return first_revision if first_calls <= 3 else changed_first
        if candidate == second.resolve():
            return second_revision
        return third_revision

    real_hash_file = _source_proof._hash_file
    reload_started = threading.Event()
    release_reload = threading.Event()
    hashes = 0

    def hash_with_blocked_reload(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        if candidate == first.resolve() and hashes == 2:
            reload_started.set()
            assert release_reload.wait(timeout=5)
        return real_hash_file(candidate)

    memo = _DataFileSignatureMemo(max_entries=1)
    monkeypatch.setattr(_source_proof, "_strong_file_revision", revisions)
    monkeypatch.setattr(_source_proof, "_hash_file", hash_with_blocked_reload)
    memo.get(first)
    first_key = os.path.normcase(str(first.resolve()))
    with ThreadPoolExecutor(max_workers=1) as executor:
        reloading = executor.submit(memo.get, first)
        assert reload_started.wait(timeout=5)
        memo.get(second)
        assert first_key in memo._load_gates
        release_reload.set()
        reloading.result(timeout=5)
    memo.get(third)

    assert first_key not in memo._load_gates


# ─── _data_file_matches — stat-fast freshness with hash arbitration ──
