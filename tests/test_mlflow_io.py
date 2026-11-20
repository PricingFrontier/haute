"""Tests for haute._mlflow_io — MLflow model loading and caching.

A load asks the model resolution which model a reference means, then loads
that answer. The loading tests serve the resolution a fake tracking store
(``tests._mlflow_fakes.FakeArtifactClient``, listing each run's files with their
sizes) and serve the downloads the bytes it lists; the helper tests build the
resolution's answer, a ``ResolvedModel``, by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import (
    _MODEL_CACHE_MAX_SIZE,
    ScoringModel,
    _append_classification_proba,
    _artifact_cache_path,
    _disk_cache_root,
    _load_rustystats_model,
    _model_cache,
    _model_cache_key,
    _prepare_predict_frame,
    _wrap_catboost,
    _wrap_pyfunc,
    load_local_model,
    load_mlflow_model,
)
from haute._mlflow_model import ResolvedModel, StoredArtifact, resolve_model
from haute._mlflow_utils import ResolvedBackend
from haute._model_flavors import family_for_artifact, model_families
from haute._model_scorer import _cleanup_registered_temp_files
from haute._model_source import RegisteredModelSource, RunModelSource
from tests._mlflow_fakes import FakeArtifactClient
from tests._project import use_project


def _signature(*names: str) -> object:
    """A real MLflow signature with required double inputs *names*."""
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, Schema

    return ModelSignature(inputs=Schema([ColSpec("double", name) for name in names]))


# The backend ``serve_runs`` pins, and the one the destination-free helper
# tests below thread through explicitly.
_FAKE_BACKEND = ResolvedBackend(
    mode="local",
    tracking_uri="file:///mlruns",
    registry_uri="file:///mlruns",
    identity="local:mlruns|registry=file:///mlruns",
    digest="0123456789abcdef",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the model cache before/after each test."""
    _model_cache.clear()
    yield
    _model_cache.clear()


class _RunStore:
    """A tracking store's runs: listed by a fake client, downloaded as the bytes it lists.

    ``files`` holds each run's files; the client lists their sizes as they were
    when the store was made, so a test can serve other bytes than it lists.
    """

    def __init__(self, runs: dict[str, dict[str, bytes]]) -> None:
        self.files = {run_id: dict(files) for run_id, files in runs.items()}
        self.client = FakeArtifactClient(
            {
                run_id: {path: len(payload) for path, payload in files.items()}
                for run_id, files in runs.items()
            }
        )
        self.downloads: list[str] = []

    def download_artifacts(
        self,
        artifact_uri: str,
        dst_path: str | None = None,
        *,
        tracking_uri: str | None = None,
        **_kwargs: Any,
    ) -> str:
        assert tracking_uri == _FAKE_BACKEND.tracking_uri
        assert dst_path is not None
        self.downloads.append(artifact_uri)
        run_id, _, path = artifact_uri.removeprefix("runs:/").partition("/")
        out = Path(dst_path) / PurePosixPath(path).name
        out.write_bytes(self.files[run_id][path])
        return str(out)


@pytest.fixture()
def serve_runs(monkeypatch: pytest.MonkeyPatch):
    """Make a store holding the given runs the one every load in the test resolves against."""

    def serve(runs: dict[str, dict[str, bytes]]) -> _RunStore:
        store = _RunStore(runs)
        monkeypatch.setattr(
            "haute._mlflow_utils.resolve_backend",
            lambda destination="", project_root=None: _FAKE_BACKEND,
        )
        monkeypatch.setattr("haute._mlflow_utils.pinned_client", lambda backend: store.client)
        monkeypatch.setattr("mlflow.artifacts.download_artifacts", store.download_artifacts)
        return store

    return serve


class _FakeRegistry:
    """A store's client with registered versions, each ``(run_id, source)``."""

    def __init__(self, client: Any, versions: dict[str, tuple[str, str]]) -> None:
        self.client = client
        self.versions = versions

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def get_model_version(self, name: str, version: str) -> Any:
        run_id, source = self.versions[version]
        return SimpleNamespace(run_id=run_id, source=source)


def _run_source(run_id: str, artifact_path: str = "") -> RunModelSource:
    return RunModelSource(run_id=run_id, artifact_path=artifact_path, mlflow_destination="")


def _resolved(
    run_id: str,
    path: str,
    payload: bytes = b"",
    *,
    contract: StoredArtifact | None = None,
) -> ResolvedModel:
    """The resolution's answer for *path* of a run, its bytes being *payload*."""
    return ResolvedModel(
        backend=_FAKE_BACKEND,
        run_id=run_id,
        artifact=StoredArtifact(path, f"size:{len(payload)}"),
        family=family_for_artifact(path),
        contract=contract,
        version="",
        reference=f"MLflow run {run_id!r}",
    )


def _transport(files: dict[str, bytes | BaseException], downloads: list[str]) -> Any:
    """An ``mlflow`` module whose downloads serve *files* by run-relative path."""

    def download(artifact_uri: str, dst_path: str, *, tracking_uri: str) -> str:
        downloads.append(artifact_uri)
        path = artifact_uri.removeprefix("runs:/").partition("/")[2]
        payload = files[path]
        if isinstance(payload, BaseException):
            raise payload
        out = Path(dst_path) / PurePosixPath(path).name
        out.write_bytes(payload)
        return str(out)

    return SimpleNamespace(artifacts=SimpleNamespace(download_artifacts=download))


def _fake_catboost(*features: str) -> MagicMock:
    fake_model = MagicMock()
    fake_model.feature_names_ = list(features)
    fake_model.get_cat_feature_indices.return_value = []
    return fake_model


# ---------------------------------------------------------------------------
# load_mlflow_model — run-based
# ---------------------------------------------------------------------------


class TestLoadRunBasedModel:
    def test_load_run_based_model(self, serve_runs):
        """Run-based loading downloads artifacts and returns ScoringModel."""
        store = serve_runs({"abc123": {"model.cbm": b"model bytes"}})
        fake_model = _fake_catboost("a", "b")

        with patch("haute._mlflow_io._load_catboost_model", return_value=fake_model) as load:
            result = load_mlflow_model(_run_source("abc123", "model.cbm"), task="regression")

        assert isinstance(result, ScoringModel)
        assert result.raw_model is fake_model
        assert result.flavor == "catboost"
        assert result.feature_names == ["a", "b"]
        assert store.downloads == ["runs:/abc123/model.cbm"]
        # The loader read the bytes the run holds.
        assert Path(load.call_args.args[0]).read_bytes() == b"model bytes"

    def test_run_artifact_identifier_rejects_traversal_before_resolution(self, serve_runs):
        """MLflow identifiers stay external but cannot contain traversal."""
        store = serve_runs({"abc123": {"model.cbm": b"model bytes"}})

        with pytest.raises(ValueError, match="Invalid artifact_path"):
            load_mlflow_model(_run_source("abc123", "../model"), task="regression")

        # Refused before the store was asked anything.
        assert store.client.listings == []
        assert store.downloads == []


# ---------------------------------------------------------------------------
# load_mlflow_model — registered
# ---------------------------------------------------------------------------


class TestLoadRegisteredModel:
    def test_load_registered_model(self, serve_runs, monkeypatch):
        """A registered version loads the file its source names in its run."""
        store = serve_runs(
            {"resolved_run_id": {"model.cbm": b"model bytes", "other.cbm": b"other bytes"}}
        )
        registry = _FakeRegistry(
            store.client, {"2": ("resolved_run_id", "runs:/resolved_run_id/model.cbm")}
        )
        monkeypatch.setattr("haute._mlflow_utils.pinned_client", lambda backend: registry)
        fake_model = _fake_catboost("x")

        with patch("haute._mlflow_io._load_catboost_model", return_value=fake_model):
            result = load_mlflow_model(
                RegisteredModelSource(
                    registered_model="my-model", version="2", alias="", mlflow_destination=""
                ),
                task="regression",
            )

        assert isinstance(result, ScoringModel)
        assert result.raw_model is fake_model
        assert store.downloads == ["runs:/resolved_run_id/model.cbm"]


# ---------------------------------------------------------------------------
# load_mlflow_model — pyfunc auto-detection
# ---------------------------------------------------------------------------

_PYFUNC_FOLDER = {"model/MLmodel": b"flavors: {}", "model/python_model.pkl": b"pickle"}


class TestPyfuncAutoDetect:
    def test_non_cbm_artifact_uses_pyfunc(self, serve_runs):
        """An artifact path naming a folder with an MLmodel file loads via pyfunc."""
        fake_pyfunc = MagicMock()
        fake_pyfunc.metadata.signature = _signature("f1", "f2")
        store = serve_runs({"abc123": _PYFUNC_FOLDER})

        with patch("haute._mlflow_io._load_pyfunc_model", return_value=fake_pyfunc) as load:
            result = load_mlflow_model(_run_source("abc123", "model"), task="regression")

        assert isinstance(result, ScoringModel)
        assert result.flavor == "pyfunc"
        assert result.feature_names == ["f1", "f2"]
        assert result.cat_feature_names == frozenset()
        assert load.call_args.args[1:] == ("abc123", "model")
        assert load.call_args.kwargs == {"backend": _FAKE_BACKEND}
        # A pyfunc folder loads through MLflow; nothing is copied into the disk cache.
        assert store.downloads == []

    def test_auto_discover_falls_back_to_pyfunc(self, serve_runs):
        """With no model file and no artifact_path, the run's pyfunc folder is found."""
        fake_pyfunc = MagicMock()
        fake_pyfunc.metadata.signature = _signature("a")
        serve_runs(
            {
                "abc123": {
                    "custom_model/MLmodel": b"flavors: {}",
                    "custom_model/python_model.pkl": b"pickle",
                    "notes.txt": b"notes",
                }
            }
        )

        with patch("haute._mlflow_io._load_pyfunc_model", return_value=fake_pyfunc) as load:
            result = load_mlflow_model(_run_source("abc123"), task="regression")

        assert result.flavor == "pyfunc"
        assert load.call_args.args[1:] == ("abc123", "custom_model")


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestModelCache:
    def test_cache_hit(self, serve_runs):
        """A hit whose copies are on disk returns the cached model without a download.

        A hit whose copies are gone fetches them once (the cached model names its
        contract's copy, and a disk-cache-only load reads the record), and loads nothing.
        """
        import mlflow

        from haute._mlflow_io import fetch_resolved_files

        store = serve_runs({"abc123": {"model.cbm": b"model bytes"}})
        source = _run_source("abc123", "model.cbm")
        fake_sm = ScoringModel(MagicMock(), ["a"], frozenset(), "catboost")
        resolved = resolve_model(source, backend=_FAKE_BACKEND)
        _model_cache.put(_model_cache_key(resolved, "regression"), fake_sm)

        with patch("haute._mlflow_io._load_catboost_file") as load:
            assert load_mlflow_model(source, task="regression") is fake_sm
            assert store.downloads == ["runs:/abc123/model.cbm"]
            fetch_resolved_files(mlflow, resolved)
            assert load_mlflow_model(source, task="regression") is fake_sm
        assert store.downloads == ["runs:/abc123/model.cbm"]
        load.assert_not_called()

    def test_cache_lru_eviction(self, serve_runs):
        """Exceeding max cache size evicts oldest entry."""
        for i in range(_MODEL_CACHE_MAX_SIZE + 2):
            _model_cache.put(("run", f"run_{i}", f"art_{i}", "regression", ""), MagicMock())

        serve_runs({"new_run": {"model.cbm": b"model bytes"}})
        source = _run_source("new_run", "model.cbm")

        with patch("haute._mlflow_io._load_catboost_model", return_value=_fake_catboost("a")):
            load_mlflow_model(source, task="regression")

        expected_key = _model_cache_key(resolve_model(source, backend=_FAKE_BACKEND), "regression")
        assert expected_key in _model_cache
        assert ("run", "run_0", "art_0", "regression", "") not in _model_cache

    def test_a_warm_entry_is_served_after_one_listing(self, serve_runs):
        """Every load asks the store which model the reference means, once."""
        store = serve_runs({"abc123": {"model.cbm": b"model bytes"}})
        source = _run_source("abc123", "model.cbm")

        with patch("haute._mlflow_io._load_catboost_model", return_value=_fake_catboost("a")):
            first = load_mlflow_model(source, task="regression")
            second = load_mlflow_model(source, task="regression")

        assert second is first
        assert store.client.listings == [("abc123", ""), ("abc123", "")]
        assert store.downloads == ["runs:/abc123/model.cbm"]


