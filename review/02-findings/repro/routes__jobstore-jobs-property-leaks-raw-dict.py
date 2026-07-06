"""Adversarial repro for claim `jobstore-jobs-property-leaks-raw-dict`.

The claim has a *design* part (provably true) and a *runtime-race* part
(its stated failure_scenario / repro_strategy). This script separates them
and asserts on the specific predicted behaviours.

DESIGN FACT (expected TRUE):
  - JobStore.jobs returns the SAME object as the private _jobs (reference
    leak, no copy, no lock).

RUNTIME FAILURE_SCENARIO (claim predicts a problem; we test whether it is real
on the interpreter this project actually targets, CPython 3.11 with GIL):
  - F1: "a writer's whole-dict swap" can be observed -> tested by checking
        whether self._jobs is ever rebound. (It is not in the code; here we
        confirm the container identity is stable across many writes.)
  - F2: a reader holding `old = store.jobs[job_id]` can observe a "torn view"
        of that per-job dict while a concurrent writer updates the job.
        We assert the reader's snapshot is internally consistent (never torn),
        because writers swap a fresh dict at the key rather than mutating
        in place.
  - F3: the *actual* access pattern used by real callers
        (single-key subscript `store.jobs[job_id]`) never raises
        "dictionary changed size during iteration" under heavy concurrent
        eviction/creation. (No real caller iterates `.jobs`.)

A "reproduced bug" would require F2 (torn per-job view) or F3 (size-change
RuntimeError from a real-style access) to actually occur. We assert they do
NOT, which REFUTES the runtime failure_scenario as written.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

import haute._sandbox as _sandbox
from haute.routes._job_store import JobStore


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="haute_repro_jobstore_"))
    _sandbox.set_project_root(tmp)

    print(f"interpreter: {sys.version.split()[0]}  GIL="
          f"{getattr(sys, '_is_gil_enabled', lambda: True)()}")

    # ----------------------------------------------------------------
    # DESIGN FACT: .jobs is the underlying dict by reference (no copy).
    # ----------------------------------------------------------------
    store = JobStore(ttl_seconds=10_000, heavy_object_ttl_seconds=10_000)
    leaked = store.jobs
    assert leaked is store._jobs, "EXPECTED reference leak: .jobs should be _jobs"
    # mutating the leaked dict directly is visible to the store -> encapsulation
    # is not enforced.
    leaked["__poked__"] = {"status": "running", "created_at": time.time()}
    assert store.get_job("__poked__") is not None, (
        "EXPECTED: external mutation of leaked dict is visible via the store"
    )
    print("DESIGN FACT confirmed: .jobs leaks _jobs by reference (no copy/lock).")
    leaked.pop("__poked__", None)

    # ----------------------------------------------------------------
    # F1: container identity of _jobs is stable (no whole-dict swap).
    # ----------------------------------------------------------------
    container_id_before = id(store._jobs)
    # create_job() generates its own UUID; capture it for deterministic updates.
    j1 = store.create_job({"status": "running"})
    for i in range(200):
        store.atomic_update(j1, {"progress": i / 200.0}, expected_status="running")
    assert id(store._jobs) == container_id_before, (
        "claim says writers do a 'whole-dict swap'; identity changed!"
    )
    print("F1 confirmed FALSE-in-code: _jobs container is never swapped; "
          "writers only replace per-key values.")

    # ----------------------------------------------------------------
    # F2: torn per-job view under concurrent writes?
    # A writer flips two correlated fields together; a reader that grabbed the
    # dict reference must see a self-consistent pair (both old or both new),
    # never a mix. Writers build a fresh dict, so the grabbed reference is a
    # frozen snapshot.
    # ----------------------------------------------------------------
    store2 = JobStore(ttl_seconds=10_000, heavy_object_ttl_seconds=10_000)
    jc = store2.create_job({"status": "running", "gen": 0, "gen_mirror": 0})

    torn_observations = 0
    stop = threading.Event()

    def writer() -> None:
        gen = 0
        while not stop.is_set():
            gen += 1
            # atomic_update merges under the lock into a brand-new dict.
            store2.atomic_update(
                jc,
                {"gen": gen, "gen_mirror": gen},
                expected_status="running",
            )

    def reader() -> None:
        nonlocal torn_observations
        for _ in range(200_000):
            snap = store2.jobs[jc]  # single subscript, no lock (real pattern)
            # read both correlated fields off the SAME grabbed reference
            g = snap.get("gen")
            m = snap.get("gen_mirror")
            if g != m:
                torn_observations += 1

    wt = threading.Thread(target=writer, daemon=True)
    rt = threading.Thread(target=reader, daemon=True)
    wt.start()
    rt.start()
    rt.join()
    stop.set()
    wt.join(timeout=2)

    assert torn_observations == 0, (
        f"TORN per-job view observed {torn_observations} times -> failure_scenario REAL"
    )
    print("F2 confirmed FALSE: 0 torn per-job views across 200k concurrent reads "
          "(writers swap a fresh dict; the grabbed reference is a frozen snapshot).")

    # ----------------------------------------------------------------
    # F3: does the REAL access pattern (single-key subscript) ever raise
    # 'dictionary changed size during iteration'? Stress create/evict vs
    # repeated subscript. (No real caller iterates `.jobs`.)
    # ----------------------------------------------------------------
    store3 = JobStore(ttl_seconds=10_000, heavy_object_ttl_seconds=10_000)
    runtime_errors: list[str] = []
    key_errors = 0
    stop3 = threading.Event()
    raw3 = store3.jobs  # leaked container; churner adds/pops to change its SIZE

    def churn() -> None:
        # Continuously add and remove keys -> the dict changes size constantly,
        # which is exactly what would trip a concurrent *iteration*.
        n = 0
        while not stop3.is_set():
            n += 1
            jid = f"k{n % 64}"
            raw3[jid] = {"status": "running", "created_at": time.time()}
            raw3.pop(f"k{(n + 7) % 64}", None)

    def raw_reader() -> None:
        nonlocal key_errors
        for i in range(400_000):
            try:
                # EXACT real pattern from _optimiser_service.py:3478 etc:
                # a single-key subscript on the leaked dict, no lock held.
                _ = store3.jobs[f"k{i % 64}"]
            except KeyError:
                key_errors += 1
            except RuntimeError as exc:  # the claim's predicted failure mode
                runtime_errors.append(str(exc))

    ct = threading.Thread(target=churn, daemon=True)
    rrt = threading.Thread(target=raw_reader, daemon=True)
    ct.start()
    rrt.start()
    rrt.join()
    stop3.set()
    ct.join(timeout=2)

    assert not runtime_errors, (
        f"'dict changed size during iteration' RuntimeError from real-style "
        f"subscript -> {runtime_errors[:3]}"
    )
    print(
        "F3 confirmed FALSE: single-key subscript never raised "
        "'changed size during iteration' "
        f"(KeyError seen {key_errors} times -- that is the SEPARATE sibling "
        "finding optimiser-worker-raw-jobs-keyerror, not a torn read)."
    )

    print()
    print("VERDICT INPUT: design fact TRUE; runtime failure_scenario "
          "(whole-dict swap / torn view / size-change RuntimeError) NOT "
          "reproduced on CPython 3.11+GIL via real access patterns.")


if __name__ == "__main__":
    main()
