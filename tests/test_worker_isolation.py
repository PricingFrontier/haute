from __future__ import annotations

import asyncio
import importlib
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
from haute._project_context import context_thread, current_project
from haute._worker_failures import (
    WorkerCrashedError,
    WorkerHostError,
    WorkerMemoryLimitError,
    WorkerMemoryLimitUnsupportedError,
    WorkerRemoteError,
    WorkerStartError,
    WorkerStoppedError,
    WorkerTerminationError,
    WorkerTimeoutError,
    exitcode_looks_memory_limited,
    worker_failure_is_cap_unavailable,
    worker_failure_is_memory,
)
from haute._worker_isolation import (
    IsolatedWorkerConfig,
    _create_worker_rss_watchdog,
    _terminate_process,
    create_worker_queue,
    ensure_spawnable_interpreter,
    process_memory_caps_supported,
    resolve_worker_memory_enforcement,
    run_isolated_worker,
    start_process_with_environment,
    worker_config_for_memory_policy,
)
from haute._worker_protocol import (
    WorkerFailurePayload,
    WorkerProgressEvent,
    WorkerRemoteFailureError,
    WorkerRequest,
    WorkerResultManifest,
    run_worker_protocol,
)
from haute.routes._background_jobs import (
    CancellableJobRegistry,
    IsolatedJobSupervisor,
    SupervisorInfrastructureError,
    start_job_thread,
    stop_background_jobs,
)
from haute.routes._isolated_worker_async import (
    WorkerCancellationGate,
    run_cancellable_worker_transaction,
    run_isolated_worker_async,
)
from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import JobStore
from tests._source_files import source_files


def _return_payload(left: int, right: int) -> dict[str, int]:
    return {"sum": left + right, "pid": os.getpid()}


def _return_large_payload(size: int) -> bytes:
    return b"x" * size


def _allocate_native_column(size_bytes: int) -> int:
    import polars as pl

    return pl.select(pl.int_range(0, size_bytes // 8, dtype=pl.Int64).alias("x")).height


def _read_environment(name: str) -> str | None:
    return os.environ.get(name)


def _sum_protocol_worker(runtime, request):
    del runtime
    payload = request.payload
    return WorkerResultManifest(metadata={"sum": payload["left"] + payload["right"]})


def _failing_protocol_worker(runtime, request):
    del runtime
    raise ValueError(request.payload["message"])


def _launch(
    supervisor: IsolatedJobSupervisor,
    job_id: str,
    artifact_root: Path,
    function=_sum_protocol_worker,
    payload: object = None,
    **kwargs: object,
):
    """Launch *function* as the job's protocol worker, with no artifacts."""
    return supervisor.launch_protocol(
        job_id,
        function,
        WorkerRequest(job_id, "test", {"left": 2, "right": 4} if payload is None else payload),
        artifact_root=artifact_root,
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        **kwargs,
    )


def _raise_memory_refusal(kind: str) -> None:
    """Raise one of the two MemoryError subclasses Haute's own checks raise."""
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionMemoryLimitExceededError, ExecutionProfile

    if kind == "admission":
        raise ExecutionAdmissionError(
            "child",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=1,
            rss_at_admission_bytes=None,
            reason="refused in the child",
        )
    raise ExecutionMemoryLimitExceededError("child", rss_bytes=2, limit_bytes=1)


def _memory_refusal_protocol_worker(runtime, request):
    del runtime
    _raise_memory_refusal(request.payload["kind"])


def _sleeping_protocol_worker(runtime, request):
    del request
    runtime.emit_progress(progress=0.0, message="Sleeping", kind="phase")
    time.sleep(120)
    return WorkerResultManifest(metadata={})


def _quiet_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the server lifespan without its project, pool, watcher or reaper work."""
    import haute.deploy._config as deploy_config
    import haute.server as server

    for name in (
        "configure_logging",
        "configure_execution_telemetry",
        "recover_json_runtime_storage",
        "_ensure_pipeline_index",
        "_follow_pipeline_settings_chunk_rows",
        "start_interactive_worker_pool",
    ):
        monkeypatch.setattr(server, name, lambda: None)
    monkeypatch.setattr(deploy_config, "_load_env", lambda _path: None)
    monkeypatch.setattr(server, "_artifact_stale_seconds", lambda: 86_400)

    async def idle(*_args: object) -> None:
        await asyncio.Event().wait()

    async def no_reap(_stale_after_seconds: int) -> None:
        return None

    monkeypatch.setattr(server, "_watcher_forever", idle)
    monkeypatch.setattr(server, "_reap_stale_job_artifacts_in_background", no_reap)


def test_server_shutdown_cancels_a_running_job_and_leaves_no_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ROAD-WORKER-06: shutdown stops a sleeping protocol worker instead of joining it."""
    import multiprocessing

    import haute.server as server

    _quiet_lifespan(monkeypatch)
    store = JobStore()
    registry = CancellableJobRegistry()
    job_id = store.create_job({"status": "running"})
    registry.register_latest("shutdown", job_id)
    sleeping = threading.Event()
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))
    process_name = f"haute-shutdown-test-{job_id}"

    async def run_and_shut_down() -> float:
        async with server._lifespan(server.create_app(current_project())):
            supervisor.launch_protocol(
                job_id,
                _sleeping_protocol_worker,
                WorkerRequest(job_id, "sleep", {}),
                artifact_root=tmp_path,
                artifact_kinds=frozenset(),
                max_artifact_size_bytes=0,
                config=IsolatedWorkerConfig(
                    stop_reason=lambda: registry.cancellation_reason(job_id),
                    process_name=process_name,
                ),
                on_progress=lambda _event: sleeping.set(),
                on_finished=lambda: registry.release(job_id),
            )
            assert await asyncio.to_thread(sleeping.wait, 120)
            shutdown_started = time.monotonic()
        return time.monotonic() - shutdown_started

    try:
        elapsed = asyncio.run(run_and_shut_down())
        workers_left = [
            child for child in multiprocessing.active_children() if child.name == process_name
        ]
        job = store.require_job(job_id)

        assert elapsed < server._JOB_SHUTDOWN_TIMEOUT_SECONDS
        assert workers_left == []
        assert job["status"] == "cancelled"
        assert job["worker_error_class"] == "WorkerStoppedError"
    finally:
        # Without the shutdown under test, the worker would sleep on past this test.
        registry.cancel(job_id)
        stop_background_jobs(timeout_seconds=10)


