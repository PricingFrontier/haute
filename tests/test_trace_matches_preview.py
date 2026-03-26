"""Tests that trace output matches the actual preview cell the user clicked.

The real user flow:
  1. execute_graph() produces preview data (the table shown in the UI)
  2. User clicks a cell at (row_index, column) in that table
  3. execute_trace() is called with that row_index
  4. The trace's target-node values MUST match preview[row_index]

These tests verify that contract — the trace shows the row the user
actually clicked, not data from some other row.

IMPORTANT: Both execute_graph and execute_trace must use the same
row_limit so they share the same cache fingerprint.  In the real app
the frontend sends the same rowLimit to both endpoints.
"""

from __future__ import annotations

import polars as pl

from haute.executor import _preview_cache, execute_graph
from haute.trace import TraceResult, execute_trace
from haute.trace import _cache as _trace_cache
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

# Use a consistent row_limit across preview and trace, matching real usage.
_ROW_LIMIT = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _preview_row(graph, node_id: str, row_index: int) -> dict:
    """Run execute_graph and return the preview dict at row_index for node_id.

    This is what the user actually sees in the UI table.
    """
    results = execute_graph(graph, target_node_id=node_id, row_limit=_ROW_LIMIT)
    node_result = results[node_id]
    assert node_result.status == "ok", f"Node {node_id} failed: {node_result.error}"
    assert row_index < len(node_result.preview), (
        f"row_index={row_index} out of range (preview has {len(node_result.preview)} rows)"
    )
    return node_result.preview[row_index]


def _step_by_id(result: TraceResult, node_id: str):
    for s in result.steps:
        if s.node_id == node_id:
            return s
    raise KeyError(f"No step with node_id={node_id!r}")


# ===========================================================================
# 1. Simple transform — baseline sanity check
# ===========================================================================


class TestPreviewMatchSimple:
    """Baseline: trace matches preview for a simple passthrough pipeline."""

    def test_passthrough_trace_matches_preview(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30], "y": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", ".with_columns(z=pl.col('x') + pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        for row_idx in range(3):
            preview = _preview_row(graph, "t", row_idx)
            trace = execute_trace(graph, row_index=row_idx, target_node_id="t")

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )


# ===========================================================================
# 2. Sort — the trace must return the same row the preview shows
# ===========================================================================


