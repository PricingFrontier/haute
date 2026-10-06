"""Cross-process lease contracts shared by input and node snapshot stores."""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._node_snapshots import NodeSnapshotStore
from haute._source_cache import (
    SourceCacheBuildContext,
    SourceCacheIdentity,
    SourceCacheStore,
)

_TIMEOUT = 60.0


@dataclass
class _Builder:
    value: int

    def build(self, _context: SourceCacheBuildContext) -> pl.LazyFrame:
        return pl.DataFrame({"value": [self.value]}).lazy()


def _identity() -> SourceCacheIdentity:
    return SourceCacheIdentity(provider="file", descriptor={"path": "input.parquet"})


def _context() -> SourceCacheBuildContext:
    return SourceCacheBuildContext(
        profile=ExecutionProfile.LAZY_SINK,
        build_class="bounded",
    )


def _reader(
    root: str,
    ready: Any,
    release: Any,
    results: Any,
    nested: bool = False,
    node_reader: bool = False,
    retire_grace_seconds: float | None = None,
) -> None:
    kwargs = {} if retire_grace_seconds is None else {"retire_grace_seconds": retire_grace_seconds}
    store = NodeSnapshotStore(root, **kwargs) if node_reader else SourceCacheStore(root, **kwargs)
    identity = _identity()
    with store.lease(identity) as generation:
        if nested:
            with store.lease_generation(identity, generation.generation_id):
                ready.put(generation.generation_id)
                if not release.wait(_TIMEOUT):
                    raise TimeoutError("test did not release reader")
        else:
            ready.put(generation.generation_id)
            if not release.wait(_TIMEOUT):
                raise TimeoutError("test did not release reader")
        results.put(generation.lazy_frame.collect()["value"].to_list())


def _stop(process: Any, *events: Any) -> None:
    if process.is_alive():
        for event in events:
            event.set()
    process.join(timeout=_TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)


@pytest.mark.parametrize(
    ("node_reader", "clearer"),
    [(False, NodeSnapshotStore), (True, SourceCacheStore)],
)
def test_foreign_input_reader_survives_clear_until_release(
    tmp_path: Path, node_reader: bool, clearer: type[SourceCacheStore]
) -> None:
    ctx = mp.get_context("spawn")
    ready, release, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    store = SourceCacheStore(tmp_path, retire_grace_seconds=0)
    identity = _identity()
    generation = store.build(identity, _Builder(1), context=_context())
    reader = ctx.Process(
        target=_reader,
        args=(str(tmp_path), ready, release, results, False, node_reader),
    )
    try:
        reader.start()
        assert ready.get(timeout=_TIMEOUT) == generation.generation_id
        clearer(tmp_path, retire_grace_seconds=0).clear(identity)
        assert generation.directory.exists()
        release.set()
        assert results.get(timeout=_TIMEOUT) == [1]
    finally:
        _stop(reader, release)
    assert reader.exitcode == 0
    assert not generation.directory.exists()


def test_foreign_reader_keeps_old_input_generation_during_refresh(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    ready, release, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    store = SourceCacheStore(tmp_path, retire_grace_seconds=0)
    identity = _identity()
    first = store.build(identity, _Builder(1), context=_context())
    reader = ctx.Process(target=_reader, args=(str(tmp_path), ready, release, results, True))
    try:
        reader.start()
        assert ready.get(timeout=_TIMEOUT) == first.generation_id
        second = store.build(identity, _Builder(2), context=_context(), refresh=True)
        assert second.lazy_frame.collect()["value"].to_list() == [2]
        assert first.directory.exists()
        release.set()
        assert results.get(timeout=_TIMEOUT) == [1]
    finally:
        _stop(reader, release)


def test_refresh_keeps_a_foreign_held_generation_until_its_last_release(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    ready, release, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    store = SourceCacheStore(tmp_path, retire_grace_seconds=0)
    identity = _identity()
    first = store.build(identity, _Builder(1), context=_context())
    reader = ctx.Process(
        target=_reader, args=(str(tmp_path), ready, release, results, False, False, 0)
    )
    try:
        reader.start()
        assert ready.get(timeout=_TIMEOUT) == first.generation_id
        second = store.build(identity, _Builder(2), context=_context(), refresh=True)
        assert second.generation_id != first.generation_id
        assert first.directory.exists()
        release.set()
        assert results.get(timeout=_TIMEOUT) == [1]
    finally:
        _stop(reader, release)
    assert not first.directory.exists()


def test_same_root_nested_leases_keep_the_marker_until_the_last_release(tmp_path: Path) -> None:
    source_store = SourceCacheStore(tmp_path, retire_grace_seconds=0)
    node_store = NodeSnapshotStore(tmp_path, retire_grace_seconds=0)
    identity = _identity()
    generation = source_store.build(identity, _Builder(1), context=_context())
    marker = generation.directory / f".lease-{source_store._own_token()}"

    with source_store.lease(identity):
        assert marker.exists()
        with node_store.lease_generation(identity, generation.generation_id):
            assert marker.exists()
        assert marker.exists()
        node_store.clear(identity)
        assert generation.directory.exists()

    assert not marker.exists()
    assert not generation.directory.exists()


def test_failed_input_validation_releases_its_marker_and_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SourceCacheStore(tmp_path)
    identity = _identity()
    generation = store.build(identity, _Builder(1), context=_context())
    marker = generation.directory / f".lease-{store._own_token()}"

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("metadata validation failed")

    monkeypatch.setattr(store, "_metadata_from_path", fail_validation)
    with pytest.raises(RuntimeError, match="metadata validation failed"):
        with store.lease(identity):
            pass

    assert store._in_process_lease_count(identity.digest, generation.generation_id) == 0
    assert not marker.exists()


def test_lease_retries_when_the_current_pointer_changes_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SourceCacheStore(tmp_path, retire_grace_seconds=0)
    identity = _identity()
    first = store.build(identity, _Builder(1), context=_context())
    original = store._acquire_input_lease
    replaced = False

    def replace_before_acquire(
        candidate: SourceCacheIdentity, generation_id: str, *, require_current: bool
    ) -> bool:
        nonlocal replaced
        if not replaced:
            replaced = True
            store.build(candidate, _Builder(2), context=_context(), refresh=True)
        return original(candidate, generation_id, require_current=require_current)

    monkeypatch.setattr(store, "_acquire_input_lease", replace_before_acquire)
    with store.lease(identity) as leased:
        assert leased.generation_id != first.generation_id
        assert leased.lazy_frame.collect()["value"].to_list() == [2]


def test_dead_foreign_reader_marker_is_reclaimed_by_clear(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    ready, release, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    store = SourceCacheStore(tmp_path, retire_grace_seconds=0)
    identity = _identity()
    generation = store.build(identity, _Builder(1), context=_context())
    reader = ctx.Process(target=_reader, args=(str(tmp_path), ready, release, results))
    try:
        reader.start()
        assert ready.get(timeout=_TIMEOUT) == generation.generation_id
        reader.terminate()
        reader.join(timeout=_TIMEOUT)
        store.clear(identity)
    finally:
        _stop(reader, release)
    assert not generation.directory.exists()
