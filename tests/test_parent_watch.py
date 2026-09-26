"""Every kind of haute worker ends when the server process that spawned it dies."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from haute._parent_watch import PARENT_PID_ENV
from haute._process_memory import process_rss_bytes


def block_forever(*_args: object) -> None:
    while True:
        time.sleep(0.05)


def report_parent_pid_env() -> str | None:
    import os

    return os.environ.get(PARENT_PID_ENV)


_POOL_HARNESS = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {tests_dir!r})
    from haute._interactive_workers import InteractiveWorkerPool

    pool = InteractiveWorkerPool(size=1, polars_threads=1)
    pool.start()
    print("WORKER_PID", pool._slots[0].process.pid, flush=True)
    time.sleep(600)
    """
)

_ISOLATED_HARNESS = textwrap.dedent(
    """
    import sys, threading, time
    sys.path.insert(0, {tests_dir!r})
    import multiprocessing as mp
    from haute import _worker_isolation
    from haute._worker_isolation import IsolatedWorkerConfig, run_isolated_worker
    import test_parent_watch as commands

    real_start = _worker_isolation.start_process_with_environment

    def announce(process, environment):
        real_start(process, environment)
        print("WORKER_PID", process.pid, flush=True)

    _worker_isolation.start_process_with_environment = announce
    threading.Thread(
        target=lambda: run_isolated_worker(
            commands.block_forever,
            config=IsolatedWorkerConfig(timeout_seconds=None, memory_limit_bytes=None),
        ),
        daemon=True,
    ).start()
    time.sleep(600)
    """
)


def _exits(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process_rss_bytes(pid) is None:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("harness", [_POOL_HARNESS, _ISOLATED_HARNESS], ids=["pool", "isolated"])
def test_a_worker_exits_when_its_server_process_is_killed(harness: str) -> None:
    script = harness.format(tests_dir=str(Path(__file__).parent))
    server = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert server.stdout is not None
        worker_pid = next(
            int(line.split()[1]) for line in server.stdout if line.startswith("WORKER_PID ")
        )
        time.sleep(0.5)
        server.kill()
        server.wait(timeout=10)
        assert _exits(worker_pid, 5)
    finally:
        if server.poll() is None:
            server.kill()


def test_every_spawn_records_the_servers_pid_for_its_worker() -> None:
    import os

    from haute._worker_isolation import IsolatedWorkerConfig, run_isolated_worker

    recorded = run_isolated_worker(
        report_parent_pid_env,
        config=IsolatedWorkerConfig(timeout_seconds=60, memory_limit_bytes=None),
    )
    assert recorded == str(os.getpid())
    # The spawn's override is restored: the server itself carries no parent pid.
    assert os.environ.get(PARENT_PID_ENV) is None
