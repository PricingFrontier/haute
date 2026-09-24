"""EBM family acceptance (MOD-F04): fit, persist, score, explain, tune, serve."""

from __future__ import annotations

import dataclasses
import json
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
from interpret.glassbox import ExplainableBoostingClassifier, ExplainableBoostingRegressor

from haute._mlflow_io import load_local_model
from haute._model_explainability import explain_native_prediction
from haute._model_scorer import score_frame
from haute._sandbox import ArtifactVersionMismatchError
from haute.errors import ConfigError, HauteValidationError
from haute.modelling._descriptors import EBM
from haute.modelling._ebm import EBMAlgorithm, EBMModel
from haute.modelling._feature_contract import load_contract, save_contract
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob, model_contract_filename

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 3,
    "validation": {"method": "single", "size": 0.2},
}
LEVELS = ["east", "north", "south"]
PARAMS = {"max_rounds": 150, "learning_rate": 0.05, "interactions": 0}


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
        name="ebm",
        data=kwargs.pop("data", frame()),
        target=target,
        algorithm="ebm",
        loss_function=loss,
        params=kwargs.pop("params", PARAMS),
        metrics=kwargs.pop("metrics", ["rmse"]),
        output_dir=str(tmp_path),
        evaluation=kwargs.pop("evaluation", EVALUATION),
        feature_columns=kwargs.pop("feature_columns", ["region", "age"]),
        **kwargs,
    )
    return job, job.run()


def contract_of(result: object):
    path = Path(result.model_path)  # type: ignore[attr-defined]
    return load_contract(path.parent / model_contract_filename(path.stem))


@pytest.mark.parametrize(
    ("loss", "target", "extra", "objective"),
    [
        ("RMSE", "severity", {}, "rmse"),
        ("Poisson", "claims", {"offset": "exposure"}, "poisson_deviance"),
        ("Gamma", "severity", {}, "gamma_deviance"),
        ("Tweedie", "claims", {"variance_power": 1.5, "offset": "exposure"}, "tweedie_deviance"),
    ],
)
def test_each_loss_trains_weighted_and_reloads_through_its_contract(
    tmp_path: Path, loss: str, target: str, extra: dict, objective: str
) -> None:
    _job, result = train(tmp_path, target=target, loss=loss, weight="weight", **extra)
    assert result.diagnostics_errors == []
    assert result.ebm_terms and result.pdp_data
    assert Path(result.model_path).suffix == ".ebm"
    scoring = load_local_model(result.model_path)
    assert scoring.flavor == "ebm"
    assert scoring.raw_model.objective() == objective
    identity = contract_of(result).model
    assert (identity.algorithm, identity.loss, identity.engine_name) == ("ebm", loss, "interpret")
    if loss == "Tweedie":
        assert scoring.raw_model.estimator.objective == "tweedie_deviance:variance_power=1.5"


def test_fit_evidence_records_the_budget_and_native_steps_never_a_tree_count(
    tmp_path: Path,
) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    evidence = result.fit_evidence
    assert evidence["rounds_configured"] == 150
    assert evidence["rounds_fitted"] is None
    assert evidence["stopping_reason"] == "none"
    assert evidence["threads"] == 1
    assert evidence["term_update_steps"] and all(
        isinstance(step, int) and step > 0 for step in evidence["term_update_steps"]
    )
    assert result.final_tree_count is None
    assert result.best_iteration is None


def test_scoring_matches_the_native_estimator_with_the_offset_applied_once(
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
        flavor="ebm",
        offset_column=scoring.offset_column,
    ).collect()["prediction"]
    native = scoring.raw_model.estimator.predict(
        pd.DataFrame(
            {
                "region": pd.Categorical(data["region"].to_list(), categories=LEVELS),
                "age": data["age"].to_numpy(),
            }
        ),
        init_score=np.log(data["exposure"].to_numpy()),
    )
    np.testing.assert_allclose(scored.to_numpy(), native, rtol=1e-12)
    doubled = data.with_columns(pl.col("exposure") * 2)
    np.testing.assert_allclose(scoring.raw_model.predict(doubled), 2 * scored.to_numpy())


