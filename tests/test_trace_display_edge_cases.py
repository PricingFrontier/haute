"""Tests for trace display edge cases — unusual data patterns, pipeline structures,
and value types that the trace system must handle gracefully.

Each test builds a real pipeline, runs ``execute_trace()``, and verifies the
enrichment fields handle the edge case correctly.

Categories:
  1. Value type edge cases (None, NaN, Inf, bool, date, etc.)
  2. Column identity edge cases (alias, overwrite, join suffix, special names)
  3. Pipeline structure edge cases (single node, long chains, diamonds, fan-out)
  4. Row correlation edge cases (filter, sort, join, positional fallback)
  5. Expression parser integration (arithmetic, conditional, opaque, variable resolution)
  6. Calculation accuracy (verify result_value matches actual output)
  7. Serialization edge cases (trace_result_to_dict correctness)
  8. Concurrent/cache edge cases (different rows, columns, cache invalidation)
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.executor import execute_graph
from haute.trace import (
    SchemaDiff,
    TraceResult,
    TraceStep,
    execute_trace,
    trace_result_to_dict,
)
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_ready_file_input_config
from tests.conftest import make_source_node as _source_node
from tests.conftest import make_transform_node as _transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

NAN_SENTINEL = {"__haute_type__": "non_finite_float", "value": "nan"}
INF_SENTINEL = {"__haute_type__": "non_finite_float", "value": "inf"}
NEG_INF_SENTINEL = {"__haute_type__": "non_finite_float", "value": "-inf"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_by_id(result: TraceResult, node_id: str) -> TraceStep:
    """Return the TraceStep for a given node_id, or raise."""
    for s in result.steps:
        if s.node_id == node_id:
            return s
    raise KeyError(f"No step with node_id={node_id!r}")


def _step_ids(result: TraceResult) -> list[str]:
    """Return ordered list of node_ids in the trace."""
    return [s.node_id for s in result.steps]


# ===========================================================================
# CATEGORY 1: Value Type Edge Cases (12 tests)
# ===========================================================================


class TestValueTypeEdgeCases:
    """Trace handling of unusual value types."""

    def test_null_value_in_traced_column(self, tmp_path):
        """NULL value in a traced column is handled gracefully."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "x": pl.Series([None, 10, 20], dtype=pl.Float64),
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        # NULL * 2 = NULL — trace should not crash
        assert step.output_values["y"] is None

    def test_nan_from_division_by_zero(self, tmp_path):
        """0.0 / 0.0 produces NaN — trace should not crash."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [0.0], "b": [0.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(c=pl.col('a') / pl.col('b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["c"] == NAN_SENTINEL

    def test_positive_infinity(self, tmp_path):
        """1.0 / 0.0 produces +Inf — trace handles inf."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1.0], "b": [0.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(c=pl.col('a') / pl.col('b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["c"] == INF_SENTINEL

    def test_negative_infinity(self, tmp_path):
        """-1.0 / 0.0 produces -Inf — trace handles negative inf."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [-1.0], "b": [0.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(c=pl.col('a') / pl.col('b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["c"] == NEG_INF_SENTINEL

    def test_zero_value_not_treated_as_missing(self, tmp_path):
        """premium * 0 = 0 — zero should be shown, not treated as missing."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [100.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(result=pl.col('premium') * 0)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["result"] == 0.0

    def test_very_large_float(self, tmp_path):
        """1e15 * 2 — verify no overflow in trace."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1e15]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["y"] == 2e15

    def test_very_small_float(self, tmp_path):
        """1e-10 * 3 — verify precision preserved."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1e-10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 3)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert abs(step.output_values["y"] - 3e-10) < 1e-20

    def test_boolean_column(self, tmp_path):
        """pl.col('x') > 5 produces bool — trace shows True/False."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(flag=(pl.col('x') > 5))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        r0 = execute_trace(graph, row_index=0)
        r1 = execute_trace(graph, row_index=1)
        assert _step_by_id(r0, "t").output_values["flag"] is True
        assert _step_by_id(r1, "t").output_values["flag"] is False

    def test_string_column_from_source(self, tmp_path):
        """Trace a string column from source data."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"name": ["Alice", "Bob"]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["name"] == "Alice"

    def test_date_column(self, tmp_path):
        """Trace a date column — should be stringified."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "d": pl.Series([date(2025, 1, 15), date(2025, 6, 30)]),
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        # _jsonify_row converts date to string
        assert step.output_values["d"] == "2025-01-15"

    def test_integer_column_no_float_suffix(self, tmp_path):
        """Trace an integer column — verify int type preserved (no .0 suffix)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [42]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["x"] == 42
        assert isinstance(step.output_values["x"], int)

    def test_all_identical_values_row_correlation(self, tmp_path):
        """Column with all identical values — row correlation still works."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3],
                "constant": [100, 100, 100],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('id') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # Trace row 1 (id=2) — correlation should use 'id' to distinguish
        result = execute_trace(graph, row_index=1)
        step = _step_by_id(result, "t")
        assert step.output_values["id"] == 2
        assert step.output_values["y"] == 4


# ===========================================================================
# CATEGORY 2: Column Identity Edge Cases (8 tests)
# ===========================================================================


class TestColumnIdentityEdgeCases:
    """Trace handling of column renaming, overwriting, join suffix, and special names."""

    def test_column_renamed_via_alias(self, tmp_path):
        """Trace the alias name — verify expression references original column."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(pl.col('x').alias('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.output_values["y"] == 10
        assert "y" in step.schema_diff.columns_added

    def test_column_overwritten_by_second_with_columns(self, tmp_path):
        """Two with_columns both create 'x' — trace 'x' shows the last one."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"base": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t1",
                        "df = src.with_columns(x=pl.col('base') * 2)",
                    ),
                    _transform_node(
                        "t2",
                        "df = t1.with_columns(x=pl.col('x') + 100)",
                    ),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, column="x")
        step = _step_by_id(result, "t2")
        # base=10, x=20 from t1, then x=20+100=120 from t2
        assert step.output_values["x"] == 120

    def test_column_from_right_side_of_join(self, tmp_path):
        """Trace a column that came from the joined (right) table."""
        p_left = tmp_path / "left.parquet"
        p_right = tmp_path / "right.parquet"
        pl.DataFrame({"key": [1], "left_val": [10]}).write_parquet(p_left)
        pl.DataFrame({"key": [1], "right_val": [99]}).write_parquet(p_right)

        graph = _g(
            {
                "nodes": [
                    _source_node("left", str(p_left)),
                    _source_node("right", str(p_right)),
                    _transform_node("join", "df = left.join(right, on='key')"),
                ],
                "edges": [_edge("left", "join"), _edge("right", "join")],
            }
        )

        result = execute_trace(graph, row_index=0, column="right_val")
        step = _step_by_id(result, "join")
        assert step.output_values["right_val"] == 99

    def test_column_duplicate_in_both_join_sides(self, tmp_path):
        """Both sides of join have 'label' — Polars adds _right suffix."""
        p_left = tmp_path / "left.parquet"
        p_right = tmp_path / "right.parquet"
        pl.DataFrame({"key": [1], "label": ["left_label"]}).write_parquet(p_left)
        pl.DataFrame({"key": [1], "label": ["right_label"]}).write_parquet(p_right)

        graph = _g(
            {
                "nodes": [
                    _source_node("left", str(p_left)),
                    _source_node("right", str(p_right)),
                    _transform_node("join", "df = left.join(right, on='key')"),
                ],
                "edges": [_edge("left", "join"), _edge("right", "join")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "join")
        assert "label" in step.output_values
        assert "label_right" in step.output_values
        assert step.output_values["label"] == "left_label"
        assert step.output_values["label_right"] == "right_label"

    def test_column_removed_by_select(self, tmp_path):
        """Trace a column that was dropped by .select() — verify graceful handling."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "y": [2], "z": [3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.select(['x', 'y'])"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # Tracing 'z' which was dropped — should not crash
        result = execute_trace(graph, row_index=0, column="z")
        # z doesn't exist in any output, so either no steps are column_relevant
        # or it gracefully returns an empty/pruned trace
        assert isinstance(result, TraceResult)

    def test_column_with_space_in_name(self, tmp_path):
        """Column named 'driver age' — verify no crash."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"driver age": [25, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('driver age') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["driver age"] == 25
        assert step.output_values["y"] == 50

    def test_column_with_dots_in_name(self, tmp_path):
        """Column named 'proposer.date_of_birth' — from JSON flattening."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"proposer.date_of_birth": ["1990-01-01"]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["proposer.date_of_birth"] == "1990-01-01"

    def test_column_named_class_python_keyword(self, tmp_path):
        """Column name that's a Python keyword: 'class' — verify no crash."""
        p = tmp_path / "data.parquet"
        # Use pl.DataFrame with explicit column name to avoid Python keyword issues
        df = pl.DataFrame({"class": ["sedan", "suv"], "value": [100, 200]})
        df.write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        step = _step_by_id(result, "t")
        assert step.output_values["class"] == "sedan"


