"""Mutation witnesses for the source freshness proof's native revisions.

The Windows (volume, file id, USN) and POSIX (device, inode, ctime) readers
behind :func:`observe_freshness`, and the content signature's refusal to reuse a
proof across a rewrite that size and mtime cannot see. A mutation that made a
revision wrongly match would serve stale rows silently, so each branch decision
gets a discriminating witness.
"""

from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from haute._hashing import content_hash
from haute._json_shred import _source_proof
from haute._json_shred._source_proof import file_signature


def test_hash_file_is_the_complete_content_hash(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(b"x" * (3 << 20))
    assert _source_proof._hash_file(path) == content_hash(path)


def test_file_signature_rehashes_in_place_rewrite_with_restored_mtime(
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
    before = file_signature(path)
    path.write_bytes(b"bbbb")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    after = file_signature(path)

    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert hashes == 2
    assert after.digest != before.digest


def test_file_signature_rehashes_atomic_same_stat_replacement(
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
    before = file_signature(path)
    os.replace(replacement, path)
    after = file_signature(path)

    assert path.stat().st_size == original_stat.st_size
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert hashes == 2
    assert after.digest != before.digest


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
