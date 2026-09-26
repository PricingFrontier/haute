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
from haute.routes._optimiser_solver import solve_input_summary
from haute.routes.optimiser import _build_artifact_payload
from haute.schemas import OptimiserStatusResponse
from tests.job_store_support import seed_job
from tests.optimiser_fixtures import make_frontier_point

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
    seed_job(
        optimiser_routes._store,
        job_id,
        {
            "status": fixture["status"],
            "progress": fixture["progress"],
            "message": fixture["message"],
            "elapsed_seconds": fixture["elapsed_seconds"],
            "result": validated.result.model_dump(mode="python") if validated.result else None,
            "frontier_data": frontier,
            "created_at": time.time(),
        },
    )

    try:
        response = asyncio.run(optimiser_routes.solve_status(job_id))
    finally:
        optimiser_routes._store.delete_job(job_id)

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
        "selected_frontier_point": 0,
        "frontier_data": {
            "n_points": 4,
            "constraint_names": ["loss"],
            "points": [
                make_frontier_point(thresholds={"loss": 0.85}),
                make_frontier_point(thresholds={"loss": 0.92}),
            ],
        },
        "input_provenance": {
            "node_id": "my_opt",
            "data_source": "batch",
            "source_file": "main.py",
            "graph_fingerprint": "f00d",
        },
    }
    # The solve result's own summary, built once when the solve completed.
    job["base_result"] = {"n_quotes": 250, "n_steps": 5, "input_summary": solve_input_summary(job)}
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

    payload = _build_artifact_payload(
        job,
        solve_result,
        version_override="opt_v1",
        point_index=1,
        stale_at_publish=True,
    )

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
            "max_cd_iterations": 6,
        },
        "input_provenance": {
            "node_id": "ratebook_opt",
            "data_source": "batch",
            "source_file": None,
            "graph_fingerprint": "beef",
        },
    }
    job["result"] = {
        "n_quotes": 400,
        "n_steps": 7,
        "input_summary": solve_input_summary(job),
        "factor_tables": {
            "region": [
                {"__factor_group__": "North", "optimal_scenario_value": 1.1, "quote_count": 400}
            ]
        },
        "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
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
        combined_factor_bounds={"min": 0.9, "max": 1.1},
    )

    payload = _build_artifact_payload(job, solve_result, version_override="rb_v1")

    assert payload == _load_golden_fixture("optimiser_artifact_ratebook")