def test_training_uses_the_offset_as_init_score() -> None:
    """An independent EBM fitted with the same init_score predicts the same."""
    data = frame(700)
    params = {"max_rounds": 120, "interactions": 0}
    fitted = EBMAlgorithm().fit(
        data,
        ["region", "age"],
        ["region"],
        "claims",
        None,
        params,
        "regression",
        loss="Poisson",
        offset="exposure",
        seed=0,
    )
    native_x = pd.DataFrame(
        {
            "region": pd.Categorical(data["region"].to_list(), categories=LEVELS),
            "age": data["age"].to_numpy(),
        }
    )
    oracle = ExplainableBoostingRegressor(
        objective="poisson_deviance",
        feature_names=["region", "age"],
        feature_types=["nominal", "continuous"],
        interactions=0,
        max_rounds=120,
        outer_bags=1,
        inner_bags=0,
        validation_size=0,
        early_stopping_rounds=0,
        n_jobs=1,
        random_state=0,
    )
    oracle.fit(native_x, data["claims"].to_numpy(), init_score=np.log(data["exposure"].to_numpy()))
    np.testing.assert_allclose(
        fitted.model.predict(data),
        oracle.predict(native_x, init_score=np.log(data["exposure"].to_numpy())),
        rtol=1e-12,
    )


def test_selection_fits_see_training_rows_and_the_refit_development_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    original = ExplainableBoostingRegressor.fit

    def spy(self, X, y, **kwargs):  # noqa: N803 - sklearn's name
        seen.append(len(X))
        return original(self, X, y, **kwargs)

    monkeypatch.setattr(ExplainableBoostingRegressor, "fit", spy)
    evaluation = {**EVALUATION, "test": {"size": 0.25}}
    _job, result = train(tmp_path, target="severity", loss="RMSE", evaluation=evaluation)
    selection, final = seen
    fit = result.evaluation["selection_fits"][0]
    assert selection == fit["train_rows"]
    assert final == result.development_rows
    assert final + result.final_test_rows == 600
    assert selection + fit["validation_rows"] == result.development_rows


def test_contributions_reconstruct_the_margin_and_keep_interactions_whole(
    tmp_path: Path,
) -> None:
    data = frame()
    _job, result = train(
        tmp_path,
        target="claims",
        loss="Poisson",
        offset="exposure",
        params={"max_rounds": 150, "interactions": [["region", "age"]]},
    )
    model = load_local_model(result.model_path).raw_model
    contributions = model.contributions(data)
    assert contributions.terms == [("region",), ("age",), ("region", "age")]
    margin = contributions.bias + contributions.values.sum(axis=1)
    np.testing.assert_allclose(np.exp(margin), model.predict(data), rtol=1e-12)
    explanation = explain_native_prediction(
        load_local_model(result.model_path),
        {"region": "north", "age": 44.0, "exposure": 0.5},
    )
    assert explanation["status"] == "ok"
    assert explanation["type"] == "ebm_terms"
    interaction = next(c for c in explanation["contributions"] if c["term_type"] == "interaction")
    assert interaction["feature"] == "region & age"
    assert interaction["term_features"] == ["region", "age"]
    assert interaction["feature_value"] == {"region": "north", "age": 44.0}


def test_term_report_describes_shapes_missing_bins_and_surfaces(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="claims",
        loss="Poisson",
        offset="exposure",
        params={"max_rounds": 150, "interactions": [["region", "age"]]},
    )
    terms = {term["term"]: term for term in result.ebm_terms}
    region = terms["region"]
    assert region["kind"] == "main"
    assert region["axes"][0]["labels"] == ["Missing", "east", "north", "south"]
    assert len(region["scores"]) == 4
    age = terms["age"]
    assert age["axes"][0]["type"] == "continuous"
    assert len(age["scores"]) == len(age["axes"][0]["labels"])
    surface = terms["region & age"]
    assert surface["kind"] == "interaction"
    assert len(surface["scores"]) == len(surface["axes"][0]["labels"])
    assert {len(row) for row in surface["scores"]} == {len(surface["axes"][1]["labels"])}
    # Term importances are the report's ranking, interactions kept whole.
    assert [row["feature"] for row in result.feature_importance] == [
        term["term"] for term in result.ebm_terms
    ]


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
        flavor="ebm",
        task="classification",
    ).collect()
    expected = np.where(scored["prediction_proba"].to_numpy() > 0.5, "yes", "no")
    assert scored["prediction"].to_list() == expected.tolist()
    assert isinstance(scoring.raw_model.estimator, ExplainableBoostingClassifier)
    margin = scoring.raw_model.predict_margin(frame())
    np.testing.assert_allclose(
        1 / (1 + np.exp(-margin)), scored["prediction_proba"].to_numpy(), rtol=1e-12
    )


