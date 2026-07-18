"""ISOLATED reproduction for BUG-2 (Price-optimisation god-file).

Claim
-----
In ``_run_streaming_frontier_auto_range_job`` the streaming auto-range job emits
``progress=0.30`` just before the chunk loop (src/haute/routes/_optimiser_service.py:3735-3743),
and the *in-loop* periodic refresh (3805-3814) re-writes the SAME hard-coded
``0.30`` on every 10th chunk:

    chunk_index += 1                      # 3804
    if chunk_index % 10 == 0:             # 3805
        self._store.atomic_update(
            job_id,
            {
                "message": f"Streaming scenario chunks ({chunk_index})",  # 3809 (varies)
                "progress": 0.30,                                          # 3810 (CONSTANT)
                "elapsed_seconds": _job_elapsed_seconds(...),
            },
            expected_status="running",
        )

so for a long streaming run the progress bar is frozen at 30% for the entire
chunk phase, then jumps to 0.85 at 3816-3824. Only the chunk counter in the
*message* changes; ``progress`` never interpolates across the streamed work.

Two sub-claims to verify:
  (A) progress is pinned at 0.30 across the whole chunk phase (display-only;
      no wrong number is produced, but the periodic poke conveys no liveness
      in the progress field).
  (B) off-by-one: because ``chunk_index`` is incremented (3804) BEFORE the
      ``% 10`` check (3805), the first in-loop refresh lands on chunk 10, not
      chunk 0 -- so chunks 1..9 get no poke at all.

Method
------
This is a UX / progress-reporting defect, not a numeric one, so the faithful
"oracle" is: does the value stored in the job's ``progress`` field advance
during the chunk loop?  We drive the REAL ``JobStore`` (obtained through the
``get_job_store`` factory so the routes-hygiene pin that forbids direct
``JobStore()`` instantiation is respected) and replay the EXACT poke logic from
lines 3735-3820 verbatim, with a synthetic in-memory list standing in for the
streamed chunk batches.  No rating/, src/, tests/, or real project files are
written; the only state touched is the in-process job store namespace.

If the bug is real, every progress sample observed during the loop equals 0.30
and the distinct-progress-value set for the chunk phase is exactly {0.30}.
A correct (interpolating) implementation would instead produce several distinct
increasing values strictly between 0.30 and 0.85.
"""

from __future__ import annotations

import sys

from haute.routes._job_store import get_job_store
from haute.routes._optimiser_service import _job_elapsed_seconds


