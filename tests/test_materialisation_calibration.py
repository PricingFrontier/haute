from __future__ import annotations

import os
import runpy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from haute._estimate_calibration import (
    CALIBRATION_BASE_BASIS_POINTS,
    CALIBRATION_MAX_BASIS_POINTS,
    _reset_materialisation_calibration_for_tests,
    calibrate_materialisation_bytes,
    materialisation_calibration_snapshot,
    observe_materialisation_estimate,
)
from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._ram_estimate import MaterialisationEstimate
from haute.errors import GroupByExecutionUnsupportedError
from haute.execution import ProjectionRequest, plan_execution_strategy
from tests.conftest import make_edge, make_file_input_config, make_graph


@pytest.fixture(autouse=True)
def _reset_calibration() -> None:
    _reset_materialisation_calibration_for_tests()
    yield
    _reset_materialisation_calibration_for_tests()


def _group_by_graph():
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config("missing.parquet"),
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = df.group_by('segment').agg("
                                "pl.col('premium').sum().alias('premium'))"
                            )
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "agg").model_dump()],
        }
    )


def _context(*, headroom_bytes: int = 1_000) -> ExecutionContext:
    admission = ExecutionAdmission(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=headroom_bytes,
        rss_at_admission_bytes=100,
        rss_limit_bytes=100 + headroom_bytes,
        headroom_bytes=headroom_bytes,
        config_key="test",
    )
    return ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=admission,
        memory_baseline_bytes=100,
    )


def test_calibration_starts_at_identity_and_ratchets_up_with_margin() -> None:
    initial = calibrate_materialisation_bytes(ExecutionProfile.PREVIEW_EAGER, 100)
    assert initial.raw_bytes == 100
    assert initial.calibrated_bytes == 100
    assert initial.factor_basis_points == CALIBRATION_BASE_BASIS_POINTS

    updated = observe_materialisation_estimate(
        ExecutionProfile.PREVIEW_EAGER,
        estimated_bytes=100,
        observed_growth_bytes=150,
    )

    assert updated == 18_750
    assert (
        calibrate_materialisation_bytes(ExecutionProfile.PREVIEW_EAGER, 100).calibrated_bytes == 188
    )


def test_calibration_rejects_invalid_profile_and_byte_evidence() -> None:
    with pytest.raises(TypeError, match="ExecutionProfile"):
        calibrate_materialisation_bytes("preview_eager", 1)  # type: ignore[arg-type]

    for value in (-1, True, 1.5):
        with pytest.raises(ValueError, match="non-negative integer"):
            calibrate_materialisation_bytes(ExecutionProfile.PREVIEW_EAGER, value)  # type: ignore[arg-type]


def test_calibration_registers_a_child_reset_when_fork_hooks_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[object] = []

    def register_at_fork(*, after_in_child: object) -> None:
        callbacks.append(after_in_child)

    monkeypatch.setattr(os, "register_at_fork", register_at_fork, raising=False)

    module_path = Path(__file__).parents[1] / "src" / "haute" / "_estimate_calibration.py"
    namespace = runpy.run_path(str(module_path), run_name="_fork_hook_probe")

    assert callbacks == [namespace["_reset_materialisation_calibration_for_tests"]]


def test_calibration_loads_when_fork_hooks_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "register_at_fork", raising=False)

    module_path = Path(__file__).parents[1] / "src" / "haute" / "_estimate_calibration.py"
    namespace = runpy.run_path(str(module_path), run_name="_no_fork_hook_probe")

    assert callable(namespace["_reset_materialisation_calibration_for_tests"])


def test_calibration_never_ratchets_down_and_is_profile_scoped() -> None:
    observe_materialisation_estimate(
        ExecutionProfile.PREVIEW_EAGER,
        estimated_bytes=100,
        observed_growth_bytes=200,
    )
    before = materialisation_calibration_snapshot()

    observe_materialisation_estimate(
        ExecutionProfile.PREVIEW_EAGER,
        estimated_bytes=100,
        observed_growth_bytes=110,
    )

    assert materialisation_calibration_snapshot() == before
    assert (
        calibrate_materialisation_bytes(ExecutionProfile.DEPLOY_LIVE, 100).factor_basis_points
        == CALIBRATION_BASE_BASIS_POINTS
    )


@pytest.mark.parametrize(
    ("estimated", "observed"),
    [(0, 100), (100, 0)],
)
def test_calibration_ignores_non_positive_evidence(estimated: int, observed: int) -> None:
    assert (
        observe_materialisation_estimate(
            ExecutionProfile.PREVIEW_EAGER,
            estimated_bytes=estimated,
            observed_growth_bytes=observed,
        )
        == CALIBRATION_BASE_BASIS_POINTS
    )


def test_calibration_is_capped_and_thread_safe() -> None:
    observations = [120, 180, 250, 10_000, 300, 90] * 20
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda observed: observe_materialisation_estimate(
                    ExecutionProfile.PREVIEW_EAGER,
                    estimated_bytes=100,
                    observed_growth_bytes=observed,
                ),
                observations,
            )
        )

    assert materialisation_calibration_snapshot()["preview_eager"] == (CALIBRATION_MAX_BASIS_POINTS)


def test_planning_compares_calibrated_estimate_with_headroom() -> None:
    observe_materialisation_estimate(
        ExecutionProfile.PREVIEW_EAGER,
        estimated_bytes=100,
        observed_growth_bytes=200,
    )

    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        plan_execution_strategy(
            ProjectionRequest(
                graph=_group_by_graph(),
                target_node_id="agg",
                profile=ExecutionProfile.PREVIEW_EAGER,
            ),
            execution_context=_context(headroom_bytes=99),
            materialisation_estimate=MaterialisationEstimate.available(40),
        )

    assert error.value.estimated_peak_bytes == 100


def test_terminal_metrics_record_observed_growth_once_and_expose_calibration_evidence() -> None:
    context = _context()
    result = plan_execution_strategy(
        ProjectionRequest(
            graph=_group_by_graph(),
            target_node_id="agg",
            profile=ExecutionProfile.PREVIEW_EAGER,
        ),
        execution_context=context,
        materialisation_estimate=MaterialisationEstimate.available(100),
    )
    context._observe_rss(250)

    first = context.metrics_payload(status="completed")
    after_first = materialisation_calibration_snapshot()
    second = context.metrics_payload(status="completed")

    assert result.diagnostic.raw_estimated_peak_bytes == 100
    assert result.diagnostic.estimated_peak_bytes == 100
    assert result.diagnostic.estimate_calibration_factor_basis_points == 10_000
    assert first["raw_estimated_bytes"] == 100
    assert first["estimated_bytes"] == 100
    assert first["estimate_calibration_factor_basis_points"] == 10_000
    assert first["observed_peak_rss_growth_bytes"] == 150
    telemetry = context._telemetry_attributes(first)
    assert telemetry["raw_estimated_bytes"] == 100
    assert telemetry["estimate_calibration_factor_basis_points"] == 10_000
    assert telemetry["estimate_admission_basis"] == "provided"
    assert telemetry["observed_peak_rss_growth_bytes"] == 150
    assert second["observed_peak_rss_growth_bytes"] == 150
    assert after_first == materialisation_calibration_snapshot()
    assert after_first["preview_eager"] == 18_750
