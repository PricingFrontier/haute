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
    ExplorePivotMembersResponse,
    ExplorePivotRunResponse,
    ExplorePivotStatusResponse,
    GitArchiveResponse,
    GitDeleteBranchResponse,
    GitPushResponse,
    GraphEdge,
    JsonCacheBuildResponse,
    LogExperimentResponse,
    MlflowDestinationsResponse,
    MlflowSettingsResponse,
    MlflowTestConnectionResponse,
    ModelSaveDestinationResponse,
    NodeDataClearResponse,
    NodeDataPointResponse,
    NodeDataProfileResponse,
    NodeDataRunResponse,
    NodeDataStatusResponse,
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
    RatingLevelsResponse,
    SaveModelResponse,
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
from scripts.generate_api_contracts import RESPONSE_CONTRACT_GROUPS

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ui_contracts"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


_FIXTURE_MODELS: list[tuple[str, type[Any]]] = [
    ("save_pipeline", SavePipelineResponse),
    ("preview_node", PreviewNodeResponse),
    ("trace_response", TraceResponse),
    ("schema_response", SchemaResponse),
    ("train_response", TrainResponse),
    ("train_started_response", TrainResponse),
    ("train_status_response", TrainStatusResponse),
    ("explore_pivot_run_response", ExplorePivotRunResponse),
    ("explore_pivot_status_response", ExplorePivotStatusResponse),
    ("explore_pivot_members_response", ExplorePivotMembersResponse),
    ("node_data_point_response", NodeDataPointResponse),
    ("rating_levels_response", RatingLevelsResponse),
    ("node_data_profile_response", NodeDataProfileResponse),
    ("node_data_run_response", NodeDataRunResponse),
    ("node_data_status_response", NodeDataStatusResponse),
    ("node_data_clear_response", NodeDataClearResponse),
    ("optimiser_status_response", OptimiserStatusResponse),
    ("json_cache_build_response", JsonCacheBuildResponse),
    ("submodel_create_response", CreateSubmodelResponse),
    ("submodel_graph_response", SubmodelGraphResponse),
    ("dissolve_submodel_response", DissolveSubmodelResponse),
    ("mlflow_destinations_response", MlflowDestinationsResponse),
    ("mlflow_settings_response", MlflowSettingsResponse),
    ("mlflow_test_connection_response", MlflowTestConnectionResponse),
    ("train_estimate_response", TrainEstimateResponse),
    ("train_estimate_unavailable_response", TrainEstimateResponse),
    ("train_mlflow_log_response", LogExperimentResponse),
    ("mlflow_log_response", OptimiserMlflowLogResponse),
    ("solve_optimiser_response", OptimiserSolveResponse),
    ("optimiser_estimate_response", OptimiserEstimateResponse),
    ("optimiser_apply_response", OptimiserApplyResponse),
    ("optimiser_frontier_auto_range_response", OptimiserFrontierAutoRangeResponse),
    ("optimiser_frontier_response", OptimiserFrontierResponse),
    ("optimiser_frontier_select_response", OptimiserFrontierSelectResponse),
    ("optimiser_save_response", OptimiserSaveResponse),
    ("model_save_response", SaveModelResponse),
    ("model_save_destination_response", ModelSaveDestinationResponse),
    ("utility_list_response", UtilityListResponse),
    ("utility_read_response", UtilityReadResponse),
    ("utility_write_response", UtilityWriteResponse),
    ("utility_delete_response", UtilityDeleteResponse),
    ("git_archive_response", GitArchiveResponse),
    ("git_delete_branch_response", GitDeleteBranchResponse),
    ("git_push_response", GitPushResponse),
]

_GENERATED_RESPONSE_MODELS = frozenset(
    model for models in RESPONSE_CONTRACT_GROUPS.values() for model in models
)


@pytest.mark.parametrize(("fixture_name", "model"), _FIXTURE_MODELS)
def test_ui_contract_fixture_validates_against_backend_model(
    fixture_name: str,
    model: type[Any],
) -> None:
    validated = model.model_validate(_load_fixture(fixture_name))

    assert isinstance(validated, model)


@pytest.mark.parametrize(
    ("fixture_name", "model"),
    [(name, model) for name, model in _FIXTURE_MODELS if model in _GENERATED_RESPONSE_MODELS],
)
def test_generated_response_fixture_is_exactly_what_the_server_sends(
    fixture_name: str,
    model: type[Any],
) -> None:
    """A converted response's generated validator requires every serialized
    field, so its fixture must match the server's serialization byte for byte."""
    fixture = _load_fixture(fixture_name)

    assert model.model_validate(fixture).model_dump(mode="json") == fixture


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
    assert validated.glm_inference == {
        "status": "valid_standard",
        "valid": True,
        "standard_errors": "model",
        "reason": None,
    }
    assert validated.diagnostics_errors[0]["diagnostic"] == "shap"
