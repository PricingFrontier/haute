"""Adversarial repro: cancelled GPU CatBoost fit leaks a live worker thread,
the held Pool/model, and the train_dir.

CLAIM under test (id: gpu-fit-zombie-thread-and-train-dir-leak-on-cancel):
  On cancellation, ``_run_gpu_fit_with_metric_polling`` joins the fit worker
  for at most ``abort_join_timeout_seconds``.  CatBoost cannot be interrupted
  mid-fit, so if the worker is still running after the timeout the helper:
    (a) re-raises the cancellation exception,
    (b) does NOT remove ``train_dir``, and
    (c) leaves the worker thread alive (``worker.is_alive()`` True).

This script forces the zombie path deterministically (Event-gated fit that
outlives a tiny abort timeout — no sleeps-as-sync) and ASSERTS on all three
observable facts.  It then RELEASES the worker for hygiene.

ISOLATION: all disk I/O via tempfile; no real project files touched.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path


def main() -> int:
    from haute.modelling._algorithms import _run_gpu_fit_with_metric_polling

    class _CancelledForTestError(RuntimeError):
        """Stand-in for routes._background_jobs.BackgroundJobStoppedError."""

    # Tiny abort timeout: the worker must outlive it -> zombie path.
    ABORT_JOIN_TIMEOUT_S = 0.05
    # Generous outer bound used only for hygiene at the end (never as sync).
    HYGIENE_WAIT_S = 10.0

    with tempfile.TemporaryDirectory(prefix="gpu_fit_zombie_repro_") as td:
        train_dir = Path(td) / "catboost_gpu_train"
        train_dir.mkdir()
        # CatBoost-shaped learn_error.tsv so the first poll yields a data
        # line and drives on_iteration (which raises the cancellation).
        (train_dir / "learn_error.tsv").write_text("iter\tRMSE\n0\t0.5\n")

        fit_release = threading.Event()
        fit_finished = threading.Event()
        captured: dict[str, threading.Thread] = {}

        def fit() -> None:
            # Holds a reference to "the CatBoost model + Pool" in the real
            # code; here it just blocks past the abort timeout, modelling
            # CatBoost's uninterruptible mid-fit.
            captured["worker"] = threading.current_thread()
            # Will NOT be released until AFTER we assert -> guarantees the
            # worker is still alive when the helper gives up on it.
            assert fit_release.wait(timeout=HYGIENE_WAIT_S), "fit gate never released"
            fit_finished.set()

        def on_iteration(iteration: int, total: int, metrics: dict) -> None:
            # Mirrors the live server: cancellation raised into the poll loop.
            raise _CancelledForTestError("user pressed stop")

        raised: BaseException | None = None
        try:
            _run_gpu_fit_with_metric_polling(
                fit,
                train_dir=str(train_dir),
                on_iteration=on_iteration,
                total_iterations=5,
                poll_interval_seconds=0.01,
                abort_join_timeout_seconds=ABORT_JOIN_TIMEOUT_S,
            )
        except BaseException as exc:  # noqa: BLE001 - capturing for assertions
            raised = exc

        # --- ASSERTION (a): the cancellation re-raises ----------------------
        expected_reraise = isinstance(raised, _CancelledForTestError)
        # --- ASSERTION (c): worker still alive after the call ---------------
        worker = captured.get("worker")
        worker_present = worker is not None
        worker_alive = bool(worker is not None and worker.is_alive())
        # --- ASSERTION (b): train_dir left in place -------------------------
        train_dir_present = train_dir.exists()
        # The fit had NOT finished by the time the helper gave up (the
        # worker is genuinely a zombie, not already-completed).
        fit_not_finished = not fit_finished.is_set()
        # Loud-not-silent: the cancellation carries a note naming train_dir.
        notes = list(getattr(raised, "__notes__", []))
        note_names_dir = any(str(train_dir) in n for n in notes)

        print("=" * 70)
        print("REPRO: gpu-fit-zombie-thread-and-train-dir-leak-on-cancel")
        print("=" * 70)
        print(f"(a) re-raised cancellation type      : "
              f"{type(raised).__name__} -> expected_reraise={expected_reraise}")
        print(f"(c) worker thread captured           : {worker_present}")
        print(f"(c) worker.is_alive() after helper   : {worker_alive}  "
              f"(EXPECTED True per claim)")
        print(f"(b) train_dir present after helper   : {train_dir_present}  "
              f"(EXPECTED True per claim)")
        print(f"    fit had NOT finished (true zombie): {fit_not_finished}")
        print(f"    exception note names train_dir    : {note_names_dir}")
        print(f"    note(s)                           : {notes!r}")

        # Release the zombie for hygiene and confirm it can exit, then the
        # claim is fully demonstrated: only the abort timeout abandoned it.
        fit_release.set()
        if worker is not None:
            worker.join(timeout=HYGIENE_WAIT_S)
            worker_exits_when_released = not worker.is_alive()
        else:
            worker_exits_when_released = False
        print(f"    worker exits once released        : {worker_exits_when_released}")
        print("-" * 70)

        claim_holds = (
            expected_reraise
            and worker_present
            and worker_alive
            and train_dir_present
            and fit_not_finished
        )

        if claim_holds:
            print("RESULT: CLAIM REPRODUCED — on cancel the helper re-raised, left")
            print("        train_dir in place, AND the worker thread was still alive")
            print("        (zombie). The held model/Pool/GPU memory stay live with it.")
        else:
            print("RESULT: CLAIM NOT REPRODUCED — one or more observable facts differ:")
            print(f"        reraise={expected_reraise} worker_alive={worker_alive} "
                  f"train_dir_present={train_dir_present} "
                  f"fit_not_finished={fit_not_finished}")

        # Hard assertions so a non-zero exit means "not reproduced".
        assert expected_reraise, (
            f"expected helper to re-raise _CancelledForTestError, got {raised!r}"
        )
        assert worker_present, "fit worker thread was never captured (wiring broken)"
        assert worker_alive, (
            "claim predicts a ZOMBIE: worker.is_alive() must be True immediately "
            "after the helper returns, but it was not"
        )
        assert train_dir_present, (
            "claim predicts a LEAK: train_dir must still exist after cancel, "
            "but it was removed"
        )
        assert fit_not_finished, (
            "worker had already finished before the helper gave up — not a "
            "genuine zombie; the abort-timeout path was not exercised"
        )
        assert worker_exits_when_released, (
            "released worker failed to exit — test hygiene broken"
        )

    print("\nALL ASSERTIONS PASSED — claim demonstrably holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
