"""Contract tests for one submodel definition with many graph instances."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from haute._flatten import flatten_graph
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._parser_submodels import extract_submodel_registrations, parse_submodel_source
from haute._polars_io_registry import validate_data_input_config
from haute._submodel_instances import (
    qualified_runtime_node_id,
    rewrite_boundary_input_names,
    validate_submodel_instances,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelInstanceConfig,
    SubmodelOutputPort,
)
from haute.codegen import graph_to_code_multi
from haute.errors import ParseError
from haute.parser import parse_pipeline_source
from haute.routes._submodel_ops import create_submodel_graph


def _node(
    node_id: str,
    node_type: NodeType = NodeType.POLARS,
    *,
    config: dict[str, object] | None = None,
    label: str | None = None,
    x: float = 0,
    y: float = 0,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="submodel" if node_type == NodeType.SUBMODEL else "pipelineNode",
        position={"x": x, "y": y},
        data=NodeData(
            label=label or node_id,
            nodeType=node_type,
            config=dict(config or {}),
        ),
    )


def _definition(
    *,
    graph: PipelineGraph | None = None,
    input_ports: list[SubmodelInputPort] | None = None,
    output_ports: list[SubmodelOutputPort] | None = None,
) -> SubmodelDefinition:
    child_graph = graph or PipelineGraph(
        nodes=[
            _node("local_input"),
            _node(
                "local_output",
                config={"instanceOf": "local_input"},
            ),
        ],
        edges=[GraphEdge(id="local_edge", source="local_input", target="local_output")],
    )
    return SubmodelDefinition(
        definition_id="definition_scoring",
        file="modules/scoring.py",
        graph=child_graph,
        input_ports=input_ports
        if input_ports is not None
        else [
            SubmodelInputPort(
                port_id="policy",
                label="Policy data",
                targets=[SubmodelEndpoint(node_id="local_input", handle_id="frame")],
            )
        ],
        output_ports=output_ports
        if output_ports is not None
        else [
            SubmodelOutputPort(
                port_id="premium",
                label="Written premium",
                source=SubmodelEndpoint(node_id="local_output", handle_id="scored"),
            )
        ],
    )


def _instance(
    instance_id: str,
    alias: str,
    *,
    instance_of: str | None = None,
    definition_id: str = "definition_scoring",
    x: float = 0,
    y: float = 0,
) -> GraphNode:
    config: dict[str, object] = {"definitionId": definition_id, "alias": alias}
    if instance_of is not None:
        config["instanceOf"] = instance_of
    return _node(
        instance_id,
        NodeType.SUBMODEL,
        config=config,
        label=alias.replace("_", " ").title(),
        x=x,
        y=y,
    )


def test_definition_rejects_duplicate_public_port_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate.*port"):
        _definition(
            input_ports=[
                SubmodelInputPort(
                    port_id="shared",
                    label="Input",
                    targets=[SubmodelEndpoint(node_id="local_input")],
                )
            ],
            output_ports=[
                SubmodelOutputPort(
                    port_id="shared",
                    label="Output",
                    source=SubmodelEndpoint(node_id="local_output"),
                )
            ],
        )


def test_definition_rejects_missing_internal_endpoint() -> None:
    with pytest.raises(ValidationError, match="missing_child"):
        _definition(
            input_ports=[
                SubmodelInputPort(
                    port_id="policy",
                    label="Policy",
                    targets=[SubmodelEndpoint(node_id="missing_child")],
                )
            ]
        )


def test_instance_validation_rejects_unknown_definition_and_duplicate_alias() -> None:
    unknown = PipelineGraph(nodes=[_instance("instance_a", "pricing")], edges=[], submodels={})
    with pytest.raises(ParseError, match="definition"):
        validate_submodel_instances(unknown)

    duplicate_alias = PipelineGraph(
        nodes=[_instance("instance_a", "pricing"), _instance("instance_b", "pricing")],
        edges=[],
        submodels={"definition_scoring": _definition()},
    )
    with pytest.raises(ParseError, match="alias"):
        validate_submodel_instances(duplicate_alias)


def test_instance_alias_cannot_shadow_any_parent_node_id() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("scoring"),
            _instance("instance_a", "scoring"),
        ],
        edges=[],
        submodels={"definition_scoring": _definition()},
    )

    with pytest.raises(ParseError, match="alias.*node id"):
        validate_submodel_instances(graph)


def test_only_submodel_nodes_are_resolved_as_occurrences() -> None:
    ordinary = _node(
        "ordinary",
        config={
            "definitionId": "definition_scoring",
            "alias": "looks_like_an_occurrence",
        },
    )
    graph = PipelineGraph(
        nodes=[ordinary],
        edges=[],
        submodels={},
    )

    validate_submodel_instances(graph)
    assert flatten_graph(graph) is graph


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"alias": "scoring"},
        {"definitionId": 123, "alias": "scoring"},
    ],
)
def test_submodel_occurrence_identity_is_always_required(
    config: dict[str, object],
) -> None:
    graph = PipelineGraph(
        nodes=[
            _node(
                "submodel__definition_scoring",
                NodeType.SUBMODEL,
                config=config,
            )
        ],
        edges=[],
        submodels={"definition_scoring": _definition()},
    )

    with pytest.raises(ParseError, match="identity|config|definitionId"):
        validate_submodel_instances(graph)


def test_definition_registry_never_infers_definition_identity() -> None:
    with pytest.raises(ValidationError, match="definitionId"):
        PipelineGraph(
            submodels={
                "definition_scoring": {
                    "file": "modules/scoring.py",
                    "graph": {"nodes": [], "edges": []},
                    "inputPorts": [],
                    "outputPorts": [],
                }
            }
        )


def test_submodel_instance_config_preserves_owner_reference_aliases() -> None:
    config = SubmodelInstanceConfig.model_validate(
        {
            "definitionId": "definition_scoring",
            "alias": "scoring_copy",
            "instanceOf": "instance_owner",
        }
    )

    assert config.instance_of == "instance_owner"
    assert config.model_dump(by_alias=True)["instanceOf"] == "instance_owner"
    owner = SubmodelInstanceConfig(definitionId="definition_scoring", alias="scoring")
    assert owner.model_dump(by_alias=True) == {
        "definitionId": "definition_scoring",
        "alias": "scoring",
    }


@pytest.mark.parametrize(
    ("nodes", "definitions"),
    [
        (
            [
                _instance("instance_owner", "scoring"),
                _instance("instance_copy", "scoring_copy", instance_of="missing"),
            ],
            {"definition_scoring": _definition()},
        ),
        (
            [
                _instance("instance_owner", "scoring", definition_id="definition_other"),
                _instance("instance_copy", "scoring_copy", instance_of="instance_owner"),
            ],
            {
                "definition_scoring": _definition(),
                "definition_other": _definition().model_copy(
                    update={"definition_id": "definition_other"}
                ),
            },
        ),
        (
            [
                _instance("instance_owner", "scoring"),
                _instance("instance_copy", "scoring_copy", instance_of="instance_owner"),
                _instance("instance_chained", "scoring_chained", instance_of="instance_copy"),
            ],
            {"definition_scoring": _definition()},
        ),
        (
            [_instance("instance_owner", "scoring", instance_of="instance_owner")],
            None,
        ),
        (
            [
                _instance("instance_a", "scoring_a"),
                _instance("instance_b", "scoring_b"),
            ],
            None,
        ),
    ],
)
def test_instance_validation_rejects_invalid_owner_topology(
    nodes: list[GraphNode],
    definitions: dict[str, SubmodelDefinition] | None,
) -> None:
    graph = PipelineGraph(
        nodes=nodes,
        edges=[],
        submodels=definitions or {"definition_scoring": _definition()},
    )

    with pytest.raises(ParseError, match="owner|instanceOf"):
        validate_submodel_instances(graph)


def test_targeted_flatten_rejects_dissolving_owner_with_remaining_copy() -> None:
    graph = PipelineGraph(
        nodes=[
            _instance("instance_owner", "scoring"),
            _instance("instance_copy", "scoring_copy", instance_of="instance_owner"),
        ],
        edges=[],
        submodels={"definition_scoring": _definition()},
    )

    with pytest.raises(ParseError, match="owner|instanceOf"):
        flatten_graph(graph, target_instance_id="instance_owner")


def test_staged_dissolve_merges_definition_preamble_once() -> None:
    child_graph = PipelineGraph(
        nodes=[
            _node("local_input"),
            _node("local_output", config={"instanceOf": "local_input"}),
        ],
        edges=[GraphEdge(id="local_edge", source="local_input", target="local_output")],
        preamble="import child_helpers",
    )
    graph = PipelineGraph(
        nodes=[
            _instance("instance_owner", "scoring"),
            _instance("instance_copy", "scoring_copy", instance_of="instance_owner"),
        ],
        edges=[],
        submodels={"definition_scoring": _definition(graph=child_graph)},
        preamble="import parent_helpers",
    )

    after_copy = flatten_graph(graph, target_instance_id="instance_copy")
    assert (after_copy.preamble or "").count("import child_helpers") == 1

    after_owner = flatten_graph(after_copy)
    assert (after_owner.preamble or "").count("import child_helpers") == 1


def test_preamble_merge_treats_indented_lines_as_distinct_from_top_level() -> None:
    child_graph = PipelineGraph(
        nodes=[
            _node("local_input"),
            _node("local_output", config={"instanceOf": "local_input"}),
        ],
        edges=[GraphEdge(id="local_edge", source="local_input", target="local_output")],
        preamble="import child_helpers",
    )
    graph = PipelineGraph(
        nodes=[_instance("instance_owner", "scoring")],
        edges=[],
        submodels={"definition_scoring": _definition(graph=child_graph)},
        preamble="def load():\n    import child_helpers",
    )

    flat = flatten_graph(graph)
    lines = (flat.preamble or "").splitlines()
    assert "import child_helpers" in lines
    assert "    import child_helpers" in lines


def test_preamble_merge_never_skips_on_partial_line_overlap() -> None:
    child_graph = PipelineGraph(
        nodes=[
            _node("local_input"),
            _node("local_output", config={"instanceOf": "local_input"}),
        ],
        edges=[GraphEdge(id="local_edge", source="local_input", target="local_output")],
        preamble="import child",
    )
    graph = PipelineGraph(
        nodes=[_instance("instance_owner", "scoring")],
        edges=[],
        submodels={"definition_scoring": _definition(graph=child_graph)},
        preamble="import child_helpers",
    )

    flat = flatten_graph(graph)
    lines = [line.strip() for line in (flat.preamble or "").splitlines() if line.strip()]
    assert "import child" in lines
    assert "import child_helpers" in lines


def test_flatten_expands_two_instances_with_independent_bindings_and_references() -> None:
    definition = _definition()
    graph = PipelineGraph(
        nodes=[
            _node("upstream_a"),
            _instance("instance_a", "scoring_a", x=100, y=200),
            _node("downstream_a"),
            _node("upstream_b"),
            _instance("instance_b", "scoring_b", instance_of="instance_a", x=500, y=200),
            _node("downstream_b"),
        ],
        edges=[
            GraphEdge(
                id="a_in",
                source="upstream_a",
                target="instance_a",
                sourceHandle="batch_a",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="a_out",
                source="instance_a",
                target="downstream_a",
                sourceHandle="out__premium",
                targetHandle="left",
            ),
            GraphEdge(
                id="b_in",
                source="upstream_b",
                target="instance_b",
                sourceHandle="batch_b",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="b_out",
                source="instance_b",
                target="downstream_b",
                sourceHandle="out__premium",
                targetHandle="right",
            ),
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    a_input = qualified_runtime_node_id("instance_a", "local_input")
    a_output = qualified_runtime_node_id("instance_a", "local_output")
    b_input = qualified_runtime_node_id("instance_b", "local_input")
    b_output = qualified_runtime_node_id("instance_b", "local_output")

    assert {a_input, a_output, b_input, b_output} <= {node.id for node in result.nodes}
    assert len({a_input, a_output, b_input, b_output}) == 4
    assert ("upstream_a", a_input, "batch_a", "frame") in {
        (edge.source, edge.target, edge.sourceHandle, edge.targetHandle) for edge in result.edges
    }
    assert (a_output, "downstream_a", "scored", "left") in {
        (edge.source, edge.target, edge.sourceHandle, edge.targetHandle) for edge in result.edges
    }
    assert ("upstream_b", b_input, "batch_b", "frame") in {
        (edge.source, edge.target, edge.sourceHandle, edge.targetHandle) for edge in result.edges
    }
    assert (b_output, "downstream_b", "scored", "right") in {
        (edge.source, edge.target, edge.sourceHandle, edge.targetHandle) for edge in result.edges
    }

    a_output_node = result.node_map[a_output]
    b_output_node = result.node_map[b_output]
    assert a_output_node.data.config["instanceOf"] == a_input
    assert b_output_node.data.config["instanceOf"] == b_input
    assert "_submodelOrigin" not in a_output_node.data.config
    assert "_submodelOrigin" not in b_output_node.data.config
    assert result.submodels is None

    # Flattening is pure; the hierarchical input graph is untouched.
    assert {node.id for node in graph.nodes} >= {"instance_a", "instance_b"}
    assert graph.submodels == {"definition_scoring": definition}


def test_flatten_preserves_strict_data_input_domain_config() -> None:
    config: dict[str, object] = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "nb_batch.parquet",
        "arguments": {},
        "contract": "opaque",
    }
    definition = _definition(
        graph=PipelineGraph(
            nodes=[_node("nb_batch", NodeType.DATA_INPUT, config=config)],
            edges=[],
        ),
        input_ports=[],
        output_ports=[],
    )
    graph = PipelineGraph(
        nodes=[_instance("instance_inputs", "inputs")],
        edges=[],
        submodels={"definition_scoring": definition},
    )

    flattened = flatten_graph(graph)
    runtime_id = qualified_runtime_node_id("instance_inputs", "nb_batch")
    flattened_config = flattened.node_map[runtime_id].data.config

    assert flattened_config == config
    assert validate_data_input_config(flattened_config) == {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "nb_batch.parquet",
        "arguments": {},
        "contract": "opaque",
    }


def test_public_input_port_can_fan_out_to_ordered_targets() -> None:
    child = PipelineGraph(
        nodes=[_node("left"), _node("right")],
        edges=[],
    )
    definition = _definition(
        graph=child,
        input_ports=[
            SubmodelInputPort(
                port_id="policy",
                label="Policy",
                targets=[
                    SubmodelEndpoint(node_id="left", handle_id="left_frame"),
                    SubmodelEndpoint(node_id="right", handle_id="right_frame"),
                ],
            )
        ],
        output_ports=[],
    )
    graph = PipelineGraph(
        nodes=[_node("source"), _instance("instance_a", "scoring")],
        edges=[
            GraphEdge(
                id="in",
                source="source",
                target="instance_a",
                targetHandle="in__policy",
            )
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    assert [
        (edge.target, edge.targetHandle) for edge in result.edges if edge.source == "source"
    ] == [
        (qualified_runtime_node_id("instance_a", "left"), "left_frame"),
        (qualified_runtime_node_id("instance_a", "right"), "right_frame"),
    ]


def test_schema_declared_instance_reference_is_qualified() -> None:
    child = PipelineGraph(
        nodes=[
            _node("referenced"),
            _node("consumer", config={"instanceOf": "referenced"}),
        ],
        edges=[],
    )
    definition = _definition(graph=child, input_ports=[], output_ports=[])
    graph = PipelineGraph(
        nodes=[_instance("instance_a", "scoring")],
        edges=[],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    consumer = result.node_map[qualified_runtime_node_id("instance_a", "consumer")]
    assert consumer.data.config["instanceOf"] == qualified_runtime_node_id(
        "instance_a",
        "referenced",
    )


@pytest.mark.parametrize(
    ("node_type", "field"),
    [
        (NodeType.OPTIMISER, "data_input"),
        (NodeType.OPTIMISER, "banding_source"),
        (NodeType.OPTIMISER_APPLY, "ratebook_input"),
    ],
)
def test_exact_input_selectors_are_not_rewritten_as_node_ids(
    node_type: NodeType,
    field: str,
) -> None:
    child = PipelineGraph(
        nodes=[
            _node("referenced"),
            _node("consumer", node_type, config={field: "referenced"}),
        ],
        edges=[GraphEdge(id="selected", source="referenced", target="consumer")],
    )
    definition = _definition(graph=child, input_ports=[], output_ports=[])
    graph = PipelineGraph(
        nodes=[_instance("instance_a", "scoring")],
        edges=[],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    consumer = result.node_map[qualified_runtime_node_id("instance_a", "consumer")]
    assert consumer.data.config[field] == "referenced"


def test_flatten_rewrites_public_input_label_to_exact_external_frame_name() -> None:
    child = PipelineGraph(
        nodes=[
            _node(
                "consumer",
                NodeType.OPTIMISER,
                config={"data_input": "Quote_records"},
            )
        ],
        edges=[],
    )
    definition = _definition(
        graph=child,
        input_ports=[
            SubmodelInputPort(
                port_id="quotes",
                label="Quote records",
                targets=[SubmodelEndpoint(node_id="consumer")],
            )
        ],
        output_ports=[],
    )
    graph = PipelineGraph(
        nodes=[
            _node("request", NodeType.API_INPUT, label="Quote Input"),
            _instance("instance_a", "scoring"),
        ],
        edges=[
            GraphEdge(
                id="input",
                source="request",
                target="instance_a",
                sourceHandle="quote_info",
                targetHandle="in__quotes",
            )
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    consumer = result.node_map[qualified_runtime_node_id("instance_a", "consumer")]
    assert consumer.data.config["data_input"] == "quote_info"


def test_flatten_rewrites_public_output_label_to_exact_internal_source_name() -> None:
    child = PipelineGraph(nodes=[_node("output", label="Internal Output")], edges=[])
    definition = _definition(
        graph=child,
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(
                port_id="results",
                label="Published results",
                source=SubmodelEndpoint(node_id="output"),
            )
        ],
    )
    graph = PipelineGraph(
        nodes=[
            _instance("instance_a", "score"),
            _node(
                "consumer",
                NodeType.OPTIMISER_APPLY,
                config={"ratebook_input": "Published_results"},
            ),
        ],
        edges=[
            GraphEdge(
                id="output",
                source="instance_a",
                target="consumer",
                sourceHandle="out__results",
            )
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    assert result.node_map["consumer"].data.config["ratebook_input"] == "Internal_Output"


def test_flatten_rewrites_public_output_source_port_in_multi_frame_mapping() -> None:
    child = PipelineGraph(nodes=[_node("output", label="Internal Output")], edges=[])
    definition = _definition(
        graph=child,
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(
                port_id="results",
                label="Published results",
                source=SubmodelEndpoint(node_id="output"),
            )
        ],
    )
    graph = PipelineGraph(
        nodes=[
            _node("other", label="Unrelated source"),
            _instance("instance_a", "score"),
            _node(
                "response",
                NodeType.OUTPUT,
                config={
                    "outputMapping": [
                        {
                            "source_port": "Published_results",
                            "source_column": "premium",
                            "output_path": "$.premium",
                            "enabled": True,
                        },
                        {
                            "source_port": "Unrelated_source",
                            "source_column": "reference",
                            "output_path": "$.reference",
                            "enabled": True,
                        },
                    ]
                },
            ),
        ],
        edges=[
            GraphEdge(
                id="submodel_output",
                source="instance_a",
                target="response",
                sourceHandle="out__results",
            ),
            GraphEdge(id="other_output", source="other", target="response"),
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    assert result.node_map["response"].data.config["outputMapping"] == [
        {
            "source_port": "Internal_Output",
            "source_column": "premium",
            "output_path": "$.premium",
            "enabled": True,
        },
        {
            "source_port": "Unrelated_source",
            "source_column": "reference",
            "output_path": "$.reference",
            "enabled": True,
        },
    ]


@pytest.mark.parametrize(
    ("output_mapping", "entry_index"),
    [
        pytest.param({"source_port": "Published_results"}, None, id="non-list-mapping"),
        pytest.param(["not-an-object"], 0, id="non-object-entry"),
        pytest.param([{}], 0, id="missing-source-port"),
        pytest.param([{"source_port": ""}], 0, id="empty-source-port"),
        pytest.param([{"source_port": 1}], 0, id="non-string-source-port"),
    ],
)
def test_rewrite_boundary_input_names_rejects_malformed_output_mapping(
    output_mapping: object,
    entry_index: int | None,
) -> None:
    node = _node(
        "response",
        NodeType.OUTPUT,
        config={"outputMapping": output_mapping},
    )

    with pytest.raises(ParseError) as exc_info:
        rewrite_boundary_input_names([node], {"response": {"Published_results": "Internal_Output"}})

    assert exc_info.value.context["node_id"] == "response"
    if entry_index is not None:
        assert exc_info.value.context["entry_index"] == entry_index


def test_flatten_preserves_public_input_label_for_ordinary_polars_code() -> None:
    child = PipelineGraph(
        nodes=[
            _node(
                "consumer",
                NodeType.POLARS,
                label="Child transform",
                config={"code": "df = Policy_records"},
            )
        ],
        edges=[],
    )
    definition = _definition(
        graph=child,
        input_ports=[
            SubmodelInputPort(
                port_id="policy",
                label="Policy records",
                targets=[SubmodelEndpoint(node_id="consumer")],
            )
        ],
        output_ports=[],
    )
    graph = PipelineGraph(
        nodes=[
            _node("source", label="External policies"),
            _instance("instance_a", "scoring"),
        ],
        edges=[
            GraphEdge(
                id="input",
                source="source",
                target="instance_a",
                targetHandle="in__policy",
            )
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)
    consumer = result.node_map[qualified_runtime_node_id("instance_a", "consumer")]

    assert consumer.data.config["inputMapping"] == {"Policy_records": "External_policies"}
    generated = graph_to_code_multi(result, pipeline_name="main")["main.py"]
    assert "def Child_transform(Policy_records: pl.LazyFrame)" in generated
    assert "df = Policy_records" in generated


def test_flatten_preserves_public_output_label_for_ordinary_polars_code() -> None:
    child = PipelineGraph(nodes=[_node("output", label="Internal Output")], edges=[])
    definition = _definition(
        graph=child,
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(
                port_id="results",
                label="Published results",
                source=SubmodelEndpoint(node_id="output"),
            )
        ],
    )
    graph = PipelineGraph(
        nodes=[
            _instance("instance_a", "score"),
            _node(
                "consumer",
                NodeType.POLARS,
                label="Consumer",
                config={"code": "df = Published_results"},
            ),
        ],
        edges=[
            GraphEdge(
                id="output",
                source="instance_a",
                target="consumer",
                sourceHandle="out__results",
            )
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)

    assert result.node_map["consumer"].data.config["inputMapping"] == {
        "Published_results": "Internal_Output"
    }
    generated = graph_to_code_multi(result, pipeline_name="main")["main.py"]
    assert "def Consumer(Published_results: pl.LazyFrame)" in generated
    assert "df = Published_results" in generated


def test_flatten_preserves_public_input_label_for_polars_instances() -> None:
    child = PipelineGraph(
        nodes=[
            _node(
                "original",
                NodeType.POLARS,
                label="Original",
                config={"code": "df = Published_input"},
            ),
            _node(
                "copy",
                NodeType.POLARS,
                label="Copy",
                config={
                    "instanceOf": "original",
                    "inputMapping": {"Published_input": "Published_input"},
                },
            ),
        ],
        edges=[],
    )
    definition = _definition(
        graph=child,
        input_ports=[
            SubmodelInputPort(
                port_id="published",
                label="Published input",
                targets=[
                    SubmodelEndpoint(node_id="original"),
                    SubmodelEndpoint(node_id="copy"),
                ],
            )
        ],
        output_ports=[],
    )
    graph = PipelineGraph(
        nodes=[
            _node("source", label="External source"),
            _instance("instance_a", "scoring"),
        ],
        edges=[
            GraphEdge(
                id="input",
                source="source",
                target="instance_a",
                targetHandle="in__published",
            )
        ],
        submodels={"definition_scoring": definition},
    )

    flattened = flatten_graph(graph)
    original = flattened.node_map[qualified_runtime_node_id("instance_a", "original")]
    copy = flattened.node_map[qualified_runtime_node_id("instance_a", "copy")]

    assert original.data.config["inputMapping"] == {"Published_input": "External_source"}
    assert copy.data.config["inputMapping"] == {"Published_input": "External_source"}
    generated = graph_to_code_multi(flattened, pipeline_name="main")["main.py"]
    assert "def Original(Published_input: pl.LazyFrame)" in generated
    assert "def Copy(External_source: pl.LazyFrame)" in generated
    assert "return Original(Published_input=External_source)" in generated


def test_stale_schema_declared_node_reference_fails_loudly() -> None:
    child = PipelineGraph(
        nodes=[_node("consumer", config={"instanceOf": "missing"})],
        edges=[],
    )
    definition = _definition(graph=child, input_ports=[], output_ports=[])
    graph = PipelineGraph(
        nodes=[_instance("instance_a", "scoring")],
        edges=[],
        submodels={"definition_scoring": definition},
    )

    with pytest.raises(ParseError, match="instanceOf") as exc_info:
        flatten_graph(graph)

    assert exc_info.value.context["instance_id"] == "instance_a"
    assert exc_info.value.context["local_node_id"] == "consumer"


def test_unbound_occurrence_validates_definition_topology_from_public_interface() -> None:
    child = PipelineGraph(
        nodes=[_node("explore", NodeType.EXPLORE)],
        edges=[],
    )
    definition = _definition(
        graph=child,
        input_ports=[
            SubmodelInputPort(
                port_id="policy",
                label="Policy",
                targets=[SubmodelEndpoint(node_id="explore")],
            )
        ],
        output_ports=[],
    )
    graph = PipelineGraph(
        nodes=[_instance("instance_unbound", "scoring")],
        edges=[],
        submodels={"definition_scoring": definition},
    )

    validate_pipeline_graph_shape_contracts(graph)


def test_targeted_flatten_removes_only_one_occurrence_and_keeps_definition() -> None:
    definition = _definition()
    graph = PipelineGraph(
        nodes=[
            _instance("instance_a", "scoring_a"),
            _instance("instance_b", "scoring_b", instance_of="instance_a"),
        ],
        edges=[],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph, target_instance_id="instance_b")

    assert "instance_a" in result.node_map
    assert "instance_b" not in result.node_map
    assert qualified_runtime_node_id("instance_a", "local_input") not in result.node_map
    assert qualified_runtime_node_id("instance_b", "local_input") in result.node_map
    assert result.submodels == {"definition_scoring": definition}


def test_registration_parser_preserves_explicit_identity_and_alias() -> None:
    tree = ast.parse(
        """