def test_job_shutdown_stops_other_jobs_while_one_holds_its_registry() -> None:
    """A publication holding a registry delays only that registry's cancel: every other
    registry is cancelled at once, its jobs stop, and the shutdown keeps its bound."""
    held = CancellableJobRegistry()
    held_token, _ = held.register_latest("publishing", "publishing-job")
    free = CancellableJobRegistry()
    free_token, _ = free.register_latest("waiting", "waiting-job")
    publishing = threading.Event()
    release = threading.Event()

    def publish() -> None:
        with held.latest_publication("publishing-job"):
            publishing.set()
            release.wait(30)

    holder = context_thread(publish, name="haute-test-publishing-job", daemon=True)
    waiter = context_thread(
        free_token.event.wait, args=(30,), name="haute-test-waiting-job", daemon=True
    )
    start_job_thread(holder)
    start_job_thread(waiter)
    assert publishing.wait(10)
    try:
        started = time.monotonic()
        assert held.cancel_all(reason="cancelled") is False  # held: refused, never waited for
        assert time.monotonic() - started < 1
        still_running = stop_background_jobs(timeout_seconds=1.0)
        elapsed = time.monotonic() - started
        # Read from the tokens: the held registry's own lock is still taken.
        held_cancelled_while_publishing = held_token.cancelled
    finally:
        release.set()
        holder.join(10)
        waiter.join(10)

    assert free_token.cancelled
    assert "haute-test-waiting-job" not in still_running
    assert "haute-test-publishing-job" in still_running
    assert held_cancelled_while_publishing is False
    assert elapsed < 5
    stop_background_jobs(timeout_seconds=5)
    assert held_token.cancelled


def test_the_lifespan_logs_the_jobs_that_outlive_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import structlog.testing

    import haute.server as server

    monkeypatch.setattr(
        server, "stop_background_jobs", lambda *, timeout_seconds: ("haute-test-stuck-job",)
    )
    with structlog.testing.capture_logs() as logs:
        asyncio.run(server._stop_background_jobs())

    (record,) = [r for r in logs if r["event"] == "background_jobs_outlived_shutdown"]
    assert record["threads"] == ["haute-test-stuck-job"]
    assert record["timeout_s"] == server._JOB_SHUTDOWN_TIMEOUT_SECONDS


def test_job_shutdown_returns_at_its_bound_naming_the_jobs_still_running() -> None:
    release = threading.Event()
    thread = context_thread(
        release.wait, args=(30,), name="haute-test-unstoppable-job", daemon=True
    )
    start_job_thread(thread)
    try:
        started = time.monotonic()
        still_running = stop_background_jobs(timeout_seconds=0.2)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join(10)

    assert "haute-test-unstoppable-job" in still_running
    assert 0.2 <= elapsed < 5


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


def _crash_process(exit_code: int) -> None:
    os._exit(exit_code)


def _sleep_for(seconds: float) -> None:
    time.sleep(seconds)


def _go() -> threading.Event:
    """The parent's go-ahead, already given: an in-process entrypoint runs its job at once."""
    go = threading.Event()
    go.set()
    return go


class _EntrypointQueue:
    """Small child-queue fake that records its explicit feeder shutdown."""

    def __init__(self) -> None:
        self.values: list[bytes] = []
        self.closed = 0
        self.joined = 0

    def put(self, value: bytes) -> None:
        self.values.append(value)

    def get_nowait(self) -> bytes:
        """The next payload past the child's ready report."""
        value = self.values.pop(0)
        if pickle.loads(value)[0] == "ready":
            return self.values.pop(0)
        return value

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
    with pytest.raises(WorkerStoppedError) as exc_info:
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
        requester = context_thread(request_cancellation)
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
        raise WorkerStoppedError(terminal_reason="cancelled")

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
        raise WorkerStoppedError(terminal_reason="cancelled")

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
        raise WorkerStoppedError(terminal_reason="superseded")

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
        raise WorkerStoppedError(terminal_reason=config.stop_reason() or "missing")

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


