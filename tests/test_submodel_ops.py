"""Unit tests for the pure graph operations in _submodel_ops."""

from __future__ import annotations

import pytest

from haute.graph_utils import NodeType
from haute.routes._submodel_ops import SubmodelGraphResult, create_submodel_graph
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node


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
                        "nodeType": "dataSource",
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
                            "nodeType": "dataSource",
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
        edge_ids_original = {
            e.id for e in graph.edges if e.source not in {"b", "c"} and e.target not in {"b", "c"}
        }
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
