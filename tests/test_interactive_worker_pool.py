from __future__ import annotations

import asyncio
import os
import pickle
import queue
import signal
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

import haute._interactive_workers as worker_mod
from haute._interactive_workers import (
    InteractiveWorkerCrashedError,
    InteractiveWorkerMemoryLimitError,
    InteractiveWorkerPool,
    InteractiveWorkerRemoteError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
    resolve_interactive_execution_mode,
)


def _worker_identity(value: int = 0) -> tuple[int, int]:
    return os.getpid(), value


def _worker_sleep(seconds: float) -> int:
    time.sleep(seconds)
    return os.getpid()


def _worker_mark_started(path: str, seconds: float) -> int:
    with open(path, "wb") as marker:
        marker.write(b"started")
    time.sleep(seconds)
    return os.getpid()


def _worker_fail() -> None:
    raise ValueError("child failed")


class _EmptyResultQueue:
    def get(self, *, timeout: float) -> Any:
        assert timeout > 0
        raise queue.Empty


class _Queue:
    def __init__(self, values: list[Any] | None = None) -> None:
        self.values = list(values or [])
        self.closed = 0
        self.joined = 0

    def get(self, **_kwargs: Any) -> Any:
        if not self.values:
            raise queue.Empty
        return self.values.pop(0)

    def put(self, value: Any, **_kwargs: Any) -> None:
        self.values.append(value)

    def close(self) -> None:
        self.closed += 1

    def join_thread(self) -> None:
        self.joined += 1


def _returns(value: Any) -> Any:
    return value


def _raises() -> None:
    raise RuntimeError("remote")


def _unpicklable_result() -> Any:
    return lambda: None


def test_startup_readiness_fails_promptly_when_child_has_exited() -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    process = SimpleNamespace(pid=123, exitcode=17, is_alive=lambda: False)

    with pytest.raises(InteractiveWorkerCrashedError) as exc_info:
        pool._wait_for_ready(process, _EmptyResultQueue())

    assert exc_info.value.exitcode == 17


def test_startup_readiness_retains_a_bounded_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._interactive_workers as worker_mod

    samples = iter((10.0, 11.0))
    monkeypatch.setattr(worker_mod, "_START_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(worker_mod.time, "monotonic", lambda: next(samples))
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    process = SimpleNamespace(pid=123, exitcode=None, is_alive=lambda: True)

    with pytest.raises(TimeoutError, match="did not become ready"):
        pool._wait_for_ready(process, _EmptyResultQueue())


def test_execution_mode_defaults_to_process_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAUTE_INTERACTIVE_EXECUTION_MODE", raising=False)
    assert resolve_interactive_execution_mode() == "process"

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "thread")
    assert resolve_interactive_execution_mode() == "thread"

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "fallback")
    with pytest.raises(RuntimeError, match="HAUTE_INTERACTIVE_EXECUTION_MODE"):
        resolve_interactive_execution_mode()


def test_pool_reuses_warm_affinity_worker() -> None:
    pool = InteractiveWorkerPool(size=1)
    try:
        first_pid, first_value = pool.run(
            _worker_identity,
            1,
            affinity_key="same-lineage",
            timeout_seconds=5,
        )
        second_pid, second_value = pool.run(
            _worker_identity,
            2,
            affinity_key="same-lineage",
            timeout_seconds=5,
        )
    finally:
        pool.close()

    assert first_pid == second_pid
    assert first_pid != os.getpid()
    assert (first_value, second_value) == (1, 2)


def test_timeout_kills_and_replaces_exact_worker() -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    try:
        original_pid, _ = pool.run(
            _worker_identity,
            affinity_key="lineage",
            timeout_seconds=5,
        )
        with pytest.raises(InteractiveWorkerTimeoutError) as exc_info:
            pool.run(
                _worker_sleep,
                5.0,
                affinity_key="lineage",
                timeout_seconds=0.05,
            )
        replacement_pid, _ = pool.run(
            _worker_identity,
            affinity_key="lineage",
            timeout_seconds=5,
        )
    finally:
        pool.close()

    assert exc_info.value.terminal_reason == "timed_out"
    assert replacement_pid != original_pid


def test_stop_reason_kills_and_replaces_worker() -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    polls = 0

    def stop_reason() -> str | None:
        nonlocal polls
        polls += 1
        return "superseded" if polls >= 2 else None

    try:
        original_pid, _ = pool.run(
            _worker_identity,
            affinity_key="lineage",
            timeout_seconds=5,
        )
        with pytest.raises(InteractiveWorkerStoppedError) as exc_info:
            pool.run(
                _worker_sleep,
                5.0,
                affinity_key="lineage",
                timeout_seconds=5,
                stop_reason=stop_reason,
            )
        replacement_pid, _ = pool.run(
            _worker_identity,
            affinity_key="lineage",
            timeout_seconds=5,
        )
    finally:
        pool.close()

    assert getattr(exc_info.value, "terminal_reason", None) == "superseded"
    assert replacement_pid != original_pid


def test_required_memory_enforcement_rejects_absolute_watchdog_without_native_limit() -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    try:
        with pytest.raises(ValueError, match="native memory growth limit"):
            pool.run(
                _worker_sleep,
                5.0,
                affinity_key="lineage",
                timeout_seconds=5,
                absolute_rss_limit_bytes=9_999,
                require_memory_limit=True,
            )
    finally:
        pool.close()


def test_required_native_cap_unavailable_is_rejected_before_pool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    starts: list[bool] = []
    monkeypatch.setattr(pool, "start", lambda: starts.append(True))
    monkeypatch.setattr(worker_mod, "native_memory_caps_supported", lambda: False)

    with pytest.raises(RuntimeError, match="native memory caps are unavailable"):
        pool.run(
            _returns,
            affinity_key="lineage",
            timeout_seconds=1,
            memory_growth_limit_bytes=100,
            require_memory_limit=True,
        )

    assert starts == []


