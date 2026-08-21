from __future__ import annotations

import asyncio
import os
import pickle
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import haute._worker_isolation as isolation
from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerHostError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStartError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTerminationError,
    IsolatedWorkerTimeoutError,
    _create_worker_rss_watchdog,
    _terminate_process,
    address_space_caps_supported,
    create_worker_queue,
    ensure_spawnable_interpreter,
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
from haute.routes._isolated_worker_async import (
    WorkerCancellationGate,
    run_cancellable_worker_transaction,
    run_isolated_worker_async,
)
from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import JobStore


def _return_payload(left: int, right: int) -> dict[str, int]:
    return {"sum": left + right, "pid": os.getpid()}


def _return_large_payload(size: int) -> bytes:
    return b"x" * size


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


def _crash_process(exit_code: int) -> None:
    os._exit(exit_code)


def _sleep_for(seconds: float) -> None:
    time.sleep(seconds)


class _EntrypointQueue:
    """Small child-queue fake that records its explicit feeder shutdown."""

    def __init__(self) -> None:
        self.values: list[bytes] = []
        self.closed = 0
        self.joined = 0

    def put(self, value: bytes) -> None:
        self.values.append(value)

    def get_nowait(self) -> bytes:
        return self.values.pop(0)

    def close(self) -> None:
        self.closed += 1

    def join_thread(self) -> None:
        self.joined += 1


def _current_native_memory_limit() -> tuple[str | None, int, int]:
    import resource

    from haute._native_memory_limit import current_native_memory_backend

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    return current_native_memory_backend(), int(soft), int(hard)


def test_worker_cancellation_gate_rejects_publication_after_cancellation() -> None:
    gate = WorkerCancellationGate()

    assert not gate.is_set()
    gate.request()
    assert gate.is_set()
    with pytest.raises(IsolatedWorkerStoppedError) as exc_info:
        with gate.publication_guard():
            raise AssertionError("publication body must not run")

    assert exc_info.value.terminal_reason == "cancelled"


def test_worker_cancellation_gate_serializes_request_after_publication() -> None:
    gate = WorkerCancellationGate()
    request_started = threading.Event()

    def request_cancellation() -> None:
        request_started.set()
        gate.request()

    with gate.publication_guard():
        requester = threading.Thread(target=request_cancellation)
        requester.start()
        assert request_started.wait(timeout=1.0)
        assert requester.is_alive()
        assert not gate.is_set()

    requester.join(timeout=1.0)
    assert not requester.is_alive()
    assert gate.is_set()


async def test_cancellable_worker_transaction_returns_success() -> None:
    def transaction(gate: WorkerCancellationGate) -> int:
        assert not gate.is_set()
        return 7

    assert await run_cancellable_worker_transaction(transaction, task_name="test-supervisor") == 7


