"""Canonical config-builder coverage for unified evaluation and tuning."""

from __future__ import annotations

import pytest

from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "test": {"size": 0.2},
    "validation": {"method": "cross_validation", "fold_count": 3},
}

TUNING = {
    "schema_version": 1,
    "trial_count": 5,
    "seed": 7,
    "metric": "gini",
    "search_space": {
        "depth": [4, 6, 8],
    },
}


def test_builder_threads_only_canonical_evaluation_and_tuning() -> None:
    kwargs = build_training_job_kwargs(
        {
            "target": "y",
            "algorithm": "catboost",
            "loss_function": "RMSE",
            "params": {"iterations": 100, "metadata": {"owner": "pricing"}},
            "metrics": ["gini", "rmse"],
            "evaluation": EVALUATION,
            "tuning": TUNING,
        },
        data="data.parquet",
    )
    assert kwargs["evaluation"] == EVALUATION
    assert kwargs["tuning"] == TUNING
    assert "split" not in kwargs
    assert "cross_validation" not in kwargs


def test_builder_rejects_missing_evaluation_and_legacy_public_fields() -> None:
    with pytest.raises(TrainingConfigError, match="evaluation"):
        build_training_job_kwargs(
            {"target": "y", "loss_function": "RMSE"},
            data="data.parquet",
        )
    for legacy in (
        {"split": {"strategy": "random", "validation_size": 0.2}},
        {
            "cross_validation": {
                "schema_version": 1,
                "strategy": "random",
                "fold_count": 3,
                "seed": 42,
            }
        },
    ):
        with pytest.raises(TrainingConfigError, match="legacy"):
            build_training_job_kwargs(
                {
                    "target": "y",
                    "loss_function": "RMSE",
                    "evaluation": EVALUATION,
                    **legacy,
                },
                data="data.parquet",
            )


def test_builder_rejects_tuning_without_validation_before_job_creation() -> None:
    with pytest.raises(TrainingConfigError, match="validation"):
        build_training_job_kwargs(
            {
                "target": "y",
                "loss_function": "RMSE",
                "metrics": ["gini", "rmse"],
                "params": {"iterations": 100},
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 42,
                    "validation": {"method": "none"},
                },
                "tuning": TUNING,
            },
            data="data.parquet",
        )


def test_builder_rejects_tuning_for_glm() -> None:
    with pytest.raises(TrainingConfigError, match="CatBoost"):
        build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "all_factors": True,
                "metrics": ["gini", "poisson_deviance"],
                "evaluation": EVALUATION,
                "tuning": TUNING,
            },
            data="data.parquet",
        )
