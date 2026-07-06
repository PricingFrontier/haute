"""Regression tests for the W4-trace correlation-soundness fixes.

Each test pins a specific audit finding: the trace surface must fail
loudly / mark a step unresolved rather than attach a wrong parent row,
and its numeric comparisons must agree with what the engine actually did.
"""

from __future__ import annotations

import types

import polars as pl
import pytest
import structlog.testing

from haute._trace_correlation import (
    _build_value_match_expr,
    _correlate_rows_posthoc,
    _shared_key_is_unique,
    _trace_values_match,
)
from haute._trace_enrichment import (
    _fix_upstream_values,
    _match_continuous_rule,
    detect_row_lineage_type,
)
from haute._trace_export import export_trace
from haute._trace_waterfall import (
    WaterfallReconciliationError,
    _check_display_consistency,
    _classify_contribution,
    build_waterfall_from_steps,
)
from haute.trace import SchemaDiff, TraceStep


def _node(node_id: str, node_type: str = "generic", config: dict | None = None):
    return types.SimpleNamespace(
        id=node_id,
        data=types.SimpleNamespace(nodeType=node_type, config=config or {}),
    )


def _step(
    node_id: str,
    *,
    name: str | None = None,
    node_type: str = "polars",
    added: list[str] | None = None,
    modified: list[str] | None = None,
    passed: list[str] | None = None,
    output: dict | None = None,
    input_values: dict | None = None,
    expression: dict | None = None,
) -> TraceStep:
    return TraceStep(
        node_id=node_id,
        node_name=name or node_id,
        node_type=node_type,
        schema_diff=SchemaDiff(
            columns_added=added or [],
            columns_removed=[],
            columns_modified=modified or [],
            columns_passed=passed or [],
        ),
        input_values=input_values or {},
        output_values=output or {},
        expression=expression,
    )


# ---------------------------------------------------------------------------
# F041 / F694 — positional fast-path must never attach a wrong parent row
# ---------------------------------------------------------------------------


class TestPositionalFastPath:
    def test_reorder_with_no_shared_columns_marks_unresolved(self):
        """A rename that also reorders rows shares no column name with its
        parent; the old positional fast-path returned the wrong parent row.
        A reordering transform with nothing to verify against must be
        unresolved, not guessed."""
        eager = {
            "p": pl.DataFrame({"a": [10, 30]}),
            # b is a reordered rename of a: child row 0 (b=30) truly came
            # from parent row 1 (a=30), not parent row 0 (a=10).
            "c": pl.DataFrame({"b": [30, 10]}),
        }
        node_map = {
            "p": _node("p", "source"),
            "c": _node(
                "c",
                "polars",
                {"code": "df = df.select(pl.col('a').alias('b')).sort('b', descending=True)"},
            ),
        }

        result = _correlate_rows_posthoc(eager, ["p", "c"], {"c": ["p"]}, "c", 0, node_map=node_map)

        assert result["c"] == {"b": 30}
        # Must NOT be the positionally-aligned {'a': 10}.
        assert result["p"] is None

    def test_full_rename_preserving_order_resolves_positionally(self):
        """A pure rename/select (no reordering op) with no shared columns
        provably preserves row order, so the positional match is correct
        and the step must still resolve."""
        eager = {
            "p": pl.DataFrame({"old": [10, 20, 30]}),
            "c": pl.DataFrame({"new": [10, 20, 30]}),
        }
        node_map = {
            "p": _node("p", "source"),
            "c": _node("c", "polars", {"code": "df = df.rename({'old': 'new'})"}),
        }

        result = _correlate_rows_posthoc(eager, ["p", "c"], {"c": ["p"]}, "c", 2, node_map=node_map)

        assert result["p"] == {"old": 30}

    def test_single_parent_row_resolves_even_without_shared_columns(self):
        eager = {
            "p": pl.DataFrame({"a": [42]}),
            "c": pl.DataFrame({"b": [42]}),
        }
        # No code at all → conservatively "may reorder", but a single row
        # is unambiguous.
        node_map = {"p": _node("p", "source"), "c": _node("c", "generic")}

        result = _correlate_rows_posthoc(eager, ["p", "c"], {"c": ["p"]}, "c", 0, node_map=node_map)

        assert result["p"] == {"a": 42}

    def test_nonunique_carried_key_falls_through_to_ambiguity(self):
        """When the carried shared key is not unique among parent rows a
        reorder could have swapped a different matching row into position;
        the fast path must fall through and report ambiguity, not guess."""
        eager = {
            "p": pl.DataFrame({"k": [1, 1], "extra": [5, 6]}),
            "c": pl.DataFrame({"k": [1, 1], "other": [9, 9]}),
        }
        node_map = {"p": _node("p", "source"), "c": _node("c", "generic")}
        diagnostics: list[dict] = []

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            0,
            node_map=node_map,
            diagnostics=diagnostics,
        )

        assert result["p"] is None
        assert any(d["code"] == "ambiguous_row_match" for d in diagnostics)

    def test_unique_shared_key_still_accepts_positional_row(self):
        """The happy path is preserved: a unique carried key that matches
        the positional row is accepted."""
        eager = {
            "p": pl.DataFrame({"k": [1, 2], "extra": [5, 6]}),
            "c": pl.DataFrame({"k": [1, 2], "other": [9, 8]}),
        }
        node_map = {"p": _node("p", "source"), "c": _node("c", "generic")}

        result = _correlate_rows_posthoc(eager, ["p", "c"], {"c": ["p"]}, "c", 1, node_map=node_map)

        assert result["p"] == {"k": 2, "extra": 6}

    def test_shared_key_is_unique_helper(self):
        df = pl.DataFrame({"k": [1, 1, 2]})
        assert _shared_key_is_unique(df, {"k": 2}, ["k"]) is True
        assert _shared_key_is_unique(df, {"k": 1}, ["k"]) is False
        assert _shared_key_is_unique(df, {"k": 1}, []) is False


