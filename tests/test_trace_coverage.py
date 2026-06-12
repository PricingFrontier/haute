"""Coverage-focused tests for trace.py, _trace_waterfall.py, and _trace_export.py.

Targets uncovered paths identified by coverage analysis.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl
import pytest

from haute._trace_export import export_trace
from haute._trace_waterfall import WaterfallEntry, WaterfallResult, build_waterfall
from haute.trace import (
    SchemaDiff,
    TraceResult,
    TraceStep,
    _compute_schema_diff,
    _jsonify_row,
    _tag_column_relevance,
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
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

NAN_SENTINEL = {"__haute_type__": "non_finite_float", "value": "nan"}
INF_SENTINEL = {"__haute_type__": "non_finite_float", "value": "inf"}
NEG_INF_SENTINEL = {"__haute_type__": "non_finite_float", "value": "-inf"}

# ===========================================================================
# _jsonify_row — uncovered paths
# ===========================================================================


class TestJsonifyRowEdgeCases:
    """Cover date stringification, datetime, timedelta, list, struct."""

    def test_date_stringified(self):
        row = {"d": date(2025, 6, 15)}
        result = _jsonify_row(row)
        assert result["d"] == "2025-06-15"
        assert isinstance(result["d"], str)

    def test_datetime_stringified(self):
        row = {"dt": datetime(2025, 6, 15, 12, 30, 45)}
        result = _jsonify_row(row)
        assert isinstance(result["dt"], str)
        assert "2025-06-15" in result["dt"]

    def test_timedelta_stringified(self):
        row = {"td": timedelta(days=3, hours=5)}
        result = _jsonify_row(row)
        assert isinstance(result["td"], str)

    def test_list_stringified(self):
        row = {"lst": [1, 2, 3]}
        result = _jsonify_row(row)
        assert isinstance(result["lst"], str)
        assert result["lst"] == "[1, 2, 3]"

    def test_dict_stringified(self):
        row = {"d": {"nested": "value"}}
        result = _jsonify_row(row)
        assert isinstance(result["d"], str)

    def test_empty_row(self):
        assert _jsonify_row({}) == {}

    def test_bool_preserved_not_stringified(self):
        row = {"flag": True, "other": False}
        result = _jsonify_row(row)
        assert result["flag"] is True
        assert result["other"] is False

    def test_int_preserved(self):
        row = {"n": 42}
        result = _jsonify_row(row)
        assert result["n"] == 42
        assert isinstance(result["n"], int)

    def test_none_preserved(self):
        row = {"n": None}
        result = _jsonify_row(row)
        assert result["n"] is None

    def test_nan_becomes_non_finite_sentinel(self):
        row = {"x": float("nan")}
        result = _jsonify_row(row)
        assert result["x"] == NAN_SENTINEL

    def test_inf_becomes_non_finite_sentinel(self):
        row = {"x": float("inf"), "y": float("-inf")}
        result = _jsonify_row(row)
        assert result["x"] == INF_SENTINEL
        assert result["y"] == NEG_INF_SENTINEL


# ===========================================================================
# _compute_schema_diff — uncovered paths
# ===========================================================================


class TestComputeSchemaDiffExtended:
    """Cover source node (input=None), all classification buckets."""

    def test_source_node_no_input(self):
        diff = _compute_schema_diff(None, {"a": 1, "b": "hello", "c": True})
        assert sorted(diff.columns_added) == ["a", "b", "c"]
        assert diff.columns_removed == []
        assert diff.columns_modified == []
        assert diff.columns_passed == []

    def test_source_node_empty_output(self):
        diff = _compute_schema_diff(None, {})
        assert diff.columns_added == []

    def test_all_columns_passed_through(self):
        row = {"a": 1, "b": "x"}
        diff = _compute_schema_diff(row, row.copy())
        assert diff.columns_passed == ["a", "b"]
        assert diff.columns_added == []
        assert diff.columns_removed == []
        assert diff.columns_modified == []

    def test_mixed_added_removed_modified_passed(self):
        inp = {"keep": 1, "change": 10, "drop": 99}
        out = {"keep": 1, "change": 20, "new": 42}
        diff = _compute_schema_diff(inp, out)
        assert diff.columns_added == ["new"]
        assert diff.columns_removed == ["drop"]
        assert diff.columns_modified == ["change"]
        assert diff.columns_passed == ["keep"]

    def test_nan_equals_nan_treated_as_passed(self):
        diff = _compute_schema_diff({"x": float("nan")}, {"x": float("nan")})
        assert diff.columns_passed == ["x"]
        assert diff.columns_modified == []

    def test_nan_to_value_treated_as_modified(self):
        diff = _compute_schema_diff({"x": float("nan")}, {"x": 5.0})
        assert diff.columns_modified == ["x"]

    def test_value_to_nan_treated_as_modified(self):
        diff = _compute_schema_diff({"x": 5.0}, {"x": float("nan")})
        assert diff.columns_modified == ["x"]

    def test_none_values_treated_as_equal(self):
        diff = _compute_schema_diff({"x": None}, {"x": None})
        assert diff.columns_passed == ["x"]

    def test_none_to_value_is_modified(self):
        diff = _compute_schema_diff({"x": None}, {"x": 42})
        assert diff.columns_modified == ["x"]


# ===========================================================================
# _tag_column_relevance — coverage for standalone function
# ===========================================================================


class TestTagColumnRelevance:
    def _make_step(self, node_id, added=None, modified=None, passed=None, output=None):
        return TraceStep(
            node_id=node_id,
            node_name=node_id,
            node_type="polars",
            schema_diff=SchemaDiff(
                columns_added=added or [],
                columns_removed=[],
                columns_modified=modified or [],
                columns_passed=passed or [],
            ),
            input_values={},
            output_values=output or {},
        )

    def test_tags_added(self):
        s = self._make_step("a", added=["x"])
        _tag_column_relevance([s], "x")
        assert s.column_relevant is True

    def test_tags_modified(self):
        s = self._make_step("a", modified=["x"])
        _tag_column_relevance([s], "x")
        assert s.column_relevant is True

    def test_tags_passed(self):
        s = self._make_step("a", passed=["x"])
        _tag_column_relevance([s], "x")
        assert s.column_relevant is True

    def test_tags_in_output_values(self):
        s = self._make_step("a", output={"x": 1})
        _tag_column_relevance([s], "x")
        assert s.column_relevant is True

    def test_tags_irrelevant(self):
        s = self._make_step("a", added=["y"])
        _tag_column_relevance([s], "x")
        assert s.column_relevant is False

    def test_multiple_steps_mixed(self):
        s1 = self._make_step("a", added=["x"])
        s2 = self._make_step("b", added=["y"])
        s3 = self._make_step("c", passed=["x"])
        _tag_column_relevance([s1, s2, s3], "x")
        assert s1.column_relevant is True
        assert s2.column_relevant is False
        assert s3.column_relevant is True


# ===========================================================================
# trace_result_to_dict — comprehensive serialization
# ===========================================================================


class TestTraceResultToDictCoverage:
    def test_full_serialisation(self):
        result = TraceResult(
            target_node_id="t",
            row_index=2,
            column="premium",
            output_value=100.5,
            steps=[
                TraceStep(
                    node_id="src",
                    node_name="Source",
                    node_type="dataSource",
                    schema_diff=SchemaDiff(
                        columns_added=["x", "y"],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=[],
                    ),
                    input_values={},
                    output_values={"x": 1, "y": 2},
                    execution_ms=0.5,
                    expression={"expression_text": "x + y"},
                    calculation={"substituted_text": "1 + 2", "result_value": 3},
                    node_detail={"detail_type": "rating_step"},
                    row_lineage_type="one_to_one",
                ),
                TraceStep(
                    node_id="t",
                    node_name="Transform",
                    node_type="polars",
                    schema_diff=SchemaDiff(
                        columns_added=["premium"],
                        columns_removed=["y"],
                        columns_modified=["x"],
                        columns_passed=[],
                    ),
                    input_values={"x": 1, "y": 2},
                    output_values={"x": 10, "premium": 100.5},
                    execution_ms=1.5,
                ),
            ],
            row_id_column="policy_id",
            row_id_value=42,
            total_nodes_in_pipeline=5,
            nodes_in_trace=2,
            execution_ms=3.0,
            waterfall=[{"label": "base", "operation": "base", "value": 100}],
        )
        d = trace_result_to_dict(result)

        assert d["target_node_id"] == "t"
        assert d["row_index"] == 2
        assert d["column"] == "premium"
        assert d["output_value"] == 100.5
        assert d["row_id_column"] == "policy_id"
        assert d["row_id_value"] == 42
        assert d["total_nodes_in_pipeline"] == 5
        assert d["nodes_in_trace"] == 2
        assert d["execution_ms"] == 3.0
        assert d["waterfall"] == [{"label": "base", "operation": "base", "value": 100}]

        assert len(d["steps"]) == 2

        s0 = d["steps"][0]
        assert s0["node_id"] == "src"
        assert s0["node_name"] == "Source"
        assert s0["node_type"] == "dataSource"
        assert s0["schema_diff"]["columns_added"] == ["x", "y"]
        assert s0["input_values"] == {}
        assert s0["output_values"] == {"x": 1, "y": 2}
        assert s0["execution_ms"] == 0.5
        assert s0["expression"] == {"expression_text": "x + y"}
        assert s0["calculation"]["result_value"] == 3
        assert s0["node_detail"]["detail_type"] == "rating_step"
        assert s0["row_lineage_type"] == "one_to_one"

        s1 = d["steps"][1]
        assert s1["schema_diff"]["columns_removed"] == ["y"]
        assert s1["schema_diff"]["columns_modified"] == ["x"]
        assert s1["expression"] is None
        assert s1["calculation"] is None
        assert s1["node_detail"] is None
        assert s1["row_lineage_type"] is None

    def test_minimal_serialisation(self):
        result = TraceResult(
            target_node_id="n",
            row_index=0,
            column=None,
            output_value=None,
            steps=[],
            total_nodes_in_pipeline=0,
            nodes_in_trace=0,
            execution_ms=0.0,
        )
        d = trace_result_to_dict(result)
        assert d["target_node_id"] == "n"
        assert d["column"] is None
        assert d["output_value"] is None
        assert d["steps"] == []
        assert d["waterfall"] is None
        assert d["row_id_column"] is None
        assert d["row_id_value"] is None

    def test_serialisation_applies_json_safe_boundary_to_enriched_values(self):
        unsafe = 2**53
        result = TraceResult(
            target_node_id="t",
            row_index=0,
            column="premium",
            output_value=math.inf,
            steps=[
                TraceStep(
                    node_id="t",
                    node_name="Transform",
                    node_type="polars",
                    schema_diff=SchemaDiff(
                        columns_added=[],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=["premium"],
                    ),
                    input_values={"id": unsafe, "missing": None},
                    output_values={"premium": math.nan},
                    calculation={"result_value": -math.inf, "input": unsafe},
                    node_detail={"diagnostics": [{"value": math.nan}]},
                    row_lineage_type="one_to_one",
                ),
            ],
            row_id_column="policy_id",
            row_id_value=unsafe,
            total_nodes_in_pipeline=1,
            nodes_in_trace=1,
            execution_ms=0.0,
            waterfall=[{"label": "premium", "value": math.nan}],
        )

        d = trace_result_to_dict(result)

        assert d["output_value"] == INF_SENTINEL
        assert d["row_id_value"] == str(unsafe)
        step = d["steps"][0]
        assert step["input_values"]["id"] == str(unsafe)
        assert step["input_values"]["missing"] is None
        assert step["output_values"]["premium"] == NAN_SENTINEL
        assert step["calculation"] == {
            "result_value": NEG_INF_SENTINEL,
            "input": str(unsafe),
        }
        assert step["node_detail"] == {"diagnostics": [{"value": NAN_SENTINEL}]}
        assert d["waterfall"] == [{"label": "premium", "value": NAN_SENTINEL}]
        json.dumps(d, allow_nan=False)

    def test_serialisation_applies_json_safe_boundary_to_whole_payload(self, monkeypatch):
        import haute.trace as trace_module

        original_to_json_safe = trace_module.to_json_safe
        calls: list[Any] = []

        def recording_to_json_safe(value: Any) -> Any:
            calls.append(value)
            return original_to_json_safe(value)

        monkeypatch.setattr(trace_module, "to_json_safe", recording_to_json_safe)
        unsafe = 2**53
        result = TraceResult(
            target_node_id="t",
            row_index=0,
            column="premium",
            output_value=unsafe,
            steps=[
                TraceStep(
                    node_id="t",
                    node_name="Transform",
                    node_type="polars",
                    schema_diff=SchemaDiff(
                        columns_added=["premium"],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=[],
                    ),
                    input_values={},
                    output_values={"premium": unsafe},
                ),
            ],
            row_id_column="policy_id",
            row_id_value=unsafe,
            total_nodes_in_pipeline=1,
            nodes_in_trace=1,
            execution_ms=0.0,
            waterfall=[{"label": "premium", "value": unsafe}],
        )

        d = trace_module.trace_result_to_dict(result)

        assert len(calls) == 1
        assert calls[0]["output_value"] == unsafe
        assert calls[0]["steps"][0]["output_values"]["premium"] == unsafe
        assert d["output_value"] == str(unsafe)
        assert d["steps"][0]["output_values"]["premium"] == str(unsafe)
        assert d["waterfall"] == [{"label": "premium", "value": str(unsafe)}]
        json.dumps(d, allow_nan=False)


# ===========================================================================
# execute_trace — uncovered paths
# ===========================================================================


class TestExecuteTraceColumnTracing:
    """Cover column-based tracing, pruning, and relevance tagging."""

    def test_column_tracing_marks_relevance(self, tmp_path):
        """Column tracing sets column_relevant correctly on each step."""
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
        result = execute_trace(graph, row_index=0, column="z")

        # z is added at t, src is an ancestor
        t_step = next(s for s in result.steps if s.node_id == "t")
        assert t_step.column_relevant is True
        assert result.column == "z"
        assert result.output_value is not None

    def test_column_tracing_passthrough_column(self, tmp_path):
        """Pass-through column keeps all nodes that carry it."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "y": [10]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid"),  # passes x through
                    _transform_node("end"),  # also passes x through
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "end")],
            }
        )
        result = execute_trace(graph, row_index=0, column="x")
        assert len(result.steps) == 3
        assert all(s.column_relevant for s in result.steps)

    def test_target_node_selection(self, tmp_path):
        """Explicit target_node_id selects the correct node."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid"),
                    _transform_node("end"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "end")],
            }
        )
        result = execute_trace(graph, row_index=0, target_node_id="mid")
        assert result.target_node_id == "mid"

    def test_output_value_is_full_row_without_column(self, tmp_path):
        """Without column param, output_value is the full row dict."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10], "y": [20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )
        result = execute_trace(graph, row_index=0)
        assert isinstance(result.output_value, dict)
        assert result.output_value["x"] == 10
        assert result.output_value["y"] == 20

    def test_output_value_is_scalar_with_column(self, tmp_path):
        """With column param, output_value is the single column value."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [42], "y": [99]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )
        result = execute_trace(graph, row_index=0, column="x")
        assert result.output_value == 42


class TestExecuteTraceCaching:
    """Cover cache hit/miss/invalidation paths."""

    def test_cache_hit_reuses_data(self, tmp_path):
        """Second call with same graph reuses cached execution."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )

        r1 = execute_trace(graph, row_index=0)
        r2 = execute_trace(graph, row_index=1)
        assert r1.output_value["x"] == 1
        assert r2.output_value["x"] == 2

    def test_cache_invalidation_on_code_change(self, tmp_path):
        """Different graph code invalidates cache."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5]}).write_parquet(p)

        g1 = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t")],
                "edges": [_edge("src", "t")],
            }
        )
        r1 = execute_trace(g1, row_index=0)
        assert "y" not in r1.output_value

        g2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 3)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r2 = execute_trace(g2, row_index=0)
        assert r2.output_value["y"] == 15


class TestExecuteTraceEdgeCases:
    """Cover error handling and edge cases in execute_trace."""

    def test_empty_graph_raises(self):
        with pytest.raises(ValueError, match="Empty graph"):
            execute_trace(_g({"nodes": [], "edges": []}))

    def test_missing_target_raises(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        graph = _g({"nodes": [_source_node("src", str(p))], "edges": []})
        with pytest.raises(ValueError, match="not found"):
            execute_trace(graph, target_node_id="nonexistent")

    def test_row_index_out_of_range_raises(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )
        with pytest.raises(ValueError, match="out of range"):
            execute_trace(graph, row_index=999)

    def test_three_node_linear_chain(self, tmp_path):
        """Three-node chain exercises full pipeline logic."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1, 2], "b": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = df.with_columns(c=pl.col('a') + pl.col('b'))"),
                    _transform_node("t2", "df = df.with_columns(d=pl.col('c') * 2)"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )
        result = execute_trace(graph, row_index=0)
        assert len(result.steps) == 3
        assert result.output_value["a"] == 1
        assert result.output_value["c"] == 11
        assert result.output_value["d"] == 22

    def test_column_not_in_output(self, tmp_path):
        """Tracing a column that doesn't exist returns None output_value."""
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


