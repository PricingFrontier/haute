from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

import haute.modelling._training_job as training_job
from haute._execution_context import (
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute.modelling._split import PARTITION_TRAIN, PARTITION_VALIDATION
from haute.modelling._training_job import TrainingJob, TrainResult, _PreparedData
from haute.routes._job_store import JobStore
from haute.routes._train_service import TrainService
from tests.test_training_worker_protocol import (
    _inline_protocol_runner,
    _SuccessfulTrainingJob,
)


def _context() -> ExecutionContext:
    return ExecutionContext(
        operation="training_job",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 1_000,
    )


def _prepared(data_path: str, row_count: int) -> _PreparedData:
    return _PreparedData(
        data_path=data_path,
        owns_tmp=False,
        features=["feature"],
        cat_features=[],
        total_rows=row_count,
        feature_dtypes={"feature": "Float64"},
        target_dtype="Float64",
    )


def test_prepare_data_null_target_count_uses_streaming_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = tmp_path / "training.parquet"
    pl.DataFrame(
        {
            "feature": [1.0, 2.0, 3.0],
            "target": [1.0, None, 0.0],
        }
    ).write_parquet(data_path)
    context = _context()
    calls: list[dict[str, Any]] = []
    original = training_job.streaming_collect

    def recording_streaming_collect(
        lf: pl.LazyFrame,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> pl.DataFrame:
        calls.append(
            {
                "columns": lf.collect_schema().names(),
                "execution_context": execution_context,
            }
        )
        return original(lf, execution_context=execution_context)

    monkeypatch.setattr(training_job, "streaming_collect", recording_streaming_collect)

    prepared = TrainingJob(
        name="null_target_count",
        data=str(data_path),
        target="target",
    )._prepare_data(lambda _msg, _frac: None, execution_context=context)

    assert prepared.total_rows == 2
    assert prepared.target_null_count == 1
    assert {
        "columns": ["target"],
        "execution_context": context,
    } in calls
    assert "training_target_null_count" in context.metrics_summary().stage_elapsed_ms


def test_group_split_mask_uses_streaming_collect_for_split_column_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = tmp_path / "training.parquet"
    pl.DataFrame(
        {
            "feature": [float(i) for i in range(8)],
            "target": [float(i % 2) for i in range(8)],
            "group": [f"g{i // 2}" for i in range(8)],
            "unused_wide": [f"payload-{i}" for i in range(8)],
        }
    ).write_parquet(data_path)
    context = _context()
    calls: list[list[str]] = []
    original = training_job.streaming_collect

    def recording_streaming_collect(
        lf: pl.LazyFrame,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> pl.DataFrame:
        calls.append(lf.collect_schema().names())
        return original(lf, execution_context=execution_context)

    monkeypatch.setattr(training_job, "streaming_collect", recording_streaming_collect)

    split = TrainingJob(
        name="group_split",
        data=str(data_path),
        target="target",
        split={
            "strategy": "group",
            "group_column": "group",
            "validation_size": 0.25,
            "holdout_size": 0.0,
            "seed": 1,
        },
    )._split_data(
        _prepared(str(data_path), 8),
        lambda _msg, _frac: None,
        execution_context=context,
    )
    try:
        assert calls == [["group"]]
        assert "training_split_key_collect" in context.metrics_summary().stage_elapsed_ms
    finally:
        os.unlink(split.split_path)


def test_partition_reads_use_streaming_collect_and_preserve_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split_path = tmp_path / "split.parquet"
    pl.DataFrame(
        {
            "feature": [1.0, 2.0, 3.0, 4.0],
            "target": [1.0, 0.0, 1.0, 0.0],
            "weight": [1.0, 1.1, 1.2, 1.3],
            "unused_wide": ["a", "b", "c", "d"],
            "_partition": [
                PARTITION_TRAIN,
                PARTITION_VALIDATION,
                PARTITION_TRAIN,
                PARTITION_VALIDATION,
            ],
        }
    ).write_parquet(split_path)
    context = _context()
    calls: list[list[str]] = []
    original = training_job.streaming_collect

    def recording_streaming_collect(
        lf: pl.LazyFrame,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> pl.DataFrame:
        calls.append(lf.collect_schema().names())
        return original(lf, execution_context=execution_context)

    monkeypatch.setattr(training_job, "streaming_collect", recording_streaming_collect)

    df = TrainingJob(
        name="partition_projection",
        data=str(split_path),
        target="target",
    )._read_partition(
        str(split_path),
        PARTITION_TRAIN,
        columns=["feature", "target", "weight"],
        execution_context=context,
        stage_name="training_partition_materialise",
    )

    assert df.columns == ["feature", "target", "weight"]
    assert calls == [["feature", "target", "weight"]]
    assert "unused_wide" not in calls[0]
    assert "training_partition_materialise" in context.metrics_summary().stage_elapsed_ms


def _admitted_training_context(
    job_id: str | None = None,
) -> tuple[ExecutionContext, dict[str, int]]:
    """Build an admitted-like context with a real memory_limit so checkpoints fire."""
    admission_calls: dict[str, int] = {"release": 0}

    def _release() -> None:
        admission_calls["release"] += 1

    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_limit_bytes=1_000,
        memory_baseline_bytes=500,
        rss_limit_bytes=1_500,
        memory_sampler=lambda: 600,
        admission_release=_release,
    )
    return context, admission_calls


def _running_training_job(store: JobStore) -> str:
    return store.create_job(
        {
            "status": "running",
            "progress": 0.0,
            "message": "Starting",
            "job_type": "training",
            "start_time": time.monotonic(),
            "timeout": 60,
        }
    )


def test_training_background_memory_limit_sets_typed_terminal_status(
    tmp_path: Path,
) -> None:
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    job_id = _running_training_job(store)
    tmp_parquet = tmp_path / "training.parquet"
    tmp_parquet.write_bytes(b"placeholder")

    class MemoryLimitedTrainingJob:
        def __init__(self, *args, **kwargs):
            pass

        def run(
            self, progress, on_iteration, check_cancelled=None, execution_context=None, **_kwargs
        ):
            assert execution_context is not None
            raise ExecutionMemoryLimitExceededError(
                "training_job",
                rss_bytes=2_000,
                limit_bytes=1_000,
                baseline_rss_bytes=500,
                rss_limit_bytes=1_500,
            )

    admitted, _ = _admitted_training_context(job_id)
    with patch("haute.modelling.TrainingJob", MemoryLimitedTrainingJob):
        thread = service._launch_background(
            job_id,
            "train",
            {
                "target": "target",
                "loss_function": "RMSE",
                "output_dir": str(tmp_path / "outputs"),
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
            },
            {},
            str(tmp_parquet),
            None,
            None,
            execution_context=admitted,
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["error_detail"]["operation"] == "training_job"
    assert job["http_status_code"] == 507
    assert job["execution_metrics"]["terminal_reason"] == "memory_limited"
    assert not tmp_parquet.exists()


def test_launch_background_reconstructs_child_context_from_admitted_budget(
    tmp_path: Path,
) -> None:
    """The spawn request carries limits, never the parent context object."""
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    job_id = _running_training_job(store)
    tmp_parquet = tmp_path / "training.parquet"
    tmp_parquet.write_bytes(b"placeholder")
    captured: dict[str, Any] = {}

    class _TrainingJob(_SuccessfulTrainingJob):
        def run(
            self, progress, on_iteration, check_cancelled=None, execution_context=None, **_kwargs
        ):
            captured["execution_context"] = execution_context
            return super().run(
                progress,
                on_iteration,
                check_cancelled=check_cancelled,
                execution_context=execution_context,
                **_kwargs,
            )

    admitted, _ = _admitted_training_context(job_id)
    with patch("haute.modelling.TrainingJob", _TrainingJob):
        thread = service._launch_background(
            job_id,
            "train",
            {
                "name": "model",
                "target": "target",
                "loss_function": "RMSE",
                "output_dir": str(tmp_path / "outputs"),
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
            },
            {},
            str(tmp_parquet),
            None,
            None,
            execution_context=admitted,
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)

    ctx = captured["execution_context"]
    assert ctx is not admitted
    assert ctx.memory_limit_bytes == 1_000
    assert ctx.admission is None
    assert ctx.admission_release is None


def test_launch_background_releases_admission_after_thread_completes(
    tmp_path: Path,
) -> None:
    """Regression for bug_003: admission must be released by the background
    thread's finally, not by the synchronous prep handler, so the in-flight
    reservation stays held while training runs.
    """
    started = threading.Event()
    allow_finish = threading.Event()

    def blocking_protocol_runner(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert allow_finish.wait(timeout=10)
        return _inline_protocol_runner(*args, **kwargs)

    store = JobStore()
    service = TrainService(store, protocol_runner=blocking_protocol_runner)
    job_id = _running_training_job(store)
    tmp_parquet = tmp_path / "training.parquet"
    tmp_parquet.write_bytes(b"placeholder")

    admitted, admission_calls = _admitted_training_context(job_id)
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        thread = service._launch_background(
            job_id,
            "train",
            {
                "name": "model",
                "target": "target",
                "loss_function": "RMSE",
                "output_dir": str(tmp_path / "outputs"),
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
            },
            {},
            str(tmp_parquet),
            None,
            None,
            execution_context=admitted,
        )

        assert thread is not None
        try:
            assert started.wait(timeout=10)
            # While background runs: admission is still held by the worker.
            assert admission_calls["release"] == 0
        finally:
            allow_finish.set()

        # Background completes -> admission released exactly once.
        thread.join_and_raise(timeout=10)

    assert admission_calls["release"] == 1


def test_launch_background_releases_admission_on_thread_start_failure(
    tmp_path: Path,
) -> None:
    """If we cannot start the worker thread, admission must still be released."""
    from fastapi import HTTPException

    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    job_id = _running_training_job(store)
    tmp_parquet = tmp_path / "training.parquet"
    tmp_parquet.write_bytes(b"placeholder")

    admitted, admission_calls = _admitted_training_context(job_id)
    with (
        patch(
            "haute.routes._background_jobs.IsolatedSupervisorThread.start",
            side_effect=RuntimeError("thread spawn refused"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        service._launch_background(
            job_id,
            "train",
            {
                "target": "target",
                "loss_function": "RMSE",
                "output_dir": str(tmp_path / "outputs"),
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
            },
            {},
            str(tmp_parquet),
            None,
            None,
            execution_context=admitted,
        )

    assert exc_info.value.status_code == 500
    assert admission_calls["release"] == 1


def test_start_releases_admission_when_prep_fails_before_launch(
    tmp_path: Path,
) -> None:
    """When prep raises before launch, the admitted context must be released."""
    from haute.schemas import TrainRequest
    from tests.test_modelling_routes import _make_modelling_graph

    data_path = tmp_path / "training.parquet"
    pl.DataFrame({"x": [1.0, 2.0], "y": [0.0, 1.0]}).write_parquet(data_path)
    store = JobStore()
    service = TrainService(store)

    captured_contexts: list[ExecutionContext] = []
    original = __import__(
        "haute._execution_admission", fromlist=["create_admitted_execution_context"]
    ).create_admitted_execution_context

    def recording_create(*args, **kwargs):
        ctx = original(*args, **kwargs)
        captured_contexts.append(ctx)
        return ctx

    with (
        patch(
            "haute.routes._train_service.create_admitted_execution_context",
            side_effect=recording_create,
        ),
        patch.object(service, "_execute_and_sink", side_effect=RuntimeError("prep boom")),
    ):
        response = service.start(
            TrainRequest(graph=_make_modelling_graph(str(data_path)), node_id="train")
        )
        service._join_preparation(response.job_id)

    assert captured_contexts, "admitted context should have been created"
    assert captured_contexts[0]._admission_released is True
    job = store.require_job(response.job_id)
    assert job["status"] == "error"
    assert "prep boom" in job["error"]


def test_start_keeps_admission_held_after_successful_launch(
    tmp_path: Path,
) -> None:
    """Regression for bug_003: on success, start() must NOT release admission
    in its finally — ownership transfers to the background worker.
    """
    from haute.schemas import TrainRequest
    from tests.test_modelling_routes import _make_modelling_graph

    data_path = tmp_path / "training.parquet"
    pl.DataFrame({"x": [1.0, 2.0], "y": [0.0, 1.0]}).write_parquet(data_path)
    store = JobStore()
    service = TrainService(store)

    captured: dict[str, Any] = {}
    original = __import__(
        "haute._execution_admission", fromlist=["create_admitted_execution_context"]
    ).create_admitted_execution_context

    def recording_create(*args, **kwargs):
        ctx = original(*args, **kwargs)
        captured["context"] = ctx
        return ctx

    fake_tmp = tmp_path / "prep.parquet"
    fake_tmp.write_bytes(b"placeholder")

    def capture_launch(*_args, **kwargs):
        captured["on_finished"] = kwargs["on_finished"]
        return MagicMock()

    with (
        patch(
            "haute.routes._train_service.create_admitted_execution_context",
            side_effect=recording_create,
        ),
        patch.object(service, "_execute_and_sink", return_value=str(fake_tmp)),
        patch.object(service._supervisor, "launch_protocol", side_effect=capture_launch),
    ):
        response = service.start(
            TrainRequest(graph=_make_modelling_graph(str(data_path)), node_id="train"),
        )
        service._join_preparation(response.job_id)

    assert response.status == "started"
    admitted = captured["context"]
    assert admitted._admission_released is False, (
        "admission must remain held after start() returns; "
        "ownership belongs to the background worker"
    )
    # The parent retains the admitted context while its plain budget fields are
    # sent in the worker request.
    assert admitted.memory_limit_bytes is not None
    assert admitted.admission is not None
    captured["on_finished"]()
    assert admitted._admission_released is True


def test_catboost_gpu_vram_limit_refuses_before_launch(
    client,
    haute_scratch: Path,
) -> None:
    from tests.test_modelling_routes import (
        _fast_training_params,
        _make_modelling_graph,
        _poll_until_done,
    )

    data_path = haute_scratch / "training.parquet"
    pl.DataFrame(
        {
            "x1": [float(i) for i in range(20)],
            "x2": [float(i % 3) for i in range(20)],
            "y": [float(i % 2) for i in range(20)],
        }
    ).write_parquet(data_path)
    graph = _make_modelling_graph(
        str(data_path),
        params=_fast_training_params(task_type="GPU"),
    )

    with (
        patch("haute._host_memory.available_vram_bytes", return_value=1),
        patch("haute.modelling.TrainingJob.run", return_value=SimpleNamespace()) as run,
    ):
        resp = client.post(
            "/api/modelling/train",
            json={"graph": graph, "node_id": "train"},
        )
        assert resp.status_code == 200
        detail = _poll_until_done(client, resp.json()["job_id"])

    assert detail["status"] == "memory_limited"
    assert detail["http_status_code"] == 507
    assert detail["error_code"] == "gpu_vram_limit"
    assert "GPU training needs" in detail["error_detail"]["message"]
    assert "Select CPU and retry" in detail["error_detail"]["message"]
    run.assert_not_called()


def test_training_memory_estimate_failure_refuses_before_pipeline_execution(
    tmp_path: Path,
) -> None:
    from haute.schemas import TrainRequest
    from tests.test_modelling_routes import _make_modelling_graph

    data_path = tmp_path / "training.parquet"
    pl.DataFrame({"x": [1.0, 2.0], "y": [0.0, 1.0]}).write_parquet(data_path)
    store = JobStore()
    service = TrainService(store)

    with (
        patch("haute._ram_estimate.estimate_safe_training_rows", side_effect=RuntimeError("boom")),
        patch.object(service, "_execute_and_sink") as execute_and_sink,
    ):
        response = service.start(
            TrainRequest(graph=_make_modelling_graph(str(data_path)), node_id="train")
        )
        service._join_preparation(response.job_id)

    execute_and_sink.assert_not_called()
    job = store.require_job(response.job_id)
    assert job["status"] == "contract_error"
    assert job["terminal_reason"] == "contract_error"
    assert job["http_status_code"] == 422
    assert job["error_code"] == "training_memory_estimate_failed"


def test_training_mlflow_log_receives_cancellation_checkpoint(tmp_path: Path) -> None:
    job = TrainingJob(
        name="mlflow_cancel",
        data=pl.DataFrame({"y": [1.0]}),
        target="y",
        mlflow_experiment="/experiments/cancel",
        output_dir=str(tmp_path),
    )
    result = TrainResult(
        metrics={"rmse": 0.1},
        feature_importance=[],
        model_path=str(tmp_path / "model.cbm"),
        train_rows=1,
        validation_rows=0,
        features=["x"],
        cat_features=[],
    )

    def check_cancelled() -> None:
        return None

    with patch("haute.modelling._mlflow_log.log_experiment") as log:
        job._log_to_mlflow(result, check_cancelled=check_cancelled)

    assert log.call_args.kwargs["check_cancelled"] is check_cancelled
