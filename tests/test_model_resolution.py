"""MLR-11: an MLflow reference resolves to one model, for every caller.

A real local MLflow store holds a run with two model files of different engines
and a version registered from the second, a run whose model sits one folder down,
a run holding only a pyfunc model folder, a native model with its feature contract
beside it, and a run whose model file is logged again over its own path with the
same number of bytes. For each reference to a native model the loader, the run
picker's listing, the deploy bundle and the image's dependency list name the same
model, and the scores are that model's own ``predict``. A pyfunc folder is loaded
and listed, and refused by the bundle naming the node. After a file is logged
again the same preview and the same trace give the new model's scores, and a load
whose run is unchanged downloads nothing. The last test fails when code outside
the model resolution lists a run's artifacts for a scoring model.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
import pytest

from tests._project import use_project

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_ROWS = 200
_FEATURES = ["x0", "x1"]


def _features() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame(rng.normal(size=(_ROWS, 2)), columns=_FEATURES)


def _target(frame: pd.DataFrame, scale: float) -> np.ndarray:
    return scale * (1.5 * frame["x0"].to_numpy() - frame["x1"].to_numpy() + 3.0)


def _catboost(frame: pd.DataFrame, scale: float) -> Any:
    from catboost import CatBoostRegressor

    model = CatBoostRegressor(iterations=8, depth=2, verbose=0, allow_writing_files=False)
    model.fit(frame, _target(frame, scale))
    # Declared as Haute declares a fit without an offset.
    model.get_metadata()["haute_offset_column"] = ""
    return model


def _lightgbm(frame: pd.DataFrame) -> Any:
    import lightgbm as lgb

    data = lgb.Dataset(frame, label=_target(frame, 100.0))
    params = {"objective": "regression", "verbose": -1, "num_threads": 1}
    return lgb.train(params, data, num_boost_round=5)


@dataclass
class _Store:
    client: Any
    root: Path
    experiment_id: str
    frame: pd.DataFrame
    models: dict[str, Any]
    runs: dict[str, str]

    def log(self, run: str, path: Path, folder: str | None = None) -> None:
        self.client.log_artifact(self.runs[run], str(path), folder)


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Store:
    from mlflow.tracking import MlflowClient

    from haute.modelling._feature_contract import build_contract, save_contract

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    use_project(tmp_path)
    uri = (tmp_path / "mlruns").as_uri()
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    experiment = client.create_experiment("resolution")
    frame = _features()
    work = tmp_path / "work"
    work.mkdir()
    runs = {
        name: client.create_run(experiment).info.run_id
        for name in ("pair", "nested", "pyfunc", "contracted", "relogged")
    }
    store = _Store(client, tmp_path, experiment, frame, {}, runs)

    frequency, severity = _catboost(frame, 1.0), _lightgbm(frame)
    frequency.save_model(str(work / "frequency.cbm"))
    severity.save_model(str(work / "severity.lgbm"))
    store.log("pair", work / "frequency.cbm")
    store.log("pair", work / "severity.lgbm")
    store.models.update(frequency=frequency, severity=severity)
    client.create_registered_model("severity")
    pair_location = client.get_run(runs["pair"]).info.artifact_uri
    client.create_model_version("severity", f"{pair_location}/severity.lgbm", runs["pair"])

    nested = _catboost(frame, 2.0)
    (work / "nested").mkdir()
    nested.save_model(str(work / "nested" / "model.cbm"))
    store.log("nested", work / "nested" / "model.cbm", "model")
    store.models["nested"] = nested

    import mlflow
    from mlflow.models import infer_signature

    pyfunc_model = _catboost(frame, 3.0)
    mlflow.catboost.save_model(
        pyfunc_model,
        str(work / "pyfunc"),
        signature=infer_signature(frame, pyfunc_model.predict(frame)),
        # Named, so MLflow does not spend seconds inferring them.
        pip_requirements=["catboost"],
    )
    client.log_artifacts(runs["pyfunc"], str(work / "pyfunc"), "model")
    store.models["pyfunc"] = pyfunc_model

    contracted = _catboost(frame, 4.0)
    contracted.save_model(str(work / "glm.cbm"))
    save_contract(
        build_contract(
            features=_FEATURES,
            feature_types={"x0": "Float64", "x1": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        ),
        work / "glm.feature_contract.json",
    )
    store.log("contracted", work / "glm.cbm")
    store.log("contracted", work / "glm.feature_contract.json")
    store.models["contracted"] = contracted

    first = _catboost(frame, 5.0)
    (work / "first").mkdir()
    first.save_model(str(work / "first" / "model.cbm"))
    store.log("relogged", work / "first" / "model.cbm")
    store.models["relogged"] = first

    for run_id in runs.values():
        client.set_terminated(run_id)
    return store


def _relog_with_the_same_size(store: _Store) -> Any:
    """Log a retrained model over ``model.cbm`` of the relogged run, byte count unchanged."""
    retrained = _catboost(store.frame, 50.0)
    work = store.root / "work" / "second"
    work.mkdir()
    retrained.save_model(str(work / "model.cbm"))
    first = store.root / "work" / "first" / "model.cbm"
    assert (work / "model.cbm").stat().st_size == first.stat().st_size, (
        "the retrained file must keep the first one's size, which the store's listing reports"
    )
    assert (work / "model.cbm").read_bytes() != first.read_bytes()
    store.log("relogged", work / "model.cbm")
    store.models["relogged"] = retrained
    return retrained


def _run_source(run_id: str, artifact_path: str = "") -> Any:
    from haute._model_source import RunModelSource

    return RunModelSource(run_id=run_id, artifact_path=artifact_path, mlflow_destination="")


def _registered_source(name: str, version: str) -> Any:
    from haute._model_source import RegisteredModelSource

    return RegisteredModelSource(
        registered_model=name, version=version, alias="", mlflow_destination=""
    )


def _config(source: Any) -> dict[str, Any]:
    from haute._model_source import RunModelSource

    base = {"task": "regression", "output_column": "prediction"}
    if isinstance(source, RunModelSource):
        return base | {
            "source_type": "run",
            "run_id": source.run_id,
            "artifact_path": source.artifact_path,
        }
    return base | {
        "source_type": "registered",
        "registered_model": source.registered_model,
        "version": source.version,
    }


def _scores(source: Any, frame: pd.DataFrame) -> np.ndarray:
    """What a Model Scoring node with *source* scores *frame* as."""
    from haute._model_scorer import ModelScorer
    from haute._model_source import parse_model_source

    scorer = ModelScorer(
        model_source=parse_model_source(_config(source)),
        task="regression",
        output_col="prediction",
        source="live",
    )
    scored = scorer.score(pl.from_pandas(frame).lazy()).collect()
    return scored["prediction"].to_numpy()


def _graph(config: dict[str, Any], data: Path | None = None) -> Any:
    from tests.conftest import make_graph, make_source_node

    source = (
        make_source_node("src", str(data)).model_dump(mode="json")
        if data is not None
        else {"id": "src", "data": {"label": "src", "nodeType": "apiInput", "config": {}}}
    )
    return make_graph(
        {
            "nodes": [
                source,
                {"id": "ms", "data": {"label": "ms", "nodeType": "modelScore", "config": config}},
            ],
            "edges": [{"id": "e1", "source": "src", "target": "ms", "sourceHandle": "src"}],
        }
    )


def _bundle(store: _Store, source: Any) -> dict[str, Path]:
    from haute.deploy._bundler import collect_artifacts

    graph = _graph(_config(source))
    return collect_artifacts(graph, ["src"], store.root, project_root=store.root)


def _image_distributions(source: Any, artifacts: dict[str, Path]) -> set[str]:
    from haute.deploy._config import ContainerConfig
    from haute.deploy._container import runtime_versions
    from tests._deploy_helpers import make_resolved_deploy

    resolved = make_resolved_deploy(
        pipeline_file=Path("main.py"),
        model_name="resolution",
        target="container",
        container=ContainerConfig(base_image="python:3.11.9-slim"),
        pruned_graph=_graph(_config(source)),
        artifacts=artifacts,
        input_schema={"x0": "Float64", "x1": "Float64"},
        output_schema={"prediction": "Float64"},
    )
    return {pin.partition("==")[0] for pin in runtime_versions(resolved)}


def _listed(client: Any, store: _Store) -> dict[str, list[str]]:
    response = client.get(
        f"/api/mlflow/runs?experiment_id={store.experiment_id}&max_results=100"
    )
    assert response.status_code == 200, response.text
    return {run["run_id"]: run["artifacts"] for run in response.json()}


def _stored_bytes(store: _Store, run: str, path: str) -> bytes:
    from mlflow.utils.file_utils import local_file_uri_to_path

    location = store.client.get_run(store.runs[run]).info.artifact_uri
    return (Path(local_file_uri_to_path(location)) / path).read_bytes()


#: (reference, run it reads, the artifact it must resolve to, the model it scores,
#:  the engine the image needs, the engine it must not, whether a contract is bundled)
_NATIVE = {
    "the first file of a pair": (
        lambda s: _run_source(s.runs["pair"], "frequency.cbm"),
        "pair",
        "frequency.cbm",
        "frequency",
        "catboost",
        "lightgbm",
        False,
    ),
    "the second file of a pair": (
        lambda s: _run_source(s.runs["pair"], "severity.lgbm"),
        "pair",
        "severity.lgbm",
        "severity",
        "lightgbm",
        "catboost",
        False,
    ),
    "a version registered from the second file": (
        lambda s: _registered_source("severity", "1"),
        "pair",
        "severity.lgbm",
        "severity",
        "lightgbm",
        "catboost",
        False,
    ),
    "a run whose model is one folder down": (
        lambda s: _run_source(s.runs["nested"]),
        "nested",
        "model/model.cbm",
        "nested",
        "catboost",
        "lightgbm",
        False,
    ),
    "a native model with its contract beside it": (
        lambda s: _run_source(s.runs["contracted"]),
        "contracted",
        "glm.cbm",
        "contracted",
        "catboost",
        "lightgbm",
        True,
    ),
}


class TestEveryCallerScoresOneModel:
    @pytest.mark.parametrize("case", list(_NATIVE))
    def test_loader_listing_bundle_and_image_name_the_same_model(
        self, store: _Store, client: Any, case: str
    ) -> None:
        from haute._mlflow_model import resolve_model
        from haute._mlflow_utils import resolve_backend

        make, run, artifact, model, engine, other_engine, has_contract = _NATIVE[case]
        source = make(store)
        resolved = resolve_model(source, backend=resolve_backend(""))
        assert (resolved.run_id, resolved.artifact.path) == (store.runs[run], artifact)
        assert (resolved.contract is not None) is has_contract

        expected = np.asarray(store.models[model].predict(store.frame), dtype=float)
        np.testing.assert_allclose(_scores(source, store.frame), expected, rtol=1e-6)

        assert artifact in _listed(client, store)[store.runs[run]]

        bundled = _bundle(store, source)
        name = Path(artifact).name
        assert bundled[f"ms__{name}"].read_bytes() == _stored_bytes(store, run, artifact)
        assert ("ms__feature_contract.json" in bundled) is has_contract
        assert sorted(bundled) == sorted(
            [f"ms__{name}", *(["ms__feature_contract.json"] if has_contract else [])]
        )

        distributions = _image_distributions(source, bundled)
        assert engine in distributions
        assert other_engine not in distributions
        assert "mlflow" not in distributions

    def test_a_pyfunc_folder_is_loaded_and_listed_and_refused_by_the_bundle(
        self, store: _Store, client: Any
    ) -> None:
        from haute.errors import DeployError

        source = _run_source(store.runs["pyfunc"])
        expected = np.asarray(store.models["pyfunc"].predict(store.frame), dtype=float)
        np.testing.assert_allclose(_scores(source, store.frame), expected, rtol=1e-6)
        assert _listed(client, store)[store.runs["pyfunc"]] == ["model"]

        with pytest.raises(DeployError, match=r"Node 'ms' scores the MLflow pyfunc model folder"):
            _bundle(store, source)

    def test_a_run_holding_two_models_is_refused_naming_both_everywhere(
        self, store: _Store, client: Any
    ) -> None:
        from haute.errors import ModelResolutionError

        source = _run_source(store.runs["pair"])
        words = r"holds several models: frequency\.cbm, severity\.lgbm\. Set the artifact path"
        with pytest.raises(ModelResolutionError, match=words):
            _scores(source, store.frame)
        with pytest.raises(ModelResolutionError, match=words):
            _bundle(store, source)
        assert _listed(client, store)[store.runs["pair"]] == ["frequency.cbm", "severity.lgbm"]


class TestALoggedAgainFileScores:
    def test_the_same_preview_and_trace_give_the_new_models_scores(self, store: _Store) -> None:
        from haute.executor import _preview_cache, execute_graph
        from haute.trace import _cache as trace_cache
        from haute.trace import execute_trace

        data = store.root / "data.parquet"
        pl.from_pandas(store.frame.head(3)).write_parquet(data)
        graph = _graph(_config(_run_source(store.runs["relogged"], "model.cbm")), data)
        _preview_cache.clear()
        trace_cache.clear()

        def preview() -> list[float]:
            rows = execute_graph(graph, target_node_id="ms")["ms"].preview
            return [row["prediction"] for row in rows]

        def trace() -> float:
            return float(
                execute_trace(graph, row_index=0, target_node_id="ms", column="prediction")
                .output_value
            )

        first = store.models["relogged"].predict(store.frame.head(3))
        np.testing.assert_allclose(preview(), first, rtol=1e-6)
        assert trace() == pytest.approx(first[0], rel=1e-6)
        np.testing.assert_allclose(preview(), first, rtol=1e-6)

        retrained = _relog_with_the_same_size(store)

        second = retrained.predict(store.frame.head(3))
        assert not np.allclose(first, second)
        np.testing.assert_allclose(preview(), second, rtol=1e-6)
        assert trace() == pytest.approx(second[0], rel=1e-6)

    def test_a_load_downloads_only_when_the_file_changed(
        self, store: _Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import mlflow

        from haute._mlflow_io import _model_cache

        downloads: list[str] = []
        download = mlflow.artifacts.download_artifacts

        def counting(artifact_uri: str, **kwargs: Any) -> str:
            downloads.append(artifact_uri)
            return download(artifact_uri, **kwargs)

        monkeypatch.setattr(mlflow.artifacts, "download_artifacts", counting)
        source = _run_source(store.runs["relogged"], "model.cbm")
        head = store.frame.head(3)

        np.testing.assert_allclose(
            _scores(source, head), store.models["relogged"].predict(head), rtol=1e-6
        )
        assert len(downloads) == 1
        _scores(source, head)
        _model_cache.clear()  # what a new process starts with
        _scores(source, head)
        assert len(downloads) == 1, "an unchanged run's model was downloaded again"

        retrained = _relog_with_the_same_size(store)
        np.testing.assert_allclose(_scores(source, head), retrained.predict(head), rtol=1e-6)
        assert len(downloads) == 2
        _model_cache.clear()
        np.testing.assert_allclose(_scores(source, head), retrained.predict(head), rtol=1e-6)
        assert len(downloads) == 2


# ---------------------------------------------------------------------------
# The guard: only the model resolution lists a run's artifacts for a model
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parents[1] / "src" / "haute"
_RESOLUTION = _SRC / "_mlflow_model.py"
#: The optimiser result's own lookup, which is not a search for a scoring model.
_NOT_A_MODEL_SEARCH = {("routes/mlflow.py", "_optimiser_result_listed")}


def _artifact_listings(path: Path) -> list[tuple[str, int]]:
    """Each ``list_artifacts`` call in *path*, by the function it sits in."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else function
            )
            if isinstance(child, ast.Call):
                called = child.func
                callee = (
                    called.attr
                    if isinstance(called, ast.Attribute)
                    else called.id
                    if isinstance(called, ast.Name)
                    else ""
                )
                if callee == "list_artifacts":
                    found.append((function, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_only_the_model_resolution_lists_a_runs_artifacts() -> None:
    outside = [
        f"{path.relative_to(_SRC).as_posix()}:{line} in {function}"
        for path in sorted(_SRC.rglob("*.py"))
        if path != _RESOLUTION
        for function, line in _artifact_listings(path)
        if (path.relative_to(_SRC).as_posix(), function) not in _NOT_A_MODEL_SEARCH
    ]
    assert outside == [], (
        "Code outside haute._mlflow_model lists a run's artifacts; which model a "
        "reference means is the model resolution's answer alone:\n" + "\n".join(outside)
    )
    assert _artifact_listings(_RESOLUTION), "the guard no longer sees the resolution's listings"


def test_the_guard_sees_a_listing_outside_the_resolution(tmp_path: Path) -> None:
    rogue = tmp_path / "rogue.py"
    rogue.write_text(
        "def find(client, run_id):\n    return client.list_artifacts(run_id)\n", encoding="utf-8"
    )
    assert _artifact_listings(rogue) == [("find", 2)]


def test_the_identity_token_is_sixteen_hex_characters() -> None:
    from haute._mlflow_model import StoredArtifact

    token = StoredArtifact("model.cbm", "size:12").token
    assert token == hashlib.sha256(b"size:12").hexdigest()[:16]
