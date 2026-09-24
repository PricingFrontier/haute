"""Small review probes for PR 227. No production code is modified.

Run: uv run python scripts/benchmarks/pr-227-review-probes.py
Each probe prints observations, including defects; success is not an approval.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import polars as pl

import haute._chunked_writes as chunked_writes
import haute._polars_utils as polars_utils
from haute._chunked_writes import (
    JoinRecipe,
    _ChunkJoin,
    is_part_name,
    part_name,
    part_paths,
    scan_parts,
    write_parts,
)
from haute._execution_context import ExecutionProfile
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotSlot, NodeSnapshotStore
from haute._source_cache import SourceCacheBuildContext, SourceCacheIdentity, describe_parts


def publish(store, identity, value, *, explicit=True):
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"x": [value]}).write_parquet(artifact.part_path(0))
    try:
        return store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=explicit,
            profile=ExecutionProfile.NODE_SNAPSHOT,
        )
    except BaseException:
        artifact.close()
        raise


def slot_replacement(root):
    store = NodeSnapshotStore(root, node_output_max_generations=1)
    slot = NodeSnapshotSlot(str(root / "pipeline.py"), "node", "live", "bounded")
    with publish(store, slot.identity("old-code"), 1):
        pass
    try:
        with publish(store, slot.identity("new-code"), 2):
            outcome = "published"
    except Exception as exc:
        outcome = type(exc).__name__
    return {"replacement_with_one_unleased_pinned_slot": outcome}


def input_lease(root):
    store = NodeSnapshotStore(root)
    identity = SourceCacheIdentity(provider="file", descriptor={"path": "input.csv"})

    class Builder:
        def build(self, context):
            return pl.DataFrame({"x": [1, 2, 3]}).lazy()

    store.build(
        identity,
        Builder(),
        context=SourceCacheBuildContext(profile=ExecutionProfile.LAZY_SINK, build_class="bounded"),
    )
    child = (
        "import sys; from haute._node_snapshots import NodeSnapshotStore; "
        "from haute._source_cache import SourceCacheIdentity; "
        "NodeSnapshotStore(sys.argv[1]).clear(SourceCacheIdentity("
        "provider='file', descriptor={'path':'input.csv'}))"
    )
    with store.lease(identity) as generation:
        result = subprocess.run(
            [sys.executable, "-c", child, str(root)], capture_output=True, text=True
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        exists = generation.directory.exists()
        try:
            generation.lazy_frame.collect()
            read = "succeeded"
        except Exception as exc:
            read = type(exc).__name__
    return {"leased_input_exists_after_other_process_clear": exists, "leased_input_read": read}


def part_overflow(root):
    root.mkdir()
    for index in (99_999, 100_000):
        pl.DataFrame({"x": [index]}).write_parquet(root / part_name(index))
    try:
        describe_parts(root, {part_name(100_000): "0" * 64})
        publication = "accepted"
    except ValueError as exc:
        publication = str(exc)
    return {
        "part_100000_accepted": is_part_name(part_name(100_000)),
        "written_rows": 2,
        "discovered_rows": scan_parts(part_paths(root)).collect().height,
        "normal_publication_with_recorded_digest": publication,
    }


def index_failure(root):
    store = NodeSnapshotStore(root)
    slot = NodeSnapshotSlot(str(root / "pipeline.py"), "node", "live", "bounded")
    identity = slot.identity("first-code")
    with patch.object(
        store, "_write_slot_index_locked", side_effect=OSError("injected index failure")
    ):
        try:
            with publish(store, identity, 1):
                pass
        except OSError as exc:
            failure = str(exc)
    store.clear_slot(slot)
    try:
        with store.lease(identity) as generation:
            rows = generation.lazy_frame.collect().height
    except Exception as exc:
        rows = type(exc).__name__
    return {"publish_error": failure, "read_after_clear_slot": rows}


def join_probes(root):
    root.mkdir()
    source = root / "lookup.parquet"
    pl.DataFrame({"key": range(80), "payload": range(80)}).write_parquet(source)
    original = _ChunkJoin._matches
    result = []
    for n in (20, 40, 80):
        scans = []

        def observed(self, frame, lookup=None):
            query = original(self, frame, lookup)
            scans.append("lookup.parquet" in query.explain())
            return query

        base = pl.DataFrame({"key": range(n), "value": range(n)}).lazy()
        recipe = JoinRecipe(base, pl.scan_parquet(source), {"on": "key", "how": "left"})
        directory = root / str(n)
        directory.mkdir()
        with patch.object(_ChunkJoin, "_matches", observed):
            written = write_parts(directory, recipe.native(), join=recipe, chunk_rows=10)
        output = scan_parts(part_paths(directory)).collect().sort("key")
        assert output.equals(recipe.native().collect().sort("key"))
        result.append({"driving_rows": n, "lookup_scan_plans": sum(scans), "parts": written.chunks})

    key = "__haute_chunk_matches"
    base = pl.DataFrame({key: [1, 1], "a": [10, 20]}).lazy()
    lookup = pl.DataFrame({key: [1, 1], "b": [30, 40]}).lazy()
    recipe = JoinRecipe(base, lookup, {"on": key, "how": "left"})
    native_rows = recipe.native().collect().height
    destination = root / "reserved-key"
    destination.mkdir()
    try:
        write_parts(destination, recipe.native(), join=recipe, chunk_rows=2)
        outcome = "succeeded"
    except Exception as exc:
        outcome = type(exc).__name__ + ": " + str(exc).splitlines()[0]
    return {
        "left_join_rescans": result,
        "reserved_key_native_rows": native_rows,
        "reserved_key_chunked": outcome,
    }


def materialisation_probes(root):
    root.mkdir()
    collect_heights = []
    original = chunked_writes.execution_collect

    def observed(frame, **kwargs):
        result = original(frame, **kwargs)
        collect_heights.append(result.height)
        return result

    recipe = JoinRecipe(
        pl.DataFrame({"a": [1, 2]}).lazy(),
        pl.DataFrame({"b": range(80)}).lazy(),
        {"how": "cross"},
    )
    with patch.object(chunked_writes, "execution_collect", observed):
        written = write_parts(root, recipe.native(), join=recipe, chunk_rows=10)
    max_part_rows = max(pl.read_parquet(path).height for path in part_paths(root))
    cross_heights = collect_heights.copy()
    collect_heights.clear()
    original = polars_utils.execution_collect
    derived = (
        pl.DataFrame({"a": range(20)})
        .lazy()
        .join(pl.DataFrame({"b": range(30)}).lazy(), how="cross")
    )
    with patch.object(polars_utils, "execution_collect", observed):
        batches = polars_utils.bounded_collect_batches(derived, chunk_size=10)
        try:
            first_rows = next(batches).height
        finally:
            batches.close()
    return {
        "cross_join_requested_chunk_rows": 10,
        "cross_join_collected_frame_heights": cross_heights,
        "cross_join_max_part_rows": max_part_rows,
        "cross_join_reported_bound": written.chunk_rows,
        "memory_derived_first_batch_rows": first_rows,
        "memory_derived_collected_frame_heights": collect_heights,
    }


def main():
    # The store holds process-token handles until interpreter exit. Run probes
    # in a child so Windows can remove its private temporary directory afterwards.
    if len(sys.argv) == 1:
        with tempfile.TemporaryDirectory(prefix="haute-pr227-review-") as directory:
            subprocess.run([sys.executable, __file__, directory], check=True)
        return
    root = Path(sys.argv[1])
    observations = {
        "polars_version": pl.__version__,
        "slot_replacement": slot_replacement(root / "slots"),
        "input_lease": input_lease(root / "inputs"),
        "part_overflow": part_overflow(root / "parts"),
        "index_failure": index_failure(root / "index-failure"),
        "joins": join_probes(root / "joins"),
        "materialisation": materialisation_probes(root / "materialisation"),
    }
    print("REVIEW_OBSERVATIONS=" + json.dumps(observations, indent=2))


if __name__ == "__main__":
    main()