def test_a_regression_model_refuses_to_score_as_a_classifier(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    with pytest.raises(
        ConfigError, match="Regressor but its contract describes a classification model"
    ):
        EBMModel.load(
            result.model_path, dataclasses.replace(contract_of(result), task="classification")
        )


def test_unseen_categories_fail_and_nulls_score_through_the_missing_bin(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    model = load_local_model(result.model_path).raw_model
    with pytest.raises(HauteValidationError, match="not trained on: 'mars'"):
        model.predict(frame().with_columns(pl.lit("mars").alias("region")))
    probe = pl.DataFrame({"region": [None], "age": [40.0]})
    missing_score = model.contributions(probe).values[0][0]
    region_term = next(term for term in result.ebm_terms if term["term"] == "region")
    assert missing_score == pytest.approx(region_term["scores"][0])


def test_restricted_loader_round_trips_bit_identically(tmp_path: Path) -> None:
    data = frame()
    fitted = EBMAlgorithm().fit(
        data,
        ["region", "age"],
        ["region"],
        "severity",
        None,
        PARAMS,
        "regression",
        loss="RMSE",
        seed=0,
    )
    before = fitted.model.predict(data)
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    contract = contract_of(result)
    path = tmp_path / "copy.ebm"
    fitted.model.save(path)
    save_contract(contract, tmp_path / model_contract_filename("copy"))
    assert np.array_equal(EBMModel.load(path, contract).predict(data), before)


def test_a_mismatched_or_missing_engine_version_fails_before_unpickling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    contract = contract_of(result)

    def must_not_unpickle(path):
        raise AssertionError("unpickled before the version check")

    monkeypatch.setattr("haute._sandbox.restricted_joblib_load", must_not_unpickle)
    stale = dataclasses.replace(
        contract, model=dataclasses.replace(contract.model, engine_version="0.7.7")
    )
    with pytest.raises(ArtifactVersionMismatchError, match="interpret-core 0.7.7"):
        EBMModel.load(result.model_path, stale)
    with pytest.raises(ConfigError, match="no EBM feature contract"):
        EBMModel.load(result.model_path, dataclasses.replace(contract, model=None))


def test_a_model_file_without_its_contract_is_refused(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    lone = tmp_path / "lone" / "orphan.ebm"
    lone.parent.mkdir()
    lone.write_bytes(Path(result.model_path).read_bytes())
    with pytest.raises(ConfigError, match="No feature contract was found beside orphan.ebm"):
        load_local_model(str(lone))


class _Payload:
    def __reduce__(self):
        import os

        return (os.system, ("echo pwned",))


def test_a_crafted_payload_in_an_ebm_file_is_blocked(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    target = tmp_path / Path(result.model_path).name
    assert str(target) == result.model_path
    target.write_bytes(pickle.dumps(_Payload()))
    with pytest.raises(pickle.UnpicklingError, match="system"):
        load_local_model(result.model_path)


def test_a_contract_describing_another_objective_fails_the_identity_check(
    tmp_path: Path,
) -> None:
    from haute._mlflow_io import verify_contract_identity

    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    identity = contract_of(result).model
    scoring = load_local_model(result.model_path)
    verify_contract_identity(identity, scoring)
    with pytest.raises(ConfigError, match="describes a Gamma model.*EBM model"):
        verify_contract_identity(dataclasses.replace(identity, loss="Gamma"), scoring)


@pytest.mark.parametrize(
    ("params", "config_extra", "message"),
    [
        ({"learning_rate": 0.1}, {}, "explicit max_rounds"),
        ({"max_rounds": 0}, {}, "max_rounds must be a positive integer"),
        ({"max_rounds": 10, "outer_bags": 4}, {}, "cannot set 'outer_bags'"),
        ({"max_rounds": 10, "early_stopping_rounds": 5}, {}, "cannot set 'early_stopping_rounds'"),
        ({"max_rounds": 10, "interactions": -1}, {}, "non-negative count"),
        ({"max_rounds": 10, "interactions": [["age"]]}, {}, "pair of two different"),
        ({"max_rounds": 10, "interactions": [["age", "age"]]}, {}, "pair of two different"),
        (
            {"max_rounds": 10, "interactions": [["age", "region"], ["region", "age"]]},
            {},
            "listed twice",
        ),
        (
            {"max_rounds": 10, "interactions": [["age", "region"]]},
            {"monotone_constraints": {"age": 1}},
            "involves monotone-constrained 'age'",
        ),
    ],
)
def test_config_rejects_invalid_ebm_settings(
    params: dict, config_extra: dict, message: str
) -> None:
    config = {
        "target": "y",
        "algorithm": "ebm",
        "loss_function": "RMSE",
        "params": params,
        "evaluation": EVALUATION,
        **config_extra,
    }
    with pytest.raises(TrainingConfigError, match=message):
        build_training_job_kwargs(config, data="d.parquet")


def test_config_refuses_mae_and_feature_weights_and_accepts_a_valid_ebm() -> None:
    config = {
        "target": "y",
        "algorithm": "ebm",
        "loss_function": "Poisson",
        "params": {"max_rounds": 100, "interactions": [["a", "b"]]},
        "evaluation": EVALUATION,
        "monotone_constraints": {"c": 1},
    }
    assert build_training_job_kwargs(config, data="d.parquet")["algorithm"] == "ebm"
    with pytest.raises(TrainingConfigError, match="does not support the MAE loss"):
        build_training_job_kwargs({**config, "loss_function": "MAE"}, data="d.parquet")
    with pytest.raises(TrainingConfigError, match="does not support feature weights"):
        build_training_job_kwargs({**config, "feature_weights": {"a": 2.0}}, data="d.parquet")
    assert EBM.native_loss("classification", "Logloss").objective == "log_loss"


def test_an_interaction_naming_an_unused_feature_fails_before_fitting(tmp_path: Path) -> None:
    with pytest.raises(HauteValidationError, match="features the model does not use: 'exposure'"):
        train(
            tmp_path,
            target="severity",
            loss="RMSE",
            params={"max_rounds": 10, "interactions": [["age", "exposure"]]},
        )
    assert not list(tmp_path.glob("*.ebm"))


def test_a_study_searches_the_budget_and_refits_with_the_winning_parameters(
    tmp_path: Path,
) -> None:
    from haute.schemas import TuningReportPayload

    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        params={"max_rounds": 40, "interactions": 0},
        tuning={
            "schema_version": 1,
            "trial_count": 5,
            "seed": 7,
            "metric": "rmse",
            "search_space": {"max_rounds": [20, 40, 80], "learning_rate": [0.02, 0.1]},
        },
    )
    tuning = result.tuning
    winner = tuning["trials"][tuning["winner_trial_index"]]
    assert tuning["final_params"] == winner["resolved_params"]
    assert tuning["final_tree_count"] is None
    assert result.fit_evidence["rounds_configured"] == winner["resolved_params"]["max_rounds"]
    # The public payload validates the fixed-budget projection too.
    payload = TuningReportPayload.model_validate(
        {key: value for key, value in tuning.items() if value is not None}
    )
    assert payload.final_tree_count is None


def test_the_public_tuning_payload_validates_a_non_catboost_tree_study(tmp_path: Path) -> None:
    """The payload once recomputed CatBoost's ``iterations`` projection for every family."""
    from haute.schemas import TuningReportPayload

    job = TrainingJob(
        name="xgb",
        data=frame(),
        target="severity",
        algorithm="xgboost",
        loss_function="RMSE",
        params={"num_boost_round": 30, "eta": 0.3, "max_depth": 3, "early_stopping_rounds": 5},
        metrics=["rmse"],
        output_dir=str(tmp_path),
        evaluation=EVALUATION,
        feature_columns=["region", "age"],
        tuning={
            "schema_version": 1,
            "trial_count": 5,
            "seed": 7,
            "metric": "rmse",
            "search_space": {"max_depth": [2, 3], "eta": [0.1, 0.3]},
        },
    )
    tuning = job.run().tuning
    payload = TuningReportPayload.model_validate(tuning)
    assert payload.final_params["num_boost_round"] == payload.final_tree_count


def test_the_shared_pyfunc_serves_an_ebm_package_through_mlflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow

    from haute._mlflow_utils import mlflow_fluent_operation
    from haute.modelling._native_pyfunc import package_native_model

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    data = frame()
    _job, result = train(tmp_path / "train", target="claims", loss="Poisson", offset="exposure")
    model_path = Path(result.model_path)
    package = package_native_model(
        model_path, model_path.parent / model_contract_filename("ebm"), tmp_path / "package"
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
    np.testing.assert_allclose(
        served, load_local_model(str(model_path)).raw_model.predict(data), rtol=1e-12
    )


def test_an_mlflow_run_artifact_loads_with_the_contract_logged_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow

    from haute._mlflow_io import load_mlflow_model
    from haute._mlflow_utils import mlflow_fluent_operation, set_tracking_uri_preserving_env
    from haute._sandbox import set_project_root
    from haute.modelling._mlflow_log import resolve_tracking_backend

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    data = frame()
    _job, result = train(tmp_path / "train", target="severity", loss="RMSE")
    model_path = Path(result.model_path)
    with mlflow_fluent_operation():
        set_tracking_uri_preserving_env(mlflow, resolve_tracking_backend("")[0])
        with mlflow.start_run() as run:
            mlflow.log_artifact(str(model_path))
            mlflow.log_artifact(str(model_path.parent / model_contract_filename("ebm")))
    scoring = load_mlflow_model(source_type="run", run_id=run.info.run_id, task="regression")
    assert scoring.flavor == "ebm"
    assert np.array_equal(
        scoring.raw_model.predict(data), load_local_model(str(model_path)).raw_model.predict(data)
    )


def test_saved_contract_records_the_installed_interpret_version(tmp_path: Path) -> None:
    import interpret

    _job, result = train(tmp_path, target="severity", loss="RMSE")
    raw = json.loads(
        (Path(result.model_path).parent / model_contract_filename("ebm")).read_text("utf-8")
    )
    assert raw["model"]["engine"] == {"name": "interpret", "version": interpret.__version__}


def test_deploy_bundles_the_run_contract_an_ebm_needs(tmp_path: Path) -> None:
    from unittest.mock import patch

    from haute._types import PipelineGraph
    from haute.deploy._bundler import collect_artifacts

    model = tmp_path / "ebm.ebm"
    model.write_bytes(b"model")
    contract = tmp_path / "fetched.json"
    contract.write_text("{}", encoding="utf-8")
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "score",
                    "data": {
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "run",
                            "run_id": "run",
                            "artifact_path": "ebm.ebm",
                        },
                    },
                }
            ]
        }
    )
    with (
        patch("haute.deploy._bundler._download_model_artifact", return_value=model),
        patch("haute._mlflow_io._resolve_run_contract", return_value=str(contract)) as fetch,
    ):
        artifacts = collect_artifacts(graph, [], tmp_path)
    assert fetch.call_args.args[2:] == ("run", "ebm.ebm")
    assert artifacts["score__feature_contract.json"] == contract
    assert artifacts["score__ebm.ebm"] == model


