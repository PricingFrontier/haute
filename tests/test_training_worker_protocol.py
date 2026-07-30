from __future__ import annotations

import json
import os
import pickle
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._worker_protocol import (
    WorkerFailurePayload,
    WorkerProtocolError,
    WorkerRemoteFailureError,
    WorkerRequest,
    WorkerResultManifest,
    WorkerRuntime,
    build_artifact_manifest,
    validate_result_manifest,
)
from haute.errors import PreambleError
from haute.modelling._evaluation import (
    EvaluationConfig,
    EvaluationFitResult,
    EvaluationResultsArtifact,
    aggregate_evaluation_results,
    file_sha256,
    generate_evaluation_plan,
    save_evaluation_plan,
    save_evaluation_report,
    save_evaluation_results,
)
from haute.modelling._training_job import (
    TrainResult,
    evaluation_artifact_filenames,
    model_contract_filename,
    tuning_artifact_filenames,
)
from haute.modelling._tuning import (
    TuningConfig,
    TuningPlanArtifact,
    TuningTrialResult,
    TuningTrialsArtifact,
    build_tuning_report,
    save_tuning_plan,
    save_tuning_report,
    save_tuning_trials,
)
from haute.routes._job_store import JobStore
from haute.routes._train_service import (
    TrainingArtifactPublicationError,
    TrainService,
    _publish_training_artifacts,
    _run_dispersion_process_job,
    _run_training_process_job,
    _validate_evaluation_artifact_contents,
    _validate_tuning_artifact_contents,
    _worker_timing,
)
from haute.schemas import EvaluationReportPayload, TuningReportPayload


class _ForwardingQueue:
    def __init__(self, callback=None) -> None:
        self.callback = callback
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)
        if self.callback is not None:
            self.callback(event)

    def put_nowait(self, payload: bytes) -> None:
        self.put(pickle.loads(payload))


def _write_scratch_text(path: Path, text: str) -> None:
    """Write test data to a path whose caller derives it from tmp_path."""
    path.write_text(text, encoding="utf-8")


def _write_scratch_bytes(path: Path, content: bytes) -> None:
    """Write test data to a path whose caller derives it from tmp_path."""
    path.write_bytes(content)


def _inline_protocol_runner(
    function,
    request,
    *,
    artifact_root,
    artifact_kinds,
    max_artifact_size_bytes,
    on_progress=None,
    config=None,
):
    del config
    result = function(WorkerRuntime(_ForwardingQueue(on_progress), str(artifact_root)), request)
    if isinstance(result, WorkerFailurePayload):
        raise WorkerRemoteFailureError(result)
    assert isinstance(result, WorkerResultManifest)
    validate_result_manifest(
        result,
        artifact_root=artifact_root,
        artifact_kinds=artifact_kinds,
        max_artifact_size_bytes=max_artifact_size_bytes,
    )
    return result


def _evaluation_payload(output_dir: Path, model_name: str) -> dict[str, object]:
    """Write a canonical MOD-M11/M12 evaluation triplet and its public payload."""
    names = evaluation_artifact_filenames(model_name)
    plan_path = output_dir / names["plan"]
    results_path = output_dir / names["results"]
    report_path = output_dir / names["report"]
    config = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 7,
            "validation": {"method": "cross_validation", "fold_count": 2},
            "test": {"size": 0.2},
        }
    )
    plan = generate_evaluation_plan(config, source_sha256="c" * 64, row_count=10, task="regression")
    save_evaluation_plan(plan, plan_path)
    plan_sha256 = file_sha256(plan_path)
    results = EvaluationResultsArtifact(
        1,
        plan_sha256,
        tuple(
            EvaluationFitResult(
                1, index, fit.train_rows, fit.validation_rows, {"rmse": float(index + 1)}, 1
            )
            for index, fit in enumerate(plan.validation_fits)
        ),
    )
    save_evaluation_results(results, results_path)
    results_sha256 = file_sha256(results_path)
    report = aggregate_evaluation_results(plan, results, ("rmse",), results_sha256=results_sha256)
    save_evaluation_report(report, report_path)
    return {
        "schema_version": 1,
        "strategy": "random",
        "validation_method": "cross_validation",
        "validation_fit_count": 2,
        "fit_count": 3,
        "development_rows": 8,
        "final_test_rows": 2,
        "selection_fits": [fit.to_plain_data() for fit in results.fits],
        "selection_metrics": report.to_plain_data()["metrics"],
        "plan_sha256": plan_sha256,
        "results_sha256": results_sha256,
        "plan_path": str(plan_path),
        "results_path": str(results_path),
        "report_path": str(report_path),
        "summary": plan.summary,
    }


def _published_evaluation(payload: dict[str, object]) -> EvaluationReportPayload:
    data = dict(payload)
    for field in ("plan_path", "results_path", "report_path"):
        data[field] = f"output/{Path(str(data[field])).name}"
    return EvaluationReportPayload.model_validate(data)


def _tuning_payload(
    output_dir: Path,
    model_name: str,
    *,
    evaluation_plan_sha256: str,
) -> dict[str, object]:
    """Write a canonical tuning triplet linked to an evaluation plan."""
    names = tuning_artifact_filenames(model_name)
    plan_path = output_dir / names["plan"]
    trials_path = output_dir / names["trials"]
    report_path = output_dir / names["report"]
    evaluation = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 7,
            "validation": {"method": "cross_validation", "fold_count": 2},
            "test": {"size": 0.2},
        }
    )
    base_params = {"iterations": 100, "depth": 6}
    config = TuningConfig.from_plain_data(
        {
            "schema_version": 1,
            "trial_count": 5,
            "seed": 7,
            "metric": "rmse",
            "search_space": {"depth": [5, 7]},
        },
        algorithm="catboost",
        base_params=base_params,
        evaluation=evaluation,
        configured_metrics=["rmse"],
    )
    plan = TuningPlanArtifact.create(
        config=config,
        base_params=base_params,
        evaluation_plan_sha256=evaluation_plan_sha256,
        sampler="TPESampler",
        sampler_version="4.9.0",
    )
    save_tuning_plan(plan, plan_path)
    objectives = (1.0, 0.5, 0.7, 0.8, 0.9)
    sampled_depths: tuple[int | None, ...] = (None, 5, 7, 5, 7)
    trial_results = tuple(
        TuningTrialResult(
            schema_version=1,
            trial_index=index,
            label="baseline" if index == 0 else "sampled",
            sampled_params={} if depth is None else {"depth": depth},
            resolved_params={
                **base_params,
                **({} if depth is None else {"depth": depth}),
            },
            fits=(
                EvaluationFitResult(1, 0, 6, 2, {"rmse": objective}, 9),
                EvaluationFitResult(1, 1, 6, 2, {"rmse": objective}, 11),
            ),
            aggregate_metrics={"rmse": objective},
            objective=objective,
            elapsed_seconds=0.1,
        )
        for index, (objective, depth) in enumerate(zip(objectives, sampled_depths, strict=True))
    )
    trials = TuningTrialsArtifact(
        schema_version=1,
        plan_sha256=file_sha256(plan_path),
        evaluation_plan_sha256=evaluation_plan_sha256,
        trials=trial_results,
    )
    save_tuning_trials(trials, trials_path)
    report = build_tuning_report(
        plan,
        trials,
        trials_sha256=file_sha256(trials_path),
        final_params={"iterations": 10, "depth": 5},
        final_tree_count=10,
    )
    save_tuning_report(report, report_path)
    return {
        **report.to_plain_data(),
        "trials": [trial.to_plain_data() for trial in trials.trials],
        "plan_path": str(plan_path),
        "trials_path": str(trials_path),
        "report_path": str(report_path),
    }


