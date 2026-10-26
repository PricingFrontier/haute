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
from haute.errors import ConfigError, FeatureMismatchError, HauteValidationError
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


@pytest.mark.parametrize("kind", SOURCES)
def test_an_undeclared_catboost_offset_scores_with_no_offset(project: Path, kind: str) -> None:
    """A quote is priced for a full policy term: an exposure of one adds nothing."""
    model_path = external_catboost(project, offset=True)
    frame = data(seed=14)

    scored = score(source_for(kind, model_path, None), frame)

    np.testing.assert_allclose(scored, native_reference(model_path, frame, offset=False))
    full_term = frame.with_columns(exposure=pl.lit(1.0))
    np.testing.assert_allclose(scored, native_reference(model_path, full_term, offset=True))


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


def test_a_missing_or_empty_additive_offset_is_refused_and_the_trace_too(project: Path) -> None:
    """An additive offset is a base premium, not an exposure: it has no default."""
    from haute._model_explainability import (
        ModelExplanationError,
        explain_model_score_from_config,
    )

    model_path = external_catboost(project, offset=True)
    write_contract(model_path, offset="exposure", link="identity")
    source = FileModelSource(model_path=model_path.name)
    frame = data(seed=19)

    # The bound contract's check names the absent offset before scoring starts.
    with pytest.raises(FeatureMismatchError, match="offset_column: expected='exposure'"):
        score(source, frame.drop("exposure"))
    with pytest.raises(HauteValidationError, match="1 rows are empty"):
        score(source, frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("exposure")).head(1))
    with pytest.raises(ModelExplanationError, match="exposure"):
        explain_model_score_from_config(
            {"sourceType": "file", "model_path": model_path.name, "task": "regression"},
            frame.drop("exposure").row(0, named=True),
            {"pred": 1.0},
            prediction_column="pred",
            prediction_value=1.0,
        )


@pytest.mark.parametrize("kind", SOURCES)
def test_a_missing_declared_offset_input_scores_as_a_full_policy_term(
    project: Path, kind: str
) -> None:
    model_path = external_catboost(project, offset=True)
    contract = write_contract(model_path, offset="exposure", link="log")
    frame = data(seed=16)

    scored = score(source_for(kind, model_path, contract), frame.drop("exposure"))

    full_term = frame.with_columns(exposure=pl.lit(1.0))
    np.testing.assert_allclose(scored, native_reference(model_path, full_term, offset=True))


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


def test_an_ebm_scores_without_its_contract(project: Path) -> None:
    model_path = train(project, "ebm", loss="Poisson", max_rounds=20, interactions=0)
    frame = data(seed=15)
    with_contract = score(FileModelSource(model_path=model_path.name), frame)
    model_path.with_name(model_contract_filename(model_path.stem)).unlink()

    np.testing.assert_array_equal(
        score(FileModelSource(model_path=model_path.name), frame), with_contract
    )


GLM = {"terms": {"age": {"type": "linear"}}, "family": "poisson", "link": "log"}

#: Every family Haute trains, each with an exposure offset.
FAMILIES = [
    ("catboost", {"loss": "Poisson", "iterations": 20, "depth": 3}),
    ("glm", GLM),
    ("xgboost", {"loss": "Poisson", "num_boost_round": 20, "max_depth": 3}),
    ("lightgbm", {"loss": "Poisson", "num_iterations": 20, "num_leaves": 7, "min_data_in_leaf": 5}),
    ("ebm", {"loss": "Poisson", "max_rounds": 20, "interactions": 0}),
    ("tboost", {"loss": "Poisson", "n_trees": 50, "n_bags": 1, "prune": False}),
]


