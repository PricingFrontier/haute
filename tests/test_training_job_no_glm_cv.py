"""Regression tests for Phase 2 Package 2C-5 — delete GLM CV path.

Item #60: ``src/haute/modelling/_training_job.py`` had a cross-validation
branch gated on ``self.cv_folds > 1`` that called ``algo.cross_validate()``
and silently swallowed exceptions into ``diagnostics_errors``.

Reviewer decision (locked): **delete** the branch. No callers set
``cv_folds`` from the UI, the feature was effectively dead, and silent
exception-swallowing violates the project's fail-loud policy.

These tests lock in the post-delete state so the CV path cannot
re-appear. They are the contract the dev must satisfy.

Expected state pre-delete:
    - Tests 1, 3, 5 **fail** — the CV code / references still exist.
    - Tests 2, 4 **pass** regardless (tiny GLM fit keeps working).

Expected state post-delete:
    - All tests pass.
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

import numpy as np
import polars as pl
import pytest

# Skip the whole module if RustyStats is not installed — the "training
# still works without CV" test needs a real GLM fit end-to-end.
rs = pytest.importorskip("rustystats", reason="rustystats not installed")

from haute.modelling._training_job import TrainingJob, TrainResult  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "haute"


def _iter_src_py_files() -> list[Path]:
    """Every ``.py`` file under ``src/haute/`` (excluding ``__pycache__``)."""
    return [
        p
        for p in SRC_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _tiny_glm_df(n: int = 120, seed: int = 42) -> pl.DataFrame:
    """Small deterministic DataFrame the GLM can actually fit."""
    rng = np.random.default_rng(seed)
    return pl.DataFrame(
        {
            "driver_age": rng.integers(18, 70, n),
            "vehicle_age": rng.integers(0, 20, n),
            "area": rng.choice(["A", "B", "C"], n),
            "exposure": rng.uniform(0.5, 1.0, n),
            "claim_count": rng.poisson(0.1, n),
        }
    )


def _fit_tiny_glm(tmp_path: Path, **extra_kwargs) -> TrainResult:
    """Fit a tiny RustyStats GLM and return the ``TrainResult``.

    ``extra_kwargs`` are forwarded into the ``TrainingJob`` constructor so
    individual tests can probe the effect of (or absence of) ``cv_folds``.
    """
    df = _tiny_glm_df()
    job = TrainingJob(
        name="glm_no_cv_test",
        data=df,
        target="claim_count",
        weight="exposure",
        algorithm="glm",
        task="regression",
        params={
            "family": "poisson",
            "terms": {
                "driver_age": {"type": "linear"},
                "vehicle_age": {"type": "linear"},
                "area": {"type": "categorical"},
            },
        },
        split={"strategy": "random", "validation_size": 0.2, "seed": 42},
        metrics=["gini", "poisson_deviance"],
        output_dir=str(tmp_path),
        **extra_kwargs,
    )
    return job.run()


# ---------------------------------------------------------------------------
# Test 1: Source does not contain CV code
# ---------------------------------------------------------------------------


class TestTrainingJobSourceHasNoCV:
    """After the delete, the ``TrainingJob`` source must not reference
    the CV branch. These symbols are the tombstones that mark the dead
    path's old location; grepping for them will catch any revert or
    re-introduction."""

    def test_trainingjob_source_has_no_cross_validate_call(self) -> None:
        """``TrainingJob`` class source must not mention ``cross_validate``.

        The deleted branch called ``algo.cross_validate(...)``. After the
        delete, no call to this method may remain in the class body — the
        algorithm interface keeps the method for unit tests, but the
        orchestrator no longer invokes it.
        """
        source = inspect.getsource(TrainingJob)
        assert "cross_validate" not in source, (
            "TrainingJob source still contains 'cross_validate' — "
            "the GLM CV branch must be fully removed (Phase 2 Package 2C-5)."
        )

    def test_trainingjob_source_has_no_cv_folds_gate(self) -> None:
        """``TrainingJob`` source must not contain the ``cv_folds > 1`` gate.

        The CV branch was gated on ``self.cv_folds > 1``. With the branch
        gone, that comparison should be gone as well. We check for both
        the exact pattern and the ``need_cv`` local variable.
        """
        source = inspect.getsource(TrainingJob)
        assert "cv_folds > 1" not in source, (
            "TrainingJob source still contains 'cv_folds > 1' gate — "
            "the dead CV branch must be removed."
        )
        assert "need_cv" not in source, (
            "TrainingJob source still contains 'need_cv' local — "
            "the dead CV branch must be removed entirely."
        )

    def test_trainingjob_source_does_not_record_cv_diag_error(self) -> None:
        """The CV branch's silent exception-swallow must not remain.

        The old branch did ``_record_diag_error(diagnostics_errors, "cv", exc)``
        inside an ``except Exception`` — precisely the fail-loud violation
        that motivated the delete. After the delete, no ``"cv"`` tag is
        recorded into ``diagnostics_errors`` from this module.
        """
        source = inspect.getsource(TrainingJob)
        assert '"cv"' not in source, (
            "TrainingJob source still records a 'cv' diagnostic error — "
            "the silent-swallow CV branch must be fully removed."
        )


# ---------------------------------------------------------------------------
# Test 2: cv_folds attribute has no runtime effect
# ---------------------------------------------------------------------------


class TestCvFoldsHasNoRuntimeEffect:
    """After the delete, one of two outcomes is acceptable:

      (a) ``cv_folds`` is removed from ``TrainingJob.__init__`` entirely —
          passing it as a kwarg would raise ``TypeError``.
      (b) ``cv_folds`` survives as a soft-deprecated no-op — passing it
          does not change training output.

    This test accepts either. It first tries to build a job with
    ``cv_folds=5`` — if that raises ``TypeError`` we're done (outcome
    (a)). Otherwise we compare the metrics of a ``cv_folds=5`` run
    against a ``cv_folds=1`` run with the same seed; they must be
    identical because CV was never affecting the primary model fit.
    """

    def test_cv_folds_kwarg_is_removed_or_noop(self, tmp_path) -> None:
        """Setting ``cv_folds=5`` either raises ``TypeError`` (arg gone)
        or produces metrics identical to ``cv_folds=1`` (no-op)."""
        # (a) attribute removed — kwarg is rejected at the constructor
        try:
            result_cv5 = _fit_tiny_glm(tmp_path / "cv5", cv_folds=5)
        except TypeError as e:
            # Accepted: the argument has been fully removed.
            assert "cv_folds" in str(e), (
                f"TypeError raised for cv_folds kwarg should mention 'cv_folds' "
                f"in its message, got: {e}"
            )
            return

        # (b) argument still exists but must be a no-op. Same seed → same
        # metrics regardless of cv_folds value. We compare against a
        # cv_folds=1 run (which even pre-delete skipped the CV branch).
        result_cv1 = _fit_tiny_glm(tmp_path / "cv1", cv_folds=1)

        # Primary metrics must be identical — CV never fed back into the
        # model fit, so changing cv_folds must not perturb them. This is
        # the core "no runtime effect" contract: the delete is removing
        # a branch whose only output was side-channel cv_results, so the
        # primary training output is unchanged. Whether the side-channel
        # is also gone is enforced separately by TestTrainResultHasNoCvFields.
        assert result_cv5.metrics == result_cv1.metrics, (
            "cv_folds survived as a kwarg but changed primary metrics — "
            "the CV branch must be a complete no-op after the delete."
        )


# ---------------------------------------------------------------------------
# Test 3: No caller in src/ references cv_folds
# ---------------------------------------------------------------------------


class TestNoSrcReferenceToCvFolds:
    """``cv_folds`` must be fully excised from ``src/haute/``.

    Any remaining Python reference in src would mean the feature is
    still threaded through config / export / routing paths, which is
    exactly the dead-code smell the delete is removing.

    Tests under ``tests/`` are allowed to still reference ``cv_folds``
    as regression guards (this test file is one such example) — so we
    scan only ``src/haute/``.
    """

    def test_no_cv_folds_string_in_src_python_sources(self) -> None:
        """No ``.py`` file under ``src/haute/`` contains the string
        ``cv_folds``.

        Uses a pure-Python walk rather than a ``subprocess`` call to
        ``grep`` so the check is portable across Windows / macOS / Linux
        CI runners.
        """
        offenders: list[str] = []
        for path in _iter_src_py_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Non-UTF-8 file (unlikely in src) — skip; grep would
                # also skip in --binary-files=without-match mode.
                continue
            if "cv_folds" in text:
                offenders.append(str(path.relative_to(SRC_DIR.parent.parent)))

        assert not offenders, (
            "Found lingering references to 'cv_folds' in src/haute/ after "
            "the Phase 2 Package 2C-5 delete. All call-sites must be "
            "removed. Offending files:\n  " + "\n  ".join(sorted(offenders))
        )

    def test_no_cross_validate_call_in_training_job_module(self) -> None:
        """Belt-and-braces: the training_job module file itself must
        not contain the literal ``cross_validate`` string.

        Module-level test (not class-source) catches the case where
        someone moves the CV branch to a free function instead of a
        method — the grep-on-file check would still flag it."""
        training_job_path = SRC_DIR / "modelling" / "_training_job.py"
        text = training_job_path.read_text(encoding="utf-8")
        assert "cross_validate" not in text, (
            f"{training_job_path} still contains 'cross_validate' — "
            "the GLM CV path must be deleted, not merely moved."
        )


# ---------------------------------------------------------------------------
# Test 4: Training still works without CV
# ---------------------------------------------------------------------------


class TestGlmTrainsWithoutCv:
    """End-to-end sanity: after the delete, a tiny GLM must still train
    and produce a valid ``TrainResult``. This is the functional
    contract — the delete removes a branch, it must not break the
    primary pipeline."""

    def test_glm_trains_with_cv_folds_unset(self, tmp_path) -> None:
        """Default path — no ``cv_folds`` kwarg — produces a valid result."""
        result = _fit_tiny_glm(tmp_path)

        # Basic TrainResult sanity checks
        assert isinstance(result, TrainResult)
        assert result.model_path.endswith(".rsglm")
        assert Path(result.model_path).exists()
        assert result.train_rows > 0
        assert result.test_rows > 0

        # GLM-specific artefacts still populated — the delete must not
        # have collaterally damaged the GLM diagnostics branch.
        assert len(result.glm_coefficients) > 0
        assert len(result.glm_relativities) > 0
        assert "deviance" in result.glm_fit_statistics

        # Primary metrics all finite
        assert result.metrics, "GLM run should produce metrics"
        for key, value in result.metrics.items():
            assert np.isfinite(value), f"metric {key!r} is not finite: {value}"


# ---------------------------------------------------------------------------
# Test 5: TrainResult has no CV fields
# ---------------------------------------------------------------------------


class TestTrainResultHasNoCvFields:
    """After the delete, the ``TrainResult`` dataclass should no longer
    expose CV-specific fields, and runs should not surface CV entries
    anywhere in their diagnostics payload.

    We accept two post-delete shapes:
      (a) the ``cv_results`` field is removed from the dataclass, OR
      (b) the field survives but is always ``None`` / empty and no CV
          key appears in ``diagnostics_errors``.
    """

    def test_trainresult_has_no_cv_prefixed_field(self) -> None:
        """No top-level ``cv_*`` key on the dataclass — or if one
        survives (``cv_results`` is the only candidate), it must default
        to ``None`` so no caller ever observes CV data through it."""
        field_names = {f.name for f in fields(TrainResult)}
        cv_fields = {name for name in field_names if name.startswith("cv_")}

        if not cv_fields:
            # Outcome (a): fully removed — done.
            return

        # Outcome (b): surviving cv_* fields must all default to a
        # sentinel (None / empty). We enforce the default via the
        # dataclass ``field`` metadata.
        for f in fields(TrainResult):
            if f.name.startswith("cv_"):
                # dataclass default can live on ``f.default`` or on a
                # ``field(default_factory=...)``; both must produce a
                # falsy value so the field never surfaces real CV data.
                if f.default is not None and not callable(
                    getattr(f, "default_factory", None)
                ):
                    pytest.fail(
                        f"TrainResult.{f.name} survives but has a non-None "
                        f"default ({f.default!r}) — after the CV delete it "
                        "must default to None/empty so no caller can depend "
                        "on it."
                    )

    def test_trainresult_instance_has_no_cv_data_after_run(
        self, tmp_path
    ) -> None:
        """A fresh ``TrainResult`` from a real run must not carry CV data,
        *even when the caller asks for it*.

        Pre-delete, passing ``cv_folds=5`` populates ``cv_results`` with
        fold metrics — and failures silently land in ``diagnostics_errors``
        under the ``"cv"`` key. Both channels must be dead post-delete.

        We pass ``cv_folds=5`` explicitly so the test is sensitive to the
        pre-impl behaviour (pre-impl: CV runs and ``cv_results`` is a
        populated dict → assertion fails). If the kwarg has been removed
        from the signature, we fall back to a default run — the contract
        is the same either way.
        """
        # Request CV; accept that the kwarg may have been removed.
        try:
            result = _fit_tiny_glm(tmp_path, cv_folds=5)
        except TypeError:
            # cv_folds argument has been removed — good. Run without it
            # so we can still assert on the TrainResult shape.
            result = _fit_tiny_glm(tmp_path)

        # Guard against a surviving cv_results field carrying data.
        # Pre-delete with cv_folds=5 this is a populated dict; after the
        # delete it must be None / empty regardless of the caller's
        # request.
        cv_results = getattr(result, "cv_results", None)
        assert cv_results in (None, {}, []), (
            f"TrainResult.cv_results should be empty after the delete, "
            f"got: {cv_results!r}"
        )

        # Guard against a CV diagnostic error being surfaced — the
        # silent-swallow ``_record_diag_error(..., 'cv', ...)`` call is
        # the exact anti-pattern the delete removes.
        cv_diag_entries = [
            entry
            for entry in result.diagnostics_errors
            if entry.get("diagnostic") == "cv"
        ]
        assert not cv_diag_entries, (
            f"TrainResult.diagnostics_errors contains CV entries "
            f"({cv_diag_entries!r}) — the CV exception-swallow branch "
            "must be removed entirely, not merely disabled."
        )