@pytest.mark.parametrize("kind", ["admission", "checkpoint"])
@pytest.mark.parametrize("parent", ["one_shot", "protocol", "pool"])
def test_every_parent_reports_a_child_memory_refusal_as_memory_limited(
    parent: str, kind: str, tmp_path: Path
) -> None:
    """ROAD-WORKER-07: ExecutionAdmissionError and ExecutionMemoryLimitExceededError
    are MemoryErrors, so each parent reports them as memory, whatever their names."""
    from haute._interactive_workers import InteractiveWorkerPool
    from haute._worker_failures import WorkerRemoteError

    if parent == "one_shot":
        with pytest.raises(WorkerRemoteError) as raised:
            run_isolated_worker(_raise_memory_refusal, kind)
        assert worker_failure_is_memory(raised.value)
    elif parent == "protocol":
        with pytest.raises(WorkerRemoteError) as raised:
            run_worker_protocol(
                _memory_refusal_protocol_worker,
                WorkerRequest("memory", "test", {"kind": kind}),
                artifact_root=tmp_path,
                artifact_kinds=frozenset(),
                max_artifact_size_bytes=0,
            )
        assert worker_failure_is_memory(raised.value)
    else:
        pool = InteractiveWorkerPool(size=1, polars_threads=2)
        try:
            with pytest.raises(WorkerRemoteError) as raised:
                pool.run(_raise_memory_refusal, kind, affinity_key="memory", timeout_seconds=120)
        finally:
            pool.close()

    assert raised.value.terminal_reason == "memory_limited"
    assert raised.value.is_memory is True


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (WorkerMemoryLimitError(rss_bytes=200, rss_limit_bytes=100), True),
        (WorkerMemoryLimitUnsupportedError(memory_limit_bytes=100), True),
        (WorkerCrashedError(-9, memory_limited=exitcode_looks_memory_limited(-9, 100)), True),
        (WorkerCrashedError(1, memory_limited=exitcode_looks_memory_limited(1, 100)), False),
        (
            WorkerRemoteError(
                remote_type="MemoryError",
                remote_message="out of memory",
                remote_traceback="traceback",
                is_memory=True,
            ),
            True,
        ),
        (
            WorkerRemoteError(
                remote_type="NativeMemoryLimitUnsupportedError",
                remote_message="unsupported",
                remote_traceback="traceback",
                is_memory=False,
            ),
            True,
        ),
        (
            WorkerRemoteError(
                remote_type="ValueError",
                remote_message="bad value",
                remote_traceback="traceback",
                is_memory=False,
            ),
            False,
        ),
        (WorkerTimeoutError(timeout_seconds=1.0), False),
        (RuntimeError("generic"), False),
    ],
)
def test_isolated_worker_failure_is_memory_classifies_every_worker_outcome(
    failure: BaseException,
    expected: bool,
) -> None:
    """Every supervisor maps worker failures to 507 through one shared predicate."""
    assert worker_failure_is_memory(failure) is expected


def _remote(remote_type: str, *, is_memory: bool = False) -> WorkerRemoteError:
    return WorkerRemoteError(
        remote_type=remote_type,
        remote_message="message",
        remote_traceback="traceback",
        is_memory=is_memory,
    )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (WorkerMemoryLimitUnsupportedError(memory_limit_bytes=100), True),
        (_remote("NativeMemoryLimitUnsupportedError"), True),
        (_remote("NativeMemoryLimitCleanupError"), True),
        (_remote("MemoryError", is_memory=True), False),
        (WorkerMemoryLimitError(rss_bytes=200, rss_limit_bytes=100), False),
        (WorkerCrashedError(-9, memory_limited=exitcode_looks_memory_limited(-9, 100)), False),
        (RuntimeError("generic"), False),
    ],
)
def test_isolated_worker_failure_is_cap_unavailable_only_for_a_cap_the_host_cannot_install(
    failure: BaseException,
    expected: bool,
) -> None:
    """A missing cap is told apart from running out of memory, which also claims it."""
    assert worker_failure_is_cap_unavailable(failure) is expected


