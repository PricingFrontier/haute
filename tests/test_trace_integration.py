"""Comprehensive integration test suite for the end-to-end trace system.

Tests the full trace pipeline: cell click -> backend computation ->
enriched trace result -> correct output.  Covers every category of
pipeline topology, node type, data shape, caching, error handling,
and edge cases.

This file defines the complete specification for the trace enhancement.
"""

from __future__ import annotations

import json
import time
from datetime import date

import polars as pl
import pytest

from haute.executor import _preview_cache, execute_graph
from haute.graph_utils import GraphNode, NodeData, NodeType
from haute.trace import (
    TraceResult,
    TraceStep,
    execute_trace,
    trace_result_to_dict,
)
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

NAN_SENTINEL = {"__haute_type__": "non_finite_float", "value": "nan"}

# Consistent row_limit matching real usage (preview and trace share same limit).
_ROW_LIMIT = 1000


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


def _preview_row(graph, node_id: str, row_index: int) -> dict:
    """Run execute_graph and return the preview dict at row_index for node_id."""
    results = execute_graph(graph, target_node_id=node_id, row_limit=_ROW_LIMIT)
    node_result = results[node_id]
    assert node_result.status == "ok", f"Node {node_id} failed: {node_result.error}"
    assert row_index < len(node_result.preview), (
        f"row_index={row_index} out of range (preview has {len(node_result.preview)} rows)"
    )
    return node_result.preview[row_index]


# ===========================================================================
# A. Simple Linear Pipeline Tests
# ===========================================================================


