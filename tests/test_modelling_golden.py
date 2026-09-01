"""Golden snapshots for modelling route payloads."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

import haute.routes.modelling as modelling_routes
from haute._types import PipelineGraph
from haute.schemas import TrainRequest, TrainResponse
from tests.job_store_support import seed_job

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ui_contracts"


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_train_model_start_route_matches_ui_contract_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _load_fixture("train_started_response")
    expected = TrainResponse.model_validate(fixture)

    monkeypatch.setattr(modelling_routes._train_service, "start", lambda _body: expected)

    response = modelling_routes.train_model(
        TrainRequest(graph=PipelineGraph(), node_id="train"),
    )

    assert response.model_dump(mode="json") == fixture


def test_train_status_route_matches_ui_contract_fixture() -> None:
    fixture = _load_fixture("train_status_response")
    job_id = "job-1"
    seed_job(
        modelling_routes._store,
        job_id,
        {
            "status": fixture["status"],
            "progress": fixture["progress"],
            "message": fixture["message"],
            "iteration": fixture["iteration"],
            "total_iterations": fixture["total_iterations"],
            "train_loss": fixture["train_loss"],
            "elapsed_seconds": fixture["elapsed_seconds"],
            "result": TrainResponse.model_validate(fixture["result"]),
            "warning": fixture["warning"],
            "created_at": time.time(),
        },
    )

    try:
        response = asyncio.run(modelling_routes.train_status(job_id))
    finally:
        modelling_routes._store.delete_job(job_id)

    assert response.model_dump(mode="json") == fixture
