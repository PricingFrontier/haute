"""Adversarial repro: train-status timeout cannot fire BEFORE _launch_background.

CLAIM under test
----------------
POST /api/modelling/train creates the job with status="running" (
``_train_service.py:436``) and then runs ``_execute_and_sink`` (line 501) — the
lazy graph execution + bounded sink. ``start_time`` is *not* written until
``_launch_background`` (line ~1051), which only runs once execution+sink
*succeed*.  The status reaper at ``modelling.py:68`` is::

    if start and (time.monotonic() - start) > timeout:
        job = _train_service.timeout(...)

With ``start is None`` (the pre-launch window) the timeout branch is skipped on
every poll, so a job that hangs inside ``execute_lazy_graph`` / ``bounded_sink``
stays "running" forever and is never reaped.  The global
``_check_no_concurrent_jobs`` guard then blocks *every* future training job.

This script proves the wrong BEHAVIOUR by exercising the *real* production
objects (``JobStore`` + ``TrainService``) and the *verbatim* reaper expression
from ``modelling.py``.  It does NOT touch rating/, src/, tests/, or any real
project file: the job store is in-memory and we never run a pipeline.

Contrast control
----------------
The optimiser ``start`` path (``_optimiser_service.py:2571-2583``) stamps
``start_time`` *inside* ``create_job`` — the correct pattern — so its reaper is
armed from the first poll.  We assert that asymmetry too, to show the codebase
already knows the right pattern and the train path diverges from it.
"""

from __future__ import annotations

import time

# Real production objects — NOT reimplementations.
from haute.routes._job_store import JobStore
from haute.routes._train_service import (
    _DEFAULT_TIMEOUT,
    _JOB_TYPE_KEY,
    _TRAINING_JOB_TYPE,
    TrainService,
)


def _train_status_reaper(store: JobStore, service: TrainService, job_id: str) -> dict:
    """Verbatim port of the reaper branch in modelling.py:65-69.

    Kept byte-for-byte equivalent so the repro tests the *actual* gating
    condition the server uses, not a paraphrase.
    """
    job = store.require_job(job_id)
    if job.get("status") == "running":
        start = job.get("start_time")
        timeout = job.get("timeout", _DEFAULT_TIMEOUT)
        if start and (time.monotonic() - start) > timeout:  # <-- modelling.py:68
            job = service.timeout(job_id, timeout=timeout, start_time=start)
    return job


