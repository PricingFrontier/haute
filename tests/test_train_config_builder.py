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
    TrainingConfigError,
    build_train_params,
    build_training_job_kwargs,
    default_metrics,
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

    def test_glm_projects_all_top_level_keys(self):
        config = {
            "algorithm": "glm",
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

    def test_glm_missing_keys_are_skipped_not_defaulted(self):
        config = {"algorithm": "glm", "family": "tweedie"}
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
            "all_factors",
            "family",
            "link",
            "interactions",
            "regularization",
            "alpha",
            "l1_ratio",
            "intercept",
            "var_power",
            "theta",
            "offset",
        }


class TestBuildTrainingJobKwargs:
    def test_minimal_config_defaults(self):
        kwargs = build_training_job_kwargs(
            {"target": "y", "loss_function": "RMSE"}, data="d.parquet"
        )
        assert kwargs["name"] == "model"
        assert kwargs["data"] == "d.parquet"
        assert kwargs["target"] == "y"
        assert kwargs["algorithm"] == "catboost"
        assert kwargs["task"] == "regression"
        assert kwargs["params"] == {}
        assert kwargs["split"] == DEFAULT_SPLIT_DICT
        assert kwargs["metrics"] == ["gini", "rmse"]
        assert kwargs["output_dir"] == "outputs"
        assert kwargs["loss_function"] == "RMSE"
        for key in (
            "weight",
            "feature_columns",
            "fold_column",
            "id_columns",
            "mlflow_experiment",
            "model_name",
            "variance_power",
            "offset",
            "monotone_constraints",
            "feature_weights",
            "categorical_levels",
        ):
            assert kwargs[key] is None, key
        assert kwargs["exclude"] == []

    def test_default_name_override(self):
        kwargs = build_training_job_kwargs(
            {"target": "y", "loss_function": "RMSE"}, data="d", default_name="node_7"
        )
        assert kwargs["name"] == "node_7"
        named = build_training_job_kwargs(
            {"target": "y", "name": "freq", "loss_function": "RMSE"}, data="d"
        )
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
            "loss_function": "RMSE",
        }
        kwargs = build_training_job_kwargs(config, data="d")
        assert kwargs["weight"] is None
        assert kwargs["offset"] is None
        assert kwargs["fold_column"] is None
        assert kwargs["mlflow_experiment"] is None
        assert kwargs["model_name"] is None

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


class TestExplicitObjectiveRequired:
    """An unset training objective must fail at build time, not silently
    train under the library default (CatBoost → RMSE, GLM → gaussian):
    a frequency dataset trained without an explicit loss would produce
    plausible numbers from the wrong model with zero signal."""

    def test_catboost_missing_loss_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="loss"):
            build_training_job_kwargs({"target": "y"}, data="d")

    def test_catboost_empty_loss_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="loss"):
            build_training_job_kwargs({"target": "y", "loss_function": ""}, data="d")

    def test_catboost_classification_missing_loss_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="loss"):
            build_training_job_kwargs({"target": "y", "task": "classification"}, data="d")

    def test_glm_missing_family_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="family"):
            build_training_job_kwargs({"target": "y", "algorithm": "glm"}, data="d")

    def test_glm_empty_family_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="family"):
            build_training_job_kwargs({"target": "y", "algorithm": "glm", "family": ""}, data="d")

    def test_glm_does_not_require_loss_function(self):
        kwargs = build_training_job_kwargs(
            {"target": "y", "algorithm": "glm", "family": "gamma", "all_factors": True},
            data="d",
        )
        assert kwargs["loss_function"] is None


