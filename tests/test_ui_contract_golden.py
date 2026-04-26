"""Golden snapshots for the canonical UI contract fixture corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute.schemas import (
    GitStatusResponse,
    OptimiserStatusResponse,
    PreviewNodeResponse,
    SavePipelineResponse,
    SchemaResponse,
    TraceResponse,
    TrainResponse,
    TrainStatusResponse,
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
        ("train_status_response", TrainStatusResponse),
        ("optimiser_status_response", OptimiserStatusResponse),
        ("git_status_response", GitStatusResponse),
    ],
)
def test_ui_contract_fixture_is_canonical_json_snapshot(
    fixture_name: str,
    model: type[Any],
) -> None:
    fixture = _load_fixture(fixture_name)
    validated = model.model_validate(fixture)

    assert validated.model_dump(mode="json") == fixture


def test_train_status_fixture_keeps_nested_train_result_complete() -> None:
    fixture = _load_fixture("train_status_response")
    validated = TrainStatusResponse.model_validate(fixture)

    assert validated.result is not None
    assert validated.result.model_dump(mode="json") == fixture["result"]
