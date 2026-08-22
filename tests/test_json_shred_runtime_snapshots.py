"""Regression coverage for private runtime parquet snapshot ownership."""

from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
import structlog

from haute._json_shred import _runtime_storage, _source_proof


@pytest.fixture
def isolated_snapshot_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace process-global snapshot bookkeeping with test-local containers."""
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_DIRS", set())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_REFERENCES", {})
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_PINS", set())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid())
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_TOKEN", "test-owner")
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_ATEXIT_REGISTERED", True)
    monkeypatch.setattr(
        _runtime_storage,
        "_VERIFIED_RUNTIME_SNAPSHOT_CACHE",
        _runtime_storage._VerifiedRuntimeSnapshotCache(
            max_entries=8,
            max_bytes=1024 * 1024,
        ),
    )


def _signature(payload: bytes) -> dict[str, Any]:
    return {
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_cleanup_owned_snapshots_clears_bookkeeping_and_keeps_nonempty_parent(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    parent = tmp_path / _runtime_storage._RUNTIME_SNAPSHOT_DIRNAME
    owned_dir = parent / "test-owner"
    owned_file = owned_dir / "owned.parquet"
    owned_dir.mkdir(parents=True)
    owned_file.write_bytes(b"owned")
    (parent / "other-process").mkdir()

    _runtime_storage._RUNTIME_SNAPSHOT_DIRS.add(owned_dir)
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[owned_file] = 2
    _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS.add(owned_file)

    _runtime_storage._cleanup_runtime_snapshot_dirs()

    assert not owned_dir.exists()
    assert parent.exists()
    assert _runtime_storage._RUNTIME_SNAPSHOT_DIRS == set()
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES == {}
    assert _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS == set()


def test_cleanup_in_inherited_pid_leaves_parent_snapshot_state_untouched(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_dir = tmp_path / "inherited"
    snapshot_path = snapshot_dir / "snapshot.parquet"
    snapshot_dir.mkdir()
    snapshot_path.write_bytes(b"data")
    _runtime_storage._RUNTIME_SNAPSHOT_DIRS.add(snapshot_dir)
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1
    _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS.add(snapshot_path)
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid() + 1)

    _runtime_storage._cleanup_runtime_snapshot_dirs()

    assert snapshot_path.exists()
    assert _runtime_storage._RUNTIME_SNAPSHOT_DIRS == {snapshot_dir}
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES == {snapshot_path: 1}
    assert _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS == {snapshot_path}


def test_runtime_snapshot_dir_resets_inherited_state_for_new_pid(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_dir = tmp_path / "old"
    old_path = old_dir / "old.parquet"
    _runtime_storage._RUNTIME_SNAPSHOT_DIRS.add(old_dir)
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[old_path] = 1
    _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS.add(old_path)
    monkeypatch.setattr(_runtime_storage, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid() + 1)

    snapshot_dir = _runtime_storage._runtime_snapshot_dir(tmp_path / "cache")

    assert snapshot_dir.exists()
    assert snapshot_dir in _runtime_storage._RUNTIME_SNAPSHOT_DIRS
    assert old_dir not in _runtime_storage._RUNTIME_SNAPSHOT_DIRS
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES == {}
    assert _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS == set()
    assert _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_TOKEN.startswith(f"{os.getpid()}-")


def test_runtime_snapshot_creation_preserves_primary_error_when_cleanup_fails(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        _runtime_storage,
        "_ensure_runtime_owner_metadata",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid owner metadata")),
    )
    monkeypatch.setattr(
        _runtime_storage,
        "_remove_empty_runtime_owner_dir",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup denied")),
    )

    with pytest.raises(ValueError, match="invalid owner metadata"):
        _runtime_storage._runtime_snapshot_dir(tmp_path / "cache")


def test_stream_copy_removes_partial_target_when_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    target = tmp_path / "target.parquet"
    source.write_bytes(b"payload")
    original_open = Path.open

    class _FailingWriter:
        def __init__(self, file: Any) -> None:
            self.file = file

        def __enter__(self) -> _FailingWriter:
            self.file.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.file.__exit__(*args)

        def write(self, _chunk: bytes) -> int:
            raise OSError("disk full")

    def open_with_failing_target(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        file = original_open(path, mode, *args, **kwargs)
        return _FailingWriter(file) if path == target and mode == "xb" else file

    monkeypatch.setattr(Path, "open", open_with_failing_target)

    with pytest.raises(OSError, match="disk full"):
        _runtime_storage._stream_copy_with_signature(source, target)

    assert not target.exists()


def test_release_rejects_double_release_and_tolerates_missing_final_file(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    snapshot_path.write_bytes(b"data")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1

    _runtime_storage._release_runtime_snapshot(snapshot_path)

    assert not snapshot_path.exists()
    with pytest.raises(RuntimeError, match="released twice"):
        _runtime_storage._release_runtime_snapshot(snapshot_path)

    missing_path = tmp_path / "missing.parquet"
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[missing_path] = 1
    _runtime_storage._release_runtime_snapshot(missing_path)
    assert missing_path not in _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES


def test_release_of_process_pinned_snapshot_keeps_file_but_clears_reference(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    snapshot_path.write_bytes(b"data")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1
    _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS.add(snapshot_path)

    _runtime_storage._release_runtime_snapshot(snapshot_path)

    assert snapshot_path.exists()
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES == {}
    assert _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS == {snapshot_path}


def test_unmanaged_retain_requires_owner_and_pins_one_of_many_references(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    with pytest.raises(RuntimeError, match="no transient owner"):
        _runtime_storage._retain_runtime_snapshot(snapshot_path)

    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 2
    _runtime_storage._retain_runtime_snapshot(snapshot_path)

    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES == {snapshot_path: 1}
    assert _runtime_storage._RUNTIME_SNAPSHOT_PROCESS_PINS == {snapshot_path}


def test_managed_retain_releases_transient_snapshot_when_registration_fails(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    snapshot_path.write_bytes(b"data")
    _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1

    class _FailingContext:
        def add_cleanup(self, _callback: Any) -> None:
            raise RuntimeError("cleanup registration failed")

    monkeypatch.setattr(_runtime_storage, "current_execution_context", lambda: _FailingContext())

    with pytest.raises(RuntimeError, match="cleanup registration failed"):
        _runtime_storage._retain_runtime_snapshot(snapshot_path)

    assert not snapshot_path.exists()
    assert _runtime_storage._RUNTIME_SNAPSHOT_REFERENCES == {}


def test_hard_link_signature_failure_removes_candidate_and_reraises(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    source.write_bytes(b"payload")
    monkeypatch.setattr(
        _source_proof,
        "_file_content_signature",
        lambda _path: (_ for _ in ()).throw(RuntimeError("cannot hash candidate")),
    )

    with pytest.raises(RuntimeError, match="cannot hash candidate"):
        _runtime_storage._snapshot_cache_artifact(
            cache_dir,
            source,
            {"size": len(b"payload"), "sha256": "0" * 64},
        )

    snapshot_root = cache_dir.parent / _runtime_storage._RUNTIME_SNAPSHOT_DIRNAME / "test-owner"
    assert list(snapshot_root.glob("*.tmp")) == []


def test_hard_link_missing_source_reraises_without_leaving_candidate(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    candidates: list[Path] = []

    def missing_link(_source: Path, candidate: Path) -> None:
        candidates.append(candidate)
        raise FileNotFoundError("source vanished")

    # Reach the capture seam on every platform. POSIX otherwise raises while
    # obtaining the initial native revision, before a candidate is allocated.
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(os, "link", missing_link)

    with pytest.raises(FileNotFoundError, match="source vanished"):
        _runtime_storage._snapshot_cache_artifact(
            cache_dir,
            source,
            {"size": 0, "sha256": "0" * 64},
        )

    assert len(candidates) == 1
    assert not candidates[0].exists()


def test_unchanged_artifact_reuses_one_verified_snapshot_without_rehashing(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"verified-payload"
    source.write_bytes(payload)
    if _source_proof._strong_file_revision(source) is None:
        pytest.skip("filesystem has no strong native file revision")

    real_signature = _source_proof._file_content_signature
    hash_calls = 0

    def counting_signature(path: Path) -> dict[str, Any]:
        nonlocal hash_calls
        hash_calls += 1
        return real_signature(path)

    monkeypatch.setattr(_source_proof, "_file_content_signature", counting_signature)

    first = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))
    assert first is not None
    _runtime_storage._release_runtime_snapshot(first)
    assert first.exists()
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_snapshot_dir",
        lambda _cache_dir: (_ for _ in ()).throw(
            AssertionError("a verified warm hit must not allocate or scan runtime storage")
        ),
    )

    second = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))
    assert second == first
    _runtime_storage._release_runtime_snapshot(second)

    assert hash_calls == 1
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats() == {
        "entries": 1,
        "bytes": len(payload),
        "inflight": 0,
    }


def test_same_stat_artifact_corruption_invalidates_retained_snapshot(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    original = b"original"
    corrupted = b"corrupt!"
    assert len(original) == len(corrupted)
    source.write_bytes(original)
    if _source_proof._strong_file_revision(source) is None:
        pytest.skip("filesystem has no strong native file revision")

    real_signature = _source_proof._file_content_signature
    hash_calls = 0

    def counting_signature(path: Path) -> dict[str, Any]:
        nonlocal hash_calls
        hash_calls += 1
        return real_signature(path)

    monkeypatch.setattr(_source_proof, "_file_content_signature", counting_signature)
    first = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(original))
    assert first is not None
    _runtime_storage._release_runtime_snapshot(first)
    original_stat = source.stat()

    source.write_bytes(corrupted)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    rejected = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(original))

    assert rejected is None
    assert hash_calls == 2
    assert not first.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


def test_verified_snapshot_cache_enforces_entry_bound_and_active_lease(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _runtime_storage,
        "_VERIFIED_RUNTIME_SNAPSHOT_CACHE",
        _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=1, max_bytes=1024),
    )
    cache_dir = tmp_path / "cache"
    first_source = tmp_path / "first.parquet"
    second_source = tmp_path / "second.parquet"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")

    first = _runtime_storage._snapshot_cache_artifact(
        cache_dir,
        first_source,
        _signature(b"first"),
    )
    second = _runtime_storage._snapshot_cache_artifact(
        cache_dir,
        second_source,
        _signature(b"second"),
    )
    assert first is not None and second is not None
    assert first.exists(), "entry eviction must not break an active execution lease"

    _runtime_storage._release_runtime_snapshot(first)
    assert not first.exists()
    _runtime_storage._release_runtime_snapshot(second)
    assert second.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 1


def test_oversized_verified_snapshot_is_not_retained(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _runtime_storage,
        "_VERIFIED_RUNTIME_SNAPSHOT_CACHE",
        _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=8, max_bytes=3),
    )
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    source.write_bytes(b"four")

    snapshot = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(b"four"))
    assert snapshot is not None
    _runtime_storage._release_runtime_snapshot(snapshot)

    assert not snapshot.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


def test_verified_snapshot_cache_enforces_aggregate_byte_bound_and_lru_recency(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=8, max_bytes=8)
    monkeypatch.setattr(_runtime_storage, "_VERIFIED_RUNTIME_SNAPSHOT_CACHE", cache)
    cache_dir = tmp_path / "cache"
    first_source = tmp_path / "first.parquet"
    second_source = tmp_path / "second.parquet"
    third_source = tmp_path / "third.parquet"
    first_source.write_bytes(b"one")
    second_source.write_bytes(b"twos")
    third_source.write_bytes(b"five!")

    first = _runtime_storage._snapshot_cache_artifact(cache_dir, first_source, _signature(b"one"))
    second = _runtime_storage._snapshot_cache_artifact(
        cache_dir, second_source, _signature(b"twos")
    )
    assert first is not None and second is not None
    _runtime_storage._release_runtime_snapshot(first)
    _runtime_storage._release_runtime_snapshot(second)

    # Touch the first generation so the second becomes the byte-pressure victim.
    first_hit = _runtime_storage._snapshot_cache_artifact(
        cache_dir, first_source, _signature(b"one")
    )
    assert first_hit == first
    _runtime_storage._release_runtime_snapshot(first_hit)
    third = _runtime_storage._snapshot_cache_artifact(cache_dir, third_source, _signature(b"five!"))
    assert third is not None
    _runtime_storage._release_runtime_snapshot(third)

    assert first.exists()
    assert not second.exists()
    assert third.exists()
    assert cache.stats() == {"entries": 2, "bytes": 8, "inflight": 0}


def test_concurrent_snapshot_requests_share_one_verification(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"concurrent"
    source.write_bytes(payload)
    if _source_proof._strong_file_revision(source) is None:
        pytest.skip("filesystem has no strong native file revision")

    real_signature = _source_proof._file_content_signature
    hash_calls = 0

    def counting_signature(path: Path) -> dict[str, Any]:
        nonlocal hash_calls
        hash_calls += 1
        return real_signature(path)

    monkeypatch.setattr(_source_proof, "_file_content_signature", counting_signature)
    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = list(
            pool.map(
                lambda _index: _runtime_storage._snapshot_cache_artifact(
                    cache_dir,
                    source,
                    _signature(payload),
                ),
                range(8),
            )
        )

    assert all(snapshot is not None for snapshot in snapshots)
    assert len(set(snapshots)) == 1
    assert hash_calls == 1
    for snapshot in snapshots:
        assert snapshot is not None
        _runtime_storage._release_runtime_snapshot(snapshot)


def test_unavailable_artifact_revision_falls_back_to_hash_every_time(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"fallback"
    source.write_bytes(payload)
    real_signature = _source_proof._file_content_signature
    hash_calls = 0

    def counting_signature(path: Path) -> dict[str, Any]:
        nonlocal hash_calls
        hash_calls += 1
        return real_signature(path)

    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(_source_proof, "_file_content_signature", counting_signature)

    for _ in range(2):
        snapshot = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))
        assert snapshot is not None
        _runtime_storage._release_runtime_snapshot(snapshot)

    assert hash_calls == 2
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


def test_store_records_first_lease_before_another_key_can_evict_it(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission and first lease are indivisible from a competing eviction."""
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=1, max_bytes=1024)
    monkeypatch.setattr(_runtime_storage, "_VERIFIED_RUNTIME_SNAPSHOT_CACHE", cache)
    cache_dir = tmp_path / "cache"
    first_source = tmp_path / "first.parquet"
    second_source = tmp_path / "second.parquet"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    entered = threading.Event()
    release_store = threading.Event()
    original_store = cache.store

    def pausing_store(*args: Any, **kwargs: Any) -> tuple[bool, list[Path]]:
        result = original_store(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            assert release_store.wait(timeout=5)
        return result

    monkeypatch.setattr(cache, "store", pausing_store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            _runtime_storage._snapshot_cache_artifact, cache_dir, first_source, _signature(b"first")
        )
        assert entered.wait(timeout=5)
        second_future = pool.submit(
            _runtime_storage._snapshot_cache_artifact,
            cache_dir,
            second_source,
            _signature(b"second"),
        )
        release_store.set()
        first = first_future.result(timeout=5)
        second = second_future.result(timeout=5)
    assert first is not None and second is not None
    assert first.exists()
    _runtime_storage._release_runtime_snapshot(first)
    assert not first.exists()
    _runtime_storage._release_runtime_snapshot(second)


def test_identical_digest_different_keys_keep_their_verified_inodes_concurrently(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    cache_dir = tmp_path / "cache"
    first_source = tmp_path / "first.parquet"
    second_source = tmp_path / "second.parquet"
    payload = b"same-content"
    first_source.write_bytes(payload)
    second_source.write_bytes(payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            _runtime_storage._snapshot_cache_artifact, cache_dir, first_source, _signature(payload)
        )
        second_future = pool.submit(
            _runtime_storage._snapshot_cache_artifact, cache_dir, second_source, _signature(payload)
        )
        first = first_future.result(timeout=5)
        second = second_future.result(timeout=5)

    assert first is not None and second is not None
    assert first != second
    assert first.read_bytes() == payload
    assert second.read_bytes() == payload
    _runtime_storage._release_runtime_snapshot(first)
    _runtime_storage._release_runtime_snapshot(second)


def test_hard_link_mutation_during_hash_rejects_unstable_capture(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"original"
    source.write_bytes(payload)
    if _source_proof._strong_file_revision(source) is None:
        pytest.skip("filesystem has no strong native file revision")
    real_signature = _source_proof._file_content_signature

    def mutate_after_hash(path: Path) -> dict[str, Any]:
        signature = real_signature(path)
        source.write_bytes(b"corrupt!")
        return signature

    monkeypatch.setattr(_source_proof, "_file_content_signature", mutate_after_hash)
    assert _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload)) is None
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


@pytest.mark.parametrize(
    ("max_entries", "max_bytes"),
    [(0, 1), (1, 0), (True, 1), (1, "1")],
)
def test_verified_snapshot_cache_rejects_invalid_bounds(
    max_entries: object,
    max_bytes: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        _runtime_storage._VerifiedRuntimeSnapshotCache(  # type: ignore[arg-type]
            max_entries=max_entries,
            max_bytes=max_bytes,
        )


def test_verified_snapshot_cache_discards_inherited_process_state(tmp_path: Path) -> None:
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=2, max_bytes=10)
    revision = _source_proof._StrongFileRevision((1, 2), 1, 3, 4)
    snapshot = tmp_path / "snapshot.parquet"
    snapshot.write_bytes(b"x")
    cache.store(("path", 1, "digest"), revision, snapshot, 1)
    cache.begin(("inflight", 1, "digest"))
    cache.warn_revision_unavailable_once("warning", snapshot)
    cache._process_id = os.getpid() + 1

    assert cache.stats() == {"entries": 0, "bytes": 0, "inflight": 0}


def test_verified_snapshot_revision_warning_history_is_bounded(tmp_path: Path) -> None:
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=1, max_bytes=10)
    first = tmp_path / "first.parquet"
    second = tmp_path / "second.parquet"

    with structlog.testing.capture_logs() as logs:
        cache.warn_revision_unavailable_once("first", first)
        cache.warn_revision_unavailable_once("second", second)
        cache.warn_revision_unavailable_once("first", first)

    assert [record["parquet_path"] for record in logs] == [
        str(first),
        str(second),
        str(first),
    ]


