"""Optimiser input materialisation in a hard-capped worker (process mode).

Production runs solve setup's and auto-range's pipeline execution in a
killable spawn worker; the explicit thread compatibility mode the rest of the
suite uses runs the same steps on the job's thread. These tests opt into
process mode: real spawns prove the worker produces what the thread path
produces, and an inline stand-in for ``run_isolated_worker`` proves how each
worker outcome becomes the job's terminal state.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi import HTTPException

from haute._execution_admission import ExecutionAdmissionError, IsolatedExecutionBudget
from haute._execution_context import ExecutionProfile
from haute._sandbox import set_project_root
from haute._worker_isolation import (
    IsolatedWorkerCrashedError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
)
from haute.routes import _optimiser_artifacts, _optimiser_service, _optimiser_worker
from haute.routes._background_jobs import BackgroundJobStoppedError
from haute.routes._job_store import JobStore, get_job_store
from haute.routes._optimiser_service import OptimiserSolveService
from haute.routes._optimiser_worker import (
    FrontierAutoRangeWorkerOutcome,
    FrontierAutoRangeWorkerRequest,
    OptimiserWorkerFailure,
    SolveInputWorkerOutcome,
    SolveInputWorkerRequest,
    _failure_before_job_mapping,
)
from haute.schemas import OptimiserFrontierAutoRangeRequest
from tests.conftest import make_edge, make_graph

_TERMINAL = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project root a spawned worker can open its stores under."""
    set_project_root(tmp_path)
    return tmp_path


def _process_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")


def _scored_parquet(root: Path, *, null_quote: bool = False) -> Path:
    quote_ids: list[str | None] = []
    steps: list[int] = []
    values: list[float] = []
    incomes: list[float] = []
    volumes: list[float] = []
    for quote in range(8):
        for step, value in enumerate((0.8, 1.0, 1.2)):
            quote_ids.append(f"q{quote:02d}")
            steps.append(step)
            values.append(value)
            incomes.append((100.0 + 10.0 * quote) * value)
            volumes.append((1.0 + 0.1 * quote) * (2.0 - value))
    if null_quote:
        quote_ids[0] = None
    path = root / "scored.parquet"
    pl.DataFrame(
        {
            "quote_id": quote_ids,
            "scenario_index": pl.Series(steps, dtype=pl.Int32),
            "scenario_value": pl.Series(values, dtype=pl.Float32),
            "expected_income": pl.Series(incomes, dtype=pl.Float32),
            "volume": pl.Series(volumes, dtype=pl.Float32),
            "region": [("north", "south")[quote % 2] for quote in range(8) for _ in range(3)],
        }
    ).write_parquet(path)
    return path


def _banding_parquet(scored: Path) -> Path:
    path = scored.parent / "banding.parquet"
    frame = pl.read_parquet(scored).select("quote_id", "region")
    frame.unique(maintain_order=True).write_parquet(path)
    return path


def _optimiser_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "mode": "online",
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "data_input": "source",
        "max_iter": 20,
        "tolerance": 1e-4,
    }
    config.update(overrides)
    return config


def _file_input(path: Path) -> dict[str, Any]:
    return {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(path)}


def _online_graph(path: Path, **overrides: Any) -> dict[str, Any]:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": _file_input(path),
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": _optimiser_config(**overrides),
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    ).model_dump()


def _ratebook_graph(path: Path, banding_path: Path) -> dict[str, Any]:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": _file_input(path),
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataInput",
                        "config": _file_input(banding_path),
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": _optimiser_config(
                            mode="ratebook",
                            factor_columns=[["region"]],
                            banding_source="banding",
                            max_cd_iterations=3,
                            cd_tolerance=1e-3,
                        ),
                    },
                },
            ],
            "edges": [
                make_edge("source", "opt").model_dump(),
                make_edge("banding", "opt").model_dump(),
            ],
        }
    ).model_dump()