def test_parent_rss_watchdog_applies_limit_to_per_request_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._interactive_workers as worker_mod

    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    samples = iter([1_000, 1_051])
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: next(samples))
    try:
        with pytest.raises(InteractiveWorkerMemoryLimitError) as exc_info:
            pool.run(
                _worker_sleep,
                5.0,
                affinity_key="lineage",
                timeout_seconds=5,
                memory_growth_limit_bytes=50,
                # Native-cap enforcement has its own contract tests. This case
                # isolates the cross-platform parent RSS watchdog.
                require_memory_limit=False,
            )
    finally:
        pool.close()

    assert exc_info.value.rss_bytes == 1_051
    assert exc_info.value.limit_bytes == 1_050


def test_remote_error_is_structured_and_worker_remains_usable() -> None:
    pool = InteractiveWorkerPool(size=1)
    try:
        with pytest.raises(InteractiveWorkerRemoteError) as exc_info:
            pool.run(
                _worker_fail,
                affinity_key="lineage",
                timeout_seconds=5,
            )
        worker_pid, _ = pool.run(
            _worker_identity,
            affinity_key="lineage",
            timeout_seconds=5,
        )
    finally:
        pool.close()

    assert exc_info.value.remote_type == "ValueError"
    assert exc_info.value.remote_module == "builtins"
    assert exc_info.value.remote_message == "child failed"
    assert "ValueError: child failed" in exc_info.value.remote_traceback
    assert worker_pid != os.getpid()


def test_close_is_idempotent_and_rejects_new_work() -> None:
    pool = InteractiveWorkerPool(size=1)
    pool.start()
    pool.close()
    pool.close()

    with pytest.raises(RuntimeError, match="closed"):
        pool.run(
            _worker_identity,
            affinity_key="lineage",
            timeout_seconds=5,
        )


def test_close_kills_in_flight_work_without_waiting_for_its_deadline(
    tmp_path,
) -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    marker = tmp_path / "worker-started"
    failures: list[BaseException] = []

    def run_work() -> None:
        try:
            pool.run(
                _worker_mark_started,
                str(marker),
                10.0,
                affinity_key="lineage",
                timeout_seconds=30,
            )
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=run_work)
    thread.start()
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()

    started = time.monotonic()
    pool.close()
    elapsed = time.monotonic() - started
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert elapsed < 2
    assert len(failures) == 1
    assert isinstance(failures[0], InteractiveWorkerStoppedError)
    assert failures[0].terminal_reason == "cancelled"


def test_exception_payload_and_entrypoint_protocols(monkeypatch: pytest.MonkeyPatch) -> None:
    class PayloadError(Exception):
        def to_payload(self):
            return {1: "ok"}

    assert worker_mod._public_exception_payload(PayloadError()) == {"1": "ok"}
    assert worker_mod._public_exception_payload(Exception()) is None

    class BadPayloadError(Exception):
        def to_payload(self):
            return lambda: None

    assert worker_mod._public_exception_payload(BadPayloadError()) is None
    request = _Queue(
        [
            pickle.dumps(("run", "one", _returns, (3,), {}, None, False)),
            pickle.dumps(("ack", "one")),
            pickle.dumps(("run", "two", _raises, (), {}, None, False)),
            pickle.dumps(("ack", "two")),
            pickle.dumps(("shutdown",)),
        ]
    )
    results = _Queue()
    monkeypatch.setattr(worker_mod.os, "getpid", lambda: 42)
    worker_mod._interactive_worker_entrypoint(request, results, ())
    ready, good, good_release, bad, bad_release = [pickle.loads(value) for value in results.values]
    assert ready == ("ready", 42)
    assert good == ("result", "one", "ok", 3)
    assert good_release == ("released", "one", "ok", None)
    assert bad[2:4][0] == "error"
    assert bad_release == ("released", "two", "ok", None)


def test_pool_validation_result_shapes_and_queue_cleanup() -> None:
    for kwargs in (
        {"size": 0},
        {"size": True},
        {"size": 1, "poll_interval_seconds": 0},
        {"size": 1, "preload_modules": (1,)},
    ):
        with pytest.raises(ValueError):
            InteractiveWorkerPool(**kwargs)
    pool = InteractiveWorkerPool(size=1)
    slot = SimpleNamespace(index=0)
    assert pool._interpret_result(slot, job_id="id", envelope=("result", "id", "ok", 7)) == 7
    for envelope in (None, ("result", "other", "ok", 7), ("result", "id", "error", None)):
        with pytest.raises(worker_mod.InteractiveWorkerProtocolError):
            pool._interpret_result(slot, job_id="id", envelope=envelope)
    with pytest.raises(InteractiveWorkerRemoteError) as remote:
        pool._interpret_result(
            slot,
            job_id="id",
            envelope=("result", "id", "error", ("MemoryError", "m", "x", "t", {"x": 1})),
        )
    assert remote.value.terminal_reason == "memory_limited"
    first, second = _Queue(), _Queue()
    pool._close_unstarted_queues(first, second)
    assert (first.closed, second.joined) == (1, 1)


def test_run_rejects_invalid_limits_without_starting() -> None:
    pool = InteractiveWorkerPool(size=1)
    cases = (
        {"timeout_seconds": 0},
        {"timeout_seconds": 1, "absolute_rss_limit_bytes": 0},
        {"timeout_seconds": 1, "memory_growth_limit_bytes": 0},
        {"timeout_seconds": 1, "require_memory_limit": True},
        {
            "timeout_seconds": 1,
            "absolute_rss_limit_bytes": 1,
            "require_memory_limit": True,
        },
    )
    for options in cases:
        with pytest.raises(ValueError):
            pool.run(_returns, affinity_key="x", **options)


def test_wait_for_result_defensive_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    process = SimpleNamespace(pid=1, exitcode=9, is_alive=lambda: False)
    slot = SimpleNamespace(index=0, process=process, result_queue=_Queue(), closed=False)
    monkeypatch.setattr(pool, "_replace_slot", lambda _slot: None)
    with pytest.raises(InteractiveWorkerCrashedError):
        pool._wait_for_result(
            slot,
            job_id="x",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )
    alive = SimpleNamespace(pid=1, exitcode=None, is_alive=lambda: True)
    slot.process = alive
    slot.result_queue = _Queue([pickle.dumps(("bad",))])
    with pytest.raises(worker_mod.InteractiveWorkerProtocolError):
        pool._wait_for_result(
            slot,
            job_id="x",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )


