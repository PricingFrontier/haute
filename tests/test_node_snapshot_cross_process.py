"""Node-output snapshot coordination across processes (CACHE-S01)."""

from __future__ import annotations

import multiprocessing as mp
import threading
import time
from pathlib import Path
from typing import Any

import polars as pl

import haute._node_snapshots as node_snapshots
from haute._execution_context import ExecutionProfile
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotSlot,
    NodeSnapshotStore,
)
from haute._source_cache import SourceCacheGenerationMissingError, SourceCacheIdentity

_TIMEOUT = 60.0


def _slot(root: str, node_id: str) -> NodeSnapshotSlot:
    return NodeSnapshotSlot(
        pipeline_source_file=str(Path(root) / "main.py"),
        node_id=node_id,
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )


def _stage(store: NodeSnapshotStore, identity: SourceCacheIdentity, values: list[int]):
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"a": values}).write_parquet(artifact.part_path(0))
    return artifact


def _publish(store: NodeSnapshotStore, identity: SourceCacheIdentity, values: list[int]):
    return store.publish_node_output(
        identity,
        _stage(store, identity, values),
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    )


def _publishing_worker(root: str, value: int, ready: Any, go: Any, results: Any) -> None:
    store = NodeSnapshotStore(root)
    identity = _slot(root, "join").identity("s1")
    artifact = _stage(store, identity, [value])
    ready.put(value)
    if not go.wait(_TIMEOUT):
        raise TimeoutError("test did not start publication")
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ) as publication:
        results.put((value, publication.outcome, publication.lazy_frame.collect()["a"].to_list()))


def _columns_worker(root: str, columns: list[str], ready: Any, go: Any, results: Any) -> None:
    store = NodeSnapshotStore(root)
    identity = _slot(root, "join").identity("s1")
    artifact = store.stage_node_output(identity)
    pl.DataFrame({name: [1] for name in columns}).write_parquet(artifact.part_path(0))
    ready.put(tuple(columns))
    if not go.wait(_TIMEOUT):
        raise TimeoutError("test did not start publication")
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.of(columns),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ) as publication:
        results.put((tuple(columns), publication.outcome, publication.lazy_frame.collect().columns))


def _paused_reader(root: str, leased: Any, release: Any, results: Any) -> None:
    store = NodeSnapshotStore(root)
    identity = _slot(root, "join").identity("s1")
    with store.lease(identity) as generation:
        leased.put(generation.generation_id)
        if not release.wait(_TIMEOUT):
            raise TimeoutError("test did not release the reader")
        results.put(generation.lazy_frame.collect()["a"].to_list())


def _faulted_lease_worker(
    root: str,
    generation_id: str,
    fault_name: str,
    events: Any,
    resume: Any,
    finish: Any,
) -> None:
    def pause(name: str) -> None:
        if name == fault_name:
            events.put("paused")
            if not resume.wait(_TIMEOUT):
                raise TimeoutError("test did not resume the lease")

    node_snapshots._fault_point = pause
    store = NodeSnapshotStore(root)
    identity = _slot(root, "join").identity("s1")
    events.put("leasing")
    try:
        with store.lease_generation(identity, generation_id) as generation:
            events.put("leased")
            if not finish.wait(_TIMEOUT):
                raise TimeoutError("test did not finish the reader")
            events.put(("rows", generation.lazy_frame.collect()["a"].to_list()))
    except SourceCacheGenerationMissingError:
        events.put("missing")


def _stop(process: Any, *events: Any) -> None:
    # Setting a multiprocessing Event whose waiter was killed can block forever,
    # so only signal a process that is still running.
    if process.is_alive():
        for event in events:
            event.set()
    process.join(timeout=_TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)


def _generation_dirs(store: NodeSnapshotStore, identity: SourceCacheIdentity) -> list[Path]:
    generations = store.identity_path(identity) / "generations"
    return sorted(generations.iterdir()) if generations.exists() else []