async def test_cancellable_worker_transaction_drains_before_reraising_cancellation() -> None:
    started = threading.Event()
    finalized = threading.Event()

    def transaction(gate: WorkerCancellationGate) -> None:
        started.set()
        while not gate.is_set():
            time.sleep(0.001)
        time.sleep(0.01)
        finalized.set()
        raise IsolatedWorkerStoppedError(terminal_reason="cancelled")

    task = asyncio.create_task(
        run_cancellable_worker_transaction(transaction, task_name="test-supervisor")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert finalized.is_set()


async def test_cancellable_worker_transaction_notes_non_cancellation_finalization_error() -> None:
    started = threading.Event()

    def transaction(gate: WorkerCancellationGate) -> None:
        started.set()
        while not gate.is_set():
            time.sleep(0.001)
        raise RuntimeError("finalization failed")

    task = asyncio.create_task(
        run_cancellable_worker_transaction(transaction, task_name="test-supervisor")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task
    assert any("finalization failed" in note for note in exc_info.value.__notes__)


async def test_cancellable_worker_transaction_tolerates_repeated_task_cancellation() -> None:
    started = threading.Event()
    cancellation_observed = threading.Event()
    release = threading.Event()

    def transaction(gate: WorkerCancellationGate) -> None:
        started.set()
        while not gate.is_set():
            time.sleep(0.001)
        cancellation_observed.set()
        assert release.wait(timeout=1.0)
        raise IsolatedWorkerStoppedError(terminal_reason="cancelled")

    task = asyncio.create_task(
        run_cancellable_worker_transaction(transaction, task_name="test-supervisor")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()
    assert await asyncio.to_thread(cancellation_observed.wait, 1.0)
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancellable_worker_transaction_handles_result_ready_at_cancel_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BoundaryGate:
        def request(self) -> None:
            release.set()
            assert finished.wait(timeout=1.0)

    def transaction(_gate: BoundaryGate) -> int:
        started.set()
        assert release.wait(timeout=1.0)
        finished.set()
        return 7

    monkeypatch.setattr(
        "haute.routes._isolated_worker_async.WorkerCancellationGate",
        BoundaryGate,
    )
    task = asyncio.create_task(
        run_cancellable_worker_transaction(
            transaction,  # type: ignore[arg-type]
            task_name="test-supervisor",
        )
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancellable_worker_transaction_notes_non_cancelled_stop() -> None:
    started = threading.Event()

    def transaction(gate: WorkerCancellationGate) -> None:
        started.set()
        while not gate.is_set():
            time.sleep(0.001)
        raise IsolatedWorkerStoppedError(terminal_reason="superseded")

    task = asyncio.create_task(
        run_cancellable_worker_transaction(transaction, task_name="test-supervisor")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task
    assert "isolated worker stopped as superseded" in exc_info.value.__notes__


async def test_run_isolated_worker_async_composes_configured_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[object] = []

    def fake_run(
        _function: object, *_args: object, config: IsolatedWorkerConfig, **_kwargs: object
    ):
        assert config.stop_reason is not None
        observed.append(config.stop_reason())
        return "done"

    monkeypatch.setattr("haute.routes._isolated_worker_async.run_isolated_worker", fake_run)

    assert (
        await run_isolated_worker_async(
            _return_payload,
            1,
            2,
            config=IsolatedWorkerConfig(
                process_name="test-worker",
                stop_reason=lambda: "superseded",
            ),
        )
        == "done"
    )
    assert observed == ["superseded"]


async def test_run_isolated_worker_async_propagates_route_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()

    def fake_run(
        _function: object, *_args: object, config: IsolatedWorkerConfig, **_kwargs: object
    ) -> None:
        assert config.stop_reason is not None
        started.set()
        while config.stop_reason() is None:
            time.sleep(0.001)
        raise IsolatedWorkerStoppedError(terminal_reason=config.stop_reason() or "missing")

    monkeypatch.setattr("haute.routes._isolated_worker_async.run_isolated_worker", fake_run)
    task = asyncio.create_task(
        run_isolated_worker_async(
            _return_payload,
            1,
            2,
            config=IsolatedWorkerConfig(process_name="test-worker"),
        )
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_isolated_worker_async_uses_no_stop_reason_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        _function: object, *_args: object, config: IsolatedWorkerConfig, **_kwargs: object
    ):
        assert config.stop_reason is not None
        assert config.stop_reason() is None
        return "done"

    monkeypatch.setattr("haute.routes._isolated_worker_async.run_isolated_worker", fake_run)

    assert (
        await run_isolated_worker_async(
            _return_payload,
            1,
            2,
            config=IsolatedWorkerConfig(process_name="test-worker"),
        )
        == "done"
    )


def test_isolated_worker_returns_picklable_value() -> None:
    result = run_isolated_worker(_return_payload, 2, 3)

    assert result["sum"] == 5
    assert result["pid"] != os.getpid()


def test_isolated_worker_drains_large_result_before_joining_child() -> None:
    result = run_isolated_worker(
        _return_large_payload,
        8 * 1024 * 1024,
        config=IsolatedWorkerConfig(timeout_seconds=5),
    )

    assert len(result) == 8 * 1024 * 1024
    assert result[:1] == b"x"


def test_unexpected_parent_failure_terminates_child_and_runs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQueue:
        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    class FakeProcess:
        exitcode = None

        def __init__(self, **_kwargs: object) -> None:
            self.alive = True
            self.terminated = False

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def join(self, timeout: float | None = None) -> None:
            pass

    class FakeContext:
        process: FakeProcess | None = None

        def Queue(self, maxsize: int) -> FakeQueue:  # noqa: N802 - multiprocessing API
            return FakeQueue()

        def Process(self, **kwargs: object) -> FakeProcess:  # noqa: N802 - multiprocessing API
            self.process = FakeProcess(**kwargs)
            return self.process

    context = FakeContext()
    cleaned: list[str] = []
    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: context)
    monkeypatch.setattr(
        "haute._worker_isolation._wait_for_worker",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("parent polling failed")),
    )

    with pytest.raises(RuntimeError, match="parent polling failed"):
        run_isolated_worker(
            _return_payload,
            1,
            2,
            config=IsolatedWorkerConfig(cleanup_callbacks=(lambda: cleaned.append("done"),)),
        )

    assert context.process is not None
    assert context.process.terminated
    assert not context.process.is_alive()
    assert cleaned == ["done"]


def test_start_failure_preserves_typed_error_and_runs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQueue:
        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    class UnstartedProcess:
        exitcode = None

        def start(self) -> None:
            raise OSError("spawn unavailable")

        def is_alive(self) -> bool:
            raise AssertionError("unstarted process has no child state")

        def join(self, timeout: float | None = None) -> None:
            pass

    class FakeContext:
        def Queue(self, maxsize: int) -> FakeQueue:  # noqa: N802 - multiprocessing API
            return FakeQueue()

        def Process(self, **kwargs: object) -> UnstartedProcess:  # noqa: N802
            return UnstartedProcess()

    cleaned: list[str] = []
    monkeypatch.setattr(
        "haute._worker_isolation.mp.get_context",
        lambda _method: FakeContext(),
    )

    with pytest.raises(IsolatedWorkerStartError, match="spawn unavailable"):
        run_isolated_worker(
            _return_payload,
            1,
            2,
            config=IsolatedWorkerConfig(cleanup_callbacks=(lambda: cleaned.append("done"),)),
        )

    assert cleaned == ["done"]


def test_one_shot_join_cleans_exact_dead_worker_native_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQueue:
        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    class Process:
        pid = 4321
        exitcode = 0

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            pass

    class Context:
        def Queue(self, maxsize: int) -> FakeQueue:  # noqa: N802
            return FakeQueue()

        def Process(self, **_kwargs: object) -> Process:  # noqa: N802
            return Process()

    cleaned: list[int] = []
    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: Context())
    monkeypatch.setattr("haute._worker_isolation._wait_for_worker", lambda *_args: ("ok", 7))
    monkeypatch.setattr("haute._worker_isolation.cleanup_private_cgroups_for_pid", cleaned.append)

    assert run_isolated_worker(lambda: 7) == 7
    assert cleaned == [4321]


def test_one_shot_rejects_queued_payload_when_child_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Queue:
        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    class Process:
        pid = 4321
        exitcode = 23

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            pass

    class Context:
        def Queue(self, maxsize: int) -> Queue:  # noqa: N802 - multiprocessing API
            return Queue()

        def Process(self, **_kwargs: object) -> Process:  # noqa: N802
            return Process()

    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: Context())
    monkeypatch.setattr("haute._worker_isolation._wait_for_worker", lambda *_args: ("ok", 7))

    with pytest.raises(IsolatedWorkerCrashedError) as exc_info:
        run_isolated_worker(lambda: 7)

    assert exc_info.value.exitcode == 23


def test_one_shot_notes_native_cleanup_failure_without_masking_remote_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Queue:
        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    class Process:
        pid = 4321
        exitcode = 0

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            pass

    class Context:
        def Queue(self, maxsize: int) -> Queue:  # noqa: N802 - multiprocessing API
            return Queue()

        def Process(self, **_kwargs: object) -> Process:  # noqa: N802
            return Process()

    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: Context())
    monkeypatch.setattr(
        "haute._worker_isolation._wait_for_worker",
        lambda *_args: ("error", ("ValueError", "bad", "traceback")),
    )
    monkeypatch.setattr(
        "haute._worker_isolation.cleanup_private_cgroups_for_pid",
        lambda _pid: (_ for _ in ()).throw(OSError("cgroup cleanup failed")),
    )

    with pytest.raises(IsolatedWorkerRemoteError, match="bad") as exc_info:
        run_isolated_worker(lambda: 7)

    assert (
        "native memory resource cleanup failed: cgroup cleanup failed" in exc_info.value.__notes__
    )


