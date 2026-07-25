from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

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
from haute.modelling._training_job import TrainResult, model_contract_filename
from haute.routes._job_store import JobStore
from haute.routes._train_service import (
    TrainService,
    _publish_training_artifacts,
    _run_dispersion_process_job,
    _run_training_process_job,
    _worker_timing,
)


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
    result = function(
        WorkerRuntime(_ForwardingQueue(on_progress), str(artifact_root)),
        request,
    )
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


class _SuccessfulTrainingJob:
    def __init__(self, **kwargs) -> None:
        self.output_dir = Path(kwargs["output_dir"])
        self.name = str(kwargs.get("name", "model"))

    def run(self, progress, on_iteration, **_kwargs):
        progress("Fitting", 0.4)
        on_iteration(1, 2, {"loss": 0.5})
        self.output_dir.mkdir(parents=True, exist_ok=True)
        model_path = self.output_dir / f"{self.name}.cbm"
        model_path.write_bytes(b"model")
        (self.output_dir / model_contract_filename(self.name)).write_text(
            '{"schema_version": 1}',
            encoding="utf-8",
        )
        return TrainResult(
            metrics={"rmse": 0.1},
            feature_importance=[],
            model_path=str(model_path),
            train_rows=8,
            test_rows=2,
            features=["x"],
            cat_features=[],
            loss_history=[{"iteration": 1.0, "loss": 0.5}],
        )


def _request(tmp_path: Path) -> WorkerRequest:
    return WorkerRequest(
        "job-1",
        "training",
        {
            "job_kwargs": {
                "name": "quoted",
                "data": str(tmp_path / "input.parquet"),
            },
            "profile": ExecutionProfile.TRAINING_PREP.value,
            "memory_limit_bytes": None,
        },
    )


def _staged_training_manifest(tmp_path: Path) -> tuple[Path, Path, WorkerResultManifest]:
    artifact_root = tmp_path / "artifacts"
    staged_output = artifact_root / "output"
    staged_output.mkdir(parents=True)
    model_path = staged_output / "quoted.cbm"
    contract_path = staged_output / model_contract_filename("quoted")
    model_path.write_bytes(b"new-model")
    contract_path.write_bytes(b"new-contract")
    manifest = WorkerResultManifest(
        metadata={},
        artifacts=(
            build_artifact_manifest(
                artifact_root=artifact_root,
                path=model_path,
                kind="model",
                lifetime="staged",
            ),
            build_artifact_manifest(
                artifact_root=artifact_root,
                path=contract_path,
                kind="feature_contract",
                lifetime="staged",
            ),
        ),
    )
    return artifact_root, staged_output, manifest


def _dispersion_request(tmp_path: Path, **overrides: object) -> WorkerRequest:
    payload: object = {
        "job_kwargs": {
            "target": "y",
            "weight": "weight",
            "offset": "offset",
            "params": {
                "family": "negbinomial",
                "terms": {"x": {}},
                "interactions": [["x", "x"]],
            },
        },
        "param": "theta",
        "profile": ExecutionProfile.TRAINING_PREP.value,
        "memory_limit_bytes": None,
    }
    assert isinstance(payload, dict)
    payload.update(overrides)
    return WorkerRequest("job-1", "dispersion", payload)


def test_training_entrypoint_stages_model_contract_and_plain_result(tmp_path: Path) -> None:
    queue = _ForwardingQueue()
    runtime = WorkerRuntime(queue, str(tmp_path / "artifacts"))

    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        result = _run_training_process_job(runtime, _request(tmp_path))

    assert isinstance(result, WorkerResultManifest)
    assert result.metadata["response"]["model_path"] == "output/quoted.cbm"
    assert {artifact.kind for artifact in result.artifacts} == {
        "model",
        "feature_contract",
    }
    assert [event.sequence for event in queue.events] == [0, 1]
    assert [event.kind for event in queue.events] == ["progress", "iteration"]


@pytest.mark.parametrize(
    ("worker_request", "error_type", "reason"),
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
            "contract_error",
        ),
        (
            _request(Path()),
            "PreambleError",
            "contract_error",
        ),
    ],
)
def test_training_entrypoint_maps_invalid_and_public_contract_failures(
    tmp_path: Path,
    worker_request: WorkerRequest,
    error_type: str,
    reason: str,
) -> None:
    class ContractFailingJob:
        def __init__(self, **_kwargs) -> None:
            raise PreambleError("invalid training preamble", source_line=7)

    with patch("haute.modelling.TrainingJob", ContractFailingJob):
        result = _run_training_process_job(
            WorkerRuntime(_ForwardingQueue(), str(tmp_path / "artifacts")), worker_request
        )

    assert isinstance(result, WorkerFailurePayload)
    assert result.error_type == error_type
    assert result.terminal_reason == reason
    if error_type == "PreambleError":
        assert result.fields["error_code"] == "preamble_failed"
        assert result.fields["http_status_code"] == 422


