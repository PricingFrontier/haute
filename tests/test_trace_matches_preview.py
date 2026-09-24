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
import pytest

from haute.executor import _preview_cache, execute_graph
from haute.trace import TraceResult, execute_trace
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

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

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
                    _transform_node("t", "df = src.with_columns(z=pl.col('x') + pl.col('y'))"),
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
                    _transform_node("sorted", "df = src.sort('id')"),
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
                    _transform_node("sorted", "df = src.sort('id')"),
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
                    _transform_node("filt", "df = src.filter(pl.col('value') > 25)"),
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
                    _transform_node("filt", "df = src.filter(pl.col('value') > 25)"),
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
                    _transform_node("join", "df = a.join(b, on='key')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        num_rows = results["join"].row_count

        for row_idx in range(num_rows):
            preview = results["join"].preview[row_idx]
            # A cold re-execution of a non-deterministic polars join may order
            # rows differently, so the clicked row's values anchor the trace to
            # the previewed row, as the HTTP route does.
            trace = execute_trace(
                graph,
                row_index=row_idx,
                target_node_id="join",
                row_limit=_ROW_LIMIT,
                row_values=preview,
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
                    _transform_node("join", "df = a.join(b, on='key')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        # Check every row
        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        for row_idx in range(results["join"].row_count):
            preview = results["join"].preview[row_idx]
            # See the matching comment in
            # ``test_join_trace_matches_every_preview_row``.
            trace = execute_trace(
                graph,
                row_index=row_idx,
                target_node_id="join",
                row_limit=_ROW_LIMIT,
                row_values=preview,
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
                    _transform_node("filt", "df = src.filter(pl.col('value') >= 30)"),
                    _transform_node("sorted", "df = filt.sort('id')"),
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
                    _transform_node("join", "df = a.join(b, on='key')"),
                    _transform_node("filt", "df = join.filter(pl.col('amount') > 150)"),
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
            # See the matching comment in
            # ``test_join_trace_matches_every_preview_row``.
            trace = execute_trace(
                graph,
                row_index=row_idx,
                target_node_id="filt",
                row_values=preview,
            )

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
                    _transform_node("join", "df = facts.join(lookup, on='region')"),
                ],
                "edges": [_edge("facts", "join"), _edge("lookup", "join")],
            }
        )

        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        for row_idx in range(results["join"].row_count):
            preview = results["join"].preview[row_idx]
            # See the matching comment in
            # ``test_join_trace_matches_every_preview_row``.
            trace = execute_trace(
                graph,
                row_index=row_idx,
                target_node_id="join",
                row_values=preview,
            )

            # Trace output matches preview
            for col in preview:
                assert trace.output_value[col] == preview[col], (
                    f"Row {row_idx}, col '{col}': "
                    f"preview={preview[col]}, trace={trace.output_value[col]}"
                )

            # Lookup step must show the correct region (if correlation succeeded)
            step_ids = {s.node_id for s in trace.steps}
            if "lookup" in step_ids:
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
                        "agg",
                        "df = src.group_by('region').agg(pl.col('premium').sum()).sort('region')",
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
                    _transform_node("sorted", "df = src.sort('id', descending=True)"),
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
# Limited preview: trace follows the rows a target-only preview shows
# ===========================================================================


class TestLimitedPreviewTrace:
    """Trace resolves the lineage of rows a row-limited target preview shows."""

    @staticmethod
    def _limited_preview_row(graph, node_id: str, row_index: int, row_limit: int) -> dict:
        result = execute_graph(
            graph,
            target_node_id=node_id,
            row_limit=row_limit,
            target_preview_only=True,
            include_schema_metadata=True,
        )[node_id]
        assert result.status == "ok", result.error
        return result.preview[row_index]

    def _trace(self, graph, node_id: str, row_index: int, row_limit: int, column: str):
        row = self._limited_preview_row(graph, node_id, row_index, row_limit)
        _preview_cache.clear()
        return row, execute_trace(
            graph,
            row_index=row_index,
            target_node_id=node_id,
            column=column,
            row_limit=row_limit,
            row_values=row,
        )

    @staticmethod
    def _join_graph(tmp_path, maintain_order: str | None):
        from haute._types import GraphNode, NodeData, NodeType

        base_path = tmp_path / "base.parquet"
        lookup_path = tmp_path / "lookup.parquet"
        pl.DataFrame({"id": list(range(99, -1, -1))}).write_parquet(base_path)
        pl.DataFrame(
            {"id": list(range(100)), "premium": [float(i) * 10 for i in range(100)]}
        ).write_parquet(lookup_path)
        config: dict[str, object] = {"how": "left", "on": ["id"]}
        if maintain_order is not None:
            config["maintainOrder"] = maintain_order
        return _g(
            {
                "nodes": [
                    _source_node("base", str(base_path)),
                    _source_node("lookup", str(lookup_path)),
                    GraphNode(
                        id="join",
                        data=NodeData(label="join", nodeType=NodeType.EDGE_JOIN, config=config),
                    ),
                ],
                "edges": [
                    _edge("base", "join", target_handle="base"),
                    _edge("lookup", "join", target_handle="join"),
                ],
            }
        )

    @pytest.mark.parametrize("maintain_order", ["left", None])
    def test_joined_value_traces_to_its_lookup_row(self, tmp_path, maintain_order):
        graph = self._join_graph(tmp_path, maintain_order)

        for row_index in (0, 4):
            row, result = self._trace(graph, "join", row_index, row_limit=5, column="premium")

            assert result.output_value == row["premium"] == float(row["id"]) * 10
            assert _step_by_id(result, "lookup").output_values == {
                "id": row["id"],
                "premium": row["premium"],
            }
            assert result.omissions == []

            _row, id_result = self._trace(graph, "join", row_index, row_limit=5, column="id")
            assert _step_by_id(id_result, "base").output_values == {"id": row["id"]}

    def test_filtered_row_traces_past_the_source_prefix(self, tmp_path):
        path = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(100))}).write_parquet(path)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(path)),
                    _transform_node("kept", "df = src.filter(pl.col('x') >= 50)"),
                ],
                "edges": [_edge("src", "kept")],
            }
        )

        row, result = self._trace(graph, "kept", 1, row_limit=3, column="x")

        assert row == {"x": 51}
        assert _step_by_id(result, "src").output_values == {"x": 51}

    def test_grouped_row_reports_its_source_rows_as_ambiguous(self, tmp_path):
        path = tmp_path / "data.parquet"
        pl.DataFrame(
            {"region": ["north", "south", "north", "south"], "premium": [1, 2, 3, 4]}
        ).write_parquet(path)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(path)),
                    _transform_node(
                        "grouped",
                        "df = src.group_by('region').agg(pl.col('premium').sum()).sort('region')",
                    ),
                ],
                "edges": [_edge("src", "grouped")],
            }
        )

        _row, result = self._trace(graph, "grouped", 0, row_limit=1, column="premium")

        assert [omission.node_id for omission in result.omissions] == ["src"]
        assert result.omissions[0].reason == "duplicate_exact_match"

    def test_order_preserving_lineage_uses_head_frames_only(self, tmp_path, monkeypatch):
        from haute._trace_correlation import RowScopeResolver

        lookups: list[str] = []
        real_lookup = RowScopeResolver.lookup

        def counting_lookup(self, node_id, source_handle, values):
            lookups.append(node_id)
            return real_lookup(self, node_id, source_handle, values)

        monkeypatch.setattr(RowScopeResolver, "lookup", counting_lookup)
        path = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(50))}).write_parquet(path)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(path)),
                    _transform_node("doubled", "df = src.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("shifted", "df = doubled.with_columns(z=pl.col('y') + 1)"),
                ],
                "edges": [_edge("src", "doubled"), _edge("doubled", "shifted")],
            }
        )

        row, result = self._trace(graph, "shifted", 3, row_limit=5, column="z")

        assert row == {"x": 3, "y": 6, "z": 7}
        assert _step_by_id(result, "src").output_values == {"x": 3}
        assert lookups == []
        assert {step.row_lineage_type for step in result.steps} <= {"created", "passthrough"}

    @pytest.mark.parametrize(
        ("code", "resolves"),
        [
            # ``sort`` is not chunk-local, so the code edge is not aligned and the
            # join row is looked up by the columns the code carries unchanged.
            ("df = join.sort('id').with_columns(double=pl.col('premium') * 2)", True),
            ("df = join.sort('id').with_columns(premium=pl.col('premium') * 2).drop('id')", False),
        ],
    )
    def test_code_below_an_unordered_join_uses_carried_columns(self, tmp_path, code, resolves):
        graph = self._join_graph(tmp_path, None)
        graph.nodes.append(_transform_node("after", code))
        graph.edges.append(_edge("join", "after"))

        _row, result = self._trace(graph, "after", 2, row_limit=5, column="premium")

        omitted = {omission.node_id: omission.reason for omission in result.omissions}
        if resolves:
            assert "join" not in omitted
        else:
            assert omitted.get("join") == "row_scope_unproven"

    def test_a_later_join_key_does_not_identify_an_earlier_joined_input(self, tmp_path):
        frames = {
            "policies": pl.DataFrame({"id": [1], "x": [1]}),
            "rates": pl.DataFrame({"id": [1, 1], "x": [2, 1], "premium": [7, 7]}),
            "factors": pl.DataFrame({"x": [1], "f": [9]}),
        }
        nodes = []
        for name, frame in frames.items():
            path = tmp_path / f"{name}.parquet"
            frame.write_parquet(path)
            nodes.append(_source_node(name, str(path)))
        code = "df = policies.join(rates, on='id').join(factors, on='x')"
        graph = _g(
            {
                "nodes": [*nodes, _transform_node("priced", code)],
                "edges": [_edge(name, "priced") for name in frames],
            }
        )
        preview = [self._limited_preview_row(graph, "priced", index, 5) for index in (0, 1)]
        row_index = next(index for index, row in enumerate(preview) if row["x_right"] == 2)

        row, result = self._trace(graph, "priced", row_index, row_limit=5, column="premium")

        # The output ``x`` is the policies value; the rates row behind this output
        # row has ``x = 2`` (now ``x_right``), so ``x`` must not select a rates row.
        assert row == {"id": 1, "x": 1, "x_right": 2, "premium": 7, "f": 9}
        steps = {step.node_id: step.output_values for step in result.steps}
        assert steps.get("rates") != {"id": 1, "x": 1, "premium": 7}
        assert steps["policies"] == {"id": 1, "x": 1}
        assert steps["factors"] == {"x": 1, "f": 9}

    @staticmethod
    def _premium_join_graph(tmp_path, code: str):
        from haute._types import GraphNode, NodeData, NodeType

        base_path = tmp_path / "base.parquet"
        lookup_path = tmp_path / "lookup.parquet"
        pl.DataFrame(
            {"id": list(range(19, -1, -1)), "region": [f"r{i % 3}" for i in range(20)]}
        ).write_parquet(base_path)
        pl.DataFrame(
            {
                "id": list(range(20)),
                "premium": [float(i) * 10 for i in range(20)],
                "premium_tax": [float(i) for i in range(20)],
            }
        ).write_parquet(lookup_path)
        return _g(
            {
                "nodes": [
                    _source_node("base", str(base_path)),
                    _source_node("lookup", str(lookup_path)),
                    GraphNode(
                        id="join",
                        data=NodeData(
                            label="join",
                            nodeType=NodeType.EDGE_JOIN,
                            config={"how": "left", "on": ["id"]},
                        ),
                    ),
                    _transform_node("after", code),
                ],
                "edges": [
                    _edge("base", "join", target_handle="base"),
                    _edge("lookup", "join", target_handle="join"),
                    _edge("join", "after"),
                ],
            }
        )

    @pytest.mark.parametrize(
        ("code", "resolved_through", "with_region"),
        [
            ("df = join.sort('id').with_columns(pl.exclude('id') * 2)", {"id"}, False),
            (
                "df = join.sort('id').with_columns(pl.col('^premium.*$') * 1.1)",
                {"id", "region"},
                True,
            ),
        ],
    )
    def test_a_computed_selector_below_an_unordered_join_carries_what_it_skips(
        self, tmp_path, monkeypatch, code, resolved_through, with_region
    ):
        from haute._trace_correlation import RowScopeResolver

        looked_up: list[dict] = []
        real_lookup = RowScopeResolver.lookup

        def recording_lookup(self, node_id, source_handle, values):
            if node_id == "join":
                looked_up.append(dict(values))
            return real_lookup(self, node_id, source_handle, values)

        monkeypatch.setattr(RowScopeResolver, "lookup", recording_lookup)
        if with_region:
            graph = self._premium_join_graph(tmp_path, code)
        else:
            graph = self._join_graph(tmp_path, None)
            graph.nodes.append(_transform_node("after", code))
            graph.edges.append(_edge("join", "after"))

        row, result = self._trace(graph, "after", 2, row_limit=5, column="id")

        assert "join" not in {omission.node_id for omission in result.omissions}
        assert looked_up and {frozenset(values) for values in looked_up} == {
            frozenset(resolved_through)
        }
        assert _step_by_id(result, "join").output_values["id"] == row["id"]

    @staticmethod
    def _recorded_lookups(monkeypatch) -> list[str]:
        from haute._trace_correlation import RowScopeResolver

        looked_up: list[str] = []
        real_lookup = RowScopeResolver.lookup

        def recording_lookup(self, node_id, source_handle, values):
            looked_up.append(node_id)
            return real_lookup(self, node_id, source_handle, values)

        monkeypatch.setattr(RowScopeResolver, "lookup", recording_lookup)
        return looked_up

    @pytest.mark.parametrize(
        ("join_config", "transfers"),
        [
            pytest.param({"how": "left", "on": ["id"]}, True, id="left"),
            pytest.param({"how": "inner", "on": ["id"]}, True, id="inner"),
            pytest.param({"how": "right", "on": ["id"]}, False, id="right"),
            pytest.param(
                {"how": "left", "on": ["id"], "column_renames": {"premium": "cost"}},
                False,
                id="renamed",
            ),
        ],
    )
    def test_a_unique_joined_row_transfers_its_base_row_without_a_lookup(
        self, tmp_path, monkeypatch, join_config, transfers
    ):
        graph = self._join_graph(tmp_path, None)
        join_node = next(node for node in graph.nodes if node.id == "join")
        join_node.data.config = join_config
        graph.nodes.append(_transform_node("after", "df = join.sort('id')"))
        graph.edges.append(_edge("join", "after"))
        looked_up = self._recorded_lookups(monkeypatch)

        row, result = self._trace(graph, "after", 2, row_limit=5, column="id")

        assert result.omissions == []
        assert _step_by_id(result, "base").output_values == {"id": row["id"]}
        assert "join" in looked_up
        assert ("base" in looked_up) is not transfers

    def test_a_slice_below_the_transferred_row_is_still_looked_up(self, tmp_path, monkeypatch):
        graph = self._join_graph(tmp_path, None)
        base_edge = next(edge for edge in graph.edges if edge.source == "base")
        graph.edges.remove(base_edge)
        graph.nodes.append(_transform_node("limited", "df = base.head(50)"))
        graph.nodes.append(_transform_node("after", "df = join.sort('id')"))
        graph.edges.extend(
            [
                _edge("base", "limited"),
                _edge("limited", "join", target_handle="base"),
                _edge("join", "after"),
            ]
        )
        looked_up = self._recorded_lookups(monkeypatch)

        row, result = self._trace(graph, "after", 2, row_limit=5, column="id")

        assert result.omissions == []
        assert _step_by_id(result, "limited").output_values == {"id": row["id"]}
        assert _step_by_id(result, "base").output_values == {"id": row["id"]}
        assert "limited" not in looked_up
        assert "base" in looked_up

    @pytest.mark.parametrize("node_type", ["liveSwitch", "dataOutput", "modelling"])
    def test_an_unselected_pass_through_input_is_not_given_the_selected_row(
        self, tmp_path, node_type
    ):
        """A pass-through node with several inputs returns only one of them, so a
        unique row below it proves nothing about the other input."""
        from haute._types import GraphNode, NodeData, NodeType

        first_path = tmp_path / "first.parquet"
        second_path = tmp_path / "second.parquet"
        pl.DataFrame({"id": [1]}).write_parquet(first_path)
        pl.DataFrame({"id": [999]}).write_parquet(second_path)
        graph = _g(
            {
                "nodes": [
                    _source_node("first", str(first_path)),
                    _source_node("second", str(second_path)),
                    GraphNode(
                        id="through",
                        data=NodeData(label="through", nodeType=NodeType(node_type), config={}),
                    ),
                    _transform_node("kept", "df = through.filter(pl.col('id') == 1)"),
                ],
                "edges": [
                    _edge("first", "through"),
                    _edge("second", "through"),
                    _edge("through", "kept"),
                ],
            }
        )

        _row, result = self._trace(graph, "kept", 0, row_limit=5, column="id")

        second = next((step for step in result.steps if step.node_id == "second"), None)
        assert second is None or second.output_values != {"id": 1}

    def test_a_duplicated_base_row_is_not_transferred_as_unique(self, tmp_path, monkeypatch):
        from haute._types import GraphNode, NodeData, NodeType

        base_path = tmp_path / "base.parquet"
        lookup_path = tmp_path / "lookup.parquet"
        pl.DataFrame({"id": [3, 1, 1, 0, 2]}).write_parquet(base_path)
        pl.DataFrame({"id": [0, 1, 2, 3], "premium": [0.0, 10.0, 20.0, 30.0]}).write_parquet(
            lookup_path
        )
        graph = _g(
            {
                "nodes": [
                    _source_node("base", str(base_path)),
                    _source_node("lookup", str(lookup_path)),
                    GraphNode(
                        id="join",
                        data=NodeData(
                            label="join",
                            nodeType=NodeType.EDGE_JOIN,
                            config={"how": "left", "on": ["id"]},
                        ),
                    ),
                    _transform_node("after", "df = join.sort('id')"),
                ],
                "edges": [
                    _edge("base", "join", target_handle="base"),
                    _edge("lookup", "join", target_handle="join"),
                    _edge("join", "after"),
                ],
            }
        )

        row, result = self._trace(graph, "after", 1, row_limit=5, column="premium")

        assert row["id"] == 1
        # The two joined id-1 rows are identical in every column: the step shows
        # their values as one of two identical rows, never as a unique row.
        join = next(step for step in result.steps if step.node_id == "join")
        assert join.identical_row_count == 2
        assert "join" not in {omission.node_id for omission in result.omissions}

    def test_a_positional_selector_computation_is_unproven(self, tmp_path):
        graph = self._join_graph(tmp_path, None)
        graph.nodes.append(
            _transform_node("after", "df = join.sort('id').with_columns(pl.nth(1) * 2)")
        )
        graph.edges.append(_edge("join", "after"))

        _row, result = self._trace(graph, "after", 2, row_limit=5, column="id")

        omitted = {omission.node_id: omission.reason for omission in result.omissions}
        assert omitted.get("join") == "row_scope_unproven"

    def test_a_row_local_selector_program_traces_from_head_frames(self, tmp_path, monkeypatch):
        from haute._trace_correlation import RowScopeResolver

        lookups: list[str] = []
        real_lookup = RowScopeResolver.lookup

        def counting_lookup(self, node_id, source_handle, values):
            lookups.append(node_id)
            return real_lookup(self, node_id, source_handle, values)

        monkeypatch.setattr(RowScopeResolver, "lookup", counting_lookup)
        path = tmp_path / "data.parquet"
        pl.DataFrame(
            {"x": [None, *range(1, 30)], "label": [f"l{i}" for i in range(30)]}
        ).write_parquet(path)
        graph = _g(
            {
                "preamble": "import polars.selectors as cs",
                "nodes": [
                    _source_node("src", str(path)),
                    _transform_node("filled", "df = src.with_columns(cs.numeric().fill_null(0))"),
                ],
                "edges": [_edge("src", "filled")],
            }
        )

        row, result = self._trace(graph, "filled", 3, row_limit=5, column="label")

        assert row == {"x": 3, "label": "l3"}
        assert _step_by_id(result, "src").output_values == {"x": 3, "label": "l3"}
        assert lookups == []

    def test_a_selector_renamed_mid_expression_is_not_attributed_by_the_overwritten_column(
        self, tmp_path
    ):
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": [2, 1], "x": [1, 2]}).write_parquet(path)
        code = "df = src.sort('a').with_columns(pl.col('^a$').alias('x').fill_null(0)).drop('a')"
        graph = _g(
            {
                "nodes": [_source_node("src", str(path)), _transform_node("renamed", code)],
                "edges": [_edge("src", "renamed")],
            }
        )

        row, result = self._trace(graph, "renamed", 0, row_limit=5, column="x")

        # ``x`` now holds the smallest ``a`` (1) from the source row ``{a: 1, x: 2}``;
        # the unrelated source row ``{a: 2, x: 1}`` must not be reported.
        assert row == {"x": 1}
        assert "src" not in {step.node_id for step in result.steps}
        assert [omission.reason for omission in result.omissions] == ["row_scope_unproven"]

    def test_code_addressing_its_input_through_input_mapping_traces_both_joins(self, tmp_path):
        from haute._types import GraphNode, NodeData, NodeType

        frames = {
            "raw_rows": pl.DataFrame({"id": [1, 2], "value": [11, 12]}),
            "lookup_rows": pl.DataFrame({"id": [2, 1], "lookup_value": ["b", "a"]}),
            "scores": pl.DataFrame({"id": [2, 1], "api_score": [0.5, 0.25]}),
        }
        nodes = []
        for name, frame in frames.items():
            path = tmp_path / f"{name}.parquet"
            frame.write_parquet(path)
            nodes.append(_source_node(name, str(path)))
        for join_id in ("Edge_Join_1", "Edge_Join_3"):
            nodes.append(
                GraphNode(
                    id=join_id,
                    data=NodeData(
                        label=join_id,
                        nodeType=NodeType.EDGE_JOIN,
                        config={"how": "left", "on": ["id"]},
                    ),
                )
            )
        enriched = _transform_node(
            "enriched",
            "df = raw_rows\ndf = df.with_columns((pl.col('value') * 2).alias('value_doubled'))",
        )
        enriched.data.config["inputMapping"] = {"raw_rows": "Edge_Join_3"}
        nodes.append(enriched)
        graph = _g(
            {
                "nodes": nodes,
                "edges": [
                    _edge("raw_rows", "Edge_Join_1", target_handle="base"),
                    _edge("lookup_rows", "Edge_Join_1", target_handle="join"),
                    _edge("Edge_Join_1", "Edge_Join_3", target_handle="base"),
                    _edge("scores", "Edge_Join_3", target_handle="join"),
                    _edge("Edge_Join_3", "enriched"),
                ],
            }
        )

        row, result = self._trace(graph, "enriched", 0, row_limit=5, column="value_doubled")

        steps = {step.node_id: step.output_values for step in result.steps}
        assert {"Edge_Join_1", "Edge_Join_3", "raw_rows"} <= set(steps)
        assert steps["raw_rows"] == {"id": row["id"], "value": row["value"]}
        assert result.omissions == []

    def test_order_dependent_expression_is_not_positionally_attributed(self, tmp_path):
        path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [0, 1, 2]}).write_parquet(path)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(path)),
                    _transform_node(
                        "reversed", "df = src.with_columns(y=pl.col('x').reverse()).drop('x')"
                    ),
                ],
                "edges": [_edge("src", "reversed")],
            }
        )

        row, result = self._trace(graph, "reversed", 0, row_limit=1, column="y")

        assert row == {"y": 2}
        assert "src" not in {step.node_id for step in result.steps}
        assert [omission.reason for omission in result.omissions] == ["row_scope_unproven"]

    def test_limited_rating_step_traces_without_validating_unread_rows(self, tmp_path):
        from haute._types import GraphNode, NodeData, NodeType

        path = tmp_path / "data.parquet"
        pl.DataFrame({"region": ["north", "missing"]}).write_parquet(path)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(path)),
                    GraphNode(
                        id="rating",
                        data=NodeData(
                            label="rating",
                            nodeType=NodeType.RATING_STEP,
                            config={
                                "tables": [
                                    {
                                        "name": "region",
                                        "factors": ["region"],
                                        "outputColumn": "factor",
                                        "entries": [{"region": "north", "value": 2.0}],
                                    }
                                ]
                            },
                        ),
                    ),
                    _transform_node("first", "df = rating.head(1)"),
                ],
                "edges": [_edge("src", "rating"), _edge("rating", "first")],
            }
        )

        row, result = self._trace(graph, "first", 0, row_limit=100, column="factor")

        assert row == {"region": "north", "factor": 2.0}
        assert _step_by_id(result, "src").output_values == {"region": "north"}
