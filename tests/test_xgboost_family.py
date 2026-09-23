"""XGBoost family acceptance (MOD-F02): fit, persist, score, explain, tune, serve."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
import xgboost as xgb

from haute._mlflow_io import load_local_model
from haute._model_explainability import explain_native_prediction
from haute._model_scorer import score_frame
from haute.errors import HauteValidationError
from haute.modelling._descriptors import XGBOOST
from haute.modelling._feature_contract import load_contract
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob, model_contract_filename
from haute.modelling._xgboost import XGBoostModel

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 3,
    "validation": {"method": "single", "size": 0.2},
}
LEVELS = ["east", "north", "south"]


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


def train(
    tmp_path: Path, *, target: str, loss: str, **kwargs: object
) -> tuple[TrainingJob, object]:
    job = TrainingJob(
        name="xgb",
        data=kwargs.pop("data", frame()),
        target=target,
        algorithm="xgboost",
        loss_function=loss,
        params=kwargs.pop("params", {"num_boost_round": 40, "eta": 0.3, "max_depth": 3}),
        metrics=kwargs.pop("metrics", ["rmse"]),
        output_dir=str(tmp_path),
        evaluation=kwargs.pop("evaluation", EVALUATION),
        feature_columns=kwargs.pop("feature_columns", ["region", "age"]),
        **kwargs,
    )
    return job, job.run()


@pytest.mark.parametrize(
    ("loss", "target", "extra", "objective"),
    [
        ("RMSE", "severity", {}, "reg:squarederror"),
        ("MAE", "severity", {}, "reg:absoluteerror"),
        ("Poisson", "claims", {"offset": "exposure"}, "count:poisson"),
        ("Gamma", "severity", {}, "reg:gamma"),
        ("Tweedie", "claims", {"variance_power": 1.5, "offset": "exposure"}, "reg:tweedie"),
    ],
)
def test_each_loss_trains_weighted_and_reloads_with_its_objective(
    tmp_path: Path, loss: str, target: str, extra: dict, objective: str
) -> None:
    _job, result = train(tmp_path, target=target, loss=loss, weight="weight", **extra)
    # SHAP summary, importances, PDP, lift and AvE all ran for XGBoost.
    assert result.diagnostics_errors == []
    assert result.shap_summary and result.pdp_data
    model = XGBoostModel.load(result.model_path)
    assert model.objective() == objective
    identity = load_contract(Path(result.model_path).parent / model_contract_filename("xgb")).model
    assert (identity.algorithm, identity.loss) == ("xgboost", loss)


def test_save_reload_is_bit_identical_and_scoring_matches_the_native_booster(
    tmp_path: Path,
) -> None:
    data = frame()
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    scoring = load_local_model(result.model_path)
    scored = score_frame(
        model=scoring.raw_model,
        lf=data.lazy(),
        features=scoring.feature_names,
        cat_feature_names=scoring.cat_feature_names,
        flavor="xgboost",
        offset_column=scoring.offset_column,
    ).collect()["prediction"]
    reloaded = XGBoostModel.load(result.model_path).predict(data)
    assert np.array_equal(reloaded, scored.to_numpy())

    booster = xgb.Booster()
    booster.load_model(result.model_path)
    levels = [level for level in scoring.raw_model.categorical_levels["region"] if level]
    native = booster.predict(
        xgb.DMatrix(
            pd.DataFrame(
                {
                    "region": pd.Categorical(data["region"].to_list(), categories=levels),
                    "age": data["age"].to_numpy(),
                }
            ),
            base_margin=np.log(data["exposure"].to_numpy()),
            enable_categorical=True,
        )
    )
    np.testing.assert_allclose(scored.to_numpy(), native, rtol=1e-6)


def test_reordered_categories_score_identically_and_unseen_ones_fail(tmp_path: Path) -> None:
    data = frame()
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    model = XGBoostModel.load(result.model_path)
    base = model.predict(data)
    reordered = data.with_columns(pl.col("region").cast(pl.Enum(["south", "north", "east"])))
    assert np.array_equal(model.predict(reordered), base)
    with pytest.raises(HauteValidationError, match="not trained on: 'mars'"):
        model.predict(data.with_columns(pl.lit("mars").alias("region")))


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
        evaluation={**EVALUATION},
        params={"num_boost_round": 60, "eta": 0.5, "max_depth": 3},
    )
    model = XGBoostModel.load(result.model_path)
    assert model.categorical_levels["region"] == ["__missing__", "a", None]
    probe = pl.DataFrame({"region": ["a", "__missing__", None], "age": [0.5, 0.5, 0.5]})
    a, literal, null = model.predict(probe)
    assert null == pytest.approx(5.0, abs=0.3)
    assert literal == pytest.approx(2.0, abs=0.3)
    assert a == pytest.approx(0.5, abs=0.3)


def test_contributions_reconstruct_the_margin_including_the_offset(tmp_path: Path) -> None:
    data = frame()
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    model = XGBoostModel.load(result.model_path)
    contributions = model.contributions(data)
    margin = model.predict_margin(data)
    reconstructed = contributions.bias + contributions.values.sum(axis=1)
    assert np.max(np.abs(reconstructed - margin) / np.maximum(1, np.abs(margin))) <= 1e-5
    explanation = explain_native_prediction(
        load_local_model(result.model_path),
        {"region": "north", "age": 44.0, "exposure": 0.5},
    )
    assert explanation["status"] == "ok"


def test_early_stopping_trims_to_the_selected_round_and_the_refit_reuses_it(
    tmp_path: Path,
) -> None:
    job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        params={"num_boost_round": 500, "eta": 0.9, "max_depth": 6, "early_stopping_rounds": 3},
    )
    fits = result.evaluation["selection_fits"]
    assert len(fits) == 1 and fits[0]["best_iteration"] is not None
    assert result.final_tree_count == fits[0]["best_iteration"] + 1
    assert result.fit_evidence["rounds_configured"] == result.final_tree_count
    assert XGBoostModel.load(result.model_path).booster.num_boosted_rounds() == (
        result.final_tree_count
    )


def test_a_batch_holding_some_levels_scores_exactly_as_the_full_frame(tmp_path: Path) -> None:
    """XGBoost 3.2 re-maps categories by name, except on a booster sliced to its
    best round — exactly what an early-stopped saved fit is — where codes are
    positional. Haute's encoder must give such a model contract-order codes."""
    data = frame(1200)
    _job, result = train(
        tmp_path,
        target="claims",
        loss="Poisson",
        data=data,
        refit_on_development=False,
        params={"num_boost_round": 300, "eta": 0.3, "max_depth": 3, "early_stopping_rounds": 5},
    )
    model = XGBoostModel.load(result.model_path)
    assert model.booster.num_boosted_rounds() < 300  # the saved booster is sliced
    full = model.predict(data)
    # "north" carries the signal and is not the first level, so a batch that
    # derived its own codes would score north as another level.
    north = data["region"].to_numpy() == "north"
    only_north = data.filter(pl.col("region") == "north")
    assert np.array_equal(model.predict(only_north), full[north])


