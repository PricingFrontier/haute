"""Codegen contracts for edge-derived input identity.

These tests exercise the public graph-to-source boundary: input names are
observable Python parameters and persisted ``connect`` metadata.
"""

from __future__ import annotations

import ast

import pytest

import haute._codegen_builders as codegen_builders
from haute._codegen_builders import _build_params
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


def test_generated_params_follow_edge_order_across_all_source_kinds() -> None:
    api_input = _node(
        "api",
        "API Input",
        NodeType.API_INPUT,
        _api_config("quotes"),
    )
    ordinary_source = _node("ordinary", "ordinary source", NodeType.CONSTANT)
    nested_child = _node("child", "nested child source", NodeType.CONSTANT)
    target = _node(
        "target",
        "combine inputs",
        NodeType.POLARS,
        {"code": "df = quotes"},
    )

    graph = PipelineGraph(
        nodes=[api_input, ordinary_source, nested_child, target],
        edges=[
            _edge("api-edge", "api", "target", source_port="quotes"),
            _edge("ordinary-edge", "ordinary", "target"),
            _edge(
                "child-edge",
                "submodel__rating",
                "target",
                source_port="out__child",
            ),
        ],
        submodels={
            "rating": {
                "file": "modules/rating.py",
                "childNodeIds": ["child"],
                "graph": {
                    "nodes": [nested_child.model_dump(by_alias=True)],
                    "edges": [],
                },
            }
        },
    )

    code = graph_to_code_multi(graph, pipeline_name="main")["main.py"]

    assert _function_args(code, "combine_inputs") == [
        "quotes",
        "ordinary_source",
        "nested_child_source",
    ]


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


def test_build_params_preserves_supplied_names_one_to_one() -> None:
    params = _build_params(["quotes", "drivers"])

    assert params == "quotes: pl.LazyFrame, drivers: pl.LazyFrame"


def test_build_params_rejects_duplicate_supplied_names_loudly() -> None:
    with pytest.raises(AssertionError) as exc_info:
        _build_params(["quotes", "quotes"])

    message = str(exc_info.value)
    assert "duplicate" in message
    assert "quotes" in message
