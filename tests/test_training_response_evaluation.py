"""Strict public response contracts for evaluation and tuning."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from haute.schemas import EvaluationPreviewPayload, TrainResponse, TrainStatusResponse


def evaluation_payload() -> dict:
    return {
        "schema_version": 1,
        "strategy": "random",
        "validation_method": "cross_validation",
        "validation_fit_count": 2,
        "fit_count": 3,
        "development_rows": 8,
        "final_test_rows": 2,
        "selection_fits": [
            {
                "schema_version": 1,
                "fit_index": 0,
                "train_rows": 4,
                "validation_rows": 4,
                "metrics": {"gini": 0.4, "rmse": 1.2},
                "best_iteration": 8,
            },
            {
                "schema_version": 1,
                "fit_index": 1,
                "train_rows": 4,
                "validation_rows": 4,
                "metrics": {"gini": 0.6, "rmse": 0.8},
                "best_iteration": 10,
            },
        ],
        "selection_metrics": {
            "gini": {
                "mean": 0.5,
                "stddev": 0.1,
                "min": 0.4,
                "max": 0.6,
                "fit_count": 2,
                "validation_rows": 8,
            },
            "rmse": {
                "mean": 1.0,
                "stddev": 0.2,
                "min": 0.8,
                "max": 1.2,
                "fit_count": 2,
                "validation_rows": 8,
            },
        },
        "plan_sha256": "a" * 64,
        "results_sha256": "b" * 64,
        "plan_path": "model.evaluation-plan.json",
        "results_path": "model.evaluation-results.json",
        "report_path": "model.evaluation-report.json",
        "summary": {
            "development_rows": 8,
            "test_rows": 2,
            "validation_fit_count": 2,
        },
    }


def completed_response() -> dict:
    return {
        "status": "completed",
        "job_id": "job-1",
        "diagnostic_metrics": {"gini": 0.55, "rmse": 0.9},
        "final_test_metrics": {"gini": 0.55, "rmse": 0.9},
        "development_rows": 8,
        "final_test_rows": 2,
        "diagnostics_set": "final_test",
        "model_path": "model.cbm",
        "evaluation": evaluation_payload(),
    }


def test_completed_response_uses_only_canonical_result_labels() -> None:
    response = TrainResponse.model_validate(completed_response())
    dumped = response.model_dump(mode="json", exclude_none=True)
    assert dumped["development_rows"] == 8
    assert dumped["final_test_rows"] == 2
    assert dumped["final_test_metrics"]["gini"] == 0.55
    assert dumped["diagnostics_set"] == "final_test"
    for legacy in (
        "metrics",
        "train_rows",
        "validation_rows",
        "holdout_rows",
        "holdout_metrics",
        "cross_validation",
    ):
        assert legacy not in dumped


def test_evaluation_report_recomputes_weighted_metrics_and_rejects_drift() -> None:
    bad = completed_response()
    bad["evaluation"]["selection_metrics"]["gini"]["mean"] = 0.1
    with pytest.raises(ValidationError, match="mean"):
        TrainResponse.model_validate(bad)

    bad = completed_response()
    bad["evaluation"]["selection_fits"][0]["validation_rows"] = 3
    with pytest.raises(ValidationError, match="validation_rows"):
        TrainResponse.model_validate(bad)


def test_final_test_and_diagnostics_labels_are_consistent() -> None:
    bad = completed_response()
    bad["final_test_rows"] = 0
    with pytest.raises(ValidationError, match="final_test"):
        TrainResponse.model_validate(bad)
    bad = completed_response()
    bad["diagnostics_set"] = "development"
    with pytest.raises(ValidationError, match="diagnostics_set"):
        TrainResponse.model_validate(bad)


def tuning_payload() -> dict:
    trials = []
    for index, objective in enumerate((0.4, 0.5, 0.45, 0.42, 0.41)):
        fits = copy.deepcopy(evaluation_payload()["selection_fits"])
        for fit_index, fit in enumerate(fits):
            fit["metrics"]["gini"] = objective
            fit["metrics"]["rmse"] = 1.0
            fit["best_iteration"] = (9, 11)[fit_index]
        trials.append(
            {
                "schema_version": 1,
                "trial_index": index,
                "label": "baseline" if index == 0 else "sampled",
                "sampled_params": {} if index == 0 else {"depth": 4 + index},
                "resolved_params": {
                    "iterations": 100,
                    "depth": 4 if index == 0 else 4 + index,
                },
                "fits": fits,
                "aggregate_metrics": {"gini": objective, "rmse": 1.0},
                "objective": objective,
                "elapsed_seconds": 0.0,
            }
        )
    return {
        "schema_version": 1,
        "plan_sha256": "c" * 64,
        "trials_sha256": "d" * 64,
        "evaluation_plan_sha256": "a" * 64,
        "metric": "gini",
        "direction": "maximize",
        "baseline_objective": 0.4,
        "winner_trial_index": 1,
        "winner_objective": 0.5,
        "improvement": 0.1,
        "best_sampled_params": {"depth": 5},
        "final_params": {"iterations": 10, "depth": 5},
        "final_tree_count": 10,
        "trial_count": 5,
        "trial_fit_count": 10,
        "total_fit_count": 11,
        "trials": trials,
        "plan_path": "model.tuning-plan.json",
        "trials_path": "model.tuning-trials.json",
        "report_path": "model.tuning-report.json",
    }


def test_tuning_response_links_plan_counts_baseline_and_winner() -> None:
    raw = completed_response()
    raw["evaluation"]["fit_count"] = 11
    raw["tuning"] = tuning_payload()
    parsed = TrainResponse.model_validate(raw)
    assert parsed.tuning is not None
    assert parsed.tuning.trials[0].label == "baseline"
    assert parsed.tuning.winner_trial_index == 1

    bad = copy.deepcopy(raw)
    bad["tuning"]["winner_trial_index"] = 2
    with pytest.raises(ValidationError, match="winner"):
        TrainResponse.model_validate(bad)
    bad = copy.deepcopy(raw)
    bad["tuning"]["evaluation_plan_sha256"] = "e" * 64
    with pytest.raises(ValidationError, match="evaluation"):
        TrainResponse.model_validate(bad)
    bad = copy.deepcopy(raw)
    bad["tuning"]["trials"][0]["aggregate_metrics"]["gini"] = 0.9
    bad["tuning"]["trials"][0]["objective"] = 0.9
    bad["tuning"]["baseline_objective"] = 0.9
    with pytest.raises(ValidationError, match="validation fits"):
        TrainResponse.model_validate(bad)
    bad = copy.deepcopy(raw)
    bad["tuning"]["best_sampled_params"] = {"depth": 99}
    with pytest.raises(ValidationError, match="sampled parameters"):
        TrainResponse.model_validate(bad)
    bad = copy.deepcopy(raw)
    bad["tuning"]["final_params"] = {"iterations": 10, "depth": 99}
    with pytest.raises(ValidationError, match="final parameter projection"):
        TrainResponse.model_validate(bad)
    bad = copy.deepcopy(raw)
    bad["tuning"].update(
        {
            "direction": "minimize",
            "winner_trial_index": 0,
            "winner_objective": 0.4,
            "improvement": 0.0,
            "best_sampled_params": {},
            "final_params": {"iterations": 10, "depth": 4},
        }
    )
    with pytest.raises(ValidationError, match="metric direction"):
        TrainResponse.model_validate(bad)


def test_tuning_progress_fields_are_all_or_none_and_monotonic_shape() -> None:
    status = TrainStatusResponse.model_validate(
        {
            "status": "running",
            "phase": "trial_fit",
            "trial_index": 2,
            "trial_count": 5,
            "fold_index": 1,
            "fold_count": 2,
            "completed_fits": 2,
            "total_fits": 11,
            "best_objective": 0.4,
        }
    )
    assert status.completed_fits == 2
    with pytest.raises(ValidationError, match="tuning progress"):
        TrainStatusResponse.model_validate(
            {
                "status": "running",
                "phase": "trial_fit",
                "trial_index": 2,
            }
        )
    for field, value in (
        ("trial_count", 4),
        ("fold_count", 11),
        ("total_fits", 12),
    ):
        invalid = {
            "status": "running",
            "phase": "trial_fit",
            "trial_index": 2,
            "trial_count": 5,
            "fold_index": 1,
            "fold_count": 2,
            "completed_fits": 2,
            "total_fits": 11,
        }
        invalid[field] = value
        with pytest.raises(ValidationError, match=field):
            TrainStatusResponse.model_validate(invalid)


def test_completed_response_supports_no_validation_and_no_final_test() -> None:
    raw = completed_response()
    raw.update(
        {
            "diagnostic_metrics": {"gini": 0.7},
            "final_test_metrics": {},
            "final_test_rows": 0,
            "diagnostics_set": "development",
        }
    )
    raw["evaluation"].update(
        {
            "validation_method": "none",
            "validation_fit_count": 0,
            "fit_count": 1,
            "final_test_rows": 0,
            "selection_fits": [],
            "selection_metrics": {},
            "summary": {
                "development_rows": 8,
                "test_rows": 0,
                "validation_fit_count": 0,
            },
        }
    )
    parsed = TrainResponse.model_validate(raw)
    assert parsed.diagnostics_set == "development"
    assert parsed.evaluation is not None
    assert parsed.evaluation.selection_fits == []


def test_evaluation_response_supports_single_validation_and_strategy_summary() -> None:
    raw = completed_response()
    raw["evaluation"].update(
        {
            "strategy": "group",
            "validation_method": "single",
            "validation_fit_count": 1,
            "fit_count": 2,
            "selection_fits": [raw["evaluation"]["selection_fits"][0]],
            "selection_metrics": {
                name: {
                    "mean": fit["metrics"][name],
                    "stddev": 0.0,
                    "min": fit["metrics"][name],
                    "max": fit["metrics"][name],
                    "fit_count": 1,
                    "validation_rows": fit["validation_rows"],
                }
                for name in ("gini", "rmse")
                for fit in [raw["evaluation"]["selection_fits"][0]]
            },
            "summary": {
                "development_rows": 8,
                "test_rows": 2,
                "validation_fit_count": 1,
                "development_group_count": 4,
                "test_group_count": 1,
            },
        }
    )
    parsed = TrainResponse.model_validate(raw)
    assert parsed.evaluation is not None
    assert parsed.evaluation.summary.development_group_count == 4


def test_evaluation_preview_enforces_method_counts_bounds_and_strategy_details() -> None:
    no_validation = EvaluationPreviewPayload.model_validate(
        {
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "none",
            "development_rows": 8,
            "final_test_rows": 0,
            "validation_fit_count": 0,
        }
    )
    assert no_validation.validation_fit_count == 0

    base = {
        "schema_version": 1,
        "strategy": "random",
        "validation_method": "single",
        "development_rows": 8,
        "final_test_rows": 2,
        "validation_fit_count": 1,
        "min_selection_train_rows": 6,
        "max_selection_train_rows": 6,
        "min_selection_validation_rows": 2,
        "max_selection_validation_rows": 2,
    }
    EvaluationPreviewPayload.model_validate(base)

    bad = {**base, "validation_fit_count": 2}
    with pytest.raises(ValidationError, match="exactly one"):
        EvaluationPreviewPayload.model_validate(bad)
    bad = {**base, "validation_method": "cross_validation", "validation_fit_count": 1}
    with pytest.raises(ValidationError, match="2 to 10"):
        EvaluationPreviewPayload.model_validate(bad)
    bad = {**base, "strategy": "group"}
    with pytest.raises(ValidationError, match="group counts"):
        EvaluationPreviewPayload.model_validate(bad)
    bad = {
        **base,
        "strategy": "group",
        "development_group_count": 5,
        "final_test_group_count": 0,
    }
    with pytest.raises(ValidationError, match="rows and group count"):
        EvaluationPreviewPayload.model_validate(bad)
    bad = {
        **base,
        "strategy": "temporal",
        "development_date_range": {"start": "2025-01-01", "end": "2025-02-01"},
    }
    with pytest.raises(ValidationError, match="date range"):
        EvaluationPreviewPayload.model_validate(bad)