def test_a_deployed_ebm_loads_under_its_bundled_contract(tmp_path: Path) -> None:
    from haute.deploy._scorer import _load_local_model_cached

    data = frame()
    _job, result = train(tmp_path / "train", target="severity", loss="RMSE")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    model = bundle / "score__ebm.ebm"
    model.write_bytes(Path(result.model_path).read_bytes())
    contract = bundle / "score__feature_contract.json"
    contract.write_bytes(
        (Path(result.model_path).parent / model_contract_filename("ebm")).read_bytes()
    )
    scoring = _load_local_model_cached(str(model), "regression", str(contract))
    assert np.array_equal(
        scoring.raw_model.predict(data),
        load_local_model(result.model_path).raw_model.predict(data),
    )
    with pytest.raises(ConfigError, match="No feature contract was found"):
        _load_local_model_cached(str(model), "regression", None)


def _write_contract(path: Path, contract: object) -> None:
    """Save *contract* re-hashed, as a genuine (if wrong) contract would be."""
    from haute.modelling._feature_contract import build_contract

    save_contract(
        build_contract(
            features=list(contract.features),  # type: ignore[attr-defined]
            feature_types=dict(contract.feature_types),  # type: ignore[attr-defined]
            categorical_features=list(contract.categorical_features),  # type: ignore[attr-defined]
            target_name=contract.target_name,  # type: ignore[attr-defined]
            target_type=contract.target_type,  # type: ignore[attr-defined]
            task=contract.task,  # type: ignore[attr-defined]
            categorical_levels=contract.categorical_levels,  # type: ignore[attr-defined]
            offset_column=contract.offset_column,  # type: ignore[attr-defined]
            model=contract.model,  # type: ignore[attr-defined]
        ),
        path,
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"loss": "RMSE", "link": "identity"}, "trained with poisson_deviance"),
        ({"loss": "Gamma"}, "describes a Gamma model"),
        ({"loss": None}, "records no loss"),
    ],
)
def test_the_loaders_refuse_a_contract_for_another_loss(
    tmp_path: Path, change: dict, message: str
) -> None:
    from haute.deploy._scorer import _load_local_model_cached

    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    contract = contract_of(result)
    wrong = dataclasses.replace(contract, model=dataclasses.replace(contract.model, **change))
    sidecar = Path(result.model_path).parent / model_contract_filename("ebm")
    _write_contract(sidecar, wrong)
    with pytest.raises(ConfigError, match=message):
        load_local_model(result.model_path)
    with pytest.raises(ConfigError, match=message):
        _load_local_model_cached(result.model_path, "regression", str(sidecar))


