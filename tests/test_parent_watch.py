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
    # The server's job runs in its project, which the request carries.
    from pathlib import Path
    from haute._project_context import ProjectContext, context_thread, project_scope

    with project_scope(ProjectContext.at(Path.cwd())):
        context_thread(
            lambda: run_isolated_worker(
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


_WAITER = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from haute._parent_watch import wait_for_process_exit

    def held():
        if sys.platform == "win32":
            import psutil
            return psutil.Process().num_handles()
        return len(os.listdir("/proc/self/fd"))

    parent, result = int(sys.argv[1]), Path(sys.argv[2])
    # Load what the wait and the count use once, so only what the wait holds is counted.
    import ctypes, select, subprocess
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    wait_for_process_exit(finished.pid)
    held()
    before = held()
    print("WAITING", flush=True)
    wait_for_process_exit(parent)
    result.write_text(str(held() - before), encoding="utf-8")
    """
)

_WAITERS_PARENT = textwrap.dedent(
    """
    import os, subprocess, sys
    waiter = subprocess.Popen(
        [sys.executable, "-c", {waiter!r}, str(os.getpid()), {result!r}],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert waiter.stdout.readline().strip() == "WAITING"
    """
)


def test_waiting_for_a_process_to_exit_keeps_no_handle_open(tmp_path: Path) -> None:
    """The wait opens a process handle (Windows) or a pidfd (Linux) on the parent it
    watches, and closes it once the parent has gone."""
    result = tmp_path / "held.txt"
    script = _WAITERS_PARENT.format(waiter=_WAITER, result=str(result))
    subprocess.run([sys.executable, "-c", script], check=True, timeout=60)

    deadline = time.monotonic() + 30
    while not result.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert result.read_text(encoding="utf-8") == "0"
