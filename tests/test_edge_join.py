"""Backend contracts for edge-join nodes."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute._config_validation import VALID_KEYS, warn_unrecognized_config_keys
from haute._flatten import flatten_graph
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.errors import ConfigError
from haute.executor import _build_node_fn
from haute.parser import parse_pipeline_file
from haute.pipeline import Pipeline


def _edge_join_node(config: dict) -> GraphNode:
    return GraphNode(
        id="join",
        data=NodeData(
            label="Join Rates",
            nodeType=NodeType.EDGE_JOIN,
            config=config,
        ),
    )


def _build_edge_join(
    config: dict,
    source_ids: list[str],
    source_names: list[str] | None = None,
    target_handles: list[str | None] | None = None,
):
    return _build_node_fn(
        _edge_join_node(config),
        source_ids=source_ids,
        source_names=source_names or source_ids,
        target_handles=target_handles,
    )


def _write_pipeline(tmp_path: Path, code: str) -> Path:
    path = tmp_path / "pipeline.py"
    path.write_text(code, encoding="utf-8")
    return path


def test_edge_join_node_type_and_decorator_contract() -> None:
    assert NodeType.EDGE_JOIN.value == "edgeJoin"
    assert NodeType("edgeJoin") is NodeType.EDGE_JOIN

    pipeline = Pipeline("joins")

    @pipeline.edge_join(base_input="quotes", join_input="lookup", how="left", on=["region"])
    def join_rates(quotes: pl.LazyFrame, lookup: pl.LazyFrame) -> pl.LazyFrame:
        return quotes.join(lookup, on="region", how="left")

    node = pipeline.nodes[0]
    assert node.config["_node_type"] is NodeType.EDGE_JOIN
    assert node.config["baseInput"] == "quotes"


def test_edge_join_decorator_registers_original_function_like_other_nodes() -> None:
    pipeline = Pipeline("joins")

    def join_rates(quotes: pl.LazyFrame, lookup: pl.LazyFrame) -> pl.LazyFrame:
        return quotes

    registered = pipeline.edge_join(
        base_input="quotes",
        join_input="lookup",
        how="left",
        on=["region"],
    )(join_rates)

    assert registered is join_rates
    assert pipeline.nodes[0].fn is join_rates


def test_edge_join_invalid_config_fails_when_shared_helper_runs() -> None:
    pipeline = Pipeline("joins")

    @pipeline.edge_join(base_input="quotes", join_input="lookup")
    def join_rates(quotes: pl.LazyFrame, lookup: pl.LazyFrame) -> pl.LazyFrame:
        return pipeline._apply_edge_join("join_rates", quotes, lookup)

    df = pl.DataFrame({"region": ["N"]})
    with pytest.raises(ConfigError, match="join keys"):
        pipeline._apply_edge_join("join_rates", df, df)


def test_edge_join_config_keys_are_registered() -> None:
    expected = {
        "baseInput",
        "joinInput",
        "how",
        "on",
        "leftOn",
        "rightOn",
        "suffix",
        "coalesce",
        "validate",
        "maintainOrder",
    }
    assert expected <= VALID_KEYS[NodeType.EDGE_JOIN]
    assert (
        warn_unrecognized_config_keys(
            NodeType.EDGE_JOIN,
            {
                "baseInput": "quotes",
                "joinInput": "lookup",
                "how": "left",
                "on": ["region"],
                "suffix": "_lookup",
            },
        )
        == []
    )


def test_parser_extracts_edge_join_decorator_config(tmp_path: Path) -> None:
    code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("joins")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.polars
def lookup() -> pl.LazyFrame:
    return pl.LazyFrame()


@pipeline.edge_join(
    base_input="quotes",
    join_input="lookup",
    how="left",
    left_on=["region"],
    right_on=["rating_region"],
    suffix="_lookup",
    coalesce=True,
    validate="m:1",
    maintain_order="left",
)
def join_rates(quotes: pl.LazyFrame, lookup: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.join(
        lookup,
        left_on="region",
        right_on="rating_region",
        how="left",
    )


pipeline.connect("lookup", "join_rates")
pipeline.connect("quotes", "join_rates")
"""
    graph = parse_pipeline_file(_write_pipeline(tmp_path, code))
    node = graph.node_map["join_rates"]
    assert node.data.nodeType is NodeType.EDGE_JOIN
    assert node.data.config == {
        "baseInput": "quotes",
        "joinInput": "lookup",
        "how": "left",
        "leftOn": ["region"],
        "rightOn": ["rating_region"],
        "suffix": "_lookup",
        "coalesce": True,
        "validate": "m:1",
        "maintainOrder": "left",
    }


