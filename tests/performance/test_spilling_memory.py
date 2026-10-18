"""Fresh-process evidence for the decision whether capped workers turn spilling on.

Haute runs every process with Polars' out-of-core spilling off (PL2-16). Polars 2
measures only its own allocations against the spill budget, so whether a budget lowers
a boundary operator's resident peak depends on the platform's allocator (jemalloc on
Linux, mimalloc on Windows). Each operator is measured through the certification
lane's probe with spilling off, then on at budgets set from its own peak with spilling
off; the probe records the most it held in its spill folder. The test asserts only
that every run completes: the figures, recorded as evidence, decide the policy
(PL2-17, specs/execution-engine).
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import pytest

from tests.performance.test_execution_engine_certification import (
    _run_operation_memory_probe,
    _write_operation_fixtures,
)

pytestmark = [
    pytest.mark.perf,
    pytest.mark.perf_evidence,
    pytest.mark.usefixtures("_widen_sandbox_root"),
]

#: The boundary operators whose streaming nodes hold state Polars can spill.
SPILL_OPERATIONS = ("sort", "unique", "group_by", "join", "over")
#: Each budget is this share of the operator's mean incremental peak with spilling off.
BUDGET_SHARES = (0.5, 0.25)
_MIN_BUDGET_MB = 32
_SAMPLES = 3
#: A peak within this share of the off-run's mean has not risen beyond run-to-run drift.
_RISE_TOLERANCE = 0.05
#: A peak this share below the off-run's mean has fallen materially.
_MATERIAL_FALL = 0.2


def _runs(
    tmp_path: Path, fixtures: dict[str, Any], operation: str, budget_mb: int | None
) -> dict[str, Any]:
    runs = [
        _run_operation_memory_probe(tmp_path, fixtures, operation, spill_budget_mb=budget_mb)
        for _ in range(_SAMPLES)
    ]
    peaks = [run["incremental_peak_rss_bytes"] for run in runs]
    return {
        "spill_budget_mb": budget_mb,
        "incremental_peak_samples": peaks,
        "incremental_peak_mean_bytes": statistics.fmean(peaks),
        "elapsed_mean_seconds": statistics.fmean(run["elapsed_seconds"] for run in runs),
        "spill_peak_bytes": max(run["spill_peak_bytes"] for run in runs),
        "spill_peak_files": max(run["spill_peak_files"] for run in runs),
        "rows_out": runs[-1]["rows_out"],
    }


def test_spilling_is_measured_against_each_boundary_operator_s_peak(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    fixtures = _write_operation_fixtures(tmp_path / "fixtures")
    measurements: list[dict[str, Any]] = []
    environment: dict[str, Any] = {}
    for operation in SPILL_OPERATIONS:
        off = _runs(tmp_path, fixtures, operation, None)
        off_mean = off["incremental_peak_mean_bytes"]
        budgets = []
        for share in BUDGET_SHARES:
            budget_mb = max(_MIN_BUDGET_MB, round(off_mean * share / 2**20))
            on = _runs(tmp_path, fixtures, operation, budget_mb)
            assert on["rows_out"] == off["rows_out"], operation
            on_mean = on["incremental_peak_mean_bytes"]
            budgets.append(
                {
                    **on,
                    "share_of_off_peak": share,
                    "peak_ratio_to_off": on_mean / off_mean,
                    "time_ratio_to_off": on["elapsed_mean_seconds"] / off["elapsed_mean_seconds"],
                    "spilled": on["spill_peak_bytes"] > 0,
                }
            )
        measurements.append({"operation": operation, "spilling_off": off, "budgets": budgets})
        environment = environment or {"fact_rows": fixtures["fact_rows"]}

    verdicts = {
        str(share): {
            "no_peak_rises": all(
                entry["budgets"][index]["peak_ratio_to_off"] <= 1 + _RISE_TOLERANCE
                for entry in measurements
            ),
            "a_peak_falls_materially": any(
                entry["budgets"][index]["peak_ratio_to_off"] <= 1 - _MATERIAL_FALL
                for entry in measurements
            ),
        }
        for index, share in enumerate(BUDGET_SHARES)
    }
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "polars_spilling_against_boundary_peaks",
                "scale": "ci-operation-memory",
                **environment,
                "measurements": measurements,
                "verdicts": verdicts,
                "rule": {
                    "samples": _SAMPLES,
                    "rise_tolerance": _RISE_TOLERANCE,
                    "material_fall": _MATERIAL_FALL,
                    "min_budget_mb": _MIN_BUDGET_MB,
                },
            },
        )
    )