def test_a_contract_with_another_tweedie_power_is_refused(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path, target="claims", loss="Tweedie", variance_power=1.5, offset="exposure"
    )
    contract = contract_of(result)
    _write_contract(
        Path(result.model_path).parent / model_contract_filename("ebm"),
        dataclasses.replace(
            contract, model=dataclasses.replace(contract.model, variance_power=1.2)
        ),
    )
    with pytest.raises(ConfigError, match="records variance power 1.2"):
        load_local_model(result.model_path)


def _stale_version(contract: object) -> object:
    return dataclasses.replace(
        contract,
        model=dataclasses.replace(contract.model, engine_version="0.0.1"),  # type: ignore[attr-defined]
    )


def _replace_sidecar(path: Path, contract: object) -> None:
    before = path.stat()
    _write_contract(path, contract)
    # Guarantee the replacement is visible to a stat gate even on coarse clocks.
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))


def test_the_deployed_cache_reloads_when_only_the_contract_changes(tmp_path: Path) -> None:
    from haute.deploy._scorer import _clear_deploy_artifact_caches, _load_local_model_cached

    _clear_deploy_artifact_caches()
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    sidecar = Path(result.model_path).parent / model_contract_filename("ebm")
    first = _load_local_model_cached(result.model_path, "regression", str(sidecar))
    assert _load_local_model_cached(result.model_path, "regression", str(sidecar)) is first
    _replace_sidecar(sidecar, _stale_version(contract_of(result)))
    with pytest.raises(ArtifactVersionMismatchError):
        _load_local_model_cached(result.model_path, "regression", str(sidecar))
    _clear_deploy_artifact_caches()


