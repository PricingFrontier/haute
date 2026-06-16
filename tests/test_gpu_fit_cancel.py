"""GPU fit-thread lifecycle on cancellation — remediation 4b.7.

CODE_REVIEW MEDIUM "Modelling": GPU cancel leaves a zombie fit thread and
leaks ``train_dir`` (``_algorithms.py:466``).  The GPU progress path runs
``model.fit`` on a worker thread and polls CatBoost's metric files,
invoking ``on_iteration`` from the polling loop.  In the live server that
callback raises ``BackgroundJobStoppedError`` when the user cancels — and
before the fix the exception unwound straight through ``fit()``, silently
abandoning the still-running fit thread and skipping the
``shutil.rmtree(train_dir)`` cleanup.

Contract under test (W2.10 concurrency-test doctrine — deterministic
Events, bounded waits, no sleeps-as-sync):

* a cancellation raised from ``on_iteration`` joins the fit thread with a
  bounded timeout before propagating — never a silent abandon;
* once the thread is joined, ``train_dir`` is removed;
* if the thread refuses to die within the bound, the original exception
  still propagates but carries a loud note naming the zombie and the
  retained ``train_dir`` (left in place for the live writer), plus an
  error-level structured log;
* natural fit errors and clean completions keep their existing contract:
  ``train_dir`` removed, fit error re-raised after cleanup.

The fit is a CPU stand-in (Event-gated callable) — no GPU required.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

# Generous bound for "this MUST happen" waits — only matters on a wedged
# run.  Short bounds are used where the production code itself must give
# up quickly (zombie-path join timeout).
WAIT_MUST_HAPPEN_S = 10.0


class _CancelledForTestError(RuntimeError):
    """Stands in for routes._background_jobs.BackgroundJobStoppedError."""


def _seed_metric_file(train_dir: Path, lines: int = 1) -> None:
    """Write a CatBoost-shaped learn_error.tsv so the first poll sees data."""
    body = "iter\tRMSE\n" + "".join(f"{i}\t0.5\n" for i in range(lines))
    (train_dir / "learn_error.tsv").write_text(body)


# ---------------------------------------------------------------------------
# Full algorithm-level path: CatBoostAlgorithm.fit with a blocking stand-in
# ---------------------------------------------------------------------------


class TestAlgorithmLevelCancel:
    def test_cancel_during_gpu_fit_joins_thread_and_removes_train_dir(self, tmp_path: Path) -> None:
        """User cancel mid-GPU-fit: the fit worker must be joined before the
        cancellation propagates, and the train_dir must be cleaned."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        train_dir = tmp_path / "catboost_gpu_cancel"
        train_dir.mkdir()
        _seed_metric_file(train_dir)

        fit_started = threading.Event()
        fit_release = threading.Event()
        fit_finished = threading.Event()

        class _BlockingModel:
            def fit(self, pool: Any, **kwargs: Any) -> None:
                fit_started.set()
                assert fit_release.wait(timeout=WAIT_MUST_HAPPEN_S), "fit gate was never released"
                fit_finished.set()

        def on_iteration(iteration: int, total: int, metrics: dict) -> None:
            # Mirrors _train_service._on_iteration observing a cancelled
            # job: the (CPU stand-in) fit becomes free to finish, then the
            # cancellation is raised into the polling loop.
            fit_release.set()
            raise _CancelledForTestError("user pressed stop")

        with (
            patch("catboost.CatBoostRegressor", return_value=_BlockingModel()),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("tempfile.mkdtemp", return_value=str(train_dir)),
        ):
            with pytest.raises(_CancelledForTestError):
                algo.fit(
                    None,
                    features=["x1"],
                    cat_features=[],
                    target="y",
                    weight=None,
                    params={"task_type": "GPU", "iterations": 2},
                    task="regression",
                    on_iteration=on_iteration,
                    pool=MagicMock(),
                )

        assert fit_started.is_set(), "fit thread never started — test wiring broken"
        # Deterministic leak pin: the cleanup must have run before the
        # cancellation propagated out of fit().
        assert not train_dir.exists(), "train_dir leaked on cancel"
        # Join pin: by the time fit() raised, the worker had completed —
        # a bounded join happened; the thread was not abandoned.
        assert fit_finished.is_set(), "fit thread was abandoned (zombie) on cancel"


# ---------------------------------------------------------------------------
# Helper-level contract: _run_gpu_fit_with_metric_polling
# ---------------------------------------------------------------------------