def _chunked_auto_range_graph(path: Path) -> dict[str, Any]:
    """Base -> scenario expander -> row-local feature node: a provably chunkable chain."""
    base = path.parent / "base.parquet"
    pl.DataFrame(
        {
            "quote_id": [f"q{quote:02d}" for quote in range(8)],
            "premium": [100.0 + 10.0 * quote for quote in range(8)],
        }
    ).write_parquet(base)
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": _file_input(base),
                    },
                },
                {
                    "id": "scenario",
                    "data": {
                        "label": "scenario",
                        "nodeType": "scenarioExpander",
                        "config": {
                            "quote_id": "quote_id",
                            "column_name": "premium_multiplier",
                            "min_value": 0.8,
                            "max_value": 1.2,
                            "stepCount": 3,
                            "step_column": "scenario_index",
                            "contract": {
                                "inputs": [],
                                "outputs": ["premium_multiplier", "scenario_index"],
                            },
                        },
                    },
                },
                {
                    "id": "features",
                    "data": {
                        "label": "features",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = scenario.with_columns("
                                "volume=2.0 - pl.col('premium_multiplier'),"
                                " expected_income=pl.col('premium')"
                                " * pl.col('premium_multiplier'))"
                            ),
                            "contract": {
                                "inputs": ["premium", "premium_multiplier"],
                                "outputs": ["volume", "expected_income"],
                            },
                        },
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": _optimiser_config(
                            scenario_value="premium_multiplier",
                            data_input="features",
                            auto_range_chunk_size=6,
                        ),
                    },
                },
            ],
            "edges": [
                make_edge("source", "scenario").model_dump(),
                make_edge("scenario", "features").model_dump(),
                make_edge("features", "opt").model_dump(),
            ],
        }
    ).model_dump()


def _poll(client: Any, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in _TERMINAL:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"solve {job_id} did not finish within {timeout}s")


