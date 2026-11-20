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
from tests._source_files import source_files

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

    # Both this model and its retrain below are copies of one trained model, which
    # save to one size on every platform (a copy can differ in size from the model
    # it was copied from), and differ only in their scale and bias.
    trained = _catboost(frame, 5.0)
    store.models["relogged_trained"] = trained
    first = trained.copy()
    first.set_scale_and_bias(1.5, 0.5)
    (work / "first").mkdir()
    first.save_model(str(work / "first" / "model.cbm"))
    store.log("relogged", work / "first" / "model.cbm")
    store.models["relogged"] = first

    for run_id in runs.values():
        client.set_terminated(run_id)
    return store


def _relog_with_the_same_size(store: _Store) -> Any:
    """Log a retrained model over ``model.cbm`` of the relogged run, byte count unchanged.

    The same trees under another scale and bias: different bytes and predictions
    in a file of exactly the same size on every platform.
    """
    retrained = store.models["relogged_trained"].copy()
    retrained.set_scale_and_bias(15.0, 5.0)
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


def _image_distributions(config: dict[str, Any], artifacts: dict[str, Path]) -> set[str]:
    from haute.deploy._config import ContainerConfig
    from haute.deploy._container import runtime_versions
    from tests._deploy_helpers import make_resolved_deploy

    resolved = make_resolved_deploy(
        pipeline_file=Path("main.py"),
        model_name="resolution",
        target="container",
        container=ContainerConfig(base_image="python:3.11.9-slim"),
        pruned_graph=_graph(config),
        artifacts=artifacts,
        input_schema={"x0": "Float64", "x1": "Float64"},
        output_schema={"prediction": "Float64"},
    )
    return {pin.partition("==")[0] for pin in runtime_versions(resolved)}


