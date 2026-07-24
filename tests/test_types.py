"""Comprehensive tests for haute._types.

Covers:
  - NodeType enum completeness and StrEnum behavior
  - NodeData defaults and construction
  - GraphNode defaults and construction
  - GraphEdge construction and optional handles
  - PipelineGraph construction, cached properties, defaults
  - _sanitize_func_name (additional edge cases beyond test_graph_utils.py)
  - build_instance_mapping (additional combos beyond test_graph_utils.py)
  - resolve_orig_source_names (additional edge cases)
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from haute._graph_utils import (
    _resolve_sink_path,
    _sanitize_func_name,
    build_instance_mapping,
    resolve_orig_source_names,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)

# ===========================================================================
# NodeType enum
# ===========================================================================


class TestNodeType:
    EXPECTED_MEMBERS = {
        "API_INPUT": "apiInput",
        "DATA_INPUT": "dataInput",
        "DATA_OUTPUT": "dataOutput",
        "POLARS": "polars",
        "EDGE_JOIN": "edgeJoin",
        "MODEL_SCORE": "modelScore",
        "BANDING": "banding",
        "RATING_STEP": "ratingStep",
        "OUTPUT": "output",
        "EXPLORE": "explore",
        "EXTERNAL_FILE": "externalFile",
        "LIVE_SWITCH": "liveSwitch",
        "MODELLING": "modelling",
        "OPTIMISER": "optimiser",
        "SCENARIO_EXPANDER": "scenarioExpander",
        "OPTIMISER_APPLY": "optimiserApply",
        "CONSTANT": "constant",
        "SUBMODEL": "submodel",
        "SUBMODEL_PORT": "submodelPort",
    }

    def test_no_unexpected_members(self):
        actual = {m.name for m in NodeType}
        expected = set(self.EXPECTED_MEMBERS.keys())
        assert actual == expected, f"Unexpected members: {actual - expected}"

    def test_string_values_match(self):
        for member_name, value in self.EXPECTED_MEMBERS.items():
            assert NodeType[member_name].value == value

    def test_str_enum_in_json_serialization(self):
        """StrEnum values serialize to plain strings in JSON."""
        import json

        result = json.dumps({"type": NodeType.OUTPUT})
        assert '"output"' in result

    def test_construct_from_string(self):
        assert NodeType("dataInput") == NodeType.DATA_INPUT

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            NodeType("nonexistent")

    def test_str_enum_equality_with_plain_string(self):
        assert NodeType("output") == "output"

    def test_hashable_as_dict_key(self):
        d = {NodeType.OUTPUT: "ok"}
        assert d[NodeType.OUTPUT] == "ok"
        assert hash(NodeType.OUTPUT) == hash("output")

    def test_case_sensitive_invalid(self):
        with pytest.raises(ValueError):
            NodeType("INVALID")

    def test_empty_string_invalid(self):
        with pytest.raises(ValueError):
            NodeType("")


# ===========================================================================
# NodeData
# ===========================================================================


class TestNodeData:
    def test_defaults(self):
        nd = NodeData()
        assert nd.label == "Unnamed"
        assert nd.description == ""
        assert nd.nodeType == NodeType.POLARS
        assert nd.config == {}

    def test_custom_values(self):
        nd = NodeData(
            label="My Node",
            description="A description",
            nodeType=NodeType.DATA_INPUT,
            config={"path": "data.parquet"},
        )
        assert nd.label == "My Node"
        assert nd.nodeType == NodeType.DATA_INPUT
        assert nd.config["path"] == "data.parquet"

    def test_node_type_accepts_string(self):
        nd = NodeData(nodeType="banding")
        assert nd.nodeType == NodeType.BANDING

    def test_config_is_independent_copy(self):
        """Config dict should not be shared between instances."""
        nd1 = NodeData()
        nd2 = NodeData()
        nd1.config["x"] = 1
        assert "x" not in nd2.config

    def test_json_serialization_roundtrip(self):
        nd = NodeData(label="Test", nodeType=NodeType.DATA_INPUT, config={"k": 1})
        json_str = nd.model_dump_json()
        restored = NodeData.model_validate_json(json_str)
        assert restored.label == "Test"
        assert restored.nodeType == NodeType.DATA_INPUT
        assert restored.config == {"k": 1}

    def test_empty_string_label_preserved(self):
        nd = NodeData(label="")
        assert nd.label == ""

    def test_invalid_node_type_string_raises(self):
        with pytest.raises(ValidationError):
            NodeData(nodeType="totallyBogus")

    def test_extra_fields_ignored(self):
        nd = NodeData(label="X", bonus="nope")
        assert not hasattr(nd, "bonus")


# ===========================================================================
# GraphNode
# ===========================================================================


class TestGraphNode:
    def test_defaults(self):
        node = GraphNode(id="test")
        assert node.id == "test"
        assert node.type == "pipelineNode"
        assert node.position == {"x": 0.0, "y": 0.0}
        assert node.data.label == "Unnamed"

    def test_custom_construction(self):
        node = GraphNode(
            id="source",
            type="customType",
            position={"x": 100.0, "y": 200.0},
            data=NodeData(label="Source", nodeType=NodeType.DATA_INPUT),
        )
        assert node.id == "source"
        assert node.type == "customType"
        assert node.position["x"] == 100.0
        assert node.data.nodeType == NodeType.DATA_INPUT

    def test_model_validate(self):
        raw = {
            "id": "n1",
            "data": {"label": "N1", "nodeType": "polars", "config": {"code": "x"}},
        }
        node = GraphNode.model_validate(raw)
        assert node.id == "n1"
        assert node.data.config["code"] == "x"

    def test_position_default_factory(self):
        """Each node should get its own position dict, not a shared reference."""
        n1 = GraphNode(id="a")
        n2 = GraphNode(id="b")
        n1.position["x"] = 999.0
        assert n2.position["x"] == 0.0

    def test_position_with_nan_coordinates(self):
        node = GraphNode(id="n", position={"x": float("nan"), "y": float("nan")})
        assert math.isnan(node.position["x"])
        assert math.isnan(node.position["y"])

    def test_position_with_negative_coordinates(self):
        node = GraphNode(id="n", position={"x": -100.5, "y": -200.0})
        assert node.position["x"] == -100.5
        assert node.position["y"] == -200.0

    def test_model_validate_nested_node_data(self):
        raw = {
            "id": "deep",
            "position": {"x": 5.0, "y": 10.0},
            "data": {
                "label": "Deep",
                "nodeType": "banding",
                "config": {"factors": []},
            },
        }
        node = GraphNode.model_validate(raw)
        assert node.data.nodeType == NodeType.BANDING
        assert node.data.config == {"factors": []}


# ===========================================================================
# GraphEdge
# ===========================================================================


class TestGraphEdge:
    def test_basic_construction(self):
        edge = GraphEdge(id="e1", source="a", target="b")
        assert edge.id == "e1"
        assert edge.source == "a"
        assert edge.target == "b"
        assert edge.sourceHandle is None
        assert edge.targetHandle is None

    def test_with_handles(self):
        edge = GraphEdge(
            id="e1",
            source="a",
            target="b",
            sourceHandle="out1",
            targetHandle="in1",
        )
        assert edge.sourceHandle == "out1"
        assert edge.targetHandle == "in1"

    def test_model_validate(self):
        raw = {"id": "e", "source": "x", "target": "y"}
        edge = GraphEdge.model_validate(raw)
        assert edge.source == "x"

    def test_self_loop_allowed(self):
        edge = GraphEdge(id="loop", source="a", target="a")
        assert edge.source == edge.target == "a"

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            GraphEdge(id="e1", source="a")
        with pytest.raises(ValidationError):
            GraphEdge(id="e1", target="b")
        with pytest.raises(ValidationError):
            GraphEdge(source="a", target="b")

    def test_handles_default_to_none(self):
        edge = GraphEdge(id="e", source="a", target="b")
        assert edge.sourceHandle is None
        assert edge.targetHandle is None


# ===========================================================================
# PipelineGraph
# ===========================================================================


class TestPipelineGraph:
    def test_empty_graph_defaults(self):
        g = PipelineGraph()
        assert g.nodes == []
        assert g.edges == []
        assert g.pipeline_name is None
        assert g.pipeline_description is None
        assert g.preamble is None
        assert g.preserved_blocks == []
        assert g.source_file is None
        assert g.submodels is None
        assert g.warning is None
        assert g.sources == ["live"]
        assert g.active_source == "live"

    def test_construction_with_nodes_and_edges(self):
        g = PipelineGraph(
            nodes=[
                GraphNode(id="a", data=NodeData(label="A")),
                GraphNode(id="b", data=NodeData(label="B")),
            ],
            edges=[GraphEdge(id="e1", source="a", target="b")],
            pipeline_name="test",
        )
        assert len(g.nodes) == 2
        assert len(g.edges) == 1
        assert g.pipeline_name == "test"

    def test_node_map_cached_property(self):
        g = PipelineGraph(
            nodes=[
                GraphNode(id="x", data=NodeData(label="X")),
                GraphNode(id="y", data=NodeData(label="Y")),
            ],
        )
        nm = g.node_map
        assert "x" in nm
        assert "y" in nm
        assert nm["x"].data.label == "X"
        # Verify same object returned on second access (cached)
        assert g.node_map is nm

    def test_parents_of_cached_property(self):
        g = PipelineGraph(
            nodes=[
                GraphNode(id="a"),
                GraphNode(id="b"),
                GraphNode(id="c"),
            ],
            edges=[
                GraphEdge(id="e1", source="a", target="c"),
                GraphEdge(id="e2", source="b", target="c"),
            ],
        )
        po = g.parents_of
        assert set(po["c"]) == {"a", "b"}
        assert po.get("a", []) == []  # "a" has no parents
        # Cached
        assert g.parents_of is po

    def test_parents_of_empty_graph(self):
        g = PipelineGraph()
        assert g.parents_of == {}

    def test_model_validate_roundtrip(self):
        data = {
            "nodes": [
                {"id": "n1", "data": {"label": "N1", "nodeType": "dataInput", "config": {}}},
            ],
            "edges": [],
            "pipeline_name": "test",
            "sources": ["live", "test_batch"],
            "active_source": "test_batch",
        }
        g = PipelineGraph.model_validate(data)
        assert g.pipeline_name == "test"
        assert g.sources == ["live", "test_batch"]
        assert g.active_source == "test_batch"
        assert g.nodes[0].data.nodeType == NodeType.DATA_INPUT

    def test_preserved_blocks_default_factory(self):
        g1 = PipelineGraph()
        g2 = PipelineGraph()
        g1.preserved_blocks.append("block1")
        assert g2.preserved_blocks == []

    def test_node_map_returns_correct_mapping(self):
        n1 = GraphNode(id="a", data=NodeData(label="A"))
        n2 = GraphNode(id="b", data=NodeData(label="B"))
        g = PipelineGraph(nodes=[n1, n2])
        nm = g.node_map
        assert len(nm) == 2
        assert nm["a"].data.label == "A"
        assert nm["b"].data.label == "B"

    def test_parents_of_empty_graph_is_empty(self):
        g = PipelineGraph(nodes=[], edges=[])
        assert g.parents_of == {}

    def test_duplicate_node_ids_last_wins_in_node_map(self):
        n1 = GraphNode(id="dup", data=NodeData(label="First"))
        n2 = GraphNode(id="dup", data=NodeData(label="Second"))
        g = PipelineGraph(nodes=[n1, n2])
        assert g.node_map["dup"].data.label == "Second"


# ===========================================================================
# _sanitize_func_name — additional edge cases
# ===========================================================================


class TestSanitizeFuncNameExtended:
    def test_multiple_spaces(self):
        assert _sanitize_func_name("my  node") == "my__node"

    def test_leading_trailing_spaces(self):
        assert _sanitize_func_name("  hello  ") == "hello"

    def test_tab_becomes_empty(self):
        """Tabs are not alphanumeric and not underscore, so they're stripped."""
        assert _sanitize_func_name("foo\tbar") == "foobar"

    def test_only_digits(self):
        assert _sanitize_func_name("123") == "node_123"
        assert _sanitize_func_name("123").isidentifier()

    def test_single_underscore(self):
        assert _sanitize_func_name("_") == "_"


