"""3a.3 — a rating-table lookup miss must fail loud by default.

At HEAD a missing rating level (e.g. a renamed band label) flowed through
the lookup join as null and was silently neutralised by the combined-rating
path (1.0 for multiply, 0.0 for add): base-rate pricing presented as
success, with no log and no counter.  Repro evidence (HEAD):

    entries: 18-25 -> 1.5, 26-40 -> 1.1; data row has age_band "26-39"
    combined premium = base 100 * region 0.9 = 90.0, age miss invisible.

These tests pin the new contract:

* DEFAULT: a miss with no usable ``defaultValue`` raises
  ``RatingTableMissError`` at materialisation, naming the table, the
  missing key(s) (capped) and the affected row count — in both the
  in-memory and streaming engines.
* OPT-IN: ``"onMissing": "neutral"`` accepts misses explicitly — the
  table output stays null, combined outputs fill the operation's neutral
  element exactly as before, and every miss is counted and logged
  (WARNING with table + count).  Visible, never silent.
* ``defaultValue`` still wins when usable: misses fill the default with
  no error and no warning (explicit config, surfaced in traces).

This is a breaking behaviour change with a planned release note
(REMEDIATION_PLAN.md W3a).
"""

from __future__ import annotations

import json
import re
from typing import Any

import polars as pl
import pytest
import structlog.testing

from haute._rating import (
    RatingTableMissError,
    _apply_rating_table,
    apply_rating_step_from_config,
)
from haute._rating_step_config import (
    normalise_rating_step_config,
)
from haute.executor import _build_node_fn
from haute.graph_utils import GraphNode, NodeData

MISS_EVENT = "rating_table_lookup_misses"


def _rating_node(nid: str, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="ratingStep", config=config))


def _age_region_config(**age_table_extra: Any) -> dict[str, Any]:
    """Two-table config with a combined multiply output (the repro shape)."""
    return {
        "tables": [
            {
                "name": "Age Factor",
                "factors": ["age_band"],
                "outputColumn": "age_factor",
                "entries": [
                    {"age_band": "18-25", "value": 1.5},
                    {"age_band": "26-40", "value": 1.1},
                ],
                **age_table_extra,
            },
            {
                "name": "Region Factor",
                "factors": ["region"],
                "outputColumn": "region_factor",
                "entries": [
                    {"region": "North", "value": 1.2},
                    {"region": "South", "value": 0.9},
                ],
            },
        ],
        "combinedOutputs": [
            {"outputColumn": "premium", "operation": "multiply", "baseValue": 100.0}
        ],
    }


def _renamed_band_frame() -> pl.LazyFrame:
    """Row 2's age_band was renamed 26-40 -> 26-39 and now misses the table."""
    return pl.DataFrame({"age_band": ["18-25", "26-39"], "region": ["North", "South"]}).lazy()


# ---------------------------------------------------------------------------
# Default: fail loud
# ---------------------------------------------------------------------------


