"""Witness for the submodel ``out__<child_id>`` sourceHandle invariant.

Under the unified port meaning (MULTI_FRAME_PLAN commit 2), a submodel
placeholder node ``submodel__<name>`` exposes an internal child to the
outside graph through a synthetic boundary handle ``out__<child_id>`` on
its outbound (internal→external) edges. That handle must flow UNCHANGED
across the three code paths that touch it, so a submodel save/codegen
round-trip is lossless and the same spelling never means two things:

  1. PRODUCTION — ``_submodel_graph.rewire_edges`` emits
     ``sourceHandle=f"out__{child}"`` for internal→external edges.
  2. FLATTEN — ``_flatten.flatten_graph`` strips it back to the bare
     child id and clears ``sourceHandle`` to ``None`` on the rewired edge.
  3. CODEGEN — ``codegen`` recognises a boundary ``out__`` handle and
     does NOT forward it as a user-facing ``source_port`` (it gates on
     the source being a submodel placeholder, per the adversarial S1
     review), while a *regular* apiInput edge literally labelled
     ``out__claims`` IS preserved as a ``source_port``.

The legs are each covered piecemeal in test_submodel_graph.py,
test_flatten.py, and test_parser_submodels.py, but nothing pins the
produce↔strip inverse or the S1 disambiguation as a single named
contract. This file owns that.
"""

from __future__ import annotations

from haute._flatten import flatten_graph
from haute._submodel_graph import rewire_edges
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.codegen import graph_to_code, graph_to_code_multi

# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_flatten.py:13-33)
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


class TestOutPortHandleInvariant:
    def test_rewire_produces_out_prefix_inverse_of_flatten_strip(self) -> None:
        """PRODUCTION leg: rewire_edges emits ``out__<child>`` on the
        placeholder-sourced edge, and stripping the prefix is the clean
        inverse — recovering the bare child id (mirrors
        _submodel_graph.py:132 ↔ _flatten.py:65).
        """
        edges = [_edge("child_b", "ext")]
        result = rewire_edges(edges, "submodel__sm", {"child_b"})

        assert len(result) == 1
        assert result[0].source == "submodel__sm"
        assert result[0].target == "ext"
        assert result[0].sourceHandle == "out__child_b"
        # produce → strip is a clean inverse back to the bare child id.
        assert result[0].sourceHandle is not None
        assert result[0].sourceHandle.removeprefix("out__") == "child_b"

    def test_out_handle_flattens_to_bare_child_and_clears_handle(self) -> None:
        """FLATTEN leg: an ``out__<child>`` handle on a submodel edge
        rewires the source back to the bare child id AND clears the
        sourceHandle to ``None`` (the strip half of the inverse).
        """
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
                                "data": {
                                    "label": "inner",
                                    "nodeType": "polars",
                                    "config": {},
                                },
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

        rewired = [e for e in result.edges if e.source == "inner" and e.target == "downstream"]
        assert len(rewired) == 1
        # sourceHandle is cleared after the boundary handle is consumed.
        assert rewired[0].sourceHandle is None

    def test_codegen_out_boundary_not_forwarded_but_namesake_frame_is(self) -> None:
        """CODEGEN leg + S1 disambiguation.

        (i) A boundary ``out__<child>`` handle on an edge FROM a submodel
            placeholder must NOT be forwarded as a user-facing
            ``source_port`` (codegen gates on the source being a submodel
            placeholder, so it resolves the handle to the child and drops
            the port).
        (ii) A *regular* (non-submodel) edge whose sourceHandle is
            literally ``out__claims`` — a frame that happens to be named
            that way — MUST be preserved as ``source_port`` (the prefix
            alone is not enough to gate on; this is the adversarial S1
            case).
        """
        # (i) Boundary case: submodel placeholder → downstream.
        boundary_graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "child_a",
                        "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                    },
                    {
                        "id": "down",
                        "data": {"label": "Down", "nodeType": "polars", "config": {}},
                    },
                ],
                "edges": [
                    {
                        "id": "e_in",
                        "source": "src",
                        "target": "submodel__sm1",
                        "targetHandle": "in__child_a",
                    },
                    {
                        "id": "e_out",
                        "source": "submodel__sm1",
                        "target": "down",
                        "sourceHandle": "out__child_a",
                    },
                ],
                "submodels": {
                    "sm1": {
                        "file": "modules/sm1.py",
                        "childNodeIds": ["child_a"],
                        "graph": {
                            "nodes": [
                                {
                                    "id": "child_a",
                                    "data": {
                                        "label": "ChildA",
                                        "nodeType": "polars",
                                        "config": {},
                                    },
                                },
                            ],
                            "edges": [],
                        },
                    },
                },
            }
        )
        boundary_main = graph_to_code_multi(boundary_graph, pipeline_name="main")["main.py"]
        # The boundary handle resolves to the child and the connect carries
        # NO source_port (the user never sees "out__child_a").
        assert 'pipeline.connect("ChildA", "Down")' in boundary_main
        assert "source_port=" not in boundary_main
        assert "out__child_a" not in boundary_main

        # (ii) Namesake case: a regular edge with a frame literally named
        # "out__claims" — no submodels — keeps it as a source_port.
        namesake_graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "NodeA",
                            "nodeType": "polars",
                            "config": {"code": "df = df"},
                        },
                    },
                    {
                        "id": "b",
                        "data": {
                            "label": "NodeB",
                            "nodeType": "polars",
                            "config": {"code": "df = df"},
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "a",
                        "target": "b",
                        "sourceHandle": "out__claims",
                    },
                ],
            }
        )
        namesake_code = graph_to_code(namesake_graph, pipeline_name="main")
        assert 'pipeline.connect("NodeA", "NodeB", source_port="out__claims")' in namesake_code
