"""Target/task/metric pairing gate and worker-boundary message contract.

Pins the fix for the motivating low-context error surface: a continuous
target under ``task="classification"`` — or under objective-implied AUC/log
loss defaults with ``task="regression"`` (e.g. a binomial family) — used to
train to the metric stage and surface sklearn's bare "ValueError: continuous
format is not supported" with no target column, no task, and no call to
action. The gate (`haute.modelling._target_check.training_target_task_issue`)
now keys on the EFFECTIVE metric set and rejects the pairing before dispatch
with the user-model objects named; the metric stage wraps any remaining
library error with the same context; and the failure payload's
``user_message`` field carries curated wording verbatim across the worker
boundary.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._worker_isolation import IsolatedWorkerCrashedError, IsolatedWorkerTimeoutError
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

if TYPE_CHECKING:
    from collections.abc import Callable

_CURATED_ROUND_TRIP_MESSAGE = (
    "Target column 'sev' contains continuous values, but the training task is "
    "classification. Choose a discrete target column, or set the task to regression."
)


def _curated_failure_worker(runtime: object, request: object) -> WorkerFailurePayload:
    """Spawn-picklable worker returning a curated failure payload."""
    del runtime, request
    return WorkerFailurePayload(
        terminal_reason="contract_error",
        error_type="ValueError",
        message=_CURATED_ROUND_TRIP_MESSAGE,
        traceback="ValueError: continuous format is not supported",
        fields={
            "error": _CURATED_ROUND_TRIP_MESSAGE,
            WORKER_USER_MESSAGE_FIELD: _CURATED_ROUND_TRIP_MESSAGE,
        },
    )


_CLASSIFICATION_DEFAULT_METRICS = ["auc", "logloss"]
_REGRESSION_DEFAULT_METRICS = ["gini", "rmse"]


class TestTrainingTargetTaskIssue:
    def test_regression_task_with_regression_metrics_never_gates(self) -> None:
        data = pl.LazyFrame({"sev": [1.5, 2.25, 3.75]})
        assert (
            training_target_task_issue(
                data, target="sev", task="regression", metrics=_REGRESSION_DEFAULT_METRICS
            )
            is None
        )

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
        assert (
            training_target_task_issue(
                data, target="flag", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_integral_float_flag_passes_classification(self) -> None:
        data = pl.LazyFrame({"flag": [0.0, 1.0, None, 1.0, float("nan")]})
        assert (
            training_target_task_issue(
                data, target="flag", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_continuous_float_gates_classification(self) -> None:
        data = pl.LazyFrame({"sev": [123.45, 0.0, 1.0]})
        issue = training_target_task_issue(
            data, target="sev", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
        )
        assert issue is not None
        # The message must name the user-model objects and a call to action.
        assert "'sev'" in issue
        assert "classification" in issue
        assert "regression" in issue
        assert "Choose a discrete target column" in issue

    def test_classification_task_gates_even_with_regression_metrics(self) -> None:
        """The classification FIT is undefined on a continuous target — the
        gate cannot be escaped by requesting regression metrics."""
        data = pl.LazyFrame({"sev": [123.45, 0.0, 1.0]})
        issue = training_target_task_issue(
            data, target="sev", task="classification", metrics=_REGRESSION_DEFAULT_METRICS
        )
        assert issue is not None
        assert "'sev'" in issue
        assert "classification" in issue

    def test_regression_task_with_classification_metrics_gates_continuous_target(self) -> None:
        """The binomial-family bypass: objective-implied AUC/log loss under
        ``task="regression"`` must gate pre-dispatch, naming the metrics."""
        data = pl.LazyFrame({"prop": [0.25, 0.5, 0.75]})
        issue = training_target_task_issue(
            data, target="prop", task="regression", metrics=_CLASSIFICATION_DEFAULT_METRICS
        )
        assert issue is not None
        assert "'prop'" in issue
        assert "regression" in issue
        assert "auc, logloss" in issue
        assert "Choose a discrete target column" in issue
        # The escape hatch for a legitimate continuous-proportion binomial
        # fit must be named: set regression metrics explicitly.
        assert "set the reported metrics explicitly to regression metrics" in issue

    def test_metric_names_are_matched_case_insensitively(self) -> None:
        data = pl.LazyFrame({"prop": [0.25, 0.5]})
        issue = training_target_task_issue(data, target="prop", task="regression", metrics=["AUC"])
        assert issue is not None
        assert "'prop'" in issue
        # The message names the normalised offending metric, not the raw list.
        assert "(auc)" in issue

    def test_task_is_matched_case_insensitively(self) -> None:
        """A creatively-cased task must not skip the gate entirely."""
        data = pl.LazyFrame({"sev": [123.45, 0.0, 1.0]})
        issue = training_target_task_issue(
            data, target="sev", task="Classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
        )
        assert issue is not None
        assert "'sev'" in issue

    def test_message_names_only_the_offending_metrics(self) -> None:
        """gini/rmse ride alongside auc in a mixed explicit list — the
        message must indict only the discrete-label metrics."""
        data = pl.LazyFrame({"prop": [0.25, 0.5]})
        issue = training_target_task_issue(
            data, target="prop", task="regression", metrics=["gini", "auc", "rmse"]
        )
        assert issue is not None
        assert "(auc)" in issue
        assert "(gini" not in issue

    def test_regression_task_with_classification_metrics_passes_integral_flag(self) -> None:
        data = pl.LazyFrame({"flag": [0.0, 1.0, None, 1.0]})
        assert (
            training_target_task_issue(
                data, target="flag", task="regression", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_regression_task_with_classification_metrics_defers_non_float_targets(self) -> None:
        """On the metric-keyed branch only fractional float/decimal targets
        gate; other dtypes stay owned by the fit's own target validation."""
        data = pl.LazyFrame({"when": pl.Series([1, 2]).cast(pl.Date)})
        assert (
            training_target_task_issue(
                data, target="when", task="regression", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_non_classifiable_dtype_gates_classification(self) -> None:
        data = pl.LazyFrame({"when": pl.Series([1, 2]).cast(pl.Date)})
        issue = training_target_task_issue(
            data, target="when", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
        )
        assert issue is not None
        assert "'when'" in issue
        assert "regression" in issue

    def test_missing_target_column_is_not_this_gates_job(self) -> None:
        data = pl.LazyFrame({"other": [1.5]})
        assert (
            training_target_task_issue(
                data, target="sev", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_all_null_target_defers_to_the_null_count_gate(self) -> None:
        """A Null-typed column is a null-count problem, not a type problem."""
        data = pl.LazyFrame({"flag": [None, None]})
        assert (
            training_target_task_issue(
                data, target="flag", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_integral_decimal_flag_passes_classification(self) -> None:
        data = pl.LazyFrame(
            {"flag": pl.Series([Decimal("0"), Decimal("1")], dtype=pl.Decimal(3, 0))}
        )
        assert (
            training_target_task_issue(
                data, target="flag", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_fractional_decimal_gates_classification(self) -> None:
        data = pl.LazyFrame(
            {"sev": pl.Series([Decimal("123.45"), Decimal("6.70")], dtype=pl.Decimal(10, 2))}
        )
        issue = training_target_task_issue(
            data, target="sev", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
        )
        assert issue is not None
        assert "'sev'" in issue
        assert "continuous values" in issue

    def test_decimal_fractionality_is_checked_in_native_arithmetic(self) -> None:
        """A fractional part beyond float precision must still gate.

        Casting to Float64 first would round 12345678901234567890.5 to an
        integral float and misclassify the target as discrete.
        """
        data = pl.LazyFrame(
            {"sev": pl.Series([Decimal("12345678901234567890.5")], dtype=pl.Decimal(38, 2))}
        )
        issue = training_target_task_issue(
            data, target="sev", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
        )
        assert issue is not None
        assert "'sev'" in issue

    def test_integral_scaled_decimal_flag_passes_classification(self) -> None:
        data = pl.LazyFrame(
            {"flag": pl.Series([Decimal("0.00"), Decimal("1.00"), None], dtype=pl.Decimal(38, 2))}
        )
        assert (
            training_target_task_issue(
                data, target="flag", task="classification", metrics=_CLASSIFICATION_DEFAULT_METRICS
            )
            is None
        )

    def test_custom_collector_is_used_for_the_fractional_scan(self) -> None:
        data = pl.LazyFrame({"sev": [123.45]})
        collected: list[pl.LazyFrame] = []

        def collect(lf: pl.LazyFrame) -> pl.DataFrame:
            collected.append(lf)
            return lf.collect()

        issue = training_target_task_issue(
            data,
            target="sev",
            task="classification",
            metrics=_CLASSIFICATION_DEFAULT_METRICS,
            collect=collect,
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

    def test_gate_fires_through_the_evaluation_plan_pipeline(self, tmp_path: Path) -> None:
        """The live route's canonical evaluation pipeline runs the same gate.

        `_run_evaluation` prepares the source through `_prepare_data`, so a
        continuous target under classification gates before any plan is
        generated or fit is run.
        """
        data = pl.DataFrame(
            {
                "x1": [0.1, 0.2, 0.3, 0.4, 0.5],
                "sev": [123.45, 67.8, 9.1, 2.5, 4.75],
            }
        )
        job = TrainingJob(
            name="gate_eval_model",
            data=data,
            target="sev",
            task="classification",
            evaluation={
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="continuous values") as excinfo:
            job.run()
        message = str(excinfo.value)
        assert "'sev'" in message
        assert "set the task to regression" in message

    def test_prepare_data_gates_objective_implied_metrics_under_regression(
        self, tmp_path: Path
    ) -> None:
        """The binomial-family bypass closed: a Logloss objective under
        ``task="regression"`` defaults the metrics to AUC/log loss, so a
        continuous target must gate at prepare time, not the metric stage."""
        data = pl.DataFrame(
            {
                "x1": [0.1, 0.2, 0.3, 0.4],
                "prop": [0.25, 0.5, 0.75, 0.1],
            }
        )
        job = TrainingJob(
            name="gate_implied_metrics_model",
            data=data,
            target="prop",
            task="regression",
            loss_function="Logloss",
            output_dir=str(tmp_path),
        )
        assert job.metrics == ["auc", "logloss"]  # the objective-implied defaults
        with pytest.raises(ValueError, match="continuous values") as excinfo:
            job.run()
        message = str(excinfo.value)
        assert "'prop'" in message
        assert "auc, logloss" in message
        assert "set the reported metrics explicitly to regression metrics" in message

    def test_prepare_data_passes_explicit_regression_metrics_over_proportions(
        self, tmp_path: Path
    ) -> None:
        """The escape hatch: explicit regression metrics keep a legitimate
        continuous-proportion fit trainable — the gate must not fire, so the
        run proceeds past _prepare_data (here into the fit itself)."""
        data = pl.DataFrame(
            {
                "x1": [0.1, 0.2, 0.3, 0.4],
                "prop": [0.25, 0.5, 0.75, 0.1],
            }
        )
        job = TrainingJob(
            name="gate_escape_hatch_model",
            data=data,
            target="prop",
            task="regression",
            loss_function="Logloss",
            metrics=["gini", "rmse"],
            output_dir=str(tmp_path),
        )
        prepared = job._prepare_data(lambda message, fraction: None)
        assert prepared is not None

    def test_escape_hatch_trains_a_binomial_glm_on_proportions_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """The exact scenario the rejection message prescribes must work:
        config-built binomial GLM, continuous proportion target, explicit
        regression metrics — through the builder, the gate, the fit, and the
        metric stage to a completed result."""
        from haute.modelling._train_config import build_training_job_kwargs

        rng = np.random.RandomState(42)
        n = 80
        x1 = rng.randn(n)
        prop = 1.0 / (1.0 + np.exp(-(0.8 * x1 + rng.randn(n) * 0.3)))
        data_path = tmp_path / "proportions.parquet"
        pl.DataFrame({"x1": x1, "prop": prop}).write_parquet(data_path)
        config = {
            "name": "hatch_binomial_model",
            "target": "prop",
            "task": "regression",
            "algorithm": "glm",
            "family": "binomial",
            "terms": {"x1": {"type": "linear"}},
            "metrics": ["gini", "rmse"],
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "output_dir": str(tmp_path),
        }
        kwargs = build_training_job_kwargs(config, data=str(data_path))
        assert kwargs["metrics"] == ["gini", "rmse"]
        result = TrainingJob(**kwargs).run()
        assert "gini" in result.metrics
        assert "rmse" in result.metrics


class TestMetricStageContext:
    def test_metric_failure_names_target_task_and_metrics(self, tmp_path: Path) -> None:
        """A metric/task mismatch the gate cannot catch still surfaces with context.

        A multi-class integer target passes the gate (integers are discrete
        labels), but binary AUC over it raises inside sklearn; the wrap must
        name the evaluation set, target, task, and metric list around the
        library error.
        """
        rng = np.random.RandomState(42)
        n = 60
        x1 = rng.randn(n)
        data = pl.DataFrame(
            {
                "x1": x1,
                "x2": rng.randn(n),
                "sev": rng.randint(0, 3, n),
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
        # The library detail is chained last (truncation-safe) without
        # coupling to sklearn's exact wording.
        assert "Underlying error:" in message
        assert isinstance(excinfo.value.__cause__, ValueError)

    def test_metric_failure_is_wrapped_on_the_evaluation_plan_path(self, tmp_path: Path) -> None:
        """The canonical evaluation-plan pipeline wraps its validation-fit metrics.

        The live train route always supplies the canonical ``evaluation``
        object, so the first metric computation happens inside a validation
        fit — the wrap must fire there too, not only in the legacy
        constructor-only pipeline's final-fit metric stage. The multi-class
        integer target passes the pre-dispatch gate but breaks binary AUC.
        """
        rng = np.random.RandomState(42)
        n = 60
        x1 = rng.randn(n)
        data = pl.DataFrame(
            {
                "x1": x1,
                "x2": rng.randn(n),
                "sev": rng.randint(0, 3, n),
            }
        )
        job = TrainingJob(
            name="metric_wrap_eval_model",
            data=data,
            target="sev",
            task="regression",
            loss_function="RMSE",
            metrics=["auc"],
            params={"iterations": 4, "depth": 2},
            evaluation={
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError) as excinfo:
            job.run()
        message = str(excinfo.value)
        assert "Could not evaluate the trained model" in message
        assert "validation fit" in message
        assert "'sev'" in message
        assert "'regression'" in message
        assert "auc" in message
        assert "Underlying error:" in message
        assert isinstance(excinfo.value.__cause__, ValueError)


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

    def test_validate_target_task_pairing_rejects_binomial_family_under_regression(
        self, tmp_path: Path
    ) -> None:
        """A binomial GLM under ``task="regression"`` implies AUC/log loss —
        a continuous target must fail here pre-dispatch, not at the metric
        stage inside the child."""
        tmp_parquet = tmp_path / "train_input.parquet"
        pl.DataFrame({"x1": [0.1, 0.2], "prop": [0.25, 0.75]}).write_parquet(tmp_parquet)
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        with pytest.raises(HTTPException) as excinfo:
            TrainService._validate_target_task_pairing(
                str(tmp_parquet),
                {
                    "target": "prop",
                    "task": "regression",
                    "algorithm": "glm",
                    "family": "binomial",
                    "terms": ["x1"],
                },
                execution_context=context,
            )
        assert excinfo.value.status_code == 422
        detail = str(excinfo.value.detail)
        assert "'prop'" in detail
        assert "auc, logloss" in detail
        assert "set the reported metrics explicitly to regression metrics" in detail
        assert not tmp_parquet.exists()

    def test_validate_target_task_pairing_honours_explicit_regression_metrics(
        self, tmp_path: Path
    ) -> None:
        """Explicit regression metrics keep a continuous-proportion binomial
        fit dispatchable — the effective metric set has no classification
        metric, so the gate must not fire."""
        tmp_parquet = tmp_path / "train_input.parquet"
        pl.DataFrame({"x1": [0.1, 0.2], "prop": [0.25, 0.75]}).write_parquet(tmp_parquet)
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        TrainService._validate_target_task_pairing(
            str(tmp_parquet),
            {
                "target": "prop",
                "task": "regression",
                "algorithm": "glm",
                "family": "binomial",
                "terms": ["x1"],
                "metrics": ["gini", "rmse"],
            },
            execution_context=context,
        )
        assert tmp_parquet.exists()

    def test_validate_target_task_pairing_maps_malformed_metrics_to_422(
        self, tmp_path: Path
    ) -> None:
        """A malformed metrics config (normally rejected by the route's
        upfront validation) is a config problem, not a scan failure — it must
        map to 422/contract_error and still not orphan the temp parquet."""
        tmp_parquet = tmp_path / "train_input.parquet"
        pl.DataFrame({"x1": [0.1, 0.2], "sev": [123.45, 6.7]}).write_parquet(tmp_parquet)
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        with pytest.raises(HTTPException) as excinfo:
            TrainService._validate_target_task_pairing(
                str(tmp_parquet),
                {"target": "sev", "task": "regression", "metrics": "auc"},
                execution_context=context,
            )
        assert excinfo.value.status_code == 422
        assert "non-empty string list" in str(excinfo.value.detail)
        assert not tmp_parquet.exists()

    def test_validate_target_task_pairing_removes_parquet_when_the_scan_fails(
        self, tmp_path: Path
    ) -> None:
        """A corrupt sunk parquet must not be orphaned by the gate itself."""
        tmp_parquet = tmp_path / "train_input.parquet"
        tmp_parquet.write_bytes(b"not a parquet file")
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        with pytest.raises(pl.exceptions.ComputeError):
            TrainService._validate_target_task_pairing(
                str(tmp_parquet),
                {"target": "sev", "task": "classification"},
                execution_context=context,
            )
        assert not tmp_parquet.exists()

    def test_preparation_thread_gates_before_launching_the_fit_worker(self, tmp_path: Path) -> None:
        """The route wiring: gate runs after sink, before _launch_background."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        tmp_parquet = tmp_path / "train_input.parquet"
        pl.DataFrame({"x1": [0.1, 0.2, 0.3], "sev": [123.45, 6.7, 8.9]}).write_parquet(tmp_parquet)
        graph = {
            "nodes": [
                {
                    "id": "source",
                    "type": "custom",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "train",
                    "type": "custom",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Model Training 9",
                        "nodeType": "modelling",
                        "config": {
                            "target": "sev",
                            "task": "classification",
                            "algorithm": "catboost",
                            "loss_function": "Logloss",
                            "params": {"iterations": 2},
                            "evaluation": {
                                "schema_version": 1,
                                "strategy": "random",
                                "seed": 42,
                                "validation": {"method": "single", "size": 0.2},
                            },
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "source-train",
                    "source": "source",
                    "target": "train",
                }
            ],
        }
        store = JobStore()
        service = TrainService(store)
        body = TrainRequest.model_validate({"graph": graph, "node_id": "train"})
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        launched: list[str] = []
        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 3, 2)),
            patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
            patch(
                "haute.routes._train_service.create_admitted_execution_context",
                return_value=context,
            ),
            patch.object(service, "_execute_and_sink", return_value=str(tmp_parquet)),
            patch.object(
                service, "_launch_background", side_effect=lambda *a, **k: launched.append("yes")
            ),
        ):
            response = service.start(body)
            service._join_preparation(response.job_id)

        job = store.require_job(response.job_id)
        assert job["status"] == "contract_error"
        assert "'sev'" in job["message"]
        assert "classification" in job["message"]
        assert launched == []
        assert not tmp_parquet.exists()


class TestWorkerBoundaryUserMessage:
    def test_value_error_channel_is_stamped_user_facing(self) -> None:
        from haute.routes._train_service import _known_training_worker_failure

        payload = _known_training_worker_failure(
            ValueError("Target column 'sev' not found. Available: ['x1']"),
            bounded_memory_prefix="Training cannot run in bounded streaming mode",
        )
        assert payload is not None
        assert payload.terminal_reason == "contract_error"
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == payload.message

    def test_memory_error_keeps_the_typed_wrapper_surface(self) -> None:
        from haute.routes._train_service import _known_training_worker_failure

        payload = _known_training_worker_failure(
            MemoryError(),
            bounded_memory_prefix="Training cannot run in bounded streaming mode",
        )
        assert payload is not None
        assert payload.terminal_reason == "memory_limited"
        assert payload.fields["error_code"] == "memory_limit"
        assert WORKER_USER_MESSAGE_FIELD not in payload.fields

    def test_unexpected_exception_fallback_is_curated_without_third_party_text(self) -> None:
        """The fallback names the fit context and exception type only — the
        arbitrary third-party message body (which may carry internal paths)
        stays in the diagnostic ``error`` field, and the failure remains a
        plain system ``error``, never a ``contract_error``."""
        from haute.routes._train_service import _friendly_error

        exc = RuntimeError("failed to lock /var/lib/haute/internal/state.db")
        message = _friendly_error(exc, context="target 'sev' (objective 'Tweedie')")
        assert "RuntimeError" in message
        assert "target 'sev' (objective 'Tweedie')" in message
        assert "/var/lib" not in message
        payload = _worker_failure_payload(
            exc,
            terminal_reason="error",
            message=message,
            fields={"error": str(exc)},
            user_facing=True,
        )
        assert payload.terminal_reason == "error"
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == message
        assert payload.fields["error"] == "failed to lock /var/lib/haute/internal/state.db"

    def test_model_save_os_error_names_the_reason_not_the_staging_path(self) -> None:
        from haute.routes._train_service import _friendly_error

        exc = OSError(28, "No space left on device", "/tmp/.haute-training-j1/output/model.cbm")
        message = _friendly_error(exc)
        assert "No space left on device" in message
        assert "/tmp" not in message
        assert "model.cbm" not in message

    def test_memory_limit_failure_names_budget_and_call_to_action(self) -> None:
        from haute._execution_context import ExecutionMemoryLimitExceededError
        from haute.routes._train_service import _known_training_worker_failure

        exc = ExecutionMemoryLimitExceededError(
            "training_job",
            rss_bytes=3 * 1024**3,
            limit_bytes=2 * 1024**3,
            job_id="abc",
        )
        payload = _known_training_worker_failure(
            exc,
            bounded_memory_prefix="Training cannot run in bounded streaming mode",
            operation_noun="Training",
        )
        assert payload is not None
        assert payload.terminal_reason == "memory_limited"
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == payload.message
        assert "3.0 GiB used, 2.0 GiB allowed" in payload.message
        assert "try again" in payload.message
        assert "training_job" not in payload.message
        # The internal wording stays available for diagnostics.
        assert "training_job" in payload.fields["error"]

    def test_memory_limit_message_branches_never_crash_the_failure_path(self) -> None:
        """Every authored branch of the memory-limit message: sampler loss,
        process-cap override, baseline subtraction (with negative clamp), and
        size omission when a bound is missing — a partially-populated
        exception must degrade to omitting sizes, never TypeError."""
        from haute._execution_context import ExecutionMemoryLimitExceededError
        from haute.routes._train_service import _memory_limit_user_message

        sampler_lost = ExecutionMemoryLimitExceededError(
            "training_job",
            rss_bytes=None,
            limit_bytes=1024**3,
            reason="memory_sampler_unavailable",
        )
        message = _memory_limit_user_message(sampler_lost, operation_noun="Training")
        assert "could no longer measure its memory use" in message

        process_cap = ExecutionMemoryLimitExceededError(
            "training_job",
            rss_bytes=4 * 1024**3,
            limit_bytes=1024**3,
            baseline_rss_bytes=1024**3,
            rss_limit_bytes=3 * 1024**3,
            reason="process_rss_limit_exceeded",
        )
        message = _memory_limit_user_message(process_cap, operation_noun="Training")
        assert "4.0 GiB used, 3.0 GiB allowed" in message

        growth = ExecutionMemoryLimitExceededError(
            "training_job",
            rss_bytes=3 * 1024**3,
            limit_bytes=1024**3,
            baseline_rss_bytes=2 * 1024**3 + 512 * 1024**2,
        )
        message = _memory_limit_user_message(growth, operation_noun="Training")
        assert "512.0 MiB used, 1.0 GiB allowed" in message

        below_baseline = ExecutionMemoryLimitExceededError(
            "training_job",
            rss_bytes=1024**3,
            limit_bytes=1024**3,
            baseline_rss_bytes=2 * 1024**3,
        )
        message = _memory_limit_user_message(below_baseline, operation_noun="Training")
        assert "(0 bytes used" in message

        no_limit = ExecutionMemoryLimitExceededError(
            "training_job",
            rss_bytes=1024**3,
            limit_bytes=None,  # type: ignore[arg-type]
        )
        message = _memory_limit_user_message(no_limit, operation_noun="Training")
        assert "used" not in message
        assert "needs more memory than this server allows." in message

    def test_507_detail_message_is_curated_even_when_payload_carries_one(self) -> None:
        """The curated wording is assigned unconditionally — a payload-carried
        message (now or via a future to_payload change) cannot expose
        str(exc)."""
        from haute._execution_context import ExecutionMemoryLimitExceededError
        from haute.routes._train_service import _memory_limit_http_exception

        class PayloadMessageError(ExecutionMemoryLimitExceededError):
            def to_payload(self) -> dict[str, object]:
                payload = super().to_payload()
                payload["message"] = str(self)
                return payload

        exc = PayloadMessageError(
            "training_job",
            rss_bytes=3 * 1024**3,
            limit_bytes=2 * 1024**3,
        )
        http_exc = _memory_limit_http_exception(exc)
        assert isinstance(http_exc.detail, dict)
        assert "training_job" not in http_exc.detail["message"]
        assert "needs more memory than this server allows" in http_exc.detail["message"]

    def test_overlong_curated_message_truncates_instead_of_raising(self) -> None:
        """A wrapped message beyond the 512-char worker bound must truncate at
        the payload builder, never detonate into a protocol-length error."""
        payload = _worker_failure_payload(
            ValueError("x" * 600),
            terminal_reason="contract_error",
            user_facing=True,
        )
        assert len(payload.message) == 512
        assert len(payload.fields[WORKER_USER_MESSAGE_FIELD]) == 512

    def test_failure_payload_keeps_explicit_fields_and_adds_marker(self) -> None:
        payload = _worker_failure_payload(
            ValueError("out of range"),
            terminal_reason="contract_error",
            fields={"error": "out of range", "error_code": "range"},
            user_facing=True,
        )
        assert payload.fields["error_code"] == "range"
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == "out of range"

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

    def test_user_message_field_is_bounded_like_the_payload_message(self) -> None:
        from haute._worker_protocol import WorkerProtocolError

        with pytest.raises(WorkerProtocolError, match="fields.user_message"):
            WorkerFailurePayload(
                terminal_reason="error",
                error_type="ValueError",
                message="short",
                traceback="tb",
                fields={WORKER_USER_MESSAGE_FIELD: "x" * 600},
            )

    def test_worker_cancellation_surfaces_as_plain_cancelled(self) -> None:
        """The internal operation/job-id wording of the cancellation exception
        is diagnostics; the terminal message matches the preparation path."""
        from haute._execution_context import ExecutionCancelledError
        from haute.routes._train_service import _known_training_worker_failure

        payload = _known_training_worker_failure(
            ExecutionCancelledError("training_job", job_id="abc"),
            bounded_memory_prefix="Training cannot run in bounded streaming mode",
        )
        assert payload is not None
        assert payload.terminal_reason == "cancelled"
        assert payload.message == "Cancelled"
        assert payload.fields[WORKER_USER_MESSAGE_FIELD] == "Cancelled"
        assert "training_job" in payload.fields["error"]

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

    @pytest.mark.parametrize(
        ("make_exc", "expected_fragment", "expected_reason"),
        [
            (
                lambda: IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=None),
                "stopped unexpectedly before returning a result",
                "error",
            ),
            (
                # SIGKILL under a configured cap: the OOM wording is hedged —
                # the exit-code heuristic is indicative, not proof.
                lambda: IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=100),
                "may have run out of memory",
                "memory_limited",
            ),
            (
                lambda: IsolatedWorkerTimeoutError(timeout_seconds=30.0),
                "exceeded timeout",
                "timed_out",
            ),
        ],
    )
    def test_crash_and_timeout_keep_wrapper_text_through_the_supervisor(
        self,
        make_exc: Callable[[], Exception],
        expected_fragment: str,
        expected_reason: str,
    ) -> None:
        """Failures that never produce a payload keep the typed wrapper text."""

        def execute() -> None:
            raise make_exc()

        outcome = IsolatedJobSupervisor._produce_outcome(
            execute,
            completed_fields=lambda result: {"result": result},
            completed_message="Completed",
        )
        assert outcome.terminal_reason == expected_reason
        assert expected_fragment in outcome.message
        assert WORKER_USER_MESSAGE_FIELD not in outcome.fields

    def test_user_message_survives_a_real_spawned_protocol_round_trip(self, tmp_path: Path) -> None:
        """Pin payload → result queue → parent reconstruction across spawn."""
        from haute._worker_protocol import WorkerRequest, run_worker_protocol

        with pytest.raises(WorkerRemoteFailureError) as excinfo:
            run_worker_protocol(
                _curated_failure_worker,
                WorkerRequest("round-trip-1", "training", {}),
                artifact_root=tmp_path / "artifacts",
                artifact_kinds=frozenset(),
                max_artifact_size_bytes=0,
            )
        exc = excinfo.value
        assert exc.terminal_reason == "contract_error"
        assert exc.fields[WORKER_USER_MESSAGE_FIELD] == _CURATED_ROUND_TRIP_MESSAGE