@pytest.mark.parametrize(("algorithm", "params"), FAMILIES, ids=[name for name, _ in FAMILIES])
def test_every_family_prices_a_missing_or_empty_exposure_as_a_full_policy_term(
    project: Path, algorithm: str, params: dict[str, Any]
) -> None:
    source = FileModelSource(model_path=train(project, algorithm, **dict(params)).name)
    frame = data(seed=17)
    full_term = score(source, frame.with_columns(exposure=pl.lit(1.0)))
    as_given = score(source, frame)

    np.testing.assert_allclose(score(source, frame.drop("exposure")), full_term, rtol=1e-9)
    even = np.arange(frame.height) % 2 == 0
    blanks = frame.with_columns(
        exposure=pl.when(pl.Series(even)).then(None).otherwise(pl.col("exposure"))
    )
    np.testing.assert_allclose(
        score(source, blanks), np.where(even, full_term, as_given), rtol=1e-9
    )
    with pytest.raises(HauteValidationError, match="must be positive under a log link"):
        score(source, frame.with_columns(exposure=pl.lit(0.0)))


@pytest.mark.parametrize(("algorithm", "params"), FAMILIES, ids=[name for name, _ in FAMILIES])
def test_the_trace_explains_a_row_priced_without_its_exposure(
    project: Path, algorithm: str, params: dict[str, Any]
) -> None:
    from haute._model_explainability import explain_model_score_from_config

    model_path = train(project, algorithm, **dict(params))
    row = data(seed=18).head(1).drop("exposure")
    prediction = float(score(FileModelSource(model_path=model_path.name), row)[0])

    explanation = explain_model_score_from_config(
        {"sourceType": "file", "model_path": model_path.name, "task": "regression"},
        row.row(0, named=True),
        {"pred": prediction},
        prediction_column="pred",
        prediction_value=prediction,
    )

    assert explanation is not None
    assert explanation["prediction_value"] == pytest.approx(prediction)
    assert explanation["notes"] == ["Priced as a full policy term: this row has no 'exposure'."]


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

    def test_reports_an_undeclared_offset_as_none(self, project: Path, client: Any) -> None:
        model_path = external_catboost(project, offset=True)

        response = client.get("/api/model-file", params={"path": model_path.name})

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["offset_column"], body["offset_link"]) == (None, None)
        assert body["contract_path"] is None


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


@pytest.mark.parametrize("kind", ["run", "registered"])
def test_a_run_model_that_declares_its_offset_binds_its_logged_contract(
    project: Path, kind: str
) -> None:
    """The preview binds the contract the deploy bundle ships for every native model."""
    from haute._model_source import load_bound_model

    model_path = external_catboost(project, offset=False, stamp="")
    contract = write_contract(model_path, offset=None, link=None)

    bound = load_bound_model(source_for(kind, model_path, contract), "regression")

    assert bound.contract_path is not None
    assert Path(bound.contract_path).read_bytes() == contract.read_bytes()


@pytest.mark.parametrize("kind", ["run", "registered"])
def test_a_run_model_without_a_logged_contract_scores_against_its_own_features(
    project: Path, kind: str
) -> None:
    """A model trained outside Haute scores; it is checked against the features it names."""
    from haute._model_source import load_bound_model

    model_path = external_catboost(project, offset=False, stamp="")
    source = source_for(kind, model_path, None)
    frame = data(seed=11)

    assert load_bound_model(source, "regression").contract_path is None
    np.testing.assert_allclose(
        score(source, frame), native_reference(model_path, frame, offset=False)
    )
    with pytest.raises(FeatureMismatchError, match="age"):
        score(source, frame.drop("age"))


def test_a_run_without_a_contract_loads_from_the_disk_cache_alone(project: Path) -> None:
    """The cache records that the run logged none, so a cache-only execution still loads it."""
    from haute._mlflow_io import _model_cache, disk_cache_only_model_loads

    model_path = external_catboost(project, offset=False, stamp="")
    source = source_for("run", model_path, None)
    frame = data(seed=12)
    expected = native_reference(model_path, frame, offset=False)
    np.testing.assert_allclose(score(source, frame), expected)
    _model_cache.clear()

    with disk_cache_only_model_loads():
        np.testing.assert_allclose(score(source, frame), expected)


