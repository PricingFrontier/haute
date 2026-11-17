"""A rating-table lookup miss never stops the run.

A miss is a row whose factor combination has no entry. A live quote must still
be priced when a level is not in a table (decided 13 November 2026), so a miss
never raises:

* A table with a ``default_value`` (On miss: Use default) fills every miss
  with it, silently.
* A table without one (On miss: Leave empty) leaves its output null; combined
  outputs fill the operation's neutral element, and every batch with misses is
  counted and logged (WARNING with the table, the count and the keys). Visible,
  never silent.
* A ``default_value`` that is set but is not a finite number is refused when
  the lookup is built, rather than ignored, which would leave the misses empty
  unseen. There is no ``on_missing`` key: the config model refuses one.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl
import pytest
import structlog.testing

from haute._config_validation import validate_node_config
from haute._polars_utils import streaming_collect
from haute._rating import (
    _apply_rating_miss_guard,
    _apply_rating_table,
    apply_rating_step_from_config,
)
from haute._types import NodeType
from haute._validation_error import ConfigSettingError
from haute.errors import HauteError
from haute.executor import _build_node_fn
from haute.graph_utils import GraphNode, NodeData

MISS_EVENT = "rating_table_lookup_misses"


def _rating_node(nid: str, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="ratingStep", config=config))


def _age_region_config(**age_table_extra: Any) -> dict[str, Any]:
    """Two-table config with a combined multiply output."""
    return {
        "tables": [
            {
                "factors": ["age_band"],
                "output_column": "age_factor",
                "entries": [
                    {"age_band": "18-25", "value": 1.5},
                    {"age_band": "26-40", "value": 1.1},
                ],
                **age_table_extra,
            },
            {
                "factors": ["region"],
                "output_column": "region_factor",
                "entries": [
                    {"region": "North", "value": 1.2},
                    {"region": "South", "value": 0.9},
                ],
            },
        ],
        "combined_outputs": [
            {"output_column": "premium", "operation": "multiply", "base_value": 100.0}
        ],
    }


def _renamed_band_frame() -> pl.LazyFrame:
    """Row 2's age_band was renamed 26-40 -> 26-39 and now misses the table."""
    return pl.DataFrame({"age_band": ["18-25", "26-39"], "region": ["North", "South"]}).lazy()


def _one_table(**extra: Any) -> dict[str, Any]:
    return {
        "tables": [
            {
                "factors": ["band"],
                "output_column": "out",
                "entries": [{"band": "A", "value": 2.0}],
                **extra,
            }
        ]
    }


def _miss_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [log for log in logs if log["event"] == MISS_EVENT]


# ---------------------------------------------------------------------------
# Leave empty: a miss never stops the run, and is reported
# ---------------------------------------------------------------------------


