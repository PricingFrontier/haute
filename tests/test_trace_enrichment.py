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

import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.trace import (
    TraceResult,
    TraceStep,
    execute_trace,
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
                    _transform_node("lookup", "df = policies.join(rates, on='region')"),
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
                        "df = policies.join(rates, on='region', how='left')\n"
                        "df = df.with_columns(pl.col('rate').fill_null(1.0))",
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
                    _transform_node("lookup", "df = policies.join(rates, on='region', how='left')"),
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
                        "lookup", "df = policies.join(rates, on=['region', 'vehicle_type'])"
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
                    _transform_node("join_age", "df = policies.join(age_rates, on='age_band')"),
                    _transform_node("join_region", "df = join_age.join(region_rates, on='region')"),
                    _transform_node(
                        "calc",
                        "df = df.with_columns(premium="
                        "pl.col('base_premium') * pl.col('age_rate') * pl.col('region_rate'))",
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
                    _transform_node("j1", "df = data.join(a, on='key_a')"),
                    _transform_node("j2", "df = j1.join(b, on='key_b')"),
                    _transform_node(
                        "calc", "df = df.with_columns(total=pl.col('rate_a') + pl.col('rate_b'))"
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
                    _transform_node("j1", "df = data.join(a, on='key_a')"),
                    _transform_node("j2", "df = j1.join(b, on='key_b')"),
                    _transform_node(
                        "calc",
                        "df = df.with_columns(min_rate=pl.min_horizontal('rate_a', 'rate_b'))",
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
                    _transform_node("j1", "df = data.join(a, on='key_a')"),
                    _transform_node("j2", "df = j1.join(b, on='key_b')"),
                    _transform_node(
                        "calc",
                        "df = df.with_columns(max_rate=pl.max_horizontal('rate_a', 'rate_b'))",
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
                    _transform_node("lookup", "df = policies.join(rates, on='region', how='left')"),
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
                    _transform_node("lookup", "df = policies.join(rates, on='region_code')"),
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
                    _transform_node("lookup", "df = policies.join(rates, on='region')"),
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
                    _transform_node("lookup", "df = policies.join(rates, on='region', how='left')"),
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
                    _transform_node("j1", "df = data.join(a, on='key_a')"),
                    _transform_node("j2", "df = j1.join(b, on='key_b', how='left')"),
                    _transform_node(
                        "calc",
                        "df = df.with_columns("
                        "total=pl.col('rate_a') + pl.col('rate_b').fill_null(0.0))",
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
                        "df = data.join(rates, on='key', how='left')\n"
                        "df = df.with_columns(pl.col('rate').fill_null(1.0))",
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
                    _transform_node("lookup", "df = policies.join(rates, on='region')"),
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
                    _transform_node("lookup", "df = policies.join(rates, on='code')"),
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
                    _transform_node("lookup", "df = policies.join(rates, on='region')"),
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
                    _transform_node("lookup", "df = policies.join(rates, on='region')"),
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
            "pl.when(pl.col('age') < 25)"
            ".then(pl.lit('young')).otherwise(pl.lit('adult')).alias('age_band'),"
            "pl.when(pl.col('fuel') == 'diesel')"
            ".then(pl.lit(1.2)).otherwise(pl.lit(1.0)).alias('fuel_factor')"
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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

        code = (
            "df = df.with_columns("
            "pl.when(pl.lit(False))"
            ".then(pl.lit('never')).otherwise(pl.lit('always')).alias('band'))"
        )
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
            "df = df.with_columns("
            "pl.when(pl.col('age') < 30)"
            ".then(pl.lit('young')).otherwise(pl.lit('old')).alias('band')"
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
            "df = df.with_columns("
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
            "df = df.with_columns("
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
                        "df = df.with_columns("
                        "prediction=pl.col('feature_a') * 0.5 + pl.col('feature_b') * 0.1)",
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
                        "model",
                        "df = df.with_columns(pred=pl.col('feature_a') + pl.col('feature_b'))",
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
                    _transform_node(
                        "model", "df = df.with_columns(raw_pred=pl.col('feature') * 2.0)"
                    ),
                    _transform_node(
                        "clip", "df = df.with_columns(pred=pl.col('raw_pred').clip(0, 150))"
                    ),
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
                        "df = df.with_columns(pred=pl.col('feature_a') + pl.col('feature_b'))",
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
                        "df = df.with_columns(pred=pl.col('x') * 2, confidence=pl.lit(0.95))",
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
                        "df = df.with_columns(pred=pl.col('feat1') * 3 + pl.col('feat2'))",
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
                    _transform_node("model", "df = df.with_columns(pred=pl.col('x') ** 2)"),
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
                    _transform_node("model", "df = df.with_columns(pred=pl.col('x').log())"),
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
                    _transform_node("raw", "df = df.with_columns(raw_pred=pl.col('x') * 0.3)"),
                    _transform_node(
                        "adj", "df = df.with_columns(final_pred=pl.col('raw_pred') + 1.0)"
                    ),
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
                    _transform_node("model", "df = df.with_columns(pred=pl.col('x') * 2)"),
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
                    _transform_node("model", "df = df.with_columns(pred=pl.col('x') * 2)"),
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
                    _transform_node("model", "df = df.with_columns(pred=pl.col('x') * 2)"),
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
                    _transform_node("model", "df = df.with_columns(pred=pl.col('x') * 2)"),
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
                    _transform_node(
                        "model", "df = df.with_columns(pred=pl.col('a') + pl.col('b'))"
                    ),
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
                        "df = df.with_columns("
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
                    _transform_node(
                        "calc",
                        "df = df.with_columns(adjusted=pl.col('base') * pl.col('multiplier'))",
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("expand", "df = data.join(scenarios, how='cross')"),
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
                    _transform_node("t", "df = df.with_columns(z=pl.col('x') + pl.col('y'))"),
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
                    _transform_node("t", "df = df.with_columns(y=pl.col('x') * 2)"),
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
                    _transform_node("t", "df = df.rename({'old_name': 'new_name'})"),
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
                    _transform_node("filt", "df = df.filter(pl.col('val') > 25)"),
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
                    _transform_node("filt", "df = df.filter(pl.col('val') > 15)"),
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
                        "agg",
                        "df = df.group_by('region').agg(pl.col('premium').sum()).sort('region')",
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
                    _transform_node(
                        "agg", "df = df.group_by('region').agg(pl.col('premium').sum())"
                    ),
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
                    _transform_node("join", "df = a.join(b, on='key', how='left')"),
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
                    _transform_node("join", "df = a.join(b, on='key')"),
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
                    _transform_node("join", "df = a.join(b, on='key')"),
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
                    _transform_node("expand", "df = a.join(b, how='cross')"),
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
                    _transform_node("expand", "df = a.join(b, how='cross')"),
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
                    _transform_node("sorted", "df = df.sort('id')"),
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
                    _transform_node("sorted", "df = df.sort('id')"),
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
                    _transform_node("sorted", "df = df.sort('score', descending=True)"),
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
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

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
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

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
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

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
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

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
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

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
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

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


# ===========================================================================
# Extended coverage tests for haute._trace_enrichment
# ===========================================================================


class TestMatchContinuousRule:
    """Tests for _match_continuous_rule with all operator combinations."""

    def test_less_than_true(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {"op1": "<", "val1": 10}) is True

    def test_less_than_false(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(10, {"op1": "<", "val1": 10}) is False

    def test_less_than_equal_true(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(10, {"op1": "<=", "val1": 10}) is True

    def test_less_than_equal_false(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(11, {"op1": "<=", "val1": 10}) is False

    def test_greater_than_true(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(15, {"op1": ">", "val1": 10}) is True

    def test_greater_than_false(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(10, {"op1": ">", "val1": 10}) is False

    def test_greater_than_equal_true(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(10, {"op1": ">=", "val1": 10}) is True

    def test_greater_than_equal_false(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(9, {"op1": ">=", "val1": 10}) is False

    def test_equal_single_eq(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(10, {"op1": "=", "val1": 10}) is True

    def test_equal_double_eq(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(10, {"op1": "==", "val1": 10}) is True

    def test_not_equal(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {"op1": "!=", "val1": 10}) is True
        assert _match_continuous_rule(10, {"op1": "!=", "val1": 10}) is False

    def test_not_equal_diamond(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {"op1": "<>", "val1": 10}) is True
        assert _match_continuous_rule(10, {"op1": "<>", "val1": 10}) is False

    def test_two_conditions_range(self):
        """Test a range rule: val >= 10 AND val < 20."""
        from haute._trace_enrichment import _match_continuous_rule

        rule = {"op1": ">=", "val1": 10, "op2": "<", "val2": 20}
        assert _match_continuous_rule(10, rule) is True
        assert _match_continuous_rule(15, rule) is True
        assert _match_continuous_rule(20, rule) is False
        assert _match_continuous_rule(9, rule) is False

    def test_none_input_returns_false(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(None, {"op1": "<", "val1": 10}) is False

    def test_non_numeric_input_returns_false(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule("abc", {"op1": "<", "val1": 10}) is False

    def test_empty_rule_returns_true(self):
        """No operators means no conditions to fail."""
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {}) is True

    def test_missing_val_skips_condition(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {"op1": "<", "val1": ""}) is True

    def test_non_numeric_threshold_skips(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {"op1": "<", "val1": "abc"}) is True

    def test_unknown_operator_skips(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule(5, {"op1": "??", "val1": 10}) is True

    def test_string_numeric_input(self):
        from haute._trace_enrichment import _match_continuous_rule

        assert _match_continuous_rule("5", {"op1": "<", "val1": 10}) is True


class TestEnrichBandingRealConfig:
    """Tests for enrich_banding with real Haute config format."""

    def test_categorical_banding_match(self):
        """Categorical banding matches input value to rule."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "vehicle_type",
                    "outputColumn": "vehicle_band",
                    "banding": "categorical",
                    "rules": [
                        {"value": "sedan", "assignment": "A"},
                        {"value": "suv", "assignment": "B"},
                        {"value": "truck", "assignment": "C"},
                    ],
                    "default": "D",
                }
            ]
        }
        input_row = {"vehicle_type": "suv"}
        output_row = {"vehicle_band": "B"}

        result = enrich_banding(config, input_row, output_row)

        assert result["detail_type"] == "banding"
        assert result["selected_band"] == "B"
        assert result["rule_index"] == 1
        assert result["is_default"] is False
        assert result["input_value"] == "suv"
        assert len(result["factors"]) == 1
        factor = result["factors"][0]
        assert factor["banding_type"] == "categorical"
        assert factor["column"] == "vehicle_type"
        assert factor["output_column"] == "vehicle_band"

    def test_categorical_banding_default(self):
        """Categorical banding falls to default when no rule matches."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "vehicle_type",
                    "outputColumn": "vehicle_band",
                    "banding": "categorical",
                    "rules": [
                        {"value": "sedan", "assignment": "A"},
                    ],
                    "default": "X",
                }
            ]
        }
        input_row = {"vehicle_type": "motorcycle"}
        output_row = {"vehicle_band": "X"}

        result = enrich_banding(config, input_row, output_row)
        assert result["is_default"] is True
        assert result["rule_index"] == -1

    def test_categorical_banding_matches_runtime_string_key_semantics(self):
        """Categorical enrichment mirrors runtime remap keys instead of Python equality."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "score",
                    "outputColumn": "score_band",
                    "banding": "categorical",
                    "rules": [{"value": 1, "assignment": "one"}],
                    "default": "one",
                }
            ]
        }

        result = enrich_banding(config, {"score": 1.0}, {"score_band": "one"})

        assert result["selected_band"] == "one"
        assert result["matched_band"] == "one"
        assert result["is_default"] is True
        assert result["rule_index"] == -1

    def test_continuous_banding_match(self):
        """Continuous banding matches a range rule."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "continuous",
                    "rules": [
                        {"op1": "<", "val1": 25, "assignment": "young"},
                        {"op1": ">=", "val1": 25, "op2": "<", "val2": 65, "assignment": "adult"},
                        {"op1": ">=", "val1": 65, "assignment": "senior"},
                    ],
                    "default": "unknown",
                }
            ]
        }
        input_row = {"age": 35}
        output_row = {"age_band": "adult"}

        result = enrich_banding(config, input_row, output_row)
        assert result["selected_band"] == "adult"
        assert result["matched_band"] == "adult"
        assert result["input_column"] == "age"
        assert result["output_column"] == "age_band"
        assert result["lower_bound"] == 25.0
        assert result["upper_bound"] == 65.0
        assert result["rule_index"] == 1
        assert result["is_default"] is False

    def test_breakpoint_banding_match_reports_original_value_and_range(self):
        """Breakpoint banding exposes the value before banding and matched interval."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "breakpoints",
                    "rules": [
                        {"boundary": "25", "label": "young"},
                        {"boundary": "65", "label": "adult"},
                        {"boundary": "", "label": "senior"},
                    ],
                    "rightClosed": True,
                }
            ]
        }

        result = enrich_banding(config, {"age": 35}, {"age_band": "adult"})

        assert result["input_column"] == "age"
        assert result["output_column"] == "age_band"
        assert result["input_value"] == 35
        assert result["matched_band"] == "adult"
        assert result["selected_band"] == "adult"
        assert result["lower_bound"] == 25.0
        assert result["lower_inclusive"] is False
        assert result["upper_bound"] == 65.0
        assert result["upper_inclusive"] is True

    def test_compact_categorical_banding_trace_matches_rule(self):
        """Trace enrichment accepts the compact sidecar rule map."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "fuel_type",
                    "outputColumn": "fuel_band",
                    "banding": "categorical",
                    "rules": {"Petrol": "Standard", "Electric": "Green"},
                }
            ]
        }

        result = enrich_banding(config, {"fuel_type": "Petrol"}, {"fuel_band": "Standard"})

        assert result["input_column"] == "fuel_type"
        assert result["input_value"] == "Petrol"
        assert result["selected_band"] == "Standard"
        assert result["matched_value"] == "Petrol"
        assert result["rule_index"] == 0

    def test_compact_breakpoint_banding_trace_reports_range(self):
        """Trace enrichment expands compact breakpoint maps before range matching."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "breakpoints",
                    "rules": {"25": "young", "65": "adult", "": "senior"},
                    "rightClosed": True,
                }
            ]
        }

        result = enrich_banding(config, {"age": 35}, {"age_band": "adult"})

        assert result["input_value"] == 35
        assert result["selected_band"] == "adult"
        assert result["lower_bound"] == 25.0
        assert result["upper_bound"] == 65.0

    def test_multiple_factors_focuses_top_level_detail_on_traced_output(self):
        """Multi-factor banding reports the traced output's source value at top level."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "continuous",
                    "rules": [{"op1": "<", "val1": 25, "assignment": "young"}],
                },
                {
                    "column": "region",
                    "outputColumn": "region_band",
                    "banding": "categorical",
                    "rules": [{"value": "north", "assignment": "N"}],
                },
            ]
        }

        result = enrich_banding(
            config,
            {"age": 40, "region": "north"},
            {"age_band": None, "region_band": "N"},
            traced_column="region_band",
        )

        assert result["input_column"] == "region"
        assert result["output_column"] == "region_band"
        assert result["input_value"] == "north"
        assert result["matched_band"] == "N"
        assert result["selected_band"] == "N"

    def test_multiple_factors_do_not_fallback_when_traced_output_is_unknown(self):
        """Unknown traced outputs keep factor details without inventing a top-level match."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "continuous",
                    "rules": [{"op1": "<", "val1": 25, "assignment": "young"}],
                },
                {
                    "column": "region",
                    "outputColumn": "region_band",
                    "banding": "categorical",
                    "rules": [{"value": "north", "assignment": "N"}],
                },
            ]
        }

        result = enrich_banding(
            config,
            {"age": 20, "region": "north"},
            {"age_band": "young", "region_band": "N"},
            traced_column="passed_through_column",
        )

        assert len(result["factors"]) == 2
        assert "input_column" not in result
        assert "output_column" not in result
        assert "selected_band" not in result

    def test_continuous_banding_no_match_uses_default(self):
        """Continuous banding falls to default when no rule matches."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "score",
                    "outputColumn": "score_band",
                    "banding": "continuous",
                    "rules": [
                        {"op1": "<", "val1": 0, "assignment": "negative"},
                    ],
                    "default": "other",
                }
            ]
        }
        input_row = {"score": 50}
        output_row = {"score_band": "other"}

        result = enrich_banding(config, input_row, output_row)
        assert result["is_default"] is True
        assert result["rule_index"] == -1

    def test_multiple_factors(self):
        """Multiple banding factors processed independently."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "continuous",
                    "rules": [{"op1": "<", "val1": 25, "assignment": "young"}],
                    "default": None,
                },
                {
                    "column": "region",
                    "outputColumn": "region_band",
                    "banding": "categorical",
                    "rules": [{"value": "north", "assignment": "N"}],
                    "default": None,
                },
            ]
        }
        input_row = {"age": 20, "region": "north"}
        output_row = {"age_band": "young", "region_band": "N"}

        result = enrich_banding(config, input_row, output_row)
        assert len(result["factors"]) == 2
        # Top-level convenience uses first factor
        assert result["selected_band"] == "young"
        assert result["input_value"] == 20

    def test_continuous_banding_none_input(self):
        """Continuous banding with None input value does not match any rule."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "factors": [
                {
                    "column": "age",
                    "outputColumn": "age_band",
                    "banding": "continuous",
                    "rules": [{"op1": "<", "val1": 25, "assignment": "young"}],
                    "default": "unknown",
                }
            ]
        }
        input_row = {"age": None}
        output_row = {"age_band": "unknown"}

        result = enrich_banding(config, input_row, output_row)
        assert result["is_default"] is True


class TestEnrichSingleTable:
    """Tests for _enrich_single_table covering entry matching and defaults."""

    def test_entry_match(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "factors": ["region"],
            "entries": [
                {"region": "north", "rate": 1.1},
                {"region": "south", "rate": 0.9},
            ],
            "outputColumn": "rate",
            "defaultValue": None,
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "north"},
            output_row={"rate": 1.1},
        )
        assert result["matched"] is True
        assert result["rate_value"] == 1.1
        assert result["matched_entry"] is not None
        assert result["matched_entry"]["region"] == "north"
        assert result["default_used"] is False

    def test_explicit_table_detail_contract(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "name": "territory_factor",
            "factors": ["state", "vehicle_type"],
            "entries": [
                {"state": "CA", "vehicle_type": "sedan", "value": 1.15},
            ],
            "outputColumn": "territory_factor",
            "defaultValue": "1.0",
        }
        result = _enrich_single_table(
            table,
            input_row={"state": "CA", "vehicle_type": "sedan"},
            output_row={"territory_factor": 1.15},
        )

        assert result == {
            "name": "territory_factor",
            "output_column": "territory_factor",
            "lookup_keys": {"state": "CA", "vehicle_type": "sedan"},
            "factors": [
                {"column": "state", "value": "CA"},
                {"column": "vehicle_type", "value": "sedan"},
            ],
            "selected_value": 1.15,
            "rate_value": 1.15,
            "matched": True,
            "default_used": False,
            "status": "matched",
            "default_value": 1.0,
            "matched_entry": {"state": "CA", "vehicle_type": "sedan", "value": 1.15},
        }

    def test_no_entry_match_uses_default(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "factors": ["region"],
            "entries": [
                {"region": "north", "rate": 1.1},
            ],
            "outputColumn": "rate",
            "defaultValue": "0.5",
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"rate": 0.5},
        )
        assert result["default_used"] is True
        assert result["matched"] is False
        assert result["default_value"] == 0.5

    def test_no_match_no_default(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "factors": ["region"],
            "entries": [{"region": "north", "rate": 1.1}],
            "outputColumn": "rate",
            "defaultValue": None,
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"rate": None},
        )
        assert result["matched"] is False
        assert result["default_used"] is False
        assert result["rate_value"] is None

    def test_default_value_non_numeric(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "factors": ["region"],
            "entries": [],
            "outputColumn": "rate",
            "defaultValue": "not_a_number",
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"rate": 0.5},
        )
        # non-numeric default can't be parsed, so default_val is None
        assert result["default_value"] is None

    def test_default_value_empty_string(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "factors": ["region"],
            "entries": [],
            "outputColumn": "rate",
            "defaultValue": "",
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"rate": 0.5},
        )
        assert result["default_value"] is None

    def test_default_value_infinity(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "factors": ["region"],
            "entries": [],
            "outputColumn": "rate",
            "defaultValue": "inf",
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"rate": 0.5},
        )
        # infinity is not finite, so default_val should be None
        assert result["default_value"] is None

    def test_multi_factor_entry_match(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "name": "region_tier_factor",
            "factors": ["region", "tier"],
            "entries": [
                {"region": "north", "tier": "gold", "rate": 1.5},
                {"region": "north", "tier": "silver", "rate": 1.2},
            ],
            "outputColumn": "rate",
            "defaultValue": None,
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "north", "tier": "silver"},
            output_row={"rate": 1.2},
        )
        assert result["matched"] is True
        assert result["matched_entry"]["tier"] == "silver"
        assert result["lookup_keys"] == {"region": "north", "tier": "silver"}
        assert result["name"] == "region_tier_factor"
        assert result["factors"] == [
            {"column": "region", "value": "north"},
            {"column": "tier", "value": "silver"},
        ]
        assert result["selected_value"] == 1.2
        assert result["status"] == "matched"

    def test_duplicate_entries_follow_runtime_keep_last_semantics(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "name": "region_factor",
            "factors": ["region"],
            "entries": [
                {"region": "north", "value": 1.1, "version": "old"},
                {"region": "north", "value": 1.3, "version": "new"},
            ],
            "outputColumn": "region_factor",
            "defaultValue": None,
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "north"},
            output_row={"region_factor": 1.3},
        )
        assert result["matched"] is True
        assert result["selected_value"] == 1.3
        assert result["status"] == "matched"
        assert result["matched_entry"] == {"region": "north", "value": 1.3, "version": "new"}

    def test_no_match_without_default_has_explicit_status(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "name": "region_factor",
            "factors": ["region"],
            "entries": [{"region": "north", "value": 1.1}],
            "outputColumn": "region_factor",
            "defaultValue": None,
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"region_factor": None},
        )
        assert result["matched"] is False
        assert result["status"] == "no_match"

    def test_default_used_has_explicit_status(self):
        from haute._trace_enrichment import _enrich_single_table

        table = {
            "name": "region_factor",
            "factors": ["region"],
            "entries": [{"region": "north", "value": 1.1}],
            "outputColumn": "region_factor",
            "defaultValue": "1.0",
        }
        result = _enrich_single_table(
            table,
            input_row={"region": "west"},
            output_row={"region_factor": 1.0},
        )
        assert result["default_used"] is True
        assert result["status"] == "default"


