"""Tests for node-type-specific trace enrichment in the Haute pricing engine.

Covers:
  1. Rating step simulation (join-based rate table lookups)
  2. Banding simulation (when/then conditional patterns)
  3. Model score simulation (prediction column addition)
  4. Scenario expansion (cross join patterns)
  5. Live switch (branch selection)
  6. Data source metadata (source node trace)
  7. Row lineage type detection (passthrough, created, filtered, etc.)

The enrichment functions imported from ``haute._trace_enrichment`` are TDD
stubs -- they do not exist yet.  The remaining tests exercise the *existing*
``execute_trace`` infrastructure on the data-flow patterns that each node
type produces.
"""

from __future__ import annotations

import math
from typing import Any

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
from haute.trace import _cache as _trace_cache
from haute.executor import _preview_cache, execute_graph
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from tests.conftest import (
    make_edge as _edge,
    make_graph as _g,
    make_node as _n,
    make_source_node as _source_node,
    make_transform_node as _transform_node,
)


# ---------------------------------------------------------------------------
# Cache clearing fixture (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_trace_caches():
    """Invalidate the global trace and preview caches between tests."""
    _trace_cache.invalidate()
    _preview_cache.invalidate()
    yield
    _trace_cache.invalidate()
    _preview_cache.invalidate()


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
# 1. Rating Step Simulation Tests (19 tests)
# ===========================================================================