class TestDefaultMetricsDerivation:
    """The default reported-metric list must follow the training objective —
    hardcoded ['gini', 'rmse'] gives a Poisson/Tweedie model squared-error
    headline metrics that look plausible while measuring the wrong thing."""

    @pytest.mark.parametrize(
        ("loss", "expected"),
        [
            ("RMSE", ["gini", "rmse"]),
            ("MAE", ["gini", "rmse"]),
            ("Poisson", ["gini", "poisson_deviance"]),
            ("Tweedie", ["gini", "tweedie_deviance"]),
        ],
    )
    def test_catboost_regression_metrics_follow_loss(self, loss, expected):
        config = {"target": "y", "loss_function": loss}
        if loss == "Tweedie":
            config["variance_power"] = 1.5
        kwargs = build_training_job_kwargs(config, data="d")
        assert kwargs["metrics"] == expected

    @pytest.mark.parametrize("loss", ["Logloss", "CrossEntropy"])
    def test_catboost_classification_metrics(self, loss):
        kwargs = build_training_job_kwargs(
            {"target": "y", "task": "classification", "loss_function": loss}, data="d"
        )
        assert kwargs["metrics"] == ["auc", "logloss"]

    @pytest.mark.parametrize(
        ("family", "expected"),
        [
            ("poisson", ["gini", "poisson_deviance"]),
            ("quasipoisson", ["gini", "poisson_deviance"]),
            ("negbinomial", ["gini", "poisson_deviance"]),
            ("tweedie", ["gini", "tweedie_deviance"]),
            ("gamma", ["gini", "rmse"]),
            ("gaussian", ["gini", "rmse"]),
            ("binomial", ["auc", "logloss"]),
        ],
    )
    def test_glm_metrics_follow_family(self, family, expected):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": family,
                "all_factors": True,
                "var_power": 1.5,
                "theta": 1.5,
            },
            data="d",
        )
        assert kwargs["metrics"] == expected

    def test_explicit_metrics_always_win(self):
        kwargs = build_training_job_kwargs(
            {"target": "y", "loss_function": "Poisson", "metrics": ["rmse"]}, data="d"
        )
        assert kwargs["metrics"] == ["rmse"]

    def test_default_metrics_helper_direct(self):
        assert default_metrics("regression") == ["gini", "rmse"]
        assert default_metrics("classification") == ["auc", "logloss"]
        assert default_metrics("regression", loss_function="Poisson") == [
            "gini",
            "poisson_deviance",
        ]
        assert default_metrics("regression", family="tweedie") == [
            "gini",
            "tweedie_deviance",
        ]

    def test_training_job_fallback_metrics_follow_loss(self):
        """TrainingJob is also constructed directly (not only via the
        builder) — its own metrics fallback must not reintroduce the
        hardcoded ['gini', 'rmse']."""
        from haute.modelling import TrainingJob

        job = TrainingJob(name="m", data="d.parquet", target="y", loss_function="Poisson")
        assert job.metrics == ["gini", "poisson_deviance"]

        glm_job = TrainingJob(
            name="m",
            data="d.parquet",
            target="y",
            algorithm="glm",
            params={"family": "tweedie"},
        )
        assert glm_job.metrics == ["gini", "tweedie_deviance"]