class TestEnrichRatingStepRealConfig:
    """Tests for enrich_rating_step with real Haute config (tables list)."""

    def test_single_table(self):
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [
                {
                    "name": "region_factor",
                    "factors": ["region"],
                    "entries": [
                        {"region": "north", "value": 1.1},
                        {"region": "south", "value": 0.9},
                    ],
                    "outputColumn": "rate",
                    "defaultValue": None,
                }
            ]
        }
        result = enrich_rating_step(
            config,
            input_row={"region": "north"},
            output_row={"rate": 1.1},
        )
        assert result["detail_type"] == "rating_step"
        assert result["matched_key"] == {"region": "north"}
        assert result["rate_value"] == 1.1
        assert result["matched"] is True
        assert "tables" in result
        assert len(result["tables"]) == 1
        assert result["tables"][0]["name"] == "region_factor"
        assert result["tables"][0]["factors"] == [{"column": "region", "value": "north"}]
        assert result["tables"][0]["selected_value"] == 1.1

    def test_compact_table_entries_trace_like_canonical_rows(self):
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "entries": {"1-3": {"comprehensive": 0.9}},
                    "outputColumn": "vehicle_factor",
                    "defaultValue": "1.0",
                }
            ]
        }
        result = enrich_rating_step(
            config,
            input_row={"vehicle_age_band": "1-3", "cover_type": "comprehensive"},
            output_row={"vehicle_factor": 0.9},
        )

        assert result["matched_key"] == {
            "vehicle_age_band": "1-3",
            "cover_type": "comprehensive",
        }
        assert result["matched"] is True
        assert result["tables"][0]["matched_entry"] == {
            "vehicle_age_band": "1-3",
            "cover_type": "comprehensive",
            "value": 0.9,
        }

    def test_multiple_tables_with_combined_column(self):
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [
                {
                    "name": "region_factor",
                    "factors": ["region"],
                    "entries": [{"region": "north", "value": 1.1}],
                    "outputColumn": "rate_a",
                    "defaultValue": None,
                },
                {
                    "name": "tier_factor",
                    "factors": ["tier"],
                    "entries": [{"tier": "gold", "value": 2.0}],
                    "outputColumn": "rate_b",
                    "defaultValue": None,
                },
            ],
            "operation": "multiply",
            "combinedColumn": "combined_rate",
        }
        result = enrich_rating_step(
            config,
            input_row={"region": "north", "tier": "gold"},
            output_row={"rate_a": 1.1, "rate_b": 2.0, "combined_rate": 2.2},
        )
        assert result["detail_type"] == "rating_step"
        assert "combined" in result
        assert result["combined"]["column"] == "combined_rate"
        assert result["combined"]["operation"] == "multiply"
        assert result["combined"]["value"] == 2.2
        assert result["combined"]["input_values"] == [1.1, 2.0]
        # Multiple tables: matched_key is union of all lookup keys
        assert result["matched_key"] == {"region": "north", "tier": "gold"}
        # rate_value is the combined value
        assert result["rate_value"] == 2.2
        # matched if any table matched
        assert result["matched"] is True

    def test_combined_outputs_include_base_value_and_named_inputs(self):
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "entries": [
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "comprehensive",
                            "value": 0.9,
                        }
                    ],
                    "outputColumn": "vehicle_factor",
                    "defaultValue": "1.0",
                },
                {
                    "name": "channel_factor",
                    "factors": ["channel"],
                    "entries": [{"channel": "direct", "value": 1.2}],
                    "outputColumn": "channel_factor",
                    "defaultValue": "1.0",
                },
            ],
            "combinedOutputs": [
                {
                    "outputColumn": "technical_premium_factor",
                    "operation": "multiply",
                    "baseValue": "100",
                },
                {
                    "outputColumn": "additive_adjustment",
                    "operation": "add",
                    "baseValue": "10",
                },
            ],
        }
        result = enrich_rating_step(
            config,
            input_row={
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "channel": "direct",
            },
            output_row={
                "vehicle_factor": 0.9,
                "channel_factor": 1.2,
                "technical_premium_factor": 108.0,
                "additive_adjustment": 12.1,
            },
        )

        assert result["combined_outputs"] == [
            {
                "column": "technical_premium_factor",
                "operation": "multiply",
                "base_value": 100.0,
                "input_values": {"vehicle_factor": 0.9, "channel_factor": 1.2},
                "value": 108.0,
            },
            {
                "column": "additive_adjustment",
                "operation": "add",
                "base_value": 10.0,
                "input_values": {"vehicle_factor": 0.9, "channel_factor": 1.2},
                "value": 12.1,
            },
        ]

    def test_combined_outputs_without_tables_still_trace(self):
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [],
            "combinedOutputs": [
                {
                    "outputColumn": "base_premium",
                    "operation": "multiply",
                    "baseValue": "100",
                }
            ],
        }
        result = enrich_rating_step(
            config,
            input_row={"policy_id": 1},
            output_row={"policy_id": 1, "base_premium": 100.0},
        )

        assert result["detail_type"] == "rating_step"
        assert result["tables"] == []
        assert result["combined_outputs"] == [
            {
                "column": "base_premium",
                "operation": "multiply",
                "base_value": 100.0,
                "input_values": {},
                "value": 100.0,
            }
        ]
        assert result["rate_value"] == 100.0
        assert result["matched"] is False

    def test_invalid_combined_outputs_operation_raises(self):
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [],
            "combinedOutputs": [
                {
                    "outputColumn": "technical_premium",
                    "operation": "divide",
                    "baseValue": "100",
                }
            ],
        }

        with pytest.raises(ValueError, match="Unsupported rating combine operation"):
            enrich_rating_step(
                config,
                input_row={"policy_id": 1},
                output_row={"technical_premium": 100.0},
            )

    def test_multiple_tables_no_combined_column(self):
        """Multiple tables without a combinedColumn should not have combined key."""
        from haute._trace_enrichment import enrich_rating_step

        config = {
            "tables": [
                {
                    "factors": ["region"],
                    "entries": [{"region": "north", "rate_a": 1.1}],
                    "outputColumn": "rate_a",
                    "defaultValue": None,
                },
                {
                    "factors": ["tier"],
                    "entries": [{"tier": "gold", "rate_b": 2.0}],
                    "outputColumn": "rate_b",
                    "defaultValue": None,
                },
            ],
        }
        result = enrich_rating_step(
            config,
            input_row={"region": "north", "tier": "gold"},
            output_row={"rate_a": 1.1, "rate_b": 2.0},
        )
        assert "combined" not in result

    def test_join_key_list(self):
        """Simplified config with join_key as a list."""
        from haute._trace_enrichment import enrich_rating_step

        config = {"join_key": ["region", "tier"], "rate_column": "rate"}
        result = enrich_rating_step(
            config,
            input_row={"region": "north", "tier": "gold"},
            output_row={"rate": 1.5},
        )
        assert result["matched_key"] == {"region": "north", "tier": "gold"}
        assert result["matched"] is True

    def test_empty_tables_falls_to_simple_config(self):
        """Empty tables list falls back to simple config."""
        from haute._trace_enrichment import enrich_rating_step

        config = {"tables": [], "join_key": "region", "rate_column": "rate"}
        result = enrich_rating_step(
            config,
            input_row={"region": "north"},
            output_row={"rate": 1.1},
        )
        assert result["matched_key"] == {"region": "north"}


