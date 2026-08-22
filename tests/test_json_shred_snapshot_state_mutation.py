"""State-transition mutation witnesses for private runtime parquet snapshots."""

from __future__ import annotations

import atexit
import hashlib
import os
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from haute._json_shred import _runtime_storage, _source_proof


@pytest.fixture
def snapshot_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make module-global snapshot ownership deterministic and test-local."""
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_DIRS", set())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_REFERENCES", {})
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_PINS", set())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_LOCK", threading.Lock())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_TOKEN", "mutation-owner")
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_ATEXIT_REGISTERED", False)
    monkeypatch.setattr(
        _runtime_storage,
        "_VERIFIED_RUNTIME_SNAPSHOT_CACHE",
        _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=8, max_bytes=1024),
    )


class _OpenFile:
    def __init__(self, reads: list[bytes], writes: list[bytes], fail: str | None = None) -> None:
        self.reads = iter(reads)
        self.writes = writes
        self.fail = fail
        self.requests: list[int] = []

    def __enter__(self) -> _OpenFile:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, size: int) -> bytes:
        self.requests.append(size)
        if self.fail == "read":
            raise OSError("read failed")
        return next(self.reads)

    def write(self, chunk: bytes) -> int:
        if self.fail == "write":
            raise OSError("write failed")
        self.writes.append(chunk)
        return len(chunk)


class _FakePath:
    def __init__(self, file: _OpenFile) -> None:
        self.file = file
        self.modes: list[str] = []
        self.unlinks: list[bool] = []

    def open(self, mode: str) -> _OpenFile:
        self.modes.append(mode)
        return self.file

    def unlink(self, *, missing_ok: bool = False) -> None:
        self.unlinks.append(missing_ok)


@pytest.mark.parametrize("failure", [None, "read", "write"])
def test_stream_copy_requests_exact_chunks_hashes_in_order_and_cleans_up(
    failure: str | None,
) -> None:
    writes: list[bytes] = []
    source_file = _OpenFile(
        [b"first", b"second", b""], writes, failure if failure == "read" else None
    )
    target_file = _OpenFile([], writes, failure if failure == "write" else None)
    source, target = _FakePath(source_file), _FakePath(target_file)

    if failure:
        with pytest.raises(OSError, match=failure):
            _runtime_storage._stream_copy_with_signature(source, target)  # type: ignore[arg-type]
        assert target.unlinks == [True]
        return

    size, digest = _runtime_storage._stream_copy_with_signature(source, target)  # type: ignore[arg-type]
    assert source.modes == ["rb"]
    assert target.modes == ["xb"]
    assert source_file.requests == [1 << 20, 1 << 20, 1 << 20]
    assert writes == [b"first", b"second"]
    assert (size, digest) == (11, hashlib.sha256(b"firstsecond").hexdigest())
    assert target.unlinks == []


@pytest.mark.parametrize("invalid_references", [-1, 0])
def test_release_snapshot_reference_truth_table_and_pins(
    tmp_path: Path,
    snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
    invalid_references: int,
) -> None:
    path = tmp_path / "owner" / "snapshot.parquet"
    path.parent.mkdir()
    path.write_bytes(b"data")
    if invalid_references:
        _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = invalid_references
    with pytest.raises(RuntimeError, match="released twice"):
        _runtime_storage._release_runtime_snapshot(path)

    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = 4
    _runtime_storage._release_runtime_snapshot(path)
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] == 3
    assert path.exists()
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = 1
    _runtime_storage._release_runtime_snapshot(path)
    assert path not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES
    assert not path.exists() and not path.parent.exists()

    path.parent.mkdir()
    path.write_bytes(b"pinned")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = 1
    _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS.add(path)
    _runtime_storage._release_runtime_snapshot(path)
    assert path.exists() and path not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES

    _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS.clear()
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = 1
    monkeypatch.setattr(
        _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE, "is_pinned", lambda value: value == path
    )
    _runtime_storage._release_runtime_snapshot(path)
    assert path.exists() and path not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES


@pytest.mark.parametrize("references", [-1, 0, 1, 2, 3])
def test_unmanaged_retain_pins_once_and_consumes_exactly_one_reference(
    tmp_path: Path, snapshot_state: None, references: int
) -> None:
    path = tmp_path / "snapshot.parquet"
    if references:
        _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = references
    if references <= 0:
        with pytest.raises(RuntimeError, match="no transient owner"):
            _runtime_storage._retain_runtime_snapshot(path)
        return
    _runtime_storage._retain_runtime_snapshot(path)
    assert path in _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS
    if references == 1:
        assert path not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES
    else:
        assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] == references - 1


def test_managed_retain_registers_one_release_and_failure_releases_once(
    tmp_path: Path, snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.parquet"
    path.write_bytes(b"data")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = 1

    class Context:
        callbacks: list[Any] = []

        def add_cleanup(self, callback: Any) -> None:
            self.callbacks.append(callback)

    context = Context()
    monkeypatch.setattr(_runtime_storage, "current_execution_context", lambda: context)
    _runtime_storage._retain_runtime_snapshot(path)
    assert len(context.callbacks) == 1 and _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] == 1
    context.callbacks[0]()
    assert not path.exists() and path not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES

    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"data")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[path] = 1
    releases = 0
    original_release = _runtime_storage._release_runtime_snapshot

    class BrokenContext:
        def add_cleanup(self, _callback: Any) -> None:
            raise ValueError("registration failed")

    def counting_release(value: Path) -> None:
        nonlocal releases
        releases += 1
        original_release(value)

    monkeypatch.setattr(_runtime_storage, "current_execution_context", lambda: BrokenContext())
    monkeypatch.setattr(_runtime_storage, "_release_runtime_snapshot", counting_release)
    with pytest.raises(ValueError, match="registration failed"):
        _runtime_storage._retain_runtime_snapshot(path)
    assert releases == 1 and not path.exists()


def test_snapshot_cache_finish_store_boundaries_shared_paths_and_lru() -> None:
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=2, max_bytes=10)
    key, other = ("a", 1, "x"), ("b", 1, "x")
    gate = cache.begin(key)
    gate.participants = 2
    cache.finish(key, gate)
    assert gate.participants == 1 and cache._gates[key] is gate
    cache.finish(key, gate)
    assert key not in cache._gates
    retained_gate, wrong_gate = (
        cache.begin(key),
        _runtime_storage._VerifiedRuntimeSnapshotLoadGate(),
    )
    wrong_gate.participants = 1
    cache._entries[key] = _runtime_storage._VerifiedRuntimeSnapshot(None, Path("kept"), 1)  # type: ignore[arg-type]
    cache.finish(key, wrong_gate)
    assert cache._gates[key] is retained_gate
    cache.finish(key, retained_gate)
    assert cache._gates[key] is retained_gate

    negative_gate = _runtime_storage._VerifiedRuntimeSnapshotLoadGate()
    cache._gates[other] = negative_gate
    cache.finish(other, negative_gate)
    assert negative_gate.participants == -1 and cache._gates[other] is negative_gate

    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=2, max_bytes=10)
    revision = _source_proof._StrongFileRevision((1, 2), 5, 0, 0)
    shared = Path("shared")
    assert cache.store(key, revision, shared, 10) == (True, [])
    assert cache.store(other, revision, shared, 10) == (True, [])
    assert cache.stats()["bytes"] == 10
    assert cache.store(("big", 1, "x"), revision, Path("big"), 11) == (False, [])
    retained, evicted = cache.store(("third", 1, "x"), revision, Path("third"), 6)
    assert retained and evicted == [shared]
    assert cache.stats() == {"entries": 1, "bytes": 6, "inflight": 0}


@pytest.mark.parametrize("participants,has_entry", [(1, False), (0, True)])
def test_snapshot_cache_store_retains_each_independent_live_gate_reason(
    participants: int, has_entry: bool
) -> None:
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=2, max_bytes=10)
    revision = _source_proof._StrongFileRevision((1, 2), 1, 1, 1)
    live_key, stored_key = ("live", 1, "x"), ("stored", 1, "x")
    gate = _runtime_storage._VerifiedRuntimeSnapshotLoadGate()
    gate.participants = participants
    cache._gates[live_key] = gate
    if has_entry:
        cache._entries[live_key] = _runtime_storage._VerifiedRuntimeSnapshot(
            revision, Path("live"), 1
        )
        cache._path_counts[Path("live")] = 1
        cache._path_sizes[Path("live")] = 1
        cache._bytes = 1

    retained, _evicted = cache.store(stored_key, revision, Path("stored"), 1)

    assert retained
    assert cache._gates[live_key] is gate


def test_runtime_snapshot_dir_registers_atexit_once_and_cleans_owner_on_budget_error(
    tmp_path: Path, snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    registrations: list[Any] = []
    monkeypatch.setattr(atexit, "register", registrations.append)
    monkeypatch.setattr(
        _runtime_storage, "_runtime_disk_budget_transaction", lambda *_a, **_k: nullcontext()
    )
    first = _runtime_storage._runtime_snapshot_dir(tmp_path / "cache")
    second = _runtime_storage._runtime_snapshot_dir(tmp_path / "cache")
    assert first == second and _runtime_storage._RUNTIME_SNAPSHOT_DIRS == {first}
    assert registrations == [_runtime_storage._cleanup_runtime_snapshot_dirs]

    removed: list[Path] = []
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_disk_budget_transaction",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("budget exceeded")),
    )
    monkeypatch.setattr(_runtime_storage, "_remove_empty_runtime_owner_dir", removed.append)
    with pytest.raises(RuntimeError, match="budget exceeded"):
        _runtime_storage._runtime_snapshot_dir(tmp_path / "other-cache")
    assert removed == [tmp_path / _runtime_storage._RUNTIME_SNAPSHOT_DIRNAME / "mutation-owner"]


@pytest.mark.parametrize("expected_size, expected_digest", [(4, "0" * 64), (3, "f" * 64)])
def test_capture_mismatch_removes_candidate(
    tmp_path: Path, snapshot_state: None, expected_size: int, expected_digest: str
) -> None:
    source = tmp_path / "source.parquet"
    directory = tmp_path / "snapshots"
    source.write_bytes(b"data")
    assert (
        _runtime_storage._capture_runtime_snapshot(
            tmp_path / "cache", source, directory, expected_size, expected_digest, None
        )
        is None
    )
    assert not list(directory.glob("*.tmp")) and not list(directory.glob("*.parquet"))


def test_capture_copy_fallback_logs_exact_fields_and_only_caches_stable_visible_revision(
    tmp_path: Path, snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    directory = tmp_path / "snapshots"
    payload = b"copy-data"
    source.write_bytes(payload)
    revision = _source_proof._StrongFileRevision((1, 2), len(payload), 1, 1)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(os, "link", lambda *_a: (_ for _ in ()).throw(OSError("no links")))
    monkeypatch.setattr(
        _runtime_storage.logger, "warning", lambda event, **fields: calls.append((event, fields))
    )
    monkeypatch.setattr(
        _source_proof,
        "_strong_file_revision",
        lambda _path: _source_proof._StrongFileRevision(
            revision.file_identity,
            revision.size,
            revision.mtime_ns,
            revision.change_token,
        ),
    )
    digest = hashlib.sha256(payload).hexdigest()
    snapshot = _runtime_storage._capture_runtime_snapshot(
        tmp_path / "cache", source, directory, len(payload), digest, ("k", 1, digest), revision
    )
    assert (
        snapshot is not None
        and _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 1
    )
    assert calls == [
        (
            "json_shred_runtime_snapshot_copy_fallback",
            {
                "cache_dir": str(tmp_path / "cache"),
                "parquet_path": str(source),
                "error_type": "OSError",
            },
        )
    ]
    _runtime_storage._release_runtime_snapshot(snapshot)


@pytest.mark.parametrize("moved_size", [8, 10])
def test_capture_copy_fallback_rejects_cache_when_visible_identity_or_size_moves(
    tmp_path: Path,
    snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
    moved_size: int,
) -> None:
    source = tmp_path / "source.parquet"
    directory = tmp_path / "snapshots"
    payload = b"copy-race"
    source.write_bytes(payload)
    revision = _source_proof._StrongFileRevision((1, 2), len(payload), 1, 1)
    moved = _source_proof._StrongFileRevision(revision.file_identity, moved_size, 2, 2)
    observed = iter((revision, moved))
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(os, "link", lambda *_a: (_ for _ in ()).throw(OSError("no links")))
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: next(observed))
    snapshot = _runtime_storage._capture_runtime_snapshot(
        tmp_path / "cache", source, directory, len(payload), digest, ("k", 1, digest), revision
    )
    assert (
        snapshot is not None
        and _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0
    )
    _runtime_storage._release_runtime_snapshot(snapshot)
    assert not snapshot.exists()


@pytest.mark.parametrize("moved_size", [8, 10])
def test_capture_hardlink_rejects_cache_when_visible_size_moves_both_directions(
    tmp_path: Path,
    snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
    moved_size: int,
) -> None:
    source = tmp_path / "source.parquet"
    directory = tmp_path / "snapshots"
    payload = b"copy-race"
    source.write_bytes(payload)
    captured = _source_proof._StrongFileRevision((1, 2), len(payload), 1, 1)
    moved = _source_proof._StrongFileRevision(captured.file_identity, moved_size, 2, 2)

    def revision_for(path: Path) -> _source_proof._StrongFileRevision:
        return moved if path == source else captured

    monkeypatch.setattr(_source_proof, "_strong_file_revision", revision_for)
    digest = hashlib.sha256(payload).hexdigest()
    snapshot = _runtime_storage._capture_runtime_snapshot(
        tmp_path / "cache",
        source,
        directory,
        len(payload),
        digest,
        ("k", 1, digest),
        captured,
    )

    assert snapshot is not None
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0
    _runtime_storage._release_runtime_snapshot(snapshot)


def test_capture_hardlink_collision_and_reference_increment_are_exact(
    tmp_path: Path, snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    directory = tmp_path / "snapshots"
    payload = b"content"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        _runtime_storage,
        "_stream_copy_with_signature",
        lambda *_args: (_ for _ in ()).throw(AssertionError("hardlink must not copy")),
    )
    directory.mkdir()
    named = (
        tmp_path
        / "snapshots"
        / f"{digest[: _runtime_storage._RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX]}.parquet"
    )
    named.write_bytes(b"different")
    snapshot = _runtime_storage._capture_runtime_snapshot(
        tmp_path / "cache", source, directory, len(payload), digest, None
    )
    assert snapshot is not None and snapshot != named and snapshot.read_bytes() == payload
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot] == 1

    reusable_dir = tmp_path / "reusable"
    reusable_dir.mkdir()
    reusable = (
        reusable_dir / f"{digest[: _runtime_storage._RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX]}.parquet"
    )
    os.link(source, reusable)
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[reusable] = 7
    reused = _runtime_storage._capture_runtime_snapshot(
        tmp_path / "cache", source, reusable_dir, len(payload), digest, None
    )
    assert reused == reusable and _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[reusable] == 8