def test_a_child_failure_on_a_path_too_long_for_windows_is_reported_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child names the path Windows refused, so the parent can classify the failure."""
    import haute._file_ops as file_ops

    monkeypatch.setattr(file_ops, "_IS_WINDOWS", True)
    monkeypatch.setattr(file_ops, "windows_long_paths_enabled", lambda: False)
    path = str(tmp_path / ("d" * 260))
    try:
        raise FileNotFoundError(2, "No such file or directory", path)
    except FileNotFoundError as exc:
        kind, (remote_type, remote_message, remote_traceback, is_memory) = (
            isolation._worker_error_envelope(exc)
        )

    assert kind == "error"
    assert remote_type == "PathTooLongError"
    assert f"a path of {len(path)} characters" in remote_message
    assert "FileNotFoundError" in remote_traceback
    assert is_memory is False


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            WorkerRemoteError(
                remote_type="PathTooLongError",
                remote_message="too long",
                remote_traceback="traceback",
                is_memory=False,
            ),
            True,
        ),
        (
            WorkerRemoteError(
                remote_type="FileNotFoundError",
                remote_message="missing",
                remote_traceback="traceback",
                is_memory=False,
            ),
            False,
        ),
        (RuntimeError("generic"), False),
    ],
)
def test_worker_failure_is_path_too_long_reads_the_childs_exception(
    failure: BaseException,
    expected: bool,
) -> None:
    from haute._worker_failures import worker_failure_is_path_too_long

    assert worker_failure_is_path_too_long(failure) is expected


def test_isolated_worker_environment_reaches_the_child_only() -> None:
    """A spawn-time variable is visible in the child and never left in the parent."""
    name = "HAUTE_TEST_WORKER_ENVIRONMENT"
    assert name not in os.environ

    result = run_isolated_worker(
        _read_environment,
        name,
        config=IsolatedWorkerConfig(process_name="test-worker", environment={name: "7"}),
    )

    assert result == "7"
    assert name not in os.environ


def test_isolated_worker_returns_picklable_value() -> None:
    result = run_isolated_worker(_return_payload, 2, 3)

    assert result["sum"] == 5
    assert result["pid"] != os.getpid()


# Spawning a Python process and moving 8 MB back through the queue is slow on
# Windows and slower again under a full parallel run, where this budget was the
# only thing failing the suite. The deadline is widened, never the assertion:
# what the test proves is that the whole payload arrives before the child is
# joined, not that it arrives quickly.
_LARGE_RESULT_DRAIN_TIMEOUT_SECONDS = 30


def test_isolated_worker_drains_large_result_before_joining_child() -> None:
    result = run_isolated_worker(
        _return_large_payload,
        8 * 1024 * 1024,
        config=IsolatedWorkerConfig(timeout_seconds=_LARGE_RESULT_DRAIN_TIMEOUT_SECONDS),
    )

    assert len(result) == 8 * 1024 * 1024
    assert result[:1] == b"x"


def test_unexpected_parent_failure_terminates_child(
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

        def Event(self) -> threading.Event:  # noqa: N802 - multiprocessing API
            return threading.Event()

        def Process(self, **kwargs: object) -> FakeProcess:  # noqa: N802 - multiprocessing API
            self.process = FakeProcess(**kwargs)
            return self.process

    context = FakeContext()
    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: context)
    monkeypatch.setattr(
        "haute._worker_isolation.await_worker_ready", lambda *_a, **_k: ("ready", None)
    )
    monkeypatch.setattr(
        "haute._worker_isolation._wait_for_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parent polling failed")),
    )

    with pytest.raises(RuntimeError, match="parent polling failed"):
        run_isolated_worker(_return_payload, 1, 2)

    assert context.process is not None
    assert context.process.terminated
    assert not context.process.is_alive()


def test_start_failure_preserves_typed_error(
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

        def Event(self) -> threading.Event:  # noqa: N802 - multiprocessing API
            return threading.Event()

        def Process(self, **kwargs: object) -> UnstartedProcess:  # noqa: N802
            return UnstartedProcess()

    monkeypatch.setattr(
        "haute._worker_isolation.mp.get_context",
        lambda _method: FakeContext(),
    )

    with pytest.raises(WorkerStartError, match="spawn unavailable"):
        run_isolated_worker(_return_payload, 1, 2)


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

        def Event(self) -> threading.Event:  # noqa: N802 - multiprocessing API
            return threading.Event()

        def Process(self, **_kwargs: object) -> Process:  # noqa: N802
            return Process()

    cleaned: list[int] = []
    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: Context())
    monkeypatch.setattr(
        "haute._worker_isolation.await_worker_ready", lambda *_a, **_k: ("ready", None)
    )
    monkeypatch.setattr(
        "haute._worker_isolation._wait_for_worker", lambda *_args, **_kwargs: ("ok", 7)
    )
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

        def Event(self) -> threading.Event:  # noqa: N802 - multiprocessing API
            return threading.Event()

        def Process(self, **_kwargs: object) -> Process:  # noqa: N802
            return Process()

    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: Context())
    monkeypatch.setattr(
        "haute._worker_isolation.await_worker_ready", lambda *_a, **_k: ("ready", None)
    )
    monkeypatch.setattr(
        "haute._worker_isolation._wait_for_worker", lambda *_args, **_kwargs: ("ok", 7)
    )

    with pytest.raises(WorkerCrashedError) as exc_info:
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

        def Event(self) -> threading.Event:  # noqa: N802 - multiprocessing API
            return threading.Event()

        def Process(self, **_kwargs: object) -> Process:  # noqa: N802
            return Process()

    monkeypatch.setattr("haute._worker_isolation.mp.get_context", lambda _method: Context())
    monkeypatch.setattr(
        "haute._worker_isolation.await_worker_ready", lambda *_a, **_k: ("ready", None)
    )
    monkeypatch.setattr(
        "haute._worker_isolation._wait_for_worker",
        lambda *_args, **_kwargs: ("error", ("ValueError", "bad", "traceback", False)),
    )
    monkeypatch.setattr(
        "haute._worker_isolation.cleanup_private_cgroups_for_pid",
        lambda _pid: (_ for _ in ()).throw(OSError("cgroup cleanup failed")),
    )

    with pytest.raises(WorkerRemoteError, match="bad") as exc_info:
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

    with pytest.raises(WorkerTerminationError):
        _terminate_process(StuckProcess())  # type: ignore[arg-type]


def test_isolated_worker_reports_remote_exception() -> None:
    with pytest.raises(WorkerRemoteError) as exc_info:
        run_isolated_worker(_raise_value_error, "bad input")

    assert exc_info.value.terminal_reason == "error"
    assert exc_info.value.remote_type == "ValueError"
    assert "bad input" in str(exc_info.value)
    assert "ValueError: bad input" in exc_info.value.remote_traceback


def test_isolated_worker_reports_crash_without_killing_parent() -> None:
    with pytest.raises(WorkerCrashedError) as exc_info:
        run_isolated_worker(_crash_process, 23)

    assert exc_info.value.terminal_reason == "error"
    assert exc_info.value.exitcode == 23
    assert 1 + 1 == 2


def _live_workers(process_name: str) -> list[object]:
    import multiprocessing

    return [child for child in multiprocessing.active_children() if child.name == process_name]


def test_timeout_terminates_worker() -> None:
    name = "haute-test-timed-out-worker"
    with pytest.raises(WorkerTimeoutError) as exc_info:
        run_isolated_worker(
            _sleep_for,
            5.0,
            config=IsolatedWorkerConfig(timeout_seconds=0.1, process_name=name),
        )

    assert exc_info.value.terminal_reason == "timed_out"
    assert _live_workers(name) == []


def test_stop_reason_terminates_worker_and_preserves_reason() -> None:
    name = "haute-test-stopped-worker"
    with pytest.raises(WorkerStoppedError) as exc_info:
        run_isolated_worker(
            _sleep_for,
            5.0,
            config=IsolatedWorkerConfig(stop_reason=lambda: "cancelled", process_name=name),
        )

    assert exc_info.value.terminal_reason == "cancelled"
    assert _live_workers(name) == []


def test_required_memory_cap_fails_loudly_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("haute._worker_isolation.process_memory_caps_supported", lambda: False)

    with pytest.raises(WorkerMemoryLimitUnsupportedError):
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
    not sys.platform.startswith("linux"),
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
    assert soft >= min([limit, *inherited_ceilings])
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

    watchdog = _create_worker_rss_watchdog(
        _Process(),  # type: ignore[arg-type]
        memory_limit_bytes=50,
        require_memory_limit=True,
    )

    with pytest.raises(WorkerMemoryLimitError) as exc_info:
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
    with pytest.raises(WorkerMemoryLimitUnsupportedError):
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

    with pytest.raises(WorkerStartError, match="process id"):
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
        _go(),
        lambda value, *, increment: value + increment,
        (2,),
        {"increment": 3},
        memory_limit,
        caps_supported,
        current_project(),
    )

    assert pickle.loads(results.get_nowait()) == ("ok", 5)
    assert applied == expected_limits


def test_isolated_entrypoint_serialises_child_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute._worker_isolation as isolation_mod

    results = _EntrypointQueue()
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
        _go(),
        _raise_value_error,
        ("child boom",),
        {},
        128,
        False,
        current_project(),
    )

    status, payload = pickle.loads(results.get_nowait())
    assert status == "error"
    error_type, message, remote_traceback, is_memory = payload  # type: ignore[misc]
    assert error_type == "ValueError"
    assert message == "child boom"
    assert "ValueError: child boom" in remote_traceback
    assert is_memory is False


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
        _go(),
        native.current_native_memory_backend,
        (),
        {},
        128,
        True,
        current_project(),
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

    isolation_mod._isolated_worker_entrypoint(
        results, _go(), lambda: "result", (), {}, 128, True, current_project()
    )

    assert pickle.loads(results.get_nowait()) == ("ok", "result")
    assert events == ["apply"]
    assert (results.closed, results.joined) == (1, 1)


def test_isolated_worker_starts_its_result_feeder_before_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feeder started after the cap could fail to start when the result is put."""
    import haute._worker_isolation as isolation_mod
    from haute._native_memory_limit import NativeMemoryLease

    events: list[str] = []

    class RecordingQueue:
        _thread: object | None = None

        def _start_thread(self) -> None:
            events.append("feeder")
            self._thread = object()

        def put(self, _payload: bytes) -> None:
            events.append("put")

        def close(self) -> None:
            pass

        def join_thread(self) -> None:
            pass

    monkeypatch.setattr(
        NativeMemoryLease, "apply", lambda self, *_a, **_k: events.append("cap") or False
    )

    isolation_mod._isolated_worker_entrypoint(
        RecordingQueue(), _go(), int, ("7",), {}, 128, False, current_project()
    )

    # The ready report, then the result.
    assert events == ["feeder", "cap", "put", "put"]


