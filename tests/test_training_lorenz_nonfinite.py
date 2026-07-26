"""Lorenz-curve call site must not ingest non-finite rows — W3a handoff.

``TrainingJob._compute_metrics`` calls ``compute_lorenz_curve`` at
``_training_job.py:1140`` with the RAW ``(y_true, y_pred, weight)``
arrays.  ``compute_metrics`` protects itself with a finite mask
(``_metrics.py:34``) but the Lorenz call site bypassed that discipline —
one non-finite exposure weight (or, with hardened siblings, a non-finite
prediction) enters the curve's cumulative sums and poisons every later
point with NaN/inf, which the UI then plots as garbage and
``_assert_json_finite`` rejects at the route boundary.

Contract under test (call-site fix only — ``_metrics.py`` itself is
remediation 4b.11's surface and is not edited here):

* rows with a non-finite ``y_true``, ``y_pred``, or weight are excluded
  from BOTH Lorenz curves, exactly as if ``compute_lorenz_curve`` had
  been handed pre-filtered arrays;
* the filtered count is surfaced as a structured
  ``non_finite_values_filtered`` warning (same event name and count/total
  fields as ``_metrics.py:40``) tagged ``diagnostic="lorenz_curve"``;
* all-non-finite input fails loudly before diagnostics payload construction;
* fully finite input is untouched and logs nothing (clean-path pin).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
import structlog.testing

from haute.modelling._metrics import compute_lorenz_curve
from haute.modelling._split import PARTITION_TRAIN
from haute.modelling._training_job import TrainingJob, _SplitResult, _TrainModelResult

_N = 12


class _StubAlgo:
    """Deterministic stand-in: fixed predictions, no optional diagnostics."""

    def __init__(self, predictions: np.ndarray) -> None:
        self._predictions = predictions

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        return [{"feature": "x1", "importance": 1.0}]

    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        offset: str | None = None,
    ) -> np.ndarray:
        assert len(df) == len(self._predictions)
        return self._predictions


def _base_frame(n: int = _N) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "x1": np.linspace(0.0, 1.0, n),
            "y": np.linspace(1.0, float(n), n),
        }
    )


def _write_split_parquet(path: Path, df: pl.DataFrame) -> None:
    df.with_columns(pl.lit(PARTITION_TRAIN).alias("_partition")).write_parquet(path)


def _compute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    predictions: np.ndarray,
    weights: np.ndarray | None,
    inert_siblings: bool = False,
) -> Any:
    """Drive ``_compute_metrics`` directly over an all-train split parquet."""
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])
    if inert_siblings:
        # Sibling diagnostics have their own (out-of-scope) non-finite
        # behaviour; neutralise them so this unit isolates the Lorenz
        # call site.
        monkeypatch.setattr(
            "haute.modelling._metrics.compute_residuals_histogram",
            lambda *a, **kw: ([], {}),
        )
        monkeypatch.setattr(
            "haute.modelling._metrics.compute_actual_vs_predicted",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr("haute.modelling._metrics.compute_double_lift", lambda *a, **kw: [])
        monkeypatch.setattr(
            "haute.modelling._metrics.compute_ave_per_feature",
            lambda *a, **kw: [],
        )

    df = _base_frame()
    if weights is not None:
        df = df.with_columns(pl.Series("w", weights))
    split_path = tmp_path / "split.parquet"
    _write_split_parquet(split_path, df)

    job = TrainingJob(
        name="lorenz_diag",
        data=df,
        target="y",
        weight="w" if weights is not None else None,
        metrics=["rmse"],
        output_dir=str(tmp_path / "out"),
    )
    split_result = _SplitResult(
        split_path=str(split_path),
        owns_tmp=False,
        n_train=_N,
        n_validation=0,
        n_holdout=0,
    )
    train_result = _TrainModelResult(
        model=object(),
        algo=_StubAlgo(predictions),
        fit_result=None,
        fit_params={},
    )
    return job._compute_metrics(
        split_result,
        ["x1"],
        [],
        train_result,
        lambda msg, frac: None,
    )


def _lorenz_filter_events(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ev
        for ev in logs
        if ev.get("event") == "non_finite_values_filtered"
        and ev.get("diagnostic") == "lorenz_curve"
    ]


class TestLorenzCallSiteFiltering:
    def test_non_finite_exposure_rows_are_filtered_from_both_curves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The named W3a scenario: inf/NaN exposure weights reach the call
        site without any earlier crash and poison the cumulative sums."""
        rng = np.random.RandomState(3)
        predictions = np.linspace(0.5, 6.0, _N) + rng.rand(_N) * 0.01
        weights = np.ones(_N)
        weights[2] = np.inf
        weights[5] = np.nan

        with structlog.testing.capture_logs() as logs:
            result = _compute(tmp_path, monkeypatch, predictions=predictions, weights=weights)

        y_true = _base_frame()["y"].to_numpy()
        mask = np.isfinite(y_true) & np.isfinite(predictions) & np.isfinite(weights)
        expected_model, expected_perfect = compute_lorenz_curve(
            y_true[mask], predictions[mask], weights[mask]
        )
        assert result.lorenz_curve == expected_model
        assert result.lorenz_curve_perfect == expected_perfect
        for point in [*result.lorenz_curve, *result.lorenz_curve_perfect]:
            assert math.isfinite(point["cum_weight_frac"])
            assert math.isfinite(point["cum_actual_frac"])

        events = _lorenz_filter_events(logs)
        assert len(events) == 1, f"expected exactly one lorenz filter event; got {logs!r}"
        assert events[0]["count"] == 2
        assert events[0]["total"] == _N

    def test_non_finite_predictions_are_filtered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors ``_metrics.py:34``'s y_true/y_pred mask at the call site
        (sibling diagnostics neutralised — their non-finite handling is
        out of scope here)."""
        predictions = np.linspace(0.5, 6.0, _N)
        predictions[1] = np.inf
        predictions[7] = np.nan

        with structlog.testing.capture_logs() as logs:
            result = _compute(
                tmp_path,
                monkeypatch,
                predictions=predictions,
                weights=None,
                inert_siblings=True,
            )

        y_true = _base_frame()["y"].to_numpy()
        mask = np.isfinite(y_true) & np.isfinite(predictions)
        expected_model, expected_perfect = compute_lorenz_curve(
            y_true[mask], predictions[mask], None
        )
        assert result.lorenz_curve == expected_model
        assert result.lorenz_curve_perfect == expected_perfect

        events = _lorenz_filter_events(logs)
        assert len(events) == 1
        assert events[0]["count"] == 2
        assert events[0]["total"] == _N

    def test_all_rows_non_finite_fails_loudly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        predictions = np.linspace(0.5, 6.0, _N)
        weights = np.full(_N, np.inf)

        with pytest.raises(ValueError, match=rf"All {_N} rows.*non-finite"):
            _compute(tmp_path, monkeypatch, predictions=predictions, weights=weights)

    def test_fully_finite_input_is_untouched_and_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clean-path pin: no filtering, no log event, identical curves."""
        predictions = np.linspace(0.5, 6.0, _N)
        weights = np.ones(_N)

        with structlog.testing.capture_logs() as logs:
            result = _compute(tmp_path, monkeypatch, predictions=predictions, weights=weights)

        y_true = _base_frame()["y"].to_numpy()
        expected_model, expected_perfect = compute_lorenz_curve(y_true, predictions, weights)
        assert result.lorenz_curve == expected_model
        assert result.lorenz_curve_perfect == expected_perfect
        assert _lorenz_filter_events(logs) == []
