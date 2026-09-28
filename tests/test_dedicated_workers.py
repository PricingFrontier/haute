"""Dedicated workers: one owner, state kept between commands, a cap re-applied per command."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from haute._dedicated_workers import (
    DedicatedWorker,
    DedicatedWorkerDeadError,
    child_limit_evidence_counter,
    open_dedicated_workers,
    record_job_notification,
    shutdown_dedicated_workers,
)
from haute._interactive_workers import (
    InteractiveWorkerCrashedError,
    InteractiveWorkerError,
    InteractiveWorkerRemoteError,
    InteractiveWorkerStartError,
    InteractiveWorkerStoppedError,
)
from haute._native_memory_limit import JOB_OBJECT_MSG_JOB_MEMORY_LIMIT, native_memory_caps_supported
from haute._process_memory import process_rss_bytes

_MIB = 1024 * 1024
_GROWTH = 512 * _MIB

pytestmark = pytest.mark.skipif(
    not native_memory_caps_supported(), reason="needs a native memory cap"
)

# ── Commands (module level, so the spawned child imports them) ──────────

_STATE: dict[str, int] = {}


def remember(value: int) -> int:
    _STATE["value"] = value
    return value


def recall() -> int:
    return _STATE["value"]


def allocate(n_bytes: int) -> int:
    block = bytearray(n_bytes)
    return len(block)


def block_forever() -> None:
    while True:
        time.sleep(0.05)


def notified_memory_error() -> None:
    record_job_notification(JOB_OBJECT_MSG_JOB_MEMORY_LIMIT, child_limit_evidence_counter())
    raise MemoryError("the job refused an allocation")


def other_job_message_then_memory_error() -> None:
    record_job_notification(JOB_OBJECT_MSG_JOB_MEMORY_LIMIT + 1, child_limit_evidence_counter())
    raise MemoryError("an allocation failed")


def plain_memory_error() -> None:
    raise MemoryError("an allocation failed")


def die_holding_evidence_lock() -> None:
    counter = child_limit_evidence_counter()
    counter._value.get_lock().acquire()  # noqa: SLF001 - simulating a death mid-write
    os._exit(3)


def value_error() -> None:
    raise ValueError("a command's own failure")


# ── Helpers ─────────────────────────────────────────────────────────────


@pytest.fixture()
def worker():
    open_dedicated_workers()
    created = DedicatedWorker(owner="test", polars_threads=2, process_name="haute-test-dedicated")
    created.start(start_timeout_seconds=60)
    yield created
    created.terminate("cancelled")


def _run(worker: DedicatedWorker, fn, *args, growth: int = _GROWTH, **kwargs):
    return worker.run(fn, *args, growth_bytes=growth, required=True, timeout_seconds=60, **kwargs)


def _wait_for_exit(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process_rss_bytes(pid) is None:
            return True
        time.sleep(0.1)
    return False


# ── Tests ───────────────────────────────────────────────────────────────


def test_state_survives_between_commands(worker: DedicatedWorker) -> None:
    assert _run(worker, remember, 41) == 41
    assert _run(worker, recall) == 41


def test_every_command_reapplies_the_cap_from_its_own_growth(worker: DedicatedWorker) -> None:
    _run(worker, remember, 1, growth=256 * _MIB)
    first = worker.last_cap
    assert first is not None and first.backend is not None
    assert first.ceiling_bytes - first.baseline_bytes >= 256 * _MIB
    _run(worker, remember, 2, growth=128 * _MIB)
    second = worker.last_cap
    assert second is not None
    assert second.growth_bytes == 128 * _MIB
    # The second ceiling is sized from the second growth, not left at the first.
    assert second.ceiling_bytes < first.ceiling_bytes + 64 * _MIB


def test_an_allocation_over_the_cap_fails_only_the_worker(worker: DedicatedWorker) -> None:
    with pytest.raises(InteractiveWorkerRemoteError) as raised:
        _run(worker, allocate, 1024 * _MIB, growth=128 * _MIB)
    assert raised.value.terminal_reason == "memory_limited"
    death = worker.death
    assert death is not None and death.kind == "remote_memory"
    assert death.memory_evidence in {"cap_confirmed", "suspected"}
    assert not worker.alive
    with pytest.raises(DedicatedWorkerDeadError):
        _run(worker, recall)


def test_a_command_that_fits_its_grant_runs(worker: DedicatedWorker) -> None:
    # Small, but above what installing an RLIMIT_AS cap itself costs (it starts
    # the Polars pools inside the command on Linux).
    assert _run(worker, allocate, 1 * _MIB, growth=64 * _MIB) == 1 * _MIB


def test_a_non_memory_failure_leaves_the_worker_running(worker: DedicatedWorker) -> None:
    with pytest.raises(InteractiveWorkerRemoteError):
        _run(worker, value_error)
    assert worker.alive
    assert _run(worker, remember, 5) == 5


def test_a_job_memory_notification_confirms_the_cap(worker: DedicatedWorker) -> None:
    with pytest.raises(InteractiveWorkerRemoteError):
        _run(worker, notified_memory_error)
    assert worker.death is not None and worker.death.memory_evidence == "cap_confirmed"


def test_a_missing_notification_leaves_the_cap_suspected(worker: DedicatedWorker) -> None:
    with pytest.raises(InteractiveWorkerRemoteError):
        _run(worker, plain_memory_error)
    assert worker.death is not None and worker.death.memory_evidence == "suspected"


def test_another_job_message_is_not_limit_evidence(worker: DedicatedWorker) -> None:
    with pytest.raises(InteractiveWorkerRemoteError):
        _run(worker, other_job_message_then_memory_error)
    assert worker.death is not None and worker.death.memory_evidence == "suspected"


def test_a_child_dying_holding_the_evidence_lock_never_stalls(worker: DedicatedWorker) -> None:
    started = time.monotonic()
    with pytest.raises(InteractiveWorkerCrashedError):
        _run(worker, die_holding_evidence_lock)
    assert time.monotonic() - started < 5
    assert worker.death is not None and worker.death.memory_evidence == "none"


def test_terminate_stops_a_running_command_promptly(worker: DedicatedWorker) -> None:
    errors: list[BaseException] = []

    def run_blocked() -> None:
        try:
            _run(worker, block_forever)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_blocked)
    thread.start()
    time.sleep(0.5)
    pid = worker.pid
    started = time.monotonic()
    worker.terminate("cancelled")
    thread.join(timeout=10)
    assert time.monotonic() - started < 5
    assert errors and isinstance(errors[0], InteractiveWorkerStoppedError)
    assert pid is not None and _wait_for_exit(pid, 5)


def test_the_worker_outlives_the_thread_that_started_it() -> None:
    open_dedicated_workers()
    holder: list[DedicatedWorker] = []

    def start() -> None:
        created = DedicatedWorker(owner="test", polars_threads=2, process_name="haute-test-thread")
        created.start(start_timeout_seconds=60)
        holder.append(created)

    thread = threading.Thread(target=start)
    thread.start()
    thread.join()
    created = holder[0]
    try:
        time.sleep(1.5)
        assert created.alive
        assert _run(created, remember, 7) == 7
    finally:
        created.terminate("cancelled")


def test_shutdown_fences_new_workers_and_ends_live_ones() -> None:
    open_dedicated_workers()
    created = DedicatedWorker(owner="test", polars_threads=2, process_name="haute-test-fence")
    created.start(start_timeout_seconds=60)
    pid = created.pid
    try:
        shutdown_dedicated_workers()
        assert pid is not None and _wait_for_exit(pid, 5)
        refused = DedicatedWorker(owner="test", polars_threads=2, process_name="haute-refused")
        with pytest.raises(InteractiveWorkerStartError):
            refused.start(start_timeout_seconds=60)
    finally:
        open_dedicated_workers()
        created.terminate("cancelled")


_HARNESS = textwrap.dedent(
    """
    import sys, threading, time
    sys.path.insert(0, {tests_dir!r})
    from haute._dedicated_workers import DedicatedWorker
    import test_dedicated_workers as commands

    worker = DedicatedWorker(owner="harness", polars_threads=2, process_name="haute-harness")
    worker.start(start_timeout_seconds=60)
    if {busy!r}:
        threading.Thread(
            target=lambda: worker.run(
                commands.block_forever, growth_bytes=None, required=False
            ),
            daemon=True,
        ).start()
    print("WORKER_PID", worker.pid, flush=True)
    time.sleep(600)
    """
)


@pytest.mark.parametrize("busy", [False, True], ids=["idle", "running"])
def test_the_worker_exits_when_the_server_process_dies(busy: bool) -> None:
    script = _HARNESS.format(tests_dir=str(Path(__file__).parent), busy=busy)
    harness = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert harness.stdout is not None
        child_pid = next(
            int(line.split()[1]) for line in harness.stdout if line.startswith("WORKER_PID ")
        )
        time.sleep(0.5 if busy else 0.1)
        harness.kill()
        harness.wait(timeout=10)
        assert _wait_for_exit(child_pid, 5)
    finally:
        if harness.poll() is None:
            harness.kill()


def big_result() -> bytes:
    return b"x" * (4 * _MIB)


def test_a_result_over_the_owner_bound_fails_the_command_not_the_worker(
    worker: DedicatedWorker,
) -> None:
    with pytest.raises(InteractiveWorkerRemoteError) as raised:
        _run(worker, big_result, max_result_bytes=1 * _MIB)
    assert raised.value.remote_type == "WorkerResultTooLargeError"
    assert worker.alive
    assert _run(worker, remember, 3) == 3


def test_a_worker_terminated_before_it_starts_never_spawns() -> None:
    open_dedicated_workers()
    created = DedicatedWorker(owner="test", polars_threads=2, process_name="haute-test-early")
    created.terminate("cancelled")
    with pytest.raises(InteractiveWorkerStoppedError):
        created.start(start_timeout_seconds=60)
    assert created.pid is None


def test_a_worker_terminated_while_it_spawns_does_not_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._dedicated_workers as dedicated

    open_dedicated_workers()
    created = DedicatedWorker(owner="test", polars_threads=2, process_name="haute-test-racing")
    real_spawn = dedicated.start_process_with_environment
    stopper: list[threading.Thread] = []

    def spawn_while_terminated(process, environment):  # noqa: ANN001 - the helper's shape
        thread = threading.Thread(target=created.terminate, args=("cancelled",))
        thread.start()
        stopper.append(thread)
        time.sleep(0.3)  # the terminate is now waiting for the spawn to finish
        real_spawn(process, environment)

    monkeypatch.setattr(dedicated, "start_process_with_environment", spawn_while_terminated)
    with pytest.raises(InteractiveWorkerError):
        created.start(start_timeout_seconds=60)
    stopper[0].join(timeout=30)
    assert created.pid is not None and _wait_for_exit(created.pid, 10)
