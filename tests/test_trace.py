"""Tests for haute.trace - execution trace / data lineage."""

from __future__ import annotations

import itertools
import math

import polars as pl
import pytest

import haute._trace_correlation as trace_correlation
from haute._trace_correlation import _find_matching_row, _trace_values_match
from haute.trace import (
    SchemaDiff,
    TraceResult,
    TraceStep,
    _compute_schema_diff,
    _find_target_row_index,
    _jsonify_row,
    _prune_to_column_relevance,
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
from tests.conftest import make_ready_file_input_config
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# _jsonify_row
# ---------------------------------------------------------------------------


NON_FINITE_FLOAT_TYPE = "non_finite_float"
NAN_SENTINEL = {"__haute_type__": NON_FINITE_FLOAT_TYPE, "value": "nan"}
INF_SENTINEL = {"__haute_type__": NON_FINITE_FLOAT_TYPE, "value": "inf"}
NEG_INF_SENTINEL = {"__haute_type__": NON_FINITE_FLOAT_TYPE, "value": "-inf"}
MAX_SAFE_INTEGER = 2**53 - 1


class TestJsonifyRow:
    def test_primitives_preserved(self):
        row = {"a": 1, "b": 2.5, "c": "hello", "d": True, "e": None}
        result = _jsonify_row(row)
        assert result == row

    def test_nan_replaced_with_non_finite_sentinel(self):
        row = {"a": float("nan")}
        result = _jsonify_row(row)
        assert result["a"] == NAN_SENTINEL

    def test_positive_inf_replaced_with_non_finite_sentinel(self):
        row = {"a": float("inf")}
        result = _jsonify_row(row)
        assert result["a"] == INF_SENTINEL

    def test_negative_inf_replaced_with_non_finite_sentinel(self):
        row = {"a": float("-inf")}
        result = _jsonify_row(row)
        assert result["a"] == NEG_INF_SENTINEL

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
        assert result["nan_val"] == NAN_SENTINEL
        assert result["inf_val"] == INF_SENTINEL
        assert result["neg_inf"] == NEG_INF_SENTINEL
        assert result["text"] == "hello"
        assert result["none_val"] is None

    def test_unsafe_integers_are_stringified(self):
        result = _jsonify_row(
            {
                "safe": MAX_SAFE_INTEGER,
                "unsafe": MAX_SAFE_INTEGER + 1,
                "negative_unsafe": -(MAX_SAFE_INTEGER + 1),
            }
        )
        assert result["safe"] == MAX_SAFE_INTEGER
        assert result["unsafe"] == str(MAX_SAFE_INTEGER + 1)
        assert result["negative_unsafe"] == str(-(MAX_SAFE_INTEGER + 1))

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


class TestTraceJsonSafeRowMatching:
    def test_large_integer_string_from_frontend_matches_original_int(self):
        unsafe = MAX_SAFE_INTEGER + 1

        assert _trace_values_match(unsafe, str(unsafe))
        assert not _trace_values_match(unsafe, str(unsafe + 1))

    def test_non_finite_sentinels_match_only_corresponding_float_values(self):
        assert _trace_values_match(math.nan, NAN_SENTINEL)
        assert _trace_values_match(math.inf, INF_SENTINEL)
        assert _trace_values_match(-math.inf, NEG_INF_SENTINEL)

        assert not _trace_values_match(math.nan, None)
        assert not _trace_values_match(math.inf, None)
        assert not _trace_values_match(-math.inf, None)
        assert not _trace_values_match(math.inf, NEG_INF_SENTINEL)

    def test_target_row_lookup_accepts_json_safe_frontend_values(self):
        unsafe = MAX_SAFE_INTEGER + 1
        df = pl.DataFrame(
            {
                "id": [unsafe, unsafe + 1, unsafe + 2, unsafe + 3],
                "value": [None, math.nan, math.inf, -math.inf],
            }
        )

        assert _find_target_row_index(df, {"id": str(unsafe)}) == 0
        assert _find_target_row_index(df, {"value": None}) == 0
        assert _find_target_row_index(df, {"value": NAN_SENTINEL}) == 1
        assert _find_target_row_index(df, {"value": INF_SENTINEL}) == 2
        assert _find_target_row_index(df, {"value": NEG_INF_SENTINEL}) == 3

    def test_target_row_lookup_fails_loud_on_duplicate_matches(self):
        df = pl.DataFrame(
            {
                "x": [1, 1, 2],
                "y": [10, 10, 30],
            }
        )

        with pytest.raises(ValueError, match="ambiguous"):
            _find_target_row_index(df, {"x": 1, "y": 10})

        assert _find_target_row_index(df, {"x": 2, "y": 30}) == 2

    def test_parent_row_matching_accepts_json_safe_frontend_values(self):
        unsafe = MAX_SAFE_INTEGER + 1
        df = pl.DataFrame(
            {
                "id": [unsafe, unsafe + 1, unsafe + 2],
                "value": [math.nan, None, math.inf],
            }
        )

        row, idx = _find_matching_row(df, {"id": str(unsafe), "value": NAN_SENTINEL})
        assert idx == 0
        assert row == {"id": str(unsafe), "value": NAN_SENTINEL}

        row, idx = _find_matching_row(df, {"value": None})
        assert idx == 1
        assert row == {"id": str(unsafe + 1), "value": None}

        row, idx = _find_matching_row(df, {"value": INF_SENTINEL})
        assert idx == 2
        assert row == {"id": str(unsafe + 2), "value": INF_SENTINEL}

    def test_parent_row_matching_reports_duplicate_exact_matches_without_selecting_first(self):
        df = pl.DataFrame(
            {
                "policy_id": [10, 10],
                "premium": [100.0, 100.0],
            }
        )
        diagnostics: list[dict[str, object]] = []

        row, idx = _find_matching_row(
            df,
            {"policy_id": 10, "premium": 100.0},
            diagnostics=diagnostics,
            node_id="source",
            child_node_id="rating",
        )

        assert row is None
        assert idx == -1
        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["code"] == "ambiguous_row_match"
        assert diagnostic["reason"] == "duplicate_exact_match"
        assert diagnostic["node_id"] == "source"
        assert diagnostic["child_node_id"] == "rating"
        assert diagnostic["match_strategy"] == "exact"
        assert diagnostic["match_columns"] == ["policy_id", "premium"]
        assert diagnostic["ignored_columns"] == []
        assert diagnostic["matched_row_count"] == 2
        assert diagnostic["matched_row_indices"] == [0, 1]

    def test_parent_row_matching_reports_competing_relaxed_column_sets(self):
        df = pl.DataFrame(
            {
                "a": [1, 1],
                "b": [2, 99],
                "c": [99, 3],
            }
        )
        diagnostics: list[dict[str, object]] = []

        row, idx = _find_matching_row(
            df,
            {"a": 1, "b": 2, "c": 3},
            diagnostics=diagnostics,
            node_id="source",
            child_node_id="aggregate",
        )

        assert row is None
        assert idx == -1
        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["code"] == "ambiguous_row_match"
        assert diagnostic["reason"] == "relaxed_match_ambiguous"
        assert diagnostic["match_strategy"] == "relaxed"
        assert diagnostic["matched_row_count"] == 2
        assert diagnostic["matched_row_indices"] == [0, 1]

    def test_wide_relaxed_parent_row_no_match_stays_within_operation_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        columns = [f"c{i}" for i in range(48)]
        df = pl.DataFrame({column: [0, 1] for column in columns})
        child_row = dict.fromkeys(columns, 999)
        subset_budget = 128
        enumerated_subsets = 0

        def guarded_combinations(iterable, width):
            nonlocal enumerated_subsets
            for combo in itertools.combinations(iterable, width):
                enumerated_subsets += 1
                if enumerated_subsets > subset_budget:
                    raise AssertionError("relaxed row matching exceeded the bounded subset budget")
                yield combo

        monkeypatch.setattr(
            trace_correlation,
            "combinations",
            guarded_combinations,
            raising=False,
        )

        row, idx = _find_matching_row(df, child_row)

        assert row is None
        assert idx == -1
        assert enumerated_subsets <= subset_budget

    @pytest.mark.parametrize(
        ("child_row", "expected_idx", "expected_row"),
        [
            (
                {"policy_id": 102, "region": "north", "tier": "silver", "premium": 999},
                1,
                {"policy_id": 102, "region": "north", "tier": "silver", "premium": 200},
            ),
            (
                {"policy_id": 103, "region": "south", "tier": "gold", "premium": 999},
                2,
                {"policy_id": 103, "region": "south", "tier": "gold", "premium": 300},
            ),
        ],
    )
    def test_relaxed_parent_row_matching_preserves_common_partial_matches(
        self,
        child_row,
        expected_idx,
        expected_row,
    ):
        df = pl.DataFrame(
            {
                "policy_id": [101, 102, 103],
                "region": ["north", "north", "south"],
                "tier": ["gold", "silver", "gold"],
                "premium": [100, 200, 300],
            }
        )

        row, idx = _find_matching_row(df, child_row)

        assert idx == expected_idx
        assert row == expected_row

    def test_relaxed_parent_row_matching_reports_clear_ambiguity_reason(self):
        df = pl.DataFrame(
            {
                "policy_id": [101, 102],
                "region": ["north", "north"],
                "premium": [100, 200],
            }
        )
        diagnostics: list[dict[str, object]] = []

        row, idx = _find_matching_row(
            df,
            {"policy_id": 999, "region": "north", "premium": 999},
            diagnostics=diagnostics,
            node_id="source",
            child_node_id="aggregate",
        )

        assert row is None
        assert idx == -1
        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic["code"] == "ambiguous_row_match"
        assert diagnostic["reason"] == "relaxed_match_ambiguous"
        assert diagnostic["match_strategy"] == "relaxed"
        assert diagnostic["matched_row_indices"] == [0, 1]
        message = str(diagnostic["message"])
        assert (
            "Row correlation for node 'source' for child node 'aggregate' is ambiguous" in message
        )
        assert "2 relaxed matches" in message

    def test_relaxed_parent_row_ambiguity_is_serialized_on_trace_result(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "region": ["north", "north", "south"],
                "premium": [10, 20, 40],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("source", str(p)),
                    _transform_node(
                        "aggregate",
                        "df = source.group_by('region').agg(pl.col('premium').sum())",
                    ),
                ],
                "edges": [_edge("source", "aggregate")],
            }
        )

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="aggregate",
            column="premium",
            row_values={"region": "north", "premium": 30},
        )

        assert {step.node_id for step in result.steps} == {"aggregate"}
        assert len(result.correlation_diagnostics) == 1
        diagnostic = result.correlation_diagnostics[0]
        assert diagnostic["code"] == "ambiguous_row_match"
        assert diagnostic["reason"] == "relaxed_match_ambiguous"
        assert diagnostic["node_id"] == "source"
        assert diagnostic["child_node_id"] == "aggregate"
        assert diagnostic["match_strategy"] == "relaxed"
        assert diagnostic["match_columns"] == ["region"]
        assert diagnostic["ignored_columns"] == ["premium"]
        assert diagnostic["matched_row_count"] == 2
        assert diagnostic["matched_row_indices"] == [0, 1]
        assert "ambiguous" in str(diagnostic["message"])

        payload = trace_result_to_dict(result)
        assert payload["correlation_diagnostics"] == [diagnostic]


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

    def test_qualified_parent_variants_are_not_removed_from_unqualified_child(self):
        diff = _compute_schema_diff(
            {"left.score": 1, "right.score": 2},
            {"score": 3},
            provenance_aliases={"left.score": "score", "right.score": "score"},
        )
        assert diff.columns_removed == []
        assert diff.columns_added == []
        assert diff.columns_modified == ["score"]

    def test_real_dotted_column_is_not_treated_as_a_provenance_alias(self):
        diff = _compute_schema_diff(
            {"profile.score": 1},
            {"profile.score": 1, "score": 2},
        )
        assert diff.columns_added == ["score"]
        assert diff.columns_passed == ["profile.score"]


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
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 10)"),
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
                    _transform_node("t", "df = src"),
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
                    _transform_node("mid", "df = src"),
                    # adds 'y' - column_relevant, ancestors kept for calc path
                    _transform_node("t", "df = mid.with_columns(y=pl.col('x') * 2)"),
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

    def test_trace_modified_column_keeps_branch_feeding_later_assignment(self, tmp_path):
        """A later assignment to the traced column keeps branches used by it."""
        base_path = tmp_path / "base.parquet"
        factor_path = tmp_path / "factor.parquet"
        pl.DataFrame({"quote_id": [1], "base": [100]}).write_parquet(base_path)
        pl.DataFrame({"quote_id": [1], "factor": [1.2]}).write_parquet(factor_path)

        graph = _g(
            {
                "nodes": [
                    _source_node("base", str(base_path)),
                    _transform_node("calc", "df = base.with_columns(premium=pl.col('base') * 10)"),
                    _source_node("factor", str(factor_path)),
                    _transform_node("join", "df = calc.join(factor, on='quote_id', how='left')"),
                    _transform_node(
                        "final",
                        "df = join.with_columns("
                        "(pl.col('premium') * pl.col('factor')).alias('premium'))",
                    ),
                    _transform_node("sink", "df = final"),
                ],
                "edges": [
                    _edge("base", "calc"),
                    _edge("calc", "join"),
                    _edge("factor", "join"),
                    _edge("join", "final"),
                    _edge("final", "sink"),
                ],
            }
        )
        result = execute_trace(graph, column="premium")

        ids = [s.node_id for s in result.steps]
        assert set(ids) == {"base", "calc", "factor", "join", "final", "sink"}
        assert result.steps[-1].output_values["premium"] == 1200.0

    def test_relevance_pruning_keeps_branch_feeding_later_modification(self):
        """Pruning keeps non-column branches referenced by later modifications."""

        def diff(
            *,
            added: list[str] | None = None,
            modified: list[str] | None = None,
            passed: list[str] | None = None,
        ) -> SchemaDiff:
            return SchemaDiff(
                columns_added=added or [],
                columns_removed=[],
                columns_modified=modified or [],
                columns_passed=passed or [],
            )

        def step(
            node_id: str,
            schema_diff: SchemaDiff,
            output_values: dict,
            *,
            expression: dict | None = None,
        ) -> TraceStep:
            return TraceStep(
                node_id=node_id,
                node_name=node_id.title(),
                node_type="transform",
                schema_diff=schema_diff,
                input_values={},
                output_values=output_values,
                expression=expression,
            )

        steps = [
            step("base", diff(added=["base"]), {"base": 100}),
            step(
                "calc",
                diff(added=["premium"]),
                {"base": 100, "premium": 1000},
                expression={"referenced_columns": ["base"]},
            ),
            step("factor", diff(added=["factor"]), {"factor": 1.2}),
            step("join", diff(passed=["premium", "factor"]), {"premium": 1000, "factor": 1.2}),
            step(
                "final",
                diff(modified=["premium"]),
                {"premium": 1200, "factor": 1.2},
                expression={"referenced_columns": ["premium", "factor"]},
            ),
            step("sink", diff(passed=["premium"]), {"premium": 1200}),
        ]
        parents_of = {
            "calc": ["base"],
            "join": ["calc", "factor"],
            "final": ["join"],
            "sink": ["final"],
        }

        pruned = _prune_to_column_relevance(steps, "premium", parents_of, node_map={})

        assert [step.node_id for step in pruned] == [
            "base",
            "calc",
            "factor",
            "join",
            "final",
            "sink",
        ]

    def test_trace_column_passthrough_keeps_path(self, tmp_path):
        """A pass-through column traces back through all nodes that carry it."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "z": [99]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid", "df = src"),  # passes x through
                    _transform_node("t", "df = mid.with_columns(y=pl.col('x') * 2)"),
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
        # Use a parquet-backed apiInput so the executor reads data directly
        # without needing the v2 JSON cache infrastructure.
        p = tmp_path / "data.parquet"
        pl.DataFrame({"policy_id": [100, 200, 300], "x": [1, 2, 3]}).write_parquet(p)

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
                    _transform_node("t", "df = src"),
                ],
                "edges": [_edge("src", "t", source_handle="src")],
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
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
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
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )
        r1 = execute_trace(graph1, row_index=0)
        assert "y" not in r1.output_value

        graph2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        r2 = execute_trace(graph2, row_index=0)
        assert r2.output_value["y"] == 2

    def test_cache_invalidates_on_preamble_change(self, tmp_path):
        """Changing only graph.preamble must not serve stale trace rows."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)
        code = "df = src.with_columns(y=pl.col('x') * FACTOR)"

        graph1 = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", code)],
                "edges": [_edge("src", "t")],
                "preamble": "FACTOR = 2\n",
            }
        )
        graph2 = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("t", code)],
                "edges": [_edge("src", "t")],
                "preamble": "FACTOR = 3\n",
            }
        )

        r1 = execute_trace(graph1, row_index=0, target_node_id="t", column="y")
        assert r1.output_value == 2

        r2 = execute_trace(graph2, row_index=0, target_node_id="t", column="y")
        assert r2.output_value == 3

    def test_cache_invalidates_on_utility_change(
        self,
        tmp_path,
        monkeypatch,
        _widen_sandbox_root,
    ):
        """Changing imported utility code must not serve stale trace rows."""
        monkeypatch.chdir(tmp_path)
        utility_dir = tmp_path / "utility"
        utility_dir.mkdir()
        (utility_dir / "__init__.py").write_text("", encoding="utf-8")
        helper = utility_dir / "helpers.py"
        helper.write_text("FACTOR = 2\n", encoding="utf-8")

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * FACTOR)"),
                ],
                "edges": [_edge("src", "t")],
                "preamble": "from utility.helpers import FACTOR\n",
                "source_file": str(tmp_path / "pipeline.py"),
            }
        )

        r1 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert r1.output_value == 2

        helper.write_text("FACTOR = 20\n", encoding="utf-8")

        r2 = execute_trace(graph, row_index=0, target_node_id="t", column="y")
        assert r2.output_value == 20

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
        assert "execution_ms" not in d["steps"][0]
        assert d["execution_ms"] == 2.0
