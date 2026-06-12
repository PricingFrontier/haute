"""Unit tests for haute.modelling._train_config — the single config→kwargs builder.

This module is the single source of truth shared by live training
(``routes/_train_service``) and script export (``modelling/_export``): both
consumers must derive TrainingJob kwargs from it so they can never drift
(4b.1 / 4b.2).
"""

from __future__ import annotations

import pytest

from haute.modelling._split import DEFAULT_SPLIT_DICT
from haute.modelling._train_config import (
    GLM_CONFIG_KEYS,
    build_train_params,
    build_training_job_kwargs,
)


class TestBuildTrainParams:
    def test_catboost_params_exclude_top_level_glm_keys(self):
        """4b.1 — CatBoost constructors have no **kwargs: GLM-shaped top-level
        config (offset, terms, family, …) must never leak into its params."""
        config = {
            "target": "claim_count",
            "algorithm": "catboost",
            "params": {"iterations": 100, "depth": 4},
            "offset": "log_exposure",
            "terms": {"age": {"type": "linear"}},
            "family": "poisson",
            "link": "log",
            "regularization": "ridge",
            "alpha": 0.5,
            "l1_ratio": 0.1,
            "intercept": True,
            "interactions": [],
            "var_power": 1.5,
        }
        assert build_train_params(config) == {"iterations": 100, "depth": 4}

    def test_default_algorithm_is_catboost_no_merge(self):
        config = {"params": {"iterations": 10}, "offset": "log_exposure"}
        assert build_train_params(config) == {"iterations": 10}

    def test_glm_merges_all_top_level_keys(self):
        config = {
            "algorithm": "glm",
            "params": {"some_param": 1},
            "terms": {"age": {"type": "linear"}},
            "family": "poisson",
            "link": "log",
            "interactions": [],
            "regularization": "ridge",
            "alpha": 0.5,
            "l1_ratio": 0.1,
            "intercept": True,
            "var_power": 1.5,
            "offset": "log_exposure",
        }
        params = build_train_params(config)
        assert params["terms"] == {"age": {"type": "linear"}}
        assert params["family"] == "poisson"
        assert params["link"] == "log"
        assert params["interactions"] == []
        assert params["regularization"] == "ridge"
        assert params["alpha"] == 0.5
        assert params["l1_ratio"] == 0.1
        assert params["intercept"] is True
        assert params["var_power"] == 1.5
        assert params["offset"] == "log_exposure"
        assert params["some_param"] == 1

    def test_glm_params_dict_wins_over_top_level(self):
        config = {
            "algorithm": "glm",
            "params": {"family": "gaussian"},
            "family": "poisson",
        }
        assert build_train_params(config)["family"] == "gaussian"

    def test_glm_missing_keys_are_skipped_not_defaulted(self):
        config = {"algorithm": "glm", "params": {}, "family": "tweedie"}
        params = build_train_params(config)
        assert params == {"family": "tweedie"}

    def test_none_params_treated_as_empty(self):
        assert build_train_params({"algorithm": "catboost", "params": None}) == {}

    def test_returns_copy_not_alias(self):
        source_params = {"iterations": 5}
        config = {"algorithm": "catboost", "params": source_params}
        built = build_train_params(config)
        built["iterations"] = 999
        assert source_params["iterations"] == 5

    def test_glm_config_keys_complete(self):
        assert set(GLM_CONFIG_KEYS) == {
            "terms",
            "family",
            "link",
            "interactions",
            "regularization",
            "alpha",
            "l1_ratio",
            "intercept",
            "var_power",
            "offset",
        }