def test_terminate_process_fails_loudly_when_child_survives_kill() -> None:
    class StuckProcess:
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            pass

        def is_alive(self) -> bool:
            return True

    with pytest.raises(IsolatedWorkerTerminationError):
        _terminate_process(StuckProcess())  # type: ignore[arg-type]


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
    not address_space_caps_supported(),
    reason="worker address-space caps are only available on platforms with resource.RLIMIT_AS",
)
def test_native_memory_cap_is_active_inside_child_process() -> None:
    import resource

    limit = 512 * 1024 * 1024
    inherited_soft, inherited_hard = (
        int(value) for value in resource.getrlimit(resource.RLIMIT_AS)
    )

    backend, soft, hard = run_isolated_worker(
        _current_native_memory_limit,
        config=IsolatedWorkerConfig(
            memory_limit_bytes=limit,
            require_memory_limit=True,
        ),
    )

    assert backend in {"cgroup", "rlimit"}
    if backend == "cgroup":
        return

    # RLIMIT_AS is an address-space *growth* ceiling.  The installed soft
    # limit therefore includes the worker's pre-existing virtual-memory
    # baseline and must not be confused with the growth budget itself.
    infinity = int(resource.RLIM_INFINITY)
    assert hard == inherited_hard
    assert soft != infinity
    inherited_ceilings = [value for value in (inherited_soft, inherited_hard) if value != infinity]
    assert soft >= min(limit, *inherited_ceilings)
    for inherited_ceiling in inherited_ceilings:
        assert soft <= inherited_ceiling


