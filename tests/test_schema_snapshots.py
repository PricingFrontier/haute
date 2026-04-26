"""Focused response-model snapshots for the UI-facing API surface."""

from __future__ import annotations

from typing import Any

import pytest

from haute.schemas import (
    FetchTableResponse,
    GitStatusResponse,
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
                "metrics": {"type": "object", "additionalProperties": "number"},
                "holdout_metrics": {"type": "object", "additionalProperties": "number"},
                "holdout_rows": {"type": "integer", "default": 0},
                "diagnostics_set": {"type": "string", "default": "validation"},
                "glm_coefficients": {"type": "array", "items": "object"},
                "glm_relativities": {"type": "array", "items": "object"},
                "glm_fit_statistics": {"type": "object", "additionalProperties": "number"},
                "glm_regularization_path": {"anyOf": ["object", "null"], "default": None},
                "diagnostics_errors": {"type": "array", "items": "object"},
                "warning": {"anyOf": ["string", "null"], "default": None},
                "total_source_rows": {"anyOf": ["integer", "null"], "default": None},
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
            },
        ),
        (
            GitStatusResponse,
            ["branch", "is_main", "is_read_only"],
            {
                "branch": {"type": "string"},
                "is_main": {"type": "boolean"},
                "is_read_only": {"type": "boolean"},
                "changed_files": {"type": "array", "items": "string"},
                "main_ahead": {"type": "boolean", "default": False},
                "main_ahead_by": {"type": "integer", "default": 0},
                "main_last_updated": {"anyOf": ["string", "null"], "default": None},
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
        (
            FetchTableResponse,
            [
                "path",
                "table",
                "row_count",
                "column_count",
                "columns",
                "size_bytes",
                "fetched_at",
                "fetch_seconds",
            ],
            {
                "path": {"type": "string"},
                "table": {"type": "string"},
                "row_count": {"type": "integer"},
                "column_count": {"type": "integer"},
                "columns": {"type": "object", "additionalProperties": "string"},
                "size_bytes": {"type": "integer"},
                "fetched_at": {"type": "number"},
                "fetch_seconds": {"type": "number"},
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
