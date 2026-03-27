"""Tests for haute._submodel_graph shared helpers."""

from __future__ import annotations

import pytest

from haute._submodel_graph import (
    build_submodel_placeholder,
    classify_ports,
    rewire_edges,
)
from haute.graph_utils import GraphEdge, NodeType


# ---------------------------------------------------------------------------
# build_submodel_placeholder
# ---------------------------------------------------------------------------


class TestBuildSubmodelPlaceholder:
    """Tests for building submodel placeholder nodes."""

    def test_basic_placeholder(self):
        """Placeholder has correct ID, type, and config."""
        node = build_submodel_placeholder(
            sm_name="scoring",
            sm_file="modules/scoring.py",
            child_node_ids=["a", "b"],
            input_ports=["a"],
            output_ports=["b"],
        )
        assert node.id == "submodel__scoring"
        assert node.type == NodeType.SUBMODEL
        assert node.data.nodeType == NodeType.SUBMODEL
        assert node.data.label == "scoring"
        assert node.data.description == ""
        assert node.data.config["file"] == "modules/scoring.py"
        assert node.data.config["childNodeIds"] == ["a", "b"]
        assert node.data.config["inputPorts"] == ["a"]
        assert node.data.config["outputPorts"] == ["b"]

    def test_with_description(self):
        """Description is passed through."""
        node = build_submodel_placeholder(
            "sub",
            "modules/sub.py",
            ["x"],
            ["x"],
            [],
            description="My submodel",
        )
        assert node.data.description == "My submodel"

    def test_empty_ports(self):
        """Works with empty input/output port lists."""
        node = build_submodel_placeholder(
            "isolated",
            "modules/isolated.py",
            ["a", "b"],
            [],
            [],
        )
        assert node.data.config["inputPorts"] == []
        assert node.data.config["outputPorts"] == []

    def test_position_defaults_to_origin(self):
        """Placeholder node position is (0, 0)."""
        node = build_submodel_placeholder("n", "f.py", ["a"], [], [])
        assert node.position == {"x": 0, "y": 0}


# ---------------------------------------------------------------------------
# classify_ports
# ---------------------------------------------------------------------------


class TestClassifyPorts:
    """Tests for determining input/output ports from cross-boundary edges."""

    def test_basic_classification(self):
        """Inbound edges → input ports, outbound → output ports."""
        child_ids = {"a", "b"}
        cross_edges = [
            ("external", "a"),  # external → child a: input
            ("b", "external2"),  # child b → external: output
        ]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == ["a"]
        assert outputs == ["b"]

    def test_deduplication(self):
        """Duplicate port references are deduplicated, preserving order."""
        child_ids = {"a"}
        cross_edges = [
            ("x", "a"),
            ("y", "a"),
        ]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == ["a"]
        assert outputs == []

    def test_no_cross_edges(self):
        """Empty cross-edges → empty ports."""
        inputs, outputs = classify_ports([], {"a", "b"})
        assert inputs == []
        assert outputs == []

    def test_bidirectional_node(self):
        """A child node can be both input and output port."""
        child_ids = {"a"}
        cross_edges = [
            ("ext1", "a"),
            ("a", "ext2"),
        ]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == ["a"]
        assert outputs == ["a"]

    def test_internal_edges_ignored(self):
        """Edges fully inside the submodel produce no ports."""
        child_ids = {"a", "b"}
        cross_edges = [("a", "b")]  # both inside
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == []
        assert outputs == []


# ---------------------------------------------------------------------------
# rewire_edges
# ---------------------------------------------------------------------------


class TestRewireEdges:
    """Tests for edge rewiring to/from submodel placeholder."""

    def _edge(self, src: str, tgt: str) -> GraphEdge:
        return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)

    def test_internal_edges_dropped(self):
        """Edges fully inside the submodel are excluded."""
        edges = [self._edge("a", "b")]
        result = rewire_edges(edges, "submodel__grp", {"a", "b"})
        assert result == []

    def test_external_edges_preserved(self):
        """Edges fully outside the submodel pass through unchanged."""
        edges = [self._edge("x", "y")]
        result = rewire_edges(edges, "submodel__grp", {"a", "b"})
        assert len(result) == 1
        assert result[0].source == "x"
        assert result[0].target == "y"

    def test_inbound_edge_rewired(self):
        """External → internal edge becomes external → submodel with targetHandle."""
        edges = [self._edge("ext", "a")]
        result = rewire_edges(edges, "submodel__grp", {"a", "b"})
        assert len(result) == 1
        e = result[0]
        assert e.source == "ext"
        assert e.target == "submodel__grp"
        assert e.targetHandle == "in__a"

    def test_outbound_edge_rewired(self):
        """Internal → external edge becomes submodel → external with sourceHandle."""
        edges = [self._edge("b", "ext")]
        result = rewire_edges(edges, "submodel__grp", {"a", "b"})
        assert len(result) == 1
        e = result[0]
        assert e.source == "submodel__grp"
        assert e.sourceHandle == "out__b"
        assert e.target == "ext"

    def test_mixed_edges(self):
        """Mix of internal, external, inbound, and outbound edges."""
        edges = [
            self._edge("x", "y"),  # external
            self._edge("a", "b"),  # internal
            self._edge("ext", "a"),  # inbound
            self._edge("b", "out"),  # outbound
        ]
        result = rewire_edges(edges, "submodel__grp", {"a", "b"})
        # internal dropped, so 3 results
        assert len(result) == 3
        sources = {e.source for e in result}
        targets = {e.target for e in result}
        assert "x" in sources  # external preserved
        assert "submodel__grp" in sources  # outbound rewired
        assert "submodel__grp" in targets  # inbound rewired

    def test_empty_edges(self):
        """Empty edge list returns empty list."""
        assert rewire_edges([], "submodel__grp", {"a"}) == []

    def test_edge_id_format(self):
        """Rewired edge IDs follow the expected naming convention."""
        edges = [
            self._edge("ext", "child"),
            self._edge("child", "ext2"),
        ]
        result = rewire_edges(edges, "submodel__grp", {"child"})
        ids = {e.id for e in result}
        assert "e_ext_submodel__grp__child" in ids
        assert "e_submodel__grp_ext2__child" in ids
