"""Target/task pairing gate and worker-boundary message contract.

Pins the fix for the motivating low-context error surface: a continuous
target under ``task="classification"`` used to train to the metric stage and
surface sklearn's bare "ValueError: continuous format is not supported" with
no target column, no task, and no call to action. The gate
(`haute.modelling._target_check.training_target_task_issue`) now rejects the
pairing before dispatch with the user-model objects named; the metric stage
wraps any remaining library error with the same context; and the failure
payload's ``user_message`` field carries curated wording verbatim across the
worker boundary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._worker_protocol import (
    WORKER_USER_MESSAGE_FIELD,
    WorkerFailurePayload,
    WorkerRemoteFailureError,
)
from haute.modelling._algorithms import CatBoostAlgorithm
from haute.modelling._target_check import training_target_task_issue
from haute.modelling._training_job import TrainingJob
from haute.routes._background_jobs import IsolatedJobSupervisor
from haute.routes._train_service import TrainService, _worker_failure_payload


class TestTrainingTargetTaskIssue:
    def test_regression_task_never_gates(self) -> None:
        data = pl.LazyFrame({"sev": [1.5, 2.25, 3.75]})
        assert training_target_task_issue(data, target="sev", task="regression") is None

    @pytest.mark.parametrize(
        "values",
        [
            pl.Series("flag", [0, 1, 1, 0], dtype=pl.Int64),
            pl.Series("flag", [True, False, True], dtype=pl.Boolean),
            pl.Series("flag", ["a", "b", "a"], dtype=pl.String),
            pl.Series("flag", ["a", "b", "a"], dtype=pl.Categorical),
            pl.Series("flag", ["a", "b"], dtype=pl.Enum(["a", "b"])),
        ],
    )
    def test_discrete_targets_pass_classification(self, values: pl.Series) -> None:
        data = pl.LazyFrame([values])
        assert training_target_task_issue(data, target="flag", task="classification") is None

    def test_integral_float_flag_passes_classification(self) -> None:
        data = pl.LazyFrame({"flag": [0.0, 1.0, None, 1.0, float("nan")]})
        assert training_target_task_issue(data, target="flag", task="classification") is None

    def test_continuous_float_gates_classification(self) -> None:
        data = pl.LazyFrame({"sev": [123.45, 0.0, 1.0]})
        issue = training_target_task_issue(data, target="sev", task="classification")
        assert issue is not None
        # The message must name the user-model objects and a call to action.
        assert "'sev'" in issue
        assert "classification" in issue
        assert "regression" in issue
        assert "Choose a discrete target column" in issue

    def test_non_classifiable_dtype_gates_classification(self) -> None:
        data = pl.LazyFrame({"when": pl.Series([1, 2]).cast(pl.Date)})
        issue = training_target_task_issue(data, target="when", task="classification")
        assert issue is not None
        assert "'when'" in issue
        assert "regression" in issue

    def test_missing_target_column_is_not_this_gates_job(self) -> None:
        data = pl.LazyFrame({"other": [1.5]})
        assert training_target_task_issue(data, target="sev", task="classification") is None

    def test_custom_collector_is_used_for_the_fractional_scan(self) -> None:
        data = pl.LazyFrame({"sev": [123.45]})
        collected: list[pl.LazyFrame] = []

        def collect(lf: pl.LazyFrame) -> pl.DataFrame:
            collected.append(lf)
            return lf.collect()

        issue = training_target_task_issue(
            data, target="sev", task="classification", collect=collect
        )
        assert issue is not None
        assert len(collected) == 1


class TestTrainingJobGate:
    def test_prepare_data_gates_continuous_target_under_classification(
        self, tmp_path: Path
    ) -> None:
        data = pl.DataFrame(
            {
                "x1": [0.1, 0.2, 0.3, 0.4],
                "sev": [123.45, 67.8, 9.1, 2.5],
            }
        )
        job = TrainingJob(
            name="gate_model",
            data=data,
            target="sev",
            task="classification",
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="continuous values") as excinfo:
            job.run()
        message = str(excinfo.value)
        assert "'sev'" in message
        assert "set the task to regression" in message


class TestMetricStageContext:
    def test_metric_failure_names_target_task_and_metrics(self, tmp_path: Path) -> None:
        """A metric/task mismatch the gate cannot catch still surfaces with context.

        Regression task passes the target gate, but AUC over a continuous
        target raises inside sklearn; the wrap must name the evaluation set,
        target, task, and metric list around the library error.
        """
        rng = np.random.RandomState(42)
        n = 60
        x1 = rng.randn(n)
        data = pl.DataFrame(
            {
                "x1": x1,
                "x2": rng.randn(n),
                "sev": (x1 + rng.randn(n) * 0.5) + 5.0,
            }
        )
        with (
            patch.object(CatBoostAlgorithm, "shap_summary", return_value=[]),
            patch.object(CatBoostAlgorithm, "feature_importance_typed", return_value=[]),
            patch("haute.modelling._metrics.compute_pdp", return_value=[]),
        ):
            job = TrainingJob(
                name="metric_wrap_model",
                data=data,
                target="sev",
                task="regression",
                loss_function="RMSE",
                metrics=["auc"],
                params={"iterations": 4, "depth": 2},
                output_dir=str(tmp_path),
            )
            with pytest.raises(ValueError) as excinfo:
                job.run()
        message = str(excinfo.value)
        assert "Could not evaluate the trained model" in message
        assert "'sev'" in message
        assert "'regression'" in message
        assert "auc" in message
        assert "continuous" in message  # the chained sklearn detail survives


class TestPreDispatchServiceGate:
    def test_validate_target_task_pairing_rejects_and_removes_parquet(self, tmp_path: Path) -> None:
        tmp_parquet = tmp_path / "train_input.parquet"
        pl.DataFrame({"x1": [0.1, 0.2], "sev": [123.45, 6.7]}).write_parquet(tmp_parquet)
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        with pytest.raises(HTTPException) as excinfo:
            TrainService._validate_target_task_pairing(
                str(tmp_parquet),
                {"target": "sev", "task": "classification"},
                execution_context=context,
            )
        assert excinfo.value.status_code == 422
        assert "'sev'" in str(excinfo.value.detail)
        assert "classification" in str(excinfo.value.detail)
        assert not tmp_parquet.exists()

    def test_validate_target_task_pairing_passes_regression(self, tmp_path: Path) -> None:
        tmp_parquet = tmp_path / "train_input.parquet"
        pl.DataFrame({"x1": [0.1, 0.2], "sev": [123.45, 6.7]}).write_parquet(tmp_parquet)
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        TrainService._validate_target_task_pairing(
            str(tmp_parquet),
            {"target": "sev", "task": "regression"},
            execution_context=context,
        )
        assert tmp_parquet.exists()


class TestWorkerBoundaryUserMessage:
    def test_failure_payload_marks_curated_message_user_facing(self) -> None:
        payload = _worker_failure_payload(ValueError("boom"), terminal_reason="contract_error")
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == "boom"

    def test_failure_payload_keeps_explicit_fields_and_adds_marker(self) -> None:
        payload = _worker_failure_payload(
            MemoryError("out of memory"),
            terminal_reason="memory_limited",
            fields={"error": "out of memory", "error_code": "memory_limit"},
        )
        assert payload.fields["error_code"] == "memory_limit"
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == "out of memory"

    def test_supervisor_surfaces_curated_message_without_wrapper(self) -> None:
        curated = (
            "Target column 'sev' contains continuous values, but the training task "
            "is classification. Choose a discrete target column, or set the task to "
            "regression."
        )
        payload = WorkerFailurePayload(
            terminal_reason="contract_error",
            error_type="ValueError",
            message=curated,
            traceback="ValueError: continuous format is not supported",
            fields={"error": curated, WORKER_USER_MESSAGE_FIELD: curated},
        )

        def execute() -> None:
            raise WorkerRemoteFailureError(payload)

        outcome = IsolatedJobSupervisor._produce_outcome(
            execute,
            completed_fields=lambda result: {"result": result},
            completed_message="Completed",
        )
        assert outcome.terminal_reason == "contract_error"
        assert outcome.message == curated
        assert "Isolated worker raised" not in outcome.message
        # The typed wrapper text stays available for diagnostics.
        assert outcome.fields["error"].startswith("Isolated worker raised ValueError:")

    def test_supervisor_keeps_wrapper_for_uncurated_failures(self) -> None:
        payload = WorkerFailurePayload(
            terminal_reason="error",
            error_type="KeyError",
            message="'frame'",
            traceback="KeyError: 'frame'",
            fields={},
        )

        def execute() -> None:
            raise WorkerRemoteFailureError(payload)

        outcome = IsolatedJobSupervisor._produce_outcome(
            execute,
            completed_fields=lambda result: {"result": result},
            completed_message="Completed",
        )
        assert outcome.message == "Isolated worker raised KeyError: 'frame'"