def test_cross_platform_worker_rss_watchdog_enforces_growth_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    class _Process:
        pid = 42

    samples = iter([1_000, 1_051])
    monkeypatch.setattr(isolation_mod, "process_rss_bytes", lambda _pid: next(samples))
    monkeypatch.setattr(isolation_mod, "address_space_caps_supported", lambda: False)

    watchdog = _create_worker_rss_watchdog(
        _Process(),  # type: ignore[arg-type]
        memory_limit_bytes=50,
        require_memory_limit=True,
    )

    with pytest.raises(IsolatedWorkerMemoryLimitExceededError) as exc_info:
        watchdog.checkpoint()
    assert exc_info.value.rss_bytes == 1_051
    assert exc_info.value.rss_limit_bytes == 1_050


def test_worker_rss_watchdog_is_secondary_when_child_is_unobservable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    class _Process:
        pid = 42

    monkeypatch.setattr(isolation_mod, "process_rss_bytes", lambda _pid: None)
    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: True)
    watchdog = _create_worker_rss_watchdog(
        _Process(),  # type: ignore[arg-type]
        memory_limit_bytes=50,
        require_memory_limit=True,
    )
    assert watchdog.rss_limit_bytes is None


def test_process_memory_support_excludes_rss_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    monkeypatch.setattr(isolation_mod, "native_memory_caps_supported", lambda: False)
    assert process_memory_caps_supported() is False
    monkeypatch.setattr(isolation_mod, "native_memory_caps_supported", lambda: True)
    assert process_memory_caps_supported() is True


def test_worker_rss_watchdog_handles_absent_limits_and_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    no_limit = isolation_mod._WorkerRssWatchdog(None, None, False, False)
    no_limit.checkpoint()

    monkeypatch.setattr(isolation_mod, "process_rss_bytes", lambda _pid: None)
    isolation_mod._WorkerRssWatchdog(42, 100, False, False).checkpoint()
    isolation_mod._WorkerRssWatchdog(42, 100, True, True).checkpoint()
    with pytest.raises(IsolatedWorkerMemoryLimitUnsupportedError):
        isolation_mod._WorkerRssWatchdog(42, 100, True, False).checkpoint()

    monkeypatch.setattr(isolation_mod, "process_rss_bytes", lambda _pid: 99)
    isolation_mod._WorkerRssWatchdog(42, 100, True, False).checkpoint()


