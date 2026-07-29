"""Focused response-model snapshots for the UI-facing API surface."""

from __future__ import annotations

from typing import Any

import pytest

from haute.schemas import (
    ExecutionMetricsPayload,
    ExploreRunResponse,
    ExploreStatusResponse,
    JsonCacheStatusResponse,
    OptimiserStatusResponse,
    PreviewNodeResponse,
    SavePipelineResponse,
    SchemaResponse,
    TraceResponse,
    TrainResponse,
    TrainStatusResponse,
)


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _schema_summary(model: type[Any]) -> dict[str, Any]:
    schema = model.model_json_schema()
    summary: dict[str, Any] = {
        "required": schema.get("required", []),
        "properties": {},
    }

    for name, prop in schema.get("properties", {}).items():
        prop_summary: dict[str, Any] = {}
        if "$ref" in prop:
            prop_summary["ref"] = _ref_name(prop["$ref"])
        if "type" in prop:
            prop_summary["type"] = prop["type"]
        if "default" in prop:
            prop_summary["default"] = prop["default"]
        if "anyOf" in prop:
            prop_summary["anyOf"] = [
                f"ref:{_ref_name(item['$ref'])}" if "$ref" in item else item.get("type")
                for item in prop["anyOf"]
            ]
        if prop.get("type") == "array":
            items = prop.get("items", {})
            prop_summary["items"] = (
                f"ref:{_ref_name(items['$ref'])}" if "$ref" in items else items.get("type")
            )
        if prop.get("type") == "object" and "additionalProperties" in prop:
            additional = prop["additionalProperties"]
            if "$ref" in additional:
                prop_summary["additionalProperties"] = f"ref:{_ref_name(additional['$ref'])}"
            elif additional.get("type") == "array":
                items = additional.get("items", {})
                item_summary = (
                    f"ref:{_ref_name(items['$ref'])}" if "$ref" in items else items.get("type")
                )
                prop_summary["additionalProperties"] = f"array:{item_summary}"
            else:
                prop_summary["additionalProperties"] = additional.get("type")
        summary["properties"][name] = prop_summary

    return summary