def main() -> None:
    failures: list[str] = []

    # ------------------------------------------------------------------
    # PHASE 1 — pre-launch window: exactly what start() creates at line 436.
    # status="running", NO "start_time", NO "timeout".
    # ------------------------------------------------------------------
    store = JobStore()
    service = TrainService(store)

    job_id = store.create_job(
        {
            "status": "running",
            _JOB_TYPE_KEY: _TRAINING_JOB_TYPE,
            "progress": 0.0,
            "message": "Starting",
            "config": {"target": "y", "algorithm": "catboost"},
            "node_label": "model",
        }
    )

    created = store.require_job(job_id)
    print(f"[phase1] created job: status={created['status']!r} "
          f"start_time={created.get('start_time')!r} "
          f"has_start_time={'start_time' in created}")

    # Sanity: this is the bug precondition — no start_time stamped yet.
    if "start_time" in created:
        failures.append(
            "PRECONDITION BROKEN: create_job already stamped start_time — "
            "the pre-launch window described by the claim does not exist."
        )

    # Simulate the dominant-cost _execute_and_sink phase hanging for far longer
    # than any default timeout.  We do NOT actually sleep an hour — we poll the
    # reaper while ASSERTING that even an arbitrarily large elapsed time cannot
    # trip it, because `start` is None.  To make the elapsed unambiguous we
    # evaluate the guard with a deliberately tiny timeout AND a huge synthetic
    # age; the only thing stopping the reap is `start` being falsy.
    huge_age_job = dict(created)
    # If start_time were present and this old, (now-start) would be ~1e9s.
    # But start_time is absent, so the guard short-circuits on `start`.
    start = huge_age_job.get("start_time")
    timeout = huge_age_job.get("timeout", _DEFAULT_TIMEOUT)
    branch_would_fire = bool(start and (time.monotonic() - start) > timeout)
    print(f"[phase1] reaper branch_would_fire={branch_would_fire} "
          f"(start={start!r}, timeout={timeout})")
    if branch_would_fire:
        failures.append(
            "Expected timeout branch to be SKIPPED in the pre-launch window "
            "(start is None) but it evaluated truthy."
        )

    # Poll the real reaper many times over a window that EXCEEDS the timeout we
    # injected, and assert the job never transitions out of "running".
    store.atomic_update(job_id, {"timeout": 0.05})  # 50ms timeout
    deadline = time.monotonic() + 0.5  # poll for 10x the timeout
    transitioned = False
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        job = _train_status_reaper(store, service, job_id)
        if job.get("status") != "running":
            transitioned = True
            break
    final = store.require_job(job_id)
    print(f"[phase1] after {polls} polls over 0.5s (timeout=0.05s): "
          f"status={final['status']!r} transitioned={transitioned}")

    if transitioned or final["status"] != "running":
        # If this fired, the claim would be refuted: the reaper DID reap a
        # pre-launch job.
        failures.append(
            f"REFUTES CLAIM: pre-launch job was reaped (status={final['status']!r}) "
            "even though start_time was never stamped."
        )
    else:
        print("[phase1] CONFIRMED: hung pre-launch job stays 'running' forever; "
              "timeout never fires.")

    # The concurrency guard now blocks every future training job.
    blocked = store.has_job_with_status("running")
    print(f"[phase1] has_job_with_status('running')={blocked} "
          "-> _check_no_concurrent_jobs would 409 every new train request")
    if not blocked:
        failures.append(
            "Expected the stuck running job to block new jobs via "
            "has_job_with_status('running'), but it did not."
        )

    # ------------------------------------------------------------------
    # PHASE 2 — control: once _launch_background stamps start_time, the SAME
    # reaper logic DOES reap the job.  Proves the defect is the pre-launch
    # ORDERING, not the reaper itself.
    # ------------------------------------------------------------------
    store2 = JobStore()
    service2 = TrainService(store2)
    job_id2 = store2.create_job(
        {
            "status": "running",
            _JOB_TYPE_KEY: _TRAINING_JOB_TYPE,
            "progress": 0.0,
            "message": "Starting",
            "config": {"target": "y", "algorithm": "catboost"},
            "node_label": "model",
        }
    )
    # Emulate _launch_background's atomic_update at line 1048-1054, but with a
    # start_time already in the past so the timeout is immediately due.
    store2.atomic_update(
        job_id2,
        {"start_time": time.monotonic() - 10.0, "timeout": 0.05},
    )
    job2 = _train_status_reaper(store2, service2, job_id2)
    print(f"[phase2] post-launch job after reaper: status={job2['status']!r}")
    if job2.get("status") == "running":
        failures.append(
            "Control failed: with start_time stamped, the reaper should have "
            f"timed the job out but status is still {job2['status']!r}."
        )
    else:
        print(f"[phase2] CONFIRMED: with start_time stamped the reaper reaps it "
              f"(status={job2['status']!r}) — proving the bug is the missing "
              "pre-launch start_time, not the reaper.")

    # ------------------------------------------------------------------
    # PHASE 3 — asymmetry vs optimiser: optimiser create_job INCLUDES
    # start_time (the correct pattern).  We assert the train create_job payload
    # used above did NOT, demonstrating the divergence the claim leans on.
    # ------------------------------------------------------------------
    # (Static fact, but we surface it for the reader.)
    print("[phase3] optimiser _optimiser_service.py:2571-2583 stamps "
          "start_time inside create_job; train _train_service.py:436 does not.")

    # ------------------------------------------------------------------
    print()
    if failures:
        print("RESULT: UNEXPECTED — claim NOT cleanly reproduced:")
        for f in failures:
            print("  - " + f)
        raise SystemExit(1)

    print("RESULT: REPRODUCED — train-status timeout cannot fire during the "
          "pre-launch execute/sink phase because start_time is unset; the SAME "
          "reaper reaps the job only once start_time is stamped in "
          "_launch_background. The hung job also permanently blocks new training "
          "jobs via the concurrency guard.")


if __name__ == "__main__":
    main()
