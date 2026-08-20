"""Mutation witnesses for the v2 cache-validity boundary (W2 items 2.4/2.6).

Direct witnesses for the data-file freshness check (:func:`_data_file_matches`),
its content hash (:func:`_hash_file`), and the schema fingerprint
(:func:`_v2_fingerprint`). These gate whether a cached parquet set is served or
rebuilt; a mutation that makes them wrongly report "fresh" / "equal" would serve
stale rows silently, so each branch decision gets a discriminating witness.
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

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _DATA_FILE_SIGNATURE_MEMO,
    _clear_data_file_signature_memo,
    _content_signature_parts,
    _data_file_matches,
    _data_file_signature,
    _DataFileSignatureMemo,
    _file_content_matches,
    _file_content_signature,
    _hash_file,
    _v2_fingerprint,
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
    import haute._json_shred as shred_mod

    p = tmp_path / "data.json"
    p.write_bytes(b"[1]")
    real_hash_file = shred_mod._hash_file

    def racing_hash_file(path: Path) -> str:
        path.write_bytes(b"[1, 2]")
        return real_hash_file(path)

    monkeypatch.setattr(shred_mod, "_hash_file", racing_hash_file)

    with pytest.raises(OSError, match="changed while its signature was computed"):
        _data_file_signature(p)


# ─── _DataFileSignatureMemo — source-signature memo contract ───────


def test_data_file_signature_memoizes_unchanged_content_without_aliasing_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = shred_mod._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)
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
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"aaaa")
    original_stat = path.stat()
    real_hash_file = shred_mod._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)
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
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"aaaa")
    original_stat = path.stat()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"bbbb")
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    real_hash_file = shred_mod._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)
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
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = shred_mod._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)

    assert _data_file_signature(path) == _data_file_signature(path)
    assert hashes == 2
    assert len(_DATA_FILE_SIGNATURE_MEMO) == 0


def test_data_file_signature_coalesces_simultaneous_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = shred_mod._hash_file
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

    monkeypatch.setattr(shred_mod, "_hash_file", blocking_hash_file)
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
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = shred_mod._hash_file
    hashes = 0

    def flaky_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        if hashes == 1:
            raise OSError("temporary read failure")
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_hash_file", flaky_hash_file)
    with pytest.raises(OSError, match="temporary read failure"):
        _data_file_signature(path)

    assert _data_file_signature(path)["sha256"] == hashlib.sha256(b"[1]").hexdigest()
    assert hashes == 2


def test_data_file_signature_memo_is_bounded_lru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    paths = [tmp_path / f"{index}.json" for index in range(3)]
    for index, path in enumerate(paths):
        path.write_bytes(f"[{index}]".encode())
    real_hash_file = shred_mod._hash_file
    hashes: list[Path] = []

    def counting_hash_file(candidate: Path) -> str:
        hashes.append(candidate)
        return real_hash_file(candidate)

    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)
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
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    real_hash_file = shred_mod._hash_file
    hashes = 0

    def counting_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_hash_file(candidate)

    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(shred_mod, "_hash_file", counting_hash_file)
    memo.get(path)
    original_pid = os.getpid()
    monkeypatch.setattr(shred_mod.os, "getpid", lambda: original_pid + 1)
    memo.get(path)

    assert hashes == 2
    assert len(memo) == 1


@pytest.mark.parametrize("max_entries", [0, -1, True, 1.5, "2"])
def test_data_file_signature_memo_rejects_invalid_bounds(max_entries: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        _DataFileSignatureMemo(max_entries=max_entries)  # type: ignore[arg-type]


def test_posix_strong_file_revision_requires_regular_identified_file(tmp_path: Path) -> None:
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = shred_mod._posix_strong_file_revision(path)

    assert revision is not None
    assert revision.file_identity[1] > 0
    assert revision.change_token > 0
    assert shred_mod._posix_strong_file_revision(tmp_path) is None

    actual = path.stat()
    no_inode = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_dev=actual.st_dev,
        st_ino=0,
        st_size=actual.st_size,
        st_mtime_ns=actual.st_mtime_ns,
        st_ctime_ns=actual.st_ctime_ns,
    )
    assert shred_mod._posix_strong_file_revision(SimpleNamespace(stat=lambda: no_inode)) is None


def test_posix_strong_file_revision_rejects_missing_ctime(tmp_path: Path) -> None:
    import haute._json_shred as shred_mod

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

    assert shred_mod._posix_strong_file_revision(SimpleNamespace(stat=lambda: record)) is None


def test_strong_file_revision_dispatches_posix_without_constructing_windows_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    expected = SimpleNamespace(marker="posix")
    monkeypatch.setattr(shred_mod.os, "name", "posix")
    monkeypatch.setattr(shred_mod, "_posix_strong_file_revision", lambda _path: expected)
    monkeypatch.setattr(
        shred_mod,
        "_windows_strong_file_revision",
        lambda _path: pytest.fail("Windows helper must not run"),
    )

    assert shred_mod._strong_file_revision(tmp_path / "data.json") is expected


class _NativeCallable:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)


def _windows_kernel32(
    shred_mod: Any,
    *,
    handle: object = 1,
    fail_query: int | None = None,
    directory: int = 0,
    size: int = 4,
    change_time: int = 10,
    file_id: bytes = b"x" * 16,
) -> tuple[SimpleNamespace, list[object]]:
    closed: list[object] = []

    def get_information(_handle: object, info_class: int, target: object, _size: object) -> int:
        if info_class == fail_query:
            return 0
        if info_class == 0:
            info = ctypes.cast(target, ctypes.POINTER(shred_mod._WindowsFileBasicInfo)).contents
            info.LastWriteTime = shred_mod._WINDOWS_EPOCH_OFFSET_100NS + 2
            info.ChangeTime = change_time
        elif info_class == 1:
            info = ctypes.cast(target, ctypes.POINTER(shred_mod._WindowsFileStandardInfo)).contents
            info.EndOfFile = size
            info.Directory = directory
        else:
            info = ctypes.cast(target, ctypes.POINTER(shred_mod._WindowsFileIdInfo)).contents
            info.VolumeSerialNumber = 7
            info.FileId.Identifier[:] = file_id
        return 1

    return (
        SimpleNamespace(
            CreateFileW=_NativeCallable(lambda *_args: handle),
            GetFileInformationByHandleEx=_NativeCallable(get_information),
            CloseHandle=_NativeCallable(lambda value: closed.append(value) or 1),
        ),
        closed,
    )


def test_windows_strong_file_revision_declines_unavailable_or_invalid_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    monkeypatch.delattr(shred_mod.ctypes, "WinDLL", raising=False)
    assert shred_mod._windows_strong_file_revision(tmp_path / "data.json") is None

    kernel32, _ = _windows_kernel32(shred_mod, handle=None)
    monkeypatch.setattr(
        shred_mod.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False
    )
    assert shred_mod._windows_strong_file_revision(tmp_path / "data.json") is None


@pytest.mark.parametrize("failed_query", [0, 1, 18])
def test_windows_strong_file_revision_closes_handle_when_query_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_query: int
) -> None:
    import haute._json_shred as shred_mod

    kernel32, closed = _windows_kernel32(shred_mod, fail_query=failed_query)
    monkeypatch.setattr(
        shred_mod.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False
    )

    assert shred_mod._windows_strong_file_revision(tmp_path / "data.json") is None
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
    import haute._json_shred as shred_mod

    monkeypatch.setattr(
        shred_mod.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: factory(),
        raising=False,
    )
    assert shred_mod._windows_strong_file_revision(tmp_path / "data.json") is None


@pytest.mark.parametrize(
    ("directory", "size", "change_time", "file_id"),
    [
        (1, 4, 10, b"x" * 16),
        (0, -1, 10, b"x" * 16),
        (0, 4, 0, b"x" * 16),
        (0, 4, -1, b"x" * 16),
        (0, 4, 10, b"\0" * 16),
    ],
)
def test_windows_strong_file_revision_rejects_invalid_native_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: int,
    size: int,
    change_time: int,
    file_id: bytes,
) -> None:
    import haute._json_shred as shred_mod

    kernel32, closed = _windows_kernel32(
        shred_mod,
        directory=directory,
        size=size,
        change_time=change_time,
        file_id=file_id,
    )
    monkeypatch.setattr(
        shred_mod.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False
    )

    assert shred_mod._windows_strong_file_revision(tmp_path / "data.json") is None
    assert closed == [1]


def test_windows_strong_file_revision_returns_native_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    kernel32, closed = _windows_kernel32(shred_mod)
    monkeypatch.setattr(
        shred_mod.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False
    )

    revision = shred_mod._windows_strong_file_revision(tmp_path / "data.json")
    assert revision is not None
    assert revision.file_identity == (7, b"x" * 16)
    assert revision.size == 4
    assert revision.mtime_ns == 200
    assert revision.change_token == 10
    assert closed == [1]


def test_uncached_signature_rejects_hidden_identity_or_ctime_movement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._json_shred as shred_mod

    before = SimpleNamespace(st_dev=1, st_ino=2, st_size=4, st_mtime_ns=5, st_ctime_ns=6)
    for changed in (
        SimpleNamespace(st_dev=1, st_ino=3, st_size=4, st_mtime_ns=5, st_ctime_ns=6),
        SimpleNamespace(st_dev=1, st_ino=2, st_size=4, st_mtime_ns=5, st_ctime_ns=7),
    ):
        observations = iter((before, changed))
        path = SimpleNamespace(stat=lambda: next(observations))
        monkeypatch.setattr(shred_mod, "_hash_file", lambda _path: "digest")
        with pytest.raises(OSError, match="changed while its signature was computed"):
            shred_mod._uncached_data_file_signature(path)


def test_memo_falls_back_when_revision_disappears_inside_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = shred_mod._posix_strong_file_revision(path)
    assert revision is not None
    revisions = iter((revision, None))
    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: next(revisions))

    assert memo.get(path)["sha256"] == hashlib.sha256(b"[1]").hexdigest()
    assert len(memo) == 0


def test_unavailable_revision_warnings_are_once_per_bounded_retained_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._json_shred as shred_mod

    paths = [tmp_path / f"{index}.json" for index in range(3)]
    for path in paths:
        path.write_bytes(b"[1]")
    warnings: list[tuple[str, dict[str, object]]] = []
    memo = _DataFileSignatureMemo(max_entries=2)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(
        shred_mod.logger,
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
    import haute._json_shred as shred_mod

    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    memo = _DataFileSignatureMemo(max_entries=2)
    real_hash_file = shred_mod._hash_file
    started = threading.Event()
    release = threading.Event()
    hashes = 0

    def blocking_hash_file(candidate: Path) -> str:
        nonlocal hashes
        hashes += 1
        started.set()
        assert release.wait(timeout=5)
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_hash_file", blocking_hash_file)
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
    import haute._json_shred as shred_mod

    first, second, third = (tmp_path / name for name in ("first.json", "second.json", "third.json"))
    for path in (first, second, third):
        path.write_bytes(b"[1]")
    first_revision = shred_mod._posix_strong_file_revision(first)
    second_revision = shred_mod._posix_strong_file_revision(second)
    third_revision = shred_mod._posix_strong_file_revision(third)
    assert first_revision is not None and second_revision is not None and third_revision is not None
    changed_first = shred_mod._StrongFileRevision(
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

    real_hash_file = shred_mod._hash_file
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
    monkeypatch.setattr(shred_mod, "_strong_file_revision", revisions)
    monkeypatch.setattr(shred_mod, "_hash_file", hash_with_blocked_reload)
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


@pytest.mark.parametrize("replacement", [b"expanded", b""])
@pytest.mark.parametrize(
    ("signature", "message"),
    [
        (_data_file_signature, "data file changed"),
        (_file_content_signature, "file changed"),
    ],
)
def test_content_signatures_reject_growth_and_shrink_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: bytes,
    signature: Any,
    message: str,
) -> None:
    """Both before/after-stat guards reject a source altered during hashing."""
    import haute._json_shred as shred_mod

    path = tmp_path / "racing.json"
    path.write_bytes(b"same")
    real_hash_file = shred_mod._hash_file

    def racing_hash_file(candidate: Path) -> str:
        candidate.write_bytes(replacement)
        return real_hash_file(candidate)

    monkeypatch.setattr(shred_mod, "_hash_file", racing_hash_file)
    with pytest.raises(OSError, match=message):
        signature(path)


@pytest.mark.parametrize(
    "recorded",
    [
        {"size": -1, "sha256": "0" * 64},
        {"size": True, "sha256": "0" * 64},
        {"size": 0, "sha256": 0},
        {"size": 0, "sha256": "0" * 63},
        {"size": 0, "sha256": "0" * 65},
        {"size": 0, "sha256": "g" * 64},
    ],
)
def test_content_signature_parser_rejects_malformed_records(recorded: dict[str, Any]) -> None:
    assert _content_signature_parts(recorded) is None


def test_content_signature_parser_accepts_empty_payload() -> None:
    assert _content_signature_parts({"size": 0, "sha256": "0" * 64}) == (0, "0" * 64)


def test_file_content_matcher_requires_exact_content(tmp_path: Path) -> None:
    payload = b"middle"
    path = tmp_path / "artifact.parquet"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    exact = {"size": len(payload), "sha256": digest}
    lower = {"size": len(payload), "sha256": "0" * 64}
    upper = {"size": len(payload), "sha256": "f" * 64}

    assert _file_content_matches(exact, path) is True
    assert _file_content_matches("bad", path) is False
    assert _file_content_matches({**exact, "size": len(payload) - 1}, path) is False
    assert _file_content_matches({**exact, "size": len(payload) + 1}, path) is False
    assert _file_content_matches(lower, path) is False
    assert _file_content_matches(upper, path) is False
    assert _file_content_matches(exact, tmp_path / "missing.parquet") is False


# ─── _data_file_matches — stat-fast freshness with hash arbitration ──


def _sig(
    path: Path,
    *,
    size: int | None = None,
    mtime_ns: int | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    """A recorded signature, each field defaulting to the file's actual value."""
    st = path.stat()
    return {
        "size": st.st_size if size is None else size,
        "mtime_ns": st.st_mtime_ns if mtime_ns is None else mtime_ns,
        "sha256": _hash_file(path) if sha256 is None else sha256,
    }


