from __future__ import annotations

import gc
import os
import threading
import time
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

import haute.modelling._training_job as training_job
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute.modelling._split import PARTITION_TRAIN, PARTITION_VALIDATION
from haute.modelling._training_job import TrainingJob, TrainResult, _PreparedData, _SplitResult
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


def test_diagnostic_predictions_are_bounded_and_preserve_multidimensional_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = TrainingJob(name="diagnostic_batches", data=pl.DataFrame({"x": [1]}), target="y")
    frame = pl.DataFrame({"x": [1, 2, 3, 4, 5]})
    batches: list[int] = []

    def bounded(lf, *, chunk_size, **_kwargs):
        assert chunk_size == 65_536
        for offset in range(0, 5, 2):
            yield lf.slice(offset, 2).collect()

    class Algo:
        def predict(self, _model, batch, _features, *, offset=None):
            batches.append(len(batch))
            return np.column_stack((batch["x"].to_numpy(), batch["x"].to_numpy() * 10))

    monkeypatch.setattr(training_job, "bounded_collect_batches", bounded)
    result = job._predict_diagnostic_batches(Algo(), object(), frame, ["x"], execution_context=None)
    assert batches == [2, 2, 1]
    assert result.tolist() == [[1, 10], [2, 20], [3, 30], [4, 40], [5, 50]]


def test_diagnostic_predictions_keep_empty_input_algorithm_behavior() -> None:
    job = TrainingJob(name="diagnostic_empty", data=pl.DataFrame({"x": [1]}), target="y")

    class Algo:
        def predict(self, _model, frame, _features, *, offset=None):
            assert frame.height == 0
            return np.asarray([], dtype=np.float32)

    result = job._predict_diagnostic_batches(
        Algo(),
        object(),
        pl.DataFrame({"x": pl.Series([], dtype=pl.Int64)}),
        ["x"],
        execution_context=None,
    )
    assert result.dtype == np.float32


def test_diagnostic_prediction_cancellation_stops_before_second_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = TrainingJob(name="diagnostic_cancel", data=pl.DataFrame({"x": [1]}), target="y")
    closed = False

    def batches(*_args, **_kwargs):
        nonlocal closed
        try:
            yield pl.DataFrame({"x": [1]})
            raise ExecutionCancelledError("cancelled")
        finally:
            closed = True

    class Algo:
        calls = 0

        def predict(self, _model, _frame, _features, *, offset=None):
            self.calls += 1
            return np.asarray([1.0])

    algo = Algo()
    monkeypatch.setattr(training_job, "bounded_collect_batches", batches)
    with pytest.raises(ExecutionCancelledError):
        job._predict_diagnostic_batches(
            algo, object(), pl.DataFrame({"x": [1, 2]}), ["x"], execution_context=_context()
        )
    assert algo.calls == 1
    assert closed


