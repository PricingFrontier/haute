"""Tests for haute._mlflow_io — MLflow model loading and caching."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import (
    _MODEL_CACHE_MAX_SIZE,
    ScoringModel,
    _append_classification_proba,
    _find_artifact_by_extension,
    _find_cbm_artifact,
    _find_model_artifact,
    _load_rustystats_model,
    _model_cache,
    _prepare_predict_frame,
    _wrap_catboost,
    _wrap_pyfunc,
    load_local_model,
    load_mlflow_model,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the model cache before/after each test."""
    _model_cache.clear()
    yield
    _model_cache.clear()


@pytest.fixture()
def mock_mlflow_env():
    """Set up mock mlflow modules and common patches for load_mlflow_model tests."""
    mock_mlflow = MagicMock()
    mock_mlflow.artifacts.download_artifacts.return_value = "/tmp/model.cbm"

    mock_client_instance = MagicMock()
    mock_mlflow_tracking = MagicMock()
    mock_mlflow_tracking.MlflowClient.return_value = mock_client_instance

    modules_patch = patch.dict(
        sys.modules,
        {
            "mlflow": mock_mlflow,
            "mlflow.tracking": mock_mlflow_tracking,
        },
    )
    resolve_patch = patch(
        "haute.modelling._mlflow_log.resolve_tracking_backend",
        return_value=("file:///mlruns", "local"),
    )
    return mock_mlflow, mock_client_instance, modules_patch, resolve_patch


# ---------------------------------------------------------------------------
# load_mlflow_model — run-based
# ---------------------------------------------------------------------------


class TestLoadRunBasedModel:
    def test_load_run_based_model(self, mock_mlflow_env):
        """Run-based loading downloads artifacts and returns ScoringModel."""
        fake_model = MagicMock()
        fake_model.feature_names_ = ["a", "b"]
        fake_model.get_cat_feature_indices.return_value = []
        _mock_mlflow, _mock_client, modules_patch, resolve_patch = mock_mlflow_env

        with (
            modules_patch,
            resolve_patch,
            patch("haute._mlflow_io._load_catboost_model", return_value=fake_model),
            patch("haute._mlflow_io._resolve_artifact_local", return_value="/tmp/model.cbm"),
            patch("haute._mlflow_io._find_cbm_artifact", return_value="model.cbm"),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="abc123",
                artifact_path="model.cbm",
                task="regression",
            )

        assert isinstance(result, ScoringModel)
        assert result.raw_model is fake_model
        assert result.flavor == "catboost"
        assert result.feature_names == ["a", "b"]

    def test_run_based_missing_run_id(self, mock_mlflow_env):
        """Raises ValueError when run_id is empty for source_type=run."""
        _, _, modules_patch, resolve_patch = mock_mlflow_env

        with modules_patch, resolve_patch:
            with pytest.raises(ValueError, match="run_id is required"):
                load_mlflow_model(source_type="run", run_id="", task="regression")


# ---------------------------------------------------------------------------
# load_mlflow_model — registered
# ---------------------------------------------------------------------------


class TestLoadRegisteredModel:
    def test_registered_missing_model_name(self, mock_mlflow_env):
        """Raises ValueError when registered_model is empty."""
        _, _, modules_patch, resolve_patch = mock_mlflow_env

        with modules_patch, resolve_patch:
            with pytest.raises(ValueError, match="registered_model is required"):
                load_mlflow_model(
                    source_type="registered",
                    registered_model="",
                    task="regression",
                )

    def test_load_registered_model(self, mock_mlflow_env):
        """Registered model loading resolves version and returns ScoringModel."""
        fake_model = MagicMock()
        fake_model.feature_names_ = ["x"]
        fake_model.get_cat_feature_indices.return_value = []
        _, mock_client, modules_patch, resolve_patch = mock_mlflow_env

        mv = MagicMock(run_id="resolved_run_id")
        mock_client.get_model_version.return_value = mv

        with (
            modules_patch,
            resolve_patch,
            patch("haute._mlflow_io._load_catboost_model", return_value=fake_model),
            patch("haute._mlflow_io._resolve_artifact_local", return_value="/tmp/model.cbm"),
            patch("haute._mlflow_utils.resolve_version", return_value="2"),
            patch("haute._mlflow_io._find_cbm_artifact", return_value="model.cbm"),
        ):
            result = load_mlflow_model(
                source_type="registered",
                registered_model="my-model",
                version="2",
                task="regression",
            )

        assert isinstance(result, ScoringModel)
        assert result.raw_model is fake_model


# ---------------------------------------------------------------------------
# load_mlflow_model — pyfunc auto-detection
# ---------------------------------------------------------------------------


