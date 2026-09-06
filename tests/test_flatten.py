"""Edge-case coverage for canonical submodel flattening."""

from __future__ import annotations

import pytest

from haute._flatten import flatten_graph
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ParseError


def _node(
    node_id: str,
    *,
    node_type: NodeType = NodeType.POLARS,
    config: dict[str, object] | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=node_id,
            nodeType=node_type,
            config=config or {},
        ),
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=edge_id,
        source=source,
        target=target,
        sourceHandle=source_handle,
        targetHandle=target_handle,
    )


def _definition_graph(
    *,
    duplicate_internal_edge: bool = False,
    preamble: str | None = None,
    preserved_blocks: list[str] | None = None,
) -> PipelineGraph:
    edges = [_edge("internal", "child_input", "child_output")]
    if duplicate_internal_edge:
        edges.append(_edge("internal_duplicate", "child_input", "child_output"))
    return PipelineGraph(
        nodes=[_node("child_input"), _node("child_output")],
        edges=edges,
        preamble=preamble,
        preserved_blocks=preserved_blocks or [],
    )


def _definition_payload(
    *,
    graph: PipelineGraph | None = None,
) -> dict[str, object]:
    return {
        "definitionId": "definition_pricing",
        "file": "modules/pricing.py",
        "graph": graph or _definition_graph(),
        "inputPorts": [
            {
                "name": "records",
                "targets": [{"nodeId": "child_input", "handleId": None}],
            }
        ],
        "outputPorts": [
            {
                "name": "priced",
                "source": {"nodeId": "child_output", "handleId": None},
            }
        ],
    }


def _occurrence(
    name: str = "pricing",
    *,
    instance_of: str | None = None,
) -> GraphNode:
    config: dict[str, object] = {"definitionId": "definition_pricing", "alias": name}
    if instance_of is not None:
        config["instanceOf"] = instance_of
    return GraphNode(
        id=name,
        data=NodeData(
            label=name,
            nodeType=NodeType.SUBMODEL,
            config=config,
        ),
    )


def _bound_graph(
    *,
    input_handle: str | None = "in__records",
    output_handle: str | None = "out__priced",
    definition: dict[str, object] | None = None,
) -> PipelineGraph:
    return PipelineGraph.model_validate(
        {
            "pipeline_name": "main",
            "pipeline_description": "Canonical flatten fixture",
            "nodes": [
                _node("upstream"),
                _occurrence(),
                _node("downstream", node_type=NodeType.OUTPUT),
            ],
            "edges": [
                _edge(
                    "incoming",
                    "upstream",
                    "pricing",
                    target_handle=input_handle,
                ),
                _edge(
                    "outgoing",
                    "pricing",
                    "downstream",
                    source_handle=output_handle,
                ),
            ],
            "submodels": {
                "definition_pricing": definition or _definition_payload(),
            },
        }
    )


def test_no_occurrences_returns_the_same_graph() -> None:
    graph = PipelineGraph(nodes=[_node("ordinary")])

    assert flatten_graph(graph) is graph


def test_unknown_target_instance_fails_loudly() -> None:
    graph = _bound_graph()

    with pytest.raises(ParseError, match="instance not found"):
        flatten_graph(graph, target_instance_id="instance_missing")


@pytest.mark.parametrize("handle", [None, "records", "in__", "wrong__records"])
def test_malformed_public_input_handles_fail_loudly(handle: str | None) -> None:
    graph = _bound_graph(input_handle=handle)

    with pytest.raises(ParseError, match="public-port handle|empty public port"):
        flatten_graph(graph)


@pytest.mark.parametrize("handle", [None, "priced", "out__", "wrong__priced"])
def test_malformed_public_output_handles_fail_loudly(handle: str | None) -> None:
    graph = _bound_graph(output_handle=handle)

    with pytest.raises(ParseError, match="public-port handle|empty public port"):
        flatten_graph(graph)


def test_internal_edges_are_preserved_with_qualified_endpoints() -> None:
    result = flatten_graph(_bound_graph())

    expected = (
        qualified_runtime_node_id("pricing", "child_input"),
        qualified_runtime_node_id("pricing", "child_output"),
    )
    assert expected in {(edge.source, edge.target) for edge in result.edges}


def test_duplicate_internal_edges_are_deduplicated_by_full_identity() -> None:
    definition = _definition_payload(
        graph=_definition_graph(duplicate_internal_edge=True),
    )

    result = flatten_graph(_bound_graph(definition=definition))

    source = qualified_runtime_node_id("pricing", "child_input")
    target = qualified_runtime_node_id("pricing", "child_output")
    matching = [edge for edge in result.edges if edge.source == source and edge.target == target]
    assert len(matching) == 1


def test_empty_unbound_definition_dissolves_cleanly() -> None:
    graph = PipelineGraph.model_validate(
        {
            "nodes": [_node("ordinary"), _occurrence()],
            "submodels": {
                "definition_pricing": {
                    "definitionId": "definition_pricing",
                    "file": "modules/pricing.py",
                    "graph": {"nodes": [], "edges": []},
                    "inputPorts": [],
                    "outputPorts": [],
                }
            },
        }
    )

    result = flatten_graph(graph)

    assert set(result.node_map) == {"ordinary"}
    assert result.submodels is None


def test_graph_metadata_is_preserved() -> None:
    graph = _bound_graph().model_copy(
        update={
            "source_file": "rating/main.py",
            "source_revision": "sha256:" + "a" * 64,
        }
    )

    result = flatten_graph(graph)

    assert result.pipeline_name == "main"
    assert result.pipeline_description == "Canonical flatten fixture"
    assert result.source_file == "rating/main.py"
    assert result.source_revision == "sha256:" + "a" * 64


def test_definition_support_code_is_merged_once_for_repeated_occurrences() -> None:
    child_graph = _definition_graph(
        preamble="CHILD_HELPER = 2",
        preserved_blocks=["SHARED = 1", "CHILD_KEEP = 3"],
    )
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _occurrence(),
                _occurrence(
                    "pricing_2",
                    instance_of="pricing",
                ),
            ],
            "preamble": "PARENT_HELPER = 1",
            "preserved_blocks": ["SHARED = 1"],
            "submodels": {
                "definition_pricing": _definition_payload(graph=child_graph),
            },
        }
    )

    result = flatten_graph(graph)

    assert result.preamble == "PARENT_HELPER = 1\n\nCHILD_HELPER = 2"
    assert result.preserved_blocks == ["SHARED = 1", "CHILD_KEEP = 3"]
