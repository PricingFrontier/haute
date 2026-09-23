"""LightGBM family acceptance (MOD-F03): fit, persist, score, explain, tune, serve."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from haute._mlflow_io import load_local_model
from haute._model_explainability import explain_native_prediction
from haute._model_scorer import score_frame
from haute.errors import HauteValidationError
from haute.modelling._descriptors import LIGHTGBM
from haute.modelling._feature_contract import load_contract
from haute.modelling._lightgbm import LightGBMAlgorithm, LightGBMModel
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob, model_contract_filename

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 3,
    "validation": {"method": "single", "size": 0.2},
}
LEVELS = ["east", "north", "south"]
PARAMS = {"num_iterations": 40, "learning_rate": 0.3, "num_leaves": 7, "min_data_in_leaf": 5}


def frame(n: int = 600, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice([*LEVELS, None], n)
    age = rng.uniform(18, 80, n)
    exposure = rng.uniform(0.2, 1.0, n)
    weight = rng.uniform(0.5, 2.0, n)
    rate = np.exp(-2 + 0.01 * (age - 40) + np.where(region == "north", 0.4, 0.0))
    return pl.DataFrame(
        {
            "region": region.tolist(),
            "age": age,
            "exposure": exposure,
            "weight": weight,
            "claims": rng.poisson(rate * exposure).astype(float),
            "severity": rng.gamma(2.0, 500 * np.exp(0.005 * age)),
            "flag": np.where(rng.uniform(size=n) < 1 / (1 + np.exp(-(age - 50) / 8)), "yes", "no"),
        }
    )


def native_frame(data: pl.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region": pd.Categorical(data["region"].to_list(), categories=LEVELS),
            "age": data["age"].to_numpy(),
        }
    )


def train(
    tmp_path: Path, *, target: str, loss: str, **kwargs: object
) -> tuple[TrainingJob, object]:
    job = TrainingJob(
        name="lgbm",
        data=kwargs.pop("data", frame()),
        target=target,
        algorithm="lightgbm",
        loss_function=loss,
        params=kwargs.pop("params", PARAMS),
        metrics=kwargs.pop("metrics", ["rmse"]),
        output_dir=str(tmp_path),
        evaluation=kwargs.pop("evaluation", EVALUATION),
        feature_columns=kwargs.pop("feature_columns", ["region", "age"]),
        **kwargs,
    )
    return job, job.run()


def _fit_adapter(data: pl.DataFrame, **kwargs: object):
    return LightGBMAlgorithm().fit(
        data,
        kwargs.pop("features", ["region", "age"]),
        kwargs.pop("cat_features", ["region"]),
        kwargs.pop("target", "severity"),
        kwargs.pop("weight", None),
        kwargs.pop("params", {"num_iterations": 25, "learning_rate": 0.3, "num_leaves": 7}),
        "regression",
        loss=kwargs.pop("loss", "RMSE"),
        threads=1,
        seed=0,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("loss", "target", "extra", "objective"),
    [
        ("RMSE", "severity", {}, "regression"),
        ("MAE", "severity", {}, "regression_l1"),
        ("Poisson", "claims", {"offset": "exposure"}, "poisson"),
        ("Gamma", "severity", {}, "gamma"),
        ("Tweedie", "claims", {"variance_power": 1.5, "offset": "exposure"}, "tweedie"),
    ],
)
def test_each_loss_trains_weighted_and_reloads_with_its_objective(
    tmp_path: Path, loss: str, target: str, extra: dict, objective: str
) -> None:
    _job, result = train(tmp_path, target=target, loss=loss, weight="weight", **extra)
    # SHAP summary, importances, PDP, lift and AvE all ran for LightGBM.
    assert result.diagnostics_errors == []
    assert result.shap_summary and result.pdp_data
    assert Path(result.model_path).suffix == ".lgbm"
    model = LightGBMModel.load(result.model_path)
    assert model.objective() == objective
    identity = load_contract(Path(result.model_path).parent / model_contract_filename("lgbm")).model
    assert (identity.algorithm, identity.loss) == ("lightgbm", loss)


def test_scoring_matches_the_native_booster_with_the_offset_applied_once(tmp_path: Path) -> None:
    data = frame()
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    scoring = load_local_model(result.model_path)
    scored = score_frame(
        model=scoring.raw_model,
        lf=data.lazy(),
        features=scoring.feature_names,
        cat_feature_names=scoring.cat_feature_names,
        flavor="lightgbm",
        offset_column=scoring.offset_column,
    ).collect()["prediction"]
    reloaded = LightGBMModel.load(result.model_path).predict(data)
    assert np.array_equal(reloaded, scored.to_numpy())

    # Plain LightGBM loads the file (the haute record is ignored) and ignores
    # the offset at predict time, so the served score is the native raw score
    # plus log(exposure), exactly once.
    booster = lgb.Booster(model_file=result.model_path)
    raw = booster.predict(native_frame(data), raw_score=True)
    native = np.exp(raw + np.log(data["exposure"].to_numpy()))
    np.testing.assert_allclose(scored.to_numpy(), native, rtol=1e-12)

    doubled = data.with_columns(pl.col("exposure") * 2)
    np.testing.assert_allclose(LightGBMModel.load(result.model_path).predict(doubled), 2 * reloaded)


def test_reordered_categories_score_identically_and_unseen_ones_fail(tmp_path: Path) -> None:
    data = frame()
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    model = LightGBMModel.load(result.model_path)
    base = model.predict(data)
    reordered = data.with_columns(pl.col("region").cast(pl.Enum(["south", "north", "east"])))
    assert np.array_equal(model.predict(reordered), base)
    with pytest.raises(HauteValidationError, match="not trained on: 'mars'"):
        model.predict(data.with_columns(pl.lit("mars").alias("region")))


def test_a_batch_holding_some_levels_scores_exactly_as_the_full_frame(tmp_path: Path) -> None:
    data = frame(1200)
    _job, result = train(
        tmp_path,
        target="claims",
        loss="Poisson",
        data=data,
        refit_on_development=False,
        params={**PARAMS, "num_iterations": 300, "early_stopping_round": 5},
    )
    model = LightGBMModel.load(result.model_path)
    full = model.predict(data)
    north = data["region"].to_numpy() == "north"
    only_north = data.filter(pl.col("region") == "north")
    assert np.array_equal(model.predict(only_north), full[north])


def test_nulls_score_as_missing_and_a_literal_missing_label_is_its_own_level(
    tmp_path: Path,
) -> None:
    rng = np.random.default_rng(4)
    n = 800
    region = rng.choice(["a", "__missing__", None], n)
    target = np.where(region == "__missing__", 2.0, np.where(pd.isna(region), 5.0, 0.5))
    data = pl.DataFrame({"region": region.tolist(), "age": rng.random(n), "y": target})
    _job, result = train(
        tmp_path,
        target="y",
        loss="RMSE",
        data=data,
        params={"num_iterations": 60, "learning_rate": 0.5, "min_data_in_leaf": 5},
    )
    model = LightGBMModel.load(result.model_path)
    assert model.categorical_levels["region"] == ["__missing__", "a", None]
    probe = pl.DataFrame({"region": ["a", "__missing__", None], "age": [0.5, 0.5, 0.5]})
    a, literal, null = model.predict(probe)
    assert null == pytest.approx(5.0, abs=0.3)
    assert literal == pytest.approx(2.0, abs=0.3)
    assert a == pytest.approx(0.5, abs=0.3)


def test_contributions_reconstruct_the_margin_including_the_offset(tmp_path: Path) -> None:
    data = frame()
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    model = LightGBMModel.load(result.model_path)
    contributions = model.contributions(data)
    margin = model.predict_margin(data)
    np.testing.assert_allclose(
        contributions.bias + contributions.values.sum(axis=1), margin, rtol=1e-9
    )
    explanation = explain_native_prediction(
        load_local_model(result.model_path),
        {"region": "north", "age": 44.0, "exposure": 0.5},
    )
    assert explanation["status"] == "ok"
    assert explanation["type"] == "lightgbm_contributions"


def test_early_stopping_trims_to_the_selected_round_and_the_refit_reuses_it(
    tmp_path: Path,
) -> None:
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        params={**PARAMS, "num_iterations": 500, "learning_rate": 0.9, "early_stopping_round": 3},
    )
    fits = result.evaluation["selection_fits"]
    assert len(fits) == 1 and fits[0]["best_iteration"] is not None
    assert result.final_tree_count == fits[0]["best_iteration"] + 1
    assert result.fit_evidence["rounds_configured"] == result.final_tree_count
    assert LightGBMModel.load(result.model_path).booster.current_iteration() == (
        result.final_tree_count
    )


def test_the_adapter_trims_an_early_stopped_booster_to_its_selected_round() -> None:
    data = frame(800)
    result = _fit_adapter(
        data.head(600),
        params={"num_iterations": 500, "learning_rate": 0.9, "early_stopping_round": 3},
        eval_df=data.tail(200),
    )
    assert result.best_iteration is not None and result.best_iteration < 499
    assert result.rounds_fitted == result.best_iteration + 1
    assert result.model.booster.current_iteration() == result.best_iteration + 1
    assert result.stopping_reason == "validation"


def test_a_constant_feature_stops_by_native_exhaustion() -> None:
    data = frame().with_columns(pl.lit(1.0).alias("age"))
    result = _fit_adapter(data, features=["age"], cat_features=[])
    assert result.rounds_configured == 25
    assert result.rounds_fitted < 25
    assert result.stopping_reason == "native_exhaustion"


def test_an_offset_model_refuses_to_score_without_its_offset(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    model = LightGBMModel.load(result.model_path)
    with pytest.raises(HauteValidationError, match="offset column 'exposure' is missing"):
        model.predict(frame().drop("exposure"))


def test_binary_string_labels_score_original_labels_from_the_probability(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="flag",
        loss="Logloss",
        task="classification",
        positive_class="yes",
        metrics=["auc"],
    )
    scoring = load_local_model(result.model_path, task="classification")
    scored = score_frame(
        model=scoring.raw_model,
        lf=frame().lazy(),
        features=scoring.feature_names,
        cat_feature_names=scoring.cat_feature_names,
        flavor="lightgbm",
        task="classification",
    ).collect()
    expected = np.where(scored["prediction_proba"].to_numpy() > 0.5, "yes", "no")
    assert scored["prediction"].to_list() == expected.tolist()
    assert {"yes", "no"} == set(scored["prediction"].to_list())


def test_a_regression_model_refuses_to_score_as_a_classifier(tmp_path: Path) -> None:
    from haute.errors import ConfigError

    _job, result = train(tmp_path, target="severity", loss="RMSE")
    with pytest.raises(ConfigError, match="This LightGBM model was trained for regression"):
        load_local_model(result.model_path, task="classification")


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"eta": 0.1}, "alias; use 'learning_rate'"),
        ({"n_estimators": 10}, "alias; use 'num_iterations'"),
        ({"seed": 1}, "cannot set 'seed'"),
        ({"random_state": 1}, r"cannot set 'random_state' \(an alias of 'seed'\)"),
        ({"xgboost_dart_mode": True}, "is not supported"),
    ],
)
def test_config_rejects_aliases_reserved_and_unknown_params(params: dict, message: str) -> None:
    config = {
        "target": "y",
        "algorithm": "lightgbm",
        "loss_function": "RMSE",
        "params": params,
        "evaluation": EVALUATION,
    }
    with pytest.raises(TrainingConfigError, match=message):
        build_training_job_kwargs(config, data="d.parquet")


def test_config_rejects_feature_weights_and_accepts_gamma() -> None:
    config = {
        "target": "y",
        "algorithm": "lightgbm",
        "loss_function": "Gamma",
        "params": {"num_iterations": 5},
        "evaluation": EVALUATION,
    }
    assert build_training_job_kwargs(config, data="d.parquet")["algorithm"] == "lightgbm"
    with pytest.raises(TrainingConfigError, match="does not support feature weights"):
        build_training_job_kwargs({**config, "feature_weights": {"x": 2.0}}, data="d.parquet")


def test_the_alias_table_matches_the_installed_release() -> None:
    """LightGBM silently accepts conflicting aliases, so Haute rejects every
    alias of an allowed or reserved key; the snapshot must track the release."""
    from lightgbm.basic import _ConfigAliases

    keys = LIGHTGBM.allowed_params | LIGHTGBM.reserved_params
    installed = {
        alias: key
        for key, aliases in _ConfigAliases._get_all_param_aliases().items()
        if key in keys
        for alias in aliases
        if alias != key
    }
    assert dict(LIGHTGBM.param_aliases) == installed


def test_a_bounded_study_tunes_and_refits_through_the_round_key(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        tuning={
            "schema_version": 1,
            "trial_count": 5,
            "seed": 7,
            "metric": "rmse",
            "search_space": {"num_leaves": [3, 7, 15], "learning_rate": [0.1, 0.3]},
        },
    )
    final_params = result.tuning["final_params"]
    assert final_params["num_iterations"] == result.tuning["final_tree_count"]
    assert "iterations" not in final_params and "num_boost_round" not in final_params


def test_the_shared_pyfunc_serves_a_lightgbm_package_through_mlflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow

    from haute._mlflow_utils import mlflow_fluent_operation
    from haute.modelling._native_pyfunc import package_native_model

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    data = frame()
    _job, result = train(tmp_path / "train", target="claims", loss="Poisson", offset="exposure")
    model_path = Path(result.model_path)
    package = package_native_model(
        model_path, model_path.parent / model_contract_filename("lgbm"), tmp_path / "package"
    )
    with mlflow_fluent_operation():
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        with mlflow.start_run() as run:
            mlflow.pyfunc.log_model(
                name="model",
                loader_module="haute.modelling._native_pyfunc",
                data_path=str(package),
            )
        served = mlflow.pyfunc.load_model(f"runs:/{run.info.run_id}/model").predict(
            data.to_pandas()
        )
    np.testing.assert_allclose(served, LightGBMModel.load(model_path).predict(data), rtol=1e-12)


def test_saved_model_records_the_haute_metadata_once(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    text = Path(result.model_path).read_text(encoding="utf-8")
    records = [line for line in text.splitlines() if line.startswith("haute:")]
    assert len(records) == 1
    meta = json.loads(records[0].removeprefix("haute:"))
    assert meta["offset_column"] == "exposure"
    assert meta["offset_link"] == "log"
    assert meta["features"] == ["region", "age"]


def test_saving_does_not_change_the_fitted_models_predictions(tmp_path: Path) -> None:
    data = frame()
    fitted = _fit_adapter(data).model
    before = fitted.predict(data)
    path = tmp_path / "model.lgbm"
    fitted.save(path)
    assert np.array_equal(LightGBMModel.load(path).predict(data), before)


def test_sample_weights_reach_the_booster() -> None:
    data = frame()
    weighted = _fit_adapter(data, weight="weight").model.predict(data)
    unweighted = _fit_adapter(data).model.predict(data)
    assert not np.allclose(weighted, unweighted)

    oracle = lgb.train(
        {
            "objective": "regression",
            "boosting": "gbdt",
            "learning_rate": 0.3,
            "num_leaves": 7,
            "seed": 0,
            "num_threads": 1,
            "verbosity": -1,
        },
        lgb.Dataset(
            native_frame(data),
            label=data["severity"].to_numpy(),
            weight=data["weight"].to_numpy(),
            categorical_feature=["region"],
        ),
        num_boost_round=25,
    )
    np.testing.assert_allclose(weighted, oracle.predict(native_frame(data)), rtol=1e-12)


def test_empty_string_categories_fail_before_fitting(tmp_path: Path) -> None:
    data = frame().with_columns(
        pl.when(pl.col("region") == "east")
        .then(pl.lit(""))
        .otherwise(pl.col("region"))
        .alias("region")
    )
    with pytest.raises(HauteValidationError, match="empty-string values"):
        train(tmp_path, target="severity", loss="RMSE", data=data)
    assert not list(tmp_path.glob("*.lgbm"))


def test_a_model_without_the_haute_record_is_refused(tmp_path: Path) -> None:
    booster = lgb.train(
        {"objective": "regression", "verbosity": -1, "min_data_in_leaf": 2},
        lgb.Dataset(np.random.default_rng(0).random((20, 2)), label=np.arange(20.0)),
        num_boost_round=2,
    )
    path = tmp_path / "foreign.lgbm"
    booster.save_model(str(path))
    with pytest.raises(HauteValidationError, match="not a LightGBM model Haute trained"):
        LightGBMModel.load(path)


def test_a_contract_describing_another_objective_fails_the_identity_check(tmp_path: Path) -> None:
    import dataclasses

    from haute._mlflow_io import verify_contract_identity
    from haute.errors import ConfigError

    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    identity = load_contract(Path(result.model_path).parent / model_contract_filename("lgbm")).model
    scoring = load_local_model(result.model_path)
    verify_contract_identity(identity, scoring)
    with pytest.raises(ConfigError, match="describes a Gamma model.*LightGBM model"):
        verify_contract_identity(dataclasses.replace(identity, loss="Gamma"), scoring)


def test_an_explanation_that_does_not_reconstruct_the_margin_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._model_explainability import ModelExplanationError
    from haute.modelling._algorithm_base import Contributions

    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    scoring = load_local_model(result.model_path)
    row = {"region": "north", "age": 44.0, "exposure": 0.5}
    genuine = scoring.raw_model.contributions(pl.DataFrame({k: [v] for k, v in row.items()}))
    monkeypatch.setattr(
        type(scoring.raw_model),
        "contributions",
        lambda self, frame: Contributions(
            bias=genuine.bias + 0.01, values=genuine.values, terms=genuine.terms
        ),
    )
    with pytest.raises(ModelExplanationError, match="LightGBM explanation does not match"):
        explain_native_prediction(scoring, row)


def test_fitting_with_the_offset_as_init_score_matches_an_independent_native_fit() -> None:
    """The adapter trains from the offset (not only scores with it): an
    independent LightGBM fit with the same init_score on train and validation
    selects the same round and predicts the same raw scores."""
    data = frame(900)
    train_rows, valid_rows = data.head(700), data.tail(200)
    params = {"num_iterations": 200, "learning_rate": 0.3, "num_leaves": 7}
    fitted = _fit_adapter(
        train_rows,
        target="claims",
        loss="Poisson",
        offset="exposure",
        params={**params, "early_stopping_round": 5},
        eval_df=valid_rows,
    )

    def native_set(rows: pl.DataFrame, reference: object = None) -> lgb.Dataset:
        return lgb.Dataset(
            native_frame(rows),
            label=rows["claims"].to_numpy(),
            init_score=np.log(rows["exposure"].to_numpy()),
            categorical_feature=["region"],
            reference=reference,
        )

    native_train = native_set(train_rows)
    oracle = lgb.train(
        {
            "objective": "poisson",
            "boosting": "gbdt",
            "learning_rate": 0.3,
            "num_leaves": 7,
            "seed": 0,
            "num_threads": 1,
            "verbosity": -1,
        },
        native_train,
        num_boost_round=200,
        valid_sets=[native_train, native_set(valid_rows, native_train)],
        valid_names=["train", "validation"],
        callbacks=[lgb.early_stopping(5, verbose=False)],
    )
    assert fitted.best_iteration == oracle.best_iteration - 1
    expected = np.exp(
        oracle.predict(native_frame(data), raw_score=True, num_iteration=oracle.best_iteration)
        + np.log(data["exposure"].to_numpy())
    )
    np.testing.assert_allclose(fitted.model.predict(data), expected, rtol=1e-12)


@pytest.mark.parametrize("validation", ["single", "cross_validation"])
def test_disabled_early_stopping_refits_with_every_fitted_round(
    tmp_path: Path, validation: str
) -> None:
    evaluation = {
        **EVALUATION,
        "validation": (
            {"method": "single", "size": 0.2}
            if validation == "single"
            else {"method": "cross_validation", "fold_count": 2}
        ),
    }
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        evaluation=evaluation,
        params={**PARAMS, "early_stopping_round": 0},
    )
    assert all(fit["best_iteration"] == 39 for fit in result.evaluation["selection_fits"])
    assert result.final_tree_count == 40


def test_mae_refuses_monotone_constraints_before_fitting() -> None:
    config = {
        "target": "y",
        "algorithm": "lightgbm",
        "loss_function": "MAE",
        "params": {"num_iterations": 5},
        "evaluation": EVALUATION,
        "monotone_constraints": {"age": 1},
    }
    with pytest.raises(TrainingConfigError, match="monotonicity constraints with the MAE loss"):
        build_training_job_kwargs(config, data="d.parquet")
    # A constraint on an excluded feature is dormant, and other losses keep them.
    dormant = {**config, "exclude": ["age"]}
    assert build_training_job_kwargs(dormant, data="d.parquet")["monotone_constraints"] is None
    rmse = {**config, "loss_function": "RMSE"}
    assert build_training_job_kwargs(rmse, data="d.parquet")["monotone_constraints"] == {"age": 1}
    with pytest.raises(HauteValidationError, match="monotonicity constraints with the MAE loss"):
        _fit_adapter(frame(), loss="MAE", monotone_constraints={"age": 1})
    assert _fit_adapter(frame(), loss="RMSE", monotone_constraints={"age": 1}).rounds_fitted
