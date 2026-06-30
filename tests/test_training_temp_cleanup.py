"""Training temp-parquet lifecycle — remediation 4b.6.

CODE_REVIEW MEDIUM "Modelling": split parquet leaks on failure/cancel
(``_training_job.py:1186``).  ``TrainingJob.run`` writes up to three temp
parquets (prepared input ``haute_split_*``, null-cleaned input
``haute_clean_*``, and the split-with-partition file ``haute_split_*``).
Before the fix, their only deletions sat inline on the success path: the
prepared input was removed by ``_split_data`` after the split file was
written, and the split file at the tail of ``_compute_metrics``.  Any
failure or cancellation between those points orphaned multi-GB parquets
in the OS temp dir.

Contract under test:

* an aborted run — cancellation raised from ``check_cancelled`` at any
  stage, or a hard failure inside the metrics phase or data prep — leaves
  no ``haute_split_*`` / ``haute_clean_*`` parquet behind;
* the success path still deletes everything (nothing reuses the split
  file post-run: its only consumers are ``_train_model`` and
  ``_compute_metrics`` inside the same ``run()``);
* externally supplied parquet inputs (``owns_tmp=False``) are NEVER
  deleted, aborted run or not.

Temp isolation: ``tempfile.tempdir`` is pointed at a per-test directory so
the leak assertions cannot collide with other tests or processes.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from haute.modelling._training_job import TrainingJob

_FAST_PARAMS = {"iterations": 2, "depth": 1, "verbose": 0}


class _CancelledForTestError(RuntimeError):
    """Stands in for routes._background_jobs.BackgroundJobStoppedError."""


@pytest.fixture()
def temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``tempfile`` into a per-test directory for leak assertions."""
    root = tmp_path / "tmproot"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _haute_temp_files(root: Path) -> list[str]:
    return sorted(
        p.name for p in root.iterdir() if p.name.startswith(("haute_split_", "haute_clean_"))
    )


def _training_frame(n: int = 80, *, null_targets: int = 0) -> pl.DataFrame:
    rng = np.random.RandomState(0)
    target = rng.poisson(0.3, n).astype(np.float64).tolist()
    for i in range(null_targets):
        target[i] = None
    return pl.DataFrame(
        {
            "x1": rng.randn(n),
            "x2": rng.randn(n),
            "y": pl.Series(target, dtype=pl.Float64),
            "exposure": np.ones(n),
        }
    )


def _cancel_at(stage_message: str) -> tuple[Callable[[str, float], None], Callable[[], None]]:
    """Build (progress, check_cancelled) that mirrors the train service.

    ``TrainingJob.run``'s ``_report`` calls ``check_cancelled`` immediately
    after ``progress``, so flipping the flag inside ``progress`` raises the
    cancellation at exactly the named stage — the same interleaving the
    real ``_train_service`` produces when the user presses stop.
    """
    state = {"cancel": False}

    def progress(msg: str, frac: float) -> None:
        if msg == stage_message:
            state["cancel"] = True

    def check_cancelled() -> None:
        if state["cancel"]:
            raise _CancelledForTestError(stage_message)

    return progress, check_cancelled


def _job(data: str | pl.DataFrame, tmp_path: Path, **overrides: object) -> TrainingJob:
    kwargs: dict = {
        "name": "cleanup_model",
        "data": data,
        "target": "y",
        "weight": "exposure",
        "params": dict(_FAST_PARAMS),
        "metrics": ["rmse"],
        "split": {"validation_size": 0.2, "holdout_size": 0.0, "seed": 7},
        "output_dir": str(tmp_path / "out"),
    }
    kwargs.update(overrides)
    return TrainingJob(**kwargs)