def test_memory_limited_exitcode_classification_is_platform_independent() -> None:
    import haute._worker_isolation as isolation_mod

    assert isolation_mod.exitcode_looks_memory_limited(None, 10) is False
    assert isolation_mod.exitcode_looks_memory_limited(-9, None) is False
    assert isolation_mod.exitcode_looks_memory_limited(-9, 10) is True
    assert isolation_mod.exitcode_looks_memory_limited(-int(signal.SIGABRT), 10) is True
    assert isolation_mod.exitcode_looks_memory_limited(-7, 10) is False
    # Windows fail-fast (STATUS_STACK_BUFFER_OVERRUN): a refused native allocation.
    assert isolation_mod.exitcode_looks_memory_limited(0xC0000409, 10) is True
    assert isolation_mod.exitcode_looks_memory_limited(0xC0000409, None) is False
    # Windows STATUS_STACK_OVERFLOW: the Job Object cap refused to commit a
    # thread's next stack page (seen on a 10M-row preview join).
    assert isolation_mod.exitcode_looks_memory_limited(0xC00000FD, 10) is True
    assert isolation_mod.exitcode_looks_memory_limited(0xC00000FD, None) is False
    assert isolation_mod.exitcode_looks_memory_limited(3, 10) is False


@pytest.mark.skipif(
    not process_memory_caps_supported(),
    reason="the refused-allocation crash needs a native per-worker memory cap",
)
def test_native_allocation_refused_by_the_worker_cap_is_a_memory_outcome() -> None:
    """A native allocator the cap refuses aborts the child without a payload
    (Linux ``SIGABRT``/``SIGKILL``, Windows fail-fast); the parent reports it
    as a hedged memory outcome rather than a generic crash."""
    with pytest.raises(WorkerCrashedError) as exc_info:
        run_isolated_worker(
            _allocate_native_column,
            1_600_000_000,
            config=IsolatedWorkerConfig(
                memory_limit_bytes=256 * 1024 * 1024,
                require_memory_limit=True,
            ),
        )

    assert exc_info.value.terminal_reason == "memory_limited"


