"""Comprehensive tests for haute.modelling._export.generate_training_script."""

from __future__ import annotations

import pytest

from haute.modelling._export import generate_training_script
from haute.modelling._split import DEFAULT_SPLIT_DICT

MINIMAL_CONFIG = {
    "name": "freq",
    "target": "ClaimCount",
    "algorithm": "catboost",
    "task": "regression",
    "loss_function": "Poisson",
}


class TestBasicScriptGeneration:
    def test_minimal_config_produces_valid_python(self):
        script = generate_training_script(MINIMAL_CONFIG, "data.parquet")
        compile(script, "<test>", "exec")

    def test_contains_correct_data_path(self):
        script = generate_training_script(MINIMAL_CONFIG, "output/frequency.parquet")
        assert "data='output/frequency.parquet'" in script

    def test_contains_correct_target_column(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "target='ClaimCount'" in script

    def test_contains_correct_algorithm_name(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "algorithm='catboost'" in script

    def test_contains_correct_task(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "task='regression'" in script

    def test_contains_correct_name(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "name='freq'" in script


class TestImportsAndStructure:
    def test_imports_training_job(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "from haute.modelling import TrainingJob" in script

    def test_contains_training_job_instantiation(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "job = TrainingJob(" in script

    def test_contains_job_run_call(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "job.run()" in script

    def test_contains_main_guard(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert '__name__ == "__main__"' in script


class TestParameterRepr:
    def test_string_params_are_repr_quoted(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "name='freq'" in script
        assert "data='d.parquet'" in script

    def test_dict_params_are_repr_formatted(self):
        config = {**MINIMAL_CONFIG, "params": {"iterations": 100, "depth": 4}}
        script = generate_training_script(config, "d.parquet")
        assert "params={'iterations': 100, 'depth': 4}" in script

    def test_list_params_are_repr_formatted(self):
        config = {**MINIMAL_CONFIG, "exclude": ["IDpol", "PolicyID"]}
        script = generate_training_script(config, "d.parquet")
        assert "exclude=['IDpol', 'PolicyID']" in script

    def test_int_params_are_repr_formatted(self):
        # Use offset (a simple string config) as the integer-adjacent
        # smoke-test for repr formatting — ``cv_folds`` was removed in
        # Phase 2 Package 2C-5.
        config = {**MINIMAL_CONFIG, "split": {"strategy": "random", "seed": 12345}}
        script = generate_training_script(config, "d.parquet")
        assert "'seed': 12345" in script

    def test_float_params_are_repr_formatted(self):
        config = {**MINIMAL_CONFIG, "loss_function": "Tweedie", "variance_power": 1.7}
        script = generate_training_script(config, "d.parquet")
        assert "variance_power=1.7" in script


class TestLossFunction:
    @pytest.mark.parametrize("loss", ["RMSE", "MAE", "Poisson", "Logloss", "CrossEntropy"])
    def test_loss_function_included(self, loss):
        config = {**MINIMAL_CONFIG, "loss_function": loss}
        script = generate_training_script(config, "d.parquet")
        assert f"loss_function='{loss}'" in script
        compile(script, "<test>", "exec")

    def test_tweedie_includes_variance_power(self):
        config = {**MINIMAL_CONFIG, "loss_function": "Tweedie", "variance_power": 1.5}
        script = generate_training_script(config, "d.parquet")
        assert "loss_function='Tweedie'" in script
        assert "variance_power=1.5" in script
        compile(script, "<test>", "exec")

    def test_tweedie_without_variance_power_fails_loud(self):
        """Exporting a Tweedie script with no variance power must gate, not
        emit a script that silently trains at the CatBoost 1.5 default."""
        from haute.modelling._train_config import TrainingConfigError

        config = {**MINIMAL_CONFIG, "loss_function": "Tweedie"}
        with pytest.raises(TrainingConfigError, match="variance power"):
            generate_training_script(config, "d.parquet")

    def test_non_tweedie_omits_variance_power(self):
        config = {**MINIMAL_CONFIG, "loss_function": "Poisson", "variance_power": 1.5}
        script = generate_training_script(config, "d.parquet")
        assert "loss_function='Poisson'" in script
        assert "variance_power" not in script

    @pytest.mark.parametrize("loss", [None, ""])
    def test_unset_loss_function_fails_loud(self, loss):
        """An unset loss must not export a script that silently trains under
        CatBoost's RMSE default (same rule as live training)."""
        from haute.modelling._train_config import TrainingConfigError

        config = {**MINIMAL_CONFIG, "loss_function": loss}
        with pytest.raises(TrainingConfigError, match="loss"):
            generate_training_script(config, "d.parquet")

    def test_absent_loss_function_fails_loud(self):
        from haute.modelling._train_config import TrainingConfigError

        config = dict(MINIMAL_CONFIG)
        del config["loss_function"]
        with pytest.raises(TrainingConfigError, match="loss"):
            generate_training_script(config, "d.parquet")


class TestWeightColumn:
    def test_weight_included_when_set(self):
        config = {**MINIMAL_CONFIG, "weight": "Exposure"}
        script = generate_training_script(config, "d.parquet")
        assert "weight='Exposure'" in script

    def test_weight_excluded_when_none(self):
        config = {**MINIMAL_CONFIG, "weight": None}
        script = generate_training_script(config, "d.parquet")
        assert "weight" not in script

    def test_weight_excluded_when_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "weight" not in script


class TestOffsetColumn:
    def test_offset_included_when_set(self):
        config = {**MINIMAL_CONFIG, "offset": "log_exposure"}
        script = generate_training_script(config, "d.parquet")
        assert "offset='log_exposure'" in script
        compile(script, "<test>", "exec")

    def test_offset_excluded_when_none(self):
        config = {**MINIMAL_CONFIG, "offset": None}
        script = generate_training_script(config, "d.parquet")
        assert "offset" not in script

    def test_offset_excluded_when_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "offset" not in script


class TestExcludeList:
    def test_exclude_list_properly_formatted(self):
        config = {**MINIMAL_CONFIG, "exclude": ["IDpol", "PolicyID", "Date"]}
        script = generate_training_script(config, "d.parquet")
        assert "exclude=['IDpol', 'PolicyID', 'Date']" in script

    def test_empty_exclude_list_omitted(self):
        config = {**MINIMAL_CONFIG, "exclude": []}
        script = generate_training_script(config, "d.parquet")
        assert "exclude" not in script

    def test_single_item_exclude_list(self):
        config = {**MINIMAL_CONFIG, "exclude": ["IDpol"]}
        script = generate_training_script(config, "d.parquet")
        assert "exclude=['IDpol']" in script


class TestMetricsList:
    def test_metrics_list_properly_formatted(self):
        config = {**MINIMAL_CONFIG, "metrics": ["gini", "rmse", "mae"]}
        script = generate_training_script(config, "d.parquet")
        assert "metrics=['gini', 'rmse', 'mae']" in script

    def test_default_metrics_derived_from_loss_when_absent(self):
        # MINIMAL_CONFIG trains with a Poisson loss, so the derived default
        # metrics follow the objective rather than hardcoding gini/rmse.
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "metrics=['gini', 'poisson_deviance']" in script

        rmse_config = {**MINIMAL_CONFIG, "loss_function": "RMSE"}
        script = generate_training_script(rmse_config, "d.parquet")
        assert "metrics=['gini', 'rmse']" in script


class TestSplitConfiguration:
    def test_random_split(self):
        config = {
            **MINIMAL_CONFIG,
            "split": {"strategy": "random", "validation_size": 0.3, "seed": 99},
        }
        script = generate_training_script(config, "d.parquet")
        assert "'strategy': 'random'" in script
        assert "'validation_size': 0.3" in script
        assert "'seed': 99" in script
        compile(script, "<test>", "exec")

    def test_temporal_split(self):
        config = {
            **MINIMAL_CONFIG,
            "split": {
                "strategy": "temporal",
                "date_column": "event_date",
                "cutoff_date": "2023-01-01",
            },
        }
        script = generate_training_script(config, "d.parquet")
        assert "'strategy': 'temporal'" in script
        assert "'date_column': 'event_date'" in script
        assert "'cutoff_date': '2023-01-01'" in script
        compile(script, "<test>", "exec")

    def test_group_split(self):
        config = {
            **MINIMAL_CONFIG,
            "split": {
                "strategy": "group",
                "group_column": "policy_id",
                "validation_size": 0.2,
                "seed": 42,
            },
        }
        script = generate_training_script(config, "d.parquet")
        assert "'strategy': 'group'" in script
        assert "'group_column': 'policy_id'" in script
        compile(script, "<test>", "exec")

    def test_default_split_dict_used_when_not_provided(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert repr(DEFAULT_SPLIT_DICT) in script


class TestCVFolds:
    """``cv_folds`` was removed in Phase 2 Package 2C-5 — the generated
    script must never contain it, even if an obsolete config dict still
    carries the key.
    """

    def test_cv_folds_never_rendered(self):
        # Legacy config might still have the key; the generator must
        # strip it rather than emit an unknown-kwarg call.
        config = {**MINIMAL_CONFIG, "cv_folds": 5}
        script = generate_training_script(config, "d.parquet")
        assert "cv_folds" not in script

    def test_cv_folds_excluded_when_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "cv_folds" not in script

    def test_cv_folds_excluded_when_none(self):
        config = {**MINIMAL_CONFIG, "cv_folds": None}
        script = generate_training_script(config, "d.parquet")
        assert "cv_folds" not in script


class TestMLflow:
    def test_mlflow_experiment_included(self):
        config = {**MINIMAL_CONFIG, "mlflow_experiment": "/Shared/test"}
        script = generate_training_script(config, "d.parquet")
        assert "mlflow_experiment='/Shared/test'" in script

    def test_model_name_included(self):
        config = {**MINIMAL_CONFIG, "model_name": "my_model"}
        script = generate_training_script(config, "d.parquet")
        assert "model_name='my_model'" in script

    def test_mlflow_excluded_when_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "mlflow_experiment" not in script
        assert "model_name" not in script

    def test_mlflow_excluded_when_none(self):
        config = {**MINIMAL_CONFIG, "mlflow_experiment": None, "model_name": None}
        script = generate_training_script(config, "d.parquet")
        assert "mlflow_experiment" not in script
        assert "model_name" not in script


class TestMonotoneConstraints:
    def test_monotone_constraints_included(self):
        config = {**MINIMAL_CONFIG, "monotone_constraints": {"age": 1, "risk_score": -1}}
        script = generate_training_script(config, "d.parquet")
        assert "monotone_constraints=" in script
        assert "'age': 1" in script
        assert "'risk_score': -1" in script
        compile(script, "<test>", "exec")

    def test_monotone_constraints_excluded_when_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "monotone_constraints" not in script

    def test_monotone_constraints_excluded_when_none(self):
        config = {**MINIMAL_CONFIG, "monotone_constraints": None}
        script = generate_training_script(config, "d.parquet")
        assert "monotone_constraints" not in script

    def test_monotone_constraints_excluded_when_empty(self):
        config = {**MINIMAL_CONFIG, "monotone_constraints": {}}
        script = generate_training_script(config, "d.parquet")
        assert "monotone_constraints" not in script


class TestFeatureWeights:
    def test_feature_weights_included(self):
        config = {**MINIMAL_CONFIG, "feature_weights": {"age": 2.0, "region": 0.5}}
        script = generate_training_script(config, "d.parquet")
        assert "feature_weights=" in script
        assert "'age': 2.0" in script
        compile(script, "<test>", "exec")

    def test_feature_weights_excluded_when_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "feature_weights" not in script

    def test_feature_weights_excluded_when_none(self):
        config = {**MINIMAL_CONFIG, "feature_weights": None}
        script = generate_training_script(config, "d.parquet")
        assert "feature_weights" not in script

    def test_feature_weights_excluded_when_empty(self):
        config = {**MINIMAL_CONFIG, "feature_weights": {}}
        script = generate_training_script(config, "d.parquet")
        assert "feature_weights" not in script


class TestSpecialCharacters:
    def test_name_with_single_quotes_escaped(self):
        config = {**MINIMAL_CONFIG, "name": "it's a model"}
        script = generate_training_script(config, "d.parquet")
        compile(script, "<test>", "exec")
        assert "it's a model" in script or "it\\'s a model" in script

    def test_name_with_backslashes_escaped(self):
        config = {**MINIMAL_CONFIG, "name": "path\\to\\model"}
        script = generate_training_script(config, "d.parquet")
        compile(script, "<test>", "exec")

    def test_data_path_with_special_chars(self):
        script = generate_training_script(MINIMAL_CONFIG, "out/my data (v2).parquet")
        compile(script, "<test>", "exec")
        assert "my data (v2)" in script

    def test_target_with_spaces(self):
        config = {**MINIMAL_CONFIG, "target": "Claim Count"}
        script = generate_training_script(config, "d.parquet")
        compile(script, "<test>", "exec")
        assert "target='Claim Count'" in script


class TestEmptyOptionalParamsExcluded:
    def test_all_optional_params_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        for param in [
            "weight",
            "variance_power",
            "offset",
            "monotone_constraints",
            "feature_weights",
            "mlflow_experiment",
            "model_name",
        ]:
            assert param not in script

    def test_empty_params_dict_excluded(self):
        config = {**MINIMAL_CONFIG, "params": {}}
        script = generate_training_script(config, "d.parquet")
        assert "params=" not in script

    def test_exclude_absent_means_no_exclude_line(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "exclude" not in script


class TestOutputDir:
    def test_default_output_dir(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        assert "output_dir='outputs'" in script

    def test_custom_output_dir(self):
        config = {**MINIMAL_CONFIG, "output_dir": "results/v2"}
        script = generate_training_script(config, "d.parquet")
        assert "output_dir='results/v2'" in script


class TestGLMExportParity:
    """4b.2 — GLM config lives at the config top level (not ``params``); the
    exported script must merge it exactly like live training does, or it
    silently trains a Gaussian all-features model."""

    GLM_CONFIG = {
        "name": "freq_glm",
        "target": "ClaimCount",
        "algorithm": "glm",
        "task": "regression",
        "family": "poisson",
        "link": "log",
        "terms": {"age": {"type": "linear"}},
        "regularization": "ridge",
        "alpha": 0.5,
        "l1_ratio": 0.1,
        "intercept": True,
        "interactions": [],
    }

    def _script_job(self, script: str):
        """Execute the generated script (without __main__) and return the job."""
        namespace = {"__name__": "haute_export_test"}
        exec(compile(script, "<generated>", "exec"), namespace)  # noqa: S102
        return namespace["job"]

    def test_top_level_glm_config_lands_in_script_params(self):
        script = generate_training_script(self.GLM_CONFIG, "d.parquet")
        job = self._script_job(script)
        assert job.params["family"] == "poisson"
        assert job.params["link"] == "log"
        assert job.params["terms"] == {"age": {"type": "linear"}}
        assert job.params["regularization"] == "ridge"
        assert job.params["alpha"] == 0.5
        assert job.params["l1_ratio"] == 0.1
        assert job.params["intercept"] is True

    def test_glm_script_text_contains_family_and_terms(self):
        script = generate_training_script(self.GLM_CONFIG, "d.parquet")
        assert "'family': 'poisson'" in script
        assert "'link': 'log'" in script
        assert "'terms':" in script

    def test_params_dict_values_win_over_top_level(self):
        config = {**self.GLM_CONFIG, "params": {"family": "gaussian"}}
        script = generate_training_script(config, "d.parquet")
        job = self._script_job(script)
        assert job.params["family"] == "gaussian"

    def test_glm_offset_merged_into_params_and_kwarg(self):
        config = {**self.GLM_CONFIG, "offset": "log_exposure"}
        script = generate_training_script(config, "d.parquet")
        job = self._script_job(script)
        assert job.params["offset"] == "log_exposure"
        assert job.offset == "log_exposure"

    def test_catboost_script_params_exclude_glm_keys(self):
        """CatBoost params must stay pure even when GLM-shaped keys linger in
        the node config (e.g. after the user switches algorithm)."""
        config = {
            **MINIMAL_CONFIG,
            "params": {"iterations": 4, "depth": 2},
            "offset": "log_exposure",
            "family": "poisson",
            "terms": {"age": {"type": "linear"}},
        }
        script = generate_training_script(config, "d.parquet")
        job = self._script_job(script)
        assert job.params == {"iterations": 4, "depth": 2}
        assert job.offset == "log_exposure"

    def test_glm_var_power_merged_for_tweedie_family(self):
        config = {**self.GLM_CONFIG, "family": "tweedie", "link": "log", "var_power": 1.8}
        script = generate_training_script(config, "d.parquet")
        job = self._script_job(script)
        assert job.params["family"] == "tweedie"
        assert job.params["var_power"] == 1.8
        assert job.variance_power == 1.8

    def test_glm_variance_power_alias_routes_to_param_and_job_kwarg(self):
        config = {
            **self.GLM_CONFIG,
            "family": "tweedie",
            "link": "log",
            "variance_power": 1.7,
        }
        script = generate_training_script(config, "d.parquet")
        job = self._script_job(script)
        assert job.params["family"] == "tweedie"
        assert job.params["var_power"] == 1.7
        assert job.variance_power == 1.7


class TestPreviouslyDroppedTrainingKwargs:
    """Kwargs live training passes but the exporter used to drop entirely —
    each one silently changed the trained model."""

    def test_feature_columns_rendered(self):
        config = {**MINIMAL_CONFIG, "feature_columns": ["age", "region"]}
        script = generate_training_script(config, "d.parquet")
        assert "feature_columns=['age', 'region']" in script
        compile(script, "<test>", "exec")

    def test_fold_column_rendered(self):
        config = {**MINIMAL_CONFIG, "fold_column": "fold"}
        script = generate_training_script(config, "d.parquet")
        assert "fold_column='fold'" in script

    def test_id_columns_rendered(self):
        config = {**MINIMAL_CONFIG, "id_columns": ["policy_id"]}
        script = generate_training_script(config, "d.parquet")
        assert "id_columns=['policy_id']" in script

    def test_categorical_levels_rendered(self):
        config = {**MINIMAL_CONFIG, "categorical_levels": {"region": ["north", "south"]}}
        script = generate_training_script(config, "d.parquet")
        assert "categorical_levels=" in script
        assert "'region': ['north', 'south']" in script
        compile(script, "<test>", "exec")

    def test_absent_optional_kwargs_stay_absent(self):
        script = generate_training_script(MINIMAL_CONFIG, "d.parquet")
        for param in ["feature_columns", "fold_column", "id_columns", "categorical_levels"]:
            assert param not in script


class TestByteStableExportForExistingConfigs:
    """Charter pin: configs that were already exported correctly (clean
    CatBoost, no GLM keys, no newly-supported kwargs) must produce a
    byte-identical script after the shared-builder refactor."""

    def test_clean_catboost_script_is_byte_stable(self):
        config = {
            "name": "freq",
            "target": "ClaimCount",
            "weight": "Exposure",
            "exclude": ["IDpol"],
            "algorithm": "catboost",
            "task": "regression",
            "params": {"iterations": 100, "depth": 4},
            "split": {"strategy": "random", "validation_size": 0.2, "seed": 42},
            "metrics": ["gini", "rmse"],
            "loss_function": "Poisson",
            "offset": "log_exposure",
            "output_dir": "outputs",
        }
        expected = '''"""Training script generated by Haute."""

from haute.modelling import TrainingJob


job = TrainingJob(
    name='freq',
    data='output/freq.parquet',
    target='ClaimCount',
    weight='Exposure',
    exclude=['IDpol'],
    algorithm='catboost',
    task='regression',
    params={'iterations': 100, 'depth': 4},
    split={'strategy': 'random', 'validation_size': 0.2, 'seed': 42},
    metrics=['gini', 'rmse'],
    loss_function='Poisson',
    offset='log_exposure',
    output_dir='outputs',
)


if __name__ == "__main__":
    result = job.run()
    print(f"Model saved to: {result.model_path}")
    for name, value in result.metrics.items():
        print(f"  {name}: {value:.4f}")
'''
        assert generate_training_script(config, "output/freq.parquet") == expected


class TestFullConfig:
    def test_full_config_compiles(self):
        config = {
            "name": "severity",
            "target": "ClaimAmount",
            "weight": "Exposure",
            "exclude": ["IDpol", "PolicyID"],
            "algorithm": "lightgbm",
            "task": "regression",
            "params": {"num_leaves": 31, "learning_rate": 0.05},
            "split": {"strategy": "random", "validation_size": 0.25, "seed": 123},
            "metrics": ["gini", "rmse", "mae"],
            "loss_function": "Tweedie",
            "variance_power": 1.5,
            "offset": "log_exposure",
            "monotone_constraints": {"age": 1, "risk": -1},
            "feature_weights": {"age": 2.0},
            "mlflow_experiment": "/Shared/severity",
            "model_name": "severity_prod",
            "output_dir": "artifacts",
        }
        script = generate_training_script(config, "output/severity.parquet")
        compile(script, "<test>", "exec")
        assert "name='severity'" in script
        assert "data='output/severity.parquet'" in script
        assert "target='ClaimAmount'" in script
        assert "weight='Exposure'" in script
        assert "exclude=['IDpol', 'PolicyID']" in script
        assert "algorithm='lightgbm'" in script
        assert "task='regression'" in script
        assert "'num_leaves': 31" in script
        assert "loss_function='Tweedie'" in script
        assert "variance_power=1.5" in script
        assert "offset='log_exposure'" in script
        assert "mlflow_experiment='/Shared/severity'" in script
        assert "model_name='severity_prod'" in script
        assert "output_dir='artifacts'" in script
