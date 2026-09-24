"""Tests for the Scenario Expander node type."""

import numpy as np
import polars as pl
import pytest

from haute._config_builder import _build_node_config
from haute._types import GraphNode, NodeData, NodeType
from haute.codegen import _node_to_code
from haute.executor import _build_node_fn


def _make_node(config: dict, label: str = "test_expander") -> GraphNode:
    """Helper to create a GraphNode for the scenario expander."""
    return GraphNode(
        id="expander_1",
        data=NodeData(
            label=label,
            nodeType=NodeType.SCENARIO_EXPANDER,
            config=config,
        ),
    )


class TestBuildConfig:
    def test_build_config(self):
        decorator_kwargs = {
            "scenario_expander": True,
            "quote_id": "policy_id",
            "column_name": "scenario_value",
            "min_value": 0.8,
            "max_value": 1.2,
            "stepCount": 11,
            "step_column": "step",
        }
        config = _build_node_config(
            node_type=NodeType.SCENARIO_EXPANDER,
            decorator_kwargs=decorator_kwargs,
            param_names=["df"],
            body="",
        )
        assert config["quote_id"] == "policy_id"
        assert config["column_name"] == "scenario_value"
        assert config["min_value"] == 0.8
        assert config["max_value"] == 1.2
        assert config["stepCount"] == 11
        assert config["step_column"] == "step"


class TestCodegen:
    def test_codegen(self):
        config = {
            "quote_id": "quote_id",
            "column_name": "scenario_value",
            "min_value": 0.8,
            "max_value": 1.2,
            "stepCount": 21,
            "step_column": "scenario_index",
        }
        node = _make_node(config, label="expand_scenarios")
        code = _node_to_code(node, source_names=["base_data"])
        assert 'config="config/expander/expand_scenarios.json"' in code
        assert "def expand_scenarios(base_data" in code
        # Body applies the sidecar config via the shared helper (not a no-op
        # passthrough) so a standalone pipeline.run() expands the grid.
        assert "expand_scenarios_from_config(base_data" in code


