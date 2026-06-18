"""Round-trip contract for user-editable input binding aliases.

A node's input binding (parameter) name is normally derived from the
upstream node's label.  ``GraphEdge.inputAlias`` lets a user override that
name per connection.  Because node inputs are wired *positionally* via
``pipeline.connect()`` edges — not by parameter name (see
``pipeline.run`` calling ``n(*input_dfs)``) — an alias is purely a
**codegen + parser** concern: codegen emits the parameter under the
chosen name, and the parser recovers it on round-trip by position.

These tests are the load-bearing invariant: a graph edge carrying
``inputAlias`` survives graph -> code -> parse.  The guard cases pin the
boundaries (edge-join role inputs are NOT aliasable; colliding aliases
fail loudly; wiring never depends on the alias).
"""

from __future__ import annotations

from pathlib import Path

from haute._config_io import collect_node_configs
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.codegen import graph_to_code
from haute.errors import ParseError
from haute.parser import parse_pipeline_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_configs(graph: PipelineGraph, base_dir: Path) -> None:
    for rel_path, content in collect_node_configs(graph).items():
        abs_path = base_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)


def _roundtrip(graph: PipelineGraph, base_dir: Path) -> PipelineGraph:
    code = graph_to_code(graph, pipeline_name="alias_test")
    _write_configs(graph, base_dir)
    return parse_pipeline_source(code, source_file="alias.py", _base_dir=base_dir)


def _source(node_id: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=node_id,
            nodeType=NodeType.DATA_SOURCE,
            config={"path": f"{node_id}.parquet", "sourceType": "flat_file"},
        ),
    )


def _transform(node_id: str, code: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=NodeType.POLARS, config={"code": code}),
    )


def _edge_to(
    target: str,
    source: str,
    *,
    alias: str | None = None,
    eid: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=eid or f"e_{source}_{target}",
        source=source,
        target=target,
        inputAlias=alias,
    )


def _inbound(graph: PipelineGraph, target: str, source: str) -> GraphEdge:
    return next(e for e in graph.edges if e.target == target and e.source == source)


# ---------------------------------------------------------------------------
# Single-input alias
# ---------------------------------------------------------------------------


def test_single_input_alias_emits_param_name() -> None:
    """Codegen emits the input parameter under the chosen alias."""
    graph = PipelineGraph(
        nodes=[_source("src"), _transform("clean", "df = df.select(pl.all())")],
        edges=[_edge_to("clean", "src", alias="renamed_input")],
        pipeline_name="alias_test",
    )
    code = graph_to_code(graph, pipeline_name="alias_test")
    assert "def clean(renamed_input: pl.LazyFrame)" in code
    # The alias is the parameter name; the body still threads it through ``df``.
    assert "    df = renamed_input" in code


def test_single_input_alias_roundtrips(tmp_path: Path) -> None:
    """A single-input alias survives graph -> code -> parse by position."""
    graph = PipelineGraph(
        nodes=[_source("src"), _transform("clean", "df = df.select(pl.all())")],
        edges=[_edge_to("clean", "src", alias="renamed_input")],
        pipeline_name="alias_test",
    )
    parsed = _roundtrip(graph, tmp_path)
    edge = _inbound(parsed, "clean", "src")
    assert edge.inputAlias == "renamed_input"


def test_alias_with_spaces_is_sanitised(tmp_path: Path) -> None:
    """A non-identifier alias is sanitised to a valid parameter name."""
    graph = PipelineGraph(
        nodes=[_source("src"), _transform("clean", "df = df.select(pl.all())")],
        edges=[_edge_to("clean", "src", alias="my alias")],
        pipeline_name="alias_test",
    )
    code = graph_to_code(graph, pipeline_name="alias_test")
    assert "def clean(my_alias: pl.LazyFrame)" in code
    parsed = _roundtrip(graph, tmp_path)
    assert _inbound(parsed, "clean", "src").inputAlias == "my_alias"


# ---------------------------------------------------------------------------
# Multi-input alias
# ---------------------------------------------------------------------------


