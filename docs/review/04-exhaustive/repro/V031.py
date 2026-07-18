"""Reproduction for V031.

Claim: ``safe_joblib_load`` reads ``original_find_class = NumpyUnpickler.find_class``
at line 452 OUTSIDE the ``with _joblib_lock`` block. Under a concurrent interleaving,
thread B can capture thread A's *restricted* closure as its "original", and then
restore that leaked closure in its own ``finally``. The net effect is that, after the
race, ``NumpyUnpickler.find_class`` is permanently left pointing at a dead
``safe_joblib_load.<locals>._restricted_joblib_find_class`` closure instead of the
genuine ``pickle._Unpickler.find_class`` — so EVERY subsequent process-wide
``joblib.load`` silently uses a leaked restricted hook.

DETERMINISM STRATEGY (no timing luck):

The bug needs this exact interleaving:
  1. A: read genuine original (452); acquire lock; patch find_class -> A_restricted (466)
  2. B: read original (452) == A_restricted   [B must read while A still holds the patch]
  3. B: block on the lock at 465
  4. A: finally restore genuine; release lock
  5. B: acquire lock; patch B_restricted; run; finally restore A_restricted  <-- LEAK

To force step 2 to observe A_restricted, we must hold A inside the lock until B has
*passed* its own line 452 and is *blocked on the lock*. We achieve this by:
  * Wrapping ``_joblib_lock`` with an instrumented lock that fires an event the moment
    a thread *blocks* on ``acquire`` (i.e. B parked at line 465). That guarantees B has
    already executed line 452.
  * Stubbing ``joblib.load`` so that A (the holder) waits for that "B is blocked" event
    before returning, i.e. A keeps the lock — and thus keeps A_restricted installed —
    until after B's line 452 has run.

ISOLATION: everything in memory; project root -> throwaway tempdir; ``joblib.load`` is
stubbed so no real pickle is parsed; no real project files are touched.
"""

import pickle
import sys
import tempfile
import threading
from pathlib import Path

import haute._sandbox as sandbox
from haute._sandbox import safe_joblib_load
from joblib.numpy_pickle import NumpyUnpickler


class InstrumentedLock:
    """Wraps a real Lock; sets ``blocked_evt`` when a thread parks on acquire().

    The first acquirer (A) gets the lock without blocking. The second acquirer (B)
    finds it held, so its blocking ``acquire`` fires ``blocked_evt`` — at which point
    we KNOW B has already executed everything up to and including line 452.
    """

    def __init__(self, blocked_evt: threading.Event) -> None:
        self._lock = threading.Lock()
        self._blocked_evt = blocked_evt

    def acquire(self, *a, **k):
        if not self._lock.acquire(blocking=False):
            # Lock is held by A -> B is about to block here. Signal, then block.
            self._blocked_evt.set()
            return self._lock.acquire(*a, **k)
        return True

    def release(self) -> None:
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def main() -> None:
    # --- Isolation: project root -> throwaway tempdir ---
    tmp = Path(tempfile.mkdtemp(prefix="v031_"))
    sandbox.set_project_root(tmp)
    path_a = tmp / "model_a.joblib"
    path_b = tmp / "model_b.joblib"
    path_a.write_bytes(b"stub")  # never parsed (joblib.load stubbed)
    path_b.write_bytes(b"stub")

    genuine_find_class = pickle._Unpickler.find_class
    assert NumpyUnpickler.find_class is genuine_find_class, (
        "precondition: NumpyUnpickler should inherit pickle._Unpickler.find_class"
    )

    b_blocked_on_lock = threading.Event()  # set the instant B parks on the lock

    # Install instrumented lock in place of the module-level _joblib_lock.
    real_lock = sandbox._joblib_lock
    sandbox._joblib_lock = InstrumentedLock(b_blocked_on_lock)

    real_joblib = sys.modules.get("joblib")

    class _StubJoblib:
        """Stub for the ``joblib`` module imported at line 468 inside the lock."""

        def __init__(self) -> None:
            self.calls: list[str] = []

        def load(self, _path):
            self.calls.append(str(_path))
            # A is the holder (it patched A_restricted at 466). Keep the lock until
            # B has parked on the lock, guaranteeing B's line 452 already ran and
            # observed A_restricted. B's own stubbed load won't reach here until A
            # releases, so this wait is A-specific.
            if len(self.calls) == 1:
                assert b_blocked_on_lock.wait(timeout=10.0), "B never blocked on lock"
            return "SENTINEL"

    stub = _StubJoblib()
    sys.modules["joblib"] = stub

    try:
        def thread_a() -> None:
            safe_joblib_load(path_a)

        def thread_b() -> None:
            safe_joblib_load(path_b)

        ta = threading.Thread(target=thread_a, name="A")
        tb = threading.Thread(target=thread_b, name="B")

        ta.start()
        # Wait until A has actually entered the lock and is parked in joblib.load,
        # i.e. A_restricted is installed. We detect this by polling the find_class
        # name flipping to the restricted closure.
        def a_has_patched() -> bool:
            f = NumpyUnpickler.find_class
            return callable(f) and getattr(f, "__name__", "") == "_restricted_joblib_find_class"

        deadline = threading.Event()
        # Busy-wait with a bounded loop for A to install its patch.
        import time
        t0 = time.time()
        while not a_has_patched():
            if time.time() - t0 > 10.0:
                raise AssertionError("A never installed its restricted patch")
            time.sleep(0.001)

        # Now A holds the lock with A_restricted installed. Start B; B will read
        # line 452 (== A_restricted) and then park on the lock, firing the event,
        # which releases A.
        tb.start()

        ta.join(timeout=15.0)
        tb.join(timeout=15.0)
        assert not ta.is_alive() and not tb.is_alive(), "threads did not finish"
    finally:
        sandbox._joblib_lock = real_lock
        if real_joblib is not None:
            sys.modules["joblib"] = real_joblib
        else:
            sys.modules.pop("joblib", None)

    final = NumpyUnpickler.find_class
    restored_to_genuine = final is genuine_find_class
    leaked_restricted = (
        callable(final)
        and getattr(final, "__name__", "") == "_restricted_joblib_find_class"
    )

    print(f"joblib.load call count                                  : {len(stub.calls)}")
    print(f"final is genuine pickle._Unpickler.find_class (RESTORED): {restored_to_genuine}")
    print(f"final is a LEAKED _restricted_joblib_find_class closure  : {leaked_restricted}")
    print(f"final repr: {final!r}")

    # Cleanup BEFORE asserting so a failed assert never poisons the interpreter.
    leaked_obj = final
    NumpyUnpickler.find_class = genuine_find_class

    assert len(stub.calls) == 2, f"expected both threads to load, got {stub.calls}"
    assert not restored_to_genuine, (
        "REFUTED: NumpyUnpickler.find_class WAS restored to the genuine "
        f"pickle._Unpickler.find_class — no permanent leak. final={leaked_obj!r}"
    )
    assert leaked_restricted, (
        "REFUTED: final find_class is neither genuine nor a leaked restricted "
        f"closure: {leaked_obj!r}"
    )
    print(
        "CONFIRMED: after the concurrent interleaving, NumpyUnpickler.find_class is "
        "permanently left pointing at a leaked restricted closure instead of the "
        "genuine pickle._Unpickler.find_class."
    )


if __name__ == "__main__":
    main()