def _published_tuning(payload: dict[str, object]) -> TuningReportPayload:
    data = dict(payload)
    for field in ("plan_path", "trials_path", "report_path"):
        data[field] = f"output/{Path(str(data[field])).name}"
    return TuningReportPayload.model_validate(data)


class _SuccessfulTrainingJob:
    def __init__(self, **kwargs) -> None:
        self.output_dir = Path(kwargs["output_dir"])
        self.name = str(kwargs.get("name", "model"))

    def run(self, progress, on_iteration, **_kwargs):
        progress("Fitting", 0.4)
        on_iteration(1, 2, {"rmse": 0.5})
        self.output_dir.mkdir(parents=True, exist_ok=True)
        model_path = self.output_dir / f"{self.name}.cbm"
        model_path.write_bytes(b"model")
        (self.output_dir / model_contract_filename(self.name)).write_text(
            '{"schema_version": 1}', encoding="utf-8"
        )
        return TrainResult(
            metrics={"rmse": 0.2},
            feature_importance=[],
            model_path=str(model_path),
            train_rows=8,
            validation_rows=2,
            features=["x"],
            cat_features=[],
            development_rows=8,
            final_test_rows=2,
            final_test_metrics={"rmse": 0.2},
            diagnostics_set="final_test",
            loss_history=[{"iteration": 1.0, "loss": 0.5}],
            evaluation=_evaluation_payload(self.output_dir, self.name),
        )


class _SuccessfulTunedTrainingJob(_SuccessfulTrainingJob):
    def run(self, progress, on_iteration, **kwargs):
        result = super().run(progress, on_iteration, **kwargs)
        evaluation = dict(result.evaluation or {})
        tuning = _tuning_payload(
            self.output_dir,
            self.name,
            evaluation_plan_sha256=str(evaluation["plan_sha256"]),
        )
        evaluation["fit_count"] = tuning["total_fit_count"]
        kwargs["on_tuning_progress"](
            {
                "phase": "trial_fit",
                "trial_index": 1,
                "trial_count": 5,
                "fold_index": 1,
                "fold_count": 2,
                "completed_fits": 0,
                "total_fits": 11,
                "best_objective": None,
            }
        )
        return replace(result, evaluation=evaluation, tuning=tuning)


def _request(tmp_path: Path) -> WorkerRequest:
    return WorkerRequest(
        "job-1",
        "training",
        {
            "job_kwargs": {
                "name": "quoted",
                "data": str(tmp_path / "input.parquet"),
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 7,
                    "validation": {"method": "cross_validation", "fold_count": 2},
                    "test": {"size": 0.2},
                },
            },
            "profile": ExecutionProfile.TRAINING_PREP.value,
            "memory_limit_bytes": None,
        },
    )


def _dispersion_request(tmp_path: Path, **overrides: object) -> WorkerRequest:
    payload: dict[str, object] = {
        "job_kwargs": {
            "target": "y",
            "weight": "weight",
            "offset": "offset",
            "params": {"family": "negbinomial", "terms": {"x": {}}, "interactions": []},
        },
        "param": "theta",
        "profile": ExecutionProfile.TRAINING_PREP.value,
        "memory_limit_bytes": None,
    }
    payload.update(overrides)
    return WorkerRequest("job-1", "dispersion", payload)


def _staged_training_manifest(tmp_path: Path):
    root = tmp_path / "artifacts"
    output = root / "output"
    output.mkdir(parents=True)
    model = output / "quoted.cbm"
    contract = output / model_contract_filename("quoted")
    model.write_bytes(b"new-model")
    contract.write_bytes(b"new-contract")
    payload = _evaluation_payload(output, "quoted")
    paths = {
        "evaluation_plan": payload["plan_path"],
        "evaluation_results": payload["results_path"],
        "evaluation_report": payload["report_path"],
    }
    artifacts = [
        build_artifact_manifest(artifact_root=root, path=model, kind="model", lifetime="staged"),
        build_artifact_manifest(
            artifact_root=root, path=contract, kind="feature_contract", lifetime="staged"
        ),
    ]
    artifacts.extend(
        build_artifact_manifest(
            artifact_root=root, path=Path(str(path)), kind=kind, lifetime="staged"
        )
        for kind, path in paths.items()
    )
    return (
        root,
        output,
        WorkerResultManifest(metadata={}, artifacts=tuple(artifacts)),
        _published_evaluation(payload),
    )


def _staged_tuned_training_manifest(tmp_path: Path):
    root, output, manifest, expected_evaluation = _staged_training_manifest(tmp_path)
    payload = _tuning_payload(
        output,
        "quoted",
        evaluation_plan_sha256=expected_evaluation.plan_sha256,
    )
    tuning_paths = {
        "tuning_plan": payload["plan_path"],
        "tuning_trials": payload["trials_path"],
        "tuning_report": payload["report_path"],
    }
    tuning_artifacts = tuple(
        build_artifact_manifest(
            artifact_root=root,
            path=Path(str(path)),
            kind=kind,
            lifetime="staged",
        )
        for kind, path in tuning_paths.items()
    )
    return (
        root,
        output,
        WorkerResultManifest(
            metadata={},
            artifacts=(*manifest.artifacts, *tuning_artifacts),
        ),
        expected_evaluation.model_copy(update={"fit_count": int(payload["total_fit_count"])}),
        _published_tuning(payload),
    )