def test_worker_memory_enforcement_defaults_to_required_where_a_cap_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    monkeypatch.delenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", raising=False)
    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: True)

    assert resolve_worker_memory_enforcement() == "required"
    config = worker_config_for_memory_policy(memory_limit_bytes=123)
    assert config.memory_limit_bytes == 123
    assert config.require_memory_limit is True


def test_worker_memory_enforcement_supervises_by_default_where_no_cap_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS: workers run supervised by the parent's RSS watchdog, not refused."""
    import haute._worker_isolation as isolation_mod

    monkeypatch.delenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", raising=False)
    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: False)
    monkeypatch.setattr(isolation_mod, "_supervision_noted", threading.Event())
    events: list[str] = []
    monkeypatch.setattr(
        isolation_mod.logger, "warning", lambda event, **_fields: events.append(event)
    )

    assert resolve_worker_memory_enforcement() == "best_effort"
    config = worker_config_for_memory_policy(memory_limit_bytes=123)
    assert config.memory_limit_bytes == 123
    assert config.require_memory_limit is False
    # Said once per process, however many workers resolve it.
    assert events == ["worker_memory_supervised_without_cap"]


def test_an_explicit_worker_memory_policy_wins_over_the_host_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._worker_isolation as isolation_mod

    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: False)
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")
    assert resolve_worker_memory_enforcement() == "required"

    monkeypatch.setattr(isolation_mod, "process_memory_caps_supported", lambda: True)
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    assert resolve_worker_memory_enforcement() == "best_effort"


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


def test_worker_config_rejects_a_negative_address_space_allowance() -> None:
    with pytest.raises(ValueError, match="address_space_allowance_bytes must not be negative"):
        IsolatedWorkerConfig(address_space_allowance_bytes=-1)


def test_unknown_worker_memory_enforcement_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "sometimes")

    with pytest.raises(RuntimeError, match="HAUTE_WORKER_MEMORY_ENFORCEMENT"):
        resolve_worker_memory_enforcement()


def test_isolated_job_supervisor_records_completed_result(tmp_path: Path) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))

    thread = _launch(
        supervisor, job_id, tmp_path, completed_fields=lambda result: dict(result.metadata)
    )
    thread.join(timeout=60)

    assert not thread.is_alive()
    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["terminal_reason"] == "completed"
    assert job["sum"] == 6


def test_isolated_job_supervisor_records_remote_failure(tmp_path: Path) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(JobLifecycle(store))

    thread = _launch(
        supervisor, job_id, tmp_path, _failing_protocol_worker, {"message": "bad input"}
    )
    thread.join(timeout=60)

    assert not thread.is_alive()
    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert job["terminal_reason"] == "error"
    assert job["worker_error_type"] == "ValueError"
    assert "bad input" in job["message"]


def test_isolated_job_supervisor_terminalizes_unexpected_parent_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    parent_error = RuntimeError("parent isolation bug")
    reported: list[threading.ExceptHookArgs] = []

    def raise_parent_error(*args: object, **kwargs: object) -> object:
        raise parent_error

    supervisor = IsolatedJobSupervisor(JobLifecycle(store), protocol_runner=raise_parent_error)
    monkeypatch.setattr(threading, "excepthook", reported.append)
    thread = _launch(supervisor, job_id, tmp_path)
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

    thread = _launch(
        supervisor,
        job_id,
        tmp_path / "artifacts",
        _sleeping_protocol_worker,
        {},
        config=IsolatedWorkerConfig(stop_reason=lambda: "cancelled"),
        on_finished=cleanup,
    )
    thread.join(timeout=60)

    assert not thread.is_alive()
    job = store.require_job(job_id)
    assert job["status"] == "cancelled"
    assert job["terminal_reason"] == "cancelled"
    assert not temp_dir.exists()


def test_isolated_job_supervisor_preserves_higher_precedence_terminal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    lifecycle = JobLifecycle(store)
    assert lifecycle.transition(job_id, to="timed_out") is not None
    reported: list[threading.ExceptHookArgs] = []

    def fail_late(*_args, **_kwargs):
        raise RuntimeError("late parent failure")

    supervisor = IsolatedJobSupervisor(lifecycle, protocol_runner=fail_late)
    monkeypatch.setattr(threading, "excepthook", reported.append)

    thread = _launch(supervisor, job_id, tmp_path)
    thread.join_and_raise(timeout=10)

    job = store.require_job(job_id)
    assert job["status"] == "timed_out"
    assert job["terminal_reason"] == "timed_out"
    assert thread.infrastructure_failure is None
    assert len(reported) == 1
    assert str(reported[0].exc_value) == "late parent failure"


def test_isolated_job_supervisor_rejects_incoherent_existing_terminal_state(
    tmp_path: Path,
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    with store._write_lock:  # noqa: SLF001 - deliberate corrupt-state boundary test
        store._jobs[job_id]["status"] = "cancelled"  # noqa: SLF001
    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("late")),
    )

    thread = _launch(supervisor, job_id, tmp_path)
    thread.join(timeout=10)

    assert isinstance(thread.infrastructure_failure, SupervisorInfrastructureError)
    with pytest.raises(
        SupervisorInfrastructureError,
        match="Terminal job reason must match its status",
    ):
        thread.join_and_raise()


