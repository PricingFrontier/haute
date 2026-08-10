"""TDD tests for Calculation Hero features.

These tests define the specifications for the trace hero features.
They now pass against the current implementation.

Categories:
  1. Conditional Branch Indication (8 tests)
  2. Waterfall Data Generation (6 tests)
  3. Preamble Constant Resolution (5 tests)
  4. Window Function Fallback (4 tests)
  5. Intra-Node Dependency Chain (5 tests)
  6. Column Rename Tracking (4 tests)
  7. Null Explanation (4 tests)
  8. Copy/Export Data Structure (4 tests)
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from haute._expression_parser import evaluate_expression
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

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

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


def _expr(step: TraceStep) -> dict[str, Any] | None:
    """Shorthand for step.expression."""
    return step.expression


def _calc(step: TraceStep) -> dict[str, Any] | None:
    """Shorthand for step.calculation."""
    return step.calculation


# ===========================================================================
# CATEGORY 1: Conditional Branch Indication (8 tests)
# ===========================================================================


class TestConditionalBranchIndication:
    """When a conditional expression is evaluated, the trace should indicate
    which branch was taken."""

    def test_conditional_indicates_taken_branch_then(self, tmp_path):
        """when age < 25 then 1.5 otherwise 1.0 with age=22 -> taken_branch == 'then'."""
        code = "df = df.with_columns(factor=pl.when(pl.col('age') < 25).then(1.5).otherwise(1.0))"
        result = evaluate_expression(code, "factor", {"age": 22})

        assert result.result_value is not None
        assert abs(result.result_value - 1.5) < 0.01
        # NEW: taken_branch field should exist on EvaluatedExpression
        assert hasattr(result, "taken_branch"), "EvaluatedExpression must have taken_branch field"
        assert result.taken_branch == "then"

    def test_conditional_indicates_taken_branch_otherwise(self, tmp_path):
        """when age < 25 then 1.5 otherwise 1.0 with age=30 -> taken_branch == 'otherwise'."""
        code = "df = df.with_columns(factor=pl.when(pl.col('age') < 25).then(1.5).otherwise(1.0))"
        result = evaluate_expression(code, "factor", {"age": 30})

        assert result.result_value is not None
        assert abs(result.result_value - 1.0) < 0.01
        assert hasattr(result, "taken_branch"), "EvaluatedExpression must have taken_branch field"
        assert result.taken_branch == "otherwise"

    def test_chained_conditional_indicates_second_branch(self, tmp_path):
        """Three-branch conditional, second matches -> taken_branch_index == 1."""
        code = (
            "df = df.with_columns(tier=pl.when(pl.col('score') > 90).then(pl.lit('gold'))"
            ".when(pl.col('score') > 70).then(pl.lit('silver'))"
            ".otherwise(pl.lit('bronze')))"
        )
        result = evaluate_expression(code, "tier", {"score": 80})

        assert hasattr(result, "taken_branch_index"), (
            "EvaluatedExpression must have taken_branch_index field"
        )
        assert result.taken_branch_index == 1  # 0-based: second branch

    def test_chained_conditional_indicates_otherwise(self, tmp_path):
        """None of the when-branches match -> taken_branch == 'otherwise'."""
        code = (
            "df = df.with_columns(tier=pl.when(pl.col('score') > 90).then(pl.lit('gold'))"
            ".when(pl.col('score') > 70).then(pl.lit('silver'))"
            ".otherwise(pl.lit('bronze')))"
        )
        result = evaluate_expression(code, "tier", {"score": 50})

        assert hasattr(result, "taken_branch"), "EvaluatedExpression must have taken_branch field"
        assert result.taken_branch == "otherwise"
        assert hasattr(result, "taken_branch_index")
        assert result.taken_branch_index == 2  # last index = otherwise

    def test_nested_conditional_indicates_outer_and_inner_branch(self, tmp_path):
        """Nested when: verify both outer and inner branch are tracked."""
        code = (
            "df = df.with_columns(result=pl.when(pl.col('type') == pl.lit('A'))"
            ".then(pl.when(pl.col('age') < 25).then(1.5).otherwise(1.0))"
            ".otherwise(2.0))"
        )
        result = evaluate_expression(code, "result", {"type": "A", "age": 22})

        assert hasattr(result, "taken_branch"), "EvaluatedExpression must have taken_branch field"
        assert result.taken_branch == "then"
        # NEW: nested_branches field for inner conditionals
        assert hasattr(result, "nested_branches"), (
            "EvaluatedExpression must have nested_branches for nested conditionals"
        )
        assert len(result.nested_branches) >= 1
        assert result.nested_branches[0] == "then"

    def test_conditional_with_null_input_indicates_branch(self, tmp_path):
        """NULL value in conditional -> should indicate which branch null takes."""
        code = "df = df.with_columns(factor=pl.when(pl.col('age') < 25).then(1.5).otherwise(1.0))"
        result = evaluate_expression(code, "factor", {"age": None})

        assert hasattr(result, "taken_branch"), "EvaluatedExpression must have taken_branch field"
        # NULL comparison is false in Polars, so otherwise is taken
        assert result.taken_branch == "otherwise"

    def test_conditional_branch_in_trace_step(self, tmp_path):
        """Full trace: verify step.calculation contains branch info."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"age": [22]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns("
                        "factor=pl.when(pl.col('age') < 25).then(1.5).otherwise(1.0))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="factor")
        step = _step_by_id(result, "t")

        assert step.calculation is not None
        assert "taken_branch" in step.calculation, (
            "step.calculation must contain 'taken_branch' key"
        )
        assert step.calculation["taken_branch"] == "then"

    def test_conditional_dimmed_branches(self, tmp_path):
        """Non-taken branches should be available for UI dimming."""
        code = (
            "df = df.with_columns(tier=pl.when(pl.col('score') > 90).then(pl.lit('gold'))"
            ".when(pl.col('score') > 70).then(pl.lit('silver'))"
            ".otherwise(pl.lit('bronze')))"
        )
        result = evaluate_expression(code, "tier", {"score": 80})

        assert hasattr(result, "dimmed_branches"), (
            "EvaluatedExpression must have dimmed_branches field"
        )
        # Second branch taken (index 1), so indices 0 and 2 should be dimmed
        assert 0 in result.dimmed_branches
        assert 2 in result.dimmed_branches
        assert 1 not in result.dimmed_branches