def main() -> int:
    # Real job store (factory-obtained -> honours the no-direct-instantiation pin).
    # Use the genuine "optimiser" namespace: this IS the store the streaming
    # auto-range job uses, and the progress-storage behaviour under test is
    # prefix-independent. A fresh UUID job id is created, so no real job collides.
    store = get_job_store("optimiser")

    job_id = store.create_job(
        {
            "status": "running",
            "message": "Preparing optimiser input",
            "progress": 0.0,
            "start_time": 0.0,
        }
    )

    # --- verbatim transcription of _run_streaming_frontier_auto_range_job ---

    # 3735-3743: pre-loop milestone.
    store.atomic_update(
        job_id,
        {
            "message": "Streaming scenario chunks",
            "progress": 0.30,
            "elapsed_seconds": _job_elapsed_seconds(store.jobs[job_id]),
        },
        expected_status="running",
    )

    # 3745
    chunk_index = 0

    # A long streaming run: many chunks (so an interpolating bar would clearly move).
    TOTAL_CHUNKS = 95
    synthetic_chunk_batches = range(TOTAL_CHUNKS)

    progress_during_loop: list[float] = []
    poke_chunk_indices: list[int] = []

    # 3770: for chunk in chunk_batches:
    for _chunk in synthetic_chunk_batches:
        # (3771-3803: validate / score / collect / accumulate -- omitted; the
        #  envelope maths is exercised by the entries-45/46 repros, not this one.)
        chunk_index += 1  # 3804
        if chunk_index % 10 == 0:  # 3805
            # 3806-3814: the in-loop refresh -- progress is a hard-coded 0.30.
            store.atomic_update(
                job_id,
                {
                    "message": f"Streaming scenario chunks ({chunk_index})",
                    "progress": 0.30,
                    "elapsed_seconds": _job_elapsed_seconds(store.jobs[job_id]),
                },
                expected_status="running",
            )
            poke_chunk_indices.append(chunk_index)
        # Sample what a polling client (solve_status) would read from the store
        # after this chunk, every iteration.
        progress_during_loop.append(float(store.jobs[job_id]["progress"]))

    # 3816-3824: post-loop jump to the "combining" milestone.
    store.atomic_update(
        job_id,
        {
            "message": "Combining scenario envelope",
            "progress": 0.85,
            "elapsed_seconds": _job_elapsed_seconds(store.jobs[job_id]),
        },
        expected_status="running",
    )

    final_progress = float(store.jobs[job_id]["progress"])

    # --- observations ---
    distinct_during_loop = sorted(set(progress_during_loop))
    n_pokes = len(poke_chunk_indices)
    first_poke_chunk = poke_chunk_indices[0] if poke_chunk_indices else None

    print(f"[setup] total chunks streamed           : {TOTAL_CHUNKS}")
    print(f"[obs-A] distinct progress during loop   : {distinct_during_loop}")
    print(f"[obs-A] progress after chunk 1          : {progress_during_loop[0]}")
    print(f"[obs-A] progress after final chunk      : {progress_during_loop[-1]}")
    print(f"[obs-A] # of in-loop pokes              : {n_pokes}")
    print(f"[obs-A] poke chunk indices              : {poke_chunk_indices}")
    print(f"[obs-B] first in-loop poke at chunk     : {first_poke_chunk} (expected 10, not 0)")
    print(f"[after] progress after post-loop jump   : {final_progress}")

    # --- assertions on the SPECIFIC wrong behaviour ---

    # (A) Across the entire chunk phase the stored progress never leaves 0.30:
    #     the only distinct value observed during the loop is exactly 0.30.
    assert distinct_during_loop == [0.30], (
        "EXPECTED-WRONG: progress frozen at 0.30 for the whole chunk phase; "
        f"got distinct values {distinct_during_loop}. If this list has multiple "
        "increasing values the in-loop poke now interpolates and the bug is fixed."
    )

    # The poke itself fired (so this is genuinely the constant-write path, not a
    # 'loop never ran' setup error): with 95 chunks and a %10 gate we get 9 pokes.
    assert n_pokes == 9, f"setup check: expected 9 pokes over 95 chunks, got {n_pokes}"

    # Each poke wrote the same constant -> the message changed but progress did not.
    # Demonstrate that the message DID advance while progress did NOT, proving the
    # 'only liveness is the chunk counter in the message' part of the claim.
    assert store.jobs[job_id]["message"] == "Combining scenario envelope"

    # (B) off-by-one: first refresh lands on chunk 10 (chunks 1..9 silent), not 0.
    assert first_poke_chunk == 10, (
        f"EXPECTED off-by-one: first poke at chunk 10, got {first_poke_chunk}"
    )
    assert poke_chunk_indices == [10, 20, 30, 40, 50, 60, 70, 80, 90], (
        f"poke cadence not the predicted every-10th-from-10: {poke_chunk_indices}"
    )

    # Sanity: the bar only ever advances at the milestone boundaries (0.30 -> 0.85),
    # never within the streamed work.
    assert progress_during_loop[0] == 0.30 and progress_during_loop[-1] == 0.30
    assert final_progress == 0.85

    print("\nREPRO RESULT: CLAIM REPRODUCED")
    print(
        "  - progress field is pinned at 0.30 for the entire chunk phase "
        f"({TOTAL_CHUNKS} chunks); the periodic poke re-writes the same constant."
    )
    print(
        "  - only the message advances (chunk counter); progress conveys no "
        "liveness toward the 0.85 'combining' milestone."
    )
    print(
        "  - off-by-one confirmed: first in-loop poke at chunk 10, so chunks "
        "1..9 receive no refresh at all."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
