"""Focused tests for boundary-contract classification in ``haute._execute_lazy``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from haute._contracts import Contract
from haute._execute_lazy import _effective_contract
from haute._types import GraphNode, NodeData, NodeType
from haute.errors import ConfigError, ContractMismatchError


def _node(node_type: NodeType, config: dict[str, object] | None = None) -> GraphNode:
    return GraphNode(
        id="node_1",
        data=NodeData(label="Node 1", nodeType=node_type, config=config or {}),
    )


def test_effective_contract_downgrades_config_error_to_opaque() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=ConfigError("missing model"),
    ):
        contract = _effective_contract(_node(NodeType.MODEL_SCORE))

    assert contract == Contract.opaque()


def test_effective_contract_downgrades_oserror_to_opaque() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=OSError("disk offline"),
    ):
        contract = _effective_contract(_node(NodeType.MODEL_SCORE))

    assert contract == Contract.opaque()


def test_effective_contract_reraises_attribute_error() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=AttributeError("bug"),
    ):
        with pytest.raises(AttributeError, match="bug"):
            _effective_contract(_node(NodeType.MODEL_SCORE))


def test_effective_contract_raises_contract_mismatch_for_malformed_declared_contract() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        return_value=({"premium"}, {"base_rate"}),
    ):
        with pytest.raises(ContractMismatchError, match="malformed"):
            _effective_contract(
                _node(
                    NodeType.POLARS,
                    {"contract": {"inputs": ["base_rate"]}},
                )
            )


def test_effective_contract_merges_declared_inputs_with_builder_outputs() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        return_value=({"premium"}, {"base_rate"}),
    ):
        contract = _effective_contract(
            _node(
                NodeType.POLARS,
                {"contract": {"inputs": ["declared_rate"], "outputs": None}},
            )
        )

    assert contract == Contract(
        inputs=frozenset({"declared_rate"}),
        outputs=frozenset({"premium"}),
    )


def test_effective_contract_declared_opaque_preserves_builder_contract() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        return_value=({"premium"}, {"base_rate"}),
    ):
        contract = _effective_contract(
            _node(NodeType.POLARS, {"contract": "opaque"})
        )

    assert contract == Contract(
        inputs=frozenset({"base_rate"}),
        outputs=frozenset({"premium"}),
    )