@pytest.mark.parametrize("column", ["region", "age"])
def test_a_feature_whose_dtype_drifted_from_training_is_refused_as_deploy_refuses_it(
    project: Path, column: str
) -> None:
    """The preview compares the bound contract's dtypes with the input, as deploy does.

    ``region`` was trained as String and ``age`` as Float64; an Int64 column in
    either place would be cast and scored without this check.
    """
    from haute.deploy._scorer import _assert_runtime_contract_matches

    model_path = external_catboost(project, offset=True)
    contract = write_contract(model_path, offset="exposure", link="log")
    frame = data().with_columns(pl.lit(7, dtype=pl.Int64).alias(column))

    with pytest.raises(FeatureMismatchError, match="contract mismatch: feature_types") as preview:
        score(FileModelSource(model_path=model_path.name), frame)
    with pytest.raises(FeatureMismatchError) as deployed:
        _assert_runtime_contract_matches(frame.lazy(), str(contract), "regression")
    assert str(preview.value) == str(deployed.value)


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
    fetch = _mlflow_io._run_contract_if_logged

    def promote_then_fetch(*args: Any) -> str | None:
        # The alias moves after the model was resolved and before its contract is read.
        client.set_registered_model_alias("ext", "champion", "2")
        return fetch(*args)

    monkeypatch.setattr(_mlflow_io, "_run_contract_if_logged", promote_then_fetch)
    source = RegisteredModelSource(
        registered_model="ext",
        version="",
        alias="champion",
        artifact_path="ext.cbm",
        mlflow_destination="",
    )
    frame = data(seed=13)

    scored = score(source, frame)

    # Version 1's model under version 1's exposure offset, not version 2's contract.
    np.testing.assert_allclose(scored, native_reference(pairs[0][0], frame, offset=True))


def test_a_run_logged_contract_scores_values_the_model_never_saw(project: Path) -> None:
    model_path = external_catboost(project, offset=True)
    contract = model_path.with_name(model_contract_filename(model_path.stem))
    save_contract(
        build_contract(
            features=FEATURES,
            feature_types={"age": "Float64", "region": "String"},
            categorical_features=["region"],
            target_name="claims",
            target_type="Float64",
            task="regression",
            offset_column="exposure",
            offset_link="log",
        ),
        contract,
    )
    frame = data().with_columns(region=pl.lit("south"))

    scored = score(source_for("run", model_path, contract), frame)

    assert len(scored) == frame.height
    assert np.isfinite(scored).all()


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


@pytest.mark.parametrize("kind", ["run", "registered"])
def test_a_model_and_its_logged_contract_are_read_from_one_resolved_backend(
    project: Path, kind: str
) -> None:
    """A settings change mid-load cannot split the model and contract reads."""
    from unittest.mock import patch

    from haute._mlflow_utils import resolve_backend
    from haute._model_source import load_bound_model

    model_path = external_catboost(project, offset=True)
    contract = write_contract(model_path, offset="exposure", link="log")
    source = source_for(kind, model_path, contract)
    backend = resolve_backend(source.mlflow_destination)  # type: ignore[union-attr]

    # Exactly one resolution is allowed: a second read resolving the
    # destination again would raise StopIteration here.
    with patch("haute._mlflow_utils.resolve_backend", side_effect=[backend]):
        bound = load_bound_model(source, "regression")

    assert bound.scoring_model.offset_column == "exposure"
    assert bound.contract_path is not None


# ---------------------------------------------------------------------------
# Native models trained outside Haute score from what their libraries record
# ---------------------------------------------------------------------------


def native_frame(frame: pl.DataFrame) -> Any:
    """The training rows as pandas, region a categorical as the libraries take it."""
    pdf = frame.select(FEATURES).to_pandas()
    pdf["region"] = pdf["region"].astype("category")
    return pdf