def test_worker_rss_watchdog_creation_covers_unobservable_and_missing_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    class Process:
        pid: int | None = None

    no_limit = _create_worker_rss_watchdog(
        Process(),  # type: ignore[arg-type]
        memory_limit_bytes=None,
        require_memory_limit=False,
    )
    assert no_limit.rss_limit_bytes is None

    with pytest.raises(IsolatedWorkerStartError, match="process id"):
        _create_worker_rss_watchdog(
            Process(),  # type: ignore[arg-type]
            memory_limit_bytes=50,
            require_memory_limit=False,
        )

    Process.pid = 42
    monkeypatch.setattr(isolation_mod, "process_rss_bytes", lambda _pid: None)
    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: False)
    optional = _create_worker_rss_watchdog(
        Process(),  # type: ignore[arg-type]
        memory_limit_bytes=50,
        require_memory_limit=False,
    )
    assert optional.rss_limit_bytes is None
    assert optional.address_space_cap_active is False

    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: True)
    required = _create_worker_rss_watchdog(
        Process(),  # type: ignore[arg-type]
        memory_limit_bytes=50,
        require_memory_limit=True,
    )
    assert required.rss_limit_bytes is None
    assert required.address_space_cap_active is True


@pytest.mark.parametrize(
    ("memory_limit", "caps_supported", "expected_limits"),
    [(None, True, []), (128, False, [128]), (128, True, [128])],
)
def test_isolated_entrypoint_applies_native_lease_before_callable(
    monkeypatch: pytest.MonkeyPatch,
    memory_limit: int | None,
    caps_supported: bool,
    expected_limits: list[int],
) -> None:
    import haute._worker_isolation as isolation_mod

    results = _EntrypointQueue()
    applied: list[int] = []
    monkeypatch.setattr(
        isolation_mod,
        "NativeMemoryLease",
        lambda: SimpleNamespace(
            backend=None,
            apply=lambda limit, **_kwargs: applied.append(limit),
            restore=lambda: None,
            close=lambda: None,
        ),
    )

    isolation_mod._isolated_worker_entrypoint(
        results,
        lambda value, *, increment: value + increment,
        (2,),
        {"increment": 3},
        memory_limit,
        caps_supported,
    )

    assert pickle.loads(results.get_nowait()) == ("ok", 5)
    assert applied == expected_limits


def test_isolated_entrypoint_serialises_child_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute._worker_isolation as isolation_mod

    results = _EntrypointQueue()
    monkeypatch.setattr(isolation_mod, "address_space_caps_supported", lambda: False)
    monkeypatch.setattr(
        isolation_mod,
        "NativeMemoryLease",
        lambda: SimpleNamespace(
            backend=None,
            apply=lambda *_args, **_kwargs: True,
            restore=lambda: None,
            close=lambda: None,
        ),
    )

    isolation_mod._isolated_worker_entrypoint(
        results,
        _raise_value_error,
        ("child boom",),
        {},
        128,
    )

    status, payload = pickle.loads(results.get_nowait())
    assert status == "error"
    error_type, message, remote_traceback = payload  # type: ignore[misc]
    assert error_type == "ValueError"
    assert message == "child boom"
    assert "ValueError: child boom" in remote_traceback


def test_isolated_entrypoint_exposes_native_backend_only_during_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._native_memory_limit as native
    import haute._worker_isolation as isolation_mod

    results = _EntrypointQueue()

    class Lease:
        backend = None

        def apply(self, *_args, **_kwargs):
            self.backend = "cgroup"
            return True

    monkeypatch.setattr(isolation_mod, "NativeMemoryLease", Lease)

    isolation_mod._isolated_worker_entrypoint(
        results,
        native.current_native_memory_backend,
        (),
        {},
        128,
        True,
    )

    assert pickle.loads(results.get_nowait()) == ("ok", "cgroup")
    assert native.current_native_memory_backend() is None