# ===========================================================================
# build_waterfall — comprehensive coverage
# ===========================================================================


class TestBuildWaterfall:
    """Cover all paths in _trace_waterfall.build_waterfall."""

    def test_less_than_3_steps_returns_none(self):
        assert build_waterfall([]) is None
        assert build_waterfall([{"label": "a", "operation": "base", "value": 100}]) is None
        assert (
            build_waterfall(
                [
                    {"label": "a", "operation": "base", "value": 100},
                    {"label": "b", "operation": "multiply", "value": 1.1},
                ]
            )
            is None
        )

    def test_base_multiply_add(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 100},
            {"label": "Factor", "operation": "multiply", "value": 1.5},
            {"label": "Loading", "operation": "add", "value": 20},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert len(result.entries) == 3

        assert result.entries[0].label == "Base"
        assert result.entries[0].operation == "base"
        assert result.entries[0].value == 100.0
        assert result.entries[0].delta == 0.0
        assert result.entries[0].cumulative == 100.0

        assert result.entries[1].label == "Factor"
        assert result.entries[1].operation == "multiply"
        assert result.entries[1].value == 1.5
        assert result.entries[1].delta == 50.0
        assert result.entries[1].cumulative == 150.0

        assert result.entries[2].label == "Loading"
        assert result.entries[2].operation == "add"
        assert result.entries[2].value == 20.0
        assert result.entries[2].delta == 20.0
        assert result.entries[2].cumulative == 170.0

        assert result.final_value == 170.0

    def test_negative_delta_discount(self):
        """Multiply by < 1 produces a negative delta (discount)."""
        steps = [
            {"label": "Base", "operation": "base", "value": 200},
            {"label": "Discount", "operation": "multiply", "value": 0.8},
            {"label": "Fee", "operation": "add", "value": -10},
        ]
        result = build_waterfall(steps)
        assert result is not None

        assert result.entries[1].delta == pytest.approx(-40.0)
        assert result.entries[1].cumulative == pytest.approx(160.0)

        assert result.entries[2].delta == -10.0
        assert result.entries[2].cumulative == pytest.approx(150.0)
        assert result.final_value == pytest.approx(150.0)

    def test_nan_value_returns_none(self):
        steps = [
            {"label": "Base", "operation": "base", "value": float("nan")},
            {"label": "F1", "operation": "multiply", "value": 1.1},
            {"label": "F2", "operation": "add", "value": 5},
        ]
        result = build_waterfall(steps)
        assert result is None

    def test_inf_cumulative_returns_none(self):
        steps = [
            {"label": "Base", "operation": "base", "value": float("inf")},
            {"label": "F1", "operation": "multiply", "value": 2},
            {"label": "F2", "operation": "add", "value": 5},
        ]
        result = build_waterfall(steps)
        assert result is None

    def test_multiply_causing_inf_returns_none(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 1e308},
            {"label": "F1", "operation": "multiply", "value": 1e308},
            {"label": "F2", "operation": "add", "value": 5},
        ]
        result = build_waterfall(steps)
        assert result is None

    def test_invalid_value_returns_none(self):
        """Non-numeric value that can't be converted returns None."""
        steps = [
            {"label": "Base", "operation": "base", "value": "not_a_number"},
            {"label": "F1", "operation": "multiply", "value": 1.1},
            {"label": "F2", "operation": "add", "value": 5},
        ]
        result = build_waterfall(steps)
        assert result is None

    def test_none_value_returns_none(self):
        steps = [
            {"label": "Base", "operation": "base", "value": None},
            {"label": "F1", "operation": "multiply", "value": 1.1},
            {"label": "F2", "operation": "add", "value": 5},
        ]
        result = build_waterfall(steps)
        assert result is None

    def test_unknown_operation_zero_delta(self):
        """Unknown operation type produces delta=0, cumulative unchanged."""
        steps = [
            {"label": "Base", "operation": "base", "value": 100},
            {"label": "Unknown", "operation": "unknown_op", "value": 50},
            {"label": "Add", "operation": "add", "value": 10},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert result.entries[1].delta == 0.0
        assert result.entries[1].cumulative == 100.0  # unchanged
        assert result.entries[2].cumulative == 110.0

    def test_missing_keys_use_defaults(self):
        """Steps with missing keys use defaults (label='', operation='base', value=0)."""
        steps = [{}, {}, {}]
        result = build_waterfall(steps)
        assert result is not None
        assert result.entries[0].label == ""
        assert result.entries[0].value == 0.0

    def test_final_value_equals_last_cumulative(self):
        steps = [
            {"label": "A", "operation": "base", "value": 50},
            {"label": "B", "operation": "multiply", "value": 2},
            {"label": "C", "operation": "add", "value": 25},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert result.final_value == result.entries[-1].cumulative

    def test_exception_in_step_returns_none(self):
        """An unprocessable step list triggers the exception handler."""
        # Pass non-dict items to trigger AttributeError on .get()
        steps = [1, 2, 3]  # type: ignore[list-item]
        result = build_waterfall(steps)
        assert result is None

    def test_multiple_multiplies(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 100},
            {"label": "F1", "operation": "multiply", "value": 1.1},
            {"label": "F2", "operation": "multiply", "value": 1.2},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert result.entries[1].cumulative == pytest.approx(110.0)
        assert result.entries[2].cumulative == pytest.approx(132.0)
        assert result.final_value == pytest.approx(132.0)

    def test_multiple_adds(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 100},
            {"label": "A1", "operation": "add", "value": 10},
            {"label": "A2", "operation": "add", "value": 20},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert result.final_value == pytest.approx(130.0)

    def test_string_numeric_value_converts(self):
        """String values that can convert to float work fine."""
        steps = [
            {"label": "Base", "operation": "base", "value": "100"},
            {"label": "F1", "operation": "multiply", "value": "1.5"},
            {"label": "F2", "operation": "add", "value": "10"},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert result.final_value == pytest.approx(160.0)


# ===========================================================================
# export_trace — comprehensive coverage
# ===========================================================================


class TestExportTrace:
    """Cover all paths in _trace_export.export_trace."""

    def _make_trace_result(
        self,
        column="premium",
        steps=None,
        execution_ms=5.0,
        total_nodes=3,
    ):
        """Helper to build a mock TraceResult for export_trace."""
        if steps is None:
            steps = []

        @dataclass
        class MockSchemaDiff:
            columns_added: list[str]
            columns_removed: list[str]
            columns_modified: list[str]
            columns_passed: list[str]

        @dataclass
        class MockTraceStep:
            node_id: str
            node_name: str
            node_type: str
            schema_diff: Any
            input_values: dict[str, Any]
            output_values: dict[str, Any]
            expression: dict[str, Any] | None = None
            calculation: dict[str, Any] | None = None

        @dataclass
        class MockTraceResult:
            column: str | None
            output_value: Any
            target_node_id: str
            row_index: int
            steps: list
            execution_ms: float
            total_nodes_in_pipeline: int

        mock_steps = []
        for s in steps:
            sd = MockSchemaDiff(
                columns_added=s.get("added", []),
                columns_removed=s.get("removed", []),
                columns_modified=s.get("modified", []),
                columns_passed=s.get("passed", []),
            )
            mock_steps.append(
                MockTraceStep(
                    node_id=s.get("node_id", "n"),
                    node_name=s.get("node_name", "Node"),
                    node_type=s.get("node_type", "polars"),
                    schema_diff=sd,
                    input_values=s.get("input_values", {}),
                    output_values=s.get("output_values", {}),
                    expression=s.get("expression"),
                    calculation=s.get("calculation"),
                )
            )

        return MockTraceResult(
            column=column,
            output_value=100.5,
            target_node_id="target",
            row_index=0,
            steps=mock_steps,
            execution_ms=execution_ms,
            total_nodes_in_pipeline=total_nodes,
        )

    def test_header_extraction(self):
        tr = self._make_trace_result(column="premium")
        result = export_trace(tr)
        assert result["header"]["column"] == "premium"
        assert result["header"]["output_value"] == 100.5
        assert result["header"]["target_node_id"] == "target"
        assert result["header"]["row_index"] == 0

    def test_formula_from_expression(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "t",
                    "added": ["premium"],
                    "expression": {
                        "expression_text": "base * factor",
                        "referenced_columns": ["base", "factor"],
                    },
                    "calculation": None,
                }
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == "base * factor"
        assert result["formula"]["substituted"] == ""

    def test_formula_from_calculation(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "t",
                    "added": ["premium"],
                    "expression": None,
                    "calculation": {
                        "expression_text": "base * factor",
                        "substituted_text": "100 * 1.5",
                    },
                }
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == "base * factor"
        assert result["formula"]["substituted"] == "100 * 1.5"

    def test_formula_with_both_expression_and_calculation(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "t",
                    "added": ["premium"],
                    "expression": {"expression_text": "x + y"},
                    "calculation": {"substituted_text": "1 + 2", "expression_text": "x + y"},
                }
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == "x + y"
        assert result["formula"]["substituted"] == "1 + 2"

    def test_formula_with_no_expression_or_calculation(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "t",
                    "added": ["premium"],
                    "expression": None,
                    "calculation": None,
                }
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == ""
        assert result["formula"]["substituted"] == ""

    def test_missing_target_step(self):
        """When no step adds/modifies the target column, formula is empty."""
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "t",
                    "added": ["other_col"],  # not premium
                    "expression": {"expression_text": "something"},
                }
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == ""
        assert result["formula"]["substituted"] == ""

    def test_formula_from_modified_column(self):
        """Target step found via columns_modified."""
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "t",
                    "modified": ["premium"],
                    "expression": {"expression_text": "old_val * 2"},
                    "calculation": {"substituted_text": "50 * 2"},
                }
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == "old_val * 2"

    def test_sources_with_referenced_columns(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "src",
                    "node_name": "Source",
                    "added": ["base", "factor"],
                    "output_values": {"base": 100, "factor": 1.5},
                },
                {
                    "node_id": "t",
                    "node_name": "Calc",
                    "added": ["premium"],
                    "input_values": {"base": 100, "factor": 1.5},
                    "output_values": {"premium": 150},
                    "expression": {
                        "expression_text": "base * factor",
                        "referenced_columns": ["base", "factor"],
                    },
                },
            ],
        )
        result = export_trace(tr)
        sources = result["sources"]
        assert len(sources) == 2
        col_names = [s["column"] for s in sources]
        assert "base" in col_names
        assert "factor" in col_names

        base_src = next(s for s in sources if s["column"] == "base")
        assert base_src["value"] == 100
        assert base_src["origin"] == "Source"

    def test_sources_from_calculation_referenced_columns(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "src",
                    "node_name": "Source",
                    "added": ["x"],
                    "output_values": {"x": 5},
                },
                {
                    "node_id": "t",
                    "added": ["premium"],
                    "input_values": {"x": 5},
                    "output_values": {"premium": 10},
                    "expression": None,
                    "calculation": {
                        "expression_text": "x * 2",
                        "referenced_columns": ["x"],
                    },
                },
            ],
        )
        result = export_trace(tr)
        assert len(result["sources"]) == 1
        assert result["sources"][0]["column"] == "x"

    def test_sources_empty_when_no_target_step(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[{"node_id": "t", "added": ["other"]}],
        )
        result = export_trace(tr)
        assert result["sources"] == []

    def test_data_flow_step_summaries(self):
        tr = self._make_trace_result(
            column="premium",
            steps=[
                {
                    "node_id": "src",
                    "node_name": "Source",
                    "node_type": "dataSource",
                    "added": ["x", "y"],
                    "removed": [],
                },
                {
                    "node_id": "t",
                    "node_name": "Transform",
                    "node_type": "polars",
                    "added": ["premium"],
                    "removed": ["y"],
                },
            ],
        )
        result = export_trace(tr)
        flow = result["data_flow"]
        assert len(flow) == 2

        assert flow[0]["node_id"] == "src"
        assert flow[0]["node_name"] == "Source"
        assert flow[0]["node_type"] == "dataSource"
        assert flow[0]["columns_added"] == ["x", "y"]
        assert flow[0]["columns_removed"] == []

        assert flow[1]["columns_added"] == ["premium"]
        assert flow[1]["columns_removed"] == ["y"]

    def test_metadata(self):
        tr = self._make_trace_result(
            execution_ms=42.5,
            total_nodes=7,
            steps=[{"node_id": "t", "added": ["x"]}],
        )
        result = export_trace(tr)
        meta = result["metadata"]
        assert meta["step_count"] == 1
        assert meta["execution_ms"] == 42.5
        assert meta["total_nodes_in_pipeline"] == 7

    def test_full_export_structure(self):
        """Verify all top-level keys are present."""
        tr = self._make_trace_result(steps=[])
        result = export_trace(tr)
        assert set(result.keys()) == {"header", "formula", "sources", "data_flow", "metadata"}

    def test_no_column_set(self):
        """When column is None, no target_step is found."""
        tr = self._make_trace_result(
            column=None,
            steps=[
                {"node_id": "t", "added": ["x"]},
            ],
        )
        result = export_trace(tr)
        assert result["formula"]["expression"] == ""
        assert result["sources"] == []

    def test_empty_steps(self):
        tr = self._make_trace_result(steps=[])
        result = export_trace(tr)
        assert result["data_flow"] == []
        assert result["metadata"]["step_count"] == 0
        assert result["sources"] == []