def external_model(directory: Path, library: str, task: str) -> tuple[Path, Any]:
    """A model trained through *library*'s own API, and its own predict function."""
    frame = data(seed=21)
    target = (
        frame["claims"].to_numpy()
        if task == "regression"
        else (frame["claims"].to_numpy() > 0).astype(int)
    )
    if library == "xgboost":
        import xgboost as xgb

        objective = "count:poisson" if task == "regression" else "binary:logistic"
        booster = xgb.train(
            {"objective": objective, "max_depth": 2, "verbosity": 0},
            xgb.DMatrix(native_frame(frame), label=target, enable_categorical=True),
            num_boost_round=10,
        )
        booster.set_attr(haute_offset="none")
        path = directory / "ext.ubj"
        booster.save_model(str(path))

        def predict(rows: pl.DataFrame) -> Any:
            return booster.predict(xgb.DMatrix(native_frame(rows), enable_categorical=True))

        return path, predict
    if library == "lightgbm":
        import lightgbm as lgb

        objective = "poisson" if task == "regression" else "binary"
        booster = lgb.train(
            {"objective": objective, "num_leaves": 4, "min_data_in_leaf": 5, "verbosity": -1},
            lgb.Dataset(native_frame(frame), label=target),
            num_boost_round=10,
        )
        path = directory / "ext.lgbm"
        booster.save_model(str(path))
        return path, lambda rows: booster.predict(native_frame(rows))
    from t_boost import TBoostClassifier, TBoostRegressor

    estimator = (
        TBoostRegressor(n_trees=30, objective="poisson")
        if task == "regression"
        else TBoostClassifier(n_trees=30)
    )
    estimator.fit(frame.select(FEATURES), target)
    path = directory / "ext.tboost"
    path.write_text(estimator.to_json(), encoding="utf-8")
    if task == "regression":
        return path, lambda rows: estimator.predict(rows.select(FEATURES))
    return path, lambda rows: estimator.predict_proba(rows.select(FEATURES))[:, 1]


LIBRARIES = ["xgboost", "lightgbm", "tboost"]


@pytest.mark.parametrize("kind", SOURCES)
@pytest.mark.parametrize("library", LIBRARIES)
def test_a_model_trained_outside_haute_scores_as_its_library_predicts(
    project: Path, library: str, kind: str
) -> None:
    from haute._model_explainability import explain_model_score_from_config

    model_path, native = external_model(project, library, "regression")
    source = source_for(kind, model_path, None)
    frame = data(seed=22)

    scored = score(source, frame)

    np.testing.assert_allclose(scored, native(frame), rtol=1e-6)
    if kind == "file":
        row = frame.head(1)
        explanation = explain_model_score_from_config(
            {"sourceType": "file", "model_path": model_path.name, "task": "regression"},
            row.row(0, named=True),
            {"pred": float(scored[0])},
            prediction_column="pred",
            prediction_value=float(scored[0]),
        )
        assert explanation is not None
        assert explanation["prediction_value"] == pytest.approx(float(scored[0]), rel=1e-5)


@pytest.mark.parametrize("library", LIBRARIES)
def test_a_classifier_trained_outside_haute_scores_its_positive_class(
    project: Path, library: str
) -> None:
    from haute._model_scorer import ModelScorer

    model_path, native = external_model(project, library, "classification")
    frame = data(seed=23)

    scored = (
        ModelScorer(
            model_source=FileModelSource(model_path=model_path.name),
            task="classification",
            output_col="pred",
        )
        .score(frame.lazy())
        .collect()
    )

    np.testing.assert_allclose(scored["pred_proba"], native(frame), rtol=1e-6)
    np.testing.assert_array_equal(scored["pred"], (native(frame) > 0.5).astype(int))