def test_the_mlflow_cache_reloads_when_only_the_run_contract_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow

    from haute._mlflow_io import (
        _artifact_cache_path,
        _disk_cache_root,
        _model_cache,
        load_mlflow_model,
    )
    from haute._mlflow_utils import (
        mlflow_fluent_operation,
        resolve_backend,
        set_tracking_uri_preserving_env,
    )
    from haute._sandbox import set_project_root
    from haute.modelling._mlflow_log import resolve_tracking_backend

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    _job, result = train(tmp_path / "train", target="severity", loss="RMSE")
    model_path = Path(result.model_path)
    with mlflow_fluent_operation():
        set_tracking_uri_preserving_env(mlflow, resolve_tracking_backend("")[0])
        with mlflow.start_run() as run:
            mlflow.log_artifact(str(model_path))
            mlflow.log_artifact(str(model_path.parent / model_contract_filename("ebm")))
    run_id = run.info.run_id
    _model_cache.clear()
    loads: list[str] = []
    real_load = EBMModel.load.__func__

    def counting_load(cls, path, contract):
        loads.append(str(path))
        return real_load(cls, path, contract)

    monkeypatch.setattr(EBMModel, "load", classmethod(counting_load))

    def load(artifact_path: str = "") -> object:
        return load_mlflow_model(
            source_type="run", run_id=run_id, artifact_path=artifact_path, task="regression"
        )

    # Auto-discovered loads always take the full path: the first entry is reused.
    discovered = load()
    assert load() is discovered and load() is discovered
    assert len(loads) == 1
    # Explicit-artifact loads use the disk-cache fast path, which keys the same identity.
    explicit = load("ebm.ebm")
    assert load("ebm.ebm") is explicit
    assert len(loads) <= 2
    cached_contract = _artifact_cache_path(
        _disk_cache_root(),
        resolve_backend("").digest,
        run_id,
        model_contract_filename("ebm"),
    )
    assert cached_contract.is_file()
    _replace_sidecar(cached_contract, _stale_version(contract_of(result)))
    with pytest.raises(ArtifactVersionMismatchError):
        load("ebm.ebm")
    with pytest.raises(ArtifactVersionMismatchError):
        load()
    _model_cache.clear()


