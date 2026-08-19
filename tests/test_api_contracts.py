"""API contract fingerprints for backend/UI integration safety."""

from __future__ import annotations

from typing import Any


def _normalise_schema(schema: dict[str, Any] | None) -> Any:
    if schema is None:
        return None
    if "$ref" in schema:
        return {"$ref": schema["$ref"]}
    if schema.get("type") == "array":
        return {
            "type": "array",
            "items": _normalise_schema(schema.get("items")),
        }
    return {key: schema[key] for key in ("type", "title") if key in schema}


def _api_contract_fingerprint() -> dict[str, dict[str, dict[str, Any]]]:
    from haute.server import app

    openapi = app.openapi()
    fingerprint: dict[str, dict[str, dict[str, Any]]] = {}

    for path, methods in sorted(openapi["paths"].items()):
        if not path.startswith("/api/"):
            continue

        fingerprint[path] = {}
        for method, spec in sorted(methods.items()):
            success_schema = None
            for status_code in ("200", "201", "202"):
                response = spec.get("responses", {}).get(status_code)
                if response is None:
                    continue
                success_schema = (
                    response.get("content", {}).get("application/json", {}).get("schema")
                )
                break

            fingerprint[path][method.upper()] = {
                "request_ref": (
                    spec.get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                    .get("$ref")
                ),
                "success_schema": _normalise_schema(success_schema),
            }

    return fingerprint