class TestMetricPollingHelper:
    def _make_dir(self, tmp_path: Path) -> Path:
        train_dir = tmp_path / "gpu_train_dir"
        train_dir.mkdir()
        _seed_metric_file(train_dir)
        return train_dir

    def test_cancel_joins_worker_then_cleans_train_dir(self, tmp_path: Path) -> None:
        from haute.modelling._algorithms import _run_gpu_fit_with_metric_polling

        train_dir = self._make_dir(tmp_path)
        fit_release = threading.Event()
        fit_finished = threading.Event()

        def fit() -> None:
            assert fit_release.wait(timeout=WAIT_MUST_HAPPEN_S)
            fit_finished.set()

        def on_iteration(iteration: int, total: int, metrics: dict) -> None:
            fit_release.set()
            raise _CancelledForTestError("stop")

        with pytest.raises(_CancelledForTestError):
            _run_gpu_fit_with_metric_polling(
                fit,
                train_dir=str(train_dir),
                on_iteration=on_iteration,
                total_iterations=5,
                poll_interval_seconds=0.01,
                abort_join_timeout_seconds=WAIT_MUST_HAPPEN_S,
            )

        assert fit_finished.is_set()
        assert not train_dir.exists()

    def test_zombie_worker_is_loud_and_train_dir_retained(self, tmp_path: Path) -> None:
        """A worker that outlives the bounded join: the cancellation still
        propagates, but with a note naming the zombie + retained dir, and
        an error-level log — never a silent abandon."""
        from haute.modelling._algorithms import _run_gpu_fit_with_metric_polling

        train_dir = self._make_dir(tmp_path)
        fit_release = threading.Event()
        fit_finished = threading.Event()
        worker_threads: list[threading.Thread] = []

        def fit() -> None:
            worker_threads.append(threading.current_thread())
            # Refuses to die within the (tiny) abort join timeout below;
            # the outer bounded wait keeps the suite from hanging.
            assert fit_release.wait(timeout=WAIT_MUST_HAPPEN_S)
            fit_finished.set()

        def on_iteration(iteration: int, total: int, metrics: dict) -> None:
            raise _CancelledForTestError("stop")

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(_CancelledForTestError) as exc_info:
                _run_gpu_fit_with_metric_polling(
                    fit,
                    train_dir=str(train_dir),
                    on_iteration=on_iteration,
                    total_iterations=5,
                    poll_interval_seconds=0.01,
                    abort_join_timeout_seconds=0.05,
                )

        try:
            notes = getattr(exc_info.value, "__notes__", [])
            assert any(str(train_dir) in note for note in notes), (
                f"cancellation must carry a note naming the retained train_dir; got {notes!r}"
            )
            zombie_logs = [
                ev
                for ev in logs
                if ev.get("log_level") == "error" and ev.get("train_dir") == str(train_dir)
            ]
            assert zombie_logs, f"expected an error-level zombie log naming train_dir; got {logs!r}"
            # The dir is retained for the live writer — deleting under a
            # running CatBoost fit would half-destroy its working files.
            assert train_dir.exists()
        finally:
            # Hygiene: release the stand-in worker and prove it exits.
            fit_release.set()
            for worker in worker_threads:
                worker.join(timeout=WAIT_MUST_HAPPEN_S)
                assert not worker.is_alive(), "stand-in fit worker failed to exit"

    def test_fit_error_cleans_train_dir_and_reraises(self, tmp_path: Path) -> None:
        """Natural fit failure (no cancellation): existing contract — the
        loop drains, the train_dir is removed, the fit error re-raised."""
        from haute.modelling._algorithms import _run_gpu_fit_with_metric_polling

        train_dir = self._make_dir(tmp_path)

        def fit() -> None:
            raise RuntimeError("GPU training failed")

        seen: list[int] = []

        def on_iteration(iteration: int, total: int, metrics: dict) -> None:
            seen.append(iteration)

        with pytest.raises(RuntimeError, match="GPU training failed"):
            _run_gpu_fit_with_metric_polling(
                fit,
                train_dir=str(train_dir),
                on_iteration=on_iteration,
                total_iterations=5,
                poll_interval_seconds=0.01,
            )

        assert not train_dir.exists()

    def test_successful_fit_drains_metrics_and_cleans_up(self, tmp_path: Path) -> None:
        """Clean completion: every metric line is reported (including the
        post-exit drain) and the train_dir is removed."""
        from haute.modelling._algorithms import _run_gpu_fit_with_metric_polling

        train_dir = tmp_path / "gpu_train_dir"
        train_dir.mkdir()

        def fit() -> None:
            # Worker writes its metric file just before exiting — only the
            # final drain can observe it.
            _seed_metric_file(train_dir, lines=3)

        seen: list[int] = []

        def on_iteration(iteration: int, total: int, metrics: dict) -> None:
            seen.append(iteration)

        _run_gpu_fit_with_metric_polling(
            fit,
            train_dir=str(train_dir),
            on_iteration=on_iteration,
            total_iterations=3,
            poll_interval_seconds=0.01,
        )

        assert seen == [1, 2, 3]
        assert not train_dir.exists()
