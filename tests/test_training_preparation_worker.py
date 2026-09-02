"""Supervisor and child contract for the hard-capped training preparation worker.

``TrainService._execute_and_sink`` no longer materialises training data in the
server thread: it supervises exactly one ``haute-training-prep`` spawn worker.
These tests pin the whole outcome/exception mapping table with the worker
monkeypatched, then prove the real spawn path end to end.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from haute import _worker_isolation
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
)
from haute._worker_isolation import (
    IsolatedWorkerCrashedError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
)
from haute.routes import _training_lifecycle
from haute.routes._job_store import JobStore
from haute.routes._train_service import TrainService
from haute.routes._training_preparation import (
    TrainingPreparationFailure,
    TrainingPreparationOutcome,
    TrainingPreparationRequest,
    prepare_training_data,
)
from haute.schemas import TrainRequest
from tests.conftest import make_file_input_config

_MEMORY_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# Graph fixtures
# ---------------------------------------------------------------------------


def _training_frame(rows: int = 40) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "x1": [float(index % 7) for index in range(rows)],
            "x2": [float(index % 3) for index in range(rows)],
            "y": [float(index % 5) for index in range(rows)],
        }
    )


@pytest.fixture()
def training_parquet(tmp_path: Path) -> str:
    path = tmp_path / "train_data.parquet"
    _training_frame().write_parquet(path)
    return str(path)


def _training_graph(data_path: str) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "source",
                "data": {
                    "label": "source",
                    "nodeType": "dataInput",
                    "config": make_file_input_config(data_path),
                },
            },
            {
                "id": "train",
                "data": {
                    "label": "train",
                    "nodeType": "modelling",
                    "config": {
                        "target": "y",
                        "algorithm": "catboost",
                        "task": "regression",
                        "loss_function": "RMSE",
                        "params": {"iterations": 2, "depth": 2, "learning_rate": 0.3},
                        "evaluation": {
                            "schema_version": 1,
                            "strategy": "random",
                            "seed": 42,
                            "validation": {"method": "single", "size": 0.2},
                        },
                        "metrics": ["rmse"],
                    },
                },
            },
        ],
        "edges": [{"id": "e1", "source": "source", "target": "train"}],
    }


def _unsizable_group_by_graph(tmp_path: Path) -> dict[str, Any]:
    """A graph whose materialisation estimate is genuinely unavailable.

    ``explode`` has unbounded row expansion, so the cardinality proof stops at
    that node and the downstream group-by boundary cannot be sized. Without a
    hard cap this is the typed ``materialisation_estimate_unavailable``
    rejection; inside the capped preparation worker it must plan conservatively
    instead.
    """
    path = tmp_path / "unsizable.parquet"
    rows = 24
    pl.DataFrame(
        {
            "g": [f"g{index % 4}" for index in range(rows)],
            "vals": [[float(index), float(index + 1)] for index in range(rows)],
            "y": [float(index % 5) for index in range(rows)],
        }
    ).write_parquet(path)
    return {
        "nodes": [
            {
                "id": "source",
                "data": {
                    "label": "src",
                    "nodeType": "dataInput",
                    "config": make_file_input_config(str(path)),
                },
            },
            {
                "id": "boom",
                "data": {
                    "label": "boom",
                    "nodeType": "polars",
                    "config": {"code": 'df = src.explode("vals")'},
                },
            },
            {
                "id": "agg",
                "data": {
                    "label": "agg",
                    "nodeType": "polars",
                    "config": {
                        "code": (
                            'df = boom.group_by("g").agg('
                            'pl.col("y").sum().alias("y"), pl.col("vals").mean().alias("x1"))'
                        )
                    },
                },
            },
            {
                "id": "train",
                "data": {
                    "label": "train",
                    "nodeType": "modelling",
                    "config": {
                        "target": "y",
                        "algorithm": "catboost",
                        "task": "regression",
                        "loss_function": "RMSE",
                        "params": {"iterations": 2, "depth": 2, "learning_rate": 0.3},
                        "evaluation": {
                            "schema_version": 1,
                            "strategy": "random",
                            "seed": 42,
                            "validation": {"method": "single", "size": 0.2},
                        },
                        "metrics": ["rmse"],
                    },
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "source", "target": "boom"},
            {"id": "e2", "source": "boom", "target": "agg"},
            {"id": "e3", "source": "agg", "target": "train"},
        ],
    }


def _glm_dispersion_graph(data_path: str) -> dict[str, Any]:
    """A negbinomial GLM node whose theta is estimable by dispersion search."""
    graph = _training_graph(data_path)
    graph["nodes"][1]["data"]["config"] = {
        "target": "y",
        "algorithm": "glm",
        "task": "regression",
        "family": "negbinomial",
        "terms": {"x1": {"type": "linear"}, "x2": {"type": "linear"}},
        "params": {},
        "evaluation": {
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "validation": {"method": "single", "size": 0.2},
        },
    }
    return graph


# ---------------------------------------------------------------------------
# Preparation driver
# ---------------------------------------------------------------------------


class _PreparationRun:
    def __init__(self) -> None:
        self.store = JobStore()
        self.service = TrainService(self.store)
        self.job_id = ""
        self.release_operations: list[str] = []
        self.worker_calls: list[dict[str, Any]] = []

    @property
    def job(self) -> Any:
        return self.store.require_job(self.job_id)

    @property
    def parquet_path(self) -> str:
        assert self.worker_calls, "no preparation worker was launched"
        return str(self.worker_calls[0]["request"].parquet_path)

    def prepared_admission_releases(self) -> int:
        return self.release_operations.count("training_pipeline")


def _drive_preparation(
    monkeypatch: pytest.MonkeyPatch,
    graph: dict[str, Any],
    *,
    worker: Any,
    run: _PreparationRun | None = None,
) -> _PreparationRun:
    """Run one full ``_prepare_and_launch_training`` with a stubbed worker."""
    run = run if run is not None else _PreparationRun()
    body = TrainRequest.model_validate({"graph": graph, "node_id": "train"})
    config = dict(body.graph.node_map["train"].data.config)
    run.job_id = run.store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "progress": 0.0,
            "message": "Preparing training data...",
            "config": config,
            "node_label": "train",
            "start_time": time.monotonic(),
            "timeout": 600,
        }
    )
    token = ExecutionCancellationToken()
    run.service._training_jobs.register_latest(
        ("training", run.job_id),
        run.job_id,
        execution_token=token,
    )

    real_release = ExecutionContext.release_admission

    def counted_release(self: ExecutionContext, *args: Any, **kwargs: Any) -> Any:
        run.release_operations.append(self.operation)
        return real_release(self, *args, **kwargs)

    monkeypatch.setattr(ExecutionContext, "release_admission", counted_release)

    def fake_run_isolated_worker(function, *args, config=None, **kwargs):
        run.worker_calls.append(
            {
                "function": function,
                "request": args[0],
                "budget": args[1],
                "config": config,
            }
        )
        return worker(args[0], args[1])

    monkeypatch.setattr(
        _training_lifecycle,
        "run_isolated_worker",
        fake_run_isolated_worker,
    )
    monkeypatch.setattr(
        TrainService,
        "_launch_background",
        lambda *args, **kwargs: None,
    )
    run.service._prepare_and_launch_training(run.job_id, body, "train", config, token)
    return run


def _failing_worker(failure: TrainingPreparationFailure) -> Any:
    def worker(request: TrainingPreparationRequest, _budget: Any) -> TrainingPreparationOutcome:
        Path(request.parquet_path).unlink(missing_ok=True)
        return TrainingPreparationOutcome(
            execution_metrics={"status": "failed"},
            failure=failure,
        )

    return worker


def _raising_worker(exc: BaseException) -> Any:
    def worker(request: TrainingPreparationRequest, _budget: Any) -> TrainingPreparationOutcome:
        raise exc

    return worker


class TestPreparationWorkerLaunch:
    def test_launches_exactly_one_capped_worker_for_the_remaining_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        observed: dict[str, Any] = {}
        holder: list[_PreparationRun] = []

        def worker(request: TrainingPreparationRequest, _budget: Any) -> TrainingPreparationOutcome:
            # The stop reason must read the live cancellation registry while
            # the child is still supervised, not a snapshot taken at launch.
            run = holder[0]
            stop_reason = run.worker_calls[0]["config"].stop_reason
            observed["before_cancel"] = stop_reason()
            run.service._training_jobs.cancel(run.job_id, reason="cancelled")
            observed["after_cancel"] = stop_reason()
            Path(request.parquet_path).unlink(missing_ok=True)
            return TrainingPreparationOutcome(
                failure=TrainingPreparationFailure(
                    terminal_reason="error",
                    message="stop here",
                    http_status_code=500,
                    http_detail="stop here",
                    fields={"error": "stop here"},
                )
            )

        run = _PreparationRun()
        holder.append(run)
        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=worker,
            run=run,
        )

        assert len(run.worker_calls) == 1
        call = run.worker_calls[0]
        assert call["function"].__name__ == "prepare_training_data_worker"
        worker_config = call["config"]
        assert worker_config.process_name == "haute-training-prep"
        assert worker_config.memory_limit_bytes == call["budget"].memory_limit_bytes
        assert worker_config.timeout_seconds is not None
        assert 0 < worker_config.timeout_seconds <= 600
        assert observed == {"before_cancel": None, "after_cancel": "cancelled"}


class TestInChildFailureOutcomes:
    @pytest.mark.parametrize(
        ("terminal_reason", "status_code", "error_code"),
        [
            ("contract_error", 422, "training_contract"),
            ("memory_limited", 507, "memory_limit"),
            ("error", 500, None),
        ],
    )
    def test_child_failure_becomes_its_terminal_job_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
        terminal_reason: str,
        status_code: int,
        error_code: str | None,
    ) -> None:
        detail: Any = (
            {"error_code": error_code, "message": "child said no"}
            if error_code is not None
            else "child said no"
        )
        failure = TrainingPreparationFailure(
            terminal_reason=terminal_reason,  # type: ignore[arg-type]
            message="child said no",
            http_status_code=status_code,
            http_detail=detail,
            fields={
                "error": "child said no",
                "error_detail": detail,
                "error_code": error_code,
                "http_status_code": status_code,
            },
        )
        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=_failing_worker(failure),
        )

        job = run.job
        assert job["status"] == terminal_reason
        assert job["terminal_reason"] == terminal_reason
        assert job["message"] == "child said no"
        assert job["error_code"] == error_code
        assert job["http_status_code"] == status_code
        assert job["error_detail"] == detail
        assert job["execution_metrics"] == {"status": "failed"}
        assert run.prepared_admission_releases() == 1
        assert not Path(run.parquet_path).exists()


class TestPublicationFailure:
    def test_success_outcome_without_its_file_is_an_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        def worker(request: TrainingPreparationRequest, _budget: Any) -> TrainingPreparationOutcome:
            Path(request.parquet_path).unlink(missing_ok=True)
            return TrainingPreparationOutcome(
                parquet_path=request.parquet_path,
                feature_selection=None,
                execution_metrics={"status": "completed"},
            )

        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=worker,
        )

        job = run.job
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert job["http_status_code"] == 500
        assert "did not produce its prepared data" in job["message"]
        assert run.prepared_admission_releases() == 1
        assert not Path(run.parquet_path).exists()


class TestWorkerExceptionMapping:
    def test_stopped_worker_is_a_cancellation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=_raising_worker(IsolatedWorkerStoppedError(terminal_reason="cancelled")),
        )

        job = run.job
        assert job["status"] == "cancelled"
        assert job["terminal_reason"] == "cancelled"
        assert run.prepared_admission_releases() == 1
        assert not Path(run.parquet_path).exists()

    def test_timed_out_worker_marks_the_job_timed_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=_raising_worker(IsolatedWorkerTimeoutError(timeout_seconds=600.0)),
        )

        job = run.job
        assert job["status"] == "timed_out"
        assert job["terminal_reason"] == "timed_out"
        assert run.prepared_admission_releases() == 1
        assert not Path(run.parquet_path).exists()

    @pytest.mark.parametrize(
        "exc",
        [
            IsolatedWorkerMemoryLimitExceededError(
                rss_bytes=_MEMORY_LIMIT_BYTES + 1,
                rss_limit_bytes=_MEMORY_LIMIT_BYTES,
            ),
            IsolatedWorkerMemoryLimitUnsupportedError(memory_limit_bytes=_MEMORY_LIMIT_BYTES),
            IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=_MEMORY_LIMIT_BYTES),
            IsolatedWorkerRemoteError(
                remote_type="MemoryError",
                remote_message="out of memory",
                remote_traceback="",
            ),
        ],
        ids=["rss_breach", "cap_unsupported", "crash_with_memory_guess", "remote_memory"],
    )
    def test_memory_shaped_worker_failures_are_507_memory_limited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
        exc: BaseException,
    ) -> None:
        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=_raising_worker(exc),
        )

        job = run.job
        assert job["status"] == "memory_limited"
        assert job["terminal_reason"] == "memory_limited"
        assert job["http_status_code"] == 507
        assert job["error_code"] == "memory_limit"
        detail = job["error_detail"]
        assert detail["operation"] == "training_pipeline"
        assert detail["memory_limit_bytes"] == run.worker_calls[0]["budget"].memory_limit_bytes
        assert run.prepared_admission_releases() == 1
        assert not Path(run.parquet_path).exists()

    @pytest.mark.parametrize(
        "exc",
        [
            IsolatedWorkerCrashedError(exitcode=1, memory_limit_bytes=None),
            IsolatedWorkerRemoteError(
                remote_type="ValueError",
                remote_message="bad frame",
                remote_traceback="",
            ),
        ],
        ids=["crash_without_memory_guess", "remote_non_memory"],
    )
    def test_other_worker_failures_are_500_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
        exc: BaseException,
    ) -> None:
        run = _drive_preparation(
            monkeypatch,
            _training_graph(training_parquet),
            worker=_raising_worker(exc),
        )

        job = run.job
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert job["http_status_code"] == 500
        assert "Training preparation failed" in job["message"]
        assert run.prepared_admission_releases() == 1
        assert not Path(run.parquet_path).exists()


# ---------------------------------------------------------------------------
# Real spawn
# ---------------------------------------------------------------------------


def _record_real_worker(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record supervisor arguments while still spawning the real worker."""
    calls: list[dict[str, Any]] = []
    real = _worker_isolation.run_isolated_worker

    def recording(function, *args, config=None, **kwargs):
        calls.append({"request": args[0], "budget": args[1], "config": config})
        return real(function, *args, config=config, **kwargs)

    monkeypatch.setattr(_training_lifecycle, "run_isolated_worker", recording)
    return calls