def test_dispersion_entrypoint_runs_profile_search_and_emits_fit_events(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "prepared.parquet"
    frame = pl.DataFrame(
        {"x": [1.0, 2.0], "y": [3.0, 4.0], "weight": [1.0, 1.0], "offset": [0.0, 0.0]}
    )
    frame.write_parquet(data_path)
    queue = _ForwardingQueue()
    captured: dict[str, object] = {}

    class DispersionJob:
        def __init__(self, **kwargs) -> None:
            captured["job_kwargs"] = kwargs

        def _prepare_data(self, progress, **_kwargs):
            progress("Preparing", 0.1)
            return SimpleNamespace(
                features=["x", "unused"],
                cat_features=["unused"],
                data_path=data_path,
            )

    def fake_estimate(**kwargs):
        captured["estimate"] = kwargs
        kwargs["on_fit"](0)
        kwargs["on_fit"](2)
        return SimpleNamespace(param="theta", value=1.25, llf=-4.5, n_fits=3)

    runtime = WorkerRuntime(queue, str(tmp_path / "artifacts"))
    with (
        patch("haute.modelling.TrainingJob", DispersionJob),
        patch("haute.modelling._rustystats._resolve_glm_terms", return_value=["x"]),
        patch("haute.modelling._rustystats._build_interactions", return_value=[("x", "x")]),
        patch("haute.modelling._rustystats.estimate_glm_dispersion", side_effect=fake_estimate),
        patch("haute._polars_utils.streaming_collect", return_value=frame) as collect,
    ):
        result = _run_dispersion_process_job(runtime, _dispersion_request(tmp_path))

    assert isinstance(result, WorkerResultManifest)
    assert result.metadata["param"] == "theta"
    assert result.metadata["value"] == 1.25
    assert result.metadata["n_fits"] == 3
    assert [event.kind for event in queue.events] == [
        "progress",
        "progress",
        "dispersion_fit",
        "dispersion_fit",
    ]
    estimate = captured["estimate"]
    assert isinstance(estimate, dict)
    assert estimate["data"].equals(frame)
    assert {key: value for key, value in estimate.items() if key != "data"} == {
        "terms": ["x"],
        "target": "y",
        "family": "negbinomial",
        "param": "theta",
        "link": None,
        "intercept": True,
        "weight": "weight",
        "offset": "offset",
        "interactions": [("x", "x")],
        "on_fit": ANY,
    }
    assert collect.call_args.kwargs["profile"] is ExecutionProfile.TRAINING_PREP


@pytest.mark.parametrize(
    ("worker_request", "reason"),
    [
        (WorkerRequest("job-1", "training", {}), "contract_error"),
        (WorkerRequest("job-1", "dispersion", []), "contract_error"),
        (_dispersion_request(Path(), job_kwargs=[]), "contract_error"),
        (_dispersion_request(Path(), param="unknown"), "contract_error"),
        (_dispersion_request(Path(), profile=123), "contract_error"),
        (_dispersion_request(Path(), memory_limit_bytes=0), "contract_error"),
    ],
)
def test_dispersion_entrypoint_maps_malformed_requests_to_contract_failures(
    tmp_path: Path, worker_request: WorkerRequest, reason: str
) -> None:
    result = _run_dispersion_process_job(
        WorkerRuntime(_ForwardingQueue(), str(tmp_path / "artifacts")), worker_request
    )

    assert isinstance(result, WorkerFailurePayload)
    assert result.terminal_reason == reason
    assert result.error_type in {"ValueError", "TypeError"}


@pytest.mark.parametrize(
    ("exception", "reason"),
    [(MemoryError("full"), "memory_limited"), (RuntimeError("boom"), "error")],
)
def test_dispersion_entrypoint_maps_estimation_failures(
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


@pytest.mark.parametrize(
    "job", [{}, {"start_time": True, "timeout": 1}, {"start_time": 1, "timeout": 0}]
)
def test_worker_timing_rejects_invalid_values(job: dict[str, object]) -> None:
    with pytest.raises(RuntimeError, match="job-1.*valid"):
        _worker_timing(job, job_id="job-1")


def test_train_service_publishes_validated_pair_and_cleans_parent_state(
    tmp_path: Path,
) -> None:
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    output_dir = tmp_path / "outputs"
    prepared = tmp_path / "prepared.parquet"
    prepared.write_bytes(b"prepared")
    released: list[bool] = []
    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        admission_release=lambda: released.append(True),
    )
    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "start_time": time.monotonic(),
            "timeout": 60,
        }
    )

    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        thread = service._launch_background(
            job_id,
            "quoted",
            {
                "name": "quoted",
                "target": "y",
                "algorithm": "catboost",
                "loss_function": "RMSE",
                "output_dir": str(output_dir),
            },
            {"iterations": 2},
            str(prepared),
            None,
            10,
            execution_context=context,
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["result"].model_path == str((output_dir / "quoted.cbm").resolve())
    assert (output_dir / "quoted.cbm").read_bytes() == b"model"
    assert (output_dir / model_contract_filename("quoted")).exists()
    assert not prepared.exists()
    assert released == [True]
    assert not list(output_dir.glob(".haute-training-*"))


def test_train_service_rejects_mismatched_artifact_pair_without_overwrite(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    final_model = output_dir / "quoted.cbm"
    final_contract = output_dir / model_contract_filename("quoted")
    final_model.write_bytes(b"old-model")
    final_contract.write_bytes(b"old-contract")

    def mismatched_runner(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        root = Path(kwargs["artifact_root"])
        original_contract = next(
            root / artifact.relative_path
            for artifact in result.artifacts
            if artifact.kind == "feature_contract"
        )
        wrong_contract = original_contract.with_name("wrong.feature_contract.json")
        original_contract.replace(wrong_contract)
        wrong_manifest = build_artifact_manifest(
            artifact_root=root,
            path=wrong_contract,
            kind="feature_contract",
            lifetime="staged",
        )
        malformed = WorkerResultManifest(
            metadata=result.metadata,
            artifacts=tuple(
                wrong_manifest if artifact.kind == "feature_contract" else artifact
                for artifact in result.artifacts
            ),
        )
        validate_result_manifest(
            malformed,
            artifact_root=root,
            artifact_kinds=kwargs["artifact_kinds"],
            max_artifact_size_bytes=kwargs["max_artifact_size_bytes"],
        )
        return malformed

    store = JobStore()
    service = TrainService(store, protocol_runner=mismatched_runner)
    prepared = tmp_path / "prepared.parquet"
    prepared.write_bytes(b"prepared")
    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "start_time": time.monotonic(),
            "timeout": 60,
        }
    )

    with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
        thread = service._launch_background(
            job_id,
            "quoted",
            {
                "name": "quoted",
                "target": "y",
                "algorithm": "catboost",
                "loss_function": "RMSE",
                "output_dir": str(output_dir),
            },
            {"iterations": 2},
            str(prepared),
            None,
            10,
            execution_context=context,
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "contract_error"
    assert final_model.read_bytes() == b"old-model"
    assert final_contract.read_bytes() == b"old-contract"
    assert not prepared.exists()
    assert not list(output_dir.glob(".haute-training-*"))


def test_publication_rejects_model_name_not_declared_by_request(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    staged_output = artifact_root / "output"
    staged_output.mkdir(parents=True)
    model_path = staged_output / "wrong.cbm"
    contract_path = staged_output / model_contract_filename("wrong")
    model_path.write_bytes(b"model")
    contract_path.write_text('{"schema_version": 1}', encoding="utf-8")
    manifest = WorkerResultManifest(
        metadata={},
        artifacts=(
            build_artifact_manifest(
                artifact_root=artifact_root,
                path=model_path,
                kind="model",
                lifetime="staged",
            ),
            build_artifact_manifest(
                artifact_root=artifact_root,
                path=contract_path,
                kind="feature_contract",
                lifetime="staged",
            ),
        ),
    )
    output_dir = tmp_path / "outputs"

    with pytest.raises(WorkerProtocolError, match="requested name"):
        _publish_training_artifacts(
            manifest,
            artifact_root=artifact_root,
            output_root=output_dir,
            job_id="job-1",
            expected_model_name="quoted",
        )

    assert not output_dir.exists()
    assert model_path.exists()
    assert contract_path.exists()


@pytest.mark.parametrize(
    ("artifacts", "message"),
    [
        ("duplicate", "Duplicate training artifact kind"),
        ("durable", "must have staged lifetime"),
        ("incomplete", "exactly one model and feature contract"),
        ("invalid_path", "not in the staged output"),
    ],
)
def test_publication_rejects_invalid_artifact_manifests(
    tmp_path: Path, artifacts: str, message: str
) -> None:
    artifact_root, _staged_output, complete = _staged_training_manifest(tmp_path)
    model, contract = complete.artifacts
    if artifacts == "duplicate":
        malformed = WorkerResultManifest(metadata={}, artifacts=(model, model, contract))
    elif artifacts == "durable":
        malformed = WorkerResultManifest(
            metadata={},
            artifacts=(
                build_artifact_manifest(
                    artifact_root=artifact_root,
                    path=artifact_root / model.relative_path,
                    kind="model",
                    lifetime="durable",
                ),
                contract,
            ),
        )
    elif artifacts == "incomplete":
        malformed = WorkerResultManifest(metadata={}, artifacts=(model,))
    else:
        malformed = WorkerResultManifest(
            metadata={},
            artifacts=(
                type(model)(
                    kind=model.kind,
                    relative_path="elsewhere/quoted.cbm",
                    size_bytes=model.size_bytes,
                    sha256=model.sha256,
                    lifetime=model.lifetime,
                ),
                contract,
            ),
        )

    with pytest.raises(WorkerProtocolError, match=message):
        _publish_training_artifacts(
            malformed,
            artifact_root=artifact_root,
            output_root=tmp_path / "outputs",
            job_id="job-1",
            expected_model_name="quoted",
        )


def test_publication_warns_about_legacy_contract_and_rejects_backup_collision(
    tmp_path: Path,
) -> None:
    artifact_root, _staged_output, manifest = _staged_training_manifest(tmp_path)
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    legacy_contract = output_root / "feature_contract.json"
    legacy_contract.write_bytes(b"legacy")
    final_model = output_root / "quoted.cbm"
    final_model.write_bytes(b"old-model")
    backup = output_root / ".quoted.cbm.job-1.haute-backup"
    backup.write_bytes(b"existing backup")
    warning = patch("haute.routes._train_service.logger.warning")

    with warning as logger_warning, pytest.raises(FileExistsError, match="backup already exists"):
        _publish_training_artifacts(
            manifest,
            artifact_root=artifact_root,
            output_root=output_root,
            job_id="job-1",
            expected_model_name="quoted",
        )

    logger_warning.assert_called_once_with(
        "legacy_shared_feature_contract_present",
        legacy_path=str(legacy_contract),
        per_model_path=str(output_root / model_contract_filename("quoted")),
        model_name="quoted",
    )
    assert backup.read_bytes() == b"existing backup"
    assert final_model.read_bytes() == b"old-model"
    assert (artifact_root / "output" / "quoted.cbm").exists()


def test_publication_rolls_back_pair_when_second_staged_artifact_fails(
    tmp_path: Path,
) -> None:
    artifact_root, staged_output, manifest = _staged_training_manifest(tmp_path)
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    final_model = output_root / "quoted.cbm"
    final_contract = output_root / model_contract_filename("quoted")
    final_model.write_bytes(b"old-model")
    final_contract.write_bytes(b"old-contract")
    original_replace = os.replace
    staged_contract = staged_output / model_contract_filename("quoted")

    def fail_second_publication(source: Path | str, destination: Path | str) -> None:
        if Path(source) == staged_contract and Path(destination) == final_contract:
            raise OSError("second artifact cannot be published")
        original_replace(source, destination)

    with patch("haute.routes._train_service.os.replace", side_effect=fail_second_publication):
        with pytest.raises(OSError, match="second artifact"):
            _publish_training_artifacts(
                manifest,
                artifact_root=artifact_root,
                output_root=output_root,
                job_id="job-1",
                expected_model_name="quoted",
            )

    assert final_model.read_bytes() == b"old-model"
    assert final_contract.read_bytes() == b"old-contract"
    assert not (output_root / ".quoted.cbm.job-1.haute-backup").exists()
    assert not (output_root / f".{final_contract.name}.job-1.haute-backup").exists()
    assert not (staged_output / "quoted.cbm").exists()
    assert staged_contract.exists()


def test_publication_keeps_new_pair_when_backup_deletion_is_denied(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    staged_output = artifact_root / "output"
    staged_output.mkdir(parents=True)
    model_path = staged_output / "quoted.cbm"
    contract_path = staged_output / model_contract_filename("quoted")
    model_path.write_bytes(b"new-model")
    contract_path.write_bytes(b"new-contract")
    manifest = WorkerResultManifest(
        metadata={},
        artifacts=(
            build_artifact_manifest(
                artifact_root=artifact_root,
                path=model_path,
                kind="model",
                lifetime="staged",
            ),
            build_artifact_manifest(
                artifact_root=artifact_root,
                path=contract_path,
                kind="feature_contract",
                lifetime="staged",
            ),
        ),
    )
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    final_model = output_dir / "quoted.cbm"
    final_contract = output_dir / model_contract_filename("quoted")
    final_model.write_bytes(b"old-model")
    final_contract.write_bytes(b"old-contract")
    original_unlink = Path.unlink

    def deny_backup_unlink(path: Path, *args, **kwargs) -> None:
        if path.name.endswith(".haute-backup"):
            raise PermissionError("backup is temporarily locked")
        original_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", autospec=True, side_effect=deny_backup_unlink):
        _publish_training_artifacts(
            manifest,
            artifact_root=artifact_root,
            output_root=output_dir,
            job_id="job-1",
            expected_model_name="quoted",
        )

    assert final_model.read_bytes() == b"new-model"
    assert final_contract.read_bytes() == b"new-contract"
    assert (output_dir / ".quoted.cbm.job-1.haute-backup").exists()
    assert (output_dir / f".{model_contract_filename('quoted')}.job-1.haute-backup").exists()


def test_train_service_keeps_completed_status_when_artifact_cleanup_is_denied(
    tmp_path: Path,
) -> None:
    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    output_dir = tmp_path / "outputs"
    prepared = tmp_path / "prepared.parquet"
    prepared.write_bytes(b"prepared")
    released: list[bool] = []
    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        admission_release=lambda: released.append(True),
    )
    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "start_time": time.monotonic(),
            "timeout": 60,
        }
    )
    import shutil

    original_rmtree = shutil.rmtree

    def deny_artifact_cleanup(path, *args, **kwargs) -> None:
        if Path(path).name.startswith(".haute-training-"):
            raise PermissionError("artifact directory is temporarily locked")
        original_rmtree(path, *args, **kwargs)

    with (
        patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob),
        patch("haute.routes._train_service.shutil.rmtree", side_effect=deny_artifact_cleanup),
    ):
        thread = service._launch_background(
            job_id,
            "quoted",
            {
                "name": "quoted",
                "target": "y",
                "algorithm": "catboost",
                "loss_function": "RMSE",
                "output_dir": str(output_dir),
            },
            {"iterations": 2},
            str(prepared),
            None,
            10,
            execution_context=context,
        )
        assert thread is not None
        thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert (output_dir / "quoted.cbm").read_bytes() == b"model"
    assert (output_dir / model_contract_filename("quoted")).exists()
    assert not prepared.exists()
    assert released == [True]
    assert list(output_dir.glob(".haute-training-*"))


