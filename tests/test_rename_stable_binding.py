"""Test stable inputMapping bindings preserved across renames at execution and codegen."""

from __future__ import annotations

import ast

import pytest

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code
from haute.executor import execute_graph
from haute.parser import parse_pipeline_source

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def test_rename_stable_binding_execution_and_round_trip():
    # 1. Build initial two-node graph
    src_node = GraphNode(
        id="src",
        data=NodeData(
            label="src",
            nodeType=NodeType.POLARS,
            config={"code": "df = pl.LazyFrame({'value': [11]})"},
        ),
    )
    consumer_node = GraphNode(
        id="consumer",
        data=NodeData(
            label="consumer",
            nodeType=NodeType.POLARS,
            config={"code": "df = src.with_columns(value_doubled=pl.col('value') * 2)"},
        ),
    )
    edge = GraphEdge(id="e1", source="src", target="consumer")
    graph = PipelineGraph(nodes=[src_node, consumer_node], edges=[edge])

    results = execute_graph(graph, target_node_id="consumer")
    assert results["src"].status == "ok"
    assert results["consumer"].status == "ok", results["consumer"].error
    expected_rows = results["consumer"].preview
    assert expected_rows == [{"value": 11, "value_doubled": 22}]

    # 2. Apply rename shape the frontend produces
    renamed_src = GraphNode(
        id="src",
        data=NodeData(
            label="Renamed Src",
            nodeType=NodeType.POLARS,
            config={"code": "df = pl.LazyFrame({'value': [11]})"},
        ),
    )
    bound_consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="consumer",
            nodeType=NodeType.POLARS,
            config={
                "code": "df = src.with_columns(value_doubled=pl.col('value') * 2)",
                "inputMapping": {"src": "Renamed_Src"},
            },
        ),
    )
    renamed_graph = PipelineGraph(nodes=[renamed_src, bound_consumer], edges=[edge])

    renamed_results = execute_graph(renamed_graph, target_node_id="consumer")
    assert renamed_results["src"].status == "ok"
    assert renamed_results["consumer"].status == "ok", renamed_results["consumer"].error
    assert renamed_results["consumer"].preview == expected_rows

    # 3. Assert negative: same renamed graph WITHOUT mapping fails naming 'src' (F11 baseline)
    unbound_consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="consumer",
            nodeType=NodeType.POLARS,
            config={"code": "df = src.with_columns(value_doubled=pl.col('value') * 2)"},
        ),
    )
    unbound_graph = PipelineGraph(nodes=[renamed_src, unbound_consumer], edges=[edge])
    unbound_results = execute_graph(unbound_graph, target_node_id="consumer")
    assert unbound_results["consumer"].status == "error"
    assert "src" in (unbound_results["consumer"].error or "")

    # 4. Codegen round-trip asserting inputMapping preserved and parameter list is 'src'
    code = graph_to_code(renamed_graph, pipeline_name="rename_stable_binding")
    assert "inputMapping={'src': 'Renamed_Src'}" in code
    assert "def consumer(src: pl.LazyFrame)" in code
    assert "df = src.with_columns" in code

    parsed = parse_pipeline_source(code)
    parsed_consumer = next(node for node in parsed.nodes if node.id == "consumer")
    assert parsed_consumer.data.config.get("inputMapping") == {"src": "Renamed_Src"}

    tree = ast.parse(code)
    consumer_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "consumer"
    )
    assert [arg.arg for arg in consumer_fn.args.args] == ["src"]
