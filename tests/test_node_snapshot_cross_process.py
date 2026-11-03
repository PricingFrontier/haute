"""Node-output snapshot coordination across processes (CACHE-S01)."""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import haute._node_snapshots as node_snapshots
from haute._execution_context import ExecutionProfile
from haute._file_lock import FileLock
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotSlot,
    NodeSnapshotStore,
)
from haute._project_context import context_thread
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
        clearer = context_thread(clear)
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


_OLD = time.time() - 3600


def _take_token(root: str, tokens: Any, release: Any, *, aged: bool) -> None:
    store = NodeSnapshotStore(root)
    token = store._own_token()
    if aged:
        # The owner ages its own file: Windows may refuse another process's
        # change to a file this one holds open.
        os.utime(store._processes_dir / f"{token}.lock", (_OLD, _OLD))
    tokens.put(token)
    if release is not None and not release.wait(_TIMEOUT):
        raise TimeoutError("test did not release the token holder")


def _hold_publication_lock(root: str, digest: str, held: Any, release: Any) -> None:
    with FileLock(Path(root) / ".haute_cache" / "inputs" / ".locks" / f"publication-{digest}.lock"):
        held.put(digest)
        if not release.wait(_TIMEOUT):
            raise TimeoutError("test did not release the publication lock")


def test_the_once_per_process_sweep_removes_dead_tokens_and_orphaned_publication_locks(
    tmp_path: Path,
) -> None:
    """Housekeeping (CACHE-S17): what has gone is removed, what is live or held is kept."""
    ctx = mp.get_context("spawn")
    root = str(tmp_path)
    tokens, held = ctx.Queue(), ctx.Queue()
    release = ctx.Event()
    inputs = tmp_path / ".haute_cache" / "inputs"
    locks = inputs / ".locks"
    orphan, held_orphan, existing = "a" * 64, "b" * 64, "c" * 64
    dead = ctx.Process(target=_take_token, args=(root, tokens, None), kwargs={"aged": True})
    young_dead = ctx.Process(target=_take_token, args=(root, tokens, None), kwargs={"aged": False})
    live = ctx.Process(target=_take_token, args=(root, tokens, release), kwargs={"aged": True})
    holder = ctx.Process(target=_hold_publication_lock, args=(root, held_orphan, held, release))
    try:
        # Every child's first store sweeps too, so the dead token's owner runs
        # last: no other child is left to sweep it before this process does.
        young_dead.start()
        young_token = tokens.get(timeout=_TIMEOUT)
        young_dead.join(timeout=_TIMEOUT)
        live.start()
        live_token = tokens.get(timeout=_TIMEOUT)
        dead.start()
        dead_token = tokens.get(timeout=_TIMEOUT)
        dead.join(timeout=_TIMEOUT)
        # Each child's first store has swept already; these are for this process's.
        locks.mkdir(parents=True, exist_ok=True)
        for digest in (orphan, held_orphan, existing):
            (locks / f"publication-{digest}.lock").write_bytes(b"\0")
        (inputs / existing).mkdir()
        holder.start()
        assert held.get(timeout=_TIMEOUT) == held_orphan
        processes = inputs / ".processes"

        # This process's first store sweeps.
        NodeSnapshotStore(tmp_path)

        assert not (processes / f"{dead_token}.lock").exists()
        # A token as young as one still being created, and a live process's, stay.
        assert (processes / f"{young_token}.lock").exists()
        assert (processes / f"{live_token}.lock").exists()
        assert not (locks / f"publication-{orphan}.lock").exists()
        assert (locks / f"publication-{held_orphan}.lock").exists()
        assert (locks / f"publication-{existing}.lock").exists()
    finally:
        for process in (live, holder):
            if process.pid is not None:
                _stop(process, release)
    assert (dead.exitcode, young_dead.exitcode, live.exitcode, holder.exitcode) == (0, 0, 0, 0)


def _publish_after_sweep(root: str, opened: Any, published: Any, release: Any) -> None:
    import haute._file_lock as file_lock

    real_acquire = file_lock._acquire_file_lock
    attempts: list[int] = []

    def reporting(handle: Any, **kwargs: Any) -> bool:
        attempts.append(os.fstat(handle.fileno()).st_ino)
        if len(attempts) == 1:
            opened.put(attempts[0])
        return real_acquire(handle, **kwargs)

    store = NodeSnapshotStore(root)
    identity = _slot(root, "join").identity("s1")
    file_lock._acquire_file_lock = reporting  # type: ignore[assignment]
    with store._publication_lock(identity):
        published.put(len(attempts))
        if not release.wait(_TIMEOUT):
            raise TimeoutError("test did not release the publisher")


def test_a_publisher_racing_the_sweep_still_excludes_a_second_publisher(tmp_path: Path) -> None:
    """The sweep removes the publication lock file a waiting publisher opened."""
    if os.name == "nt":
        pytest.skip("Windows refuses to delete a lock file a process has open")
    ctx = mp.get_context("spawn")
    root = str(tmp_path)
    opened, published = ctx.Queue(), ctx.Queue()
    release = ctx.Event()
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(root, "join").identity("s1")
    lock_path = store._locks_dir / f"publication-{identity.digest}.lock"
    sweeper = FileLock(lock_path)
    sweeper.acquire()
    swept_inode = lock_path.stat().st_ino
    publisher = ctx.Process(target=_publish_after_sweep, args=(root, opened, published, release))
    try:
        publisher.start()
        # The publisher has opened the lock file the sweep removes, and locks it next.
        assert opened.get(timeout=_TIMEOUT) == swept_inode
        lock_path.unlink()
        sweeper.release()
        # It locked the removed file, then the one the path names.
        assert published.get(timeout=_TIMEOUT) == 2
        assert FileLock(lock_path).acquire(blocking=False) is False
    finally:
        _stop(publisher, release)
    assert publisher.exitcode == 0