def test_parent_worker_cleanup_reports_all_failures_once(tmp_path: Path) -> None:
    service = TrainService(JobStore())
    tmp_parquet = tmp_path / "prepared.parquet"
    tmp_parquet.write_bytes(b"prepared")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    def fail_admission_release() -> None:
        raise RuntimeError("admission release failed")

    context = ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        admission_release=fail_admission_release,
    )
    cleanup = service._parent_worker_cleanup(
        "job-1",
        execution_context=context,
        tmp_parquet=tmp_parquet,
        artifact_root=artifact_root,
    )

    with (
        patch.object(
            service._training_jobs,
            "release",
            side_effect=RuntimeError("registry release failed"),
        ) as release,
        patch(
            "haute.routes._train_service.shutil.rmtree",
            side_effect=OSError("artifact cleanup failed"),
        ) as rmtree,
    ):
        with pytest.raises(RuntimeError, match="registry release failed") as raised:
            cleanup()
        cleanup()

    assert getattr(raised.value, "__notes__", []) == [
        "Additional cleanup failure: admission release failed",
        "Additional cleanup failure: artifact cleanup failed",
    ]
    assert not tmp_parquet.exists()
    assert artifact_root.exists()
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
        {
            "status": "running",
            "job_type": "training",
            "start_time": time.monotonic(),
            "timeout": 60,
        }
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
        },
        {"iterations": 2},
        str(prepared),
        None,
        10,
        execution_context=context,
    )

    assert thread is None
    assert calls == []
    assert not prepared.exists()
    assert released == [True]