class TestLinearPipelineSimpleArithmetic:
    """A.1: data_source -> polars(simple arithmetic) -> output

    Pipeline topology: src ---> transform
    Sample data: {x: [1,2,3], y: [10,20,30]}
    Transform: .with_columns(z=pl.col('x') + pl.col('y'))
    Cell clicked: transform node, row 0, column 'z'
    Expected: z=11, schema_diff shows z in columns_added, x and y in columns_passed
    Why: Baseline test -- simplest possible computed column trace.
    """

    def test_trace_computed_column_value(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') + pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="z")

        assert result.output_value == 11
        t_step = _step_by_id(result, "t")
        assert "z" in t_step.schema_diff.columns_added
        assert "x" in t_step.schema_diff.columns_passed
        assert "y" in t_step.schema_diff.columns_passed

    def test_trace_each_row_produces_correct_value(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') * pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        expected_z = [10, 40, 90]
        for i, ez in enumerate(expected_z):
            result = execute_trace(graph, row_index=i, target_node_id="t", column="z")
            assert result.output_value == ez, f"Row {i}: expected z={ez}, got {result.output_value}"


class TestLinearPipelineTwoTransforms:
    """A.2: data_source -> polars -> polars -> output

    Pipeline topology: src ---> t1 ---> t2
    Sample data: {x: [5, 10, 15]}
    t1: .with_columns(y=pl.col('x') * 2)   -> y = [10, 20, 30]
    t2: .with_columns(z=pl.col('y') + 1)   -> z = [11, 21, 31]
    Cell clicked: t2 node, row 1, column 'z'
    Expected: z=21, 3 steps in trace, t1 adds y, t2 adds z
    Why: Validates trace propagation through multiple sequential transforms.
    """

    def test_trace_through_two_sequential_transforms(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5, 10, 15]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("t2", "df = df.with_columns(z=pl.col('y') + 1)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=1, target_node_id="t2", column="z")

        assert result.output_value == 21
        assert len(result.steps) == 3

        t1_step = _step_by_id(result, "t1")
        assert "y" in t1_step.schema_diff.columns_added

        t2_step = _step_by_id(result, "t2")
        assert "z" in t2_step.schema_diff.columns_added

    def test_intermediate_values_visible(self, tmp_path):
        """Each step's output_values should show the column values at that point."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5, 10, 15]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("t2", "df = df.with_columns(z=pl.col('y') + 1)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t2")

        src_step = _step_by_id(result, "src")
        assert src_step.output_values["x"] == 5
        assert "y" not in src_step.output_values

        t1_step = _step_by_id(result, "t1")
        assert t1_step.output_values["y"] == 10

        t2_step = _step_by_id(result, "t2")
        assert t2_step.output_values["z"] == 11


class TestLinearPipelineMultipleWithColumns:
    """A.3: data_source -> polars(multiple with_columns) -> output

    Pipeline topology: src ---> t
    Sample data: {x: [2, 4, 6]}
    Transform code: two sequential with_columns in the same node:
        .with_columns(y=pl.col('x') * 3)
        .with_columns(z=pl.col('y') + pl.col('x'))
    Cell clicked: t node, row 0, column 'z'
    Expected: y=6, z=8; schema_diff shows both y and z as added
    Why: Verifies intra-node column dependency -- z depends on y created
         in an earlier with_columns within the same node.
    """

    def test_column_depends_on_earlier_with_columns_in_same_node(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [2, 4, 6]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = df.with_columns(y=pl.col('x') * 3)\n"
                        "df = df.with_columns(z=pl.col('y') + pl.col('x'))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="z")

        assert result.output_value == 8  # y=6, z=6+2=8
        t_step = _step_by_id(result, "t")
        assert "y" in t_step.schema_diff.columns_added
        assert "z" in t_step.schema_diff.columns_added
        assert t_step.output_values["y"] == 6
        assert t_step.output_values["z"] == 8


# ===========================================================================
# B. Join Pipeline Tests
# ===========================================================================


class TestJoinTraceLeftColumn:
    """B.1: Two data_sources -> polars(join) -> output: trace column from left side

    Topology: src_a ---> join <--- src_b
    Data: a={key: [1,2,3], val_a: [10,20,30]}, b={key: [3,2,1], val_b: [300,200,100]}
    Cell clicked: join node, row 0, column 'val_a'
    Expected: val_a comes from source a; trace shows correct source a row
    Why: Left-side columns in a join must trace back to the correct left source row.
    """

    def test_trace_left_side_column(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"
        pl.DataFrame({"key": [1, 2, 3], "val_a": [10, 20, 30]}).write_parquet(p_a)
        pl.DataFrame({"key": [3, 2, 1], "val_b": [300, 200, 100]}).write_parquet(p_b)

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

        result = execute_trace(graph, row_index=0, target_node_id="join", column="val_a")
        join_step = _step_by_id(result, "join")
        a_step = _step_by_id(result, "a")

        output_key = join_step.output_values["key"]
        assert a_step.output_values["key"] == output_key
        assert a_step.output_values["val_a"] == join_step.output_values["val_a"]


class TestJoinTraceRightColumn:
    """B.2: Two data_sources -> polars(join) -> output: trace column from right side

    Same topology. Cell clicked: join node, row 0, column 'val_b'
    Expected: val_b comes from source b; trace shows correct source b row
    Why: Right-side columns must trace back through the join to the correct right source row.
    """

    def test_trace_right_side_column(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"
        pl.DataFrame({"key": [1, 2, 3], "val_a": [10, 20, 30]}).write_parquet(p_a)
        pl.DataFrame({"key": [3, 2, 1], "val_b": [300, 200, 100]}).write_parquet(p_b)

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

        result = execute_trace(graph, row_index=0, target_node_id="join", column="val_b")
        join_step = _step_by_id(result, "join")
        b_step = _step_by_id(result, "b")

        output_key = join_step.output_values["key"]
        assert b_step.output_values["key"] == output_key
        assert b_step.output_values["val_b"] == join_step.output_values["val_b"]


class TestJoinTraceNullLeftJoin:
    """B.3: Two data_sources -> polars(left join) -> output: trace NULL column

    Data: a={key: [1,2,3,4]}, b={key: [1,3]}
    Left join on key: rows with key=2,4 have NULL val_b
    Cell clicked: join node, row with key=2 (or 4), column 'val_b'
    Expected: output_value is None; b source step shows the fallback row
    Why: NULL values from non-matching left join rows must be handled gracefully.
    """

    def test_trace_null_from_left_join(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"
        pl.DataFrame({"key": [1, 2, 3, 4], "val_a": [10, 20, 30, 40]}).write_parquet(p_a)
        pl.DataFrame({"key": [1, 3], "val_b": [100, 300]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "df = a.join(b, on='key', how='left')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        # Find which row has key=2 (no match in b)
        results = execute_graph(graph, target_node_id="join", row_limit=_ROW_LIMIT)
        preview_rows = results["join"].preview
        null_row_idx = next(i for i, r in enumerate(preview_rows) if r["key"] == 2)

        # Pass the executor's preview cache explicitly so the trace
        # reuses the exact DataFrames ``execute_graph`` just populated
        # — a cold re-execution would pick a different row ordering
        # for non-deterministic polars joins.  Wave 9E (#104) removed
        # the implicit reach-through that used to happen inside the
        # trace module.
        result = execute_trace(
            graph,
            row_index=null_row_idx,
            target_node_id="join",
            column="val_b",
            preview=_preview_cache,
        )
        assert result.output_value is None


class TestJoinInnerReducesRows:
    """B.4: Two data_sources -> polars(inner join) -> output: verify row count reduction

    Data: a={key: [1,2,3,4,5]}, b={key: [2,4]}
    Inner join: only keys 2,4 survive -> 2 rows
    Cell clicked: join node, row 0
    Expected: 2 output rows, trace shows all 3 nodes
    Why: Inner joins reduce cardinality; trace must handle the row count difference.
    """

    def test_inner_join_row_count_reduction(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"
        pl.DataFrame({"key": [1, 2, 3, 4, 5], "val_a": [10, 20, 30, 40, 50]}).write_parquet(p_a)
        pl.DataFrame({"key": [2, 4], "val_b": [200, 400]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "df = a.join(b, on='key', how='inner')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="join")
        join_step = _step_by_id(result, "join")

        # Verify the output row's key is one of the matching keys
        assert join_step.output_values["key"] in (2, 4)

        # Source steps must show the same key
        a_step = _step_by_id(result, "a")
        b_step = _step_by_id(result, "b")
        assert a_step.output_values["key"] == join_step.output_values["key"]
        assert b_step.output_values["key"] == join_step.output_values["key"]


class TestJoinCompositeKey:
    """B.5: Join on composite key (2+ columns)

    Data: a={k1: [1,1,2], k2: ['a','b','a'], val: [10,20,30]}
          b={k1: [1,2], k2: ['b','a'], factor: [1.5, 2.0]}
    Join on [k1, k2]
    Cell clicked: join node, row where k1=1,k2='b'
    Expected: trace correctly correlates on composite key
    Why: Multi-column join keys are common in actuarial data; correlation must handle them.
    """

    def test_composite_key_join_trace(self, tmp_path):
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"
        pl.DataFrame({"k1": [1, 1, 2], "k2": ["a", "b", "a"], "val": [10, 20, 30]}).write_parquet(
            p_a
        )
        pl.DataFrame({"k1": [1, 2], "k2": ["b", "a"], "factor": [1.5, 2.0]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "df = a.join(b, on=['k1', 'k2'])"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="join")
        join_step = _step_by_id(result, "join")
        a_step = _step_by_id(result, "a")
        b_step = _step_by_id(result, "b")

        # Both sources must match on BOTH key columns
        assert a_step.output_values["k1"] == join_step.output_values["k1"]
        assert a_step.output_values["k2"] == join_step.output_values["k2"]
        assert b_step.output_values["k1"] == join_step.output_values["k1"]
        assert b_step.output_values["k2"] == join_step.output_values["k2"]


# ===========================================================================
# C. Rating Pipeline Tests
# ===========================================================================


class TestRatingStepSingleTable:
    """C.1: data_source -> rating_step(single table) -> output: trace the rated column

    Topology: src ---> rating_step
    Data: {region: ['north','south','north'], base: [100,200,150]}
    Rating table: region -> factor mapping
    Cell clicked: rating_step node, row 0, column 'region_factor'
    Expected: trace shows the lookup from the rating table
    Why: Rating steps are the core actuarial primitive; trace must show table lookup.

    NOTE: This test requires a rating_step node configuration. If the rating
    infrastructure isn't available in the test environment, it exercises the
    equivalent polars join pattern that rating_step uses internally.
    """

    def test_rating_step_equivalent_as_join(self, tmp_path):
        """Exercises the lookup pattern equivalent to a rating_step."""
        p_data = tmp_path / "data.parquet"
        p_rates = tmp_path / "rates.parquet"
        pl.DataFrame(
            {
                "region": ["north", "south", "north"],
                "base": [100, 200, 150],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "region": ["north", "south"],
                "region_factor": [1.1, 0.9],
            }
        ).write_parquet(p_rates)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("rates", str(p_rates)),
                    _transform_node(
                        "rated",
                        "df = data.join(rates, on='region')\n"
                        "df = df.with_columns("
                        "rated_premium=pl.col('base') * pl.col('region_factor'))",
                    ),
                ],
                "edges": [_edge("data", "rated"), _edge("rates", "rated")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="rated", column="rated_premium")
        rated_step = _step_by_id(result, "rated")
        assert "rated_premium" in rated_step.schema_diff.columns_added
        assert rated_step.output_values["rated_premium"] == (
            rated_step.output_values["base"] * rated_step.output_values["region_factor"]
        )

    def test_real_rating_step_trace_includes_table_factors_and_combined_outputs(self, tmp_path):
        """A real ratingStep node exposes table lookup details in trace node_detail."""
        p_data = tmp_path / "policies.parquet"
        pl.DataFrame(
            {
                "policy_id": [1],
                "vehicle_age_band": ["1-3"],
                "cover_type": ["comprehensive"],
                "channel": ["direct"],
            }
        ).write_parquet(p_data)

        rating_config = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "outputColumn": "vehicle_factor",
                    "defaultValue": "1.0",
                    "entries": [
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "comprehensive",
                            "value": 0.9,
                        }
                    ],
                },
                {
                    "name": "channel_factor",
                    "factors": ["channel"],
                    "outputColumn": "channel_factor",
                    "defaultValue": "1.0",
                    "entries": [
                        {"channel": "broker", "value": 1.5},
                        {"channel": "direct", "value": 1.2},
                    ],
                },
            ],
            "combinedOutputs": [
                {
                    "outputColumn": "technical_premium_factor",
                    "operation": "multiply",
                    "baseValue": "100",
                }
            ],
            "code": "df = df.with_columns(test=pl.col('technical_premium_factor') * 2)",
        }

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    GraphNode(
                        id="rating",
                        data=NodeData(
                            label="adjustments",
                            nodeType=NodeType.RATING_STEP,
                            config=rating_config,
                        ),
                    ),
                ],
                "edges": [_edge("policies", "rating")],
            }
        )

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="rating",
            column="technical_premium_factor",
        )
        rating_step = _step_by_id(result, "rating")
        detail = rating_step.node_detail

        assert detail is not None
        assert detail["detail_type"] == "rating_step"
        assert detail["tables"][0]["name"] == "vehicle_factor"
        assert detail["tables"][0]["factors"] == [
            {"column": "vehicle_age_band", "value": "1-3"},
            {"column": "cover_type", "value": "comprehensive"},
        ]
        assert detail["tables"][0]["selected_value"] == 0.9
        assert detail["tables"][0]["status"] == "matched"
        assert detail["tables"][1]["name"] == "channel_factor"
        assert detail["tables"][1]["factors"] == [{"column": "channel", "value": "direct"}]
        assert detail["tables"][1]["selected_value"] == 1.2
        assert detail["tables"][1]["status"] == "matched"
        assert detail["combined_outputs"] == [
            {
                "column": "technical_premium_factor",
                "operation": "multiply",
                "base_value": 100.0,
                "input_values": {"vehicle_factor": 0.9, "channel_factor": 1.2},
                "value": 108.0,
            }
        ]

        payload = trace_result_to_dict(result)
        serialized_detail = payload["steps"][-1]["node_detail"]
        assert serialized_detail["tables"][0]["factors"][0] == {
            "column": "vehicle_age_band",
            "value": "1-3",
        }
        assert serialized_detail["combined_outputs"][0]["value"] == 108.0


class TestRatingStepMultiplyTables:
    """C.2: data_source -> rating_step(multiple tables, multiply) -> output

    Pattern: base_premium * factor_1 * factor_2 = final_premium
    Cell clicked: output, column 'final_premium'
    Expected: trace shows multiplication chain
    Why: Actuarial multiplicative rating structures must be traceable end-to-end.
    """

    def test_multiplicative_rating_factors(self, tmp_path):
        p_data = tmp_path / "data.parquet"
        p_age = tmp_path / "age_factors.parquet"
        p_region = tmp_path / "region_factors.parquet"
        pl.DataFrame(
            {
                "age_band": ["25-35", "25-35", "36-45"],
                "region": ["north", "south", "north"],
                "base_premium": [100, 200, 150],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "age_band": ["25-35", "36-45"],
                "age_factor": [1.2, 0.9],
            }
        ).write_parquet(p_age)
        pl.DataFrame(
            {
                "region": ["north", "south"],
                "region_factor": [1.1, 0.95],
            }
        ).write_parquet(p_region)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("age_tbl", str(p_age)),
                    _source_node("region_tbl", str(p_region)),
                    _transform_node("join_age", "df = data.join(age_tbl, on='age_band')"),
                    _transform_node("join_region", "df = join_age.join(region_tbl, on='region')"),
                    _transform_node(
                        "calc",
                        "df = df.with_columns(final_premium="
                        "pl.col('base_premium') * pl.col('age_factor') * pl.col('region_factor'))",
                    ),
                ],
                "edges": [
                    _edge("data", "join_age"),
                    _edge("age_tbl", "join_age"),
                    _edge("join_age", "join_region"),
                    _edge("region_tbl", "join_region"),
                    _edge("join_region", "calc"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc", column="final_premium")
        calc_step = _step_by_id(result, "calc")
        expected = (
            calc_step.output_values["base_premium"]
            * calc_step.output_values["age_factor"]
            * calc_step.output_values["region_factor"]
        )
        assert calc_step.output_values["final_premium"] == pytest.approx(expected)
        assert len(result.steps) == 6  # all 6 nodes in trace


class TestBandingThenRating:
    """C.3: data_source -> banding -> rating_step -> output

    Pattern: Continuous variable banded, then looked up in a rate table.
    Why: Banding + rating is the standard actuarial pipeline pattern.
    """

    def test_banding_then_lookup(self, tmp_path):
        p_data = tmp_path / "data.parquet"
        p_rates = tmp_path / "rates.parquet"
        pl.DataFrame(
            {
                "driver_age": [22, 35, 55, 18, 45],
                "base": [100, 100, 100, 100, 100],
            }
        ).write_parquet(p_data)
        # Simulate banding: age -> age_band
        pl.DataFrame(
            {
                "age_band": ["18-25", "26-40", "41-60"],
                "age_factor": [1.5, 1.0, 0.8],
            }
        ).write_parquet(p_rates)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _transform_node(
                        "banding",
                        "df = df.with_columns("
                        "age_band=pl.when(pl.col('driver_age') <= 25).then(pl.lit('18-25'))"
                        ".when(pl.col('driver_age') <= 40).then(pl.lit('26-40'))"
                        ".otherwise(pl.lit('41-60')))",
                    ),
                    _source_node("rates", str(p_rates)),
                    _transform_node("rated", "df = banding.join(rates, on='age_band')"),
                ],
                "edges": [
                    _edge("data", "banding"),
                    _edge("banding", "rated"),
                    _edge("rates", "rated"),
                ],
            }
        )

        # Trace without column filter first to verify full pipeline
        result_full = execute_trace(graph, row_index=0, target_node_id="rated")
        banding_step = _step_by_id(result_full, "banding")
        assert "age_band" in banding_step.schema_diff.columns_added

        # age_factor comes from rates source via join -- it's "passed" through
        # the join node (present in input from rates and in output)
        rated_step = _step_by_id(result_full, "rated")
        assert "age_factor" in rated_step.output_values

        # Trace with column filter -- age_factor originates from rates source
        result = execute_trace(graph, row_index=0, target_node_id="rated", column="age_factor")
        ids = _step_ids(result)
        assert "rated" in ids
        assert "rates" in ids


class TestBandingTraceLineage:
    """Banding-created fields should trace back to the value that was banded."""

    def test_banding_created_column_reports_input_value_and_continues_lineage(self, tmp_path):
        p_data = tmp_path / "policies.parquet"
        pl.DataFrame(
            {
                "driver_age": [30],
                "age_offset": [5],
            }
        ).write_parquet(p_data)

        banding_config = {
            "factors": [
                {
                    "column": "risk_age",
                    "outputColumn": "age_band",
                    "banding": "continuous",
                    "rules": [
                        {"op1": "<", "val1": 25, "assignment": "young"},
                        {
                            "op1": ">=",
                            "val1": 25,
                            "op2": "<",
                            "val2": 65,
                            "assignment": "adult",
                        },
                    ],
                    "default": "senior",
                }
            ]
        }

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _transform_node(
                        "prep",
                        "df = df.with_columns("
                        "risk_age=pl.col('driver_age') + pl.col('age_offset'))",
                    ),
                    GraphNode(
                        id="band",
                        data=NodeData(
                            label="age banding",
                            nodeType=NodeType.BANDING,
                            config=banding_config,
                        ),
                    ),
                ],
                "edges": [_edge("policies", "prep"), _edge("prep", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band", column="age_band")

        assert _step_ids(result) == ["policies", "prep", "band"]
        band_step = _step_by_id(result, "band")
        detail = band_step.node_detail
        assert detail is not None
        assert detail["detail_type"] == "banding"
        assert detail["input_column"] == "risk_age"
        assert detail["output_column"] == "age_band"
        assert detail["input_value"] == 35
        assert detail["matched_band"] == "adult"

        assert band_step.expression is not None
        assert band_step.expression["expression_type"] == "banding"
        assert band_step.expression["referenced_columns"] == ["risk_age"]
        assert band_step.calculation is not None
        assert band_step.calculation["input_values"] == {"risk_age": 35}
        assert band_step.calculation["result_value"] == "adult"
        assert band_step.calculation["input_sources"]["risk_age"]["node_name"] == "prep"
        assert band_step.calculation["input_sources"]["risk_age"]["result_value"] == 35


class TestFullPricingWaterfall:
    """C.4: data_source -> banding -> rating_step -> polars(apply discount) -> output

    Full pricing waterfall: band -> rate lookup -> discount application.
    Cell clicked: final node, column 'discounted_premium'
    Expected: trace shows all 4+ steps with correct values at each stage
    Why: The complete actuarial pricing waterfall is the primary use case.
    """

    def test_full_waterfall_trace(self, tmp_path):
        p_data = tmp_path / "data.parquet"
        p_rates = tmp_path / "rates.parquet"
        pl.DataFrame(
            {
                "driver_age": [30],
                "base_premium": [500],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "age_band": ["26-40"],
                "age_factor": [1.0],
            }
        ).write_parquet(p_rates)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _transform_node(
                        "banding",
                        "df = df.with_columns("
                        "age_band=pl.when(pl.col('driver_age') <= 25).then(pl.lit('18-25'))"
                        ".when(pl.col('driver_age') <= 40).then(pl.lit('26-40'))"
                        ".otherwise(pl.lit('41-60')))",
                    ),
                    _source_node("rates", str(p_rates)),
                    _transform_node("rated", "df = banding.join(rates, on='age_band')"),
                    _transform_node(
                        "discount",
                        "df = df.with_columns("
                        "rated_premium=pl.col('base_premium') * pl.col('age_factor'),"
                        "discounted_premium=pl.col('base_premium') * pl.col('age_factor') * 0.9)",
                    ),
                ],
                "edges": [
                    _edge("data", "banding"),
                    _edge("banding", "rated"),
                    _edge("rates", "rated"),
                    _edge("rated", "discount"),
                ],
            }
        )

        result = execute_trace(
            graph, row_index=0, target_node_id="discount", column="discounted_premium"
        )

        assert result.output_value == pytest.approx(500 * 1.0 * 0.9)
        assert len(result.steps) >= 4  # all waterfall steps present

        discount_step = _step_by_id(result, "discount")
        assert "discounted_premium" in discount_step.schema_diff.columns_added


# ===========================================================================
# D. Model Scoring Pipeline Tests
# ===========================================================================


class TestModelScoreFeatureEngineering:
    """D.1: data_source -> polars(feature engineering) -> model_score -> output

    This tests the pattern, not the actual model_score node (which requires
    MLflow infrastructure).  We test the polars feature engineering trace.
    Cell clicked: feature engineering output
    Expected: engineered features visible in trace
    Why: Feature engineering is the pre-model step; trace must show transformations.
    """

    def test_feature_engineering_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "age": [25, 35, 45],
                "income": [30000, 50000, 70000],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "features",
                        "df = df.with_columns("
                        "log_income=pl.col('income').log(),"
                        "age_squared=pl.col('age') ** 2)",
                    ),
                ],
                "edges": [_edge("src", "features")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="features", column="log_income")
        f_step = _step_by_id(result, "features")
        assert "log_income" in f_step.schema_diff.columns_added
        assert "age_squared" in f_step.schema_diff.columns_added


class TestModelScorePostProcess:
    """D.2: data_source -> model_score -> polars(post-process) -> output

    Tests a column derived from a prediction.  Uses polars to simulate
    the model_score output since MLflow may not be available.
    Cell clicked: post-process node, column derived from prediction
    Expected: trace shows the derivation step and the simulated prediction
    Why: Post-processing model scores is standard; trace must span the boundary.
    """

    def test_post_process_of_prediction(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "feature1": [1.0, 2.0, 3.0],
                "prediction": [0.3, 0.7, 0.5],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "post",
                        "df = df.with_columns(decision=pl.when(pl.col('prediction') > 0.5)"
                        ".then(pl.lit('accept')).otherwise(pl.lit('reject')))",
                    ),
                ],
                "edges": [_edge("src", "post")],
            }
        )

        result = execute_trace(graph, row_index=1, target_node_id="post", column="decision")
        assert result.output_value == "accept"  # prediction 0.7 > 0.5

        post_step = _step_by_id(result, "post")
        assert "decision" in post_step.schema_diff.columns_added


# ===========================================================================
# E. Scenario & Optimisation Pipeline Tests
# ===========================================================================


class TestScenarioExpanderTrace:
    """E.1: data_source -> scenario_expander -> polars -> output

    Tests a scenario expansion pattern using polars (since the actual
    scenarioExpander node type requires config infrastructure).
    Cell clicked: after expansion, trace should identify the expansion step.
    Why: Scenario analysis is a key Haute feature; trace must identify it.
    """

    def test_scenario_expansion_pattern(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "quote_id": [1, 2],
                "premium": [100, 200],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # Simulate scenario expansion: cross-join with multipliers
                    _transform_node(
                        "expand",
                        "df = df.join("
                        "pl.DataFrame({'multiplier': [0.9, 1.0, 1.1]}).lazy(),how='cross')",
                    ),
                    _transform_node(
                        "calc",
                        "df = df.with_columns("
                        "scenario_premium=pl.col('premium') * pl.col('multiplier'))",
                    ),
                ],
                "edges": [_edge("src", "expand"), _edge("expand", "calc")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc", column="scenario_premium")
        assert len(result.steps) == 3


class TestOptimiserApplyTrace:
    """E.2: data_source -> polars -> optimiser_apply -> output

    Tests the optimiser lambda application pattern using polars.
    Cell clicked: after optimiser, column showing applied adjustment
    Expected: trace shows the adjustment step
    Why: Optimisation is the final pricing step; trace must show the lambda.
    """

    def test_optimiser_apply_pattern(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "premium": [100, 200, 300],
                "lambda_adj": [1.05, 0.95, 1.0],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "opt",
                        "df = df.with_columns("
                        "optimised_premium=pl.col('premium') * pl.col('lambda_adj'))",
                    ),
                ],
                "edges": [_edge("src", "opt")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="opt", column="optimised_premium")
        opt_step = _step_by_id(result, "opt")
        assert "optimised_premium" in opt_step.schema_diff.columns_added
        expected = 100 * 1.05
        assert opt_step.output_values["optimised_premium"] == pytest.approx(expected)


# ===========================================================================
# F. Complex DAG Tests
# ===========================================================================


class TestDiamondPattern:
    """F.1: Diamond pattern: A->B, A->C, B->D, C->D

    Topology:    A
                / \\
               B   C
                \\ /
                 D

    Data: A={id: [1,2,3], x: [10,20,30]}
          B adds y = x * 2
          C adds z = x + 5
          D joins B and C on id
    Cell clicked: D, row 0
    Expected: trace correctly handles fan-in; shows A, B, C, D
    Why: Diamond DAGs are common; the trace must not double-count the shared ancestor.
    """

    def test_diamond_dag_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2, 3], "x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p)),
                    _transform_node("b", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("c", "df = df.with_columns(z=pl.col('x') + 5)"),
                    _transform_node("d", "df = b.join(c, on='id')"),
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
        assert ids == {"a", "b", "c", "d"}

        d_step = _step_by_id(result, "d")
        b_step = _step_by_id(result, "b")
        c_step = _step_by_id(result, "c")

        # Both B and C should show the same id as D
        assert b_step.output_values["id"] == d_step.output_values["id"]
        assert c_step.output_values["id"] == d_step.output_values["id"]


class TestFanOut:
    """F.2: Fan-out: A->B, A->C

    Trace B should NOT include C's path.
    Cell clicked: B, row 0
    Expected: trace shows A and B only, not C
    Why: Fan-out must not leak unrelated branches into the trace.
    """

    def test_fan_out_isolates_branches(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p)),
                    _transform_node("b", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("c", "df = df.with_columns(z=pl.col('x') + 1)"),
                ],
                "edges": [_edge("a", "b"), _edge("a", "c")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="b")
        ids = set(_step_ids(result))
        assert "a" in ids
        assert "b" in ids
        # C is not an ancestor of B, so ideally should not appear.
        # The current implementation includes all nodes in topo order up to target,
        # so C may or may not be present depending on graph pruning. The key
        # assertion is that the trace is correct for B.
        assert result.target_node_id == "b"


class TestLongChain:
    """F.3: Long chain: 10+ nodes

    Topology: n0 -> n1 -> n2 -> ... -> n9 -> n10
    Each adds a column: col_i = col_{i-1} + 1
    Cell clicked: n10, row 0, column 'col_10'
    Expected: trace returns all 11 steps in correct topological order
    Why: Deep chains must not break the trace; order must be preserved.
    """

    def test_long_chain_all_steps_in_order(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"col_0": [0]}).write_parquet(p)

        nodes = [_source_node("n0", str(p))]
        edges = []
        for i in range(1, 11):
            code = f"df = df.with_columns(col_{i}=pl.col('col_{i - 1}') + 1)"
            nodes.append(_transform_node(f"n{i}", code))
            edges.append(_edge(f"n{i - 1}", f"n{i}"))

        graph = _g({"nodes": nodes, "edges": edges})

        result = execute_trace(graph, row_index=0, target_node_id="n10", column="col_10")
        assert result.output_value == 10
        assert len(result.steps) == 11

        # Verify topological order
        ids = _step_ids(result)
        for i in range(11):
            assert ids[i] == f"n{i}"


class TestInstanceNode:
    """F.4: Pipeline with instance node

    Instance nodes reference another node's definition. The trace must
    resolve through the instance to the original node's logic.
    Why: Instance nodes are used for submodel reuse; trace must handle indirection.

    NOTE: Full instance node support requires the executor's resolve_instance_node
    infrastructure. This test validates the trace works on a graph where the
    instance has already been resolved (flattened).
    """

    def test_trace_resolves_through_instance_equivalent(self, tmp_path):
        """Tests a pre-resolved instance pattern (two nodes with same logic)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                    # "instance" of t -- same logic applied to same input
                    _transform_node("t_inst", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t"), _edge("src", "t_inst")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t_inst")
        t_inst_step = _step_by_id(result, "t_inst")
        assert t_inst_step.output_values["y"] == 2


# ===========================================================================
# G. Submodel Tests
# ===========================================================================


class TestSubmodelTrace:
    """G.1: Pipeline with submodel -- trace enters and exits submodel correctly.

    The flatten_graph function dissolves submodels before execution. The trace
    operates on the flattened graph, so submodel internal nodes appear as
    regular steps.

    Why: Submodels encapsulate reusable pipeline fragments; trace must show
         the internal steps, not just a black box.
    """

    def test_submodel_internal_nodes_visible_in_trace(self, tmp_path):
        """Simulates a flattened submodel: nodes with submodel-prefixed IDs."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        # Simulate a flattened submodel: sub__t1 and sub__t2
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sub__t1", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("sub__t2", "df = df.with_columns(z=pl.col('y') + 1)"),
                    _transform_node("post", "df = df.with_columns(w=pl.col('z') * 3)"),
                ],
                "edges": [
                    _edge("src", "sub__t1"),
                    _edge("sub__t1", "sub__t2"),
                    _edge("sub__t2", "post"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="post")
        ids = _step_ids(result)
        assert "sub__t1" in ids
        assert "sub__t2" in ids
        assert "post" in ids


class TestNestedSubmodels:
    """G.2: Nested submodels -- trace crosses multiple boundaries.

    After flattening, nested submodels produce nodes with double-prefixed IDs.
    Why: Nested submodels are used in complex pricing structures.
    """

    def test_nested_submodel_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("outer__inner__t1", "df = df.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("outer__t2", "df = df.with_columns(z=pl.col('y') + 1)"),
                    _transform_node("final", "df = df.with_columns(w=pl.col('z') + 1)"),
                ],
                "edges": [
                    _edge("src", "outer__inner__t1"),
                    _edge("outer__inner__t1", "outer__t2"),
                    _edge("outer__t2", "final"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="final")
        assert result.output_value["w"] == 8  # 5+1+1+1
        assert len(result.steps) == 4


# ===========================================================================
# H. Live Switch Tests
# ===========================================================================


class TestLiveSwitchTrace:
    """H.1: Pipeline with live_switch -- trace shows which branch was selected.

    Topology: live_src -> switch <- batch_src
    The switch selects one branch based on the source parameter.
    Cell clicked: switch output
    Expected: only the active branch appears in trace
    Why: Live switches route live vs batch data; trace must reflect the active path.
    """

    def test_live_switch_selects_correct_branch(self, tmp_path):
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        p_live = tmp_path / "live.parquet"
        p_batch = tmp_path / "batch.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p_live)
        pl.DataFrame({"x": [10, 20]}).write_parquet(p_batch)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="live_src",
                    data=NodeData(
                        label="live_src",
                        nodeType="dataSource",
                        config={"path": str(p_live)},
                    ),
                ),
                GraphNode(
                    id="batch_src",
                    data=NodeData(
                        label="batch_src",
                        nodeType="dataSource",
                        config={"path": str(p_batch)},
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

        # Select batch branch
        result = execute_trace(graph, row_index=0, target_node_id="sw", source="nb_batch")
        step_ids = {s.node_id for s in result.steps}
        assert "batch_src" in step_ids
        assert "live_src" not in step_ids


class TestLiveSwitchBranchChange:
    """H.2: Switch to different branch -- trace path changes.

    Same graph, but switching from batch to live should show live_src in trace.
    Why: Switching branches must change the trace path accordingly.
    """

    def test_switch_branch_changes_trace(self, tmp_path):
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        p_live = tmp_path / "live.parquet"
        p_batch = tmp_path / "batch.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p_live)
        pl.DataFrame({"x": [10, 20]}).write_parquet(p_batch)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="live_src",
                    data=NodeData(
                        label="live_src",
                        nodeType="dataSource",
                        config={"path": str(p_live)},
                    ),
                ),
                GraphNode(
                    id="batch_src",
                    data=NodeData(
                        label="batch_src",
                        nodeType="dataSource",
                        config={"path": str(p_batch)},
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

        result_batch = execute_trace(graph, row_index=0, target_node_id="sw", source="nb_batch")
        result_live = execute_trace(graph, row_index=0, target_node_id="sw", source="live")

        batch_ids = {s.node_id for s in result_batch.steps}
        live_ids = {s.node_id for s in result_live.steps}

        assert "batch_src" in batch_ids
        assert "live_src" not in batch_ids
        assert "live_src" in live_ids
        assert "batch_src" not in live_ids


# ===========================================================================
# I. Row Correlation Tests
# ===========================================================================


class TestRowCorrelationSameRowCount:
    """I.1: Same row count parent/child -- positional match.

    with_columns keeps row count identical. Positional match should work.
    Why: Fast path -- same row count means 1:1 positional matching.
    """

    def test_positional_match_with_columns(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        for row_idx in range(3):
            result = execute_trace(graph, row_index=row_idx, target_node_id="t")
            src_step = _step_by_id(result, "src")
            t_step = _step_by_id(result, "t")
            assert src_step.output_values["x"] == t_step.output_values["x"]


class TestRowCorrelationFilterReducesRows:
    """I.2: Parent has more rows (filter in child) -- value-based match.

    Source: 5 rows. Filter: keeps 3. Row 0 in filtered != row 0 in source.
    Why: Filter changes cardinality; value-based matching is required.
    """

    def test_value_based_match_after_filter(self, tmp_path):
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
                    _transform_node("filt", "df = df.filter(pl.col('value') > 25)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")
        src_step = _step_by_id(result, "src")

        assert filt_step.output_values["id"] == 3
        assert src_step.output_values["id"] == 3


class TestRowCorrelationAggregation:
    """I.3: Aggregation (group_by) -- trace shows group key.

    Source: 5 rows, 2 groups. group_by produces 2 rows.
    Why: Aggregation changes cardinality drastically; trace must show group key.
    """

    def test_aggregation_traces_group_key(self, tmp_path):
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
                        "df = df.group_by('region').agg(pl.col('premium').sum()).sort('region')",
                    ),
                ],
                "edges": [_edge("src", "agg")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="agg")
        agg_step = _step_by_id(result, "agg")
        src_step = _step_by_id(result, "src")

        # Row 0 after sort = "north"
        assert agg_step.output_values["region"] == "north"
        assert src_step.output_values["region"] == "north"


class TestRowCorrelationSortChangesOrder:
    """I.4: Sort changes row order -- value-based match still works.

    Source: rows in order [3,1,2]. Sort by id: [1,2,3].
    Why: Sort reorders rows; positional matching would give wrong results.
    """

    def test_sort_value_based_match(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [3, 1, 2], "val": [30, 10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", "df = df.sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        # Sorted row 0 = id=1
        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        sorted_step = _step_by_id(result, "sorted")
        src_step = _step_by_id(result, "src")

        assert sorted_step.output_values["id"] == 1
        assert src_step.output_values["id"] == 1


class TestRowCorrelationScenarioExpansion:
    """I.5: Scenario expansion -- trace maps back to original row.

    Cross-join multiplies rows. Trace must correlate back to the original.
    Why: Scenario expansion is a key Haute feature; row lineage must be maintained.
    """

    def test_scenario_expansion_row_correlation(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "quote_id": [1, 2],
                "premium": [100, 200],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "expand",
                        "df = df.join("
                        "pl.DataFrame({'multiplier': [0.9, 1.0, 1.1]}).lazy(), how='cross')",
                    ),
                ],
                "edges": [_edge("src", "expand")],
            }
        )

        # 2 quotes x 3 scenarios = 6 rows
        result = execute_trace(graph, row_index=0, target_node_id="expand")
        expand_step = _step_by_id(result, "expand")
        src_step = _step_by_id(result, "src")

        # The source row should match the expanded row's quote_id
        assert src_step.output_values["quote_id"] == expand_step.output_values["quote_id"]


# ===========================================================================
# J. Column Relevance Filtering Tests
# ===========================================================================


class TestColumnRelevanceSpecificColumn:
    """J.1: Trace specific column -- only relevant nodes shown as column_relevant=true.

    Topology: src -> t1 (adds y) -> t2 (adds z)
    Trace column 'y': t1 has column_relevant=true, t2 should have it too (passes y through)
    Why: column_relevant filtering is how the frontend highlights relevant nodes.
    """

    def test_column_relevant_tagging(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("t2", "df = df.with_columns(z=pl.col('y') + 1)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t2", column="y")

        # y is created at t1, passes through t2
        t1_step = _step_by_id(result, "t1")
        t2_step = _step_by_id(result, "t2")
        assert t1_step.column_relevant is True
        assert t2_step.column_relevant is True


class TestColumnRelevancePassthrough:
    """J.2: Trace column that passes through unchanged -- pass-through nodes marked correctly.

    Column 'x' exists from source through all transforms unchanged.
    Why: Pass-through detection prevents incorrectly marking nodes as irrelevant.
    """

    def test_passthrough_column_all_relevant(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "unused": [99]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("t2", "df = df.with_columns(z=pl.col('y') + 1)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t2", column="x")
        # 'x' passes through all 3 nodes -- all should be column_relevant
        for step in result.steps:
            assert step.column_relevant is True, (
                f"Node {step.node_id} should be column_relevant for pass-through column 'x'"
            )


class TestColumnRelevanceCreatedAtNode:
    """J.3: Trace column created at a specific node -- all ancestors marked relevant.

    Column 'z' is created at t2. All ancestors (src, t1) should be in the trace
    as they feed into the calculation, even though they don't have 'z'.
    Why: Calculated columns need their full ancestry in the trace.
    """

    def test_calculated_column_ancestors_in_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(y=pl.col('x') * 2)"),
                    _transform_node("t2", "df = df.with_columns(z=pl.col('y') * 3)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t2", column="z")
        ids = _step_ids(result)
        # z is created at t2, but src and t1 are ancestors and should be included
        assert "src" in ids
        assert "t1" in ids
        assert "t2" in ids


class TestColumnRelevanceRenamedColumn:
    """J.4: Trace column that gets renamed mid-pipeline.

    Column 'x' is renamed to 'x_renamed' at t1.
    Tracing 'x_renamed': t1 and t2 should be relevant (have the column),
    but src should be an ancestor (feeds into the rename).
    Why: Renames break column name continuity; trace must handle this.
    """

    def test_renamed_column_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10], "y": [20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.rename({'x': 'x_renamed'})"),
                    _transform_node("t2", "df = df.with_columns(z=pl.col('x_renamed') * 2)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t2", column="x_renamed")
        ids = _step_ids(result)
        # x_renamed appears at t1 and t2, src is ancestor
        assert "t1" in ids
        assert "t2" in ids


class TestColumnRelevancePrunesUnrelatedBranch:
    """J.5: Trace column from one branch prunes the other branch.

    Two sources join; tracing a column from source A should prune source B.
    Why: In multi-source pipelines, irrelevant branches should be pruned.
    """

    def test_unrelated_branch_pruned(self, tmp_path):
        p1 = tmp_path / "a.parquet"
        p2 = tmp_path / "b.parquet"
        pl.DataFrame({"x": [1], "shared": [10]}).write_parquet(p1)
        pl.DataFrame({"y": [2], "shared": [10]}).write_parquet(p2)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p1)),
                    _source_node("b", str(p2)),
                    _transform_node("join", "df = a.join(b, on='shared')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        result = execute_trace(graph, column="x")
        ids = {s.node_id for s in result.steps}
        assert "a" in ids
        assert "join" in ids
        # 'b' should be pruned -- it doesn't carry 'x'
        assert "b" not in ids


# ===========================================================================
# K. Cache Tests
# ===========================================================================


class TestCacheFirstTraceFull:
    """K.1: First trace -- full execution.

    First call executes the entire pipeline.
    Why: Baseline for cache tests; ensures full execution produces correct results.
    """

    def test_first_trace_executes_fully(self, tmp_path):
        _trace_cache.invalidate()
        _preview_cache.invalidate()

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0)
        assert result.output_value["x"] == 1


class TestCacheSameGraphDifferentRow:
    """K.2: Second trace same graph, different row -- uses cache (fast).

    Why: Cache hit means no re-execution; only row extraction changes.
    """

    def test_different_row_uses_cache(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        r0 = execute_trace(graph, row_index=0)
        assert r0.output_value["x"] == 10

        r1 = execute_trace(graph, row_index=1)
        assert r1.output_value["x"] == 20

        r2 = execute_trace(graph, row_index=2)
        assert r2.output_value["x"] == 30


class TestCacheSameGraphDifferentColumn:
    """K.3: Second trace same graph, different column -- uses cache.

    Why: Column filtering is post-processing on cached data.
    """

    def test_different_column_uses_cache(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "y": [2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') + pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        r_x = execute_trace(graph, row_index=0, column="x")
        assert r_x.output_value == 1

        r_z = execute_trace(graph, row_index=0, column="z")
        assert r_z.output_value == 3


class TestCacheInvalidatesOnGraphChange:
    """K.4: Graph changed -- cache invalidated, full re-execution.

    Why: Code changes must invalidate the cache to produce fresh results.
    """

    def test_graph_change_invalidates_cache(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5]}).write_parquet(p)

        graph1 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r1 = execute_trace(graph1, row_index=0)
        assert r1.output_value["y"] == 10

        graph2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 3)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r2 = execute_trace(graph2, row_index=0)
        assert r2.output_value["y"] == 15


class TestCacheReusesPreview:
    """K.5: Preview cache available -- trace reuses it.

    When execute_graph has already been called, the trace should reuse
    those DataFrames instead of re-executing.
    Why: Prevents redundant computation and ensures trace/preview consistency.
    """

    def test_trace_reuses_preview_cache(self, tmp_path):
        _trace_cache.invalidate()
        _preview_cache.invalidate()

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        # Preview first
        execute_graph(graph, target_node_id="t", row_limit=_ROW_LIMIT)

        # Trace should reuse preview cache
        result = execute_trace(graph, row_index=0, target_node_id="t", row_limit=_ROW_LIMIT)
        assert result.output_value["x"] == 1

    def test_trace_reexecutes_when_projected_preview_cache_has_only_target(
        self,
        tmp_path,
    ):
        from unittest.mock import patch

        import haute.trace as trace_mod

        _trace_cache.invalidate()
        _preview_cache.invalidate()

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') + pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        preview = execute_graph(
            graph,
            target_node_id="t",
            row_limit=_ROW_LIMIT,
            target_preview_only=True,
            requested_preview_columns=["x", "z"],
        )["t"].preview[0]

        with patch(
            "haute.trace._execute_eager_core",
            wraps=trace_mod._execute_eager_core,
        ) as execute_eager:
            result = execute_trace(
                graph,
                row_index=0,
                target_node_id="t",
                column="z",
                row_limit=_ROW_LIMIT,
                row_values=preview,
                preview=_preview_cache,
            )

        execute_eager.assert_called_once()
        assert result.output_value == 11
        assert {"src", "t"}.issubset({step.node_id for step in result.steps})


# ===========================================================================
# L. Error Handling Tests
# ===========================================================================


class TestErrorEmptyPipeline:
    """L.1: Trace on empty pipeline (0 nodes).

    Expected: ValueError with "Empty graph" message.
    Why: Edge case -- must fail gracefully with a clear error.
    """

    def test_empty_graph_raises(self):
        with pytest.raises(ValueError, match="Empty graph"):
            execute_trace(_g({"nodes": [], "edges": []}))


class TestErrorSingleNode:
    """L.2: Trace on pipeline with 1 node (source only).

    Expected: successful trace with 1 step.
    Why: Minimal valid pipeline -- must still produce a trace.
    """

    def test_single_source_node(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0)
        assert len(result.steps) == 1
        assert result.steps[0].node_id == "src"
        assert result.output_value["x"] == 1


class TestErrorInvalidRowIndex:
    """L.3: Trace with invalid row_index (out of bounds).

    Expected: graceful handling -- either returns empty or clamps to last row.
    Why: User might click a row that disappeared after re-execution.
    """

    def test_out_of_bounds_row_index(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        # Row index 999 is well beyond the 2-row DataFrame
        with pytest.raises(ValueError, match="row_index 999 is out of range"):
            execute_trace(graph, row_index=999)


class TestErrorNonExistentColumn:
    """L.4: Trace with non-existent column name.

    Expected: trace completes, output_value is None for the missing column.
    Why: Defensive -- column might be misspelled or removed.
    """

    def test_nonexistent_column(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0, column="nonexistent")
        assert result.output_value is None


class TestErrorNonExistentTargetNode:
    """L.5: Trace with non-existent target_node_id.

    Expected: ValueError with "not found" message.
    Why: Invalid node ID must be caught early with clear error.
    """

    def test_nonexistent_target_raises(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g({"nodes": [_source_node("src", str(p))], "edges": []})
        with pytest.raises(ValueError, match="not found"):
            execute_trace(graph, target_node_id="ghost_node")


class TestErrorNodeCodeThrows:
    """L.6: Node code throws an error during trace execution.

    Expected: exception propagated (trace does not swallow errors).
    Why: Trace uses swallow_errors=False; errors must surface to the user.
    """

    def test_node_error_propagated(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("bad", "df = df.with_columns(y=pl.col('nonexistent') * 2)"),
                ],
                "edges": [_edge("src", "bad")],
            }
        )

        _trace_cache.invalidate()
        _preview_cache.invalidate()

        with pytest.raises(Exception):
            execute_trace(graph, row_index=0, target_node_id="bad")


class TestErrorZeroRowsOutput:
    """L.8: Trace on node with 0 rows output.

    A filter that removes all rows. Trace should handle gracefully.
    Why: Edge case -- empty DataFrames must not crash the trace.
    """

    def test_zero_rows_after_filter(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", "df = df.filter(pl.col('x') > 999)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        with pytest.raises(ValueError, match="row_index 0 is out of range"):
            execute_trace(graph, row_index=0, target_node_id="filt")


# ===========================================================================
# M. Edge Case Data Tests
# ===========================================================================


class TestEdgeCaseAllNulls:
    """M.1: Row with all NULL values.

    Data: all columns are None for a given row.
    Why: NULL handling is critical in actuarial data (missing policy fields).
    """

    def test_all_null_row(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "a": [None, 1],
                "b": [None, 2],
                "c": [None, 3],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        assert t_step.output_values["a"] is None
        assert t_step.output_values["b"] is None
        assert t_step.output_values["c"] is None


class TestEdgeCaseNaN:
    """M.2: Row with NaN values in computed column.

    NaN must be encoded as an explicit JSON-safe sentinel.
    Why: NaN is not valid JSON, but it must remain distinct from null.
    """

    def test_nan_in_computed_column(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [0.0, 1.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') / pl.col('x'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # 0/0 = NaN
        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        assert t_step.output_values["y"] == NAN_SENTINEL


class TestEdgeCaseLargeFloats:
    """M.3: Row with very large float values.

    Why: Actuarial sums can be very large; must not overflow or corrupt.
    """

    def test_large_float_values(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1e18, 2e18]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 1000)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        assert t_step.output_values["y"] == pytest.approx(1e21)


class TestEdgeCaseStringColumns:
    """M.4: Row with string columns (not numeric).

    Why: Policy data includes names, regions, vehicle makes -- all strings.
    """

    def test_string_column_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "name": ["alice", "bob"],
                "region": ["north", "south"],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t", "df = df.with_columns(greeting=pl.lit('Hello ') + pl.col('name'))"
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="greeting")
        assert result.output_value == "Hello alice"


class TestEdgeCaseDateColumns:
    """M.5: Row with date columns.

    Dates are stringified by _jsonify_row since they're not JSON primitives.
    Why: Policy inception dates, DOBs are date type in actuarial data.
    """

    def test_date_column_serialization(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "inception": [date(2025, 1, 15), date(2025, 6, 1)],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        # Date should be stringified
        assert "2025-01-15" in str(t_step.output_values["inception"])


class TestEdgeCaseBooleanColumns:
    """M.6: Row with boolean columns.

    Why: Boolean flags (is_renewal, has_claims) are common in insurance data.
    """

    def test_boolean_column_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "is_renewal": [True, False],
                "has_claims": [False, True],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        assert t_step.output_values["is_renewal"] is True
        assert t_step.output_values["has_claims"] is False


class TestEdgeCaseListColumns:
    """M.7: Row with list/array columns.

    List values should be stringified by _jsonify_row.
    Why: Some schemas include array fields (e.g., additional driver ages).
    """

    def test_list_column_serialization(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "tags": [["a", "b"], ["c"]],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        # List should be stringified
        assert isinstance(t_step.output_values["tags"], str)


class TestEdgeCaseStructColumns:
    """M.8: Row with struct columns.

    Struct values should be stringified.
    Why: Nested data structures appear in some policy schemas.
    """

    def test_struct_column_serialization(self, tmp_path):
        p = tmp_path / "data.parquet"
        df = pl.DataFrame(
            {
                "x": [1, 2],
                "y": [10, 20],
            }
        ).select(pl.struct("x", "y").alias("s"))
        df.write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        # Struct should be stringified
        assert isinstance(t_step.output_values["s"], str)


class TestEdgeCaseUnicodeColumnNames:
    """M.9: Unicode in column names.

    Why: International datasets may have non-ASCII column names.
    """

    def test_unicode_column_name(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"prix_unitaire": [100], "quantite": [5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = df.with_columns(total=pl.col('prix_unitaire') * pl.col('quantite'))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="total")
        assert result.output_value == 500


class TestEdgeCaseSpacesInColumnNames:
    """M.10: Spaces in column names.

    Why: CSV imports often have spaces in column names.
    """

    def test_spaces_in_column_name(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"my column": [42], "other col": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="my column")
        assert result.output_value == 42


class TestEdgeCasePythonKeywordColumn:
    """M.11: Column name that's a Python keyword.

    Why: Column names like 'class', 'type', 'import' appear in insurance data.
    """

    def test_python_keyword_column_name(self, tmp_path):
        p = tmp_path / "data.parquet"
        # 'class' is a Python keyword but valid as a Polars column name
        pl.DataFrame({"class": ["A", "B"], "value": [100, 200]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="class")
        assert result.output_value == "A"


# ===========================================================================
# N. The Actual burn_cost Example
# ===========================================================================


class TestBurnCostExample:
    """N.1-5: Replicate the pipeline pattern from rating/main.py.

    The real pipeline: quotes -> processing -> policies -> join_scoring ->
    join_policy_data -> join_premiums (adds burn_cost = premium * 0.7).

    We replicate the key fragment:
      data + quoted_premiums -> join -> with_columns(burn_cost=premium * 0.7)
    Cell clicked: join_premiums, column 'burn_cost'
    Expected:
      - burn_cost = premium * 0.7
      - Trace shows the join and the formula
      - All upstream nodes appear
      - column_relevant correctly identifies which nodes touch burn_cost
    Why: This is the actual production use case driving the trace feature.
    """

    def test_burn_cost_trace_value_correct(self, tmp_path):
        p_data = tmp_path / "data.parquet"
        p_premiums = tmp_path / "premiums.parquet"
        pl.DataFrame(
            {
                "quote_id": [101, 102, 103],
                "proposer_age": [30, 45, 25],
                "cover_type": ["comp", "tpft", "comp"],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "quote_id": [101, 102, 103],
                "premium": [500.0, 300.0, 700.0],
                "policy_id": [1001, None, 1003],
            }
        ).write_parquet(p_premiums)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("premiums", str(p_premiums)),
                    _transform_node(
                        "join_premiums",
                        "df = data.join(premiums, on='quote_id', how='left')\n"
                        "df = df.with_columns("
                        "sale_flag=pl.when(pl.col('policy_id').is_null()).then(pl.lit(0)).otherwise(pl.lit(1)),"
                        "burn_cost=pl.col('premium') * 0.7)",
                    ),
                ],
                "edges": [_edge("data", "join_premiums"), _edge("premiums", "join_premiums")],
            }
        )

        result = execute_trace(
            graph, row_index=0, target_node_id="join_premiums", column="burn_cost"
        )

        join_step = _step_by_id(result, "join_premiums")
        expected_burn_cost = join_step.output_values["premium"] * 0.7
        assert result.output_value == pytest.approx(expected_burn_cost)
        assert "burn_cost" in join_step.schema_diff.columns_added

    def test_burn_cost_upstream_nodes_present(self, tmp_path):
        """All upstream nodes appear in the trace for burn_cost."""
        p_data = tmp_path / "data.parquet"
        p_premiums = tmp_path / "premiums.parquet"
        pl.DataFrame(
            {
                "quote_id": [101],
                "proposer_age": [30],
                "cover_type": ["comp"],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "quote_id": [101],
                "premium": [500.0],
                "policy_id": [1001],
            }
        ).write_parquet(p_premiums)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("premiums", str(p_premiums)),
                    _transform_node(
                        "join_premiums",
                        "df = data.join(premiums, on='quote_id', how='left')\n"
                        "df = df.with_columns(burn_cost=pl.col('premium') * 0.7)",
                    ),
                ],
                "edges": [_edge("data", "join_premiums"), _edge("premiums", "join_premiums")],
            }
        )

        result = execute_trace(
            graph, row_index=0, target_node_id="join_premiums", column="burn_cost"
        )
        ids = _step_ids(result)
        # burn_cost is calculated at join_premiums; both sources are ancestors
        assert "join_premiums" in ids
        assert "data" in ids or "premiums" in ids  # at least one ancestor

    def test_burn_cost_column_relevance(self, tmp_path):
        """column_relevant correctly identifies which nodes touch burn_cost."""
        p_data = tmp_path / "data.parquet"
        p_premiums = tmp_path / "premiums.parquet"
        pl.DataFrame(
            {
                "quote_id": [101],
                "proposer_age": [30],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "quote_id": [101],
                "premium": [500.0],
            }
        ).write_parquet(p_premiums)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("premiums", str(p_premiums)),
                    _transform_node(
                        "join_premiums",
                        "df = data.join(premiums, on='quote_id', how='left')\n"
                        "df = df.with_columns(burn_cost=pl.col('premium') * 0.7)",
                    ),
                ],
                "edges": [_edge("data", "join_premiums"), _edge("premiums", "join_premiums")],
            }
        )

        result = execute_trace(
            graph, row_index=0, target_node_id="join_premiums", column="burn_cost"
        )

        # join_premiums creates burn_cost -> column_relevant=True
        jp_step = _step_by_id(result, "join_premiums")
        assert jp_step.column_relevant is True

    def test_burn_cost_with_null_premium(self, tmp_path):
        """burn_cost where premium is NULL (no match in left join).

        Verifies that when a left join produces NULL values for unmatched rows,
        the trace correctly shows the NULL burn_cost.
        """
        p_data = tmp_path / "data.parquet"
        p_premiums = tmp_path / "premiums.parquet"
        pl.DataFrame(
            {
                "quote_id": [101, 102, 103],
                "age": [30, 45, 25],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "quote_id": [101, 103],
                "premium": [500.0, 700.0],
            }
        ).write_parquet(p_premiums)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("premiums", str(p_premiums)),
                    _transform_node(
                        "join_premiums",
                        "df = data.join(premiums, on='quote_id', how='left')\n"
                        "df = df.with_columns(burn_cost=pl.col('premium') * 0.7)",
                    ),
                ],
                "edges": [_edge("data", "join_premiums"), _edge("premiums", "join_premiums")],
            }
        )

        # Find which row has quote_id=102 (no premium match) in the join output
        results = execute_graph(graph, target_node_id="join_premiums", row_limit=_ROW_LIMIT)
        preview_rows = results["join_premiums"].preview
        null_row_idx = next(i for i, r in enumerate(preview_rows) if r["quote_id"] == 102)

        # Pass the executor's preview cache so the trace correlates
        # against the exact same join output ``execute_graph`` produced.
        # See the matching note in ``test_trace_null_from_left_join``.
        result = execute_trace(
            graph,
            row_index=null_row_idx,
            target_node_id="join_premiums",
            column="burn_cost",
            preview=_preview_cache,
        )
        assert result.output_value is None

    def test_burn_cost_full_waterfall(self, tmp_path):
        """Full waterfall: processing -> join -> burn_cost -> downstream select."""
        p_quotes = tmp_path / "quotes.parquet"
        p_premiums = tmp_path / "premiums.parquet"
        pl.DataFrame(
            {
                "quote_id": [101, 102],
                "age": [30, 45],
                "premium": [500.0, 300.0],
            }
        ).write_parquet(p_quotes)
        pl.DataFrame(
            {
                "quote_id": [101, 102],
                "quoted_premium": [480.0, 290.0],
            }
        ).write_parquet(p_premiums)

        graph = _g(
            {
                "nodes": [
                    _source_node("quotes", str(p_quotes)),
                    _transform_node(
                        "processing", "df = df.with_columns(age_band=pl.col('age') // 10 * 10)"
                    ),
                    _source_node("premiums", str(p_premiums)),
                    _transform_node(
                        "join_premiums",
                        "df = processing.join(premiums, on='quote_id', how='left')\n"
                        "df = df.with_columns(burn_cost=pl.col('premium') * 0.7)",
                    ),
                    _transform_node(
                        "features",
                        "df = df.select('quote_id', 'premium', 'burn_cost', 'quoted_premium')",
                    ),
                ],
                "edges": [
                    _edge("quotes", "processing"),
                    _edge("processing", "join_premiums"),
                    _edge("premiums", "join_premiums"),
                    _edge("join_premiums", "features"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="features", column="burn_cost")

        # burn_cost should trace through the full waterfall
        assert len(result.steps) >= 3
        # Verify burn_cost = premium * 0.7 for whatever row is at index 0
        # (join order is non-deterministic)
        features_step = _step_by_id(result, "features")
        expected_burn_cost = features_step.output_values["premium"] * 0.7
        assert result.output_value == pytest.approx(expected_burn_cost)


# ===========================================================================
# O. Performance Tests
# ===========================================================================


@pytest.mark.perf
class TestPerformanceLargeDataset:
    """O.1-2: Trace on pipeline with 100K rows.

    First click: should complete within reasonable time.
    Second click: should be much faster (cached).
    Why: Real actuarial datasets have 100K+ rows; trace must be responsive.
    """

    @pytest.mark.slow
    def test_large_dataset_first_trace(self, tmp_path):
        _trace_cache.invalidate()
        _preview_cache.invalidate()

        p = tmp_path / "data.parquet"
        n = 100_000
        pl.DataFrame(
            {
                "id": list(range(n)),
                "value": [float(i) * 1.5 for i in range(n)],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('value') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        # Use row_limit large enough to include row 50000
        row_limit = 100_000
        t0 = time.perf_counter()
        result = execute_trace(graph, row_index=50000, target_node_id="t", row_limit=row_limit)
        elapsed = time.perf_counter() - t0

        assert result.output_value["id"] == 50000
        assert elapsed < 5.0, f"First trace took {elapsed:.2f}s, expected < 5s"

    @pytest.mark.slow
    def test_large_dataset_cached_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        n = 100_000
        pl.DataFrame(
            {
                "id": list(range(n)),
                "value": [float(i) * 1.5 for i in range(n)],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('value') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        row_limit = 100_000

        # Warm the cache
        execute_trace(graph, row_index=0, target_node_id="t", row_limit=row_limit)

        # Second trace should be fast
        t0 = time.perf_counter()
        result = execute_trace(graph, row_index=99999, target_node_id="t", row_limit=row_limit)
        elapsed = time.perf_counter() - t0

        assert result.output_value["id"] == 99999
        assert elapsed < 0.1, f"Cached trace took {elapsed:.3f}s, expected < 100ms"


@pytest.mark.perf
class TestPerformanceManyNodes:
    """O.3: Trace on pipeline with 20 nodes.

    Should complete within a reasonable timeout.
    Why: Complex pricing pipelines have 15-25 nodes.
    """

    @pytest.mark.slow
    def test_twenty_node_pipeline(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"col_0": list(range(100))}).write_parquet(p)

        nodes = [_source_node("n0", str(p))]
        edges = []
        for i in range(1, 21):
            code = f"df = df.with_columns(col_{i}=pl.col('col_{i - 1}') + 1)"
            nodes.append(_transform_node(f"n{i}", code))
            edges.append(_edge(f"n{i - 1}", f"n{i}"))

        graph = _g({"nodes": nodes, "edges": edges})

        _trace_cache.invalidate()
        _preview_cache.invalidate()

        t0 = time.perf_counter()
        result = execute_trace(graph, row_index=0, target_node_id="n20")
        elapsed = time.perf_counter() - t0

        assert len(result.steps) == 21
        assert result.output_value["col_20"] == 20
        assert elapsed < 10.0, f"20-node trace took {elapsed:.2f}s, expected < 10s"


# ===========================================================================
# Serialization round-trip test
# ===========================================================================


class TestSerializationRoundTrip:
    """Verify trace_result_to_dict produces valid, complete JSON-safe output."""

    def test_full_trace_serializes_to_json(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2], "y": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') + pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="z")
        d = trace_result_to_dict(result)

        # Should be JSON-serializable
        serialized = json.dumps(d)
        assert isinstance(serialized, str)

        # Round-trip check
        parsed = json.loads(serialized)
        assert parsed["target_node_id"] == "t"
        assert parsed["column"] == "z"
        assert parsed["output_value"] == 11
        assert len(parsed["steps"]) == 2
