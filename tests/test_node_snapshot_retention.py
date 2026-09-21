"""Node-output snapshot store contracts within one process (CACHE-S01)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pyarrow.parquet as pq
import pytest
from polars.testing import assert_frame_equal

import haute._source_cache as source_cache_module
from haute._chunked_writes import write_parts
from haute._execution_context import ExecutionProfile
from haute._file_ops import atomic_write_text
from haute._hashing import content_hash
from haute._node_config_recovery import _DISCRIMINANTS
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotPublication,
    NodeSnapshotQuotaRejectedError,
    NodeSnapshotSlot,
    NodeSnapshotStore,
    snapshot_read_classes,
    snapshot_write_class,
)
from haute._source_cache import (
    SourceCacheBuildContext,
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
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
    module._COORDINATION.clear()

    first = module.NodeSnapshotStore(tmp_path)
    module.NodeSnapshotStore(tmp_path)
    module.NodeSnapshotStore(tmp_path)

    assert len(sweeps) == 1
    assert sweeps[0] == first.inputs_root


def test_eviction_retires_the_least_recently_used_unleased_automatic_generation(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path, node_output_max_generations=2)
    old_slot = _slot(tmp_path, "old")
    recent_slot = _slot(tmp_path, "recent")
    # Publish in the opposite order to use, so eviction follows last use, not creation.
    _published_id(store, recent_slot.identity("s1"), pl.DataFrame({"a": [2]}))
    _published_id(store, old_slot.identity("s1"), pl.DataFrame({"a": [1]}))
    _set_last_used(store, old_slot.identity("s1"), 100.0)
    _set_last_used(store, recent_slot.identity("s1"), 200.0)

    new_id = _published_id(store, _slot(tmp_path, "new").identity("s1"), pl.DataFrame({"a": [3]}))

    assert new_id
    assert store.slot_status(old_slot, "s1").state == "missing"
    assert store.slot_status(recent_slot, "s1").state == "current"


def test_eviction_spares_leased_and_pinned_generations_and_then_rejects(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path, node_output_max_generations=2)
    pinned_slot = _slot(tmp_path, "pinned")
    leased_slot = _slot(tmp_path, "leased")
    with _publish(store, pinned_slot.identity("s1"), pl.DataFrame({"a": [1]}), explicit=True):
        pass
    _published_id(store, leased_slot.identity("s1"), pl.DataFrame({"a": [2]}))

    with store.lease(leased_slot.identity("s1")):
        rejected_identity = _slot(tmp_path, "new").identity("s1")
        expected = pl.DataFrame({"a": [3, 4, 5]})
        with pytest.raises(NodeSnapshotQuotaRejectedError) as raised:
            _publish(store, rejected_identity, expected)
        artifact = raised.value.artifact
        # The staged artifact is handed over intact, never deleted.
        assert_frame_equal(artifact.lazy_frame().collect(), expected)
        artifact_dir = artifact.directory
        artifact.close()
        assert not artifact_dir.exists()

    assert store.slot_status(pinned_slot, "s1").state == "current"
    assert store.slot_status(leased_slot, "s1").state == "current"


def test_quota_rejection_is_the_existing_quota_error(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path, node_output_max_generations=1)
    with _publish(
        store, _slot(tmp_path, "a").identity("s1"), pl.DataFrame({"a": [1]}), explicit=True
    ):
        pass

    with pytest.raises(source_cache_module.SourceCacheQuotaExceededError) as raised:
        _publish(store, _slot(tmp_path, "b").identity("s1"), pl.DataFrame({"a": [1]}))
    raised.value.artifact.close()


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


def test_eviction_sizes_a_generation_by_all_of_its_parts(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    old_slot = _slot(tmp_path, "old")
    new_slot = _slot(tmp_path, "new")
    old_identity = old_slot.identity("s1")
    new_identity = new_slot.identity("s1")

    df_part0 = pl.DataFrame({"a": list(range(100)), "b": [1.0] * 100})
    df_part1 = pl.DataFrame({"a": list(range(100, 200)), "b": [2.0] * 100})
    df_part2 = pl.DataFrame({"a": list(range(200, 300)), "b": [3.0] * 100})

    # Stage and write 3 parts for old_identity
    artifact_old = store.stage_node_output(old_identity)
    df_part0.write_parquet(artifact_old.part_path(0))
    df_part1.write_parquet(artifact_old.part_path(1))
    df_part2.write_parquet(artifact_old.part_path(2))

    part0_old_size = artifact_old.part_path(0).stat().st_size
    total_old_size = sum(artifact_old.part_path(i).stat().st_size for i in range(3))

    # Publish old_identity as automatic generation
    with store.publish_node_output(
        old_identity,
        artifact_old,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ) as old_pub:
        assert old_pub.outcome == "published"
        assert len(old_pub.generation.generation.data_paths) == 3

    _set_last_used(store, old_identity, 100.0)

    # Stage and write 3 parts for new_identity
    artifact_new = store.stage_node_output(new_identity)
    df_part0.write_parquet(artifact_new.part_path(0))
    df_part1.write_parquet(artifact_new.part_path(1))
    df_part2.write_parquet(artifact_new.part_path(2))
    total_new_size = sum(artifact_new.part_path(i).stat().st_size for i in range(3))

    # Set quota so that sizing by first part only would admit without eviction:
    # part0_old_size + total_new_size <= max_bytes < total_old_size + total_new_size
    quota = part0_old_size + total_new_size
    assert quota < total_old_size + total_new_size
    assert quota >= total_new_size
    store.node_output_max_bytes = quota

    with store.publish_node_output(
        new_identity,
        artifact_new,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ) as new_pub:
        assert new_pub.outcome == "published"
        assert len(new_pub.generation.generation.data_paths) == 3

    assert store.slot_status(old_slot, "s1").state == "missing"
    assert store.slot_status(new_slot, "s1").state == "current"


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


def test_input_snapshots_do_not_consume_node_output_slots(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path, max_generations=10, node_output_max_generations=1)
    for i in range(3):
        _build_input_snapshot(store, _input_identity(f"inp_{i}"), pl.DataFrame({"x": [i]}))
    node_id = _slot(tmp_path, "node").identity("s1")
    with _publish(store, node_id, pl.DataFrame({"a": [42]})) as pub:
        assert pub.outcome == "published"
        assert pub.generation is not None
    with store.lease(node_id) as leased:
        assert_frame_equal(leased.lazy_frame.collect(), pl.DataFrame({"a": [42]}))
    assert store.slot_status(_slot(tmp_path, "node"), "s1").state == "current"


def test_node_outputs_do_not_consume_input_snapshot_slots(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path, max_generations=1, node_output_max_generations=10)
    for i in range(3):
        _published_id(
            store,
            _slot(tmp_path, f"node_{i}").identity("s1"),
            pl.DataFrame({"a": [i]}),
        )
    inp = _input_identity("single_input")
    _build_input_snapshot(store, inp, pl.DataFrame({"val": [99]}))
    with store.lease(inp) as leased:
        assert_frame_equal(leased.lazy_frame.collect(), pl.DataFrame({"val": [99]}))
    assert store.open_generation(inp).generation_id is not None


def test_a_node_output_admission_never_evicts_an_input_snapshot(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path, max_generations=10, node_output_max_generations=2)
    inp1 = _input_identity("inp1")
    inp2 = _input_identity("inp2")
    _build_input_snapshot(store, inp1, pl.DataFrame({"x": [10]}))
    _build_input_snapshot(store, inp2, pl.DataFrame({"x": [20]}))

    node1 = _slot(tmp_path, "n1").identity("s1")
    node2 = _slot(tmp_path, "n2").identity("s1")
    _published_id(store, node1, pl.DataFrame({"a": [1]}))
    _published_id(store, node2, pl.DataFrame({"a": [2]}))
    _set_last_used(store, node1, 100.0)
    _set_last_used(store, node2, 200.0)

    node3 = _slot(tmp_path, "n3").identity("s1")
    _published_id(store, node3, pl.DataFrame({"a": [3]}))

    assert store.slot_status(_slot(tmp_path, "n1"), "s1").state == "missing"
    assert store.slot_status(_slot(tmp_path, "n2"), "s1").state == "current"
    assert store.slot_status(_slot(tmp_path, "n3"), "s1").state == "current"

    with store.lease(inp1) as gen1:
        assert_frame_equal(gen1.lazy_frame.collect(), pl.DataFrame({"x": [10]}))
    with store.lease(inp2) as gen2:
        assert_frame_equal(gen2.lazy_frame.collect(), pl.DataFrame({"x": [20]}))


def test_each_budget_counts_only_its_own_bytes_including_staging(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)

    node_gen_id = _slot(tmp_path, "gen_node").identity("s1")
    _published_id(store, node_gen_id, pl.DataFrame({"a": [1] * 50}))

    art_node_extra = store.stage_node_output(node_gen_id)
    pl.DataFrame({"extra": [2] * 50}).write_parquet(art_node_extra.part_path(0))

    node_staging_only = _slot(tmp_path, "stage_only_node").identity("s1")
    art_node_only = store.stage_node_output(node_staging_only)
    pl.DataFrame({"only": [3] * 50}).write_parquet(art_node_only.part_path(0))

    inp_gen_id = _input_identity("gen_inp")
    _build_input_snapshot(store, inp_gen_id, pl.DataFrame({"x": [1] * 50}))

    staging_inp_extra = store.identity_path(inp_gen_id) / ".staging-retextra"
    staging_inp_extra.mkdir()
    pl.DataFrame({"extra_in": [2] * 50}).write_parquet(staging_inp_extra / "part-00000.parquet")

    inp_staging_only = _input_identity("stage_only_inp")
    staging_only_dir = store.identity_path(inp_staging_only)
    staging_only_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(staging_only_dir / "provider", "file\n")
    staging_inp_only = staging_only_dir / ".staging-retstage"
    staging_inp_only.mkdir()
    pl.DataFrame({"only_in": [3] * 50}).write_parquet(staging_inp_only / "part-00000.parquet")

    _node_count, node_bytes = store._bucket_usage("node_output")
    _inp_count, inp_bytes = store._bucket_usage("input")
    assert node_bytes > 0
    assert inp_bytes > 0

    # Add large staging to input side so inp_bytes alone exceeds node limit
    art_node_overflow_check = store.identity_path(inp_staging_only) / ".staging-huge"
    art_node_overflow_check.mkdir()
    # Re-rooted under tmp_path: the scanner cannot see the fixture through the
    # store's helper, and relative_to raises if the path ever escaped.
    (tmp_path / art_node_overflow_check.relative_to(tmp_path) / "data.parquet").write_bytes(
        b"x" * 20_000
    )
    _inp_count, inp_bytes = store._bucket_usage("input")

    # Pin node output limit: node side fits, but input bytes alone would exceed it
    store.node_output_max_bytes = node_bytes + 2000
    assert inp_bytes > store.node_output_max_bytes

    new_node_id = _slot(tmp_path, "new_node_ok").identity("s1")
    with _publish(store, new_node_id, pl.DataFrame({"new_node": [8] * 10})) as pub:
        assert pub.outcome == "published"

    # Add large staging to node side so node_bytes alone exceeds input limit
    node_huge = store.identity_path(node_staging_only) / ".staging-huge-node"
    node_huge.mkdir()
    # Re-rooted under tmp_path: the scanner cannot see the fixture through the
    # store's helper, and relative_to raises if the path ever escaped.
    (tmp_path / node_huge.relative_to(tmp_path) / "data.parquet").write_bytes(b"x" * 40_000)
    _node_count, node_bytes = store._bucket_usage("node_output")

    # Pin input limit: input side fits, but node bytes alone would exceed it
    store.max_bytes = inp_bytes + 2000
    assert node_bytes > store.max_bytes

    new_inp_id = _input_identity("new_inp_ok")
    _build_input_snapshot(store, new_inp_id, pl.DataFrame({"new_in": [9] * 10}))
    with store.lease(new_inp_id) as gen:
        assert_frame_equal(gen.lazy_frame.collect(), pl.DataFrame({"new_in": [9] * 10}))

    # A publication whose own bucket overflows is still refused:
    _inp_count, current_inp_bytes = store._bucket_usage("input")
    store.max_bytes = current_inp_bytes + 100
    overflow_inp = _input_identity("overflow_inp")
    with pytest.raises(source_cache_module.SourceCacheQuotaExceededError):
        _build_input_snapshot(store, overflow_inp, pl.DataFrame({"ov": [1] * 50}))

    # Node side: pin existing candidates or make them unevictable by leasing them
    _node_count, current_node_bytes = store._bucket_usage("node_output")
    store.node_output_max_bytes = current_node_bytes + 100
    with (
        store.lease(node_gen_id),
        store.lease(new_node_id),
    ):
        overflow_node = _slot(tmp_path, "overflow_node").identity("s1")
        with pytest.raises(NodeSnapshotQuotaRejectedError) as raised:
            _publish(store, overflow_node, pl.DataFrame({"ov_node": [1] * 50}))
        raised.value.artifact.close()

    # The other bucket's snapshots are untouched afterwards
    with store.lease(node_gen_id) as leased_node:
        assert_frame_equal(leased_node.lazy_frame.collect(), pl.DataFrame({"a": [1] * 50}))
    with store.lease(inp_gen_id) as leased_inp:
        assert_frame_equal(leased_inp.lazy_frame.collect(), pl.DataFrame({"x": [1] * 50}))


def test_an_identity_whose_marker_is_unreadable_is_charged_to_both_budgets(
    tmp_path: Path,
) -> None:
    # Note: this file's existing corruption helper (_corrupt_latest) overwrites
    # Parquet data and leaves the marker readable, so it does not exercise this.
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path, "unreadable_marker")
    identity = slot.identity("s1")

    _published_id(store, identity, pl.DataFrame({"a": [1] * 50}))
    gen_id = store._current_generation_id(identity.digest)
    assert gen_id is not None
    gen_dir = store._generation_dir(identity.digest, gen_id)
    gen_size = source_cache_module.generation_bytes(gen_dir)
    assert gen_size > 0

    # Corrupt the marker file
    marker_path = store.identity_path(identity) / "provider"
    assert marker_path.exists()
    # Re-rooted under tmp_path: the scanner cannot see the fixture through the
    # store's helper, and relative_to raises if the path ever escaped.
    (tmp_path / marker_path.relative_to(tmp_path)).write_text(
        "unknown_provider_value\n", encoding="utf-8"
    )
    assert classify_identity_marker(store.identity_path(identity)) == "unknown"

    node_count, node_bytes = store._bucket_usage("node_output")
    inp_count, inp_bytes = store._bucket_usage("input")
    assert node_count >= 1
    assert node_bytes >= gen_size
    assert inp_count >= 1
    assert inp_bytes >= gen_size

    # Real admission in both buckets sees the unreadable identity's bytes and count:
    # 1. Input bucket: building an input snapshot sees the unreadable identity
    inp_id = _input_identity("inp_charged_unknown")
    inp_frame = pl.DataFrame({"x": [1] * 50})
    store.max_generations = 1
    with pytest.raises(source_cache_module.SourceCacheQuotaExceededError):
        _build_input_snapshot(store, inp_id, inp_frame)
    store.max_generations = 64

    store.max_bytes = gen_size + 100
    with pytest.raises(source_cache_module.SourceCacheQuotaExceededError):
        _build_input_snapshot(store, inp_id, inp_frame)
    store.max_bytes = 20 * 1024 * 1024 * 1024

    # 2. Node-output bucket: publishing a new node output sees the unreadable identity
    other_node_slot = _slot(tmp_path, "other_node")
    other_node_ident = other_node_slot.identity("s1")
    other_frame = pl.DataFrame({"b": [2] * 50})
    store.node_output_max_generations = 1
    with store.lease(identity):
        with pytest.raises(NodeSnapshotQuotaRejectedError) as raised:
            _publish(store, other_node_ident, other_frame)
        raised.value.artifact.close()
    store.node_output_max_generations = 512

    store.node_output_max_bytes = gen_size + 100
    with store.lease(identity):
        with pytest.raises(NodeSnapshotQuotaRejectedError) as raised:
            _publish(store, other_node_ident, other_frame)
        raised.value.artifact.close()
    store.node_output_max_bytes = 40 * 1024 * 1024 * 1024

    # An explicit refresh of that identity large enough to exceed the limit without free
    # credit is refused.
    # Existing generation is 498 bytes; replacement is 1807 bytes.
    # Correct accounting projection: 1807 bytes (counts the 498-byte unreadable
    # generation and refunds it upon replacement: 498 - 498 + 1807 = 1807).
    # Wrongly-credited projection: 1309 bytes (did not count the 498-byte
    # generation, but subtracted it upon replacement anyway: 0 - 498 + 1807 = 1309).
    # Setting node_output_max_bytes = 1500 sits strictly between the two projections:
    # the correct accounting refuses (1807 > 1500), while the wrongly-credited
    # projection would admit (1309 <= 1500).
    store.node_output_max_bytes = 1500
    large_frame = pl.DataFrame({"a": list(range(500))})
    with pytest.raises(NodeSnapshotQuotaRejectedError) as raised:
        _publish(store, identity, large_frame, explicit=True, refresh=True)
    raised.value.artifact.close()


def test_a_filesystem_error_during_admission_walk_fails_publication_and_preserves_generations(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot1 = _slot(tmp_path, "node1")
    ident1 = slot1.identity("s1")
    gen1_id = _published_id(store, ident1, pl.DataFrame({"a": [1] * 50}))
    gen1_dir = store._generation_dir(ident1.digest, gen1_id)
    part_files = tuple(gen1_dir.glob("part-*.parquet"))
    assert len(part_files) == 1
    part_file = part_files[0]
    gen1_size = source_cache_module.generation_bytes(gen1_dir)
    assert gen1_size > 0

    inp_id = _input_identity("inp1")
    _build_input_snapshot(store, inp_id, pl.DataFrame({"x": [10, 20, 30]}))

    slot2 = _slot(tmp_path, "node2")
    ident2 = slot2.identity("s2")
    store.node_output_max_bytes = gen1_size + 100

    target_norm = os.path.normcase(str(part_file))
    orig_stat = Path.stat

    def selective_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if os.path.normcase(str(self)) == target_norm:
            raise PermissionError(f"simulated permission denied on {self.name}")
        return orig_stat(self, *args, **kwargs)

    with patch.object(Path, "stat", selective_stat):
        with pytest.raises(PermissionError, match="simulated permission denied"):
            _publish(store, ident2, pl.DataFrame({"b": [2] * 50}))

    assert store._current_generation_id(ident2.digest) is None

    assert store._current_generation_id(ident1.digest) == gen1_id
    with store.lease(ident1) as leased1:
        assert_frame_equal(leased1.lazy_frame.collect(), pl.DataFrame({"a": [1] * 50}))

    with store.lease(inp_id) as leased_inp:
        assert_frame_equal(leased_inp.lazy_frame.collect(), pl.DataFrame({"x": [10, 20, 30]}))


def test_a_generation_with_unreadable_metadata_stays_in_its_marked_budget(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path, "corrupt_meta")
    identity = slot.identity("s1")

    _published_id(store, identity, pl.DataFrame({"a": [1] * 50}))
    gen_id = store._current_generation_id(identity.digest)
    assert gen_id is not None
    gen_dir = store._generation_dir(identity.digest, gen_id)
    gen_size = source_cache_module.generation_bytes(gen_dir)
    assert gen_size > 0

    marker_path = store.identity_path(identity) / "provider"
    assert marker_path.read_text(encoding="utf-8").strip() == "node_output"

    # Re-rooted under tmp_path: the scanner cannot see the fixture through the
    # store's helper, and relative_to raises if the path ever escaped.
    (tmp_path / gen_dir.relative_to(tmp_path) / "meta.json").write_text(
        "corrupted json {", encoding="utf-8"
    )

    node_count, node_bytes = store._bucket_usage("node_output")
    inp_count, inp_bytes = store._bucket_usage("input")

    assert node_count == 1
    assert node_bytes == gen_size
    assert inp_count == 0
    assert inp_bytes == 0


def test_the_node_output_quota_reads_its_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = NodeSnapshotStore(tmp_path)
    assert store.node_output_max_bytes == 40 * 1024 * 1024 * 1024
    assert store.node_output_max_generations == 512

    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_BYTES", "2048")
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS", "16")
    env_store = NodeSnapshotStore(tmp_path / "env")
    assert env_store.node_output_max_bytes == 2048
    assert env_store.node_output_max_generations == 16

    override_store = NodeSnapshotStore(
        tmp_path / "override",
        node_output_max_bytes=4096,
        node_output_max_generations=32,
    )
    assert override_store.node_output_max_bytes == 4096
    assert override_store.node_output_max_generations == 32

    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_BYTES", "invalid")
    with pytest.raises(RuntimeError):
        NodeSnapshotStore(tmp_path / "inv_bytes")
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_BYTES", "0")
    with pytest.raises(RuntimeError):
        NodeSnapshotStore(tmp_path / "zero_bytes")
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_BYTES", "2048")

    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS", "-1")
    with pytest.raises(RuntimeError):
        NodeSnapshotStore(tmp_path / "neg_gen")
    monkeypatch.undo()

    for bad_bytes in (0, -1, True, False):
        with pytest.raises(
            ValueError, match="source-cache node_output_max_bytes must be a positive integer"
        ):
            NodeSnapshotStore(tmp_path / "bad_b", node_output_max_bytes=bad_bytes)  # type: ignore[arg-type]

    for bad_gens in (0, -5, True, False):
        with pytest.raises(
            ValueError, match="source-cache node_output_max_generations must be a positive integer"
        ):
            NodeSnapshotStore(tmp_path / "bad_g", node_output_max_generations=bad_gens)  # type: ignore[arg-type]


def test_marker_input_providers_match_data_input_types_and_exclude_node_output(
    tmp_path: Path,
) -> None:
    expected_data_inputs = _DISCRIMINANTS[NodeType.DATA_INPUT][1]
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


def test_the_node_output_quota_does_not_inherit_input_limits(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path, max_bytes=1024, max_generations=2)
    assert store.max_bytes == 1024
    assert store.max_generations == 2
    assert store.node_output_max_bytes == 40 * 1024 * 1024 * 1024
    assert store.node_output_max_generations == 512


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
