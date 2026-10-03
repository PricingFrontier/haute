"""Model Scoring scores a model file in the project (MSC-03).

A file source loads through the model family registry under the contract saved
beside the file. Every source binds the loaded model to its contract: a
CatBoost file records no baseline of its own, so its offset must be declared by
Haute's metadata or by the contract, and a declaration that disagrees with the
model, or a declared offset missing from the input, is refused for file, run
and registered sources alike. Family-by-family parity across the preview, a
standalone run and a deployed bundle is in ``test_model_family_acceptance``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._model_source import (
    FileModelSource,
    ModelSource,
    RegisteredModelSource,
    RunModelSource,
    load_scoring_model,
)
from haute._path_resolution import RuntimePathOutsideProjectError
from haute.errors import ConfigError, FeatureMismatchError
from haute.modelling._feature_contract import build_contract, save_contract
from haute.modelling._training_job import model_contract_filename

FEATURES = ["age", "region"]


def data(n: int = 300, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice(["east", "north", "south"], n)
    age = rng.uniform(18, 80, n)
    exposure = rng.uniform(0.1, 1.0, n)
    rate = np.exp(-1.5 + 0.02 * (age - 40) + np.where(region == "north", 0.5, 0.0))
    return pl.DataFrame(
        {
            "age": age,
            "region": region.tolist(),
            "exposure": exposure,
            "claims": rng.poisson(rate * exposure).astype(float),
        }
    )


def native_pool(frame: pl.DataFrame, *, offset: bool, label: bool = False) -> Any:
    from catboost import Pool

    return Pool(
        frame.select(FEATURES).to_pandas(),
        label=frame["claims"].to_numpy() if label else None,
        cat_features=["region"],
        baseline=np.log(frame["exposure"].to_numpy()) if offset else None,
    )


def external_catboost(directory: Path, *, offset: bool, stamp: str | None = None) -> Path:
    """A CatBoost model trained through CatBoost's own API, never Haute's.

    *stamp* writes Haute's offset metadata (``""`` declares no offset).
    """
    from catboost import CatBoostRegressor

    model = CatBoostRegressor(
        loss_function="Poisson", iterations=40, depth=3, verbose=0, allow_writing_files=False
    )
    model.fit(native_pool(data(), offset=offset, label=True))
    if stamp is not None:
        model.get_metadata()["haute_offset_column"] = stamp
    path = directory / "ext.cbm"
    model.save_model(str(path))
    return path


def write_contract(model_path: Path, *, offset: str | None, link: str | None) -> Path:
    path = model_path.with_name(model_contract_filename(model_path.stem))
    save_contract(
        build_contract(
            features=FEATURES,
            feature_types={"age": "Float64", "region": "String"},
            categorical_features=["region"],
            target_name="claims",
            target_type="Float64",
            task="regression",
            offset_column=offset,
            offset_link=link,
        ),
        path,
    )
    return path


def native_reference(model_path: Path, frame: pl.DataFrame, *, offset: bool) -> np.ndarray:
    from catboost import CatBoostRegressor

    model = CatBoostRegressor()
    model.load_model(str(model_path))
    return np.asarray(model.predict(native_pool(frame, offset=offset)))


def score(source: ModelSource, frame: pl.DataFrame) -> np.ndarray:
    from haute._model_scorer import ModelScorer

    scored = ModelScorer(model_source=source, task="regression", output_col="pred").score(
        frame.lazy()
    )
    return scored.collect()["pred"].to_numpy()


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with a local MLflow folder; every file is spelled from it."""
    from haute._mlflow_io import clear_local_model_cache, clear_model_cache
    from haute._sandbox import set_project_root

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    set_project_root(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute._mlflow_io._disk_cache_root", lambda: tmp_path / ".cache")
    clear_model_cache()
    clear_local_model_cache()
    return tmp_path


def source_for(kind: str, model_path: Path, contract: Path | None) -> ModelSource:
    """The model (and its contract, beside it) as a file, a run or a registered model."""
    if kind == "file":
        return FileModelSource(model_path=model_path.name)
    from mlflow.tracking import MlflowClient

    uri = (model_path.parent / "mlruns").as_uri()
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    run = client.create_run(client.create_experiment(f"exp-{kind}"))
    client.log_artifact(run.info.run_id, str(model_path))
    if contract is not None:
        client.log_artifact(run.info.run_id, str(contract))
    client.set_terminated(run.info.run_id)
    if kind == "run":
        return RunModelSource(
            run_id=run.info.run_id, artifact_path=model_path.name, mlflow_destination=""
        )
    client.create_registered_model("ext")
    client.create_model_version("ext", f"runs:/{run.info.run_id}", run.info.run_id)
    return RegisteredModelSource(
        registered_model="ext",
        version="1",
        alias="",
        artifact_path=model_path.name,
        mlflow_destination="",
    )


SOURCES = ["file", "run", "registered"]


def test_an_external_catboost_baseline_declared_by_its_contract_matches_native(
    project: Path,
) -> None:
    model_path = external_catboost(project, offset=True)
    write_contract(model_path, offset="exposure", link="log")
    frame = data(seed=7)

    scored = score(FileModelSource(model_path=model_path.name), frame)

    reference = native_reference(model_path, frame, offset=True)
    np.testing.assert_allclose(scored, reference, rtol=1e-10)
    # The baseline is nonconstant: scoring without it would differ row by row.
    assert not np.allclose(scored, native_reference(model_path, frame, offset=False))


def test_the_trace_reconstructs_a_contract_bound_external_prediction(project: Path) -> None:
    from haute._model_explainability import explain_model_score_from_config

    model_path = external_catboost(project, offset=True)
    write_contract(model_path, offset="exposure", link="log")
    frame = data(seed=8).head(1)
    prediction = float(native_reference(model_path, frame, offset=True)[0])

    explanation = explain_model_score_from_config(
        {"sourceType": "file", "model_path": model_path.name, "task": "regression"},
        frame.row(0, named=True),
        {"pred": prediction},
        prediction_column="pred",
        prediction_value=prediction,
    )

    # The explainer refuses a decomposition that does not add up to the model's
    # output, and that output carries the contract-declared baseline.
    assert explanation is not None
    assert explanation["prediction_value"] == pytest.approx(prediction)


def test_an_explicit_no_offset_contract_scores(project: Path) -> None:
    model_path = external_catboost(project, offset=False)
    write_contract(model_path, offset=None, link=None)
    frame = data(seed=9).drop("exposure")

    scored = score(FileModelSource(model_path=model_path.name), frame)

    np.testing.assert_allclose(
        scored, native_reference(model_path, frame.with_columns(exposure=1.0), offset=False)
    )


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("file", "does not record whether it was trained with an offset"),
        # A run's model is bound to the contract logged beside it, which is missing.
        ("run", "has no feature contract ext.feature_contract.json beside ext.cbm"),
        ("registered", "has no feature contract ext.feature_contract.json beside ext.cbm"),
    ],
)
def test_an_undeclared_catboost_offset_is_refused(project: Path, kind: str, message: str) -> None:
    model_path = external_catboost(project, offset=True)

    with pytest.raises(ConfigError, match=message):
        score(source_for(kind, model_path, None), data())


