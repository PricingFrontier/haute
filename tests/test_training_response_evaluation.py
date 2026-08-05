"""Strict public response contracts for evaluation and tuning."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from haute.schemas import (
    EvaluationFitPayload,
    EvaluationMetricSummaryPayload,
    EvaluationPreviewPayload,
    TrainResponse,
    TrainStatusResponse,
    TuningTrialPayload,
)


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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "schema_version": 1,
                "fit_index": 0,
                "train_rows": 1,
                "validation_rows": 1,
                "metrics": {},
            },
            "non-empty object",
        ),
        (
            {
                "schema_version": 1,
                "fit_index": 0,
                "train_rows": 1,
                "validation_rows": 1,
                "metrics": {"": 1},
            },
            "names must be non-empty",
        ),
        (
            {
                "schema_version": 1,
                "fit_index": 0,
                "train_rows": 1,
                "validation_rows": 1,
                "metrics": {"gini": float("nan")},
            },
            "finite number",
        ),
    ],
)
def test_evaluation_fit_rejects_noncanonical_metrics(payload: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EvaluationFitPayload.model_validate(payload)


def test_evaluation_summary_rejects_nonfinite_and_inverted_ranges() -> None:
    with pytest.raises(ValidationError, match="finite number"):
        EvaluationMetricSummaryPayload.model_validate(
            {
                "mean": float("inf"),
                "stddev": 0,
                "min": 0,
                "max": 0,
                "fit_count": 1,
                "validation_rows": 1,
            }
        )
    with pytest.raises(ValidationError, match="must not exceed"):
        EvaluationMetricSummaryPayload.model_validate(
            {"mean": 0, "stddev": 0, "min": 2, "max": 1, "fit_count": 1, "validation_rows": 1}
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["evaluation"].update(validation_method="none"),
            "validation_fit_count is inconsistent",
        ),
        (
            lambda raw: raw["evaluation"].update(
                validation_method="cross_validation", validation_fit_count=1
            ),
            "cross-validation requires",
        ),
        (
            lambda raw: raw["evaluation"].update(
                selection_fits=[raw["evaluation"]["selection_fits"][0]]
            ),
            "number of selection_fits",
        ),
        (lambda raw: raw["evaluation"]["selection_fits"][1].update(fit_index=3), "contiguous"),
        (
            lambda raw: raw["evaluation"]["summary"].update(development_rows=7),
            "summary development_rows",
        ),
        (lambda raw: raw["evaluation"]["summary"].update(test_rows=1), "summary test_rows"),
        (
            lambda raw: raw["evaluation"]["summary"].update(validation_fit_count=1),
            "summary validation_fit_count",
        ),
        (
            lambda raw: raw["evaluation"]["summary"].update(development_group_count=1),
            "random evaluation summary",
        ),
        (
            lambda raw: raw["evaluation"].update(selection_metrics={}),
            "selection_metrics are required",
        ),
        (
            lambda raw: raw["evaluation"]["selection_metrics"]["gini"].update(fit_count=1),
            "fit_count must equal",
        ),
    ],
)
def test_evaluation_report_rejects_incoherent_shape(mutate, message: str) -> None:
    raw = completed_response()
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        TrainResponse.model_validate(raw)


def test_evaluation_report_rejects_strategy_counts_and_no_validation_metrics() -> None:
    raw = completed_response()
    raw["evaluation"].update(strategy="group")
    with pytest.raises(ValidationError, match="requires its strategy counts"):
        TrainResponse.model_validate(raw)
    raw = completed_response()
    raw["evaluation"].update(
        strategy="temporal",
        summary={
            **raw["evaluation"]["summary"],
            "development_date_count": 2,
            "test_date_count": 1,
            "development_group_count": 1,
        },
    )
    with pytest.raises(ValidationError, match="incompatible strategy"):
        TrainResponse.model_validate(raw)
    raw = completed_response()
    raw["evaluation"].update(
        validation_method="none",
        validation_fit_count=0,
        fit_count=1,
        selection_fits=[],
        selection_metrics={
            "gini": {
                "mean": 0,
                "stddev": 0,
                "min": 0,
                "max": 0,
                "fit_count": 1,
                "validation_rows": 1,
            }
        },
        summary={"development_rows": 8, "test_rows": 2, "validation_fit_count": 0},
    )
    with pytest.raises(ValidationError, match="must be empty"):
        TrainResponse.model_validate(raw)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"x": float("nan")}, "finite JSON"),
        ({1: "x"}, "keys must be strings"),
        ({"x": {"bad"}}, "only JSON values"),
        ([], "must be an object"),
    ],
)
def test_tuning_trial_rejects_nonfinite_or_nonjson_parameters(params: object, message: str) -> None:
    payload = copy.deepcopy(tuning_payload()["trials"][0])
    payload["resolved_params"] = params
    with pytest.raises(ValidationError, match=message):
        TuningTrialPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda tuning: tuning.update(metric="not-a-metric"), "unsupported"),
        (lambda tuning: tuning.update(trial_count=6), "number of trials"),
        (lambda tuning: tuning["trials"][1].update(trial_index=3), "indices must be contiguous"),
        (lambda tuning: tuning["trials"][0].update(label="sampled"), "baseline"),
        (lambda tuning: tuning["trials"][1].update(sampled_params={}), "sampled parameters"),
        (
            lambda tuning: tuning["trials"][1].update(resolved_params={"iterations": 100}),
            "resolved parameters",
        ),
        (
            lambda tuning: tuning["trials"][1].update(aggregate_metrics={"gini": 0.5}),
            "metric names",
        ),
        (
            lambda tuning: tuning["trials"][1].update(fits=[tuning["trials"][1]["fits"][0]]),
            "same contiguous",
        ),
        (
            lambda tuning: tuning["trials"][1]["fits"][0].update(metrics={"gini": 0.5}),
            "fit metric names",
        ),
        (lambda tuning: tuning.update(metric="mae", direction="minimize"), "must be present"),
        (lambda tuning: tuning["trials"][1].update(objective=0.2), "objective must equal"),
        (lambda tuning: tuning.update(baseline_objective=0.2), "baseline_objective"),
        (
            lambda tuning: tuning["trials"][1]["fits"][0].update(best_iteration=None),
            "positive iteration ceiling",
        ),
        (lambda tuning: tuning.update(improvement=0.2), "improvement must equal"),
        (lambda tuning: tuning.update(trial_fit_count=9), "trial_fit_count"),
        (lambda tuning: tuning.update(total_fit_count=12), "total_fit_count"),
    ],
)
def test_tuning_report_rejects_incoherent_trials(mutate, message: str) -> None:
    raw = completed_response()
    raw["evaluation"]["fit_count"] = 11
    raw["tuning"] = tuning_payload()
    mutate(raw["tuning"])
    with pytest.raises(ValidationError, match=message):
        TrainResponse.model_validate(raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"status": "started", "evaluation": evaluation_payload()}, "only for completed"),
        ({**completed_response(), "evaluation": None}, "requires evaluation"),
        ({**completed_response(), "diagnostic_metrics": {}}, "requires diagnostic_metrics"),
        ({**completed_response(), "development_rows": 7}, "development_rows must equal"),
        (
            {**completed_response(), "diagnostic_metrics": {"gini": 0.2}},
            "must equal final_test_metrics",
        ),
    ],
)
def test_train_response_rejects_status_and_result_inconsistency(raw: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        TrainResponse.model_validate(raw)


def test_train_response_rejects_no_test_and_fit_count_inconsistency() -> None:
    raw = completed_response()
    raw.update(final_test_rows=0, final_test_metrics={})
    raw["evaluation"].update(
        final_test_rows=0,
        summary={"development_rows": 8, "test_rows": 0, "validation_fit_count": 2},
    )
    raw["diagnostics_set"] = "final_test"
    with pytest.raises(ValidationError, match="development without a test"):
        TrainResponse.model_validate(raw)
    raw["diagnostics_set"] = "development"
    raw["evaluation"]["fit_count"] = 2
    with pytest.raises(ValidationError, match=r"validation_fit_count \+ final fit"):
        TrainResponse.model_validate(raw)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"status": "running", "trial_index": 1}, "require phase"),
        (
            {
                "status": "running",
                "phase": "trial_fit",
                "trial_count": 5,
                "fold_count": 2,
                "completed_fits": 0,
                "total_fits": 11,
            },
            "requires trial and fold",
        ),
        (
            {
                "status": "running",
                "phase": "trial_complete",
                "trial_index": 1,
                "fold_index": 1,
                "trial_count": 5,
                "fold_count": 2,
                "completed_fits": 0,
                "total_fits": 11,
            },
            "requires only",
        ),
        (
            {
                "status": "running",
                "phase": "final_fit",
                "trial_index": 1,
                "trial_count": 5,
                "fold_count": 2,
                "completed_fits": 0,
                "total_fits": 11,
            },
            "must not contain",
        ),
        (
            {
                "status": "running",
                "phase": "trial_fit",
                "trial_index": 6,
                "fold_index": 1,
                "trial_count": 5,
                "fold_count": 2,
                "completed_fits": 0,
                "total_fits": 11,
            },
            "within its count",
        ),
        (
            {
                "status": "running",
                "phase": "final_fit",
                "trial_count": 5,
                "fold_count": 2,
                "completed_fits": 12,
                "total_fits": 11,
            },
            "must not exceed",
        ),
    ],
)
def test_tuning_progress_rejects_invalid_phase_shapes(payload: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        TrainStatusResponse.model_validate(payload)


def test_evaluation_report_rejects_metric_names_and_strategy_test_count_mismatch() -> None:
    raw = completed_response()
    raw["evaluation"]["selection_fits"][0]["metrics"] = {"gini": 0.4}
    with pytest.raises(ValidationError, match="metric names"):
        TrainResponse.model_validate(raw)
    raw = completed_response()
    raw["evaluation"].update(
        strategy="group",
        summary={
            **raw["evaluation"]["summary"],
            "development_group_count": 4,
            "test_group_count": 0,
        },
    )
    with pytest.raises(ValidationError, match="test count disagrees"):
        TrainResponse.model_validate(raw)


def test_train_response_covers_noncompleted_and_remaining_completed_invariants() -> None:
    assert TrainResponse.model_validate({"status": "started"}).status == "started"
    raw = completed_response()
    raw.update(final_test_rows=0, final_test_metrics={"gini": 0.55}, diagnostics_set="development")
    raw["evaluation"].update(
        final_test_rows=0,
        summary={"development_rows": 8, "test_rows": 0, "validation_fit_count": 2},
    )
    with pytest.raises(ValidationError, match="must be empty without a final test"):
        TrainResponse.model_validate(raw)
    raw = completed_response()
    raw["evaluation"]["fit_count"] = 10
    raw["tuning"] = tuning_payload()
    with pytest.raises(ValidationError, match="tuning total_fit_count"):
        TrainResponse.model_validate(raw)


def test_tuning_progress_and_preview_cover_remaining_boundaries() -> None:
    assert TrainStatusResponse.model_validate({"status": "running"}).phase is None
    assert (
        TrainStatusResponse.model_validate(
            {
                "status": "running",
                "phase": "trial_complete",
                "trial_index": 1,
                "trial_count": 5,
                "fold_count": 2,
                "completed_fits": 2,
                "total_fits": 11,
            }
        ).phase
        == "trial_complete"
    )
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
    for mutation, message in (
        ({"validation_method": "none"}, "must not contain selection bounds"),
        ({"min_selection_train_rows": None}, "requires all selection row bounds"),
        ({"min_selection_train_rows": 7}, "minimums must not exceed"),
        ({"development_group_count": 1}, "only group preview"),
        (
            {"strategy": "group", "development_group_count": 4, "final_test_group_count": 0},
            "group final-test",
        ),
        ({"development_date_range": {"start": "a", "end": "b"}}, "only temporal preview"),
    ):
        with pytest.raises(ValidationError, match=message):
            EvaluationPreviewPayload.model_validate({**base, **mutation})
    assert (
        EvaluationPreviewPayload.model_validate(
            {
                **base,
                "strategy": "group",
                "development_group_count": 4,
                "final_test_group_count": 1,
            }
        ).strategy
        == "group"
    )
    with pytest.raises(ValidationError, match="requires a development date range"):
        EvaluationPreviewPayload.model_validate({**base, "strategy": "temporal"})
    assert (
        EvaluationPreviewPayload.model_validate(
            {
                **base,
                "strategy": "temporal",
                "development_date_range": {"start": "a", "end": "b"},
                "final_test_date_range": {"start": "c", "end": "d"},
            }
        ).strategy
        == "temporal"
    )


def test_tuning_trial_accepts_nested_finite_json_parameters() -> None:
    payload = copy.deepcopy(tuning_payload()["trials"][0])
    payload["resolved_params"] = {"nested": [1.5, {"enabled": True}]}
    assert TuningTrialPayload.model_validate(payload).resolved_params == payload["resolved_params"]