def _launch(service: TrainService, store: JobStore, tmp_path: Path, output_dir: Path):
    prepared = tmp_path / "prepared.parquet"
    prepared.write_bytes(b"prepared")
    job_id = store.create_job(
        {"status": "running", "job_type": "training", "start_time": time.monotonic(), "timeout": 60}
    )
    thread = service._launch_background(
        job_id,
        "quoted",
        {
            "name": "quoted",
            "target": "y",
            "algorithm": "catboost",
            "loss_function": "RMSE",
            "output_dir": str(output_dir),
            "evaluation": _request(tmp_path).payload["job_kwargs"]["evaluation"],
        },
        {"iterations": 2},
        str(prepared),
        None,
        10,
        execution_context=ExecutionContext(
            operation="training_pipeline", profile=ExecutionProfile.TRAINING_PREP
        ),
    )
    assert thread is not None
    thread.join_and_raise(timeout=10)
    return job_id, prepared


def test_training_entrypoint_stages_complete_evaluation_and_public_response(tmp_path: Path) -> None:
    queue = _ForwardingQueue()
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        result = _run_training_process_job(
            WorkerRuntime(queue, str(tmp_path / "artifacts")), _request(tmp_path)
        )
    assert isinstance(result, WorkerResultManifest)
    assert {artifact.kind for artifact in result.artifacts} == {
        "model",
        "feature_contract",
        "evaluation_plan",
        "evaluation_results",
        "evaluation_report",
    }
    response = result.metadata["response"]
    assert response["diagnostic_metrics"] == {"rmse": 0.2}
    assert response["final_test_metrics"] == {"rmse": 0.2}
    assert response["development_rows"] == 8 and response["final_test_rows"] == 2
    assert response["diagnostics_set"] == "final_test"
    assert response["evaluation"]["plan_path"] == "output/quoted.evaluation-plan.json"
    assert "tuning" not in response
    assert [event.kind for event in queue.events] == ["progress", "iteration"]


@pytest.mark.parametrize(
    ("evaluation", "tuning", "message"),
    [
        (None, None, "evaluation result must be an object"),
        ({"plan_path": ""}, None, "evaluation result has no plan_path"),
        ("canonical", [], "tuning result must be an object"),
        ("canonical", {"plan_path": ""}, "tuning result has no plan_path"),
    ],
)
def test_training_entrypoint_rejects_incomplete_evaluation_and_tuning_artifacts(
    tmp_path: Path,
    evaluation: object,
    tuning: object,
    message: str,
) -> None:
    class IncompleteArtifactJob(_SuccessfulTrainingJob):
        def run(self, progress, on_iteration, **kwargs):
            result = super().run(progress, on_iteration, **kwargs)
            selected_evaluation = result.evaluation if evaluation == "canonical" else evaluation
            return replace(result, evaluation=selected_evaluation, tuning=tuning)

    with patch("haute.modelling.TrainingJob", IncompleteArtifactJob):
        result = _run_training_process_job(
            WorkerRuntime(_ForwardingQueue(), str(tmp_path / "artifacts")),
            _request(tmp_path),
        )

    assert isinstance(result, WorkerFailurePayload)
    assert result.error_type == "ValueError"
    assert message in result.message


@pytest.mark.parametrize(
    "worker_request, error_type",
    [
        (
            WorkerRequest(
                "job-1",
                "training",
                {
                    "job_kwargs": [],
                    "profile": ExecutionProfile.TRAINING_PREP.value,
                    "memory_limit_bytes": None,
                },
            ),
            "ValueError",
        ),
        (_request(Path()), "PreambleError"),
    ],
)
def test_training_entrypoint_maps_contract_failures(
    tmp_path: Path, worker_request: WorkerRequest, error_type: str
) -> None:
    class ContractFailingJob:
        def __init__(self, **_kwargs):
            raise PreambleError("invalid training preamble", source_line=7)

    with patch("haute.modelling.TrainingJob", ContractFailingJob):
        result = _run_training_process_job(
            WorkerRuntime(_ForwardingQueue(), str(tmp_path / "artifacts")), worker_request
        )
    assert isinstance(result, WorkerFailurePayload)
    assert result.error_type == error_type and result.terminal_reason == "contract_error"


def test_train_service_publishes_complete_evaluation_run(tmp_path: Path) -> None:
    launches = []

    def recording_runner(*args, **kwargs):
        launches.append(kwargs["artifact_kinds"])
        return _inline_protocol_runner(*args, **kwargs)

    store = JobStore()
    service = TrainService(store, protocol_runner=recording_runner)
    output_dir = tmp_path / "outputs"
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        job_id, prepared = _launch(service, store, tmp_path, output_dir)
    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["result"].evaluation.plan_path == str(
        (output_dir / "quoted.evaluation-plan.json").resolve()
    )
    assert job["result"].evaluation.results_path == str(
        (output_dir / "quoted.evaluation-results.json").resolve()
    )
    assert job["result"].evaluation.report_path == str(
        (output_dir / "quoted.evaluation-report.json").resolve()
    )
    assert launches == [
        frozenset(
            {
                "model",
                "feature_contract",
                "evaluation_plan",
                "evaluation_results",
                "evaluation_report",
                "tuning_plan",
                "tuning_trials",
                "tuning_report",
            }
        )
    ]
    assert not prepared.exists() and not list(output_dir.glob(".haute-training-*"))


