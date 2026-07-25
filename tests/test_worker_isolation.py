from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    process_memory_caps_supported,
    resolve_worker_memory_enforcement,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute._worker_protocol import (
    WorkerFailurePayload,
    WorkerProgressEvent,
    WorkerRemoteFailureError,
    WorkerRequest,
    WorkerResultManifest,
)
from haute.routes._background_jobs import (
    IsolatedJobSupervisor,
    SupervisorInfrastructureError,
)
from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import JobStore


def _return_payload(left: int, right: int) -> dict[str, int]:
    return {"sum": left + right, "pid": os.getpid()}


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


def _crash_process(exit_code: int) -> None:
    os._exit(exit_code)


def _sleep_for(seconds: float) -> None:
    time.sleep(seconds)


def _current_address_space_limit() -> tuple[int, int]:
    import resource

    return tuple(int(value) for value in resource.getrlimit(resource.RLIMIT_AS))


def test_isolated_worker_returns_picklable_value() -> None:
    result = run_isolated_worker(_return_payload, 2, 3)

    assert result["sum"] == 5
    assert result["pid"] != os.getpid()


def test_isolated_worker_reports_remote_exception() -> None:
    with pytest.raises(IsolatedWorkerRemoteError) as exc_info:
        run_isolated_worker(_raise_value_error, "bad input")

    assert exc_info.value.terminal_reason == "error"
    assert exc_info.value.remote_type == "ValueError"
    assert "bad input" in str(exc_info.value)
    assert "ValueError: bad input" in exc_info.value.remote_traceback


def test_isolated_worker_reports_crash_without_killing_parent() -> None:
    with pytest.raises(IsolatedWorkerCrashedError) as exc_info:
        run_isolated_worker(_crash_process, 23)

    assert exc_info.value.terminal_reason == "error"
    assert exc_info.value.exitcode == 23
    assert 1 + 1 == 2


def test_cleanup_runs_when_worker_fails(tmp_path: Path) -> None:
    temp_dir = tmp_path / "owned"
    temp_dir.mkdir()
    cleaned: list[str] = []

    def cleanup() -> None:
        cleaned.append("yes")
        shutil.rmtree(temp_dir)

    with pytest.raises(IsolatedWorkerRemoteError):
        run_isolated_worker(
            _raise_value_error,
            "primary failure",
            config=IsolatedWorkerConfig(cleanup_callbacks=(cleanup,)),
        )

    assert cleaned == ["yes"]
    assert not temp_dir.exists()


def test_timeout_terminates_worker_and_runs_cleanup(tmp_path: Path) -> None:
    temp_dir = tmp_path / "owned"
    temp_dir.mkdir()

    def cleanup() -> None:
        shutil.rmtree(temp_dir)

    with pytest.raises(IsolatedWorkerTimeoutError) as exc_info:
        run_isolated_worker(
            _sleep_for,
            5.0,
            config=IsolatedWorkerConfig(timeout_seconds=0.1, cleanup_callbacks=(cleanup,)),
        )

    assert exc_info.value.terminal_reason == "timed_out"
    assert not temp_dir.exists()


def test_stop_reason_terminates_worker_preserves_reason_and_runs_cleanup(
    tmp_path: Path,
) -> None:
    temp_dir = tmp_path / "owned"
    temp_dir.mkdir()

    def cleanup() -> None:
        shutil.rmtree(temp_dir)

    with pytest.raises(IsolatedWorkerStoppedError) as exc_info:
        run_isolated_worker(
            _sleep_for,
            5.0,
            config=IsolatedWorkerConfig(
                cleanup_callbacks=(cleanup,),
                stop_reason=lambda: "cancelled",
            ),
        )

    assert exc_info.value.terminal_reason == "cancelled"
    assert not temp_dir.exists()


def test_required_memory_cap_fails_loudly_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("haute._worker_isolation.process_memory_caps_supported", lambda: False)

    with pytest.raises(IsolatedWorkerMemoryLimitUnsupportedError):
        run_isolated_worker(
            _return_payload,
            1,
            2,
            config=IsolatedWorkerConfig(
                memory_limit_bytes=512 * 1024 * 1024,
                require_memory_limit=True,
            ),
        )


