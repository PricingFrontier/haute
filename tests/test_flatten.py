"""Tests for haute._flatten — graph flattening of submodel nodes."""

from __future__ import annotations

from haute._flatten import flatten_graph
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, ntype: str = "polars", config: dict | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=ntype, config=config or {}),
    )


def _edge(
    src: str,
    tgt: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=f"e_{src}_{tgt}",
        source=src,
        target=tgt,
        sourceHandle=source_handle,
        targetHandle=target_handle,
    )


# ---------------------------------------------------------------------------
# No submodels — graph returned unchanged
# ---------------------------------------------------------------------------


class TestNoSubmodels:
    def test_no_submodels_returns_same_graph(self) -> None:
        graph = PipelineGraph(
            nodes=[_node("a"), _node("b")],
            edges=[_edge("a", "b")],
            submodels=None,
        )
        result = flatten_graph(graph)
        assert result is graph

    def test_empty_submodels_dict_returns_same_graph(self) -> None:
        graph = PipelineGraph(
            nodes=[_node("a")],
            edges=[],
            submodels={},
        )
        result = flatten_graph(graph)
        assert result is graph


# ---------------------------------------------------------------------------
# Target name not in submodels — graph returned unchanged
# ---------------------------------------------------------------------------


class TestTargetNameNotInSubmodels:
    def test_target_name_not_matching_returns_same_graph(self) -> None:
        graph = PipelineGraph(
            nodes=[_node("a"), _node("submodel__freq")],
            edges=[],
            submodels={"freq": {"graph": {"nodes": [], "edges": []}}},
        )
        result = flatten_graph(graph, target_name="nonexistent")
        assert result is graph


# ---------------------------------------------------------------------------
# Flatten all submodels (target_name=None)
# ---------------------------------------------------------------------------


class TestFlattenAll:
    def test_single_submodel_flattened(self) -> None:
        """A single submodel with one child node is dissolved."""
        graph = PipelineGraph(
            nodes=[
                _node("data_src"),
                _node("submodel__freq"),
                _node("output"),
            ],
            edges=[
                _edge("data_src", "submodel__freq", target_handle="in__freq_child"),
                _edge("submodel__freq", "output", source_handle="out__freq_child"),
            ],
            submodels={
                "freq": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "freq_child",
                                "data": {"label": "freq_child", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)

        node_ids = {n.id for n in result.nodes}
        # Submodel placeholder removed, child node inlined
        assert "submodel__freq" not in node_ids
        assert "freq_child" in node_ids
        assert "data_src" in node_ids
        assert "output" in node_ids

        # Edges rewired: data_src -> freq_child -> output
        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("data_src", "freq_child") in edge_pairs
        assert ("freq_child", "output") in edge_pairs

        # Submodels cleared
        assert result.submodels is None

    def test_multiple_submodels_flattened(self) -> None:
        """Two submodels are both dissolved when target_name is None."""
        graph = PipelineGraph(
            nodes=[
                _node("data"),
                _node("submodel__alpha"),
                _node("submodel__beta"),
                _node("out"),
            ],
            edges=[
                _edge("data", "submodel__alpha", target_handle="in__alpha_child"),
                _edge(
                    "submodel__alpha",
                    "submodel__beta",
                    source_handle="out__alpha_child",
                    target_handle="in__beta_child",
                ),
                _edge("submodel__beta", "out", source_handle="out__beta_child"),
            ],
            submodels={
                "alpha": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "alpha_child",
                                "data": {
                                    "label": "alpha_child",
                                    "nodeType": "polars",
                                    "config": {},
                                },
                            },
                        ],
                        "edges": [],
                    }
                },
                "beta": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "beta_child",
                                "data": {"label": "beta_child", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [],
                    }
                },
            },
        )
        result = flatten_graph(graph)

        node_ids = {n.id for n in result.nodes}
        assert "submodel__alpha" not in node_ids
        assert "submodel__beta" not in node_ids
        assert "alpha_child" in node_ids
        assert "beta_child" in node_ids

        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("data", "alpha_child") in edge_pairs
        assert ("alpha_child", "beta_child") in edge_pairs
        assert ("beta_child", "out") in edge_pairs

        assert result.submodels is None