def test_train_service_publishes_complete_tuned_run_and_terminal_progress(tmp_path: Path) -> None:
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    output_dir = tmp_path / "outputs"

    with patch("haute.modelling.TrainingJob", _SuccessfulTunedTrainingJob):
        job_id, prepared = _launch(service, store, tmp_path, output_dir)

    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["phase"] == "completed"
    assert job["trial_count"] == 5
    assert job["fold_count"] == 2
    assert job["completed_fits"] == job["total_fits"] == 11
    assert job["best_objective"] == pytest.approx(0.5)
    assert job["result"].tuning is not None
    assert job["result"].tuning.winner_trial_index == 1
    assert job["result"].tuning.evaluation_plan_sha256 == job["result"].evaluation.plan_sha256
    for path in (
        job["result"].tuning.plan_path,
        job["result"].tuning.trials_path,
        job["result"].tuning.report_path,
    ):
        assert Path(path).is_file()
    assert not prepared.exists() and not list(output_dir.glob(".haute-training-*"))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.__setitem__("plan_path", data["report_path"]),
        lambda data: data.__setitem__("plan_sha256", "d" * 64),
    ],
)
def test_train_service_rejects_evaluation_response_artifact_mismatch(
    tmp_path: Path, mutation
) -> None:
    def mismatched_runner(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        metadata, response = dict(result.metadata), dict(result.metadata["response"])
        evaluation = dict(response["evaluation"])
        mutation(evaluation)
        response["evaluation"] = evaluation
        metadata["response"] = response
        return WorkerResultManifest(metadata=metadata, artifacts=result.artifacts)

    store = JobStore()
    service = TrainService(store, protocol_runner=mismatched_runner)
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        job_id, _prepared = _launch(service, store, tmp_path, tmp_path / "outputs")
    assert store.require_job(job_id)["status"] == "contract_error"


def test_train_service_rejects_schema_invalid_completed_response(tmp_path: Path) -> None:
    def invalid_response_runner(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        metadata = dict(result.metadata)
        response = dict(metadata["response"])
        response["development_rows"] = int(response["development_rows"]) + 1
        metadata["response"] = response
        return WorkerResultManifest(metadata=metadata, artifacts=result.artifacts)

    store = JobStore()
    service = TrainService(store, protocol_runner=invalid_response_runner)
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        job_id, _prepared = _launch(service, store, tmp_path, tmp_path / "outputs")

    assert store.require_job(job_id)["status"] == "contract_error"
    assert "malformed" in store.require_job(job_id)["message"].lower()


def test_train_service_rejects_tuning_artifacts_omitted_by_response(tmp_path: Path) -> None:
    def undeclared_tuning_runner(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        source = next(
            artifact for artifact in result.artifacts if artifact.kind == "evaluation_plan"
        )
        undeclared = type(source)(
            kind="tuning_plan",
            relative_path=source.relative_path,
            size_bytes=source.size_bytes,
            sha256=source.sha256,
            lifetime=source.lifetime,
        )
        return WorkerResultManifest(
            metadata=result.metadata,
            artifacts=(*result.artifacts, undeclared),
        )

    store = JobStore()
    service = TrainService(store, protocol_runner=undeclared_tuning_runner)
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        job_id, _prepared = _launch(service, store, tmp_path, tmp_path / "outputs")

    assert store.require_job(job_id)["status"] == "contract_error"
    assert "omits declared tuning artifacts" in store.require_job(job_id)["message"]


def test_train_service_rejects_tuning_response_manifest_path_mismatch(tmp_path: Path) -> None:
    def mismatched_tuning_runner(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        report = next(artifact for artifact in result.artifacts if artifact.kind == "tuning_report")
        mismatched = type(report)(
            kind=report.kind,
            relative_path="output/not-the-tuning-report.json",
            size_bytes=report.size_bytes,
            sha256=report.sha256,
            lifetime=report.lifetime,
        )
        return WorkerResultManifest(
            metadata=result.metadata,
            artifacts=tuple(
                mismatched if artifact.kind == "tuning_report" else artifact
                for artifact in result.artifacts
            ),
        )

    store = JobStore()
    service = TrainService(store, protocol_runner=mismatched_tuning_runner)
    with patch("haute.modelling.TrainingJob", _SuccessfulTunedTrainingJob):
        job_id, _prepared = _launch(service, store, tmp_path, tmp_path / "outputs")

    assert store.require_job(job_id)["status"] == "contract_error"
    assert "tuning response path" in store.require_job(job_id)["message"].lower()


def test_publication_validates_evaluation_digests_and_canonical_paths(tmp_path: Path) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    published = _publish_training_artifacts(
        manifest,
        artifact_root=root,
        output_root=tmp_path / "outputs",
        job_id="job-1",
        expected_model_name="quoted",
        expected_evaluation=expected,
    )
    assert set(published) == {
        "model",
        "feature_contract",
        "evaluation_plan",
        "evaluation_results",
        "evaluation_report",
    }
    assert file_sha256(published["evaluation_plan"]) == expected.plan_sha256
    assert file_sha256(published["evaluation_results"]) == expected.results_sha256
    assert published["evaluation_report"].exists()


def test_publication_validates_tuning_digests_and_canonical_paths(tmp_path: Path) -> None:
    root, _output, manifest, expected_evaluation, expected_tuning = _staged_tuned_training_manifest(
        tmp_path
    )

    published = _publish_training_artifacts(
        manifest,
        artifact_root=root,
        output_root=tmp_path / "outputs",
        job_id="job-1",
        expected_model_name="quoted",
        expected_evaluation=expected_evaluation,
        expected_tuning=expected_tuning,
    )

    assert set(published) == {
        "model",
        "feature_contract",
        "evaluation_plan",
        "evaluation_results",
        "evaluation_report",
        "tuning_plan",
        "tuning_trials",
        "tuning_report",
    }
    assert file_sha256(published["tuning_plan"]) == expected_tuning.plan_sha256
    assert file_sha256(published["tuning_trials"]) == expected_tuning.trials_sha256
    assert published["tuning_report"].is_file()


def test_publication_rejects_response_without_required_artifact_contracts(tmp_path: Path) -> None:
    root, _output, manifest, expected_evaluation = _staged_training_manifest(tmp_path)
    with pytest.raises(WorkerProtocolError, match="must declare evaluation"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=None,  # type: ignore[arg-type] - runtime boundary guard
        )

    tuned_root, _tuned_output, _tuned_manifest, _tuned_evaluation, expected_tuning = (
        _staged_tuned_training_manifest(tmp_path / "tuned")
    )
    assert tuned_root.is_dir()
    with pytest.raises(WorkerProtocolError, match="tuning artifact set disagree"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation,
            expected_tuning=expected_tuning,
        )


@pytest.mark.parametrize(
    ("kind", "relative_path", "message"),
    [
        (
            "feature_contract",
            "output/wrong.feature-contract.json",
            "feature contract filenames",
        ),
        (
            "evaluation_plan",
            "output/wrong.evaluation-plan.json",
            "evaluation_plan filename",
        ),
    ],
)
def test_publication_rejects_noncanonical_companion_filenames(
    tmp_path: Path,
    kind: str,
    relative_path: str,
    message: str,
) -> None:
    root, _output, manifest, expected_evaluation = _staged_training_manifest(tmp_path)
    selected = next(artifact for artifact in manifest.artifacts if artifact.kind == kind)
    changed = type(selected)(
        kind=selected.kind,
        relative_path=relative_path,
        size_bytes=selected.size_bytes,
        sha256=selected.sha256,
        lifetime=selected.lifetime,
    )
    malformed = WorkerResultManifest(
        metadata={},
        artifacts=tuple(
            changed if artifact.kind == kind else artifact for artifact in manifest.artifacts
        ),
    )

    with pytest.raises(WorkerProtocolError, match=message):
        _publish_training_artifacts(
            malformed,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation,
        )


def test_publication_rejects_response_paths_and_digests_that_disagree(
    tmp_path: Path,
) -> None:
    root, _output, manifest, expected_evaluation = _staged_training_manifest(tmp_path)
    with pytest.raises(WorkerProtocolError, match="response path"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation.model_copy(
                update={"plan_path": "output/not-the-plan.json"}
            ),
        )
    with pytest.raises(WorkerProtocolError, match="staged artifact contents"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation.model_copy(update={"results_sha256": "e" * 64}),
        )


def test_publication_rejects_tuning_filename_path_and_digest_disagreement(
    tmp_path: Path,
) -> None:
    root, _output, manifest, expected_evaluation, expected_tuning = _staged_tuned_training_manifest(
        tmp_path
    )
    tuning_plan = next(
        artifact for artifact in manifest.artifacts if artifact.kind == "tuning_plan"
    )
    wrong_filename = type(tuning_plan)(
        kind=tuning_plan.kind,
        relative_path="output/wrong.tuning-plan.json",
        size_bytes=tuning_plan.size_bytes,
        sha256=tuning_plan.sha256,
        lifetime=tuning_plan.lifetime,
    )
    with pytest.raises(WorkerProtocolError, match="tuning_plan filename"):
        _publish_training_artifacts(
            WorkerResultManifest(
                metadata={},
                artifacts=tuple(
                    wrong_filename if artifact.kind == "tuning_plan" else artifact
                    for artifact in manifest.artifacts
                ),
            ),
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation,
            expected_tuning=expected_tuning,
        )
    with pytest.raises(WorkerProtocolError, match="tuning response path"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation,
            expected_tuning=expected_tuning.model_copy(
                update={"plan_path": "output/not-the-tuning-plan.json"}
            ),
        )
    with pytest.raises(WorkerProtocolError, match="staged artifact contents"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected_evaluation,
            expected_tuning=expected_tuning.model_copy(update={"plan_sha256": "e" * 64}),
        )


def test_tuning_artifact_validation_rejects_wrong_evaluation_link(tmp_path: Path) -> None:
    root, _output, manifest, _expected_evaluation, _expected_tuning = (
        _staged_tuned_training_manifest(tmp_path)
    )
    staged_and_final = {
        artifact.kind: (
            root / artifact.relative_path,
            tmp_path / "unused" / Path(artifact.relative_path).name,
        )
        for artifact in manifest.artifacts
    }

    with pytest.raises(WorkerProtocolError, match="does not link"):
        _validate_tuning_artifact_contents(
            staged_and_final,
            evaluation_plan_sha256="f" * 64,
        )


def test_persisted_reports_must_match_their_source_artifacts(tmp_path: Path) -> None:
    root, output, manifest, expected_evaluation, _expected_tuning = _staged_tuned_training_manifest(
        tmp_path
    )
    staged_and_final = {
        artifact.kind: (
            root / artifact.relative_path,
            tmp_path / "unused" / Path(artifact.relative_path).name,
        )
        for artifact in manifest.artifacts
    }

    evaluation_report = output / evaluation_artifact_filenames("quoted")["report"]
    evaluation_data = json.loads(evaluation_report.read_text(encoding="utf-8"))
    evaluation_data["metrics"]["rmse"]["stddev"] += 0.25
    _write_scratch_text(evaluation_report, json.dumps(evaluation_data))
    with pytest.raises(WorkerProtocolError, match="does not match"):
        _validate_evaluation_artifact_contents(
            staged_and_final,
            response_fit_count=expected_evaluation.fit_count,
        )

    tuning_report = output / tuning_artifact_filenames("quoted")["report"]
    tuning_data = json.loads(tuning_report.read_text(encoding="utf-8"))
    tuning_data["trials_sha256"] = "d" * 64
    _write_scratch_text(tuning_report, json.dumps(tuning_data))
    with pytest.raises(WorkerProtocolError, match="does not match"):
        _validate_tuning_artifact_contents(
            staged_and_final,
            evaluation_plan_sha256=expected_evaluation.plan_sha256,
        )


@pytest.mark.parametrize("kind", ["evaluation_report", "evaluation_results"])
def test_publication_rejects_malformed_or_incomplete_evaluation_set(
    tmp_path: Path, kind: str
) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    if kind == "evaluation_report":
        path = output / evaluation_artifact_filenames("quoted")["report"]
        _write_scratch_text(path, "{}")
        changed = build_artifact_manifest(
            artifact_root=root, path=path, kind=kind, lifetime="staged"
        )
        artifacts = tuple(
            changed if artifact.kind == kind else artifact for artifact in manifest.artifacts
        )
    else:
        artifacts = tuple(artifact for artifact in manifest.artifacts if artifact.kind != kind)
    with pytest.raises(WorkerProtocolError):
        _publish_training_artifacts(
            WorkerResultManifest(metadata={}, artifacts=artifacts),
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )


def test_publication_rejects_results_not_linked_to_exact_evaluation_plan(tmp_path: Path) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    plan = output / evaluation_artifact_filenames("quoted")["plan"]
    _write_scratch_text(plan, plan.read_text(encoding="utf-8") + "\n")
    changed = build_artifact_manifest(
        artifact_root=root, path=plan, kind="evaluation_plan", lifetime="staged"
    )
    malformed = WorkerResultManifest(
        metadata={},
        artifacts=tuple(changed if a.kind == "evaluation_plan" else a for a in manifest.artifacts),
    )
    with pytest.raises(WorkerProtocolError, match="evaluation artifact"):
        _publish_training_artifacts(
            malformed,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )


def test_publication_retires_stale_tuning_companions(tmp_path: Path) -> None:
    root, _output, manifest, expected = _staged_training_manifest(tmp_path)
    destination = tmp_path / "outputs"
    destination.mkdir()
    stale = [destination / name for name in tuning_artifact_filenames("quoted").values()]
    for path in stale:
        _write_scratch_bytes(path, b"stale")
    _publish_training_artifacts(
        manifest,
        artifact_root=root,
        output_root=destination,
        job_id="job-1",
        expected_model_name="quoted",
        expected_evaluation=expected,
    )
    assert not any(path.exists() for path in stale)


def test_publication_rolls_back_all_evaluation_artifacts(tmp_path: Path) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    destination = tmp_path / "outputs"
    destination.mkdir()
    old = {}
    for artifact in manifest.artifacts:
        final = destination / Path(artifact.relative_path).name
        old[final] = f"old-{artifact.kind}".encode()
        final.write_bytes(old[final])
    failing = output / evaluation_artifact_filenames("quoted")["report"]
    original = os.replace

    def fail(source, destination_path):
        if Path(source) == failing:
            raise OSError("report cannot be published")
        original(source, destination_path)

    with (
        patch("haute.routes._train_service.os.replace", side_effect=fail),
        pytest.raises(OSError, match="report"),
    ):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=destination,
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )
    assert {path: path.read_bytes() for path in old} == old


def test_publication_requires_tuning_artifacts_all_or_nothing(tmp_path: Path) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    tuning = output / tuning_artifact_filenames("quoted")["plan"]
    _write_scratch_text(tuning, "{}")
    artifact = build_artifact_manifest(
        artifact_root=root, path=tuning, kind="tuning_plan", lifetime="staged"
    )
    with pytest.raises(WorkerProtocolError, match="complete"):
        _publish_training_artifacts(
            WorkerResultManifest(metadata={}, artifacts=(*manifest.artifacts, artifact)),
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )


def test_windows_publication_retries_transient_contention_and_publishes_complete_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    destination = tmp_path / "outputs"
    staged_model = output / "quoted.cbm"
    original, attempts = os.replace, 0

    def transient(source, target):
        nonlocal attempts
        if Path(source) == staged_model and attempts < 2:
            attempts += 1
            raise PermissionError("sharing violation")
        original(source, target)

    monkeypatch.setattr("haute.routes._train_service.sys.platform", "win32")
    with (
        patch("haute.routes._train_service.os.replace", side_effect=transient),
        patch("haute.routes._train_service.time.sleep") as sleep,
    ):
        published = _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=destination,
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )
    assert attempts == sleep.call_count == 2
    assert {path.read_bytes() for path in published.values()} >= {b"new-model", b"new-contract"}


def test_cancel_before_publication_preserves_durable_artifacts(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outputs"
    output.mkdir()
    model, contract = output / "quoted.cbm", output / model_contract_filename("quoted")
    _write_scratch_bytes(model, b"old-model")
    _write_scratch_bytes(contract, b"old-contract")
    store = JobStore()
    service: TrainService

    def cancelling(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        assert service.cancel(request.request_id)["status"] == "cancelled"
        return result

    service = TrainService(store, protocol_runner=cancelling)
    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        job_id, prepared = _launch(service, store, tmp_path, output)
    assert store.require_job(job_id)["status"] == "cancelled"
    assert model.read_bytes() == b"old-model" and contract.read_bytes() == b"old-contract"
    assert not prepared.exists()


def test_publication_rejects_model_name_not_declared_by_request(tmp_path: Path) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    model = next(artifact for artifact in manifest.artifacts if artifact.kind == "model")
    wrong_path = output / "wrong.cbm"
    (root / model.relative_path).replace(wrong_path)
    wrong = build_artifact_manifest(
        artifact_root=root, path=wrong_path, kind="model", lifetime="staged"
    )
    malformed = WorkerResultManifest(
        metadata={},
        artifacts=tuple(
            wrong if artifact.kind == "model" else artifact for artifact in manifest.artifacts
        ),
    )

    with pytest.raises(WorkerProtocolError, match="requested name"):
        _publish_training_artifacts(
            malformed,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )


@pytest.mark.parametrize("variant", ["duplicate", "durable", "incomplete", "invalid_path"])
def test_publication_rejects_invalid_artifact_manifests(tmp_path: Path, variant: str) -> None:
    root, _output, complete, expected = _staged_training_manifest(tmp_path)
    model = next(artifact for artifact in complete.artifacts if artifact.kind == "model")
    if variant == "duplicate":
        artifacts = (*complete.artifacts, model)
    elif variant == "durable":
        durable = build_artifact_manifest(
            artifact_root=root,
            path=root / model.relative_path,
            kind="model",
            lifetime="durable",
        )
        artifacts = tuple(
            durable if artifact.kind == "model" else artifact for artifact in complete.artifacts
        )
    elif variant == "incomplete":
        artifacts = tuple(artifact for artifact in complete.artifacts if artifact.kind != "model")
    else:
        invalid = type(model)(
            kind=model.kind,
            relative_path="elsewhere/quoted.cbm",
            size_bytes=model.size_bytes,
            sha256=model.sha256,
            lifetime=model.lifetime,
        )
        artifacts = tuple(
            invalid if artifact.kind == "model" else artifact for artifact in complete.artifacts
        )

    with pytest.raises(WorkerProtocolError):
        _publish_training_artifacts(
            WorkerResultManifest(metadata={}, artifacts=artifacts),
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )


def test_publication_rejects_backup_collision_without_modifying_durable_artifacts(
    tmp_path: Path,
) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    destination = tmp_path / "outputs"
    destination.mkdir()
    final = destination / "quoted.cbm"
    backup = destination / ".quoted.cbm.job-1.haute-backup"
    final.write_bytes(b"old-model")
    backup.write_bytes(b"existing backup")

    with pytest.raises(FileExistsError, match="backup already exists"):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=destination,
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )

    assert final.read_bytes() == b"old-model"
    assert backup.read_bytes() == b"existing backup"
    assert (output / "quoted.cbm").exists()


def test_windows_publication_exhaustion_restores_complete_old_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    destination = tmp_path / "outputs"
    destination.mkdir()
    old = {}
    for artifact in manifest.artifacts:
        final = destination / Path(artifact.relative_path).name
        old[final] = f"old-{artifact.kind}".encode()
        final.write_bytes(old[final])
    staged_model = output / "quoted.cbm"
    attempts = 0
    original = os.replace

    def blocked(source, target):
        nonlocal attempts
        if Path(source) == staged_model:
            attempts += 1
            raise PermissionError("sharing violation")
        original(source, target)

    monkeypatch.setattr("haute.routes._train_service.sys.platform", "win32")
    with (
        patch("haute.routes._train_service.os.replace", side_effect=blocked),
        patch("haute.routes._train_service.time.sleep") as sleep,
        pytest.raises(TrainingArtifactPublicationError),
    ):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=destination,
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )
    assert attempts == 4 and sleep.call_count == 3
    assert {path: path.read_bytes() for path in old} == old


def test_windows_publication_does_not_retry_non_contention_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, manifest, expected = _staged_training_manifest(tmp_path)
    staged_model, attempts = output / "quoted.cbm", 0
    original = os.replace

    def failing(source, target):
        nonlocal attempts
        if Path(source) == staged_model:
            attempts += 1
            raise OSError("disk failure")
        original(source, target)

    monkeypatch.setattr("haute.routes._train_service.sys.platform", "win32")
    with (
        patch("haute.routes._train_service.os.replace", side_effect=failing),
        patch("haute.routes._train_service.time.sleep") as sleep,
        pytest.raises(OSError, match="disk failure"),
    ):
        _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )
    assert attempts == 1
    sleep.assert_not_called()


def test_publication_keeps_new_generation_when_backup_deletion_is_denied(tmp_path: Path) -> None:
    root, _output, manifest, expected = _staged_training_manifest(tmp_path)
    destination = tmp_path / "outputs"
    destination.mkdir()
    for artifact in manifest.artifacts:
        (destination / Path(artifact.relative_path).name).write_bytes(b"old")
    original_unlink = Path.unlink

    def deny_backup_unlink(path: Path, *args, **kwargs) -> None:
        if path.name.endswith(".haute-backup"):
            raise PermissionError("backup is temporarily locked")
        original_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", autospec=True, side_effect=deny_backup_unlink):
        published = _publish_training_artifacts(
            manifest,
            artifact_root=root,
            output_root=destination,
            job_id="job-1",
            expected_model_name="quoted",
            expected_evaluation=expected,
        )
    assert published["model"].read_bytes() == b"new-model"
    assert published["feature_contract"].read_bytes() == b"new-contract"
    assert list(destination.glob("*.haute-backup"))


def test_worker_timing_rejects_all_invalid_values() -> None:
    for job in ({}, {"start_time": True, "timeout": 1}, {"start_time": 1, "timeout": 0}):
        with pytest.raises(RuntimeError, match="job-1.*valid"):
            _worker_timing(job, job_id="job-1")


def test_dispersion_worker_remains_isolated_and_emits_fit_events(tmp_path: Path) -> None:
    data = tmp_path / "prepared.parquet"
    frame = pl.DataFrame(
        {"x": [1.0, 2.0], "y": [3.0, 4.0], "weight": [1.0, 1.0], "offset": [0.0, 0.0]}
    )
    frame.write_parquet(data)
    queue, captured = _ForwardingQueue(), {}

    class Job:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def _prepare_data(self, progress, **_kwargs):
            progress("Preparing", 0.1)
            return SimpleNamespace(features=["x"], cat_features=[], data_path=data)

    def estimate(**kwargs):
        kwargs["on_fit"](0)
        return SimpleNamespace(param="theta", value=1.25, llf=-4.5, n_fits=1)

    request = WorkerRequest(
        "job-1",
        "dispersion",
        {
            "job_kwargs": {
                "target": "y",
                "weight": "weight",
                "offset": "offset",
                "params": {"family": "negbinomial", "terms": {"x": {}}, "interactions": []},
            },
            "param": "theta",
            "profile": ExecutionProfile.TRAINING_PREP.value,
            "memory_limit_bytes": None,
        },
    )
    with (
        patch("haute.modelling.TrainingJob", Job),
        patch("haute.modelling._rustystats._resolve_glm_terms", return_value=["x"]),
        patch("haute.modelling._rustystats._build_interactions", return_value=[]),
        patch("haute.modelling._rustystats.estimate_glm_dispersion", side_effect=estimate),
        patch("haute._polars_utils.streaming_collect", return_value=frame),
    ):
        result = _run_dispersion_process_job(
            WorkerRuntime(queue, str(tmp_path / "artifacts")), request
        )
    assert isinstance(result, WorkerResultManifest)
    assert result.metadata["value"] == 1.25
    assert [event.kind for event in queue.events] == ["progress", "progress", "dispersion_fit"]


@pytest.mark.parametrize(
    "worker_request",
    [
        WorkerRequest("job-1", "training", {}),
        WorkerRequest("job-1", "dispersion", []),
        _dispersion_request(Path(), job_kwargs=[]),
        _dispersion_request(Path(), param="unknown"),
        _dispersion_request(Path(), profile=123),
        _dispersion_request(Path(), memory_limit_bytes=0),
    ],
)
def test_dispersion_worker_maps_malformed_requests_to_contract_failures(
    tmp_path: Path, worker_request: WorkerRequest
) -> None:
    result = _run_dispersion_process_job(
        WorkerRuntime(_ForwardingQueue(), str(tmp_path / "artifacts")), worker_request
    )
    assert isinstance(result, WorkerFailurePayload)
    assert result.terminal_reason == "contract_error"
    assert result.error_type in {"ValueError", "TypeError"}


@pytest.mark.parametrize(
    ("exception", "reason"),
    [(MemoryError("full"), "memory_limited"), (RuntimeError("boom"), "error")],
)
def test_dispersion_worker_maps_estimator_failures(
    tmp_path: Path, exception: Exception, reason: str
) -> None:
    class FailingJob:
        def __init__(self, **_kwargs) -> None:
            pass

        def _prepare_data(self, *_args, **_kwargs):
            raise exception

    with patch("haute.modelling.TrainingJob", FailingJob):
        result = _run_dispersion_process_job(
            WorkerRuntime(_ForwardingQueue(), str(tmp_path / "artifacts")),
            _dispersion_request(tmp_path),
        )
    assert isinstance(result, WorkerFailurePayload)
    assert result.terminal_reason == reason
    assert result.error_type == type(exception).__name__


def test_train_service_keeps_completed_status_when_post_commit_cleanup_is_denied(
    tmp_path: Path,
) -> None:
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    output = tmp_path / "outputs"
    released: list[bool] = []
    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        admission_release=lambda: released.append(True),
    )
    original_rmtree = __import__("shutil").rmtree

    def deny_cleanup(path, *args, **kwargs) -> None:
        if Path(path).name.startswith(".haute-training-"):
            raise PermissionError("artifact directory is temporarily locked")
        original_rmtree(path, *args, **kwargs)

    prepared = tmp_path / "prepared.parquet"
    prepared.write_bytes(b"prepared")
    job_id = store.create_job(
        {"status": "running", "job_type": "training", "start_time": time.monotonic(), "timeout": 60}
    )
    with (
        patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob),
        patch("haute.routes._train_service.shutil.rmtree", side_effect=deny_cleanup),
    ):
        thread = service._launch_background(
            job_id,
            "quoted",
            {
                "name": "quoted",
                "target": "y",
                "algorithm": "catboost",
                "loss_function": "RMSE",
                "output_dir": str(output),
                "evaluation": _request(tmp_path).payload["job_kwargs"]["evaluation"],
            },
            {"iterations": 2},
            str(prepared),
            None,
            10,
            execution_context=context,
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)
    assert store.require_job(job_id)["status"] == "completed"
    assert not prepared.exists() and released == [True]
    assert list(output.glob(".haute-training-*"))