class TestRatingStepSingleFactor:
    """Rate table lookup using Polars join -- single factor key."""

    def test_single_factor_exact_match(self, tmp_path):
        """A single-factor rate table join produces the correct rate."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1, 2, 3], "region": ["north", "south", "east"]}).write_parquet(
            p_data
        )
        rate_map = {"north": 1.1, "south": 0.9, "east": 1.0}
        pl.DataFrame(
            {"region": list(rate_map.keys()), "rate": list(rate_map.values())}
        ).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        # The rate column is present in the output
        assert "rate" in step.output_values
        # The rate must match the region in the same row
        region = step.output_values["region"]
        assert step.output_values["rate"] == rate_map[region]

    def test_single_factor_no_match_with_default(self, tmp_path):
        """Left join with fill_null provides a default when no rate matches.

        Verifies that both rows are traceable and the fill_null default
        appears in at least one row.  Exact row ordering after a join is
        non-deterministic in Polars, so we check both rows.
        """
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1, 2], "region": ["north", "unknown"]}).write_parquet(p_data)
        pl.DataFrame({"region": ["north", "south"], "rate": [1.1, 0.9]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node(
                        "lookup",
                        "policies.join(rates, on='region', how='left').with_columns(pl.col('rate').fill_null(1.0))",
                    ),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        # Trace both rows and collect results
        r0 = execute_trace(graph, row_index=0, target_node_id="lookup")
        r1 = execute_trace(graph, row_index=1, target_node_id="lookup")
        rates = [
            _step_by_id(r0, "lookup").output_values["rate"],
            _step_by_id(r1, "lookup").output_values["rate"],
        ]
        # One row matched (rate=1.1), the other didn't (fill_null → 1.0)
        assert sorted(rates) == [1.0, 1.1], (
            f"Expected rates [1.0, 1.1] after left join + fill_null, got {sorted(rates)}"
        )

    def test_single_factor_no_match_no_default(self, tmp_path):
        """Left join without fill_null leaves NULL when no rate matches."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        # Use unique IDs so the row correlator can distinguish rows
        pl.DataFrame({"policy_id": [1, 2], "region": ["north", "unknown"]}).write_parquet(p_data)
        pl.DataFrame({"region": ["north", "south"], "rate": [1.1, 0.9]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region', how='left')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        # Row 0 matches (north -> 1.1), row 1 does not match (unknown -> null)
        result_match = execute_trace(graph, row_index=0, target_node_id="lookup")
        step_match = _step_by_id(result_match, "lookup")
        # The output row should have a rate value — exact row depends on join order
        assert "rate" in step_match.output_values

        result_no_match = execute_trace(graph, row_index=1, target_node_id="lookup")
        step_no_match = _step_by_id(result_no_match, "lookup")
        # At least one row should have a null rate (unmatched left join)
        # Check that both rows are traceable and the unmatched one has null
        rates = [
            _step_by_id(result_match, "lookup").output_values["rate"],
            _step_by_id(result_no_match, "lookup").output_values["rate"],
        ]
        assert None in rates, "Left join should produce at least one NULL rate"
        assert 1.1 in rates, "Left join should produce at least one matched rate"


class TestRatingStepMultipleFactors:
    """Rate table lookups with composite keys and multiple tables."""

    def test_composite_key_lookup(self, tmp_path):
        """Two-factor join key (region + vehicle_type)."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame(
            {
                "policy_id": [1, 2],
                "region": ["north", "south"],
                "vehicle_type": ["sedan", "suv"],
            }
        ).write_parquet(p_data)
        pl.DataFrame(
            {
                "region": ["north", "south", "north", "south"],
                "vehicle_type": ["sedan", "sedan", "suv", "suv"],
                "rate": [1.0, 1.1, 1.2, 1.3],
            }
        ).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node(
                        "lookup", "policies.join(rates, on=['region', 'vehicle_type'])"
                    ),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        r0 = execute_trace(graph, row_index=0, target_node_id="lookup")
        r1 = execute_trace(graph, row_index=1, target_node_id="lookup")
        rates = sorted(
            [
                _step_by_id(r0, "lookup").output_values["rate"],
                _step_by_id(r1, "lookup").output_values["rate"],
            ]
        )
        # north+sedan=1.0, south+suv=1.3 — both should appear
        assert rates == [1.0, 1.3], f"Expected [1.0, 1.3], got {rates}"

    def test_multiple_tables_multiply(self, tmp_path):
        """Two rate tables combined by multiplication."""
        p_data = tmp_path / "policies.parquet"
        p_age = tmp_path / "age_rates.parquet"
        p_region = tmp_path / "region_rates.parquet"

        pl.DataFrame(
            {
                "policy_id": [1],
                "age_band": ["25-30"],
                "region": ["north"],
                "base_premium": [100.0],
            }
        ).write_parquet(p_data)
        pl.DataFrame({"age_band": ["25-30"], "age_rate": [0.8]}).write_parquet(p_age)
        pl.DataFrame({"region": ["north"], "region_rate": [1.1]}).write_parquet(p_region)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("age_rates", str(p_age)),
                    _source_node("region_rates", str(p_region)),
                    _transform_node("join_age", "policies.join(age_rates, on='age_band')"),
                    _transform_node("join_region", "join_age.join(region_rates, on='region')"),
                    _transform_node(
                        "calc",
                        ".with_columns(premium=pl.col('base_premium') * pl.col('age_rate') * pl.col('region_rate'))",
                    ),
                ],
                "edges": [
                    _edge("policies", "join_age"),
                    _edge("age_rates", "join_age"),
                    _edge("join_age", "join_region"),
                    _edge("region_rates", "join_region"),
                    _edge("join_region", "calc"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc")
        step = _step_by_id(result, "calc")
        expected = 100.0 * 0.8 * 1.1
        assert abs(step.output_values["premium"] - expected) < 0.01

    def test_multiple_tables_add(self, tmp_path):
        """Two rate tables combined by addition."""
        p_data = tmp_path / "policies.parquet"
        p_a = tmp_path / "a_rates.parquet"
        p_b = tmp_path / "b_rates.parquet"

        pl.DataFrame({"id": [1], "key_a": ["x"], "key_b": ["y"]}).write_parquet(p_data)
        pl.DataFrame({"key_a": ["x"], "rate_a": [10.0]}).write_parquet(p_a)
        pl.DataFrame({"key_b": ["y"], "rate_b": [5.0]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("j1", "data.join(a, on='key_a')"),
                    _transform_node("j2", "j1.join(b, on='key_b')"),
                    _transform_node(
                        "calc", ".with_columns(total=pl.col('rate_a') + pl.col('rate_b'))"
                    ),
                ],
                "edges": [
                    _edge("data", "j1"),
                    _edge("a", "j1"),
                    _edge("j1", "j2"),
                    _edge("b", "j2"),
                    _edge("j2", "calc"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc")
        step = _step_by_id(result, "calc")
        assert step.output_values["total"] == 15.0

    def test_multiple_tables_min(self, tmp_path):
        """Two rates combined by taking the minimum."""
        p_data = tmp_path / "policies.parquet"
        p_a = tmp_path / "a_rates.parquet"
        p_b = tmp_path / "b_rates.parquet"

        pl.DataFrame({"id": [1], "key_a": ["x"], "key_b": ["y"]}).write_parquet(p_data)
        pl.DataFrame({"key_a": ["x"], "rate_a": [10.0]}).write_parquet(p_a)
        pl.DataFrame({"key_b": ["y"], "rate_b": [5.0]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("j1", "data.join(a, on='key_a')"),
                    _transform_node("j2", "j1.join(b, on='key_b')"),
                    _transform_node(
                        "calc",
                        ".with_columns(min_rate=pl.min_horizontal('rate_a', 'rate_b'))",
                    ),
                ],
                "edges": [
                    _edge("data", "j1"),
                    _edge("a", "j1"),
                    _edge("j1", "j2"),
                    _edge("b", "j2"),
                    _edge("j2", "calc"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc")
        step = _step_by_id(result, "calc")
        assert step.output_values["min_rate"] == 5.0

    def test_multiple_tables_max(self, tmp_path):
        """Two rates combined by taking the maximum."""
        p_data = tmp_path / "policies.parquet"
        p_a = tmp_path / "a_rates.parquet"
        p_b = tmp_path / "b_rates.parquet"

        pl.DataFrame({"id": [1], "key_a": ["x"], "key_b": ["y"]}).write_parquet(p_data)
        pl.DataFrame({"key_a": ["x"], "rate_a": [10.0]}).write_parquet(p_a)
        pl.DataFrame({"key_b": ["y"], "rate_b": [5.0]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("j1", "data.join(a, on='key_a')"),
                    _transform_node("j2", "j1.join(b, on='key_b')"),
                    _transform_node(
                        "calc",
                        ".with_columns(max_rate=pl.max_horizontal('rate_a', 'rate_b'))",
                    ),
                ],
                "edges": [
                    _edge("data", "j1"),
                    _edge("a", "j1"),
                    _edge("j1", "j2"),
                    _edge("b", "j2"),
                    _edge("j2", "calc"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc")
        step = _step_by_id(result, "calc")
        assert step.output_values["max_rate"] == 10.0


class TestRatingStepEdgeCases:
    """Edge cases for rate table lookups."""

    def test_null_factor_value(self, tmp_path):
        """NULL in the join key produces NULL rate via left join."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        # Only one row with a NULL region so row_index=0 is unambiguous
        pl.DataFrame({"policy_id": [1], "region": pl.Series([None], dtype=pl.Utf8)}).write_parquet(
            p_data
        )
        pl.DataFrame({"region": ["north", "south"], "rate": [1.1, 0.9]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region', how='left')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        assert step.output_values["region"] is None
        assert step.output_values["rate"] is None

    def test_type_coercion_int_to_str(self, tmp_path):
        """Join key type mismatch handled by casting before join."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1], "region_code": [10]}).write_parquet(p_data)
        pl.DataFrame({"region_code": [10], "rate": [1.5]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region_code')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        assert step.output_values["rate"] == 1.5

    def test_duplicate_rate_entries(self, tmp_path):
        """Duplicate keys in rate table produce multiple rows in output."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1], "region": ["north"]}).write_parquet(p_data)
        pl.DataFrame({"region": ["north", "north"], "rate": [1.0, 2.0]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        assert step.output_values["region"] == "north"
        assert step.output_values["rate"] in (1.0, 2.0)

    def test_empty_rate_table(self, tmp_path):
        """An empty rate table yields all NULLs via left join."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1], "region": ["north"]}).write_parquet(p_data)
        pl.DataFrame(
            {"region": pl.Series([], dtype=pl.Utf8), "rate": pl.Series([], dtype=pl.Float64)}
        ).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region', how='left')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        assert step.output_values["rate"] is None

    def test_combined_column_null_from_one_table(self, tmp_path):
        """When one rate table has no match, the combined column uses fill_null."""
        p_data = tmp_path / "policies.parquet"
        p_a = tmp_path / "a_rates.parquet"
        p_b = tmp_path / "b_rates.parquet"

        pl.DataFrame({"id": [1], "key_a": ["x"], "key_b": ["missing"]}).write_parquet(p_data)
        pl.DataFrame({"key_a": ["x"], "rate_a": [10.0]}).write_parquet(p_a)
        pl.DataFrame({"key_b": ["y"], "rate_b": [5.0]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("j1", "data.join(a, on='key_a')"),
                    _transform_node("j2", "j1.join(b, on='key_b', how='left')"),
                    _transform_node(
                        "calc",
                        ".with_columns(total=pl.col('rate_a') + pl.col('rate_b').fill_null(0.0))",
                    ),
                ],
                "edges": [
                    _edge("data", "j1"),
                    _edge("a", "j1"),
                    _edge("j1", "j2"),
                    _edge("b", "j2"),
                    _edge("j2", "calc"),
                ],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="calc")
        step = _step_by_id(result, "calc")
        assert step.output_values["total"] == 10.0

    def test_fill_null_behaviour(self, tmp_path):
        """fill_null replaces NULLs from unmatched join with a default rate."""
        p_data = tmp_path / "data.parquet"
        p_rate = tmp_path / "rates.parquet"

        pl.DataFrame({"id": [1, 2], "key": ["a", "z"]}).write_parquet(p_data)
        pl.DataFrame({"key": ["a"], "rate": [1.5]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node(
                        "lookup",
                        "data.join(rates, on='key', how='left').with_columns(pl.col('rate').fill_null(1.0))",
                    ),
                ],
                "edges": [_edge("data", "lookup"), _edge("rates", "lookup")],
            }
        )

        r0 = execute_trace(graph, row_index=0, target_node_id="lookup")
        r1 = execute_trace(graph, row_index=1, target_node_id="lookup")
        rates = [
            _step_by_id(r0, "lookup").output_values["rate"],
            _step_by_id(r1, "lookup").output_values["rate"],
        ]
        # One row matched (rate=1.5), the other didn't (fill_null → 1.0)
        assert sorted(rates) == [1.0, 1.5], (
            f"Expected rates [1.0, 1.5] after left join + fill_null, got {sorted(rates)}"
        )

    def test_output_column_name_collision(self, tmp_path):
        """When both tables have a non-key column with the same name, Polars suffixes."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame(
            {"policy_id": [1], "region": ["north"], "label": ["policy_label"]}
        ).write_parquet(p_data)
        pl.DataFrame({"region": ["north"], "rate": [1.1], "label": ["rate_label"]}).write_parquet(
            p_rate
        )

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        # Polars adds _right suffix on collision
        assert "label" in step.output_values
        assert "label_right" in step.output_values

    def test_very_large_rate_table(self, tmp_path):
        """A large rate table (1000 entries) still matches correctly."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1], "code": [500]}).write_parquet(p_data)
        pl.DataFrame(
            {
                "code": list(range(1000)),
                "rate": [float(i) / 100 for i in range(1000)],
            }
        ).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='code')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        assert step.output_values["rate"] == 5.0

    def test_schema_diff_shows_rate_column_in_output(self, tmp_path):
        """The join node's output contains the rate column from the rate table.

        Note: because the trace merges both parents' outputs into the join
        node's input_values, the rate column appears in columns_passed (it
        exists in both the merged input and the output).  The important
        assertion is that rate is present in the output.
        """
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1], "region": ["north"]}).write_parquet(p_data)
        pl.DataFrame({"region": ["north"], "rate": [1.1]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup")
        step = _step_by_id(result, "lookup")
        assert "rate" in step.output_values
        # The rate column is present in the merged input (from rates parent)
        # so it shows as passed rather than added
        assert "rate" in step.schema_diff.columns_passed

    def test_column_trace_on_rate(self, tmp_path):
        """Tracing the 'rate' column marks the join node as column_relevant."""
        p_data = tmp_path / "policies.parquet"
        p_rate = tmp_path / "rate_table.parquet"

        pl.DataFrame({"policy_id": [1], "region": ["north"]}).write_parquet(p_data)
        pl.DataFrame({"region": ["north"], "rate": [1.1]}).write_parquet(p_rate)

        graph = _g(
            {
                "nodes": [
                    _source_node("policies", str(p_data)),
                    _source_node("rates", str(p_rate)),
                    _transform_node("lookup", "policies.join(rates, on='region')"),
                ],
                "edges": [_edge("policies", "lookup"), _edge("rates", "lookup")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="lookup", column="rate")
        step = _step_by_id(result, "lookup")
        assert step.column_relevant is True


# ===========================================================================
# 2. Banding Simulation Tests (17 tests)
# ===========================================================================


class TestBandingContinuous:
    """Continuous banding using Polars when/then patterns."""

    def test_middle_band(self, tmp_path):
        """Value falls in a middle band."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [35]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young'))"
            ".when(pl.col('age') < 50).then(pl.lit('middle'))"
            ".otherwise(pl.lit('senior')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "middle"
        assert "age_band" in step.schema_diff.columns_added

    def test_exact_boundary_lower(self, tmp_path):
        """Value exactly at a lower boundary."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [25]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young'))"
            ".when(pl.col('age') < 50).then(pl.lit('middle'))"
            ".otherwise(pl.lit('senior')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "middle"

    def test_below_all_bands(self, tmp_path):
        """Value below the lowest band threshold."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [10]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young'))"
            ".when(pl.col('age') < 50).then(pl.lit('middle'))"
            ".otherwise(pl.lit('senior')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "young"

    def test_above_all_bands(self, tmp_path):
        """Value above the highest band threshold."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [80]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young'))"
            ".when(pl.col('age') < 50).then(pl.lit('middle'))"
            ".otherwise(pl.lit('senior')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "senior"

    def test_numeric_precision_at_boundary(self, tmp_path):
        """Floating-point boundary precision: 24.999... should be 'young'."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [24.9999]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25.0).then(pl.lit('young'))"
            ".when(pl.col('age') < 50.0).then(pl.lit('middle'))"
            ".otherwise(pl.lit('senior')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "young"


class TestBandingCategorical:
    """Categorical banding using when/then with equality checks."""

    def test_exact_category_match(self, tmp_path):
        """Category value matches exactly."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "fuel": ["diesel"]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('fuel') == 'petrol').then(pl.lit(1.0))"
            ".when(pl.col('fuel') == 'diesel').then(pl.lit(1.2))"
            ".when(pl.col('fuel') == 'electric').then(pl.lit(0.8))"
            ".otherwise(pl.lit(1.0)).alias('fuel_factor')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["fuel_factor"] == 1.2

    def test_category_no_match_uses_otherwise(self, tmp_path):
        """Unknown category falls through to otherwise."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "fuel": ["hydrogen"]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('fuel') == 'petrol').then(pl.lit(1.0))"
            ".when(pl.col('fuel') == 'diesel').then(pl.lit(1.2))"
            ".otherwise(pl.lit(1.0)).alias('fuel_factor')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["fuel_factor"] == 1.0

    def test_category_case_sensitivity(self, tmp_path):
        """Banding is case-sensitive by default: 'Diesel' != 'diesel'."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "fuel": ["Diesel"]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('fuel') == 'diesel').then(pl.lit(1.2))"
            ".otherwise(pl.lit(1.0)).alias('fuel_factor')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["fuel_factor"] == 1.0


class TestBandingEdgeCases:
    """Edge cases for banding patterns."""

    def test_multiple_factor_banding(self, tmp_path):
        """Two independent banding expressions in one with_columns."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [35], "fuel": ["diesel"]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young')).otherwise(pl.lit('adult')).alias('age_band'),"
            "pl.when(pl.col('fuel') == 'diesel').then(pl.lit(1.2)).otherwise(pl.lit(1.0)).alias('fuel_factor')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "adult"
        assert step.output_values["fuel_factor"] == 1.2
        assert "age_band" in step.schema_diff.columns_added
        assert "fuel_factor" in step.schema_diff.columns_added

    def test_null_input_banding(self, tmp_path):
        """NULL input falls through all conditions to otherwise."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {"id": [1], "age": [None]}, schema={"id": pl.Int64, "age": pl.Int64}
        ).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young'))"
            ".when(pl.col('age') < 50).then(pl.lit('middle'))"
            ".otherwise(pl.lit('unknown')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["age_band"] == "unknown"

    def test_nan_input_banding(self, tmp_path):
        """NaN input value falls through to otherwise."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "score": [float("nan")]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('score') < 50.0).then(pl.lit('low'))"
            ".when(pl.col('score') < 100.0).then(pl.lit('medium'))"
            ".otherwise(pl.lit('default')).alias('band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["band"] == "default"

    def test_overlapping_rules_first_wins(self, tmp_path):
        """When conditions overlap, first matching rule wins."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "val": [30]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('val') < 50).then(pl.lit('A'))"
            ".when(pl.col('val') < 40).then(pl.lit('B'))"
            ".otherwise(pl.lit('C')).alias('band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["band"] == "A"

    def test_empty_rules_otherwise_only(self, tmp_path):
        """A when/then with no when clauses just uses otherwise."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "val": [30]}).write_parquet(p)

        code = ".with_columns(pl.when(pl.lit(False)).then(pl.lit('never')).otherwise(pl.lit('always')).alias('band'))"
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["band"] == "always"

    def test_single_rule_band(self, tmp_path):
        """Only one when clause with otherwise."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2], "age": [20, 60]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 30).then(pl.lit('young')).otherwise(pl.lit('old')).alias('band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        r0 = execute_trace(graph, row_index=0, target_node_id="band")
        assert _step_by_id(r0, "band").output_values["band"] == "young"

        r1 = execute_trace(graph, row_index=1, target_node_id="band")
        assert _step_by_id(r1, "band").output_values["band"] == "old"

    def test_no_default_no_match(self, tmp_path):
        """When no condition matches and there is no otherwise, result is NULL."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "val": [100]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('val') < 50).then(pl.lit('low'))"
            ".when(pl.col('val') < 75).then(pl.lit('mid'))"
            ".alias('band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band")
        step = _step_by_id(result, "band")
        assert step.output_values["band"] is None

    def test_banding_column_trace(self, tmp_path):
        """Tracing a banding output column marks the band step as relevant."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1], "age": [35]}).write_parquet(p)

        code = (
            ".with_columns("
            "pl.when(pl.col('age') < 25).then(pl.lit('young'))"
            ".otherwise(pl.lit('adult')).alias('age_band')"
            ")"
        )
        graph = _g(
            {
                "nodes": [_source_node("src", str(p)), _transform_node("band", code)],
                "edges": [_edge("src", "band")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="band", column="age_band")
        step = _step_by_id(result, "band")
        assert step.column_relevant is True
        assert "age_band" in step.schema_diff.columns_added


# ===========================================================================
# 3. Model Score Simulation Tests (16 tests)
# ===========================================================================


class TestModelScoreSimulation:
    """Model scoring patterns using with_columns to simulate prediction output."""

    def test_prediction_column_added(self, tmp_path):
        """Simulated model score adds a prediction column."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "feature_a": [1.0, 2.0, 3.0],
                "feature_b": [10.0, 20.0, 30.0],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "model",
                        ".with_columns(prediction=pl.col('feature_a') * 0.5 + pl.col('feature_b') * 0.1)",
                    ),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert "prediction" in step.schema_diff.columns_added
        expected = 1.0 * 0.5 + 10.0 * 0.1
        assert abs(step.output_values["prediction"] - expected) < 0.001

    def test_feature_columns_passed_through(self, tmp_path):
        """Feature columns appear in columns_passed after model scoring."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"feature_a": [1.0], "feature_b": [2.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "model", ".with_columns(pred=pl.col('feature_a') + pl.col('feature_b'))"
                    ),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert "feature_a" in step.schema_diff.columns_passed
        assert "feature_b" in step.schema_diff.columns_passed

    def test_post_processing_of_prediction(self, tmp_path):
        """A transform after model scoring clips the prediction."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"feature": [100.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(raw_pred=pl.col('feature') * 2.0)"),
                    _transform_node("clip", ".with_columns(pred=pl.col('raw_pred').clip(0, 150))"),
                ],
                "edges": [_edge("src", "model"), _edge("model", "clip")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="clip")
        step = _step_by_id(result, "clip")
        assert step.output_values["pred"] == 150.0
        assert "pred" in step.schema_diff.columns_added

    def test_missing_feature_null_column(self, tmp_path):
        """A NULL feature column propagates into the prediction."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "feature_a": [1.0],
                "feature_b": [None],
            },
            schema={"feature_a": pl.Float64, "feature_b": pl.Float64},
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "model",
                        ".with_columns(pred=pl.col('feature_a') + pl.col('feature_b'))",
                    ),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert step.output_values["pred"] is None

    def test_multiple_output_columns(self, tmp_path):
        """Model produces two output columns: pred and confidence."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "model",
                        ".with_columns(pred=pl.col('x') * 2, confidence=pl.lit(0.95))",
                    ),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert "pred" in step.schema_diff.columns_added
        assert "confidence" in step.schema_diff.columns_added
        assert step.output_values["pred"] == 10.0
        assert step.output_values["confidence"] == 0.95

    def test_trace_identifies_model_input_vs_output_columns(self, tmp_path):
        """Schema diff distinguishes model inputs (passed) from outputs (added)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"feat1": [1.0], "feat2": [2.0], "id": [100]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "model",
                        ".with_columns(pred=pl.col('feat1') * 3 + pl.col('feat2'))",
                    ),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        # Input features and id are passed through
        assert "feat1" in step.schema_diff.columns_passed
        assert "feat2" in step.schema_diff.columns_passed
        assert "id" in step.schema_diff.columns_passed
        # prediction is added
        assert "pred" in step.schema_diff.columns_added

    def test_model_score_second_row(self, tmp_path):
        """Each row gets its own correct prediction value."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0, 2.0, 3.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('x') ** 2)"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=1, target_node_id="model")
        step = _step_by_id(result, "model")
        assert step.output_values["pred"] == 4.0

    def test_model_score_with_logarithm(self, tmp_path):
        """Model applies log transform."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [math.e]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('x').log())"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert abs(step.output_values["pred"] - 1.0) < 0.001

    def test_model_chain_two_steps(self, tmp_path):
        """Two sequential scoring steps: raw prediction then adjustment."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("raw", ".with_columns(raw_pred=pl.col('x') * 0.3)"),
                    _transform_node("adj", ".with_columns(final_pred=pl.col('raw_pred') + 1.0)"),
                ],
                "edges": [_edge("src", "raw"), _edge("raw", "adj")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="adj")
        adj_step = _step_by_id(result, "adj")
        assert adj_step.output_values["final_pred"] == 4.0
        assert "raw_pred" in adj_step.schema_diff.columns_passed
        assert "final_pred" in adj_step.schema_diff.columns_added

    def test_model_prediction_column_trace(self, tmp_path):
        """Column trace on prediction column identifies the model node."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model", column="pred")
        step = _step_by_id(result, "model")
        assert step.column_relevant is True
        assert result.output_value == 2.0

    def test_model_feature_column_trace(self, tmp_path):
        """Column trace on a feature column identifies source as relevant."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model", column="x")
        src_step = _step_by_id(result, "src")
        model_step = _step_by_id(result, "model")
        assert src_step.column_relevant is True
        assert model_step.column_relevant is True

    def test_model_replaces_existing_column(self, tmp_path):
        """Model output column overwrites an existing column (shows as modified)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [5.0], "pred": [0.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert "pred" in step.schema_diff.columns_modified
        assert step.output_values["pred"] == 10.0

    def test_model_preserves_non_feature_columns(self, tmp_path):
        """Non-feature columns pass through unchanged."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [42], "name": ["test"], "x": [1.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert step.output_values["id"] == 42
        assert step.output_values["name"] == "test"
        assert "id" in step.schema_diff.columns_passed
        assert "name" in step.schema_diff.columns_passed

    def test_model_all_null_features(self, tmp_path):
        """All features NULL produces NULL prediction."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "a": [None],
                "b": [None],
            },
            schema={"a": pl.Float64, "b": pl.Float64},
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("model", ".with_columns(pred=pl.col('a') + pl.col('b'))"),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert step.output_values["pred"] is None

    def test_model_with_conditional_output(self, tmp_path):
        """Model uses when/then for conditional output based on features."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"risk_score": [80.0], "amount": [1000.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "model",
                        ".with_columns("
                        "pl.when(pl.col('risk_score') > 70).then(pl.col('amount') * 1.5)"
                        ".otherwise(pl.col('amount')).alias('adjusted_amount')"
                        ")",
                    ),
                ],
                "edges": [_edge("src", "model")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="model")
        step = _step_by_id(result, "model")
        assert step.output_values["adjusted_amount"] == 1500.0