@pytest.mark.parametrize(
    ("exitcode", "memory_growth_limit_bytes", "expected_reason"),
    [
        (-9, 64 * 1024 * 1024, "memory_limited"),
        (-int(signal.SIGABRT), 64 * 1024 * 1024, "memory_limited"),
        (-9, None, "error"),
        (3, 64 * 1024 * 1024, "error"),
    ],
)
def test_crashed_worker_memory_classification_follows_the_one_shot_heuristic(
    monkeypatch: pytest.MonkeyPatch,
    exitcode: int,
    memory_growth_limit_bytes: int | None,
    expected_reason: str,
) -> None:
    """A SIGKILL/SIGABRT-shaped exit under a configured growth cap is a
    hedged memory outcome; without a cap, or for any other exit, it stays a
    plain crash — exactly the one-shot isolated-worker classification."""
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    process = SimpleNamespace(pid=1, exitcode=exitcode, is_alive=lambda: False)
    slot = SimpleNamespace(index=0, process=process, result_queue=_Queue(), closed=False)
    monkeypatch.setattr(pool, "_replace_slot", lambda _slot: None)
    with pytest.raises(InteractiveWorkerCrashedError) as exc_info:
        pool._wait_for_result(
            slot,
            job_id="x",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=memory_growth_limit_bytes,
            require_memory_limit=False,
        )
    assert exc_info.value.terminal_reason == expected_reason
    assert exc_info.value.exitcode == exitcode