# ===========================================================================
# CATEGORY 3: Preamble Constant Resolution (5 tests)
# ===========================================================================


class TestPreambleConstantResolution:
    """The expression evaluator should resolve constants defined in the
    pipeline preamble."""

    def test_preamble_constant_resolved_in_expression(self, tmp_path):
        """BASE_RATE constant from preamble should be substituted in the text."""
        code = "df = df.with_columns(x=pl.lit(BASE_RATE) * 2)"
        result = evaluate_expression(code, "x", {}, preamble_ns={"BASE_RATE": 250})

        assert result.substituted_text is not None
        assert "250" in result.substituted_text
        assert abs(result.result_value - 500) < 0.01

    def test_preamble_multiple_constants(self, tmp_path):
        """Two preamble constants used in the same expression."""
        code = "df = df.with_columns(x=pl.lit(RATE_A) + pl.lit(RATE_B))"
        result = evaluate_expression(code, "x", {}, preamble_ns={"RATE_A": 100, "RATE_B": 50})

        assert "100" in result.substituted_text
        assert "50" in result.substituted_text
        assert abs(result.result_value - 150) < 0.01

    def test_preamble_constant_not_found_shows_name(self, tmp_path):
        """Constant not in preamble_ns -> shows variable name unresolved."""
        code = "df = df.with_columns(x=pl.lit(UNKNOWN_RATE) * 2)"
        result = evaluate_expression(code, "x", {}, preamble_ns={})

        assert "UNKNOWN_RATE" in result.substituted_text

    def test_preamble_constant_in_trace_step(self, tmp_path):
        """Full trace with preamble: verify step.calculation has resolved values."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [1000.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns(adjusted=pl.col('premium') * pl.lit(LOADING))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(
            graph,
            row_index=0,
            target_node_id="t",
            column="adjusted",
            preamble_ns={"LOADING": 1.15},
        )
        step = _step_by_id(result, "t")

        assert step.calculation is not None
        assert "1.15" in step.calculation["substituted_text"]

    def test_preamble_does_not_override_column_values(self, tmp_path):
        """Column named same as preamble constant -> column value wins."""
        code = "df = df.with_columns(result=pl.col('RATE') * 2)"
        result = evaluate_expression(code, "result", {"RATE": 500}, preamble_ns={"RATE": 100})

        # Column value 500 should be used, not preamble constant 100
        assert abs(result.result_value - 1000) < 0.01
        assert "500" in result.substituted_text


# ===========================================================================
# CATEGORY 4: Window Function Fallback (4 tests)
# ===========================================================================


class TestWindowFunctionFallback:
    """Window functions (.over()) should produce a descriptive fallback
    instead of broken substitution."""

    def test_window_function_detected_as_window_type(self, tmp_path):
        """.sum().over('region') -> expression_type should be 'window'."""
        code = "df = df.with_columns(region_total=pl.col('premium').sum().over('region'))"
        result = evaluate_expression(code, "region_total", {"premium": 1200, "region": "North"})

        assert result.expression_type == "window", (
            f"Expected expression_type='window', got '{result.expression_type}'"
        )

    def test_window_function_substitution_shows_description(self, tmp_path):
        """substituted_text should be human-readable, not broken '1200.0.sum().over(region)'."""
        code = "df = df.with_columns(region_total=pl.col('premium').sum().over('region'))"
        result = evaluate_expression(code, "region_total", {"premium": 1200, "region": "North"})

        # substituted_text must be a clean human-readable description,
        # NOT the raw code or the full .with_columns(...) expression.
        assert "sum" in result.substituted_text.lower()
        assert "premium" in result.substituted_text.lower()
        assert "region" in result.substituted_text.lower()
        # Must NOT be the raw code string -- it should be a natural-language description
        assert ".with_columns" not in result.substituted_text, (
            "substituted_text should be a human-readable description, not raw code"
        )
        assert "pl.col" not in result.substituted_text, (
            "substituted_text should not contain raw Polars API calls"
        )

    def test_window_function_result_value_correct(self, tmp_path):
        """result_value should still be the actual value from the DataFrame."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "premium": [100.0, 200.0, 300.0],
                "region": ["A", "A", "B"],
            }
        ).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "t",
                        "df = src.with_columns("
                        "region_total=pl.col('premium').sum().over('region'))",
                    ),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="region_total")
        step = _step_by_id(result, "t")

        assert step.calculation is not None
        # Row 0 is region "A": sum of 100 + 200 = 300
        assert abs(step.calculation["result_value"] - 300.0) < 0.01

    def test_window_function_referenced_columns_includes_partition(self, tmp_path):
        """referenced_columns should include both aggregated and partition columns."""
        code = "df = df.with_columns(region_total=pl.col('premium').sum().over('region'))"
        result = evaluate_expression(code, "region_total", {"premium": 1200, "region": "North"})

        refs = result.referenced_columns
        assert "premium" in refs, "Aggregated column 'premium' must be in referenced_columns"
        assert "region" in refs, "Partition column 'region' must be in referenced_columns"


