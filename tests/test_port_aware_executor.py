"""Port-aware executor behavior.

These tests cover the live pieces that keep multi-port data flow explicit:

1. ``GraphEdge.sourceHandle`` / ``.targetHandle`` reject empty string at
   Pydantic ingest. Null means "no port specified"; empty string is an
   invalid serialisation that surfaces immediately.

2. ``PreparedGraph.relevant_edges`` and ``_prepare_graph_with_edges``
   expose the post-pruning, ancestor-filtered edge list. The executor
   uses this to look up per-edge ``sourceHandle`` when picking frames
   from multi-port sources.

3. Framework function wrappers accept both positional and keyword forms.
   Direct callers and deploy paths keep working while port-aware routing
   happens at the source-emit layer by picking frames per edge.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl
import pytest
from pydantic import ValidationError

from haute._execute_lazy import (
    _build_funcs,
    _execute_eager_core,
    _prepare_graph,
    _prepare_graph_with_edges,
)
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ConfigError

# 1. Pydantic validator rejects "" handles


def test_edge_accepts_null_source_handle() -> None:
    edge = GraphEdge(id="e", source="a", target="b")
    assert edge.sourceHandle is None
    assert edge.targetHandle is None


def test_edge_accepts_nonempty_string_source_handle() -> None:
    edge = GraphEdge(id="e", source="a", target="b", sourceHandle="policies")
    assert edge.sourceHandle == "policies"


def test_edge_rejects_empty_source_handle() -> None:
    with pytest.raises(ValidationError, match="Edge handle must be either"):
        GraphEdge(id="e", source="a", target="b", sourceHandle="")


def test_edge_rejects_empty_target_handle() -> None:
    with pytest.raises(ValidationError, match="Edge handle must be either"):
        GraphEdge(id="e", source="a", target="b", targetHandle="")


# 2. PreparedGraph.relevant_edges + _prepare_graph_with_edges


def _simple_polars_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(
                    label="src",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "ignored.parquet"},
                ),
            ),
            GraphNode(
                id="tfm",
                data=NodeData(
                    label="tfm",
                    nodeType=NodeType.POLARS,
                    config={"code": "df = df.with_columns(y=pl.col('x') * 2)"},
                ),
            ),
        ],
        edges=[GraphEdge(id="e_src_tfm", source="src", target="tfm")],
    )


def test_prepare_graph_with_edges_exposes_relevant_edges() -> None:
    g = _simple_polars_graph()
    node_map, order, parents_of, id_to_name, relevant_edges = _prepare_graph_with_edges(g)
    assert [e.source for e in relevant_edges] == ["src"]
    assert [e.target for e in relevant_edges] == ["tfm"]
    # Backward-compat helper still returns the 4-tuple it always did.
    legacy = _prepare_graph(g)
    assert len(legacy) == 4
    assert legacy[2] == parents_of
    assert node_map["src"].data.label == "src"
    assert order == ["src", "tfm"]
    assert id_to_name["tfm"] == "tfm"


def test_prepare_graph_with_edges_prunes_live_switch_inactive_edges() -> None:
    """Only the active source's edge survives in ``relevant_edges``."""
    g = PipelineGraph(
        nodes=[
            GraphNode(
                id="batch",
                data=NodeData(
                    label="batch",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "ignored.parquet"},
                ),
            ),
            GraphNode(
                id="live",
                data=NodeData(
                    label="live",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "ignored.parquet"},
                ),
            ),
            GraphNode(
                id="sw",
                data=NodeData(
                    label="sw",
                    nodeType=NodeType.LIVE_SWITCH,
                    config={"input_scenario_map": {"batch": "nb_batch", "live": "live"}},
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e_batch_sw", source="batch", target="sw"),
            GraphEdge(id="e_live_sw", source="live", target="sw"),
        ],
        sources=["live", "nb_batch"],
        active_source="live",
    )
    _, _, parents_of, _, relevant_edges = _prepare_graph_with_edges(g, source="live")
    edges_into_sw = [e for e in relevant_edges if e.target == "sw"]
    assert len(edges_into_sw) == 1
    assert edges_into_sw[0].source == "live"
    assert parents_of.get("sw") == ["live"]