def test_two_worker_processes_publish_one_identity_once(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    ready = ctx.Queue()
    results = ctx.Queue()
    go = ctx.Event()
    root = str(tmp_path)
    workers = [
        ctx.Process(target=_publishing_worker, args=(root, value, ready, go, results))
        for value in (1, 2)
    ]
    try:
        for worker in workers:
            worker.start()
        assert {ready.get(timeout=_TIMEOUT), ready.get(timeout=_TIMEOUT)} == {1, 2}
        go.set()
        outcomes = [results.get(timeout=_TIMEOUT), results.get(timeout=_TIMEOUT)]
    finally:
        for worker in workers:
            _stop(worker, go)

    assert sorted(outcome for _value, outcome, _rows in outcomes) == ["published", "superseded"]
    # Each worker continues from its own rows, published or not.
    assert all(rows == [value] for value, _outcome, rows in outcomes)
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(root, "join").identity("s1")
    assert len(_generation_dirs(store, identity)) == 1
    assert all(worker.exitcode == 0 for worker in workers)


def test_a_paused_reader_keeps_its_generation_through_clear(
    tmp_path: Path,
) -> None:
    ctx = mp.get_context("spawn")
    leased = ctx.Queue()
    results = ctx.Queue()
    release = ctx.Event()
    root = str(tmp_path)
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(root, "join").identity("s1")
    with _publish(store, identity, [1, 2, 3]):
        pass
    reader = ctx.Process(target=_paused_reader, args=(root, leased, release, results))
    try:
        reader.start()
        generation_id = leased.get(timeout=_TIMEOUT)
        generation_dir = store.identity_path(identity) / "generations" / generation_id

        assert generation_dir.is_dir()

        store.clear_slot(_slot(root, "join"))
        assert generation_dir.is_dir()
        assert store.slot_status(_slot(root, "join"), "s1").state == "missing"

        release.set()
        assert results.get(timeout=_TIMEOUT) == [1, 2, 3]
    finally:
        _stop(reader, release)

    assert reader.exitcode == 0
    assert not generation_dir.exists()


def test_a_killed_readers_marker_is_reclaimed_by_clear(
    tmp_path: Path,
) -> None:
    ctx = mp.get_context("spawn")
    leased = ctx.Queue()
    results = ctx.Queue()
    release = ctx.Event()
    root = str(tmp_path)
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(root, "join").identity("s1")
    with _publish(store, identity, [1]):
        pass
    reader = ctx.Process(target=_paused_reader, args=(root, leased, release, results))
    try:
        reader.start()
        generation_id = leased.get(timeout=_TIMEOUT)
        reader.terminate()
        reader.join(timeout=_TIMEOUT)
        store.clear_slot(_slot(root, "join"))
    finally:
        _stop(reader, release)

    assert not (store.identity_path(identity) / "generations" / generation_id).exists()
    assert store.slot_status(_slot(root, "join"), "s1").state == "missing"


def test_a_lease_paused_before_its_marker_blocks_clear_and_keeps_the_generation(
    tmp_path: Path,
) -> None:
    ctx = mp.get_context("spawn")
    events = ctx.Queue()
    resume = ctx.Event()
    finish = ctx.Event()
    root = str(tmp_path)
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(root, "join").identity("s1")
    with _publish(store, identity, [5]) as publication:
        generation_id = publication.generation.generation_id
    worker = ctx.Process(
        target=_faulted_lease_worker,
        args=(root, generation_id, "lease_before_marker", events, resume, finish),
    )
    clear_result: list[object] = []

    def clear() -> None:
        store.clear_slot(_slot(root, "join"))
        clear_result.append("cleared")

    try:
        worker.start()
        assert events.get(timeout=_TIMEOUT) == "leasing"
        assert events.get(timeout=_TIMEOUT) == "paused"
        clearer = threading.Thread(target=clear)
        clearer.start()
        time.sleep(0.5)
        assert clearer.is_alive(), "clear must wait for the lease lock"

        resume.set()
        assert events.get(timeout=_TIMEOUT) == "leased"
        clearer.join(timeout=_TIMEOUT)
        assert clear_result == ["cleared"]

        finish.set()
        assert events.get(timeout=_TIMEOUT) == ("rows", [5])
    finally:
        _stop(worker, resume, finish)

    assert worker.exitcode == 0


def test_a_writer_missing_a_concurrently_widened_generations_columns_keeps_its_artifact(
    tmp_path: Path,
) -> None:
    ctx = mp.get_context("spawn")
    ready = ctx.Queue()
    results = ctx.Queue()
    go_wide = ctx.Event()
    go_narrow = ctx.Event()
    root = str(tmp_path)
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(root, "join").identity("s1")
    initial = store.stage_node_output(identity)
    pl.DataFrame({"a": [1]}).write_parquet(initial.part_path(0))
    with store.publish_node_output(
        identity,
        initial,
        columns=NodeSnapshotColumns.of(["a"]),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ):
        pass
    wide = ctx.Process(
        target=_columns_worker, args=(root, ["a", "b", "c"], ready, go_wide, results)
    )
    narrow = ctx.Process(target=_columns_worker, args=(root, ["a", "b"], ready, go_narrow, results))
    try:
        wide.start()
        narrow.start()
        assert {ready.get(timeout=_TIMEOUT), ready.get(timeout=_TIMEOUT)} == {
            ("a", "b", "c"),
            ("a", "b"),
        }
        go_wide.set()
        assert results.get(timeout=_TIMEOUT) == (("a", "b", "c"), "published", ["a", "b", "c"])
        go_narrow.set()
        assert results.get(timeout=_TIMEOUT) == (("a", "b"), "superseded", ["a", "b"])
    finally:
        _stop(wide, go_wide)
        _stop(narrow, go_narrow)

    latest = store.latest_generation(identity)
    assert latest is not None
    assert latest.columns == NodeSnapshotColumns.of(["a", "b", "c"])
    assert wide.exitcode == 0
    assert narrow.exitcode == 0