@pytest.mark.parametrize("kind", SOURCES)
def test_a_run_logged_or_sibling_contract_declares_the_offset(project: Path, kind: str) -> None:
    model_path = external_catboost(project, offset=True)
    contract = write_contract(model_path, offset="exposure", link="log")
    frame = data(seed=10)

    scored = score(source_for(kind, model_path, contract), frame)

    np.testing.assert_allclose(scored, native_reference(model_path, frame, offset=True))


@pytest.mark.parametrize("kind", SOURCES)
def test_metadata_and_contract_that_disagree_are_refused(project: Path, kind: str) -> None:
    # Haute's metadata declares no offset; the contract declares one.
    model_path = external_catboost(project, offset=False, stamp="")
    contract = write_contract(model_path, offset="exposure", link="log")
    source = source_for(kind, model_path, contract)
    contract_path = str(contract) if kind != "file" else None

    with pytest.raises(ConfigError, match="records offset None"):
        load_scoring_model(source, "regression", feature_contract_path=contract_path)


@pytest.mark.parametrize("kind", SOURCES)
def test_a_missing_declared_offset_input_fails_before_prediction(project: Path, kind: str) -> None:
    model_path = external_catboost(project, offset=True)
    contract = write_contract(model_path, offset="exposure", link="log")

    with pytest.raises(FeatureMismatchError, match="exposure"):
        score(source_for(kind, model_path, contract), data().drop("exposure"))


def test_a_contract_naming_an_offset_without_its_link_is_refused(project: Path) -> None:
    model_path = external_catboost(project, offset=True)
    write_contract(model_path, offset="exposure", link=None)

    with pytest.raises(ConfigError, match="not how it enters the model"):
        score(FileModelSource(model_path=model_path.name), data())