# ===========================================================================
# Waterfall integration with execute_trace
# ===========================================================================


class TestWaterfallIntegration:
    """Cover waterfall building within execute_trace."""

    def test_waterfall_built_for_modified_column_chain(self, tmp_path):
        """A chain of modifications to a column produces waterfall data.

        PIN REVISION (C8): this test previously asserted only "doesn't
        crash", which let the waterfall feed post-step cumulative values
        in as multiply factors (100 x 150 = 15,000) without any test
        noticing.  It now pins the value-derived arithmetic: implied
        factor / delta per step and exact reconciliation with the traced
        output value.
        """
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [100], "factor1": [1.5], "loading": [20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "step1",
                        "df = df.with_columns(premium=pl.col('premium') * pl.col('factor1'))",
                    ),
                    _transform_node(
                        "step2",
                        "df = df.with_columns(premium=pl.col('premium') + pl.col('loading'))",
                    ),
                ],
                "edges": [_edge("src", "step1"), _edge("step1", "step2")],
            }
        )
        result = execute_trace(graph, row_index=0, column="premium")

        assert isinstance(result.waterfall, list)
        assert [e["label"] for e in result.waterfall] == ["src", "step1", "step2"]
        base, mult, add = result.waterfall

        assert base["operation"] == "base"
        assert base["cumulative"] == pytest.approx(100.0)

        assert mult["operation"] == "multiply"
        assert mult["value"] == pytest.approx(1.5)  # implied factor, not 150
        assert mult["delta"] == pytest.approx(50.0)
        assert mult["cumulative"] == pytest.approx(150.0)

        assert add["operation"] == "add"
        assert add["value"] == pytest.approx(20.0)
        assert add["cumulative"] == pytest.approx(170.0)

        # C8 invariant: the chain reconciles with the traced output value.
        assert result.waterfall[-1]["cumulative"] == result.output_value

    def test_waterfall_none_without_column(self, tmp_path):
        """Without column param, waterfall is None."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )
        result = execute_trace(graph, row_index=0)
        assert result.waterfall is None

    def test_waterfall_none_with_too_few_steps(self, tmp_path):
        """With fewer than 3 steps, waterfall is None."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = df.with_columns(x=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        result = execute_trace(graph, row_index=0, column="x")
        # Only 2 steps, waterfall needs >= 3
        assert result.waterfall is None


