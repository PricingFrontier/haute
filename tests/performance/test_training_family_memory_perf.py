"""Fresh-process evidence for training memory per model family (MOD-M02).

``_estimate_peak_bytes`` (``src/haute/_ram_estimate.py``) multiplies rows by the
source row width and one overhead multiplier for every family. Each family here
trains a canonical Haute job on the same generated parquet (30 numeric and 5 string
categorical features, a Poisson frequency target, an exposure weight) in a fresh
process, as the training worker runs it, at several row counts
(``_training_family_memory_probe.py``). The figures set against the estimate's
prediction for the same source decide whether the multiplier becomes per family.

The second test compares XGBoost's external-memory ``ExtMemQuantileDMatrix`` with
the in-memory ``QuantileDMatrix`` on the same data, seed and ``max_bin``: peak
resident memory, wall time, predictions and the per-iteration training objective,
the first decision input the MOD-M02 roadmap entry names.

Both tests assert only that every probe completes: the figures are evidence.

The third test is MOD-M01's acceptance gate, so the pull-request perf lane runs it: the
canonical GLM job out of core finishes within a memory budget the in-memory job exceeds.
``HAUTE_POLARS_PERF_SCALE`` picks the row counts; ``ci`` keeps the run short and
``1m`` is the decision scale.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Iterator
from importlib.metadata import packages_distributions, version
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from tests.performance._training_family_memory_probe import (
    CATEGORICAL_LEVELS,
    EVALUATION,
    FAMILIES,
    FAMILY_PARAMS,
    NUMERIC_FEATURES,
    PREDICTION_ROWS,
    ROW_GROUP_ROWS,
    TRAINING_THREADS,
    XGBOOST_PARAMS,
    XGBOOST_ROUNDS,
    XGBOOST_VARIANTS,
    current_estimate,
    write_training_parquet,
)

pytestmark = pytest.mark.perf

_PROBE = Path(__file__).with_name("_training_family_memory_probe.py")
_FAMILY_ROWS_BY_SCALE = {
    "ci": (20_000, 80_000),
    "1m": (100_000, 400_000, 1_600_000),
    "10m": (100_000, 400_000, 1_600_000),
}
#: Rows for the chunked GLM acceptance: where the in-memory fit's design dominates.
_GLM_CHUNKED_ROWS_BY_SCALE = {"ci": 300_000, "1m": 1_600_000, "10m": 6_400_000}
#: The out-of-core job's growth budget, as a share of the in-memory job's growth.
_GLM_CHUNKED_BUDGET_SHARE = 3 / 4
_XGBOOST_ROWS_BY_SCALE = {
    "ci": (80_000,),
    "1m": (1_600_000, 6_400_000),
    "10m": (1_600_000, 6_400_000),
}
#: Every engine and the Polars engine get the same thread cap.
_THREAD_ENVIRONMENT = {
    "HAUTE_TRAINING_THREADS": str(TRAINING_THREADS),
    "POLARS_MAX_THREADS": str(TRAINING_THREADS),
    "OMP_NUM_THREADS": str(TRAINING_THREADS),
    "RAYON_NUM_THREADS": str(TRAINING_THREADS),
}
_PROBE_TIMEOUT_SECONDS = 1_800
#: A progress stage is listed when it raised the high-water mark by at least this.
_PEAK_RISE_BYTES = 16 * 1024**2


def _scale() -> str:
    scale = os.environ.get("HAUTE_POLARS_PERF_SCALE", "ci")
    if scale not in _FAMILY_ROWS_BY_SCALE:
        raise AssertionError(f"Unsupported HAUTE_POLARS_PERF_SCALE={scale!r}")
    return scale


def _engine_versions() -> dict[str, str]:
    distributions = packages_distributions()
    modules = ("polars", "numpy", "catboost", "xgboost", "lightgbm", "interpret", "t_boost")
    versions = {module: version(distributions[module][0]) for module in modules}
    # By distribution name: an editable RustyStats build has no top-level module
    # record for packages_distributions to map.
    versions["rustystats"] = version("rustystats")
    return versions


def _record(request: pytest.FixtureRequest, **evidence: object) -> None:
    total_ram = (
        os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else None
    )
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "cpu_count": os.cpu_count(),
                    "total_ram_bytes": total_ram,
                    "thread_cap": TRAINING_THREADS,
                    **_engine_versions(),
                },
                "scale": _scale(),
                "workload": {
                    "numeric_features": len(NUMERIC_FEATURES),
                    "categorical_levels": CATEGORICAL_LEVELS,
                    "target": "Poisson frequency (claims / exposure), exposure weight",
                    "row_group_rows": ROW_GROUP_ROWS,
                },
                "report_artifact": ".cache/perf/perf-report.json",
                **evidence,
            },
        ),
    )


@pytest.fixture(scope="module")
def training_source(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Callable[[int], dict[str, Any]]]:
    """Generate each row count's parquet once, with its in-memory size and estimate."""
    root = tmp_path_factory.mktemp("training-family-memory")
    sources: dict[int, dict[str, Any]] = {}

    def source(rows: int) -> dict[str, Any]:
        if rows not in sources:
            path = root / f"training-{rows}.parquet"
            write_training_parquet(path, rows)
            frame = pl.read_parquet(path)
            sources[rows] = {
                "path": path,
                "parquet_bytes": path.stat().st_size,
                "frame_bytes": int(frame.estimated_size()),
                "estimate": current_estimate(path),
            }
            del frame
        return sources[rows]

    yield source
    for generated in sources.values():
        generated["path"].unlink(missing_ok=True)