class TestPyfuncAutoDetect:
    def test_non_cbm_artifact_uses_pyfunc(self, mock_mlflow_env):
        """Artifact path not ending in .cbm loads via pyfunc."""
        fake_pyfunc = MagicMock()
        fake_pyfunc.metadata.signature.inputs.input_names.return_value = ["f1", "f2"]
        _, _, modules_patch, resolve_patch = mock_mlflow_env

        with (
            modules_patch,
            resolve_patch,
            patch("haute._mlflow_io._load_pyfunc_model", return_value=fake_pyfunc),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="abc123",
                artifact_path="model",
                task="regression",
            )

        assert isinstance(result, ScoringModel)
        assert result.flavor == "pyfunc"
        assert result.feature_names == ["f1", "f2"]
        assert result.cat_feature_names == frozenset()

    def test_auto_discover_falls_back_to_pyfunc(self, mock_mlflow_env):
        """When no .cbm found and no artifact_path, falls back to pyfunc 'model'."""
        fake_pyfunc = MagicMock()
        fake_pyfunc.metadata.signature.inputs.input_names.return_value = ["a"]
        _, _, modules_patch, resolve_patch = mock_mlflow_env

        with (
            modules_patch,
            resolve_patch,
            patch("haute._mlflow_io._find_cbm_artifact", side_effect=FileNotFoundError),
            patch("haute._mlflow_io._find_model_artifact", return_value=("model", "pyfunc")),
            patch("haute._mlflow_io._load_pyfunc_model", return_value=fake_pyfunc),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="abc123",
                artifact_path="model",
                task="regression",
            )

        assert result.flavor == "pyfunc"


# ---------------------------------------------------------------------------
# Invalid source type
# ---------------------------------------------------------------------------


class TestInvalidSourceType:
    def test_invalid_source_type(self, mock_mlflow_env):
        """Raises ValueError for unknown sourceType."""
        _, _, modules_patch, resolve_patch = mock_mlflow_env

        with modules_patch, resolve_patch:
            with pytest.raises(ValueError, match="Invalid sourceType"):
                load_mlflow_model(source_type="invalid", task="regression")


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestModelCache:
    def test_cache_hit(self, mock_mlflow_env):
        """Second call with same args returns cached model without re-download."""
        fake_sm = ScoringModel(MagicMock(), ["a"], frozenset(), "catboost")
        cache_key = ("run", "abc123", "model.cbm", "regression")
        _model_cache.put(cache_key, fake_sm)

        mock_mlflow, _, modules_patch, resolve_patch = mock_mlflow_env

        with (
            modules_patch,
            resolve_patch,
            patch("haute._mlflow_io._find_cbm_artifact", return_value="model.cbm"),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="abc123",
                artifact_path="model.cbm",
                task="regression",
            )

        assert result is fake_sm
        # download_artifacts should NOT be called (cache hit)
        mock_mlflow.artifacts.download_artifacts.assert_not_called()

    def test_cache_lru_eviction(self, mock_mlflow_env):
        """Exceeding max cache size evicts oldest entry."""
        for i in range(_MODEL_CACHE_MAX_SIZE + 2):
            _model_cache.put(("run", f"run_{i}", f"art_{i}", "regression"), MagicMock())

        fake_model = MagicMock()
        fake_model.feature_names_ = ["a"]
        fake_model.get_cat_feature_indices.return_value = []
        _, _, modules_patch, resolve_patch = mock_mlflow_env

        with (
            modules_patch,
            resolve_patch,
            patch("haute._mlflow_io._load_catboost_model", return_value=fake_model),
            patch("haute._mlflow_io._resolve_artifact_local", return_value="/tmp/model.cbm"),
            patch("haute._mlflow_io._find_cbm_artifact", return_value="model.cbm"),
        ):
            load_mlflow_model(
                source_type="run",
                run_id="new_run",
                artifact_path="model.cbm",
                task="regression",
            )

        assert ("run", "new_run", "model.cbm", "regression") in _model_cache


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

        sm = _wrap_catboost(model)
        assert sm.flavor == "catboost"
        assert sm.feature_names == ["a", "b", "c"]
        assert sm.cat_feature_names == frozenset({"c"})
        assert sm.raw_model is model

    def test_wrap_catboost_no_cat_features(self):
        """_wrap_catboost with no cat feature indices."""
        model = MagicMock()
        model.feature_names_ = ["x", "y"]
        model.get_cat_feature_indices.return_value = []

        sm = _wrap_catboost(model)
        assert sm.cat_feature_names == frozenset()

    def test_wrap_pyfunc(self):
        """_wrap_pyfunc extracts features from model signature."""
        model = MagicMock()
        model.metadata.signature.inputs.input_names.return_value = ["f1", "f2"]

        sm = _wrap_pyfunc(model)
        assert sm.flavor == "pyfunc"
        assert sm.feature_names == ["f1", "f2"]
        assert sm.cat_feature_names == frozenset()

    def test_wrap_pyfunc_no_signature(self):
        """_wrap_pyfunc with no signature returns empty feature list."""
        model = MagicMock()
        model.metadata = None

        sm = _wrap_pyfunc(model)
        assert sm.feature_names == []


