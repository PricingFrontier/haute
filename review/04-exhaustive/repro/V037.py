"""Isolated reproduction for V037.

Claim: ``IsolatedWorkerConfig.stop_reason`` is typed
``Callable[[], WorkerTerminalReason | None]`` and ``WorkerTerminalReason``
*includes* the member ``"completed"``. If a (type-valid) ``stop_reason``
returns ``"completed"``, ``_wait_for_worker`` first kills the worker via
``_terminate_process`` and then constructs
``IsolatedWorkerStoppedError(terminal_reason="completed")``, whose
``__init__`` raises ``ValueError("completed is not a valid stopped-worker
reason")`` *before* calling ``super().__init__`` -- so the raised object is a
plain ``ValueError``, NOT an ``IsolatedWorkerError``.

Consequences proven here:
  1. The exception escaping ``run_isolated_worker`` is a ``ValueError`` and is
     NOT an instance of ``IsolatedWorkerError``. Every real caller guards with
     ``except IsolatedWorkerError`` (see routes/_background_jobs.py:198 and the
     supervisor's ``run()``), so this exception slips past that handler.
  2. Because the ``ValueError`` is not caught by the ``except IsolatedWorkerError``
     arm at run_isolated_worker line 236, it propagates straight through the
     ``finally`` and SKIPS the cleanup section (lines 245-251):
     ``_run_cleanup_callbacks`` is never invoked -- contradicting the module
     contract (lines 178-180) that "the parent ... always runs them after the
     child reaches a terminal state".

Control case: ``stop_reason`` returning the documented ``"cancelled"`` reason
raises a proper ``IsolatedWorkerStoppedError`` AND runs the cleanup callback,
demonstrating that only the ``"completed"`` member triggers the defect.

ISOLATION: no disk I/O of project files; the only state is in-memory module
flags. The child process runs a trivial top-level sleeper (picklable under the
spawn start method). ``stop_reason``/cleanup run only in the parent, so they
need not be picklable.
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

from haute._worker_isolation import (  # noqa: E402
    IsolatedWorkerConfig,
    IsolatedWorkerError,
    IsolatedWorkerStoppedError,
    WorkerTerminalReason,
    run_isolated_worker,
)


def _sleep_for(seconds: float) -> str:
    """Top-level, picklable child worker: stay alive long enough to be stopped."""
    time.sleep(seconds)
    return "child-finished"


# Parent-side mutable flags (only touched in the parent process).
_CLEANUP_RAN: dict[str, bool] = {"completed": False, "cancelled": False}


def _make_stop_reason(reason: WorkerTerminalReason):
    # Return the requested terminal reason on the FIRST poll so _wait_for_worker
    # takes the stop branch while the child is still alive.
    def _stop() -> WorkerTerminalReason:
        return reason

    return _stop


def _make_cleanup(key: str):
    def _cleanup() -> None:
        _CLEANUP_RAN[key] = True

    return _cleanup


def _run_case(reason: WorkerTerminalReason, key: str) -> dict:
    config = IsolatedWorkerConfig(
        cleanup_callbacks=(_make_cleanup(key),),
        stop_reason=_make_stop_reason(reason),
        stop_poll_interval_seconds=0.05,
    )
    raised: BaseException | None = None
    try:
        # Child sleeps 5s; the parent's stop_reason fires on first poll and
        # terminates it, so this returns quickly via an exception either way.
        run_isolated_worker(_sleep_for, 5.0, config=config)
    except BaseException as exc:  # noqa: BLE001 - characterising the failure mode
        raised = exc
    return {
        "reason": reason,
        "key": key,
        "raised": raised,
        "cleanup_ran": _CLEANUP_RAN[key],
    }


def main() -> int:
    failures: list[str] = []

    # --- Control: documented "cancelled" reason behaves correctly ---
    control = _run_case("cancelled", "cancelled")
    c_err = control["raised"]
    print("--- control: stop_reason() -> 'cancelled' ---")
    print(f"  raised       -> {type(c_err).__name__ if c_err else None}: {c_err}")
    print(f"  cleanup_ran  -> {control['cleanup_ran']}")
    if not isinstance(c_err, IsolatedWorkerStoppedError):
        failures.append(
            "control: expected IsolatedWorkerStoppedError for 'cancelled', got "
            f"{type(c_err).__name__ if c_err else 'no error'}: {c_err}"
        )
    elif getattr(c_err, "terminal_reason", None) != "cancelled":
        failures.append(
            f"control: expected terminal_reason 'cancelled', got {getattr(c_err, 'terminal_reason', None)!r}"
        )
    elif not control["cleanup_ran"]:
        failures.append("control: cleanup callback did NOT run for the 'cancelled' path")

    # --- Bug: type-valid "completed" reason ---
    bug = _run_case("completed", "completed")
    b_err = bug["raised"]
    print("--- bug: stop_reason() -> 'completed' (a valid WorkerTerminalReason) ---")
    print(f"  raised               -> {type(b_err).__name__ if b_err else None}: {b_err}")
    print(f"  isinstance IsolatedWorkerError -> {isinstance(b_err, IsolatedWorkerError)}")
    print(f"  cleanup_ran          -> {bug['cleanup_ran']}")

    # Prediction 1: a bare ValueError escapes, and it is NOT an IsolatedWorkerError,
    # so `except IsolatedWorkerError` in every real caller will miss it.
    if not isinstance(b_err, ValueError):
        failures.append(
            f"bug: expected a ValueError to escape, got {type(b_err).__name__ if b_err else 'no error'}: {b_err}"
        )
    elif isinstance(b_err, IsolatedWorkerError):
        failures.append(
            "bug: escaping exception IS an IsolatedWorkerError -- caller guard would catch it; defect not present."
        )
    elif "completed is not a valid stopped-worker reason" not in str(b_err):
        failures.append(f"bug: ValueError message did not match the constructor guard: {b_err!r}")
    # Prediction 2: cleanup callback was BYPASSED (contract violation).
    elif bug["cleanup_ran"]:
        failures.append(
            "bug: cleanup callback DID run -- the cleanup-bypass claim is not present."
        )
    else:
        print(
            "  REPRODUCED: a non-IsolatedWorkerError ValueError escaped AND the parent-owned "
            "cleanup callback was skipped."
        )

    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: BUG REPRODUCED -- stop_reason()=='completed' (a type-valid")
    print("WorkerTerminalReason) makes IsolatedWorkerStoppedError.__init__ raise a plain")
    print("ValueError that is NOT an IsolatedWorkerError; it escapes the 'except")
    print("IsolatedWorkerError' arm and SKIPS the parent-owned cleanup callbacks,")
    print("violating the module's 'always runs cleanup after a terminal state' contract.")
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