def test_data_file_matches_rejects_non_dict_signature(tmp_path: Path) -> None:
    # L271: a missing / garbled (non-dict) signature is stale. Kills False->True.
    p = tmp_path / "d.json"
    p.write_bytes(b"x")
    assert _data_file_matches("not a dict", p) is False
    assert _data_file_matches(None, p) is False


def test_data_file_matches_missing_file_is_stale_not_raising(tmp_path: Path) -> None:
    # L274/L275: ``stat()`` raising OSError (FileNotFoundError ⊂ OSError) is
    # caught and returns False. Kills the ExceptionReplacer (a narrowed/changed
    # except would let it propagate) and the False->True (calling a deleted
    # source "fresh" would serve cached rows for a file that no longer exists).
    missing = tmp_path / "gone.json"
    sig = {"size": 1, "mtime_ns": 1, "sha256": "0" * 64}
    assert _data_file_matches(sig, missing) is False


def test_data_file_matches_size_mismatch_is_stale_both_directions(tmp_path: Path) -> None:
    # L276/L277: a size mismatch is stale, and the mtime is left matching so a
    # mutant can't sneak a True via the fast path. Two directions pin the
    # operator: recorded LARGER than real kills '!=' -> '>' (5 > 10 is False);
    # recorded SMALLER kills '!=' -> '<' (5 < 2 is False). Either also kills the
    # False->True on the size-stale return.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")  # size 5, mtime recorded as real below
    assert _data_file_matches(_sig(p, size=10), p) is False
    assert _data_file_matches(_sig(p, size=2), p) is False