EXPECTED_API_CONTRACT_FINGERPRINT = {
    "/api/assistant/message": {
        "POST": {
            "request_ref": "#/components/schemas/AssistantMessageRequest",
            # SSE StreamingResponse — an event stream, not a JSON body; the
            # per-event wire contract is the AssistantStreamEvent union.
            "success_schema": None,
        },
    },
    "/api/assistant/session": {
        "POST": {
            "request_ref": "#/components/schemas/AssistantSessionRequest",
            "success_schema": {"$ref": "#/components/schemas/AssistantSessionResponse"},
        },
    },
    "/api/assistant/sessions": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/AssistantSessionListResponse"},
        },
    },
    "/api/assistant/status": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/AssistantStatusResponse"},
        },
    },
    "/api/databricks/catalogs": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/CatalogListResponse"},
        },
    },
    "/api/databricks/schemas": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/SchemaListResponse"},
        },
    },
    "/api/databricks/tables": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/TableListResponse"},
        },
    },
    "/api/databricks/warehouses": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/WarehouseListResponse"},
        },
    },
    "/api/explore/cache-status": {
        "POST": {
            "request_ref": "#/components/schemas/ExploreRunRequest",
            "success_schema": {"$ref": "#/components/schemas/ExploreCacheSnapshotResponse"},
        },
    },
    "/api/explore/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/ExploreStatusResponse"},
        },
    },
    "/api/explore/pivots/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/ExplorePivotStatusResponse"},
        },
    },
    "/api/explore/pivots/members": {
        "POST": {
            "request_ref": "#/components/schemas/ExplorePivotMembersRequest",
            "success_schema": {"$ref": "#/components/schemas/ExplorePivotMembersResponse"},
        },
    },
    "/api/explore/pivots/run": {
        "POST": {
            "request_ref": "#/components/schemas/ExplorePivotRunRequest",
            "success_schema": {"$ref": "#/components/schemas/ExplorePivotRunResponse"},
        },
    },
    "/api/explore/pivots/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/ExplorePivotStatusResponse"},
        },
    },
    "/api/explore/run": {
        "POST": {
            "request_ref": "#/components/schemas/ExploreRunRequest",
            "success_schema": {"$ref": "#/components/schemas/ExploreRunResponse"},
        },
    },
    "/api/explore/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/ExploreStatusResponse"},
        },
    },
    "/api/files": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/BrowseFilesResponse"},
        },
    },
    "/api/input-cache/build": {
        "POST": {
            "request_ref": "#/components/schemas/InputCacheBuildRequest",
            "success_schema": {"$ref": "#/components/schemas/InputCacheBuildResponse"},
        },
    },
    "/api/input-cache/clear": {
        "POST": {
            "request_ref": "#/components/schemas/InputCacheSourceRequest",
            "success_schema": {"$ref": "#/components/schemas/InputCacheSnapshotStatusResponse"},
        },
    },
    "/api/input-cache/jobs/{job_id}": {
        "DELETE": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/InputCacheCancelResponse"},
        },
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/InputCacheJobStatusResponse"},
        },
    },
    "/api/input-cache/status": {
        "POST": {
            "request_ref": "#/components/schemas/InputCacheSourceRequest",
            "success_schema": {"$ref": "#/components/schemas/InputCacheSnapshotStatusResponse"},
        },
    },
    "/api/io-capabilities": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/IoCapabilitiesResponse"},
        },
    },
    "/api/git/archive": {
        "POST": {
            "request_ref": "#/components/schemas/GitArchiveRequest",
            "success_schema": {"$ref": "#/components/schemas/GitArchiveResponse"},
        },
    },
    "/api/git/branches": {
        "DELETE": {
            "request_ref": "#/components/schemas/GitDeleteBranchRequest",
            "success_schema": {"$ref": "#/components/schemas/GitDeleteBranchResponse"},
        },
    },
    "/api/git/commit": {
        "POST": {
            "request_ref": "#/components/schemas/GitCommitRequest",
            "success_schema": {"$ref": "#/components/schemas/GitCommitResponse"},
        },
    },
    "/api/git/commit-context/{sha}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitCommitContext"},
        },
    },
    "/api/git/identity": {
        "POST": {
            "request_ref": "#/components/schemas/GitSetIdentityRequest",
            "success_schema": {"$ref": "#/components/schemas/GitSetIdentityResponse"},
        },
    },
    "/api/git/milestones": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitMilestonesResponse"},
        },
    },
    "/api/git/milestones/{sha}/saves": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitLedgerSavesResponse"},
        },
    },
    "/api/git/branch-away": {
        "POST": {
            "request_ref": "#/components/schemas/GitBranchAwayRequest",
            "success_schema": {"$ref": "#/components/schemas/GitBranchAwayResponse"},
        },
    },
    "/api/git/fast-forward": {
        "POST": {
            "request_ref": "#/components/schemas/GitFastForwardRequest",
            "success_schema": {"$ref": "#/components/schemas/GitFastForwardResponse"},
        },
    },
    "/api/git/graph": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitGraphResponse"},
        },
    },
    "/api/git/move": {
        "POST": {
            "request_ref": "#/components/schemas/GitMoveRequest",
            "success_schema": {"$ref": "#/components/schemas/GitMoveResponse"},
        },
    },
    "/api/git/pending-saves": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitLedgerSavesResponse"},
        },
    },
    "/api/git/push": {
        "POST": {
            "request_ref": "#/components/schemas/GitPushRequest",
            "success_schema": {"$ref": "#/components/schemas/GitPushResponse"},
        },
    },
    "/api/git/remotes": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitRemotesResponse"},
        },
    },
    "/api/git/restore": {
        "POST": {
            "request_ref": "#/components/schemas/GitRestoreRequest",
            "success_schema": {"$ref": "#/components/schemas/GitRestoreResponse"},
        },
    },
    "/api/git/show/{sha}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/PipelineGraph"},
        },
    },
    "/api/git/storage/bind": {
        "POST": {
            "request_ref": "#/components/schemas/GitBindStorageRequest",
            "success_schema": {"$ref": "#/components/schemas/GitBindStorageResponse"},
        },
    },
    "/api/git/storage/bind/ack": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitWorkingBranchResponse"},
        },
    },
    "/api/git/storage/fork": {
        "POST": {
            "request_ref": "#/components/schemas/GitForkStorageRequest",
            "success_schema": {"$ref": "#/components/schemas/GitForkStorageResponse"},
        },
    },
    "/api/git/storage/retry": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitWorkingBranchResponse"},
        },
    },
    "/api/git/storage/upstream/check": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitUpstreamStatusResponse"},
        },
    },
    "/api/git/storage/upstream/pull": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitFastForwardResponse"},
        },
    },
    "/api/git/undelete": {
        "POST": {
            "request_ref": "#/components/schemas/GitUndeleteRequest",
            "success_schema": {"$ref": "#/components/schemas/GitUndeleteResponse"},
        },
    },
    "/api/git/working-branch": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitWorkingBranchResponse"},
        },
        "POST": {
            "request_ref": "#/components/schemas/GitSetWorkingBranchRequest",
            "success_schema": {"$ref": "#/components/schemas/GitSetWorkingBranchResponse"},
        },
    },
    "/api/git/working-branches": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitWorkingBranchesResponse"},
        },
        "POST": {
            "request_ref": "#/components/schemas/GitCreateWorkingBranchRequest",
            "success_schema": {"$ref": "#/components/schemas/GitCreateWorkingBranchResponse"},
        },
    },
    "/api/git/prefs": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitPrefs"},
        },
        "POST": {
            "request_ref": "#/components/schemas/GitPrefs",
            "success_schema": {"$ref": "#/components/schemas/GitPrefs"},
        },
    },
    "/api/json-cache": {
        "DELETE": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/JsonCacheStatusResponse"},
        },
    },
    "/api/json-cache/build": {
        "POST": {
            "request_ref": "#/components/schemas/JsonCacheBuildRequest",
            "success_schema": {"$ref": "#/components/schemas/JsonCacheBuildResponse"},
        },
    },
    "/api/json-cache/infer": {
        "POST": {
            "request_ref": "#/components/schemas/JsonCacheInferRequest",
            "success_schema": {"$ref": "#/components/schemas/JsonCacheInferResponse"},
        },
    },
    "/api/json-cache/progress": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/JsonCacheProgressResponse"},
        },
    },
    "/api/json-cache/status": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/JsonCacheStatusResponse"},
        },
        "POST": {
            "request_ref": "#/components/schemas/JsonCacheBuildRequest",
            "success_schema": {"$ref": "#/components/schemas/JsonCacheStatusResponse"},
        },
    },
    "/api/mlflow/experiments": {
        "GET": {
            "request_ref": None,
            "success_schema": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/MlflowExperimentSummary"},
            },
        },
    },
    "/api/mlflow/model-versions": {
        "GET": {
            "request_ref": None,
            "success_schema": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/MlflowModelVersionSummary"},
            },
        },
    },
    "/api/mlflow/models": {
        "GET": {
            "request_ref": None,
            "success_schema": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/MlflowModelSummary"},
            },
        },
    },
    "/api/mlflow/runs": {
        "GET": {
            "request_ref": None,
            "success_schema": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/MlflowRunSummary"},
            },
        },
    },
    "/api/modelling/dispersion/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/DispersionEstimateStatusResponse"},
        },
    },
    "/api/modelling/dispersion/estimate": {
        "POST": {
            "request_ref": "#/components/schemas/DispersionEstimateRequest",
            "success_schema": {"$ref": "#/components/schemas/DispersionEstimateResponse"},
        },
    },
    "/api/modelling/dispersion/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/DispersionEstimateStatusResponse"},
        },
    },
    "/api/modelling/estimate": {
        "POST": {
            "request_ref": "#/components/schemas/TrainEstimateRequest",
            "success_schema": {"$ref": "#/components/schemas/TrainEstimateResponse"},
        },
    },
    "/api/modelling/export": {
        "POST": {
            "request_ref": "#/components/schemas/ExportScriptRequest",
            "success_schema": {"$ref": "#/components/schemas/ExportScriptResponse"},
        },
    },
    "/api/modelling/mlflow/check": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/MlflowCheckResponse"},
        },
    },
    "/api/modelling/mlflow/log": {
        "POST": {
            "request_ref": "#/components/schemas/LogExperimentRequest",
            "success_schema": {"$ref": "#/components/schemas/LogExperimentResponse"},
        },
    },
    "/api/modelling/model-cache": {
        "DELETE": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/ModelCacheClearResponse"},
        },
    },
    "/api/modelling/train": {
        "POST": {
            "request_ref": "#/components/schemas/TrainRequest",
            "success_schema": {"$ref": "#/components/schemas/TrainResponse"},
        },
    },
    "/api/modelling/train/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/TrainStatusResponse"},
        },
    },
    "/api/modelling/train/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/TrainStatusResponse"},
        },
    },
    "/api/optimiser/apply": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserApplyRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserApplyResponse"},
        },
    },
    "/api/optimiser/estimate": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserEstimateRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserEstimateResponse"},
        },
    },
    "/api/optimiser/frontier": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserFrontierRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserFrontierResponse"},
        },
    },
    "/api/optimiser/frontier/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/OptimiserFrontierStatusResponse"},
        },
    },
    "/api/optimiser/frontier/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/OptimiserFrontierStatusResponse"},
        },
    },
    "/api/optimiser/frontier/auto-range/start": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserFrontierAutoRangeRequest",
            "success_schema": {
                "$ref": "#/components/schemas/OptimiserFrontierAutoRangeStartResponse"
            },
        },
    },
    "/api/optimiser/frontier/auto-range/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {
                "$ref": "#/components/schemas/OptimiserFrontierAutoRangeStatusResponse"
            },
        },
    },
    "/api/optimiser/frontier/auto-range/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {
                "$ref": "#/components/schemas/OptimiserFrontierAutoRangeStatusResponse"
            },
        },
    },
    "/api/optimiser/frontier/select": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserFrontierSelectRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserFrontierSelectResponse"},
        },
    },
    "/api/optimiser/mlflow/log": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserMlflowLogRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserMlflowLogResponse"},
        },
    },
    "/api/optimiser/save": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserSaveRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserSaveResponse"},
        },
    },
    "/api/optimiser/solve": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserSolveRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserSolveResponse"},
        },
    },
    "/api/optimiser/solve/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/OptimiserStatusResponse"},
        },
    },
    "/api/optimiser/solve/status/{job_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/OptimiserStatusResponse"},
        },
    },
    "/api/output-assemble/dry-run": {
        "POST": {
            "request_ref": "#/components/schemas/OutputAssembleDryRunRequest",
            "success_schema": {"$ref": "#/components/schemas/OutputAssembleDryRunResponse"},
        },
    },
    "/api/pipeline": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/PipelineEditorDocument"},
        },
    },
    "/api/pipeline/read-json": {
        "POST": {
            "request_ref": "#/components/schemas/ReadJsonRequest",
            "success_schema": {"$ref": "#/components/schemas/ReadJsonResponse"},
        },
    },
    "/api/pipeline/preview": {
        "POST": {
            "request_ref": "#/components/schemas/PreviewNodeRequest",
            "success_schema": {"$ref": "#/components/schemas/PreviewNodeResponse"},
        },
    },
    "/api/pipeline/recovery-preview": {
        "POST": {
            "request_ref": "#/components/schemas/RecoveryPreviewRequest",
            "success_schema": {"$ref": "#/components/schemas/PreviewNodeResponse"},
        },
    },
    "/api/pipeline/repair/remove/apply": {
        "POST": {
            "request_ref": "#/components/schemas/PipelineRepairApplyRequest",
            "success_schema": {"$ref": "#/components/schemas/PipelineRepairApplyResponse"},
        },
    },
    "/api/pipeline/repair/remove/dry-run": {
        "POST": {
            "request_ref": "#/components/schemas/PipelineRepairDryRunRequest",
            "success_schema": {"$ref": "#/components/schemas/PipelineRepairPlanResponse"},
        },
    },
    "/api/pipeline/output-destination": {
        "POST": {
            "request_ref": "#/components/schemas/OutputDestinationRequest",
            "success_schema": {"$ref": "#/components/schemas/OutputDestinationResponse"},
        },
    },
    "/api/pipeline/save": {
        "POST": {
            "request_ref": "#/components/schemas/SavePipelineRequest",
            "success_schema": {"$ref": "#/components/schemas/SavePipelineResponse"},
        },
    },
    "/api/pipeline/write-output": {
        "POST": {
            "request_ref": "#/components/schemas/WriteOutputRequest",
            "success_schema": {"$ref": "#/components/schemas/WriteOutputResponse"},
        },
    },
    "/api/pipeline/trace": {
        "POST": {
            "request_ref": "#/components/schemas/TraceRequest",
            "success_schema": {"$ref": "#/components/schemas/TraceResponse"},
        },
    },
    "/api/pipeline/{name}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/PipelineEditorDocument"},
        },
    },
    "/api/pipelines": {
        "GET": {
            "request_ref": None,
            "success_schema": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/PipelineSummary"},
            },
        },
    },
    "/api/schema": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/SchemaResponse"},
        },
    },
    "/api/session": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/SessionStatusResponse"},
        },
    },
    "/api/session/bootstrap": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/SessionStatusResponse"},
        },
    },
    "/api/submodel/create": {
        "POST": {
            "request_ref": "#/components/schemas/CreateSubmodelRequest",
            "success_schema": {"$ref": "#/components/schemas/CreateSubmodelResponse"},
        },
    },
    "/api/submodel/dissolve": {
        "POST": {
            "request_ref": "#/components/schemas/DissolveSubmodelRequest",
            "success_schema": {"$ref": "#/components/schemas/DissolveSubmodelResponse"},
        },
    },
    "/api/submodel/{definition_id}": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/SubmodelGraphResponse"},
        },
    },
    "/api/utility": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/UtilityListResponse"},
        },
        "POST": {
            "request_ref": "#/components/schemas/UtilityCreateRequest",
            "success_schema": {"$ref": "#/components/schemas/UtilityWriteResponse"},
        },
    },
    "/api/utility/{module}": {
        "DELETE": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/UtilityDeleteResponse"},
        },
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/UtilityReadResponse"},
        },
        "PUT": {
            "request_ref": "#/components/schemas/UtilityWriteRequest",
            "success_schema": {"$ref": "#/components/schemas/UtilityWriteResponse"},
        },
    },
}