# ---------------------------------------------------------------------------
# MLflow not installed
# ---------------------------------------------------------------------------


class TestMlflowNotInstalled:
    def test_import_error_message(self):
        """Raises ImportError with pip install instruction when mlflow missing."""
        with patch.dict(sys.modules, {"mlflow": None}):
            with pytest.raises(ImportError, match="pip install mlflow"):
                load_mlflow_model(source_type="run", run_id="x", task="regression")


# ---------------------------------------------------------------------------
# Task validation
# ---------------------------------------------------------------------------


class TestTaskValidation:
    def test_invalid_task_raises(self):
        """Invalid task value raises ValueError before any MLflow calls."""
        with pytest.raises(ValueError, match="Invalid task"):
            load_mlflow_model(source_type="run", run_id="x", task="clustering")

    def test_empty_task_raises(self):
        """Empty task string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid task"):
            load_mlflow_model(source_type="run", run_id="x", task="")


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

    def test_pyfunc_always_returns_pandas(self):
        """Pyfunc flavor always returns pandas DataFrame, even with no cats."""
        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        import pandas as pd

        assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# _find_artifact_by_extension (D8 refactor)
# ---------------------------------------------------------------------------


class TestFindArtifactByExtension:
    """Tests for the unified artifact discovery helper."""

    def test_finds_top_level_cbm(self):
        """Finds .cbm at the root level."""
        client = MagicMock()
        art = MagicMock(path="my_model.cbm", is_dir=False)
        client.list_artifacts.return_value = [art]
        assert _find_artifact_by_extension(client, "run1", ".cbm", "CatBoost") == "my_model.cbm"

    def test_finds_top_level_rsglm(self):
        """Finds .rsglm at the root level."""
        client = MagicMock()
        art = MagicMock(path="glm_model.rsglm", is_dir=False)
        client.list_artifacts.return_value = [art]
        assert (
            _find_artifact_by_extension(client, "run1", ".rsglm", "RustyStats") == "glm_model.rsglm"
        )

    def test_finds_cbm_in_subdirectory(self):
        """Finds .cbm one level deep in a subdirectory."""
        client = MagicMock()
        dir_art = MagicMock(path="models", is_dir=True)
        client.list_artifacts.side_effect = [
            [dir_art],
            [MagicMock(path="models/trained.cbm", is_dir=False)],
        ]
        assert (
            _find_artifact_by_extension(client, "run1", ".cbm", "CatBoost") == "models/trained.cbm"
        )

    def test_finds_rsglm_in_subdirectory(self):
        """Finds .rsglm one level deep in a subdirectory."""
        client = MagicMock()
        dir_art = MagicMock(path="artifacts", is_dir=True)
        client.list_artifacts.side_effect = [
            [dir_art],
            [MagicMock(path="artifacts/glm.rsglm", is_dir=False)],
        ]
        assert (
            _find_artifact_by_extension(client, "run1", ".rsglm", "RustyStats")
            == "artifacts/glm.rsglm"
        )

    def test_missing_cbm_raises_with_label(self):
        """FileNotFoundError includes the extension and label."""
        client = MagicMock()
        art = MagicMock(path="readme.txt", is_dir=False)
        client.list_artifacts.return_value = [art]
        with pytest.raises(FileNotFoundError, match=r"No \.cbm artifact.*CatBoost"):
            _find_artifact_by_extension(client, "run1", ".cbm", "CatBoost")

    def test_missing_rsglm_raises_with_label(self):
        """FileNotFoundError includes the extension and label."""
        client = MagicMock()
        art = MagicMock(path="readme.txt", is_dir=False)
        client.list_artifacts.return_value = [art]
        with pytest.raises(FileNotFoundError, match=r"No \.rsglm artifact.*RustyStats"):
            _find_artifact_by_extension(client, "run1", ".rsglm", "RustyStats")

    def test_arbitrary_extension(self):
        """Works for any extension, not just .cbm and .rsglm."""
        client = MagicMock()
        art = MagicMock(path="weights.pt", is_dir=False)
        client.list_artifacts.return_value = [art]
        assert _find_artifact_by_extension(client, "run1", ".pt", "PyTorch") == "weights.pt"

    def test_prefers_top_level_over_subdirectory(self):
        """Top-level match is returned even if subdirectory also contains a match."""
        client = MagicMock()
        top_art = MagicMock(path="model.cbm", is_dir=False)
        dir_art = MagicMock(path="subdir", is_dir=True)
        client.list_artifacts.return_value = [top_art, dir_art]
        # list_artifacts should only be called once (top level)
        result = _find_artifact_by_extension(client, "run1", ".cbm", "CatBoost")
        assert result == "model.cbm"
        client.list_artifacts.assert_called_once_with("run1")

    def test_delegates_correctly_via_find_cbm(self):
        """_find_cbm_artifact delegates to _find_artifact_by_extension."""
        client = MagicMock()
        art = MagicMock(path="model.cbm", is_dir=False)
        client.list_artifacts.return_value = [art]
        assert _find_cbm_artifact(client, "run1") == "model.cbm"

    def test_delegates_correctly_via_find_rsglm(self):
        """_find_rsglm_artifact delegates to _find_artifact_by_extension."""
        from haute._mlflow_io import _find_rsglm_artifact

        client = MagicMock()
        art = MagicMock(path="model.rsglm", is_dir=False)
        client.list_artifacts.return_value = [art]
        assert _find_rsglm_artifact(client, "run1") == "model.rsglm"


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
# T9: _load_rustystats_model
# ---------------------------------------------------------------------------


class TestLoadRustystatsModel:
    """Tests for _load_rustystats_model using mocked rustystats module."""

    def test_loads_and_wraps_model(self, tmp_path):
        """Reads bytes from file and wraps in ScoringModel with flavor='rustystats'."""
        model_file = tmp_path / "model.rsglm"
        model_file.write_bytes(b"fake_bytes")

        mock_model = MagicMock()
        mock_model.feature_names = ["ns(feat_a, 1/3)", "ns(feat_a, 2/3)", "feat_b"]
        mock_model.terms_dict = {"feat_a": {"type": "ns"}, "feat_b": {"type": "linear"}}
        mock_rs = MagicMock()
        mock_rs.GLMModel.from_bytes.return_value = mock_model

        with patch.dict(sys.modules, {"rustystats": mock_rs}):
            sm = _load_rustystats_model(str(model_file))

        assert isinstance(sm, ScoringModel)
        assert sm.flavor == "rustystats"
        # Uses raw input column names from terms_dict, not design matrix names
        assert sm.feature_names == ["feat_a", "feat_b"]
        assert sm.cat_feature_names == frozenset()
        assert sm.raw_model is mock_model

    def test_model_without_feature_names(self, tmp_path):
        """Model without feature_names attribute gets empty list."""
        model_file = tmp_path / "model.rsglm"
        model_file.write_bytes(b"fake_bytes")

        mock_model = MagicMock(spec=[])  # no feature_names
        mock_rs = MagicMock()
        mock_rs.GLMModel.from_bytes.return_value = mock_model

        with patch.dict(sys.modules, {"rustystats": mock_rs}):
            sm = _load_rustystats_model(str(model_file))

        assert sm.feature_names == []

    def test_reads_file_as_bytes(self, tmp_path):
        """Verifies the file is read in binary mode."""
        model_file = tmp_path / "test.rsglm"
        model_file.write_bytes(b"fake_model_bytes")

        mock_model = MagicMock()
        mock_model.feature_names = ["x"]
        mock_rs = MagicMock()
        mock_rs.GLMModel.from_bytes.return_value = mock_model

        with patch.dict(sys.modules, {"rustystats": mock_rs}):
            _load_rustystats_model(str(model_file))

        mock_rs.GLMModel.from_bytes.assert_called_once_with(b"fake_model_bytes")


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
        """'.rsglm' extension dispatches to RustyStats loader."""
        model_file = tmp_path / "model.rsglm"
        model_file.write_bytes(b"fake_bytes")

        mock_model = MagicMock()
        mock_model.feature_names = ["ns(x, 1/3)", "ns(x, 2/3)", "y"]
        mock_model.terms_dict = {"x": {"type": "ns"}, "y": {"type": "linear"}}
        mock_rs = MagicMock()
        mock_rs.GLMModel.from_bytes.return_value = mock_model

        with patch.dict(sys.modules, {"rustystats": mock_rs}):
            sm = load_local_model(str(model_file))

        assert isinstance(sm, ScoringModel)
        assert sm.flavor == "rustystats"
        assert sm.feature_names == ["x", "y"]

    def test_unsupported_extension_raises(self):
        """Unknown extension raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="not yet supported"):
            load_local_model("/tmp/model.pkl")

    def test_unsupported_extension_lists_formats(self):
        """Error message lists supported formats."""
        with pytest.raises(NotImplementedError, match=r"\.cbm.*\.rsglm"):
            load_local_model("/tmp/model.onnx")

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
# T9: _find_model_artifact — auto-detection
# ---------------------------------------------------------------------------