def test_verified_snapshot_cache_replaces_key_and_counts_shared_path_once(
    tmp_path: Path,
) -> None:
    cache = _runtime_storage._VerifiedRuntimeSnapshotCache(max_entries=2, max_bytes=10)
    revision = _source_proof._StrongFileRevision((1, 2), 1, 3, 4)
    snapshot = tmp_path / "snapshot.parquet"
    snapshot.write_bytes(b"x")
    first_key = ("first", 1, "digest")
    second_key = ("second", 1, "digest")

    assert cache.store(first_key, revision, snapshot, 1) == (True, [])
    assert cache.store(second_key, revision, snapshot, 1) == (True, [])
    assert cache.store(first_key, revision, snapshot, 1) == (True, [])

    assert cache.stats() == {"entries": 2, "bytes": 1, "inflight": 0}


def test_remove_unpinned_snapshot_accepts_already_missing_file(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()

    _runtime_storage._remove_unpinned_runtime_snapshot(snapshot_dir / "missing.parquet")

    assert not snapshot_dir.exists()


def test_capture_reuses_existing_snapshot_only_for_the_same_inode(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    cache_dir = tmp_path / "cache"
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    source = tmp_path / "source.parquet"
    payload = b"same-generation"
    source.write_bytes(payload)
    signature = _signature(payload)
    snapshot_path = snapshot_dir / (
        f"{signature['sha256'][: _runtime_storage._RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX]}.parquet"
    )
    try:
        os.link(source, snapshot_path)
    except OSError:
        pytest.skip("filesystem does not support hard links")

    captured = _runtime_storage._capture_runtime_snapshot(
        cache_dir,
        source,
        snapshot_dir,
        signature["size"],
        signature["sha256"],
        None,
    )

    assert captured == snapshot_path
    _runtime_storage._release_runtime_snapshot(snapshot_path)


def test_snapshot_publication_name_fits_bounded_windows_path_headroom(
    tmp_path: Path,
    isolated_snapshot_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    snapshot_dir = tmp_path / "snapshots"
    source = tmp_path / "source.parquet"
    payload = b"bounded-name"
    source.write_bytes(payload)
    signature = _signature(payload)
    path_budget = len(str(snapshot_dir)) + 60
    real_rename = Path.rename

    def reject_overlong_destination(candidate: Path, target: Path) -> Path:
        if len(str(target)) > path_budget:
            raise OSError(3, "simulated legacy Windows path limit", str(target))
        return real_rename(candidate, target)

    monkeypatch.setattr(Path, "rename", reject_overlong_destination)

    snapshot = _runtime_storage._capture_runtime_snapshot(
        cache_dir,
        source,
        snapshot_dir,
        signature["size"],
        signature["sha256"],
        None,
    )

    assert snapshot is not None
    assert len(str(snapshot)) <= path_budget
    assert signature["sha256"] not in snapshot.name
    _runtime_storage._release_runtime_snapshot(snapshot)


def test_copy_capture_is_not_cached_when_revision_moves_during_copy(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"copy-race"
    source.write_bytes(payload)
    revision = _source_proof._strong_file_revision(source)
    if revision is None:
        pytest.skip("filesystem has no strong native file revision")
    changed = _source_proof._StrongFileRevision(
        revision.file_identity,
        revision.size,
        revision.mtime_ns,
        revision.change_token + 1,
    )
    observations = iter((revision, revision, changed))

    def reject_link(_source: Path, _target: Path) -> None:
        raise OSError("links unavailable")

    monkeypatch.setattr(os, "link", reject_link)
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: next(observations))

    snapshot = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))

    assert snapshot is not None
    _runtime_storage._release_runtime_snapshot(snapshot)
    assert not snapshot.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


def test_copy_capture_is_not_cached_when_revision_moves_at_admission(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"copy-admission-race"
    source.write_bytes(payload)
    revision = _source_proof._strong_file_revision(source)
    if revision is None:
        pytest.skip("filesystem has no strong native file revision")
    changed = _source_proof._StrongFileRevision(
        revision.file_identity,
        revision.size,
        revision.mtime_ns,
        revision.change_token + 1,
    )
    observations = iter((revision, revision, revision, changed))

    def reject_link(_source: Path, _target: Path) -> None:
        raise OSError("links unavailable")

    monkeypatch.setattr(os, "link", reject_link)
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: next(observations))

    snapshot = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))

    assert snapshot is not None
    _runtime_storage._release_runtime_snapshot(snapshot)
    assert not snapshot.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