# ===========================================================================
# CATEGORY 5: Intra-Node Dependency Chain (5 tests)
# ===========================================================================


class TestIntraNodeDependencyChain:
    """When a column depends on another column computed in the same node,
    the trace should show the chain."""

    def test_chain_two_steps(self, tmp_path):
        """exposure=...; earned_premium = written_premium * exposure -> chain of 2."""
        from haute._expression_parser import parse_expression_chain

        code = (
            "df = df.with_columns(\n"
            "    exposure=pl.col('months') / 12,\n"
            "    earned_premium=pl.col('written_premium') * pl.col('exposure'),\n"
            ")"
        )
        chain = parse_expression_chain(code, "earned_premium")

        assert chain is not None
        assert len(chain) == 2
        assert chain[0].target_column == "exposure"
        assert chain[1].target_column == "earned_premium"

    def test_chain_three_steps(self, tmp_path):
        """Three sequential dependencies within one with_columns."""
        from haute._expression_parser import parse_expression_chain

        code = (
            "df = df.with_columns(\n"
            "    rate=pl.col('base') * pl.col('factor'),\n"
            "    adjusted=pl.col('rate') * pl.col('discount'),\n"
            "    final=pl.col('adjusted') + pl.col('loading'),\n"
            ")"
        )
        chain = parse_expression_chain(code, "final")

        assert chain is not None
        assert len(chain) == 3
        assert chain[0].target_column == "rate"
        assert chain[1].target_column == "adjusted"
        assert chain[2].target_column == "final"

    def test_chain_independent_columns(self, tmp_path):
        """Two columns in same with_columns that don't depend on each other -> no chain."""
        from haute._expression_parser import parse_expression_chain

        code = "df = df.with_columns(\n    x=pl.col('a') * 2,\n    y=pl.col('b') * 3,\n)"
        chain = parse_expression_chain(code, "x")

        # No dependencies within the node, so chain should be length 1 (just itself)
        assert chain is not None
        assert len(chain) == 1
        assert chain[0].target_column == "x"

    def test_chain_in_trace_step(self, tmp_path):
        """Full trace: verify step has expression_chain field.

        Uses two sequential ``.with_columns()`` calls because Polars
        cannot resolve forward references within a single
        ``.with_columns()`` call.  The chain enrichment still detects the
        dependency because ``parse_expression_chain`` walks backward
        through sequential ``with_columns`` statements in the node code.
        """
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "written_premium": [1000.0],
                "months": [6],
            }
        ).write_parquet(p)

        code = (
            "df = src.with_columns(\n"
            "    exposure=pl.col('months') / 12,\n"
            ").with_columns(\n"
            "    earned_premium=pl.col('written_premium') * pl.col('exposure'),\n"
            ")"
        )
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", code),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="earned_premium")
        step = _step_by_id(result, "t")

        assert step.calculation is not None
        assert "expression_chain" in step.calculation, (
            "step.calculation must contain 'expression_chain' key"
        )
        assert len(step.calculation["expression_chain"]) == 2

    def test_chain_circular_reference_handled(self, tmp_path):
        """Column references itself -> no crash, graceful handling."""
        from haute._expression_parser import parse_expression_chain

        code = "df = df.with_columns(x=pl.col('x') + 1)"
        # Should not raise; returns a chain of length 1 or empty
        chain = parse_expression_chain(code, "x")
        assert chain is not None  # does not crash