@pytest.mark.skipif(
    not process_memory_caps_supported(),
    reason="worker address-space caps are only available on platforms with resource.RLIMIT_AS",
)
def test_memory_cap_is_applied_inside_child_process() -> None:
    limit = 512 * 1024 * 1024

    soft, hard = run_isolated_worker(
        _current_address_space_limit,
        config=IsolatedWorkerConfig(
            memory_limit_bytes=limit,
            require_memory_limit=True,
        ),
    )

    assert soft == limit
    assert hard == limit


def test_worker_memory_enforcement_defaults_to_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", raising=False)

    assert resolve_worker_memory_enforcement() == "best_effort"
    config = worker_config_for_memory_policy(memory_limit_bytes=123)
    assert config.memory_limit_bytes == 123
    assert config.require_memory_limit is False


def test_required_worker_memory_enforcement_sets_required_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")

    config = worker_config_for_memory_policy(memory_limit_bytes=123)

    assert config.require_memory_limit is True


def test_required_worker_memory_enforcement_requires_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")

    with pytest.raises(RuntimeError, match="requires a configured memory limit"):
        worker_config_for_memory_policy(memory_limit_bytes=None)


def test_direct_required_worker_config_requires_a_limit() -> None:
    with pytest.raises(ValueError, match="needs a configured memory limit"):
        IsolatedWorkerConfig(require_memory_limit=True)


def test_unknown_worker_memory_enforcement_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "sometimes")

    with pytest.raises(RuntimeError, match="HAUTE_WORKER_MEMORY_ENFORCEMENT"):
        resolve_worker_memory_enforcement()


def test_isolated_job_supervisor_records_completed_result() -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))

    thread = supervisor.launch(job_id, _return_payload, 2, 4)
    thread.join(timeout=10)

    assert not thread.is_alive()
    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["terminal_reason"] == "completed"
    assert job["result"]["sum"] == 6


def test_isolated_job_supervisor_records_remote_failure() -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))

    thread = supervisor.launch(job_id, _raise_value_error, "bad input")
    thread.join(timeout=10)

    assert not thread.is_alive()
    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert job["terminal_reason"] == "error"
    assert job["worker_error_type"] == "ValueError"
    assert "bad input" in job["message"]


def test_isolated_job_supervisor_terminalizes_unexpected_parent_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))
    parent_error = RuntimeError("parent isolation bug")
    reported: list[threading.ExceptHookArgs] = []

    def raise_parent_error(*args: object, **kwargs: object) -> object:
        raise parent_error

    monkeypatch.setattr(threading, "excepthook", reported.append)
    monkeypatch.setattr("haute.routes._background_jobs.run_isolated_worker", raise_parent_error)
    thread = supervisor.launch(job_id, _return_payload, 2, 4)
    thread.join_and_raise(timeout=10)

    assert not thread.is_alive()
    assert thread.infrastructure_failure is None
    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert job["terminal_reason"] == "error"
    assert job["worker_error_class"] == "RuntimeError"
    assert job["supervisor_error_class"] == "RuntimeError"
    assert job["message"] == "Unexpected isolated worker supervisor failure."
    assert "parent isolation bug" not in str(job)
    assert len(reported) == 1
    assert reported[0].exc_value is parent_error


def test_isolated_job_supervisor_records_stopped_reason_and_runs_cleanup(
    tmp_path: Path,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))
    temp_dir = tmp_path / "owned"
    temp_dir.mkdir()

    def cleanup() -> None:
        shutil.rmtree(temp_dir)

    thread = supervisor.launch(
        job_id,
        _sleep_for,
        5.0,
        config=IsolatedWorkerConfig(
            cleanup_callbacks=(cleanup,),
            stop_reason=lambda: "cancelled",
        ),
    )
    thread.join(timeout=10)

    assert not thread.is_alive()
    job = store.require_job(job_id)
    assert job["status"] == "cancelled"
    assert job["terminal_reason"] == "cancelled"
    assert not temp_dir.exists()


