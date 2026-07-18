"""Adversarial repro: CatBoost classification 'prediction' semantics mismatch.

CLAIM under test:
  - Training metrics path (`CatBoostAlgorithm.predict`) returns
    `predict_proba(x)[:, 1]` for a classifier -> a PROBABILITY.
  - Score path (`ScoringModel.predict` / `_score_eager_unified`) returns
    `model.predict(x)` -> the HARD class LABEL (0/1).
  - Therefore residuals_histogram / actual_vs_predicted (training diagnostics)
    are computed as (label - probability), while the deployed 'prediction'
    column holds the hard label; the SAME model reports a 'prediction' with
    two different meanings.

This script exercises the REAL production functions cited in the finding:
  - haute.modelling._algorithms.CatBoostAlgorithm.predict   (train-metrics predict)
  - haute._mlflow_io._wrap_catboost(...).predict            (score-time predict)
  - haute._model_scorer._predict_positive_proba(...)        (score-time proba)
  - haute.modelling._metrics.compute_residuals_histogram    (train diagnostic)
  - haute.modelling._metrics.compute_actual_vs_predicted    (train diagnostic)

It ASSERTS the specific wrong behaviour: the train-time 'prediction' values
are continuous probabilities while the score-time 'prediction' values are the
integer {0,1} labels, and the residuals computed in diagnostics equal
(label - probability) rather than (label - label).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import polars as pl

# Isolate any disk I/O / project-root resolution inside a tempdir.
import haute._sandbox as _sandbox

_TMP = Path(tempfile.mkdtemp(prefix="catboost_predict_semantics_"))
_sandbox.set_project_root(_TMP)

from catboost import CatBoostClassifier

from haute._mlflow_io import _wrap_catboost
from haute._model_scorer import _predict_positive_proba
from haute.modelling._algorithms import CatBoostAlgorithm
from haute.modelling._metrics import (
    compute_actual_vs_predicted,
    compute_residuals_histogram,
)


def main() -> int:
    rng = np.random.default_rng(0)
    n = 400
    # Two numeric features with a learnable signal so the model is non-degenerate
    # (predicted probabilities land strictly inside (0, 1), not all 0/1).
    x0 = rng.normal(size=n)
    x1 = rng.normal(size=n)
    logit = 0.9 * x0 - 0.6 * x1
    p = 1.0 / (1.0 + np.exp(-logit))
    y = (rng.uniform(size=n) < p).astype(int)

    features = ["f0", "f1"]
    df = pl.DataFrame({"f0": x0, "f1": x1, "target": y})

    # Train a tiny CatBoost classifier (task='classification').
    model = CatBoostClassifier(
        iterations=60,
        depth=3,
        learning_rate=0.2,
        loss_function="Logloss",
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(df.select(features).to_numpy(), y)

    # ── TRAIN-METRICS PATH: exact cited function ──
    algo = CatBoostAlgorithm()
    train_pred = np.asarray(algo.predict(model, df, features)).flatten()

    # ── SCORE PATH: exact cited code ──
    scoring = _wrap_catboost(model)  # flavor='catboost' ScoringModel
    x_data = df.select(features).to_numpy()
    score_pred = scoring.predict(x_data)  # ScoringModel.predict -> model.predict
    score_proba = _predict_positive_proba(scoring.raw_model, x_data, "prediction")
    score_proba = np.asarray(score_proba).flatten()

    # Independent ground-truth references.
    raw_proba = model.predict_proba(x_data)[:, 1]
    raw_label = np.asarray(model.predict(x_data)).flatten().astype(float)

    print("--- value samples (first 8 rows) ---")
    print("train-metrics predict :", np.round(train_pred[:8], 4))
    print("score-path  predict   :", score_pred[:8].astype(float))
    print("score-path  _proba    :", np.round(score_proba[:8], 4))

    # ---- Characterise each surface ----
    train_is_continuous = np.any((train_pred > 1e-6) & (train_pred < 1 - 1e-6))
    score_is_binary_labels = np.array_equal(np.unique(score_pred), np.array([0, 1])) or set(
        np.unique(score_pred).tolist()
    ).issubset({0, 1})

    print("\n--- characterisation ---")
    print("train-metrics predict has continuous values in (0,1):", bool(train_is_continuous))
    print("score-path predict values subset of {0,1}          :", bool(score_is_binary_labels))
    print("max |train_pred - raw_proba| :", float(np.max(np.abs(train_pred - raw_proba))))
    print("max |score_pred - raw_label| :", float(np.max(np.abs(score_pred.astype(float) - raw_label))))
    print("max |score_proba - raw_proba|:", float(np.max(np.abs(score_proba - raw_proba))))

    # ---- ASSERTIONS on the SPECIFIC wrong behaviour ----

    # 1. Train-metrics 'prediction' == positive-class probability (continuous).
    assert train_is_continuous, "EXPECTED train-metrics predict to be continuous probabilities"
    np.testing.assert_allclose(
        train_pred, raw_proba, rtol=0, atol=1e-9,
        err_msg="train-metrics predict should equal predict_proba[:,1]",
    )

    # 2. Score-path 'prediction' == hard class label in {0,1} (NOT probability).
    assert score_is_binary_labels, "EXPECTED score-path predict to be hard {0,1} labels"
    np.testing.assert_allclose(
        score_pred.astype(float), raw_label, rtol=0, atol=0,
        err_msg="score-path predict should equal model.predict() hard labels",
    )

    # 3. The two surfaces DISAGREE meaningfully for the SAME model+rows.
    #    (If they agreed, there would be no bug.)
    max_div = float(np.max(np.abs(train_pred - score_pred.astype(float))))
    print("\nmax |train_pred - score_pred| :", max_div)
    assert max_div > 0.1, (
        "EXPECTED train-time 'prediction' (probability) to differ from "
        "score-time 'prediction' (label); same column, two meanings"
    )

    # 4. Diagnostics consume the PROBABILITY-valued y_pred, so residuals are
    #    (label - probability), NOT (label - label). Demonstrate the residual
    #    used in the histogram is the probability residual.
    w = None
    _, stats = compute_residuals_histogram(y.astype(float), train_pred, w)
    ave = compute_actual_vs_predicted(y.astype(float), train_pred, w)

    # The residual-mean reported equals mean(label - probability).
    expected_resid_mean = float(np.mean(y.astype(float) - train_pred))
    print("\nresiduals_histogram stats['mean'] :", stats["mean"])
    print("mean(label - probability)         :", round(expected_resid_mean, 6))
    np.testing.assert_allclose(
        stats["mean"], round(expected_resid_mean, 6), rtol=0, atol=1e-6,
        err_msg="residuals histogram mean must reflect (label - probability)",
    )

    # The actual_vs_predicted 'predicted' values are probabilities (continuous),
    # i.e. they match the train-time probability surface, NOT the {0,1} labels
    # that the deployed 'prediction' column would carry.
    ave_pred_vals = np.array([row["predicted"] for row in ave], dtype=float)
    ave_continuous = np.any((ave_pred_vals > 1e-6) & (ave_pred_vals < 1 - 1e-6))
    print("actual_vs_predicted 'predicted' continuous:", bool(ave_continuous))
    assert ave_continuous, (
        "EXPECTED actual_vs_predicted 'predicted' to be probabilities, "
        "diverging from the scored hard-label 'prediction'"
    )

    print("\nREPRODUCED: same CatBoost classifier -> 'prediction' is a PROBABILITY "
          "in training diagnostics but a HARD LABEL {0,1} at score time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
