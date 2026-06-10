"""Waterfall arithmetic tests — CODE_REVIEW.md C8 (Wave 3a.2).

Pre-fix, ``_trace_waterfall.build_waterfall_from_steps`` fed each modified
step's POST-STEP CUMULATIVE column value in as the multiply/add factor
(``new_cumulative = cumulative * value`` with ``value`` = 120, the new
premium, not the 1.2 factor — so 100 x 120 = 12,000), and classified
``premium * (1 - discount)`` as additive because the text contains a "-".
The regulator-facing waterfall showed "x120.0" steps and totals orders of
magnitude away from the traced output value displayed beside it.

These tests drive the flagship scenarios through ``execute_trace`` on real
pipeline graphs (the pre-existing arithmetic tests fed hand-written factors
straight into ``build_waterfall``, which is why C8 survived).  Required
behavior:

  * each step's contribution is derived from CONSECUTIVE OUTPUT VALUES
    along the traced path: ``delta = value_after - value_before``;
  * multiplicative display shows the implied factor
    ``value_after / value_before``;
  * ``value_before == 0`` (or an overflowing implied factor) is guarded:
    the step is displayed delta-only — never an Inf factor;
  * the op classifier is labeling-only and conservative — it can never
    corrupt the arithmetic;
  * MANDATORY invariant: the final cumulative reconciles exactly (float
    tolerance) with the traced output value, asserted in the builder
    (``WaterfallReconciliationError`` -> structured error payload) and here.

Fixture rules: small int/string fixtures, no values anywhere near 2**53
(Int64 JSON semantics change in Wave 7).
"""

from __future__ import annotations

import math

import polars as pl
import pytest
import structlog.testing

from haute._trace_waterfall import (
    WaterfallReconciliationError,
    build_waterfall,
    build_waterfall_from_steps,
)
from haute.trace import SchemaDiff, TraceStep, execute_trace
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


def _chain_graph(tmp_path, df: pl.DataFrame, transforms: list[tuple[str, str]]):
    """Linear pipeline: parquet source -> sequential polars transforms."""
    p = tmp_path / "data.parquet"
    df.write_parquet(p)
    nodes = [make_source_node("src", str(p))]
    edges = []
    prev = "src"
    for node_id, code in transforms:
        nodes.append(make_transform_node(node_id, code))
        edges.append(make_edge(prev, node_id))
        prev = node_id
    return make_graph({"nodes": nodes, "edges": edges}), prev


def _entries(result) -> list[dict]:
    """Return the waterfall as a list of entries, failing on error payloads."""
    assert isinstance(result.waterfall, list), (
        f"expected a waterfall entry list, got {result.waterfall!r}"
    )
    return result.waterfall


def _labels(entries: list[dict]) -> list[str]:
    return [e["label"] for e in entries]


# ---------------------------------------------------------------------------
# 1. Flagship repro: base rate -> two multiplicative rating steps
# ---------------------------------------------------------------------------