def test_the_adapter_trims_an_early_stopped_booster_to_its_selected_round() -> None:
    from haute.modelling._xgboost import XGBoostAlgorithm

    data = frame(800)
    result = XGBoostAlgorithm().fit(
        data.head(600),
        ["region", "age"],
        ["region"],
        "severity",
        None,
        {"num_boost_round": 500, "eta": 0.9, "max_depth": 6, "early_stopping_rounds": 3},
        "regression",
        eval_df=data.tail(200),
        loss="RMSE",
    )
    assert result.best_iteration is not None and result.best_iteration < 499
    assert result.rounds_fitted == result.best_iteration + 1
    assert result.model.booster.num_boosted_rounds() == result.best_iteration + 1
    assert result.stopping_reason == "validation"


def test_a_saved_validation_fit_holds_only_the_selected_rounds(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        refit_on_development=False,
        params={"num_boost_round": 500, "eta": 0.9, "max_depth": 6, "early_stopping_rounds": 3},
    )
    best = result.evaluation["selection_fits"][0]["best_iteration"]
    assert XGBoostModel.load(result.model_path).booster.num_boosted_rounds() == best + 1


def test_an_offset_model_refuses_to_score_without_its_offset(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    model = XGBoostModel.load(result.model_path)
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
        flavor="xgboost",
        task="classification",
    ).collect()
    expected = np.where(scored["prediction_proba"].to_numpy() > 0.5, "yes", "no")
    assert scored["prediction"].to_list() == expected.tolist()
    assert {"yes", "no"} == set(scored["prediction"].to_list())


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"learning_rate": 0.1}, "alias; use 'eta'"),
        ({"n_estimators": 10}, "alias; use 'num_boost_round'"),
        ({"seed": 1}, "cannot set 'seed'"),
        ({"subsample_freq": 1}, "is not supported"),
    ],
)
def test_config_rejects_aliases_reserved_and_unknown_params(params: dict, message: str) -> None:
    config = {
        "target": "y",
        "algorithm": "xgboost",
        "loss_function": "RMSE",
        "params": params,
        "evaluation": EVALUATION,
    }
    with pytest.raises(TrainingConfigError, match=message):
        build_training_job_kwargs(config, data="d.parquet")


