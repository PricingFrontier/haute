"""Tests for executor builder functions — the _build_* dispatch table.

Covers the simpler node builders: constant, banding, scenario_expander,
api_input, output, and the data input's config path handling.  Uses the same
helpers from conftest.py as test_executor.py.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._builders import NodeBuildContext, _build_node_fn
from haute._execute_lazy import _apply_selected_columns
from haute._execution_context import ExecutionProfile
from haute._output_assembler import OutputMappingSchemaError
from haute._types import NodeType
from haute.errors import (
    BoundedMemoryUnsupportedError,
    ConfigError,
    LiveSwitchScenarioError,
    NodeConfigError,
)
from haute.graph_utils import GraphNode, NodeData
from tests.conftest import (
    make_file_input_config,
    make_ready_file_input_config,
)
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
            "input_type": "file",
            "format": "csv",
            "mode": "scan",
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

    def test_null_values_are_refused_when_built(self) -> None:
        # ``values`` takes a list; null is not a cleared setting Haute writes.
        with pytest.raises(NodeConfigError, match="setting 'values': Input should be a valid list"):
            _build("constant", {"values": None})

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
    """Tests for the output node builder."""

    @staticmethod
    def _cfg(fields: list[str], *, source_port: str = "in") -> dict:
        """A v2 flat output_mapping (one top-level array element per field)."""
        return {
            "output_mapping": [
                {
                    "source_port": source_port,
                    "source_column": f,
                    "output_path": f"$[:].{f}",
                    "enabled": True,
                }
                for f in fields
            ],
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

    def test_empty_config_raises(self) -> None:
        with pytest.raises(OutputMappingSchemaError, match="requires `output_mapping`"):
            _build("output", {}, source_names=["upstream"])

    def test_empty_mapping_renders_empty_document(self) -> None:
        _, fn, _ = _build("output", self._cfg([]), source_names=["upstream"])
        result = fn(pl.DataFrame({"a": [1], "b": [2]}).lazy()).collect()
        assert result.to_dicts() == []

    def test_disabled_entry_is_skipped(self) -> None:
        cfg = self._cfg(["x", "y"])
        cfg["output_mapping"][1]["enabled"] = False
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

    def test_breakpoints_single_factor(self) -> None:
        _, fn, is_source = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "output_column": "age_band",
                        "banding": "breakpoints",
                        "rules": [
                            {"boundary": "25", "label": "young"},
                            {"boundary": "65", "label": "adult"},
                            {"boundary": "", "label": "senior"},
                        ],
                        "right_closed": False,
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
                        "output_column": "vehicle_group",
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
                        "output_column": "age_band",
                        "banding": "breakpoints",
                        "rules": [
                            {"boundary": "50", "label": "under50"},
                            {"boundary": "", "label": "50plus"},
                        ],
                        "right_closed": False,
                    },
                    {
                        "column": "region",
                        "output_column": "region_group",
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
                        "output_column": "out",
                        "banding": "breakpoints",
                        "rules": [{"boundary": "10", "label": "low"}],
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
                        "output_column": "age_band",
                        "banding": "breakpoints",
                        "rules": [],
                    }
                ],
            },
            source_names=["data"],
        )
        input_df = pl.DataFrame({"age": [25]}).lazy()
        result = fn(input_df).collect()
        assert "age_band" not in result.columns

    def test_breakpoints_with_default(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "output_column": "age_band",
                        "banding": "breakpoints",
                        "default": "unknown",
                        "rules": [{"boundary": "25", "label": "young"}],
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


class TestInputLessNodes:
    """A node built without an input starts from a frame with no rows and returns none."""

    @pytest.mark.parametrize(
        ("node_type", "config"),
        [
            ("banding", {"factors": []}),
            (
                "scenarioExpander",
                {"column_name": "sv", "min_value": 0.9, "max_value": 1.1, "step_count": 3},
            ),
        ],
    )
    def test_an_unconnected_node_returns_no_rows(self, node_type: str, config: dict) -> None:
        _, fn, is_source = _build(node_type, config)

        assert is_source is False
        assert fn().collect().height == 0


class TestBuildScenarioExpander:
    """Tests for the scenario expander node builder.

    Note: basic coverage already exists in test_scenario_expander.py.
    These tests focus on edge cases and builder-specific behavior.
    """

    def test_is_not_a_source_node(self) -> None:
        _, _, is_source = _build(
            "scenarioExpander",
            {"column_name": "sv", "min_value": 0.9, "max_value": 1.1, "step_count": 3},
            source_names=["upstream"],
        )
        assert is_source is False

    def test_empty_input_cross_join(self) -> None:
        """Expanding an empty input should yield 0 rows but correct columns."""
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "sv", "min_value": 0.9, "max_value": 1.1, "step_count": 3},
            source_names=["upstream"],
        )
        input_df = pl.DataFrame({"id": pl.Series([], dtype=pl.Int32)}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 0
        assert "sv" in result.columns

    def test_single_step(self) -> None:
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "val", "min_value": 1.0, "max_value": 1.0, "step_count": 1},
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
                "step_count": 3,
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
            {"column_name": "", "step_count": 3, "step_column": "idx"},
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
                "step_count": 3,
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
                "step_count": 3,
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
                "step_count": 3,
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
            execution_profile=ExecutionProfile.OPTIMISER_SOLVE.value,
        )

        with pytest.raises(BoundedMemoryUnsupportedError, match="CSV sources require"):
            fn()

    def test_bounded_csv_source_uses_declared_dtypes(
        self,
        tmp_path: Path,
        _widen_sandbox_root,
    ) -> None:
        """A flat-file source read through the public reader declares its CSV's dtypes."""
        from haute._io import read_data_source

        data_file = tmp_path / "input.csv"
        data_file.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        result = read_data_source(
            {
                "source_type": "flat_file",
                "path": str(data_file),
                "schema_overrides": {"quote_id": "String", "premium": "Float64"},
            },
            profile=ExecutionProfile.OPTIMISER_SOLVE.value,
        ).collect()

        assert result.schema["quote_id"] == pl.String
        assert result["quote_id"].to_list() == ["001"]

    def test_func_name_sanitized(self) -> None:
        func_name, _, _ = _build(
            "apiInput",
            {"path": "some.parquet"},
            label="My Input (v2)",
        )
        assert func_name.isidentifier()

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_source_projection_maps_renamed_output_columns_to_physical_columns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_file = tmp_path / "input.csv"
        data_file.write_text("quote_id,premium_raw,unused\n001,10.5,x\n", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_resolve_api_input_from_config(
            config,
            *,
            base_dir=None,
            profile=None,
            columns=None,
            port_columns=None,
            read_snapshots=False,
            schema_tier_node=None,
        ):
            captured["profile"] = profile
            captured["columns"] = columns
            captured["port_columns"] = port_columns
            return pl.DataFrame({"quote_id": ["001"], "premium_raw": [10.5]}).lazy()

        monkeypatch.setattr(
            "haute._builders.resolve_api_input_from_config",
            fake_resolve_api_input_from_config,
        )
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
            execution_profile=ExecutionProfile.OPTIMISER_SOLVE.value,
        )

        fn()

        assert captured["columns"] == frozenset({"quote_id", "premium_raw"})
        assert captured["port_columns"] is None

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_source_projection_avoids_ambiguous_rename_pushdown(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_file = tmp_path / "input.csv"
        data_file.write_text("a,b\n1,2\n", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_resolve_api_input_from_config(
            config,
            *,
            base_dir=None,
            profile=None,
            columns=None,
            port_columns=None,
            read_snapshots=False,
            schema_tier_node=None,
        ):
            captured["columns"] = columns
            captured["port_columns"] = port_columns
            return pl.DataFrame({"a": [1], "b": [2]}).lazy()

        monkeypatch.setattr(
            "haute._builders.resolve_api_input_from_config",
            fake_resolve_api_input_from_config,
        )
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
            execution_profile=ExecutionProfile.OPTIMISER_SOLVE.value,
        )

        fn()

        assert captured["columns"] is None
        assert captured["port_columns"] is None

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_stale_selected_column_is_absent_not_fatal_in_every_profile(
        self,
        tmp_path: Path,
    ) -> None:
        from haute._execute_lazy import _apply_selected_columns

        data_file = tmp_path / "input.parquet"
        pl.DataFrame({"quote_id": [1], "premium": [10.5]}).write_parquet(data_file)
        config = {
            "path": str(data_file),
            "selected_columns": ["quote_id", "stale_column"],
        }
        node = _n(
            {
                "id": "api",
                "data": {"label": "api", "nodeType": "apiInput", "config": config},
            }
        )

        columns_by_profile = {}
        for profile in (
            ExecutionProfile.PREVIEW_EAGER.value,
            ExecutionProfile.OPTIMISER_SOLVE.value,
        ):
            _, fn, _ = _build_node_fn(
                node,
                required_output_columns=frozenset({"quote_id"}),
                execution_profile=profile,
            )
            frame = _apply_selected_columns(fn(), config, node_label="node")
            columns_by_profile[profile] = frame.collect_schema().names()

        assert columns_by_profile[ExecutionProfile.OPTIMISER_SOLVE.value] == ["quote_id"]
        assert columns_by_profile[ExecutionProfile.PREVIEW_EAGER.value] == ["quote_id"]

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
            execution_profile=ExecutionProfile.OPTIMISER_SOLVE.value,
        )

        with pytest.raises(ValueError, match="excluded by selected_columns"):
            fn()