# ---------------------------------------------------------------------------
# F158 — dtype-asymmetric value match must not crash the trace
# ---------------------------------------------------------------------------


class TestValueMatchExprDtypeRobust:
    def test_numeric_value_against_string_column_does_not_crash(self):
        df = pl.DataFrame({"x": ["a", "b", "5"]})
        # Numeric value vs Utf8 column: previously raised ComputeError.
        expr = _build_value_match_expr("x", 5, df.schema["x"])
        matches = df.select(expr.fill_null(False).alias("m"))["m"].to_list()
        # int-like value still matches the "5" string key via stringwise compare.
        assert matches == [False, False, True]

    def test_nan_value_against_string_column_does_not_crash(self):
        df = pl.DataFrame({"x": ["a", "b"]})
        expr = _build_value_match_expr("x", float("nan"), df.schema["x"])
        matches = df.select(expr.fill_null(False).alias("m"))["m"].to_list()
        assert matches == [False, False]

    def test_inf_value_against_int_column_does_not_crash(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        expr = _build_value_match_expr("x", float("inf"), df.schema["x"])
        matches = df.select(expr.fill_null(False).alias("m"))["m"].to_list()
        assert matches == [False, False, False]

    def test_numeric_value_against_numeric_column_still_matches(self):
        df = pl.DataFrame({"x": [1, 5, 9]})
        expr = _build_value_match_expr("x", 5, df.schema["x"])
        matches = df.select(expr.fill_null(False).alias("m"))["m"].to_list()
        assert matches == [False, True, False]


# ---------------------------------------------------------------------------
# F695 — near-zero float must be able to match exactly 0.0
# ---------------------------------------------------------------------------


class TestTraceValuesMatchNearZero:
    def test_tiny_value_matches_zero(self):
        assert _trace_values_match(1e-15, 0.0) is True
        assert _trace_values_match(0.0, 1e-15) is True

    def test_distinct_small_values_do_not_match(self):
        assert _trace_values_match(1.0, 0.0) is False
        assert _trace_values_match(0.5, 0.4) is False


# ---------------------------------------------------------------------------
# F042 / F078 — _fix_upstream_values: scale-relative + unique + by node_id
# ---------------------------------------------------------------------------


class TestFixUpstreamValues:
    def test_distinct_small_factors_do_not_collide(self):
        """1e-6 absolute tolerance collided 1.0000001 and 1.0000004 and
        .row(0) overwrote the value with the wrong row."""
        eager = {"n": pl.DataFrame({"f": [1.0000001, 1.0000004]})}
        steps = [_step("n", name="src", output={"f": None})]
        input_sources = {"f": {"node_id": "n", "node_name": "src", "result_value": 1.0000004}}

        _fix_upstream_values(input_sources, steps, eager)

        assert steps[0].output_values["f"] == pytest.approx(1.0000004, abs=1e-12)

    def test_ambiguous_match_leaves_value_untouched_and_logs(self):
        eager = {"n": pl.DataFrame({"f": [2.0, 2.0]})}
        steps = [_step("n", name="src", output={"f": None})]
        input_sources = {"f": {"node_id": "n", "node_name": "src", "result_value": 2.0}}

        with structlog.testing.capture_logs() as captured:
            _fix_upstream_values(input_sources, steps, eager)

        assert steps[0].output_values["f"] is None
        assert any(e.get("event") == "fix_upstream_row_ambiguous" for e in captured)

    def test_matches_by_node_id_not_shared_name(self):
        """Two nodes share the display name 'src'; the known value belongs
        to the second node and must land there, not on the first match."""
        eager = {
            "s1": pl.DataFrame({"a": [10.0]}),
            "s2": pl.DataFrame({"a": [30.0]}),
        }
        steps = [
            _step("s1", name="src", output={"a": None}),
            _step("s2", name="src", output={"a": None}),
        ]
        input_sources = {"a": {"node_id": "s2", "node_name": "src", "result_value": 30.0}}

        _fix_upstream_values(input_sources, steps, eager)

        assert steps[0].output_values["a"] is None
        assert steps[1].output_values["a"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# F693 — continuous banding equality must be dtype-faithful (Float32)
# ---------------------------------------------------------------------------


class TestContinuousRuleDtypeFaithful:
    def test_float32_banded_value_matches_equality_rule(self):
        widened = float(pl.Series([0.1], dtype=pl.Float32).item())
        rule = {"op1": "=", "val1": 0.1}

        # Widened float64 exact == fails (the self-contradiction bug)...
        assert _match_continuous_rule(widened, rule) is False
        # ...but comparing in the source Float32 domain reproduces the
        # engine's own match.
        assert _match_continuous_rule(widened, rule, pl.Float32) is True

    def test_dtype_faithful_range_rule_still_matches(self):
        widened = float(pl.Series([5.5], dtype=pl.Float32).item())
        rule = {"op1": ">=", "val1": 5.5}
        assert _match_continuous_rule(widened, rule, pl.Float32) is True

    def test_none_dtype_preserves_legacy_behaviour(self):
        assert _match_continuous_rule(10, {"op1": "=", "val1": 10}) is True


# ---------------------------------------------------------------------------
# F080 — edge-join nodes are classified as joined, not by row-count delta
# ---------------------------------------------------------------------------


class TestRowLineageEdgeJoin:
    def test_edge_join_fan_in_is_joined_not_filtered(self):
        assert (
            detect_row_lineage_type(input_row_count=5, output_row_count=1, node_type="edgeJoin")
            == "joined"
        )

    def test_edge_join_fan_out_is_joined_not_expanded(self):
        assert (
            detect_row_lineage_type(input_row_count=1, output_row_count=5, node_type="edgeJoin")
            == "joined"
        )


# ---------------------------------------------------------------------------
# F314 — export_trace reports the upstream-most origin of a source column
# ---------------------------------------------------------------------------


class TestExportTraceOrigin:
    def test_origin_is_first_producer_not_last_carrier(self):
        steps = [
            _step(
                "creator",
                added=["burn"],
                output={"burn": 70.0},
            ),
            _step(
                "carrier",
                node_type="polars",
                passed=["burn"],
                added=["premium"],
                output={"burn": 70.0, "premium": 100.0},
                input_values={"burn": 70.0},
                expression={
                    "expression_text": "premium = burn * 1.4",
                    "referenced_columns": ["burn"],
                },
            ),
        ]
        trace_result = types.SimpleNamespace(
            steps=steps,
            column="premium",
            output_value=100.0,
            target_node_id="carrier",
            row_index=0,
            execution_ms=1.0,
            total_nodes_in_pipeline=2,
        )

        exported = export_trace(trace_result)

        origins = {s["column"]: s["origin"] for s in exported["sources"]}
        # 'burn' originates at 'creator', not at the downstream 'carrier'.
        assert origins["burn"] == "creator"


# ---------------------------------------------------------------------------
# F045 — an identity (×1.0) contributor must not vanish from the waterfall
# ---------------------------------------------------------------------------


class TestIdentityContributorNotOmitted:
    def test_no_op_factor_step_appears_as_identity_entry(self):
        steps = [
            _step("base", added=["premium"], output={"premium": 100.0}),
            # region_relativity is 1.0 for this row → cell unchanged, so
            # the schema diff records premium as *passed*, not *modified*.
            _step(
                "region",
                passed=["premium"],
                output={"premium": 100.0},
                input_values={"premium": 100.0},
            ),
            _step("uplift", modified=["premium"], output={"premium": 110.0}),
        ]
        node_map = {
            "base": _node("base", "polars", {"code": "df.with_columns(premium=pl.col('base'))"}),
            "region": _node(
                "region",
                "polars",
                {"code": "df.with_columns(premium=pl.col('premium') * pl.col('region_rel'))"},
            ),
            "uplift": _node(
                "uplift", "polars", {"code": "df.with_columns(premium=pl.col('premium') * 1.1)"}
            ),
        }

        result = build_waterfall_from_steps(
            steps,
            "premium",
            target_node_id="uplift",
            final_output_value=110.0,
            node_map=node_map,
        )

        assert isinstance(result, list)
        labels = [e["label"] for e in result]
        assert labels == ["base", "region", "uplift"]
        region_entry = result[1]
        assert region_entry["value"] == pytest.approx(1.0)
        assert region_entry["delta"] == pytest.approx(0.0)
        assert region_entry["cumulative"] == pytest.approx(100.0)

    def test_unrelated_passthrough_step_still_omitted(self):
        steps = [
            _step("base", added=["premium"], output={"premium": 100.0}),
            # A passthrough that does NOT target premium must stay omitted.
            _step(
                "other",
                passed=["premium"],
                added=["misc"],
                output={"premium": 100.0, "misc": 1.0},
            ),
            _step("age", modified=["premium"], output={"premium": 120.0}),
            _step("region", modified=["premium"], output={"premium": 108.0}),
        ]
        node_map = {
            "base": _node("base", "polars", {"code": "df.with_columns(premium=pl.col('base'))"}),
            "other": _node("other", "polars", {"code": "df.with_columns(misc=pl.col('x') + 1.0)"}),
            "age": _node(
                "age", "polars", {"code": "df.with_columns(premium=pl.col('premium') * 1.2)"}
            ),
            "region": _node(
                "region", "polars", {"code": "df.with_columns(premium=pl.col('premium') * 0.9)"}
            ),
        }

        result = build_waterfall_from_steps(
            steps,
            "premium",
            target_node_id="region",
            final_output_value=108.0,
            node_map=node_map,
        )

        assert isinstance(result, list)
        assert [e["label"] for e in result] == ["base", "age", "region"]


# ---------------------------------------------------------------------------
# F287 — C8 guard must reject an unverifiable factor from a zero base
# ---------------------------------------------------------------------------


class TestZeroBaseMultiplyGuard:
    def test_nonidentity_factor_from_zero_base_raises(self):
        with pytest.raises(WaterfallReconciliationError):
            _check_display_consistency("multiply", 0.0, 1.5, 0.0, "step")

    def test_identity_factor_from_zero_base_is_accepted(self):
        _check_display_consistency("multiply", 0.0, 1.0, 0.0, "step")


# ---------------------------------------------------------------------------
# F696 — a sign change must not render as a negative multiplicative factor
# ---------------------------------------------------------------------------


class TestSignChangeContribution:
    def test_positive_to_negative_is_additive(self):
        step = _step("s", output={"premium": -50.0})
        with structlog.testing.capture_logs() as captured:
            operation, value = _classify_contribution(step, "premium", 100.0, -50.0, "t")
        assert operation == "add"
        assert value == pytest.approx(-150.0)
        assert any(e.get("event") == "waterfall_sign_change" for e in captured)

    def test_negative_base_is_additive(self):
        step = _step("s", output={"premium": -50.0})
        operation, value = _classify_contribution(step, "premium", -100.0, -50.0, "t")
        assert operation == "add"
        assert value == pytest.approx(50.0)

    def test_positive_to_positive_stays_multiplicative(self):
        step = _step("s", output={"premium": 120.0})
        operation, value = _classify_contribution(step, "premium", 100.0, 120.0, "t")
        assert operation == "multiply"
        assert value == pytest.approx(1.2)