def test_hard_link_capture_is_not_cached_when_visible_identity_moves_at_admission(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"link-admission-race"
    source.write_bytes(payload)
    revision = _source_proof._strong_file_revision(source)
    if revision is None:
        pytest.skip("filesystem has no strong native file revision")
    changed = _source_proof._StrongFileRevision(
        (revision.file_identity[0], b"z" * 16),
        revision.size,
        revision.mtime_ns,
        revision.change_token + 1,
    )
    source_observations = iter((revision, revision, changed))

    def moving_revision(path: Path) -> Any:
        return next(source_observations) if path == source else revision

    monkeypatch.setattr(_source_proof, "_strong_file_revision", moving_revision)

    snapshot = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))

    assert snapshot is not None
    _runtime_storage._release_runtime_snapshot(snapshot)
    assert not snapshot.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0


def test_artifact_revision_becoming_unavailable_inside_singleflight_falls_back(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    payload = b"revision-disappears"
    source.write_bytes(payload)
    revision = _source_proof._strong_file_revision(source)
    if revision is None:
        pytest.skip("filesystem has no strong native file revision")
    source_observations = iter((revision, None))

    def disappearing_revision(path: Path) -> Any:
        return next(source_observations) if path == source else None

    monkeypatch.setattr(_source_proof, "_strong_file_revision", disappearing_revision)

    snapshot = _runtime_storage._snapshot_cache_artifact(cache_dir, source, _signature(payload))

    assert snapshot is not None
    _runtime_storage._release_runtime_snapshot(snapshot)
    assert not snapshot.exists()
    assert _runtime_storage._VERIFIED_RUNTIME_SNAPSHOT_CACHE.stats()["entries"] == 0