class TestFlagshipMultiplicativeChain:
    """The exact C8 scenario: base -> xfactor -> xfactor via execute_trace."""

    def _graph(self, tmp_path):
        df = pl.DataFrame(
            {
                "policy_id": [1, 2],
                "base_rate": [100.0, 200.0],
                "age_factor": [1.2, 1.1],
                "region_factor": [0.9, 0.8],
            }
        )
        return _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("age", "df = df.with_columns(premium=pl.col('premium') * pl.col('age_factor'))"),
                (
                    "region",
                    "df = df.with_columns(premium=pl.col('premium') * pl.col('region_factor'))",
                ),
            ],
        )

    def test_steps_show_implied_factors_not_cumulative_values(self, tmp_path):
        """Pre-fix: the 'age' step showed x120.0 (the new premium) and a
        cumulative of 12,000.  The displayed factor must be the implied
        factor value_after / value_before."""
        graph, target = self._graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        assert _labels(entries) == ["base", "age", "region"]

        base, age, region = entries
        assert base["operation"] == "base"
        assert base["value"] == pytest.approx(100.0)
        assert base["cumulative"] == pytest.approx(100.0)
        assert base["delta"] == 0.0

        assert age["operation"] == "multiply"
        assert age["value"] == pytest.approx(1.2), (
            f"expected the implied factor 1.2, got {age['value']!r} "
            "(the C8 bug displayed the post-step premium as the factor)"
        )
        assert age["delta"] == pytest.approx(20.0)
        assert age["cumulative"] == pytest.approx(120.0)

        assert region["operation"] == "multiply"
        assert region["value"] == pytest.approx(0.9)
        assert region["delta"] == pytest.approx(-12.0)
        assert region["cumulative"] == pytest.approx(108.0)

    def test_final_cumulative_reconciles_with_traced_output_value(self, tmp_path):
        """The C8 invariant: the chain ends exactly at the value displayed
        beside the waterfall (pre-fix it ended at 1,296,000 vs 108)."""
        graph, target = self._graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        # Exact: the cumulative is the observed output value, not a
        # re-derived product that can drift.
        assert entries[-1]["cumulative"] == result.output_value

    def test_second_row_uses_that_rows_factors(self, tmp_path):
        graph, target = self._graph(tmp_path)
        result = execute_trace(graph, row_index=1, target_node_id=target, column="premium")

        entries = _entries(result)
        base, age, region = entries
        assert base["cumulative"] == pytest.approx(200.0)
        assert age["value"] == pytest.approx(1.1)
        assert region["value"] == pytest.approx(0.8)
        assert entries[-1]["cumulative"] == result.output_value
        assert result.output_value == pytest.approx(200.0 * 1.1 * 0.8)

    def test_cumulative_chain_is_consecutive_observed_values(self, tmp_path):
        """Each cumulative equals the column's observed value at that step;
        each delta equals the difference of consecutive observed values."""
        graph, target = self._graph(tmp_path)
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        observed = {s.node_name: s.output_values.get("premium") for s in result.steps}
        for entry in entries:
            assert entry["cumulative"] == observed[entry["label"]]
        for previous, current in zip(entries, entries[1:]):
            assert current["delta"] == pytest.approx(current["cumulative"] - previous["cumulative"])


# ---------------------------------------------------------------------------
# 2. Additive chain
# ---------------------------------------------------------------------------


class TestAdditiveChain:
    def test_loadings_show_as_added_amounts(self, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": [11],
                "base_rate": [100.0],
                "loading_a": [50.0],
                "loading_b": [30.0],
            }
        )
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("load_a", "df = df.with_columns(premium=pl.col('premium') + pl.col('loading_a'))"),
                ("load_b", "df = df.with_columns(premium=pl.col('premium') + pl.col('loading_b'))"),
            ],
        )
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        base, load_a, load_b = entries
        assert base["operation"] == "base"
        assert base["cumulative"] == pytest.approx(100.0)

        assert load_a["operation"] == "add"
        assert load_a["value"] == pytest.approx(50.0)
        assert load_a["delta"] == pytest.approx(50.0)
        assert load_a["cumulative"] == pytest.approx(150.0)

        assert load_b["operation"] == "add"
        assert load_b["value"] == pytest.approx(30.0)
        assert load_b["cumulative"] == pytest.approx(180.0)

        assert entries[-1]["cumulative"] == result.output_value

    def test_subtraction_shows_negative_delta(self, tmp_path):
        df = pl.DataFrame({"quote_id": [7], "base_rate": [100.0], "fee": [15.0], "x": [1.0]})
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("fees", "df = df.with_columns(premium=pl.col('premium') - pl.col('fee'))"),
                ("more", "df = df.with_columns(premium=pl.col('premium') - 5.0)"),
            ],
        )
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        assert entries[1]["operation"] == "add"
        assert entries[1]["value"] == pytest.approx(-15.0)
        assert entries[1]["delta"] == pytest.approx(-15.0)
        assert entries[2]["cumulative"] == pytest.approx(80.0)
        assert entries[-1]["cumulative"] == result.output_value


# ---------------------------------------------------------------------------
# 3. Mixed chain including `premium * (1 - discount)`
# ---------------------------------------------------------------------------


