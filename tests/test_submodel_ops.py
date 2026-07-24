"""Unit tests for the pure graph operations in _submodel_ops."""

from __future__ import annotations

import pytest

from haute.graph_utils import NodeType
from haute.routes._submodel_ops import SubmodelGraphResult, create_submodel_graph
from tests.conftest import make_graph


def _simple_graph():
    """Build a 3-node linear graph: src → t1 → t2."""
    return make_graph(
        {
            "pipeline_name": "test",
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "dataInput",
                        "config": {"path": "x.parquet"},
                    },
                },
                {"id": "t1", "data": {"label": "t1", "nodeType": "polars", "config": {}}},
                {"id": "t2", "data": {"label": "t2", "nodeType": "polars", "config": {}}},
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "t1"},
                {"id": "e2", "source": "t1", "target": "t2"},
            ],
        }
    )


class TestCreateSubmodelGraph:
    """Tests for create_submodel_graph()."""

    def test_basic_extraction(self):
        """Grouping t1+t2 produces a submodel node and rewired edges."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "my sub")

        assert isinstance(result, SubmodelGraphResult)
        assert result.sm_name == "my_sub"
        assert result.sm_file == "modules/my_sub.py"

        # Parent graph should have 2 nodes: src + submodel placeholder
        new_nodes = result.graph.nodes
        assert len(new_nodes) == 2
        node_ids = {n.id for n in new_nodes}
        assert "src" in node_ids
        assert "submodel__my_sub" in node_ids

        # The submodel node should be type SUBMODEL
        sm_node = next(n for n in new_nodes if n.id == "submodel__my_sub")
        assert sm_node.data.nodeType == NodeType.SUBMODEL
        assert sm_node.data.config["file"] == "modules/my_sub.py"

    def test_edges_rewired(self):
        """Cross-boundary edge src→t1 rewired to src→submodel."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "grp")

        edges = result.graph.edges
        # Only 1 edge: src → submodel (the internal t1→t2 is removed)
        assert len(edges) == 1
        e = edges[0]
        assert e.source == "src"
        assert e.target == "submodel__grp"
        assert e.targetHandle == "in__t1"

    def test_output_port_rewiring(self):
        """Output edge from child node to external node rewires correctly."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": {"path": "x.parquet"},
                        },
                    },
                    {"id": "t1", "data": {"label": "t1", "nodeType": "polars", "config": {}}},
                    {"id": "out", "data": {"label": "out", "nodeType": "output", "config": {}}},
                ],
                "edges": [
                    {"id": "e1", "source": "src", "target": "t1"},
                    {"id": "e2", "source": "t1", "target": "out"},
                ],
            }
        )
        # Group src + t1, leaving 'out' outside
        result = create_submodel_graph(graph, ["src", "t1"], "inner")

        edges = result.graph.edges
        assert len(edges) == 1
        e = edges[0]
        assert e.source == "submodel__inner"
        assert e.sourceHandle == "out__t1"
        assert e.target == "out"

    def test_submodels_metadata_populated(self):
        """Submodel metadata includes child IDs, ports, and internal graph."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "sub")

        subs = result.graph.submodels
        assert "sub" in subs
        meta = subs["sub"]
        assert meta["file"] == "modules/sub.py"
        assert set(meta["childNodeIds"]) == {"t1", "t2"}
        assert "t1" in meta["inputPorts"]
        assert meta["graph"]["submodel_name"] == "sub"

    def test_preserves_existing_submodels(self):
        """Existing submodel metadata is preserved when adding a new one."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {"id": "a", "data": {"label": "a", "nodeType": "polars", "config": {}}},
                    {"id": "b", "data": {"label": "b", "nodeType": "polars", "config": {}}},
                ],
                "edges": [{"id": "e1", "source": "a", "target": "b"}],
                "submodels": {"existing": {"file": "modules/existing.py", "childNodeIds": []}},
            }
        )
        result = create_submodel_graph(graph, ["a", "b"], "new_one")

        assert "existing" in result.graph.submodels
        assert "new_one" in result.graph.submodels

    def test_external_edges_preserved(self):
        """Edges between two non-selected nodes are preserved unchanged."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {"id": "a", "data": {"label": "a", "nodeType": "polars", "config": {}}},
                    {"id": "b", "data": {"label": "b", "nodeType": "polars", "config": {}}},
                    {"id": "c", "data": {"label": "c", "nodeType": "polars", "config": {}}},
                    {"id": "d", "data": {"label": "d", "nodeType": "polars", "config": {}}},
                ],
                "edges": [
                    {"id": "e1", "source": "a", "target": "b"},
                    {"id": "e2", "source": "b", "target": "c"},
                    {"id": "e3", "source": "c", "target": "d"},
                ],
            }
        )
        # Group b + c
        result = create_submodel_graph(graph, ["b", "c"], "mid")
        # No fully-external edges in this case, but rewired ones are present
        assert len(result.graph.edges) == 2  # a→sm, sm→d

    def test_fewer_than_2_nodes_raises(self):
        """Selecting fewer than 2 nodes raises ValueError."""
        graph = _simple_graph()
        with pytest.raises(ValueError, match="at least 2 nodes"):
            create_submodel_graph(graph, ["t1"], "solo")

    def test_empty_selection_raises(self):
        """Empty node list raises ValueError."""
        graph = _simple_graph()
        with pytest.raises(ValueError, match="at least 2 nodes"):
            create_submodel_graph(graph, [], "empty")

    def test_child_node_ids_returned(self):
        """Result includes the list of child node IDs."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "sub")
        assert set(result.child_node_ids) == {"t1", "t2"}

    def test_name_sanitized(self):
        """Names with spaces/special chars are sanitized."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "My Sub Model!")
        # _sanitize_func_name replaces special chars but preserves case
        assert result.sm_name == "My_Sub_Model"
        assert "My_Sub_Model" in result.sm_file

    def test_nonexistent_node_ids_filtered(self):
        """Node IDs not present in the graph are filtered out; if <2 remain, raises."""
        graph = _simple_graph()
        with pytest.raises(ValueError, match="at least 2 nodes"):
            create_submodel_graph(graph, ["t1", "does_not_exist"], "bad")

    def test_duplicate_node_ids_counted_once(self):
        """Duplicate node IDs in the list are treated as one node."""
        graph = _simple_graph()
        with pytest.raises(ValueError, match="at least 2 nodes"):
            create_submodel_graph(graph, ["t1", "t1", "t1"], "dup")

    def test_all_nodes_selected(self):
        """Selecting all nodes in the graph creates a valid submodel."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["src", "t1", "t2"], "all_in")
        # All 3 nodes become child nodes; parent has only the placeholder
        assert len(result.graph.nodes) == 1
        assert result.graph.nodes[0].id == "submodel__all_in"
        # No external edges remain
        assert len(result.graph.edges) == 0
        assert set(result.child_node_ids) == {"src", "t1", "t2"}

    def test_multiple_input_ports(self):
        """Submodel with multiple incoming cross-boundary edges gets multiple input ports."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "a",
                            "nodeType": "dataInput",
                            "config": {"path": "a.parquet"},
                        },
                    },
                    {
                        "id": "b",
                        "data": {
                            "label": "b",
                            "nodeType": "dataInput",
                            "config": {"path": "b.parquet"},
                        },
                    },
                    {"id": "t1", "data": {"label": "t1", "nodeType": "polars", "config": {}}},
                    {"id": "t2", "data": {"label": "t2", "nodeType": "polars", "config": {}}},
                ],
                "edges": [
                    {"id": "e1", "source": "a", "target": "t1"},
                    {"id": "e2", "source": "b", "target": "t2"},
                    {"id": "e3", "source": "t1", "target": "t2"},
                ],
            }
        )
        # Group t1 + t2: both have incoming edges from outside (a→t1, b→t2)
        result = create_submodel_graph(graph, ["t1", "t2"], "multi_in")

        subs = result.graph.submodels["multi_in"]
        input_ports = subs["inputPorts"]
        assert "t1" in input_ports
        assert "t2" in input_ports

    def test_multiple_output_ports(self):
        """Submodel with multiple outgoing cross-boundary edges gets multiple output ports."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {"id": "t1", "data": {"label": "t1", "nodeType": "polars", "config": {}}},
                    {"id": "t2", "data": {"label": "t2", "nodeType": "polars", "config": {}}},
                    {
                        "id": "out1",
                        "data": {"label": "out1", "nodeType": "output", "config": {}},
                    },
                    {
                        "id": "out2",
                        "data": {"label": "out2", "nodeType": "output", "config": {}},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "t1", "target": "t2"},
                    {"id": "e2", "source": "t1", "target": "out1"},
                    {"id": "e3", "source": "t2", "target": "out2"},
                ],
            }
        )
        # Group t1 + t2: both have outgoing edges to outside (t1→out1, t2→out2)
        result = create_submodel_graph(graph, ["t1", "t2"], "multi_out")

        subs = result.graph.submodels["multi_out"]
        output_ports = subs["outputPorts"]
        assert "t1" in output_ports
        assert "t2" in output_ports

    def test_bidirectional_cross_edges(self):
        """A submodel with both input and output cross-boundary edges."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": {"path": "x.parquet"},
                        },
                    },
                    {"id": "t1", "data": {"label": "t1", "nodeType": "polars", "config": {}}},
                    {"id": "t2", "data": {"label": "t2", "nodeType": "polars", "config": {}}},
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "src", "target": "t1"},
                    {"id": "e2", "source": "t1", "target": "t2"},
                    {"id": "e3", "source": "t2", "target": "out"},
                ],
            }
        )
        # Group t1 + t2: input from src, output to out
        result = create_submodel_graph(graph, ["t1", "t2"], "middle")

        subs = result.graph.submodels["middle"]
        assert "t1" in subs["inputPorts"]
        assert "t2" in subs["outputPorts"]

        # Parent graph should have 3 nodes: src, submodel, out
        assert len(result.graph.nodes) == 3
        # Parent graph should have 2 edges: src→submodel, submodel→out
        assert len(result.graph.edges) == 2

    def test_internal_graph_structure(self):
        """The submodel's internal graph has the right nodes and edges."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "inner")

        sm_graph = result.graph.submodels["inner"]["graph"]
        inner_node_ids = {n["id"] for n in sm_graph["nodes"]}
        assert inner_node_ids == {"t1", "t2"}

        inner_edge_sources = {e["source"] for e in sm_graph["edges"]}
        inner_edge_targets = {e["target"] for e in sm_graph["edges"]}
        assert inner_edge_sources == {"t1"}
        assert inner_edge_targets == {"t2"}

        assert sm_graph["submodel_name"] == "inner"
        assert sm_graph["source_file"] == "modules/inner.py"

    def test_no_cross_edges(self):
        """When selected nodes have no cross-boundary edges, only internal graph is built."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {"id": "a", "data": {"label": "a", "nodeType": "polars", "config": {}}},
                    {"id": "b", "data": {"label": "b", "nodeType": "polars", "config": {}}},
                    {"id": "c", "data": {"label": "c", "nodeType": "polars", "config": {}}},
                ],
                "edges": [
                    {"id": "e1", "source": "a", "target": "b"},
                ],
            }
        )
        # Group a + b (connected), c is isolated external
        result = create_submodel_graph(graph, ["a", "b"], "pair")

        subs = result.graph.submodels["pair"]
        assert subs["inputPorts"] == []
        assert subs["outputPorts"] == []

        # Parent graph: c + submodel, no edges between them
        assert len(result.graph.nodes) == 2
        assert len(result.graph.edges) == 0

    def test_nesting_two_submodel_nodes_raises(self):
        """Selecting two submodel nodes also raises (not just mixed selection)."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "submodel__a",
                        "data": {
                            "label": "a",
                            "nodeType": "submodel",
                            "config": {
                                "file": "modules/a.py",
                                "childNodeIds": [],
                                "inputPorts": [],
                                "outputPorts": [],
                            },
                        },
                    },
                    {
                        "id": "submodel__b",
                        "data": {
                            "label": "b",
                            "nodeType": "submodel",
                            "config": {
                                "file": "modules/b.py",
                                "childNodeIds": [],
                                "inputPorts": [],
                                "outputPorts": [],
                            },
                        },
                    },
                ],
                "edges": [],
                "submodels": {
                    "a": {"file": "modules/a.py", "childNodeIds": []},
                    "b": {"file": "modules/b.py", "childNodeIds": []},
                },
            }
        )
        with pytest.raises(ValueError, match="cannot be nested"):
            create_submodel_graph(graph, ["submodel__a", "submodel__b"], "outer")

    def test_single_submodel_node_raises_nesting_not_count(self):
        """One submodel node: nesting error takes precedence over count error."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "submodel__only",
                        "data": {
                            "label": "only",
                            "nodeType": "submodel",
                            "config": {
                                "file": "modules/only.py",
                                "childNodeIds": [],
                                "inputPorts": [],
                                "outputPorts": [],
                            },
                        },
                    },
                ],
                "edges": [],
                "submodels": {"only": {"file": "modules/only.py", "childNodeIds": []}},
            }
        )
        # Should get nesting error, not "at least 2 nodes" error
        with pytest.raises(ValueError, match="cannot be nested"):
            create_submodel_graph(graph, ["submodel__only"], "wrap")

    def test_nesting_submodel_node_raises(self):
        """Selecting a submodel node for grouping raises ValueError (no nesting)."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {"id": "a", "data": {"label": "a", "nodeType": "polars", "config": {}}},
                    {
                        "id": "submodel__existing",
                        "data": {
                            "label": "existing",
                            "nodeType": "submodel",
                            "config": {
                                "file": "modules/existing.py",
                                "childNodeIds": [],
                                "inputPorts": [],
                                "outputPorts": [],
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "a", "target": "submodel__existing"}],
                "submodels": {"existing": {"file": "modules/existing.py", "childNodeIds": []}},
            }
        )
        with pytest.raises(ValueError, match="cannot be nested"):
            create_submodel_graph(graph, ["a", "submodel__existing"], "outer")
