"""Tests for haute._submodel_graph shared helpers."""

from __future__ import annotations

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
        assert node.data.config["outputPortLabels"] == {}

    def test_output_port_labels_follow_port_order_and_exclude_non_outputs(self):
        node = build_submodel_placeholder(
            "scoring",
            "modules/scoring.py",
            ["a", "b", "c"],
            [],
            ["b", "a"],
            output_port_labels={
                "a": "Quote frame",
                "b": "Claims frame",
                "c": "Internal only",
            },
        )

        assert list(node.data.config["outputPortLabels"]) == ["b", "a"]
        assert node.data.config["outputPortLabels"] == {
            "b": "Claims frame",
            "a": "Quote frame",
        }

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

    def test_empty_child_node_ids(self):
        node = build_submodel_placeholder("empty", "f.py", [], [], [])
        assert node.data.config["childNodeIds"] == []
        assert node.id == "submodel__empty"
        assert node.type == NodeType.SUBMODEL

    def test_empty_input_and_output_ports(self):
        node = build_submodel_placeholder("iso", "f.py", ["a"], [], [])
        assert node.data.config["inputPorts"] == []
        assert node.data.config["outputPorts"] == []

    def test_description_with_special_characters(self):
        desc = 'Line1\nLine2\t"quoted" <tag> & symbol'
        node = build_submodel_placeholder("sp", "f.py", ["a"], [], [], description=desc)
        assert node.data.description == desc


# ---------------------------------------------------------------------------
# classify_ports
# ---------------------------------------------------------------------------


class TestClassifyPorts:
    """Tests for determining input/output ports from cross-boundary edges."""

    def test_basic_classification(self):
        """Inbound edges → input ports, outbound → output ports."""
        child_ids = {"a", "b"}
        cross_edges = [
            GraphEdge(id="in", source="external", target="a"),
            GraphEdge(id="out", source="b", target="external2"),
        ]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == ["a"]
        assert outputs == ["b"]

    def test_deduplication(self):
        """Duplicate port references are deduplicated, preserving order."""
        child_ids = {"a"}
        cross_edges = [
            GraphEdge(id="x-a", source="x", target="a"),
            GraphEdge(id="y-a", source="y", target="a"),
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
            GraphEdge(id="in", source="ext1", target="a"),
            GraphEdge(id="out", source="a", target="ext2"),
        ]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == ["a"]
        assert outputs == ["a"]

    def test_internal_edges_ignored(self):
        """Edges fully inside the submodel produce no ports."""
        child_ids = {"a", "b"}
        cross_edges = [GraphEdge(id="a-b", source="a", target="b")]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == []
        assert outputs == []

    def test_empty_cross_edges_empty_children(self):
        inputs, outputs = classify_ports([], set())
        assert (inputs, outputs) == ([], [])

    def test_node_both_input_and_output(self):
        child_ids = {"a", "b"}
        cross_edges = [
            GraphEdge(id="in-a", source="ext1", target="a"),
            GraphEdge(id="out-a", source="a", target="ext2"),
            GraphEdge(id="in-b", source="ext3", target="b"),
            GraphEdge(id="out-b", source="b", target="ext4"),
        ]
        inputs, outputs = classify_ports(cross_edges, child_ids)
        assert inputs == ["a", "b"]
        assert outputs == ["a", "b"]

    def test_deduplication_with_order_preservation(self):
        child_ids = {"a", "b", "c"}
        cross_edges = [
            GraphEdge(id="ext-c-1", source="ext", target="c"),
            GraphEdge(id="ext-a-1", source="ext", target="a"),
            GraphEdge(id="ext-c-2", source="ext", target="c"),
            GraphEdge(id="ext-b", source="ext", target="b"),
            GraphEdge(id="ext-a-2", source="ext", target="a"),
        ]
        inputs, _ = classify_ports(cross_edges, child_ids)
        assert inputs == ["c", "a", "b"]

    def test_all_edges_internal_empty_ports(self):
        child_ids = {"a", "b", "c"}
        cross_edges = [
            GraphEdge(id="a-b", source="a", target="b"),
            GraphEdge(id="b-c", source="b", target="c"),
            GraphEdge(id="a-c", source="a", target="c"),
        ]
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
        """Rewired boundary IDs include a deterministic port digest."""
        edges = [
            self._edge("ext", "child"),
            self._edge("child", "ext2"),
        ]
        result = rewire_edges(edges, "submodel__grp", {"child"})
        assert result[0].id.startswith("e_ext_submodel__grp__child_")
        assert result[1].id.startswith("e_submodel__grp_ext2__child_")

    def test_all_internal_all_dropped(self):
        edges = [self._edge("a", "b"), self._edge("b", "c"), self._edge("a", "c")]
        result = rewire_edges(edges, "submodel__grp", {"a", "b", "c"})
        assert result == []

    def test_all_external_all_preserved(self):
        edges = [self._edge("x", "y"), self._edge("y", "z")]
        result = rewire_edges(edges, "submodel__grp", {"a"})
        assert len(result) == 2
        assert result[0].source == "x" and result[0].target == "y"
        assert result[1].source == "y" and result[1].target == "z"

    def test_multiple_inbound_to_same_child(self):
        edges = [self._edge("ext1", "a"), self._edge("ext2", "a")]
        result = rewire_edges(edges, "submodel__grp", {"a"})
        assert len(result) == 2
        for e in result:
            assert e.target == "submodel__grp"
            assert e.targetHandle == "in__a"
        assert result[0].source == "ext1"
        assert result[1].source == "ext2"

    def test_multiple_outbound_from_same_child(self):
        edges = [self._edge("a", "ext1"), self._edge("a", "ext2")]
        result = rewire_edges(edges, "submodel__grp", {"a"})
        assert len(result) == 2
        for e in result:
            assert e.source == "submodel__grp"
            assert e.sourceHandle == "out__a"
        assert result[0].target == "ext1"
        assert result[1].target == "ext2"

    def test_edge_id_deterministic(self):
        edges = [self._edge("ext", "child")]
        r1 = rewire_edges(edges, "submodel__grp", {"child"})
        r2 = rewire_edges(edges, "submodel__grp", {"child"})
        base_id = "e_ext_submodel__grp__child"
        assert r1[0].id == r2[0].id
        assert r1[0].id.startswith(f"{base_id}_")
        assert r1[0].id != base_id

    def test_cross_submodel_edge_keeps_both_boundary_handles(self):
        """A child-of-A → child-of-B edge is rewired once per submodel.

        The second pass must not clobber the boundary handle set by the first
        pass: the opposite-side handle has to be preserved so the flattener
        can rebuild the direct child→child edge on re-save.
        """
        # a1 lives in submodel A, b1 lives in submodel B.
        edges = [self._edge("a1", "b1")]
        # First pass: dissolve submodel A (a1 is inside it).
        after_a = rewire_edges(edges, "submodel__A", {"a1"})
        assert after_a[0].source == "submodel__A"
        assert after_a[0].sourceHandle == "out__a1"
        # Second pass: dissolve submodel B (b1 is inside it). The source-side
        # handle from the first pass must survive.
        after_b = rewire_edges(after_a, "submodel__B", {"b1"})
        assert len(after_b) == 1
        e = after_b[0]
        assert e.source == "submodel__A"
        assert e.target == "submodel__B"
        assert e.sourceHandle == "out__a1"  # preserved, not clobbered to None
        assert e.targetHandle == "in__b1"
