"""Node-output snapshot store contracts within one process (CACHE-S01)."""

from __future__ import annotations

import errno
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from polars.testing import assert_frame_equal

import haute._source_cache as source_cache_module
from haute._chunked_writes import write_parts
from haute._execution_context import ExecutionProfile
from haute._hashing import content_hash
from haute._node_config_recovery import _DISCRIMINANTS
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotPublication,
    NodeSnapshotSlot,
    NodeSnapshotStore,
    snapshot_read_classes,
    snapshot_write_class,
)
from haute._source_cache import (
    SourceCacheBuildContext,
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
    SourceCacheStore,
    classify_identity_marker,
    generation_bytes,
)
from haute._types import NodeType


@dataclass
class _LazyBuilder:
    frame: pl.LazyFrame

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        return self.frame


def _input_identity(name: str = "input1", provider: str = "file") -> SourceCacheIdentity:
    return SourceCacheIdentity(provider=provider, descriptor={"path": f"{name}.parquet"})


def _build_input_snapshot(
    store: NodeSnapshotStore | source_cache_module.SourceCacheStore,
    identity: SourceCacheIdentity,
    frame: pl.DataFrame | None = None,
) -> None:
    df = frame if frame is not None else pl.DataFrame({"x": [1, 2, 3]})
    context = SourceCacheBuildContext(
        profile=ExecutionProfile.LAZY_SINK,
        build_class="bounded",
    )
    store.build(identity, _LazyBuilder(df.lazy()), context=context)


def _slot(tmp_path: Path, node_id: str = "join") -> NodeSnapshotSlot:
    return NodeSnapshotSlot(
        pipeline_source_file=str(tmp_path / "main.py"),
        node_id=node_id,
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )


def _columns(*names: str) -> NodeSnapshotColumns:
    return NodeSnapshotColumns.of(names) if names else NodeSnapshotColumns.all()


def _publish(
    store: NodeSnapshotStore,
    identity: SourceCacheIdentity,
    frame: pl.DataFrame,
    *,
    columns: NodeSnapshotColumns | None = None,
    dependencies: Mapping[str, str] | None = None,
    explicit: bool = False,
    refresh: bool = False,
) -> NodeSnapshotPublication:
    artifact = store.stage_node_output(identity)
    frame.write_parquet(artifact.part_path(0))
    return store.publish_node_output(
        identity,
        artifact,
        columns=columns if columns is not None else NodeSnapshotColumns.all(),
        dependencies=dependencies or {},
        explicit=explicit,
        profile=ExecutionProfile.NODE_SNAPSHOT,
        refresh=refresh,
    )


def _published_id(store: NodeSnapshotStore, identity: SourceCacheIdentity, frame) -> str:
    with _publish(store, identity, frame) as publication:
        assert publication.outcome == "published"
        assert publication.generation is not None
        return publication.generation.generation_id


def test_write_and_read_class_mappings() -> None:
    for profile in (
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
        ExecutionProfile.NODE_SNAPSHOT,
    ):
        assert snapshot_write_class(profile) == "bounded"
        assert snapshot_read_classes(profile) == frozenset({"bounded"})
    for profile in (ExecutionProfile.DEPLOY_LIVE, ExecutionProfile.DEPLOY_BATCH):
        assert snapshot_write_class(profile) is None
        assert snapshot_read_classes(profile) == frozenset()
    # An admitted preview writes the bounded class; every preview may read it.
    assert snapshot_write_class(ExecutionProfile.PREVIEW_EAGER, preview_admitted=True) == "bounded"
    assert snapshot_write_class(ExecutionProfile.PREVIEW_EAGER) is None
    assert snapshot_read_classes(ExecutionProfile.PREVIEW_EAGER) == frozenset({"bounded"})


def test_low_disk_refuses_node_staging_before_creating_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("low-disk")
    actual_usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        "haute._file_ops.shutil.disk_usage",
        lambda _directory: actual_usage._replace(free=65_535),
    )

    with pytest.raises(OSError) as exc_info:
        store.stage_node_output(identity)

    assert exc_info.value.errno == errno.ENOSPC
    assert not any(store.identity_path(identity).glob(".staging-*"))


