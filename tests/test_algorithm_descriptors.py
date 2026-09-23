"""Algorithm descriptors: the single source for family capabilities (MOD-F01)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from haute.errors import HauteValidationError
from haute.modelling._algorithms import ALGORITHM_REGISTRY, CatBoostAlgorithm
from haute.modelling._descriptors import (
    CATBOOST,
    DESCRIPTORS,
    GLM,
    HAUTE_LOSSES,
    TRAINING_THREADS_ENV,
    AlgorithmDescriptor,
    NativeLoss,
    algorithm_descriptor,
    capability_fixture,
    project_refit_params,
    refit_descriptor,
    round_ceiling,
    training_threads,
)
from haute.modelling._evaluation import EvaluationConfig
from haute.modelling._model_export import MODEL_FILE_SUFFIXES
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob
from haute.modelling._tuning import TuningConfig

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}


def catboost_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "target": "y",
        "algorithm": "catboost",
        "loss_function": "RMSE",
        "params": {"iterations": 10, "depth": 3},
        "evaluation": EVALUATION,
    }
    config.update(overrides)
    return config


def test_every_registered_algorithm_has_a_descriptor_and_suffix() -> None:
    assert set(DESCRIPTORS) == set(ALGORITHM_REGISTRY)
    assert dict(MODEL_FILE_SUFFIXES) == {key: d.suffix for key, d in DESCRIPTORS.items()}
    assert MODEL_FILE_SUFFIXES["catboost"] == ".cbm"
    assert MODEL_FILE_SUFFIXES["glm"] == ".rsglm"


def test_descriptor_without_a_suffix_fails_at_registration() -> None:
    with pytest.raises(ValueError, match="artifact suffix"):
        AlgorithmDescriptor(
            key="nosuffix",
            label="No suffix",
            tasks=frozenset({"regression"}),
            losses={},
            allowed_params=frozenset(),
            reserved_params=frozenset(),
            tuning_reserved_params=frozenset(),
            param_aliases={},
            round_key=None,
            round_key_aliases=(),
            validation_only_params=(),
            refit_policy="none",
            feature_controls=frozenset(),
            suffix="",
            engine_module="none",
        )


def test_catboost_loss_translation_and_gamma_rejection() -> None:
    assert CATBOOST.native_loss("regression", "Poisson") == NativeLoss("Poisson", "log")
    assert CATBOOST.native_loss("classification", "Logloss").link == "logit"
    assert "Gamma" in HAUTE_LOSSES["regression"]
    with pytest.raises(HauteValidationError, match="CatBoost does not support the Gamma loss"):
        CATBOOST.native_loss("regression", "Gamma")
    with pytest.raises(HauteValidationError, match="not valid for task"):
        CATBOOST.native_loss("classification", "RMSE")


def test_config_builder_rejects_gamma_and_unknown_algorithms_for_every_consumer() -> None:
    with pytest.raises(TrainingConfigError, match="does not support the Gamma loss"):
        build_training_job_kwargs(catboost_config(loss_function="Gamma"), data="d.parquet")
    with pytest.raises(TrainingConfigError, match="Unknown algorithm: lightgbm"):
        build_training_job_kwargs(catboost_config(algorithm="lightgbm"), data="d.parquet")


def test_catboost_params_stay_open_but_thread_count_is_owned_by_the_allotment() -> None:
    kwargs = build_training_job_kwargs(
        catboost_config(params={"iterations": 10, "l2_leaf_reg": 3, "border_count": 64}),
        data="d.parquet",
    )
    assert kwargs["params"] == {"iterations": 10, "l2_leaf_reg": 3, "border_count": 64}
    with pytest.raises(TrainingConfigError, match="cannot set 'thread_count'"):
        build_training_job_kwargs(
            catboost_config(params={"iterations": 10, "thread_count": 2}), data="d.parquet"
        )


def test_glm_parameter_contract_is_unchanged() -> None:
    assert GLM.allowed_params is None
    assert GLM.reserved_params == frozenset()
    assert not GLM.supports_tuning
    kwargs = build_training_job_kwargs(
        {
            "target": "y",
            "algorithm": "glm",
            "family": "poisson",
            "terms": {"x": {"type": "linear"}},
            "evaluation": EVALUATION,
        },
        data="d.parquet",
    )
    assert kwargs["params"]["family"] == "poisson"


def test_direct_training_job_applies_the_same_parameter_rules() -> None:
    frame = pl.DataFrame({"y": [1.0, 2.0], "x": [0.0, 1.0]})
    with pytest.raises(TrainingConfigError, match="cannot set 'thread_count'"):
        TrainingJob(name="t", data=frame, target="y", params={"thread_count": 2})
    with pytest.raises(TrainingConfigError, match="Unknown algorithm"):
        TrainingJob(name="t", data=frame, target="y", algorithm="lightgbm")


def test_allowlisted_family_rejects_reserved_alias_duplicate_and_unknown_keys() -> None:
    family = AlgorithmDescriptor(
        key="demo",
        label="Demo",
        tasks=frozenset({"regression"}),
        losses={},
        allowed_params=frozenset({"eta", "depth"}),
        reserved_params=frozenset({"seed"}),
        tuning_reserved_params=frozenset(),
        param_aliases={"learning_rate": "eta"},
        round_key=None,
        round_key_aliases=(),
        validation_only_params=(),
        refit_policy="none",
        feature_controls=frozenset(),
        suffix=".demo",
        engine_module="demo",
    )
    family.validate_params({"eta": 0.1, "depth": 3})
    with pytest.raises(TrainingConfigError, match="cannot set 'seed'"):
        family.validate_params({"seed": 1})
    with pytest.raises(TrainingConfigError, match="alias; use 'eta'"):
        family.validate_params({"learning_rate": 0.1})
    with pytest.raises(TrainingConfigError, match="is not supported"):
        family.validate_params({"colsample": 0.5})


def test_tuning_still_excludes_catboost_orchestration_keys() -> None:
    evaluation = EvaluationConfig.from_plain_data(
        {**EVALUATION, "validation": {"method": "cross_validation", "fold_count": 3}}
    )
    raw = {
        "schema_version": 1,
        "trial_count": 5,
        "seed": 7,
        "metric": "gini",
        "search_space": {"task_type": ["CPU", "GPU"]},
    }
    with pytest.raises(ValueError, match="orchestration-owned key 'task_type'"):
        TuningConfig.from_plain_data(
            raw,
            algorithm="catboost",
            base_params={"iterations": 100},
            evaluation=evaluation,
            configured_metrics=["gini"],
        )


def test_refit_projection_writes_one_round_key_and_drops_validation_only_params() -> None:
    params = {"n_estimators": 500, "early_stopping_rounds": 20, "od_wait": 5, "depth": 6}
    assert round_ceiling(CATBOOST, params, 1000) == 500
    projected = project_refit_params(CATBOOST, params, 3)
    assert projected == {"depth": 6, "iterations": 3}
    assert refit_descriptor(projected) is CATBOOST
    with pytest.raises(HauteValidationError, match="exactly one family's refit round key"):
        refit_descriptor({"depth": 6})


def test_thread_allotment_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TRAINING_THREADS_ENV, "3")
    assert training_threads() == 3
    monkeypatch.setenv(TRAINING_THREADS_ENV, "0")
    with pytest.raises(HauteValidationError, match="positive integer"):
        training_threads()
    monkeypatch.delenv(TRAINING_THREADS_ENV)
    assert training_threads() >= 1


def test_catboost_fit_receives_the_thread_allotment() -> None:
    rng = np.random.default_rng(0)
    frame = pl.DataFrame({"x": rng.random(60), "y": rng.random(60)})
    result = CatBoostAlgorithm().fit(
        frame,
        ["x"],
        [],
        "y",
        None,
        {"iterations": 3, "loss_function": "RMSE"},
        "regression",
        threads=2,
    )
    assert result.model.get_params()["thread_count"] == 2


def test_training_job_resolves_one_allotment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TRAINING_THREADS_ENV, "2")
    job = TrainingJob(name="t", data=pl.DataFrame({"y": [1.0], "x": [0.0]}), target="y")
    assert job.threads == 2


def test_capability_fixture_lists_each_family_in_vocabulary_order() -> None:
    fixture = capability_fixture()
    assert fixture["catboost"]["losses"]["regression"] == ["RMSE", "MAE", "Poisson", "Tweedie"]
    assert fixture["catboost"]["supports_tuning"] is True
    assert fixture["glm"]["losses"] == {}
    assert fixture["glm"]["tasks"] == ["regression"]
    assert algorithm_descriptor("CatBoost") is CATBOOST


def test_frontend_capability_fixture_matches_the_descriptors() -> None:
    """The UI's checked-in capability table is exactly the backend descriptors."""
    fixture = (
        Path(__file__).resolve().parents[1]
        / "frontend/src/panels/modelling/algorithmCapabilities.json"
    )
    assert json.loads(fixture.read_text(encoding="utf-8")) == capability_fixture()


def test_config_builder_rejects_a_task_the_family_does_not_support() -> None:
    with pytest.raises(TrainingConfigError, match="GLM does not support the classification task"):
        build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "task": "classification",
                "family": "binomial",
                "terms": {"x": {"type": "linear"}},
                "evaluation": EVALUATION,
            },
            data="d.parquet",
        )