class TestMixedChainDiscountExpression:
    def test_discount_product_is_not_misread_as_additive(self, tmp_path):
        """Pre-fix the op classifier saw the '-' in `premium * (1 - discount)`
        and called the step additive.  The step is multiplicative; the
        value-derived delta must be correct either way."""
        df = pl.DataFrame(
            {
                "quote_id": [21],
                "base_rate": [200.0],
                "age_factor": [1.1],
                "loading": [30.0],
                "discount": [0.25],
            }
        )
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("age", "df = df.with_columns(premium=pl.col('premium') * pl.col('age_factor'))"),
                ("load", "df = df.with_columns(premium=pl.col('premium') + pl.col('loading'))"),
                (
                    "disc",
                    "df = df.with_columns(premium=pl.col('premium') * (1 - pl.col('discount')))",
                ),
            ],
        )
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        assert _labels(entries) == ["base", "age", "load", "disc"]
        base, age, load, disc = entries

        assert age["operation"] == "multiply"
        assert age["value"] == pytest.approx(1.1)
        assert age["cumulative"] == pytest.approx(220.0)

        assert load["operation"] == "add"
        assert load["value"] == pytest.approx(30.0)
        assert load["cumulative"] == pytest.approx(250.0)

        # 250 * (1 - 0.25) = 187.5 — multiplicative, implied factor 0.75.
        assert disc["operation"] == "multiply", (
            "`premium * (1 - discount)` is multiplicative; the '-' inside the "
            "parenthesised factor must not flip the label to additive"
        )
        assert disc["value"] == pytest.approx(0.75)
        assert disc["delta"] == pytest.approx(-62.5)
        assert disc["cumulative"] == pytest.approx(187.5)

        assert entries[-1]["cumulative"] == result.output_value


# ---------------------------------------------------------------------------
# 4. Zero baseline guard — value_before == 0
# ---------------------------------------------------------------------------


class TestZeroBaselineGuard:
    def _graph(self, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": [31],
                "zero": [0.0],
                "base_rate": [100.0],
                "age_factor": [1.2],
            }
        )
        return _chain_graph(
            tmp_path,
            df,
            [
                ("seed", "df = df.with_columns(premium=pl.col('zero') * 1.0)"),
                (
                    "rated",
                    "df = df.with_columns(premium=pl.col('base_rate') * pl.col('age_factor'))",
                ),
                ("uplift", "df = df.with_columns(premium=pl.col('premium') * 1.1)"),
            ],
        )

    def test_zero_before_value_displays_delta_never_inf_factor(self, tmp_path):
        graph, target = self._graph(tmp_path)
        with structlog.testing.capture_logs() as captured:
            result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        seed, rated, uplift = entries
        assert seed["operation"] == "base"
        assert seed["cumulative"] == 0.0

        # 0 -> 120 has no defined implied factor: delta-only display,
        # even though the expression is multiplicative in shape.
        assert rated["operation"] == "add"
        assert rated["value"] == pytest.approx(120.0)
        assert rated["delta"] == pytest.approx(120.0)
        assert rated["cumulative"] == pytest.approx(120.0)

        assert uplift["operation"] == "multiply"
        assert uplift["value"] == pytest.approx(1.1)

        for entry in entries:
            for key in ("value", "delta", "cumulative"):
                assert math.isfinite(entry[key]), f"non-finite {key} in {entry!r}"

        assert entries[-1]["cumulative"] == result.output_value

        # Loud: the undefined implied factor is logged at WARNING.
        assert any(
            e.get("event") == "waterfall_implied_factor_undefined"
            and str(e.get("log_level", "")).lower() == "warning"
            for e in captured
        ), f"expected a waterfall_implied_factor_undefined warning, got {captured!r}"


# ---------------------------------------------------------------------------
# 5. Passthrough nodes contribute nothing
# ---------------------------------------------------------------------------


class TestPassthroughSteps:
    def test_passthrough_between_rating_steps_adds_no_entry(self, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": [41],
                "base_rate": [100.0],
                "age_factor": [1.2],
                "region_factor": [0.9],
                "other": [1.0],
            }
        )
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("untouched", "df = df.with_columns(other_2=pl.col('other') + 1.0)"),
                ("age", "df = df.with_columns(premium=pl.col('premium') * pl.col('age_factor'))"),
                (
                    "region",
                    "df = df.with_columns(premium=pl.col('premium') * pl.col('region_factor'))",
                ),
            ],
        )
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        assert _labels(entries) == ["base", "age", "region"]
        assert "untouched" not in _labels(entries)
        assert entries[1]["value"] == pytest.approx(1.2)
        assert entries[-1]["cumulative"] == result.output_value

    def test_trailing_passthrough_target_still_reconciles(self, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": [42],
                "base_rate": [100.0],
                "age_factor": [1.2],
                "region_factor": [0.9],
            }
        )
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("age", "df = df.with_columns(premium=pl.col('premium') * pl.col('age_factor'))"),
                (
                    "region",
                    "df = df.with_columns(premium=pl.col('premium') * pl.col('region_factor'))",
                ),
                ("tail", "df = df.with_columns(flag=pl.col('premium') > 0)"),
            ],
        )
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        assert _labels(entries) == ["base", "age", "region"]
        assert entries[-1]["cumulative"] == result.output_value
        assert result.output_value == pytest.approx(108.0)