def test_dead_build_input_kwargs_api_is_absent() -> None:
    import haute._execute_lazy as execute_lazy

    assert not hasattr(execute_lazy, "_build_input_kwargs")


# 3. Framework wrappers accept positional AND kwarg forms


def test_passthrough_fn_accepts_positional() -> None:
    """Existing positional callers (tests, deploy code) still work."""
    from haute._builders import _passthrough_fn

    frame = pl.LazyFrame({"x": [1, 2]})
    result = _passthrough_fn(frame)
    assert result is frame


def test_passthrough_fn_accepts_keyword() -> None:
    """Keyword callers get the same behaviour."""
    from haute._builders import _passthrough_fn

    frame = pl.LazyFrame({"x": [1, 2]})
    result = _passthrough_fn(some_label=frame)
    assert result is frame


def test_passthrough_fn_returns_first_when_multiple() -> None:
    """The wrapper preserves insertion order for first-input semantics."""
    from haute._builders import _passthrough_fn

    frame_a = pl.LazyFrame({"x": [1]})
    frame_b = pl.LazyFrame({"y": [2]})
    assert _passthrough_fn(frame_a, frame_b) is frame_a
    assert _passthrough_fn(first=frame_a, second=frame_b) is frame_a


def test_passthrough_fn_empty_returns_empty_lazyframe() -> None:
    from haute._builders import _passthrough_fn

    result = _passthrough_fn()
    assert isinstance(result, pl.LazyFrame)
    assert result.collect().shape == (0, 0)


# 4. Every executable input name is derived from its own incoming edge


def _node(
    node_id: str,
    label: str,
    node_type: NodeType,
    *,
    config: dict[str, Any] | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=label, nodeType=node_type, config=config or {}),
    )


