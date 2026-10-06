"""The model family registry: one registration makes a family scoreable everywhere."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import numpy as np
import polars as pl
import pytest

from haute import _model_flavors
from haute._mlflow_io import ScoringModel, clear_model_cache, load_local_model, load_mlflow_model
from haute._model_flavors import (
    _SUPPORTED_FLAVORS,
    ModelFamily,
    ModelFlavor,
    family_for_algorithm,
    family_for_artifact,
    model_families,
    model_family_fixture,
    model_file_suffixes,
    register_model_family,
)
from haute.errors import ConfigError

BUILT_IN_SUFFIXES = [".cbm", ".rsglm", ".ubj", ".lgbm", ".ebm", ".tboost"]


class _StubModel:
    """Predicts ``coefficient * sum(features)`` from a Polars frame."""

    def __init__(self, features: list[str], coefficient: float) -> None:
        self.features = features
        self.coefficient = coefficient

    def predict(self, frame: pl.DataFrame) -> np.ndarray:
        return frame.select(self.features).sum_horizontal().to_numpy() * self.coefficient


def _load_stub_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    return ScoringModel(
        model=_StubModel(spec["features"], spec["coefficient"]),
        feature_names=list(spec["features"]),
        flavor="stub",
    )


@pytest.fixture
def stub_family(monkeypatch: pytest.MonkeyPatch) -> ModelFamily:
    """A family registered with one call, on a registry copy the test discards."""
    monkeypatch.setattr(_model_flavors, "_FAMILIES", dict(_model_flavors._FAMILIES))
    family = ModelFamily(
        flavor="stub",
        label="Stub",
        suffixes=(".stub",),
        load_file=_load_stub_file,
        algorithm=None,
        requires_contract=False,
        self_describing=False,
        predict_frame="polars",
        offset_input="column",
        offset_column=None,
        offset_link=None,
        explanation=None,
        distributions=("stub-runtime",),
    )
    register_model_family(family)
    clear_model_cache()
    yield family
    clear_model_cache()


def _write_stub(directory: Path, name: str = "model.stub") -> Path:
    path = directory / name
    path.write_text(json.dumps({"features": ["a", "b"], "coefficient": 2.0}), encoding="utf-8")
    return path


def _log_to_local_run(
    path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, artifact_dir: str | None = None
) -> str:
    import mlflow

    from haute._mlflow_utils import mlflow_fluent_operation, set_tracking_uri_preserving_env
    from haute._sandbox import set_project_root
    from haute.modelling._mlflow_log import resolve_tracking_backend

    # The local file store sits under the project root; keep it in tmp_path.
    set_project_root(tmp_path)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    with mlflow_fluent_operation():
        set_tracking_uri_preserving_env(mlflow, resolve_tracking_backend("")[0])
        with mlflow.start_run() as run:
            mlflow.log_artifact(str(path), artifact_path=artifact_dir)
    return str(run.info.run_id)


FRAME = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})


def _scores(scoring: Any) -> list[float]:
    from haute._model_scorer import score_frame

    scored = score_frame(
        model=scoring.raw_model,
        lf=FRAME.lazy(),
        features=list(scoring.feature_names),
        cat_feature_names=scoring.cat_feature_names,
        flavor=scoring.flavor,
        task="regression",
        batch=False,
    ).collect()
    return scored["prediction"].to_list()


def test_a_registered_family_loads_and_scores_from_a_local_file(
    stub_family: ModelFamily, tmp_path: Path
) -> None:
    scoring = load_local_model(str(_write_stub(tmp_path)), task="regression")

    assert scoring.flavor == "stub"
    assert scoring.feature_names == ["a", "b"]
    assert _scores(scoring) == [8.0, 12.0]


def test_a_registered_family_loads_from_a_run_artifact_and_by_discovery(
    stub_family: ModelFamily, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _log_to_local_run(_write_stub(tmp_path), tmp_path, monkeypatch, artifact_dir="models")

    named = load_mlflow_model(
        source_type="run", run_id=run_id, artifact_path="models/model.stub", task="regression"
    )
    clear_model_cache()
    discovered = load_mlflow_model(source_type="run", run_id=run_id, task="regression")

    assert named.flavor == discovered.flavor == "stub"
    assert _scores(named) == _scores(discovered) == [8.0, 12.0]


def test_a_registered_family_is_in_the_suffix_list_and_maps_to_its_distributions(
    stub_family: ModelFamily, tmp_path: Path
) -> None:
    from haute.deploy._container import _extra_deps_by_artifact

    resolved = SimpleNamespace(artifacts={"ms__model.stub": tmp_path / "model.stub"})

    assert model_file_suffixes() == (*BUILT_IN_SUFFIXES, ".stub")
    assert _extra_deps_by_artifact(resolved) == {"stub-runtime": ["ms__model.stub"]}  # type: ignore[arg-type]


def test_an_unrecognised_suffix_is_refused_naming_the_supported_suffixes() -> None:
    suffixes = r"\.cbm, \.rsglm, \.ubj, \.lgbm, \.ebm, \.tboost"
    with pytest.raises(ConfigError, match=suffixes) as local:
        load_local_model("model.pkl")
    with pytest.raises(ConfigError, match=suffixes) as run:
        load_mlflow_model(source_type="run", run_id="abc123", artifact_path="model.pkl")

    assert local.value.context["supported_suffixes"] == BUILT_IN_SUFFIXES
    assert run.value.context["supported_suffixes"] == BUILT_IN_SUFFIXES


@pytest.mark.parametrize(
    ("path", "flavor"),
    [
        ("model.cbm", "catboost"),
        ("runs/x/MODEL.RSGLM", "rustystats"),
        ("C:\\models\\model.ubj", "xgboost"),
        ("model.lgbm", "lightgbm"),
        ("model.ebm", "ebm"),
        ("model", "pyfunc"),
        ("models/model/", "pyfunc"),
    ],
)
def test_an_artifact_path_resolves_to_its_family(path: str, flavor: str) -> None:
    assert family_for_artifact(path).flavor == flavor


def test_the_built_in_registrations_are_exactly_the_typed_flavors() -> None:
    assert {family.flavor for family in model_families()} == set(_SUPPORTED_FLAVORS)
    assert _SUPPORTED_FLAVORS == frozenset(get_args(ModelFlavor))


def test_a_flavor_or_suffix_cannot_be_registered_twice(stub_family: ModelFamily) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_model_family(stub_family)
    clash = ModelFamily(
        flavor="other",
        label="Other",
        suffixes=(".cbm",),
        load_file=_load_stub_file,
        algorithm=None,
        requires_contract=False,
        self_describing=False,
        predict_frame="polars",
        offset_input="column",
        offset_column=None,
        offset_link=None,
        explanation=None,
        distributions=(),
    )
    with pytest.raises(ValueError, match="'catboost' already loads"):
        register_model_family(clash)


def test_every_trained_algorithm_writes_the_suffix_its_family_loads() -> None:
    from haute.modelling._descriptors import DESCRIPTORS

    for key, descriptor in DESCRIPTORS.items():
        family = family_for_algorithm(key)
        assert family is not None, key
        assert descriptor.suffix in family.suffixes
    assert {f.algorithm for f in model_families() if f.algorithm} == set(DESCRIPTORS)


def test_frontend_model_family_fixture_matches_the_registry() -> None:
    """The UI's checked-in family table is exactly the backend registry."""
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/utils/modelFamilies.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == model_family_fixture()