pipeline.submodel(
    "modules/scoring.py",
    definition_id="definition_scoring",
    instance_id="instance_a",
    alias="scoring_a",
).submodel(
    "modules/scoring.py",
    definition_id="definition_scoring",
    instance_id="instance_b",
    alias="scoring_b",
    instance_of="instance_a",
)
"""
    )

    registrations = extract_submodel_registrations(tree)

    assert [
        (item.path, item.definition_id, item.instance_id, item.alias, item.instance_of)
        for item in registrations
    ] == [
        ("modules/scoring.py", "definition_scoring", "instance_a", "scoring_a", None),
        ("modules/scoring.py", "definition_scoring", "instance_b", "scoring_b", "instance_a"),
    ]


def test_parser_rejects_path_only_submodel_source(tmp_path: Path) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "scoring.py").write_text(
        'import haute\nsubmodel = haute.Submodel("scoring", outputs=[])\n',
        encoding="utf-8",
    )

    with pytest.raises(ParseError, match="explicit stable identity|identity"):
        parse_pipeline_source(
            'import haute\npipeline = haute.Pipeline("main")\n'
            'pipeline.submodel("modules/scoring.py")\n',
            source_file=str(tmp_path / "main.py"),
            _base_dir=tmp_path,
        )


def test_parser_resolves_repeated_definition_once(tmp_path: Path) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    child_file = modules / "scoring.py"
    child_file.write_text(
        """
