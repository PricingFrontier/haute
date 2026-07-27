"""Opt-in MOD-M05 evidence for the numeric CatBoost array handoff."""

from __future__ import annotations

import gc
import platform
import statistics
import sys
import time
from collections.abc import Callable
from importlib.metadata import version

import numpy as np
import polars as pl
import pytest
from catboost import CatBoostRegressor, Pool

from haute._mlflow_io import _prepare_predict_frame

pytestmark = pytest.mark.perf

_ROWS = 100_000
_FEATURES = 32
_WARMUPS = 1
_SAMPLES = 5
_MATERIALITY_THRESHOLD = 0.20


def _median_pool_ns(operation: Callable[[], Pool]) -> int:
    for _ in range(_WARMUPS):
        pool = operation()
        del pool
        gc.collect()

    samples: list[int] = []
    for _ in range(_SAMPLES):
        started = time.perf_counter_ns()
        pool = operation()
        samples.append(time.perf_counter_ns() - started)
        del pool
        gc.collect()
    return int(statistics.median(samples))


def _record(request: pytest.FixtureRequest, **evidence: object) -> None:
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "numpy": np.__version__,
                    "polars": pl.__version__,
                    "catboost": version("catboost"),
                },
                "materiality_threshold": _MATERIALITY_THRESHOLD,
                "report_artifact": ".cache/perf/perf-report.json",
                **evidence,
            },
        ),
    )


def _numeric_frame() -> tuple[pl.DataFrame, list[str]]:
    row = np.arange(_ROWS, dtype=np.float64)
    feature_names = [f"feature_{index:02d}" for index in range(_FEATURES)]
    frame = pl.DataFrame(
        {
            name: np.sin(row / (index + 3.0)) + (row % (index + 11)) / (index + 1.0)
            for index, name in enumerate(feature_names)
        },
    )
    return frame, feature_names


def test_catboost_numeric_pool_contiguity_decision(request: pytest.FixtureRequest) -> None:
    frame, feature_names = _numeric_frame()
    labels = (
        frame[feature_names[0]].to_numpy()
        + 0.25 * frame[feature_names[1]].to_numpy()
        - 0.1 * frame[feature_names[2]].to_numpy()
    )
    source = _prepare_predict_frame(
        frame,
        feature_names,
        cat_feature_names=frozenset(),
        flavor="catboost",
    )

    assert isinstance(source, np.ndarray)
    assert source.shape == (_ROWS, _FEATURES)
    assert source.dtype == np.float32
    assert source.flags.f_contiguous
    assert not source.flags.c_contiguous

    contiguous = np.ascontiguousarray(source)
    assert contiguous.flags.c_contiguous
    assert not np.shares_memory(source, contiguous)
    assert contiguous.nbytes == source.nbytes
    np.testing.assert_array_equal(contiguous, source)
    extra_copy_bytes = contiguous.nbytes

    def native_pool() -> Pool:
        return Pool(data=source, label=labels, feature_names=feature_names)

    def contiguous_pool() -> Pool:
        return Pool(
            data=np.ascontiguousarray(source),
            label=labels,
            feature_names=feature_names,
        )

    native_ns = _median_pool_ns(native_pool)
    contiguous_ns = _median_pool_ns(contiguous_pool)
    improvement = 1.0 - (contiguous_ns / native_ns)

    native = native_pool()
    candidate = Pool(data=contiguous, label=labels, feature_names=feature_names)
    assert native.get_feature_names() == candidate.get_feature_names() == feature_names
    np.testing.assert_array_equal(native.get_features(), candidate.get_features())
    native_labels = np.asarray(native.get_label())
    candidate_labels = np.asarray(candidate.get_label())
    np.testing.assert_allclose(native_labels, candidate_labels, rtol=1e-7, atol=1e-6)
    max_label_delta = float(np.max(np.abs(native_labels - candidate_labels)))

    model_params = {
        "allow_writing_files": False,
        "depth": 4,
        "iterations": 12,
        "loss_function": "RMSE",
        "random_seed": 1729,
        "thread_count": 1,
        "verbose": False,
    }
    native_model = CatBoostRegressor(**model_params).fit(native)
    candidate_model = CatBoostRegressor(**model_params).fit(candidate)
    native_predictions = np.asarray(native_model.predict(native))
    candidate_predictions = np.asarray(candidate_model.predict(candidate))
    assert native_predictions.dtype == candidate_predictions.dtype
    np.testing.assert_allclose(
        native_predictions,
        candidate_predictions,
        rtol=1e-12,
        atol=1e-12,
    )
    max_prediction_delta = float(
        np.max(np.abs(native_predictions - candidate_predictions)),
    )

    clears_speed_gate = improvement >= _MATERIALITY_THRESHOLD
    avoids_full_matrix_allocation = extra_copy_bytes == 0
    implement_candidate = clears_speed_gate and avoids_full_matrix_allocation
    assert not implement_candidate

    _record(
        request,
        workload=(
            "100,000 rows x 32 deterministic numeric features; compare the production "
            "Fortran-contiguous Float32 matrix -> Pool handoff with "
            "ascontiguousarray(matrix) -> Pool"
        ),
        artifact_paths=[
            "src/haute/modelling/_algorithms.py:_build_pool",
            "src/haute/_mlflow_io.py:_prepare_predict_frame",
        ],
        measured_medians_ns={
            "fortran_direct_pool": native_ns,
            "c_copy_then_pool": contiguous_ns,
        },
        improvement_fraction=improvement,
        source_layout="F",
        source_matrix_bytes=source.nbytes,
        candidate_extra_copy_bytes=extra_copy_bytes,
        result_equivalence={
            "feature_names": True,
            "feature_values": True,
            "max_label_delta": max_label_delta,
            "label_rtol": 1e-7,
            "label_atol": 1e-6,
            "max_seeded_prediction_delta": max_prediction_delta,
            "prediction_rtol": 1e-12,
            "prediction_atol": 1e-12,
            "prediction_dtype": str(native_predictions.dtype),
        },
        decision="no_change",
        decision_reason=(
            "The C-layout candidate necessarily adds one full feature-matrix allocation, "
            "so it cannot satisfy the combined 20%-and-no-extra-allocation gate."
        ),
    )