def test_isolated_job_supervisor_exposes_terminal_persistence_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    lifecycle = JobLifecycle(store)
    supervisor = IsolatedJobSupervisor(
        lifecycle,
        protocol_runner=lambda *_args, **_kwargs: WorkerResultManifest(metadata={"ok": True}),
    )

    monkeypatch.setattr(
        JobLifecycle,
        "transition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("store unavailable")),
    )

    thread = _launch(supervisor, job_id, tmp_path)
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert isinstance(thread.infrastructure_failure, SupervisorInfrastructureError)
    assert store.require_job(job_id)["status"] == "running"
    with pytest.raises(SupervisorInfrastructureError, match="store unavailable"):
        thread.join_and_raise()


def test_an_unrecorded_outcome_is_settled_as_an_error(tmp_path: Path) -> None:
    """ROAD-WORKER-07: a terminal write the store refused leaves the job running until
    its status route settles it; the settling write carries only the failure's text."""
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=lambda *_args, **_kwargs: WorkerResultManifest(metadata={}),
    )

    # A lifecycle-owned field makes the store refuse the supervisor's terminal write.
    thread = _launch(supervisor, job_id, tmp_path, completed_fields=lambda _r: {"ended_at": 1.0})
    thread.join(timeout=10)

    assert isinstance(thread.infrastructure_failure, SupervisorInfrastructureError)
    assert store.require_job(job_id)["status"] == "running"
    with patch.object(JobLifecycle, "transition", side_effect=OSError("store unavailable")):
        with pytest.raises(OSError, match="store unavailable"):
            supervisor.settle_unrecorded_outcome(job_id)
    # The failed settling write kept the outcome for the next poll.
    settled = supervisor.settle_unrecorded_outcome(job_id)
    assert settled is not None
    assert settled["status"] == "error"
    assert settled["supervisor_error_class"] == "ValueError"
    assert "could not record" in settled["message"]
    assert supervisor.settle_unrecorded_outcome(job_id) is None


def test_an_unrecorded_outcome_is_kept_no_longer_than_its_job(tmp_path: Path) -> None:
    """What a status route could settle expires with the store's TTL, as the job does."""
    store = JobStore(ttl_seconds=0)
    supervisor = IsolatedJobSupervisor(
        JobLifecycle(store),
        protocol_runner=lambda *_args, **_kwargs: WorkerResultManifest(metadata={}),
    )
    first, second = (store.create_job({"status": "running"}) for _ in range(2))
    for job_id in (first, second):
        # The job is gone (TTL 0) or refuses the field: either way nothing is recorded.
        _launch(supervisor, job_id, tmp_path, completed_fields=lambda _r: {"ended_at": 1.0}).join(
            timeout=10
        )

    assert supervisor.settle_unrecorded_outcome(first) is None


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
        with pytest.raises(WorkerHostError) as excinfo:
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


def test_worker_payload_decoding_refuses_malformed_payloads() -> None:
    with pytest.raises(WorkerCrashedError):
        isolation._decode_worker_payload(b"not pickle")
    with pytest.raises(WorkerCrashedError):
        isolation._decode_worker_payload(pickle.dumps(("wrong", 1)))


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
    isolation._isolated_worker_entrypoint(
        queue, _go(), lambda: 1, (), {}, 10, True, current_project()
    )
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
    with pytest.raises(WorkerMemoryLimitUnsupportedError):
        isolation._create_worker_rss_watchdog(
            process, memory_limit_bytes=5, require_memory_limit=True
        )
    watchdog = isolation._WorkerRssWatchdog(4, 5, False, False)
    monkeypatch.setattr(isolation, "process_rss_bytes", lambda _pid: 6)
    with pytest.raises(WorkerMemoryLimitError):
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


def test_isolated_entrypoint_exposes_no_backend_when_best_effort_apply_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale lease evidence must not become the request's active backend."""
    import haute._worker_isolation as isolation_mod
    from haute._native_memory_limit import current_native_memory_backend

    results = _EntrypointQueue()
    monkeypatch.setattr(
        isolation_mod,
        "NativeMemoryLease",
        lambda: SimpleNamespace(
            backend="rlimit",
            apply=lambda *_args, **_kwargs: False,
            restore=lambda: None,
            close=lambda: None,
        ),
    )

    isolation_mod._isolated_worker_entrypoint(
        results,
        _go(),
        current_native_memory_backend,
        (),
        {},
        64,
        False,
        current_project(),
    )

    assert pickle.loads(results.get_nowait()) == ("ok", None)


def test_running_interpreter_resource_tracker_has_the_private_shape_recovery_needs() -> None:
    """``_reset_resource_tracker`` reads ``_lock``, ``_fd`` and ``_pid``.

    They are private CPython state with no supported replacement, and the
    recovery path degrades to no recovery (with a warning) if they move. This
    asks the running interpreter directly so the compatibility matrix shows
    the shape on every Python, instead of the degraded branch being reached
    silently in production first.
    """
    from multiprocessing import resource_tracker

    tracker = resource_tracker._resource_tracker
    missing = [name for name in ("_lock", "_fd", "_pid") if not hasattr(tracker, name)]
    assert not missing, (
        f"multiprocessing.resource_tracker._resource_tracker on Python "
        f"{sys.version.split()[0]} lacks {missing}: dead-tracker recovery in "
        "haute._worker_isolation._reset_resource_tracker is now a no-op on this "
        "interpreter. Find the new layout before relying on the recovery path."
    )


