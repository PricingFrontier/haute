"""A preview reports a submodel occurrence's output-port columns keyed by its handle."""

from __future__ import annotations

from haute._submodel_instances import qualified_runtime_node_id
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelOutputPort,
)
from haute.routes.pipeline import _occurrence_output_columns
from haute.schemas import ColumnInfo, NodeResult


def _node(node_id: str, node_type: NodeType = NodeType.POLARS, **config: object) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="submodel" if node_type == NodeType.SUBMODEL else "pipelineNode",
        data=NodeData(label=node_id, nodeType=node_type, config=dict(config)),
    )


def _graph() -> PipelineGraph:
    definition = SubmodelDefinition(
        definition_id="inputs",
        file="modules/inputs.py",
        graph=PipelineGraph(
            nodes=[_node("live_switch"), _node("shredded")],
            edges=[GraphEdge(id="e", source="live_switch", target="shredded")],
        ),
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(name="output_1", source=SubmodelEndpoint(node_id="live_switch")),
            SubmodelOutputPort(
                name="claims", source=SubmodelEndpoint(node_id="shredded", handle_id="claims")
            ),
        ],
    )
    occurrence = _node("Inputs", NodeType.SUBMODEL, definitionId="inputs", alias="Inputs")
    consumer = _node("t", config={"code": "df = Inputs"})
    return PipelineGraph(
        nodes=[occurrence, consumer],
        edges=[GraphEdge(id="e1", source="Inputs", target="t", sourceHandle="out__output_1")],
        submodels={"inputs": definition},
    )


def test_occurrence_output_columns_are_keyed_by_the_edge_handle() -> None:
    premium = [ColumnInfo(name="premium", dtype="Float64")]
    claims = [ColumnInfo(name="claim_id", dtype="Int64")]
    results = {
        qualified_runtime_node_id("Inputs", "live_switch"): NodeResult(
            status="ok", columns=premium
        ),
        qualified_runtime_node_id("Inputs", "shredded"): NodeResult(
            status="ok", columns=[], frame_columns={"claims": claims, "other": premium}
        ),
        "t": NodeResult(status="ok", columns=premium),
    }

    assert _occurrence_output_columns(_graph(), results) == {
        "Inputs": {"out__output_1": premium, "out__claims": claims}
    }


def test_occurrence_without_results_reports_nothing() -> None:
    assert _occurrence_output_columns(_graph(), {}) == {}
    plain = PipelineGraph(nodes=[_node("t")], edges=[])
    assert _occurrence_output_columns(plain, {"t": NodeResult(status="ok", columns=[])}) == {}