def test_data_file_matches_verifies_hash_even_when_mtime_matches(tmp_path: Path) -> None:
    # The content identity stays authoritative — matching size+mtime never
    # short-circuits to fresh. A byte-changing rewrite that preserves both stat
    # fields (a deliberate os.utime restore, or a same-length edit the
    # filesystem's mtime resolution didn't record) must be seen as STALE, not
    # served as fresh. Real size+mtime with a deliberately WRONG recorded hash
    # is therefore stale.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    sig = _sig(p, sha256="f" * 64)  # real size+mtime, deliberately wrong hash
    assert _data_file_matches(sig, p) is False
    # And a correct hash with matching size+mtime is fresh, as before.
    assert _data_file_matches(_sig(p), p) is True


def test_data_file_matches_stale_when_content_rewritten_preserving_size_and_mtime(
    tmp_path: Path,
) -> None:
    # The exact stale-cache vector the fast path allowed: same byte length,
    # same mtime_ns, different bytes. Record the signature of the ORIGINAL
    # content, rewrite in place preserving stat, and assert the cache is stale.
    import os

    p = tmp_path / "d.json"
    p.write_bytes(b"aaaaa")
    st = p.stat()
    sig = _sig(p)  # signature of the original bytes
    p.write_bytes(b"bbbbb")  # same length, different content
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime exactly
    assert p.stat().st_size == sig["size"]
    assert p.stat().st_mtime_ns == sig["mtime_ns"]
    assert _data_file_matches(sig, p) is False