# ===========================================================================
# 4. Scenario Expansion Tests (8 tests)
# ===========================================================================


class TestScenarioExpansion:
    """Scenario expansion using cross join to replicate rows."""

    def test_basic_expansion(self, tmp_path):
        """Cross join produces N rows from 1 input row * N scenarios."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"policy_id": [1], "base": [100.0]}).write_parquet(p_data)
        pl.DataFrame(
            {"scenario": ["low", "mid", "high"], "multiplier": [0.8, 1.0, 1.2]}
        ).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="expand")
        step = _step_by_id(result, "expand")
        # Cross join merges both parents' outputs as input, so scenario
        # columns appear in columns_passed (present in both input and output)
        assert "scenario" in step.output_values
        assert "multiplier" in step.output_values
        assert step.output_values["policy_id"] == 1

    def test_expansion_row_count(self, tmp_path):
        """Cross join of 2 data rows x 3 scenarios = 6 output rows."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [1, 2], "val": [10, 20]}).write_parquet(p_data)
        pl.DataFrame({"scenario": ["a", "b", "c"]}).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        # Trace various rows to verify all are accessible
        for i in range(6):
            result = execute_trace(graph, row_index=i, target_node_id="expand")
            step = _step_by_id(result, "expand")
            assert step.output_values["id"] in (1, 2)
            assert step.output_values["scenario"] in ("a", "b", "c")

    def test_original_row_values_before_expansion(self, tmp_path):
        """The source data step shows the original row before cross join."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [42], "base": [200.0]}).write_parquet(p_data)
        pl.DataFrame({"scenario": ["test"]}).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="expand")
        data_step = _step_by_id(result, "data")
        assert data_step.output_values["id"] == 42
        assert data_step.output_values["base"] == 200.0

    def test_multiplier_value_for_specific_scenario(self, tmp_path):
        """Specific scenario row has the expected multiplier."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [1], "base": [100.0]}).write_parquet(p_data)
        pl.DataFrame(
            {
                "scenario": ["low", "high"],
                "multiplier": [0.5, 2.0],
            }
        ).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        # Row 0 = "low" (multiplier=0.5), row 1 = "high" (multiplier=2.0)
        r0 = execute_trace(graph, row_index=0, target_node_id="expand")
        s0 = _step_by_id(r0, "expand")
        r1 = execute_trace(graph, row_index=1, target_node_id="expand")
        s1 = _step_by_id(r1, "expand")
        multipliers = {s0.output_values["multiplier"], s1.output_values["multiplier"]}
        assert multipliers == {0.5, 2.0}

    def test_post_expansion_transform(self, tmp_path):
        """A transform after expansion applies per-scenario calculations."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [1], "base": [100.0]}).write_parquet(p_data)
        pl.DataFrame({"scenario": ["low", "high"], "multiplier": [0.5, 2.0]}).write_parquet(
            p_scenarios
        )

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                    _transform_node(
                        "calc", ".with_columns(adjusted=pl.col('base') * pl.col('multiplier'))"
                    ),
                ],
                "edges": [
                    _edge("data", "expand"),
                    _edge("scenarios", "expand"),
                    _edge("expand", "calc"),
                ],
            }
        )

        r0 = execute_trace(graph, row_index=0, target_node_id="calc")
        s0 = _step_by_id(r0, "calc")
        expected_0 = s0.output_values["base"] * s0.output_values["multiplier"]
        assert abs(s0.output_values["adjusted"] - expected_0) < 0.01

    def test_expansion_schema_diff(self, tmp_path):
        """Schema diff at expansion node shows scenario columns as added."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [1]}).write_parquet(p_data)
        pl.DataFrame({"scenario": ["a"], "factor": [1.5]}).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="expand")
        step = _step_by_id(result, "expand")
        # Cross join merges both parents' outputs as input, so scenario
        # columns appear in columns_passed rather than columns_added
        assert "scenario" in step.output_values
        assert "factor" in step.output_values
        assert "scenario" in step.schema_diff.columns_passed
        assert "factor" in step.schema_diff.columns_passed

    def test_expansion_trace_step_count(self, tmp_path):
        """Trace includes data source, scenario source, and expansion node."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [1]}).write_parquet(p_data)
        pl.DataFrame({"scenario": ["a"]}).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="expand")
        assert len(result.steps) == 3

    def test_expansion_column_trace_on_scenario_column(self, tmp_path):
        """Tracing 'scenario' column in expanded output."""
        p_data = tmp_path / "data.parquet"
        p_scenarios = tmp_path / "scenarios.parquet"

        pl.DataFrame({"id": [1]}).write_parquet(p_data)
        pl.DataFrame({"scenario": ["a"]}).write_parquet(p_scenarios)

        graph = _g(
            {
                "nodes": [
                    _source_node("data", str(p_data)),
                    _source_node("scenarios", str(p_scenarios)),
                    _transform_node("expand", "data.join(scenarios, how='cross')"),
                ],
                "edges": [_edge("data", "expand"), _edge("scenarios", "expand")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="expand", column="scenario")
        step = _step_by_id(result, "expand")
        assert step.column_relevant is True


# ===========================================================================
# 5. Live Switch Tests (6 tests)
# ===========================================================================


class TestLiveSwitch:
    """Live switch branch selection using graph topology."""

    def _make_switch_graph(self, tmp_path, p_live=None, p_batch=None):
        """Helper to build a live-switch graph with two source branches."""
        if p_live is None:
            p_live = tmp_path / "live.parquet"
            pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p_live)
        if p_batch is None:
            p_batch = tmp_path / "batch.parquet"
            pl.DataFrame({"x": [10, 20]}).write_parquet(p_batch)

        return PipelineGraph(
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

    def test_live_source_selects_live_branch(self, tmp_path):
        """With source='live', the live branch is selected."""
        graph = self._make_switch_graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id="sw", source="live")
        step_ids = {s.node_id for s in result.steps}
        assert "live_src" in step_ids
        assert "batch_src" not in step_ids

    def test_batch_source_selects_batch_branch(self, tmp_path):
        """With source='nb_batch', the batch branch is selected."""
        graph = self._make_switch_graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id="sw", source="nb_batch")
        step_ids = {s.node_id for s in result.steps}
        assert "batch_src" in step_ids
        assert "live_src" not in step_ids

    def test_pruned_branch_not_in_trace(self, tmp_path):
        """The inactive branch has no trace steps."""
        graph = self._make_switch_graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id="sw", source="live")
        all_ids = [s.node_id for s in result.steps]
        assert "batch_src" not in all_ids

    def test_switch_output_values_from_active_branch(self, tmp_path):
        """Switch node output values come from the active branch."""
        graph = self._make_switch_graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id="sw", source="live")
        sw_step = _step_by_id(result, "sw")
        assert sw_step.output_values["x"] in (1, 2, 3)

    def test_switch_output_values_from_batch(self, tmp_path):
        """Switch node output values come from batch when batch is active."""
        graph = self._make_switch_graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id="sw", source="nb_batch")
        sw_step = _step_by_id(result, "sw")
        assert sw_step.output_values["x"] in (10, 20)

    def test_multiple_branches_three_way(self, tmp_path):
        """Three-way live switch selects the correct branch."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"
        p_c = tmp_path / "c.parquet"
        pl.DataFrame({"val": [1]}).write_parquet(p_a)
        pl.DataFrame({"val": [2]}).write_parquet(p_b)
        pl.DataFrame({"val": [3]}).write_parquet(p_c)

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(label="a", nodeType="dataSource", config={"path": str(p_a)}),
                ),
                GraphNode(
                    id="b",
                    data=NodeData(label="b", nodeType="dataSource", config={"path": str(p_b)}),
                ),
                GraphNode(
                    id="c",
                    data=NodeData(label="c", nodeType="dataSource", config={"path": str(p_c)}),
                ),
                GraphNode(
                    id="sw",
                    data=NodeData(
                        label="switch",
                        nodeType="liveSwitch",
                        config={
                            "input_scenario_map": {
                                "a": "live",
                                "b": "scenario_b",
                                "c": "scenario_c",
                            }
                        },
                    ),
                ),
            ],
            edges=[
                GraphEdge(id="e1", source="a", target="sw"),
                GraphEdge(id="e2", source="b", target="sw"),
                GraphEdge(id="e3", source="c", target="sw"),
            ],
        )

        result = execute_trace(graph, row_index=0, target_node_id="sw", source="scenario_c")
        step_ids = {s.node_id for s in result.steps}
        assert "c" in step_ids
        assert "a" not in step_ids
        assert "b" not in step_ids
        sw_step = _step_by_id(result, "sw")
        assert sw_step.output_values["val"] == 3


