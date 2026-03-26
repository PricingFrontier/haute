"""Tests for row-identity tracking through cardinality-changing operations.

These tests exercise the core tracing bug: execute_trace uses the same
positional row_index for EVERY node in the pipeline.  After a join, filter,
or sort the positional index in the output no longer corresponds to the
same positional index in the upstream nodes.

All tests in this file are expected to FAIL until the fix is implemented.
Once the trace correctly tracks row identity through the pipeline (rather
than using a fixed positional index), they should all pass.
"""

from __future__ import annotations

import polars as pl

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_by_id(result: TraceResult, node_id: str):
    """Return the TraceStep for a given node_id, or raise."""
    for s in result.steps:
        if s.node_id == node_id:
            return s
    raise KeyError(f"No step with node_id={node_id!r}")


# ===========================================================================
# 1. Joins reorder / change cardinality
# ===========================================================================


class TestJoinRowIdentity:
    """A join changes which source row maps to which output row.

    The trace must follow the join key back to the correct source row
    rather than grabbing the same positional index.
    """

    def test_join_traces_correct_source_rows(self, tmp_path):
        """After a join, row N in the output came from DIFFERENT positional
        rows in the two sources.  The trace should show the matching source
        values, not the values at positional index N."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        # Source A: ids in order 1,2,3
        pl.DataFrame(
            {
                "key": [1, 2, 3],
                "val_a": ["a1", "a2", "a3"],
            }
        ).write_parquet(p_a)

        # Source B: ids in REVERSE order 3,2,1
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

        # Execute the join to find out what row 0 actually contains
        result = execute_trace(graph, row_index=0, target_node_id="join")

        # Get the key value at the traced output row
        join_step = _step_by_id(result, "join")
        output_key = join_step.output_values["key"]

        # The source steps MUST show the same key value — the row that
        # actually contributed to this output row, not just positional row 0.
        a_step = _step_by_id(result, "a")
        b_step = _step_by_id(result, "b")

        assert a_step.output_values["key"] == output_key, (
            f"Source A shows key={a_step.output_values['key']} but output "
            f"row has key={output_key} — trace used wrong source row"
        )
        assert b_step.output_values["key"] == output_key, (
            f"Source B shows key={b_step.output_values['key']} but output "
            f"row has key={output_key} — trace used wrong source row"
        )

    def test_join_non_first_row_traces_correctly(self, tmp_path):
        """Clicking row 2 in joined output should trace back to the
        source rows that produced that specific output row."""
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

        result = execute_trace(graph, row_index=2, target_node_id="join")
        join_step = _step_by_id(result, "join")
        output_key = join_step.output_values["key"]

        a_step = _step_by_id(result, "a")
        b_step = _step_by_id(result, "b")

        # Both sources must agree on which key we're tracing
        assert a_step.output_values["key"] == output_key
        assert b_step.output_values["key"] == output_key

        # And the val columns must be consistent with that key
        # (not just whatever happened to be at positional index 2)
        assert a_step.output_values["price"] == join_step.output_values["price"]
        assert b_step.output_values["factor"] == join_step.output_values["factor"]

    def test_many_to_one_join_traces_correct_source(self, tmp_path):
        """A many-to-one join duplicates rows from the right table.
        Each output row should trace back to the correct right-side row."""
        p_facts = tmp_path / "facts.parquet"
        p_lookup = tmp_path / "lookup.parquet"

        # 5 fact rows referencing 2 distinct region codes
        pl.DataFrame(
            {
                "policy_id": [1, 2, 3, 4, 5],
                "region": ["north", "south", "north", "south", "north"],
                "base_premium": [100, 200, 150, 250, 175],
            }
        ).write_parquet(p_facts)

        # Lookup: 2 rows
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

        # Trace row 3 (policy_id=4, region="south")
        result = execute_trace(graph, row_index=3, target_node_id="join")
        join_step = _step_by_id(result, "join")
        output_region = join_step.output_values["region"]

        lookup_step = _step_by_id(result, "lookup")
        assert lookup_step.output_values["region"] == output_region, (
            f"Lookup shows region={lookup_step.output_values['region']} but "
            f"output row has region={output_region}"
        )
        assert lookup_step.output_values["region_factor"] == (
            1.1 if output_region == "north" else 0.9
        )


# ===========================================================================
# 2. Filters change row positions
# ===========================================================================


class TestFilterRowIdentity:
    """A filter removes rows, so positional indices shift.

    Row 0 in the filtered output is NOT row 0 in the source.
    """

    def test_filter_traces_back_to_correct_source_row(self, tmp_path):
        """After filtering, row 0 in output corresponds to a different
        positional row in the source."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "value": [10, 20, 30, 40, 50],
            }
        ).write_parquet(p)

        # Filter keeps only rows where value > 25 → rows with id 3,4,5
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('value') > 25)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        # Row 0 in filtered output should be id=3, value=30
        result = execute_trace(graph, row_index=0, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")
        src_step = _step_by_id(result, "src")

        assert filt_step.output_values["id"] == 3
        assert filt_step.output_values["value"] == 30

        # Source step must show the SAME row (id=3), not positional row 0 (id=1)
        assert src_step.output_values["id"] == filt_step.output_values["id"], (
            f"Source shows id={src_step.output_values['id']} but filtered "
            f"output row has id={filt_step.output_values['id']} — "
            f"trace used positional index instead of tracking through filter"
        )

    def test_filter_second_row_traces_correctly(self, tmp_path):
        """Row 1 in filtered output traces to the correct source row."""
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

        # Row 1 in filtered output should be id=4, value=40
        result = execute_trace(graph, row_index=1, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")
        src_step = _step_by_id(result, "src")

        assert filt_step.output_values["id"] == 4

        # Source must show the same row
        assert src_step.output_values["id"] == 4, (
            f"Source shows id={src_step.output_values['id']} but filtered output row 1 has id=4"
        )
        assert src_step.output_values["value"] == 40


# ===========================================================================
# 3. Sorts reorder rows
# ===========================================================================


class TestSortRowIdentity:
    """A sort reorders rows.  Positional index 0 in the sorted output
    maps to a different positional index in the unsorted source.
    """

    def test_sort_traces_back_to_correct_source_row(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [3, 1, 2],
                "name": ["charlie", "alice", "bob"],
            }
        ).write_parquet(p)

        # Sort ascending by id → output order: 1,2,3
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        # Row 0 in sorted output is id=1 ("alice")
        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        sorted_step = _step_by_id(result, "sorted")
        src_step = _step_by_id(result, "src")

        assert sorted_step.output_values["id"] == 1
        assert sorted_step.output_values["name"] == "alice"

        # Source must show the same record (id=1), not positional row 0 (id=3)
        assert src_step.output_values["id"] == 1, (
            f"Source shows id={src_step.output_values['id']} but sorted "
            f"output row 0 has id=1 — trace didn't track through sort"
        )
        assert src_step.output_values["name"] == "alice"

    def test_sort_descending_traces_correctly(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "score": [50, 40, 30, 20, 10],
            }
        ).write_parquet(p)

        # Sort descending by score → output order: 50,40,30,20,10
        # (same as input in this case, but sort by id desc reverses it)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id', descending=True)"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        # Row 0 in desc-sorted output is id=5
        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        sorted_step = _step_by_id(result, "sorted")
        src_step = _step_by_id(result, "src")

        assert sorted_step.output_values["id"] == 5

        # Source must show id=5, not positional row 0 (id=1)
        assert src_step.output_values["id"] == 5, (
            f"Source shows id={src_step.output_values['id']} but sorted output row 0 has id=5"
        )