def test_integer_categories_a_library_recorded_score_from_their_text(project: Path) -> None:
    """Haute reads categoricals as text; the library's own integer levels are put back."""
    import lightgbm as lgb
    import pandas as pd
    import xgboost as xgb

    rng = np.random.default_rng(24)
    groups = rng.choice([3, 7, 11], 300)
    pdf = pd.DataFrame({"age": rng.uniform(18, 80, 300), "group": pd.Categorical(groups)})
    label = (groups == 7) * 2.0 + rng.uniform(size=300)
    lgbm = lgb.train(
        {"objective": "regression", "num_leaves": 4, "min_data_in_leaf": 5, "verbosity": -1},
        lgb.Dataset(pdf, label=label),
        num_boost_round=10,
    )
    lgbm.save_model(str(project / "groups.lgbm"))
    booster = xgb.train(
        {"objective": "reg:squarederror", "max_depth": 2, "verbosity": 0},
        xgb.DMatrix(pdf, label=label, enable_categorical=True),
        num_boost_round=10,
    )
    booster.set_attr(haute_offset="none")
    booster.save_model(str(project / "groups.ubj"))
    rows = pl.DataFrame({"age": [30.0, 30.0, 30.0], "group": ["3", "7", "11"]})
    native_rows = pd.DataFrame({"age": [30.0] * 3, "group": pd.Categorical([3, 7, 11])})

    np.testing.assert_allclose(
        score(FileModelSource(model_path="groups.lgbm"), rows), lgbm.predict(native_rows)
    )
    np.testing.assert_allclose(
        score(FileModelSource(model_path="groups.ubj"), rows),
        booster.predict(xgb.DMatrix(native_rows, enable_categorical=True)),
        rtol=1e-6,
    )
    # The category the input names, not its position: 7 scores above 3 and 11.
    scored = score(FileModelSource(model_path="groups.lgbm"), rows)
    assert scored[1] > max(scored[0], scored[2])


def test_categories_trained_as_codes_score_from_the_input_codes(project: Path) -> None:
    """A categorical feature that recorded no levels reads the input's numeric codes."""
    import lightgbm as lgb
    import pandas as pd
    import xgboost as xgb

    rng = np.random.default_rng(25)
    codes = rng.choice([0, 1, 2], 300)
    pdf = pd.DataFrame({"age": rng.uniform(18, 80, 300), "code": codes})
    label = (codes == 1) * 2.0 + rng.uniform(size=300)
    lgbm = lgb.train(
        {"objective": "regression", "num_leaves": 4, "min_data_in_leaf": 5, "verbosity": -1},
        lgb.Dataset(pdf, label=label, categorical_feature=["code"]),
        num_boost_round=10,
    )
    lgbm.save_model(str(project / "codes.lgbm"))
    booster = xgb.train(
        {"objective": "reg:squarederror", "max_depth": 2, "verbosity": 0},
        xgb.DMatrix(
            pdf.astype(float), label=label, feature_types=["float", "c"], enable_categorical=True
        ),
        num_boost_round=10,
    )
    booster.set_attr(haute_offset="none")
    booster.save_model(str(project / "codes.ubj"))
    rows = pl.DataFrame({"age": [30.0, 30.0, 30.0], "code": [0.0, 1.0, 2.0]})
    native_rows = rows.to_pandas()

    np.testing.assert_allclose(
        score(FileModelSource(model_path="codes.lgbm"), rows), lgbm.predict(native_rows)
    )
    np.testing.assert_allclose(
        score(FileModelSource(model_path="codes.ubj"), rows),
        booster.predict(
            xgb.DMatrix(native_rows, feature_types=["float", "c"], enable_categorical=True)
        ),
        rtol=1e-6,
    )