@pytest.mark.parametrize(
    ("model_path", "error", "message"),
    [
        ("missing.cbm", ConfigError, "'missing.cbm' does not exist"),
        ("model.pkl", ConfigError, "'model.pkl' is not a model file"),
        ("../outside.cbm", RuntimePathOutsideProjectError, "outside the project"),
    ],
)
def test_an_unusable_model_file_fails_naming_it(
    project: Path, model_path: str, error: type[Exception], message: str
) -> None:
    (project / "model.pkl").write_bytes(b"model")

    with pytest.raises(error, match=message):
        score(FileModelSource(model_path=model_path), data())


def train(project: Path, algorithm: str, **params: Any) -> Path:
    from haute.modelling._training_job import TrainingJob

    result = TrainingJob(
        name=algorithm,
        data=data(),
        target="claims",
        algorithm=algorithm,
        loss_function=params.pop("loss", None),
        params=params,
        offset="exposure",
        metrics=["poisson_deviance"],
        output_dir=str(project),
        # A GLM's features are its terms.
        feature_columns=None if algorithm == "glm" else FEATURES,
    ).run()
    return Path(result.model_path)


def test_an_ebm_without_its_contract_fails_naming_the_file(project: Path) -> None:
    model_path = train(project, "ebm", loss="Poisson", max_rounds=20, interactions=0)
    model_path.with_name(model_contract_filename(model_path.stem)).unlink()

    with pytest.raises(ConfigError, match=f"beside {model_path.name}"):
        score(FileModelSource(model_path=model_path.name), data())


GLM = {"terms": {"age": {"type": "linear"}}, "family": "poisson", "link": "log"}


@pytest.mark.parametrize(
    ("algorithm", "params"),
    [
        ("catboost", {"loss": "Poisson", "iterations": 20, "depth": 3}),
        ("glm", GLM),
        ("xgboost", {"loss": "Poisson", "num_boost_round": 20, "max_depth": 3}),
    ],
)
def test_the_trace_explains_a_file_sourced_prediction(
    project: Path, algorithm: str, params: dict[str, Any]
) -> None:
    from haute._model_explainability import explain_model_score_from_config

    model_path = train(project, algorithm, **params)
    frame = data(seed=11).head(1)
    prediction = float(score(FileModelSource(model_path=model_path.name), frame)[0])

    explanation = explain_model_score_from_config(
        {"sourceType": "file", "model_path": model_path.name, "task": "regression"},
        frame.row(0, named=True),
        {"pred": prediction},
        prediction_column="pred",
        prediction_value=prediction,
    )

    assert explanation is not None
    assert explanation["prediction_value"] == pytest.approx(prediction)


def test_a_glm_file_scores_as_its_run_does(project: Path) -> None:
    model_path = train(project, "glm", **GLM)
    contract = model_path.with_name(model_contract_filename(model_path.stem))
    frame = data(seed=12)

    from_file = score(FileModelSource(model_path=model_path.name), frame)
    from_run = score(source_for("run", model_path, contract), frame)

    np.testing.assert_allclose(from_file, from_run, rtol=1e-12)
    doubled = score(
        FileModelSource(model_path=model_path.name),
        frame.with_columns(pl.col("exposure") * 2),
    )
    np.testing.assert_allclose(doubled, 2 * from_file, rtol=1e-9)


def test_haute_catboost_without_an_offset_declares_none_in_its_metadata(project: Path) -> None:
    from haute.modelling._training_job import TrainingJob

    result = TrainingJob(
        name="plain",
        data=data(),
        target="claims",
        algorithm="catboost",
        loss_function="Poisson",
        params={"iterations": 10, "depth": 2},
        metrics=["poisson_deviance"],
        output_dir=str(project),
        feature_columns=FEATURES,
    ).run()
    model_path = Path(result.model_path)
    model_path.with_name(model_contract_filename(model_path.stem)).unlink()

    scoring = load_scoring_model(FileModelSource(model_path=model_path.name), "regression")

    assert scoring.offset_declared and scoring.offset_column is None


