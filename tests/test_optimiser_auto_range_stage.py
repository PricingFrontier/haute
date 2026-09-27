"""Auto-range runs the optimiser solve setup's pipeline stage, then reduces its frame.

It admits as solve setup does, inside its background job, and reads the
resolved data-input frame in batches of the pipeline's streaming chunk size.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi import HTTPException

from haute import _execution_admission
from haute._execution_admission import ExecutionAdmissionError, create_admitted_execution_context
from haute._execution_context import ExecutionProfile
from haute._polars_utils import set_streaming_chunk_size
from haute._sandbox import set_project_root
from haute.routes import _optimiser_service
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import OptimiserSolveService
from haute.routes._optimiser_session import ESTIMATE_HOLDERS
from haute.schemas import OptimiserFrontierAutoRangeRequest
from tests.conftest import make_edge, make_graph

_QUOTES = 40
_SCENARIOS = 3


@pytest.fixture()
def scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    set_project_root(tmp_path)
    monkeypatch.setenv("HAUTE_OPTIMISER_SETUP_MEMORY_LIMIT_MB", "256")
    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "1024")
    rows = [(quote, step) for quote in range(_QUOTES) for step in range(_SCENARIOS)]
    path = tmp_path / "scored.parquet"
    pl.DataFrame(
        {
            "quote_id": [f"q{quote:03d}" for quote, _ in rows],
            "scenario_index": [step for _, step in rows],
            "scenario_value": [0.9 + 0.1 * step for _, step in rows],
            "expected_income": [100.0 + quote + 5.0 * step for quote, step in rows],
            "volume": [float(quote % 7) - step for quote, step in rows],
        }
    ).write_parquet(path)
    return path


def _body(path: Path, **config: Any) -> OptimiserFrontierAutoRangeRequest:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(path),
                        },
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "online",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.0}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "data_input": "source",
                            **config,
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    )
    return OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")


def _run(
    service: OptimiserSolveService, body: OptimiserFrontierAutoRangeRequest
) -> tuple[str, Any]:
    """Run one auto-range job as its background thread does: no caller-owned context."""
    _node, prepared = service._prepare_frontier_auto_range(body)
    job_id = service._store.create_job({"status": "running", "job_type": "frontier_auto_range"})
    service._store.atomic_update(job_id, {"start_time": time.monotonic()})
    return job_id, service._run_frontier_auto_range_job(body, job_id, **prepared)


def _held_reservations() -> list[tuple[str, str]]:
    return sorted(
        (profile.value, operation)
        for profile, _bytes, operation in _execution_admission._IN_FLIGHT_RESERVATIONS.values()
    )


def test_auto_range_admits_as_solve_setup_does_after_the_estimate_releases(
    scored: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grants: list[dict[str, Any]] = []
    real_grant = _optimiser_service.admit_growth_grant

    def recording_grant(**kwargs: Any) -> Any:
        grants.append(kwargs)
        return real_grant(**kwargs)

    stage_starts: list[tuple[float, list[tuple[str, str]]]] = []
    real_stage = OptimiserSolveService._run_optimiser_input_stage

    def recording_stage(self: OptimiserSolveService, *args: Any, **kwargs: Any) -> Any:
        stage_starts.append((time.monotonic(), _held_reservations()))
        return real_stage(self, *args, **kwargs)

    monkeypatch.setattr(_optimiser_service, "admit_growth_grant", recording_grant)
    monkeypatch.setattr(OptimiserSolveService, "_run_optimiser_input_stage", recording_stage)
    # Admitted exactly as the estimate route admits it.
    estimate = create_admitted_execution_context(
        operation="optimiser_estimate", profile=ExecutionProfile.OPTIMISER_SETUP
    )
    assert ("optimiser_setup", "optimiser_estimate") in _held_reservations()
    released_at: list[float] = []

    def release() -> None:
        released_at.append(time.monotonic())
        estimate.release_admission()

    releaser = threading.Timer(0.3, release)
    releaser.start()
    try:
        _job_id, response = _run(OptimiserSolveService(JobStore()), _body(scored))
    finally:
        releaser.join()
        estimate.release_admission()

    assert response.status == "ok"
    (grant,) = grants
    assert grant["profile"] is ExecutionProfile.OPTIMISER_SOLVE
    assert grant["operation"] == "frontier_auto_range"
    assert grant["wait_out_holders"] == ESTIMATE_HOLDERS
    ((stage_started, held),) = stage_starts
    # The job waited for the estimate to release before running the pipeline,
    # and ran it under its own solve-profile reservation.
    assert stage_started >= released_at[0]
    assert ("optimiser_setup", "optimiser_estimate") not in held
    assert ("optimiser_solve", "frontier_auto_range") in held
    assert ("optimiser_solve", "frontier_auto_range") not in _held_reservations()


def test_an_auto_range_admission_refusal_ends_the_job_memory_limited(
    scored: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(**kwargs: Any) -> Any:
        raise ExecutionAdmissionError(
            kwargs["operation"],
            profile=kwargs["profile"],
            memory_limit_bytes=1,
            rss_at_admission_bytes=None,
            reason="memory_sampler_unavailable",
        )

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a refused job ran the pipeline")

    monkeypatch.setattr(_optimiser_service, "admit_growth_grant", refuse)
    monkeypatch.setattr(OptimiserSolveService, "_run_optimiser_input_stage", never)
    service = OptimiserSolveService(JobStore())

    with pytest.raises(HTTPException) as raised:
        _run(service, _body(scored))

    assert raised.value.status_code == 507
    (job,) = [service._store.require_job(job_id) for job_id in service._store.list_jobs()]
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"


def _recording_batches(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, list[int]]]:
    """Record each auto-range read: its requested batch rows and the batch heights."""
    reads: list[tuple[int, list[int]]] = []
    real = _optimiser_service.bounded_collect_batches

    def recording(frame: Any, *, chunk_size: int, **kwargs: Any) -> Any:
        heights: list[int] = []
        reads.append((chunk_size, heights))
        for batch in real(frame, chunk_size=chunk_size, **kwargs):
            heights.append(batch.height)
            yield batch

    monkeypatch.setattr(_optimiser_service, "bounded_collect_batches", recording)
    return reads


def test_auto_range_batches_follow_the_pipeline_streaming_chunk_size(
    scored: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _recording_batches(monkeypatch)
    service = OptimiserSolveService(JobStore())

    set_streaming_chunk_size(7)
    _job, seven = _run(service, _body(scored))
    set_streaming_chunk_size(50)
    _job, fifty = _run(service, _body(scored))

    ((seven_rows, seven_heights), (fifty_rows, fifty_heights)) = reads
    assert (seven_rows, fifty_rows) == (7, 50)
    assert sum(seven_heights) == sum(fifty_heights) == _QUOTES * _SCENARIOS
    assert max(seven_heights) <= 7 < max(fifty_heights) <= 50
    assert seven.ranges == fifty.ranges


def test_the_optimiser_chunk_size_does_not_size_auto_range_batches(
    scored: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _recording_batches(monkeypatch)
    service = OptimiserSolveService(JobStore())
    set_streaming_chunk_size(11)

    _job, unset = _run(service, _body(scored))
    _job, tiny = _run(service, _body(scored, chunk_size=2))
    _job, huge = _run(service, _body(scored, chunk_size=100_000))

    assert [rows for rows, _heights in reads] == [11, 11, 11]
    assert reads[0][1] == reads[1][1] == reads[2][1]
    assert unset.ranges == tiny.ranges == huge.ranges


def test_optimiser_workers_spawn_with_a_capped_polars_thread_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup and auto-range workers get POLARS_MAX_THREADS at spawn; the override wins."""
    from haute.routes._optimiser_worker import resolve_optimiser_polars_threads

    captured: list[dict[str, str]] = []

    def fake_run(function: Any, request: Any, budget: Any, *, config: Any) -> str:
        captured.append(dict(config.environment))
        return "done"

    monkeypatch.setattr(_optimiser_service, "run_isolated_worker", fake_run)
    service = OptimiserSolveService(JobStore())
    context = create_admitted_execution_context(
        operation="optimiser_setup_test",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
    )
    try:
        for override, expected in ((None, str(resolve_optimiser_polars_threads())), ("3", "3")):
            if override is None:
                monkeypatch.delenv("HAUTE_OPTIMISER_POLARS_THREADS", raising=False)
            else:
                monkeypatch.setenv("HAUTE_OPTIMISER_POLARS_THREADS", override)
            service._run_optimiser_worker(
                lambda *_: None,
                None,
                job_id="job",
                node_id="opt",
                execution_context=context,
                timeout_seconds=None,
                process_name="haute-optimiser-setup",
            )
            assert captured[-1]["POLARS_MAX_THREADS"] == expected
    finally:
        context.release_admission()
    assert int(captured[0]["POLARS_MAX_THREADS"]) <= 8
