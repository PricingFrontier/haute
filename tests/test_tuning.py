"""Pure contracts for bounded deterministic CatBoost tuning."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from haute.modelling._evaluation import (
    EvaluationConfig,
    EvaluationFitResult,
    file_sha256,
)
from haute.modelling._tuning import (
    MAX_TRIAL_FITS,
    TUNING_SCHEMA_VERSION,
    TuningConfig,
    TuningPlanArtifact,
    TuningReportArtifact,
    TuningTrialResult,
    TuningTrialsArtifact,
    build_tuning_report,
    canonical_json_bytes,
    choose_winner,
    load_tuning_plan,
    load_tuning_report,
    load_tuning_trials,
    metric_direction,
    resolve_trial_parameters,
    save_tuning_plan,
    save_tuning_report,
    save_tuning_trials,
    suggest_parameters,
    validation_weighted_tree_count,
)


def evaluation(folds: int = 3) -> EvaluationConfig:
    return EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "test": {"size": 0.2},
            "validation": {"method": "cross_validation", "fold_count": folds},
        }
    )


def tuning_raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "schema_version": 1,
        "trial_count": 5,
        "seed": 7,
        "metric": "gini",
        "search_space": {
            "depth": [4, 6, 8, 10],
            "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],
            "grow_policy": ["SymmetricTree", "Depthwise"],
            "min_data_in_leaf": {
                "choices": [10, 25, 50, 100],
                "when": {"grow_policy": ["Depthwise"]},
            },
        },
    }
    raw.update(overrides)
    return raw


def parse(raw: dict[str, object] | None = None, *, folds: int = 3) -> TuningConfig:
    return TuningConfig.from_plain_data(
        tuning_raw() if raw is None else raw,
        algorithm="catboost",
        base_params={"iterations": 100, "grow_policy": "SymmetricTree"},
        evaluation=evaluation(folds),
        configured_metrics=["gini", "rmse"],
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "schema"),
        (tuning_raw(schema_version=True), "schema"),
        (tuning_raw(trial_count=True), "trial_count"),
        (tuning_raw(trial_count=4), "trial_count"),
        (tuning_raw(trial_count=51), "trial_count"),
        (tuning_raw(metric="not_configured"), "metric"),
        (tuning_raw(search_space={}), "search_space"),
        (
            tuning_raw(search_space={"iterations": [1, 5]}),
            "iterations",
        ),
        (
            tuning_raw(search_space={"task_type": ["CPU", "GPU"]}),
            "task_type",
        ),
        (
            tuning_raw(search_space={"used_ram_limit": ["2gb", "4gb"]}),
            "used_ram_limit",
        ),
        (
            tuning_raw(search_space={"x": [1]}),
            "2 through",
        ),
        (
            tuning_raw(search_space={"x": list(range(51))}),
            "2 through",
        ),
        (
            tuning_raw(search_space={"x": [1, 1.0]}),
            "distinct",
        ),
        (
            tuning_raw(search_space={"x": [1, float("inf")]}),
            "non-finite",
        ),
        (
            tuning_raw(search_space={"x": {"type": "int", "low": 1, "high": 3}}),
            "fields",
        ),
        (
            tuning_raw(search_space={"x": {"choices": [1, 2], "when": None}}),
            "non-empty object",
        ),
    ],
)
def test_tuning_config_is_strict_bounded_and_rejects_owned_keys(
    raw: dict[str, object],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        parse(raw)


def test_tuning_requires_catboost_validation_and_fit_bound() -> None:
    with pytest.raises(ValueError, match="CatBoost"):
        TuningConfig.from_plain_data(
            tuning_raw(),
            algorithm="glm",
            base_params={},
            evaluation=evaluation(),
            configured_metrics=["gini"],
        )
    no_validation = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "validation": {"method": "none"},
        }
    )
    with pytest.raises(ValueError, match="validation"):
        TuningConfig.from_plain_data(
            tuning_raw(),
            algorithm="catboost",
            base_params={"iterations": 100},
            evaluation=no_validation,
            configured_metrics=["gini"],
        )
    assert MAX_TRIAL_FITS == 200
    with pytest.raises(ValueError, match="200"):
        parse(tuning_raw(trial_count=50), folds=5)


def test_choices_only_config_round_trips_direct_and_conditional_entries() -> None:
    config = parse()

    assert config.to_plain_data()["search_space"] == {
        "depth": [4, 6, 8, 10],
        "grow_policy": ["SymmetricTree", "Depthwise"],
        "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],
        "min_data_in_leaf": {
            "choices": [10, 25, 50, 100],
            "when": {"grow_policy": ["Depthwise"]},
        },
    }


def test_conditions_are_known_acyclic_and_possible() -> None:
    with pytest.raises(ValueError, match="unknown"):
        parse(
            tuning_raw(
                search_space={
                    "child": {
                        "choices": [1, 2],
                        "when": {"missing": [1]},
                    }
                }
            )
        )
    with pytest.raises(ValueError, match="cycle"):
        parse(
            tuning_raw(
                search_space={
                    "a": {
                        "choices": [1, 2],
                        "when": {"b": [1]},
                    },
                    "b": {
                        "choices": [1, 2],
                        "when": {"a": [1]},
                    },
                }
            )
        )
    with pytest.raises(ValueError, match="impossible"):
        parse(
            tuning_raw(
                search_space={
                    "child": {
                        "choices": [1, 2],
                        "when": {"grow_policy": ["Depthwise"]},
                    }
                }
            )
        )
    with pytest.raises(ValueError, match="declared choices"):
        parse(
            tuning_raw(
                search_space={
                    "parent": ["a", "b"],
                    "child": {
                        "choices": [1, 2],
                        "when": {"parent": ["a", "not-a-parent-choice"]},
                    },
                }
            )
        )


def test_seeded_sequential_optuna_suggestions_are_reproducible_and_conditional() -> None:
    optuna = pytest.importorskip("optuna")
    config = parse()

    def sequence() -> list[dict[str, object]]:
        sampler = optuna.samplers.TPESampler(seed=config.seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        values: list[dict[str, object]] = []
        for index in range(4):
            trial = study.ask()
            sampled = suggest_parameters(trial, config, {"grow_policy": "SymmetricTree"})
            values.append(sampled)
            study.tell(trial, float(index))
        return values

    assert sequence() == sequence()
    assert all(
        "min_data_in_leaf" not in item or item["grow_policy"] == "Depthwise" for item in sequence()
    )


def test_sampled_parameters_override_without_dropping_untouched_nested_json() -> None:
    base = {
        "iterations": 100,
        "depth": 6,
        "metadata": {"owner": "pricing", "tags": ["a", "b"]},
    }
    resolved = resolve_trial_parameters(base, {"depth": 9, "learning_rate": 0.03})
    assert resolved == {
        "iterations": 100,
        "depth": 9,
        "learning_rate": 0.03,
        "metadata": {"owner": "pricing", "tags": ["a", "b"]},
    }
    assert json.dumps(base, sort_keys=True) == json.dumps(
        {
            "iterations": 100,
            "depth": 6,
            "metadata": {"owner": "pricing", "tags": ["a", "b"]},
        },
        sort_keys=True,
    )


@pytest.mark.parametrize(
    ("metric", "direction"),
    [
        ("gini", "maximize"),
        ("auc", "maximize"),
        ("r2", "maximize"),
        ("rmse", "minimize"),
        ("mae", "minimize"),
        ("mse", "minimize"),
        ("logloss", "minimize"),
        ("poisson_deviance", "minimize"),
        ("tweedie_deviance", "minimize"),
    ],
)
def test_metric_direction_is_server_owned(metric: str, direction: str) -> None:
    assert metric_direction(metric) == direction


def trial(index: int, objective: float) -> TuningTrialResult:
    return TuningTrialResult(
        schema_version=TUNING_SCHEMA_VERSION,
        trial_index=index,
        label="baseline" if index == 0 else "sampled",
        sampled_params={},
        resolved_params={"iterations": 100},
        fits=(),
        aggregate_metrics={"gini": objective},
        objective=objective,
        elapsed_seconds=0.1,
    )


def test_baseline_participates_and_ties_choose_lower_trial_index() -> None:
    assert choose_winner([trial(0, 0.4), trial(1, 0.4)], direction="maximize").trial_index == 0
    assert choose_winner([trial(0, 0.4), trial(1, 0.5)], direction="maximize").trial_index == 1
    assert choose_winner([trial(0, 2.0), trial(1, 1.0)], direction="minimize").trial_index == 1


def test_final_tree_count_is_validation_row_weighted_median_and_capped() -> None:
    assert (
        validation_weighted_tree_count(
            best_iterations=[0, 4, 9],
            validation_rows=[1, 8, 1],
            iteration_ceiling=100,
        )
        == 5
    )
    assert (
        validation_weighted_tree_count(
            best_iterations=[99, 199],
            validation_rows=[1, 2],
            iteration_ceiling=120,
        )
        == 120
    )


def fitted_trial(index: int, objective: float) -> TuningTrialResult:
    return TuningTrialResult(
        schema_version=1,
        trial_index=index,
        label="baseline" if index == 0 else "sampled",
        sampled_params={} if index == 0 else {"depth": 4 + index},
        resolved_params={"iterations": 100, "depth": 6 if index == 0 else 4 + index},
        fits=(
            EvaluationFitResult(1, 0, 8, 2, {"gini": objective, "rmse": 1.0}, 9),
            EvaluationFitResult(1, 1, 8, 2, {"gini": objective, "rmse": 1.0}, 11),
        ),
        aggregate_metrics={"gini": objective, "rmse": 1.0},
        objective=objective,
        elapsed_seconds=0.0,
    )


def test_tuning_artifacts_are_strict_digest_linked_and_byte_stable(tmp_path: Path) -> None:
    config = parse(tuning_raw(trial_count=5), folds=2)
    plan = TuningPlanArtifact.create(
        config=config,
        base_params={"iterations": 100, "depth": 6},
        evaluation_plan_sha256="a" * 64,
        sampler="TPESampler",
        sampler_version="4.9.0",
    )
    first_plan = tmp_path / "plan.json"
    second_plan = tmp_path / "plan-copy.json"
    save_tuning_plan(plan, first_plan)
    save_tuning_plan(plan, second_plan)
    assert first_plan.read_bytes() == second_plan.read_bytes()
    assert load_tuning_plan(first_plan) == plan

    trials = TuningTrialsArtifact(
        schema_version=1,
        plan_sha256=file_sha256(first_plan),
        evaluation_plan_sha256="a" * 64,
        trials=tuple(
            [
                fitted_trial(0, 0.4),
                fitted_trial(1, 0.5),
                fitted_trial(2, 0.45),
                fitted_trial(3, 0.42),
                fitted_trial(4, 0.41),
            ]
        ),
    )
    trials_path = tmp_path / "trials.json"
    save_tuning_trials(trials, trials_path)
    assert load_tuning_trials(trials_path, plan_sha256=file_sha256(first_plan)) == trials

    report = build_tuning_report(
        plan,
        trials,
        trials_sha256=file_sha256(trials_path),
        final_params={"iterations": 10, "depth": 5},
        final_tree_count=10,
    )
    report_path = tmp_path / "report.json"
    save_tuning_report(report, report_path)
    assert load_tuning_report(report_path) == report
    assert report.winner_trial_index == 1
    assert report.baseline_objective == pytest.approx(0.4)
    assert report.winner_objective == pytest.approx(0.5)
    assert report.improvement == pytest.approx(0.1)
    assert report.best_sampled_params == {"depth": 5}
    assert report.final_params == {"iterations": 10, "depth": 5}
    assert report.total_fit_count == 11


def test_tuning_report_rejects_a_final_projection_not_derived_from_the_winner(
    tmp_path: Path,
) -> None:
    config = parse(tuning_raw(trial_count=5), folds=2)
    plan = TuningPlanArtifact.create(
        config=config,
        base_params={"iterations": 100, "depth": 6},
        evaluation_plan_sha256="a" * 64,
        sampler="TPESampler",
        sampler_version="4.9.0",
    )
    plan_path = tmp_path / "plan.json"
    save_tuning_plan(plan, plan_path)
    trials = TuningTrialsArtifact(
        schema_version=1,
        plan_sha256=file_sha256(plan_path),
        evaluation_plan_sha256="a" * 64,
        trials=tuple(
            [
                fitted_trial(0, 0.4),
                fitted_trial(1, 0.5),
                fitted_trial(2, 0.45),
                fitted_trial(3, 0.42),
                fitted_trial(4, 0.41),
            ]
        ),
    )
    trials_path = tmp_path / "trials.json"
    save_tuning_trials(trials, trials_path)

    with pytest.raises(ValueError, match="final parameter projection"):
        build_tuning_report(
            plan,
            trials,
            trials_sha256=file_sha256(trials_path),
            final_params={"iterations": 999, "depth": 99},
            final_tree_count=999,
        )


def test_tuning_artifacts_reject_tampering_and_non_contiguous_trials() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        TuningTrialsArtifact(
            schema_version=1,
            plan_sha256="a" * 64,
            evaluation_plan_sha256="b" * 64,
            trials=tuple(fitted_trial(index, 0.4 + index / 100) for index in (0, 1, 2, 4, 5)),
        )
    with pytest.raises(ValueError):
        TuningReportArtifact.from_plain_data(
            {
                "schema_version": 1,
                "plan_sha256": "a" * 64,
                "trials_sha256": "b" * 64,
                "evaluation_plan_sha256": "c" * 64,
                "metric": "gini",
                "direction": "maximize",
                "baseline_objective": 0.4,
                "winner_trial_index": 0,
                "winner_objective": float("nan"),
                "improvement": 0.0,
                "best_sampled_params": {},
                "final_params": {"iterations": 10},
                "final_tree_count": 10,
                "trial_count": 5,
                "trial_fit_count": 10,
                "total_fit_count": 11,
            }
        )
    with pytest.raises(ValueError, match="validation fits"):
        TuningTrialsArtifact(
            schema_version=1,
            plan_sha256="a" * 64,
            evaluation_plan_sha256="b" * 64,
            trials=tuple(
                [
                    replace(
                        fitted_trial(0, 0.4),
                        aggregate_metrics={"gini": 0.9, "rmse": 1.0},
                        objective=0.9,
                    ),
                    *(fitted_trial(index, 0.4) for index in range(1, 5)),
                ]
            ),
        )
    with pytest.raises(ValueError, match="schema_version"):
        TuningTrialResult(
            schema_version=True,
            trial_index=0,
            label="baseline",
            sampled_params={},
            resolved_params={},
            fits=(),
            aggregate_metrics={"gini": 0.4},
            objective=0.4,
            elapsed_seconds=0.0,
        )
    assert canonical_json_bytes({"x": 1}) == b'{"x":1}'
