"""Codegen keeps the function order a file has (READ-12).

Spec: specs/codegen/high-level.md, the order of functions. A node the file defines stays
where it is, a removed one goes and nothing else moves, and each new node goes just above
the first function that uses its output, or at the end.
"""

from __future__ import annotations

import ast

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import function_order, graph_to_code
from haute.parser import parse_pipeline_source

_SOURCE = '''\
"""Pipeline: main"""

import haute
import polars as pl

pipeline = haute.Pipeline("main")


@pipeline.polars
def zeta() -> pl.LazyFrame:
    df = pl.LazyFrame({"z": [1]})
    return df


@pipeline.polars
def alpha() -> pl.LazyFrame:
    df = pl.LazyFrame({"a": [1]})
    return df


@pipeline.polars
def beta(alpha: pl.LazyFrame) -> pl.LazyFrame:
    df = alpha.with_columns(b=pl.lit(1))
    return df


@pipeline.polars
def gamma(beta: pl.LazyFrame) -> pl.LazyFrame:
    df = beta
    return df


pipeline.connect("alpha", "beta")
pipeline.connect("beta", "gamma")
'''


def _baseline() -> tuple[PipelineGraph, str]:
    graph = parse_pipeline_source(_SOURCE, source_file="main.py")
    code = graph_to_code(graph, pipeline_name="main", existing_source=_SOURCE)
    return parse_pipeline_source(code, source_file="main.py"), code


def _functions(code: str) -> dict[str, str]:
    tree = ast.parse(code)
    return {
        statement.name: ast.get_source_segment(code, statement) or ""
        for statement in tree.body
        if isinstance(statement, ast.FunctionDef)
    }


def _polars(node_id: str, code: str) -> GraphNode:
    return GraphNode(
        id=node_id, data=NodeData(label=node_id, nodeType=NodeType.POLARS, config={"code": code})
    )


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"e_{source}_{target}", source=source, target=target)


def test_an_unchanged_pipeline_regenerates_byte_for_byte_in_its_own_order() -> None:
    graph, code = _baseline()
    assert function_order(code) == ["zeta", "alpha", "beta", "gamma"]
    assert graph_to_code(graph, pipeline_name="main", existing_source=code) == code


def test_new_nodes_go_above_their_first_consumer_or_at_the_end() -> None:
    graph, code = _baseline()
    edges = [edge for edge in graph.edges if (edge.source, edge.target) != ("beta", "gamma")]
    edited = graph.model_copy(
        update={
            "nodes": [*graph.nodes, _polars("mid", "df = beta"), _polars("tail", "df = gamma")],
            "edges": [
                *edges,
                _edge("beta", "mid"),
                _edge("mid", "gamma"),
                _edge("gamma", "tail"),
            ],
        }
    )

    regenerated = graph_to_code(edited, pipeline_name="main", existing_source=code)

    assert function_order(regenerated) == ["zeta", "alpha", "beta", "mid", "gamma", "tail"]
    before, after = _functions(code), _functions(regenerated)
    for name in ("zeta", "alpha", "beta"):
        assert after[name] == before[name]


def test_a_removed_node_goes_and_nothing_else_moves() -> None:
    graph, code = _baseline()
    edited = graph.model_copy(update={"nodes": [node for node in graph.nodes if node.id != "zeta"]})

    regenerated = graph_to_code(edited, pipeline_name="main", existing_source=code)

    assert function_order(regenerated) == ["alpha", "beta", "gamma"]
    before, after = _functions(code), _functions(regenerated)
    assert all(after[name] == before[name] for name in ("alpha", "beta", "gamma"))


def test_a_renamed_node_keeps_its_place_and_its_consumer_stays_where_it_is() -> None:
    graph, code = _baseline()
    renamed = [
        node.model_copy(update={"data": node.data.model_copy(update={"label": "beta_two"})})
        if node.id == "beta"
        else node
        for node in graph.nodes
    ]
    gamma_code = {"code": "df = beta_two"}
    renamed = [
        node.model_copy(update={"data": node.data.model_copy(update={"config": gamma_code})})
        if node.id == "gamma"
        else node
        for node in renamed
    ]
    edited = graph.model_copy(update={"nodes": renamed})

    regenerated = graph_to_code(edited, pipeline_name="main", existing_source=code)

    assert function_order(regenerated) == ["zeta", "alpha", "beta_two", "gamma"]
    before, after = _functions(code), _functions(regenerated)
    assert after["zeta"] == before["zeta"] and after["alpha"] == before["alpha"]


def test_a_file_not_yet_written_is_in_emission_order() -> None:
    graph = parse_pipeline_source(_SOURCE, source_file="main.py")
    fresh = function_order(graph_to_code(graph, pipeline_name="main"))
    assert fresh.index("alpha") < fresh.index("beta") < fresh.index("gamma")


def test_a_submodel_file_keeps_the_order_its_file_has() -> None:
    from haute.codegen import graph_to_code_multi
    from tests.test_submodel_instances import _reuse_graph

    graph = _reuse_graph()
    files = graph_to_code_multi(graph, pipeline_name="reuse", source_file="main.py")
    definition = next(path for path in files if path != "main.py")
    names = function_order(files[definition])
    assert len(names) > 1
    # The definition file as an analyst ordered it: its functions in reverse.
    stubs = [f"@submodel.polars\ndef {name}(): ..." for name in reversed(names)]
    reordered = "\n\n".join(stubs)
    existing = {**files, definition: reordered}

    again = graph_to_code_multi(
        graph, pipeline_name="reuse", source_file="main.py", read_existing=existing.get
    )

    assert function_order(again[definition]) == list(reversed(names))
    assert _functions(again[definition]) == _functions(files[definition])
    assert again["main.py"] == files["main.py"]