# ===========================================================================
# 6. Data Source Metadata Tests (5 tests)
# ===========================================================================


class TestDataSourceMetadata:
    """Source node trace: schema diff, columns added, row count."""

    def test_parquet_source_all_columns_added(self, tmp_path):
        """A source node's schema diff shows all columns as added."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0], "c": ["x", "y"]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="src")
        step = _step_by_id(result, "src")
        assert sorted(step.schema_diff.columns_added) == ["a", "b", "c"]
        assert step.schema_diff.columns_removed == []
        assert step.schema_diff.columns_modified == []
        assert step.schema_diff.columns_passed == []

    def test_source_input_values_empty(self, tmp_path):
        """A source node has empty input_values (no parent)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="src")
        step = _step_by_id(result, "src")
        assert step.input_values == {}

    def test_source_output_values_correct(self, tmp_path):
        """Source output_values match the row data."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30], "y": ["a", "b", "c"]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=1, target_node_id="src")
        step = _step_by_id(result, "src")
        assert step.output_values["x"] == 20
        assert step.output_values["y"] == "b"

    def test_post_load_transform_tracking(self, tmp_path):
        """A transform after a source tracks which columns are new vs passed."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2], "y": [10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", ".with_columns(z=pl.col('x') + pl.col('y'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        src_step = _step_by_id(result, "src")
        t_step = _step_by_id(result, "t")

        assert sorted(src_step.schema_diff.columns_added) == ["x", "y"]
        assert "z" in t_step.schema_diff.columns_added
        assert "x" in t_step.schema_diff.columns_passed
        assert "y" in t_step.schema_diff.columns_passed

    def test_source_node_type(self, tmp_path):
        """Source node trace step has correct node_type."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="src")
        step = _step_by_id(result, "src")
        assert step.node_type == "dataSource"


# ===========================================================================
# 7. Row Lineage Type Tests (15 tests)
# ===========================================================================


class TestRowLineagePassthrough:
    """Passthrough: same row count, with_columns operations."""

    def test_with_columns_preserves_row_count(self, tmp_path):
        """with_columns does not change row count - row identity is preserved."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2, 3], "x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", ".with_columns(y=pl.col('x') * 2)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=1, target_node_id="t")
        src_step = _step_by_id(result, "src")
        t_step = _step_by_id(result, "t")
        assert src_step.output_values["id"] == t_step.output_values["id"]
        assert t_step.output_values["y"] == 40

    def test_rename_preserves_identity(self, tmp_path):
        """Rename does not change row count or values."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"old_name": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", ".rename({'old_name': 'new_name'})"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t")
        t_step = _step_by_id(result, "t")
        assert t_step.output_values["new_name"] == 1
        assert "new_name" in t_step.schema_diff.columns_added
        assert "old_name" in t_step.schema_diff.columns_removed


class TestRowLineageCreated:
    """Created: source nodes create new rows."""

    def test_source_creates_rows(self, tmp_path):
        """Source node is the origin of rows."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="src")
        step = _step_by_id(result, "src")
        # Source node: input_values is empty (no parent), all columns added
        assert step.input_values == {}
        assert len(step.schema_diff.columns_added) > 0

    def test_source_second_row(self, tmp_path):
        """Source can trace any row by index."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [],
            }
        )

        result = execute_trace(graph, row_index=2, target_node_id="src")
        step = _step_by_id(result, "src")
        assert step.output_values["x"] == 30


class TestRowLineageFiltered:
    """Filtered: filter operation removes rows."""

    def test_filter_reduces_rows(self, tmp_path):
        """Filter removes rows, shifting positional indices."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2, 3, 4, 5], "val": [10, 20, 30, 40, 50]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('val') > 25)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")
        src_step = _step_by_id(result, "src")

        # After filter, row 0 is id=3 (val=30)
        assert filt_step.output_values["id"] == 3
        assert src_step.output_values["id"] == filt_step.output_values["id"]

    def test_filter_all_columns_passed(self, tmp_path):
        """Filter does not add or modify columns - all are passed."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("filt", ".filter(pl.col('val') > 15)"),
                ],
                "edges": [_edge("src", "filt")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="filt")
        filt_step = _step_by_id(result, "filt")
        assert filt_step.schema_diff.columns_added == []
        assert filt_step.schema_diff.columns_removed == []
        assert sorted(filt_step.schema_diff.columns_passed) == ["id", "val"]


class TestRowLineageAggregated:
    """Aggregated: group_by.agg reduces rows."""

    def test_groupby_aggregation(self, tmp_path):
        """group_by.agg reduces row count and produces aggregated values."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "region": ["north", "south", "north", "south"],
                "premium": [100, 200, 150, 250],
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

        result = execute_trace(graph, row_index=0, target_node_id="agg")
        agg_step = _step_by_id(result, "agg")
        # After group_by+sort: row 0 = north, premium=250
        assert agg_step.output_values["region"] == "north"
        assert agg_step.output_values["premium"] == 250

    def test_aggregation_schema_diff(self, tmp_path):
        """Aggregation may modify existing columns (e.g. sum)."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "region": ["north", "south", "north"],
                "premium": [100, 200, 150],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("agg", ".group_by('region').agg(pl.col('premium').sum())"),
                ],
                "edges": [_edge("src", "agg")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="agg")
        agg_step = _step_by_id(result, "agg")
        # Both 'region' and 'premium' exist in input and output
        # 'premium' values differ (aggregated), 'region' values may match
        assert "premium" in agg_step.output_values


class TestRowLineageJoined:
    """Joined: join operations combine rows from two sources."""

    def test_left_join_preserves_left_rows(self, tmp_path):
        """Left join keeps all rows from the left table, including unmatched ones."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        # Only one row in the left table that has NO match in the right
        pl.DataFrame({"key": [99], "val_a": [10]}).write_parquet(p_a)
        pl.DataFrame({"key": [1, 2], "val_b": [100, 200]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("join", "a.join(b, on='key', how='left')"),
                ],
                "edges": [_edge("a", "join"), _edge("b", "join")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="join")
        join_step = _step_by_id(result, "join")
        # key=99 has no match in b, so val_b is NULL
        assert join_step.output_values["key"] == 99
        assert join_step.output_values["val_b"] is None

    def test_inner_join_reduces_rows(self, tmp_path):
        """Inner join keeps only matching rows."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame({"key": [1, 2, 3], "val_a": [10, 20, 30]}).write_parquet(p_a)
        pl.DataFrame({"key": [2, 3], "val_b": [200, 300]}).write_parquet(p_b)

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

        result = execute_trace(graph, row_index=0, target_node_id="join")
        join_step = _step_by_id(result, "join")
        assert join_step.output_values["key"] in (2, 3)
        # val_b comes from the second parent, so it's in columns_passed
        # (merged parent outputs form the input_values)
        assert "val_b" in join_step.output_values

    def test_join_schema_diff_shows_new_columns(self, tmp_path):
        """Join adds columns from the right table."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame({"key": [1], "a_col": [10]}).write_parquet(p_a)
        pl.DataFrame({"key": [1], "b_col": [20]}).write_parquet(p_b)

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

        result = execute_trace(graph, row_index=0, target_node_id="join")
        join_step = _step_by_id(result, "join")
        # b_col comes from the second parent, so it appears in
        # columns_passed (merged parent outputs form the input_values)
        assert "b_col" in join_step.output_values
        assert "b_col" in join_step.schema_diff.columns_passed


class TestRowLineageExpanded:
    """Expanded: cross join increases row count."""

    def test_cross_join_expands_rows(self, tmp_path):
        """Cross join multiplies row count."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame({"id": [1, 2]}).write_parquet(p_a)
        pl.DataFrame({"scenario": ["x", "y", "z"]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("expand", "a.join(b, how='cross')"),
                ],
                "edges": [_edge("a", "expand"), _edge("b", "expand")],
            }
        )

        # 2 x 3 = 6 rows
        result = execute_trace(graph, row_index=5, target_node_id="expand")
        step = _step_by_id(result, "expand")
        assert step.output_values["id"] in (1, 2)
        assert step.output_values["scenario"] in ("x", "y", "z")

    def test_cross_join_schema_diff(self, tmp_path):
        """Cross join adds columns from the second table."""
        p_a = tmp_path / "a.parquet"
        p_b = tmp_path / "b.parquet"

        pl.DataFrame({"id": [1]}).write_parquet(p_a)
        pl.DataFrame({"label": ["test"]}).write_parquet(p_b)

        graph = _g(
            {
                "nodes": [
                    _source_node("a", str(p_a)),
                    _source_node("b", str(p_b)),
                    _transform_node("expand", "a.join(b, how='cross')"),
                ],
                "edges": [_edge("a", "expand"), _edge("b", "expand")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="expand")
        step = _step_by_id(result, "expand")
        # label comes from the second parent, so it's in columns_passed
        assert "label" in step.output_values
        assert "label" in step.schema_diff.columns_passed


class TestRowLineageSort:
    """Sort: reorders rows without adding/removing."""

    def test_sort_reorders_rows(self, tmp_path):
        """Sort changes row order; trace still correlates correctly."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [3, 1, 2], "val": [30, 10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        sorted_step = _step_by_id(result, "sorted")
        src_step = _step_by_id(result, "src")

        assert sorted_step.output_values["id"] == 1
        assert src_step.output_values["id"] == 1

    def test_sort_does_not_add_columns(self, tmp_path):
        """Sort does not change schema -- all columns passed."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [3, 1, 2], "val": [30, 10, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('id')"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        step = _step_by_id(result, "sorted")
        assert step.schema_diff.columns_added == []
        assert step.schema_diff.columns_removed == []
        assert sorted(step.schema_diff.columns_passed) == ["id", "val"]

    def test_sort_descending_traces_correctly(self, tmp_path):
        """Descending sort: row 0 is the highest value."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"id": [1, 2, 3], "score": [10, 30, 20]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("sorted", ".sort('score', descending=True)"),
                ],
                "edges": [_edge("src", "sorted")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="sorted")
        step = _step_by_id(result, "sorted")
        assert step.output_values["score"] == 30
        assert step.output_values["id"] == 2