class TestEnrichModelScoreRealConfig:
    """Tests for enrich_model_score with real Haute config."""

    def test_source_type_run(self):
        from haute._trace_enrichment import enrich_model_score

        config = {
            "output_column": "prediction",
            "sourceType": "run",
            "run_id": "abc123",
            "task": "regression",
        }
        input_row = {"feat1": 1.0, "feat2": 2.0}
        output_row = {"feat1": 1.0, "feat2": 2.0, "prediction": 0.85}

        result = enrich_model_score(config, input_row, output_row)
        assert result["detail_type"] == "model_score"
        assert result["prediction_value"] == 0.85
        assert result["prediction_column"] == "prediction"
        assert result["model_identity"]["source_type"] == "run"
        assert result["model_identity"]["run_id"] == "abc123"
        assert result["model_identity"]["task"] == "regression"

    def test_source_type_registered(self):
        from haute._trace_enrichment import enrich_model_score

        config = {
            "output_column": "score",
            "sourceType": "registered",
            "registered_model": "my_model",
            "version": "3",
            "task": "classification",
        }
        input_row = {"x1": 10, "x2": 20}
        output_row = {"x1": 10, "x2": 20, "score": 0.72}

        result = enrich_model_score(config, input_row, output_row)
        assert result["model_identity"]["source_type"] == "registered"
        assert result["model_identity"]["registered_model"] == "my_model"
        assert result["model_identity"]["version"] == "3"
        assert result["model_identity"]["task"] == "classification"

    def test_feature_columns_inferred(self):
        """Feature columns inferred from input_row minus prediction column."""
        from haute._trace_enrichment import enrich_model_score

        config = {"output_column": "pred"}
        input_row = {"feat_a": 1, "feat_b": 2, "pred": 0.5}
        output_row = {"feat_a": 1, "feat_b": 2, "pred": 0.9}

        result = enrich_model_score(config, input_row, output_row)
        assert "feat_a" in result["feature_columns"]
        assert "feat_b" in result["feature_columns"]
        assert "pred" not in result["feature_columns"]
        assert result["feature_values"]["feat_a"] == 1
        assert result["feature_values"]["feat_b"] == 2

    def test_feature_columns_explicit(self):
        """Explicit feature_columns used when provided."""
        from haute._trace_enrichment import enrich_model_score

        config = {
            "output_column": "pred",
            "feature_columns": ["feat_a"],
        }
        input_row = {"feat_a": 1, "feat_b": 2}
        output_row = {"feat_a": 1, "feat_b": 2, "pred": 0.9}

        result = enrich_model_score(config, input_row, output_row)
        assert result["feature_columns"] == ["feat_a"]

    def test_feature_columns_use_contract_inputs_before_inference(self):
        """Model-score detail should prefer the node contract over all input columns."""
        from haute._trace_enrichment import enrich_model_score

        config = {
            "output_column": "pred",
            "contract": {"inputs": ["feat_b", "feat_a"]},
        }
        input_row = {"feat_a": 1, "feat_b": 2, "technical_id": "Q1"}
        output_row = {"feat_a": 1, "feat_b": 2, "technical_id": "Q1", "pred": 0.9}

        result = enrich_model_score(config, input_row, output_row)
        assert result["feature_columns"] == ["feat_b", "feat_a"]
        assert result["feature_values"] == {"feat_b": 2, "feat_a": 1}

    def test_catboost_explanation_attached_for_mlflow_cbm_config(self, monkeypatch):
        """CatBoost MLflow configs should attach structured per-row explanation detail."""
        from haute._trace_enrichment import enrich_model_score

        def fake_explain(config, input_row, output_row, prediction_column, prediction_value):
            assert config["artifact_path"] == "model.cbm"
            assert input_row["feat_a"] == 1
            assert output_row["pred"] == 0.9
            assert prediction_column == "pred"
            assert prediction_value == 0.9
            return {
                "type": "catboost_shap",
                "method": "catboost_shap",
                "status": "ok",
                "output_space": "prediction",
                "base_value": 0.3,
                "prediction_from_shap": 0.9,
                "output_difference": 0.0,
                "contributions": [
                    {"feature": "feat_a", "feature_value": 1, "shap_value": 0.6},
                ],
            }

        monkeypatch.setattr(
            "haute._model_explainability.explain_model_score_from_config",
            fake_explain,
        )
        config = {
            "output_column": "pred",
            "sourceType": "run",
            "run_id": "abc",
            "artifact_path": "model.cbm",
            "contract": {"inputs": ["feat_a"]},
        }

        result = enrich_model_score(config, {"feat_a": 1}, {"feat_a": 1, "pred": 0.9})

        assert result["explanation"]["method"] == "catboost_shap"
        assert result["explanation"]["contributions"][0]["feature"] == "feat_a"

    def test_rustystats_explanation_attached_for_mlflow_rsglm_config(self, monkeypatch):
        """RustyStats GLM MLflow configs should attach native contribution detail."""
        from haute._trace_enrichment import enrich_model_score

        def fake_explain(config, input_row, output_row, prediction_column, prediction_value):
            assert config["artifact_path"] == "conversion.rsglm"
            assert input_row["difference_to_market"] == -10.0
            assert output_row["conversion_prediction"] == 0.42
            assert prediction_column == "conversion_prediction"
            assert prediction_value == 0.42
            return {
                "type": "rustystats_glm_contributions",
                "method": "rustystats_glm_contributions",
                "status": "ok",
                "output_space": "linear_predictor",
                "prediction_space": "response",
                "base_value": 0.1,
                "prediction_from_contributions": 0.2,
                "prediction_value": 0.42,
                "contributions": [
                    {
                        "feature": "difference_to_market",
                        "feature_value": -10.0,
                        "contribution": 0.1,
                    },
                ],
            }

        monkeypatch.setattr(
            "haute._model_explainability.explain_model_score_from_config",
            fake_explain,
        )
        config = {
            "output_column": "conversion_prediction",
            "sourceType": "run",
            "run_id": "abc",
            "artifact_path": "conversion.rsglm",
            "contract": {"inputs": ["difference_to_market"]},
        }

        result = enrich_model_score(
            config,
            {"difference_to_market": -10.0},
            {"difference_to_market": -10.0, "conversion_prediction": 0.42},
        )

        assert result["explanation"]["method"] == "rustystats_glm_contributions"
        assert result["explanation"]["contributions"][0]["feature"] == "difference_to_market"

    def test_rustystats_explanation_error_is_not_mislabeled_as_catboost(self, monkeypatch):
        """RustyStats GLM explanation failures keep GLM method metadata."""
        from haute._model_explainability import ModelExplanationError
        from haute._trace_enrichment import enrich_model_score

        def fake_explain(*args, **kwargs):
            raise ModelExplanationError("broken GLM explanation")

        monkeypatch.setattr(
            "haute._model_explainability.explain_model_score_from_config",
            fake_explain,
        )
        config = {
            "output_column": "conversion_prediction",
            "sourceType": "run",
            "run_id": "abc",
            "artifact_path": "conversion.rsglm",
            "contract": {"inputs": ["difference_to_market"]},
        }

        result = enrich_model_score(
            config,
            {"difference_to_market": -10.0},
            {"difference_to_market": -10.0, "conversion_prediction": 0.42},
        )

        assert result["explanation"]["type"] == "rustystats_glm_contributions"
        assert result["explanation"]["method"] == "rustystats_glm_contributions"
        assert result["explanation"]["status"] == "error"
        assert result["explanation"]["error"] == "broken GLM explanation"

    def test_explanation_error_handling_survives_unsupported_artifact(self, monkeypatch):
        """Defensive: if the supported-config check and the metadata lookup
        disagree at runtime, the outer enrich_model_score must still return a
        well-formed ``model_score`` detail (not crash through the catch-all).

        This pins the contract that ``explanation_error_metadata_for_config``
        is on the error-handling path and may not raise — otherwise a future
        edit could escalate an internal mismatch into "model score enrichment
        failed: ..." and lose all the structured detail.
        """
        from haute._model_explainability import ModelExplanationError
        from haute._trace_enrichment import enrich_model_score

        def fake_explain(*args, **kwargs):
            raise ModelExplanationError("some failure")

        # Force the metadata lookup to take its unreachable branch by giving
        # it an artifact_path the supported-config check would never accept.
        monkeypatch.setattr(
            "haute._model_explainability.explain_model_score_from_config",
            fake_explain,
        )
        monkeypatch.setattr(
            "haute._model_explainability._config_requests_supported_explanation",
            lambda config: True,
        )

        config = {
            "output_column": "pred",
            "sourceType": "run",
            "run_id": "abc",
            "artifact_path": "model.unknown",
            "contract": {"inputs": ["feat_a"]},
        }

        result = enrich_model_score(config, {"feat_a": 1}, {"feat_a": 1, "pred": 0.9})

        # The outer detail is intact (no "model score enrichment failed: ..."
        # crash through the generic catch-all), and the explanation carries
        # the fallback metadata.
        assert result["detail_type"] == "model_score"
        assert result["prediction_value"] == 0.9
        assert result["explanation"]["status"] == "error"
        assert result["explanation"]["type"] == "model_explanation"
        assert result["explanation"]["method"] == "model_explanation"


