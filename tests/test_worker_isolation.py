from __future__ import annotations

import os
import shutil
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
    run_isolated_worker,
)
from haute.routes._background_jobs import IsolatedJobSupervisor
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