def test_codegen_emits_edge_join_with_base_first_params_and_connects(tmp_path: Path) -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(
                    label="quotes",
                    nodeType=NodeType.CONSTANT,
                    config={"values": [{"name": "region", "value": "N"}]},
                ),
            ),
            GraphNode(
                id="lookup",
                data=NodeData(
                    label="lookup",
                    nodeType=NodeType.CONSTANT,
                    config={
                        "values": [
                            {"name": "region", "value": "N"},
                            {"name": "factor", "value": "1.1"},
                        ]
                    },
                ),
            ),
            _edge_join_node(
                {
                    "baseInput": "quotes",
                    "joinInput": "lookup",
                    "how": "left",
                    "on": ["region"],
                    "suffix": "_lookup",
                }
            ),
        ],
        edges=[
            GraphEdge(id="e_lookup_join", source="lookup", target="join", targetHandle="join"),
            GraphEdge(id="e_quotes_join", source="quotes", target="join", targetHandle="base"),
        ],
    )

    code = graph_to_code(graph, pipeline_name="joins")

    assert "@pipeline.edge_join(" in code
    assert 'base_input="quotes"' in code
    assert 'join_input="lookup"' in code
    assert "on=['region']" in code
    assert ".join(" not in code
    assert 'return pipeline._apply_edge_join("Join_Rates", quotes, lookup)' in code
    assert "def Join_Rates(quotes: pl.LazyFrame, lookup: pl.LazyFrame)" in code
    assert 'pipeline.connect("quotes", "Join_Rates", target_port="base")' in code
    assert 'pipeline.connect("lookup", "Join_Rates", target_port="join")' in code
    assert code.index('pipeline.connect("quotes", "Join_Rates", target_port="base")') < code.index(
        'pipeline.connect("lookup", "Join_Rates", target_port="join")'
    )
    compile(code, "<edge_join>", "exec")

    constant_dir = tmp_path / "config" / "constant"
    constant_dir.mkdir(parents=True)
    (constant_dir / "quotes.json").write_text(
        json.dumps({"values": [{"name": "region", "value": "N"}]}),
        encoding="utf-8",
    )
    (constant_dir / "lookup.json").write_text(
        json.dumps(
            {
                "values": [
                    {"name": "region", "value": "N"},
                    {"name": "factor", "value": "1.1"},
                ]
            }
        ),
        encoding="utf-8",
    )
    parsed = parse_pipeline_file(_write_pipeline(tmp_path, code))
    parsed_join = parsed.node_map["Join_Rates"]
    assert parsed_join.data.nodeType is NodeType.EDGE_JOIN
    assert parsed_join.data.config["baseInput"] == "quotes"
    assert parsed_join.data.config["joinInput"] == "lookup"
    parsed_edges = {(edge.source, edge.target): edge for edge in parsed.edges}
    assert parsed_edges[("quotes", "Join_Rates")].targetHandle == "base"
    assert parsed_edges[("lookup", "Join_Rates")].targetHandle == "join"

    namespace = {"__file__": str(_write_pipeline(tmp_path, code))}
    exec(compile(code, str(tmp_path / "pipeline.py"), "exec"), namespace)
    result = namespace["pipeline"].run()
    assert result.collect()["factor"].to_list() == [1.1]


def test_submodel_parent_codegen_preserves_external_edge_join_target_role() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(
                    label="Src",
                    nodeType=NodeType.CONSTANT,
                    config={"values": [{"name": "id", "value": "1"}]},
                ),
            ),
            GraphNode(
                id="join",
                data=NodeData(
                    label="Join",
                    nodeType=NodeType.EDGE_JOIN,
                    config={
                        "baseInput": "src",
                        "joinInput": "lookup",
                        "how": "left",
                        "on": ["id"],
                    },
                ),
            ),
            GraphNode(
                id="lookup",
                data=NodeData(
                    label="Lookup",
                    nodeType=NodeType.CONSTANT,
                    config={"values": [{"name": "id", "value": "1"}]},
                ),
            ),
        ],
        edges=[
            GraphEdge(
                id="e-src-sm",
                source="src",
                target="submodel__rating",
                targetHandle="in__join",
            ),
        ],
        submodels={
            "rating": {
                "file": "modules/rating.py",
                "childNodeIds": ["join", "lookup"],
                "graph": {
                    "nodes": [
                        {
                            "id": "join",
                            "data": {
                                "label": "Join",
                                "nodeType": "edgeJoin",
                                "config": {
                                    "baseInput": "src",
                                    "joinInput": "lookup",
                                    "how": "left",
                                    "on": ["id"],
                                },
                            },
                        },
                        {
                            "id": "lookup",
                            "data": {
                                "label": "Lookup",
                                "nodeType": "constant",
                                "config": {"values": [{"name": "id", "value": "1"}]},
                            },
                        },
                    ],
                    "edges": [
                        {
                            "id": "e-lookup-join",
                            "source": "lookup",
                            "target": "join",
                            "targetHandle": "join",
                        },
                    ],
                },
            },
        },
    )

    files = graph_to_code_multi(graph, pipeline_name="main")

    assert 'pipeline.connect("Src", "Join", target_port="base")' in files["main.py"]
    assert "pipeline._apply_edge_join" not in files["modules/rating.py"]
    assert "submodel._apply_edge_join" in files["modules/rating.py"]