# ---------------------------------------------------------------------------
# _load_catboost_model
# ---------------------------------------------------------------------------


class TestLoadCatboostModel:
    def test_regression_loads_regressor(self):
        """task=regression uses CatBoostRegressor."""
        from haute._mlflow_io import _load_catboost_model

        mock_model = MagicMock()
        mock_cls = MagicMock(return_value=mock_model)
        mock_catboost = MagicMock(CatBoostRegressor=mock_cls, CatBoostClassifier=MagicMock())
        with patch.dict(sys.modules, {"catboost": mock_catboost}):
            result = _load_catboost_model("/tmp/model.cbm", "regression")
        mock_model.load_model.assert_called_once_with("/tmp/model.cbm")
        assert result is mock_model

    def test_classification_loads_classifier(self):
        """task=classification uses CatBoostClassifier."""
        from haute._mlflow_io import _load_catboost_model

        mock_model = MagicMock()
        mock_cls = MagicMock(return_value=mock_model)
        mock_catboost = MagicMock(CatBoostClassifier=mock_cls, CatBoostRegressor=MagicMock())
        with patch.dict(sys.modules, {"catboost": mock_catboost}):
            result = _load_catboost_model("/tmp/model.cbm", "classification")
        mock_model.load_model.assert_called_once_with("/tmp/model.cbm")
        assert result is mock_model


# ---------------------------------------------------------------------------
# _wrap_catboost / _wrap_pyfunc
# ---------------------------------------------------------------------------


class TestWrappers:
    def test_wrap_catboost(self):
        """_wrap_catboost extracts feature names and cat features."""
        model = MagicMock()
        model.feature_names_ = ["a", "b", "c"]
        model.get_cat_feature_indices.return_value = [2]

        sm = _wrap_catboost(model, source="test model")
        assert sm.flavor == "catboost"
        assert sm.feature_names == ["a", "b", "c"]
        assert sm.cat_feature_names == frozenset({"c"})
        assert sm.raw_model is model

    def test_wrap_catboost_no_cat_features(self):
        """_wrap_catboost with no cat feature indices."""
        model = MagicMock()
        model.feature_names_ = ["x", "y"]
        model.get_cat_feature_indices.return_value = []

        sm = _wrap_catboost(model, source="test model")
        assert sm.cat_feature_names == frozenset()

    def test_wrap_pyfunc_no_signature(self):
        """_wrap_pyfunc with no signature returns empty feature list."""
        model = MagicMock()
        model.metadata = None

        sm = _wrap_pyfunc(model, task="regression")
        assert sm.feature_names == []
        assert sm.optional_feature_names == frozenset()


# ---------------------------------------------------------------------------
# MLflow not installed
# ---------------------------------------------------------------------------