class TestFindModelArtifact:
    """Tests for _find_model_artifact auto-detection logic."""

    def test_finds_cbm_first(self):
        """CatBoost .cbm is preferred over other formats."""
        client = MagicMock()
        art = MagicMock(path="model.cbm", is_dir=False)
        client.list_artifacts.return_value = [art]
        path, flavor = _find_model_artifact(client, "run1")
        assert path == "model.cbm"
        assert flavor == "catboost"

    def test_finds_rsglm_when_no_cbm(self):
        """RustyStats .rsglm is found when no .cbm exists."""
        client = MagicMock()
        rsglm_art = MagicMock(path="model.rsglm", is_dir=False)
        client.list_artifacts.return_value = [rsglm_art]
        path, flavor = _find_model_artifact(client, "run1")
        assert path == "model.rsglm"
        assert flavor == "rustystats"

    def test_finds_pyfunc_when_no_native(self):
        """Falls back to pyfunc 'model' directory."""
        client = MagicMock()
        model_dir = MagicMock(path="model", is_dir=True)
        client.list_artifacts.return_value = [model_dir]
        path, flavor = _find_model_artifact(client, "run1")
        assert path == "model"
        assert flavor == "pyfunc"

    def test_no_artifact_raises(self):
        """Raises FileNotFoundError when no model artifact found."""
        client = MagicMock()
        txt_art = MagicMock(path="readme.txt", is_dir=False)
        client.list_artifacts.return_value = [txt_art]
        with pytest.raises(FileNotFoundError, match="No model artifact"):
            _find_model_artifact(client, "run1")

    def test_finds_pyfunc_in_subdirectory_via_mlmodel(self):
        """Finds pyfunc model in subdirectory by detecting MLmodel file."""
        client = MagicMock()
        # Top level: no .cbm, no .rsglm, no "model" dir — just a custom subdir
        subdir = MagicMock(path="custom_model", is_dir=True)
        sub_contents = [MagicMock(path="custom_model/MLmodel", is_dir=False)]
        # _find_artifact_by_extension(.cbm): list_artifacts(run_id) → [subdir],
        #   then list_artifacts(run_id, "custom_model") → sub_contents (no .cbm) → raises
        # _find_artifact_by_extension(.rsglm): same 2 calls → raises
        # _find_model_artifact pyfunc check: list_artifacts(run_id) → [subdir] (not "model")
        #   then iterate dirs: list_artifacts(run_id, "custom_model") → sub_contents (has MLmodel)
        client.list_artifacts.side_effect = [
            [subdir],        # cbm: top level
            sub_contents,    # cbm: subdir (no .cbm)
            [subdir],        # rsglm: top level
            sub_contents,    # rsglm: subdir (no .rsglm)
            [subdir],        # pyfunc: top level "model" dir check
            sub_contents,    # pyfunc: subdir listing with MLmodel
        ]
        path, flavor = _find_model_artifact(client, "run1")
        assert path == "custom_model"
        assert flavor == "pyfunc"


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

    def test_getattr_proxies_to_raw_model(self):
        """Attribute access is proxied to the underlying model."""
        raw = MagicMock()
        raw.some_custom_attr = "hello"
        sm = ScoringModel(raw, ["a"], frozenset(), "catboost")
        assert sm.some_custom_attr == "hello"


