"""Fit evidence: thread allotment, round ceiling, fitted rounds, stopping reason (MOD-F01)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from haute._model_explainability import FLOAT32_CONTRIBUTION_TOLERANCE, prediction_tolerance
from haute.modelling._algorithms import CatBoostAlgorithm
from haute.modelling._training_job import TrainingJob
from haute.schemas import FitEvidencePayload


def data(n: int = 200) -> pl.DataFrame:
    rng = np.random.default_rng(3)
    x = rng.random(n)
    return pl.DataFrame({"x": x, "y": 3 * x + rng.normal(scale=0.5, size=n)})


def test_catboost_fit_without_validation_reports_the_configured_rounds() -> None:
    result = CatBoostAlgorithm().fit(
        data(), ["x"], [], "y", None, {"iterations": 17, "loss_function": "RMSE"}, "regression"
    )
    assert (result.rounds_configured, result.rounds_fitted, result.stopping_reason) == (
        17,
        17,
        "none",
    )


def test_catboost_early_stopping_reports_validation_and_fewer_rounds() -> None:
    frame = data(400)
    result = CatBoostAlgorithm().fit(
        frame.head(300),
        ["x"],
        [],
        "y",
        None,
        {"iterations": 2000, "learning_rate": 0.5, "loss_function": "RMSE"},
        "regression",
        eval_df=frame.tail(100),
    )
    assert result.rounds_configured == 2000
    assert result.rounds_fitted is not None and result.rounds_fitted < 2000
    assert result.stopping_reason == "validation"


def test_training_job_publishes_fit_evidence_with_the_thread_allotment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAUTE_TRAINING_THREADS", "2")
    result = TrainingJob(
        name="evidence",
        data=data(),
        target="y",
        loss_function="RMSE",
        params={"iterations": 9},
        metrics=["rmse"],
        output_dir=str(tmp_path),
        split={"validation_size": 0, "holdout_size": 0},
    ).run()
    assert result.fit_evidence == {
        "threads": 2,
        "rounds_configured": 9,
        "rounds_fitted": 9,
        "stopping_reason": "none",
    }
    assert result.final_tree_count == 9
    FitEvidencePayload.model_validate(result.fit_evidence)


def test_candidate_run_records_fit_evidence_as_params(tmp_path: Path) -> None:
    from tests.test_candidate_run import _build

    candidate = _build(
        tmp_path,
        fit_evidence={
            "threads": 4,
            "rounds_configured": 500,
            "rounds_fitted": 120,
            "stopping_reason": "validation",
        },
    )
    assert candidate.params["fit_threads"] == 4
    assert candidate.params["fit_rounds_configured"] == 500
    assert candidate.params["fit_rounds_fitted"] == 120
    assert candidate.params["fit_stopping_reason"] == "validation"


def test_prediction_tolerance_is_shared_and_has_a_named_float32_bound() -> None:
    assert prediction_tolerance(0.5) == 1e-6
    assert prediction_tolerance(1000.0) == pytest.approx(1e-3)
    assert prediction_tolerance(2.0, relative=FLOAT32_CONTRIBUTION_TOLERANCE) == pytest.approx(2e-5)