def test_multi_input_alias_roundtrips(tmp_path: Path) -> None:
    """Aliasing one of several inputs survives, matched by position."""
    graph = PipelineGraph(
        nodes=[
            _source("policies"),
            _source("claims"),
            _transform("merge", 'df = claims_2024.join(policies, on="id")'),
        ],
        edges=[
            _edge_to("merge", "policies"),
            _edge_to("merge", "claims", alias="claims_2024"),
        ],
        pipeline_name="alias_test",
    )
    code = graph_to_code(graph, pipeline_name="alias_test")
    assert "def merge(policies: pl.LazyFrame, claims_2024: pl.LazyFrame)" in code

    parsed = _roundtrip(graph, tmp_path)
    # The aliased edge carries the alias; the unaliased one does not.
    assert _inbound(parsed, "merge", "claims").inputAlias == "claims_2024"
    assert _inbound(parsed, "merge", "policies").inputAlias is None


def test_no_alias_is_backward_compatible(tmp_path: Path) -> None:
    """With no alias, params are derived from source labels and no alias is recovered."""
    graph = PipelineGraph(
        nodes=[
            _source("policies"),
            _source("claims"),
            _transform("merge", 'df = claims.join(policies, on="id")'),
        ],
        edges=[
            _edge_to("merge", "policies"),
            _edge_to("merge", "claims"),
        ],
        pipeline_name="alias_test",
    )
    code = graph_to_code(graph, pipeline_name="alias_test")
    assert "def merge(policies: pl.LazyFrame, claims: pl.LazyFrame)" in code

    parsed = _roundtrip(graph, tmp_path)
    for src in ("policies", "claims"):
        assert _inbound(parsed, "merge", src).inputAlias is None


def test_alias_does_not_change_positional_wiring() -> None:
    """The alias is cosmetic for wiring: connect() still names source/target funcs."""
    graph = PipelineGraph(
        nodes=[
            _source("policies"),
            _source("claims"),
            _transform("merge", 'df = claims_2024.join(policies, on="id")'),
        ],
        edges=[
            _edge_to("merge", "policies"),
            _edge_to("merge", "claims", alias="claims_2024"),
        ],
        pipeline_name="alias_test",
    )
    code = graph_to_code(graph, pipeline_name="alias_test")
    assert 'pipeline.connect("claims", "merge")' in code
    assert "claims_2024" not in code.split("# Wire nodes together")[1]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_edge_join_ignores_input_alias(tmp_path: Path) -> None:
    """Edge-join inputs are role-driven; an alias on a role edge is ignored.

    Aliasing would break the ``base_input=`` / ``join_input=`` decorator
    round-trip (those name the source funcs), so edge-join is not aliasable.
    """
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
                    config={"values": [{"name": "factor", "value": "1.1"}]},
                ),
            ),
            GraphNode(
                id="join",
                data=NodeData(
                    label="join",
                    nodeType=NodeType.EDGE_JOIN,
                    config={
                        "baseInput": "quotes",
                        "joinInput": "lookup",
                        "how": "left",
                        "on": ["region"],
                        "suffix": "_lookup",
                    },
                ),
            ),
        ],
        edges=[
            GraphEdge(
                id="e_quotes_join",
                source="quotes",
                target="join",
                targetHandle="base",
                inputAlias="should_be_ignored",
            ),
            GraphEdge(
                id="e_lookup_join",
                source="lookup",
                target="join",
                targetHandle="join",
            ),
        ],
        pipeline_name="alias_test",
    )
    code = graph_to_code(graph, pipeline_name="alias_test")
    # The alias is ignored: the role param keeps the source func name.
    assert "def join(quotes: pl.LazyFrame, lookup: pl.LazyFrame)" in code
    assert "should_be_ignored" not in code
    assert 'base_input="quotes"' in code

    parsed = _roundtrip(graph, tmp_path)
    join_node = parsed.node_map["join"]
    assert join_node.data.config["baseInput"] == "quotes"
    assert join_node.data.config["joinInput"] == "lookup"
    # No spurious alias recovered on the role edges.
    assert _inbound(parsed, "join", "quotes").inputAlias is None
    assert _inbound(parsed, "join", "lookup").inputAlias is None


def test_colliding_aliases_fail_loudly() -> None:
    """Two inputs aliased to the same param name is a hard error, not silent shadowing."""
    graph = PipelineGraph(
        nodes=[
            _source("policies"),
            _source("claims"),
            _transform("merge", "df = dup"),
        ],
        edges=[
            _edge_to("merge", "policies", alias="dup"),
            _edge_to("merge", "claims", alias="dup"),
        ],
        pipeline_name="alias_test",
    )
    try:
        graph_to_code(graph, pipeline_name="alias_test")
    except ParseError:
        return
    raise AssertionError("expected a ParseError for colliding input aliases")