def test_catboost_releases_raw_training_allocations_before_loading_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split_path = tmp_path / "split.parquet"
    pl.DataFrame(
        {
            "feature": [1.0, 2.0, 3.0, 4.0],
            "target": [1.0, 0.0, 1.0, 0.0],
            "weight": [0.5, 0.6, 0.7, 0.8],
            "offset": [0.1, 0.2, 0.3, 0.4],
            "_partition": [
                PARTITION_TRAIN,
                PARTITION_VALIDATION,
                PARTITION_TRAIN,
                PARTITION_VALIDATION,
            ],
        }
    ).write_parquet(split_path)

    class FakeCatBoostAlgorithm:
        def fit(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(model=object(), best_iteration=None, loss_history=[])

    raw_refs: dict[str, weakref.ReferenceType[Any]] = {}
    pool_values: list[dict[str, list[float] | None]] = []
    pools_built = 0
    original_collect = training_job._training_streaming_collect

    def recording_collect(
        lf: pl.LazyFrame,
        *,
        stage_name: str,
        execution_context: ExecutionContext | None = None,
    ) -> pl.DataFrame:
        nonlocal pools_built
        if stage_name == "training_validation_partition_materialise":
            gc.collect()
            assert pools_built == 1
            assert all(reference() is None for reference in raw_refs.values())
        frame = original_collect(lf, stage_name=stage_name, execution_context=execution_context)
        if stage_name == "training_train_partition_materialise":
            raw_refs["train_df"] = weakref.ref(frame)
        return frame

    def fake_build_pool(
        frame: pl.DataFrame,
        _features: list[str],
        _cat_features: list[str],
        *,
        y: Any = None,
        w: Any = None,
        baseline: Any = None,
    ) -> object:
        nonlocal pools_built
        pool_values.append(
            {
                "y": None if y is None else y.tolist(),
                "w": None if w is None else w.tolist(),
                "baseline": None if baseline is None else baseline.tolist(),
            }
        )
        if pools_built == 0:
            raw_refs["features"] = weakref.ref(frame)
            raw_refs["y"] = weakref.ref(y)
            raw_refs["w"] = weakref.ref(w)
            raw_refs["baseline"] = weakref.ref(baseline)
        pools_built += 1
        return object()

    monkeypatch.setitem(training_job.ALGORITHM_REGISTRY, "catboost", FakeCatBoostAlgorithm)
    monkeypatch.setattr(training_job, "_training_streaming_collect", recording_collect)
    monkeypatch.setattr("haute.modelling._algorithms._build_pool", fake_build_pool)

    job = TrainingJob(
        name="allocation_order",
        data=str(split_path),
        target="target",
        weight="weight",
        offset="offset",
        algorithm="catboost",
        task="regression",
    )
    job._train_model(
        _SplitResult(str(split_path), False, n_train=2, n_validation=2, n_holdout=0),
        ["feature"],
        [],
        None,
        lambda _message, _progress: None,
    )

    assert pool_values == [
        {"y": [1.0, 1.0], "w": [0.5, 0.7], "baseline": [0.1, 0.3]},
        {"y": [0.0, 0.0], "w": [0.6, 0.8], "baseline": [0.2, 0.4]},
    ]


def test_glm_keeps_training_and_validation_frames_for_its_fit_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split_path = tmp_path / "glm-split.parquet"
    pl.DataFrame(
        {
            "feature": [1.0, 2.0],
            "target": [1.0, 0.0],
            "_partition": [PARTITION_TRAIN, PARTITION_VALIDATION],
        }
    ).write_parquet(split_path)
    received: dict[str, pl.DataFrame] = {}

    class FakeGlmAlgorithm:
        def fit(
            self,
            train: pl.DataFrame,
            *_args: Any,
            eval_df: pl.DataFrame | None,
            **_kwargs: Any,
        ) -> SimpleNamespace:
            received["train"] = train
            assert eval_df is not None
            received["validation"] = eval_df
            return SimpleNamespace(model=object(), best_iteration=None, loss_history=[])

    monkeypatch.setitem(training_job.ALGORITHM_REGISTRY, "glm", FakeGlmAlgorithm)
    TrainingJob(
        name="glm_frames",
        data=str(split_path),
        target="target",
        algorithm="glm",
        params={"family": "gaussian", "terms": {"feature": {"type": "linear"}}},
    )._train_model(
        _SplitResult(str(split_path), False, n_train=1, n_validation=1, n_holdout=0),
        ["feature"],
        [],
        None,
        lambda _message, _progress: None,
    )

    assert received["train"]["target"].to_list() == [1.0]
    assert received["validation"]["target"].to_list() == [0.0]


def test_catboost_without_validation_does_not_read_validation_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split_path = tmp_path / "no-validation-split.parquet"
    pl.DataFrame(
        {"feature": [1.0], "target": [1.0], "_partition": [PARTITION_TRAIN]}
    ).write_parquet(split_path)
    stages: list[str] = []
    original_collect = training_job._training_streaming_collect

    class FakeCatBoostAlgorithm:
        def fit(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(model=object(), best_iteration=None, loss_history=[])

    def recording_collect(
        lf: pl.LazyFrame,
        *,
        stage_name: str,
        execution_context: ExecutionContext | None = None,
    ) -> pl.DataFrame:
        stages.append(stage_name)
        return original_collect(lf, stage_name=stage_name, execution_context=execution_context)

    monkeypatch.setitem(training_job.ALGORITHM_REGISTRY, "catboost", FakeCatBoostAlgorithm)
    monkeypatch.setattr(training_job, "_training_streaming_collect", recording_collect)
    monkeypatch.setattr(
        "haute.modelling._algorithms._build_pool", lambda *_args, **_kwargs: object()
    )
    TrainingJob(
        name="no_validation",
        data=str(split_path),
        target="target",
        algorithm="catboost",
        task="regression",
    )._train_model(
        _SplitResult(str(split_path), False, n_train=1, n_validation=0, n_holdout=0),
        ["feature"],
        [],
        None,
        lambda _message, _progress: None,
    )

    assert stages == ["training_train_partition_materialise"]


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


# The graph names a real parquet under ``tmp_path``; the estimate index resolves
# it through the executor's project-contained resolver before admission runs.
@pytest.mark.usefixtures("_widen_sandbox_root")
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
            "haute.routes._training_lifecycle.create_admitted_execution_context",
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


# The graph names a real parquet under ``tmp_path``; the estimate index resolves
# it through the executor's project-contained resolver before admission runs.
@pytest.mark.usefixtures("_widen_sandbox_root")
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
            "haute.routes._training_lifecycle.create_admitted_execution_context",
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
        evaluation={
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "single", "size": 0.2},
        },
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
        evaluation={
            "plan_path": str(tmp_path / "plan.json"),
            "results_path": str(tmp_path / "results.json"),
            "report_path": str(tmp_path / "report.json"),
            "plan_sha256": "b" * 64,
            "selection_metrics": {},
        },
    )
    job.evaluation = SimpleNamespace(
        strategy="random", validation={"method": "single"}, to_plain_data=lambda: {}
    )

    def check_cancelled() -> None:
        return None

    with (
        patch("haute.modelling._candidate_run.capture_provenance"),
        patch("haute.modelling._candidate_run.build_candidate_run"),
        patch("haute.modelling._mlflow_log.log_experiment") as log,
    ):
        job._log_to_mlflow(result, check_cancelled=check_cancelled)

    assert log.call_args.kwargs["check_cancelled"] is check_cancelled


