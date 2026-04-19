"""Tests for haute.trace - execution trace / data lineage."""

from __future__ import annotations

import polars as pl
import pytest

from haute.trace import (
    SchemaDiff,
    TraceResult,
    TraceStep,
    _compute_schema_diff,
    _jsonify_row,
    execute_trace,
    trace_result_to_dict,
)
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_node as _n,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

# ---------------------------------------------------------------------------
# _jsonify_row
# ---------------------------------------------------------------------------


class TestJsonifyRow:
    def test_primitives_preserved(self):
        row = {"a": 1, "b": 2.5, "c": "hello", "d": True, "e": None}
        result = _jsonify_row(row)
        assert result == row

    def test_nan_replaced_with_none(self):
        row = {"a": float("nan")}
        result = _jsonify_row(row)
        assert result["a"] is None

    def test_positive_inf_replaced_with_none(self):
        row = {"a": float("inf")}
        result = _jsonify_row(row)
        assert result["a"] is None

    def test_negative_inf_replaced_with_none(self):
        row = {"a": float("-inf")}
        result = _jsonify_row(row)
        assert result["a"] is None

    def test_mixed_nan_inf_and_normal_values(self):
        row = {
            "ok": 1.5,
            "nan_val": float("nan"),
            "inf_val": float("inf"),
            "neg_inf": float("-inf"),
            "text": "hello",
            "none_val": None,
        }
        result = _jsonify_row(row)
        assert result["ok"] == 1.5
        assert result["nan_val"] is None
        assert result["inf_val"] is None
        assert result["neg_inf"] is None
        assert result["text"] == "hello"
        assert result["none_val"] is None

    def test_result_is_json_serializable(self):
        """Ensure the output of _jsonify_row can be passed to json.dumps."""
        import json

        row = {
            "a": float("nan"),
            "b": float("inf"),
            "c": float("-inf"),
            "d": 1.5,
            "e": "text",
            "f": None,
        }
        result = _jsonify_row(row)
        # json.dumps would raise ValueError for NaN/Inf if not handled
        serialized = json.dumps(result)
        assert isinstance(serialized, str)

    def test_non_primitives_stringified(self):
        from datetime import date

        row = {"d": date(2025, 1, 1)}
        result = _jsonify_row(row)
        assert result["d"] == "2025-01-01"


# ---------------------------------------------------------------------------
# _compute_schema_diff
# ---------------------------------------------------------------------------


class TestComputeSchemaDiff:
    def test_source_node_all_added(self):
        diff = _compute_schema_diff(None, {"a": 1, "b": 2})
        assert diff.columns_added == ["a", "b"]
        assert diff.columns_removed == []
        assert diff.columns_modified == []

    def test_column_added(self):
        diff = _compute_schema_diff({"a": 1}, {"a": 1, "b": 2})
        assert diff.columns_added == ["b"]
        assert diff.columns_passed == ["a"]

    def test_column_removed(self):
        diff = _compute_schema_diff({"a": 1, "b": 2}, {"a": 1})
        assert diff.columns_removed == ["b"]

    def test_column_modified(self):
        diff = _compute_schema_diff({"a": 1}, {"a": 99})
        assert diff.columns_modified == ["a"]
        assert diff.columns_passed == []

    def test_nan_equals_nan(self):
        diff = _compute_schema_diff({"a": float("nan")}, {"a": float("nan")})
        assert diff.columns_passed == ["a"]
        assert diff.columns_modified == []


# ---------------------------------------------------------------------------
# execute_trace
# ---------------------------------------------------------------------------


