"""Unit tests for the pure graph operations in _submodel_ops."""

from __future__ import annotations

import ast

import pytest

from haute.codegen import graph_to_code_multi
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
        # The submodel node should be type SUBMODEL
        sm_node = next(n for n in new_nodes if n.data.nodeType == NodeType.SUBMODEL)
        assert sm_node.id in node_ids
        assert sm_node.data.nodeType == NodeType.SUBMODEL
        assert sm_node.data.config == {"definitionId": "my_sub", "alias": "my_sub"}
        definition = result.graph.submodels[sm_node.data.config["definitionId"]]
        assert definition.file == "modules/my_sub.py"

    def test_edges_rewired(self):
        """Cross-boundary edge src→t1 rewired to src→submodel."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "grp")

        edges = result.graph.edges
        # Only 1 edge: src → submodel (the internal t1→t2 is removed)
        assert len(edges) == 1
        e = edges[0]
        assert e.source == "src"
        assert e.target.startswith("submodel_instance_")
        assert e.targetHandle == "in__input_1"

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
                    {
                        "id": "t1",
                        "data": {
                            "label": "Priced quotes",
                            "nodeType": "polars",
                            "config": {},
                        },
                    },
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
        assert e.source.startswith("submodel_instance_")
        assert e.sourceHandle == "out__output_1"
        assert e.target == "out"
        definition = result.graph.submodels["inner"]
        assert [(port.port_id, port.label) for port in definition.output_ports] == [
            ("output_1", "Priced quotes")
        ]

    def test_submodels_metadata_populated(self):
        """Submodel metadata includes child IDs, ports, and internal graph."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "sub")

        subs = result.graph.submodels
        assert "sub" in subs
        meta = subs["sub"]
        assert meta.file == "modules/sub.py"
        assert meta.definition_id == "sub"
        assert [port.port_id for port in meta.input_ports] == ["input_1"]
        assert [target.node_id for target in meta.input_ports[0].targets] == ["t1"]
        assert meta.graph.pipeline_name == "sub"

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
                "submodels": {
                    "definition_existing": {
                        "definitionId": "definition_existing",
                        "file": "modules/existing.py",
                        "graph": {"nodes": [], "edges": []},
                        "inputPorts": [],
                        "outputPorts": [],
                    }
                },
            }
        )
        result = create_submodel_graph(graph, ["a", "b"], "new_one")

        assert "definition_existing" in result.graph.submodels
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

    def test_name_sanitized(self):
        """Names with spaces/special chars are sanitized."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "My Sub Model!")
        # _sanitize_func_name replaces special chars but preserves case
        assert result.sm_name == "My_Sub_Model"
        assert "My_Sub_Model" in result.sm_file

    def test_nonexistent_node_ids_reject_the_whole_selection(self):
        from haute.routes._submodel_ops import SubmodelValidationError

        graph = _simple_graph()
        with pytest.raises(SubmodelValidationError) as exc_info:
            create_submodel_graph(graph, ["t1", "does_not_exist"], "bad")
        assert (exc_info.value.code, exc_info.value.status_code) == ("stale_selection", 409)

    def test_duplicate_node_ids_reject_the_whole_selection(self):
        from haute.routes._submodel_ops import SubmodelValidationError

        graph = _simple_graph()
        with pytest.raises(SubmodelValidationError) as exc_info:
            create_submodel_graph(graph, ["t1", "t1", "t1"], "dup")
        assert (exc_info.value.code, exc_info.value.status_code) == (
            "duplicate_selection",
            400,
        )

    def test_all_nodes_selected(self):
        """Selecting all nodes in the graph creates a valid submodel."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["src", "t1", "t2"], "all_in")
        # All 3 nodes become child nodes; parent has only the placeholder
        assert len(result.graph.nodes) == 1
        assert result.graph.nodes[0].id.startswith("submodel_instance_")
        # No external edges remain
        assert len(result.graph.edges) == 0
        assert result.graph.nodes[0].data.config == {"definitionId": "all_in", "alias": "all_in"}

    def test_two_disconnected_data_inputs_generate_a_valid_submodel(self):
        """Two source-only nodes can be the complete reusable definition."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "competitor_premium",
                        "data": {
                            "label": "competitor_premium",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "competitor.parquet",
                                "arguments": {},
                            },
                        },
                    },
                    {
                        "id": "nb_batch",
                        "data": {
                            "label": "nb_batch",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "nb_batch.parquet",
                                "arguments": {},
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )

        result = create_submodel_graph(
            graph,
            ["competitor_premium", "nb_batch"],
            "two_inputs",
        )
        files = graph_to_code_multi(result.graph, pipeline_name="test", source_file="main.py")

        assert set(files) == {"main.py", "modules/two_inputs.py"}
        child_source = files["modules/two_inputs.py"]
        assert child_source.count("@submodel.data_input") == 2
        assert "_HAUTE_CONFIG_BASE = _HautePath(__file__).resolve().parent.parent" in child_source
        assert child_source.index("submodel = haute.Submodel") < child_source.index(
            "_HAUTE_CONFIG_BASE ="
        )
        assert child_source.count("base_dir=_HAUTE_CONFIG_BASE") == 2
        for source in files.values():
            ast.parse(source)

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
        assert [port.port_id for port in subs.input_ports] == ["input_1", "input_2"]
        assert [target.node_id for port in subs.input_ports for target in port.targets] == [
            "t1",
            "t2",
        ]

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
        assert [port.port_id for port in subs.output_ports] == ["output_1", "output_2"]
        assert [port.source.node_id for port in subs.output_ports] == ["t1", "t2"]

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
        assert [target.node_id for target in subs.input_ports[0].targets] == ["t1"]
        assert subs.output_ports[0].source.node_id == "t2"

        # Parent graph should have 3 nodes: src, submodel, out
        assert len(result.graph.nodes) == 3
        # Parent graph should have 2 edges: src→submodel, submodel→out
        assert len(result.graph.edges) == 2

    def test_internal_graph_structure(self):
        """The submodel's internal graph has the right nodes and edges."""
        graph = _simple_graph()
        result = create_submodel_graph(graph, ["t1", "t2"], "inner")

        sm_graph = result.graph.submodels["inner"].graph
        inner_node_ids = {node.id for node in sm_graph.nodes}
        assert inner_node_ids == {"t1", "t2"}

        inner_edge_sources = {edge.source for edge in sm_graph.edges}
        inner_edge_targets = {edge.target for edge in sm_graph.edges}
        assert inner_edge_sources == {"t1"}
        assert inner_edge_targets == {"t2"}

        assert sm_graph.pipeline_name == "inner"
        assert sm_graph.source_file == "modules/inner.py"

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
        assert subs.input_ports == []
        assert subs.output_ports == []

        # Parent graph: c + submodel, no edges between them
        assert len(result.graph.nodes) == 2
        assert len(result.graph.edges) == 0

    def test_nesting_two_submodel_nodes_raises(self):
        """Selecting two canonical occurrences also rejects nesting."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "instance_a",
                        "data": {
                            "label": "a",
                            "nodeType": "submodel",
                            "config": {"definitionId": "definition_a", "alias": "a"},
                        },
                    },
                    {
                        "id": "instance_b",
                        "data": {
                            "label": "b",
                            "nodeType": "submodel",
                            "config": {"definitionId": "definition_b", "alias": "b"},
                        },
                    },
                ],
                "edges": [],
                "submodels": {},
            }
        )
        with pytest.raises(ValueError, match="cannot be nested"):
            create_submodel_graph(graph, ["instance_a", "instance_b"], "outer")

    def test_single_submodel_node_raises_nesting_not_count(self):
        """A canonical occurrence reports nesting before selection count."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {
                        "id": "instance_only",
                        "data": {
                            "label": "only",
                            "nodeType": "submodel",
                            "config": {"definitionId": "definition_only", "alias": "only"},
                        },
                    },
                ],
                "edges": [],
                "submodels": {},
            }
        )
        with pytest.raises(ValueError, match="cannot be nested"):
            create_submodel_graph(graph, ["instance_only"], "wrap")

    def test_nesting_submodel_node_raises(self):
        """Selecting a canonical occurrence for grouping raises ValueError."""
        graph = make_graph(
            {
                "pipeline_name": "test",
                "nodes": [
                    {"id": "a", "data": {"label": "a", "nodeType": "polars", "config": {}}},
                    {
                        "id": "instance_existing",
                        "data": {
                            "label": "existing",
                            "nodeType": "submodel",
                            "config": {
                                "definitionId": "definition_existing",
                                "alias": "existing",
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "a", "target": "instance_existing"}],
                "submodels": {},
            }
        )
        with pytest.raises(ValueError, match="cannot be nested"):
            create_submodel_graph(graph, ["a", "instance_existing"], "outer")

    def test_blank_name_uses_stable_submodel_error(self):
        from haute.routes._submodel_ops import SubmodelValidationError

        with pytest.raises(SubmodelValidationError) as exc_info:
            create_submodel_graph(_simple_graph(), ["t1", "t2"], "   ")
        assert exc_info.value.code == "blank_name"
        assert exc_info.value.status_code == 400

    def test_existing_name_is_conflict(self):
        from haute.routes._submodel_ops import SubmodelValidationError

        graph = _simple_graph().model_copy(
            update={
                "submodels": {
                    "group": {
                        "definitionId": "group",
                        "file": "modules/group.py",
                        "graph": {"nodes": [], "edges": []},
                        "inputPorts": [],
                        "outputPorts": [],
                    }
                }
            }
        )
        with pytest.raises(SubmodelValidationError) as exc_info:
            create_submodel_graph(graph, ["t1", "t2"], "group")
        assert (exc_info.value.code, exc_info.value.status_code) == ("submodel_exists", 409)

    def test_existing_name_is_a_case_insensitive_conflict(self):
        from haute.routes._submodel_ops import SubmodelValidationError

        graph = _simple_graph().model_copy(
            update={
                "submodels": {
                    "Group": {
                        "definitionId": "Group",
                        "file": "modules/Group.py",
                        "graph": {"nodes": [], "edges": []},
                        "inputPorts": [],
                        "outputPorts": [],
                    }
                }
            }
        )
        with pytest.raises(SubmodelValidationError) as exc_info:
            create_submodel_graph(graph, ["t1", "t2"], "group")
        assert (exc_info.value.code, exc_info.value.status_code) == ("submodel_exists", 409)

    def test_child_order_context_and_managed_metadata(self):
        graph = _simple_graph().model_copy(
            update={"preamble": "HELPER = 1", "preserved_blocks": ["KEEP = 2"]}
        )
        result = create_submodel_graph(graph, ["t2", "t1"], "group")
        embedded = result.graph.submodels["group"]
        assert [node.id for node in embedded.graph.nodes] == ["t1", "t2"]
        assert embedded.graph.preamble == "HELPER = 1"
        assert embedded.graph.preserved_blocks == ["KEEP = 2"]

    def test_placeholder_uses_selected_bounding_box_centre(self):
        graph = _simple_graph()
        positions = {
            "src": {"x": -500.0, "y": -500.0},
            "t1": {"x": 20.0, "y": 40.0},
            "t2": {"x": 100.0, "y": 200.0},
        }
        graph = graph.model_copy(
            update={
                "nodes": [
                    node.model_copy(update={"position": positions[node.id]}) for node in graph.nodes
                ]
            }
        )

        result = create_submodel_graph(graph, ["t2", "t1"], "group")

        placeholder = next(
            node for node in result.graph.nodes if node.data.nodeType == NodeType.SUBMODEL
        )
        assert placeholder.position == {"x": 60.0, "y": 120.0}