def test_validation_metric_allocations_are_released_before_holdout(monkeypatch):
    from haute.modelling import _metrics
    from haute.modelling._split import PARTITION_HOLDOUT

    job = TrainingJob(
        name="diagnostic_lifetimes",
        data=pl.DataFrame({"x": [1]}),
        target="y",
        weight="w",
        offset="o",
        metrics=["rmse"],
    )
    refs = []
    order = []

    def read(_self, _path, partition, **_kwargs):
        if partition == PARTITION_HOLDOUT:
            gc.collect()
            assert len(refs) == 4
            assert all(reference() is None for reference in refs)
        order.append(partition)
        frame = pl.DataFrame(
            {
                "x": [10.0, 20.0],
                "y": [11.0, 21.0] if partition == PARTITION_VALIDATION else [12.0, 22.0],
                "w": [2.0, 3.0],
                "o": [1.0, 1.0],
            }
        )
        if partition == PARTITION_VALIDATION:
            refs.append(weakref.ref(frame))
        return frame

    real_metrics = training_job.compute_metrics

    def metrics(y, prediction, weights, *args, **kwargs):
        assert weights.tolist() == [2.0, 3.0]
        if order == [PARTITION_VALIDATION]:
            refs.extend(weakref.ref(value) for value in (y, prediction, weights))
        return real_metrics(y, prediction, weights, *args, **kwargs)

    class Algo:
        def feature_importance(self, _model):
            return [{"feature": "x", "importance": 1.0}]

        def predict(self, _model, frame, features, *, offset):
            assert features == ["x"] and offset == "o"
            return (frame["x"] + frame["o"]).to_numpy()

    monkeypatch.setattr(TrainingJob, "_read_partition", read)
    monkeypatch.setattr(training_job, "compute_metrics", metrics)
    monkeypatch.setattr(_metrics, "compute_pdp", lambda *_a, **_k: [])
    result = job._compute_metrics(
        _SplitResult("unused.parquet", False, n_train=1, n_validation=2, n_holdout=2),
        ["x"],
        [],
        SimpleNamespace(model=object(), algo=Algo(), fit_params={}),
        lambda *_args: None,
    )
    assert order == [PARTITION_VALIDATION, PARTITION_HOLDOUT]
    assert result.diagnostics_set == "holdout"
    assert result.metrics["rmse"] == 0.0
    assert result.holdout_metrics["rmse"] == 1.0


def test_diagnostic_prediction_wrong_row_count_closes_reader(monkeypatch):
    closed = []

    def batches(*_args, **_kwargs):
        try:
            yield pl.DataFrame({"x": [1, 2]})
        finally:
            closed.append(True)

    monkeypatch.setattr(training_job, "bounded_collect_batches", batches)
    job = TrainingJob(name="bad_predictions", data=pl.DataFrame({"x": [1]}), target="y")
    algo = SimpleNamespace(predict=lambda *_a, **_k: np.array([1.0]))
    with pytest.raises(ValueError, match="one row per input"):
        job._predict_diagnostic_batches(
            algo, object(), pl.DataFrame({"x": [1, 2]}), ["x"], execution_context=None
        )
    assert closed == [True]