def _capture_builder_calls(
    captured: dict[str, dict[str, Any]],
) -> Callable[..., tuple[str, Callable[..., Any], bool]]:
    def build_node_fn(
        node: GraphNode,
        *,
        source_names: list[str] | None = None,
        orig_source_names: list[str] | None = None,
        **_kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        captured[node.id] = {
            "source_names": list(source_names or []),
            "orig_source_names": (None if orig_source_names is None else list(orig_source_names)),
        }
        is_source = node.data.nodeType in {NodeType.API_INPUT, NodeType.DATA_SOURCE}
        return node.id, lambda *frames: frames[0] if frames else pl.LazyFrame(), is_source

    return build_node_fn


def _build_graph_funcs(
    graph: PipelineGraph,
    build_node_fn: Callable[..., tuple[str, Callable[..., Any], bool]],
) -> None:
    node_map, order, parents_of, id_to_name, relevant_edges = _prepare_graph_with_edges(graph)
    incoming: dict[str, list[GraphEdge]] = {}
    for edge in relevant_edges:
        incoming.setdefault(edge.target, []).append(edge)
    _build_funcs(
        order,
        node_map,
        parents_of,
        id_to_name,
        graph.parents_of,
        build_node_fn,
        incoming_edges_by_target=incoming,
    )


def test_build_funcs_derives_each_input_name_from_its_edge_in_declaration_order() -> None:
    """API frames stay verbatim; ordinary and flattened child nodes are sanitised."""
    graph = PipelineGraph(
        nodes=[
            _node("api", "API Input", NodeType.API_INPUT),
            _node("ordinary", "Ordinary Source", NodeType.DATA_SOURCE),
            # Submodel expansion has already replaced the boundary placeholder
            # with this real child node by the time the executor sees the graph.
            _node("child", "Frequency Model Child", NodeType.POLARS),
            _node("target", "target", NodeType.POLARS),
        ],
        edges=[
            GraphEdge(
                id="api_target",
                source="api",
                target="target",
                sourceHandle="quotes",
            ),
            GraphEdge(id="ordinary_target", source="ordinary", target="target"),
            GraphEdge(id="child_target", source="child", target="target"),
        ],
    )
    captured: dict[str, dict[str, Any]] = {}

    _build_graph_funcs(graph, _capture_builder_calls(captured))

    assert captured["target"]["source_names"] == [
        "quotes",
        "Ordinary_Source",
        "Frequency_Model_Child",
    ]


def test_single_frame_dict_source_binds_under_its_raw_frame_label() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("api", "Quote Input", NodeType.API_INPUT),
            _node("target", "target", NodeType.POLARS),
        ],
        edges=[
            GraphEdge(
                id="api_target",
                source="api",
                target="target",
                sourceHandle="quotes",
            )
        ],
    )
    observed: dict[str, Any] = {}

    def build_node_fn(
        node: GraphNode,
        *,
        source_names: list[str] | None = None,
        **_kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        if node.id == "api":
            return node.id, lambda: {"quotes": pl.LazyFrame({"quote_id": [101]})}, True

        def consume(frame: pl.LazyFrame) -> pl.LazyFrame:
            observed["source_names"] = list(source_names or [])
            observed["quote_ids"] = frame.collect()["quote_id"].to_list()
            return frame

        return node.id, consume, False

    _execute_eager_core(graph, build_node_fn, swallow_errors=False)

    assert observed == {"source_names": ["quotes"], "quote_ids": [101]}


def test_execute_lazy_graph_binds_api_frame_under_raw_edge_name() -> None:
    """The public lazy production entry point must supply incoming edge metadata."""
    from haute.execution import execute_lazy_graph

    graph = PipelineGraph(
        nodes=[
            _node("api", "Quote Input", NodeType.API_INPUT),
            _node("target", "target", NodeType.POLARS),
        ],
        edges=[
            GraphEdge(
                id="api_target",
                source="api",
                target="target",
                sourceHandle="quotes",
            )
        ],
    )
    observed_names: list[str] = []

    def build_node_fn(
        node: GraphNode,
        *,
        source_names: list[str] | None = None,
        **_kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        if node.id == "api":
            return node.id, lambda: {"quotes": pl.LazyFrame({"quote_id": [101]})}, True

        def consume(frame: pl.LazyFrame) -> pl.LazyFrame:
            observed_names.extend(source_names or [])
            return frame

        return node.id, consume, False

    outputs, *_ = execute_lazy_graph(graph, build_node_fn, target_node_id="target")

    assert outputs["target"].collect()["quote_id"].to_list() == [101]
    assert observed_names == ["quotes"]


def test_reconnecting_api_input_edges_in_reverse_keeps_names_bound_to_their_frames() -> None:
    def execute(edge_order: list[str]) -> list[tuple[str, str]]:
        graph = PipelineGraph(
            nodes=[
                _node("api", "API Input", NodeType.API_INPUT),
                _node("target", "target", NodeType.POLARS),
            ],
            edges=[
                GraphEdge(
                    id=f"api_target_{port}",
                    source="api",
                    target="target",
                    sourceHandle=port,
                )
                for port in edge_order
            ],
        )
        observed: list[tuple[str, str]] = []

        def build_node_fn(
            node: GraphNode,
            *,
            source_names: list[str] | None = None,
            **_kwargs: Any,
        ) -> tuple[str, Callable[..., Any], bool]:
            if node.id == "api":
                return (
                    node.id,
                    lambda: {
                        "quotes": pl.LazyFrame({"origin": ["quote-frame"]}),
                        "drivers": pl.LazyFrame({"origin": ["driver-frame"]}),
                    },
                    True,
                )

            def consume(*frames: pl.LazyFrame) -> pl.LazyFrame:
                observed.extend(
                    (name, frame.collect()["origin"][0])
                    for name, frame in zip(source_names or [], frames, strict=True)
                )
                return frames[0]

            return node.id, consume, False

        _execute_eager_core(graph, build_node_fn, swallow_errors=False)
        return observed

    assert execute(["quotes", "drivers"]) == [
        ("quotes", "quote-frame"),
        ("drivers", "driver-frame"),
    ]
    assert execute(["drivers", "quotes"]) == [
        ("drivers", "driver-frame"),
        ("quotes", "quote-frame"),
    ]


def test_duplicate_derived_input_name_raises_config_error_naming_target_and_name() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("api", "API Input", NodeType.API_INPUT),
            _node("ordinary", "shared name", NodeType.DATA_SOURCE),
            _node("pricing", "pricing", NodeType.POLARS),
        ],
        edges=[
            GraphEdge(
                id="api_pricing",
                source="api",
                target="pricing",
                sourceHandle="shared_name",
            ),
            GraphEdge(id="ordinary_pricing", source="ordinary", target="pricing"),
        ],
    )

    with pytest.raises(ConfigError) as exc_info:
        _build_graph_funcs(graph, _capture_builder_calls({}))

    message = str(exc_info.value)
    assert "pricing" in message
    assert "shared_name" in message