def test_data_file_matches_recorded_mtime_older_falls_to_hash(tmp_path: Path) -> None:
    # L278 directional: recorded mtime OLDER than the file (file is "newer").
    # '==' is False -> hash arbitrates -> wrong hash -> False. '>=' / 'is not'
    # would (wrongly) take the fast-path True, so correct=False kills them.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    older = _sig(p, mtime_ns=p.stat().st_mtime_ns - 1_000_000, sha256="f" * 64)
    assert _data_file_matches(older, p) is False


def test_data_file_matches_recorded_mtime_newer_falls_to_hash(tmp_path: Path) -> None:
    # L278 directional: recorded mtime NEWER than the file. '==' False -> hash ->
    # wrong -> False; '<=' would take the fast-path True. Kills '==' -> '<='.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    newer = _sig(p, mtime_ns=p.stat().st_mtime_ns + 1_000_000, sha256="f" * 64)
    assert _data_file_matches(newer, p) is False


def test_data_file_matches_hash_arbitrates_when_mtime_moved(tmp_path: Path) -> None:
    # L280: size matches but mtime moved (deploy copy / touch) -> the content
    # hash arbitrates. A correct hash validates despite the drift; a wrong one is
    # stale. The two wrong hashes bracket the real digest lexically so '>=' and
    # '<=' are pinned too (a sha256 hex digest sorts strictly between "0"*64 and
    # "f"*64). Kills '==' -> '!=', 'is', 'is not', '>', '<', '>=', '<='.
    p = tmp_path / "d.json"
    p.write_bytes(b"abcde")
    moved = p.stat().st_mtime_ns + 5_000_000  # content unchanged, mtime drifted
    assert _data_file_matches(_sig(p, mtime_ns=moved), p) is True
    assert _data_file_matches(_sig(p, mtime_ns=moved, sha256="0" * 64), p) is False
    assert _data_file_matches(_sig(p, mtime_ns=moved, sha256="f" * 64), p) is False


