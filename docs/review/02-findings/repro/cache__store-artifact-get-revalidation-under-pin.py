"""Adversarial reproduction for claim:
store_artifact-get-revalidation-under-pin

CLAIM: store_artifact, after put(), calls self.get(key) which runs
_evict_if_invalid -> _validate_entry -> pl.scan_parquet(entry.path).collect_schema().
If that read TRANSIENTLY fails (a Windows sharing violation / AV lock against the
file just written), _validate_entry raises CacheArtifactCorruptError,
_evict_if_invalid catches it and calls _remove_key (which UNLINKS the artifact
because the just-stored entry has no live scans), get returns None, and
store_artifact raises DataFrameExecutionCacheError("...vanished immediately").

A perfectly valid, just-materialised artifact is therefore DELETED and the call
fails LOUDLY on a transient read, with no retry -- even though the write itself
succeeded and the bytes on disk are a valid parquet.

This script reproduces that by injecting ONE transient failure into the
revalidation read and asserting the specific wrong outcome (exception text +
the valid file having been unlinked).

ISOLATION: all disk I/O is under a tempfile.TemporaryDirectory; no project file
is touched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

from haute._dataframe_execution_cache import (
    CacheArtifactCorruptError,
    DataFrameExecutionCache,
    DataFrameExecutionCacheEntry,
    DataFrameExecutionCacheError,
    dataframe_execution_cache_key,
)
from haute._execution_context import ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "data/input.parquet"},
                ),
            ),
            GraphNode(
                id="target",
                data=NodeData(
                    label="target",
                    nodeType=NodeType.POLARS,
                    config={"output": "premium"},
                ),
            ),
        ],
        edges=[GraphEdge(id="source-target", source="source", target="target")],
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "cache"
        cache = DataFrameExecutionCache(root=root, max_entries=4, max_bytes=10_000_000)

        key = dataframe_execution_cache_key(
            _graph(),
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:a",
        )

        # Write a genuinely VALID parquet artifact -- the "just-materialised frame".
        artifact = root / "artifact.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(artifact)
        assert artifact.exists(), "precondition: artifact written"
        # And it really is readable -- so any corruption signal below is purely
        # the injected transient failure, not actual corruption.
        assert pl.scan_parquet(artifact).collect_schema().names() == ["x"]

        metadata = {
            "row_count": 3,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": artifact.stat().st_size,
            "uncompressed_size_bytes": artifact.stat().st_size,
        }

        # Inject exactly ONE transient validation failure -- this models a
        # Windows sharing violation / AV lock when the just-written file is
        # immediately re-opened by the in-store revalidation read.
        calls = {"n": 0}
        original_validate = DataFrameExecutionCache._validate_entry

        def flaky_validate(
            self: DataFrameExecutionCache,
            entry: DataFrameExecutionCacheEntry,
        ) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                # Same wrapping _validate_entry itself does for a transient
                # scan_parquet/collect_schema error.
                raise CacheArtifactCorruptError(
                    "transient read failure (simulated sharing violation) "
                    f"path={entry.path!r}"
                )
            original_validate(self, entry)

        DataFrameExecutionCache._validate_entry = flaky_validate  # type: ignore[assignment]

        raised: DataFrameExecutionCacheError | None = None
        try:
            cache.store_artifact(key, artifact, metadata)
        except DataFrameExecutionCacheError as exc:
            raised = exc
        finally:
            DataFrameExecutionCache._validate_entry = original_validate  # type: ignore[assignment]

        # --- Assert the SPECIFIC wrong behaviour (expected vs actual) ---------

        # 1) store_artifact must have raised the "vanished immediately" error,
        #    NOT returned the stored entry, despite the write having succeeded.
        assert raised is not None, (
            "EXPECTED store_artifact to fail on the transient revalidation read; "
            "it returned normally instead (claim would be REFUTED)."
        )
        assert "vanished immediately" in str(raised), (
            "EXPECTED the 'Stored dataframe cache entry vanished immediately' "
            f"failure; got a different error: {raised!r}"
        )

        # 2) The valid, just-written artifact was UNLINKED by _evict_if_invalid
        #    -> _remove_key (it had no live scans), destroying good data on a
        #    transient read.
        assert not artifact.exists(), (
            "EXPECTED the valid artifact to have been UNLINKED by the in-store "
            "revalidation eviction; it still exists (claim would be REFUTED)."
        )

        # 3) The entry is gone from the cache too.
        assert cache.get(key) is None, "entry should have been evicted from the cache"

        # 4) Critically: the failure was transient. A retry of the very same
        #    store would have succeeded (validation now passes), proving the
        #    design discarded recoverable, valid data instead of retrying --
        #    unlike the rename path which retries PermissionError.
        artifact2 = root / "artifact2.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(artifact2)
        metadata2 = dict(metadata, size_bytes=artifact2.stat().st_size,
                         uncompressed_size_bytes=artifact2.stat().st_size)
        entry2 = cache.store_artifact(key, artifact2, metadata2)
        assert entry2 is not None and artifact2.exists(), (
            "second (non-flaky) store should succeed, confirming the first "
            "failure was transient/recoverable"
        )
        assert cache.get(key) is not None

        print("REPRO CONFIRMED:")
        print(
            "  store_artifact raised 'vanished immediately' on a single transient "
            "revalidation read"
        )
        print("  -> valid just-written artifact was UNLINKED (data destroyed)")
        print("  -> a subsequent identical store succeeded (failure was transient)")
        print(f"  _validate_entry call count during store: {calls['n']}")


if __name__ == "__main__":
    main()