def test_config_rejects_feature_weights_and_accepts_gamma() -> None:
    config = {
        "target": "y",
        "algorithm": "xgboost",
        "loss_function": "Gamma",
        "params": {"num_boost_round": 5},
        "evaluation": EVALUATION,
    }
    assert build_training_job_kwargs(config, data="d.parquet")["algorithm"] == "xgboost"
    with pytest.raises(TrainingConfigError, match="does not support feature weights"):
        build_training_job_kwargs({**config, "feature_weights": {"x": 2.0}}, data="d.parquet")
    assert XGBOOST.native_loss("regression", "Gamma").objective == "reg:gamma"


def test_a_bounded_study_tunes_and_refits_through_the_round_key(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        params={"num_boost_round": 60, "eta": 0.3, "max_depth": 3},
        tuning={
            "schema_version": 1,
            "trial_count": 5,
            "seed": 7,
            "metric": "rmse",
            "search_space": {"max_depth": [2, 3, 4], "eta": [0.1, 0.3]},
        },
    )
    final_params = result.tuning["final_params"]
    assert final_params["num_boost_round"] == result.tuning["final_tree_count"]
    assert "iterations" not in final_params


def test_the_shared_pyfunc_serves_an_xgboost_package_through_mlflow(
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
        model_path, model_path.parent / model_contract_filename("xgb"), tmp_path / "package"
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
    np.testing.assert_allclose(served, XGBoostModel.load(model_path).predict(data), rtol=1e-6)


def test_saved_model_records_the_haute_metadata(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    booster = xgb.Booster()
    booster.load_model(result.model_path)
    meta = json.loads(booster.attr("haute"))
    assert meta["offset_column"] == "exposure"
    assert meta["offset_link"] == "log"
    assert meta["features"] == ["region", "age"]


def test_gamma_deviance_is_weighted_and_checks_its_domain() -> None:
    from haute.modelling._metrics import compute_metrics
    from haute.modelling._train_config import default_metrics

    y = np.array([1.0, 2.0, 4.0])
    mu = np.array([1.0, 2.5, 3.0])
    weights = np.array([1.0, 2.0, 1.0])
    dev = 2.0 * (-np.log(y / mu) + (y - mu) / mu)
    result = compute_metrics(y, mu, weights, ["gamma_deviance"])
    assert result["gamma_deviance"] == pytest.approx(float(np.average(dev, weights=weights)))
    with pytest.raises(HauteValidationError, match="strictly positive targets"):
        compute_metrics(np.array([0.0, 1.0]), np.array([1.0, 1.0]), None, ["gamma_deviance"])
    assert default_metrics("regression", loss_function="Gamma") == ["gini", "gamma_deviance"]


def _fit_adapter(data: pl.DataFrame, **kwargs: object):
    from haute.modelling._xgboost import XGBoostAlgorithm

    return XGBoostAlgorithm().fit(
        data,
        ["region", "age"],
        ["region"],
        kwargs.pop("target", "severity"),
        kwargs.pop("weight", None),
        kwargs.pop("params", {"num_boost_round": 25, "eta": 0.3, "max_depth": 3}),
        "regression",
        loss=kwargs.pop("loss", "RMSE"),
        threads=1,
        seed=0,
        **kwargs,
    )


def test_saving_does_not_change_the_fitted_models_predictions(tmp_path: Path) -> None:
    data = frame()
    fitted = _fit_adapter(data).model
    before = fitted.predict(data)
    path = tmp_path / "model.ubj"
    fitted.save(path)
    assert np.array_equal(XGBoostModel.load(path).predict(data), before)


def test_sample_weights_reach_the_booster() -> None:
    data = frame()
    weighted = _fit_adapter(data, weight="weight").model.predict(data)
    unweighted = _fit_adapter(data).model.predict(data)
    assert not np.allclose(weighted, unweighted)

    levels = [level for level in LEVELS]
    oracle = xgb.train(
        {
            "objective": "reg:squarederror",
            "tree_method": "hist",
            "eta": 0.3,
            "max_depth": 3,
            "seed": 0,
            "nthread": 1,
            "verbosity": 0,
        },
        xgb.DMatrix(
            pd.DataFrame(
                {
                    "region": pd.Categorical(data["region"].to_list(), categories=levels),
                    "age": data["age"].to_numpy(),
                }
            ),
            label=data["severity"].to_numpy(),
            weight=data["weight"].to_numpy(),
            enable_categorical=True,
        ),
        num_boost_round=25,
    )
    native = oracle.predict(
        xgb.DMatrix(
            pd.DataFrame(
                {
                    "region": pd.Categorical(data["region"].to_list(), categories=levels),
                    "age": data["age"].to_numpy(),
                }
            ),
            enable_categorical=True,
        )
    )
    np.testing.assert_allclose(weighted, native, rtol=1e-6)


def test_empty_string_categories_fail_before_fitting(tmp_path: Path) -> None:
    data = frame().with_columns(
        pl.when(pl.col("region") == "east")
        .then(pl.lit(""))
        .otherwise(pl.col("region"))
        .alias("region")
    )
    with pytest.raises(HauteValidationError, match="empty-string values"):
        train(tmp_path, target="severity", loss="RMSE", data=data)
    assert not list(tmp_path.glob("*.ubj"))


def test_gamma_deviance_can_drive_an_xgboost_study() -> None:
    config = {
        "target": "y",
        "algorithm": "xgboost",
        "loss_function": "Gamma",
        "params": {"num_boost_round": 20},
        "metrics": ["gamma_deviance"],
        "evaluation": {**EVALUATION, "validation": {"method": "cross_validation", "fold_count": 2}},
        "tuning": {
            "schema_version": 1,
            "trial_count": 5,
            "seed": 1,
            "metric": "gamma_deviance",
            "search_space": {"max_depth": [2, 3], "eta": [0.1, 0.3]},
        },
    }
    kwargs = build_training_job_kwargs(config, data="d.parquet")
    assert kwargs["tuning"]["metric"] == "gamma_deviance"


def test_a_model_without_the_haute_record_is_refused(tmp_path: Path) -> None:
    booster = xgb.train(
        {"objective": "reg:squarederror", "verbosity": 0},
        xgb.DMatrix(np.random.default_rng(0).random((20, 2)), label=np.arange(20.0)),
        num_boost_round=2,
    )
    path = tmp_path / "foreign.ubj"
    booster.save_model(str(path))
    with pytest.raises(HauteValidationError, match="not an XGBoost model Haute trained"):
        XGBoostModel.load(path)


def test_a_contract_describing_another_objective_fails_the_identity_check(tmp_path: Path) -> None:
    import dataclasses

    from haute._mlflow_io import verify_contract_identity
    from haute.errors import ConfigError

    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    identity = load_contract(Path(result.model_path).parent / model_contract_filename("xgb")).model
    scoring = load_local_model(result.model_path)
    verify_contract_identity(identity, scoring)
    with pytest.raises(ConfigError, match="describes a Gamma model"):
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
            bias=genuine.bias + 0.5, values=genuine.values, terms=genuine.terms
        ),
    )
    with pytest.raises(ModelExplanationError, match="does not match the model margin"):
        explain_native_prediction(scoring, row)


