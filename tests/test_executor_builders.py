"""Tests for executor builder functions — the _build_* dispatch table.

Covers the simpler node builders: constant, banding, scenario_expander,
api_input, and output.  Uses the same helpers from conftest.py as the
existing test_executor.py.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._output_assembler import OutputMappingSchemaError
from haute.errors import (
    BoundedMemoryUnsupportedError,
    LiveSwitchScenarioError,
    SchemaMismatchError,
)
from haute.executor import NodeBuildContext, _build_node_fn
from haute.graph_utils import GraphNode, NodeData
from tests.conftest import make_node as _n

# ---------------------------------------------------------------------------
# NodeBuildContext property tests
# ---------------------------------------------------------------------------


class TestNodeBuildContextProperties:
    """Tests for the func_name and config computed properties."""

    def _make_ctx(
        self,
        label: str = "My Node",
        config: dict | None = None,
    ) -> NodeBuildContext:
        node = GraphNode(
            id="n1",
            data=NodeData(
                label=label,
                nodeType="polars",
                config=config or {},
            ),
        )
        return NodeBuildContext(
            node=node,
            source_names=[],
            source_ids=[],
            target_handles=None,
            row_limit=None,
            node_map=None,
            orig_source_names=None,
            preamble_ns=None,
            source=None,
        )

    def test_func_name_sanitizes_spaces(self) -> None:
        ctx = self._make_ctx(label="My Node")
        assert ctx.func_name == "My_Node"
        assert ctx.func_name.isidentifier()

    def test_func_name_sanitizes_special_chars(self) -> None:
        ctx = self._make_ctx(label="rate@2024!")
        name = ctx.func_name
        assert "@" not in name
        assert "!" not in name
        assert name.isidentifier()

    def test_func_name_sanitizes_leading_digit(self) -> None:
        ctx = self._make_ctx(label="1st node")
        assert ctx.func_name.isidentifier()
        assert not ctx.func_name[0].isdigit()

    def test_func_name_sanitizes_reserved_word(self) -> None:
        ctx = self._make_ctx(label="return")
        assert ctx.func_name != "return"
        assert ctx.func_name.isidentifier()

    def test_func_name_empty_label(self) -> None:
        ctx = self._make_ctx(label="")
        assert ctx.func_name.isidentifier()

    def test_config_returns_node_config_dict(self) -> None:
        cfg = {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "cacheMode": "direct",
            "path": "data.csv",
            "arguments": {},
        }
        ctx = self._make_ctx(config=cfg)
        assert ctx.config is ctx.node.data.config
        assert ctx.config == cfg

    def test_config_empty_dict(self) -> None:
        ctx = self._make_ctx(config={})
        assert ctx.config == {}

    def test_config_returns_same_object(self) -> None:
        """config property should return the same dict (not a copy)."""
        cfg = {"key": "value"}
        ctx = self._make_ctx(config=cfg)
        assert ctx.config is ctx.config  # same object on repeated access

    def test_func_name_consistent_across_calls(self) -> None:
        """func_name should return the same value on repeated access."""
        ctx = self._make_ctx(label="Test Node")
        assert ctx.func_name == ctx.func_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build(
    node_type: str,
    config: dict,
    label: str = "test_node",
    source_names: list[str] | None = None,
):
    """Build a node function via _build_node_fn and return (func_name, fn, is_source)."""
    node = _n(
        {
            "id": "n1",
            "data": {"label": label, "nodeType": node_type, "config": config},
        }
    )
    return _build_node_fn(node, source_names=source_names or [])


# ---------------------------------------------------------------------------
# _build_constant
# ---------------------------------------------------------------------------


class TestBuildConstant:
    """Tests for the constant node builder."""

    def test_single_numeric_value(self) -> None:
        func_name, fn, is_source = _build(
            "constant",
            {"values": [{"name": "rate", "value": "0.05"}]},
            label="MyRate",
        )
        assert func_name == "MyRate"
        assert is_source is True
        result = fn().collect()
        assert result.shape == (1, 1)
        assert result["rate"].to_list() == [0.05]

    def test_multiple_values(self) -> None:
        _, fn, _ = _build(
            "constant",
            {
                "values": [
                    {"name": "a", "value": "10"},
                    {"name": "b", "value": "20.5"},
                ]
            },
        )
        result = fn().collect()
        assert result.shape == (1, 2)
        assert result["a"].to_list() == [10.0]
        assert result["b"].to_list() == [20.5]

    def test_string_value_not_numeric(self) -> None:
        _, fn, _ = _build(
            "constant",
            {"values": [{"name": "region", "value": "north"}]},
        )
        result = fn().collect()
        assert result["region"].to_list() == ["north"]

    def test_empty_values_gives_default(self) -> None:
        _, fn, _ = _build("constant", {"values": []})
        result = fn().collect()
        assert "constant" in result.columns
        assert result["constant"].to_list() == [0]

    def test_none_values_gives_default(self) -> None:
        _, fn, _ = _build("constant", {"values": None})
        result = fn().collect()
        assert "constant" in result.columns
        assert result["constant"].to_list() == [0]

    def test_missing_values_key_gives_default(self) -> None:
        _, fn, _ = _build("constant", {})
        result = fn().collect()
        assert "constant" in result.columns

    def test_value_with_empty_name_skipped(self) -> None:
        _, fn, _ = _build(
            "constant",
            {"values": [{"name": "", "value": "5"}, {"name": "x", "value": "1"}]},
        )
        result = fn().collect()
        assert "x" in result.columns
        assert result.shape == (1, 1)  # empty name skipped

    def test_mixed_numeric_and_string(self) -> None:
        _, fn, _ = _build(
            "constant",
            {
                "values": [
                    {"name": "count", "value": "42"},
                    {"name": "label", "value": "hello"},
                ]
            },
        )
        result = fn().collect()
        assert result["count"].to_list() == [42.0]
        assert result["label"].to_list() == ["hello"]

    def test_integer_value_stored_as_float(self) -> None:
        _, fn, _ = _build(
            "constant",
            {"values": [{"name": "n", "value": "100"}]},
        )
        result = fn().collect()
        assert result["n"].to_list() == [100.0]


# ---------------------------------------------------------------------------
# _build_output
# ---------------------------------------------------------------------------


class TestBuildOutput:
    """Tests for the output node builder (v2 ``outputMapping``; v1 ``fields`` killed)."""

    @staticmethod
    def _cfg(fields: list[str], *, source_port: str = "in") -> dict:
        """A v2 flat outputMapping (one top-level array element per field)."""
        return {
            "outputMapping": [
                {
                    "source_port": source_port,
                    "source_column": f,
                    "output_path": f"$[:].{f}",
                    "enabled": True,
                }
                for f in fields
            ],
            "outputFormat": "json",
        }

    def test_flat_mapping_renders_array_of_rows(self) -> None:
        _, fn, is_source = _build(
            "output",
            self._cfg(["x", "y"]),
            source_names=["upstream"],
        )
        assert is_source is False
        input_df = pl.DataFrame({"x": [1], "y": [2], "z": [3]}).lazy()
        result = fn(input_df).collect()
        # A flat mapping projects to the mapped columns, dropping unmapped ``z``.
        assert result.columns == ["x", "y"]
        assert result.to_dicts() == [{"x": 1, "y": 2}]

    def test_missing_output_mapping_raises(self) -> None:
        # The legacy ``fields`` shape no longer builds — it carries no mapping.
        with pytest.raises(OutputMappingSchemaError, match="no `outputMapping`"):
            _build("output", {"fields": ["x"]}, source_names=["upstream"])

    def test_empty_config_raises(self) -> None:
        with pytest.raises(OutputMappingSchemaError, match="no `outputMapping`"):
            _build("output", {}, source_names=["upstream"])

    def test_empty_mapping_renders_empty_document(self) -> None:
        _, fn, _ = _build("output", self._cfg([]), source_names=["upstream"])
        result = fn(pl.DataFrame({"a": [1], "b": [2]}).lazy()).collect()
        assert result.to_dicts() == []

    def test_disabled_entry_is_skipped(self) -> None:
        cfg = self._cfg(["x", "y"])
        cfg["outputMapping"][1]["enabled"] = False
        _, fn, _ = _build("output", cfg, source_names=["upstream"])
        result = fn(pl.DataFrame({"x": [1], "y": [2]}).lazy()).collect()
        assert result.columns == ["x"]

    def test_single_frame_fallback_resolves_named_port(self) -> None:
        # ``source_port`` names the upstream table, which need not equal the
        # sanitized node label the executor uses as the positional key. A
        # single incoming frame resolves it regardless.
        _, fn, _ = _build(
            "output",
            self._cfg(["x"], source_port="policies"),
            source_names=["some_other_node"],
        )
        result = fn(pl.DataFrame({"x": [7]}).lazy()).collect()
        assert result.to_dicts() == [{"x": 7}]

    def test_func_name_sanitized(self) -> None:
        func_name, _, _ = _build(
            "output",
            self._cfg(["x"]),
            label="Final Output (v2)",
            source_names=["upstream"],
        )
        # Should be a valid Python identifier
        assert func_name.isidentifier()


# ---------------------------------------------------------------------------
# _build_banding
# ---------------------------------------------------------------------------


class TestBuildBanding:
    """Tests for the banding node builder."""

    def test_continuous_single_factor(self) -> None:
        _, fn, is_source = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 25, "assignment": "young"},
                            {
                                "op1": ">=",
                                "val1": 25,
                                "op2": "<",
                                "val2": 65,
                                "assignment": "adult",
                            },
                            {
                                "op1": ">=",
                                "val1": 65,
                                "op2": "<=",
                                "val2": 200,
                                "assignment": "senior",
                            },
                        ],
                    }
                ],
            },
            source_names=["upstream"],
        )
        assert is_source is False
        input_df = pl.DataFrame({"age": [10, 30, 70]}).lazy()
        result = fn(input_df).collect()
        assert "age_band" in result.columns
        assert result["age_band"].to_list() == ["young", "adult", "senior"]

    def test_categorical_banding(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "vehicle",
                        "outputColumn": "vehicle_group",
                        "banding": "categorical",
                        "rules": [
                            {"value": "sedan", "assignment": "car"},
                            {"value": "suv", "assignment": "car"},
                            {"value": "motorcycle", "assignment": "bike"},
                        ],
                    }
                ],
            },
            source_names=["data"],
        )
        input_df = pl.DataFrame({"vehicle": ["sedan", "suv", "motorcycle"]}).lazy()
        result = fn(input_df).collect()
        assert result["vehicle_group"].to_list() == ["car", "car", "bike"]

    def test_multi_factor_banding(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {
                                "op1": ">=",
                                "val1": 0,
                                "op2": "<",
                                "val2": 50,
                                "assignment": "under50",
                            },
                            {
                                "op1": ">=",
                                "val1": 50,
                                "op2": "<=",
                                "val2": 200,
                                "assignment": "50plus",
                            },
                        ],
                    },
                    {
                        "column": "region",
                        "outputColumn": "region_group",
                        "banding": "categorical",
                        "rules": [
                            {"value": "north", "assignment": "cold"},
                            {"value": "south", "assignment": "warm"},
                        ],
                    },
                ],
            },
            source_names=["data"],
        )
        input_df = pl.DataFrame(
            {
                "age": [20, 60],
                "region": ["north", "south"],
            }
        ).lazy()
        result = fn(input_df).collect()
        assert result["age_band"].to_list() == ["under50", "50plus"]
        assert result["region_group"].to_list() == ["cold", "warm"]

    def test_empty_factors_list_passthrough(self) -> None:
        _, fn, _ = _build("banding", {"factors": []}, source_names=["upstream"])
        input_df = pl.DataFrame({"x": [1, 2]}).lazy()
        result = fn(input_df).collect()
        assert result.columns == ["x"]
        assert result["x"].to_list() == [1, 2]

    def test_no_factors_key_passthrough(self) -> None:
        _, fn, _ = _build("banding", {}, source_names=["upstream"])
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert result.columns == ["x"]

    def test_factor_with_missing_column_skipped(self) -> None:
        """A factor with empty column name should be skipped, not error."""
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "",
                        "outputColumn": "out",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "low"},
                        ],
                    }
                ],
            },
            source_names=["data"],
        )
        input_df = pl.DataFrame({"x": [5]}).lazy()
        result = fn(input_df).collect()
        # Should pass through without adding column
        assert "out" not in result.columns

    def test_factor_with_empty_rules_skipped(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [],
                    }
                ],
            },
            source_names=["data"],
        )
        input_df = pl.DataFrame({"age": [25]}).lazy()
        result = fn(input_df).collect()
        assert "age_band" not in result.columns

    def test_continuous_with_default(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "default": "unknown",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 25, "assignment": "young"},
                        ],
                    }
                ],
            },
            source_names=["data"],
        )
        input_df = pl.DataFrame({"age": [10, 50]}).lazy()
        result = fn(input_df).collect()
        bands = result["age_band"].to_list()
        assert bands[0] == "young"
        assert bands[1] == "unknown"


# ---------------------------------------------------------------------------
# _build_scenario_expander
# ---------------------------------------------------------------------------


class TestBuildScenarioExpander:
    """Tests for the scenario expander node builder.

    Note: basic coverage already exists in test_scenario_expander.py.
    These tests focus on edge cases and builder-specific behavior.
    """

    def test_is_not_a_source_node(self) -> None:
        _, _, is_source = _build(
            "scenarioExpander",
            {"column_name": "sv", "min_value": 0.9, "max_value": 1.1, "steps": 3},
            source_names=["upstream"],
        )
        assert is_source is False

    def test_empty_input_cross_join(self) -> None:
        """Expanding an empty input should yield 0 rows but correct columns."""
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "sv", "min_value": 0.9, "max_value": 1.1, "steps": 3},
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"id": pl.Series([], dtype=pl.Int32)}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 0
        assert "sv" in result.columns

    def test_single_step(self) -> None:
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "val", "min_value": 1.0, "max_value": 1.0, "steps": 1},
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"x": [100]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 1
        assert result["val"].to_list() == [pytest.approx(1.0)]

    def test_custom_step_column(self) -> None:
        _, fn, _ = _build(
            "scenarioExpander",
            {
                "column_name": "sv",
                "min_value": 0.5,
                "max_value": 1.5,
                "steps": 3,
                "step_column": "my_idx",
            },
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert "my_idx" in result.columns
        assert result["my_idx"].to_list() == [0, 1, 2]

    def test_index_only_when_column_name_empty(self) -> None:
        """Empty column_name produces index column only, no value column."""
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "", "steps": 3, "step_column": "idx"},
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 3
        assert "idx" in result.columns
        assert "x" in result.columns
        # No value column should be present
        assert len(result.columns) == 2

    def test_user_code_transforms_expanded_data(self) -> None:
        """User Polars code runs after the cross-join expansion."""
        _, fn, _ = _build(
            "scenarioExpander",
            {
                "column_name": "sv",
                "min_value": 0.8,
                "max_value": 1.2,
                "steps": 3,
                "code": 'df = df.filter(pl.col("sv") >= 1.0)',
            },
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        # 3 steps: 0.8, 1.0, 1.2 — filter keeps 1.0 and 1.2
        assert result.shape[0] == 2
        assert all(v >= 1.0 for v in result["sv"].to_list())

    def test_empty_code_behaves_as_before(self) -> None:
        """Empty code string doesn't change behavior."""
        _, fn, _ = _build(
            "scenarioExpander",
            {
                "column_name": "sv",
                "min_value": 0.8,
                "max_value": 1.2,
                "steps": 3,
                "code": "",
            },
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 3

    def test_user_code_with_assignment(self) -> None:
        """User code using assignment syntax works."""
        _, fn, _ = _build(
            "scenarioExpander",
            {
                "column_name": "sv",
                "min_value": 0.5,
                "max_value": 1.5,
                "steps": 3,
                "code": 'df = df.with_columns(pl.col("sv").alias("factor"))',
            },
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert "factor" in result.columns
        assert result.shape[0] == 3


# ---------------------------------------------------------------------------
# _build_api_input
# ---------------------------------------------------------------------------


class TestBuildApiInput:
    """Tests for the API input node builder."""

    def test_parquet_source(self, tmp_path: Path, _widen_sandbox_root) -> None:
        data_file = tmp_path / "input.parquet"
        pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}).write_parquet(data_file)

        func_name, fn, is_source = _build(
            "apiInput",
            {"path": str(data_file)},
            label="API_Data",
        )
        assert func_name == "API_Data"
        assert is_source is True
        result = fn().collect()
        assert result.shape == (3, 2)
        assert result["a"].to_list() == [1, 2, 3]

    def test_csv_source(self, tmp_path: Path, _widen_sandbox_root) -> None:
        data_file = tmp_path / "input.csv"
        pl.DataFrame({"x": [10, 20]}).write_csv(data_file)

        _, fn, is_source = _build(
            "apiInput",
            {"path": str(data_file)},
            label="CSV_Input",
        )
        assert is_source is True
        result = fn().collect()
        assert result.shape == (2, 1)

    def test_bounded_csv_source_requires_declared_dtypes(
        self,
        tmp_path: Path,
        _widen_sandbox_root,
    ) -> None:
        data_file = tmp_path / "input.csv"
        data_file.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "CSV_Input",
                    "nodeType": "apiInput",
                    "config": {"path": str(data_file)},
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            execution_profile=ExecutionProfile.AUTO_RANGE.value,
        )

        with pytest.raises(BoundedMemoryUnsupportedError, match="CSV sources require"):
            fn()

    def test_bounded_csv_source_uses_declared_dtypes(
        self,
        tmp_path: Path,
        _widen_sandbox_root,
    ) -> None:
        data_file = tmp_path / "input.csv"
        data_file.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "CSV_Input",
                    "nodeType": "apiInput",
                    "config": {
                        "path": str(data_file),
                        "schema_overrides": {
                            "quote_id": "String",
                            "premium": "Float64",
                        },
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            execution_profile=ExecutionProfile.AUTO_RANGE.value,
        )

        result = fn().collect()

        assert result.schema["quote_id"] == pl.String
        assert result["quote_id"].to_list() == ["001"]

    def test_func_name_sanitized(self) -> None:
        func_name, _, _ = _build(
            "apiInput",
            {"path": "some.parquet"},
            label="My Input (v2)",
        )
        assert func_name.isidentifier()

    def test_source_projection_maps_renamed_output_columns_to_physical_columns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_file = tmp_path / "input.csv"
        data_file.write_text("quote_id,premium_raw,unused\n001,10.5,x\n", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_read_data_source(
            config,
            *,
            profile=None,
            columns=None,
            validate_columns=None,
        ):
            captured["profile"] = profile
            captured["columns"] = columns
            captured["validate_columns"] = validate_columns
            return pl.DataFrame({"quote_id": ["001"], "premium_raw": [10.5]}).lazy()

        monkeypatch.setattr("haute._builders.read_data_source", fake_read_data_source)
        node = _n(
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": "apiInput",
                    "config": {
                        "path": str(data_file),
                        "selected_columns": ["quote_id", "premium_raw", "unused"],
                        "column_renames": {"premium_raw": "premium"},
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            required_output_columns=frozenset({"quote_id", "premium"}),
            execution_profile=ExecutionProfile.AUTO_RANGE.value,
        )

        fn()

        assert captured["columns"] == frozenset({"quote_id", "premium_raw"})
        assert captured["validate_columns"] == frozenset({"quote_id", "premium_raw", "unused"})

    def test_source_projection_avoids_ambiguous_rename_pushdown(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_file = tmp_path / "input.csv"
        data_file.write_text("a,b\n1,2\n", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_read_data_source(
            config,
            *,
            profile=None,
            columns=None,
            validate_columns=None,
        ):
            captured["columns"] = columns
            captured["validate_columns"] = validate_columns
            return pl.DataFrame({"a": [1], "b": [2]}).lazy()

        monkeypatch.setattr("haute._builders.read_data_source", fake_read_data_source)
        node = _n(
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": "apiInput",
                    "config": {
                        "path": str(data_file),
                        "column_renames": {"a": "x", "b": "x"},
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            required_output_columns=frozenset({"x"}),
            execution_profile=ExecutionProfile.AUTO_RANGE.value,
        )

        fn()

        assert captured["columns"] is None
        assert captured["validate_columns"] == frozenset()

    def test_source_projection_validates_stale_selected_columns(
        self,
        tmp_path: Path,
    ) -> None:
        data_file = tmp_path / "input.parquet"
        pl.DataFrame({"quote_id": [1], "premium": [10.5]}).write_parquet(data_file)
        node = _n(
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": "apiInput",
                    "config": {
                        "path": str(data_file),
                        "selected_columns": ["quote_id", "stale_column"],
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            required_output_columns=frozenset({"quote_id"}),
            execution_profile=ExecutionProfile.AUTO_RANGE.value,
        )

        with pytest.raises(SchemaMismatchError, match="selected_columns"):
            fn()

    def test_source_projection_rejects_demand_excluded_by_selected_columns(
        self,
        tmp_path: Path,
    ) -> None:
        data_file = tmp_path / "input.parquet"
        pl.DataFrame({"quote_id": [1], "raw_premium": [10.5]}).write_parquet(data_file)
        node = _n(
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": "apiInput",
                    "config": {
                        "path": str(data_file),
                        "selected_columns": ["quote_id"],
                        "column_renames": {"raw_premium": "premium"},
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            required_output_columns=frozenset({"premium"}),
            execution_profile=ExecutionProfile.AUTO_RANGE.value,
        )

        with pytest.raises(ValueError, match="excluded by selected_columns"):
            fn()


# ---------------------------------------------------------------------------
# Builder dispatch fallback
# ---------------------------------------------------------------------------


class TestBuildNodeFnFallback:
    """Ensure unknown node types fall back to passthrough."""

    def test_unknown_type_passthrough(self) -> None:
        node = _n(
            {
                "id": "n1",
                "data": {"label": "Unknown", "nodeType": "polars", "config": {}},
            }
        )
        func_name, fn, is_source = _build_node_fn(node, source_names=["upstream"])
        assert func_name == "Unknown"
        assert is_source is False
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert result["x"].to_list() == [1]


# ---------------------------------------------------------------------------
# _build_data_sink (passthrough in preview mode)
# ---------------------------------------------------------------------------


class TestBuildDataSink:
    """Sink builder should be a passthrough during preview."""

    def test_passthrough(self) -> None:
        _, fn, is_source = _build(
            "dataOutput",
            {"path": "out.parquet", "format": "parquet"},
            source_names=["upstream"],
        )
        assert is_source is False
        input_df = pl.DataFrame({"x": [1, 2]}).lazy()
        result = fn(input_df).collect()
        assert result["x"].to_list() == [1, 2]


# ---------------------------------------------------------------------------
# _build_explore (analysis-only passthrough in preview mode)
# ---------------------------------------------------------------------------


class TestBuildExplore:
    """Explore builder should be a one-input analysis sink passthrough."""

    def test_passthrough(self) -> None:
        _, fn, is_source = _build(
            "explore",
            {},
            source_names=["upstream"],
        )
        assert is_source is False
        input_df = pl.DataFrame({"x": [1, 2]}).lazy()
        result = fn(input_df).collect()
        assert result["x"].to_list() == [1, 2]

    def test_user_code_transforms_analysis_data(self) -> None:
        _, fn, is_source = _build(
            "explore",
            {
                "code": (
                    "df = df.filter(pl.col('x') > 1)"
                    ".with_columns((pl.col('x') * 10).alias('scaled'))"
                )
            },
            source_names=["upstream"],
        )
        assert is_source is False
        input_df = pl.DataFrame({"x": [1, 2, 3]}).lazy()

        result = fn(input_df).collect()

        assert result["x"].to_list() == [2, 3]
        assert result["scaled"].to_list() == [20, 30]

    def test_no_input_raises(self) -> None:
        _, fn, is_source = _build("explore", {})
        assert is_source is False
        with pytest.raises(TypeError):
            fn()

    def test_multiple_inputs_raise(self) -> None:
        _, fn, is_source = _build("explore", {})
        assert is_source is False
        left = pl.DataFrame({"x": [1]}).lazy()
        right = pl.DataFrame({"y": [2]}).lazy()
        with pytest.raises(TypeError):
            fn(left, right)


# ---------------------------------------------------------------------------
# _build_optimiser (passthrough in preview mode)
# ---------------------------------------------------------------------------


class TestBuildOptimiser:
    """Optimiser builder should be a passthrough during preview."""

    def test_passthrough(self) -> None:
        _, fn, is_source = _build(
            "optimiser",
            {"mode": "online", "objective": "profit"},
            source_names=["upstream"],
        )
        assert is_source is False
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert result["x"].to_list() == [1]

    def test_data_input_selects_correct_input(self) -> None:
        """When data_input is set, the optimiser should pick that specific
        input rather than blindly using dfs[0]."""
        # Build node map: two upstream nodes — banding + data
        banding_node = _n(
            {
                "id": "banding_1",
                "data": {"label": "Banding", "nodeType": "banding", "config": {}},
            }
        )
        data_node = _n(
            {
                "id": "data_1",
                "data": {"label": "Scored Data", "nodeType": "polars", "config": {}},
            }
        )
        opt_node = _n(
            {
                "id": "opt_1",
                "data": {
                    "label": "Optimiser",
                    "nodeType": "optimiser",
                    "config": {
                        "mode": "online",
                        "objective": "profit",
                        "data_input": "data_1",
                    },
                },
            }
        )
        node_map = {
            "banding_1": banding_node,
            "data_1": data_node,
            "opt_1": opt_node,
        }
        # source_names order: banding first, data second (simulates edge order)
        # Names are sanitized labels: "Banding" → "Banding", "Scored Data" → "Scored_Data"
        _, fn, _ = _build_node_fn(
            opt_node,
            source_names=["Banding", "Scored_Data"],
            node_map=node_map,
        )
        banding_df = pl.DataFrame({"quote_id": ["q1"], "factor": [1.1]}).lazy()
        data_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q1"],
                "scenario_index": [0, 1, 2],
                "profit": [100.0, 110.0, 120.0],
            }
        ).lazy()
        result = fn(banding_df, data_df).collect()
        # Should pick data_df (index 1), not banding_df (index 0)
        assert result.shape[0] == 3
        assert "scenario_index" in result.columns

    def test_data_input_fallback_when_not_configured(self) -> None:
        """Without data_input, falls back to dfs[0]."""
        _, fn, _ = _build(
            "optimiser",
            {"mode": "online", "objective": "profit"},
            source_names=["upstream"],
        )
        df = pl.DataFrame({"x": [1, 2]}).lazy()
        result = fn(df).collect()
        assert result["x"].to_list() == [1, 2]

    def test_data_input_fallback_when_id_not_in_node_map(self) -> None:
        """If data_input references a missing node, fall back to dfs[0]."""
        opt_node = _n(
            {
                "id": "opt_1",
                "data": {
                    "label": "Optimiser",
                    "nodeType": "optimiser",
                    "config": {"data_input": "nonexistent_node"},
                },
            }
        )
        _, fn, _ = _build_node_fn(
            opt_node,
            source_names=["upstream"],
            node_map={"opt_1": opt_node},
        )
        df = pl.DataFrame({"x": [42]}).lazy()
        result = fn(df).collect()
        assert result["x"].to_list() == [42]

    def test_data_input_raises_on_index_mismatch(self) -> None:
        """If data_input resolves to an index beyond the actual inputs,
        raise rather than silently falling back."""
        data_node = _n(
            {
                "id": "data_1",
                "data": {"label": "Scored Data", "nodeType": "polars", "config": {}},
            }
        )
        opt_node = _n(
            {
                "id": "opt_1",
                "data": {
                    "label": "Optimiser",
                    "nodeType": "optimiser",
                    "config": {"data_input": "data_1"},
                },
            }
        )
        node_map = {"data_1": data_node, "opt_1": opt_node}
        # source_names has two entries but we only pass one df
        _, fn, _ = _build_node_fn(
            opt_node,
            source_names=["Banding", "Scored_Data"],
            node_map=node_map,
        )
        single_df = pl.DataFrame({"x": [1]}).lazy()
        with pytest.raises(ValueError, match="expected input at index 1"):
            fn(single_df)


# ---------------------------------------------------------------------------
# _build_live_switch
# ---------------------------------------------------------------------------


class TestBuildLiveSwitch:
    """Tests for the live switch node builder."""

    def test_selects_live_input(self) -> None:
        _, fn, _ = _build(
            "liveSwitch",
            {"input_scenario_map": {"live_src": "live", "batch_src": "test_batch"}},
            source_names=["live_src", "batch_src"],
        )
        live_df = pl.DataFrame({"source": ["live"]}).lazy()
        batch_df = pl.DataFrame({"source": ["batch"]}).lazy()
        result = fn(live_df, batch_df).collect()
        assert result["source"].to_list() == ["live"]

    def test_selects_non_live_scenario(self) -> None:
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Switch",
                    "nodeType": "liveSwitch",
                    "config": {
                        "input_scenario_map": {"live_src": "live", "batch_src": "test_batch"}
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            source_names=["live_src", "batch_src"],
            source="test_batch",
        )
        live_df = pl.DataFrame({"source": ["live"]}).lazy()
        batch_df = pl.DataFrame({"source": ["batch"]}).lazy()
        result = fn(live_df, batch_df).collect()
        assert result["source"].to_list() == ["batch"]

    def test_configured_mapping_rejects_unmapped_scenario(self) -> None:
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Switch",
                    "nodeType": "liveSwitch",
                    "config": {"input_scenario_map": {"a": "live", "b": "test"}},
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            source_names=["a", "b"],
            source="unknown_scenario",
        )
        df_a = pl.DataFrame({"val": [1]}).lazy()
        df_b = pl.DataFrame({"val": [2]}).lazy()
        with pytest.raises(LiveSwitchScenarioError) as exc_info:
            fn(df_a, df_b)

        assert exc_info.value.scenario == "unknown_scenario"
        assert exc_info.value.available_mappings == ("live", "test")