def _spawn_preparation(
    monkeypatch: pytest.MonkeyPatch,
    graph: dict[str, Any],
) -> tuple[_PreparationRun, list[dict[str, Any]]]:
    run = _PreparationRun()
    calls = _record_real_worker(monkeypatch)
    body = TrainRequest.model_validate({"graph": graph, "node_id": "train"})
    config = dict(body.graph.node_map["train"].data.config)
    run.job_id = run.store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "progress": 0.0,
            "message": "Preparing training data...",
            "config": config,
            "node_label": "train",
            "start_time": time.monotonic(),
            "timeout": 600,
        }
    )
    token = ExecutionCancellationToken()
    run.service._training_jobs.register_latest(
        ("training", run.job_id),
        run.job_id,
        execution_token=token,
    )
    launched: list[str] = []

    def capture_launch(self, job_id, node_id, cfg, train_params, tmp_parquet, *args, **kwargs):
        launched.append(tmp_parquet)
        return None

    monkeypatch.setattr(TrainService, "_launch_background", capture_launch)
    run.service._prepare_and_launch_training(run.job_id, body, "train", config, token)
    run.worker_calls = calls
    run.__dict__["launched"] = launched
    return run, calls


class TestRealSpawnPreparation:
    def test_real_worker_hands_the_parquet_back_under_its_own_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        run, calls = _spawn_preparation(monkeypatch, _training_graph(training_parquet))

        job = run.job
        assert job["status"] == "running", job.get("message")
        assert len(calls) == 1
        budget = calls[0]["budget"]
        launched = run.__dict__["launched"]
        assert launched and Path(launched[0]).exists()
        assert pl.scan_parquet(launched[0]).collect().height > 0
        metrics = job["execution_metrics"]
        assert metrics["admission"]["profile"] == "training_prep"
        assert metrics["admission"]["memory_limit_bytes"] == budget.memory_limit_bytes
        assert job["feature_selection"] is not None
        Path(launched[0]).unlink(missing_ok=True)

    def test_unavailable_estimate_runs_conservatively_inside_the_worker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        _widen_sandbox_root: None,
    ) -> None:
        """The hard cap turns a 422 rejection into a conservative warned plan."""
        run, _calls = _spawn_preparation(monkeypatch, _unsizable_group_by_graph(tmp_path))

        job = run.job
        assert job["status"] == "running", job.get("message")
        strategy = job["execution_metrics"]["execution_strategy"]
        assert strategy["status"] == "warned"
        assert strategy["strategy"] == "full-width-conservative"
        launched = run.__dict__["launched"]
        if launched:
            Path(launched[0]).unlink(missing_ok=True)