# ===========================================================================
# build_instance_mapping — additional edge cases
# ===========================================================================


class TestBuildInstanceMappingExtended:
    def test_empty_lists(self):
        assert build_instance_mapping([], []) == {}

    def test_more_orig_than_inst(self):
        """Extra orig names remain unmapped."""
        result = build_instance_mapping(["a", "b", "c"], ["a"])
        assert result["a"] == "a"
        assert "b" not in result
        assert "c" not in result

    def test_more_inst_than_orig(self):
        """Extra inst names are unused."""
        result = build_instance_mapping(["a"], ["a", "b", "c"])
        assert result == {"a": "a"}

    def test_no_matches_uses_positional(self):
        result = build_instance_mapping(["x", "y"], ["alpha", "beta"])
        assert result == {"x": "alpha", "y": "beta"}

    def test_explicit_empty_dict(self):
        result = build_instance_mapping(["a", "b"], ["a", "b"], explicit={})
        assert result == {"a": "a", "b": "b"}

    def test_partial_explicit(self):
        result = build_instance_mapping(["a", "b"], ["x", "y"], explicit={"a": "y"})
        assert result["a"] == "y"
        assert result["b"] == "x"

    def test_substring_match_priority_over_positional(self):
        """Substring match should be preferred over positional."""
        result = build_instance_mapping(
            ["data"],
            ["data_v2", "other"],
        )
        assert result["data"] == "data_v2"