# ===========================================================================
# CATEGORY 6: Column Rename Tracking (4 tests)
# ===========================================================================


class TestColumnRenameTracking:
    """When a column is renamed, the trace should track the rename chain."""

    def test_renamed_column_shows_original_name(self, tmp_path):
        """.alias('new_name') -> trace shows original name."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"old_name": [42]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(new_name=pl.col('old_name'))"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="new_name")
        step = _step_by_id(result, "t")

        assert step.calculation is not None
        assert "original_name" in step.calculation, (
            "step.calculation must contain 'original_name' for renames"
        )
        assert step.calculation["original_name"] == "old_name"

    def test_rename_chain_tracked(self, tmp_path):
        """old -> mid -> new, all tracked in rename chain."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"old": [100]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t1", "df = src.with_columns(mid=pl.col('old'))"),
                    _transform_node("t2", "df = t1.with_columns(new=pl.col('mid'))"),
                ],
                "edges": [_edge("src", "t1"), _edge("t1", "t2")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t2", column="new")
        step = _step_by_id(result, "t2")

        assert step.calculation is not None
        assert "rename_chain" in step.calculation, (
            "step.calculation must contain 'rename_chain' for multi-hop renames"
        )
        chain = step.calculation["rename_chain"]
        assert chain == ["old", "mid", "new"]

    def test_rename_in_trace_step(self, tmp_path):
        """Full trace, verify step has rename info in node_detail."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"source_col": [55.5]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.rename({'source_col': 'target_col'})"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="target_col")
        step = _step_by_id(result, "t")

        assert step.node_detail is not None
        assert "rename" in str(step.node_detail).lower(), (
            "node_detail must indicate rename operation"
        )

    def test_rename_does_not_break_column_relevance(self, tmp_path):
        """Renamed column still appears as relevant in the trace."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"premium": [1000.0]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.rename({'premium': 'written_premium'})"),
                ],
                "edges": [_edge("src", "t")],
            }
        )

        result = execute_trace(graph, row_index=0, target_node_id="t", column="written_premium")

        # The source step should still show the column as relevant
        src_step = _step_by_id(result, "src")
        assert src_step.output_values is not None
        assert "premium" in str(src_step.output_values), (
            "Original column 'premium' should be visible in source step"
        )
