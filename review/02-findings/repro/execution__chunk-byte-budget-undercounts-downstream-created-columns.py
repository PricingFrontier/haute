"""Adversarial repro: byte-budget chunk sizing under-counts downstream-CREATED columns.

Claim under test (chunk-byte-budget-undercounts-downstream-created-columns):
  `_plan_chunk_sizes` costs the TARGET row width from the SOURCE schema only
  (`_estimate_projected_row_bytes(target_columns, source_node=<dataSource>)`).
  Columns that do NOT exist in the source schema -- e.g. a wide String column
  CREATED by a row-local polars `with_columns(... .alias('big'))` in the chunk
  suffix -- are not found by `_source_projected_column_widths` and fall back to
  `_DEFAULT_PROJECTED_COLUMN_BYTES` (64). A produced ~2 KB/row String column is
  therefore costed at 64 bytes, so `chunk_size = target_chunk_bytes // row_bytes`
  is ~30x too large and the materialized target chunk blows the memory budget.

This script builds a tiny synthetic graph entirely in a tempdir (no real project
files touched), runs `chunk_plan`, and ASSERTS the specific wrong value:
  * estimated_target_row_bytes ~= 8 (id Int64) + 64 (big fallback)  << real width
  * the REAL per-row width of `big` is ~2 KB (measured from a materialized frame)
  * chunk_size * real_big_width  >>  target_chunk_bytes (budget blown ~30x)

Exit 0 == bug reproduced (assertions describe expected-vs-actual). Exit 1 == an
assertion that the bug exists failed (i.e. claim could not be reproduced as stated).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import polars as pl

import haute._sandbox as _sandbox
from haute.chunking import ChunkPlanRequest, chunk_plan
from haute.graph_utils import GraphEdge, PipelineGraph

# A produced String column ~2 KB/row. Source has NO such column.
_LITERAL = "x" * 2000
_ROWS = 4096
_TARGET_CHUNK_BYTES = 1_000_000  # 1 MB budget
_DEFAULT_FALLBACK_BYTES = 64  # _DEFAULT_PROJECTED_COLUMN_BYTES
_INT64_BYTES = 8  # _FIXED_DTYPE_BYTES[pl.Int64]


def _node(node_id: str, node_type: str, config: dict[str, object]) -> dict[str, object]:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": node_type, "config": config},
    }


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="haute_chunk_repro_"))
    _sandbox.set_project_root(tmp)

    # dataSource: a single narrow Int64 column 'id'. No wide/string columns.
    source_path = tmp / "narrow_source.parquet"
    pl.DataFrame({"id": list(range(_ROWS))}).write_parquet(source_path)

    # Chunk suffix CREATES a wide String column 'big' the source never had,
    # via a row-local, whitelisted concat_str.  Frame name == parent label.
    polars_code = (
        "df = source.with_columns("
        f"pl.concat_str([pl.col('id'), pl.lit('{_LITERAL}')]).alias('big'))"
    )

    # Build the synthetic graph directly from the public models (no tests pkg).
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _node("source", "dataSource", {"path": str(source_path)}),
                _node("feat", "polars", {"code": polars_code}),
            ],
            "edges": [
                GraphEdge(id="e_source_feat", source="source", target="feat").model_dump()
            ],
        }
    )

    # Target is the polars node that creates 'big'. Byte-budget plan.
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=graph,
            target_node_id="feat",
            chunk_size=None,
            target_chunk_bytes=_TARGET_CHUNK_BYTES,
            required_columns_by_node={"feat": ["id", "big"]},
        )
    )

    est = plan.estimated_target_row_bytes
    chunk_size = plan.chunk_size
    print(f"estimated_target_row_bytes = {est}")
    print(f"chunk_size                 = {chunk_size}")

    # --- Measure the REAL per-row width of the produced 'big' column. ---
    materialized = pl.read_parquet(source_path).with_columns(
        pl.concat_str([pl.col("id"), pl.lit(_LITERAL)]).alias("big")
    )
    real_big_per_row = materialized["big"].estimated_size() / materialized.height
    real_row_width = materialized.estimated_size() / materialized.height
    print(f"REAL big per-row bytes     = {real_big_per_row:.0f}")
    print(f"REAL full row width bytes  = {real_row_width:.0f}")

    # --- Assertions establishing the bug (expected-vs-actual). ---

    # (1) The plan estimate equals id(8) + big-fallback(64); 'big' was costed at
    #     the 64-byte default because it is absent from the SOURCE schema.
    expected_buggy_est = _INT64_BYTES + _DEFAULT_FALLBACK_BYTES
    assert est == expected_buggy_est, (
        f"expected source-only under-estimate {expected_buggy_est} "
        f"(id 8 + big fallback 64), got {est}"
    )

    # (2) The produced column is genuinely wide (~2 KB), i.e. the 64-byte
    #     estimate is a gross under-count, not a coincidence.
    assert real_big_per_row > 1500, (
        f"sanity: produced 'big' should be ~2KB/row, measured {real_big_per_row:.0f}"
    )

    # (3) The estimate under-counts the true row width by a large factor (~30x).
    under_factor = real_row_width / est
    print(f"under-count factor         = {under_factor:.1f}x")
    assert under_factor > 20, (
        f"expected >20x under-count of true row width, got {under_factor:.1f}x"
    )

    # (4) The chosen chunk_size, multiplied by the TRUE row width, blows the
    #     stated memory budget by roughly the same factor -> bounded-memory
    #     contract violated.
    projected_chunk_bytes = chunk_size * real_row_width
    print(f"projected chunk bytes      = {projected_chunk_bytes:.0f} "
          f"(budget {_TARGET_CHUNK_BYTES})")
    assert projected_chunk_bytes > 20 * _TARGET_CHUNK_BYTES, (
        f"expected materialized target chunk to exceed budget >20x; "
        f"projected {projected_chunk_bytes:.0f} vs budget {_TARGET_CHUNK_BYTES}"
    )

    print("\nBUG REPRODUCED: downstream-created 'big' costed at 64B; "
          "byte-budget chunk_size blows the memory budget ~"
          f"{projected_chunk_bytes / _TARGET_CHUNK_BYTES:.0f}x.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