# ===========================================================================
# resolve_orig_source_names — additional edge cases
# ===========================================================================


class TestResolveOrigSourceNamesExtended:
    def test_instance_of_missing_ref(self):
        """instanceOf points to a node not in node_map — returns None."""
        node = GraphNode(
            id="inst",
            data=NodeData(label="inst", config={"instanceOf": "nonexistent"}),
        )
        result = resolve_orig_source_names(node, {"inst": node}, {}, {})
        assert result is None

    def test_instance_with_no_parents(self):
        """Original node has no parents — returns empty list."""
        orig = GraphNode(id="orig", data=NodeData(label="orig"))
        inst = GraphNode(
            id="inst",
            data=NodeData(label="inst", config={"instanceOf": "orig"}),
        )
        node_map = {"orig": orig, "inst": inst}
        all_parents: dict[str, list[str]] = {}
        result = resolve_orig_source_names(inst, node_map, all_parents, {})
        assert result == []

    def test_instance_parent_in_id_to_name(self):
        """Parent found in id_to_name uses that name."""
        orig = GraphNode(id="orig", data=NodeData(label="orig"))
        parent = GraphNode(id="p", data=NodeData(label="Parent"))
        inst = GraphNode(
            id="inst",
            data=NodeData(label="inst", config={"instanceOf": "orig"}),
        )
        node_map = {"orig": orig, "p": parent, "inst": inst}
        all_parents = {"orig": ["p"]}
        id_to_name = {"p": "Parent"}
        result = resolve_orig_source_names(inst, node_map, all_parents, id_to_name)
        assert result == ["Parent"]

    def test_instance_parent_not_in_id_to_name_uses_label(self):
        """Parent not in id_to_name falls back to sanitized label."""
        orig = GraphNode(id="orig", data=NodeData(label="orig"))
        parent = GraphNode(id="p", data=NodeData(label="My Parent"))
        inst = GraphNode(
            id="inst",
            data=NodeData(label="inst", config={"instanceOf": "orig"}),
        )
        node_map = {"orig": orig, "p": parent, "inst": inst}
        all_parents = {"orig": ["p"]}
        id_to_name: dict[str, str] = {}
        result = resolve_orig_source_names(inst, node_map, all_parents, id_to_name)
        assert result == ["My_Parent"]

    def test_instance_parent_not_in_node_map(self):
        """Parent ID not in node_map — uses raw ID as fallback."""
        orig = GraphNode(id="orig", data=NodeData(label="orig"))
        inst = GraphNode(
            id="inst",
            data=NodeData(label="inst", config={"instanceOf": "orig"}),
        )
        node_map = {"orig": orig, "inst": inst}
        all_parents = {"orig": ["ghost"]}
        id_to_name: dict[str, str] = {}
        result = resolve_orig_source_names(inst, node_map, all_parents, id_to_name)
        assert result == ["ghost"]


# ===========================================================================
# _resolve_sink_path
# ===========================================================================


class TestResolveSinkPath:
    def test_windows_backslash_not_prefixed(self):
        result = _resolve_sink_path("dir\\file", "parquet")
        assert not result.startswith("outputs/")
        assert result.endswith(".parquet")

    def test_multiple_extensions(self):
        result = _resolve_sink_path("archive.tar.gz", "parquet")
        assert result == "outputs/archive.tar.gz.parquet"

    def test_empty_path_gets_outputs_prefix(self):
        result = _resolve_sink_path("", "parquet")
        assert result.startswith("outputs/")
        assert result.endswith(".parquet")

    def test_forward_slash_not_prefixed(self):
        result = _resolve_sink_path("my/data", "csv")
        assert not result.startswith("outputs/")
        assert result.endswith(".csv")

    def test_bare_name_gets_prefix_and_extension(self):
        result = _resolve_sink_path("results", "parquet")
        assert result == "outputs/results.parquet"

    def test_already_has_extension(self):
        result = _resolve_sink_path("results.parquet", "parquet")
        assert result == "outputs/results.parquet"