def test_parent_worker_cleanup_reports_all_failures_once(tmp_path: Path) -> None:
    service = TrainService(JobStore())
    prepared, artifact_root = tmp_path / "prepared.parquet", tmp_path / "artifacts"
    _write_scratch_bytes(prepared, b"prepared")
    artifact_root.mkdir()

    def fail_admission_release() -> None:
        raise RuntimeError("admission release failed")

    cleanup = service._parent_worker_cleanup(
        "job-1",
        execution_context=ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            admission_release=fail_admission_release,
        ),
        tmp_parquet=prepared,
        artifact_root=artifact_root,
    )
    with (
        patch.object(
            service._training_jobs, "release", side_effect=RuntimeError("registry release failed")
        ) as release,
        patch(
            "haute.routes._train_service.shutil.rmtree",
            side_effect=OSError("artifact cleanup failed"),
        ) as rmtree,
        pytest.raises(RuntimeError, match="registry release failed") as raised,
    ):
        cleanup()
        cleanup()
    assert getattr(raised.value, "__notes__", []) == [
        "Additional cleanup failure: admission release failed",
        "Additional cleanup failure: artifact cleanup failed",
    ]
    assert not prepared.exists() and artifact_root.exists()
    release.assert_called_once_with("job-1")
    rmtree.assert_called_once_with(artifact_root)