# ===========================================================================
# CATEGORY 3: Pipeline Structure Edge Cases (10 tests)
# ===========================================================================


class TestPipelineStructureEdgeCases:
    """Trace handling of various pipeline topologies."""

    def test_single_node_pipeline_source_only(self, tmp_path):
        """Source-only pipeline: verify trace has 1 step, expression=None."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0)
        assert len(result.steps) == 1
        assert result.steps[0].node_id == "src"
        assert result.steps[0].expression is None

    def test_two_node_pipeline_source_plus_transform(self, tmp_path):
        """Basic source + transform pipeline works."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        assert len(result.steps) == 2
        assert _step_ids(result) == ["src", "t"]
        assert _step_by_id(result, "t").output_values["y"] == 6

    def test_long_chain_eight_nodes(self, tmp_path):
        """8+ node chain: verify all steps present in correct order."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        nodes = [_source_node("src", str(p))]
        edges = []
        prev = "src"
        for i in range(1, 8):
            nid = f"t{i}"
            nodes.append(_transform_node(nid, f"df = {prev}.with_columns(x{i}=pl.col('x') + {i})"))
            edges.append(_edge(prev, nid))
            prev = nid

        graph = _g({"nodes": nodes, "edges": edges})
        result = execute_trace(graph, row_index=0)

        expected_ids = ["src"] + [f"t{i}" for i in range(1, 8)]
        assert _step_ids(result) == expected_ids
        assert len(result.steps) == 8

    def test_diamond_pattern(self, tmp_path):
        """Diamond: A->B, A->C, B->D, C->D — verify both branches in trace."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "key": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p)),
                    _transform_node("b", "df = a.with_columns(b_val=pl.col('x') * 10)"),
                    _transform_node("c", "df = a.with_columns(c_val=pl.col('x') * 100)"),
                    _transform_node("d", "df = b.join(c, on='key')"),
                ],
                "edges": [
                    _edge("a", "b"),
                    _edge("a", "c"),
                    _edge("b", "d"),
                    _edge("c", "d"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="d")
        ids = set(_step_ids(result))
        assert "a" in ids
        assert "d" in ids
        # At least one of b or c should be in the trace
        assert "b" in ids or "c" in ids

    def test_fan_out_trace_one_branch(self, tmp_path):
        """Fan-out: A->B, A->C — trace B only, verify C not in trace."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p)),
                    _transform_node("b", "df = a.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("c", "df = a.with_columns(z=pl.col('x') + 2)"),
                ],
                "edges": [_edge("a", "b"), _edge("a", "c")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="b")
        ids = set(_step_ids(result))
        assert "a" in ids
        assert "b" in ids
        assert "c" not in ids

    def test_node_with_no_code_raises_not_implemented(self, tmp_path):
        """Node with empty code now raises NotImplementedError, no silent passthrough."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [42]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("passthrough", ""),
                ],
                "edges": [_edge("src", "passthrough")],
            }
        )

        with pytest.raises(NotImplementedError):
            execute_trace(graph, row_index=0, column="x")

    def test_node_with_trivial_no_op_code(self, tmp_path):
        """Node with a no-op select (all columns): expression=None for pass-through."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5], "y": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # .select(pl.all()) is effectively a no-op passthrough
                    _transform_node("t", "df = src.select(pl.all())"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="x")
        step = _step_by_id(result, "t")
        assert step.output_values["x"] == 5
        assert step.expression is None

    def test_multiple_sources_feeding_join(self, tmp_path):
        """Two source nodes feeding into a join — verify both in trace."""
        p1 = tmp_path / "left.parquet"
        p2 = tmp_path / "right.parquet"
        pl.DataFrame({"key": [1], "a": [10]}).write_parquet(p1)
        pl.DataFrame({"key": [1], "b": [20]}).write_parquet(p2)

        graph = _g(
            {
                "nodes": [
                    _source_node("src1", str(p1)),
                    _source_node("src2", str(p2)),
                    _transform_node("join", "df = src1.join(src2, on='key')"),
                ],
                "edges": [_edge("src1", "join"), _edge("src2", "join")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="join")
        ids = set(_step_ids(result))
        assert "src1" in ids
        assert "src2" in ids
        assert "join" in ids

    def test_submodel_like_pattern(self, tmp_path):
        """Node group simulating submodel: trace includes all inner nodes."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [100]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("step1", "df = src.with_columns(a=pl.col('x') * 0.5)"),
                    _transform_node("step2", "df = step1.with_columns(b=pl.col('a') + 10)"),
                    _transform_node("step3", "df = step2.with_columns(c=pl.col('b') * 2)"),
                ],
                "edges": [
                    _edge("src", "step1"),
                    _edge("step1", "step2"),
                    _edge("step2", "step3"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="step3")
        ids = _step_ids(result)
        assert ids == ["src", "step1", "step2", "step3"]
        assert _step_by_id(result, "step3").output_values["c"] == 120.0

    def test_pipeline_with_live_switch(self, tmp_path):
        """Pipeline with live_switch: correct branch selected, other pruned."""
        p_live = tmp_path / "live.parquet"
        p_batch = tmp_path / "batch.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p_live)
        pl.DataFrame({"x": [10, 20]}).write_parquet(p_batch)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="live_src",
                    data=NodeData(
                        label="live_src",
                        nodeType="dataInput",
                        config=make_ready_file_input_config(p_live),
                    ),
                ),
                GraphNode(
                    id="batch_src",
                    data=NodeData(
                        label="batch_src",
                        nodeType="dataInput",
                        config=make_ready_file_input_config(p_batch),
                    ),
                ),
                GraphNode(
                    id="sw",
                    data=NodeData(
                        label="switch",
                        nodeType="liveSwitch",
                        config={
                            "input_scenario_map": {
                                "live_src": "live",
                                "batch_src": "nb_batch",
                            }
                        },
                    ),
                ),
            ],
            edges=[
                GraphEdge(id="e1", source="live_src", target="sw"),
                GraphEdge(id="e2", source="batch_src", target="sw"),
            ],
        )

        result = execute_trace(graph, row_index=0, target_node_id="sw", source="nb_batch")
        step_ids_set = {s.node_id for s in result.steps}
        assert "batch_src" in step_ids_set
        assert "live_src" not in step_ids_set