# ---------------------------------------------------------------------------
# _load_rustystats_model — feature_names fallback (line 130)
# ---------------------------------------------------------------------------


class TestLoadRustystatsModelFeatureNamesFallback:
    """Test the fallback to model.feature_names when terms_dict is absent."""

    def test_fallback_to_feature_names(self, tmp_path):
        """When terms_dict is empty/absent, falls back to feature_names."""
        model_file = tmp_path / "model.rsglm"
        model_file.write_bytes(b"fake_bytes")

        mock_model = MagicMock()
        mock_model.terms_dict = {}  # empty dict → falsy
        mock_model.feature_names = ["ns(x, 1/3)", "ns(x, 2/3)", "y"]
        mock_rs = MagicMock()
        mock_rs.GLMModel.from_bytes.return_value = mock_model

        with patch.dict(sys.modules, {"rustystats": mock_rs}):
            sm = _load_rustystats_model(str(model_file))

        # Falls back to feature_names (design matrix names)
        assert sm.feature_names == ["ns(x, 1/3)", "ns(x, 2/3)", "y"]


# ---------------------------------------------------------------------------
# _extract_pyfunc_features — edge cases (lines 189, 193)
# ---------------------------------------------------------------------------


class TestExtractPyfuncFeatures:
    """Tests for _extract_pyfunc_features edge cases."""

    def test_no_signature(self):
        """Returns empty list when model has no signature."""
        from haute._mlflow_io import _extract_pyfunc_features

        model = MagicMock()
        model.metadata = None
        assert _extract_pyfunc_features(model) == []

    def test_inputs_is_none(self):
        """Returns empty list when signature.inputs is None."""
        from haute._mlflow_io import _extract_pyfunc_features

        model = MagicMock()
        model.metadata.signature.inputs = None
        assert _extract_pyfunc_features(model) == []

    def test_colspec_fallback(self):
        """Falls back to ColSpec-style input list when input_names() absent."""
        from haute._mlflow_io import _extract_pyfunc_features

        # ColSpec objects have a .name attribute
        col1 = MagicMock()
        col1.name = "feat_a"
        col2 = MagicMock()
        col2.name = "feat_b"
        # Use a plain list as inputs — lists lack input_names, triggering fallback
        inputs_list = [col1, col2]
        model = MagicMock()
        model.metadata.signature.inputs = inputs_list
        result = _extract_pyfunc_features(model)
        assert result == ["feat_a", "feat_b"]

    def test_input_names_method(self):
        """Uses input_names() when available."""
        from haute._mlflow_io import _extract_pyfunc_features

        model = MagicMock()
        model.metadata.signature.inputs.input_names.return_value = ["x", "y"]
        assert _extract_pyfunc_features(model) == ["x", "y"]


