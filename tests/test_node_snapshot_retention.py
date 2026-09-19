"""Node-output snapshot store contracts within one process (CACHE-S01)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from polars.testing import assert_frame_equal

import haute._source_cache as source_cache_module
from haute._chunked_writes import write_parts
from haute._execution_context import ExecutionProfile
from haute._hashing import content_hash
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
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
)


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


def test_edit_and_revert_finds_the_earlier_signature_without_a_build(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = _slot(tmp_path)
    first = slot.identity("s1")
    second = slot.identity("s2")

    assert store.slot_status(slot, "s1").state == "missing"
    first_id = _published_id(store, first, pl.DataFrame({"a": [1]}))
    assert store.slot_status(slot, "s2").state == "stale"
    _published_id(store, second, pl.DataFrame({"a": [2]}))

    reverted = store.slot_status(slot, "s1")
    assert reverted.state == "current"
    assert reverted.generation is not None
    assert reverted.generation.generation_id == first_id
    assert store.slot_status(slot, "s2").state == "current"
    assert store.slot_status(slot, "s3").state == "stale"

    store.clear_slot(slot)
    assert store.slot_status(slot, "s1").state == "missing"
    assert store.slot_status(slot, "s2").state == "missing"


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


def test_eviction_retires_the_least_recently_used_unleased_automatic_generation(
    tmp_path: Path,
) -> None:
    store = NodeSnapshotStore(tmp_path, max_generations=2)
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
    store = NodeSnapshotStore(tmp_path, max_generations=2)
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
    store = NodeSnapshotStore(tmp_path, max_generations=1)
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

    assert store.slot_status(slot, "s1").generation.retention == "automatic"
    assert store.slot_status(slot, "s2").generation.retention == "pinned"


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
    store.max_bytes = quota

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