def _silence_optional_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip SHAP / loss-importance / PDP so full runs stay fast."""
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])


# ---------------------------------------------------------------------------
# Cancellation windows
# ---------------------------------------------------------------------------


class TestCancelCleansTempParquets:
    def test_cancel_after_split_write_removes_split_parquet(
        self, temp_root: Path, tmp_path: Path
    ) -> None:
        """Cancel raised at the 'Training model' report: the split parquet
        exists at that instant and must not survive the aborted run."""
        progress, check_cancelled = _cancel_at("Training model")
        job = _job(_training_frame(), tmp_path)

        with pytest.raises(_CancelledForTestError):
            job.run(progress=progress, check_cancelled=check_cancelled)

        assert _haute_temp_files(temp_root) == [], (
            "split parquet leaked after cancellation between split and fit"
        )

    def test_cancel_before_split_removes_prepared_parquet(
        self, temp_root: Path, tmp_path: Path
    ) -> None:
        """Cancel raised at the 'Splitting data' report: only the prepared
        input parquet exists at that instant; it must be removed."""
        progress, check_cancelled = _cancel_at("Splitting data")
        job = _job(_training_frame(), tmp_path)

        with pytest.raises(_CancelledForTestError):
            job.run(progress=progress, check_cancelled=check_cancelled)

        assert _haute_temp_files(temp_root) == [], (
            "prepared input parquet leaked after cancellation before the split"
        )

    def test_cancel_before_split_removes_null_cleaned_parquet(
        self, temp_root: Path, tmp_path: Path
    ) -> None:
        """With null targets the prepared input is the ``haute_clean_*``
        rewrite; an abort before the split must remove it too."""
        progress, check_cancelled = _cancel_at("Splitting data")
        job = _job(_training_frame(null_targets=5), tmp_path)

        with pytest.raises(_CancelledForTestError):
            job.run(progress=progress, check_cancelled=check_cancelled)

        assert _haute_temp_files(temp_root) == [], (
            "null-cleaned temp parquet leaked after cancellation before the split"
        )

    def test_cancel_after_fit_removes_split_parquet(
        self, temp_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancel raised at the 'Evaluating model' report — after the fit
        completed but before ``_compute_metrics`` ran its inline unlink."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        _silence_optional_diagnostics(monkeypatch)
        progress, check_cancelled = _cancel_at("Evaluating model")
        job = _job(_training_frame(), tmp_path)

        with pytest.raises(_CancelledForTestError):
            job.run(progress=progress, check_cancelled=check_cancelled)

        assert _haute_temp_files(temp_root) == [], (
            "split parquet leaked after cancellation between fit and metrics"
        )


# ---------------------------------------------------------------------------
# Hard failures
# ---------------------------------------------------------------------------


class TestFailureCleansTempParquets:
    def test_metrics_failure_removes_split_parquet(
        self, temp_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure inside the metrics phase fires before the inline unlink
        at the tail of ``_compute_metrics`` — the split parquet must still
        be removed, and the original error must propagate unchanged."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")

        def _exploding_metrics(*args: object, **kwargs: object) -> dict[str, float]:
            raise RuntimeError("metrics exploded")

        monkeypatch.setattr(
            "haute.modelling._training_job.compute_metrics",
            _exploding_metrics,
        )
        job = _job(_training_frame(), tmp_path)

        with pytest.raises(RuntimeError, match="metrics exploded"):
            job.run()

        assert _haute_temp_files(temp_root) == [], (
            "split parquet leaked after a failure inside the metrics phase"
        )

    def test_prepare_data_validation_failure_removes_prepared_parquet(
        self, temp_root: Path, tmp_path: Path
    ) -> None:
        """A DataFrame input is sunk to a temp parquet before column
        validation; a validation failure must not orphan that file."""
        job = _job(_training_frame(), tmp_path, target="no_such_column")

        with pytest.raises(ValueError, match="no_such_column"):
            job.run()

        assert _haute_temp_files(temp_root) == [], (
            "prepared input parquet leaked after a data-prep validation failure"
        )


# ---------------------------------------------------------------------------
# Clean-path pins
# ---------------------------------------------------------------------------


class TestSuccessAndForeignInputs:
    def test_successful_run_leaves_no_temp_parquets(
        self, temp_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Success-path characterisation: nothing reuses the split parquet
        after ``run()`` returns, so a clean run leaves the temp dir empty
        and the model artifact on disk."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        _silence_optional_diagnostics(monkeypatch)
        job = _job(_training_frame(), tmp_path)

        result = job.run()

        assert _haute_temp_files(temp_root) == []
        assert Path(result.model_path).is_file()

    def test_external_parquet_input_survives_aborted_run(
        self, temp_root: Path, tmp_path: Path
    ) -> None:
        """Caller-owned parquet inputs (``owns_tmp=False``) are never
        deleted by abort cleanup — only run-owned temp files are."""
        input_path = tmp_path / "caller_owned.parquet"
        _training_frame().write_parquet(input_path)
        progress, check_cancelled = _cancel_at("Training model")
        job = _job(str(input_path), tmp_path)

        with pytest.raises(_CancelledForTestError):
            job.run(progress=progress, check_cancelled=check_cancelled)

        assert input_path.is_file(), "abort cleanup deleted a caller-owned input parquet"
        assert _haute_temp_files(temp_root) == [], (
            "split parquet leaked for an external-parquet training input"
        )
