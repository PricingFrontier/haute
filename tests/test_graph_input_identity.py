"""RED contracts for edge-derived pipeline input names.

The input name is an observable graph contract: it is shown to users and later
becomes the generated Python parameter.  These tests deliberately exercise the
pure derivation before executor/codegen call sites are changed.
"""

from __future__ import annotations

import pytest

import haute._graph_utils as graph_utils
from haute._editor_identities import resolve_editor_identity
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelOutputPort,
)


def _node(node_id: str, label: str, node_type: NodeType) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=label, nodeType=node_type),
    )


def _edge(
    source: str,
    target: str = "consumer",
    *,
    source_handle: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=f"e_{source}_{target}_{source_handle or 'default'}",
        source=source,
        target=target,
        sourceHandle=source_handle,
    )


def test_api_input_edge_uses_raw_frame_label_verbatim() -> None:
    source = _node("request", "API Request", NodeType.API_INPUT)
    # Schema validation rejects non-ASCII labels separately.  This pure helper
    # must still preserve the persisted edge identity rather than sanitising it.
    edge = _edge("request", source_handle="café")

    assert graph_utils.edge_input_name(edge, source) == "café"


def test_api_input_frame_name_can_equal_an_ordinary_sanitised_label() -> None:
    """A mixed-source collision is reported instead of secretly rewritten."""
    api_source = _node("request", "API Request", NodeType.API_INPUT)
    ordinary_source = _node("quotes_node", "quote-id", NodeType.POLARS)
    api_edge = _edge("request", source_handle="quote_id")
    ordinary_edge = _edge("quotes_node")

    names = [
        graph_utils.edge_input_name(api_edge, api_source),
        graph_utils.edge_input_name(ordinary_edge, ordinary_source),
    ]

    assert names == ["quote_id", "quote_id"]
    assert graph_utils.duplicate_input_names(names) == ["quote_id"]


def test_ordinary_edge_uses_sanitised_source_label_not_its_handle() -> None:
    source = _node("claims", "Driver Claims-Step", NodeType.POLARS)
    edge = _edge("claims", source_handle="must_not_become_the_input_name")

    assert graph_utils.edge_input_name(edge, source) == "Driver_Claims_Step"


def test_edge_derived_names_preserve_incoming_edge_order() -> None:
    api_source = _node("request", "API Request", NodeType.API_INPUT)
    ordinary_source = _node("rating", "Rating Step", NodeType.RATING_STEP)
    incoming_edges = [
        _edge("request", source_handle="drivers"),
        _edge("rating"),
        _edge("request", source_handle="quotes"),
    ]
    source_nodes = {
        "request": api_source,
        "rating": ordinary_source,
    }

    names = [
        graph_utils.edge_input_name(edge, source_nodes[edge.source]) for edge in incoming_edges
    ]

    assert names == ["drivers", "Rating_Step", "quotes"]


def test_flattened_submodel_output_uses_child_node_label() -> None:
    """A flattened boundary edge names the child frame, not its old container."""
    child_source = _node("frequency_child", "Frequency Model Child", NodeType.POLARS)
    # flatten_graph rewires ``submodel__frequency`` -> consumer to this child source.
    flattened_edge = _edge("frequency_child")

    assert graph_utils.edge_input_name(flattened_edge, child_source) == "Frequency_Model_Child"


def test_edge_input_name_does_not_mutate_its_inputs() -> None:
    source = _node("request", "API Request", NodeType.API_INPUT)
    edge = _edge("request", source_handle="quotes")
    source_before = source.model_copy(deep=True)
    edge_before = edge.model_copy(deep=True)

    first = graph_utils.edge_input_name(edge, source)
    second = graph_utils.edge_input_name(edge, source)

    assert first == second == "quotes"
    assert source == source_before
    assert edge == edge_before


def test_submodel_output_identity_uses_occurrence_name_not_structural_handle() -> None:
    assert (
        graph_utils.executable_input_name(
            node_type=NodeType.SUBMODEL,
            label="Pricing",
            source_handle="out__written_premium",
            alias="pricing_secondary",
            output_port_count=1,
        )
        == "pricing_secondary"
    )

    with pytest.raises(ValueError, match=r"out__<name>"):
        graph_utils.executable_input_name(
            node_type=NodeType.SUBMODEL,
            label="Pricing",
            source_handle="written_premium",
            alias="pricing_secondary",
            output_port_count=1,
        )


