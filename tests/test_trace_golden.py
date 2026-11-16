"""Golden snapshots for trace payload serialisation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from haute._node_addresses import labelled
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.schemas import TraceResponse, TraceResultResponse
from haute.trace import SchemaDiff, TraceResult, TraceStep, trace_result_to_dict

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ui_contracts"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _traced_graph() -> PipelineGraph:
    """The graph the trace below ran: its answer addresses each node it names from here."""
    return PipelineGraph(
        nodes=[
            GraphNode(id="source", data=NodeData(label="Source", nodeType=NodeType.DATA_INPUT)),
            GraphNode(id="score", data=NodeData(label="Score", nodeType=NodeType.POLARS)),
        ],
        edges=[GraphEdge(id="e-source-score", source="source", target="score")],
    )


def _make_trace_result() -> TraceResult:
    return TraceResult(
        target_node_id="score",
        row_index=0,
        column="premium",
        output_value=12.5,
        steps=[
            TraceStep(
                node_id="source",
                node_name="Source",
                node_type="dataInput",
                schema_diff=SchemaDiff(
                    columns_added=["base_rate"],
                    columns_removed=[],
                    columns_modified=[],
                    columns_passed=["base_rate"],
                ),
                input_values={},
                output_values={"base_rate": 10.0},
                topological_rank=0,
                column_relevant=True,
                contributed_columns=["base_rate"],
                derivations=[
                    {
                        "column": "base_rate",
                        "expression_text": None,
                        "substituted_text": None,
                        "result_value": 10.0,
                        "not_computable_reason": None,
                        "result_source": None,
                        "reads": [],
                        "error": None,
                        "error_type": None,
                    }
                ],
                expression=None,
                calculation=None,
                node_detail=None,
                row_lineage_type="direct",
            )
        ],
        row_id_column="quote_id",
        row_id_value="q_001",
        total_nodes_in_pipeline=2,
        nodes_in_trace=1,
        execution_ms=1.2,
        waterfall=[
            {
                "label": "base rate",
                "operation": "base",
                "value": 10.0,
                "delta": 10.0,
                "cumulative": 10.0,
                "default_used": False,
            }
        ],
        generated_at="2026-07-23T12:00:00+00:00",
        pipeline_source="pricing/example.py",
        execution_origin="fresh_execution",
    )


def test_trace_result_to_dict_matches_ui_contract_fixture() -> None:
    fixture = _load_fixture("trace_response")
    # The route adds the addresses of the nodes the trace names (labelled, below).
    unaddressed = {key: value for key, value in fixture["trace"].items() if key != "node_addresses"}

    assert trace_result_to_dict(_make_trace_result()) == unaddressed


def test_trace_response_model_matches_ui_contract_fixture() -> None:
    fixture = _load_fixture("trace_response")
    # Built as the route builds it: every node the answer names is addressed.
    trace = labelled(
        TraceResultResponse, _traced_graph(), **trace_result_to_dict(_make_trace_result())
    )
    response = TraceResponse(status="ok", trace=trace)

    assert response.model_dump(mode="json") == fixture
    assert fixture["trace"]["node_addresses"] == {
        "score": {"occurrence": None, "node_id": "score", "label": "Score"},
        "source": {"occurrence": None, "node_id": "source", "label": "Source"},
    }