# ===========================================================================
# CATEGORY 4: Row Correlation Edge Cases (8 tests)
# ===========================================================================


class TestRowCorrelationEdgeCases:
    """Verify correct row tracking through filters, sorts, joins."""

    def test_same_row_count_positional_match(self, tmp_path):
        """Same row count parent/child: fast-path positional match works."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=1)
        src_step = _step_by_id(result, "src")
        t_step = _step_by_id(result, "t")
        # Row 1: x=20 in source, y=40 in transform
        assert src_step.output_values["x"] == 20
        assert t_step.output_values["y"] == 40

    def test_filter_reduces_rows(self, tmp_path):
        """Filter reduces rows: correct row tracked through filter."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3, 4, 5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("f", "df = src.filter(pl.col('x') > 2)"),
                ],
                "edges": [_edge("src", "f")],
            }
        )

        # After filter: [3, 4, 5], row 0 = x=3
        result = execute_trace(graph, row_index=0, target_node_id="f")
        f_step = _step_by_id(result, "f")
        assert f_step.output_values["x"] == 3
        # Source step should show the corresponding parent row (x=3)
        src_step = _step_by_id(result, "src")
        assert src_step.output_values["x"] == 3

    def test_sort_reorders_rows(self, tmp_path):
        """Sort reorders rows: correct row after sort."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [30, 10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("s", "df = src.sort('x')"),
                ],
                "edges": [_edge("src", "s")],
            }
        )

        # After sort: [10, 20, 30], row 0 = x=10
        result = execute_trace(graph, row_index=0, target_node_id="s")
        s_step = _step_by_id(result, "s")
        assert s_step.output_values["x"] == 10

    def test_left_join_with_null_columns(self, tmp_path):
        """Left join with some NULLs: row correlation handles null columns."""
        p_left = tmp_path / "left.parquet"
        p_right = tmp_path / "right.parquet"
        pl.DataFrame({"key": [1, 2], "a": [10, 20]}).write_parquet(p_left)
        pl.DataFrame({"key": [1], "b": [99]}).write_parquet(p_right)

        graph = _g(
            {
                "nodes": [
                    _source_node("left", str(p_left)),
                    _source_node("right", str(p_right)),
                    _transform_node("join", "df = left.join(right, on='key', how='left')"),
                ],
                "edges": [_edge("left", "join"), _edge("right", "join")],
            }
        )

        # Trace both rows and check that one has null b
        r0 = execute_trace(graph, row_index=0, target_node_id="join")
        r1 = execute_trace(graph, row_index=1, target_node_id="join")
        b_values = [
            _step_by_id(r0, "join").output_values["b"],
            _step_by_id(r1, "join").output_values["b"],
        ]
        # One row matched (b=99), the other didn't (b=null)
        assert None in b_values
        assert 99 in b_values

    def test_positional_fallback_no_shared_columns(self, tmp_path):
        """Column renamed between parent/child (no shared columns): positional fallback."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.rename({'x': 'y'})"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        t_step = _step_by_id(result, "t")
        assert t_step.output_values["y"] == 10
        src_step = _step_by_id(result, "src")
        assert src_step.output_values["x"] == 10

    def test_filter_parent_has_more_rows(self, tmp_path):
        """Parent has more rows than child (filter): value matching finds correct parent row."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2, 3, 4], "val": [10, 20, 30, 40]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("f", "df = src.filter(pl.col('val') >= 30)"),
                ],
                "edges": [_edge("src", "f")],
            }
        )

        # After filter: [{id:3,val:30}, {id:4,val:40}]
        result = execute_trace(graph, row_index=0, target_node_id="f")
        f_step = _step_by_id(result, "f")
        assert f_step.output_values["id"] == 3
        src_step = _step_by_id(result, "src")
        assert src_step.output_values["id"] == 3

    def test_all_rows_identical_except_one_column(self, tmp_path):
        """All rows identical except one column: that column is used for matching."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "id": [1, 2, 3],
                "same": [100, 100, 100],
                "same2": ["a", "a", "a"],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('id') * 10)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=2)
        step = _step_by_id(result, "t")
        assert step.output_values["id"] == 3
        assert step.output_values["y"] == 30

    def test_join_namespace_prefixing_in_input_values(self, tmp_path):
        """Join where both parents have same column name: verify namespace prefixing."""
        p1 = tmp_path / "left.parquet"
        p2 = tmp_path / "right.parquet"
        pl.DataFrame({"key": [1], "val": [10]}).write_parquet(p1)
        pl.DataFrame({"key": [1], "val": [20]}).write_parquet(p2)

        graph = _g(
            {
                "nodes": [
                    _source_node("left", str(p1)),
                    _source_node("right", str(p2)),
                    _transform_node("join", "df = left.join(right, on='key')"),
                ],
                "edges": [_edge("left", "join"), _edge("right", "join")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="join")
        step = _step_by_id(result, "join")
        # The input_values should contain both parents' val, possibly namespaced
        input_vals = step.input_values
        # Either val and right.val, or left.val and val — both parents' values present
        val_keys = [k for k in input_vals if "val" in k]
        assert len(val_keys) >= 2, f"Expected both parents' val in input_values, got {input_vals}"


# ===========================================================================
# CATEGORY 5: Expression Parser Integration (10 tests)
# ===========================================================================


class TestExpressionParserIntegration:
    """Test the expression parser through the full trace flow.

    Note: The expression parser requires valid Python code (assignment-style,
    e.g., ``df = df.with_columns(...)``).  The shorthand dot-syntax
    (``.with_columns(...)``) used in many tests is an executor convenience
    that the parser treats as opaque.  These tests use the full syntax.
    """

    def test_simple_arithmetic_expression_populated(self, tmp_path):
        """Simple arithmetic: verify step.expression populated."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.expression is not None
        assert step.expression["expression_type"] == "arithmetic"
        assert "x" in step.expression["referenced_columns"]

    def test_conditional_expression_type(self, tmp_path):
        """Conditional (when/then): verify expression_type='conditional'."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns("
                        "y=pl.when(pl.col('x') > 5).then(pl.lit(1)).otherwise(pl.lit(0)))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.expression is not None
        assert step.expression["expression_type"] == "conditional"

    @pytest.mark.filterwarnings("ignore::polars.exceptions.PolarsInefficientMapWarning")
    def test_opaque_pattern_map_elements(self, tmp_path):
        """map_elements: verify expression_type='opaque'."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns("
                        "y=pl.col('x').map_elements(lambda v: v * 2, return_dtype=pl.Int64))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.expression is not None
        assert step.expression["expression_type"] == "opaque"

    def test_no_with_columns_expression_none(self, tmp_path):
        """Filter/sort only (no with_columns): verify expression=None."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.filter(pl.col('x') > 1)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="x")
        step = _step_by_id(result, "t")
        # x is passed through, not created by with_columns — expression should be None
        assert step.expression is None

    def test_variable_resolution_in_expression(self, tmp_path):
        """Variable resolution: rate = 0.7; df = df.with_columns(result=pl.col('x') * rate)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [100]}).write_parquet(p)

        code = "rate = 0.7\ndf = src.with_columns(result=pl.col('x') * rate)"
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", code),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="result")
        step = _step_by_id(result, "t")
        assert step.output_values["result"] == 70.0
        # Expression should have resolved the variable
        assert step.expression is not None
        assert "0.7" in step.expression["expression_text"]

    def test_multiple_expressions_correct_one_extracted(self, tmp_path):
        """Multiple expressions in one with_columns: trace specific column extracts correct one."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10], "z": [5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns(a=pl.col('x') * 2, b=pl.col('z') + 100)",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="b")
        step = _step_by_id(result, "t")
        assert step.expression is not None
        # Should reference z, not x
        assert "z" in step.expression["referenced_columns"]
        assert step.expression["target_column"] == "b"

    def test_expression_with_fill_null(self, tmp_path):
        """Expression with .fill_null: verify method chain in expression_text."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": pl.Series([None], dtype=pl.Float64)}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x').fill_null(0.0))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.output_values["y"] == 0.0
        assert step.expression is not None
        assert "fill_null" in step.expression["expression_text"]

    def test_expression_with_cast(self, tmp_path):
        """Expression with .cast: verify cast in expression_text."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x').cast(pl.Float64))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.output_values["y"] == 10.0
        assert step.expression is not None
        assert "cast" in step.expression["expression_text"]

    def test_horizontal_func_in_trace(self, tmp_path):
        """Horizontal function: verify expression_type='horizontal_func'."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [10.0], "b": [20.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(m=pl.min_horizontal('a', 'b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="m")
        step = _step_by_id(result, "t")
        assert step.output_values["m"] == 10.0
        assert step.expression is not None
        assert step.expression["expression_type"] == "horizontal_func"

    def test_column_not_found_by_parser_expression_none(self, tmp_path):
        """Target column not in any with_columns: verify expression=None."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "y": [2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(z=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # Trace column 'y' which is only passed through, not in any with_columns
        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.expression is None


# ===========================================================================
# CATEGORY 6: Calculation Accuracy (8 tests)
# ===========================================================================


class TestCalculationAccuracy:
    """Verify that calculation.result_value matches actual output value."""

    def test_simple_multiply(self, tmp_path):
        """100 * 0.7 = 70.0."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [100.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 0.7)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert abs(step.output_values["y"] - 70.0) < 0.01
        if step.calculation is not None:
            assert abs(step.calculation["result_value"] - 70.0) < 0.01

    def test_division_float_precision(self, tmp_path):
        """100 / 3 — verify float precision."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [100.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') / 3)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert abs(step.output_values["y"] - 33.3333333) < 0.001
        if step.calculation is not None:
            assert abs(step.calculation["result_value"] - 33.3333333) < 0.001

    def test_addition(self, tmp_path):
        """100 + 200 = 300."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [100], "b": [200]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(c=pl.col('a') + pl.col('b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="c")
        step = _step_by_id(result, "t")
        assert step.output_values["c"] == 300

    def test_subtraction(self, tmp_path):
        """500 - 150 = 350."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [500], "b": [150]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(c=pl.col('a') - pl.col('b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="c")
        step = _step_by_id(result, "t")
        assert step.output_values["c"] == 350

    def test_conditional_result_matches_branch(self, tmp_path):
        """Conditional: when True, result matches the taken branch value."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns("
                        "y=pl.when(pl.col('x') > 5).then(pl.lit(100)).otherwise(pl.lit(0)))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        step = _step_by_id(result, "t")
        assert step.output_values["y"] == 100
        if step.calculation is not None:
            assert step.calculation["result_value"] == 100

    def test_chain_multiplication(self, tmp_path):
        """a * b * c — verify final result."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [2.0], "b": [3.0], "c": [5.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns(result=pl.col('a') * pl.col('b') * pl.col('c'))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="result")
        step = _step_by_id(result, "t")
        assert step.output_values["result"] == 30.0

    def test_with_null_input_result_is_null(self, tmp_path):
        """NULL input: verify result is None/null."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "a": pl.Series([None], dtype=pl.Float64),
                "b": [10.0],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(c=pl.col('a') * pl.col('b'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="c")
        step = _step_by_id(result, "t")
        assert step.output_values["c"] is None

    def test_mixed_operations_order(self, tmp_path):
        """(a + b) * c — verify order of operations correct."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [10.0], "b": [20.0], "c": [3.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns(result=(pl.col('a') + pl.col('b')) * pl.col('c'))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="result")
        step = _step_by_id(result, "t")
        # (10 + 20) * 3 = 90
        assert step.output_values["result"] == 90.0


# ===========================================================================
# CATEGORY 7: Serialization Edge Cases (5 tests)
# ===========================================================================


class TestSerializationEdgeCases:
    """Verify trace_result_to_dict() correctly serializes all enrichment fields."""

    def test_expression_field_serializes(self, tmp_path):
        """expression field serializes to dict with all keys."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        d = trace_result_to_dict(result)
        t_step = [s for s in d["steps"] if s["node_id"] == "t"][0]
        if t_step["expression"] is not None:
            assert "expression_text" in t_step["expression"]
            assert "expression_type" in t_step["expression"]
            assert "referenced_columns" in t_step["expression"]
            assert "target_column" in t_step["expression"]

    def test_calculation_field_serializes_with_input_values(self, tmp_path):
        """calculation field serializes with input_values."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, column="y")
        d = trace_result_to_dict(result)
        t_step = [s for s in d["steps"] if s["node_id"] == "t"][0]
        if t_step["calculation"] is not None:
            assert "input_values" in t_step["calculation"]
            assert "result_value" in t_step["calculation"]

    def test_node_detail_serializes_correctly(self):
        """node_detail serializes correctly when present."""
        result = TraceResult(
            target_node_id="t",
            row_index=0,
            column=None,
            output_value={"x": 1},
            steps=[
                TraceStep(
                    node_id="t",
                    node_name="Transform",
                    node_type="polars",
                    schema_diff=SchemaDiff(
                        columns_added=["x"],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=[],
                    ),
                    input_values={},
                    output_values={"x": 1},
                    node_detail={"detail_type": "rating_step", "tables": []},
                ),
            ],
            total_nodes_in_pipeline=1,
            nodes_in_trace=1,
        )
        d = trace_result_to_dict(result)
        assert d["steps"][0]["node_detail"] == {"detail_type": "rating_step", "tables": []}

    def test_row_lineage_type_serializes_as_string(self):
        """row_lineage_type serializes as string."""
        result = TraceResult(
            target_node_id="t",
            row_index=0,
            column=None,
            output_value={"x": 1},
            steps=[
                TraceStep(
                    node_id="t",
                    node_name="Transform",
                    node_type="polars",
                    schema_diff=SchemaDiff(
                        columns_added=["x"],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=[],
                    ),
                    input_values={},
                    output_values={"x": 1},
                    row_lineage_type="passthrough",
                ),
            ],
            total_nodes_in_pipeline=1,
            nodes_in_trace=1,
        )
        d = trace_result_to_dict(result)
        assert d["steps"][0]["row_lineage_type"] == "passthrough"
        assert isinstance(d["steps"][0]["row_lineage_type"], str)

    def test_all_none_fields_serialize_as_null(self):
        """All None enrichment fields serialize as null (not missing keys)."""
        result = TraceResult(
            target_node_id="t",
            row_index=0,
            column=None,
            output_value={"x": 1},
            steps=[
                TraceStep(
                    node_id="t",
                    node_name="Transform",
                    node_type="polars",
                    schema_diff=SchemaDiff(
                        columns_added=["x"],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=[],
                    ),
                    input_values={},
                    output_values={"x": 1},
                    # All enrichment fields left as None (default)
                ),
            ],
            total_nodes_in_pipeline=1,
            nodes_in_trace=1,
        )
        d = trace_result_to_dict(result)
        step_d = d["steps"][0]
        # Keys must be present even when None
        assert "expression" in step_d
        assert step_d["expression"] is None
        assert "calculation" in step_d
        assert step_d["calculation"] is None
        assert "node_detail" in step_d
        assert step_d["node_detail"] is None
        assert "row_lineage_type" in step_d
        assert step_d["row_lineage_type"] is None


# ===========================================================================
# CATEGORY 8: Concurrent/Cache Edge Cases (5 tests)
# ===========================================================================


class TestConcurrentCacheEdgeCases:
    """Verify caching behaviour across repeated trace calls."""

    def test_trace_same_graph_different_row_index(self, tmp_path):
        """Trace same graph twice with different row_index: verify different results."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        r0 = execute_trace(graph, row_index=0)
        r1 = execute_trace(graph, row_index=1)
        r2 = execute_trace(graph, row_index=2)

        assert r0.output_value["x"] == 10
        assert r1.output_value["x"] == 20
        assert r2.output_value["x"] == 30
        assert r0.output_value["y"] == 20
        assert r1.output_value["y"] == 40
        assert r2.output_value["y"] == 60

    def test_trace_same_graph_different_column(self, tmp_path):
        """Trace same graph with different column: verify different expressions."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10], "z": [5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns(a=pl.col('x') * 2, b=pl.col('z') + 100)",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        ra = execute_trace(graph, row_index=0, column="a")
        rb = execute_trace(graph, row_index=0, column="b")

        assert ra.output_value == 20
        assert rb.output_value == 105

    def test_trace_after_graph_change_cache_invalidated(self, tmp_path):
        """Trace after graph change: verify cache invalidation."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10]}).write_parquet(p)

        graph1 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r1 = execute_trace(graph1, row_index=0)
        assert r1.output_value["y"] == 20

        graph2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 10)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r2 = execute_trace(graph2, row_index=0)
        assert r2.output_value["y"] == 100

    def test_preview_then_trace_reuses_cache(self, tmp_path):
        """Preview then trace: verify trace reuses preview cache (same DataFrames)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # Run preview first (populates _preview_cache)
        execute_graph(graph)

        # Now trace — should reuse preview outputs
        result = execute_trace(graph, row_index=0)
        assert result.output_value["x"] == 10
        assert result.output_value["y"] == 11

    def test_trace_with_row_values_mismatch_raises(self, tmp_path):
        """Trace with row_values that don't match: verify ValueError raised."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # First, populate the cache so row_values verification can proceed
        execute_trace(graph, row_index=0)

        # Now trace with wrong row_values — should raise ValueError
        with pytest.raises(ValueError, match="does not match"):
            execute_trace(
                graph,
                row_index=0,
                row_values={"x": 999, "y": 888},
            )
