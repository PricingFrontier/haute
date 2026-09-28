"""Codegen contracts for edge-derived input identity.

These tests exercise the public graph-to-source boundary: input names are
observable Python parameters and persisted ``connect`` metadata.
"""

from __future__ import annotations

import ast

import pytest

import haute._codegen_builders as codegen_builders
from haute._codegen_builders import Param, _params
from haute._graph_utils import incoming_edge_bindings
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.errors import ParseError


def _node(
    node_id: str,
    label: str,
    node_type: NodeType,
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label,
            nodeType=node_type,
            config={"contract": "opaque", **(config or {})},
        ),
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_port: str | None = None,
    target_port: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=edge_id,
        source=source,
        target=target,
        sourceHandle=source_port,
        targetHandle=target_port,
    )


def _api_config(*labels: str) -> dict:
    return {
        "path": "payload.json",
        "tables": [
            {
                "path": f"$[:].{label}[:]",
                "label": label,
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "id",
                        "path": f"$[:].{label}[:].id",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
            for label in labels
        ],
    }


def _function_args(code: str, function_name: str) -> list[str]:
    tree = ast.parse(code)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return [argument.arg for argument in function.args.args]


@pytest.mark.parametrize("output_names", [("output_1",), ("output_1", "output_2")])
def test_generated_params_follow_edge_order_across_all_source_kinds(
    output_names: tuple[str, ...],
) -> None:
    api_input = _node(
        "api",
        "API Input",
        NodeType.API_INPUT,
        _api_config("quotes"),
    )
    ordinary_source = _node("ordinary", "ordinary source", NodeType.CONSTANT)
    nested_children = [
        _node(f"child_{index}", f"internal child {index}", NodeType.CONSTANT)
        for index in range(len(output_names))
    ]
    rating_instance = GraphNode(
        id="rating-instance",
        type="submodel",
        data=NodeData(
            label="rating",
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": "rating", "alias": "rating"},
        ),
    )
    target = _node(
        "target",
        "combine inputs",
        NodeType.POLARS,
        {"code": "df = quotes"},
    )

    graph = PipelineGraph(
        nodes=[api_input, ordinary_source, rating_instance, target],
        edges=[
            _edge("api-edge", "api", "target", source_port="quotes"),
            _edge("ordinary-edge", "ordinary", "target"),
            _edge(
                "child-edge",
                "rating-instance",
                "target",
                source_port="out__output_1",
            ),
        ],
        submodels={
            "rating": {
                "definitionId": "rating",
                "file": "modules/rating.py",
                "graph": {
                    "nodes": [child.model_dump(by_alias=True) for child in nested_children],
                    "edges": [],
                },
                "inputPorts": [],
                "outputPorts": [
                    {
                        "name": name,
                        "source": {"nodeId": child.id, "handleId": None},
                    }
                    for name, child in zip(output_names, nested_children)
                ],
            }
        },
    )

    for alias in ("Inputs", "renamed_inputs"):
        rating_instance.data.label = alias
        rating_instance.data.config["alias"] = alias
        code = graph_to_code_multi(graph, pipeline_name="main")["main.py"]

        assert _function_args(code, "combine_inputs") == [
            "quotes",
            "ordinary_source",
            "output_1",
        ]
        assert f'pipeline.connect("{alias}", "combine_inputs", source_port="output_1")' in code


@pytest.mark.parametrize(
    ("left_port", "right_port", "expected_names"),
    [
        ("left_result", "right_result", ["left_result", "right_result"]),
        ("result", "result", None),
    ],
)
def test_independent_submodel_outputs_use_public_port_names(
    left_port: str,
    right_port: str,
    expected_names: list[str] | None,
) -> None:
    left_internal = _node("left-internal", "left implementation", NodeType.CONSTANT)
    right_internal = _node("right-internal", "right implementation", NodeType.CONSTANT)
    left = GraphNode(
        id="left-instance",
        type="submodel",
        data=NodeData(
            label="left",
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": "left-definition", "alias": "left"},
        ),
    )
    right = GraphNode(
        id="right-instance",
        type="submodel",
        data=NodeData(
            label="right",
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": "right-definition", "alias": "right"},
        ),
    )
    consumer = _node(
        "consumer",
        "combine outputs",
        NodeType.POLARS,
        {"code": "df = left_result.join(right_result, how='cross')"},
    )
    graph = PipelineGraph(
        nodes=[left, right, consumer],
        edges=[
            _edge("left-edge", "left-instance", "consumer", source_port=f"out__{left_port}"),
            _edge("right-edge", "right-instance", "consumer", source_port=f"out__{right_port}"),
        ],
        submodels={
            "left-definition": {
                "definitionId": "left-definition",
                "file": "modules/left.py",
                "graph": {"nodes": [left_internal.model_dump(by_alias=True)], "edges": []},
                "inputPorts": [],
                "outputPorts": [
                    {"name": left_port, "source": {"nodeId": "left-internal", "handleId": None}}
                ],
            },
            "right-definition": {
                "definitionId": "right-definition",
                "file": "modules/right.py",
                "graph": {"nodes": [right_internal.model_dump(by_alias=True)], "edges": []},
                "inputPorts": [],
                "outputPorts": [
                    {"name": right_port, "source": {"nodeId": "right-internal", "handleId": None}}
                ],
            },
        },
    )

    if expected_names is None:
        with pytest.raises(ParseError, match="Duplicate derived input name"):
            graph_to_code_multi(graph, pipeline_name="main")
        return

    assert [name for _edge, name in incoming_edge_bindings(graph, "consumer")] == expected_names
    code = graph_to_code_multi(graph, pipeline_name="main")["main.py"]
    assert _function_args(code, "combine_outputs") == expected_names
    assert 'pipeline.connect("left", "combine_outputs", source_port="left_result")' in code
    assert 'pipeline.connect("right", "combine_outputs", source_port="right_result")' in code


def test_sole_frame_api_input_uses_frame_param_and_explicit_source_port() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("api", "API Input", NodeType.API_INPUT, _api_config("quotes")),
            _node("target", "use quote", NodeType.POLARS, {"code": "df = quotes"}),
        ],
        edges=[_edge("quote-edge", "api", "target", source_port="quotes")],
    )

    code = graph_to_code(graph, pipeline_name="main")

    assert _function_args(code, "use_quote") == ["quotes"]
    assert 'pipeline.connect("API_Input", "use_quote", source_port="quotes")' in code
    assert 'pipeline.connect("API_Input", "use_quote")' not in code