def test_what_a_model_trained_outside_haute_cannot_be_scored_for_is_refused(
    project: Path,
) -> None:
    """More than one output per row, or a classifier with no probability to score."""
    import lightgbm as lgb
    import xgboost as xgb
    from t_boost import TBoostClassifier

    from haute._mlflow_io import load_local_model

    frame = data(seed=26)
    pdf = native_frame(frame)
    three = np.random.default_rng(26).choice([0, 1, 2], frame.height)
    xgb.train(
        {"objective": "multi:softprob", "num_class": 3, "verbosity": 0},
        xgb.DMatrix(pdf, label=three, enable_categorical=True),
        num_boost_round=2,
    ).save_model(str(project / "multi.ubj"))
    lgb.train(
        {"objective": "multiclass", "num_class": 3, "verbosity": -1},
        lgb.Dataset(pdf, label=three),
        num_boost_round=2,
    ).save_model(str(project / "multi.lgbm"))
    xgb.train(
        {"objective": "binary:logitraw", "verbosity": 0},
        xgb.DMatrix(pdf, label=three > 0, enable_categorical=True),
        num_boost_round=2,
    ).save_model(str(project / "raw.ubj"))
    estimator = TBoostClassifier(n_trees=10)
    estimator.fit(frame.select(FEATURES), three)
    (project / "multi.tboost").write_text(estimator.to_json(), encoding="utf-8")

    ranked = xgb.train(
        {"objective": "rank:ndcg", "verbosity": 0},
        xgb.DMatrix(pdf, label=three, group=[frame.height], enable_categorical=True),
        num_boost_round=2,
    )
    ranked.set_attr(haute_offset="none")
    ranked.save_model(str(project / "ranked.ubj"))
    lgb.train(
        {"objective": "lambdarank", "verbosity": -1, "min_data_in_leaf": 5},
        lgb.Dataset(pdf, label=three, group=[frame.height]),
        num_boost_round=2,
    ).save_model(str(project / "ranked.lgbm"))

    for name in ("multi.ubj", "multi.lgbm"):
        with pytest.raises(ConfigError, match="more than one value per row"):
            load_local_model(str(project / name))
    for name in ("ranked.ubj", "ranked.lgbm"):
        with pytest.raises(ConfigError, match="ranking objective"):
            load_local_model(str(project / name))
    with pytest.raises(ConfigError, match="binary:logitraw.*no positive-class probability"):
        load_local_model(str(project / "raw.ubj"), "classification")
    with pytest.raises(ConfigError, match="multi.tboost|3-class classifier"):
        load_local_model(str(project / "multi.tboost"), "classification")


def test_an_xgboost_model_trained_outside_haute_declares_its_margin(project: Path) -> None:
    """XGBoost scores a margin-trained model from base_score when no margin is given,
    so such a model loads only once a stamp or a contract says how it was trained."""
    import xgboost as xgb

    frame = data(seed=27)
    booster = xgb.train(
        {"objective": "count:poisson", "max_depth": 2, "verbosity": 0},
        xgb.DMatrix(
            native_frame(frame),
            label=frame["claims"].to_numpy(),
            base_margin=np.log(frame["exposure"].to_numpy()),
            enable_categorical=True,
        ),
        num_boost_round=10,
    )

    def native(rows: pl.DataFrame, margin: Any) -> Any:
        return booster.predict(
            xgb.DMatrix(native_frame(rows), base_margin=margin, enable_categorical=True)
        )

    unstamped = project / "ext.ubj"
    booster.save_model(str(unstamped))
    with pytest.raises(ConfigError, match=r'set_attr\(haute_offset="none"\)'):
        score(FileModelSource(model_path=unstamped.name), frame)

    stamped_dir = project / "stamped"
    stamped_dir.mkdir()
    booster.set_attr(haute_offset="exposure:log")
    booster.save_model(str(stamped_dir / "ext.ubj"))
    stamped = FileModelSource(model_path="stamped/ext.ubj")
    np.testing.assert_allclose(
        score(stamped, frame), native(frame, np.log(frame["exposure"].to_numpy())), rtol=1e-6
    )
    # A quote without exposure is a full policy term: a zero margin, not base_score.
    np.testing.assert_allclose(
        score(stamped, frame.drop("exposure")), native(frame, np.zeros(frame.height)), rtol=1e-6
    )

    # A contract declares the same margin for the unstamped file; the wrapper applies it.
    write_contract(unstamped, offset="exposure", link="log")
    np.testing.assert_allclose(
        score(FileModelSource(model_path=unstamped.name), frame),
        native(frame, np.log(frame["exposure"].to_numpy())),
        rtol=1e-6,
    )

    booster.set_attr(haute_offset="exposure:sqrt")
    booster.save_model(str(stamped_dir / "bad.ubj"))
    with pytest.raises(ConfigError, match="'haute_offset' attribute is 'exposure:sqrt'"):
        score(FileModelSource(model_path="stamped/bad.ubj"), frame)