def test_publishing_a_signature_replaces_the_slots_previous_one(tmp_path: Path) -> None:
    """A node holds one dataset: a re-cache replaces, it does not accumulate.

    The earlier signature's data is gone, so an edit and a revert recompute
    rather than finding the old snapshot still on disk. That is the trade this
    policy makes deliberately: disk is not spent keeping every version a node
    has ever had.
    """
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("s1")
    second = slot.identity("s2")

    assert store.slot_status(slot, "s1").state == "missing"
    _published_id(store, first, pl.DataFrame({"a": [1]}))
    assert store.slot_status(slot, "s2").state == "stale"
    _published_id(store, second, pl.DataFrame({"a": [2]}))

    assert store.slot_status(slot, "s2").state == "current"
    assert store.slot_status(slot, "s1").state == "stale"
    assert store.slot_status(slot, "s3").state == "stale"

    # Not merely unselectable: the bytes are gone from the store.
    assert _slot_generation_bytes(store, tmp_path) == _identity_bytes(store, second)

    store.clear_slot(slot)
    assert store.slot_status(slot, "s1").state == "missing"
    assert store.slot_status(slot, "s2").state == "missing"


def _identity_bytes(store: NodeSnapshotStore, identity: SourceCacheIdentity) -> int:
    generations = store.inputs_root / identity.digest / "generations"
    return sum(generation_bytes(child) for child in generations.iterdir() if child.is_dir())


def _slot_generation_bytes(store: NodeSnapshotStore, tmp_path: Path) -> int:
    """Every node-output byte the store holds, whichever signature wrote it."""
    total = 0
    for identity_dir in store.inputs_root.iterdir():
        generations = identity_dir / "generations"
        if not generations.is_dir():
            continue
        for child in generations.iterdir():
            if child.is_dir():
                total += generation_bytes(child)
    return total


def test_a_signature_a_reader_still_holds_survives_until_it_releases(
    tmp_path: Path,
) -> None:
    """A scan in flight must not have its files deleted underneath it."""
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("s1")
    _published_id(store, first, pl.DataFrame({"a": [1]}))

    with store.lease(first) as leased:
        assert leased is not None
        _published_id(store, slot.identity("s2"), pl.DataFrame({"a": [2]}))
        # Still readable: the reader holds it, so publication left it alone.
        assert _identity_bytes(store, first) > 0

    # Released, and with no pointer naming it, it retires on release.
    assert _identity_bytes(store, first) == 0