class _EnvironmentProbeProcess:
    """Minimal process stand-in that records the environment it was started in."""

    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.observed: str | None = None
        self.failure = failure
        self.starts = 0

    def start(self) -> None:
        self.starts += 1
        self.observed = os.environ.get("POLARS_MAX_THREADS")
        if self.failure is not None:
            raise self.failure


def test_start_process_with_environment_restores_a_pre_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARS_MAX_THREADS", "16")
    process = _EnvironmentProbeProcess()

    start_process_with_environment(process, {"POLARS_MAX_THREADS": "2"})

    assert process.starts == 1
    assert process.observed == "2"
    assert os.environ["POLARS_MAX_THREADS"] == "16"


def test_start_process_with_environment_deletes_an_absent_value_when_start_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARS_MAX_THREADS", raising=False)
    process = _EnvironmentProbeProcess(failure=RuntimeError("spawn refused"))

    with pytest.raises(RuntimeError, match="spawn refused"):
        start_process_with_environment(process, {"POLARS_MAX_THREADS": "2"})

    assert process.observed == "2"
    assert "POLARS_MAX_THREADS" not in os.environ


@pytest.mark.meta
def test_every_multiprocessing_spawn_goes_through_the_environment_helper() -> None:
    """A bare ``process.start()`` could inherit another spawn's environment overrides.

    The overrides are applied to the parent process while ``start()`` runs, so
    every worker kind must start under the same lock or a training or
    dispersion worker spawned during an interactive spawn would silently carry
    the interactive thread cap for its whole life.
    """
    import re
    from pathlib import Path

    import haute

    package_root = Path(haute.__file__).resolve().parent
    offenders = [
        f"{path.relative_to(package_root).as_posix()}:{line_number}"
        for path in source_files(package_root)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.search(r"\bprocess\.start\(\)", line)
    ]

    assert len(offenders) == 1, offenders
    assert offenders[0].startswith("_worker_isolation.py:"), offenders


# ---------------------------------------------------------------------------
# The readiness handshake: the watchdog's baseline is the ready child's
# ---------------------------------------------------------------------------

_MIB = 1024 * 1024


@pytest.mark.parametrize(
    "module", ["tests._spawned_heavy_import", "tests._spawned_without_native_cap"]
)
def test_a_heavy_import_is_not_charged_to_the_job(module: str) -> None:
    """The child holds 160 MiB from its imports, then its job 150 MiB under a 256 MiB growth
    budget: the watchdog's baseline is the child once ready, imports included, so the job
    fits, with the host's native cap and without one."""
    allocate = importlib.import_module(module).allocate
    assert (
        run_isolated_worker(
            allocate,
            150,
            2.0,
            config=IsolatedWorkerConfig(timeout_seconds=120, memory_limit_bytes=256 * _MIB),
        )
        == 150
    )


def test_without_a_native_cap_the_watchdog_still_stops_a_job_past_its_budget() -> None:
    """The job's own allocations come after the baseline is sampled, so they count."""
    from tests import _spawned_without_native_cap as fixture

    with pytest.raises(WorkerMemoryLimitError):
        run_isolated_worker(
            fixture.allocate,
            400,
            5.0,
            config=IsolatedWorkerConfig(timeout_seconds=120, memory_limit_bytes=128 * _MIB),
        )


def test_a_worker_past_its_startup_ceiling_before_it_is_ready_is_stopped() -> None:
    from tests import _spawned_heavy_import as fixture

    with pytest.raises(WorkerMemoryLimitError) as raised:
        run_isolated_worker(
            fixture.allocate,
            1,
            config=IsolatedWorkerConfig(
                timeout_seconds=120,
                memory_limit_bytes=256 * _MIB,
                startup_rss_ceiling_bytes=64 * _MIB,
            ),
        )
    assert any("startup ceiling" in note for note in raised.value.__notes__)


def test_a_ready_report_from_a_worker_past_its_startup_ceiling_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ready message and an over-ceiling footprint arrive together: the footprint is
    checked before it becomes the job's baseline."""
    import haute._worker_isolation as isolation

    stopped: list[object] = []
    process = SimpleNamespace(pid=123, is_alive=lambda: True)
    ready_queue = SimpleNamespace(get=lambda timeout: "ready")
    monkeypatch.setattr(isolation, "process_rss_bytes", lambda _pid: 65 * _MIB)
    monkeypatch.setattr(isolation, "_terminate_process", stopped.append)

    with pytest.raises(WorkerMemoryLimitError) as raised:
        isolation.await_worker_ready(
            process,
            ready_queue,
            is_ready=lambda message: message == "ready",
            config=IsolatedWorkerConfig(timeout_seconds=120, startup_rss_ceiling_bytes=64 * _MIB),
            deadline=None,
        )

    assert stopped == [process]
    assert any("startup ceiling" in note for note in raised.value.__notes__)


def test_a_worker_that_never_reports_ready_fails_at_its_startup_deadline() -> None:
    from tests import _spawned_slow_import as fixture

    started = time.monotonic()
    with pytest.raises(WorkerStartError, match="did not report ready within 2 seconds"):
        run_isolated_worker(
            fixture.never_runs,
            config=IsolatedWorkerConfig(timeout_seconds=120, startup_timeout_seconds=2),
        )
    assert time.monotonic() - started < 60