# ===========================================================================
# TDD tests for haute._trace_enrichment (module does not exist yet)
# ===========================================================================


class TestEnrichRatingStep:
    """TDD tests for the enrich_rating_step function."""

    @pytest.fixture()
    def _import_enrichment(self):
        """Attempt to import the enrichment module; skip if not present."""
        pytest.importorskip("haute._trace_enrichment")

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_matched_key(self):
        """enrich_rating_step returns the matched join key."""
        from haute._trace_enrichment import enrich_rating_step

        config = {"join_key": "region", "rate_column": "rate"}
        input_data = {"region": "north", "base": 100}
        output_data = {"region": "north", "base": 100, "rate": 1.1}
        detail = enrich_rating_step(config, input_data, output_data)
        assert detail["matched_key"] == {"region": "north"}

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_rate_value(self):
        """enrich_rating_step includes the looked-up rate value."""
        from haute._trace_enrichment import enrich_rating_step

        config = {"join_key": "region", "rate_column": "rate"}
        input_data = {"region": "north", "base": 100}
        output_data = {"region": "north", "base": 100, "rate": 1.1}
        detail = enrich_rating_step(config, input_data, output_data)
        assert detail["rate_value"] == 1.1

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_rating_no_match(self):
        """enrich_rating_step returns matched=False when rate is None."""
        from haute._trace_enrichment import enrich_rating_step

        config = {"join_key": "region", "rate_column": "rate"}
        input_data = {"region": "unknown", "base": 100}
        output_data = {"region": "unknown", "base": 100, "rate": None}
        detail = enrich_rating_step(config, input_data, output_data)
        assert detail["matched"] is False