# ---------------------------------------------------------------------------
# Exact reconciliation even where re-applied factors would drift
# ---------------------------------------------------------------------------


class TestExactReconciliation:
    def test_cumulative_snaps_to_observed_values_bitwise(self, tmp_path):
        """0.1 * 3 * 7-style decimals: re-applying implied factors drifts by
        ulps; the cumulative must be the observed value itself."""
        df = pl.DataFrame({"quote_id": [51], "seed": [0.1], "f1": [3.0], "f2": [7.0]})
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('seed') * 1.0)"),
                ("m1", "df = df.with_columns(premium=pl.col('premium') * pl.col('f1'))"),
                ("m2", "df = df.with_columns(premium=pl.col('premium') * pl.col('f2'))"),
            ],
        )
        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")

        entries = _entries(result)
        # Bitwise equality with the traced value — not approx.
        assert entries[-1]["cumulative"] == result.output_value
        final_step = next(s for s in result.steps if s.node_name == "m2")
        assert entries[-1]["cumulative"] == final_step.output_values["premium"]


# ---------------------------------------------------------------------------
# Reconciliation invariant plumbing (unit level)
# ---------------------------------------------------------------------------


def _mk_step(node_id: str, added: list[str], modified: list[str], output: dict) -> TraceStep:
    return TraceStep(
        node_id=node_id,
        node_name=node_id,
        node_type="polars",
        schema_diff=SchemaDiff(
            columns_added=added,
            columns_removed=[],
            columns_modified=modified,
            columns_passed=[],
        ),
        input_values={},
        output_values=output,
    )


def _chain_steps() -> list[TraceStep]:
    return [
        _mk_step("src", ["premium"], [], {"premium": 100.0}),
        _mk_step("f1", [], ["premium"], {"premium": 120.0}),
        _mk_step("f2", [], ["premium"], {"premium": 132.0}),
    ]


class TestReconciliationInvariant:
    def test_mismatched_final_value_returns_loud_error_payload(self):
        """If the chain cannot reconcile with the traced output value, the
        builder must return a structured error — never a wrong chart."""
        with structlog.testing.capture_logs() as captured:
            result = build_waterfall_from_steps(
                _chain_steps(),
                "premium",
                target_node_id="f2",
                final_output_value=999.0,
            )

        assert isinstance(result, dict), f"expected an error payload, got {result!r}"
        assert result["error_type"] == "WaterfallReconciliationError"
        assert "reconcile" in result["error"]
        assert any(
            e.get("event") == "waterfall_reconciliation_failed"
            and str(e.get("log_level", "")).lower() in {"error", "critical"}
            for e in captured
        ), f"expected a waterfall_reconciliation_failed error log, got {captured!r}"

    def test_matching_final_value_builds_normally(self):
        result = build_waterfall_from_steps(
            _chain_steps(),
            "premium",
            target_node_id="f2",
            final_output_value=132.0,
        )
        assert isinstance(result, list)
        assert [e["cumulative"] for e in result] == [100.0, 120.0, 132.0]
        assert [e["operation"] for e in result] == ["base", "multiply", "multiply"]
        assert result[1]["value"] == pytest.approx(1.2)
        assert result[2]["value"] == pytest.approx(1.1)

    def test_non_numeric_final_value_yields_no_waterfall(self):
        assert (
            build_waterfall_from_steps(
                _chain_steps(),
                "premium",
                target_node_id="f2",
                final_output_value=None,
            )
            is None
        )
        assert (
            build_waterfall_from_steps(
                _chain_steps(),
                "premium",
                target_node_id="f2",
                final_output_value="132.0",
            )
            is None
        )

    def test_int_final_value_reconciles_against_float_chain(self):
        steps = [
            _mk_step("src", ["premium"], [], {"premium": 100}),
            _mk_step("f1", [], ["premium"], {"premium": 120}),
            _mk_step("f2", [], ["premium"], {"premium": 132}),
        ]
        result = build_waterfall_from_steps(
            steps, "premium", target_node_id="f2", final_output_value=132
        )
        assert isinstance(result, list)
        assert result[-1]["cumulative"] == 132.0


