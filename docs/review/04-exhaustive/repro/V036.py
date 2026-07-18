"""Isolated reproduction for V036.

Claim: ``run_isolated_worker`` deadlocks for large results because the parent
fully waits for the child to exit (``_wait_for_worker`` loops on
``process.join`` while ``process.is_alive()``) BEFORE it ever drains the
result queue (``_read_worker_payload`` -> ``result_queue.get``).

Mechanics (the classic ``multiprocessing.Queue`` feeder-pipe deadlock):
  * The child calls ``result_queue.put(("ok", big_obj))``. ``mp.Queue.put``
    pickles ``big_obj`` and hands the bytes to a background *feeder* thread,
    which writes them into an OS pipe with a finite buffer.
  * If the pickled payload exceeds the pipe buffer, the feeder thread BLOCKS
    on the pipe write until the *parent* reads from the other end.
  * When the child entrypoint returns, the child begins shutdown and the
    ``multiprocessing`` finalizer JOINS the feeder thread. Because the feeder
    is blocked, the child cannot finish exiting -> ``process.is_alive()``
    stays True.
  * The parent, meanwhile, is stuck in ``_wait_for_worker`` calling
    ``process.join(...)`` in a loop. With the default ``timeout_seconds=None``
    and no ``stop_reason``, there is no escape: it loops FOREVER. The parent
    never reaches line 213 where it would drain the queue.
  * The stdlib documents exactly this: "a process that has put items in a
    queue will wait before terminating until all the buffered items are fed
    ... if you join such a process you may get a deadlock."

This repro is ISOLATED: no disk I/O, no project files, no rating/ or src/
access. It builds a synthetic large result entirely in memory (a top-level
picklable function so ``spawn`` can import it), calls the PUBLIC API
``run_isolated_worker`` on a WATCHDOG thread, and ASSERTS on the specific
wrong BEHAVIOUR: the call fails to return a value within a generous deadline
(it is wedged), rather than returning the correct large payload quickly.

Outcome interpretation:
  * If ``run_isolated_worker`` HANGS past the watchdog deadline while the
    worker function itself does only a trivial allocation and returns
    immediately, the deadlock is REPRODUCED (the wrong behaviour is "never
    returns / blocks", a demonstrably wrong outcome vs. "returns the payload").
  * If it returns the correct large payload quickly, the bug does NOT
    reproduce on this platform (refuted here).

NOTE ON PLATFORM: the finding describes the POSIX pipe buffer (~64 KiB on
Linux), which is the production target (the module gates memory caps on
``resource.RLIMIT_AS`` / SIGKILL). On Windows the ``mp.Queue`` is backed by a
Connection over a named pipe whose buffer is larger, so we deliberately make
the payload very large to give the deadlock the best chance to manifest on any
platform. The size is also chosen to far exceed a 64 KiB POSIX pipe buffer.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

from haute._worker_isolation import IsolatedWorkerConfig, run_isolated_worker


# Must be a TOP-LEVEL function so the ``spawn`` child can import it by
# qualified name. It does a single trivial allocation and returns at once:
# the worker's own runtime is ~0, so any long wall-clock time spent inside
# ``run_isolated_worker`` is attributable to the parent/child queue-drain
# ordering, NOT to the work itself.
def _make_big_result(num_bytes: int) -> bytes:
    # A bytes object of the requested size. Pickling a bytes object is cheap
    # and its serialized form is ~num_bytes, guaranteeing the feeder must push
    # far more than any pipe buffer (64 KiB POSIX) before the child can exit.
    return b"x" * num_bytes


def main() -> None:
    # ~16 MiB: dwarfs the ~64 KiB POSIX pipe buffer by ~256x, and is large
    # enough to overflow typical Windows pipe buffers as well.
    payload_size = 16 * 1024 * 1024

    # Bound the parent's own wait so a genuine deadlock cannot wedge THIS
    # process. The worker returns instantly, so a correct implementation
    # completes in well under a second even with a 16 MiB payload.
    config = IsolatedWorkerConfig(timeout_seconds=None)

    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_holder["value"] = run_isolated_worker(
                _make_big_result,
                payload_size,
                config=config,
            )
        except BaseException as exc:  # noqa: BLE001 - record loud failures too
            error_holder["error"] = exc

    worker_thread = threading.Thread(target=_runner, name="v036-runner", daemon=True)
    start = time.monotonic()
    worker_thread.start()

    # Generous deadline. A correct primitive returns the 16 MiB payload in
    # well under this; only a deadlock blows past it.
    watchdog_seconds = 30.0
    worker_thread.join(timeout=watchdog_seconds)
    elapsed = time.monotonic() - start

    completed = not worker_thread.is_alive()
    returned_value = result_holder.get("value")
    raised = error_holder.get("error")
    returned_len = len(returned_value) if isinstance(returned_value, (bytes, bytearray)) else None

    print(f"platform: {sys.platform}")
    print(f"payload_size_requested: {payload_size}")
    print(f"completed_within_watchdog: {completed}")
    print(f"elapsed_seconds: {elapsed:.2f}")
    print(f"returned_len: {returned_len}")
    print(f"raised: {raised!r}")

    # --- The crux of the bug -------------------------------------------------
    # The worker function does a trivial allocation and returns immediately, so
    # a NON-buggy ``run_isolated_worker`` MUST return the exact 16 MiB payload
    # almost instantly. The deadlock manifests as the runner thread STILL being
    # alive after a 30s deadline: the parent is wedged in ``_wait_for_worker``
    # joining a child that cannot exit because its feeder thread is blocked on
    # the un-drained pipe.
    assert not completed, (
        "Expected run_isolated_worker to DEADLOCK for a large (16 MiB) result "
        "and never return within the watchdog window (the parent joins the "
        "child in _wait_for_worker before draining result_queue). Instead it "
        f"completed in {elapsed:.2f}s with returned_len={returned_len!r}, "
        f"raised={raised!r}. If returned_len == {payload_size}, the large "
        "result round-tripped correctly and the deadlock did NOT reproduce on "
        "this platform."
    )

    # If we get here, the call did not complete: that IS the deadlock. The
    # runner thread is a daemon, so leaving it wedged does not block process
    # exit. Make the wrong VALUE explicit: expected a 16 MiB bytes result;
    # actual = no result at all (still blocked).
    assert returned_value is None and raised is None, (
        "Deadlock expected: neither a result nor an exception should have been "
        f"produced. Got returned_value len={returned_len!r}, raised={raised!r}."
    )

    print(
        "V036 REPRODUCED: run_isolated_worker(_make_big_result, 16 MiB) did NOT "
        f"return within {watchdog_seconds:.0f}s (deadlocked). Expected a "
        f"{payload_size}-byte payload; actual = no result (parent wedged in "
        "_wait_for_worker / process.join before draining result_queue)."
    )


if __name__ == "__main__":
    main()