def _listed(client: Any, store: _Store) -> dict[str, list[str]]:
    response = client.get(f"/api/mlflow/runs?experiment_id={store.experiment_id}&max_results=100")
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

        distributions = _image_distributions(_config(source), bundled)
        assert engine in distributions
        assert other_engine not in distributions
        assert "mlflow" not in distributions

    def test_a_model_file_in_the_project_gives_the_image_its_engine(self, store: _Store) -> None:
        from haute.deploy._bundler import collect_artifacts

        # With the run and registered cases above, the dependency list of every source type.
        store.models["contracted"].save_model(str(store.root / "file_model.cbm"))
        config = {"source_type": "file", "model_path": "file_model.cbm", "task": "regression"}
        bundled = collect_artifacts(_graph(config), ["src"], store.root, project_root=store.root)
        assert sorted(bundled) == ["ms__file_model.cbm"]
        distributions = _image_distributions(config, bundled)
        assert "catboost" in distributions
        assert {"lightgbm", "mlflow"}.isdisjoint(distributions)

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
                execute_trace(
                    graph, row_index=0, target_node_id="ms", column="prediction"
                ).output_value
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

    def test_the_preview_shows_a_refusal_in_its_words(self, store: _Store) -> None:
        """The refusal reaches the editor as the node's setting error (HTTP 422)."""
        from haute.errors import NodeConfigError
        from haute.executor import _preview_cache, execute_graph

        data = store.root / "data.parquet"
        pl.from_pandas(store.frame.head(3)).write_parquet(data)
        _preview_cache.clear()
        graph = _graph(_config(_run_source(store.runs["pair"])), data)

        with pytest.raises(NodeConfigError) as refused:
            execute_graph(graph, target_node_id="ms")

        assert refused.value.setting == "model"
        assert "Model Score 'ms' cannot load its model" in str(refused.value)
        assert "holds several models: frequency.cbm, severity.lgbm" in str(refused.value)

    def test_one_preview_and_one_trace_each_resolve_a_reference_once(
        self, store: _Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute import _mlflow_model
        from haute.executor import _preview_cache, execute_graph
        from haute.trace import _cache as trace_cache
        from haute.trace import execute_trace

        data = store.root / "data.parquet"
        pl.from_pandas(store.frame.head(3)).write_parquet(data)
        graph = _graph(_config(_run_source(store.runs["relogged"], "model.cbm")), data)
        _preview_cache.clear()
        trace_cache.clear()
        resolutions: list[Any] = []
        resolve = _mlflow_model._resolve

        def counting(source: Any, backend: Any) -> Any:
            resolutions.append(source)
            return resolve(source, backend)

        monkeypatch.setattr(_mlflow_model, "_resolve", counting)

        execute_graph(graph, target_node_id="ms")
        assert len(resolutions) == 1
        execute_trace(graph, row_index=0, target_node_id="ms", column="prediction")
        assert len(resolutions) == 2

    def test_a_store_that_fails_once_fails_the_whole_preview(
        self, store: _Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure stands for the operation: no load in it reads the model."""
        from haute import _mlflow_model
        from haute.executor import _preview_cache, execute_graph

        data = store.root / "data.parquet"
        pl.from_pandas(store.frame.head(3)).write_parquet(data)
        graph = _graph(_config(_run_source(store.runs["relogged"], "model.cbm")), data)
        _preview_cache.clear()
        resolve = _mlflow_model._resolve
        failures = [OSError("the tracking server is unreachable")]

        def flaky(source: Any, backend: Any) -> Any:
            if failures:
                raise failures.pop()
            return resolve(source, backend)

        monkeypatch.setattr(_mlflow_model, "_resolve", flaky)

        # The identity read failed, so the preview's load raises that failure
        # rather than asking again and scoring under an identity it never read.
        with pytest.raises(OSError, match="unreachable"):
            execute_graph(graph, target_node_id="ms")
        assert failures == []
        recovered = execute_graph(graph, target_node_id="ms")["ms"]
        assert recovered.status == "ok"
        expected = store.models["relogged"].predict(store.frame.head(3))
        np.testing.assert_allclose([row["prediction"] for row in recovered.preview], expected)

    def test_a_disk_cache_only_load_reads_the_record_and_never_the_store(
        self, store: _Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._mlflow_io import (
            _model_cache,
            disk_cache_only_model_loads,
            disk_cached_run_model,
        )
        from haute.errors import ModelNotInDiskCacheError

        source = _run_source(store.runs["contracted"], "glm.cbm")
        head = store.frame.head(3)
        expected = store.models["contracted"].predict(head)
        np.testing.assert_allclose(_scores(source, head), expected, rtol=1e-6)
        cached = disk_cached_run_model(
            run_id=store.runs["contracted"], artifact_path="glm.cbm", destination=""
        )
        assert cached is not None and cached.present() and cached.contract_path is not None

        def no_store(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("a disk-cache-only load asked the store")

        monkeypatch.setattr("haute._mlflow_utils.pinned_client", no_store)
        monkeypatch.setattr("mlflow.artifacts.download_artifacts", no_store)
        _model_cache.clear()
        with disk_cache_only_model_loads():
            np.testing.assert_allclose(_scores(source, head), expected, rtol=1e-6)
            _model_cache.clear()
            cached.model_path.unlink()
            with pytest.raises(ModelNotInDiskCacheError):
                _scores(source, head)
            # A copy gone between the record's check and the read is refused, not fetched.
            import mlflow

            from haute._mlflow_io import _resolve_artifact_local
            from haute._mlflow_model import StoredArtifact
            from haute._mlflow_utils import resolve_backend

            stored = StoredArtifact("glm.cbm", "size:1")  # an identity no copy is kept under
            with pytest.raises(ModelNotInDiskCacheError, match="no longer holds 'glm.cbm'"):
                _resolve_artifact_local(
                    mlflow, resolve_backend(""), store.runs["contracted"], stored
                )

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

        # Each identity keeps its copy, and the record names the one the run holds now.
        from haute._mlflow_io import disk_cached_run_model

        cached = disk_cached_run_model(
            run_id=store.runs["relogged"], artifact_path="model.cbm", destination=""
        )
        assert cached is not None
        copies = [path for path in cached.model_path.parent.parent.iterdir() if path.is_dir()]
        assert len(copies) == 2 and cached.model_path.parent in copies
        assert cached.model_path.read_bytes() == _stored_bytes(store, "relogged", "model.cbm")

    def test_bytes_that_come_back_are_scored_with_their_own_copies(
        self, store: _Store, tmp_path: Path
    ) -> None:
        """A, then B, then A again, memory never cleared: A's copies and record return."""
        from haute._mlflow_io import disk_cache_only_model_loads, disk_cached_run_model
        from haute._model_source import load_bound_model
        from haute.modelling._feature_contract import build_contract, save_contract

        run_id = store.client.create_run(store.experiment_id).info.run_id
        head = store.frame.head(3)
        models = {}
        for name, scale, target in (("a", 6.0, "y"), ("b", 60.0, "z")):
            folder = tmp_path / name
            folder.mkdir()
            models[name] = _catboost(store.frame, scale)
            models[name].save_model(str(folder / "model.cbm"))
            save_contract(
                build_contract(
                    features=_FEATURES,
                    feature_types={"x0": "Float64", "x1": "Float64"},
                    categorical_features=[],
                    target_name=target,
                    target_type="Float64",
                    task="regression",
                ),
                folder / "model.feature_contract.json",
            )

        def log(name: str) -> None:
            for file in ("model.cbm", "model.feature_contract.json"):
                store.client.log_artifact(run_id, str(tmp_path / name / file))

        source = _run_source(run_id, "model.cbm")
        for name in ("a", "b", "a"):
            log(name)
            bound = load_bound_model(source, "regression")
            assert bound.contract_path is not None and Path(bound.contract_path).is_file()
            np.testing.assert_allclose(_scores(source, head), models[name].predict(head))

        cached = disk_cached_run_model(run_id=run_id, artifact_path="model.cbm", destination="")
        assert cached is not None and cached.present()
        assert cached.model_path.read_bytes() == (tmp_path / "a" / "model.cbm").read_bytes()
        with disk_cache_only_model_loads():
            np.testing.assert_allclose(_scores(source, head), models["a"].predict(head))

    def test_a_warm_hit_for_one_answer_leaves_another_loads_copies(
        self, store: _Store, tmp_path: Path
    ) -> None:
        """An operation still holding answer A takes its hit after B loaded: B keeps its copies."""
        from haute._mlflow_io import disk_cached_run_model, load_resolved_model
        from haute._mlflow_model import resolve_model
        from haute._mlflow_utils import resolve_backend
        from haute._model_source import load_bound_model
        from haute.modelling._feature_contract import build_contract, save_contract

        run_id = store.client.create_run(store.experiment_id).info.run_id
        for name, scale, target in (("a", 6.0, "y"), ("b", 60.0, "z")):
            folder = tmp_path / name
            folder.mkdir()
            _catboost(store.frame, scale).save_model(str(folder / "model.cbm"))
            save_contract(
                build_contract(
                    features=_FEATURES,
                    feature_types={"x0": "Float64", "x1": "Float64"},
                    categorical_features=[],
                    target_name=target,
                    target_type="Float64",
                    task="regression",
                ),
                folder / "model.feature_contract.json",
            )
        source = _run_source(run_id, "model.cbm")

        def log(name: str) -> None:
            for file in ("model.cbm", "model.feature_contract.json"):
                store.client.log_artifact(run_id, str(tmp_path / name / file))

        log("a")
        answer_a = resolve_model(source, backend=resolve_backend(""))
        model_a = load_resolved_model(answer_a, "regression")
        log("b")
        bound_b = load_bound_model(source, "regression")
        copies_b = disk_cached_run_model(run_id=run_id, artifact_path="model.cbm", destination="")
        assert copies_b is not None and copies_b.contract_path is not None

        assert load_resolved_model(answer_a, "regression") is model_a
        assert copies_b.model_path.is_file() and copies_b.contract_path.is_file()
        assert bound_b.contract_path is not None and Path(bound_b.contract_path).is_file()
        assert model_a.run_contract_path is not None and Path(model_a.run_contract_path).is_file()


# ---------------------------------------------------------------------------
# The discovery rule, a version's source and the refusals, against a fake store
# ---------------------------------------------------------------------------


def _remote() -> Any:
    from haute._mlflow_utils import ResolvedBackend

    return ResolvedBackend(
        mode="server",
        tracking_uri="http://tracking.invalid",
        registry_uri="http://tracking.invalid",
        identity="server:http://tracking.invalid|registry=http://tracking.invalid",
        digest="0123456789abcdef",
    )


class _FakeRegistry:
    def __init__(self, client: Any, versions: dict[str, tuple[str | None, str]]) -> None:
        self.client = client
        self.versions = versions

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def get_model_version(self, name: str, version: str) -> Any:
        from types import SimpleNamespace

        run_id, source = self.versions[version]
        return SimpleNamespace(run_id=run_id, source=source)


def _resolve(monkeypatch: pytest.MonkeyPatch, client: Any, source: Any) -> Any:
    from haute._mlflow_model import resolve_model

    monkeypatch.setattr("haute._mlflow_utils.pinned_client", lambda backend: client)
    return resolve_model(source, backend=_remote())


class TestTheDiscoveryRule:
    @pytest.mark.parametrize(
        ("files", "candidates"),
        [
            # A top-level file answers before anything one folder down.
            ({"a.cbm": 1, "sub/b.cbm": 2, "pf/MLmodel": 3}, ("a.cbm",)),
            # Several at the answering level are all candidates, in listing order.
            ({"a.cbm": 1, "b.rsglm": 2}, ("a.cbm", "b.rsglm")),
            # One folder down, never inside a pyfunc folder.
            ({"sub/b.cbm": 2, "pf/MLmodel": 3, "pf/inner.cbm": 4}, ("sub/b.cbm",)),
            # A pyfunc folder only when no model file sits at either level.
            ({"pf/MLmodel": 3, "pf/model.cb": 4, "notes.txt": 5}, ("pf",)),
            # Two folders down is not searched; other files are not models.
            ({"a/b/c.cbm": 1, "feature_contract.json": 2}, ()),
        ],
    )
    def test_the_level_that_holds_a_model_answers(
        self, files: dict[str, int], candidates: tuple[str, ...]
    ) -> None:
        from haute._mlflow_model import run_model_candidates
        from tests._mlflow_fakes import FakeArtifactClient

        assert run_model_candidates(FakeArtifactClient({"r": files}), "r") == candidates

    def test_a_run_holding_its_model_at_the_top_costs_one_listing(self) -> None:
        from haute._mlflow_model import run_model_candidates
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient(
            {"r": {"m.cbm": 1, "evaluation/plan.json": 2, "shap/values.json": 3}}
        )
        run_model_candidates(client, "r")
        assert client.listings == [("r", "")]


class TestAVersionResolvesThroughItsSource:
    _RUN = {"r": {"frequency.cbm": 10, "severity.cbm": 20, "severity.feature_contract.json": 5}}

    @pytest.mark.parametrize(
        "source",
        [
            "runs:/r/severity.cbm",
            "mlflow-artifacts:/0/r/artifacts/severity.cbm",
        ],
    )
    def test_a_source_naming_a_file_resolves_to_it(
        self, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        from tests._mlflow_fakes import FakeArtifactClient

        client = _FakeRegistry(FakeArtifactClient(self._RUN), {"1": ("r", source)})
        resolved = _resolve(monkeypatch, client, _registered_source("sev", "1"))
        assert (resolved.run_id, resolved.artifact.path, resolved.version) == (
            "r",
            "severity.cbm",
            "1",
        )
        assert resolved.artifact.identity == "size:20"
        assert resolved.contract is not None
        assert resolved.contract.identity == "size:5"

    @pytest.mark.parametrize(
        "source", ["runs:/r", "mlflow-artifacts:/0/r/artifacts", "models:/m-0123"]
    )
    def test_a_source_naming_the_run_or_a_logged_model_searches_the_run(
        self, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = _FakeRegistry(FakeArtifactClient(self._RUN), {"1": ("r", source)})
        with pytest.raises(ModelResolutionError) as refused:
            _resolve(monkeypatch, client, _registered_source("sev", "1"))
        message = str(refused.value)
        assert "MLflow run 'r' (version 1 of registered model 'sev'" in message
        assert "holds several models: frequency.cbm, severity.cbm" in message
        assert "Register the model from its file." in message
        assert ("registered from a logged model" in message) is source.startswith("models:/")
        assert refused.value.context["candidates"] == ["frequency.cbm", "severity.cbm"]

    def test_a_version_with_no_run_or_a_source_outside_it_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = _FakeRegistry(
            FakeArtifactClient(self._RUN),
            {"1": (None, "s3://bucket/model.cbm"), "2": ("r", "s3://secret-bucket/x.cbm")},
        )
        with pytest.raises(ModelResolutionError, match="has no run"):
            _resolve(monkeypatch, client, _registered_source("sev", "1"))
        with pytest.raises(ModelResolutionError, match="outside its run's artifacts") as outside:
            _resolve(monkeypatch, client, _registered_source("sev", "2"))
        assert "secret-bucket" not in str(outside.value)


class TestARunReferenceIsRefusedByName:
    def test_an_artifact_the_run_does_not_hold_names_what_it_holds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"model.cbm": 1}})
        with pytest.raises(
            ModelResolutionError, match=r"holds no artifact 'other\.cbm'\. It holds model\.cbm\."
        ):
            _resolve(monkeypatch, client, _run_source("r", "other.cbm"))

    def test_a_folder_without_mlmodel_is_not_a_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"model/notes.txt": 1}})
        with pytest.raises(ModelResolutionError, match="is a folder without an MLmodel file"):
            _resolve(monkeypatch, client, _run_source("r", "model"))

    def test_a_run_with_no_model_names_the_files_haute_scores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"readme.txt": 1}})
        no_model = r"holds no model Haute can score\. Expected"
        with pytest.raises(ModelResolutionError, match=no_model):
            _resolve(monkeypatch, client, _run_source("r"))

    def test_a_store_that_reports_no_size_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"model.cbm": None}})
        with pytest.raises(ModelResolutionError, match="reports no size"):
            _resolve(monkeypatch, client, _run_source("r", "model.cbm"))

    @pytest.mark.parametrize("folder", ["model.v1", "model.cbm"])
    def test_a_pyfunc_folder_whatever_its_name_is_discovered_and_resolved(
        self, monkeypatch: pytest.MonkeyPatch, folder: str
    ) -> None:
        """What the path holds decides its family, never its name's suffix."""
        from haute._mlflow_model import run_model_candidates
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {f"{folder}/MLmodel": 1, f"{folder}/model.pkl": 2}})
        assert run_model_candidates(client, "r") == (folder,)
        for path in ("", folder):
            resolved = _resolve(monkeypatch, client, _run_source("r", path))
            assert (resolved.artifact.path, resolved.family.flavor) == (folder, "pyfunc")
            assert resolved.contract is None

    def test_a_file_with_a_suffix_no_family_loads_is_refused_naming_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.errors import ConfigError
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"model.v1": 1}})
        with pytest.raises(ConfigError, match="is not a model file Haute can score") as refused:
            _resolve(monkeypatch, client, _run_source("r", "model.v1"))
        assert ".cbm" in refused.value.context["supported_suffixes"]

    def test_a_pyfunc_folders_identity_is_its_listing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"model/MLmodel": 1, "model/model.cb": 2}})
        first = _resolve(monkeypatch, client, _run_source("r"))
        client.runs["r"]["model/model.cb"] = 3
        second = _resolve(monkeypatch, client, _run_source("r"))
        assert first.family.flavor == second.family.flavor == "pyfunc"
        assert first.contract is None
        assert first.artifact.identity.startswith("listing:")
        assert first.artifact.identity != second.artifact.identity