def _run_probe(mode_arguments: list[str], data: Path, work: Path) -> dict[str, Any]:
    work.mkdir()
    temp = work / "tmp"
    temp.mkdir()
    output = work / "probe.json"
    inherited_paths = [path for path in sys.path if path]
    arguments = [
        "probe",
        *mode_arguments,
        "--data",
        str(data),
        "--work",
        str(work),
        "--output",
        str(output),
    ]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={inherited_paths!r};"
        f"sys.argv={arguments!r};"
        f"runpy.run_path({str(_PROBE)!r},run_name='__main__')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap],
        capture_output=True,
        text=True,
        check=False,
        cwd=work,
        env={**os.environ, **_THREAD_ENVIRONMENT, "TMPDIR": str(temp)},
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr[-8_000:]
    return json.loads(output.read_text(encoding="utf-8"))


def _peak_rises(stages: list[dict[str, Any]], baseline_peak: int) -> list[dict[str, Any]]:
    """The progress stages that raised the high-water mark, and by how much."""
    rises = []
    previous = baseline_peak
    for entry in stages:
        peak = int(entry["peak_rss_bytes_at_end"])
        if peak - previous >= _PEAK_RISE_BYTES:
            rises.append({"stage": entry["stage"], "rise_bytes": peak - previous})
        previous = max(previous, peak)
    return rises


def _linear_fit(xs: list[float], ys: list[float]) -> dict[str, float] | None:
    """Least-squares ``y = fixed + slope * x``; ``None`` below two distinct points."""
    if len(set(xs)) < 2:
        return None
    slope, fixed = np.polyfit(np.asarray(xs), np.asarray(ys), 1)
    return {"fixed_bytes": float(fixed), "slope": float(slope)}


