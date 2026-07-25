from __future__ import annotations

import pickle
import time
from pathlib import Path
from unittest.mock import patch

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
from haute.modelling._training_job import TrainResult, model_contract_filename
from haute.routes._job_store import JobStore
from haute.routes._train_service import (
    TrainService,
    _publish_training_artifacts,
    _run_training_process_job,
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