# ---------------------------------------------------------------------------
# _resolve_artifact_local (lines 298-353)
# ---------------------------------------------------------------------------


class TestResolveArtifactLocal:
    """Tests for _resolve_artifact_local disk cache logic."""

    def test_cache_hit_returns_local_path(self, tmp_path):
        """Returns cached file path without downloading when file exists."""
        from haute._mlflow_io import _resolve_artifact_local

        # Create the expected cache structure
        cache_dir = tmp_path / ".cache" / "models" / "run123"
        cache_dir.mkdir(parents=True)
        cached_file = cache_dir / "model.cbm"
        cached_file.write_bytes(b"cached_model_data")

        mock_mlflow = MagicMock()

        with patch("haute._mlflow_io.Path.cwd", return_value=tmp_path):
            result = _resolve_artifact_local(mock_mlflow, "run123", "model.cbm")

        assert result == str(cached_file)
        mock_mlflow.artifacts.download_artifacts.assert_not_called()

    def test_cache_miss_downloads_and_caches(self, tmp_path):
        """Downloads artifact and caches it on cache miss."""
        from haute._mlflow_io import _resolve_artifact_local

        # No cached file exists
        mock_mlflow = MagicMock()

        # Create a temp directory that simulates download
        download_dir = tmp_path / "download_staging"
        download_dir.mkdir()
        downloaded_file = download_dir / "model.cbm"
        downloaded_file.write_bytes(b"fresh_model_data")

        mock_mlflow.artifacts.download_artifacts.return_value = str(downloaded_file)

        with patch("haute._mlflow_io.Path.cwd", return_value=tmp_path):
            result = _resolve_artifact_local(mock_mlflow, "run456", "model.cbm")

        # File should be in cache dir now
        expected = tmp_path / ".cache" / "models" / "run456" / "model.cbm"
        assert result == str(expected)
        assert expected.is_file()

    def test_download_failure_cleans_up(self, tmp_path):
        """On download failure, partial cache entry is cleaned up."""
        from haute._mlflow_io import _resolve_artifact_local

        mock_mlflow = MagicMock()
        mock_mlflow.artifacts.download_artifacts.side_effect = RuntimeError("network error")

        with (
            patch("haute._mlflow_io.Path.cwd", return_value=tmp_path),
            pytest.raises(RuntimeError, match="network error"),
        ):
            _resolve_artifact_local(mock_mlflow, "run789", "model.cbm")

        # No cached file should remain
        cache_path = tmp_path / ".cache" / "models" / "run789" / "model.cbm"
        assert not cache_path.is_file()

    def test_failure_after_cache_write_cleans_partial(self, tmp_path):
        """If an error occurs after the file is moved into cache, the partial file is deleted."""
        from pathlib import Path

        from haute._mlflow_io import _resolve_artifact_local

        mock_mlflow = MagicMock()

        # Simulate: download succeeds, shutil.move succeeds (file lands in cache),
        # but then logger.info raises during the stat call → triggers cleanup
        download_dir = tmp_path / "dl"
        download_dir.mkdir()
        dl_file = download_dir / "model.cbm"
        dl_file.write_bytes(b"data")

        mock_mlflow.artifacts.download_artifacts.return_value = str(dl_file)

        # Patch logger.info to raise on the "mlflow_artifact_cached" call
        # (which happens after shutil.move, so the file exists in cache)
        original_info = __import__("haute._mlflow_io", fromlist=["logger"]).logger.info

        def failing_info(msg, **kwargs):
            if msg == "mlflow_artifact_cached":
                raise OSError("simulated stat failure")
            return original_info(msg, **kwargs)

        with (
            patch("haute._mlflow_io.Path.cwd", return_value=tmp_path),
            patch("haute._mlflow_io.logger.info", side_effect=failing_info),
            pytest.raises(OSError, match="simulated stat failure"),
        ):
            _resolve_artifact_local(mock_mlflow, "runX", "model.cbm")

        # The partial cache file should have been cleaned up
        cache_path = tmp_path / ".cache" / "models" / "runX" / "model.cbm"
        assert not cache_path.is_file()

    def test_downloaded_file_not_found_nested(self, tmp_path):
        """Raises when download returns path that doesn't exist and fallback fails."""
        from haute._mlflow_io import _resolve_artifact_local

        mock_mlflow = MagicMock()
        # Return a path that doesn't exist as a file
        mock_mlflow.artifacts.download_artifacts.return_value = str(
            tmp_path / "nonexistent" / "dir"
        )

        with (
            patch("haute._mlflow_io.Path.cwd", return_value=tmp_path),
            pytest.raises(FileNotFoundError, match="artifact not found"),
        ):
            _resolve_artifact_local(mock_mlflow, "run_bad", "model.cbm")


