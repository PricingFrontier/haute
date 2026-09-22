"""A generation in the retired single-file layout is absent, never corruption (CACHE-S10)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotSlot,
    NodeSnapshotStore,
)
from haute._source_cache import (
    SourceCacheBuildContext,
    SourceCacheCorruptError,
    SourceCacheGeneration,
    SourceCacheIdentity,
    SourceCacheStore,
)


class _Builder:
    def __init__(self, frame: pl.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        self.calls += 1
        return self.frame.lazy()


def _context() -> SourceCacheBuildContext:
    return SourceCacheBuildContext(profile=ExecutionProfile.LAZY_SINK, build_class="bounded")


def _retire_to_single_file_layout(generation: SourceCacheGeneration) -> None:
    """Rewrite a generation as the pre-CACHE-S10 writer left it: one data.parquet."""
    directory = generation.directory
    (single,) = generation.data_paths
    data = directory / "data.parquet"
    single.replace(data)
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("layout_version")
    meta.pop("parts")
    meta["data_sha256"] = hashlib.sha256(data.read_bytes()).hexdigest()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _retire_to_layout_2(generation: SourceCacheGeneration) -> None:
    """Rewrite a generation as layout_version 2 with sha256."""
    directory = generation.directory
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["layout_version"] = 2
    for part in meta["parts"]:
        part["sha256"] = hashlib.sha256((directory / part["name"]).read_bytes()).hexdigest()
        part.pop("digest", None)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _corrupt_part_digest(generation: SourceCacheGeneration) -> None:
    """Rewrite a generation's metadata with a mismatching part digest."""
    directory = generation.directory
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    real_digest = meta["parts"][0]["digest"]
    new_first = "b" if real_digest[0] == "a" else "a"
    meta["parts"][0]["digest"] = new_first + real_digest[1:]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_a_single_file_input_snapshot_is_missing_and_rebuilt(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = SourceCacheIdentity(provider="file", descriptor={"path": "rows.parquet"})
    builder = _Builder(pl.DataFrame({"id": [1, 2]}))
    retired = store.build(identity, builder, context=_context())
    _retire_to_single_file_layout(retired)
    fresh_store = SourceCacheStore(tmp_path)

    assert fresh_store.status(identity).state == "missing"
    with pytest.raises(FileNotFoundError), fresh_store.lease(identity):
        pass

    assert fresh_store.leased_generation_ids(identity) == frozenset()
    assert not list(retired.directory.glob(".lease-*"))

    rebuilt = fresh_store.build(identity, builder, context=_context())

    assert builder.calls == 2
    assert rebuilt.generation_id != retired.generation_id
    assert rebuilt.lazy_frame.collect()["id"].to_list() == [1, 2]
    assert fresh_store.status(identity).state == "ready"


def test_a_single_file_node_snapshot_is_absent_and_replaced(tmp_path: Path) -> None:
    store = NodeSnapshotStore(tmp_path)
    slot = NodeSnapshotSlot(
        pipeline_source_file=str(tmp_path / "main.py"),
        node_id="join",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    identity = slot.identity("s1")

    def publish(frame: pl.DataFrame) -> str:
        artifact = store.stage_node_output(identity)
        frame.write_parquet(artifact.part_path(0))
        with store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=False,
            profile=ExecutionProfile.NODE_SNAPSHOT,
        ) as publication:
            assert publication.outcome == "published"
            assert publication.generation is not None
            return publication.generation.generation_id

    first = publish(pl.DataFrame({"id": [1]}))
    latest = store.latest_generation(identity)
    assert latest is not None and latest.generation_id == first
    _retire_to_single_file_layout(latest.generation)
    fresh_store = NodeSnapshotStore(tmp_path)

    assert fresh_store.latest_generation(identity) is None
    assert fresh_store.slot_status(slot, "s1").state == "missing"
    with pytest.raises(FileNotFoundError), fresh_store.lease(identity):
        pass

    store = fresh_store
    replacement = publish(pl.DataFrame({"id": [2]}))

    current = store.latest_generation(identity)
    assert current is not None and current.generation_id == replacement != first
    assert current.lazy_frame.collect()["id"].to_list() == [2]


def test_layout_2_generation_reads_as_absent(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = SourceCacheIdentity(provider="file", descriptor={"path": "rows.parquet"})
    builder = _Builder(pl.DataFrame({"id": [1, 2]}))
    retired = store.build(identity, builder, context=_context())
    _retire_to_layout_2(retired)
    fresh_store = SourceCacheStore(tmp_path)

    assert fresh_store.status(identity).state == "missing"
    with pytest.raises(FileNotFoundError), fresh_store.lease(identity):
        pass

    assert fresh_store.leased_generation_ids(identity) == frozenset()
    assert not list(retired.directory.glob(".lease-*"))

    rebuilt = fresh_store.build(identity, builder, context=_context())

    assert builder.calls == 2
    assert rebuilt.generation_id != retired.generation_id
    assert rebuilt.lazy_frame.collect()["id"].to_list() == [1, 2]
    assert fresh_store.status(identity).state == "ready"


def test_xxh64_digest_mismatch_is_corruption(tmp_path: Path) -> None:
    store = SourceCacheStore(tmp_path)
    identity = SourceCacheIdentity(provider="file", descriptor={"path": "rows.parquet"})
    builder = _Builder(pl.DataFrame({"id": [1, 2]}))
    gen = store.build(identity, builder, context=_context())

    _corrupt_part_digest(gen)

    fresh_store = SourceCacheStore(tmp_path)
    with pytest.raises(SourceCacheCorruptError) as exc_info:
        fresh_store.open_generation(identity)
    cause = exc_info.value.__cause__
    assert cause is not None and "snapshot digest does not match metadata" in str(cause)
    assert fresh_store.status(identity).state == "corrupt"
