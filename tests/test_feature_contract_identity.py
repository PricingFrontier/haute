"""Version-2 feature contracts: the model identity section (MOD-F01)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import polars as pl
import pytest

from haute.errors import FeatureMismatchError
from haute.modelling._feature_contract import (
    CONTRACT_VERSION,
    ModelIdentity,
    build_contract,
    load_contract,
    save_contract,
)
from haute.modelling._training_job import TrainingJob

IDENTITY = ModelIdentity(
    algorithm="catboost",
    link="log",
    engine_name="catboost",
    engine_version="1.2.10",
    haute_version="0.1.0",
    loss="Tweedie",
    variance_power=1.5,
    class_labels=None,
    native_feature_names={"a b": "a_b"},
)


def contract(model: ModelIdentity | None = IDENTITY):
    return build_contract(
        features=["a b", "region"],
        feature_types={"a b": "Float64", "region": "String"},
        categorical_features=["region"],
        target_name="y",
        target_type="Float64",
        task="regression",
        categorical_levels={"region": ["north", "south", None]},
        model=model,
    )


def test_identity_round_trips_through_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    save_contract(contract(), path)
    loaded = load_contract(path)
    assert loaded.model == IDENTITY
    assert loaded.contract_version == CONTRACT_VERSION == 2
    assert json.loads(path.read_text(encoding="utf-8"))["contract_version"] == 2


def test_schema_only_contract_keeps_model_null(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    save_contract(contract(model=None), path)
    assert load_contract(path).model is None


@pytest.mark.parametrize(
    "change",
    [
        {"algorithm": "glm"},
        {"link": "identity"},
        {"engine_version": "1.2.11"},
        {"haute_version": "0.2.0"},
        {"loss": "Poisson"},
        {"glm_family": "tweedie"},
        {"variance_power": 1.6},
        {"class_labels": (False, True)},
        {"native_feature_names": {"a b": "a__b"}},
    ],
)
def test_every_identity_field_is_covered_by_the_contract_hash(change: dict) -> None:
    changed = dataclasses.replace(IDENTITY, **change)
    assert contract(changed).contract_hash != contract().contract_hash


def test_identity_does_not_change_the_live_schema_comparison() -> None:
    from haute.modelling._feature_contract import assert_contracts_match

    assert_contracts_match(contract(), contract(model=None))


def test_version_one_contract_is_rejected_with_a_retrain_message(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    save_contract(contract(), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["contract_version"]
    del raw["model"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(FeatureMismatchError, match="not a version-2 feature contract; retrain"):
        load_contract(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("link", "probit", "link must be identity, log, or logit"),
        ("class_labels", [1, 1], "two distinct labels of one type"),
        ("class_labels", [0, "yes"], "two distinct labels of one type"),
        ("engine", {"name": "catboost"}, "engine must name"),
        ("algorithm", "", "must be a non-empty string"),
    ],
)
def test_malformed_identity_fails_to_load(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    path = tmp_path / "c.json"
    save_contract(contract(), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["model"][field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(FeatureMismatchError, match=message):
        load_contract(path, verify_hash=False)


def test_training_job_writes_identity_for_catboost_and_glm(tmp_path: Path) -> None:
    from importlib.metadata import version

    frame = pl.DataFrame(
        {"x": [float(i % 7) for i in range(80)], "y": [float(i % 5) for i in range(80)]}
    )
    catboost_job = TrainingJob(
        name="cb",
        data=frame,
        target="y",
        loss_function="Poisson",
        params={"iterations": 5},
        output_dir=str(tmp_path / "cb"),
    )
    identity = catboost_job._model_identity()
    assert (identity.algorithm, identity.loss, identity.link) == ("catboost", "Poisson", "log")
    assert identity.engine_version == version("catboost")

    glm_job = TrainingJob(
        name="glm",
        data=frame,
        target="y",
        algorithm="glm",
        params={"family": "tweedie", "var_power": 1.4, "terms": {"x": {"type": "linear"}}},
        output_dir=str(tmp_path / "glm"),
    )
    glm_identity = glm_job._model_identity()
    assert glm_identity.algorithm == "glm"
    assert glm_identity.glm_family == "tweedie"
    assert glm_identity.link == "log"
    assert glm_identity.variance_power == 1.4
    assert glm_identity.loss is None
