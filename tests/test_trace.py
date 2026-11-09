"""Tests for haute.trace - execution trace / data lineage."""

from __future__ import annotations

import itertools
import json
import math
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl
import pytest

import haute._trace_correlation as trace_correlation
from haute._trace_correlation import _find_matching_row, _trace_values_match
from haute._trace_lineage import ValueLineage
from haute._trace_waterfall import WaterfallEntry, WaterfallResult
from haute.trace import (
    SchemaDiff,
    TraceResult,
    TraceStep,
    _carries_column,
    _compute_schema_diff,
    _find_target_row_index,
    _jsonify_row,
    _prune_to_column_relevance,
    execute_trace,
    trace_result_to_dict,
)
from tests._project import use_project
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
        # The candidates differ in a column the child does not carry, so the
        # tie is genuine (identical rows are shown, see test_trace_identical_rows).
        df = pl.DataFrame(
            {
                "policy_id": [10, 10],
                "premium": [100.0, 100.0],
                "region": ["north", "south"],
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

    def test_parent_row_ambiguity_is_serialized_on_trace_result(self, tmp_path):
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
                        "regions",
                        "df = source.select('region').unique(maintain_order=True)",
                    ),
                ],
                "edges": [_edge("source", "regions")],
            }
        )

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="regions",
            row_values={"region": "north"},
        )

        assert {step.node_id for step in result.steps} == {"regions"}
        assert len(result.correlation_diagnostics) == 1
        diagnostic = result.correlation_diagnostics[0]
        # The program carries only the region, so the strict lineage lookup finds
        # both north source rows, which differ, and reports the ambiguity.
        assert diagnostic["code"] == "ambiguous_row_match"
        assert diagnostic["reason"] == "duplicate_exact_match"
        assert diagnostic["node_id"] == "source"
        assert diagnostic["child_node_id"] == "regions"
        assert diagnostic["match_strategy"] == "exact"
        assert diagnostic["match_columns"] == ["region"]
        assert diagnostic["ignored_columns"] == []
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
        """A calculated column keeps its creating node and the ancestors that feed it."""
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

        # 'y' is created at t from x, which mid carries from src.
        ids = [s.node_id for s in result.steps]
        assert ids == ["src", "mid", "t"]
        assert all(s.column_relevant for s in result.steps)
        assert [s.contributed_columns for s in result.steps] == [["x"], [], ["y"]]

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

        pruned = _prune_to_column_relevance(
            steps, "premium", parents_of, node_map={}, lineage=ValueLineage(frozenset(), {}, {})
        )

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

    def test_row_id_from_a_request_table(self):
        """The trace's row identity is a request table's row id column, as the row carries it."""
        from haute.trace import request_row_identity

        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "src",
                    "nodeType": "apiInput",
                    "config": {
                        "path": "quotes.json",
                        "tables": [
                            {"path": "$[:]", "label": "quotes", "columns": []},
                            {
                                "path": "$[:].drivers[:]",
                                "label": "drivers",
                                "row_id_column": "driver_id",
                                "columns": [],
                            },
                            {
                                "path": "$[:].policies[:]",
                                "label": "policies",
                                "row_id_column": "policy_id",
                                "columns": [],
                            },
                        ],
                    },
                },
            }
        )

        assert request_row_identity([node], {"policy_id": 200, "x": 2}) == ("policy_id", 200)
        assert request_row_identity([node], {"x": 2}) == (None, None)

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
        use_project(tmp_path)
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


# ===========================================================================
# _jsonify_row — uncovered paths
# ===========================================================================


class TestJsonifyRowEdgeCases:
    """Cover temporal conversion and recursively JSON-safe nested values."""

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

    def test_list_preserved_as_structured_json(self):
        row = {"lst": [1, 2, 3]}
        result = _jsonify_row(row)
        assert result["lst"] == [1, 2, 3]

    def test_dict_preserved_as_structured_json(self):
        row = {"d": {"nested": "value"}}
        result = _jsonify_row(row)
        assert result["d"] == {"nested": "value"}

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
# _carries_column — coverage for standalone function
# ===========================================================================


class TestCarriesColumn:
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
        assert _carries_column(s, "x") is True

    def test_tags_modified(self):
        s = self._make_step("a", modified=["x"])
        assert _carries_column(s, "x") is True

    def test_tags_passed(self):
        s = self._make_step("a", passed=["x"])
        assert _carries_column(s, "x") is True

    def test_tags_in_output_values(self):
        s = self._make_step("a", output={"x": 1})
        assert _carries_column(s, "x") is True

    def test_tags_irrelevant(self):
        s = self._make_step("a", added=["y"])
        assert _carries_column(s, "x") is False

    def test_multiple_steps_mixed(self):
        s1 = self._make_step("a", added=["x"])
        s2 = self._make_step("b", added=["y"])
        s3 = self._make_step("c", passed=["x"])
        assert [_carries_column(s, "x") for s in (s1, s2, s3)] == [True, False, True]


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
                    node_type="dataInput",
                    schema_diff=SchemaDiff(
                        columns_added=["x", "y"],
                        columns_removed=[],
                        columns_modified=[],
                        columns_passed=[],
                    ),
                    input_values={},
                    output_values={"x": 1, "y": 2},
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
        assert s0["node_type"] == "dataInput"
        assert s0["schema_diff"]["columns_added"] == ["x", "y"]
        assert s0["input_values"] == {}
        assert s0["output_values"] == {"x": 1, "y": 2}
        assert "execution_ms" not in s0
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
                    _transform_node("t", "df = src.with_columns(z=pl.col('x') + pl.col('y'))"),
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
                    _transform_node("mid", "df = src"),  # passes x through
                    _transform_node("end", "df = mid"),  # also passes x through
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
                    _transform_node("mid", "df = src"),
                    _transform_node("end", "df = mid"),
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
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
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
                "nodes": [_source_node("src", str(p)), _transform_node("t", "df = src")],
                "edges": [_edge("src", "t")],
            }
        )
        r1 = execute_trace(g1, row_index=0)
        assert "y" not in r1.output_value

        g2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') * 3)"),
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

    def test_a_walk_without_the_target_frame_is_a_broken_invariant(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The walk raises a node's failure and always collects the target, so a
        target without a frame is never traced from positional guesses."""
        import haute.trace as trace_module

        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("target", "df = src.with_columns(y=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "target")],
            }
        )
        node_map = {node.id: node for node in graph.nodes}

        monkeypatch.setattr(
            trace_module,
            "_materialize_eager_outputs",
            lambda **_kwargs: (
                {"src": pl.DataFrame({"x": [1]})},
                ["src", "target"],
                {"target": ["src"]},
                node_map,
                None,
            ),
        )
        trace_module._cache.clear()
        try:
            with pytest.raises(RuntimeError, match="'target' produced no frame"):
                execute_trace(graph, row_index=0, target_node_id="target", row_values={"x": 1})
        finally:
            trace_module._cache.clear()

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
                    _transform_node("t1", "df = src.with_columns(c=pl.col('a') + pl.col('b'))"),
                    _transform_node("t2", "df = t1.with_columns(d=pl.col('c') * 2)"),
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
                        "df = src.with_columns(premium=pl.col('premium') * pl.col('factor1'))",
                    ),
                    _transform_node(
                        "step2",
                        "df = step1.with_columns(premium=pl.col('premium') + pl.col('loading'))",
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
                    _transform_node("t", "df = src.with_columns(x=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        result = execute_trace(graph, row_index=0, column="x")
        # Only 2 steps, waterfall needs >= 3
        assert result.waterfall is None


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
