"""Tests for validate_node_config mlflow_destination validation."""

from __future__ import annotations

import pytest

from haute._config_validation import validate_node_config
from haute._types import NodeType
from haute.errors import ConfigError


@pytest.mark.parametrize(
    "node_type",
    [
        NodeType.MODELLING,
        NodeType.OPTIMISER,
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
    ],
)
def test_mlflow_destination_unknown_value_is_rejected(node_type: NodeType) -> None:
    config = {"mlflow_destination": "invalid_dest"}
    with pytest.raises(ConfigError, match="databricks, server, or local"):
        validate_node_config(node_type, config)


@pytest.mark.parametrize(
    "node_type",
    [
        NodeType.MODELLING,
        NodeType.OPTIMISER,
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
    ],
)
@pytest.mark.parametrize("destination", ["", "local", "server", "databricks", None])
def test_mlflow_destination_valid_values_pass(node_type: NodeType, destination: str | None) -> None:
    config = {} if destination is None else {"mlflow_destination": destination}
    validated = validate_node_config(node_type, config)
    assert validated.get("mlflow_destination") == config.get("mlflow_destination")


@pytest.mark.parametrize(
    "node_type",
    [
        NodeType.MODELLING,
        NodeType.OPTIMISER,
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
    ],
)
def test_mlflow_destination_non_string_rejected(node_type: NodeType) -> None:
    config = {"mlflow_destination": 123}
    with pytest.raises(ConfigError, match="databricks, server, or local"):
        validate_node_config(node_type, config)