# ===========================================================================
# TraceStep.row_data property
# ===========================================================================


class TestTraceStepRowData:
    def test_row_data_is_alias_for_output_values(self):
        step = TraceStep(
            node_id="n",
            node_name="Node",
            node_type="polars",
            schema_diff=SchemaDiff([], [], [], []),
            input_values={"a": 1},
            output_values={"b": 2},
        )
        assert step.row_data == {"b": 2}
        assert step.row_data is step.output_values


# ===========================================================================
# WaterfallEntry and WaterfallResult dataclass contracts
# ===========================================================================


class TestWaterfallDataclasses:
    def test_waterfall_entry_fields(self):
        e = WaterfallEntry(label="Base", operation="base", value=100.0, delta=0.0, cumulative=100.0)
        assert e.label == "Base"
        assert e.operation == "base"
        assert e.value == 100.0
        assert e.delta == 0.0
        assert e.cumulative == 100.0

    def test_waterfall_result_fields(self):
        entries = [
            WaterfallEntry("A", "base", 50.0, 0.0, 50.0),
            WaterfallEntry("B", "multiply", 2.0, 50.0, 100.0),
            WaterfallEntry("C", "add", 10.0, 10.0, 110.0),
        ]
        r = WaterfallResult(entries=entries, final_value=110.0)
        assert len(r.entries) == 3
        assert r.final_value == 110.0


# ===========================================================================
# SchemaDiff dataclass
# ===========================================================================


class TestSchemaDiffDataclass:
    def test_all_fields_accessible(self):
        sd = SchemaDiff(
            columns_added=["a"],
            columns_removed=["b"],
            columns_modified=["c"],
            columns_passed=["d"],
        )
        assert sd.columns_added == ["a"]
        assert sd.columns_removed == ["b"]
        assert sd.columns_modified == ["c"]
        assert sd.columns_passed == ["d"]

    def test_empty_schema_diff(self):
        sd = SchemaDiff([], [], [], [])
        assert sd.columns_added == []
        assert sd.columns_removed == []
        assert sd.columns_modified == []
        assert sd.columns_passed == []
