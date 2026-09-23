"""MOD-F01 regressions for the class order, script/canvas parity, evidence, and identity."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

from haute.errors import ConfigError, HauteValidationError
from haute.modelling._algorithms import CatBoostAlgorithm
from haute.modelling._candidate_run import training_identity_sha256
from haute.modelling._export import generate_training_script
from haute.modelling._feature_contract import ModelIdentity
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 1,
    "validation": {"method": "single", "size": 0.25},
}


def classification_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "target": "y",
        "task": "classification",
        "loss_function": "Logloss",
        "params": {"iterations": 10},
        "evaluation": EVALUATION,
        "positive_class": "claim",
    }
    config.update(overrides)
    return config


def labelled_frame(n: int = 120) -> pl.DataFrame:
    rng = np.random.default_rng(5)
    x = rng.random(n)
    return pl.DataFrame({"x": x, "y": np.where(x > 0.6, "claim", "none").tolist()})


def test_catboost_class_names_cannot_reorder_the_probability_columns() -> None:
    with pytest.raises(TrainingConfigError, match="cannot set 'class_names'"):
        build_training_job_kwargs(
            classification_config(params={"iterations": 5, "class_names": [1, 0]}),
            data="d.parquet",
        )


def test_fit_refuses_a_class_order_other_than_the_encoding() -> None:
    frame = pl.DataFrame({"x": [0.1, 0.2, 0.8, 0.9] * 10, "y": [0.0, 0.0, 1.0, 1.0] * 10})
    with pytest.raises(HauteValidationError, match="did not fit the encoded classes in order"):
        CatBoostAlgorithm().fit(
            frame,
            ["x"],
            [],
            "y",
            None,
            {"iterations": 3, "loss_function": "Logloss", "class_names": [1.0, 0.0]},
            "classification",
            class_labels=("none", "claim"),
        )


def test_exported_script_constructs_the_job_with_positive_class() -> None:
    from tests.test_glm_integration import _captured_export_kwargs

    config = classification_config()
    script = generate_training_script(config, "data.parquet")
    captured = _captured_export_kwargs(script)
    assert captured["positive_class"] == "claim"
    assert captured == {
        key: value
        for key, value in build_training_job_kwargs(config, data="data.parquet").items()
        if key in captured
    }


def test_positive_class_changes_the_canvas_and_direct_training_identity() -> None:
    frame = labelled_frame()
    identities = []
    for positive in ("claim", "none"):
        kwargs = build_training_job_kwargs(
            classification_config(positive_class=positive), data="d.parquet"
        )
        job = TrainingJob(**{**kwargs, "data": frame})
        assert job.training_identity_sha256 == training_identity_sha256(kwargs)
        identities.append(job.training_identity_sha256)
    assert identities[0] != identities[1]


def test_rounds_configured_honours_every_round_count_spelling() -> None:
    frame = pl.DataFrame({"x": np.linspace(0, 1, 60), "y": np.linspace(0, 2, 60)})
    result = CatBoostAlgorithm().fit(
        frame, ["x"], [], "y", None, {"n_estimators": 3, "loss_function": "RMSE"}, "regression"
    )
    assert (result.rounds_configured, result.rounds_fitted, result.stopping_reason) == (
        3,
        3,
        "none",
    )


def test_scripted_mlflow_logging_carries_fit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAUTE_TRAINING_THREADS", "2")
    captured: dict[str, object] = {}

    def fake_log(*, experiment_name, candidate, destination, check_cancelled):
        captured["params"] = dict(candidate.params)

    rng = np.random.default_rng(2)
    x = rng.random(80)
    job = TrainingJob(
        name="scripted",
        data=pl.DataFrame({"x": x, "y": 2 * x + rng.normal(scale=0.1, size=80)}),
        target="y",
        loss_function="RMSE",
        params={"iterations": 6},
        metrics=["rmse"],
        evaluation=EVALUATION,
        mlflow_experiment="/scripted",
        output_dir=str(tmp_path),
    )
    with patch("haute.modelling._mlflow_log.log_experiment", side_effect=fake_log):
        job.run()
    params = captured["params"]
    assert params["fit_threads"] == 2
    assert params["fit_stopping_reason"] in {"none", "validation"}
    assert "fit_rounds_fitted" in params


def test_haute_trained_string_labels_round_trip_through_mlflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log and reload through real MLflow; compare with the native model independently."""
    import mlflow
    from catboost import CatBoostClassifier

    from haute._mlflow_utils import mlflow_fluent_operation
    from haute.modelling._feature_contract import load_contract
    from haute.modelling._native_pyfunc import package_native_model
    from haute.modelling._training_job import model_contract_filename

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    frame = labelled_frame()
    result = TrainingJob(
        name="labels",
        data=frame,
        target="y",
        task="classification",
        loss_function="Logloss",
        params={"iterations": 15},
        metrics=["auc"],
        positive_class="claim",
        output_dir=str(tmp_path / "train"),
    ).run()
    model_path = Path(result.model_path)
    contract_path = model_path.parent / model_contract_filename(model_path.stem)
    assert load_contract(contract_path).model.class_labels == ("none", "claim")
    package = package_native_model(model_path, contract_path, tmp_path / "package")

    with mlflow_fluent_operation():
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        with mlflow.start_run() as run:
            mlflow.pyfunc.log_model(
                name="model",
                loader_module="haute.modelling._native_pyfunc",
                data_path=str(package),
            )
        served = mlflow.pyfunc.load_model(f"runs:/{run.info.run_id}/model").predict(
            frame.select("x").to_pandas()
        )

    native = CatBoostClassifier()
    native.load_model(str(model_path))
    positive = native.predict_proba(frame.select("x").to_pandas())[:, 1]
    np.testing.assert_allclose(served["pred_proba"].to_numpy(), positive, rtol=1e-9)
    assert served["pred_label"].tolist() == np.where(positive > 0.5, "claim", "none").tolist()
    assert {"claim", "none"} == set(served["pred_label"])