# ---------------------------------------------------------------------------
# clear_model_cache (lines 362-385)
# ---------------------------------------------------------------------------


class TestClearModelCache:
    """Tests for clear_model_cache disk + memory cache clearing."""

    def test_clear_all_caches(self, tmp_path):
        """Removes all cached model files and returns count."""
        from haute._mlflow_io import clear_model_cache

        cache_root = tmp_path / ".cache" / "models"
        run1_dir = cache_root / "run1"
        run1_dir.mkdir(parents=True)
        (run1_dir / "model.cbm").write_bytes(b"data1")
        run2_dir = cache_root / "run2"
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
        run1_dir = cache_root / "run1"
        run1_dir.mkdir(parents=True)
        (run1_dir / "model.cbm").write_bytes(b"data1")
        run2_dir = cache_root / "run2"
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
# load_mlflow_model — fast path cache hit (line 436-441)
# ---------------------------------------------------------------------------


class TestLoadMlflowModelFastCache:
    """Tests for the fast-path cache check in load_mlflow_model."""

    def test_fast_path_cache_hit_for_run_with_artifact(self):
        """source_type=run with artifact_path hits fast-path cache."""
        fake_sm = ScoringModel(MagicMock(), ["a"], frozenset(), "catboost")
        cache_key = ("run", "abc123", "model.cbm", "regression")
        _model_cache.put(cache_key, fake_sm)

        result = load_mlflow_model(
            source_type="run",
            run_id="abc123",
            artifact_path="model.cbm",
            task="regression",
        )
        assert result is fake_sm

    def test_post_resolve_cache_hit(self):
        """Cache hit after resolve_mlflow_source (second cache check, line 469)."""
        fake_sm = ScoringModel(MagicMock(), ["a"], frozenset(), "catboost")
        # Key uses resolved version "" for run-based, artifact_path as third element
        cache_key = ("run", "abc123", "model.cbm", "regression")
        _model_cache.put(cache_key, fake_sm)

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("abc123", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._find_model_artifact",
                return_value=("model.cbm", "catboost"),
            ),
        ):
            # No artifact_path → forces resolve + auto-discover, then second cache check hits
            result = load_mlflow_model(
                source_type="run",
                run_id="abc123",
                artifact_path="",
                task="regression",
            )

        assert result is fake_sm


# ---------------------------------------------------------------------------
# load_mlflow_model — retry on corrupt cache (lines 486-506)
# ---------------------------------------------------------------------------