def test_flattening_submodel_restores_external_edge_join_target_role() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(label="Src", nodeType=NodeType.CONSTANT, config={}),
            ),
            GraphNode(
                id="submodel__rating",
                data=NodeData(label="rating", nodeType=NodeType.SUBMODEL, config={}),
            ),
        ],
        edges=[
            GraphEdge(
                id="e-src-sm",
                source="src",
                target="submodel__rating",
                targetHandle="in__join",
            ),
        ],
        submodels={
            "rating": {
                "graph": {
                    "nodes": [
                        {
                            "id": "join",
                            "data": {
                                "label": "Join",
                                "nodeType": "edgeJoin",
                                "config": {
                                    "baseInput": "src",
                                    "joinInput": "lookup",
                                    "how": "left",
                                    "on": ["id"],
                                },
                            },
                        },
                    ],
                    "edges": [],
                },
            },
        },
    )

    flattened = flatten_graph(graph)

    edge = next(e for e in flattened.edges if e.source == "src" and e.target == "join")
    assert edge.targetHandle == "base"


def test_edge_join_pipeline_run_honours_configured_roles_for_reversed_connects() -> None:
    pipeline = Pipeline("joins")

    @pipeline.data_source
    def quotes() -> pl.DataFrame:
        return pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]})

    @pipeline.data_source
    def lookup() -> pl.DataFrame:
        return pl.DataFrame({"region": ["N", "S"], "factor": [1.1, 0.9]})

    @pipeline.edge_join(base_input="quotes", join_input="lookup", how="left", on=["region"])
    def join_rates(base: pl.DataFrame, join: pl.DataFrame) -> pl.DataFrame:
        return base.lazy().join(join.lazy(), on="region", how="left").collect()

    pipeline.connect("lookup", "join_rates", target_port="join")
    pipeline.connect("quotes", "join_rates", target_port="base")

    result = pipeline.run()

    assert result["quote_id"].to_list() == [1, 2, 3]
    assert result["factor"].to_list() == [1.1, 0.9, None]


def test_edge_join_pipeline_run_calls_function_body_like_other_nodes() -> None:
    pipeline = Pipeline("joins")

    @pipeline.data_source
    def quotes() -> pl.DataFrame:
        return pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]})

    @pipeline.data_source
    def lookup() -> pl.DataFrame:
        return pl.DataFrame({"region": ["N", "S"], "factor": [1.1, 0.9]})

    @pipeline.edge_join(base_input="quotes", join_input="lookup", how="left", on=["region"])
    def join_rates(base: pl.DataFrame, _join: pl.DataFrame) -> pl.DataFrame:
        return base.with_columns(pl.lit("called").alias("body_result"))

    pipeline.connect("quotes", "join_rates", target_port="base")
    pipeline.connect("lookup", "join_rates", target_port="join")

    result = pipeline.run()

    assert result["quote_id"].to_list() == [1, 2, 3]
    assert result["body_result"].to_list() == ["called", "called", "called"]
    assert "factor" not in result.columns


def test_edge_join_pipeline_run_can_use_shared_edge_join_helper() -> None:
    pipeline = Pipeline("joins")

    @pipeline.data_source
    def quotes() -> pl.DataFrame:
        return pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]})

    @pipeline.data_source
    def lookup() -> pl.DataFrame:
        return pl.DataFrame({"region": ["N", "S"], "factor": [1.1, 0.9]})

    @pipeline.edge_join(base_input="quotes", join_input="lookup", how="left", on=["region"])
    def join_rates(base: pl.DataFrame, join: pl.DataFrame) -> pl.DataFrame:
        return pipeline._apply_edge_join("join_rates", base, join)

    pipeline.connect("quotes", "join_rates", target_port="base")
    pipeline.connect("lookup", "join_rates", target_port="join")

    result = pipeline.run()

    assert result["quote_id"].to_list() == [1, 2, 3]
    assert result["factor"].to_list() == [1.1, 0.9, None]


