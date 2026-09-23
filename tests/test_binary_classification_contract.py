"""One binary-classification contract for every family, CatBoost included (MOD-F01)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import binary_labels, catboost_class_labels, load_local_model
from haute._model_scorer import score_frame
from haute.errors import HauteValidationError
from haute.modelling._algorithms import CATBOOST_CLASS_LABELS_METADATA_KEY
from haute.modelling._feature_contract import load_contract
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob, model_contract_filename


def frame(target: list[object], *, dtype: pl.DataType | None = None) -> pl.DataFrame:
    n = len(target)
    rng = np.random.default_rng(0)
    return pl.DataFrame(
        {
            "x": rng.random(n),
            "region": rng.choice(["north", "south"], n).tolist(),
            "y": pl.Series(target, dtype=dtype) if dtype is not None else target,
        }
    )


def train(data: pl.DataFrame, tmp_path: Path, **kwargs: object):
    job = TrainingJob(
        name="clf",
        data=data,
        target="y",
        task="classification",
        loss_function="Logloss",
        params={"iterations": 20},
        metrics=["auc"],
        output_dir=str(tmp_path),
        **kwargs,
    )
    return job, job.run()


def score(model_path: str, data: pl.DataFrame) -> pl.DataFrame:
    scoring = load_local_model(model_path, task="classification")
    return score_frame(
        model=scoring.raw_model,
        lf=data.lazy(),
        features=["x", "region"],
        cat_feature_names=frozenset({"region"}),
        flavor="catboost",
        task="classification",
    ).collect()


def test_string_target_trains_on_the_chosen_positive_class_and_serves_original_labels(
    tmp_path: Path,
) -> None:
    labels = ["claim" if i % 3 == 0 else "none" for i in range(120)]
    data = frame(labels)
    job, result = train(data, tmp_path, positive_class="claim")
    assert job._class_labels == ("none", "claim")

    contract = load_contract(Path(result.model_path).parent / model_contract_filename("clf"))
    assert contract.model is not None
    assert contract.model.class_labels == ("none", "claim")

    scored = score(result.model_path, data)
    assert set(scored["prediction"].unique()) <= {"none", "claim"}
    expected = np.where(scored["prediction_proba"].to_numpy() > 0.5, "claim", "none")
    assert scored["prediction"].to_list() == expected.tolist()


def test_boolean_target_makes_true_positive_without_configuration(tmp_path: Path) -> None:
    data = frame([i % 4 == 0 for i in range(120)], dtype=pl.Boolean)
    job, result = train(data, tmp_path)
    assert job._class_labels == (False, True)
    scored = score(result.model_path, data)
    assert scored["prediction"].dtype == pl.Boolean


def test_zero_one_target_rejects_a_contradicting_positive_class(tmp_path: Path) -> None:
    data = frame([i % 2 for i in range(60)])
    with pytest.raises(HauteValidationError, match="positive class is 1"):
        train(data, tmp_path, positive_class=0)


def test_two_string_labels_without_positive_class_fail_before_fitting(tmp_path: Path) -> None:
    data = frame(["a" if i % 2 else "b" for i in range(60)])
    with pytest.raises(HauteValidationError, match="choose which one is the positive class"):
        train(data, tmp_path)


@pytest.mark.parametrize(
    "target",
    [["a"] * 60, ["a", "b", "c"] * 20],
    ids=["single-class", "three-class"],
)
def test_single_and_multiclass_targets_fail_before_fitting(
    tmp_path: Path, target: list[str]
) -> None:
    with pytest.raises(HauteValidationError, match="exactly two classes"):
        train(frame(target), tmp_path, positive_class="a")


def test_probability_of_exactly_one_half_scores_the_negative_class() -> None:
    labels = binary_labels(np.array([0.5, 0.5000001, 0.4999999]), ("no", "yes"))
    assert labels.tolist() == ["no", "yes", "no"]


def test_class_labels_travel_in_the_model_file_metadata(tmp_path: Path) -> None:
    data = frame(["claim" if i % 3 == 0 else "none" for i in range(90)])
    _job, result = train(data, tmp_path, positive_class="claim")
    model = load_local_model(result.model_path, task="classification").raw_model
    assert json.loads(model.get_metadata()[CATBOOST_CLASS_LABELS_METADATA_KEY]) == [
        "none",
        "claim",
    ]
    assert catboost_class_labels(model) == ("none", "claim")


def test_external_catboost_classifier_uses_its_own_class_order(tmp_path: Path) -> None:
    from catboost import CatBoostClassifier

    rng = np.random.default_rng(1)
    x = rng.random((80, 1))
    y = np.where(x[:, 0] > 0.5, "high", "low")
    model = CatBoostClassifier(iterations=5, verbose=0, allow_writing_files=False)
    model.fit(x, y)
    assert catboost_class_labels(model) == tuple(model.classes_)


def test_config_builder_passes_and_validates_positive_class() -> None:
    config = {
        "target": "y",
        "task": "classification",
        "loss_function": "Logloss",
        "params": {"iterations": 5},
        "evaluation": {
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "single", "size": 0.2},
        },
        "positive_class": "claim",
    }
    assert build_training_job_kwargs(config, data="d.parquet")["positive_class"] == "claim"
    with pytest.raises(TrainingConfigError, match="positive_class must be"):
        build_training_job_kwargs({**config, "positive_class": 1.5}, data="d.parquet")