import haute

submodel = haute.Submodel(
    "scoring",
    definition_id="definition_scoring",
    input_ports=[],
    output_ports=[],
)
""",
        encoding="utf-8",
    )
    parent_source = """
import haute

pipeline = haute.Pipeline("main")
pipeline.submodel(
    "modules/scoring.py",
    definition_id="definition_scoring",
    instance_id="instance_a",
    alias="scoring_a",
)
pipeline.submodel(
    "modules/scoring.py",
    definition_id="definition_scoring",
    instance_id="instance_b",
    alias="scoring_b",
    instance_of="instance_a",
)
"""

    from haute import parser as parser_module

    # Parent parsing feeds its captured child bytes directly into the source
    # parser, bypassing the file convenience wrapper so recovery can authenticate
    # exactly the bytes it parsed.
    with patch.object(
        parser_module,
        "_parse_submodel_source",
        wraps=parser_module._parse_submodel_source,
    ) as parse_child:
        graph = parse_pipeline_source(
            parent_source,
            source_file=str(tmp_path / "main.py"),
            _base_dir=tmp_path,
        )

    assert parse_child.call_count == 1
    assert set(graph.submodels or {}) == {"definition_scoring"}
    assert {node.id for node in graph.nodes if node.data.nodeType == NodeType.SUBMODEL} == {
        "instance_a",
        "instance_b",
    }


def test_codegen_emits_definition_once_and_two_stable_registrations() -> None:
    graph = PipelineGraph(
        nodes=[
            _instance("instance_a", "scoring_a"),
            _instance("instance_b", "scoring_b", instance_of="instance_a"),
        ],
        edges=[],
        pipeline_name="main",
        submodels={
            "definition_scoring": _definition(
                graph=PipelineGraph(nodes=[], edges=[]),
                input_ports=[],
                output_ports=[],
            )
        },
    )

    files = graph_to_code_multi(
        graph,
        pipeline_name="main",
        source_file="main.py",
    )

    assert set(files) == {"main.py", "modules/scoring.py"}
    assert list(files).count("modules/scoring.py") == 1
    main = files["main.py"]
    assert main.count("pipeline.submodel(") == 2
    assert 'definition_id="definition_scoring"' in main
    assert 'instance_id="instance_a"' in main
    assert 'instance_id="instance_b"' in main
    assert 'alias="scoring_a"' in main
    assert 'alias="scoring_b"' in main
    assert 'instance_of="instance_a"' in main


def test_codegen_derives_child_config_base_from_registration_depth(tmp_path: Path) -> None:
    score_config = {
        "sourceType": "run",
        "run_id": "abc123",
        "artifact_path": "model.cbm",
        "task": "regression",
        "output_column": "prediction",
    }
    config_dir = tmp_path / "config" / "model_scoring"
    config_dir.mkdir(parents=True)
    (config_dir / "score.json").write_text(json.dumps(score_config), encoding="utf-8")

    def emitted_child(file: str) -> str:
        score = _node("score", NodeType.MODEL_SCORE, config=dict(score_config))
        definition = SubmodelDefinition(
            definition_id="definition_scoring",
            file=file,
            graph=PipelineGraph(nodes=[score], edges=[]),
            input_ports=[
                SubmodelInputPort(
                    port_id="policy",
                    label="Policy data",
                    targets=[SubmodelEndpoint(node_id="score")],
                )
            ],
            output_ports=[
                SubmodelOutputPort(
                    port_id="scored",
                    label="Scored",
                    source=SubmodelEndpoint(node_id="score"),
                )
            ],
        )
        graph = PipelineGraph(
            nodes=[_instance("instance_a", "scoring")],
            edges=[],
            pipeline_name="main",
            submodels={"definition_scoring": definition},
        )
        files = graph_to_code_multi(graph, pipeline_name="main", source_file="main.py")
        return files[file]

    depth_cases = {
        "scoring.py": "parents[0]",
        "modules/scoring.py": "parents[1]",
        "modules/nested/scoring.py": "parents[2]",
        # Depth counts real path segments, not raw separators: dot and empty
        # segments never inflate how far the emitted base climbs.
        "./modules/scoring.py": "parents[1]",
        "modules//scoring.py": "parents[1]",
        "modules/./scoring.py": "parents[1]",
    }
    for file, expected_base in depth_cases.items():
        child_source = emitted_child(file)
        assert (
            f"_HAUTE_CONFIG_BASE = _HautePath(__file__).resolve().{expected_base}" in child_source
        ), file
        reparsed = parse_submodel_source(child_source, source_file=file, _base_dir=tmp_path)
        assert "_HAUTE_CONFIG_BASE" not in (reparsed.preamble or ""), file


def test_codegen_parse_round_trip_preserves_occurrences_ports_labels_and_bindings(
    tmp_path: Path,
) -> None:
    primary = _instance("instance_a", "scoring_a")
    primary.data.label = "Primary scoring"
    secondary = _instance("instance_b", "scoring_b", instance_of="instance_a")
    secondary.data.label = "Secondary scoring"
    graph = PipelineGraph(
        nodes=[
            _node("root_source", config={"code": "return pl.DataFrame()"}),
            primary,
            secondary,
            _node("root_sink"),
        ],
        edges=[
            GraphEdge(
                id="root_to_primary",
                source="root_source",
                target="instance_a",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="primary_to_secondary",
                source="instance_a",
                target="instance_b",
                sourceHandle="out__premium",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="secondary_to_sink",
                source="instance_b",
                target="root_sink",
                sourceHandle="out__premium",
            ),
        ],
        submodels={"definition_scoring": _definition()},
    )

    files = graph_to_code_multi(
        graph,
        pipeline_name="main",
        source_file="main.py",
    )
    for relative_path, source in files.items():
        output = tmp_path / relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source, encoding="utf-8")

    reparsed = parse_pipeline_source(
        files["main.py"],
        source_file=str(tmp_path / "main.py"),
        _base_dir=tmp_path,
    )

    occurrences = {
        node.id: node for node in reparsed.nodes if node.data.nodeType == NodeType.SUBMODEL
    }
    assert {
        instance_id: (
            node.data.config["definitionId"],
            node.data.config["alias"],
            node.data.label,
            node.data.config.get("instanceOf"),
        )
        for instance_id, node in occurrences.items()
    } == {
        "instance_a": ("definition_scoring", "scoring_a", "Primary scoring", None),
        "instance_b": ("definition_scoring", "scoring_b", "Secondary scoring", "instance_a"),
    }
    definition = (reparsed.submodels or {})["definition_scoring"]
    assert [port.port_id for port in definition.input_ports] == ["policy"]
    assert [port.port_id for port in definition.output_ports] == ["premium"]
    assert {
        (edge.source, edge.target, edge.sourceHandle, edge.targetHandle) for edge in reparsed.edges
    } == {
        ("root_source", "instance_a", None, "in__policy"),
        ("instance_a", "instance_b", "out__premium", "in__policy"),
        ("instance_b", "root_sink", "out__premium", None),
    }


def test_grouping_creates_one_canonical_definition_and_first_occurrence() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("source", config={"code": "return pl.DataFrame()"}),
            _node("child_a"),
            _node("child_b"),
            _node("sink"),
        ],
        edges=[
            GraphEdge(
                id="input_a",
                source="source",
                target="child_a",
                sourceHandle="policies",
                targetHandle="left",
            ),
            GraphEdge(
                id="input_b",
                source="source",
                target="child_b",
                sourceHandle="policies",
                targetHandle="right",
            ),
            GraphEdge(id="internal", source="child_a", target="child_b"),
            GraphEdge(
                id="output",
                source="child_b",
                target="sink",
                sourceHandle="priced",
            ),
        ],
    )

    result = create_submodel_graph(
        graph,
        ["child_a", "child_b"],
        "pricing",
    )

    occurrence = next(
        node for node in result.graph.nodes if node.data.nodeType == NodeType.SUBMODEL
    )
    assert occurrence.data.config == {"definitionId": "pricing", "alias": "pricing"}
    assert set(occurrence.data.config) == {"definitionId", "alias"}

    definition = (result.graph.submodels or {})["pricing"]
    assert definition.file == "modules/pricing.py"
    assert [port.port_id for port in definition.input_ports] == ["input_1"]
    assert [(target.node_id, target.handle_id) for target in definition.input_ports[0].targets] == [
        ("child_a", "left"),
        ("child_b", "right"),
    ]
    assert [port.port_id for port in definition.output_ports] == ["output_1"]
    assert definition.output_ports[0].source == SubmodelEndpoint(
        node_id="child_b",
        handle_id="priced",
    )
    assert {"input_1", "output_1"}.isdisjoint({"child_a", "child_b"})
    assert {
        (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)
        for edge in result.graph.edges
    } == {
        ("source", occurrence.id, "policies", "in__input_1"),
        (occurrence.id, "sink", "out__output_1", None),
    }
    assert len(result.graph.edges) == 2


def test_grouping_stores_local_positions_and_flatten_restores_authored_positions() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("child_a", x=100, y=200),
            _node("child_b", x=500, y=600),
        ],
        edges=[GraphEdge(id="internal", source="child_a", target="child_b")],
    )

    grouped = create_submodel_graph(
        graph,
        ["child_a", "child_b"],
        "pricing",
    )

    occurrence = next(
        node for node in grouped.graph.nodes if node.data.nodeType == NodeType.SUBMODEL
    )
    assert occurrence.position == {"x": 300.0, "y": 400.0}
    definition = (grouped.graph.submodels or {})["pricing"]
    assert {node.id: node.position for node in definition.graph.nodes} == {
        "child_a": {"x": -200.0, "y": -200.0},
        "child_b": {"x": 200.0, "y": 200.0},
    }

    dissolved = flatten_graph(
        grouped.graph,
        target_instance_id=occurrence.id,
    )
    by_local_id = {
        local_id: dissolved.node_map[qualified_runtime_node_id(occurrence.id, local_id)].position
        for local_id in ("child_a", "child_b")
    }
    assert by_local_id == {
        "child_a": {"x": 100.0, "y": 200.0},
        "child_b": {"x": 500.0, "y": 600.0},
    }


def test_grouping_preserves_child_input_configs_and_uses_public_labels() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("source", NodeType.API_INPUT, label="Quote source"),
            _node(
                "child_router",
                NodeType.LIVE_SWITCH,
                config={
                    "input_scenario_map": {
                        "drivers": "live",
                        "stable_input": "batch",
                    }
                },
            ),
            _node(
                "child_instance",
                config={
                    "instanceOf": "child_router",
                    "inputMapping": {
                        "drivers": "drivers",
                        "stable_input": "stable_input",
                    },
                },
            ),
        ],
        edges=[
            GraphEdge(
                id="source_to_router",
                source="source",
                target="child_router",
                sourceHandle="drivers",
            ),
            GraphEdge(
                id="source_to_instance",
                source="source",
                target="child_instance",
                sourceHandle="drivers",
            ),
        ],
    )

    grouped = create_submodel_graph(
        graph,
        ["child_router", "child_instance"],
        "pricing",
    )
    definition = (grouped.graph.submodels or {})["pricing"]
    children = definition.graph.node_map

    assert [(port.port_id, port.label) for port in definition.input_ports] == [
        ("input_1", "drivers")
    ]
    assert children["child_router"].data.config["input_scenario_map"] == {
        "drivers": "live",
        "stable_input": "batch",
    }
    assert children["child_instance"].data.config["inputMapping"] == {
        "drivers": "drivers",
        "stable_input": "stable_input",
    }


def test_grouping_does_not_rewrite_configs_to_opaque_public_port_ids() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("source", NodeType.API_INPUT, label="Quote source"),
            _node(
                "child_router",
                NodeType.LIVE_SWITCH,
                config={
                    "input_scenario_map": {
                        "drivers": "live",
                        "input_1": "batch",
                    }
                },
            ),
            _node("child_output"),
        ],
        edges=[
            GraphEdge(
                id="source_to_router",
                source="source",
                target="child_router",
                sourceHandle="drivers",
            ),
            GraphEdge(
                id="router_to_output",
                source="child_router",
                target="child_output",
            ),
        ],
    )

    grouped = create_submodel_graph(
        graph,
        ["child_router", "child_output"],
        "pricing",
    )

    definition = (grouped.graph.submodels or {})["pricing"]
    assert definition.input_ports[0].port_id == "input_1"
    assert definition.input_ports[0].label == "drivers"
    assert definition.graph.node_map["child_router"].data.config["input_scenario_map"] == {
        "drivers": "live",
        "input_1": "batch",
    }


def test_canonical_input_port_rejects_more_than_one_parent_binding() -> None:
    definition = _definition()
    graph = PipelineGraph(
        nodes=[
            _node("upstream_a"),
            _node("upstream_b"),
            _instance("instance_a", "scoring"),
        ],
        edges=[
            GraphEdge(
                id="binding_a",
                source="upstream_a",
                target="instance_a",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="binding_b",
                source="upstream_b",
                target="instance_a",
                targetHandle="in__policy",
            ),
        ],
        submodels={"definition_scoring": definition},
    )

    with pytest.raises(ParseError, match="bound more than once") as exc_info:
        validate_submodel_instances(graph)

    assert exc_info.value.context["instance_id"] == "instance_a"
    assert exc_info.value.context["port_id"] == "policy"