class TestOneResolutionPerOperation:
    def test_a_scope_answers_a_reference_once_failure_included(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._mlflow_model import model_resolution_scope
        from haute.errors import ModelResolutionError
        from tests._mlflow_fakes import FakeArtifactClient

        client = FakeArtifactClient({"r": {"model.cbm": 1}, "empty": {}})
        with model_resolution_scope():
            first = _resolve(monkeypatch, client, _run_source("r", "model.cbm"))
            with model_resolution_scope():
                assert _resolve(monkeypatch, client, _run_source("r", "model.cbm")) is first
            with pytest.raises(ModelResolutionError, match="holds no model"):
                _resolve(monkeypatch, client, _run_source("empty"))
            client.runs["empty"]["model.cbm"] = 1
            # The failure stands for the operation, though the run now holds a model.
            with pytest.raises(ModelResolutionError, match="holds no model"):
                _resolve(monkeypatch, client, _run_source("empty"))
        assert client.listings == [("r", ""), ("empty", "")]

        client.runs["r"]["model.cbm"] = 2
        outside = _resolve(monkeypatch, client, _run_source("r", "model.cbm"))
        assert outside.artifact.identity == "size:2"


# ---------------------------------------------------------------------------
# A deployed pipeline reads only its bundle: an optimiser result from MLflow too
# ---------------------------------------------------------------------------


def _optimiser_graph(config: dict[str, Any]) -> Any:
    from tests.conftest import make_graph

    return make_graph(
        {
            "nodes": [
                {"id": "src", "data": {"label": "src", "nodeType": "apiInput", "config": {}}},
                {
                    "id": "opt",
                    "data": {"label": "opt", "nodeType": "optimiserApply", "config": config},
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "opt", "sourceHandle": "src"}],
        }
    )


class TestAnOptimiserResultFromMlflowIsBundled:
    def _logged_result(self, store: _Store, tmp_path: Path) -> tuple[str, bytes]:
        run_id = store.client.create_run(store.experiment_id).info.run_id
        result = tmp_path / "logged" / "optimiser_result.json"
        result.parent.mkdir()
        result.write_text('{"version": "rb_v7", "mode": "ratebook"}', encoding="utf-8")
        store.client.log_artifact(run_id, str(result))
        return run_id, result.read_bytes()

    def test_a_run_source_is_bundled_and_served_as_its_file(
        self, store: _Store, tmp_path: Path
    ) -> None:
        from contextlib import ExitStack

        from haute.deploy._bundler import collect_artifacts

        run_id, logged = self._logged_result(store, tmp_path)
        config = {
            "source_type": "run",
            "run_id": run_id,
            "experiment_id": store.experiment_id,
            "optimised_value_column": "selected",
        }
        served: dict[str, dict[str, Any]] = {}
        with ExitStack() as resources:
            artifacts = collect_artifacts(
                _optimiser_graph(config),
                ["src"],
                store.root,
                project_root=store.root,
                resources=resources,
                served_configs=served,
            )
            assert sorted(artifacts) == ["opt__optimiser_result.json"]
            assert artifacts["opt__optimiser_result.json"].read_bytes() == logged
        assert served == {
            "opt": {
                "source_type": "file",
                "artifact_path": "optimiser_result.json",
                "optimised_value_column": "selected",
            }
        }

    def test_a_registered_source_records_the_version_it_bundled(
        self, store: _Store, tmp_path: Path
    ) -> None:
        from contextlib import ExitStack

        from haute.deploy._bundler import collect_artifacts

        run_id, logged = self._logged_result(store, tmp_path)
        store.client.create_registered_model("pricing_opt")
        location = store.client.get_run(run_id).info.artifact_uri
        store.client.create_model_version("pricing_opt", location, run_id)
        store.client.set_registered_model_alias("pricing_opt", "champion", "1")
        config = {
            "source_type": "registered",
            "registered_model": "pricing_opt",
            "alias": "champion",
        }
        sources: dict[str, dict[str, Any]] = {}
        with ExitStack() as resources:
            artifacts = collect_artifacts(
                _optimiser_graph(config),
                ["src"],
                store.root,
                project_root=store.root,
                resources=resources,
                model_sources=sources,
            )
            assert artifacts["opt__optimiser_result.json"].read_bytes() == logged
        assert sources == {
            "opt": {
                "registered_model": "pricing_opt",
                "alias": "champion",
                "version": "1",
                "run_id": run_id,
            }
        }

    def test_an_unfilled_source_bundles_nothing_and_needs_resources_otherwise(
        self, store: _Store, tmp_path: Path
    ) -> None:
        from haute.deploy._bundler import collect_artifacts

        unfilled = _optimiser_graph({"source_type": "run", "run_id": ""})
        assert collect_artifacts(unfilled, ["src"], store.root, project_root=store.root) == {}

        run_id, _logged = self._logged_result(store, tmp_path)
        chosen = _optimiser_graph({"source_type": "run", "run_id": run_id})
        with pytest.raises(ValueError, match="needs the resolution's resources"):
            collect_artifacts(chosen, ["src"], store.root, project_root=store.root)


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
        for path in source_files(_SRC)
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