class TestFailoverGates:
    """Part 2 of the silent-default work: parameters whose unset state
    previously fell through to a quiet library/literal failover (Tweedie
    variance power -> 1.5, elastic-net l1_ratio -> pure ridge, empty terms
    -> auto-terms over every column) must gate at build time instead."""

    # -- Tweedie variance power ------------------------------------------

    def test_catboost_tweedie_without_variance_power_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="variance power"):
            build_training_job_kwargs({"target": "y", "loss_function": "Tweedie"}, data="d")

    def test_catboost_tweedie_with_variance_power_passes(self):
        kwargs = build_training_job_kwargs(
            {"target": "y", "loss_function": "Tweedie", "variance_power": 1.5},
            data="d",
        )
        assert kwargs["variance_power"] == 1.5

    def test_catboost_non_tweedie_does_not_require_variance_power(self):
        build_training_job_kwargs({"target": "y", "loss_function": "Poisson"}, data="d")

    def test_glm_tweedie_without_var_power_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="variance power"):
            build_training_job_kwargs(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "tweedie",
                    "all_factors": True,
                },
                data="d",
            )

    def test_glm_tweedie_with_var_power_passes(self):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "tweedie",
                "all_factors": True,
                "var_power": 1.6,
            },
            data="d",
        )
        assert kwargs["variance_power"] == 1.6

    # -- Negative Binomial dispersion (theta) ------------------------------

    def test_glm_negbinomial_without_theta_fails_loud(self):
        """RustyStats does not estimate theta — unset fits silently at 1.0."""
        with pytest.raises(TrainingConfigError, match="theta"):
            build_training_job_kwargs(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "negbinomial",
                    "all_factors": True,
                },
                data="d",
            )

    def test_glm_negbinomial_with_theta_passes(self):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "negbinomial",
                "all_factors": True,
                "theta": 2.5,
            },
            data="d",
        )
        # theta must round-trip into the fit params — the gate is pointless
        # if the threaded value never reaches GLMAlgorithm.fit.
        assert kwargs["params"]["theta"] == 2.5

    def test_glm_non_negbinomial_does_not_require_theta(self):
        build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "quasipoisson",
                "all_factors": True,
            },
            data="d",
        )

    def test_negbinomial_theta_survives_script_export(self):
        """The exported standalone script must carry theta — an export that
        drops it would train the silent theta=1.0 model the gate forbids."""
        from haute.modelling import generate_training_script

        script = generate_training_script(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "negbinomial",
                "terms": {"age": {"type": "linear"}},
                "theta": 2.5,
            },
            "data.parquet",
        )
        # params is rendered with repr(), so the key appears single-quoted.
        assert "'theta': 2.5" in script

    # -- GLM factor set ---------------------------------------------------

    def test_glm_empty_terms_without_all_factors_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="factor"):
            build_training_job_kwargs(
                {"target": "y", "algorithm": "glm", "family": "poisson"}, data="d"
            )
        with pytest.raises(TrainingConfigError, match="factor"):
            build_training_job_kwargs(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                    "terms": {},
                },
                data="d",
            )

    def test_glm_all_factors_is_an_explicit_choice(self):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "all_factors": True,
            },
            data="d",
        )
        assert kwargs["params"]["all_factors"] is True

    def test_glm_configured_terms_pass_without_all_factors(self):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "terms": {"age": {"type": "linear"}},
            },
            data="d",
        )
        assert kwargs["params"]["terms"] == {"age": {"type": "linear"}}

    # -- Elastic-net mixing weight ----------------------------------------

    def test_glm_elastic_net_without_l1_ratio_fails_loud(self):
        with pytest.raises(TrainingConfigError, match="L1 ratio"):
            build_training_job_kwargs(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                    "all_factors": True,
                    "regularization": "elastic_net",
                },
                data="d",
            )

    def test_glm_elastic_net_with_l1_ratio_passes(self):
        kwargs = build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "all_factors": True,
                "regularization": "elastic_net",
                "l1_ratio": 0.0,
            },
            data="d",
        )
        assert kwargs["params"]["l1_ratio"] == 0.0

    def test_glm_ridge_does_not_require_l1_ratio(self):
        build_training_job_kwargs(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "all_factors": True,
                "regularization": "ridge",
            },
            data="d",
        )

    # -- Shared helper is the single source for the route's fast 400 ------

    def test_training_objective_issue_returns_none_for_complete_configs(self):
        from haute.modelling._train_config import training_objective_issue

        assert training_objective_issue({"target": "y", "loss_function": "RMSE"}) is None
        assert (
            training_objective_issue(
                {
                    "algorithm": "glm",
                    "family": "gamma",
                    "terms": {"age": {"type": "linear"}},
                }
            )
            is None
        )
        assert (
            training_objective_issue(
                {
                    "algorithm": "glm",
                    "family": "negbinomial",
                    "theta": 2.5,
                    "terms": {"age": {"type": "linear"}},
                }
            )
            is None
        )