class TestEnrichScenarioExpansionRealConfig:
    """Tests for enrich_scenario_expansion with real config."""

    def test_scenario_and_step_columns(self):
        from haute._trace_enrichment import enrich_scenario_expansion

        config = {
            "scenario_column": "scenario",
            "step_column": "step_idx",
            "min_value": 0,
            "max_value": 100,
            "steps": 5,
        }
        input_row = {"id": 1}
        output_row = {"id": 1, "scenario": "high", "step_idx": 3}

        result = enrich_scenario_expansion(config, input_row, output_row)
        assert result["detail_type"] == "scenario_expander"
        assert result["scenario_value"] == "high"
        assert result["scenario_column"] == "scenario"
        assert result["scenario_index"] == 3
        assert result["parameters"]["min_value"] == 0
        assert result["parameters"]["max_value"] == 100
        assert result["parameters"]["steps"] == 5

    def test_column_name_fallback(self):
        """Falls back to column_name if scenario_column is not set."""
        from haute._trace_enrichment import enrich_scenario_expansion

        config = {"column_name": "scen"}
        output_row = {"scen": "base", "scenario_index": 0}

        result = enrich_scenario_expansion(config, {}, output_row)
        assert result["scenario_column"] == "scen"
        assert result["scenario_value"] == "base"
        assert result["scenario_index"] == 0

    def test_default_step_column(self):
        """Default step_column is scenario_index."""
        from haute._trace_enrichment import enrich_scenario_expansion

        config = {"scenario_column": "sc"}
        output_row = {"sc": "low", "scenario_index": 2}

        result = enrich_scenario_expansion(config, {}, output_row)
        assert result["scenario_index"] == 2