class TestInspectionEndpoint:
    def test_reports_a_saved_model_and_its_contract(self, project: Path, client: Any) -> None:
        model_path = train(project, "catboost", loss="Poisson", iterations=10, depth=2)

        response = client.get("/api/model-file", params={"path": model_path.name})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["flavor"] == "catboost"
        assert body["task"] == "regression"
        assert body["features"] == FEATURES
        assert body["categorical_features"] == ["region"]
        assert (body["offset_column"], body["offset_link"]) == ("exposure", "log")
        assert body["contract_path"] == model_contract_filename(model_path.stem)

    def test_reports_the_scoring_refusal(self, project: Path, client: Any) -> None:
        model_path = external_catboost(project, offset=True)

        response = client.get("/api/model-file", params={"path": model_path.name})

        assert response.status_code == 400
        assert "does not record whether it was trained with an offset" in response.json()["detail"]


def _model_score_graph(config: dict[str, Any]) -> Any:
    from tests.conftest import make_graph

    return make_graph(
        {
            "nodes": [
                {"id": "src", "data": {"label": "src", "nodeType": "apiInput", "config": {}}},
                {"id": "ms", "data": {"label": "ms", "nodeType": "modelScore", "config": config}},
            ],
            "edges": [{"id": "e1", "source": "src", "target": "ms", "sourceHandle": "src"}],
        }
    )


class TestBundling:
    def test_a_file_source_bundles_its_model_and_the_contract_beside_it(
        self, project: Path
    ) -> None:
        from haute.deploy._bundler import collect_artifacts

        model_path = external_catboost(project, offset=True)
        contract = write_contract(model_path, offset="exposure", link="log")
        graph = _model_score_graph({"sourceType": "file", "model_path": model_path.name})

        artifacts = collect_artifacts(graph, ["src"], project, project_root=project)

        assert artifacts == {
            "ms__ext.cbm": model_path.resolve(),
            "ms__feature_contract.json": contract,
        }

    def test_an_explicit_contract_is_bundled_in_place_of_the_sibling(self, project: Path) -> None:
        from haute.deploy._bundler import collect_artifacts

        model_path = external_catboost(project, offset=True)
        write_contract(model_path, offset="exposure", link="log")
        explicit = write_contract(project / "other.cbm", offset="exposure", link="log")
        graph = _model_score_graph(
            {
                "sourceType": "file",
                "model_path": model_path.name,
                "feature_contract_path": explicit.name,
            }
        )

        artifacts = collect_artifacts(graph, ["src"], project, project_root=project)

        assert artifacts["ms__feature_contract.json"].resolve() == explicit.resolve()

    def test_a_missing_model_file_fails_the_bundle(self, project: Path) -> None:
        from haute.deploy._bundler import collect_artifacts

        graph = _model_score_graph({"sourceType": "file", "model_path": "gone.cbm"})

        with pytest.raises(FileNotFoundError, match="gone.cbm"):
            collect_artifacts(graph, ["src"], project, project_root=project)

    def test_an_undeclared_run_catboost_bundles_the_contract_its_run_logged(
        self, project: Path
    ) -> None:
        from haute.deploy._bundler import collect_artifacts

        model_path = external_catboost(project, offset=True)
        contract = write_contract(model_path, offset="exposure", link="log")
        source = source_for("run", model_path, contract)
        assert isinstance(source, RunModelSource)
        graph = _model_score_graph(
            {"sourceType": "run", "run_id": source.run_id, "artifact_path": model_path.name}
        )

        artifacts = collect_artifacts(graph, ["src"], project, project_root=project)

        bundled = artifacts["ms__feature_contract.json"]
        assert bundled.read_bytes() == contract.read_bytes()


def test_inspection_reports_the_task_a_contractless_file_was_trained_for(project: Path) -> None:
    from catboost import CatBoostClassifier

    from haute._model_source import inspect_model_file

    frame = data().with_columns(claims=(pl.col("claims") > 0).cast(pl.Float64))
    model = CatBoostClassifier(iterations=5, depth=2, verbose=0, allow_writing_files=False)
    model.fit(native_pool(frame, offset=False, label=True))
    model.get_metadata()["haute_offset_column"] = ""
    model.save_model(str(project / "flag.cbm"))

    inspection = inspect_model_file("flag.cbm")

    assert (inspection.task, inspection.contract_path) == ("classification", None)


# ---------------------------------------------------------------------------
# Codex review round 1: one resolution rule, one run, containment, domains
# ---------------------------------------------------------------------------