def test_sample_weights_reach_the_estimator() -> None:
    data = frame(800)
    rng = np.random.default_rng(9)
    # Weights that favour one region make the weighted fit demonstrably different.
    weights = np.where(data["region"].to_numpy() == "north", 20.0, 1.0) * rng.uniform(
        0.5, 1.5, len(data)
    )
    data = data.with_columns(pl.Series("weight", weights))
    params = {"max_rounds": 120, "interactions": 0}

    def fit(weight: str | None):
        return (
            EBMAlgorithm()
            .fit(
                data,
                ["region", "age"],
                ["region"],
                "severity",
                weight,
                params,
                "regression",
                loss="RMSE",
                seed=0,
            )
            .model
        )

    weighted = fit("weight").predict(data)
    assert not np.allclose(weighted, fit(None).predict(data))
    native_x = pd.DataFrame(
        {
            "region": pd.Categorical(data["region"].to_list(), categories=LEVELS),
            "age": data["age"].to_numpy(),
        }
    )
    oracle = ExplainableBoostingRegressor(
        objective="rmse",
        feature_names=["region", "age"],
        feature_types=["nominal", "continuous"],
        interactions=0,
        max_rounds=120,
        outer_bags=1,
        inner_bags=0,
        validation_size=0,
        early_stopping_rounds=0,
        n_jobs=1,
        random_state=0,
    )
    oracle.fit(native_x, data["severity"].to_numpy(), sample_weight=weights)
    np.testing.assert_allclose(weighted, oracle.predict(native_x), rtol=1e-12)


@pytest.mark.parametrize("tuned", [False, True])
def test_published_responses_match_the_staged_ebm_artifacts(tmp_path: Path, tuned: bool) -> None:
    """Publication compares the null-free worker response with the staged artifacts,
    which keep an EBM fit's null ``best_iteration`` and a fixed-budget study's null
    ``final_tree_count``; a real EBM run must publish."""
    from haute._worker_protocol import WorkerResultManifest, build_artifact_manifest
    from haute.routes._training_artifacts import (
        _EVALUATION_ARTIFACT_PATHS,
        _TUNING_ARTIFACT_PATHS,
        _validate_training_artifacts,
    )
    from haute.schemas import EvaluationReportPayload, TuningReportPayload

    root = tmp_path / "artifacts"
    output = root / "output"
    tuning = {
        "schema_version": 1,
        "trial_count": 5,
        "seed": 7,
        "metric": "rmse",
        "search_space": {"max_rounds": [20, 40]},
    }
    _job, result = train(
        output,
        target="severity",
        loss="RMSE",
        params={"max_rounds": 30, "interactions": 0},
        **({"tuning": tuning} if tuned else {}),
    )
    staged = {
        "model": Path(result.model_path),
        "feature_contract": output / model_contract_filename("ebm"),
    }
    evaluation = dict(result.evaluation)
    for kind, field in _EVALUATION_ARTIFACT_PATHS.items():
        staged[kind] = Path(evaluation[field])
        evaluation[field] = f"output/{staged[kind].name}"
    tuning_payload = None
    if tuned:
        report = dict(result.tuning)
        for kind, field in _TUNING_ARTIFACT_PATHS.items():
            staged[kind] = Path(report[field])
            report[field] = f"output/{staged[kind].name}"
        tuning_payload = TuningReportPayload.model_validate(report)
        assert tuning_payload.final_tree_count is None
    manifest = WorkerResultManifest(
        metadata={},
        artifacts=tuple(
            build_artifact_manifest(artifact_root=root, path=path, kind=kind, lifetime="staged")
            for kind, path in staged.items()
        ),
    )
    published = _validate_training_artifacts(
        manifest,
        artifact_root=root,
        expected_model_name="ebm",
        expected_evaluation=EvaluationReportPayload.model_validate(evaluation),
        expected_tuning=tuning_payload,
    )
    assert set(published) == set(staged)
