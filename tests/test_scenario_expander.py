"""Tests for the Scenario Expander node type."""

import numpy as np
import polars as pl
import pytest

from haute._parser_helpers import _build_node_config
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
            "steps": 11,
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
        assert config["steps"] == 11
        assert config["step_column"] == "step"


class TestCodegen:
    def test_codegen(self):
        config = {
            "quote_id": "quote_id",
            "column_name": "scenario_value",
            "min_value": 0.8,
            "max_value": 1.2,
            "steps": 21,
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
            "steps": 5,
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
            "steps": 3,
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
            "steps": 3,
        }
        _, fn, _ = _build_node_fn(_make_node(config), source_names=["upstream"])

        result = fn(pl.DataFrame({"x": [1]}).lazy()).collect()["price"]

        expected = np.linspace(0.1, 0.3, 3, dtype=np.float32)
        assert result.dtype == pl.Float32
        assert result.to_numpy().dtype == np.float32
        np.testing.assert_array_equal(result.to_numpy(), expected)

    def test_defaults(self):
        """Empty config uses sensible defaults (no value column without column_name)."""
        node = _make_node({})
        _, fn, _ = _build_node_fn(node, source_names=["upstream"])
        input_df = pl.DataFrame({"a": [1]}).lazy()
        result = fn(input_df).collect()
        assert result.shape[0] == 21
        assert "scenario_index" in result.columns