def test_edge_join_pipeline_score_can_use_shared_edge_join_helper() -> None:
    pipeline = Pipeline("joins")

    @pipeline.api_input(api_input=True)
    def quotes() -> pl.DataFrame:
        raise AssertionError("score() should seed API inputs without calling the source")

    @pipeline.data_source
    def lookup() -> pl.DataFrame:
        return pl.DataFrame({"region": ["N", "S"], "factor": [1.1, 0.9]})

    @pipeline.edge_join(base_input="quotes", join_input="lookup", how="left", on=["region"])
    def join_rates(base: pl.DataFrame, join: pl.DataFrame) -> pl.DataFrame:
        return pipeline._apply_edge_join("join_rates", base, join)

    pipeline.connect("quotes", "join_rates", target_port="base")
    pipeline.connect("lookup", "join_rates", target_port="join")

    result = pipeline.score(pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]}))

    assert result["quote_id"].to_list() == [1, 2, 3]
    assert result["factor"].to_list() == [1.1, 0.9, None]


def test_edge_join_builder_joins_by_configured_source_ids() -> None:
    _, fn, is_source = _build_edge_join(
        {
            "baseInput": "quotes",
            "joinInput": "lookup",
            "how": "left",
            "on": ["region"],
            "suffix": "_lookup",
        },
        source_ids=["lookup", "quotes"],
    )
    assert is_source is False

    lookup = pl.DataFrame({"region": ["N", "S"], "factor": [1.1, 0.9]}).lazy()
    quotes = pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]}).lazy()

    result = fn(lookup, quotes).collect()

    assert result["quote_id"].to_list() == [1, 2, 3]
    assert result["factor"].to_list() == [1.1, 0.9, None]


def test_edge_join_builder_supports_left_on_right_on() -> None:
    _, fn, _ = _build_edge_join(
        {
            "baseInput": "quotes",
            "joinInput": "lookup",
            "how": "inner",
            "leftOn": ["region"],
            "rightOn": ["rating_region"],
        },
        source_ids=["quotes", "lookup"],
    )
    quotes = pl.DataFrame({"quote_id": [1, 2], "region": ["N", "S"]}).lazy()
    lookup = pl.DataFrame({"rating_region": ["N"], "factor": [1.1]}).lazy()

    result = fn(quotes, lookup).collect()

    assert result["quote_id"].to_list() == [1]
    assert result["factor"].to_list() == [1.1]


def test_edge_join_builder_rejects_target_handle_role_mismatch() -> None:
    with pytest.raises(ConfigError, match="targetHandle.*baseInput"):
        _build_edge_join(
            {
                "baseInput": "quotes",
                "joinInput": "lookup",
                "how": "left",
                "on": ["region"],
            },
            source_ids=["quotes", "lookup"],
            target_handles=["join", "base"],
        )


def test_edge_join_codegen_rejects_target_handle_role_mismatch() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(label="quotes", nodeType=NodeType.CONSTANT, config={}),
            ),
            GraphNode(
                id="lookup",
                data=NodeData(label="lookup", nodeType=NodeType.CONSTANT, config={}),
            ),
            _edge_join_node(
                {
                    "baseInput": "quotes",
                    "joinInput": "lookup",
                    "how": "left",
                    "on": ["region"],
                }
            ),
        ],
        edges=[
            GraphEdge(id="e_quotes_join", source="quotes", target="join", targetHandle="join"),
            GraphEdge(id="e_lookup_join", source="lookup", target="join", targetHandle="base"),
        ],
    )

    with pytest.raises(ConfigError, match="targetHandle.*baseInput"):
        graph_to_code(graph, pipeline_name="joins")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"joinInput": "lookup", "on": ["region"]}, "baseInput"),
        ({"baseInput": "quotes", "joinInput": "lookup", "how": "cross", "on": ["region"]}, "cross"),
        (
            {
                "baseInput": "quotes",
                "joinInput": "lookup",
                "on": ["region"],
                "leftOn": ["region"],
            },
            "on.*leftOn",
        ),
        (
            {
                "baseInput": "quotes",
                "joinInput": "lookup",
                "leftOn": ["region", "vehicle"],
                "rightOn": ["rating_region"],
            },
            "same number",
        ),
        ({"baseInput": "quotes", "joinInput": "lookup"}, "join keys"),
        ({"baseInput": "quotes", "joinInput": "missing", "on": ["region"]}, "not connected"),
        ({"baseInput": "quotes", "joinInput": "quotes", "on": ["region"]}, "distinct"),
    ],
)
def test_edge_join_builder_invalid_config_fails_loudly(
    config: dict,
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        _build_edge_join(config, source_ids=["quotes", "lookup"])