@pytest.mark.parametrize(
    ("model", "expected_required", "expected_properties"),
    [
        (
            SavePipelineResponse,
            ["file", "pipeline_name"],
            {
                "status": {"type": "string", "default": "saved"},
                "file": {"type": "string"},
                "pipeline_name": {"type": "string"},
                "warnings": {"type": "array", "items": "string"},
            },
        ),
        (
            PreviewNodeResponse,
            ["status", "node_id"],
            {
                "status": {"type": "string"},
                "node_id": {"type": "string"},
                "timings": {"type": "array", "items": "ref:NodeTimingInfo"},
                "memory": {"type": "array", "items": "ref:NodeMemoryInfo"},
                "node_statuses": {"type": "object", "additionalProperties": "string"},
                "node_columns": {"type": "object", "additionalProperties": "array:ref:ColumnInfo"},
                "node_available_columns": {
                    "type": "object",
                    "additionalProperties": "array:ref:ColumnInfo",
                },
                "node_schema_warnings": {
                    "type": "object",
                    "additionalProperties": "array:ref:SchemaWarning",
                },
            },
        ),
        (
            TraceResponse,
            ["status", "trace"],
            {
                "status": {"type": "string"},
                "trace": {"ref": "TraceResultResponse"},
            },
        ),
        (
            SchemaResponse,
            ["path", "columns", "column_count"],
            {
                "path": {"type": "string"},
                "columns": {"type": "array", "items": "ref:ColumnInfo"},
                "row_count": {"anyOf": ["integer", "null"], "default": None},
                "row_count_estimated": {"type": "boolean", "default": False},
                "column_count": {"type": "integer"},
                "preview": {"type": "array", "items": "object"},
            },
        ),
        (
            TrainResponse,
            ["status"],
            {
                "status": {"type": "string"},
                "job_id": {"anyOf": ["string", "null"], "default": None},
                "diagnostic_metrics": {"type": "object", "additionalProperties": "number"},
                "final_test_metrics": {"type": "object", "additionalProperties": "number"},
                "development_rows": {"type": "integer", "default": 0},
                "final_test_rows": {"type": "integer", "default": 0},
                "diagnostics_set": {"type": "string", "default": "development"},
                "glm_coefficients": {"type": "array", "items": "object"},
                "glm_relativities": {"type": "array", "items": "object"},
                "glm_fit_statistics": {"type": "object", "additionalProperties": "number"},
                "glm_regularization_path": {"anyOf": ["object", "null"], "default": None},
                "diagnostics_errors": {"type": "array", "items": "object"},
                "warning": {"anyOf": ["string", "null"], "default": None},
                "total_source_rows": {"anyOf": ["integer", "null"], "default": None},
                "evaluation": {"anyOf": ["ref:EvaluationReportPayload", "null"], "default": None},
                "tuning": {"anyOf": ["ref:TuningReportPayload", "null"], "default": None},
            },
        ),
        (
            TrainStatusResponse,
            ["status"],
            {
                "status": {"type": "string"},
                "progress": {"type": "number", "default": 0.0},
                "message": {"type": "string", "default": ""},
                "train_loss": {"type": "object", "additionalProperties": "number"},
                "result": {"anyOf": ["ref:TrainResponse", "null"], "default": None},
                "warning": {"anyOf": ["string", "null"], "default": None},
                "execution_metrics": {
                    "anyOf": ["ref:ExecutionMetricsPayload", "null"],
                    "default": None,
                },
            },
        ),
        (
            ExploreRunResponse,
            ["status"],
            {
                "status": {"type": "string"},
                "job_id": {"anyOf": ["string", "null"], "default": None},
                "cached": {"type": "boolean", "default": False},
                "message": {"type": "string", "default": ""},
                "result": {"anyOf": ["ref:ExploreCacheReport", "null"], "default": None},
            },
        ),
        (
            ExploreStatusResponse,
            ["status"],
            {
                "status": {"type": "string"},
                "progress": {"type": "number", "default": 0.0},
                "message": {"type": "string", "default": ""},
                "result": {"anyOf": ["ref:ExploreCacheReport", "null"], "default": None},
                "terminal_reason": {"anyOf": ["string", "null"], "default": None},
                "execution_metrics": {
                    "anyOf": ["ref:ExecutionMetricsPayload", "null"],
                    "default": None,
                },
            },
        ),
        (
            OptimiserStatusResponse,
            ["status"],
            {
                "status": {"type": "string"},
                "progress": {"type": "number", "default": 0.0},
                "message": {"type": "string", "default": ""},
                "result": {"anyOf": ["ref:OptimiserSolveResult", "null"], "default": None},
                "frontier": {"anyOf": ["ref:OptimiserFrontierResponse", "null"], "default": None},
                "execution_metrics": {
                    "anyOf": ["ref:ExecutionMetricsPayload", "null"],
                    "default": None,
                },
            },
        ),
        (
            ExecutionMetricsPayload,
            [],
            {
                "schema_version": {"type": "integer", "default": 1},
                "operation": {"type": "string", "default": ""},
                "profile": {"type": "string", "default": ""},
                "status": {"anyOf": ["string", "null"], "default": None},
                "terminal_reason": {"anyOf": ["string", "null"], "default": None},
                "stage_count": {"type": "integer", "default": 0},
                "retained_stage_count": {"type": "integer", "default": 0},
                "truncated_stage_count": {"type": "integer", "default": 0},
                "stages_truncated": {"type": "boolean", "default": False},
                "n_collects": {"type": "integer", "default": 0},
                "n_checkpoints": {"type": "integer", "default": 0},
                "memory_pressure_event_count": {"type": "integer", "default": 0},
                "retained_memory_pressure_event_count": {"type": "integer", "default": 0},
                "truncated_memory_pressure_event_count": {"type": "integer", "default": 0},
                "memory_pressure_events_truncated": {"type": "boolean", "default": False},
                "node_elapsed_ms": {"type": "object", "additionalProperties": "number"},
                "stage_elapsed_ms": {"type": "object", "additionalProperties": "number"},
                "admission": {
                    "anyOf": ["ref:ExecutionAdmissionPayload", "null"],
                    "default": None,
                },
                "stages": {"type": "array", "items": "ref:ExecutionStageMetricsPayload"},
                "memory_pressure_events": {
                    "type": "array",
                    "items": "ref:ExecutionMemoryPressureEventPayload",
                },
            },
        ),
        (
            JsonCacheStatusResponse,
            ["cached"],
            {
                "cached": {"type": "boolean"},
                "path": {"anyOf": ["string", "null"], "default": None},
                "data_path": {"type": "string", "default": ""},
                "row_count": {"type": "integer", "default": 0},
                "column_count": {"type": "integer", "default": 0},
                "columns": {"type": "object", "additionalProperties": "string"},
                "size_bytes": {"type": "integer", "default": 0},
                "cached_at": {"type": "number", "default": 0},
            },
        ),
    ],
)
def test_ui_facing_response_models_match_contract_snapshots(
    model: type[Any],
    expected_required: list[str],
    expected_properties: dict[str, Any],
) -> None:
    summary = _schema_summary(model)

    assert summary["required"] == expected_required
    for name, expected in expected_properties.items():
        assert summary["properties"][name] == expected


def test_train_response_exposes_glm_fields_used_by_frontend_modelling_panels() -> None:
    properties = _schema_summary(TrainResponse)["properties"]

    for field_name in (
        "glm_coefficients",
        "glm_relativities",
        "glm_fit_statistics",
        "glm_regularization_path",
        "diagnostics_errors",
    ):
        assert field_name in properties