def test_terminal_job_after_preparation_does_not_launch_worker(tmp_path: Path) -> None:
    calls: list[str] = []

    def forbidden_runner(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("terminal jobs must not launch a worker")

    store = JobStore()
    service = TrainService(store, protocol_runner=forbidden_runner)
    prepared = tmp_path / "prepared.parquet"
    prepared.write_bytes(b"prepared")
    released: list[bool] = []
    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        admission_release=lambda: released.append(True),
    )
    job_id = store.create_job(
        {"status": "running", "job_type": "training", "start_time": time.monotonic(), "timeout": 60}
    )
    assert service.cancel(job_id)["status"] == "cancelled"
    thread = service._launch_background(
        job_id,
        "quoted",
        {
            "name": "quoted",
            "target": "y",
            "algorithm": "catboost",
            "loss_function": "RMSE",
            "output_dir": str(tmp_path / "outputs"),
            "evaluation": _request(tmp_path).payload["job_kwargs"]["evaluation"],
        },
        {"iterations": 2},
        str(prepared),
        None,
        10,
        execution_context=context,
    )
    assert thread is None and calls == [] and not prepared.exists() and released == [True]


def test_train_service_publication_wins_late_cancel_and_records_elapsed(tmp_path: Path) -> None:
    class SilentSuccessfulTrainingJob(_SuccessfulTrainingJob):
        def run(self, _progress, _on_iteration, **kwargs):
            return super().run(lambda *_args: None, lambda *_args: None, **kwargs)

    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    output, prepared = tmp_path / "outputs", tmp_path / "prepared.parquet"
    _write_scratch_bytes(prepared, b"prepared")
    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "start_time": time.monotonic() - 2,
            "timeout": 60,
        }
    )
    started, cancellation = threading.Event(), {}
    cancel_thread: threading.Thread | None = None
    real_publish = _publish_training_artifacts

    def publish_while_cancel_waits(*args, **kwargs):
        nonlocal cancel_thread
        cancel_thread = threading.Thread(
            target=lambda: (started.set(), cancellation.update(service.cancel(job_id))), daemon=True
        )
        cancel_thread.start()
        assert started.wait(timeout=10)
        return real_publish(*args, **kwargs)

    with (
        patch("haute.modelling.TrainingJob", SilentSuccessfulTrainingJob),
        patch(
            "haute.routes._train_service._publish_training_artifacts",
            side_effect=publish_while_cancel_waits,
        ),
    ):
        thread = service._launch_background(
            job_id,
            "quoted",
            {
                "name": "quoted",
                "target": "y",
                "algorithm": "catboost",
                "loss_function": "RMSE",
                "output_dir": str(output),
                "evaluation": _request(tmp_path).payload["job_kwargs"]["evaluation"],
            },
            {"iterations": 2},
            str(prepared),
            None,
            10,
            execution_context=ExecutionContext(
                operation="training_pipeline", profile=ExecutionProfile.TRAINING_PREP
            ),
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)
    job = store.require_job(job_id)
    assert cancel_thread is not None
    cancel_thread.join(timeout=10)
    assert not cancel_thread.is_alive()
    assert job["status"] == cancellation["status"] == "completed"
    assert job["elapsed_seconds"] >= 2 and (output / "quoted.cbm").read_bytes() == b"model"