def test_isolated_entrypoint_leaves_native_lease_active_until_process_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    results = _EntrypointQueue()
    events: list[str] = []
    monkeypatch.setattr(
        isolation_mod,
        "NativeMemoryLease",
        lambda: SimpleNamespace(
            backend=None,
            apply=lambda *_args, **_kwargs: events.append("apply") or True,
            restore=lambda: events.append("restore"),
            close=lambda: events.append("close"),
        ),
    )

    isolation_mod._isolated_worker_entrypoint(results, lambda: "result", (), {}, 128, True)

    assert pickle.loads(results.get_nowait()) == ("ok", "result")
    assert events == ["apply"]
    assert (results.closed, results.joined) == (1, 1)


@pytest.mark.parametrize(
    ("current_limits", "expected"),
    [((-1, -1), (100, 100)), ((50, 80), (50, 80))],
)
def test_apply_address_space_limit_respects_existing_finite_caps(
    monkeypatch: pytest.MonkeyPatch,
    current_limits: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    import haute._worker_isolation as isolation_mod

    applied: list[tuple[int, tuple[int, int]]] = []
    fake_resource = SimpleNamespace(
        RLIMIT_AS=1,
        RLIM_INFINITY=-1,
        getrlimit=lambda _limit: current_limits,
        setrlimit=lambda limit, values: applied.append((limit, values)),
    )
    monkeypatch.setattr(isolation_mod.sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "resource", fake_resource)

    isolation_mod._apply_address_space_limit(100)

    assert applied == [(1, expected)]


def test_memory_limited_exitcode_classification_is_platform_independent() -> None:
    import haute._worker_isolation as isolation_mod

    assert isolation_mod._exitcode_looks_memory_limited(None, 10) is False
    assert isolation_mod._exitcode_looks_memory_limited(-9, None) is False
    assert isolation_mod._exitcode_looks_memory_limited(-9, 10) is True
    assert isolation_mod._exitcode_looks_memory_limited(-int(signal.SIGABRT), 10) is True
    assert isolation_mod._exitcode_looks_memory_limited(-7, 10) is False


def test_worker_memory_enforcement_defaults_to_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", raising=False)

    assert resolve_worker_memory_enforcement() == "required"
    config = worker_config_for_memory_policy(memory_limit_bytes=123)
    assert config.memory_limit_bytes == 123
    assert config.require_memory_limit is True


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


def test_protocol_supervisor_cleanup_failure_preserves_committed_success(tmp_path) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})

    def fail_cleanup() -> None:
        raise OSError("cleanup failed")

    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=lambda *_args, **_kwargs: WorkerResultManifest(metadata={}),
    )
    with patch("haute.routes._background_jobs.logger.error") as log_error:
        thread = supervisor.launch_protocol(
            job_id,
            lambda *_args: None,
            WorkerRequest(job_id, "test", {}),
            artifact_root=tmp_path,
            artifact_kinds=frozenset(),
            max_artifact_size_bytes=0,
            completed_fields=lambda _result: {"published_path": "models/fitted.joblib"},
            on_finished=fail_cleanup,
        )
        thread.join_and_raise(timeout=10)

    log_error.assert_called_once_with(
        "isolated_job_cleanup_failed",
        job_id=job_id,
        terminal_reason="completed",
        error="cleanup failed",
        error_type="OSError",
        exc_info=True,
    )

    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["terminal_reason"] == "completed"
    assert job["published_path"] == "models/fitted.joblib"
    assert job["cleanup_error_class"] == "OSError"
    assert "cleanup failed" in job["cleanup_error"]


# ---------------------------------------------------------------------------
# Host process machinery — a dead multiprocessing resource tracker
# ---------------------------------------------------------------------------
#
# Measured on Databricks Apps: creating the very first worker queue raised
# BrokenPipeError from `resource_tracker.register`, because the tracker helper
# process was dead. Nothing about the job was wrong — no worker had started.


