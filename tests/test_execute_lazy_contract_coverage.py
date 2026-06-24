"""Lazy-path contract enforcement raises in ``haute._execute_lazy``.

These cover the two boundary-check raises on the lazy execution path that
ordinary lazy tests never trip: the input-side check (an upstream frame
missing a column the node's contract says it reads) and the output-side
check (a node result missing a column its contract promised to produce).
"""

from __future__ import annotations

import polars as pl
import pytest

from haute._execute_lazy import _execute_lazy
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.errors import ContractMismatchError


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _source_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.DATA_SOURCE),
    )


def _transform_node(nid: str, label: str | None = None, **extra_config) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.POLARS, config=extra_config),
    )


class TestLazyInputContractViolation:
    """Input-side boundary check raises when an upstream column is absent."""

    def test_missing_input_column_raises_contract_mismatch(self):
        """A POLARS node declaring it reads ``missing_col`` must raise when
        the only upstream frame does not carry that column."""

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("src"),
                _transform_node("t", contract={"inputs": ["missing_col"], "outputs": None}),
            ],
            edges=[_e("src", "t")],
        )

        with pytest.raises(ContractMismatchError) as exc_info:
            _execute_lazy(g, build_fn, enforce_contracts=True)

        assert exc_info.value.context["node_id"] == "t"
        assert "missing_col" in exc_info.value.context["missing"]

    def test_present_input_column_does_not_raise(self):
        """Control: when the declared input column is present upstream, no raise."""

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("src"),
                _transform_node("t", contract={"inputs": ["x"], "outputs": None}),
            ],
            edges=[_e("src", "t")],
        )

        outputs, *_ = _execute_lazy(g, build_fn, enforce_contracts=True)
        assert outputs["t"].collect()["x"].to_list() == [1, 2, 3]


class TestLazyOutputContractViolation:
    """Output-side boundary check raises when a promised column is absent."""

    def test_missing_output_column_raises_contract_mismatch(self):
        """A POLARS node promising to produce ``promised`` must raise when its
        function never emits that column."""

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
            # Returns only the input column 'x' — never produces 'promised'.
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("src"),
                _transform_node("t", contract={"inputs": None, "outputs": ["promised"]}),
            ],
            edges=[_e("src", "t")],
        )

        with pytest.raises(ContractMismatchError) as exc_info:
            _execute_lazy(g, build_fn, enforce_contracts=True)

        assert exc_info.value.context["node_id"] == "t"
        assert "promised" in exc_info.value.context["missing"]

    def test_produced_output_column_does_not_raise(self):
        """Control: when the node emits the promised column, no raise."""

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
            return node.id, lambda *dfs: dfs[0].with_columns(promised=pl.col("x") * 2), False

        g = PipelineGraph(
            nodes=[
                _source_node("src"),
                _transform_node("t", contract={"inputs": None, "outputs": ["promised"]}),
            ],
            edges=[_e("src", "t")],
        )

        outputs, *_ = _execute_lazy(g, build_fn, enforce_contracts=True)
        assert outputs["t"].collect()["promised"].to_list() == [2, 4, 6]

    def test_output_check_skipped_without_enforce_contracts(self):
        """When ``enforce_contracts`` is False the violating output is allowed."""

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("src"),
                _transform_node("t", contract={"inputs": None, "outputs": ["promised"]}),
            ],
            edges=[_e("src", "t")],
        )

        outputs, *_ = _execute_lazy(g, build_fn, enforce_contracts=False)
        assert outputs["t"].collect().columns == ["x"]