class TestInBuilderConsistencyCheck:
    """build_waterfall itself rejects display values that lie about the
    observed cumulative chain — the literal C8 failure pattern."""

    def test_post_step_value_fed_as_factor_raises(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 100.0, "cumulative": 100.0},
            # C8: the new premium (120) fed in as the multiply factor.
            {"label": "Age", "operation": "multiply", "value": 120.0, "cumulative": 120.0},
            {"label": "Region", "operation": "multiply", "value": 0.9, "cumulative": 108.0},
        ]
        with pytest.raises(WaterfallReconciliationError):
            build_waterfall(steps)

    def test_inconsistent_add_amount_raises(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 100.0, "cumulative": 100.0},
            {"label": "Load", "operation": "add", "value": 150.0, "cumulative": 150.0},
            {"label": "More", "operation": "add", "value": 10.0, "cumulative": 160.0},
        ]
        with pytest.raises(WaterfallReconciliationError):
            build_waterfall(steps)

    def test_consistent_observed_steps_snap_cumulative(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 0.1, "cumulative": 0.1},
            {
                "label": "M1",
                "operation": "multiply",
                "value": 3.0,
                "cumulative": 0.30000000000000004,
            },
            {"label": "A1", "operation": "add", "value": 0.1, "cumulative": 0.4},
        ]
        result = build_waterfall(steps)
        assert result is not None
        # Snapped to the observed values bit-for-bit, not re-derived.
        assert result.entries[1].cumulative == 0.30000000000000004
        assert result.entries[2].cumulative == 0.4
        assert result.final_value == 0.4
        assert result.entries[2].delta == pytest.approx(0.4 - 0.30000000000000004)

    def test_non_finite_observed_value_returns_none(self):
        steps = [
            {"label": "Base", "operation": "base", "value": 100.0, "cumulative": 100.0},
            {"label": "M1", "operation": "multiply", "value": 1.2, "cumulative": float("nan")},
            {"label": "M2", "operation": "multiply", "value": 1.1, "cumulative": 132.0},
        ]
        assert build_waterfall(steps) is None

    def test_legacy_hand_fed_steps_unchanged(self):
        """Without observed cumulatives the documented apply-the-op contract
        is preserved (the broad legacy suite lives in test_trace_coverage)."""
        steps = [
            {"label": "Base", "operation": "base", "value": 100.0},
            {"label": "F", "operation": "multiply", "value": 1.5},
            {"label": "L", "operation": "add", "value": 20.0},
        ]
        result = build_waterfall(steps)
        assert result is not None
        assert result.final_value == pytest.approx(170.0)


class TestInvariantSurfacesThroughExecuteTrace:
    def test_builder_failure_becomes_error_payload_not_silent_none(self, tmp_path, monkeypatch):
        """A reconciliation raise inside the builder surfaces on the
        TraceResult as the structured error payload."""
        df = pl.DataFrame({"quote_id": [61], "base_rate": [100.0], "f1": [1.2], "f2": [0.9]})
        graph, target = _chain_graph(
            tmp_path,
            df,
            [
                ("base", "df = df.with_columns(premium=pl.col('base_rate') * 1.0)"),
                ("m1", "df = df.with_columns(premium=pl.col('premium') * pl.col('f1'))"),
                ("m2", "df = df.with_columns(premium=pl.col('premium') * pl.col('f2'))"),
            ],
        )

        def _always_mismatched(steps):  # noqa: ANN001, ANN202
            raise WaterfallReconciliationError("forced reconciliation failure")

        monkeypatch.setattr("haute._trace_waterfall.build_waterfall", _always_mismatched)

        result = execute_trace(graph, row_index=0, target_node_id=target, column="premium")
        assert isinstance(result.waterfall, dict)
        assert result.waterfall["error_type"] == "WaterfallReconciliationError"
