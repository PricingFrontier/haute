"""Generated code leaves out what it repeats (READ-13).

Spec: specs/codegen/high-level.md. A join's suffix at its default and the wire comment are
not written; ``how`` (Haute's default differs from Polars') and ``maintain_order`` (which
the determinism rule requires) are.
"""

from __future__ import annotations

from haute._edge_join import build_edge_join_kwargs
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code


def _graph(suffix: str) -> PipelineGraph:
    def constant(nid: str) -> GraphNode:
        return GraphNode(
            id=nid,
            data=NodeData(
                label=nid,
                nodeType=NodeType.CONSTANT,
                config={"values": [{"name": "key", "value": 1}]},
            ),
        )

    joined = GraphNode(
        id="joined",
        data=NodeData(
            label="joined",
            nodeType=NodeType.EDGE_JOIN,
            config={"how": "left", "on": ["key"], "maintain_order": "left", "suffix": suffix},
        ),
    )
    return PipelineGraph(
        nodes=[constant("policies"), constant("regions"), joined],
        edges=[
            GraphEdge(id="e1", source="policies", target="joined", targetHandle="base"),
            GraphEdge(id="e2", source="regions", target="joined", targetHandle="join"),
        ],
    )


def test_a_join_at_the_default_suffix_and_the_wiring_carry_no_noise() -> None:
    code = graph_to_code(_graph("_right"), pipeline_name="main")

    assert "suffix=" not in code
    assert 'how="left"' in code and 'maintain_order="left"' in code
    assert "# Wire nodes together" not in code
    assert 'pipeline.connect("policies", "joined", target_port="base")' in code
    # A join reads an absent suffix as the default.
    kwargs = build_edge_join_kwargs({"how": "left", "on": ["key"], "maintain_order": "left"})
    assert kwargs["suffix"] == "_right"


def test_a_join_with_another_suffix_keeps_it() -> None:
    assert 'suffix="_other"' in graph_to_code(_graph("_other"), pipeline_name="main")