class TestEnrichLiveSwitchExtended:
    """Extended tests for enrich_live_switch."""

    def test_three_branches(self):
        from haute._trace_enrichment import enrich_live_switch

        config = {
            "input_scenario_map": {
                "branch_a": "live",
                "branch_b": "batch",
                "branch_c": "test",
            }
        }
        result = enrich_live_switch(config, source="batch")
        assert result["active_branch"] == "branch_b"
        assert result["active_scenario"] == "batch"
        assert set(result["pruned_branches"]) == {"branch_a", "branch_c"}

    def test_no_matching_branch(self):
        from haute._trace_enrichment import enrich_live_switch

        config = {
            "input_scenario_map": {"a": "x", "b": "y"},
        }
        result = enrich_live_switch(config, source="z")
        assert result["active_branch"] == ""
        assert result["active_scenario"] == ""
        assert set(result["pruned_branches"]) == {"a", "b"}

    def test_empty_map(self):
        from haute._trace_enrichment import enrich_live_switch

        config = {"input_scenario_map": {}}
        result = enrich_live_switch(config, source="live")
        assert result["detail_type"] == "live_switch"
        assert result["active_branch"] == ""
        assert result["pruned_branches"] == []


class TestDetectRowLineageTypeExtended:
    """Extended tests covering all branches of detect_row_lineage_type."""

    def test_api_input_created(self):
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(node_type="apiInput", output_row_count=5)
        assert result == "created"

    def test_live_switch_selected(self):
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(node_type="liveSwitch", output_row_count=5)
        assert result == "selected"

    def test_groupby_operation(self):
        from haute._trace_enrichment import detect_row_lineage_type

        assert detect_row_lineage_type(operation_type="groupby") == "aggregated"

    def test_agg_operation(self):
        from haute._trace_enrichment import detect_row_lineage_type

        assert detect_row_lineage_type(operation_type="agg") == "aggregated"

    def test_sort_by_operation(self):
        from haute._trace_enrichment import detect_row_lineage_type

        assert detect_row_lineage_type(operation_type="sort_by") == "sorted"

    def test_explode_operation(self):
        from haute._trace_enrichment import detect_row_lineage_type

        assert detect_row_lineage_type(operation_type="explode") == "expanded"

    def test_scenario_expand_operation(self):
        from haute._trace_enrichment import detect_row_lineage_type

        assert detect_row_lineage_type(operation_type="scenario_expand") == "expanded"

    def test_fallback_created_from_zero(self):
        """Zero input rows + positive output rows = created (fallback)."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=0,
            output_row_count=5,
            node_type="custom",
            operation_type="custom_op",
        )
        assert result == "created"

    def test_fallback_filtered_from_counts(self):
        """Output < input = filtered (fallback)."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=3,
            node_type="custom",
            operation_type="custom_op",
        )
        assert result == "filtered"

    def test_fallback_expanded_from_counts(self):
        """Output > input = expanded (fallback)."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=5,
            output_row_count=15,
            node_type="custom",
            operation_type="custom_op",
        )
        assert result == "expanded"

    def test_fallback_passthrough_equal_counts(self):
        """Output == input = passthrough (fallback)."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=10,
            output_row_count=10,
            node_type="custom",
            operation_type="custom_op",
        )
        assert result == "passthrough"

    def test_none_input_row_count_fallback(self):
        """None input_row_count defaults to 0."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=None,
            output_row_count=5,
            node_type="custom",
            operation_type="custom_op",
        )
        assert result == "created"

    def test_no_args_passthrough(self):
        """No arguments defaults to passthrough."""
        from haute._trace_enrichment import detect_row_lineage_type

        assert detect_row_lineage_type() == "passthrough"

    def test_detect_row_lineage_type_zero_rows(self):
        """input_row_count=0 and output_row_count=0 should return passthrough."""
        from haute._trace_enrichment import detect_row_lineage_type

        result = detect_row_lineage_type(
            input_row_count=0,
            output_row_count=0,
            node_type="polars",
            operation_type="with_columns",
        )
        assert result == "passthrough"


class TestEnrichBandingEmptyInputRow:
    """Edge case: empty dict as input_row for enrich_banding."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_banding_empty_input_row(self):
        """enrich_banding handles an empty dict as input_row gracefully."""
        from haute._trace_enrichment import enrich_banding

        config = {
            "input_column": "age",
            "output_column": "age_band",
            "rules": [
                {"condition": "< 25", "value": "young"},
                {"default": "senior"},
            ],
        }
        input_data: dict = {}
        output_data = {"age_band": "senior"}
        detail = enrich_banding(config, input_data, output_data)
        assert detail["detail_type"] == "banding"
        assert detail["input_value"] is None
        assert detail["selected_band"] == "senior"


class TestEnrichRatingStepNullJoinKey:
    """Edge case: None value in join key column for enrich_rating_step."""

    @pytest.fixture()
    def _import_enrichment(self):
        pytest.importorskip(
            "haute._trace_enrichment",
            reason="trace enrichment module not available in this build",
        )

    @pytest.mark.usefixtures("_import_enrichment")
    def test_enrich_rating_step_null_join_key(self):
        """enrich_rating_step handles None value in join key column."""
        from haute._trace_enrichment import enrich_rating_step

        config = {"join_key": "region", "rate_column": "rate"}
        input_data = {"region": None, "base": 100}
        output_data = {"region": None, "base": 100, "rate": None}
        detail = enrich_rating_step(config, input_data, output_data)
        assert detail["detail_type"] == "rating_step"
        assert detail["matched_key"] == {"region": None}
        assert detail["matched"] is False