# ---------------------------------------------------------------------------
# Flatten targeted submodel only
# ---------------------------------------------------------------------------


class TestFlattenTargeted:
    def test_only_targeted_submodel_flattened(self) -> None:
        """When target_name is given, only that submodel is dissolved."""
        graph = PipelineGraph(
            nodes=[
                _node("data"),
                _node("submodel__alpha"),
                _node("submodel__beta"),
                _node("out"),
            ],
            edges=[
                _edge("data", "submodel__alpha", target_handle="in__alpha_child"),
                _edge("submodel__alpha", "out", source_handle="out__alpha_child"),
                _edge("data", "submodel__beta"),
            ],
            submodels={
                "alpha": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "alpha_child",
                                "data": {
                                    "label": "alpha_child",
                                    "nodeType": "polars",
                                    "config": {},
                                },
                            },
                        ],
                        "edges": [],
                    }
                },
                "beta": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "beta_child",
                                "data": {"label": "beta_child", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [],
                    }
                },
            },
        )
        result = flatten_graph(graph, target_name="alpha")

        node_ids = {n.id for n in result.nodes}
        # alpha dissolved
        assert "submodel__alpha" not in node_ids
        assert "alpha_child" in node_ids
        # beta preserved
        assert "submodel__beta" in node_ids
        assert "beta_child" not in node_ids

        # remaining_submodels should only have beta
        assert result.submodels is not None
        assert "beta" in result.submodels
        assert "alpha" not in result.submodels


# ---------------------------------------------------------------------------
# Edge rewiring details
# ---------------------------------------------------------------------------