def test_a_contract_describing_another_loss_fails_at_load(tmp_path: Path) -> None:
    import dataclasses

    from haute.modelling._feature_contract import build_contract, load_contract, save_contract
    from haute.modelling._native_pyfunc import NativePyfuncModel, package_native_model
    from haute.modelling._training_job import model_contract_filename

    frame = pl.DataFrame({"x": np.linspace(0, 1, 50), "y": np.linspace(0, 3, 50)})
    result = TrainingJob(
        name="reg",
        data=frame,
        target="y",
        loss_function="RMSE",
        params={"iterations": 4},
        metrics=["rmse"],
        output_dir=str(tmp_path / "train"),
    ).run()
    model_path = Path(result.model_path)
    contract = load_contract(model_path.parent / model_contract_filename(model_path.stem))
    assert isinstance(contract.model, ModelIdentity)
    tampered = build_contract(
        features=contract.features,
        feature_types=contract.feature_types,
        categorical_features=contract.categorical_features,
        target_name=contract.target_name,
        target_type=contract.target_type,
        task=contract.task,
        categorical_levels=contract.categorical_levels,
        offset_column=contract.offset_column,
        model=dataclasses.replace(contract.model, loss="Poisson", link="log"),
    )
    save_contract(tampered, tmp_path / "tampered.json")
    package = package_native_model(model_path, tmp_path / "tampered.json", tmp_path / "pkg")
    with pytest.raises(ConfigError, match="describes a Poisson model"):
        NativePyfuncModel(str(package))


def test_a_contract_describing_another_algorithm_fails_the_identity_check(tmp_path: Path) -> None:
    from haute._mlflow_io import load_local_model, verify_contract_identity

    frame = pl.DataFrame({"x": np.linspace(0, 1, 50), "y": np.linspace(0, 3, 50)})
    result = TrainingJob(
        name="reg",
        data=frame,
        target="y",
        loss_function="RMSE",
        params={"iterations": 4},
        metrics=["rmse"],
        output_dir=str(tmp_path / "train"),
    ).run()
    scoring = load_local_model(result.model_path)
    glm_identity = ModelIdentity(
        algorithm="glm",
        link="identity",
        engine_name="rustystats",
        engine_version="0",
        haute_version="0",
        glm_family="gaussian",
    )
    with pytest.raises(ConfigError, match="describes a glm model"):
        verify_contract_identity(glm_identity, scoring)
