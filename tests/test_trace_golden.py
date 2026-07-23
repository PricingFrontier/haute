"""Golden snapshots for trace payload serialisation."""

from __future__ import annotations

import json
from pathlib import Path

from haute.schemas import TraceResponse
from haute.trace import SchemaDiff, TraceResult, TraceStep, trace_result_to_dict

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ui_contracts"


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


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
                node_type="data_source",
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
                "operation": "set",
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

    assert trace_result_to_dict(_make_trace_result()) == fixture["trace"]


def test_trace_response_model_matches_ui_contract_fixture() -> None:
    fixture = _load_fixture("trace_response")
    response = TraceResponse(status="ok", trace=trace_result_to_dict(_make_trace_result()))

    assert response.model_dump(mode="json") == fixture