# ─── _v2_fingerprint — canonical content hash over the schema ──────


def _col(name: str, path: str, type_: str = "str", *, selected: bool = True) -> dict[str, Any]:
    return {"name": name, "path": path, "type": type_, "selected": selected, "levels": None}


def _table(
    path: str,
    label: str,
    columns: list[Any],
    *,
    emit: bool = True,
) -> dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "emit": emit,
        "row_id_column": None,
        "columns": columns,
    }


def test_v2_fingerprint_raises_on_non_dict_table() -> None:
    # W1 fix: a non-dict table entry must FAIL LOUD, not be silently skipped —
    # silently dropping it would let two structurally-different configs collapse
    # to the same fingerprint and serve a stale cache across a schema change.
    valid = _table("$[:]", "root", [_col("id", "$[:].id", "int")])
    with pytest.raises(ApiInputSchemaError, match=r"tables\[0\] is not a dict"):
        _v2_fingerprint({"tables": ["not a table", valid]})


def test_v2_fingerprint_raises_on_non_dict_column() -> None:
    # W1 fix: same, one level down — a non-dict column entry fails loud rather
    # than being silently dropped from the fingerprint.
    junk = _table("$[:]", "root", ["not a column", _col("id", "$[:].id", "int")])
    with pytest.raises(ApiInputSchemaError, match=r"columns\[0\] is not a dict"):
        _v2_fingerprint({"tables": [junk]})


def test_v2_fingerprint_is_invariant_to_column_input_order() -> None:
    # L144: columns are sorted by (path, name) before hashing, so the editor's
    # row order doesn't move the fingerprint. Kills 'or' -> 'and' on the sort key
    # (``path and ""`` collapses every key to "" -> a no-op sort -> the column
    # list stays in input order -> an order-dependent, unstable fingerprint).
    cols_ab = [_col("a", "$[:].a"), _col("b", "$[:].b")]
    cols_ba = [_col("b", "$[:].b"), _col("a", "$[:].a")]
    fp_ab = _v2_fingerprint({"tables": [_table("$[:]", "root", cols_ab)]})
    fp_ba = _v2_fingerprint({"tables": [_table("$[:]", "root", cols_ba)]})
    assert fp_ab == fp_ba


def test_v2_fingerprint_is_invariant_to_table_input_order() -> None:
    # L154: tables are sorted by path before hashing. Kills 'or' -> 'and' on the
    # table sort key (same no-op-sort failure, one level up).
    t1 = _table("$[:].a[:]", "a", [_col("x", "$[:].a[:].x")], emit=False)
    t2 = _table("$[:]", "root", [_col("y", "$[:].y")])
    assert _v2_fingerprint({"tables": [t1, t2]}) == _v2_fingerprint({"tables": [t2, t1]})