class TestExecutor:
    def test_cross_join(self):
        config = {
            "column_name": "scenario_value",
            "min_value": 0.5,
            "max_value": 1.5,
            "stepCount": 5,
            "step_column": "scenario_index",
        }
        node = _make_node(config)
        _, fn, _ = _build_node_fn(node, source_names=["upstream"])
        input_df = pl.DataFrame({"id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 50  # 10 rows x 5 steps
        assert "scenario_value" in result.columns
        assert "scenario_index" in result.columns
        assert result["scenario_index"].dtype == pl.Int32
        assert result["scenario_value"].dtype == pl.Float32

    def test_values(self):
        config = {
            "column_name": "price",
            "min_value": 1.0,
            "max_value": 2.0,
            "stepCount": 3,
            "step_column": "idx",
        }
        node = _make_node(config)
        _, fn, _ = _build_node_fn(node, source_names=["upstream"])
        input_df = pl.DataFrame({"x": [1]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 3
        prices = result["price"].to_list()
        assert prices[0] == pytest.approx(1.0)
        assert prices[1] == pytest.approx(1.5)
        assert prices[2] == pytest.approx(2.0)
        assert result["idx"].to_list() == [0, 1, 2]

    def test_grid_values_preserve_float32_numpy_values(self):
        config = {
            "column_name": "price",
            "min_value": 0.1,
            "max_value": 0.3,
            "stepCount": 3,
        }
        _, fn, _ = _build_node_fn(_make_node(config), source_names=["upstream"])

        result = fn(pl.DataFrame({"x": [1]}).lazy()).collect()["price"]

        expected = np.linspace(0.1, 0.3, 3, dtype=np.float32)
        assert result.dtype == pl.Float32
        assert result.to_numpy().dtype == np.float32
        np.testing.assert_array_equal(result.to_numpy(), expected)

    def test_missing_step_count_is_rejected(self):
        """The grid size is required: no absent-key default, at build time or at call time."""
        with pytest.raises(ValueError, match="requires stepCount"):
            _build_node_fn(_make_node({}), source_names=["upstream"])
        from haute._node_apply import expand_scenarios_from_config

        with pytest.raises(ValueError, match="requires stepCount"):
            expand_scenarios_from_config(pl.DataFrame({"a": [1]}).lazy(), {})
        with pytest.raises(ValueError, match="whole number"):
            expand_scenarios_from_config(pl.DataFrame({"a": [1]}).lazy(), {"stepCount": 2.5})

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({}, "Scenario expander requires stepCount (the number of grid values)."),
            ({"stepCount": 2.5}, "Scenario expander stepCount must be a whole number, got 2.5"),
            ({"stepCount": 0}, "Scenario expander requires stepCount >= 1, got 0"),
        ],
    )
    def test_invalid_step_count_is_a_public_node_config_error(self, config, message):
        """A missing or malformed grid size is a user-fixable config defect: it carries the
        public contract (code, message, setting) and still reads as a ValueError to the
        chunk planner and the RAM estimator."""
        from haute._node_apply import scenario_step_count
        from haute.errors import NodeConfigError, is_public_contract_error

        with pytest.raises(NodeConfigError) as exc_info:
            scenario_step_count(config)

        assert isinstance(exc_info.value, ValueError)
        assert is_public_contract_error(exc_info.value)
        assert exc_info.value.to_payload() == {
            "error_code": "node_config_invalid",
            "message": message,
            "setting": "stepCount",
        }

    def test_explicit_default_step_count_expands_the_grid(self):
        """A new node's explicit default of 21 (no value column without column_name)."""
        node = _make_node({"stepCount": 21})
        _, fn, _ = _build_node_fn(node, source_names=["upstream"])
        input_df = pl.DataFrame({"a": [1]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 21
        assert "scenario_index" in result.columns


class TestInteractiveExpansion:
    """A preview row limit reaches the expander's input instead of expanding it all."""

    CONFIG = {
        "column_name": "price_adjustment",
        "min_value": 0.5,
        "max_value": 1.5,
        "stepCount": 3,
        "step_column": "price_strategy",
    }

    @staticmethod
    def _expander(row_limit: int | None):
        _, fn, _ = _build_node_fn(
            _make_node(TestInteractiveExpansion.CONFIG),
            source_names=["upstream"],
            row_limit=row_limit,
        )
        return fn

    @staticmethod
    def _input() -> pl.LazyFrame:
        return pl.LazyFrame({"quote_id": [f"q{i}" for i in range(6)], "premium": range(6)})

    @pytest.mark.parametrize("limit", [1, 2, 3, 4, 10, 18, 50])
    def test_a_limited_preview_returns_the_rows_a_full_expansion_returns(self, limit: int):
        full = self._expander(None)(self._input()).head(limit).collect()
        interactive = self._expander(100)(self._input()).head(limit).collect()

        assert interactive.equals(full)
        assert interactive.schema == full.schema

    def test_an_unlimited_read_of_the_interactive_form_expands_everything(self):
        full = self._expander(None)(self._input()).collect()
        interactive = self._expander(100)(self._input()).collect()

        assert interactive.equals(full)

    def test_the_limit_reaches_the_node_feeding_the_expander(self):
        """Polars pushes no slice below ``explode`` — the scan form carries it."""
        from haute._polars_utils import row_local_python_scan

        read: list[int] = []

        def transform(batch: pl.DataFrame) -> pl.DataFrame:
            read.append(batch.height)
            return batch.with_columns((pl.col("premium") * 2).alias("doubled"))

        upstream = pl.LazyFrame(
            {"quote_id": [f"q{i}" for i in range(1_000)], "premium": range(1_000)}
        )
        source = row_local_python_scan(
            upstream,
            transform,
            schema=pl.Schema({**upstream.collect_schema(), "doubled": pl.Int64()}),
            generated_columns=("doubled",),
            required_input_columns=("premium",),
            input_predicates_allowed=True,
            elide_transform_when_unused=False,
        )

        result = self._expander(100)(source).head(7).collect()

        assert result.height == 7
        assert result["quote_id"].to_list() == ["q0"] * 3 + ["q1"] * 3 + ["q2"]
        # ceil(7 / stepCount) upstream rows, not all 1_000.
        assert sum(read) == 3

    def test_a_full_run_keeps_expanding_through_the_expression(self):
        """No row limit is a batch run: the expression form, unchanged."""
        plan = self._expander(None)(self._input()).explain(optimized=False)

        assert "EXPLODE" in plan
        assert "PYTHON SCAN" not in plan

    def test_post_expansion_code_runs_on_the_interactive_expansion(self):
        node = _make_node({**self.CONFIG, "code": "df = df.with_columns(flag=pl.lit(1))"})
        _, fn, _ = _build_node_fn(node, source_names=["upstream"], row_limit=100)

        result = fn(self._input()).head(4).collect()

        assert result["flag"].to_list() == [1, 1, 1, 1]
        assert result["price_strategy"].to_list() == [0, 1, 2, 0]
