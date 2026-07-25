"""Golden snapshots for optimiser route and artifact payloads."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import haute.routes.optimiser as optimiser_routes
from haute.routes.optimiser import _build_artifact_payload
from haute.schemas import OptimiserStatusResponse

_UI_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ui_contracts"
_GOLDEN_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "golden"


def _load_ui_fixture(name: str) -> dict[str, object]:
    return json.loads((_UI_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _load_golden_fixture(name: str) -> dict[str, object]:
    return json.loads((_GOLDEN_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _freeze_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = dt.datetime(2024, 1, 2, 3, 4, 5, tzinfo=dt.UTC)

    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> FrozenDateTime:
            if tz is None:
                return cls(2024, 1, 2, 3, 4, 5)
            aware = frozen.astimezone(tz)
            return cls(
                aware.year,
                aware.month,
                aware.day,
                aware.hour,
                aware.minute,
                aware.second,
                aware.microsecond,
                tzinfo=aware.tzinfo,
            )

    monkeypatch.setattr(dt, "datetime", FrozenDateTime)


def test_solve_status_route_matches_ui_contract_fixture() -> None:
    fixture = _load_ui_fixture("optimiser_status_response")
    validated = OptimiserStatusResponse.model_validate(fixture)
    job_id = "job-1"
    frontier = validated.frontier.model_dump(mode="python") if validated.frontier else None
    optimiser_routes._store.jobs[job_id] = {
        "status": fixture["status"],
        "progress": fixture["progress"],
        "message": fixture["message"],
        "elapsed_seconds": fixture["elapsed_seconds"],
        "result": validated.result,
        "frontier_data": frontier,
        "created_at": time.time(),
    }

    try:
        response = asyncio.run(optimiser_routes.solve_status(job_id))
    finally:
        optimiser_routes._store.jobs.pop(job_id, None)

    assert response.model_dump(mode="json") == fixture


def test_build_artifact_payload_matches_online_golden_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_datetime(monkeypatch)
    job = {
        "node_label": "My Opt",
        "config": {
            "mode": "online",
            "constraints": {"loss": {"min": 0.9}},
            "objective": "income",
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
            "chunk_size": 4096,
        },
        "selected_frontier_point": 1,
        "frontier_data": {"n_points": 4},
    }
    solve_result = SimpleNamespace(
        lambdas={"loss": 0.3},
        total_objective=125.0,
        baseline_objective=119.5,
        total_constraints={"loss": 0.92},
        baseline_constraints={"loss": 0.88},
        converged=True,
        iterations=7,
        cd_iterations=None,
    )

    payload = _build_artifact_payload(job, solve_result, version_override="opt_v1")

    assert payload == _load_golden_fixture("optimiser_artifact_online")


def test_build_artifact_payload_matches_ratebook_golden_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_datetime(monkeypatch)
    job = {
        "node_label": "Ratebook Opt",
        "config": {
            "mode": "ratebook",
            "constraints": {"volume": {"min": 0.95}},
            "objective": "margin",
            "quote_id": "policy_id",
            "scenario_index": "scenario_idx",
            "scenario_value": "scenario_value",
            "chunk_size": 100000,
        },
        "result": {
            "factor_tables": {
                "region": [{"__factor_group__": "North", "optimal_scenario_value": 1.1}]
            },
            "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
        },
    }
    solve_result = SimpleNamespace(
        lambdas={"volume": 0.4},
        total_objective=88.0,
        baseline_objective=82.5,
        total_constraints={"volume": 0.97},
        baseline_constraints={"volume": 0.92},
        converged=False,
        iterations=11,
        cd_iterations=4,
        clamp_rate=0.05,
    )

    payload = _build_artifact_payload(job, solve_result, version_override="rb_v1")

    assert payload == _load_golden_fixture("optimiser_artifact_ratebook")