class TestMissFailsLoudByDefault:
    def test_renamed_band_label_raises_through_config_path(self) -> None:
        """The 3a.3 repro: a renamed band label must not price at base rate."""
        out = apply_rating_step_from_config(_renamed_band_frame(), _age_region_config())
        with pytest.raises(RatingTableMissError):
            out.collect()

    def test_error_names_table_keys_and_row_count(self) -> None:
        out = apply_rating_step_from_config(_renamed_band_frame(), _age_region_config())
        with pytest.raises(RatingTableMissError) as excinfo:
            out.collect()
        message = str(excinfo.value)
        assert "age_factor" in message
        assert "26-39" in message
        assert re.search(r"\b1 of 2 row", message)
        # The error must teach the fix, not just complain.
        assert "defaultValue" in message
        assert "onMissing" in message

    def test_raises_through_executor_builder(self) -> None:
        node = _rating_node("r1", _age_region_config())
        _, fn, _ = _build_node_fn(node)
        with pytest.raises(RatingTableMissError):
            fn(_renamed_band_frame()).collect()

    def test_raises_in_streaming_engine(self) -> None:
        out = apply_rating_step_from_config(_renamed_band_frame(), _age_region_config())
        with pytest.raises(RatingTableMissError):
            out.collect(engine="streaming")

    def test_uncombined_single_table_miss_also_raises(self) -> None:
        """A miss is a config/data bug regardless of combine usage."""
        config = {
            "tables": [
                {
                    "name": "T",
                    "factors": ["band"],
                    "outputColumn": "out",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ]
        }
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        with pytest.raises(RatingTableMissError, match="'out'"):
            apply_rating_step_from_config(lf, config).collect()

    def test_add_operation_miss_raises(self) -> None:
        config = _age_region_config()
        config["combinedOutputs"] = [
            {"outputColumn": "total", "operation": "add", "baseValue": 100.0}
        ]
        with pytest.raises(RatingTableMissError):
            apply_rating_step_from_config(_renamed_band_frame(), config).collect()

    def test_missing_keys_capped_in_message(self) -> None:
        entries = [{"k": f"present_{i}", "value": 1.0} for i in range(3)]
        config = {
            "tables": [
                {"name": "Caps", "factors": ["k"], "outputColumn": "out", "entries": entries}
            ]
        }
        frame_keys = [f"absent_{i:02d}" for i in range(25)]
        lf = pl.DataFrame({"k": frame_keys}).lazy()
        with pytest.raises(RatingTableMissError) as excinfo:
            apply_rating_step_from_config(lf, config).collect()
        message = str(excinfo.value)
        assert "25 of 25 row" in message
        assert "showing 10 of 25 distinct" in message
        # Capped: exactly 10 keys listed, in first-seen order.
        assert message.count("absent_") == 10
        assert "absent_00" in message
        assert "absent_24" not in message

    def test_multi_factor_missing_keys_show_all_factors(self) -> None:
        config = {
            "tables": [
                {
                    "name": "Two-way",
                    "factors": ["region", "tier"],
                    "outputColumn": "out",
                    "entries": [{"region": "North", "tier": "gold", "value": 1.0}],
                }
            ]
        }
        lf = pl.DataFrame({"region": ["North"], "tier": ["silver"]}).lazy()
        with pytest.raises(RatingTableMissError) as excinfo:
            apply_rating_step_from_config(lf, config).collect()
        message = str(excinfo.value)
        assert "North" in message
        assert "silver" in message

    def test_unusable_default_value_is_named_in_error(self) -> None:
        """B13 parses junk defaults to None; the miss error must say so."""
        config = {
            "tables": [
                {
                    "name": "T",
                    "factors": ["band"],
                    "outputColumn": "out",
                    "defaultValue": "N/A",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ]
        }
        lf = pl.DataFrame({"band": ["B"]}).lazy()
        with pytest.raises(RatingTableMissError, match=re.escape("'N/A'")):
            apply_rating_step_from_config(lf, config).collect()

    def test_table_without_name_is_identified_by_output_column(self) -> None:
        config = {
            "tables": [
                {
                    "factors": ["band"],
                    "outputColumn": "vehicle_factor",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ]
        }
        lf = pl.DataFrame({"band": ["B"]}).lazy()
        with pytest.raises(RatingTableMissError, match="vehicle_factor"):
            apply_rating_step_from_config(lf, config).collect()

    def test_error_is_importable_from_graph_utils(self) -> None:
        import haute.graph_utils

        assert haute.graph_utils.RatingTableMissError is RatingTableMissError

    def test_null_factor_value_is_a_miss(self) -> None:
        """Null keys never match the lookup join; default mode fails loud."""
        config = {
            "tables": [
                {
                    "name": "T",
                    "factors": ["band"],
                    "outputColumn": "out",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ]
        }
        lf = pl.DataFrame({"band": ["A", None]}).lazy()
        with pytest.raises(RatingTableMissError):
            apply_rating_step_from_config(lf, config).collect()


# ---------------------------------------------------------------------------
# Happy paths stay silent
# ---------------------------------------------------------------------------


class TestNoMissNoNoise:
    def test_full_coverage_no_error_no_warning(self) -> None:
        lf = pl.DataFrame({"age_band": ["18-25", "26-40"], "region": ["North", "South"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(lf, _age_region_config()).collect()
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(99.0)]
        assert [log for log in logs if log["event"] == MISS_EVENT] == []

    def test_empty_frame_no_error(self) -> None:
        lf = pl.DataFrame(
            {"age_band": [], "region": []},
            schema={"age_band": pl.Utf8, "region": pl.Utf8},
        ).lazy()
        out = apply_rating_step_from_config(lf, _age_region_config()).collect()
        assert out.height == 0

    def test_usable_default_fills_without_error_or_warning(self) -> None:
        config = _age_region_config(defaultValue="1.0")
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        assert out["age_factor"].to_list() == [1.5, 1.0]
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(90.0)]
        assert [log for log in logs if log["event"] == MISS_EVENT] == []


# ---------------------------------------------------------------------------
# Opt-in: onMissing = "neutral"
# ---------------------------------------------------------------------------


class TestOptInNeutral:
    def test_neutral_fills_multiply_and_warns_with_count(self) -> None:
        config = _age_region_config(onMissing="neutral")
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        # Old numeric behaviour, now explicit: null table output, neutral combine.
        assert out["age_factor"].to_list() == [1.5, None]
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(90.0)]
        miss_logs = [log for log in logs if log["event"] == MISS_EVENT]
        assert len(miss_logs) == 1
        log = miss_logs[0]
        assert log["log_level"] == "warning"
        assert log["table"] == "age_factor"
        assert log["output_column"] == "age_factor"
        assert log["miss_count"] == 1
        assert {"age_band": "26-39"} in log["missing_keys"]

    def test_neutral_add_contributes_zero(self) -> None:
        config = _age_region_config(onMissing="neutral")
        config["combinedOutputs"] = [
            {"outputColumn": "total", "operation": "add", "baseValue": 100.0}
        ]
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        assert out["total"].to_list() == [pytest.approx(102.7), pytest.approx(100.9)]
        assert any(log["event"] == MISS_EVENT for log in logs)

    @pytest.mark.parametrize("operation", ["min", "max"])
    def test_neutral_min_max_skip_nulls(self, operation: str) -> None:
        config = _age_region_config(onMissing="neutral")
        config["combinedOutputs"] = [
            {"outputColumn": "combined", "operation": operation, "baseValue": 1.0}
        ]
        out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        # Horizontal min/max ignore nulls: the missed factor has no effect.
        expected_row2 = {"min": 0.9, "max": 1.0}[operation]
        assert out["combined"].to_list()[1] == pytest.approx(expected_row2)

    def test_neutral_uncombined_table_leaves_null_and_warns(self) -> None:
        config = {
            "tables": [
                {
                    "name": "T",
                    "factors": ["band"],
                    "outputColumn": "out",
                    "onMissing": "neutral",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ]
        }
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(lf, config).collect()
        assert out["out"].to_list() == [2.0, None]
        assert any(log["event"] == MISS_EVENT for log in logs)

    def test_neutral_with_no_misses_does_not_warn(self) -> None:
        config = _age_region_config(onMissing="neutral")
        lf = pl.DataFrame({"age_band": ["18-25", "26-40"], "region": ["North", "South"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            apply_rating_step_from_config(lf, config).collect()
        assert [log for log in logs if log["event"] == MISS_EVENT] == []

    def test_usable_default_wins_over_neutral(self) -> None:
        """defaultValue fills before the miss guard sees anything."""
        config = _age_region_config(defaultValue="1.0", onMissing="neutral")
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        assert out["age_factor"].to_list() == [1.5, 1.0]
        assert [log for log in logs if log["event"] == MISS_EVENT] == []

    def test_invalid_on_missing_value_raises(self) -> None:
        config = _age_region_config(onMissing="zebra")
        with pytest.raises(ValueError, match="onMissing"):
            apply_rating_step_from_config(_renamed_band_frame(), config)

    def test_explicit_error_value_is_accepted(self) -> None:
        config = _age_region_config(onMissing="error")
        with pytest.raises(RatingTableMissError):
            apply_rating_step_from_config(_renamed_band_frame(), config).collect()

    def test_on_missing_round_trips_through_sidecar(self) -> None:
        """The opt-in key must survive canonical normalisation and JSON round-trip."""
        config = _age_region_config(onMissing="neutral")
        compacted = normalise_rating_step_config(config)
        rehydrated = normalise_rating_step_config(json.loads(json.dumps(compacted)))
        assert rehydrated["tables"][0]["onMissing"] == "neutral"
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(_renamed_band_frame(), rehydrated).collect()
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(90.0)]
        assert any(log["event"] == MISS_EVENT for log in logs)


# ---------------------------------------------------------------------------
# Direct _apply_rating_table behaviour (unit level)
# ---------------------------------------------------------------------------


class TestApplyRatingTableMissGuard:
    def test_direct_call_raises_on_miss(self) -> None:
        table = {
            "name": "Direct",
            "factors": ["k"],
            "outputColumn": "out",
            "entries": [{"k": "a", "value": 1.0}],
        }
        lf = pl.DataFrame({"k": ["a", "b"]}).lazy()
        with pytest.raises(RatingTableMissError, match="'out'"):
            _apply_rating_table(lf, table).collect()

    def test_direct_call_neutral_keeps_null(self) -> None:
        table = {
            "name": "Direct",
            "factors": ["k"],
            "outputColumn": "out",
            "onMissing": "neutral",
            "entries": [{"k": "a", "value": 1.0}],
        }
        lf = pl.DataFrame({"k": ["a", "b"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = _apply_rating_table(lf, table).collect()
        assert out["out"].to_list() == [1.0, None]
        assert any(log["event"] == MISS_EVENT for log in logs)