def test_disabled_early_stopping_refits_with_every_fitted_round(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        params={"num_boost_round": 30, "eta": 0.3, "max_depth": 3, "early_stopping_rounds": 0},
    )
    assert result.evaluation["selection_fits"][0]["best_iteration"] == 29
    assert result.final_tree_count == 30


def test_early_stopping_selects_what_native_xgboost_selects() -> None:
    """An independent native early-stopped fit is the oracle for the selected
    round: the adapter's zero-based best_iteration, its trimmed round count and
    its predictions must all equal native XGBoost's."""
    from haute.modelling._xgboost import XGBoostAlgorithm

    data = frame(900)
    train_rows, valid_rows = data.head(700), data.tail(200)
    params = {"eta": 0.9, "max_depth": 6}
    fitted = XGBoostAlgorithm().fit(
        train_rows,
        ["region", "age"],
        ["region"],
        "severity",
        None,
        {**params, "num_boost_round": 500, "early_stopping_rounds": 3},
        "regression",
        eval_df=valid_rows,
        loss="RMSE",
        threads=1,
        seed=0,
    )

    def native_matrix(rows: pl.DataFrame) -> xgb.DMatrix:
        return xgb.DMatrix(
            pd.DataFrame(
                {
                    "region": pd.Categorical(rows["region"].to_list(), categories=LEVELS),
                    "age": rows["age"].to_numpy(),
                }
            ),
            label=rows["severity"].to_numpy(),
            enable_categorical=True,
        )

    dtrain = native_matrix(train_rows)
    oracle = xgb.train(
        {
            **params,
            "objective": "reg:squarederror",
            "tree_method": "hist",
            "seed": 0,
            "nthread": 1,
            "verbosity": 0,
        },
        dtrain,
        num_boost_round=500,
        evals=[(dtrain, "train"), (native_matrix(valid_rows), "validation")],
        early_stopping_rounds=3,
        verbose_eval=False,
    )
    assert fitted.best_iteration == oracle.best_iteration
    assert fitted.rounds_fitted == oracle.best_iteration + 1
    assert fitted.stopping_reason == "validation"
    selected = oracle[: oracle.best_iteration + 1]
    np.testing.assert_allclose(
        fitted.model.predict(data), selected.predict(native_matrix(data)), rtol=1e-6
    )