def test_isolated_job_supervisor_preserves_higher_precedence_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    lifecycle = JobLifecycle(store)
    assert lifecycle.transition(job_id, to="timed_out") is not None
    supervisor = IsolatedJobSupervisor(lifecycle)
    reported: list[threading.ExceptHookArgs] = []

    def fail_late(*_args, **_kwargs):
        raise RuntimeError("late parent failure")

    monkeypatch.setattr(threading, "excepthook", reported.append)
    monkeypatch.setattr("haute.routes._background_jobs.run_isolated_worker", fail_late)

    thread = supervisor.launch(job_id, _return_payload, 1, 2)
    thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "timed_out"
    assert job["terminal_reason"] == "timed_out"
    assert thread.infrastructure_failure is None
    assert len(reported) == 1
    assert str(reported[0].exc_value) == "late parent failure"


def test_isolated_job_supervisor_rejects_incoherent_existing_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "cancelled"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))
    monkeypatch.setattr(
        "haute.routes._background_jobs.run_isolated_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("late")),
    )

    thread = supervisor.launch(job_id, _return_payload, 1, 2)
    thread.join(timeout=10)

    assert isinstance(thread.infrastructure_failure, SupervisorInfrastructureError)
    with pytest.raises(SupervisorInfrastructureError, match="incoherent"):
        thread.join_and_raise()


def test_isolated_job_supervisor_exposes_terminal_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    lifecycle = JobLifecycle(store)
    supervisor = IsolatedJobSupervisor(lifecycle)

    monkeypatch.setattr(
        "haute.routes._background_jobs.run_isolated_worker",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        JobLifecycle,
        "transition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("store unavailable")),
    )

    thread = supervisor.launch(job_id, _return_payload, 1, 2)
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert isinstance(thread.infrastructure_failure, SupervisorInfrastructureError)
    assert store.require_job(job_id)["status"] == "running"
    with pytest.raises(SupervisorInfrastructureError, match="store unavailable"):
        thread.join_and_raise()


def test_protocol_supervisor_publishes_parent_fields_and_progress(tmp_path) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    events: list[WorkerProgressEvent] = []
    cleaned: list[str] = []

    def protocol_runner(_function, request, **kwargs):
        event = WorkerProgressEvent(0, 0.5, "Halfway", "phase", {})
        kwargs["on_progress"](event)
        assert request.request_id == job_id
        return WorkerResultManifest(metadata={"answer": 42})

    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=protocol_runner,
    )
    thread = supervisor.launch_protocol(
        job_id,
        lambda *_args: None,
        WorkerRequest(job_id, "test", {}),
        artifact_root=tmp_path,
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        on_progress=events.append,
        completed_fields=lambda result: {"answer": result.metadata["answer"]},
        on_finished=lambda: cleaned.append("done"),
    )
    thread.join_and_raise(timeout=10)

    assert [event.message for event in events] == ["Halfway"]
    assert cleaned == ["done"]
    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["terminal_reason"] == "completed"
    assert job["answer"] == 42


def test_protocol_supervisor_preserves_declared_failure_fields_and_cleanup(
    tmp_path,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})

    def protocol_runner(*_args, **_kwargs):
        raise WorkerRemoteFailureError(
            WorkerFailurePayload(
                terminal_reason="memory_limited",
                error_type="ExecutionMemoryLimitExceededError",
                message="fit exceeded memory",
                traceback="worker traceback",
                fields={"error_code": "memory_limit", "http_status_code": 507},
            )
        )

    def cleanup() -> None:
        raise OSError("temporary input could not be removed")

    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=protocol_runner,
    )
    thread = supervisor.launch_protocol(
        job_id,
        lambda *_args: None,
        WorkerRequest(job_id, "test", {}),
        artifact_root=tmp_path,
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        on_finished=cleanup,
    )
    thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["error_code"] == "memory_limit"
    assert job["http_status_code"] == 507
    assert job["cleanup_error_class"] == "OSError"
    assert "temporary input could not be removed" in job["cleanup_error"]


def test_protocol_supervisor_cleanup_failure_converts_success_to_error(tmp_path) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})

    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=lambda *_args, **_kwargs: WorkerResultManifest(metadata={}),
    )
    thread = supervisor.launch_protocol(
        job_id,
        lambda *_args: None,
        WorkerRequest(job_id, "test", {}),
        artifact_root=tmp_path,
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        on_finished=lambda: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert job["terminal_reason"] == "error"
    assert job["supervisor_error_class"] == "OSError"
    assert "cleanup failed" in job["message"]