# ---------------------------------------------------------------------------
# dataInput selected_columns
# ---------------------------------------------------------------------------


class TestBuildDataInputWithCode:
    """A Data Input with post-load code keeps its scan and its code apart (CACHE-S22)."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_its_function_opens_the_scan_and_runs_the_code_on_it(self, tmp_path: Path) -> None:
        from haute._builders import SourceWithCode

        data_file = tmp_path / "quotes.parquet"
        pl.DataFrame({"premium": [100.0, 300.0]}).write_parquet(data_file)
        _, fn, is_source = _build(
            "dataInput",
            {
                "input_type": "file",
                "format": "parquet",
                "mode": "scan",
                "path": str(data_file),
                "code": "df = df.filter(pl.col('premium') > 200)",
            },
        )

        assert is_source and isinstance(fn, SourceWithCode)
        scan, frame = fn.opened()
        assert scan is not None and scan.collect()["premium"].to_list() == [100.0, 300.0]
        assert frame.collect()["premium"].to_list() == [300.0]
        assert fn().collect()["premium"].to_list() == [300.0]

    def test_one_without_a_provider_is_refused_when_built_before_its_code_runs(self) -> None:
        # The palette gives every Data Input its ``input_type``; a config without one
        # is refused when the node is built, so its code never runs.
        with pytest.raises(NodeConfigError, match="setting 'input_type'"):
            _build("dataInput", {"code": "raise RuntimeError('ran')"})


class TestBuildDataInputSelectedColumns:
    """``selected_columns`` is interpreted once, after the node's code runs."""

    @staticmethod
    def _node(data_file: Path) -> GraphNode:
        return _n(
            {
                "id": "din",
                "data": {
                    "label": "din",
                    "nodeType": "dataInput",
                    "config": {
                        "input_type": "file",
                        "format": "parquet",
                        "mode": "scan",
                        "path": str(data_file),
                        "arguments": {},
                        "code": "df = df.with_columns(SaleFlag = pl.lit(1))",
                        "selected_columns": ["quote_id", "SaleFlag"],
                    },
                },
            }
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_post_load_code_may_produce_a_selected_column(self, tmp_path: Path) -> None:
        data_file = tmp_path / "quotes.parquet"
        pl.DataFrame({"quote_id": ["001"], "sale_date": ["2024-01-01"]}).write_parquet(data_file)

        _, fn, _ = _build_node_fn(
            self._node(data_file),
            required_output_columns=frozenset({"quote_id", "SaleFlag"}),
            execution_profile=ExecutionProfile.TRAINING_PREP.value,
        )

        assert "SaleFlag" in fn().collect_schema().names()

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_code_produced_selection_matches_across_profiles(self, tmp_path: Path) -> None:
        """The executor's shared post-call filter yields one output in every profile.

        A bounded profile narrows the physical scan to planner demand carried
        back through the post-load code, so ``sale_date`` is never read, while
        preview scans full width.  The selection, applied once after the code
        runs, is identical either way.
        """
        data_file = tmp_path / "quotes.parquet"
        pl.DataFrame({"quote_id": ["001"], "sale_date": ["2024-01-01"]}).write_parquet(data_file)

        builder_columns_by_profile: dict[str, set[str]] = {}
        selected_by_profile: dict[str, list[str]] = {}
        for profile in (
            ExecutionProfile.TRAINING_PREP.value,
            ExecutionProfile.PREVIEW_EAGER.value,
        ):
            node = self._node(data_file)
            _, fn, _ = _build_node_fn(
                node,
                required_output_columns=frozenset({"quote_id", "SaleFlag"}),
                execution_profile=profile,
            )
            frame = fn()
            builder_columns_by_profile[profile] = set(frame.collect_schema().names())
            selected_by_profile[profile] = (
                _apply_selected_columns(frame, node.data.config, node_label="node")
                .collect_schema()
                .names()
            )

        assert builder_columns_by_profile[ExecutionProfile.TRAINING_PREP.value] == {
            "quote_id",
            "SaleFlag",
        }
        assert builder_columns_by_profile[ExecutionProfile.PREVIEW_EAGER.value] == {
            "quote_id",
            "sale_date",
            "SaleFlag",
        }
        assert (
            selected_by_profile[ExecutionProfile.TRAINING_PREP.value]
            == selected_by_profile[ExecutionProfile.PREVIEW_EAGER.value]
            == ["quote_id", "SaleFlag"]
        )


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
        with pytest.raises(NotImplementedError):
            fn(input_df).collect()


# ---------------------------------------------------------------------------
# _build_data_sink (passthrough in preview mode)
# ---------------------------------------------------------------------------


class TestBuildDataSink:
    """Sink builder should be a passthrough during preview."""

    def test_passthrough(self) -> None:
        _, fn, is_source = _build(
            "dataOutput",
            {"output_type": "file", "path": "out.parquet", "format": "parquet"},
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
                        "data_input": "Scored_Data",
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

    def test_stale_data_input_name_fails_loudly_instead_of_falling_back(self) -> None:
        """A data_input naming no connected input must raise, never pick dfs[0]."""
        opt_node = _n(
            {
                "id": "opt_1",
                "data": {
                    "label": "Optimiser",
                    "nodeType": "optimiser",
                    "config": {"data_input": "nonexistent_input"},
                },
            }
        )
        with pytest.raises(ConfigError, match="not an exact connected input name"):
            _build_node_fn(
                opt_node,
                source_names=["upstream"],
                node_map={"opt_1": opt_node},
            )

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
                    "config": {"data_input": "Scored_Data"},
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

    def test_data_input_named_frames_require_every_declared_input(self) -> None:
        """Named execution must not silently omit a connected input frame."""
        _, fn, _ = _build(
            "optimiser",
            {"data_input": "scored"},
            source_names=["banding", "scored"],
        )
        scored = pl.DataFrame({"quote_id": ["q1"]}).lazy()

        with pytest.raises(ValueError, match="every declared connected input name"):
            fn(scored=scored)


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


# ---------------------------------------------------------------------------
# Data Input config — empty path produces empty frame
# ---------------------------------------------------------------------------


class TestDataInputConfigPath:
    """Verify that _build_node_fn for data sources uses config.path correctly."""

    def test_a_source_without_a_provider_is_refused_when_built(self):
        """A Data Input names its provider (the palette gives ``input_type``): a config
        without one is refused when the node is built, naming the setting."""
        from haute.errors import NodeConfigError
        from haute.executor import _build_node_fn

        node = GraphNode(
            id="src",
            data=NodeData(
                label="src",
                nodeType=NodeType.DATA_INPUT,
                config={"path": ""},
            ),
        )
        with pytest.raises(
            NodeConfigError, match="Node 'src' \\(dataInput\\) setting 'input_type'"
        ):
            _build_node_fn(node, source_names=[])

    def test_valid_config_path_loads_data(self, tmp_path):
        """A data source with a valid parquet path should load data."""
        from haute.executor import _build_node_fn

        path = tmp_path / "test.parquet"
        pl.DataFrame({"quote_id": ["q1", "q2"], "value": [10, 20]}).write_parquet(path)

        node = GraphNode(
            id="src",
            data=NodeData(
                label="src",
                nodeType=NodeType.DATA_INPUT,
                config=make_ready_file_input_config(path),
            ),
        )
        _, fn, is_source = _build_node_fn(node, source_names=[])
        assert is_source is True

        result = fn()
        df = result.collect()
        assert "quote_id" in df.columns
        assert df.shape == (2, 2)

    def test_empty_config_breaks_downstream_join(self):
        """Demonstrates the exact failure mode: empty data source joined
        on quote_id fails with 'unable to find column'."""
        empty_lf = pl.LazyFrame()  # zero columns
        main_lf = pl.DataFrame({"quote_id": ["q1"], "val": [1]}).lazy()

        with pytest.raises(Exception, match="quote_id"):
            main_lf.join(empty_lf, on="quote_id", how="left").collect()

    def test_selected_columns_preserved_in_config(self, tmp_path):
        """Config's selected_columns should be preserved on the node — the
        builder layer uses this to filter columns before downstream joins,
        preventing column collisions (e.g. inception_premium vs premium)."""
        node = GraphNode(
            id="src",
            data=NodeData(
                label="src",
                nodeType=NodeType.DATA_INPUT,
                config=make_file_input_config(
                    tmp_path / "dummy.parquet",
                    selected_columns=["policy_id", "quote_id"],
                ),
            ),
        )
        config = node.data.config
        assert config["selected_columns"] == ["policy_id", "quote_id"]

    def test_manual_column_selection_prevents_collision(self, tmp_path):
        """Demonstrates why selected_columns matters: without filtering,
        extra columns from a data source can collide with pipeline columns."""
        # Simulate policy_data with extra columns
        policy_all = pl.DataFrame(
            {
                "policy_id": ["p1"],
                "quote_id": ["q1"],
                "inception_premium": [500.0],
            }
        ).lazy()

        # Simulate pipeline with its own premium column
        pipeline = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "premium": [400.0],
            }
        ).lazy()

        # Join without column filtering — both inception_premium and premium present
        joined_all = pipeline.join(policy_all, on="quote_id", how="left").collect()
        assert "inception_premium" in joined_all.columns  # unwanted column

        # Join with column filtering — only policy_id and quote_id
        policy_filtered = policy_all.select(["policy_id", "quote_id"])
        joined_filtered = pipeline.join(policy_filtered, on="quote_id", how="left").collect()
        assert "inception_premium" not in joined_filtered.columns
        assert "policy_id" in joined_filtered.columns