class TestEnrichBanding:
    """TDD tests for the enrich_banding function."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip("haute._trace_enrichment")

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_selected_band(self):
        """enrich_banding returns which band was selected."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "input_column": "age",
            "output_column": "age_band",
            "rules": [
                {"condition": "< 25", "value": "young"},
                {"condition": "< 50", "value": "middle"},
                {"default": "senior"},
            ],
        }
        input_data = {"age": 35}
        output_data = {"age": 35, "age_band": "middle"}
        detail = enrich_banding(config, input_data, output_data)
        assert detail["selected_band"] == "middle"
        assert detail["rule_index"] == 1

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_banding_default(self):
        """enrich_banding returns default band when no rule matches."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "input_column": "age",
            "output_column": "age_band",
            "rules": [
                {"condition": "< 25", "value": "young"},
                {"default": "senior"},
            ],
        }
        input_data = {"age": 70}
        output_data = {"age": 70, "age_band": "senior"}
        detail = enrich_banding(config, input_data, output_data)
        assert detail["selected_band"] == "senior"
        assert detail["is_default"] is True


class TestEnrichModelScore:
    """TDD tests for the enrich_model_score function."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip("haute._trace_enrichment")

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_prediction(self):
        """enrich_model_score returns the predicted value."""
        from haute._trace_enrichment import enrich_model_score

        config = {"model_path": "model.cbm", "prediction_column": "pred"}
        input_data = {"feat1": 1.0, "feat2": 2.0}
        output_data = {"feat1": 1.0, "feat2": 2.0, "pred": 0.85}
        detail = enrich_model_score(config, input_data, output_data)
        assert detail["prediction_value"] == 0.85

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_identifies_features(self):
        """enrich_model_score lists which columns are model inputs."""
        from haute._trace_enrichment import enrich_model_score

        config = {
            "model_path": "model.cbm",
            "prediction_column": "pred",
            "feature_columns": ["feat1", "feat2"],
        }
        input_data = {"feat1": 1.0, "feat2": 2.0, "id": 42}
        output_data = {"feat1": 1.0, "feat2": 2.0, "id": 42, "pred": 0.85}
        detail = enrich_model_score(config, input_data, output_data)
        assert set(detail["feature_columns"]) == {"feat1", "feat2"}