# ===========================================================================
# 4. Multi-step pipelines (filter + join + sort)
# ===========================================================================


class TestMultiStepRowIdentity:
    """Compound pipelines where multiple operations shift row identity."""

    def test_filter_then_sort_traces_correctly(self, tmp_path):
        """filter → sort: both operations change row positions."""
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
                    # Keep value >= 30 → ids 5,3,4
                    _transform_node("filt", ".filter(pl.col('value') >= 30)"),
                    # Sort by id asc → ids 3,4,5
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "filt"), _edge("filt", "sorted")],
            }
        )

        # Row 0 in final output: id=3, value=30
        result = execute_trace(graph, row_index=0, target_node_id="sorted")

        sorted_step = _step_by_id(result, "sorted")
        filt_step = _step_by_id(result, "filt")
        src_step = _step_by_id(result, "src")

        assert sorted_step.output_values["id"] == 3

        # Every step must show the same record
        assert filt_step.output_values["id"] == 3, "Filter step shows wrong row"
        assert src_step.output_values["id"] == 3, "Source step shows wrong row"

    def test_join_then_filter_traces_correctly(self, tmp_path):
        """join → filter: join reorders, then filter removes rows."""
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
                    # Keep only rows where amount > 150
                    _transform_node("filt", ".filter(pl.col('amount') > 150)"),
                ],
                "edges": [
                    _edge("a", "join"),
                    _edge("b", "join"),
                    _edge("join", "filt"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")
        output_key = filt_step.output_values["key"]

        # Every upstream step must show the same key
        join_step = _step_by_id(result, "join")
        a_step = _step_by_id(result, "a")
        b_step = _step_by_id(result, "b")

        assert join_step.output_values["key"] == output_key
        assert a_step.output_values["key"] == output_key, (
            f"Source A shows key={a_step.output_values['key']} but output has key={output_key}"
        )
        assert b_step.output_values["key"] == output_key, (
            f"Source B shows key={b_step.output_values['key']} but output has key={output_key}"
        )


# ===========================================================================
# 5. Output value consistency
# ===========================================================================


class TestOutputValueConsistency:
    """The output_value on TraceResult must match the actual data at the
    target node for the clicked row — not data from a different row."""

    def test_output_value_matches_target_node_row_after_sort(self, tmp_path):
        """output_value must reflect the exact row the user clicked,
        AND the source step must show the same logical record."""
        p = tmp_path / "data.parquet"
        df = pl.DataFrame(
            {
                "id": [5, 3, 1, 4, 2],
                "value": [50, 30, 10, 40, 20],
            }
        )
        df.write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        # After sorting by id: [1,2,3,4,5]
        # Row 2 should be id=3, value=30
        result = execute_trace(graph, row_index=2, target_node_id="sorted")

        assert result.output_value["id"] == 3
        assert result.output_value["value"] == 30

        # Source step must also show id=3, not positional row 2 (id=1)
        src_step = _step_by_id(result, "src")
        assert src_step.output_values["id"] == 3, (
            f"Source shows id={src_step.output_values['id']} but output row 2 (after sort) has id=3"
        )

    def test_trace_column_value_after_sort(self, tmp_path):
        """When tracing a specific column, output_value must still reflect
        the correct row."""
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

        # Sorted: id=[1,2,3], score=[100,200,300]
        # Row 0, column "score" → should be 100
        result = execute_trace(graph, row_index=0, target_node_id="sorted", column="score")

        sorted_step = _step_by_id(result, "sorted")
        assert sorted_step.output_values["score"] == 100

        src_step = _step_by_id(result, "src")
        assert src_step.output_values["id"] == 1, (
            "Source should show the row for id=1, not positional row 0 (id=3)"
        )


# ===========================================================================
# 6. Aggregation changes cardinality
# ===========================================================================


class TestAggregationRowIdentity:
    """group_by reduces cardinality — output rows don't map 1:1 to source rows."""

    def test_groupby_output_row_does_not_use_source_positional_index(self, tmp_path):
        """After a group_by, the output has fewer rows than the source.
        Row N in the aggregated output does not correspond to row N in
        the source.

        We sort the aggregated output to guarantee a known order that
        differs from the source's positional layout.
        """
        p = tmp_path / "data.parquet"
        # Source row 0 is "south", row 1 is "north", etc.
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

        # After group_by + sort by region: row 0 = "north", row 1 = "south"
        # Source positional row 0 = "south" — so a naive positional lookup
        # will show the wrong region.
        result = execute_trace(graph, row_index=0, target_node_id="agg")
        agg_step = _step_by_id(result, "agg")
        src_step = _step_by_id(result, "src")

        # Aggregated row 0 should be "north" (alphabetically first)
        assert agg_step.output_values["region"] == "north"

        # Source positional row 0 is "south" — the bug will show "south"
        # instead of a "north" row.
        assert src_step.output_values["region"] == "north", (
            f"Source shows region={src_step.output_values['region']} but "
            f"aggregated output row 0 has region='north' — trace used "
            f"positional index instead of tracking through aggregation"
        )


# ===========================================================================
# 7. Edge case: input_values consistency
# ===========================================================================


class TestInputValuesConsistency:
    """The input_values of a node must match the output_values of its
    parent for the SAME logical row.  The current bug causes input_values
    and output_values to come from different rows when the node changes
    row order.
    """

    def test_input_values_match_parent_output_values(self, tmp_path):
        """For a sort node, input_values should equal the parent's
        output_values for the same logical row."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [3, 1, 2],
                "val": [30, 10, 20],
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

        # Sorted row 0 = id=1.  The sort node's input_values should show
        # the pre-sort state of that same record (id=1, val=10),
        # NOT positional row 0 from source (id=3, val=30).
        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        sorted_step = _step_by_id(result, "sorted")

        assert sorted_step.input_values["id"] == 1, (
            f"Sort node input_values shows id={sorted_step.input_values['id']} "
            f"but should show id=1 (the record that ended up at output row 0)"
        )

    def test_input_values_match_parent_for_filter(self, tmp_path):
        """For a filter node, input_values should reflect the actual
        source row that survived the filter, not positional row N."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "keep": [False, False, True, False, True],
                "val": [10, 20, 30, 40, 50],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('keep'))"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        # Filtered row 0 = id=3.  Filter's input_values should show id=3.
        result = execute_trace(graph, row_index=0, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")

        assert filt_step.input_values["id"] == 3, (
            f"Filter input_values shows id={filt_step.input_values['id']} but should show id=3"
        )