class _QueueContext:
    """Stands in for a multiprocessing context with a scripted Queue()."""

    def __init__(self, *outcomes: BaseException | None) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def Queue(self, maxsize: int = 0) -> object:  # noqa: N802 - mirrors mp API
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        return f"queue(maxsize={maxsize})"


class TestDeadResourceTrackerRecovery:
    def test_a_healthy_host_creates_the_queue_directly(self) -> None:
        ctx = _QueueContext()
        assert create_worker_queue(ctx, 1) == "queue(maxsize=1)"
        assert ctx.calls == 1

    def test_one_dead_tracker_is_survived(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The tracker is respawned and the job proceeds, not fails."""
        resets: list[int] = []
        monkeypatch.setattr(
            "haute._worker_isolation._reset_resource_tracker",
            lambda: resets.append(1),
        )
        ctx = _QueueContext(BrokenPipeError(32, "Broken pipe"), None)
        assert create_worker_queue(ctx, 4) == "queue(maxsize=4)"
        assert ctx.calls == 2
        assert resets == [1]  # the dead handle was dropped before retrying

    def test_a_tracker_that_stays_dead_names_the_host_problem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never an unexplained internal error: say the host cannot start work."""
        monkeypatch.setattr("haute._worker_isolation._reset_resource_tracker", lambda: None)
        ctx = _QueueContext(BrokenPipeError(32, "Broken pipe"), BrokenPipeError(32, "Broken pipe"))
        with pytest.raises(IsolatedWorkerHostError) as excinfo:
            create_worker_queue(ctx, 1)
        assert ctx.calls == 2  # bounded at one retry, never a spin
        message = str(excinfo.value)
        assert "Restart the app" in message
        assert "Broken pipe" not in message  # hand-authored, not raw errno text
        # Typed as a worker failure, so the supervisor reports THIS message
        # rather than its generic "unexpected supervisor failure".
        assert excinfo.value.terminal_reason == "error"

    def test_an_unrelated_error_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only a dead pipe means 'the host', so only that is recovered."""
        monkeypatch.setattr("haute._worker_isolation._reset_resource_tracker", lambda: None)
        ctx = _QueueContext(ValueError("bad maxsize"))
        with pytest.raises(ValueError):
            create_worker_queue(ctx, 1)
        assert ctx.calls == 1

    def test_diagnostics_describe_the_host_without_raising(self) -> None:
        """The diagnostic runs on a failure path; it must never add its own."""
        from haute._worker_isolation import _resource_tracker_diagnostics

        info = _resource_tracker_diagnostics()
        assert "executable" in info
        assert isinstance(info.get("executable_usable"), bool)

    def test_diagnostics_capture_tracker_status_and_available_resource_limits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import multiprocessing.resource_tracker as resource_tracker

        from haute._worker_isolation import _resource_tracker_diagnostics

        fake_tracker = SimpleNamespace(_pid=123, _fd=7)
        fake_resource = SimpleNamespace(
            RLIMIT_NPROC=1,
            RLIMIT_AS=3,
            getrlimit=lambda limit: (limit * 10, limit * 20),
        )
        monkeypatch.setattr(resource_tracker, "_resource_tracker", fake_tracker)
        monkeypatch.setattr(os, "waitpid", lambda pid, options: (pid, options), raising=False)
        monkeypatch.delattr(os, "WNOHANG", raising=False)
        monkeypatch.setitem(sys.modules, "resource", fake_resource)

        info = _resource_tracker_diagnostics()

        assert info["tracker_pid"] == 123
        assert info["tracker_reaped"] == 123
        assert info["tracker_exit_status"] == 1
        assert info["rlimit_nproc"] == (10, 20)
        assert info["rlimit_as"] == (30, 60)
        assert "rlimit_nofile" not in info


def test_worker_isolation_support_payload_and_cleanup_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "resource", SimpleNamespace())
    assert not address_space_caps_supported()
    monkeypatch.setitem(
        sys.modules,
        "resource",
        SimpleNamespace(RLIMIT_AS=1, setrlimit=lambda *_args: None),
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    assert not address_space_caps_supported()
    monkeypatch.setattr(sys, "platform", "linux")
    assert address_space_caps_supported()
    with pytest.raises(IsolatedWorkerCrashedError):
        isolation._decode_worker_payload(b"not pickle")
    with pytest.raises(IsolatedWorkerCrashedError):
        isolation._decode_worker_payload(pickle.dumps(("wrong", 1)))
    errors = isolation._run_cleanup_callbacks((lambda: (_ for _ in ()).throw(OSError("one")),))
    assert errors is not None and len(errors.errors) == 1


def test_worker_entrypoint_reports_native_setup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Queue:
        def __init__(self) -> None:
            self.actions: list[str] = []
            self.value: bytes | None = None

        def put(self, value: bytes) -> None:
            self.value = value
            self.actions.append("put")

        def close(self) -> None:
            self.actions.append("close")

        def join_thread(self) -> None:
            self.actions.append("join")

    queue = Queue()
    monkeypatch.setattr(
        isolation.NativeMemoryLease,
        "apply",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cap")),
    )
    isolation._isolated_worker_entrypoint(queue, lambda: 1, (), {}, 10, True)
    assert queue.actions == ["put", "close", "join"]
    assert pickle.loads(queue.value)[0] == "error"


def test_worker_payload_serialization_returns_remote_error_for_unpicklable_result() -> None:
    payload = isolation._serialise_worker_payload(("ok", lambda: None))
    status, evidence = pickle.loads(payload)
    assert status == "error"
    assert evidence[0] == "RuntimeError"


def test_watchdog_and_finalization_preserve_primary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    process = SimpleNamespace(pid=4)
    monkeypatch.setattr(isolation, "process_memory_caps_supported", lambda: False)
    monkeypatch.setattr(isolation, "process_rss_bytes", lambda _pid: None)
    with pytest.raises(IsolatedWorkerMemoryLimitUnsupportedError):
        isolation._create_worker_rss_watchdog(
            process, memory_limit_bytes=5, require_memory_limit=True
        )
    watchdog = isolation._WorkerRssWatchdog(4, 5, False, False)
    monkeypatch.setattr(isolation, "process_rss_bytes", lambda _pid: 6)
    with pytest.raises(IsolatedWorkerMemoryLimitExceededError):
        watchdog.checkpoint()


class TestSpawnableInterpreter:
    """The measured root cause: a relative sys.executable plus a chdir.

    Databricks Apps launches the app as ".venv/bin/python". multiprocessing
    exec's that path for the resource tracker and every worker, so once the
    hosted boot chdirs into the project directory, every spawn exec-fails
    (status 255) and the first queue write dies on a broken pipe.
    """

    def test_an_absolute_runnable_interpreter_is_left_alone(self) -> None:
        from multiprocessing import spawn

        before = spawn.get_executable()
        assert ensure_spawnable_interpreter() is None
        assert spawn.get_executable() == before

    def test_a_relative_interpreter_is_resolved_against_the_launch_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launch = tmp_path / "app"
        (launch / ".venv" / "bin").mkdir(parents=True)
        interpreter = launch / ".venv" / "bin" / "python"
        interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        interpreter.chmod(0o755)

        from multiprocessing import spawn

        original = spawn.get_executable()
        monkeypatch.chdir(launch)
        try:
            spawn.set_executable(".venv/bin/python")
            resolved = ensure_spawnable_interpreter()
            assert resolved == str(interpreter)
            # Absolute now, so it survives the chdir that used to break it.
            monkeypatch.chdir(tmp_path)
            assert os.access(resolved, os.X_OK)
        finally:
            spawn.set_executable(original)

    def test_a_relative_interpreter_after_the_chdir_falls_back_to_the_kernel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The safety net: cwd has already moved, so abspath cannot help."""
        if not Path("/proc/self/exe").exists():
            pytest.skip("/proc/self/exe is Linux-only")

        from multiprocessing import spawn

        original = spawn.get_executable()
        monkeypatch.chdir(tmp_path)
        try:
            spawn.set_executable("nowhere/bin/python")
            resolved = ensure_spawnable_interpreter()
            assert resolved is not None
            assert os.path.isabs(resolved)
            assert os.access(resolved, os.X_OK)
        finally:
            spawn.set_executable(original)