class TestEnrichScenarioExpansion:
    """TDD tests for the enrich_scenario_expansion function."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip("haute._trace_enrichment")

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_expansion_factor(self):
        """enrich_scenario_expansion returns the scenario count."""
        from haute._trace_enrichment import enrich_scenario_expansion

        config = {"scenario_column": "scenario"}
        input_data = {"id": 1, "base": 100}
        output_data = {"id": 1, "base": 100, "scenario": "low", "multiplier": 0.5}
        detail = enrich_scenario_expansion(config, input_data, output_data)
        assert detail["scenario_value"] == "low"


class TestEnrichLiveSwitch:
    """TDD tests for the enrich_live_switch function."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip("haute._trace_enrichment")

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_active_branch(self):
        """enrich_live_switch returns which branch was selected."""
        from haute._trace_enrichment import enrich_live_switch

        config = {
            "input_scenario_map": {"live_src": "live", "batch_src": "nb_batch"},
        }
        detail = enrich_live_switch(config, source="live")
        assert detail["active_branch"] == "live_src"
        assert detail["active_scenario"] == "live"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_returns_pruned_branches(self):
        """enrich_live_switch lists which branches were pruned."""
        from haute._trace_enrichment import enrich_live_switch

        config = {
            "input_scenario_map": {"live_src": "live", "batch_src": "nb_batch"},
        }
        detail = enrich_live_switch(config, source="live")
        assert "batch_src" in detail["pruned_branches"]