class TestBuildTrainingJobKwargs:
    def test_minimal_config_defaults(self):
        kwargs = build_training_job_kwargs({"target": "y"}, data="d.parquet")
        assert kwargs["name"] == "model"
        assert kwargs["data"] == "d.parquet"
        assert kwargs["target"] == "y"
        assert kwargs["algorithm"] == "catboost"
        assert kwargs["task"] == "regression"
        assert kwargs["params"] == {}
        assert kwargs["split"] == DEFAULT_SPLIT_DICT
        assert kwargs["metrics"] == ["gini", "rmse"]
        assert kwargs["output_dir"] == "outputs"
        for key in (
            "weight",
            "feature_columns",
            "fold_column",
            "id_columns",
            "mlflow_experiment",
            "model_name",
            "loss_function",
            "variance_power",
            "offset",
            "monotone_constraints",
            "feature_weights",
            "categorical_levels",
        ):
            assert kwargs[key] is None, key
        assert kwargs["exclude"] == []

    def test_default_name_override(self):
        kwargs = build_training_job_kwargs({"target": "y"}, data="d", default_name="node_7")
        assert kwargs["name"] == "node_7"
        named = build_training_job_kwargs({"target": "y", "name": "freq"}, data="d")
        assert named["name"] == "freq"

    def test_kwargs_construct_a_real_training_job(self):
        """The builder's keys must exactly match TrainingJob's signature."""
        from haute.modelling import TrainingJob

        config = {
            "name": "freq",
            "target": "claim_count",
            "weight": "exposure",
            "exclude": ["policy_id"],
            "feature_columns": ["age"],
            "fold_column": "fold",
            "id_columns": ["policy_id"],
            "algorithm": "catboost",
            "task": "regression",
            "params": {"iterations": 5},
            "split": {"strategy": "random", "validation_size": 0.2, "seed": 42},
            "metrics": ["rmse"],
            "mlflow_experiment": "/Shared/x",
            "model_name": "freq_prod",
            "output_dir": "outputs",
            "loss_function": "Poisson",
            "variance_power": None,
            "offset": "log_exposure",
            "monotone_constraints": {"age": 1},
            "feature_weights": {"age": 2.0},
            "categorical_levels": {"region": ["north", None]},
        }
        job = TrainingJob(**build_training_job_kwargs(config, data="d.parquet"))
        assert job.target == "claim_count"
        assert job.params == {"iterations": 5}
        assert job.offset == "log_exposure"
        assert job.feature_columns == ["age"]
        assert job.fold_column == "fold"
        assert job.id_columns == ["policy_id"]

    def test_empty_string_optionals_normalised_to_none(self):
        """Live training passes ``config.get(k) or None`` — empty strings from
        UI configs must not become real column names."""
        config = {
            "target": "y",
            "weight": "",
            "offset": "",
            "fold_column": "",
            "mlflow_experiment": "",
            "model_name": "",
            "loss_function": "",
        }
        kwargs = build_training_job_kwargs(config, data="d")
        assert kwargs["weight"] is None
        assert kwargs["offset"] is None
        assert kwargs["fold_column"] is None
        assert kwargs["mlflow_experiment"] is None
        assert kwargs["model_name"] is None
        assert kwargs["loss_function"] is None

    def test_var_power_falls_back_into_variance_power(self):
        kwargs = build_training_job_kwargs({"target": "y", "var_power": 1.8}, data="d")
        assert kwargs["variance_power"] == 1.8
        explicit = build_training_job_kwargs(
            {"target": "y", "variance_power": 1.3, "var_power": 1.8}, data="d"
        )
        assert explicit["variance_power"] == 1.3

    def test_glm_variance_power_falls_back_into_var_power_param(self):
        config = {
            "target": "y",
            "algorithm": "glm",
            "family": "tweedie",
            "variance_power": 1.7,
        }
        kwargs = build_training_job_kwargs(config, data="d")
        assert kwargs["variance_power"] == 1.7
        assert kwargs["params"]["var_power"] == 1.7

    def test_glm_explicit_params_var_power_drives_training_job_variance_power(self):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "tweedie",
                "var_power": 1.8,
                "params": {"var_power": 1.4},
            },
            data="d",
        )

        assert kwargs["params"]["var_power"] == 1.4
        assert kwargs["variance_power"] == 1.4

    def test_glm_conflicting_variance_power_alias_fails_loudly(self):
        with pytest.raises(ValueError, match="variance_power.*params.*var_power"):
            build_training_job_kwargs(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "tweedie",
                    "variance_power": 1.7,
                    "params": {"var_power": 1.4},
                },
                data="d",
            )

    def test_glm_kwargs_params_carry_merged_config(self):
        config = {
            "target": "y",
            "algorithm": "glm",
            "family": "poisson",
            "link": "log",
            "terms": {"age": {"type": "linear"}},
        }
        kwargs = build_training_job_kwargs(config, data="d")
        assert kwargs["params"]["family"] == "poisson"
        assert kwargs["params"]["link"] == "log"
        assert kwargs["params"]["terms"] == {"age": {"type": "linear"}}

    def test_missing_target_fails_loud(self):
        """No silent ``target=''`` scripts/jobs — a config without a target is
        broken and must fail at build time, not at training time."""
        with pytest.raises(ValueError, match="target"):
            build_training_job_kwargs({"algorithm": "catboost"}, data="d")
        with pytest.raises(ValueError, match="target"):
            build_training_job_kwargs({"target": ""}, data="d")
