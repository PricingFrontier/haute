"""Backend contracts for edge-join nodes."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute._config_validation import VALID_KEYS, warn_unrecognized_config_keys
from haute._edge_join import (
    edge_join_config_to_decorator_kwargs,
    resolve_edge_join_role_indices,
)
from haute._flatten import flatten_graph
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.errors import ConfigError
from haute.executor import _build_node_fn, execute_graph
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


def _edge_join_decorator_line(code: str) -> str:
    return next(line for line in code.splitlines() if line.startswith("@pipeline.edge_join("))


def test_edge_join_round_trip_resolves_roles_when_node_ids_differ_from_labels(
    tmp_path: Path,
) -> None:
    """Canvas node ids (e.g. ``dataInput_5``) must not leak into decorator kwargs.

    On reload, parsed node ids are the sanitized function names, so role
    kwargs emitted verbatim from config would never resolve again — preview
    breaks and the pipeline cannot re-save after a server restart.
    """
    ds_values = [{"name": "region", "value": "N"}]
    enrich_values = [
        {"name": "region", "value": "N"},
        {"name": "factor", "value": "1.1"},
    ]
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="dataInput_5",
                data=NodeData(
                    label="Data Source 5",
                    nodeType=NodeType.CONSTANT,
                    config={"values": ds_values},
                ),
            ),
            GraphNode(
                id="polars_2",
                data=NodeData(
                    label="Enrich Step",
                    nodeType=NodeType.CONSTANT,
                    config={"values": enrich_values},
                ),
            ),
            _edge_join_node(
                {
                    "baseInput": "dataInput_5",
                    "joinInput": "polars_2",
                    "how": "left",
                    "on": ["region"],
                }
            ),
        ],
        edges=[
            GraphEdge(id="e_enrich_join", source="polars_2", target="join", targetHandle="join"),
            GraphEdge(id="e_ds_join", source="dataInput_5", target="join", targetHandle="base"),
        ],
    )

    code = graph_to_code(graph, pipeline_name="joins")

    constant_dir = tmp_path / "config" / "constant"
    constant_dir.mkdir(parents=True)
    (constant_dir / "Data_Source_5.json").write_text(
        json.dumps({"values": ds_values}),
        encoding="utf-8",
    )
    (constant_dir / "Enrich_Step.json").write_text(
        json.dumps({"values": enrich_values}),
        encoding="utf-8",
    )
    path = _write_pipeline(tmp_path, code)
    parsed = parse_pipeline_file(path)
    parsed_join = parsed.node_map["Join_Rates"]
    parsed_source_ids = [edge.source for edge in parsed.edges if edge.target == "Join_Rates"]

    # The reloaded roles must resolve against the sanitized node ids.
    base_index, join_index = resolve_edge_join_role_indices(
        parsed_join.data.config,
        parsed_source_ids,
    )
    assert parsed_source_ids[base_index] == "Data_Source_5"
    assert parsed_source_ids[join_index] == "Enrich_Step"

    assert 'base_input="Data_Source_5"' in code
    assert 'join_input="Enrich_Step"' in code
    assert "dataInput_5" not in code
    assert "polars_2" not in code
    assert parsed_join.data.config["baseInput"] == "Data_Source_5"
    assert parsed_join.data.config["joinInput"] == "Enrich_Step"

    # Preview off the generated module produces the joined frame.
    namespace = {"__file__": str(path)}
    exec(compile(code, str(path), "exec"), namespace)
    result = namespace["pipeline"].run()
    assert result.collect()["factor"].to_list() == [1.1]

    # Re-save (second codegen pass) is byte-stable for the decorator kwargs.
    resaved = graph_to_code(parsed, pipeline_name="joins")
    assert _edge_join_decorator_line(resaved) == _edge_join_decorator_line(code)


def test_edge_join_codegen_fails_loudly_when_role_references_missing_node() -> None:
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
                    "baseInput": "ghost_7",
                    "joinInput": "lookup",
                    "how": "left",
                    "on": ["region"],
                }
            ),
        ],
        edges=[
            GraphEdge(id="e_quotes_join", source="quotes", target="join"),
            GraphEdge(id="e_lookup_join", source="lookup", target="join"),
        ],
    )

    with pytest.raises(ConfigError, match="not connected") as exc_info:
        graph_to_code(graph, pipeline_name="joins")
    assert exc_info.value.context["missing"] == ["ghost_7"]


def test_edge_join_codegen_fails_loudly_when_base_and_join_share_a_node() -> None:
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
                    "joinInput": "quotes",
                    "how": "left",
                    "on": ["region"],
                }
            ),
        ],
        edges=[
            GraphEdge(id="e_quotes_join", source="quotes", target="join"),
            GraphEdge(id="e_lookup_join", source="lookup", target="join"),
        ],
    )

    with pytest.raises(ConfigError, match="distinct"):
        graph_to_code(graph, pipeline_name="joins")


def _submodel_edge_join_graph() -> PipelineGraph:
    return PipelineGraph(
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


def test_submodel_parent_codegen_preserves_external_edge_join_target_role() -> None:
    files = graph_to_code_multi(_submodel_edge_join_graph(), pipeline_name="main")

    assert 'pipeline.connect("Src", "Join", target_port="base")' in files["main.py"]
    assert "pipeline._apply_edge_join" not in files["modules/rating.py"]
    assert "submodel._apply_edge_join" in files["modules/rating.py"]


def test_submodel_edge_join_decorator_kwargs_use_emitted_function_names() -> None:
    """Cross-boundary roles must also be rewritten to emitted function names.

    The submodel join's ``baseInput`` references the parent graph node id
    ``src``; the emitted submodel file declares that input as the boundary
    parameter ``Src``, so the decorator must reference ``Src``/``Lookup``
    or the submodel file can never be reloaded.
    """
    files = graph_to_code_multi(_submodel_edge_join_graph(), pipeline_name="main")

    assert 'base_input="Src"' in files["modules/rating.py"]
    assert 'join_input="Lookup"' in files["modules/rating.py"]


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

    @pipeline.data_input
    def quotes() -> pl.DataFrame:
        return pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]})

    @pipeline.data_input
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

    @pipeline.data_input
    def quotes() -> pl.DataFrame:
        return pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]})

    @pipeline.data_input
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

    @pipeline.data_input
    def quotes() -> pl.DataFrame:
        return pl.DataFrame({"quote_id": [1, 2, 3], "region": ["N", "S", "E"]})

    @pipeline.data_input
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

    @pipeline.data_input
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


# ---------------------------------------------------------------------------
# Runtime join-semantics matrix
#
# Every test below executes the join through BOTH production runtime
# surfaces and asserts output frames (schema + row values), so the two
# cannot silently drift:
#
#   * builder surface — ``_build_node_fn`` (graph executor / preview /
#     trace / deploy), which joins LazyFrames and collects downstream;
#   * pipeline surface — generated-code shape: ``@pipeline.edge_join``
#     decorator kwargs emitted by ``edge_join_config_to_decorator_kwargs``
#     with a ``pipeline._apply_edge_join`` body, run via ``pipeline.run()``
#     (eager DataFrames, ``collect_eager=True``).
#
# Row assertions are order-insensitive (sorted row sets): Polars joins do
# not guarantee row order without ``maintain_order``.
# ---------------------------------------------------------------------------


def _sorted_rows(df: pl.DataFrame) -> list[tuple]:
    return sorted(df.rows(), key=repr)


def _run_builder_edge_join(
    config: dict,
    base_df: pl.DataFrame,
    join_df: pl.DataFrame,
) -> pl.DataFrame:
    """Execute through the graph-executor builder surface (lazy join)."""
    _, fn, _ = _build_edge_join(
        {**config, "baseInput": "base_src", "joinInput": "lookup_src"},
        source_ids=["base_src", "lookup_src"],
    )
    return fn(base_df.lazy(), join_df.lazy()).collect()


def _run_pipeline_edge_join(
    config: dict,
    base_df: pl.DataFrame,
    join_df: pl.DataFrame,
) -> pl.DataFrame:
    """Execute through the generated-code surface (``pipeline.run()``).

    Decorator kwargs are derived with the same helper codegen uses, and the
    node body delegates to ``pipeline._apply_edge_join`` exactly like an
    emitted pipeline file, so this is byte-equivalent to generated code.
    """
    pipeline = Pipeline("join_matrix")

    @pipeline.data_input
    def base_src() -> pl.DataFrame:
        return base_df

    @pipeline.data_input
    def lookup_src() -> pl.DataFrame:
        return join_df

    decorator_kwargs = dict(
        edge_join_config_to_decorator_kwargs(
            {**config, "baseInput": "base_src", "joinInput": "lookup_src"}
        )
    )

    @pipeline.edge_join(**decorator_kwargs)
    def joined(base_src: pl.DataFrame, lookup_src: pl.DataFrame) -> pl.DataFrame:
        return pipeline._apply_edge_join("joined", base_src, lookup_src)

    pipeline.connect("base_src", "joined", target_port="base")
    pipeline.connect("lookup_src", "joined", target_port="join")
    return pipeline.run()


def _run_edge_join(
    config: dict,
    base_df: pl.DataFrame,
    join_df: pl.DataFrame,
) -> pl.DataFrame:
    """Run both runtime surfaces, assert they agree, and return the frame."""
    builder_result = _run_builder_edge_join(config, base_df, join_df)
    pipeline_result = _run_pipeline_edge_join(config, base_df, join_df)
    assert builder_result.columns == pipeline_result.columns
    assert builder_result.schema == pipeline_result.schema
    assert _sorted_rows(builder_result) == _sorted_rows(pipeline_result)
    return pipeline_result


def _assert_edge_join_output(
    result: pl.DataFrame,
    expected_columns: list[str],
    expected_rows: list[tuple],
) -> None:
    assert result.columns == expected_columns
    assert _sorted_rows(result) == sorted(expected_rows, key=repr)


def _assert_edge_join_raises(
    config: dict,
    base_df: pl.DataFrame,
    join_df: pl.DataFrame,
    error_type: type[Exception],
    match: str,
) -> None:
    """Assert the failure surfaces loudly through BOTH runtime surfaces."""
    with pytest.raises(error_type, match=match):
        _run_builder_edge_join(config, base_df, join_df)
    with pytest.raises(error_type, match=match):
        _run_pipeline_edge_join(config, base_df, join_df)


# Unmatched rows on both sides: k=1 exists only in base, k=4 only in join,
# so every strategy below provably produces a different output frame.
_HOW_BASE = pl.DataFrame({"k": [1, 2, 3], "bv": ["b1", "b2", "b3"]})
_HOW_JOIN = pl.DataFrame({"k": [2, 3, 4], "jv": ["j2", "j3", "j4"]})


@pytest.mark.parametrize(
    ("how", "expected_columns", "expected_rows"),
    [
        ("inner", ["k", "bv", "jv"], [(2, "b2", "j2"), (3, "b3", "j3")]),
        ("left", ["k", "bv", "jv"], [(1, "b1", None), (2, "b2", "j2"), (3, "b3", "j3")]),
        ("right", ["bv", "k", "jv"], [("b2", 2, "j2"), ("b3", 3, "j3"), (None, 4, "j4")]),
        (
            "full",
            ["k", "bv", "k_right", "jv"],
            [
                (1, "b1", None, None),
                (2, "b2", 2, "j2"),
                (3, "b3", 3, "j3"),
                (None, None, 4, "j4"),
            ],
        ),
        ("semi", ["k", "bv"], [(2, "b2"), (3, "b3")]),
        ("anti", ["k", "bv"], [(1, "b1")]),
    ],
)
def test_edge_join_runtime_how_matrix_with_unmatched_rows_on_both_sides(
    how: str,
    expected_columns: list[str],
    expected_rows: list[tuple],
) -> None:
    result = _run_edge_join({"how": how, "on": ["k"]}, _HOW_BASE, _HOW_JOIN)
    _assert_edge_join_output(result, expected_columns, expected_rows)


def test_edge_join_runtime_cross_join_is_full_product_with_suffixed_collisions() -> None:
    result = _run_edge_join({"how": "cross"}, _HOW_BASE, _HOW_JOIN)
    # Cross joins have no keys, so the shared name ``k`` is an ordinary
    # collision and the right copy gets the default suffix.
    expected_rows = [
        (bk, bv, jk, jv)
        for bk, bv in zip([1, 2, 3], ["b1", "b2", "b3"], strict=True)
        for jk, jv in zip([2, 3, 4], ["j2", "j3", "j4"], strict=True)
    ]
    _assert_edge_join_output(result, ["k", "bv", "k_right", "jv"], expected_rows)


def test_edge_join_runtime_semi_join_dedupes_duplicate_right_matches() -> None:
    dup_join = pl.DataFrame({"k": [2, 2, 3], "jv": ["a", "b", "c"]})

    # The fixture provably multiplies under a plain left join (k=2 twice)…
    left_result = _run_edge_join({"how": "left", "on": ["k"]}, _HOW_BASE, dup_join)
    _assert_edge_join_output(
        left_result,
        ["k", "bv", "jv"],
        [(1, "b1", None), (2, "b2", "a"), (2, "b2", "b"), (3, "b3", "c")],
    )

    # …while semi keeps each matching base row exactly once with no right columns.
    semi_result = _run_edge_join({"how": "semi", "on": ["k"]}, _HOW_BASE, dup_join)
    _assert_edge_join_output(semi_result, ["k", "bv"], [(2, "b2"), (3, "b3")])


def test_edge_join_runtime_anti_join_keeps_only_unmatched_base_rows() -> None:
    dup_join = pl.DataFrame({"k": [2, 2, 3], "jv": ["a", "b", "c"]})
    result = _run_edge_join({"how": "anti", "on": ["k"]}, _HOW_BASE, dup_join)
    _assert_edge_join_output(result, ["k", "bv"], [(1, "b1")])


# -- suffix ------------------------------------------------------------------

_SUFFIX_BASE = pl.DataFrame({"k": [1], "shared": ["L"], "also": ["La"]})
_SUFFIX_JOIN = pl.DataFrame({"k": [1], "shared": ["R"], "also": ["Ra"]})


def test_edge_join_runtime_default_suffix_applies_to_every_colliding_column() -> None:
    result = _run_edge_join({"how": "inner", "on": ["k"]}, _SUFFIX_BASE, _SUFFIX_JOIN)
    _assert_edge_join_output(
        result,
        ["k", "shared", "also", "shared_right", "also_right"],
        [(1, "L", "La", "R", "Ra")],
    )


def test_edge_join_runtime_custom_suffix_applies_to_every_colliding_column() -> None:
    result = _run_edge_join(
        {"how": "inner", "on": ["k"], "suffix": "_lkp"},
        _SUFFIX_BASE,
        _SUFFIX_JOIN,
    )
    _assert_edge_join_output(
        result,
        ["k", "shared", "also", "shared_lkp", "also_lkp"],
        [(1, "L", "La", "R", "Ra")],
    )


def test_edge_join_runtime_suffix_untouched_when_no_columns_collide() -> None:
    result = _run_edge_join({"how": "inner", "on": ["k"], "suffix": "_lkp"}, _HOW_BASE, _HOW_JOIN)
    _assert_edge_join_output(result, ["k", "bv", "jv"], [(2, "b2", "j2"), (3, "b3", "j3")])
    assert not any(column.endswith("_lkp") for column in result.columns)


# -- coalesce ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("coalesce", "expected_columns", "expected_rows"),
    [
        (True, ["k", "bv", "jv"], [(2, "b2", "j2"), (3, "b3", "j3")]),
        (
            False,
            ["k", "bv", "k_right", "jv"],
            [(2, "b2", 2, "j2"), (3, "b3", 3, "j3")],
        ),
    ],
)
def test_edge_join_runtime_inner_join_coalesce_controls_key_right_column(
    coalesce: bool,
    expected_columns: list[str],
    expected_rows: list[tuple],
) -> None:
    result = _run_edge_join(
        {"how": "inner", "on": ["k"], "coalesce": coalesce},
        _HOW_BASE,
        _HOW_JOIN,
    )
    _assert_edge_join_output(result, expected_columns, expected_rows)


@pytest.mark.parametrize(
    ("how", "expected_rows"),
    [
        # k_right is null-filled on the unmatched base row (k=1) — observable
        # only under left, not derivable from the inner or full cases.
        ("left", [(1, "b1", None, None), (2, "b2", 2, "j2"), (3, "b3", 3, "j3")]),
        # Column order flips versus the right-join default ["bv", "k", "jv"]:
        # keeping the base key restores base-frame-first ordering, and the
        # unmatched join row (k=4) null-fills both base columns.
        ("right", [(2, "b2", 2, "j2"), (3, "b3", 3, "j3"), (None, None, 4, "j4")]),
    ],
)
def test_edge_join_runtime_left_right_coalesce_false_keeps_null_filled_key_right(
    how: str,
    expected_rows: list[tuple],
) -> None:
    result = _run_edge_join(
        {"how": how, "on": ["k"], "coalesce": False},
        _HOW_BASE,
        _HOW_JOIN,
    )
    _assert_edge_join_output(result, ["k", "bv", "k_right", "jv"], expected_rows)


@pytest.mark.parametrize("coalesce", [True, False])
@pytest.mark.parametrize(
    ("how", "expected_columns", "expected_rows"),
    [
        ("semi", ["k", "bv"], [(2, "b2"), (3, "b3")]),
        ("anti", ["k", "bv"], [(1, "b1")]),
    ],
)
def test_edge_join_runtime_semi_anti_accept_and_ignore_coalesce(
    how: str,
    expected_columns: list[str],
    expected_rows: list[tuple],
    coalesce: bool,
) -> None:
    # Pinned Polars semantics: semi/anti emit no right-hand columns, so
    # there is nothing to coalesce — the flag is accepted and the output
    # is identical to the unflagged join.
    result = _run_edge_join(
        {"how": how, "on": ["k"], "coalesce": coalesce},
        _HOW_BASE,
        _HOW_JOIN,
    )
    _assert_edge_join_output(result, expected_columns, expected_rows)


@pytest.mark.parametrize("coalesce", [True, False])
def test_edge_join_runtime_cross_join_ignores_coalesce(coalesce: bool) -> None:
    # Pinned Polars semantics: cross joins are keyless, so coalesce is
    # accepted and ignored — the full product with suffixed collisions is
    # unchanged (same spirit as the cross+validate pin).
    result = _run_edge_join({"how": "cross", "coalesce": coalesce}, _HOW_BASE, _HOW_JOIN)
    assert result.height == 9
    assert result.columns == ["k", "bv", "k_right", "jv"]


@pytest.mark.parametrize(
    ("config", "expected_columns", "expected_rows"),
    [
        # Polars default for full joins is coalesce=False: both key columns
        # survive, with nulls marking each side's unmatched rows.
        (
            {"how": "full", "on": ["k"]},
            ["k", "bv", "k_right", "jv"],
            [
                (1, "b1", None, None),
                (2, "b2", 2, "j2"),
                (3, "b3", 3, "j3"),
                (None, None, 4, "j4"),
            ],
        ),
        (
            {"how": "full", "on": ["k"], "coalesce": False},
            ["k", "bv", "k_right", "jv"],
            [
                (1, "b1", None, None),
                (2, "b2", 2, "j2"),
                (3, "b3", 3, "j3"),
                (None, None, 4, "j4"),
            ],
        ),
        # coalesce=True merges both key columns: k carries 4 from the right.
        (
            {"how": "full", "on": ["k"], "coalesce": True},
            ["k", "bv", "jv"],
            [(1, "b1", None), (2, "b2", "j2"), (3, "b3", "j3"), (4, None, "j4")],
        ),
    ],
    ids=["default", "coalesce_false", "coalesce_true"],
)
def test_edge_join_runtime_full_join_coalesce_controls_key_right_presence(
    config: dict,
    expected_columns: list[str],
    expected_rows: list[tuple],
) -> None:
    result = _run_edge_join(config, _HOW_BASE, _HOW_JOIN)
    _assert_edge_join_output(result, expected_columns, expected_rows)


# -- leftOn / rightOn ---------------------------------------------------------

_LO_BASE = pl.DataFrame({"lk": [1, 2], "bv": ["b1", "b2"]})
_RO_JOIN = pl.DataFrame({"rk": [2, 3], "jv": ["j2", "j3"]})


@pytest.mark.parametrize(
    ("how", "expected_columns", "expected_rows"),
    [
        ("inner", ["lk", "bv", "jv"], [(2, "b2", "j2")]),
        ("left", ["lk", "bv", "jv"], [(1, "b1", None), (2, "b2", "j2")]),
        ("right", ["bv", "rk", "jv"], [("b2", 2, "j2"), (None, 3, "j3")]),
        (
            "full",
            ["lk", "bv", "rk", "jv"],
            [(1, "b1", None, None), (2, "b2", 2, "j2"), (None, None, 3, "j3")],
        ),
        ("semi", ["lk", "bv"], [(2, "b2")]),
        ("anti", ["lk", "bv"], [(1, "b1")]),
    ],
)
def test_edge_join_runtime_left_on_right_on_matrix_with_differing_key_names(
    how: str,
    expected_columns: list[str],
    expected_rows: list[tuple],
) -> None:
    result = _run_edge_join(
        {"how": how, "leftOn": ["lk"], "rightOn": ["rk"]},
        _LO_BASE,
        _RO_JOIN,
    )
    _assert_edge_join_output(result, expected_columns, expected_rows)


def test_edge_join_runtime_full_join_left_on_right_on_coalesce_merges_into_left_key() -> None:
    result = _run_edge_join(
        {"how": "full", "leftOn": ["lk"], "rightOn": ["rk"], "coalesce": True},
        _LO_BASE,
        _RO_JOIN,
    )
    _assert_edge_join_output(
        result,
        ["lk", "bv", "jv"],
        [(1, "b1", None), (2, "b2", "j2"), (3, None, "j3")],
    )


def test_edge_join_runtime_multi_column_left_on_right_on_keys() -> None:
    base_df = pl.DataFrame({"a": [1, 1], "b": ["x", "y"], "lv": [10, 20]})
    join_df = pl.DataFrame({"c": [1, 1], "d": ["x", "z"], "rv": [100, 300]})
    result = _run_edge_join(
        {"how": "inner", "leftOn": ["a", "b"], "rightOn": ["c", "d"]},
        base_df,
        join_df,
    )
    _assert_edge_join_output(result, ["a", "b", "lv", "rv"], [(1, "x", 10, 100)])


def test_edge_join_runtime_cross_join_rejects_left_on_right_on_keys() -> None:
    _assert_edge_join_raises(
        {"how": "cross", "leftOn": ["lk"], "rightOn": ["rk"]},
        _LO_BASE,
        _RO_JOIN,
        ConfigError,
        "cross joins must not configure join keys",
    )


# -- validate ----------------------------------------------------------------

_UNIQUE_JOIN = pl.DataFrame({"k": [2, 3, 4], "jv": ["j2", "j3", "j4"]})
_DUP_KEY_BASE = pl.DataFrame({"k": [2, 2], "bv": ["x", "y"]})
_DUP_KEY_JOIN = pl.DataFrame({"k": [2, 2, 3], "jv": ["a", "b", "c"]})


@pytest.mark.parametrize(
    ("validate", "base_df", "join_df", "expected_rows"),
    [
        (
            "1:1",
            _HOW_BASE,
            _UNIQUE_JOIN,
            [(1, "b1", None), (2, "b2", "j2"), (3, "b3", "j3")],
        ),
        (
            "m:1",
            _DUP_KEY_BASE,
            _UNIQUE_JOIN,
            [(2, "x", "j2"), (2, "y", "j2")],
        ),
        (
            "1:m",
            _HOW_BASE,
            _DUP_KEY_JOIN,
            [(1, "b1", None), (2, "b2", "a"), (2, "b2", "b"), (3, "b3", "c")],
        ),
        (
            "m:m",
            _DUP_KEY_BASE,
            _DUP_KEY_JOIN,
            [(2, "x", "a"), (2, "x", "b"), (2, "y", "a"), (2, "y", "b")],
        ),
    ],
)
def test_edge_join_runtime_validate_passes_when_relationship_holds(
    validate: str,
    base_df: pl.DataFrame,
    join_df: pl.DataFrame,
    expected_rows: list[tuple],
) -> None:
    result = _run_edge_join(
        {"how": "left", "on": ["k"], "validate": validate},
        base_df,
        join_df,
    )
    _assert_edge_join_output(result, ["k", "bv", "jv"], expected_rows)


@pytest.mark.parametrize(
    ("validate", "base_df", "join_df"),
    [
        ("1:1", _HOW_BASE, _DUP_KEY_JOIN),  # right side duplicates k=2
        ("1:1", _DUP_KEY_BASE, _UNIQUE_JOIN),  # left side duplicates k=2
        ("m:1", _HOW_BASE, _DUP_KEY_JOIN),  # right side must be unique
        ("1:m", _DUP_KEY_BASE, _UNIQUE_JOIN),  # left side must be unique
    ],
    ids=["1to1_dup_right", "1to1_dup_left", "mto1_dup_right", "1tom_dup_left"],
)
def test_edge_join_runtime_validate_violation_fails_loudly(
    validate: str,
    base_df: pl.DataFrame,
    join_df: pl.DataFrame,
) -> None:
    _assert_edge_join_raises(
        {"how": "left", "on": ["k"], "validate": validate},
        base_df,
        join_df,
        pl.exceptions.ComputeError,
        f"join keys did not fulfill {validate} validation",
    )


def test_edge_join_runtime_validate_rejects_unknown_mode_loudly() -> None:
    _assert_edge_join_raises(
        {"how": "left", "on": ["k"], "validate": "bogus"},
        _HOW_BASE,
        _HOW_JOIN,
        ValueError,
        "must be one of",
    )


@pytest.mark.parametrize("how", ["semi", "anti"])
def test_edge_join_runtime_validate_unsupported_on_semi_anti_fails_loudly(how: str) -> None:
    _assert_edge_join_raises(
        {"how": how, "on": ["k"], "validate": "1:1"},
        _HOW_BASE,
        _HOW_JOIN,
        pl.exceptions.ComputeError,
        f"validation on a {how.upper()} join is not supported",
    )


def test_edge_join_runtime_cross_join_ignores_validate() -> None:
    # Pinned Polars semantics: validation describes a key relationship and
    # cross joins have no keys, so Polars accepts and ignores ``validate``.
    # If a Polars upgrade starts rejecting it, this test must be revisited
    # alongside the frontend editor's validate field.
    result = _run_edge_join({"how": "cross", "validate": "1:1"}, _HOW_BASE, _HOW_JOIN)
    assert result.height == 9
    assert result.columns == ["k", "bv", "k_right", "jv"]


def test_edge_join_runtime_validate_violation_attributed_to_join_node_in_graph_execution() -> None:
    """A validate failure must surface as the JOIN node's error, not silently.

    Executes a real graph through ``execute_graph`` (the GUI preview path):
    both sources succeed, the edge-join node alone reports the Polars
    validation message.
    """
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="base_src",
                data=NodeData(
                    label="base_src",
                    nodeType=NodeType.POLARS,
                    config={"code": 'df = pl.DataFrame({"k": [1, 2, 3], "bv": ["x", "y", "z"]})'},
                ),
            ),
            GraphNode(
                id="lookup_src",
                data=NodeData(
                    label="lookup_src",
                    nodeType=NodeType.POLARS,
                    config={"code": 'df = pl.DataFrame({"k": [2, 2, 3], "jv": ["a", "b", "c"]})'},
                ),
            ),
            _edge_join_node(
                {
                    "baseInput": "base_src",
                    "joinInput": "lookup_src",
                    "how": "left",
                    "on": ["k"],
                    "validate": "1:1",
                }
            ),
        ],
        edges=[
            GraphEdge(id="e_base", source="base_src", target="join", targetHandle="base"),
            GraphEdge(id="e_lookup", source="lookup_src", target="join", targetHandle="join"),
        ],
    )

    results = execute_graph(graph)

    assert results["base_src"].status == "ok"
    assert results["lookup_src"].status == "ok"
    assert results["join"].status == "error"
    assert "1:1 validation" in (results["join"].error or "")


# -- join-key dtype edges ------------------------------------------------------


def test_edge_join_runtime_mismatched_key_dtypes_fail_loudly() -> None:
    # Int keys joined against str keys must error, never silently produce
    # an empty or wrong frame.
    str_join = pl.DataFrame({"k": ["1", "2"], "jv": ["a", "b"]})
    _assert_edge_join_raises(
        {"how": "inner", "on": ["k"]},
        _HOW_BASE,
        str_join,
        pl.exceptions.SchemaError,
        "datatypes of join keys don't match",
    )


def test_edge_join_runtime_numeric_key_widths_upcast_to_supertype() -> None:
    # Pinned Polars semantics: Int64 vs Int32 keys join via the numeric
    # supertype (a documented cast, not silence — values still match).
    i32_join = pl.DataFrame({"k": pl.Series([2, 3], dtype=pl.Int32), "jv": ["a", "b"]})
    result = _run_edge_join({"how": "inner", "on": ["k"]}, _HOW_BASE, i32_join)
    _assert_edge_join_output(result, ["k", "bv", "jv"], [(2, "b2", "a"), (3, "b3", "b")])
    assert result.schema["k"] == pl.Int64