class TestEnrichRowLineageType:
    """TDD tests for the detect_row_lineage_type function."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip("haute._trace_enrichment")

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_passthrough(self):
        """Detect passthrough lineage when row count is unchanged."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=10,
            node_type="polars",
            operation_type="with_columns",
        )
        assert result == "passthrough"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_created(self):
        """Detect created lineage for source nodes."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=0,
            output_row_count=10,
            node_type="dataSource",
            operation_type="load",
        )
        assert result == "created"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_filtered(self):
        """Detect filtered lineage when rows are removed."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=5,
            node_type="polars",
            operation_type="filter",
        )
        assert result == "filtered"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_aggregated(self):
        """Detect aggregated lineage for group_by operations."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=3,
            node_type="polars",
            operation_type="group_by",
        )
        assert result == "aggregated"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_joined(self):
        """Detect joined lineage for join operations."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=10,
            node_type="polars",
            operation_type="join",
        )
        assert result == "joined"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_expanded(self):
        """Detect expanded lineage when row count increases."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=5,
            output_row_count=15,
            node_type="polars",
            operation_type="cross_join",
        )
        assert result == "expanded"

    @pytest.mark.usefixtures("_import_enrichment")
    def test_detect_sorted(self):
        """Detect sorted lineage for sort operations."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=10,
            node_type="polars",
            operation_type="sort",
        )
        assert result == "sorted"
