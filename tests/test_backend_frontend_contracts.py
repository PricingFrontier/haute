"""Shared backend/frontend UI contract fixtures.

These fixtures are the canonical payload corpus for the UI-facing API
surface.  They are intentionally loaded by both Python tests and Vitest
contract tests so we stop drifting through handwritten mocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute.schemas import (
    CreateSubmodelResponse,
    DissolveSubmodelResponse,
    ExploreRunResponse,
    ExploreStatusResponse,
    GitArchiveResponse,
    GitDeleteBranchResponse,
    GitPushResponse,
    GitStatusResponse,
    GraphEdge,
    JsonCacheBuildResponse,
    LogExperimentResponse,
    MlflowCheckResponse,
    OptimiserApplyResponse,
    OptimiserEstimateResponse,
    OptimiserFrontierAutoRangeResponse,
    OptimiserFrontierResponse,
    OptimiserFrontierSelectResponse,
    OptimiserMlflowLogResponse,
    OptimiserSaveResponse,
    OptimiserSolveResponse,
    OptimiserSolveResult,
    OptimiserStatusResponse,
    PreviewNodeResponse,
    SavePipelineResponse,
    SchemaResponse,
    SubmodelGraphResponse,
    TraceResponse,
    TrainEstimateResponse,
    TrainResponse,
    TrainStatusResponse,
    UtilityDeleteResponse,
    UtilityListResponse,
    UtilityReadResponse,
    UtilityWriteResponse,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ui_contracts"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture_name", "model"),
    [
        ("save_pipeline", SavePipelineResponse),
        ("preview_node", PreviewNodeResponse),
        ("trace_response", TraceResponse),
        ("schema_response", SchemaResponse),
        ("train_response", TrainResponse),
        ("train_started_response", TrainResponse),
        ("train_status_response", TrainStatusResponse),
        ("explore_run_response", ExploreRunResponse),
        ("explore_status_response", ExploreStatusResponse),
        ("optimiser_status_response", OptimiserStatusResponse),
        ("git_status_response", GitStatusResponse),
        ("json_cache_build_response", JsonCacheBuildResponse),
        ("submodel_create_response", CreateSubmodelResponse),
        ("submodel_graph_response", SubmodelGraphResponse),
        ("dissolve_submodel_response", DissolveSubmodelResponse),
        ("mlflow_check_response", MlflowCheckResponse),
        ("train_estimate_response", TrainEstimateResponse),
        ("mlflow_log_response", LogExperimentResponse),
        ("mlflow_log_response", OptimiserMlflowLogResponse),
        ("solve_optimiser_response", OptimiserSolveResponse),
        ("optimiser_estimate_response", OptimiserEstimateResponse),
        ("optimiser_apply_response", OptimiserApplyResponse),
        ("optimiser_frontier_auto_range_response", OptimiserFrontierAutoRangeResponse),
        ("optimiser_frontier_response", OptimiserFrontierResponse),
        ("optimiser_frontier_select_response", OptimiserFrontierSelectResponse),
        ("optimiser_save_response", OptimiserSaveResponse),
        ("utility_list_response", UtilityListResponse),
        ("utility_read_response", UtilityReadResponse),
        ("utility_write_response", UtilityWriteResponse),
        ("utility_delete_response", UtilityDeleteResponse),
        ("git_archive_response", GitArchiveResponse),
        ("git_delete_branch_response", GitDeleteBranchResponse),
        ("git_push_response", GitPushResponse),
    ],
)
def test_ui_contract_fixture_validates_against_backend_model(
    fixture_name: str,
    model: type[Any],
) -> None:
    validated = model.model_validate(_load_fixture(fixture_name))

    assert isinstance(validated, model)


def test_graph_edge_schema_exposes_authored_boundary_ports() -> None:
    schema = GraphEdge.model_json_schema()

    assert "sourcePort" in schema["properties"]
    assert "targetPort" in schema["properties"]
    assert GraphEdge(
        id="e",
        source="submodel__a",
        target="submodel__b",
        sourcePort="quotes",
        targetPort="base",
    ).model_dump() == {
        "id": "e",
        "source": "submodel__a",
        "target": "submodel__b",
        "sourceHandle": None,
        "targetHandle": None,
        "sourcePort": "quotes",
        "targetPort": "base",
    }


def test_optimiser_status_result_is_typed_model_not_raw_blob() -> None:
    payload = _load_fixture("optimiser_status_response")
    validated = OptimiserStatusResponse.model_validate(payload)

    assert isinstance(validated.result, OptimiserSolveResult)
    assert validated.result is not None
    assert validated.result.total_objective == 125.0


def test_optimiser_status_schema_refs_typed_result_model() -> None:
    result_schema = OptimiserStatusResponse.model_json_schema()["properties"]["result"]
    refs = {
        item["$ref"].rsplit("/", 1)[-1] for item in result_schema.get("anyOf", []) if "$ref" in item
    }

    assert "OptimiserSolveResult" in refs


def test_train_fixture_preserves_glm_fields_consumed_by_frontend() -> None:
    validated = TrainResponse.model_validate(_load_fixture("train_response"))

    assert validated.glm_coefficients
    assert validated.glm_relativities
    assert validated.glm_fit_statistics["aic"] == 1.2
    assert validated.diagnostics_errors[0]["diagnostic"] == "shap"