def test_worker_death_during_required_rss_sampling_classifies_the_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker can die between the liveness check and the RSS sample. The
    lost sample must not be misreported as a required-enforcement failure;
    the next supervision pass classifies the crash, memory heuristic included."""
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    liveness = iter((True, False, False))
    process = SimpleNamespace(pid=1, exitcode=-9, is_alive=lambda: next(liveness))
    slot = SimpleNamespace(index=0, process=process, result_queue=_Queue(), closed=False)
    monkeypatch.setattr(pool, "_replace_slot", lambda _slot: None)
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: None)
    with pytest.raises(InteractiveWorkerCrashedError) as exc_info:
        pool._wait_for_result(
            slot,
            job_id="x",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=100,
            memory_growth_limit_bytes=100,
            require_memory_limit=True,
        )
    assert exc_info.value.terminal_reason == "memory_limited"


def test_entrypoint_preload_unpicklable_and_malformed_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []
    monkeypatch.setattr(worker_mod.importlib, "import_module", imported.append)
    requests = _Queue(
        [
            pickle.dumps(("run", "x", _unpicklable_result, (), {}, None, False)),
            pickle.dumps(("ack", "x")),
            pickle.dumps(("shutdown",)),
        ]
    )
    results = _Queue()
    worker_mod._interactive_worker_entrypoint(requests, results, ("preload",))
    assert imported == ["preload"]
    assert pickle.loads(results.values[1])[2] == "error"
    with pytest.raises(RuntimeError, match="malformed"):
        worker_mod._interactive_worker_entrypoint(_Queue([pickle.dumps(("bad",))]), _Queue(), ())


def test_entrypoint_holds_native_lease_until_matching_ack_then_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._native_memory_limit as native

    events: list[str] = []

    class Lease:
        backend = None

        def apply(self, *_args, **_kwargs):
            events.append("apply")
            self.backend = "windows_job"

        def restore(self):
            events.append("restore")

        def close(self):
            events.append("close")

    monkeypatch.setattr(
        worker_mod,
        "NativeMemoryLease",
        Lease,
    )
    requests = _Queue(
        [
            pickle.dumps(("run", "x", native.current_native_memory_backend, (), {}, 128, True)),
            pickle.dumps(("ack", "x")),
            pickle.dumps(("shutdown",)),
        ]
    )
    results = _Queue()

    worker_mod._interactive_worker_entrypoint(requests, results, ())

    envelopes = [pickle.loads(value) for value in results.values]
    assert envelopes[1:] == [
        ("result", "x", "ok", "windows_job"),
        ("released", "x", "ok", None),
    ]
    assert events == ["apply", "restore", "close"]
    assert native.current_native_memory_backend() is None


def test_entrypoint_rejects_stale_ack_without_restoring_native_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        worker_mod,
        "NativeMemoryLease",
        lambda: SimpleNamespace(
            backend=None,
            apply=lambda *_args, **_kwargs: events.append("apply"),
            restore=lambda: events.append("restore"),
            close=lambda: events.append("close"),
        ),
    )

    with pytest.raises(RuntimeError, match="acknowledgement"):
        worker_mod._interactive_worker_entrypoint(
            _Queue(
                [
                    pickle.dumps(("run", "x", _returns, (3,), {}, 128, True)),
                    pickle.dumps(("ack", "other")),
                ]
            ),
            _Queue(),
            (),
        )

    assert events == ["apply", "close"]


def test_wait_for_result_acknowledges_before_returning_and_requires_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    requests, results = (
        _Queue(),
        _Queue(
            [
                pickle.dumps(("result", "x", "ok", 7)),
                pickle.dumps(("released", "x", "ok", None)),
            ]
        ),
    )
    slot = SimpleNamespace(
        index=0,
        process=SimpleNamespace(pid=1, exitcode=None, is_alive=lambda: True),
        request_queue=requests,
        result_queue=results,
        closed=False,
    )
    monkeypatch.setattr(pool, "_replace_slot", lambda _slot: None)

    assert (
        pool._wait_for_result(
            slot,
            job_id="x",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )
        == 7
    )
    assert pickle.loads(requests.values[0]) == ("ack", "x")


def test_remote_result_keeps_primary_error_when_release_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    slot = SimpleNamespace(
        index=0,
        process=SimpleNamespace(pid=1, exitcode=None, is_alive=lambda: True),
        request_queue=_Queue(),
        result_queue=_Queue(
            [
                pickle.dumps(("result", "x", "error", ("ValueError", "m", "bad", "tb", None))),
                pickle.dumps(("released", "other", "ok", None)),
            ]
        ),
        closed=False,
    )
    replacements: list[object] = []
    monkeypatch.setattr(pool, "_replace_slot", replacements.append)

    with pytest.raises(InteractiveWorkerRemoteError) as exc_info:
        pool._wait_for_result(
            slot,
            job_id="x",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )

    assert replacements == [slot]
    assert any("release confirmation failed" in note.lower() for note in exc_info.value.__notes__)


def test_start_close_and_singleton_helpers_with_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = InteractiveWorkerPool(size=2)
    slots = [SimpleNamespace(lock=threading.Lock()), SimpleNamespace(lock=threading.Lock())]
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        pool,
        "_start_slot",
        lambda *, index, generation: calls.append((index, generation)) or slots[index],
    )
    monkeypatch.setattr(pool, "_close_slot", lambda *_args, **_kwargs: None)
    pool.start()
    pool.start()
    assert calls == [(0, 1), (1, 1)]
    pool.close()
    with pytest.raises(RuntimeError, match="closed"):
        pool.start()

    created: list[Any] = []
    fake = SimpleNamespace(
        start=lambda: created.append("start"), close=lambda: created.append("close")
    )
    monkeypatch.setattr(worker_mod, "_POOL", None)
    monkeypatch.setattr(worker_mod, "InteractiveWorkerPool", lambda **_kwargs: fake)
    monkeypatch.setattr(worker_mod, "int_env", lambda *_args: 3)
    assert worker_mod.interactive_worker_pool() is fake
    monkeypatch.setattr(worker_mod, "resolve_interactive_execution_mode", lambda: "process")
    worker_mod.start_interactive_worker_pool()
    worker_mod.shutdown_interactive_worker_pool()
    assert created == ["start", "close"]


def test_close_slot_defensive_cleanup_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, **_kwargs: Any) -> None:
            pass

    process = Process()
    slot = worker_mod._WorkerSlot(0, _Queue(), _Queue(), process, threading.Lock(), 1)
    monkeypatch.setattr(
        worker_mod, "_terminate_process", lambda proc: setattr(proc, "alive", False)
    )
    worker_mod.InteractiveWorkerPool._close_slot(slot, graceful=True)
    assert slot.closed
    worker_mod.InteractiveWorkerPool._close_slot(slot, graceful=False)


@pytest.mark.asyncio
async def test_async_worker_wrapper_forwards_and_thread_mode_start_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    fake = SimpleNamespace(run=lambda _function, *_args, **kwargs: calls.append(kwargs) or 5)
    monkeypatch.setattr(worker_mod, "interactive_worker_pool", lambda: fake)
    assert (
        await worker_mod.run_in_interactive_worker(_returns, affinity_key="x", timeout_seconds=1)
        == 5
    )
    assert calls[0]["stop_reason"]() is None
    monkeypatch.setattr(worker_mod, "resolve_interactive_execution_mode", lambda: "thread")
    worker_mod.start_interactive_worker_pool()


def test_stopped_error_rejects_completed_and_payload_must_be_a_mapping() -> None:
    with pytest.raises(ValueError, match="completed"):
        InteractiveWorkerStoppedError("completed")

    class SequencePayloadError(Exception):
        def to_payload(self):
            return ("serialisable", "but not a mapping")

    assert worker_mod._public_exception_payload(SequencePayloadError()) is None


def test_partial_pool_start_closes_every_started_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = InteractiveWorkerPool(size=2)
    first = SimpleNamespace(index=0)
    closed: list[tuple[object, bool]] = []

    def start_slot(*, index: int, generation: int):
        assert generation == 1
        if index == 1:
            raise RuntimeError("second worker failed")
        return first

    monkeypatch.setattr(pool, "_start_slot", start_slot)
    monkeypatch.setattr(
        pool,
        "_close_slot",
        lambda slot, *, graceful: closed.append((slot, graceful)),
    )

    with pytest.raises(RuntimeError, match="second worker failed"):
        pool.start()

    assert closed == [(first, False)]
    assert pool._slots == []


def test_pool_close_preserves_first_failure_and_notes_later_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=2)
    slots = [
        SimpleNamespace(index=0, lock=threading.Lock()),
        SimpleNamespace(index=1, lock=threading.Lock()),
    ]
    pool._slots = slots

    def fail_close(slot, *, graceful: bool) -> None:
        assert graceful is True
        raise OSError(f"close-{slot.index}")

    monkeypatch.setattr(pool, "_close_slot", fail_close)

    with pytest.raises(OSError, match="close-0") as exc_info:
        pool.close()

    assert any("close-1" in note for note in exc_info.value.__notes__)


def test_run_can_be_stopped_while_waiting_for_an_affinity_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)

    class BusyLock:
        def acquire(self, *, timeout: float) -> bool:
            assert timeout > 0
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired lock must not be released")

    slot = SimpleNamespace(lock=BusyLock())
    monkeypatch.setattr(pool, "start", lambda: None)
    monkeypatch.setattr(pool, "_slot_for_affinity", lambda _key: slot)

    with pytest.raises(InteractiveWorkerStoppedError) as exc_info:
        pool.run(
            _returns,
            affinity_key="busy",
            timeout_seconds=1,
            stop_reason=lambda: "superseded",
        )

    assert exc_info.value.terminal_reason == "superseded"


@pytest.mark.parametrize("with_stop_callback", [False, True])
def test_run_keeps_waiting_for_slot_until_lock_is_acquired(
    monkeypatch: pytest.MonkeyPatch,
    with_stop_callback: bool,
) -> None:
    pool = InteractiveWorkerPool(size=1)

    class EventuallyAvailableLock:
        def __init__(self) -> None:
            self.outcomes = [False, True]
            self.released = False

        def acquire(self, *, timeout: float) -> bool:
            assert timeout > 0
            return self.outcomes.pop(0)

        def release(self) -> None:
            self.released = True

    lock = EventuallyAvailableLock()
    slot = SimpleNamespace(lock=lock, request_queue=_Queue())
    monkeypatch.setattr(pool, "start", lambda: None)
    monkeypatch.setattr(pool, "_slot_for_affinity", lambda _key: slot)
    monkeypatch.setattr(pool, "_wait_for_result", lambda *_args, **_kwargs: 7)

    result = pool.run(
        _returns,
        affinity_key="busy",
        timeout_seconds=1,
        stop_reason=(lambda: None) if with_stop_callback else None,
    )

    assert result == 7
    assert lock.released


def test_run_rechecks_closed_state_after_acquiring_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    lock = threading.Lock()
    slot = SimpleNamespace(lock=lock)
    monkeypatch.setattr(pool, "start", lambda: None)
    monkeypatch.setattr(pool, "_slot_for_affinity", lambda _key: slot)
    pool._closed = True

    with pytest.raises(RuntimeError, match="closed"):
        pool.run(_returns, affinity_key="closed", timeout_seconds=1)

    assert lock.acquire(blocking=False)
    lock.release()


def test_growth_limit_required_mode_uses_native_cap_when_baseline_is_unobservable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    slot = SimpleNamespace(
        index=0,
        lock=threading.Lock(),
        process=SimpleNamespace(pid=42),
        request_queue=_Queue(),
    )
    replaced: list[object] = []
    monkeypatch.setattr(pool, "start", lambda: None)
    monkeypatch.setattr(pool, "_slot_for_affinity", lambda _key: slot)
    monkeypatch.setattr(pool, "_stop_and_replace", replaced.append)
    monkeypatch.setattr(worker_mod, "native_memory_caps_supported", lambda: True)
    monkeypatch.setattr(pool, "_wait_for_result", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: None)

    assert (
        pool.run(
            _returns,
            affinity_key="lineage",
            timeout_seconds=1,
            memory_growth_limit_bytes=100,
            require_memory_limit=True,
        )
        == 7
    )

    assert replaced == []


def test_optional_growth_limit_warns_and_continues_without_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    slot = SimpleNamespace(
        index=0,
        lock=threading.Lock(),
        process=SimpleNamespace(pid=42),
        request_queue=_Queue(),
    )
    warnings: list[dict[str, object]] = []
    monkeypatch.setattr(pool, "start", lambda: None)
    monkeypatch.setattr(pool, "_slot_for_affinity", lambda _key: slot)
    monkeypatch.setattr(pool, "_wait_for_result", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: None)
    monkeypatch.setattr(
        worker_mod.logger,
        "warning",
        lambda _event, **fields: warnings.append(fields),
    )

    result = pool.run(
        _returns,
        affinity_key="lineage",
        timeout_seconds=1,
        memory_growth_limit_bytes=100,
        require_memory_limit=False,
    )

    assert result == 7
    assert warnings == [{"worker_index": 0}]


def test_unserialisable_worker_request_fails_before_queue_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    slot = SimpleNamespace(
        lock=threading.Lock(),
        process=SimpleNamespace(pid=42),
        request_queue=_Queue(),
    )
    monkeypatch.setattr(pool, "start", lambda: None)
    monkeypatch.setattr(pool, "_slot_for_affinity", lambda _key: slot)

    with pytest.raises(worker_mod.InteractiveWorkerProtocolError, match="not serialisable"):
        pool.run(lambda: None, affinity_key="lineage", timeout_seconds=1)

    assert slot.request_queue.values == []


def test_slot_lookup_fails_loudly_for_closed_or_unstarted_pool() -> None:
    pool = InteractiveWorkerPool(size=1)
    pool._closed = True
    with pytest.raises(RuntimeError, match="closed"):
        pool._slot_for_affinity("lineage")

    pool._closed = False
    with pytest.raises(RuntimeError, match="did not start"):
        pool._slot_for_affinity("lineage")


def _start_slot_test_pool(monkeypatch: pytest.MonkeyPatch, process):
    pool = InteractiveWorkerPool(size=1)
    queues = [_Queue(), _Queue()]
    monkeypatch.setattr(worker_mod, "create_worker_queue", lambda *_args: queues.pop(0))
    pool._ctx = SimpleNamespace(Process=lambda **_kwargs: process)
    return pool


def test_start_slot_preserves_host_error_and_closes_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(pid=42, start=lambda: None)
    pool = _start_slot_test_pool(monkeypatch, process)
    monkeypatch.setattr(
        pool,
        "_wait_for_ready",
        lambda *_args: (_ for _ in ()).throw(worker_mod.IsolatedWorkerHostError("host failed")),
    )

    with pytest.raises(worker_mod.IsolatedWorkerHostError, match="host failed"):
        pool._start_slot(index=0, generation=1)


@pytest.mark.parametrize("pid", [None, 42])
def test_start_slot_wraps_startup_failure_and_attempts_bounded_termination(
    monkeypatch: pytest.MonkeyPatch,
    pid: int | None,
) -> None:
    process = SimpleNamespace(pid=pid, start=lambda: None)
    pool = _start_slot_test_pool(monkeypatch, process)
    monkeypatch.setattr(
        pool,
        "_wait_for_ready",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("readiness failed")),
    )
    terminations: list[object] = []
    monkeypatch.setattr(worker_mod, "_terminate_process", terminations.append)

    with pytest.raises(worker_mod.InteractiveWorkerStartError, match="readiness failed"):
        pool._start_slot(index=0, generation=1)

    assert terminations == ([] if pid is None else [process])


def test_start_slot_notes_termination_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    process = SimpleNamespace(pid=42, start=lambda: None)
    pool = _start_slot_test_pool(monkeypatch, process)
    monkeypatch.setattr(
        pool,
        "_wait_for_ready",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("readiness failed")),
    )
    monkeypatch.setattr(
        worker_mod,
        "_terminate_process",
        lambda _process: (_ for _ in ()).throw(OSError("terminate failed")),
    )

    with pytest.raises(worker_mod.InteractiveWorkerStartError) as exc_info:
        pool._start_slot(index=0, generation=1)

    assert "worker termination failed" in str(exc_info.value.__cause__.__notes__)


def test_ready_handshake_rejects_wrong_envelope() -> None:
    pool = InteractiveWorkerPool(size=1)
    process = SimpleNamespace(pid=42, exitcode=None, is_alive=lambda: True)
    results = _Queue([pickle.dumps(("ready", 99))])

    with pytest.raises(worker_mod.InteractiveWorkerProtocolError, match="invalid ready"):
        pool._wait_for_ready(process, results)


def test_slot_replacement_rejects_close_race_and_generation_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for closed, replace_current, expected in (
        (True, True, RuntimeError),
        (False, False, worker_mod.InteractiveWorkerProtocolError),
    ):
        pool = InteractiveWorkerPool(size=1)
        original = SimpleNamespace(index=0, generation=1)
        current = original if replace_current else SimpleNamespace(index=0, generation=1)
        replacement = SimpleNamespace(index=0, generation=2)
        pool._slots = [current]
        pool._closed = closed
        closed_slots: list[object] = []
        monkeypatch.setattr(
            pool,
            "_close_slot",
            lambda slot, *, graceful: closed_slots.append((slot, graceful)),
        )
        monkeypatch.setattr(pool, "_start_slot", lambda **_kwargs: replacement)

        with pytest.raises(expected):
            pool._replace_slot(original)

        assert closed_slots == [(original, False), (replacement, False)]


class _ScriptedResultQueue:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)

    def get(self, **_kwargs: Any) -> object:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_optional_sampler_warning_is_once_and_under_limit_sample_keeps_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    ok = pickle.dumps(("result", "job", "ok", 7))
    released = pickle.dumps(("released", "job", "ok", None))
    process = SimpleNamespace(pid=42, exitcode=None, is_alive=lambda: True)
    warnings: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker_mod.logger,
        "warning",
        lambda _event, **fields: warnings.append(fields),
    )
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: None)
    unavailable_slot = SimpleNamespace(
        index=0,
        process=process,
        request_queue=_Queue(),
        result_queue=_ScriptedResultQueue(
            queue.Empty(), queue.Empty(), ok, queue.Empty(), released
        ),
        closed=False,
    )

    assert (
        pool._wait_for_result(
            unavailable_slot,
            job_id="job",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=100,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )
        == 7
    )
    assert len(warnings) == 1

    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: 50)
    under_limit_slot = SimpleNamespace(
        index=0,
        process=process,
        request_queue=_Queue(),
        result_queue=_ScriptedResultQueue(queue.Empty(), ok, released),
        closed=False,
    )
    assert (
        pool._wait_for_result(
            under_limit_slot,
            job_id="job",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=100,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )
        == 7
    )


class _CloseProcess:
    def __init__(
        self,
        *,
        alive: bool,
        join_error: BaseException | None = None,
    ) -> None:
        self.alive = alive
        self.join_error = join_error

    def is_alive(self) -> bool:
        return self.alive

    def join(self, **_kwargs: Any) -> None:
        if self.join_error is not None:
            raise self.join_error


class _CloseQueue(_Queue):
    def __init__(
        self,
        *,
        put_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.put_error = put_error
        self.close_error = close_error

    def put(self, value: Any, **kwargs: Any) -> None:
        if self.put_error is not None:
            raise self.put_error
        super().put(value, **kwargs)

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        super().close()


def _close_slot(process, request_queue=None, result_queue=None):
    return worker_mod._WorkerSlot(
        0,
        request_queue or _CloseQueue(),
        result_queue or _CloseQueue(),
        process,
        threading.Lock(),
        1,
    )


def test_close_slot_aggregates_graceful_termination_join_and_queue_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _CloseProcess(alive=True, join_error=RuntimeError("join failed"))
    slot = _close_slot(
        process,
        _CloseQueue(put_error=OSError("shutdown put failed"), close_error=OSError("request close")),
        _CloseQueue(close_error=OSError("result close")),
    )
    monkeypatch.setattr(
        worker_mod,
        "_terminate_process",
        lambda _process: (_ for _ in ()).throw(RuntimeError("terminate failed")),
    )

    with pytest.raises(OSError, match="shutdown put failed") as exc_info:
        worker_mod.InteractiveWorkerPool._close_slot(slot, graceful=True)

    notes = " ".join(exc_info.value.__notes__)
    assert "terminate failed" in notes
    assert "join failed" in notes
    assert "request close" in notes
    assert "result close" in notes
    assert slot.closed


def test_close_slot_uses_each_first_failure_source(monkeypatch: pytest.MonkeyPatch) -> None:
    termination_process = _CloseProcess(alive=True)
    termination_slot = _close_slot(termination_process)
    monkeypatch.setattr(
        worker_mod,
        "_terminate_process",
        lambda _process: (_ for _ in ()).throw(RuntimeError("terminate failed")),
    )
    with pytest.raises(RuntimeError, match="terminate failed"):
        worker_mod.InteractiveWorkerPool._close_slot(termination_slot, graceful=False)

    join_slot = _close_slot(_CloseProcess(alive=False, join_error=RuntimeError("join failed")))
    with pytest.raises(RuntimeError, match="join failed"):
        worker_mod.InteractiveWorkerPool._close_slot(join_slot, graceful=False)

    queue_slot = _close_slot(
        _CloseProcess(alive=False),
        _CloseQueue(close_error=OSError("queue failed")),
    )
    with pytest.raises(OSError, match="queue failed"):
        worker_mod.InteractiveWorkerPool._close_slot(queue_slot, graceful=False)


def test_close_slot_reports_process_that_survives_without_other_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _CloseProcess(alive=True)
    slot = _close_slot(process)
    monkeypatch.setattr(worker_mod, "_terminate_process", lambda _process: None)

    with pytest.raises(worker_mod.IsolatedWorkerTerminationError):
        worker_mod.InteractiveWorkerPool._close_slot(slot, graceful=False)


def test_close_slot_surfaces_native_cleanup_failure_after_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _CloseProcess(alive=False)
    process.pid = 42
    slot = _close_slot(process)
    monkeypatch.setattr(
        worker_mod,
        "cleanup_private_cgroups_for_pid",
        lambda _pid: (_ for _ in ()).throw(OSError("cgroup cleanup failed")),
    )

    with pytest.raises(OSError, match="cgroup cleanup failed"):
        worker_mod.InteractiveWorkerPool._close_slot(slot, graceful=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_async_wrapper_cancellation_waits_for_worker_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_failure: bool,
) -> None:
    started = threading.Event()

    def run(_function, *_args, stop_reason, **_kwargs):
        started.set()
        deadline = time.monotonic() + 2
        while stop_reason() is None and time.monotonic() < deadline:
            time.sleep(0.001)
        if cleanup_failure:
            raise RuntimeError("cleanup failed")
        raise InteractiveWorkerStoppedError("cancelled")

    monkeypatch.setattr(worker_mod, "interactive_worker_pool", lambda: SimpleNamespace(run=run))
    errors: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker_mod.logger,
        "error",
        lambda _event, **fields: errors.append(fields),
    )
    task = asyncio.create_task(
        worker_mod.run_in_interactive_worker(
            _returns,
            affinity_key="lineage",
            timeout_seconds=5,
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert bool(errors) is cleanup_failure


def test_shutdown_singleton_is_a_noop_when_pool_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_mod, "_POOL", None)
    worker_mod.shutdown_interactive_worker_pool()


def test_entrypoint_reports_native_apply_and_restore_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Queue:
        def __init__(self, values: list[object]) -> None:
            self.values = values
            self.puts: list[bytes] = []

        def get(self) -> object:
            return self.values.pop(0)

        def put(self, value: bytes) -> None:
            self.puts.append(value)

    request = pickle.dumps(("run", "job", _returns, (), {}, 10, True))
    requests = Queue([request, pickle.dumps(("ack", "job")), pickle.dumps(("shutdown",))])
    results = Queue([])
    monkeypatch.setattr(
        worker_mod.NativeMemoryLease,
        "apply",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cap")),
    )
    worker_mod._interactive_worker_entrypoint(requests, results, ())
    envelope = pickle.loads(results.puts[1])
    assert envelope[2] == "error" and envelope[3][0] == "RuntimeError"

    requests = Queue([request, pickle.dumps(("ack", "job"))])
    results = Queue([])
    monkeypatch.setattr(worker_mod.NativeMemoryLease, "apply", lambda *_a, **_k: None)
    monkeypatch.setattr(
        worker_mod.NativeMemoryLease,
        "restore",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("restore")),
    )
    with pytest.raises(RuntimeError, match="restore"):
        worker_mod._interactive_worker_entrypoint(requests, results, ())
    released = pickle.loads(results.puts[-1])
    assert released[:3] == ("released", "job", "error")


def test_wait_for_result_preserves_remote_error_when_ack_or_release_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    remote = pickle.dumps(("result", "job", "error", ("ValueError", "x", "bad", "tb", None)))
    process = SimpleNamespace(pid=1, exitcode=None, is_alive=lambda: True)
    slot = SimpleNamespace(
        index=0,
        process=process,
        result_queue=_ScriptedResultQueue(remote),
        request_queue=SimpleNamespace(put=lambda *_a: (_ for _ in ()).throw(OSError("ack"))),
    )
    replaced: list[object] = []
    monkeypatch.setattr(pool, "_stop_and_replace", replaced.append)
    with pytest.raises(InteractiveWorkerRemoteError) as exc_info:
        pool._wait_for_result(
            slot,
            job_id="job",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )
    assert "release confirmation" in " ".join(exc_info.value.__notes__) and replaced == [slot]


def test_wait_and_protocol_error_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = InteractiveWorkerPool(size=1)
    process = SimpleNamespace(pid=1, exitcode=None, is_alive=lambda: True)
    slot = SimpleNamespace(
        index=3,
        process=process,
        result_queue=_ScriptedResultQueue(queue.Empty()),
        request_queue=_Queue(),
    )
    replaced: list[object] = []
    monkeypatch.setattr(pool, "_stop_and_replace", replaced.append)
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: None)
    with pytest.raises(RuntimeError, match="could not sample"):
        pool._wait_for_result(
            slot,
            job_id="job",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=10,
            memory_growth_limit_bytes=None,
            require_memory_limit=True,
        )
    assert replaced == [slot]
    with pytest.raises(worker_mod.InteractiveWorkerProtocolError):
        pool._interpret_release(job_id="job", envelope=("released", "job", "ok", 1))
    with pytest.raises(worker_mod.InteractiveWorkerProtocolError):
        pool._interpret_release(job_id="job", envelope=("released", "job", "error", "bad"))


def test_wait_for_release_covers_protocol_stop_and_rss_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    process = SimpleNamespace(pid=5, exitcode=None, is_alive=lambda: True)

    malformed = SimpleNamespace(
        index=0,
        process=process,
        result_queue=_ScriptedResultQueue(pickle.dumps(("bad",))),
    )
    with pytest.raises(worker_mod.InteractiveWorkerProtocolError):
        pool._wait_for_release(
            malformed,
            job_id="job",
            deadline=time.monotonic() + 1,
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
            sampler_unavailable_logged=False,
        )

    empty = SimpleNamespace(
        index=0,
        process=process,
        result_queue=_ScriptedResultQueue(queue.Empty()),
    )
    with pytest.raises(InteractiveWorkerStoppedError):
        pool._wait_for_release(
            empty,
            job_id="job",
            deadline=time.monotonic() + 1,
            timeout_seconds=1,
            stop_reason=lambda: "cancelled",
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
            sampler_unavailable_logged=False,
        )

    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: 11)
    empty = SimpleNamespace(
        index=0,
        process=process,
        result_queue=_ScriptedResultQueue(queue.Empty()),
    )
    with pytest.raises(InteractiveWorkerMemoryLimitError):
        pool._wait_for_release(
            empty,
            job_id="job",
            deadline=time.monotonic() + 1,
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=10,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
            sampler_unavailable_logged=False,
        )


def test_wait_for_result_surfaces_ack_and_release_errors_without_user_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1)
    process = SimpleNamespace(pid=5, exitcode=None, is_alive=lambda: True)
    slot = SimpleNamespace(
        index=0,
        process=process,
        result_queue=_ScriptedResultQueue(pickle.dumps(("result", "job", "ok", 1))),
        request_queue=SimpleNamespace(put=lambda *_args: (_ for _ in ()).throw(OSError("ack"))),
    )
    monkeypatch.setattr(pool, "_stop_and_replace", lambda _slot: None)
    with pytest.raises(worker_mod.InteractiveWorkerProtocolError, match="acknowledgement"):
        pool._wait_for_result(
            slot,
            job_id="job",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )

    slot = SimpleNamespace(
        index=0,
        process=process,
        result_queue=_ScriptedResultQueue(pickle.dumps(("result", "job", "ok", 1))),
        request_queue=_Queue(),
    )
    monkeypatch.setattr(
        pool,
        "_wait_for_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("release")),
    )
    with pytest.raises(RuntimeError, match="release"):
        pool._wait_for_result(
            slot,
            job_id="job",
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
        )


def test_memory_limit_error_exposes_public_payload() -> None:
    error = InteractiveWorkerMemoryLimitError(rss_bytes=101, limit_bytes=100)

    assert error.to_payload() == {
        "error_code": "memory_limit",
        "rss_bytes": 101,
        "rss_limit_bytes": 100,
        "reason": "worker_rss_limit_exceeded",
    }


def test_wait_for_release_covers_shutdown_deadline_and_dead_child() -> None:
    pool = InteractiveWorkerPool(size=1)
    alive = SimpleNamespace(pid=5, exitcode=None, is_alive=lambda: True)
    slot = SimpleNamespace(index=0, process=alive, result_queue=_ScriptedResultQueue())
    pool._shutdown_event.set()
    with pytest.raises(InteractiveWorkerStoppedError, match="cancelled"):
        pool._wait_for_release(
            slot,
            job_id="job",
            deadline=time.monotonic() + 1,
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
            sampler_unavailable_logged=False,
        )
    pool._shutdown_event.clear()
    with pytest.raises(InteractiveWorkerTimeoutError):
        pool._wait_for_release(
            slot,
            job_id="job",
            deadline=time.monotonic() - 1,
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
            sampler_unavailable_logged=False,
        )
    dead = SimpleNamespace(pid=5, exitcode=17, is_alive=lambda: False)
    with pytest.raises(InteractiveWorkerCrashedError) as exc_info:
        pool._wait_for_release(
            SimpleNamespace(
                index=0, process=dead, result_queue=_ScriptedResultQueue(queue.Empty())
            ),
            job_id="job",
            deadline=time.monotonic() + 1,
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=None,
            memory_growth_limit_bytes=None,
            require_memory_limit=False,
            sampler_unavailable_logged=False,
        )
    assert exc_info.value.exitcode == 17


def test_wait_for_release_retries_stop_and_rss_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.01)
    process = SimpleNamespace(pid=5, exitcode=None, is_alive=lambda: True)
    released = pickle.dumps(("released", "job", "ok", None))
    stop_polls = iter((None, "superseded"))
    pool._wait_for_release(
        SimpleNamespace(
            index=0,
            process=process,
            result_queue=_ScriptedResultQueue(queue.Empty(), released),
        ),
        job_id="job",
        deadline=time.monotonic() + 1,
        timeout_seconds=1,
        stop_reason=lambda: next(stop_polls),
        absolute_rss_limit_bytes=None,
        memory_growth_limit_bytes=None,
        require_memory_limit=False,
        sampler_unavailable_logged=False,
    )

    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: None)
    with pytest.raises(RuntimeError, match="could not sample"):
        pool._wait_for_release(
            SimpleNamespace(
                index=0, process=process, result_queue=_ScriptedResultQueue(queue.Empty())
            ),
            job_id="job",
            deadline=time.monotonic() + 1,
            timeout_seconds=1,
            stop_reason=None,
            absolute_rss_limit_bytes=100,
            memory_growth_limit_bytes=None,
            require_memory_limit=True,
            sampler_unavailable_logged=False,
        )
    warnings: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker_mod.logger, "warning", lambda _event, **fields: warnings.append(fields)
    )
    pool._wait_for_release(
        SimpleNamespace(
            index=2,
            process=process,
            result_queue=_ScriptedResultQueue(queue.Empty(), queue.Empty(), released),
        ),
        job_id="job",
        deadline=time.monotonic() + 1,
        timeout_seconds=1,
        stop_reason=None,
        absolute_rss_limit_bytes=100,
        memory_growth_limit_bytes=None,
        require_memory_limit=False,
        sampler_unavailable_logged=False,
    )
    assert warnings == [{"worker_index": 2}]

    samples = iter((50, 50))
    monkeypatch.setattr(worker_mod, "process_rss_bytes", lambda _pid: next(samples))
    pool._wait_for_release(
        SimpleNamespace(
            index=0,
            process=process,
            result_queue=_ScriptedResultQueue(queue.Empty(), released),
        ),
        job_id="job",
        deadline=time.monotonic() + 1,
        timeout_seconds=1,
        stop_reason=None,
        absolute_rss_limit_bytes=100,
        memory_growth_limit_bytes=None,
        require_memory_limit=False,
        sampler_unavailable_logged=True,
    )


def test_interpret_release_surfaces_valid_remote_error_evidence() -> None:
    pool = InteractiveWorkerPool(size=1)

    with pytest.raises(InteractiveWorkerRemoteError) as exc_info:
        pool._interpret_release(
            job_id="job",
            envelope=("released", "job", "error", ("ValueError", "mod", "bad", "tb", {"x": 1})),
        )

    assert exc_info.value.remote_message == "bad"
    assert exc_info.value.public_payload == {"x": 1}


def test_close_slot_notes_native_cleanup_failure_after_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _CloseProcess(alive=False, join_error=RuntimeError("join failed"))
    process.pid = 42
    slot = _close_slot(process)
    monkeypatch.setattr(
        worker_mod,
        "cleanup_private_cgroups_for_pid",
        lambda _pid: (_ for _ in ()).throw(OSError("cgroup cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="join failed") as exc_info:
        worker_mod.InteractiveWorkerPool._close_slot(slot, graceful=False)

    assert (
        "native memory resource cleanup failed: cgroup cleanup failed" in exc_info.value.__notes__
    )