def test_null_handle_against_even_a_one_frame_dict_fails_loudly() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("api", "API Input", NodeType.API_INPUT),
            _node("target", "target", NodeType.POLARS),
        ],
        edges=[GraphEdge(id="api_target", source="api", target="target")],
    )

    def build_node_fn(
        node: GraphNode,
        **_kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        if node.id == "api":
            return node.id, lambda: {"quotes": pl.LazyFrame({"x": [1]})}, True
        return node.id, lambda frame: frame, False

    with pytest.raises(ValueError) as exc_info:
        _execute_eager_core(graph, build_node_fn, swallow_errors=False)

    message = str(exc_info.value)
    assert "api" in message
    assert "sourceHandle" in message


def test_instance_original_input_names_come_from_original_incoming_edges() -> None:
    graph = PipelineGraph(
        nodes=[
            _node("api", "API Input", NodeType.API_INPUT),
            _node("ordinary", "Ordinary Source", NodeType.DATA_SOURCE),
            _node("original", "original", NodeType.POLARS),
            _node("instance_source", "Instance Source", NodeType.DATA_SOURCE),
            _node(
                "instance",
                "instance",
                NodeType.POLARS,
                config={"instanceOf": "original"},
            ),
        ],
        edges=[
            GraphEdge(
                id="api_original",
                source="api",
                target="original",
                sourceHandle="quotes",
            ),
            GraphEdge(id="ordinary_original", source="ordinary", target="original"),
            GraphEdge(id="instance_source_instance", source="instance_source", target="instance"),
        ],
    )
    captured: dict[str, dict[str, Any]] = {}

    _build_graph_funcs(graph, _capture_builder_calls(captured))

    assert captured["instance"]["orig_source_names"] == ["quotes", "Ordinary_Source"]


def test_optimiser_linear_builder_uses_original_edge_metadata_for_input_name() -> None:
    """The execution.py chain used by optimiser execution keeps the frame label."""
    from haute.execution import build_linear_execution_chain_functions

    graph = PipelineGraph(
        nodes=[
            _node("api", "Quote Input", NodeType.API_INPUT),
            _node("transform", "transform", NodeType.POLARS),
        ],
        edges=[
            GraphEdge(
                id="api_transform",
                source="api",
                target="transform",
                sourceHandle="quotes",
            )
        ],
    )
    captured: dict[str, dict[str, Any]] = {}

    build_linear_execution_chain_functions(
        graph,
        _capture_builder_calls(captured),
        target_node_id="transform",
        base_node_id="api",
        chain_node_ids=["transform"],
    )

    assert captured["transform"]["source_names"] == ["quotes"]