class TestHandOffParity:
    def test_worker_transport_preserves_schema_and_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        tmp_path: Path,
        _widen_sandbox_root: None,
    ) -> None:
        """The parquet produced through the worker matches the in-process core."""
        from haute._execution_admission import create_admitted_execution_context
        from haute._execution_context import ExecutionProfile

        graph = _training_graph(training_parquet)
        run, _calls = _spawn_preparation(monkeypatch, graph)
        launched = run.__dict__["launched"]
        assert launched, run.job.get("message")
        through_worker = pl.scan_parquet(launched[0]).collect()

        body = TrainRequest.model_validate({"graph": graph, "node_id": "train"})
        direct_path = str(tmp_path / "direct.parquet")
        request = TrainingPreparationRequest(
            graph=body.graph,
            node_id="train",
            job_id="direct",
            source="live",
            parquet_path=direct_path,
            config=dict(body.graph.node_map["train"].data.config),
            project_root=str(Path("/").resolve()),
        )
        context = create_admitted_execution_context(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        try:
            outcome = prepare_training_data(request, execution_context=context)
        finally:
            context.release_admission(preserve_primary_error=True)

        assert outcome.failure is None
        in_process = pl.scan_parquet(direct_path).collect()
        assert through_worker.schema == in_process.schema
        assert through_worker.sort(pl.all()).equals(in_process.sort(pl.all()))
        Path(launched[0]).unlink(missing_ok=True)


class TestExecuteAndSinkRequiresAdmission:
    def test_missing_execution_context_is_a_programming_error(
        self,
        training_parquet: str,
    ) -> None:
        store = JobStore()
        service = TrainService(store)
        body = TrainRequest.model_validate(
            {"graph": _training_graph(training_parquet), "node_id": "train"}
        )
        job_id = store.create_job({"status": "running"})

        with pytest.raises(ValueError, match="admitted execution context"):
            service._execute_and_sink(body, None, None, job_id)


def test_preparation_request_and_outcome_pickle_round_trip(training_parquet: str) -> None:
    """Everything crossing the spawn boundary must actually pickle."""
    import pickle

    from haute.execution import AllExceptColumns

    body = TrainRequest.model_validate(
        {"graph": _training_graph(training_parquet), "node_id": "train"}
    )
    request = TrainingPreparationRequest(
        graph=body.graph,
        node_id="train",
        job_id="job",
        source="live",
        parquet_path="out.parquet",
        config={"target": "y"},
        project_root=".",
        required_columns_by_node={
            "train": AllExceptColumns(
                required_columns=frozenset({"y"}),
                excluded_columns=frozenset({"y"}),
            )
        },
    )
    restored = pickle.loads(pickle.dumps(request))
    assert restored.required_columns_by_node == request.required_columns_by_node

    outcome = TrainingPreparationOutcome(
        parquet_path="out.parquet",
        feature_selection={"schema_version": 1},
        execution_metrics={"status": "completed"},
    )
    assert pickle.loads(pickle.dumps(outcome)) == outcome


# ---------------------------------------------------------------------------
# Cleanup failures must be loud (a surviving partial parquet is terminal)
# ---------------------------------------------------------------------------


def _unlink_failing_for(target: Path):
    """Patch ``Path.unlink`` so only *target* raises PermissionError."""
    real_unlink = Path.unlink

    def guarded(self: Path, *args: Any, **kwargs: Any) -> None:
        if Path(self) == target:
            raise PermissionError(f"cannot remove {self}")
        return real_unlink(self, *args, **kwargs)

    return patch.object(Path, "unlink", guarded)


class TestPreparationCleanupFailsLoud:
    def test_child_contract_failure_keeps_original_detail_and_reports_cleanup(
        self,
        tmp_path: Path,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        """An unremovable parquet degrades the outcome without hiding the 422."""
        from haute._execution_admission import create_admitted_execution_context
        from haute._execution_context import ExecutionProfile

        graph = _training_graph(training_parquet)
        body = TrainRequest.model_validate({"graph": graph, "node_id": "train"})
        parquet_path = tmp_path / "prepared.parquet"
        parquet_path.write_bytes(b"partial")

        def fake_execute_lazy(*_args: Any, **_kwargs: Any):
            # The frame lacks the required target column: a 422 contract failure
            # whose detail must survive the cleanup failure.
            return ({"train": pl.DataFrame({"x1": [1.0]}).lazy()}, ["train"], {}, {})

        request = TrainingPreparationRequest(
            graph=body.graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=str(parquet_path),
            config=dict(body.graph.node_map["train"].data.config),
            project_root=str(tmp_path),
            keep_columns=["y"],
        )
        context = create_admitted_execution_context(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        try:
            with (
                patch(
                    "haute.routes._training_preparation.execute_lazy_graph",
                    side_effect=fake_execute_lazy,
                ),
                _unlink_failing_for(parquet_path),
            ):
                outcome = prepare_training_data(request, execution_context=context)
        finally:
            context.release_admission(preserve_primary_error=True)

        failure = outcome.failure
        assert failure is not None
        # Degraded to a terminal error naming the surviving artifact...
        assert failure.terminal_reason == "error"
        assert failure.http_status_code == 500
        assert "could not be removed" in failure.message
        assert failure.fields["cleanup_error"].startswith("cannot remove")
        # ...while the original 422 contract detail is still carried.
        original_detail = str(failure.fields["error_detail"])
        assert "missing required column" in original_detail
        assert "'y'" in original_detail
        assert failure.fields["http_status_code"] == 500
        # The file really is still there — the job no longer claims otherwise.
        assert parquet_path.exists()
        parquet_path.unlink()

    def test_parent_maps_a_failed_removal_to_a_500_error_job(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        """A parent-side removal failure ends the job as a 500 error."""
        holder: list[_PreparationRun] = []

        def worker(request: TrainingPreparationRequest, _budget: Any) -> TrainingPreparationOutcome:
            holder.append(request)  # type: ignore[arg-type]
            return TrainingPreparationOutcome(
                failure=TrainingPreparationFailure(
                    terminal_reason="contract_error",
                    message="child said no",
                    http_status_code=422,
                    http_detail="child said no",
                    fields={"error": "child said no"},
                )
            )

        run = _PreparationRun()
        # The parent's parquet path is only known once the worker is called, so
        # fail every unlink and assert on the path the request carried.
        real_unlink = Path.unlink

        def always_fail(self: Path, *args: Any, **kwargs: Any) -> None:
            if Path(self).name.startswith("haute_train_"):
                raise PermissionError(f"cannot remove {self}")
            return real_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", always_fail):
            run = _drive_preparation(
                monkeypatch,
                _training_graph(training_parquet),
                worker=worker,
                run=run,
            )

        job = run.job
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert job["http_status_code"] == 500
        assert "Training preparation cleanup failed" in job["message"]
        assert run.prepared_admission_releases() == 1
        # The leaked file is real; remove it now that the assertion has run.
        Path(run.parquet_path).unlink(missing_ok=True)

    def test_success_path_is_unaffected_by_the_fail_loud_helper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        """Nothing is removed on the success path, so no cleanup can fail."""
        run, calls = _spawn_preparation(monkeypatch, _training_graph(training_parquet))

        job = run.job
        assert job["status"] == "running", job.get("message")
        launched = run.__dict__["launched"]
        assert launched and Path(launched[0]).exists()
        Path(launched[0]).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The parent's worker-memory payload carries the admitted operation
# ---------------------------------------------------------------------------


class TestDispersionWorkerMemoryOperation:
    def test_dispersion_memory_failure_is_labelled_dispersion_estimate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        """Dispersion admits its own operation and reuses the same supervisor."""
        from fastapi import HTTPException

        from haute.schemas import DispersionEstimateRequest

        store = JobStore()
        service = TrainService(store)
        graph = _glm_dispersion_graph(training_parquet)
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )

        def raising(function, *args, config=None, **kwargs):
            raise IsolatedWorkerMemoryLimitExceededError(
                rss_bytes=_MEMORY_LIMIT_BYTES + 1,
                rss_limit_bytes=_MEMORY_LIMIT_BYTES,
            )

        monkeypatch.setattr(_training_lifecycle, "run_isolated_worker", raising)

        with pytest.raises(HTTPException) as exc_info:
            service.start_dispersion_estimate(body)

        assert exc_info.value.status_code == 507
        detail = exc_info.value.detail
        assert detail["operation"] == "dispersion_estimate"
        assert detail["error_code"] == "memory_limit"
        assert detail["reason"] == "worker_rss_limit_exceeded"

        job_id = next(iter(store._jobs))
        job = store.require_job(job_id)
        assert job["status"] == "memory_limited"
        assert job["terminal_reason"] == "memory_limited"
        assert job["http_status_code"] == 507
        assert job["error_detail"]["operation"] == "dispersion_estimate"


# ---------------------------------------------------------------------------
# Temp-path ownership: setup failures must not leak a parquet
# ---------------------------------------------------------------------------


class TestPreparationTempPathOwnership:
    def test_worker_config_failure_leaves_no_temp_parquet(
        self,
        monkeypatch: pytest.MonkeyPatch,
        training_parquet: str,
        _widen_sandbox_root: None,
    ) -> None:
        """A setup failure before launch ends the job error with nothing left."""
        import tempfile

        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "bogus")
        temp_root = Path(tempfile.gettempdir())
        before = set(temp_root.glob("haute_train_*.parquet"))

        run = _PreparationRun()
        body = TrainRequest.model_validate(
            {"graph": _training_graph(training_parquet), "node_id": "train"}
        )
        config = dict(body.graph.node_map["train"].data.config)
        run.job_id = run.store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "progress": 0.0,
                "message": "Preparing training data...",
                "config": config,
                "node_label": "train",
                "start_time": time.monotonic(),
                "timeout": 600,
            }
        )
        token = ExecutionCancellationToken()
        run.service._training_jobs.register_latest(
            ("training", run.job_id),
            run.job_id,
            execution_token=token,
        )
        launched: list[str] = []
        monkeypatch.setattr(
            TrainService,
            "_launch_background",
            lambda *args, **kwargs: launched.append("yes"),
        )
        run.service._prepare_and_launch_training(run.job_id, body, "train", config, token)

        job = run.job
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert launched == []
        assert set(temp_root.glob("haute_train_*.parquet")) == before