def test_submodel_edge_resolves_occurrence_name_from_definition() -> None:
    source = GraphNode(
        id="pricing_instance",
        data=NodeData(
            label="Pricing",
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": "pricing_definition", "alias": "pricing_secondary"},
        ),
    )
    edge = _edge("pricing_instance", source_handle="out__written_premium")
    definition = SubmodelDefinition(
        definitionId="pricing_definition",
        file="modules/pricing.py",
        graph=PipelineGraph(nodes=[_node("source", "Source", NodeType.POLARS)]),
        inputPorts=[],
        outputPorts=[
            SubmodelOutputPort(
                name="written_premium",
                source=SubmodelEndpoint(nodeId="source"),
            )
        ],
    )

    assert (
        graph_utils.edge_input_name(
            edge,
            source,
            submodels={"pricing_definition": definition},
        )
        == "pricing_secondary"
    )


def test_submodel_edge_requires_its_definition_for_public_label_resolution() -> None:
    source = GraphNode(
        id="pricing_instance",
        data=NodeData(
            label="Pricing",
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": "pricing_definition", "alias": "pricing_secondary"},
        ),
    )

    with pytest.raises(ValueError, match="definition registry"):
        graph_utils.edge_input_name(
            _edge("pricing_instance", source_handle="out__written_premium"),
            source,
        )


def test_editor_identity_resolver_for_boundary_handles() -> None:
    output = resolve_editor_identity(
        node_type=NodeType.SUBMODEL,
        label="Pricing",
        source_handles=("out__written_premium",),
        alias="pricing_secondary",
    )
    public_input = resolve_editor_identity(
        node_type=NodeType.SUBMODEL_PORT,
        label="Submodel inputs",
        source_handles=("policy_input",),
    )

    assert output.source_handle_input_names == {"out__written_premium": "pricing_secondary"}
    assert public_input.source_handle_input_names == {"policy_input": "policy_input"}


def test_editor_identity_resolver_owns_keyword_unicode_and_config_paths() -> None:
    keyword_identity = resolve_editor_identity(
        node_type=NodeType.API_INPUT,
        label="class",
        source_handles=("quotes", "vehicles"),
    )
    unicode_identity = resolve_editor_identity(
        node_type=NodeType.POLARS,
        label="café",
    )

    assert keyword_identity.function_name == "node_class"
    assert keyword_identity.config_reference == "config/quote_input/node_class.json"
    assert keyword_identity.default_input_name is None
    assert keyword_identity.source_handle_input_names == {
        "quotes": "quotes",
        "vehicles": "vehicles",
    }
    assert unicode_identity.function_name == "caf_xe9_"
    assert unicode_identity.default_input_name == "caf_xe9_"
    assert unicode_identity.config_reference is None


def test_editor_identity_resolver_rejects_duplicate_handles_without_mutation() -> None:
    handles = ["quotes", "quotes"]
    before = handles.copy()

    with pytest.raises(ValueError, match="unique"):
        resolve_editor_identity(
            node_type=NodeType.API_INPUT,
            label="Request",
            source_handles=handles,
        )

    assert handles == before


def test_duplicate_input_names_returns_empty_for_unique_names() -> None:
    assert graph_utils.duplicate_input_names([]) == []
    assert graph_utils.duplicate_input_names(["quotes", "drivers", "claims"]) == []


def test_duplicate_input_names_orders_by_first_duplicate_occurrence() -> None:
    # Initial occurrences are alpha then beta, but beta becomes a duplicate first.
    names = ["alpha", "beta", "beta", "alpha"]

    assert graph_utils.duplicate_input_names(names) == ["beta", "alpha"]


def test_duplicate_input_names_reports_each_repeated_name_once() -> None:
    names = ["quotes", "drivers", "drivers", "quotes", "drivers", "quotes"]

    assert graph_utils.duplicate_input_names(names) == ["drivers", "quotes"]


def test_duplicate_input_names_does_not_mutate_its_input() -> None:
    names = ["quotes", "drivers", "drivers", "quotes", "drivers"]
    names_before = names.copy()

    first = graph_utils.duplicate_input_names(names)
    second = graph_utils.duplicate_input_names(names)

    assert first == second == ["drivers", "quotes"]
    assert names == names_before