def _removed(path: str | Path, timeout: float = 30.0) -> bool:
    """Whether *path* is gone once the setup thread's cleanup has run.

    A job turns terminal before its setup thread's ``finally`` removes the
    files it owned, so a poller can briefly see both.
    """
    deadline = time.monotonic() + timeout
    while Path(path).exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _solve(client: Any, graph: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert response.status_code == 200, response.text
    return _poll(client, response.json()["job_id"])


def _solve_summary(status: dict[str, Any]) -> dict[str, Any]:
    result = status["result"]
    return {key: result[key] for key in ("lambdas", "total_objective", "constraints", "converged")}


def _record_real_worker(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the supervisor's arguments while still spawning the real worker."""
    calls: list[dict[str, Any]] = []
    real = _optimiser_service.run_isolated_worker

    def recording(function, *args, config=None, **kwargs):
        calls.append({"function": function, "request": args[0], "budget": args[1]})
        return real(function, *args, config=config, **kwargs)

    monkeypatch.setattr(_optimiser_service, "run_isolated_worker", recording)
    return calls


def _child_always_over_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every worker-local context sample one byte over its limit at each checkpoint.

    Only the child's context is affected: the parent admits its own context
    through ``create_admitted_execution_context``.
    """
    real_create = _optimiser_worker.create_isolated_execution_context

    def over_budget(budget: IsolatedExecutionBudget) -> Any:
        context = real_create(budget)
        limit = context.rss_limit_bytes
        context.memory_sampler = lambda: limit + 1
        return context

    monkeypatch.setattr(_optimiser_worker, "create_isolated_execution_context", over_budget)


class _InlineWorker:
    """Stand in for ``run_isolated_worker``: run the entrypoint here, or raise."""

    def __init__(self, *, raises: BaseException | None = None):
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, function, request, budget: IsolatedExecutionBudget, *, config=None):
        self.calls.append({"function": function, "request": request, "config": config})
        if self.raises is not None:
            raise self.raises
        return function(request, budget)


class TestRealWorker:
    def test_online_solve_in_a_worker_matches_the_thread_path(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _online_graph(_scored_parquet(project))
        thread_status = _solve(client, graph)
        assert thread_status["status"] == "completed", thread_status.get("message")

        _process_mode(monkeypatch)
        calls = _record_real_worker(monkeypatch)
        process_status = _solve(client, graph)

        assert process_status["status"] == "completed", process_status.get("message")
        assert [call["function"].__name__ for call in calls] == ["materialise_solve_input_worker"]
        assert isinstance(calls[0]["request"], SolveInputWorkerRequest)
        assert _solve_summary(process_status) == _solve_summary(thread_status)
        assert _removed(calls[0]["request"].output_path)

    def test_ratebook_solve_in_a_worker_matches_the_thread_path(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scored = _scored_parquet(project)
        banding = _banding_parquet(scored)
        graph = _ratebook_graph(scored, banding)
        thread_status = _solve(client, graph)
        assert thread_status["status"] == "completed", thread_status.get("message")

        _process_mode(monkeypatch)
        calls = _record_real_worker(monkeypatch)
        process_status = _solve(client, graph)

        assert process_status["status"] == "completed", process_status.get("message")
        assert len(calls) == 1
        assert _solve_summary(process_status) == _solve_summary(thread_status)
        assert process_status["result"]["factor_tables"] == thread_status["result"]["factor_tables"]

    @pytest.mark.parametrize("chunked", [True, False], ids=["chunked", "full_frame"])
    def test_auto_range_in_a_worker_matches_the_thread_path(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, chunked: bool
    ) -> None:
        path = _scored_parquet(project)
        graph = _chunked_auto_range_graph(path) if chunked else _online_graph(path)
        body = OptimiserFrontierAutoRangeRequest.model_validate({"graph": graph, "node_id": "opt"})
        service = OptimiserSolveService(JobStore())
        prepared = service._prepare_frontier_auto_range(body)
        assert (prepared["streaming_plan"] is not None) is chunked

        def run() -> dict[str, Any]:
            job_id = service._store.create_job(
                {"status": "running", "job_type": "frontier_auto_range"}
            )
            service._store.atomic_update(job_id, {"start_time": time.monotonic()})
            response = service._run_frontier_auto_range_job(body, job_id, **prepared)
            return response.model_dump()

        thread_result = run()
        _process_mode(monkeypatch)
        calls = _record_real_worker(monkeypatch)
        process_result = run()

        assert [call["function"].__name__ for call in calls] == ["frontier_auto_range_worker"]
        assert isinstance(calls[0]["request"], FrontierAutoRangeWorkerRequest)
        assert calls[0]["request"].chunked is chunked
        assert process_result == thread_result

    def test_a_solve_input_the_thread_path_would_borrow_is_written_by_the_worker(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A categorical quote id and solver dtypes already in place: the projected input
        # is an unchanged scan the thread path borrows. The worker must write the parent's file
        # instead, because a capture it made is released when its plan closes.
        path = project / "solver_ready.parquet"
        pl.read_parquet(_scored_parquet(project)).with_columns(
            pl.col("quote_id").cast(pl.Categorical)
        ).write_parquet(path)
        graph = _online_graph(path)
        thread_status = _solve(client, graph)
        assert thread_status["status"] == "completed", thread_status.get("message")

        _process_mode(monkeypatch)
        calls = _record_real_worker(monkeypatch)
        grid_inputs: list[str] = []
        real_build = OptimiserSolveService._build_grid_from_parquet

        def recording_build(self, input_path: str, *args: Any, **kwargs: Any) -> Any:
            grid_inputs.append(input_path)
            return real_build(self, input_path, *args, **kwargs)

        monkeypatch.setattr(OptimiserSolveService, "_build_grid_from_parquet", recording_build)
        process_status = _solve(client, graph)

        assert process_status["status"] == "completed", process_status.get("message")
        assert grid_inputs == [calls[0]["request"].output_path]
        assert _solve_summary(process_status) == _solve_summary(thread_status)

    def test_a_cancelled_auto_range_worker_leaves_no_scratch_files(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Many small reducer batches keep the worker writing bucket parts into its scratch
        # directory long enough to stop it mid-reduction.
        quotes = 50_000
        big = project / "big.parquet"
        pl.DataFrame(
            {
                "quote_id": [f"q{index}" for index in range(quotes) for _ in range(3)],
                "scenario_index": pl.Series([0, 1, 2] * quotes, dtype=pl.Int32),
                "scenario_value": pl.Series([0.8, 1.0, 1.2] * quotes, dtype=pl.Float32),
                "expected_income": pl.Series([1.0, 2.0, 3.0] * quotes, dtype=pl.Float32),
                "volume": pl.Series([3.0, 2.0, 1.0] * quotes, dtype=pl.Float32),
            }
        ).write_parquet(big)
        body = OptimiserFrontierAutoRangeRequest.model_validate(
            {"graph": _online_graph(big, auto_range_chunk_size=500), "node_id": "opt"}
        )
        service = OptimiserSolveService(JobStore())
        prepared = service._prepare_frontier_auto_range(body)
        job_id = service._store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        service._store.atomic_update(job_id, {"start_time": time.monotonic()})
        _process_mode(monkeypatch)
        scratch_dirs: list[Path] = []
        real = _optimiser_service.run_isolated_worker

        def stop_once_scratch_is_used(function, request, budget, *, config=None):
            scratch = Path(request.scratch_dir)
            scratch_dirs.append(scratch)

            def stop_reason() -> str | None:
                return "cancelled" if any(scratch.iterdir()) else None

            return real(
                function,
                request,
                budget,
                config=replace(config, stop_reason=stop_reason, stop_poll_interval_seconds=0.05),
            )

        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", stop_once_scratch_is_used)

        with pytest.raises(BackgroundJobStoppedError):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert len(scratch_dirs) == 1
        assert not scratch_dirs[0].exists()


class TestWorkerOutcomes:
    def _auto_range(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        worker: Callable[..., Any],
        *,
        null_quote: bool = False,
    ) -> tuple[OptimiserSolveService, str, BaseException | None]:
        _process_mode(monkeypatch)
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)
        graph = _online_graph(_scored_parquet(project, null_quote=null_quote))
        body = OptimiserFrontierAutoRangeRequest.model_validate({"graph": graph, "node_id": "opt"})
        service = OptimiserSolveService(JobStore())
        prepared = service._prepare_frontier_auto_range(body)
        job_id = service._store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        service._store.atomic_update(job_id, {"start_time": time.monotonic()})
        raised: BaseException | None = None
        try:
            service._run_frontier_auto_range_job(body, job_id, **prepared)
        except Exception as exc:
            raised = exc
        return service, job_id, raised

    def test_a_child_failure_is_replayed_as_the_thread_path_records_it(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _online_graph(_scored_parquet(project, null_quote=True))
        body = OptimiserFrontierAutoRangeRequest.model_validate({"graph": graph, "node_id": "opt"})
        thread_service = OptimiserSolveService(JobStore())
        prepared = thread_service._prepare_frontier_auto_range(body)
        thread_job = thread_service._store.create_job(
            {"status": "running", "job_type": "frontier_auto_range"}
        )
        with pytest.raises(HTTPException):
            thread_service._run_frontier_auto_range_job(body, thread_job, **prepared)
        expected = thread_service._store.require_job(thread_job)

        worker = _InlineWorker()
        service, job_id, raised = self._auto_range(project, monkeypatch, worker, null_quote=True)
        job = service._store.require_job(job_id)

        assert len(worker.calls) == 1
        assert job["status"] == expected["status"] == "contract_error"
        for key in ("message", "http_status_code", "error_detail"):
            assert job[key] == expected[key]
        assert getattr(raised, "status_code", None) == expected["http_status_code"]

    def test_a_memory_limited_child_ends_the_job_memory_limited(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _child_always_over_its_budget(monkeypatch)
        worker = _InlineWorker()
        service, job_id, _raised = self._auto_range(project, monkeypatch, worker)
        job = service._store.require_job(job_id)

        assert job["status"] == "memory_limited"
        assert job["http_status_code"] == 507
        assert job["error_code"] == "memory_limit"

    def test_a_memory_shaped_worker_failure_is_a_507(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worker = _InlineWorker(
            raises=IsolatedWorkerMemoryLimitExceededError(rss_bytes=2, rss_limit_bytes=1)
        )
        service, job_id, _raised = self._auto_range(project, monkeypatch, worker)
        job = service._store.require_job(job_id)

        assert job["status"] == "memory_limited"
        assert job["http_status_code"] == 507
        assert job["error_detail"]["reason"] == "worker_rss_limit_exceeded"
        assert job["error_detail"]["operation"] == "frontier_auto_range"

    def test_a_stopped_worker_is_the_job_stop_and_follows_its_cancellation(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worker = _InlineWorker(raises=IsolatedWorkerStoppedError(terminal_reason="cancelled"))
        service, job_id, raised = self._auto_range(project, monkeypatch, worker)

        assert isinstance(raised, BackgroundJobStoppedError)
        stop_reason = worker.calls[0]["config"].stop_reason
        assert stop_reason() is None
        service._jobs.register_latest(("frontier_auto_range", job_id), job_id)
        service._jobs.cancel(job_id, reason="cancelled")
        assert stop_reason() == "cancelled"

    def test_the_worker_timeout_times_the_job_out(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worker = _InlineWorker(raises=IsolatedWorkerTimeoutError(timeout_seconds=1.0))
        service, job_id, raised = self._auto_range(project, monkeypatch, worker)
        job = service._store.require_job(job_id)

        assert isinstance(raised, BackgroundJobStoppedError)
        assert job["status"] == "timed_out"
        assert worker.calls[0]["config"].timeout_seconds > 0

    def test_a_solve_setup_child_failure_ends_the_job_and_leaves_no_input(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _process_mode(monkeypatch)
        worker = _InlineWorker()
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)
        graph = _online_graph(_scored_parquet(project), objective="missing_column")

        status = _solve(client, graph)

        assert len(worker.calls) == 1
        assert status["status"] == "contract_error"
        assert "missing_column" in status["message"]
        assert _removed(worker.calls[0]["request"].output_path)

    def test_a_memory_limited_solve_setup_child_ends_the_job_memory_limited(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _process_mode(monkeypatch)
        _child_always_over_its_budget(monkeypatch)
        worker = _InlineWorker()
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)

        status = _solve(client, _online_graph(_scored_parquet(project)))

        assert status["status"] == "memory_limited"
        assert status["terminal_reason"] == "memory_limited"
        assert _removed(worker.calls[0]["request"].output_path)

    def test_a_worker_solve_input_builds_the_same_grid_as_the_thread_path(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _online_graph(_scored_parquet(project))
        thread_status = _solve(client, graph)
        assert thread_status["status"] == "completed", thread_status.get("message")

        _process_mode(monkeypatch)
        worker = _InlineWorker()
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)
        process_status = _solve(client, graph)

        assert process_status["status"] == "completed", process_status.get("message")
        assert _solve_summary(process_status) == _solve_summary(thread_status)
        assert _removed(worker.calls[0]["request"].output_path)

    def test_any_other_worker_failure_is_a_500_error(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _process_mode(monkeypatch)
        worker = _InlineWorker(
            raises=IsolatedWorkerCrashedError(exitcode=1, memory_limit_bytes=None)
        )
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)

        status = _solve(client, _online_graph(_scored_parquet(project)))

        assert status["status"] == "error"
        assert status["message"] == "Optimiser worker failed. Check the server logs for details."
        assert _removed(worker.calls[0]["request"].output_path)

    def test_a_failed_input_write_removes_the_ratebook_factors_the_child_persisted(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scored = _scored_parquet(project)
        banding = _banding_parquet(scored)
        _process_mode(monkeypatch)
        worker = _InlineWorker()
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)
        persisted: list[dict[str, Any]] = []
        real_persist = _optimiser_artifacts._persist_ratebook_factors_lazy_artifact

        def recording_persist(factors_lf: Any, **kwargs: Any) -> dict[str, Any]:
            handle = real_persist(factors_lf, **kwargs)
            persisted.append(handle)
            return handle

        def failing_write(self, *args: Any, **kwargs: Any) -> tuple[str, bool]:
            raise HTTPException(status_code=422, detail="solver input could not be written")

        monkeypatch.setattr(
            _optimiser_artifacts, "_persist_ratebook_factors_lazy_artifact", recording_persist
        )
        monkeypatch.setattr(OptimiserSolveService, "_write_solver_input", failing_write)

        status = _solve(client, _ratebook_graph(scored, banding))

        assert status["status"] == "contract_error"
        assert status["message"] == "solver input could not be written"
        assert len(persisted) == 1
        assert _removed(persisted[0]["directory"])

    def test_a_failed_online_input_write_ends_the_job_and_leaves_no_input(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _process_mode(monkeypatch)
        worker = _InlineWorker()
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)

        def failing_write(self, *args: Any, **kwargs: Any) -> tuple[str, bool]:
            raise HTTPException(status_code=422, detail="solver input could not be written")

        monkeypatch.setattr(OptimiserSolveService, "_write_solver_input", failing_write)

        status = _solve(client, _online_graph(_scored_parquet(project)))

        assert status["status"] == "contract_error"
        assert _removed(worker.calls[0]["request"].output_path)

    def test_a_setup_worker_without_a_timeout_that_times_out_is_an_error(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _process_mode(monkeypatch)
        worker = _InlineWorker(raises=IsolatedWorkerTimeoutError(timeout_seconds=1.0))
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)

        status = _solve(client, _online_graph(_scored_parquet(project)))

        assert worker.calls[0]["config"].timeout_seconds is None
        assert status["status"] == "error"
        assert "without a timeout timed out" in status["message"]

    @pytest.mark.parametrize(
        "outcome",
        [object(), SolveInputWorkerOutcome()],
        ids=["not_an_outcome", "empty_outcome"],
    )
    def test_a_malformed_setup_worker_outcome_is_an_error(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch, outcome: object
    ) -> None:
        _process_mode(monkeypatch)
        monkeypatch.setattr(
            _optimiser_service,
            "run_isolated_worker",
            lambda function, request, budget, *, config=None: outcome,
        )

        status = _solve(client, _online_graph(_scored_parquet(project)))

        assert status["status"] == "error"
        assert "Optimiser setup worker returned" in status["message"]

    @pytest.mark.parametrize(
        "outcome",
        [object(), FrontierAutoRangeWorkerOutcome()],
        ids=["not_an_outcome", "empty_outcome"],
    )
    def test_a_malformed_auto_range_worker_outcome_is_an_error(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, outcome: object
    ) -> None:
        service, job_id, raised = self._auto_range(
            project,
            monkeypatch,
            lambda function, request, budget, *, config=None: outcome,
        )
        job = service._store.require_job(job_id)

        assert job["status"] == "error"
        assert getattr(raised, "status_code", None) == 500

    def test_an_auto_range_out_of_time_before_its_worker_starts_is_timed_out(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _process_mode(monkeypatch)
        worker = _InlineWorker()
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", worker)
        graph = _online_graph(_scored_parquet(project))
        body = OptimiserFrontierAutoRangeRequest.model_validate({"graph": graph, "node_id": "opt"})
        service = OptimiserSolveService(JobStore())
        prepared = service._prepare_frontier_auto_range(body)
        job_id = service._store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        service._store.atomic_update(
            job_id, {"start_time": time.monotonic() - prepared["timeout"] - 1.0}
        )

        with pytest.raises(BackgroundJobStoppedError):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert worker.calls == []
        assert service._store.require_job(job_id)["status"] == "timed_out"

    def test_a_memory_error_behind_a_setup_failure_is_a_507(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The real transport reports a child's MemoryError as a remote MemoryError.
        _process_mode(monkeypatch)

        def transported(function, request, budget, *, config=None):
            try:
                return function(request, budget)
            except MemoryError as exc:
                raise IsolatedWorkerRemoteError(
                    remote_type=type(exc).__name__,
                    remote_message=str(exc),
                    remote_traceback="",
                ) from None

        def refused(*args: Any, **kwargs: Any) -> Any:
            raise MemoryError("native cap refused an allocation")

        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", transported)
        monkeypatch.setattr(_optimiser_service, "execute_lazy_graph", refused)

        status = _solve(client, _online_graph(_scored_parquet(project)))

        assert status["status"] == "memory_limited"
        assert status["terminal_reason"] == "memory_limited"

    def test_a_memory_error_behind_an_auto_range_failure_is_a_507(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def transported(function, request, budget, *, config=None):
            try:
                return function(request, budget)
            except MemoryError as exc:
                raise IsolatedWorkerRemoteError(
                    remote_type=type(exc).__name__,
                    remote_message=str(exc),
                    remote_traceback="",
                ) from None

        def refused(*args: Any, **kwargs: Any) -> Any:
            raise MemoryError("native cap refused an allocation")

        monkeypatch.setattr(_optimiser_service, "_estimate_scenario_frontier_ranges", refused)
        service, job_id, raised = self._auto_range(project, monkeypatch, transported)
        job = service._store.require_job(job_id)

        assert job["status"] == "memory_limited"
        assert job["http_status_code"] == 507
        assert getattr(raised, "status_code", None) == 507

    def test_a_stopped_setup_worker_leaves_neither_scratch_nor_factors(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The parent owns every location the child writes: a stop after the child wrote
        # into them still removes them all.
        scored = _scored_parquet(project)
        _process_mode(monkeypatch)
        requests: list[SolveInputWorkerRequest] = []

        def wrote_then_stopped(function, request, budget, *, config=None):
            requests.append(request)
            (Path(request.scratch_dir) / "bucket_part.parquet").write_bytes(b"partial")
            (Path(request.ratebook_factors_dir) / "factors.parquet").write_bytes(b"partial")
            Path(request.output_path).write_bytes(b"partial")
            raise IsolatedWorkerStoppedError(terminal_reason="cancelled")

        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", wrote_then_stopped)

        status = _solve(client, _ratebook_graph(scored, _banding_parquet(scored)))

        assert status["status"] == "cancelled"
        (request,) = requests
        assert _removed(request.scratch_dir)
        assert _removed(request.ratebook_factors_dir)
        assert _removed(request.output_path)


class TestFailureRecords:
    def test_a_completed_or_running_record_is_not_a_failure(self) -> None:
        for status in ("completed", "running"):
            with pytest.raises(RuntimeError, match="not a failure"):
                OptimiserWorkerFailure.from_job_record({"status": status, "message": ""})

    def test_a_failure_record_keeps_only_its_failure_fields(self) -> None:
        failure = OptimiserWorkerFailure.from_job_record(
            {
                "status": "contract_error",
                "message": "bad input",
                "http_status_code": 400,
                "error_detail": "bad input",
                "progress": 0.5,
                "config": {"objective": "x"},
            }
        )

        assert failure.terminal_reason == "contract_error"
        assert failure.fields == {"http_status_code": 400, "error_detail": "bad input"}
        assert failure.http_status_code == 400
        assert failure.http_detail == "bad input"
        assert OptimiserWorkerFailure("error", "boom", {}).http_status_code == 500

    @pytest.mark.parametrize(
        ("exc", "terminal_reason", "status_code"),
        [
            (
                ExecutionAdmissionError(
                    "frontier_auto_range",
                    profile=ExecutionProfile.AUTO_RANGE,
                    memory_limit_bytes=1,
                    rss_at_admission_bytes=None,
                    reason="memory_sampler_unavailable",
                ),
                "memory_limited",
                507,
            ),
            (HTTPException(status_code=400, detail="bad config"), "contract_error", 400),
            (HTTPException(status_code=503, detail="unavailable"), "error", 503),
            (RuntimeError("plan changed"), "error", 500),
        ],
        ids=["admission", "http_4xx", "http_5xx", "unexpected"],
    )
    def test_a_failure_before_any_job_mapping_is_classified_in_the_child(
        self, exc: BaseException, terminal_reason: str, status_code: int
    ) -> None:
        failure = _failure_before_job_mapping(exc, operation_noun="Frontier auto range")

        assert failure.terminal_reason == terminal_reason
        assert failure.http_status_code == status_code
        if terminal_reason == "memory_limited":
            assert failure.fields["error_code"] == "memory_limit"


class TestPrivateRecords:
    """A worker's private job record never outlives the worker, whatever its outcome."""

    @pytest.mark.parametrize("fails", [False, True], ids=["completes", "fails"])
    def test_a_setup_worker_leaves_its_store_as_it_found_it(
        self, client, project: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
    ) -> None:
        store = get_job_store("optimiser_worker")
        unrelated = store.create_job({"status": "running"})
        before = set(store.list_jobs())
        _process_mode(monkeypatch)
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", _InlineWorker())
        overrides = {"objective": "missing_column"} if fails else {}

        status = _solve(client, _online_graph(_scored_parquet(project), **overrides))

        assert status["status"] == ("contract_error" if fails else "completed")
        assert set(store.list_jobs()) == before
        assert store.get_job(unrelated) is not None

    @pytest.mark.parametrize("fails", [False, True], ids=["completes", "fails"])
    def test_an_auto_range_worker_leaves_its_store_as_it_found_it(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
    ) -> None:
        store = get_job_store("optimiser_worker")
        unrelated = store.create_job({"status": "running"})
        before = set(store.list_jobs())
        _process_mode(monkeypatch)
        monkeypatch.setattr(_optimiser_service, "run_isolated_worker", _InlineWorker())
        graph = _online_graph(_scored_parquet(project, null_quote=fails))
        body = OptimiserFrontierAutoRangeRequest.model_validate({"graph": graph, "node_id": "opt"})
        service = OptimiserSolveService(JobStore())
        prepared = service._prepare_frontier_auto_range(body)
        job_id = service._store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        service._store.atomic_update(job_id, {"start_time": time.monotonic()})

        if fails:
            with pytest.raises(HTTPException):
                service._run_frontier_auto_range_job(body, job_id, **prepared)
        else:
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert service._store.require_job(job_id)["status"] == (
            "contract_error" if fails else "completed"
        )
        assert set(store.list_jobs()) == before
        assert store.get_job(unrelated) is not None


def test_a_self_admitted_auto_range_job_returns_its_admission_when_it_fails(
    project: Path,
) -> None:
    """The job releases the admission it took, not garbage collection.

    The background launcher enters the job without a context. With the failure
    still referenced and the cycle collector off, a reservation left to the
    context's finaliser would stay in flight and refuse the next heavy request.
    """
    import gc

    from haute import _execution_admission

    graph = _online_graph(_scored_parquet(project, null_quote=True))
    body = OptimiserFrontierAutoRangeRequest.model_validate({"graph": graph, "node_id": "opt"})
    service = OptimiserSolveService(JobStore())
    prepared = service._prepare_frontier_auto_range(body)
    job_id = service._store.create_job({"status": "running", "job_type": "frontier_auto_range"})

    def held() -> list[str]:
        return [
            operation
            for _profile, _bytes, operation in _execution_admission._IN_FLIGHT_RESERVATIONS.values()
            if operation == "frontier_auto_range"
        ]

    assert held() == []
    gc.disable()
    try:
        with pytest.raises(HTTPException) as raised:
            service._run_frontier_auto_range_job(body, job_id, **prepared)
        assert raised.value.status_code == 400
        assert held() == []
    finally:
        gc.enable()