class TestEdgeRewiring:
    def test_source_handle_rewired(self) -> None:
        """out__child_node on a submodel edge rewires source to child_node."""
        graph = PipelineGraph(
            nodes=[
                _node("submodel__sm"),
                _node("downstream"),
            ],
            edges=[
                _edge("submodel__sm", "downstream", source_handle="out__inner"),
            ],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "inner",
                                "data": {"label": "inner", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("inner", "downstream") in edge_pairs
        # sourceHandle should be cleared after rewiring
        rewired = [e for e in result.edges if e.source == "inner" and e.target == "downstream"]
        assert rewired[0].sourceHandle is None

    def test_target_handle_rewired(self) -> None:
        """in__child_node on a submodel edge rewires target to child_node."""
        graph = PipelineGraph(
            nodes=[
                _node("upstream"),
                _node("submodel__sm"),
            ],
            edges=[
                _edge("upstream", "submodel__sm", target_handle="in__inner"),
            ],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "inner",
                                "data": {"label": "inner", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("upstream", "inner") in edge_pairs
        rewired = [e for e in result.edges if e.source == "upstream" and e.target == "inner"]
        assert rewired[0].targetHandle is None

    def test_edges_still_referencing_submodel_dropped(self) -> None:
        """Edges that still reference a submodel node after rewiring are dropped."""
        graph = PipelineGraph(
            nodes=[
                _node("a"),
                _node("submodel__sm"),
                _node("b"),
            ],
            edges=[
                # Edge without handles — source stays as submodel__sm, gets dropped
                _edge("submodel__sm", "b"),
            ],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        # Edge from submodel__sm to b without handles cannot be rewired
        assert len(result.edges) == 0

    def test_internal_submodel_edges_preserved(self) -> None:
        """Internal edges within a submodel graph are added to the flat graph."""
        graph = PipelineGraph(
            nodes=[
                _node("submodel__sm"),
            ],
            edges=[],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "child_a",
                                "data": {"label": "a", "nodeType": "polars", "config": {}},
                            },
                            {
                                "id": "child_b",
                                "data": {"label": "b", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [
                            {"id": "e_ca_cb", "source": "child_a", "target": "child_b"},
                        ],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("child_a", "child_b") in edge_pairs


# ---------------------------------------------------------------------------
# Edge deduplication
# ---------------------------------------------------------------------------


class TestEdgeDeduplication:
    def test_duplicate_edges_deduplicated(self) -> None:
        """If the same (source, target, sourceHandle, targetHandle) appears
        multiple times after rewiring, only one copy is kept."""
        graph = PipelineGraph(
            nodes=[
                _node("upstream"),
                _node("submodel__sm"),
                _node("downstream"),
            ],
            edges=[
                # Two edges that will rewire to the same (upstream, inner) pair
                GraphEdge(
                    id="e1", source="upstream", target="submodel__sm", targetHandle="in__inner"
                ),
                GraphEdge(
                    id="e2", source="upstream", target="submodel__sm", targetHandle="in__inner"
                ),
            ],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "inner",
                                "data": {"label": "inner", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        # Both edges rewire to (upstream, inner, None, None) — should be deduped to 1
        matching = [
            (e.source, e.target)
            for e in result.edges
            if e.source == "upstream" and e.target == "inner"
        ]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# Submodel with empty graph
# ---------------------------------------------------------------------------


class TestEmptySubmodelGraph:
    def test_empty_submodel_graph_dissolves_cleanly(self) -> None:
        """A submodel with no children or edges is just removed."""
        graph = PipelineGraph(
            nodes=[_node("a"), _node("submodel__empty")],
            edges=[],
            submodels={
                "empty": {
                    "graph": {
                        "nodes": [],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        node_ids = {n.id for n in result.nodes}
        assert "submodel__empty" not in node_ids
        assert "a" in node_ids
        assert result.submodels is None

    def test_submodel_with_missing_graph_key(self) -> None:
        """A submodel entry with no 'graph' key should not crash."""
        graph = PipelineGraph(
            nodes=[_node("a"), _node("submodel__sm")],
            edges=[],
            submodels={"sm": {}},
        )
        result = flatten_graph(graph)
        node_ids = {n.id for n in result.nodes}
        assert "submodel__sm" not in node_ids


# ---------------------------------------------------------------------------
# Graph metadata preserved
# ---------------------------------------------------------------------------


class TestMetadataPreserved:
    def test_pipeline_name_preserved(self) -> None:
        graph = PipelineGraph(
            nodes=[_node("submodel__sm")],
            edges=[],
            pipeline_name="test_pipeline",
            submodels={"sm": {"graph": {"nodes": [], "edges": []}}},
        )
        result = flatten_graph(graph)
        assert result.pipeline_name == "test_pipeline"

    def test_other_fields_preserved(self) -> None:
        graph = PipelineGraph(
            nodes=[_node("submodel__sm")],
            edges=[],
            pipeline_description="desc",
            preamble="preamble_code",
            sources=["live", "test"],
            active_source="test",
            submodels={"sm": {"graph": {"nodes": [], "edges": []}}},
        )
        result = flatten_graph(graph)
        assert result.pipeline_description == "desc"
        assert result.preamble == "preamble_code"
        assert result.sources == ["live", "test"]
        assert result.active_source == "test"


# ---------------------------------------------------------------------------
# Child nodes as dicts vs GraphNode objects
# ---------------------------------------------------------------------------


class TestChildNodeFormats:
    def test_child_nodes_as_graph_node_objects(self) -> None:
        """Child nodes that are already GraphNode objects (not dicts)."""
        child = _node("child_obj")
        graph = PipelineGraph(
            nodes=[_node("submodel__sm")],
            edges=[],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [child],
                        "edges": [],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        node_ids = {n.id for n in result.nodes}
        assert "child_obj" in node_ids

    def test_child_edges_as_graph_edge_objects(self) -> None:
        """Child edges that are already GraphEdge objects (not dicts)."""
        child_edge = _edge("c1", "c2")
        graph = PipelineGraph(
            nodes=[_node("submodel__sm")],
            edges=[],
            submodels={
                "sm": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "c1",
                                "data": {"label": "c1", "nodeType": "polars", "config": {}},
                            },
                            {
                                "id": "c2",
                                "data": {"label": "c2", "nodeType": "polars", "config": {}},
                            },
                        ],
                        "edges": [child_edge],
                    }
                }
            },
        )
        result = flatten_graph(graph)
        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("c1", "c2") in edge_pairs