def test_scoring_and_bundling_pick_the_project_file_over_the_pipeline_directorys(
    project: Path,
) -> None:
    from haute._model_source import resolve_model_file
    from haute.deploy._bundler import collect_artifacts

    pipeline_dir = project / "pipe"
    (pipeline_dir / "models").mkdir(parents=True)
    (project / "models").mkdir()
    in_project = external_catboost(project / "models", offset=False, stamp="")
    external_catboost(pipeline_dir / "models", offset=False, stamp="")
    graph = _model_score_graph({"sourceType": "file", "model_path": "models/ext.cbm"})

    scored = resolve_model_file("models/ext.cbm", pipeline_dir)
    bundled = collect_artifacts(graph, ["src"], pipeline_dir, project_root=project)["ms__ext.cbm"]

    assert scored.resolve() == bundled.resolve() == in_project.resolve()


def test_a_registered_model_and_its_logged_contract_come_from_one_run(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlflow.tracking import MlflowClient

    from haute import _mlflow_io

    pairs = []
    for name, offset in (("v1", True), ("v2", False)):
        directory = project / name
        directory.mkdir()
        model = external_catboost(directory, offset=offset)
        contract = write_contract(
            model, offset="exposure" if offset else None, link="log" if offset else None
        )
        pairs.append((model, contract))
    uri = (project / "mlruns").as_uri()
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    experiment = client.create_experiment("promotion")
    client.create_registered_model("ext")
    for model, contract in pairs:
        run = client.create_run(experiment)
        client.log_artifact(run.info.run_id, str(model))
        client.log_artifact(run.info.run_id, str(contract))
        client.set_terminated(run.info.run_id)
        client.create_model_version("ext", f"runs:/{run.info.run_id}", run.info.run_id)
    client.set_registered_model_alias("ext", "champion", "1")
    resolve = _mlflow_io.resolve_run_artifact

    def promote_then_resolve(**kwargs: Any) -> tuple[str, str]:
        # The alias moves after the first model load and before the contract read.
        client.set_registered_model_alias("ext", "champion", "2")
        return resolve(**kwargs)

    monkeypatch.setattr(_mlflow_io, "resolve_run_artifact", promote_then_resolve)
    source = RegisteredModelSource(
        registered_model="ext",
        version="",
        alias="champion",
        artifact_path="ext.cbm",
        mlflow_destination="",
    )
    frame = data(seed=13)

    scored = score(source, frame)

    np.testing.assert_allclose(scored, native_reference(pairs[1][0], frame, offset=False))


def test_a_run_logged_contract_enforces_its_categorical_domains(project: Path) -> None:
    model_path = external_catboost(project, offset=True)
    contract = model_path.with_name(model_contract_filename(model_path.stem))
    save_contract(
        build_contract(
            features=FEATURES,
            feature_types={"age": "Float64", "region": "String"},
            categorical_features=["region"],
            categorical_levels={"region": ["east", "north"]},
            target_name="claims",
            target_type="Float64",
            task="regression",
            offset_column="exposure",
            offset_link="log",
        ),
        contract,
    )
    frame = data().with_columns(region=pl.lit("south"))

    with pytest.raises(FeatureMismatchError, match="south"):
        score(source_for("run", model_path, contract), frame)


def test_a_sibling_contract_outside_the_project_is_refused(project: Path) -> None:
    from haute._sandbox import set_project_root
    from haute.deploy._bundler import collect_artifacts
    from haute.errors import DeployError

    inner = project / "inner"
    inner.mkdir()
    set_project_root(inner)
    external_catboost(inner, offset=False, stamp="")
    outside = write_contract(project / "outside.cbm", offset=None, link=None)
    link = inner / "ext.feature_contract.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(RuntimePathOutsideProjectError):
        score(FileModelSource(model_path="ext.cbm"), data())
    graph = _model_score_graph({"sourceType": "file", "model_path": "ext.cbm"})
    with pytest.raises(DeployError, match="outside the project"):
        collect_artifacts(graph, ["src"], inner, project_root=inner)


def test_a_backslash_model_path_bundles_and_serves_under_one_key(project: Path) -> None:
    from haute.deploy._bundler import collect_artifacts
    from haute.deploy._scorer import _remap_artifact
    from haute.deploy._utils import artifact_basename

    (project / "models").mkdir()
    model = external_catboost(project / "models", offset=False, stamp="")
    config = {"sourceType": "file", "model_path": "models\\ext.cbm"}

    artifacts = collect_artifacts(
        _model_score_graph(config), ["src"], project, project_root=project
    )
    remap = {key: str(path) for key, path in artifacts.items()}

    assert artifact_basename("models\\ext.cbm") == artifact_basename("models/ext.cbm") == "ext.cbm"
    served = _remap_artifact("ms", config, remap, "model_path")
    assert served is not None and Path(served).resolve() == model.resolve()