class TestMlflowNotInstalled:
    def test_import_error_message(self, monkeypatch):
        """Raises ImportError with pip install instruction when mlflow missing."""
        # Destination resolution runs first; keep it on Local (which needs no
        # mlflow import) so the friendly error from the resolution is what surfaces.
        for var in (
            "MLFLOW_TRACKING_URI",
            "DATABRICKS_HOST",
            "DATABRICKS_TOKEN",
            "DATABRICKS_MLFLOW_HOST",
            "DATABRICKS_MLFLOW_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)
        # Both entries, so an ``mlflow.tracking`` an earlier test imported is gone too.
        with patch.dict(sys.modules, {"mlflow": None, "mlflow.tracking": None}):
            with pytest.raises(ImportError, match="pip install mlflow"):
                load_mlflow_model(_run_source("x"), task="regression")


# ---------------------------------------------------------------------------
# Task validation
# ---------------------------------------------------------------------------


class TestTaskValidation:
    def test_invalid_task_raises(self):
        """Invalid task value raises ValueError before any MLflow calls."""
        with patch("haute._mlflow_utils.pinned_client") as client:
            with pytest.raises(ValueError, match="Invalid task"):
                load_mlflow_model(_run_source("x"), task="clustering")
        client.assert_not_called()

    def test_empty_task_raises(self):
        """Empty task string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid task"):
            load_mlflow_model(_run_source("x"), task="")

    def test_a_resolved_model_is_loaded_only_for_a_valid_task(self):
        from haute._mlflow_io import load_resolved_model

        with pytest.raises(ValueError, match="Invalid task"):
            load_resolved_model(_resolved("x", "model.cbm", b"m"), "clustering")


# ---------------------------------------------------------------------------
# _prepare_predict_frame — real data, no mocks
# ---------------------------------------------------------------------------


class TestPreparePredictFrame:
    def test_numeric_only_returns_numpy(self):
        """All-numeric features return numpy array (catboost flavor, no cats)."""
        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 2)

    def test_numeric_nulls_become_nan(self):
        """Null values in numeric columns become NaN after Float32 cast."""
        df = pl.DataFrame({"x": [1.0, None, 3.0]})
        result = _prepare_predict_frame(df, ["x"], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)
        assert np.isnan(result[1, 0]), "Null should become NaN"
        assert result[0, 0] == pytest.approx(1.0, abs=0.01)

    def test_categorical_nulls_become_sentinel(self):
        """Null values in categorical columns filled with '_MISSING_'."""
        df = pl.DataFrame({"cat": ["a", None, "b"]})
        result = _prepare_predict_frame(df, ["cat"], frozenset({"cat"}), "catboost")
        import pandas as pd

        assert isinstance(result, pd.DataFrame)
        assert result.iloc[1, 0] == "_MISSING_"

    def test_mixed_numeric_and_categorical(self):
        """Mixed features: numeric→float32, categorical→sentinel+Categorical."""
        df = pl.DataFrame(
            {
                "num": [1.0, None, 3.0],
                "cat": ["x", None, "y"],
            }
        )
        result = _prepare_predict_frame(
            df,
            ["num", "cat"],
            frozenset({"cat"}),
            "catboost",
        )
        import pandas as pd

        assert isinstance(result, pd.DataFrame)
        assert np.isnan(result["num"].iloc[1])
        assert result["cat"].iloc[1] == "_MISSING_"

    def test_no_cat_features_returns_numpy(self):
        """No cat_feature_names treats all features as numeric."""
        df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        result = _prepare_predict_frame(df, ["a"], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)

    def test_feature_order_preserved(self):
        """Output columns match the requested feature order."""
        df = pl.DataFrame({"b": [10.0, 20.0], "a": [1.0, 2.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "catboost")
        assert result[0, 0] == pytest.approx(1.0, abs=0.01)
        assert result[0, 1] == pytest.approx(10.0, abs=0.01)

    def test_pyfunc_no_cats_returns_named_pandas(self):
        """Pyfunc flavor ALWAYS receives a named pandas DataFrame (4a.1).

        MLflow pyfunc models with named-column signatures (the standard
        ``infer_signature`` case) reject unnamed numpy input outright:
        ``MlflowException: Model is missing inputs ['a', 'b']. Note that
        there were extra inputs: [0, 1]`` — reproduced against real
        mlflow 3.10 in ``test_mlflow_io_real_pyfunc.py``.  The numpy
        fast-path is therefore catboost-no-categoricals ONLY.
        """
        import pandas as pd

        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["a", "b"]

    def test_pyfunc_numeric_dtypes_preserved(self):
        """Pyfunc numerics keep their native dtype — no Float32 downcast (4a.6).

        MLflow silently upcasts float32 back to float64 for a ``double``
        signature, so a preemptive Float32 cast loses mantissa bits
        without any error.  ``float64`` input must stay ``float64``.
        """
        import pandas as pd

        precise = 1.0 + 2.0**-40  # not representable in float32
        df = pl.DataFrame({"a": [precise], "b": pl.Series("b", [7], dtype=pl.Int64)})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        assert isinstance(result, pd.DataFrame)
        assert result["a"].dtype == np.float64
        assert result["a"].iloc[0] == precise, "float64 value must survive bit-exact"
        assert result["b"].dtype == np.int64

    def test_pyfunc_numeric_nulls_become_nan_in_pandas(self):
        """Pyfunc float nulls surface as NaN in the named pandas frame."""
        import pandas as pd

        df = pl.DataFrame({"x": [1.5, None, 3.5]})
        result = _prepare_predict_frame(df, ["x"], frozenset(), "pyfunc")
        assert isinstance(result, pd.DataFrame)
        assert result["x"].dtype == np.float64
        assert np.isnan(result["x"].iloc[1])
        assert result["x"].iloc[0] == 1.5

    def test_pyfunc_int_nulls_become_float64_nan(self):
        """Int64-with-null arrives as float64 NaN via the Arrow conversion.

        Pins the documented Arrow→pandas widening so a future conversion
        change (e.g. pandas nullable Int64) is caught here rather than
        surfacing as an opaque pyfunc enforcement error.
        """
        df = pl.DataFrame({"n": pl.Series("n", [1, None, 3], dtype=pl.Int64)})
        result = _prepare_predict_frame(df, ["n"], frozenset(), "pyfunc")
        assert result["n"].dtype == np.float64
        assert np.isnan(result["n"].iloc[1])
        assert result["n"].iloc[0] == 1.0

    def test_pyfunc_with_cats_returns_pandas(self):
        """Pyfunc flavor with categoricals still returns pandas (dtype roundtrip),
        and its numeric columns keep their native dtype (no Float32 cast)."""
        df = pl.DataFrame({"a": [1.0, 2.0], "b": ["x", "y"]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset({"b"}), "pyfunc")
        import pandas as pd

        assert isinstance(result, pd.DataFrame)
        assert result["a"].dtype == np.float64
        assert isinstance(result["b"].dtype, pd.CategoricalDtype)

    def test_catboost_numeric_fast_path_pinned_float32_numpy(self):
        """CatBoost-no-categoricals keeps the numpy Float32 fast path EXACTLY.

        4a.1/4a.6 change the pyfunc branch only; the correctly-typed
        CatBoost path must not move (CatBoost computes in float32
        internally — the cast is its documented input contract here).
        """
        df = pl.DataFrame({"a": [1.0, None], "b": pl.Series("b", [3, 4], dtype=pl.Int64)})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert np.isnan(result[1, 0])

    def test_unknown_flavor_raises_loudly(self):
        """An unrecognised flavor must raise, never fall through silently.

        Pre-fix the function silently routed unknown flavors through the
        catboost-shaped branch (Float32 + numpy) — a typo'd flavor would
        score with the wrong input contract.
        """
        df = pl.DataFrame({"a": [1.0]})
        with pytest.raises(ValueError, match="flavor"):
            _prepare_predict_frame(df, ["a"], frozenset(), "tensorflow")


# ---------------------------------------------------------------------------
# Which model a run means — the model resolution's discovery rule
# ---------------------------------------------------------------------------
# tests/test_model_resolution.py pins the rule's levels and refusals; these pin
# each family's file, a file one folder down, a native file over a pyfunc
# folder, the refusal's wording and that a store failure is never read as
# "no model".

_FILE_FAMILIES = [family for family in model_families() if family.suffixes]


class _FailingClient(FakeArtifactClient):
    """A store whose listings fail, as an expired credential or a missing root does."""

    def __init__(self, failure: BaseException) -> None:
        super().__init__({})
        self.failure = failure

    def list_artifacts(self, run_id: str, path: str | None = None) -> list[Any]:
        raise self.failure


def _resolve(monkeypatch, client: Any, source: RunModelSource) -> ResolvedModel:
    monkeypatch.setattr("haute._mlflow_utils.pinned_client", lambda backend: client)
    return resolve_model(source, backend=_FAKE_BACKEND)


class TestTheRunDiscoveryRule:
    @pytest.mark.parametrize("family", _FILE_FAMILIES, ids=lambda family: family.flavor)
    def test_each_family_file_at_the_top_resolves_to_its_family(self, family, monkeypatch):
        path = f"model{family.suffixes[0]}"
        client = FakeArtifactClient({"run1": {path: 3, "notes.txt": 2}})

        resolved = _resolve(monkeypatch, client, _run_source("run1"))

        assert (resolved.artifact.path, resolved.family.flavor) == (path, family.flavor)
        assert resolved.artifact.identity == "size:3"

    def test_a_file_one_folder_down_is_found(self, monkeypatch):
        client = FakeArtifactClient({"run1": {"artifacts/glm.rsglm": 4, "notes.txt": 2}})

        resolved = _resolve(monkeypatch, client, _run_source("run1"))

        assert (resolved.artifact.path, resolved.family.flavor) == (
            "artifacts/glm.rsglm",
            "rustystats",
        )

    def test_a_native_file_beats_a_pyfunc_folder_in_one_listing(self, monkeypatch):
        """Runs with both native .rsglm and MLflow model/ load via RustyStats."""
        client = FakeArtifactClient(
            {"run1": {"model/MLmodel": 1, "model/model.pkl": 2, "conversion.rsglm": 3}}
        )

        resolved = _resolve(monkeypatch, client, _run_source("run1"))

        assert (resolved.artifact.path, resolved.family.flavor) == (
            "conversion.rsglm",
            "rustystats",
        )
        assert client.listings == [("run1", "")]

    def test_a_pyfunc_folder_of_any_name_is_found_by_its_mlmodel_file(self, monkeypatch):
        client = FakeArtifactClient({"run1": {"custom_model/MLmodel": 1, "readme.txt": 2}})

        resolved = _resolve(monkeypatch, client, _run_source("run1"))

        assert (resolved.artifact.path, resolved.family.flavor) == ("custom_model", "pyfunc")
        assert resolved.contract is None

    def test_no_model_is_refused_naming_every_file_haute_scores(self, monkeypatch):
        from haute.errors import ConfigError, ModelResolutionError

        client = FakeArtifactClient({"run1": {"readme.txt": 1}})

        with pytest.raises(ModelResolutionError, match="holds no model Haute can score") as exc:
            _resolve(monkeypatch, client, _run_source("run1"))

        # A refusal of the configuration, which callers handle as any ConfigError.
        assert isinstance(exc.value, ConfigError)
        for family in _FILE_FAMILIES:
            for suffix in family.suffixes:
                assert suffix in str(exc.value)
        assert "MLflow pyfunc model directory" in str(exc.value)
        assert exc.value.context["candidates"] == []

    @pytest.mark.parametrize("artifact_path", ["", "model.cbm"], ids=["discovered", "named"])
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param("mlflow", id="mlflow-exception"),
            pytest.param("filesystem", id="bare-file-not-found"),
        ],
    )
    def test_a_store_failure_propagates_rather_than_reading_as_no_model(
        self, monkeypatch, artifact_path, failure
    ):
        """A credential / network failure, or a bare ``FileNotFoundError`` from a
        local-fs shim, must surface as itself, never as "no model in this run" or
        "no contract beside the model"."""
        from mlflow.exceptions import MlflowException

        error: BaseException = (
            MlflowException("PERMISSION_DENIED: Invalid credentials")
            if failure == "mlflow"
            else FileNotFoundError("Artifact root /mnt/nonexistent/runs/abc does not exist")
        )

        with pytest.raises(type(error), match=r"PERMISSION_DENIED|Artifact root"):
            _resolve(monkeypatch, _FailingClient(error), _run_source("run1", artifact_path))


# ---------------------------------------------------------------------------
# _append_classification_proba (D9 refactor)
# ---------------------------------------------------------------------------


class TestAppendClassificationProba:
    """Tests for the unified classification probability helper."""

    def test_2d_proba_extracts_column_1(self):
        """2-D probability array extracts the positive class (column 1)."""
        df = pl.DataFrame({"x": [1, 2, 3]})
        model = MagicMock()
        model.predict_proba.return_value = np.array(
            [
                [0.8, 0.2],
                [0.3, 0.7],
                [0.5, 0.5],
            ]
        )
        sm = ScoringModel(model, ["x"], frozenset(), "catboost")
        result = _append_classification_proba(df, sm, np.array([[1], [2], [3]]), "pred")
        assert "pred_proba" in result.columns
        expected = [0.2, 0.7, 0.5]
        actual = result["pred_proba"].to_list()
        for a, e in zip(actual, expected):
            assert a == pytest.approx(e)

    def test_1d_proba_used_directly(self):
        """1-D probability array is used as-is."""
        df = pl.DataFrame({"x": [1, 2]})
        model = MagicMock()
        model.predict_proba.return_value = np.array([0.3, 0.9])
        sm = ScoringModel(model, ["x"], frozenset(), "catboost")
        result = _append_classification_proba(df, sm, np.array([[1], [2]]), "pred")
        assert "pred_proba" in result.columns
        actual = result["pred_proba"].to_list()
        assert actual[0] == pytest.approx(0.3)
        assert actual[1] == pytest.approx(0.9)

    def test_no_predict_proba_returns_unchanged(self):
        """Models without predict_proba return the DataFrame unchanged."""
        df = pl.DataFrame({"x": [1, 2]})
        model = MagicMock(spec=[])  # no predict_proba
        sm = ScoringModel(model, ["x"], frozenset(), "pyfunc")
        result = _append_classification_proba(df, sm, np.array([[1], [2]]), "pred")
        assert "pred_proba" not in result.columns
        assert result.equals(df)

    def test_custom_output_col_name(self):
        """Proba column is named ``<output_col>_proba``."""
        df = pl.DataFrame({"x": [1]})
        model = MagicMock()
        model.predict_proba.return_value = np.array([[0.4, 0.6]])
        sm = ScoringModel(model, ["x"], frozenset(), "catboost")
        result = _append_classification_proba(df, sm, np.array([[1]]), "my_score")
        assert "my_score_proba" in result.columns

    def test_regression_should_not_call_this(self):
        """Verify the helper is only called for classification (by caller convention)."""
        # This test validates the contract: the helper itself doesn't check task,
        # it just appends probas. Callers gate on task == "classification".
        df = pl.DataFrame({"x": [1]})
        model = MagicMock()
        model.predict_proba.return_value = np.array([0.5])
        sm = ScoringModel(model, ["x"], frozenset(), "catboost")
        # Even if called, it appends the column — which is correct behavior.
        result = _append_classification_proba(df, sm, np.array([[1]]), "pred")
        assert "pred_proba" in result.columns


# ---------------------------------------------------------------------------
# 4a.5 — predict_proba output shapes: the single ``<col>_proba`` column is
# the BINARY positive-class probability.  Multiclass output mislabeled as
# binary is a wrong-price hazard; (n, 1) output crashed the batch path
# while the eager path handled it ([:, 0]) — a silent eager/batch split.
# ---------------------------------------------------------------------------


class _SingleColumnProbaModel:
    """A model whose ``predict_proba`` emits only the positive column.

    Some wrappers (sigmoid-output nets, custom carriers) return shape
    ``(n, 1)`` instead of the sklearn-style ``(n, 2)``.
    """

    def __init__(self, proba: float = 0.42) -> None:
        self.proba = proba

    def predict(self, x_data):
        return np.zeros(len(np.asarray(x_data)))

    def predict_proba(self, x_data):
        return np.full((len(np.asarray(x_data)), 1), self.proba)

    def get_metadata(self):
        return {}


def _train_real_catboost_classifier(n_classes: int, seed: int = 7):
    """Fit a tiny REAL CatBoostClassifier with ``n_classes`` classes.

    Returns ``(model, features_frame)`` — deterministic (fixed seeds,
    single-threaded-safe sizes) and fast (8 trees of depth 2).
    """
    from catboost import CatBoostClassifier

    rng = np.random.RandomState(seed)
    n = 120
    x1 = rng.randn(n)
    x2 = rng.randn(n)
    if n_classes == 2:
        y = (x1 + x2 > 0.0).astype(int)
    else:
        y = np.where(x1 + x2 > 0.7, 2, np.where(x1 - x2 > 0.0, 1, 0))
    assert len(np.unique(y)) == n_classes
    model = CatBoostClassifier(
        iterations=8,
        depth=2,
        verbose=0,
        allow_writing_files=False,
        random_seed=0,
    )
    model.fit(np.column_stack([x1, x2]), y)
    return model, pl.DataFrame({"x1": x1[:6], "x2": x2[:6]})


class TestAppendClassificationProbaShapes:
    """4a.5 — shape contract for the shared batch-path proba helper."""

    def test_single_column_proba_uses_column_0(self):
        """(n, 1) output is already the positive-class vector → column 0.

        Pre-fix this raised ``IndexError: index 1 is out of bounds`` while
        the eager path returned column 0 — eager/batch divergence.
        """
        df = pl.DataFrame({"x": [1.0, 2.0]})
        sm = ScoringModel(_SingleColumnProbaModel(0.42), ["x"], frozenset(), "catboost")
        result = _append_classification_proba(df, sm, df.to_numpy(), "pred")
        assert result["pred_proba"].to_list() == pytest.approx([0.42, 0.42])

    def test_multiclass_proba_raises_named_error(self):
        """A REAL 3-class CatBoost must fail loudly, not emit P(class index 1).

        Pre-fix the helper emitted ``probas[:, 1]`` — the probability of
        whichever class sits at index 1 — silently labeled as the binary
        positive-class probability.  Repro: a row predicted class 2 with
        P=0.75 reported ``pred_proba`` = 0.20.
        """
        model, score_df = _train_real_catboost_classifier(n_classes=3)
        sm = ScoringModel(model, ["x1", "x2"], frozenset(), "catboost")
        x_data = _prepare_predict_frame(score_df, ["x1", "x2"], frozenset(), "catboost")
        with pytest.raises(ValueError, match=r"3 classes") as exc_info:
            _append_classification_proba(score_df, sm, x_data, "pred")
        message = str(exc_info.value)
        assert "pred_proba" in message, "error must name the output column"
        assert "binary" in message, "error must explain the binary-only contract"

    def test_binary_proba_unchanged_with_real_model(self):
        """A REAL binary CatBoost keeps the established [:, 1] convention."""
        model, score_df = _train_real_catboost_classifier(n_classes=2)
        sm = ScoringModel(model, ["x1", "x2"], frozenset(), "catboost")
        x_data = _prepare_predict_frame(score_df, ["x1", "x2"], frozenset(), "catboost")
        result = _append_classification_proba(score_df, sm, x_data, "pred")
        expected = model.predict_proba(x_data)[:, 1]
        np.testing.assert_array_equal(result["pred_proba"].to_numpy(), expected)

    def test_zero_width_proba_raises(self):
        """(n, 0) output is degenerate — fail loud, never emit garbage."""
        df = pl.DataFrame({"x": [1.0]})
        model = MagicMock()
        model.predict_proba.return_value = np.empty((1, 0))
        sm = ScoringModel(model, ["x"], frozenset(), "catboost")
        with pytest.raises(ValueError, match="predict_proba"):
            _append_classification_proba(df, sm, df.to_numpy(), "pred")

    def test_3d_proba_raises(self):
        """ndim > 2 output is unsupported — fail loud, never flatten garbage."""
        df = pl.DataFrame({"x": [1.0]})
        model = MagicMock()
        model.predict_proba.return_value = np.zeros((1, 2, 2))
        sm = ScoringModel(model, ["x"], frozenset(), "catboost")
        with pytest.raises(ValueError, match="predict_proba"):
            _append_classification_proba(df, sm, df.to_numpy(), "pred")


class TestEagerBatchProbaAgreement:
    """4a.5 — the eager and batch scoring paths must emit identical probas.

    Both paths are driven with REAL models (or plain deterministic Python
    classes) end-to-end: ``_score_eager`` (eager) vs
    ``_batch_score_to_parquet`` (batch).  Any labeling drift between the
    two would mean preview and production disagree on a price input.
    """

    def _batch_score(self, sm, score_df, tmp_path, output_col="pred"):

        from haute._model_scorer import _batch_score_to_parquet

        input_path = str(tmp_path / "agreement_input.parquet")
        score_df.write_parquet(input_path)
        out_path = _batch_score_to_parquet(
            sm,
            input_path,
            list(score_df.columns),
            output_col,
            "classification",
        )
        try:
            return pl.read_parquet(out_path)
        finally:
            _cleanup_registered_temp_files([out_path])

    def test_binary_catboost_eager_and_batch_probas_identical(self, tmp_path):
        """Real binary CatBoost: eager and batch proba columns are bit-equal."""
        model, score_df = _train_real_catboost_classifier(n_classes=2)
        sm = ScoringModel(model, ["x1", "x2"], frozenset(), "catboost")

        from haute._mlflow_io import _score_eager

        eager = _score_eager(sm, score_df.lazy(), ["x1", "x2"], "pred", "classification").collect()
        batch = self._batch_score(sm, score_df, tmp_path)

        assert "pred_proba" in eager.columns
        assert "pred_proba" in batch.columns
        np.testing.assert_array_equal(
            eager["pred_proba"].to_numpy(),
            batch["pred_proba"].to_numpy(),
            err_msg="eager and batch positive-class probabilities diverged",
        )
        np.testing.assert_array_equal(
            eager["pred"].to_numpy(),
            batch["pred"].to_numpy(),
        )

    def test_single_column_proba_eager_and_batch_agree(self, tmp_path):
        """(n, 1) proba output: both paths emit the single column as-is.

        Pre-fix the batch path raised IndexError where eager succeeded.
        """
        sm = ScoringModel(_SingleColumnProbaModel(0.42), ["x"], frozenset(), "catboost")
        score_df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})

        from haute._mlflow_io import _score_eager

        eager = _score_eager(sm, score_df.lazy(), ["x"], "pred", "classification").collect()
        batch = self._batch_score(sm, score_df, tmp_path)

        assert eager["pred_proba"].to_list() == pytest.approx([0.42, 0.42, 0.42])
        np.testing.assert_array_equal(
            eager["pred_proba"].to_numpy(),
            batch["pred_proba"].to_numpy(),
        )

    def test_multiclass_batch_scoring_raises_end_to_end(self, tmp_path):
        """The full batch surface refuses multiclass proba labeling loudly."""
        model, score_df = _train_real_catboost_classifier(n_classes=3)
        sm = ScoringModel(model, ["x1", "x2"], frozenset(), "catboost")
        with pytest.raises(ValueError, match="3 classes"):
            self._batch_score(sm, score_df, tmp_path)

    def test_multiclass_eager_scoring_raises_end_to_end(self):
        """The full eager surface refuses multiclass proba labeling loudly.

        Pre-fix the eager path silently emitted ``probas[:, 1]`` for a REAL
        3-class CatBoost — the exact mislabeling the batch path already
        rejects (a row predicted class 2 with P=0.75 reported 0.20).
        """
        model, score_df = _train_real_catboost_classifier(n_classes=3)
        sm = ScoringModel(model, ["x1", "x2"], frozenset(), "catboost")

        from haute._mlflow_io import _score_eager

        with pytest.raises(ValueError, match="3 classes") as exc_info:
            _score_eager(sm, score_df.lazy(), ["x1", "x2"], "pred", "classification").collect()
        message = str(exc_info.value)
        assert "pred_proba" in message, "error must name the output column"
        assert "binary" in message, "error must explain the binary-only contract"

    def test_multiclass_eager_and_batch_raise_the_same_error(self, tmp_path):
        """Eager and batch reject a 3-class model with the IDENTICAL message.

        Both surfaces share one shape dispatch
        (``_mlflow_io._positive_class_proba_vector``); byte-equal messages
        pin that sharing against future drift back to duplicated logic.
        """
        model, score_df = _train_real_catboost_classifier(n_classes=3)
        sm = ScoringModel(model, ["x1", "x2"], frozenset(), "catboost")

        from haute._mlflow_io import _score_eager

        with pytest.raises(ValueError) as eager_exc:
            _score_eager(sm, score_df.lazy(), ["x1", "x2"], "pred", "classification").collect()
        with pytest.raises(ValueError) as batch_exc:
            self._batch_score(sm, score_df, tmp_path)
        assert str(eager_exc.value) == str(batch_exc.value)


# ---------------------------------------------------------------------------
# T9: _load_rustystats_model
# ---------------------------------------------------------------------------


def _patch_rustystats(mock_model):
    """Return ``(mock_rs, sys.modules patch)`` for a rustystats GLMModel mock.

    Reduces boilerplate across the rustystats loader tests: callers
    configure ``mock_model.required_columns`` (or omit it to test the
    missing-attribute path) and use the returned context manager to
    install the mocked ``rustystats`` module.
    """
    mock_rs = MagicMock()
    # A real exception class: the loader catches RustyStats' schema refusal.
    mock_rs.exceptions.ValidationError = type("ValidationError", (Exception,), {})
    mock_model.terms_dict = {}
    mock_rs.GLMModel.from_bytes.return_value = mock_model
    return mock_rs, patch.dict(sys.modules, {"rustystats": mock_rs})


def _write_rsglm(tmp_path, contents=b"fake_bytes"):
    """Write a fake ``.rsglm`` file and return its path as a string."""
    model_file = tmp_path / "model.rsglm"
    model_file.write_bytes(contents)
    return str(model_file)


class TestLoadRustystatsModel:
    def test_model_from_an_older_schema_is_a_config_error(self, tmp_path):
        """A GLM pickled before the RustyStats 0.9 schema asks to be retrained."""
        import pickle

        from haute.errors import ConfigError

        path = _write_rsglm(tmp_path, pickle.dumps({"result_state": {}}))
        with pytest.raises(ConfigError, match=r"from 'model.rsglm' was saved") as raised:
            _load_rustystats_model(path)
        # The file name alone, never the cache directory it sits in.
        assert str(tmp_path) not in str(raised.value)
        assert "Retrain it" in str(raised.value)

        # Loaded from MLflow, the error names the run instead.
        with pytest.raises(ConfigError, match=r"from MLflow run 'abc123' was saved"):
            load_local_model(path, source="MLflow run 'abc123'")

    def test_encoding_aliases_resolve_to_unique_raw_columns(self, tmp_path):
        mock_model = MagicMock()
        mock_model.required_columns = ["region_fe", "region", "age", "offset", "complement"]
        _, mods = _patch_rustystats(mock_model)
        mock_model.terms_dict = {
            "region_fe": {"type": "frequency_encoding", "variable": "region"},
            "region": {"type": "target_encoding"},
            "age_sq": {"type": "expression", "expr": "age ** 2"},
        }
        with mods:
            sm = _load_rustystats_model(_write_rsglm(tmp_path))
        assert sm.feature_names == ["region", "age", "offset", "complement"]

    def test_wraps_model_with_required_columns(self, tmp_path):
        """Happy path: feature_names mirror model.required_columns; flavor is set."""
        model_path = _write_rsglm(tmp_path)
        mock_model = MagicMock()
        mock_model.required_columns = ["feat_a", "feat_b"]
        mock_rs, mods = _patch_rustystats(mock_model)

        with mods:
            sm = _load_rustystats_model(model_path)

        assert isinstance(sm, ScoringModel)
        assert sm.flavor == "rustystats"
        assert sm.feature_names == ["feat_a", "feat_b"]
        assert sm.cat_feature_names == frozenset()
        assert sm.raw_model is mock_model

    def test_required_columns_order_is_preserved(self, tmp_path):
        """Order matters: _validate_features compares relative position to
        training, so the loader must not sort or otherwise reshuffle."""
        mock_model = MagicMock()
        mock_model.required_columns = ["zeta", "alpha", "mu"]
        _, mods = _patch_rustystats(mock_model)

        with mods:
            sm = _load_rustystats_model(_write_rsglm(tmp_path))

        assert sm.feature_names == ["zeta", "alpha", "mu"]

    def test_empty_required_columns_yields_empty_feature_list(self, tmp_path):
        """Intercept-only models (``glm(y ~ 1)``) declare no input columns —
        the loader must accept this rather than treating empty as failure."""
        mock_model = MagicMock()
        mock_model.required_columns = []
        _, mods = _patch_rustystats(mock_model)

        with mods:
            sm = _load_rustystats_model(_write_rsglm(tmp_path))

        assert sm.feature_names == []
        assert sm.flavor == "rustystats"

    def test_feature_names_is_a_copy_of_required_columns(self, tmp_path):
        """Mutating ``ScoringModel.feature_names`` must not mutate the model.

        ``_load_rustystats_model`` wraps with ``list(...)`` so callers cannot
        clobber the underlying RustyStats artifact through the carrier.
        """
        original = ["a", "b", "c"]
        mock_model = MagicMock()
        mock_model.required_columns = original
        _, mods = _patch_rustystats(mock_model)

        with mods:
            sm = _load_rustystats_model(_write_rsglm(tmp_path))

        sm.feature_names.append("d")
        assert original == ["a", "b", "c"], "ScoringModel.feature_names must be a defensive copy"

    def test_accepts_iterable_required_columns(self, tmp_path):
        """``list(...)`` materialises any iterable — tuple, generator, etc.

        Pinning this contract prevents a regression where an upstream
        change in RustyStats returns a tuple or numpy array and silently
        breaks downstream code that assumes a list.
        """
        mock_model = MagicMock()
        mock_model.required_columns = ("a", "b")  # tuple, not list
        _, mods = _patch_rustystats(mock_model)

        with mods:
            sm = _load_rustystats_model(_write_rsglm(tmp_path))

        assert sm.feature_names == ["a", "b"]
        assert isinstance(sm.feature_names, list)

    def test_missing_required_columns_raises_attribute_error(self, tmp_path):
        """A model without ``required_columns`` must fail loudly.

        Direct attribute access — no ``hasattr`` fallback — so a stale or
        corrupt artifact surfaces immediately rather than being silently
        wrapped with an empty feature list that would pass scoring with
        zero columns.
        """
        mock_model = MagicMock(spec=[])  # no required_columns attribute
        _, mods = _patch_rustystats(mock_model)

        with mods:
            with pytest.raises(AttributeError):
                _load_rustystats_model(_write_rsglm(tmp_path))

    def test_reads_file_as_bytes(self, tmp_path):
        """``from_bytes`` is invoked once with the full file content."""
        payload = b"fake_model_bytes"
        mock_model = MagicMock()
        mock_model.required_columns = ["x"]
        mock_rs, mods = _patch_rustystats(mock_model)

        with mods:
            _load_rustystats_model(_write_rsglm(tmp_path, contents=payload))

        mock_rs.GLMModel.from_bytes.assert_called_once_with(payload)

    def test_from_bytes_failure_propagates(self, tmp_path):
        """A corrupt artifact (``from_bytes`` raising) must propagate.

        The bounded-retry path in ``load_mlflow_model`` relies on this
        exception bubbling up so it can delete the cached file and retry —
        a swallowed error would silently produce a malformed ScoringModel.
        """
        mock_rs = MagicMock()
        mock_rs.exceptions.ValidationError = type("ValidationError", (Exception,), {})
        mock_rs.GLMModel.from_bytes.side_effect = ValueError("corrupt artifact")

        with patch.dict(sys.modules, {"rustystats": mock_rs}):
            with pytest.raises(ValueError, match="corrupt artifact"):
                _load_rustystats_model(_write_rsglm(tmp_path))

    def test_real_rustystats_model_exposes_required_columns(self, tmp_path):
        """Contract test: an actual ``rustystats.GLMModel`` saved + reloaded
        through this loader must produce a ScoringModel whose feature_names
        match ``model.required_columns`` and contain raw (not design-matrix)
        column names.

        This pins the upstream API surface haute relies on, so a future
        rustystats version that renames or removes ``required_columns``
        breaks this test instead of silently failing in production.
        """
        import rustystats as rs

        rng = np.random.default_rng(42)
        train_df = pl.DataFrame(
            {
                "y": rng.standard_normal(64),
                "age": rng.standard_normal(64),
                "region": ["a", "b", "c", "a"] * 16,
            }
        )
        model = rs.glm_dict(
            response="y",
            terms={
                "age": {"type": "linear"},
                "region": {"type": "categorical"},
            },
            data=train_df,
            family="gaussian",
            intercept=True,
        ).fit()

        artifact = tmp_path / "real.rsglm"
        artifact.write_bytes(model.to_bytes())

        sm = _load_rustystats_model(str(artifact))

        assert sm.flavor == "rustystats"
        assert sm.cat_feature_names == frozenset()
        # The loader must surface raw input columns — never the post-encoding
        # design-matrix names like "region[T.b]" or "ns(age, 1/3)".
        assert set(sm.feature_names) == {"age", "region"}
        # And must match what RustyStats itself reports.
        assert sm.feature_names == list(model.required_columns)


# ---------------------------------------------------------------------------
# T9: load_local_model for .rsglm and edge cases
# ---------------------------------------------------------------------------


class TestLoadLocalModel:
    """Tests for load_local_model dispatching by file extension."""

    def test_cbm_dispatches_to_catboost(self):
        """'.cbm' extension dispatches to CatBoost loader."""
        mock_model = MagicMock()
        mock_model.feature_names_ = ["a"]
        mock_model.get_cat_feature_indices.return_value = []

        mock_catboost = MagicMock()
        mock_catboost.CatBoostRegressor.return_value = mock_model
        with patch.dict(sys.modules, {"catboost": mock_catboost}):
            sm = load_local_model("/tmp/model.cbm", task="regression")

        assert isinstance(sm, ScoringModel)
        assert sm.flavor == "catboost"

    def test_rsglm_dispatches_to_rustystats(self, tmp_path):
        """'.rsglm' extension dispatches to RustyStats loader.

        Asserts the full ScoringModel shape rather than just the flavor —
        a wrong dispatch would still return a ScoringModel with the
        flavor field set, so we also verify feature_names track
        ``required_columns`` and ``cat_feature_names`` is empty
        (rustystats handles cat encoding internally).
        """
        mock_model = MagicMock()
        mock_model.required_columns = ["x", "y"]
        _, mods = _patch_rustystats(mock_model)

        with mods:
            sm = load_local_model(_write_rsglm(tmp_path))

        assert isinstance(sm, ScoringModel)
        assert sm.flavor == "rustystats"
        assert sm.feature_names == ["x", "y"]
        assert sm.cat_feature_names == frozenset()
        assert sm.raw_model is mock_model

    def test_unsupported_extension_raises_config_error_naming_the_suffixes(self):
        """An unregistered suffix is refused by name, never guessed as a family."""
        from haute.errors import ConfigError

        with pytest.raises(
            ConfigError, match=r"\.cbm, \.rsglm, \.ubj, \.lgbm, \.ebm, \.tboost"
        ) as exc:
            load_local_model("/tmp/model.onnx")
        assert exc.value.context["supported_suffixes"] == [
            ".cbm",
            ".rsglm",
            ".ubj",
            ".lgbm",
            ".ebm",
            ".tboost",
        ]

    def test_pyfunc_directory_does_not_load_locally(self):
        """A suffix-less path names a pyfunc directory, which loads only from MLflow."""
        from haute.errors import ConfigError

        with pytest.raises(ConfigError, match="loads only from MLflow"):
            load_local_model("/tmp/model")

    def test_classification_task_forwarded_for_cbm(self):
        """task='classification' is forwarded to CatBoost loader."""
        mock_model = MagicMock()
        mock_model.feature_names_ = ["a"]
        mock_model.get_cat_feature_indices.return_value = []

        mock_catboost = MagicMock()
        mock_catboost.CatBoostClassifier.return_value = mock_model
        with patch.dict(sys.modules, {"catboost": mock_catboost}):
            sm = load_local_model("/tmp/model.cbm", task="classification")

        assert sm.flavor == "catboost"


# ---------------------------------------------------------------------------
# T9: _prepare_predict_frame with flavor="rustystats"
# ---------------------------------------------------------------------------


class TestPreparePredictFrameRustystats:
    """Tests for _prepare_predict_frame with rustystats flavor."""

    def test_returns_polars_dataframe(self):
        """RustyStats flavor returns the Polars DataFrame directly (no conversion)."""
        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "rustystats")
        assert isinstance(result, pl.DataFrame)

    def test_returns_only_feature_columns(self):
        """RustyStats should get only the feature columns, not extra columns."""
        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0], "target": [10.0, 20.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "rustystats")
        assert set(result.columns) == {"a", "b"}

    def test_nulls_not_processed(self):
        """RustyStats handles its own null preprocessing — nulls pass through."""
        df = pl.DataFrame({"a": [1.0, None, 3.0]})
        result = _prepare_predict_frame(df, ["a"], frozenset(), "rustystats")
        assert result["a"].null_count() == 1

    def test_categoricals_not_processed(self):
        """RustyStats handles its own categoricals — no sentinel fill."""
        df = pl.DataFrame({"cat": ["x", None, "y"]})
        result = _prepare_predict_frame(df, ["cat"], frozenset({"cat"}), "rustystats")
        # Should NOT have _MISSING_ sentinel — nulls pass through
        assert result["cat"].null_count() == 1


# ---------------------------------------------------------------------------
# ScoringModel direct usage
# ---------------------------------------------------------------------------


class TestScoringModelDirect:
    """Tests for ScoringModel.predict, predict_proba, __getattr__."""

    def test_predict_returns_flattened_array(self):
        """predict() flattens the raw model output."""
        raw = MagicMock()
        raw.predict.return_value = np.array([[1.0], [2.0], [3.0]])
        sm = ScoringModel(raw, ["a"], frozenset(), "catboost")
        result = sm.predict(np.array([[10], [20], [30]]))
        assert result.shape == (3,)
        assert list(result) == [1.0, 2.0, 3.0]

    def test_predict_proba_returns_array(self):
        """predict_proba() returns array from underlying model."""
        raw = MagicMock()
        raw.predict_proba.return_value = np.array([[0.3, 0.7], [0.6, 0.4]])
        sm = ScoringModel(raw, ["a"], frozenset(), "catboost")
        result = sm.predict_proba(np.array([[10], [20]]))
        assert result.shape == (2, 2)

    def test_predict_proba_returns_none_when_not_supported(self):
        """predict_proba() returns None when model lacks the method."""
        raw = MagicMock(spec=[])  # no predict_proba
        sm = ScoringModel(raw, ["a"], frozenset(), "pyfunc")
        assert sm.predict_proba(np.array([[1]])) is None


# ---------------------------------------------------------------------------
# _extract_pyfunc_features — edge cases (lines 189, 193)
# ---------------------------------------------------------------------------


class TestExtractPyfuncFeatures:
    """Tests for _extract_pyfunc_features edge cases."""

    def test_missing_metadata_attribute_fails_loudly(self):
        from haute._mlflow_io import _extract_pyfunc_features

        class ModelWithoutMetadata:
            pass

        with pytest.raises(AttributeError, match="metadata"):
            _extract_pyfunc_features(ModelWithoutMetadata())

    def test_no_signature(self):
        """No signature declares no inputs."""
        from haute._mlflow_io import _extract_pyfunc_features

        model = MagicMock()
        model.metadata = None
        assert _extract_pyfunc_features(model) == ([], frozenset())

    def test_inputs_is_none(self):
        from haute._mlflow_io import _extract_pyfunc_features

        model = MagicMock()
        model.metadata.signature.inputs = None
        assert _extract_pyfunc_features(model) == ([], frozenset())

    def test_optional_inputs_are_separated_from_required_ones(self):
        """An input the signature marks optional is read only where present."""
        from mlflow.models import ModelSignature
        from mlflow.types import ColSpec, Schema

        from haute._mlflow_io import _extract_pyfunc_features

        model = MagicMock()
        model.metadata.signature = ModelSignature(
            inputs=Schema(
                [
                    ColSpec("double", "x"),
                    ColSpec("double", "exposure", required=False),
                    ColSpec("string", "y"),
                ]
            )
        )
        assert _extract_pyfunc_features(model) == (["x", "y"], frozenset({"exposure"}))


# ---------------------------------------------------------------------------
# _resolve_artifact_local — the disk cache of one stored identity
# ---------------------------------------------------------------------------


class TestResolveArtifactLocal:
    """Tests for _resolve_artifact_local disk cache logic."""

    def test_cache_hit_returns_local_path(self, tmp_path):
        """Returns cached file path without downloading when file exists."""
        from haute._mlflow_io import _resolve_artifact_local

        use_project(tmp_path)
        stored = StoredArtifact("model.cbm", f"size:{len(b'cached_model_data')}")
        # Create the expected cache structure
        cached_file = _artifact_cache_path(
            tmp_path / ".cache" / "models",
            _FAKE_BACKEND.digest,
            "run123",
            "model.cbm",
            stored.token,
        )
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"cached_model_data")

        mock_mlflow = MagicMock()

        result = _resolve_artifact_local(mock_mlflow, _FAKE_BACKEND, "run123", stored)

        assert result == str(cached_file)
        mock_mlflow.artifacts.download_artifacts.assert_not_called()

    def test_cache_miss_downloads_and_caches(self, tmp_path):
        """Downloads artifact and caches it on cache miss."""
        from haute._mlflow_io import _resolve_artifact_local

        use_project(tmp_path)
        downloads: list[str] = []
        transport = _transport({"model.cbm": b"fresh_model_data"}, downloads)
        stored = StoredArtifact("model.cbm", f"size:{len(b'fresh_model_data')}")

        result = _resolve_artifact_local(transport, _FAKE_BACKEND, "run456", stored)

        # File should be in cache dir now
        expected = _artifact_cache_path(
            tmp_path / ".cache" / "models",
            _FAKE_BACKEND.digest,
            "run456",
            "model.cbm",
            stored.token,
        )
        assert result == str(expected)
        assert expected.read_bytes() == b"fresh_model_data"
        assert downloads == ["runs:/run456/model.cbm"]

    def test_two_identities_of_one_path_are_two_copies(self, tmp_path):
        """A file logged again never overwrites the copy of the bytes it replaced."""
        from haute._mlflow_io import _resolve_artifact_local

        use_project(tmp_path)
        first = _resolve_artifact_local(
            _transport({"model.cbm": b"v1"}, []), _FAKE_BACKEND, "run1", _model_file(b"v1")
        )
        second = _resolve_artifact_local(
            _transport({"model.cbm": b"v2-x"}, []), _FAKE_BACKEND, "run1", _model_file(b"v2-x")
        )

        assert first != second
        assert Path(first).read_bytes() == b"v1"
        assert Path(second).read_bytes() == b"v2-x"

    def test_download_failure_cleans_up(self, tmp_path):
        """On download failure, partial cache entry is cleaned up."""
        from haute._mlflow_io import _resolve_artifact_local

        use_project(tmp_path)
        mock_mlflow = MagicMock()
        mock_mlflow.artifacts.download_artifacts.side_effect = RuntimeError("network error")
        stored = StoredArtifact("model.cbm", "size:4")

        with pytest.raises(RuntimeError, match="network error"):
            _resolve_artifact_local(mock_mlflow, _FAKE_BACKEND, "run789", stored)

        # No cached file should remain
        cache_path = _artifact_cache_path(
            tmp_path / ".cache" / "models",
            _FAKE_BACKEND.digest,
            "run789",
            "model.cbm",
            stored.token,
        )
        assert not cache_path.is_file()

    @pytest.mark.parametrize("kind", ["size", "content"])
    def test_a_download_that_is_not_the_stored_bytes_is_refused_and_kept_nowhere(
        self, tmp_path, kind
    ):
        """The copy must be the bytes the store described: its size, or on a local
        store its content. A file logged again after the listing is refused."""
        from haute._mlflow_io import _resolve_artifact_local
        from haute._mlflow_model import copy_identity
        from haute.errors import ModelResolutionError

        use_project(tmp_path)
        listed = tmp_path / "listed.cbm"
        listed.write_bytes(b"weights-v1")
        # The identity the store gave, taken from the bytes it listed.
        identity = copy_identity(StoredArtifact("model.cbm", f"{kind}:"), listed)
        stored = StoredArtifact("model.cbm", identity)
        # Logged again between the listing and the download: same size, or not.
        served = b"weights-v2" if kind == "content" else b"weights-v2-longer"

        with pytest.raises(ModelResolutionError, match="changed while it was loaded"):
            _resolve_artifact_local(
                _transport({"model.cbm": served}, []), _FAKE_BACKEND, "run-r", stored
            )

        cache_path = _artifact_cache_path(
            tmp_path / ".cache" / "models", _FAKE_BACKEND.digest, "run-r", "model.cbm", stored.token
        )
        assert not cache_path.exists()
        # The listed bytes themselves are accepted.
        accepted = _resolve_artifact_local(
            _transport({"model.cbm": b"weights-v1"}, []), _FAKE_BACKEND, "run-r", stored
        )
        assert Path(accepted).read_bytes() == b"weights-v1"

    def test_failure_after_cache_write_cleans_partial(self, tmp_path):
        """If an error occurs after the file is moved into cache, the partial file is deleted."""

        from haute._mlflow_io import _resolve_artifact_local

        use_project(tmp_path)
        transport = _transport({"model.cbm": b"data"}, [])
        stored = StoredArtifact("model.cbm", "size:4")

        # Simulate: download succeeds, shutil.move succeeds (file lands in cache),
        # but then logger.info raises during the stat call → triggers cleanup
        # Patch logger.info to raise on the "mlflow_artifact_cached" call
        # (which happens after shutil.move, so the file exists in cache)
        original_info = __import__("haute._mlflow_io", fromlist=["logger"]).logger.info

        def failing_info(msg, **kwargs):
            if msg == "mlflow_artifact_cached":
                raise OSError("simulated stat failure")
            return original_info(msg, **kwargs)

        with (
            patch("haute._mlflow_io.logger.info", side_effect=failing_info),
            pytest.raises(OSError, match="simulated stat failure"),
        ):
            _resolve_artifact_local(transport, _FAKE_BACKEND, "runX", stored)

        # The partial cache file should have been cleaned up
        cache_path = _artifact_cache_path(
            tmp_path / ".cache" / "models",
            _FAKE_BACKEND.digest,
            "runX",
            "model.cbm",
            stored.token,
        )
        assert not cache_path.is_file()

    def test_downloaded_file_not_found_nested(self, tmp_path):
        """Raises when download returns path that doesn't exist and fallback fails."""
        from haute._mlflow_io import _resolve_artifact_local

        use_project(tmp_path)
        mock_mlflow = MagicMock()
        # Return a path that doesn't exist as a file
        mock_mlflow.artifacts.download_artifacts.return_value = str(
            tmp_path / "nonexistent" / "dir"
        )

        with pytest.raises(FileNotFoundError, match="artifact not found"):
            _resolve_artifact_local(
                mock_mlflow, _FAKE_BACKEND, "run_bad", StoredArtifact("model.cbm", "size:4")
            )


def _model_file(payload: bytes) -> StoredArtifact:
    return StoredArtifact("model.cbm", f"size:{len(payload)}")


# ---------------------------------------------------------------------------
# clear_model_cache (lines 362-385)
# ---------------------------------------------------------------------------


class TestClearModelCache:
    """Tests for clear_model_cache disk + memory cache clearing."""

    def test_clear_all_caches(self, tmp_path):
        """Removes all cached model files and returns count."""
        from haute._mlflow_io import clear_model_cache

        cache_root = tmp_path / ".cache" / "models"
        # Run directories live under a backend-digest partition.
        run1_dir = cache_root / _FAKE_BACKEND.digest / "run1"
        run1_dir.mkdir(parents=True)
        (run1_dir / "model.cbm").write_bytes(b"data1")
        run2_dir = cache_root / _FAKE_BACKEND.digest / "run2"
        run2_dir.mkdir(parents=True)
        (run2_dir / "model.rsglm").write_bytes(b"data2")

        with patch("haute._mlflow_io.Path.cwd", return_value=tmp_path):
            removed = clear_model_cache()

        assert removed == 2
        assert not cache_root.exists()

    def test_clear_specific_run(self, tmp_path):
        """Clears only the specified run's cache."""
        from haute._mlflow_io import clear_model_cache

        cache_root = tmp_path / ".cache" / "models"
        # Run directories live under a backend-digest partition.
        run1_dir = cache_root / _FAKE_BACKEND.digest / "run1"
        run1_dir.mkdir(parents=True)
        (run1_dir / "model.cbm").write_bytes(b"data1")
        run2_dir = cache_root / _FAKE_BACKEND.digest / "run2"
        run2_dir.mkdir(parents=True)
        (run2_dir / "model.cbm").write_bytes(b"data2")

        with patch("haute._mlflow_io.Path.cwd", return_value=tmp_path):
            removed = clear_model_cache(run_id="run1")

        assert removed == 1
        assert not run1_dir.exists()
        assert run2_dir.exists()

    def test_clear_nonexistent_cache(self, tmp_path):
        """Returns 0 when no cache directory exists."""
        from haute._mlflow_io import clear_model_cache

        with patch("haute._mlflow_io.Path.cwd", return_value=tmp_path):
            removed = clear_model_cache()

        assert removed == 0

    def test_clear_invalid_run_id_raises(self, tmp_path):
        """Raises ValueError for path-traversal run IDs."""
        from haute._mlflow_io import clear_model_cache

        # Cache root must exist for validation to be reached
        (tmp_path / ".cache" / "models").mkdir(parents=True)

        with (
            patch("haute._mlflow_io.Path.cwd", return_value=tmp_path),
            pytest.raises(ValueError, match="Invalid run_id"),
        ):
            clear_model_cache(run_id="../etc")

    def test_clear_run_id_with_slash_raises(self, tmp_path):
        """Raises ValueError for run IDs containing slashes."""
        from haute._mlflow_io import clear_model_cache

        (tmp_path / ".cache" / "models").mkdir(parents=True)

        with (
            patch("haute._mlflow_io.Path.cwd", return_value=tmp_path),
            pytest.raises(ValueError, match="Invalid run_id"),
        ):
            clear_model_cache(run_id="run/subdir")


# ---------------------------------------------------------------------------
# load_mlflow_model — the disk cache
# ---------------------------------------------------------------------------


class TestLoadMlflowModelDiskCache:
    """A load reads its copies from the disk cache once the store has answered."""

    def test_disk_cache_root_is_relative_to_current_working_directory(self, tmp_path, monkeypatch):
        use_project(tmp_path)

        assert _disk_cache_root() == tmp_path / ".cache" / "models"

    def test_a_load_whose_copies_are_on_disk_downloads_nothing(self, tmp_path, serve_runs):
        """A new process loads the model and the contract its run logged from disk."""
        use_project(tmp_path)
        store = serve_runs(
            {"abc123": {"model.cbm": b"cached model", "model.feature_contract.json": b"{}"}}
        )
        source = _run_source("abc123", "model.cbm")
        fake_sm = ScoringModel(MagicMock(), ["a"], frozenset(), "catboost")

        with patch("haute._mlflow_io._load_catboost_file", return_value=fake_sm) as load_file:
            load_mlflow_model(source, task="regression")
            assert len(store.downloads) == 2
            _model_cache.clear()  # what a new process starts with
            result = load_mlflow_model(source, task="regression")

        assert result is fake_sm
        assert len(store.downloads) == 2, "a copy on disk was downloaded again"
        resolved = resolve_model(source, backend=_FAKE_BACKEND)
        cache_root = tmp_path / ".cache" / "models"
        model_copy = _artifact_cache_path(
            cache_root, _FAKE_BACKEND.digest, "abc123", "model.cbm", resolved.artifact.token
        )
        assert resolved.contract is not None
        contract_copy = _artifact_cache_path(
            cache_root,
            _FAKE_BACKEND.digest,
            "abc123",
            resolved.contract.path,
            resolved.contract.token,
        )
        # The model carries the contract its run logged, read from the same cache.
        assert result.run_contract_path == str(contract_copy)
        load_file.assert_called_with(
            str(model_copy),
            "regression",
            contract_path=str(contract_copy),
            source="MLflow run 'abc123', artifact 'model.cbm'",
        )
        assert _model_cache.get(_model_cache_key(resolved, "regression")) is fake_sm

    def test_a_disk_cache_only_load_answers_from_the_record_without_the_store(
        self, tmp_path, serve_runs, monkeypatch
    ):
        from haute._mlflow_io import disk_cache_only_model_loads, disk_cached_run_model
        from haute.errors import ModelNotInDiskCacheError

        use_project(tmp_path)
        store = serve_runs(
            {"abc123": {"model.cbm": b"cached model", "model.feature_contract.json": b"{}"}}
        )
        fake_sm = ScoringModel(MagicMock(), ["a"], frozenset(), "catboost")
        with patch("haute._mlflow_io._load_catboost_file", return_value=fake_sm):
            loaded = load_mlflow_model(_run_source("abc123", "model.cbm"), task="regression")
        _model_cache.clear()

        cached = disk_cached_run_model(run_id="abc123", artifact_path="model.cbm", destination="")
        assert cached is not None and cached.present()
        assert cached.run_contract == loaded.run_contract_path

        monkeypatch.setattr(
            "haute._mlflow_utils.pinned_client",
            lambda backend: pytest.fail("a disk-cache-only load asked the store"),
        )
        with (
            disk_cache_only_model_loads(),
            patch("haute._mlflow_io._load_catboost_file", return_value=fake_sm),
        ):
            result = load_mlflow_model(_run_source("abc123", "model.cbm"), task="regression")
            # A reference the record cannot answer is refused, not resolved.
            for unanswerable in (_run_source("abc123"), _run_source("other", "model.cbm")):
                with pytest.raises(ModelNotInDiskCacheError):
                    load_mlflow_model(unanswerable, task="regression")

        assert result is fake_sm
        assert result.run_contract_path == loaded.run_contract_path
        assert len(store.downloads) == 2

    def test_a_discovered_reference_and_a_named_one_share_an_entry(self, serve_runs):
        """A run by discovery and the same run naming the file are one model."""
        store = serve_runs({"abc123": {"model.cbm": b"model bytes"}})

        with patch(
            "haute._mlflow_io._load_catboost_model", return_value=_fake_catboost("a")
        ) as load:
            discovered = load_mlflow_model(_run_source("abc123"), task="regression")
            named = load_mlflow_model(_run_source("abc123", "model.cbm"), task="regression")

        assert named is discovered
        assert load.call_count == 1
        assert store.downloads == ["runs:/abc123/model.cbm"]


# ---------------------------------------------------------------------------
# load_mlflow_model — retry on a corrupt copy
# ---------------------------------------------------------------------------


class TestLoadMlflowModelRetry:
    """Tests for retry-on-corrupt-cache logic."""

    def test_retry_catboost_on_load_failure(self, serve_runs):
        """Deletes corrupt file and re-downloads on first load failure."""
        store = serve_runs({"run1": {"model.cbm": b"corrupt"}})
        fake_model = _fake_catboost("a")

        call_count = 0

        def load_catboost_side_effect(path, task):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("corrupt file")
            return fake_model

        with patch(
            "haute._mlflow_io._load_catboost_model",
            side_effect=load_catboost_side_effect,
        ):
            result = load_mlflow_model(_run_source("run1", "model.cbm"), task="regression")

        assert isinstance(result, ScoringModel)
        assert result.flavor == "catboost"
        assert call_count == 2
        assert store.downloads == ["runs:/run1/model.cbm"] * 2

    def test_retry_rustystats_on_load_failure(self, serve_runs):
        """Retries RustyStats model loading on first failure."""
        store = serve_runs({"run2": {"model.rsglm": b"corrupt"}})
        fake_sm = ScoringModel(MagicMock(), ["x"], frozenset(), "rustystats")

        call_count = 0

        def load_rs_side_effect(path, *, source=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("corrupt binary")
            return fake_sm

        with patch(
            "haute._mlflow_io._load_rustystats_model",
            side_effect=load_rs_side_effect,
        ):
            result = load_mlflow_model(_run_source("run2", "model.rsglm"), task="regression")

        assert result is fake_sm
        assert call_count == 2
        assert store.downloads == ["runs:/run2/model.rsglm"] * 2

    def test_a_download_that_is_not_the_listed_file_is_refused_not_retried(self, serve_runs):
        """A file logged again between the listing and the download is refused at once:
        downloading again would fetch the new bytes under the old identity."""
        from haute.errors import ModelResolutionError

        store = serve_runs({"run3": {"model.cbm": b"listed bytes"}})
        store.files["run3"]["model.cbm"] = b"logged again since the listing"

        with (
            patch("haute._mlflow_io._load_catboost_model") as load,
            pytest.raises(ModelResolutionError, match="changed while it was loaded"),
        ):
            load_mlflow_model(_run_source("run3", "model.cbm"), task="regression")

        assert store.downloads == ["runs:/run3/model.cbm"]
        load.assert_not_called()


# ---------------------------------------------------------------------------
# load_mlflow_model — pyfunc loading
# ---------------------------------------------------------------------------


class TestLoadMlflowModelPyfunc:
    """Tests for pyfunc model loading through load_mlflow_model."""

    def test_pyfunc_model_loaded_and_wrapped(self, serve_runs):
        """Pyfunc model is loaded and wrapped correctly."""
        from haute._mlflow_io import disk_cached_run_model

        fake_pyfunc = MagicMock()
        fake_pyfunc.metadata.signature = _signature("f1")
        serve_runs({"run1": _PYFUNC_FOLDER})

        with patch("haute._mlflow_io._load_pyfunc_model", return_value=fake_pyfunc):
            result = load_mlflow_model(_run_source("run1", "model"), task="regression")

        assert result.flavor == "pyfunc"
        assert result.feature_names == ["f1"]
        assert result.run_contract_path is None
        # A pyfunc folder is never copied, so the disk cache records nothing for it.
        assert disk_cached_run_model(run_id="run1", artifact_path="model", destination="") is None


# ---------------------------------------------------------------------------
# load_mlflow_model — a run without an artifact path
# ---------------------------------------------------------------------------


class TestLoadMlflowModelAutoDiscover:
    """Tests for auto-discovery when artifact_path is empty."""

    def test_auto_discovers_rsglm(self, serve_runs):
        """Auto-discovers .rsglm when artifact_path is empty."""
        fake_sm = ScoringModel(MagicMock(), ["x"], frozenset(), "rustystats")
        store = serve_runs({"run1": {"model.rsglm": b"glm", "notes.txt": b"notes"}})

        with patch("haute._mlflow_io._load_rustystats_model", return_value=fake_sm):
            result = load_mlflow_model(_run_source("run1"), task="regression")

        assert result is fake_sm
        assert store.downloads == ["runs:/run1/model.rsglm"]

    def test_auto_discovers_cbm(self, serve_runs):
        """Auto-discovers .cbm when artifact_path is empty, one folder down too."""
        store = serve_runs({"run1": {"models/model.cbm": b"cbm", "notes.txt": b"notes"}})

        with patch("haute._mlflow_io._load_catboost_model", return_value=_fake_catboost("a")):
            result = load_mlflow_model(_run_source("run1"), task="regression")

        assert result.flavor == "catboost"
        assert store.downloads == ["runs:/run1/models/model.cbm"]


# ---------------------------------------------------------------------------
# _score_eager (lines 599-617)
# ---------------------------------------------------------------------------


class TestScoreEager:
    """Tests for the _score_eager shared scoring helper."""

    def test_regression_scoring(self):
        """Regression task scores without proba column."""
        from haute._mlflow_io import _score_eager

        raw_model = MagicMock()
        raw_model.predict.return_value = np.array([1.0, 2.0, 3.0])
        sm = ScoringModel(raw_model, ["a", "b"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
        lf = df.lazy()

        result_lf = _score_eager(sm, lf, ["a", "b"], "prediction", "regression")
        result = result_lf.collect()

        assert "prediction" in result.columns
        assert "prediction_proba" not in result.columns
        assert result["prediction"].to_list() == [1.0, 2.0, 3.0]

    def test_classification_scoring_with_proba(self):
        """Classification task appends proba column."""
        from haute._mlflow_io import _score_eager

        raw_model = MagicMock()
        raw_model.predict.return_value = np.array([0, 1, 0])
        raw_model.predict_proba.return_value = np.array([[0.8, 0.2], [0.3, 0.7], [0.9, 0.1]])
        sm = ScoringModel(raw_model, ["a"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        lf = df.lazy()

        result_lf = _score_eager(sm, lf, ["a"], "pred", "classification")
        result = result_lf.collect()

        assert "pred" in result.columns
        assert "pred_proba" in result.columns
        expected_proba = [0.2, 0.7, 0.1]
        for actual, expected in zip(result["pred_proba"].to_list(), expected_proba):
            assert actual == pytest.approx(expected)

    def test_classification_no_predict_proba(self):
        """Classification task without predict_proba returns no proba column."""
        from haute._mlflow_io import _score_eager

        raw_model = MagicMock(spec=["predict"])
        raw_model.predict.return_value = np.array([0, 1])
        sm = ScoringModel(raw_model, ["a"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0]})
        result = _score_eager(sm, df.lazy(), ["a"], "pred", "classification").collect()

        assert "pred" in result.columns
        assert "pred_proba" not in result.columns

    def test_catboost_scoring_numpy_path(self):
        """CatBoost with no categoricals uses numpy path."""
        from haute._mlflow_io import _score_eager

        raw_model = MagicMock()
        raw_model.predict.return_value = np.array([10.0, 20.0])
        sm = ScoringModel(raw_model, ["a", "b"], frozenset(), "catboost")

        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "output", "regression").collect()

        assert "output" in result.columns
        assert result["output"].to_list() == [10.0, 20.0]

    def test_rustystats_scoring(self):
        """RustyStats flavor passes Polars DataFrame to model."""
        from haute._mlflow_io import _score_eager

        raw_model = MagicMock()
        raw_model.predict.return_value = np.array([5.0, 6.0])
        sm = ScoringModel(raw_model, ["a"], frozenset(), "rustystats")

        df = pl.DataFrame({"a": [1.0, 2.0]})
        result = _score_eager(sm, df.lazy(), ["a"], "pred", "regression").collect()

        assert "pred" in result.columns
        # Verify predict was called with a Polars DataFrame
        call_args = raw_model.predict.call_args[0][0]
        assert isinstance(call_args, pl.DataFrame)


# ---------------------------------------------------------------------------
# _load_pyfunc_model (lines 167-168)
# ---------------------------------------------------------------------------


class TestLoadPyfuncModel:
    """Tests for _load_pyfunc_model URI construction."""

    def test_constructs_correct_uri(self):
        """Download from the selected destination before loading the local pyfunc."""
        from haute._mlflow_io import _load_pyfunc_model

        mock_mlflow = MagicMock()
        fake_model = MagicMock()
        mock_mlflow.pyfunc.load_model.return_value = fake_model
        mock_mlflow.artifacts.download_artifacts.return_value = "/downloaded/model"

        backend = ResolvedBackend(
            mode="server",
            tracking_uri="https://selected.example.test",
            registry_uri="https://selected.example.test",
            identity="server:https://selected.example.test|registry=https://selected.example.test",
            digest="fedcba9876543210",
        )

        result = _load_pyfunc_model(
            mock_mlflow,
            "run123",
            "model",
            backend=backend,
        )

        mock_mlflow.artifacts.download_artifacts.assert_called_once_with(
            "runs:/run123/model",
            tracking_uri="https://selected.example.test",
        )
        mock_mlflow.pyfunc.load_model.assert_called_once_with("/downloaded/model")
        assert result is fake_model


# ---------------------------------------------------------------------------
# _prepare_predict_frame — empty features for rustystats
# ---------------------------------------------------------------------------


class TestPreparePredictFrameEdgeCases:
    """Edge cases for _prepare_predict_frame."""

    @pytest.mark.parametrize("flavor", ["catboost", "pyfunc", "rustystats"])
    def test_a_128_bit_feature_is_refused_by_name_not_by_a_panic(self, flavor):
        """``to_numpy`` panics on Int128 (a BaseException no handler catches) and
        Arrow has no type for it; every flavour names the column instead."""
        from haute.errors import SchemaMismatchError

        df = pl.DataFrame(
            {"a": [1, 2], "b": pl.Series([3, 4], dtype=pl.UInt64), "x": [0.5, 1.5]}
        ).with_columns(policy_key=pl.col("a") + pl.col("b"))

        with pytest.raises(SchemaMismatchError, match="Column 'policy_key' is Int128"):
            _prepare_predict_frame(df, ["x", "policy_key"], frozenset(), flavor)

    def test_a_128_bit_column_that_is_not_a_feature_is_left_alone(self):
        df = pl.DataFrame({"x": [0.5, 1.5], "key": pl.Series([1, 2], dtype=pl.Int128)})

        result = _prepare_predict_frame(df, ["x"], frozenset(), "catboost")

        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 1)

    def test_rustystats_empty_features_returns_full_df(self):
        """RustyStats with empty features list returns the full DataFrame."""
        df = pl.DataFrame({"a": [1.0], "b": [2.0]})
        result = _prepare_predict_frame(df, [], frozenset(), "rustystats")
        assert isinstance(result, pl.DataFrame)
        assert set(result.columns) == {"a", "b"}


# ---------------------------------------------------------------------------
# Flavor SSOT — the scorer domain and mlflow_io must never drift
# ---------------------------------------------------------------------------


class TestFlavorSsot:
    """Predict-frame preparation reads the model family registry.

    Every registered family is prepared through its registered
    ``predict_frame``; a flavor outside the registry is rejected loudly rather
    than scored through a guessed input contract.
    """

    def test_prepare_predict_frame_recognises_exactly_registered_flavors(self):
        from haute._model_flavors import model_families

        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        features = ["a", "b"]

        for family in model_families():
            prepared = _prepare_predict_frame(df, features, frozenset(), family.flavor)
            expected = {"polars": pl.DataFrame, "tabular": np.ndarray}.get(family.predict_frame)
            if expected is not None:
                assert isinstance(prepared, expected)
            else:
                assert list(prepared.columns) == features  # named pandas frame

        with pytest.raises(ValueError, match="Unknown model flavor"):
            _prepare_predict_frame(df, features, frozenset(), "unregistered")

    def test_unknown_flavor_error_enumerates_the_registry(self):
        from haute._model_flavors import model_families

        df = pl.DataFrame({"a": [1.0]})
        with pytest.raises(ValueError) as exc:
            _prepare_predict_frame(df, ["a"], frozenset(), "unregistered")
        message = str(exc.value)
        for family in model_families():
            assert repr(family.flavor) in message


class TestCatBoostTaskFromTheModelFile:
    """A CatBoost model file records its loss, which fixes the task it scores."""

    @staticmethod
    def _saved(tmp_path, estimator) -> str:
        rng = np.random.RandomState(3)
        features = rng.rand(40, 2)
        estimator.fit(features, (features[:, 0] > 0.5).astype(int))
        path = tmp_path / "model.cbm"
        estimator.save_model(str(path))
        return str(path)

    def test_a_classifier_cannot_be_scored_as_regression(self, tmp_path):
        from catboost import CatBoostClassifier

        from haute.errors import ConfigError

        path = self._saved(
            tmp_path, CatBoostClassifier(iterations=2, verbose=0, allow_writing_files=False)
        )
        with pytest.raises(ConfigError, match="trained for classification"):
            load_local_model(path, task="regression")
        assert load_local_model(path, task="classification").flavor == "catboost"

    def test_a_regressor_cannot_be_scored_as_classification(self, tmp_path):
        from catboost import CatBoostRegressor

        from haute.errors import ConfigError

        path = self._saved(
            tmp_path,
            CatBoostRegressor(
                iterations=2, verbose=0, allow_writing_files=False, loss_function="Poisson"
            ),
        )
        with pytest.raises(ConfigError, match=r"trained for regression \(loss Poisson\)"):
            load_local_model(path, task="classification")
        assert load_local_model(path, task="regression").flavor == "catboost"

    def test_a_task_mismatch_is_not_retried_as_a_corrupt_download(self, tmp_path, monkeypatch):
        from catboost import CatBoostClassifier

        from haute._mlflow_io import _load_with_bounded_retry
        from haute.errors import ConfigError

        monkeypatch.setattr("haute._mlflow_io._disk_cache_root", lambda: tmp_path / "cache")
        path = self._saved(
            tmp_path, CatBoostClassifier(iterations=2, verbose=0, allow_writing_files=False)
        )
        model_bytes = Path(path).read_bytes()
        contract = StoredArtifact("model.feature_contract.json", "size:2")
        resolved = _resolved("r", "model.cbm", model_bytes, contract=contract)
        downloads: list[str] = []
        transport = _transport(
            {"model.cbm": model_bytes, "model.feature_contract.json": b"{}"}, downloads
        )

        with pytest.raises(ConfigError, match="trained for classification"):
            _load_with_bounded_retry(mlflow_mod=transport, resolved=resolved, task="regression")

        # The model and the contract its run logged, each read once: never retried.
        assert downloads == ["runs:/r/model.cbm", "runs:/r/model.feature_contract.json"]
        # And the readable copy is kept.
        copy = _artifact_cache_path(
            tmp_path / "cache", _FAKE_BACKEND.digest, "r", "model.cbm", resolved.artifact.token
        )
        assert copy.read_bytes() == model_bytes

    def test_a_haute_refusal_is_not_retried_as_a_corrupt_download(self, tmp_path, monkeypatch):
        """A Haute refusal of readable bytes keeps its own type and message."""
        import lightgbm as lgb

        from haute._mlflow_io import _load_with_bounded_retry
        from haute.errors import HauteValidationError

        monkeypatch.setattr("haute._mlflow_io._disk_cache_root", lambda: tmp_path / "cache")
        rng = np.random.RandomState(3)
        booster = lgb.train(
            {"objective": "regression", "verbose": -1, "num_leaves": 2},
            lgb.Dataset(rng.rand(40, 2), label=rng.rand(40)),
            num_boost_round=2,
        )
        # Two Haute records: readable LightGBM text the loader refuses by name.
        model_bytes = (booster.model_to_string() + "haute:{}\nhaute:{}\n").encode("utf-8")
        resolved = _resolved("r", "model.lgbm", model_bytes)
        downloads: list[str] = []

        with pytest.raises(HauteValidationError, match="holds 2 'haute:' records") as refused:
            _load_with_bounded_retry(
                mlflow_mod=_transport({"model.lgbm": model_bytes}, downloads),
                resolved=resolved,
                task="regression",
            )
        assert "Persistently corrupt" not in str(refused.value)
        assert downloads == ["runs:/r/model.lgbm"]
        copy = _artifact_cache_path(
            tmp_path / "cache", _FAKE_BACKEND.digest, "r", "model.lgbm", resolved.artifact.token
        )
        assert copy.is_file()

    def test_an_unreadable_class_label_record_is_raised_not_replaced(self, tmp_path, monkeypatch):
        from catboost import CatBoostClassifier

        from haute._mlflow_io import catboost_class_labels

        model = CatBoostClassifier(iterations=2, verbose=0, allow_writing_files=False)
        self._saved(tmp_path, model)

        def unreadable(_model):
            raise RuntimeError("metadata unreadable")

        monkeypatch.setattr(CatBoostClassifier, "get_metadata", unreadable)
        with pytest.raises(RuntimeError, match="metadata unreadable"):
            catboost_class_labels(model)


class TestTheContractBesideAModel:
    """A run's contract is optional, but only the run's own listing says it is absent.

    The resolution reads the contract from the listing that holds the model, so
    an absent contract costs no store call of its own; a failed listing raises
    (``TestTheRunDiscoveryRule.test_a_store_failure_propagates_rather_than_reading_as_no_model``);
    and the disk cache records a load's copies only once every one is in place.
    """

    def test_an_absent_contract_costs_nothing_and_is_recorded_as_none(self, monkeypatch):
        from haute._mlflow_io import _recorded_resolution, fetch_resolved_files

        client = FakeArtifactClient({"r3": {"models/m.cbm": 4, "models/notes.txt": 1}})
        resolved = _resolve(monkeypatch, client, _run_source("r3", "models/m.cbm"))

        assert resolved.contract is None
        # The model's own folder is listed, once; nothing else is asked.
        assert client.listings == [("r3", "models")]

        downloads: list[str] = []
        fetched = fetch_resolved_files(_transport({"models/m.cbm": b"mcbm"}, downloads), resolved)
        assert fetched.contract is None
        assert downloads == ["runs:/r3/models/m.cbm"]

        recorded = _recorded_resolution(_FAKE_BACKEND, "r3", "models/m.cbm")
        assert recorded is not None
        answer, cached = recorded
        assert answer.contract is None and cached.contract_path is None
        assert cached.model_path == fetched.model
        assert json.loads(cached.record_path.read_text(encoding="utf-8"))["contract"] is None

    def test_a_listed_contract_is_fetched_with_its_model_and_recorded(self, monkeypatch):
        from haute._mlflow_io import _recorded_resolution, fetch_resolved_files

        client = FakeArtifactClient(
            {"r4": {"models/m.cbm": 4, "models/m.feature_contract.json": 2}}
        )
        resolved = _resolve(monkeypatch, client, _run_source("r4", "models/m.cbm"))

        assert resolved.contract == StoredArtifact("models/m.feature_contract.json", "size:2")
        assert client.listings == [("r4", "models")]

        downloads: list[str] = []
        fetched = fetch_resolved_files(
            _transport(
                {"models/m.cbm": b"mcbm", "models/m.feature_contract.json": b"{}"}, downloads
            ),
            resolved,
        )
        assert downloads == ["runs:/r4/models/m.cbm", "runs:/r4/models/m.feature_contract.json"]
        assert fetched.contract is not None and fetched.contract.read_bytes() == b"{}"

        recorded = _recorded_resolution(_FAKE_BACKEND, "r4", "models/m.cbm")
        assert recorded is not None
        answer, cached = recorded
        assert answer.contract == resolved.contract
        assert cached.contract_path == fetched.contract
        assert cached.present()

    def test_a_failed_download_of_a_listed_contract_is_raised_and_records_nothing(self):
        """A contract that could not be fetched is never read as no contract."""
        from haute._mlflow_io import (
            _recorded_resolution,
            _resolution_record_path,
            fetch_resolved_files,
        )

        resolved = _resolved(
            "r1",
            "m.cbm",
            b"mcbm",
            contract=StoredArtifact("m.feature_contract.json", "size:2"),
        )
        transport = _transport(
            {"m.cbm": b"mcbm", "m.feature_contract.json": OSError("connection reset")}, []
        )

        with pytest.raises(OSError, match="connection reset"):
            fetch_resolved_files(transport, resolved)

        assert not _resolution_record_path(
            _disk_cache_root(), _FAKE_BACKEND, "r1", "m.cbm"
        ).exists()
        # So a disk-cache-only load refuses it rather than scoring without its contract.
        assert _recorded_resolution(_FAKE_BACKEND, "r1", "m.cbm") is None