class TestExecuteTrace:
    def test_basic_trace(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 10)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        result = execute_trace(graph, row_index=0)

        assert isinstance(result, TraceResult)
        assert result.nodes_in_trace == 2
        assert result.total_nodes_in_pipeline == 2
        assert len(result.steps) == 2

        # Source step should have all columns added
        src_step = result.steps[0]
        assert "x" in src_step.schema_diff.columns_added

        # Transform step should have y added
        t_step = result.steps[1]
        assert "y" in t_step.schema_diff.columns_added

    def test_trace_defaults_to_last_node(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        result = execute_trace(graph)
        assert result.target_node_id == "t"

    def test_trace_calculated_column_keeps_ancestors(self, tmp_path):
        """Calculated column keeps the creating node AND all its ancestors."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "z": [99]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # passthrough - doesn't have 'y' but feeds into t
                    _transform_node("mid"),
                    # adds 'y' - column_relevant, ancestors kept for calc path
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "t")],
            }
        )
        result = execute_trace(graph, column="y")

        # 'y' is created at t → t is column_relevant, src/mid are ancestors
        ids = [s.node_id for s in result.steps]
        assert ids == ["src", "mid", "t"]
        assert result.steps[2].column_relevant is True  # t: adds y
        assert result.steps[0].column_relevant is False  # src: ancestor
        assert result.steps[1].column_relevant is False  # mid: ancestor

    def test_trace_passthrough_prunes_unrelated_branches(self, tmp_path):
        """Pass-through column prunes source branches that don't carry it."""
        p1 = tmp_path / "a.parquet"
        p2 = tmp_path / "b.parquet"
        pl.DataFrame({"x": [1], "shared": [10]}).write_parquet(p1)
        pl.DataFrame({"y": [2], "shared": [10]}).write_parquet(p2)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p1)),  # has x
                    _source_node("b", str(p2)),  # has y, not x
                    _transform_node("join", "df = a.join(b, on='shared')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )
        result = execute_trace(graph, column="x")

        # 'x' comes from 'a' only — 'b' should be pruned
        ids = {s.node_id for s in result.steps}
        assert "a" in ids
        assert "join" in ids
        assert "b" not in ids

    def test_trace_column_passthrough_keeps_path(self, tmp_path):
        """A pass-through column traces back through all nodes that carry it."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "z": [99]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid"),  # passes x through
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "t")],
            }
        )
        result = execute_trace(graph, column="x")

        # 'x' exists in all 3 nodes → all 3 in trace
        assert len(result.steps) == 3
        assert all(s.column_relevant for s in result.steps)

    def test_row_id_from_api_input(self, tmp_path):
        """Trace discovers row_id_column from apiInput source and extracts its value."""
        p = tmp_path / "data.json"
        import json

        p.write_text(
            json.dumps(
                [
                    {"policy_id": 100, "x": 1},
                    {"policy_id": 200, "x": 2},
                    {"policy_id": 300, "x": 3},
                ]
            )
        )
        # Pre-cache JSON as parquet (the builder expects this)
        from haute._json_flatten import _json_cache_path

        cache_path = _json_cache_path(str(p))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"policy_id": [100, 200, 300], "x": [1, 2, 3]}).write_parquet(cache_path)

        graph = _g(
            {
                "nodes": [
                    _n(
                        {
                            "id": "src",
                            "data": {
                                "label": "src",
                                "nodeType": "apiInput",
                                "config": {
                                    "path": str(p),
                                    "row_id_column": "policy_id",
                                },
                            },
                        }
                    ),
                    _transform_node("t"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        result = execute_trace(graph, row_index=1)
        assert result.row_id_column == "policy_id"
        assert result.row_id_value == 200

    def test_row_id_none_without_api_input(self, tmp_path):
        """Without apiInput node, row_id_column and row_id_value are None."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )
        result = execute_trace(graph, row_index=0)
        assert result.row_id_column is None
        assert result.row_id_value is None

    def test_cache_reuses_execution_for_different_rows(self, tmp_path):
        """Subsequent traces on same graph reuse cached DataFrames."""
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
        # Both rows produced correct results — cache served second call

    def test_cache_invalidates_on_graph_change(self, tmp_path):
        """Changing graph code produces different results (cache invalidated)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph1 = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )
        r1 = execute_trace(graph1, row_index=0)
        assert "y" not in r1.output_value

        graph2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r2 = execute_trace(graph2, row_index=0)
        assert r2.output_value["y"] == 2

    def test_empty_graph_raises(self):
        with pytest.raises(ValueError, match="Empty graph"):
            execute_trace(_g({"nodes": [], "edges": []}))

    def test_missing_target_raises(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g({"nodes": [_source_node("src", str(p))], "edges": []})
        with pytest.raises(ValueError, match="not found"):
            execute_trace(graph, target_node_id="nonexistent")

    def test_trace_respects_scenario_pruning(self, tmp_path):
        """Trace with a non-live scenario should exclude the live branch
        behind a live_switch node (regression test for scenario threading)."""
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

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="sw",
            source="nb_batch",
        )
        step_ids = {s.node_id for s in result.steps}
        assert "batch_src" in step_ids
        assert "live_src" not in step_ids


# ---------------------------------------------------------------------------
# trace_result_to_dict
# ---------------------------------------------------------------------------


class TestTraceResultToDict:
    def test_serialises_correctly(self):
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
                    execution_ms=1.5,
                ),
            ],
            total_nodes_in_pipeline=1,
            nodes_in_trace=1,
            execution_ms=2.0,
        )
        d = trace_result_to_dict(result)
        assert d["target_node_id"] == "t"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["schema_diff"]["columns_added"] == ["x"]
        assert d["execution_ms"] == 2.0