def test_multi_frame_api_input_uses_each_frame_name_and_source_port_in_edge_order() -> None:
    graph = PipelineGraph(
        nodes=[
            _node(
                "api",
                "API Input",
                NodeType.API_INPUT,
                _api_config("quotes", "drivers"),
            ),
            _node(
                "target",
                "combine frames",
                NodeType.POLARS,
                {"code": "df = quotes.join(drivers, how='cross')"},
            ),
        ],
        edges=[
            _edge("quotes-edge", "api", "target", source_port="quotes"),
            _edge("drivers-edge", "api", "target", source_port="drivers"),
        ],
    )

    code = graph_to_code(graph, pipeline_name="main")

    assert _function_args(code, "combine_frames") == ["quotes", "drivers"]
    connect_lines = [
        line
        for line in code.splitlines()
        if line.startswith('pipeline.connect("API_Input", "combine_frames"')
    ]
    assert connect_lines == [
        'pipeline.connect("API_Input", "combine_frames", source_port="quotes")',
        'pipeline.connect("API_Input", "combine_frames", source_port="drivers")',
    ]


def test_duplicate_derived_input_name_raises_instead_of_suffixing() -> None:
    graph = PipelineGraph(
        nodes=[
            _node(
                "api-source",
                "API Input",
                NodeType.API_INPUT,
                _api_config("clean_data"),
            ),
            _node("ordinary-source", "clean data", NodeType.CONSTANT),
            _node(
                "pricing-target",
                "Pricing Transform",
                NodeType.POLARS,
                {"code": "df = clean_data"},
            ),
        ],
        edges=[
            _edge(
                "frame-edge",
                "api-source",
                "pricing-target",
                source_port="clean_data",
            ),
            _edge("ordinary-edge", "ordinary-source", "pricing-target"),
        ],
    )

    with pytest.raises(ParseError) as exc_info:
        graph_to_code(graph, pipeline_name="main")

    message = str(exc_info.value)
    assert "clean_data" in message
    assert "pricing-target" in message


def test_portless_api_edge_raises_and_names_edge_and_source() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("api-source", "API Source", NodeType.API_INPUT, _api_config("quotes")),
            _node("target", "Target", NodeType.POLARS, {"code": "df = df"}),
        ],
        edges=[_edge("edge-without-port", "api-source", "target")],
    )

    with pytest.raises(ParseError) as exc_info:
        graph_to_code(graph, pipeline_name="main")

    message = str(exc_info.value)
    assert "edge-without-port" in message
    assert "api-source" in message


def test_dedup_param_helper_is_removed() -> None:
    assert not hasattr(codegen_builders, "_dedup_param_names")


def test_params_preserve_supplied_names_one_to_one() -> None:
    # A declaration's parameters are unannotated; a hook's and a transform's
    # carry the frame annotation. Either way each supplied name is one
    # parameter, in order, with no suffixing.
    assert _params(["quotes", "drivers"]) == (Param("quotes"), Param("drivers"))
    assert _params(["quotes", "drivers"], "pl.LazyFrame") == (
        Param("quotes", "pl.LazyFrame"),
        Param("drivers", "pl.LazyFrame"),
    )


def test_params_reject_duplicate_supplied_names_loudly() -> None:
    with pytest.raises(AssertionError) as exc_info:
        _params(["quotes", "quotes"])

    message = str(exc_info.value)
    assert "duplicate" in message
    assert "quotes" in message
