"""Trace enrichment tests for optimiserApply explainability."""

from __future__ import annotations

import json

import polars as pl
import pytest

from haute._types import GraphNode, NodeData, NodeType
from haute.trace import TraceResult, TraceStep, execute_trace
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_source_node as _source_node


def _online_artifact(version: str = "online_v1") -> dict:
    return {
        "version": version,
        "mode": "online",
        "lambdas": {"predicted_volume": 0.5},
        "objective": "predicted_income",
        "constraints": {"predicted_volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }


def _ratio_artifact(version: str = "ratio_v1") -> dict:
    return {
        "version": version,
        "mode": "online",
        "lambdas": {"loss_ratio": 0.2},
        "objective": "predicted_income",
        "constraints": {
            "loss_ratio": {
                "max": 0.60,
                "numerator": "predicted_claims",
                "denominator": "predicted_premium",
            }
        },
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }


def _no_constraint_artifact(version: str = "unconstrained_v1") -> dict:
    return {
        "version": version,
        "mode": "online",
        "lambdas": {},
        "objective": "predicted_income",
        "constraints": {},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }


def _ratebook_artifact(version: str = "rb_v1") -> dict:
    return {
        "version": version,
        "mode": "ratebook",
        "lambdas": {"predicted_volume": 0.3},
        "objective": "predicted_income",
        "constraints": {"predicted_volume": {"min": 0.9}},
        "factor_tables": {
            "region": [
                {"__factor_group__": "London", "optimal_scenario_value": 1.05},
                {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
            ],
            "age_band": [
                {"__factor_group__": "young", "optimal_scenario_value": 1.10},
                {"__factor_group__": "old", "optimal_scenario_value": 0.95},
            ],
        },
    }


def _scored_online_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2", None],
            "scenario_index": [0, 1, 2, 0, 1, 2, 2],
            "scenario_value": [0.9, 1.0, 1.1, 0.9, 1.0, 1.1, 1.1],
            "predicted_income": [90.0, 100.0, 110.0, 45.0, 50.0, 55.0, 999.0],
            "predicted_volume": [1.0, 0.9, 0.7, 1.0, 0.95, 0.8, 0.0],
        }
    )


def _scored_ratio_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1"],
            "scenario_index": [0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1],
            "predicted_income": [90.0, 100.0, 110.0],
            "predicted_claims": [55.0, 60.0, 70.0],
            "predicted_premium": [100.0, 100.0, 100.0],
        }
    )


def _write_json(path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _optimiser_apply_node(config: dict) -> GraphNode:
    return GraphNode(
        id="apply",
        data=NodeData(
            label="apply",
            nodeType=NodeType.OPTIMISER_APPLY,
            config=config,
        ),
    )


def _step_by_id(result: TraceResult, node_id: str) -> TraceStep:
    for step in result.steps:
        if step.node_id == node_id:
            return step
    raise KeyError(node_id)


def test_online_execute_trace_attaches_candidate_explanation(tmp_path):
    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())
    scored_path = tmp_path / "scored.parquet"
    _scored_online_df().write_parquet(scored_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("scored", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimal_scenario_value",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["detail_type"] == "optimiser_apply"
    assert detail["mode"] == "online"
    assert detail["status"] == "ok"
    assert detail["output"]["column"] == "optimal_scenario_value"
    assert detail["output"]["value"] == pytest.approx(1.1)
    assert detail["columns"] == {
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "objective": "predicted_income",
    }
    assert detail["constraints"]["predicted_volume"]["lambda"] == pytest.approx(0.5)

    candidates = detail["candidates"]
    assert [row["scenario_index"] for row in candidates] == [0, 1, 2]
    assert {row["quote_id"] for row in candidates} == {"q1"}
    assert all("decision_score" in row for row in candidates)
    assert all("linearised_predicted_volume" in row for row in candidates)
    assert all("lambda_term_predicted_volume" in row for row in candidates)

    assert detail["selected"]["scenario_index"] == 2
    assert detail["selected"]["selected"] is True
    assert detail["baseline"]["scenario_index"] == 1
    assert detail["baseline"]["is_baseline"] is True


def test_online_execute_trace_uses_price_contour_ratio_linearisation(tmp_path):
    artifact_path = _write_json(tmp_path / "ratio.json", _ratio_artifact())
    scored_path = tmp_path / "ratio_scored.parquet"
    _scored_ratio_df().write_parquet(scored_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("scored", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimal_scenario_value",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["status"] == "ok"
    assert detail["constraints"]["loss_ratio"]["lambda"] == pytest.approx(0.2)
    selected = detail["selected"]
    assert selected["scenario_index"] == 2
    assert "linearised_loss_ratio" in selected
    assert "lambda_term_loss_ratio" in selected
    assert selected["linearised_constraints"]["loss_ratio"] != selected["constraints"].get(
        "loss_ratio"
    )


def test_online_execute_trace_handles_unconstrained_artifact(tmp_path):
    artifact_path = _write_json(tmp_path / "unconstrained.json", _no_constraint_artifact())
    scored_path = tmp_path / "scored.parquet"
    _scored_online_df().drop("predicted_volume").write_parquet(scored_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("scored", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimal_scenario_value",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["status"] == "ok"
    assert detail["constraints"] == {}
    assert detail["lambdas"] == {}
    assert detail["selected"]["decision_score"] == pytest.approx(detail["selected"]["objective"])
    assert detail["baseline"]["scenario_index"] == 1


def test_ratebook_execute_trace_explains_configured_input_factor_ladder(tmp_path):
    artifact_path = _write_json(tmp_path / "ratebook.json", _ratebook_artifact())
    scored_path = tmp_path / "scored.parquet"
    banded_path = tmp_path / "banded.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["London"],
            "age_band": ["old"],
            "base_price": [100.0],
        }
    ).write_parquet(scored_path)
    pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["Manchester"],
            "age_band": ["young"],
            "base_price": [100.0],
        }
    ).write_parquet(banded_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _source_node("banded", str(banded_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                        "ratebook_input": "banded",
                        "optimised_value_column": "selected_factor",
                    }
                ),
            ],
            "edges": [_edge("scored", "apply"), _edge("banded", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="selected_factor",
    )

    apply_step = _step_by_id(result, "apply")
    detail = apply_step.node_detail
    assert detail is not None
    assert detail["detail_type"] == "optimiser_apply"
    assert detail["mode"] == "ratebook"
    assert detail["status"] == "ok"
    assert apply_step.output_values["region"] == "Manchester"
    assert detail["output"]["column"] == "selected_factor"
    assert detail["output"]["value"] == pytest.approx(0.98 * 1.10)
    assert detail["base_value"] == pytest.approx(1.0)
    assert detail["final_value"] == pytest.approx(0.98 * 1.10)

    ladder = detail["factor_ladder"]
    assert [step["factor"] for step in ladder] == ["region", "age_band"]
    assert ladder[0]["input_value"] == "Manchester"
    assert ladder[0]["factor_value"] == pytest.approx(0.98)
    assert ladder[0]["running_product_before"] == pytest.approx(1.0)
    assert ladder[0]["running_product_after"] == pytest.approx(0.98)
    assert ladder[1]["input_value"] == "young"
    assert ladder[1]["factor_value"] == pytest.approx(1.10)
    assert ladder[1]["running_product_before"] == pytest.approx(0.98)
    assert ladder[1]["running_product_after"] == pytest.approx(0.98 * 1.10)


def test_online_enrichment_surfaces_reconciliation_error(tmp_path):
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={"quote_id": "missing", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["detail_type"] == "optimiser_apply"
    assert detail["mode"] == "online"
    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "missing" in detail["error"]