@pytest.mark.perf_evidence
@pytest.mark.parametrize("family", FAMILIES)
def test_training_family_peak_memory_is_measured(
    family: str,
    training_source: Callable[[int], dict[str, Any]],
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    measurements = []
    for rows in _FAMILY_ROWS_BY_SCALE[_scale()]:
        source = training_source(rows)
        probe = _run_probe(
            ["--mode", "family", "--family", family], source["path"], tmp_path / f"{rows}"
        )
        estimate = source["estimate"]
        frame_bytes = source["frame_bytes"]
        width_bytes = rows * estimate["base_bytes_per_row"]
        growth = probe["peak_rss_bytes"] - probe["baseline_rss_bytes"]
        measurements.append(
            {
                "rows": rows,
                "parquet_bytes": source["parquet_bytes"],
                "frame_bytes": frame_bytes,
                "baseline_rss_bytes": probe["baseline_rss_bytes"],
                "peak_rss_bytes": probe["peak_rss_bytes"],
                "peak_minus_baseline_over_frame": growth / frame_bytes,
                "estimate_base_bytes_per_row": estimate["base_bytes_per_row"],
                "estimate_predicted_peak_bytes": estimate["predicted_peak_bytes"],
                "peak_over_estimate": probe["peak_rss_bytes"] / estimate["predicted_peak_bytes"],
                # The multiplier the estimate would need on its own row width.
                "implied_multiplier_peak": probe["peak_rss_bytes"] / width_bytes,
                "implied_multiplier_peak_minus_baseline": growth / width_bytes,
                "wall_seconds": probe["wall_seconds"],
                "peak_stage": probe["peak_stage"],
                "peak_rises": _peak_rises(probe["stages"], probe["baseline_peak_rss_bytes"]),
                "sampled_maxima_bytes": probe["sampled_maxima_bytes"],
                "train_rows": probe["train_rows"],
                "validation_rows": probe["validation_rows"],
                "fit_evidence": probe["fit_evidence"],
                "diagnostics_errors": probe["diagnostics_errors"],
            }
        )

    _record(
        request,
        scenario="training_family_peak_memory",
        family=family,
        settings={
            "evaluation": EVALUATION,
            "params": FAMILY_PARAMS.get(
                family, {"family": "poisson", "terms": "linear and categorical"}
            ),
            "loss": "Poisson",
        },
        artifact_paths=[
            "src/haute/_ram_estimate.py:_estimate_peak_bytes",
            "src/haute/modelling/_training_job.py:TrainingJob.run",
            "src/haute/routes/_training_worker.py:_run_training_process_job",
        ],
        measurements=measurements,
        # Peak RSS against the frame size, and against the estimator's own row width:
        # the slope is the per-family multiplier, the fixed part the per-job overhead.
        peak_fit_by_frame_bytes=_linear_fit(
            [m["frame_bytes"] for m in measurements],
            [m["peak_rss_bytes"] for m in measurements],
        ),
        peak_fit_by_estimate_width_bytes=_linear_fit(
            [m["rows"] * m["estimate_base_bytes_per_row"] for m in measurements],
            [m["peak_rss_bytes"] for m in measurements],
        ),
    )


def _agreement(candidate: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    predictions = np.load(candidate["predictions_path"])
    reference_predictions = np.load(reference["predictions_path"])
    prediction_delta = np.abs(predictions - reference_predictions)
    history = np.asarray(candidate["objective_history"], dtype=np.float64)
    reference_history = np.asarray(reference["objective_history"], dtype=np.float64)
    comparable = min(history.size, reference_history.size)
    history_delta = np.abs(history[:comparable] - reference_history[:comparable])
    differing = np.flatnonzero(history_delta > 0)
    return {
        "max_abs_prediction_delta": float(prediction_delta.max()),
        "max_rel_prediction_delta": float((prediction_delta / np.abs(reference_predictions)).max()),
        "identical_predictions": bool(np.array_equal(predictions, reference_predictions)),
        "rounds": [int(history.size), int(reference_history.size)],
        "max_abs_objective_delta": float(history_delta.max()),
        "max_rel_objective_delta": float(
            (history_delta / np.abs(reference_history[:comparable])).max()
        ),
        "first_differing_round": int(differing[0]) + 1 if differing.size else None,
        "final_objective": [float(history[-1]), float(reference_history[-1])],
    }


@pytest.mark.perf_evidence
def test_xgboost_external_memory_is_measured_against_the_in_memory_fit(
    training_source: Callable[[int], dict[str, Any]],
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    measurements = []
    for rows in _XGBOOST_ROWS_BY_SCALE[_scale()]:
        source = training_source(rows)
        runs = {
            variant: _run_probe(
                ["--mode", "xgboost", "--variant", variant],
                source["path"],
                tmp_path / f"{variant}-{rows}",
            )
            for variant in XGBOOST_VARIANTS
        }
        for variant, run in runs.items():
            measurements.append(
                {
                    "rows": rows,
                    "variant": variant,
                    "parquet_bytes": source["parquet_bytes"],
                    "frame_bytes": source["frame_bytes"],
                    "baseline_rss_bytes": run["baseline_rss_bytes"],
                    "peak_rss_bytes": run["peak_rss_bytes"],
                    "peak_minus_baseline_over_frame": (
                        run["peak_rss_bytes"] - run["baseline_rss_bytes"]
                    )
                    / source["frame_bytes"],
                    "peak_stage": run["peak_stage"],
                    "sampled_maxima_bytes": run["sampled_maxima_bytes"],
                    "matrix_seconds": run["matrix_seconds"],
                    "wall_seconds": run["wall_seconds"],
                    "cache_file_bytes": run["cache_file_bytes"],
                    "against_in_memory": _agreement(run, runs["in_memory"]),
                    "against_iterator_in_memory": _agreement(run, runs["iterator_in_memory"]),
                }
            )

    _record(
        request,
        scenario="xgboost_external_memory",
        api={
            "in_memory": "xgboost.QuantileDMatrix(pandas frame of the whole parquet)",
            "iterator_in_memory": (
                "xgboost.QuantileDMatrix(DataIter(cache_prefix=None, release_data=True))"
            ),
            "external_disk": (
                "xgboost.ExtMemQuantileDMatrix(DataIter(cache_prefix=<work>/xgboost-cache/cache, "
                "release_data=True, on_host=False))"
            ),
            "external_host": (
                "xgboost.ExtMemQuantileDMatrix(DataIter(cache_prefix=<work>/xgboost-cache/cache, "
                "release_data=True, on_host=True))"
            ),
            "batches": f"one parquet row group ({ROW_GROUP_ROWS} rows) per DataIter batch",
            "matrix_arguments": {
                "max_bin": XGBOOST_PARAMS["max_bin"],
                "nthread": TRAINING_THREADS,
                "enable_categorical": True,
            },
            "train": {**XGBOOST_PARAMS, "num_boost_round": XGBOOST_ROUNDS},
            "prediction_rows": PREDICTION_ROWS,
        },
        artifact_paths=["src/haute/modelling/_xgboost.py:XGBoostAlgorithm.fit"],
        measurements=measurements,
    )


def test_glm_out_of_core_fits_within_a_budget_the_in_memory_fit_exceeds(
    training_source: Callable[[int], dict[str, Any]],
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """MOD-M01's end-to-end acceptance on the canonical GLM job.

    The in-memory job runs unbudgeted; the out-of-core job runs the whole training
    job (planning, split, fit, evaluation and diagnostics) under a growth budget
    below the in-memory job's growth, enforced at every checkpoint, and must finish
    within it with the same model.
    """
    rows = _GLM_CHUNKED_ROWS_BY_SCALE[_scale()]
    source = training_source(rows)
    in_memory = _run_probe(
        ["--mode", "family", "--family", "glm"], source["path"], tmp_path / "in_memory"
    )
    in_memory_growth = in_memory["peak_rss_bytes"] - in_memory["baseline_rss_bytes"]
    budget = int(in_memory_growth * _GLM_CHUNKED_BUDGET_SHARE)
    chunked = _run_probe(
        [
            "--mode",
            "family",
            "--family",
            "glm",
            "--fit-mode",
            "out_of_core",
            "--memory-limit-bytes",
            str(budget),
        ],
        source["path"],
        tmp_path / "out_of_core",
    )
    chunked_growth = chunked["peak_rss_bytes"] - chunked["baseline_rss_bytes"]

    _record(
        request,
        scenario="glm_out_of_core_memory",
        rows=rows,
        frame_bytes=source["frame_bytes"],
        budget_bytes=budget,
        artifact_paths=[
            "src/haute/modelling/_training_job.py:TrainingJob._fit_glm_out_of_core",
            "src/haute/modelling/_rustystats.py:glm_out_of_core_options",
        ],
        measurements=[
            {
                "fit_mode": mode,
                "peak_rss_bytes": run["peak_rss_bytes"],
                "growth_bytes": run["peak_rss_bytes"] - run["baseline_rss_bytes"],
                "peak_stage": run["peak_stage"],
                "peak_rises": _peak_rises(run["stages"], run["baseline_peak_rss_bytes"]),
                "wall_seconds": run["wall_seconds"],
                "fit_evidence": run["fit_evidence"],
            }
            for mode, run in (("in_memory", in_memory), ("out_of_core", chunked))
        ],
    )
    assert chunked["fit_evidence"]["mode"] == "out_of_core"
    assert chunked["train_rows"] == in_memory["train_rows"]
    assert chunked_growth <= budget < in_memory_growth
