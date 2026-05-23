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
    "/api/databricks/cache": {
        "DELETE": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/CacheStatusResponse"},
        },
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/CacheStatusResponse"},
        },
    },
    "/api/databricks/catalogs": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/CatalogListResponse"},
        },
    },
    "/api/databricks/fetch": {
        "POST": {
            "request_ref": "#/components/schemas/FetchTableRequest",
            "success_schema": {"$ref": "#/components/schemas/FetchTableResponse"},
        },
    },
    "/api/databricks/fetch/progress": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/FetchProgressResponse"},
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
    "/api/explore/cancel/{job_id}": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/ExploreStatusResponse"},
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
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitBranchListResponse"},
        },
        "POST": {
            "request_ref": "#/components/schemas/GitCreateBranchRequest",
            "success_schema": {"$ref": "#/components/schemas/GitCreateBranchResponse"},
        },
    },
    "/api/git/history": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitHistoryResponse"},
        },
    },
    "/api/git/pull": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitPullResponse"},
        },
    },
    "/api/git/revert": {
        "POST": {
            "request_ref": "#/components/schemas/GitRevertRequest",
            "success_schema": {"$ref": "#/components/schemas/GitRevertResponse"},
        },
    },
    "/api/git/save": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitSaveResponse"},
        },
    },
    "/api/git/status": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitStatusResponse"},
        },
    },
    "/api/git/submit": {
        "POST": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/GitSubmitResponse"},
        },
    },
    "/api/git/switch": {
        "POST": {
            "request_ref": "#/components/schemas/GitSwitchBranchRequest",
            "success_schema": {"$ref": "#/components/schemas/GitSwitchBranchResponse"},
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
    "/api/json-cache/cancel": {
        "POST": {
            "request_ref": "#/components/schemas/JsonCacheBuildRequest",
            "success_schema": {"$ref": "#/components/schemas/JsonCacheCancelResponse"},
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
    "/api/optimiser/frontier/auto-range": {
        "POST": {
            "request_ref": "#/components/schemas/OptimiserFrontierAutoRangeRequest",
            "success_schema": {"$ref": "#/components/schemas/OptimiserFrontierAutoRangeResponse"},
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
    "/api/pipeline": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/PipelineGraph-Output"},
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
    "/api/pipeline/save": {
        "POST": {
            "request_ref": "#/components/schemas/SavePipelineRequest",
            "success_schema": {"$ref": "#/components/schemas/SavePipelineResponse"},
        },
    },
    "/api/pipeline/sink": {
        "POST": {
            "request_ref": "#/components/schemas/SinkRequest",
            "success_schema": {"$ref": "#/components/schemas/SinkResponse"},
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
            "success_schema": {"$ref": "#/components/schemas/PipelineGraph-Output"},
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
    "/api/schema/databricks": {
        "GET": {
            "request_ref": None,
            "success_schema": {"$ref": "#/components/schemas/SchemaResponse"},
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
    "/api/submodel/{name}": {
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


def test_openapi_contract_fingerprint_matches_expected_snapshot() -> None:
    assert _api_contract_fingerprint() == EXPECTED_API_CONTRACT_FINGERPRINT