# pydantic < 2.13 publishes separate "-Input"/"-Output" component schemas even when
# the two are identical; pydantic >= 2.13 merges such pairs back to the bare name.
# EXPECTED_API_CONTRACT_FINGERPRINT above uses the forward-looking (merged) refs;
# the retrospective (pydantic < 2.13) shape differs by exactly these renames.
# Delete this map and the failover below once the pydantic floor reaches 2.13.
_RETROSPECTIVE_REF_RENAMES = {
    "#/components/schemas/PipelineGraph": "#/components/schemas/PipelineGraph-Output",
}


def _retrospective_fingerprint(
    forward: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    def rename(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: rename(inner) for key, inner in value.items()}
        if isinstance(value, str):
            return _RETROSPECTIVE_REF_RENAMES.get(value, value)
        return value

    return rename(forward)


def test_openapi_contract_fingerprint_matches_expected_snapshot() -> None:
    actual = _api_contract_fingerprint()
    if actual == EXPECTED_API_CONTRACT_FINGERPRINT:
        return
    # Failover for pydantic < 2.13 resolutions (the current lock): the only
    # tolerated difference from the forward-looking snapshot is the known ref
    # split above — any real contract drift fails fast on this delta.
    assert actual == _retrospective_fingerprint(EXPECTED_API_CONTRACT_FINGERPRINT)