class TestPreviewMatchSort:
    """After a sort, preview row N has different data than source row N.
    The trace must return the sorted row, not the pre-sort row."""

    def test_sort_trace_matches_every_preview_row(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [5, 3, 1, 4, 2],
                "value": [50, 30, 10, 40, 20],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        # After sort: id=[1,2,3,4,5], value=[10,20,30,40,50]
        for row_idx in range(5):
            preview = _preview_row(graph, "sorted", row_idx)
            trace = execute_trace(graph, row_index=row_idx, target_node_id="sorted")

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

    def test_sort_trace_column_matches_preview_cell(self, tmp_path):
        """Tracing a specific column still returns the value from the
        correct (sorted) row, matching what the user clicked."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [3, 1, 2],
                "score": [300, 100, 200],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        # User clicks row 0, column "score" in sorted preview
        # Sorted: id=[1,2,3], score=[100,200,300]
        preview = _preview_row(graph, "sorted", 0)
        trace = execute_trace(graph, row_index=0, target_node_id="sorted", column="score")

        assert trace.output_value == preview["score"], (
            f"Clicked cell shows {preview['score']} but trace says {trace.output_value}"
        )


# ===========================================================================
# 3. Filter — row indices shift after filtering
# ===========================================================================


class TestPreviewMatchFilter:
    """A filter removes rows.  Preview row 0 after a filter is NOT
    source row 0.  Trace must match the preview."""

    def test_filter_trace_matches_every_preview_row(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "value": [10, 20, 30, 40, 50],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('value') > 25)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        # Filtered: id=[3,4,5], value=[30,40,50]
        for row_idx in range(3):
            preview = _preview_row(graph, "filt", row_idx)
            trace = execute_trace(graph, row_index=row_idx, target_node_id="filt")

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

    def test_filter_trace_upstream_matches_correct_source_row(self, tmp_path):
        """The source step in the trace must show the row that actually
        survived the filter — the same row as the preview cell."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "value": [10, 20, 30, 40, 50],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('value') > 25)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        # User clicks row 0 in the filtered preview → should be id=3
        preview = _preview_row(graph, "filt", 0)
        assert preview["id"] == 3  # sanity: filtered row 0 is id=3

        trace = execute_trace(graph, row_index=0, target_node_id="filt")
        src_step = _step_by_id(trace, "src")

        assert src_step.output_values["id"] == preview["id"], (
            f"User clicked row with id={preview['id']} but trace source "
            f"shows id={src_step.output_values['id']}"
        )


# ===========================================================================
# 4. Join — trace must match joined preview, not source positions
# ===========================================================================


class TestPreviewMatchJoin:
    """A join produces an output whose row order differs from both sources.
    The trace must match the actual joined preview data."""

    def test_join_trace_matches_every_preview_row(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame(
            {
                "key": [1, 2, 3],
                "val_a": ["a1", "a2", "a3"],
            }
        ).write_parquet(p_a)

        pl.DataFrame(
            {
                "key": [3, 2, 1],
                "val_b": ["b3", "b2", "b1"],
            }
        ).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        num_rows = results["join"].row_count

        for row_idx in range(num_rows):
            preview = results["join"].preview[row_idx]
            trace = execute_trace(
                graph, row_index=row_idx, target_node_id="join", row_limit=_ROW_LIMIT
            )

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

    def test_join_trace_upstream_sources_match_clicked_row(self, tmp_path):
        """Source steps in the trace must correspond to the actual
        preview row the user clicked — verified by checking join keys."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame(
            {
                "key": [10, 20, 30],
                "price": [100, 200, 300],
            }
        ).write_parquet(p_a)

        pl.DataFrame(
            {
                "key": [30, 10, 20],
                "factor": [1.5, 0.8, 1.2],
            }
        ).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        # Check every row
        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        for row_idx in range(results["join"].row_count):
            preview = results["join"].preview[row_idx]
            trace = execute_trace(
                graph, row_index=row_idx, target_node_id="join", row_limit=_ROW_LIMIT
            )

            a_step = _step_by_id(trace, "a")
            b_step = _step_by_id(trace, "b")

            # The key in both sources must match the clicked preview row's key
            assert a_step.output_values["key"] == preview["key"], (
                f"Row {row_idx}: preview key={preview['key']} but "
                f"source A trace shows key={a_step.output_values['key']}"
            )
            assert b_step.output_values["key"] == preview["key"], (
                f"Row {row_idx}: preview key={preview['key']} but "
                f"source B trace shows key={b_step.output_values['key']}"
            )


# ===========================================================================
# 5. Multi-step pipeline — filter + sort + join
# ===========================================================================


class TestPreviewMatchMultiStep:
    """Compound pipeline: operations stack, making the row-index shift
    more severe.  The trace must still match the preview."""

    def test_filter_then_sort_trace_matches_preview(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [5, 3, 1, 4, 2],
                "value": [50, 30, 10, 40, 20],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('value') >= 30)"),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "filt"), _edge("filt", "sorted")],
            }
        )

        # Filter keeps ids 5,3,4 → sort by id asc → 3,4,5
        results = execute_graph(graph, target_node_id="sorted", row_limit=_ROW_LIMIT)
        for row_idx in range(results["sorted"].row_count):
            preview = results["sorted"].preview[row_idx]
            trace = execute_trace(graph, row_index=row_idx, target_node_id="sorted")

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

            # Source and filter steps must also show the same record
            src_step = _step_by_id(trace, "src")
            assert src_step.output_values["id"] == preview["id"], (
                f"Row {row_idx}: clicked id={preview['id']} but source "
                f"trace shows id={src_step.output_values['id']}"
            )

    def test_join_then_filter_trace_matches_preview(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame(
            {
                "key": [1, 2, 3, 4],
                "amount": [100, 200, 300, 400],
            }
        ).write_parquet(p_a)

        pl.DataFrame(
            {
                "key": [4, 3, 2, 1],
                "rate": [0.1, 0.2, 0.3, 0.4],
            }
        ).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key')"),
                    _transform_node("filt", ".filter(pl.col('amount') > 150)"),
                ],
                "edges": [
                    _edge("a", "join"),
                    _edge("b", "join"),
                    _edge("join", "filt"),
                ],
            }
        )

        results = execute_graph(graph, target_node_id="filt", row_limit=_ROW_LIMIT)
        for row_idx in range(results["filt"].row_count):
            preview = results["filt"].preview[row_idx]
            trace = execute_trace(graph, row_index=row_idx, target_node_id="filt")

            # Trace output must match the preview row
            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

            # Both source traces must agree on the key
            a_step = _step_by_id(trace, "a")
            b_step = _step_by_id(trace, "b")
            assert a_step.output_values["key"] == preview["key"], (
                f"Row {row_idx}: clicked key={preview['key']} but "
                f"source A shows key={a_step.output_values['key']}"
            )
            assert b_step.output_values["key"] == preview["key"], (
                f"Row {row_idx}: clicked key={preview['key']} but "
                f"source B shows key={b_step.output_values['key']}"
            )


# ===========================================================================
# 6. Many-to-one join — row duplication
# ===========================================================================


class TestPreviewMatchManyToOne:
    """A many-to-one join duplicates lookup rows.  Each output row
    must trace back to the correct lookup entry."""

    def test_many_to_one_join_trace_matches_preview(self, tmp_path):
        p_facts = tmp_path / "facts.parquet"
        p_lookup = tmp_path / "lookup.parquet"

        pl.DataFrame(
            {
                "policy_id": [1, 2, 3, 4, 5],
                "region": ["north", "south", "north", "south", "north"],
                "base_premium": [100, 200, 150, 250, 175],
            }
        ).write_parquet(p_facts)

        pl.DataFrame(
            {
                "region": ["north", "south"],
                "region_factor": [1.1, 0.9],
            }
        ).write_parquet(p_lookup)

        graph = _g(
            {
                "nodes": [
                    _source_node("facts", str(p_facts)),
                    _source_node("lookup", str(p_lookup)),
                    _transform_node("join", "facts.join(lookup, on='region')"),
                ],
                "edges": [_edge("facts", "join"), _edge("lookup", "join")],
            }
        )

        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        for row_idx in range(results["join"].row_count):
            preview = results["join"].preview[row_idx]
            trace = execute_trace(graph, row_index=row_idx, target_node_id="join")

            # Trace output matches preview
            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

            # Lookup step must show the correct region
            lookup_step = _step_by_id(trace, "lookup")
            assert lookup_step.output_values["region"] == preview["region"], (
                f"Row {row_idx}: clicked region={preview['region']} but "
                f"lookup trace shows region={lookup_step.output_values['region']}"
            )


# ===========================================================================
# 7. Aggregation — trace must match the aggregated preview row
# ===========================================================================


class TestPreviewMatchAggregation:
    """group_by changes cardinality.  The trace output must match
    the aggregated preview row the user clicked."""

    def test_groupby_trace_matches_preview(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "region": ["south", "north", "south", "north", "south"],
                "premium": [200, 100, 250, 150, 175],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "agg", ".group_by('region').agg(pl.col('premium').sum()).sort('region')"
                    ),
                ],
                "edges": [_edge("src", "agg")],
            }
        )

        results = execute_graph(graph, target_node_id="agg", row_limit=_ROW_LIMIT)
        for row_idx in range(results["agg"].row_count):
            preview = results["agg"].preview[row_idx]
            trace = execute_trace(graph, row_index=row_idx, target_node_id="agg")

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )


# ===========================================================================
# 8. row_limit consistency — trace uses same row_limit as preview
# ===========================================================================


class TestPreviewMatchRowLimit:
    """When row_limit is set, both preview and trace must operate on
    the same data subset, so the same row_index maps to the same record."""

    def test_trace_with_row_limit_matches_preview(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": list(range(100)),
                "value": list(range(100, 200)),
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id', descending=True)"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        row_limit = 10
        results = execute_graph(graph, target_node_id="sorted", row_limit=row_limit)

        # Check all rows in the limited preview
        for row_idx in range(min(results["sorted"].row_count, 10)):
            preview = results["sorted"].preview[row_idx]
            trace = execute_trace(
                graph,
                row_index=row_idx,
                target_node_id="sorted",
                row_limit=row_limit,
            )

            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}' (row_limit={row_limit}): "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )


# ===========================================================================
# 9. Cold cache — trace executes independently, then preview must agree
# ===========================================================================


class TestColdCacheConsistency:
    """When the preview cache is cold (no prior execute_graph call),
    the trace executes independently.  A subsequent preview call must
    show the same data at the same row_index, because the trace stores
    its results into the preview cache to prevent divergence.

    This is the scenario that causes the real-world bug: the user's
    preview came from one execution, the trace from another, and
    Polars joins produced different row orderings.
    """

    def test_join_cold_cache_trace_then_preview_agree(self, tmp_path):
        """Trace runs first (cold cache), then preview.  Both must
        show the same data at row_index."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame(
            {
                "key": [1, 2, 3],
                "val_a": ["a1", "a2", "a3"],
            }
        ).write_parquet(p_a)

        pl.DataFrame(
            {
                "key": [3, 2, 1],
                "val_b": ["b3", "b2", "b1"],
            }
        ).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        # Clear both caches to simulate cold start
        _preview_cache.invalidate()
        _trace_cache.invalidate()

        # Trace runs first — no preview cache to reuse
        trace = execute_trace(graph, row_index=0, target_node_id="join", row_limit=_ROW_LIMIT)

        # Now preview runs — must agree with the trace
        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        preview = results["join"].preview[0]

        for col in preview:
            assert trace.output_value[col] == preview[col], (
                f"Cold cache divergence: col '{col}': "
                f"trace={trace.output_value[col]}, preview={preview[col]}"
            )

    def test_join_evicted_cache_trace_matches_new_preview(self, tmp_path):
        """Preview runs, cache is evicted, trace runs independently.
        A fresh preview after the trace must match the trace's data."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame(
            {
                "key": [10, 20, 30, 40],
                "amount": [100, 200, 300, 400],
            }
        ).write_parquet(p_a)

        pl.DataFrame(
            {
                "key": [40, 30, 20, 10],
                "factor": [0.1, 0.2, 0.3, 0.4],
            }
        ).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        # Preview first
        results1 = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)

        # Evict preview cache (simulates cache pressure)
        _preview_cache.invalidate()
        _trace_cache.invalidate()

        # Trace runs — must execute independently
        traces = []
        for row_idx in range(results1["join"].row_count):
            t = execute_trace(graph, row_index=row_idx, target_node_id="join", row_limit=_ROW_LIMIT)
            traces.append(t)

        # New preview — must match trace data (trace stored into preview cache)
        results2 = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)

        for row_idx in range(results2["join"].row_count):
            preview = results2["join"].preview[row_idx]
            trace = traces[row_idx]
            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"trace={trace.output_value[col]}, preview={preview[col]}"
                )

    def test_cold_cache_all_rows_match_for_join_with_filter(self, tmp_path):
        """Multi-step pipeline: join → filter.  Cold cache.  Every row
        must match between trace and subsequent preview."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame(
            {
                "key": [1, 2, 3, 4, 5],
                "amount": [50, 150, 250, 350, 450],
            }
        ).write_parquet(p_a)

        pl.DataFrame(
            {
                "key": [5, 4, 3, 2, 1],
                "rate": [0.05, 0.04, 0.03, 0.02, 0.01],
            }
        ).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key')"),
                    _transform_node("filt", ".filter(pl.col('amount') > 100)"),
                ],
                "edges": [
                    _edge("a", "join"),
                    _edge("b", "join"),
                    _edge("join", "filt"),
                ],
            }
        )

        _preview_cache.invalidate()
        _trace_cache.invalidate()

        # Trace first (cold cache)
        trace0 = execute_trace(graph, row_index=0, target_node_id="filt", row_limit=_ROW_LIMIT)

        # Preview second — must agree
        results = execute_graph(graph, target_node_id="filt", row_limit=_ROW_LIMIT)
        preview = results["filt"].preview[0]

        for col in preview:
            assert trace0.output_value[col] == preview[col], (
                f"Cold cache join+filter divergence: col '{col}': "
                f"trace={trace0.output_value[col]}, preview={preview[col]}"
            )