class TestMissLeftEmpty:
    def test_renamed_band_label_prices_at_neutral_and_warns(self) -> None:
        """A renamed band label leaves its factor empty and says so, never stopping the run."""
        with structlog.testing.capture_logs() as logs:
            out = streaming_collect(
                apply_rating_step_from_config(_renamed_band_frame(), _age_region_config())
            )
        assert out["age_factor"].to_list() == [1.5, None]
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(90.0)]
        [log] = _miss_logs(logs)
        assert log["log_level"] == "warning"
        assert log["table"] == "age_factor"
        assert log["output_column"] == "age_factor"
        assert log["miss_count"] == 1
        assert log["row_count"] == 2
        assert log["missing_keys"] == [{"age_band": "26-39"}]

    def test_through_the_executor_builder(self) -> None:
        node = _rating_node("r1", _age_region_config())
        _, fn, _ = _build_node_fn(node)
        out = streaming_collect(fn(_renamed_band_frame()))
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(90.0)]

    def test_in_memory_engine_agrees_with_streaming(self) -> None:
        lazy = apply_rating_step_from_config(_renamed_band_frame(), _age_region_config())
        assert lazy.collect().equals(streaming_collect(lazy))

    def test_add_contributes_zero(self) -> None:
        config = _age_region_config()
        config["combined_outputs"] = [
            {"output_column": "total", "operation": "add", "base_value": 100.0}
        ]
        out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        assert out["total"].to_list() == [pytest.approx(102.7), pytest.approx(100.9)]

    @pytest.mark.parametrize("operation", ["min", "max"])
    def test_min_max_skip_the_empty_factor(self, operation: str) -> None:
        config = _age_region_config()
        config["combined_outputs"] = [
            {"output_column": "combined", "operation": operation, "base_value": 1.0}
        ]
        out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        expected_row2 = {"min": 0.9, "max": 1.0}[operation]
        assert out["combined"].to_list()[1] == pytest.approx(expected_row2)

    def test_uncombined_table_leaves_null_and_warns(self) -> None:
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(lf, _one_table()).collect()
        assert out["out"].to_list() == [2.0, None]
        assert len(_miss_logs(logs)) == 1

    def test_null_factor_value_is_a_miss(self) -> None:
        """Null keys never match the lookup join: the row's rate is left empty."""
        lf = pl.DataFrame({"band": ["A", None]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(lf, _one_table()).collect()
        assert out["out"].to_list() == [2.0, None]
        assert len(_miss_logs(logs)) == 1

    def test_missing_keys_capped_in_the_report(self) -> None:
        entries = [{"k": f"present_{i}", "value": 1.0} for i in range(3)]
        config = {"tables": [{"factors": ["k"], "output_column": "out", "entries": entries}]}
        lf = pl.DataFrame({"k": [f"absent_{i:02d}" for i in range(25)]}).lazy()
        with structlog.testing.capture_logs() as logs:
            apply_rating_step_from_config(lf, config).collect()
        [log] = _miss_logs(logs)
        assert log["miss_count"] == 25
        assert log["distinct_missing_keys"] == 25
        # Capped: the first ten keys, in first-seen order.
        assert log["missing_keys"] == [{"k": f"absent_{i:02d}"} for i in range(10)]

    def test_multi_factor_missing_keys_name_every_factor(self) -> None:
        config = {
            "tables": [
                {
                    "factors": ["region", "tier"],
                    "output_column": "out",
                    "entries": [{"region": "North", "tier": "gold", "value": 1.0}],
                }
            ]
        }
        lf = pl.DataFrame({"region": ["North"], "tier": ["silver"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            apply_rating_step_from_config(lf, config).collect()
        [log] = _miss_logs(logs)
        assert log["missing_keys"] == [{"region": "North", "tier": "silver"}]

    def test_the_guard_reports_an_eager_frames_misses_and_returns_it_unchanged(self) -> None:
        frame = pl.DataFrame({"__key": ["a", "b", "b"], "__value": [1.0, None, None]})
        with structlog.testing.capture_logs() as logs:
            out = _apply_rating_miss_guard(
                frame,
                ["k"],
                key_columns=["__key"],
                lookup_value_column="__value",
                table_label="out",
                output_col="out",
            )
        assert isinstance(out, pl.DataFrame) and out.equals(frame)
        [log] = _miss_logs(logs)
        assert (log["miss_count"], log["distinct_missing_keys"]) == (2, 1)
        assert log["missing_keys"] == [{"k": "b"}]

    def test_direct_call_keeps_null_and_warns(self) -> None:
        table = {"factors": ["k"], "output_column": "out", "entries": [{"k": "a", "value": 1.0}]}
        lf = pl.DataFrame({"k": ["a", "b"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = streaming_collect(_apply_rating_table(lf, table))
        assert out["out"].to_list() == [1.0, None]
        assert len(_miss_logs(logs)) == 1


# ---------------------------------------------------------------------------
# Use default: misses take the table's default, silently
# ---------------------------------------------------------------------------


class TestUseDefault:
    @pytest.mark.parametrize("default", ["1.0", 1.0, 1, " 1.0 "])
    def test_default_fills_every_miss_without_warning(self, default: object) -> None:
        config = _age_region_config(default_value=default)
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(_renamed_band_frame(), config).collect()
        assert out["age_factor"].to_list() == [1.5, 1.0]
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(90.0)]
        assert _miss_logs(logs) == []

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_default_is_none(self, blank: object) -> None:
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(lf, _one_table(default_value=blank)).collect()
        assert out["out"].to_list() == [2.0, None]
        assert len(_miss_logs(logs)) == 1

    @pytest.mark.parametrize("unusable", ["N/A", "inf", "-Infinity", "nan", float("inf"), True])
    def test_an_unusable_default_is_refused_naming_the_table(self, unusable: object) -> None:
        """Ignoring it would leave the table's misses empty unseen."""
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        with pytest.raises(ConfigSettingError, match=re.escape("tables[0]")) as excinfo:
            apply_rating_step_from_config(lf, _one_table(default_value=unusable))
        assert excinfo.value.setting == "tables"
        assert excinfo.value.values == (unusable,)
        assert excinfo.value.fix is not None

    def test_an_unusable_default_is_refused_when_the_config_is_saved(self) -> None:
        """Refused at the config boundary, so it can be neither saved nor deployed."""
        with pytest.raises(
            ConfigSettingError, match=re.escape("tables[0] has default_value 'N/A'")
        ):
            validate_node_config(NodeType.RATING_STEP, _one_table(default_value="N/A"))
        validate_node_config(NodeType.RATING_STEP, _one_table(default_value="1.0"))


# ---------------------------------------------------------------------------
# No miss, no noise
# ---------------------------------------------------------------------------


class TestNoMissNoNoise:
    def test_full_coverage_no_warning(self) -> None:
        lf = pl.DataFrame({"age_band": ["18-25", "26-40"], "region": ["North", "South"]}).lazy()
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(lf, _age_region_config()).collect()
        assert out["premium"].to_list() == [pytest.approx(180.0), pytest.approx(99.0)]
        assert _miss_logs(logs) == []

    def test_empty_frame(self) -> None:
        lf = pl.DataFrame(
            {"age_band": [], "region": []},
            schema={"age_band": pl.Utf8, "region": pl.Utf8},
        ).lazy()
        out = apply_rating_step_from_config(lf, _age_region_config()).collect()
        assert out.height == 0


# ---------------------------------------------------------------------------
# The retired on_missing key
# ---------------------------------------------------------------------------


class TestOnMissingRetired:
    @pytest.mark.parametrize("policy", ["neutral", "error"])
    def test_the_config_model_refuses_on_missing(self, policy: str) -> None:
        with pytest.raises(HauteError, match="on_missing"):
            validate_node_config(NodeType.RATING_STEP, _one_table(on_missing=policy))