@pytest.mark.parametrize(
    ("params", "task"),
    [
        ({"objective": "binary", "sigmoid": 2.0}, "classification"),
        ({"objective": "regression", "reg_sqrt": True}, "regression"),
    ],
    ids=["sigmoid-2", "reg-sqrt"],
)
def test_a_lightgbm_model_trained_outside_haute_keeps_its_response_transform(
    project: Path, params: dict[str, Any], task: str
) -> None:
    """LightGBM's own predict applies the transform it recorded; a link alone would not."""
    import lightgbm as lgb

    from haute._model_scorer import ModelScorer

    frame = data(seed=28)
    claims = frame["claims"].to_numpy()
    label = (claims > 0).astype(int) if task == "classification" else claims * 40.0
    booster = lgb.train(
        {**params, "num_leaves": 4, "min_data_in_leaf": 5, "verbosity": -1},
        lgb.Dataset(native_frame(frame), label=label),
        num_boost_round=10,
    )
    booster.save_model(str(project / "transform.lgbm"))

    scored = (
        ModelScorer(
            model_source=FileModelSource(model_path="transform.lgbm"),
            task=task,
            output_col="pred",
        )
        .score(frame.lazy())
        .collect()
    )

    column = "pred_proba" if task == "classification" else "pred"
    np.testing.assert_allclose(scored[column], booster.predict(native_frame(frame)), rtol=1e-9)
    # The trace explains through the link, which does not rebuild this price: it refuses.
    from haute._model_explainability import (
        ModelExplanationError,
        explain_model_score_from_config,
    )

    with pytest.raises(ModelExplanationError, match="inverse link does not reproduce"):
        explain_model_score_from_config(
            {"sourceType": "file", "model_path": "transform.lgbm", "task": task},
            frame.row(0, named=True),
            {"pred": float(scored["pred"][0])},
            prediction_column="pred",
            prediction_value=float(scored["pred"][0]),
        )


def test_the_preview_notes_an_exposure_it_priced_as_a_full_policy_term(project: Path) -> None:
    """Nothing fails, but a renamed column or a join that empties rows is said, not hidden."""
    from haute.executor import execute_graph
    from tests.conftest import make_edge, make_graph, make_node, make_source_node

    model_path = train(project, "catboost", loss="Poisson", iterations=10, depth=2)

    def notes(frame: pl.DataFrame, name: str) -> list[str]:
        frame.write_parquet(project / name)
        graph = make_graph(
            {
                "nodes": [
                    make_source_node("src", str(project / name)),
                    make_node(
                        {
                            "id": "ms",
                            "data": {
                                "label": "ms",
                                "nodeType": "modelScore",
                                "config": {
                                    "sourceType": "file",
                                    "model_path": model_path.name,
                                    "task": "regression",
                                    "output_column": "pred",
                                },
                            },
                        }
                    ),
                ],
                "edges": [make_edge("src", "ms")],
            }
        )
        result = execute_graph(graph, target_node_id="ms")["ms"]
        assert result.status == "ok", result.error
        return result.notes

    frame = data(seed=29).head(10)
    assert notes(frame, "full.parquet") == []
    assert notes(frame.drop("exposure"), "none.parquet") == [
        "Scored as a full policy term: the input has no 'exposure' column."
    ]
    blanks = frame.with_columns(
        exposure=pl.when(pl.int_range(pl.len()) < 3).then(None).otherwise(pl.col("exposure"))
    )
    assert notes(blanks, "blanks.parquet") == [
        "3 of the 10 previewed rows have no 'exposure' and were scored as a full policy term."
    ]
