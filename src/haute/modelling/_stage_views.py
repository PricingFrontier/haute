"""Compact workspace diagnostics; dense reports remain in immutable result artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from haute.modelling._loss_history import bounded_loss_history


def _cells(values: list[Any]) -> int:
    return sum(_cells(value) if isinstance(value, list) else 1 for value in values)


def table_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tables": [
            {
                "term": table["term"],
                "features": table["features"],
                "order": table["order"],
                "cells": _cells(table["scores"]),
                "shape_digest": hashlib.sha256(
                    json.dumps(
                        [table["axes"], table["scores"]],
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
            }
            for table in report["tables"]
        ],
        "factored": len(report["factored"]),
    }


def _compact_tables(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    report = value.get("tboost_tables")
    if report is not None:
        result["table_summary"] = table_summary(report)
    result["tboost_tables"] = None
    if "loss_history" in value:
        result["loss_history"], _ = bounded_loss_history(
            value["loss_history"],
            best_iteration=value.get("best_iteration"),
        )
    return result


def compact_response(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _compact_tables(value)
    result["diagnostics_view"] = "compact"
    if len(result.get("loss_history", [])) < len(value.get("loss_history", [])):
        result["loss_history_truncated"] = True
    # These diagnostics belong to the full result panels, not path decisions.
    for name in ("shap_summary", "shap_beeswarm", "shap_curves", "pdp_data", "actual_vs_predicted"):
        if name in result:
            result[name] = []
    if "shap_link" in result:
        result["shap_link"] = None
    staged = value.get("staged")
    if staged is not None:
        result["staged"] = {
            **staged,
            "stages": {
                name: _compact_tables(snapshot) for name, snapshot in staged["stages"].items()
            },
        }
    return result


def compact_result(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if isinstance(value.get("response"), dict):
        result["response"] = compact_response(value["response"])
    return result


def compact_state(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)

    def record(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "result": compact_result(item["result"])}

    for name in ("current", "preview", "final"):
        if result[name] is not None:
            result[name] = record(result[name])
    result["checkpoints"] = [
        {**item, "record": record(item["record"])} for item in result["checkpoints"]
    ]
    return result