def test_widening_serves_narrow_readers_and_stales_descendants(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    upstream = _slot(tmp_path, "join").identity("s1")
    downstream_slot = _slot(tmp_path, "banding")
    downstream = downstream_slot.identity("s1")
    frame = pl.DataFrame({"a": [1], "b": [2], "c": [3]})

    with _publish(store, upstream, frame.select("a", "b"), columns=_columns("a", "b")) as narrow:
        narrow_id = narrow.generation.generation_id
        assert narrow.generation.columns.covers(_columns("a"))
        assert not narrow.generation.columns.covers(_columns("c"))
    with _publish(
        store, downstream, pl.DataFrame({"x": [1]}), dependencies={upstream.digest: narrow_id}
    ):
        pass
    assert store.slot_status(downstream_slot, "s1").state == "current"

    with _publish(store, upstream, frame.select("c"), columns=_columns("c")) as not_covering:
        # A writer whose columns do not contain the latest generation's keeps its own data.
        assert not_covering.outcome == "superseded"
        assert not_covering.lazy_frame.collect().columns == ["c"]
    with _publish(store, upstream, frame, columns=_columns("a", "b", "c")) as widened:
        assert widened.outcome == "published"
        assert widened.generation.columns == _columns("a", "b", "c")

    assert store.slot_status(downstream_slot, "s1").state == "stale"


def test_automatic_capture_never_replaces_a_fresh_equal_width_generation(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    identity = slot.identity("s1")
    first_id = _published_id(store, identity, pl.DataFrame({"a": [1]}))

    with _publish(store, identity, pl.DataFrame({"a": [9]})) as second:
        assert second.outcome == "superseded"
        assert second.artifact is not None
        artifact_dir = second.artifact.directory
        assert second.lazy_frame.collect()["a"].to_list() == [9]
    assert not artifact_dir.exists()
    assert store.slot_status(slot, "s1").generation.generation_id == first_id

    with _publish(
        store, identity, pl.DataFrame({"a": [7]}), explicit=True, refresh=True
    ) as refreshed:
        assert refreshed.outcome == "published"
        assert refreshed.generation.generation_id != first_id
    with store.lease(identity) as leased:
        assert leased.lazy_frame.collect()["a"].to_list() == [7]


def test_a_stale_generation_is_replaced_without_widening(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    upstream = _slot(tmp_path, "a").identity("s1")
    downstream_slot = _slot(tmp_path, "b")
    downstream = downstream_slot.identity("s1")
    a1 = _published_id(store, upstream, pl.DataFrame({"x": [1]}))
    with _publish(store, downstream, pl.DataFrame({"y": [1]}), dependencies={upstream.digest: a1}):
        pass
    with _publish(
        store, upstream, pl.DataFrame({"x": [2]}), explicit=True, refresh=True
    ) as refreshed:
        a2 = refreshed.generation.generation_id
    assert store.slot_status(downstream_slot, "s1").state == "stale"

    with _publish(
        store, downstream, pl.DataFrame({"y": [2]}), dependencies={upstream.digest: a2}
    ) as rebuilt:
        assert rebuilt.outcome == "published"
    assert store.slot_status(downstream_slot, "s1").state == "current"


def test_a_writer_bound_to_a_replaced_dependency_does_not_publish(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    upstream = _slot(tmp_path, "a").identity("s1")
    downstream_slot = _slot(tmp_path, "b")
    downstream = downstream_slot.identity("s1")
    a1 = _published_id(store, upstream, pl.DataFrame({"x": [1]}))
    with _publish(
        store, upstream, pl.DataFrame({"x": [2]}), explicit=True, refresh=True
    ) as refreshed:
        a2 = refreshed.generation.generation_id
    with _publish(
        store, downstream, pl.DataFrame({"y": [2]}), dependencies={upstream.digest: a2}
    ) as other_writer:
        assert other_writer.outcome == "published"

    with _publish(
        store, downstream, pl.DataFrame({"y": [1]}), dependencies={upstream.digest: a1}
    ) as bound_to_a1:
        assert bound_to_a1.outcome == "superseded"
        assert bound_to_a1.lazy_frame.collect()["y"].to_list() == [1]
    latest = store.slot_status(downstream_slot, "s1").generation
    assert latest is not None
    assert latest.dependencies == {upstream.digest: a2}


def test_a_cleared_dependency_leaves_its_descendant_current(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    upstream_slot = _slot(tmp_path, "a")
    upstream = upstream_slot.identity("s1")
    downstream_slot = _slot(tmp_path, "b")
    a1 = _published_id(store, upstream, pl.DataFrame({"x": [1]}))
    with _publish(
        store,
        downstream_slot.identity("s1"),
        pl.DataFrame({"y": [1]}),
        dependencies={upstream.digest: a1},
    ):
        pass

    store.clear_slot(upstream_slot)

    assert store.slot_status(downstream_slot, "s1").state == "current"


def test_retired_directories_are_swept_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep globs the whole store, so it runs once, not per construction.

    A preview builds several stores for one root and nothing between them can
    retire anything, so repeating the walk is pure cost that grows with the
    store.
    """
    from haute import _node_snapshots as module

    sweeps: list[Path] = []
    real_cleanup = module.NodeSnapshotStore._cleanup_retired

    def counting_cleanup(self: module.NodeSnapshotStore) -> None:
        sweeps.append(self.inputs_root)
        real_cleanup(self)

    monkeypatch.setattr(module.NodeSnapshotStore, "_cleanup_retired", counting_cleanup)
    SourceCacheStore._coordination_by_root.clear()

    first = module.NodeSnapshotStore(tmp_path)
    module.NodeSnapshotStore(tmp_path)
    module.NodeSnapshotStore(tmp_path)

    assert len(sweeps) == 1
    assert sweeps[0] == first.inputs_root


def test_node_cache_keeps_datasets_until_explicit_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_BYTES", "1")
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS", "1")
    store = NodeSnapshotStore(tmp_path)
    identities = [_slot(tmp_path, name).identity("s1") for name in ("a", "b", "c")]
    with _publish(store, identities[0], pl.DataFrame({"a": [0]}), explicit=True):
        pass
    _published_id(store, identities[1], pl.DataFrame({"a": [1]}))
    with store.lease(identities[1]) as leased:
        _published_id(store, identities[2], pl.DataFrame({"a": [2]}))
        assert leased.lazy_frame.collect()["a"].to_list() == [1]
    assert len(store.inventory().owners) == 3
    for i, identity in enumerate(identities):
        with store.lease(identity) as leased:
            assert leased.lazy_frame.collect()["a"].to_list() == [i]
    store.clear_identity(identities[1].digest)
    assert store.latest_generation(identities[1]) is None
    assert store.latest_generation(identities[0]) is not None
    assert store.latest_generation(identities[2]) is not None


def test_publishing_keeps_other_nodes_until_explicit_clear(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    old_slot = _slot(tmp_path, "old")
    recent_slot = _slot(tmp_path, "recent")
    _published_id(store, recent_slot.identity("s1"), pl.DataFrame({"a": [2]}))
    _published_id(store, old_slot.identity("s1"), pl.DataFrame({"a": [1]}))
    new_id = _published_id(store, _slot(tmp_path, "new").identity("s1"), pl.DataFrame({"a": [3]}))

    assert new_id
    assert store.slot_status(old_slot, "s1").state == "current"
    assert store.slot_status(recent_slot, "s1").state == "current"


def test_publishing_preserves_leased_and_pinned_generations(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    pinned_slot = _slot(tmp_path, "pinned")
    leased_slot = _slot(tmp_path, "leased")
    with _publish(store, pinned_slot.identity("s1"), pl.DataFrame({"a": [1]}), explicit=True):
        pass
    _published_id(store, leased_slot.identity("s1"), pl.DataFrame({"a": [2]}))

    with store.lease(leased_slot.identity("s1")):
        published_identity = _slot(tmp_path, "new").identity("s1")
        expected = pl.DataFrame({"a": [3, 4, 5]})
        with _publish(store, published_identity, expected) as publication:
            assert_frame_equal(publication.lazy_frame.collect(), expected)

    assert store.slot_status(pinned_slot, "s1").state == "current"
    assert store.slot_status(leased_slot, "s1").state == "current"


def test_a_pin_passes_to_the_slots_newest_signature(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    with _publish(store, slot.identity("s1"), pl.DataFrame({"a": [1]}), explicit=True) as first:
        assert first.generation.retention == "pinned"
    with _publish(store, slot.identity("s2"), pl.DataFrame({"a": [2]})) as second:
        assert second.generation.retention == "pinned"

    assert store.slot_status(slot, "s2").generation.retention == "pinned"
    # The pin follows the slot's one dataset; the signature it left has none.
    assert store.slot_status(slot, "s1").generation is None


def test_pin_marks_an_existing_current_generation(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    _published_id(store, slot.identity("s1"), pl.DataFrame({"a": [1]}))
    assert store.slot_status(slot, "s1").generation.retention == "automatic"

    store.pin(slot.identity("s1"))

    assert store.slot_status(slot, "s1").generation.retention == "pinned"
    with pytest.raises(SourceCacheGenerationMissingError):
        store.pin(slot.identity("never-built"))


def test_a_second_lease_of_a_verified_generation_does_not_rehash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")
    _published_id(store, identity, pl.DataFrame({"a": [1, 2]}))
    hashes: list[Path] = []
    real_hash = source_cache_module.content_hash

    def counting_hash(path: Path) -> str:
        hashes.append(path)
        return real_hash(path)

    monkeypatch.setattr(source_cache_module, "content_hash", counting_hash)
    before = set(tmp_path.rglob("*"))

    for _ in range(2):
        with store.lease(identity) as leased:
            assert leased.lazy_frame.collect()["a"].to_list() == [1, 2]

    assert hashes == []
    created = {path for path in set(tmp_path.rglob("*")) - before if path.is_file()}
    assert all(".haute_cache" in path.parts and "inputs" in path.parts for path in created)


def test_lease_generation_names_a_non_current_generation_until_it_retires(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    identity = slot.identity("s1")
    first_id = _published_id(store, identity, pl.DataFrame({"a": [1]}))

    with store.lease_generation(identity, first_id) as held:
        with _publish(store, identity, pl.DataFrame({"a": [2]}), explicit=True, refresh=True):
            pass
        with store.lease_generation(identity, first_id) as named:
            assert named.generation_id == first_id
            assert named.lazy_frame.collect()["a"].to_list() == [1]
        store.clear_slot(slot)
        assert held.directory.exists()
        assert held.lazy_frame.collect()["a"].to_list() == [1]

    assert not held.directory.exists()
    with pytest.raises(SourceCacheGenerationMissingError):
        with store.lease_generation(identity, first_id):
            pass
    with pytest.raises(SourceCacheGenerationMissingError):
        with store.lease_generation(identity, "00000000-0000-4000-8000-000000000000"):
            pass


def test_a_lease_marker_names_this_process_and_is_removed_on_release(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")
    _published_id(store, identity, pl.DataFrame({"a": [1]}))

    with store.lease(identity) as leased:
        markers = list(leased.directory.glob(".lease-*"))
        assert [marker.name for marker in markers] == [f".lease-{store._own_token()}"]

    assert list(leased.directory.glob(".lease-*")) == []


def test_published_metadata_records_columns_and_dependencies(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    upstream = _slot(tmp_path, "a").identity("s1")
    a1 = _published_id(store, upstream, pl.DataFrame({"x": [1]}))
    identity = _slot(tmp_path, "b").identity("s1")

    with _publish(
        store,
        identity,
        pl.DataFrame({"y": [1], "z": [2]}),
        columns=_columns("y"),
        dependencies={upstream.digest: a1},
    ) as publication:
        metadata = json.loads(publication.generation.generation.metadata_path.read_text())

    assert metadata["node_output"]["column_set"] == ["y"]
    assert metadata["node_output"]["dependencies"] == {upstream.digest: a1}
    assert metadata["node_output"]["metadata_version"] == 2


def test_publication_rejects_undeclared_columns(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")

    with pytest.raises(ValueError, match="lacks declared columns"):
        _publish(store, identity, pl.DataFrame({"a": [1]}), columns=_columns("a", "missing"))


def _set_last_used(store: NodeSnapshotStore, identity: SourceCacheIdentity, value: float) -> None:
    latest = store.latest_generation(identity)
    assert latest is not None
    os.utime(latest.generation.metadata_path, (value, value))


def test_a_stale_generation_is_never_narrowed(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    upstream = _slot(tmp_path, "a").identity("s1")
    downstream_slot = _slot(tmp_path, "b")
    downstream = downstream_slot.identity("s1")
    wide = pl.DataFrame({"a": [1], "b": [2]})
    a1 = _published_id(store, upstream, pl.DataFrame({"x": [1]}))
    with _publish(
        store, downstream, wide, columns=_columns("a", "b"), dependencies={upstream.digest: a1}
    ):
        pass
    with _publish(
        store, upstream, pl.DataFrame({"x": [2]}), explicit=True, refresh=True
    ) as refreshed:
        a2 = refreshed.generation.generation_id
    assert store.slot_status(downstream_slot, "s1").state == "stale"

    with _publish(
        store,
        downstream,
        wide.select("a"),
        columns=_columns("a"),
        dependencies={upstream.digest: a2},
    ) as narrower:
        assert narrower.outcome == "superseded"
    stale = store.slot_status(downstream_slot, "s1")
    assert stale.state == "stale"
    assert stale.generation.columns == _columns("a", "b")

    with _publish(
        store, downstream, wide, columns=_columns("a", "b"), dependencies={upstream.digest: a2}
    ) as rebuilt:
        assert rebuilt.outcome == "published"
        assert rebuilt.generation.columns == _columns("a", "b")


def _corrupt_latest(store: NodeSnapshotStore, identity: SourceCacheIdentity) -> str:
    latest = store.latest_generation(identity)
    assert latest is not None
    latest.generation.data_paths[0].write_bytes(b"not parquet")
    store._verified_generations.clear()
    return latest.generation_id


def test_an_automatic_capture_surfaces_a_corrupt_generation(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    identity = slot.identity("s1")
    _published_id(store, identity, pl.DataFrame({"a": [1]}))
    corrupt_id = _corrupt_latest(store, identity)
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"a": [2]}).write_parquet(artifact.part_path(0))

    with pytest.raises(source_cache_module.SourceCacheCorruptError):
        store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=False,
            profile=ExecutionProfile.TRAINING_PREP,
        )
    artifact.close()

    assert store._current_generation_id(identity.digest) == corrupt_id
    assert store.slot_status(slot, "s1").state == "corrupt"


def test_an_explicit_build_replaces_a_corrupt_generation(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    identity = slot.identity("s1")
    _published_id(store, identity, pl.DataFrame({"a": [1]}))
    corrupt_id = _corrupt_latest(store, identity)

    with _publish(store, identity, pl.DataFrame({"a": [2]}), explicit=True) as rebuilt:
        assert rebuilt.outcome == "published"
        assert rebuilt.generation.generation_id != corrupt_id

    assert store.slot_status(slot, "s1").state == "current"


def test_only_an_explicit_build_may_refresh(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")
    artifact = store.stage_node_output(identity)

    with pytest.raises(ValueError, match="explicit build"):
        store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=False,
            profile=ExecutionProfile.TRAINING_PREP,
            refresh=True,
        )
    artifact.close()


@pytest.mark.parametrize("failure", ["describe", "slot_index", "superseded_retirement"])
def test_a_failed_publication_handoff_releases_the_publisher_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")
    if failure == "superseded_retirement":
        _published_id(store, identity, pl.DataFrame({"a": [0]}))

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("transient storage failure")

    target = {
        "describe": "describe_generation",
        "slot_index": "_write_slot_index_locked",
        "superseded_retirement": "_retire_generation_locked",
    }[failure]
    monkeypatch.setattr(store, target, fail)

    with pytest.raises(OSError, match="transient"):
        _publish(store, identity, pl.DataFrame({"a": [1]}), explicit=True, refresh=True)

    monkeypatch.undo()
    generation_id = store._current_generation_id(identity.digest)
    if failure == "slot_index":
        assert generation_id is None
        return
    assert generation_id is not None
    assert store._in_process_lease_count(identity.digest, generation_id) == 0
    generation_dir = store._generation_dir(identity.digest, generation_id)
    assert list(generation_dir.glob(".lease-*")) == []


def test_a_lease_validates_only_after_its_marker_protects_the_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")
    generation_id = _published_id(store, identity, pl.DataFrame({"a": [1]}))
    generation_dir = store._generation_dir(identity.digest, generation_id)
    real_validate = store._metadata_from_path
    seen_markers: list[list[str]] = []

    def validating(*args: object, **kwargs: object):
        seen_markers.append(sorted(path.name for path in generation_dir.glob(".lease-*")))
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(store, "_metadata_from_path", validating)

    with store.lease_generation(identity, generation_id):
        pass
    with store.lease(identity):
        pass

    assert seen_markers == [[f".lease-{store._own_token()}"]] * 2


def test_lease_generation_rejects_a_malformed_generation_id(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    identity = _slot(tmp_path).identity("s1")

    with pytest.raises(ValueError, match="generation id"):
        with store.lease_generation(identity, "../../escape"):
            pass


@pytest.mark.parametrize("writer_mode", ["chunked_write", "write_table"])
def test_publication_reads_no_part_in_full_after_writing_it(
    tmp_path: Path,
    writer_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    identity = slot.identity(f"s1_{writer_mode}")

    artifact = store.stage_node_output(identity)
    frame = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    if writer_mode == "chunked_write":
        written = write_parts(artifact.directory, frame.lazy())
        expected_digests = written.digests
        artifact.record_digests(expected_digests)
    else:
        part_file = artifact.part_path(0)
        pq.write_table(frame.to_arrow(), part_file)
        digest = content_hash(part_file)
        expected_digests = {part_file.name: digest}
        artifact.record_digests(expected_digests)

    recorded_paths: list[Path] = []
    real_content_hash = source_cache_module.content_hash

    def recording_content_hash(path: Path) -> str:
        recorded_paths.append(path)
        return real_content_hash(path)

    monkeypatch.setattr("haute._source_cache.content_hash", recording_content_hash)

    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ) as pub:
        assert pub.outcome == "published"
        assert pub.generation is not None
        assert recorded_paths == []
        assert len(pub.generation.generation.metadata.parts) == len(expected_digests)
        for part in pub.generation.generation.metadata.parts:
            assert part.digest == expected_digests[part.name]


def test_marker_input_providers_match_data_input_types_and_exclude_node_output(
    tmp_path: Path,
) -> None:
    from haute._json_shred._snapshots import API_INPUT_PROVIDER

    # Every Data Input type, plus the tables of a structured API Input.
    expected_data_inputs = _DISCRIMINANTS[NodeType.DATA_INPUT][1] | {API_INPUT_PROVIDER}
    assert source_cache_module.NODE_OUTPUT_PROVIDER not in expected_data_inputs
    assert source_cache_module.KNOWN_INPUT_PROVIDERS == expected_data_inputs

    for provider in expected_data_inputs:
        provider_dir = tmp_path / f"input_{provider}"
        provider_dir.mkdir()
        (provider_dir / "provider").write_text(f"{provider}\n", encoding="utf-8")
        assert classify_identity_marker(provider_dir) == "input"

    node_out_dir = tmp_path / "node_out"
    node_out_dir.mkdir()
    (node_out_dir / "provider").write_text(
        f"{source_cache_module.NODE_OUTPUT_PROVIDER}\n", encoding="utf-8"
    )
    assert classify_identity_marker(node_out_dir) == "node_output"
    assert classify_identity_marker(node_out_dir) != "input"


def test_a_holder_that_dies_leaves_nothing_clear_slot_cannot_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A superseded signature a reader held stays reachable after that reader dies.

    Publication leaves such a signature indexed and unpointed rather than
    dropping it: if it were dropped, a holder that died without releasing would
    strand its generation where `clear_slot` (which walks the index) and the
    retired sweep (which knows only `.retired-*`) can never see it — and the
    node would show two datasets with a Clear that cannot remove one.
    """
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("s1")
    first_id = _published_id(store, first, pl.DataFrame({"a": [1, 2, 3]}))

    # A reader in another process holds s1: its marker is present and, while the
    # publish runs, its process token reads as alive.
    token = "deadbeefdead"
    generation_dir = store.inputs_root / first.digest / "generations" / first_id
    (generation_dir / f".lease-{token}").touch()
    alive = store._token_alive
    monkeypatch.setattr(
        store, "_token_alive", lambda candidate: True if candidate == token else alive(candidate)
    )

    _published_id(store, slot.identity("s2"), pl.DataFrame({"a": [4, 5, 6]}))
    assert _identity_bytes(store, first) > 0, "a held generation survives the publish, by design"

    monkeypatch.setattr(store, "_token_alive", alive)  # the holder dies without releasing

    store.clear_slot(slot)
    assert _identity_bytes(store, first) == 0
    assert all(owner.generations <= 1 for owner in store.inventory().owners)


@pytest.mark.parametrize("failure_write", [1, 2])
@pytest.mark.parametrize("previous", [False, True])
def test_publication_index_failure_remains_discoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_write: int, previous: bool
) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first, second = slot.identity("first"), slot.identity("second")
    if previous:
        with _publish(store, first, pl.DataFrame({"a": [1]}), explicit=True):
            pass
    write_index = store._write_slot_index_locked
    calls = 0

    def fail_index(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failure_write:
            raise OSError("index unavailable")
        return write_index(*args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(store, "_write_slot_index_locked", fail_index)
        with pytest.raises(OSError, match="index unavailable"):
            _publish(store, second, pl.DataFrame({"a": [2]}), explicit=True)

    if failure_write == 1:
        assert store._current_generation_id(second.digest) is None
        if previous:
            with store.lease(first) as generation:
                assert generation.lazy_frame.collect()["a"].to_list() == [1]
            assert store.latest_generation(first).retention == "pinned"
    else:
        with store.lease(second) as generation:
            assert generation.lazy_frame.collect()["a"].to_list() == [2]
        assert store.latest_generation(second).retention == "pinned"
    store.clear_slot(slot)
    assert _slot_generation_bytes(store, tmp_path) == 0


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("same_signature", [False, True])
def test_failed_pointer_preserves_previous_data_and_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool, same_signature: bool
) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("first")
    second = first if same_signature else slot.identity("second")
    with _publish(store, first, pl.DataFrame({"a": [1]}), explicit=explicit):
        pass
    old_index = store._read_slot_index(slot)

    def fail_pointer(*args, **kwargs):
        raise OSError("pointer unavailable")

    with monkeypatch.context() as patcher:
        patcher.setattr(store, "_write_pointer_locked", fail_pointer)
        with pytest.raises(OSError, match="pointer unavailable"):
            _publish(store, second, pl.DataFrame({"a": [2]}), explicit=True, refresh=True)
    with store.lease(first) as generation:
        assert generation.lazy_frame.collect()["a"].to_list() == [1]
    assert store._read_slot_index(slot)["pinned_identity"] == old_index["pinned_identity"]
    assert store.latest_generation(first).retention == ("pinned" if explicit else "automatic")
    store.clear_slot(slot)
    assert _slot_generation_bytes(store, tmp_path) == 0


@pytest.mark.parametrize("pinned", [False, True])
def test_replacement_preserves_an_old_lease_until_release(tmp_path: Path, pinned: bool) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first, second = slot.identity("first"), slot.identity("second")
    frame = pl.DataFrame({"a": [1, 2, 3]})
    with _publish(store, first, frame, explicit=pinned):
        pass
    with store.lease(first) as leased:
        with _publish(store, second, frame, explicit=pinned) as publication:
            assert publication.outcome == "published"
        assert_frame_equal(leased.lazy_frame.collect(), frame)
    assert _identity_bytes(store, first) == 0
    assert store.latest_generation(second).retention == ("pinned" if pinned else "automatic")


def test_replacement_keeps_unrelated_datasets(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("first")
    old = pl.DataFrame({"a": [1]})
    with _publish(store, first, old):
        pass
    unrelated = _slot(tmp_path, "unrelated").identity("first")
    with _publish(store, unrelated, pl.DataFrame({"b": [2]})):
        pass
    second = slot.identity("second")
    replacement = pl.DataFrame({"a": range(100_000)})
    with _publish(store, second, replacement):
        pass
    assert store.latest_generation(first) is None
    with store.lease(second) as generation:
        assert_frame_equal(generation.lazy_frame.collect(), replacement)
    with store.lease(unrelated) as generation:
        assert_frame_equal(generation.lazy_frame.collect(), pl.DataFrame({"b": [2]}))


def _crash_before_publication_pointer(project_root: str) -> None:
    root = Path(project_root)
    store = NodeSnapshotStore(root)

    def terminate(*_args, **_kwargs):
        os._exit(29)

    store._write_pointer_locked = terminate
    _publish(store, _slot(root).identity("interrupted"), pl.DataFrame({"a": [2]}), explicit=True)


def test_interrupted_publication_remains_indexed_and_clearable(tmp_path):
    import multiprocessing

    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("first")
    with _publish(store, first, pl.DataFrame({"a": [1]}), explicit=True):
        pass
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_before_publication_pointer,
        args=(str(tmp_path),),
    )
    process.start()
    process.join(30)
    if process.is_alive():
        process.kill()
        process.join(10)
        pytest.fail("publication process did not reach the interruption point")
    assert process.exitcode == 29
    interrupted = slot.identity("interrupted")
    assert interrupted.digest in store._read_slot_index(slot)["identities"]
    assert _identity_bytes(store, interrupted) > 0
    with store.lease(first) as generation:
        assert generation.lazy_frame.collect()["a"].to_list() == [1]
    assert store.latest_generation(first).retention == "pinned"
    store.clear_slot(slot)
    assert _slot_generation_bytes(store, tmp_path) == 0


def test_pointer_and_retention_rollback_failure_preserve_existing_generation(tmp_path, monkeypatch):
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("first")
    with _publish(store, first, pl.DataFrame({"a": [1]}), explicit=True):
        pass
    real_write = store._write_slot_index_locked
    calls = 0

    def write_index(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("rollback failed")
        return real_write(*args)

    def fail_pointer(*_args):
        raise OSError("pointer failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(store, "_write_slot_index_locked", write_index)
        patcher.setattr(store, "_write_pointer_locked", fail_pointer)
        with pytest.raises(OSError, match="pointer failed") as error:
            _publish(store, slot.identity("second"), pl.DataFrame({"a": [2]}), explicit=True)
    assert any("rollback failed" in note for note in error.value.__notes__)
    with store.lease(first) as generation:
        assert generation.lazy_frame.collect()["a"].to_list() == [1]
    assert store.latest_generation(first).retention == "pinned"
    store.clear_slot(slot)
    assert _slot_generation_bytes(store, tmp_path) == 0