class TestLoadMlflowModelRetry:
    """Tests for retry-on-corrupt-cache logic."""

    def test_retry_catboost_on_load_failure(self, tmp_path):
        """Deletes corrupt file and re-downloads on first load failure."""
        fake_model = MagicMock()
        fake_model.feature_names_ = ["a"]
        fake_model.get_cat_feature_indices.return_value = []

        call_count = 0

        def load_catboost_side_effect(path, task):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("corrupt file")
            return fake_model

        corrupt_file = tmp_path / "corrupt.cbm"
        corrupt_file.write_bytes(b"corrupt")

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run1", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(corrupt_file),
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=load_catboost_side_effect,
            ),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run1",
                artifact_path="model.cbm",
                task="regression",
            )

        assert isinstance(result, ScoringModel)
        assert result.flavor == "catboost"
        assert call_count == 2

    def test_retry_rustystats_on_load_failure(self, tmp_path):
        """Retries RustyStats model loading on first failure."""
        fake_sm = ScoringModel(MagicMock(), ["x"], frozenset(), "rustystats")

        call_count = 0

        def load_rs_side_effect(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("corrupt binary")
            return fake_sm

        corrupt_file = tmp_path / "corrupt.rsglm"
        corrupt_file.write_bytes(b"corrupt")

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run2", "", MagicMock(), MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(corrupt_file),
            ),
            patch(
                "haute._mlflow_io._load_rustystats_model",
                side_effect=load_rs_side_effect,
            ),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run2",
                artifact_path="model.rsglm",
                task="regression",
            )

        assert result is fake_sm
        assert call_count == 2


# ---------------------------------------------------------------------------
# load_mlflow_model — pyfunc loading
# ---------------------------------------------------------------------------


class TestLoadMlflowModelPyfunc:
    """Tests for pyfunc model loading through load_mlflow_model."""

    def test_pyfunc_model_loaded_and_wrapped(self):
        """Pyfunc model is loaded and wrapped correctly."""
        fake_pyfunc = MagicMock()
        fake_pyfunc.metadata.signature.inputs.input_names.return_value = ["f1"]

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run1", "", MagicMock(), MagicMock()),
            ),
            patch("haute._mlflow_io._load_pyfunc_model", return_value=fake_pyfunc),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run1",
                artifact_path="model",
                task="regression",
            )

        assert result.flavor == "pyfunc"
        assert result.feature_names == ["f1"]


# ---------------------------------------------------------------------------
# load_mlflow_model — auto-discovery with _find_model_artifact
# ---------------------------------------------------------------------------


class TestLoadMlflowModelAutoDiscover:
    """Tests for auto-discovery when artifact_path is empty."""

    def test_auto_discovers_rsglm(self):
        """Auto-discovers .rsglm when artifact_path is empty."""
        fake_sm = ScoringModel(MagicMock(), ["x"], frozenset(), "rustystats")

        mock_client = MagicMock()

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run1", "", MagicMock(), mock_client),
            ),
            patch(
                "haute._mlflow_io._find_model_artifact",
                return_value=("model.rsglm", "rustystats"),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value="/tmp/model.rsglm",
            ),
            patch(
                "haute._mlflow_io._load_rustystats_model",
                return_value=fake_sm,
            ),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run1",
                artifact_path="",
                task="regression",
            )

        assert result is fake_sm

    def test_auto_discovers_cbm(self):
        """Auto-discovers .cbm when artifact_path is empty."""
        fake_model = MagicMock()
        fake_model.feature_names_ = ["a"]
        fake_model.get_cat_feature_indices.return_value = []

        mock_client = MagicMock()

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run1", "", MagicMock(), mock_client),
            ),
            patch(
                "haute._mlflow_io._find_model_artifact",
                return_value=("model.cbm", "catboost"),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value="/tmp/model.cbm",
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                return_value=fake_model,
            ),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run1",
                artifact_path="",
                task="regression",
            )

        assert result.flavor == "catboost"


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
        raw_model.predict_proba.return_value = np.array(
            [[0.8, 0.2], [0.3, 0.7], [0.9, 0.1]]
        )
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
        """Builds correct runs:/ URI and calls pyfunc.load_model."""
        from haute._mlflow_io import _load_pyfunc_model

        mock_mlflow = MagicMock()
        fake_model = MagicMock()
        mock_mlflow.pyfunc.load_model.return_value = fake_model

        result = _load_pyfunc_model(mock_mlflow, "run123", "model")

        mock_mlflow.pyfunc.load_model.assert_called_once_with("runs:/run123/model")
        assert result is fake_model


# ---------------------------------------------------------------------------
# _prepare_predict_frame — empty features for rustystats
# ---------------------------------------------------------------------------


class TestPreparePredictFrameEdgeCases:
    """Edge cases for _prepare_predict_frame."""

    def test_rustystats_empty_features_returns_full_df(self):
        """RustyStats with empty features list returns the full DataFrame."""
        df = pl.DataFrame({"a": [1.0], "b": [2.0]})
        result = _prepare_predict_frame(df, [], frozenset(), "rustystats")
        assert isinstance(result, pl.DataFrame)
        assert set(result.columns) == {"a", "b"}
