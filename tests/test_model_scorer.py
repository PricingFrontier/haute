"""Comprehensive tests for haute._model_scorer.

Covers:
  - ModelScorer construction and defaults
  - ModelScorer.score — eager vs batched routing
  - ModelScorer._score_eager delegation
  - _sink_to_temp helper
  - _batch_score_to_parquet helper
  - score_from_config thin delegation
  - Error cases: missing model features, empty input

All MLflow and CatBoost dependencies are mocked.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import numpy as np
import polars as pl

from haute._mlflow_io import ScoringModel
from haute._model_scorer import (
    FeatureMismatchError,
    ModelScorer,
    _batch_score_to_parquet,
    _sink_to_temp,
    score_from_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_model(
    feature_names: list[str] | None = None,
    predictions: Any = None,
    probas: Any = None,
) -> MagicMock:
    """Create a mock model with predict / predict_proba / feature_names_."""
    model = MagicMock()
    model.feature_names_ = feature_names or ["a", "b"]
    if predictions is not None:
        model.predict.return_value = np.array(predictions)
    else:
        model.predict.return_value = np.array([0.5, 0.6, 0.7])
    if probas is not None:
        model.predict_proba.return_value = np.array(probas)
    else:
        # Default: no predict_proba
        del model.predict_proba
    return model


def _make_scoring_model(
    feature_names: list[str] | None = None,
    cat_feature_names: frozenset[str] | None = None,
    predictions: Any = None,
    probas: Any = None,
    flavor: str = "catboost",
) -> ScoringModel:
    """Create a ScoringModel wrapping a mock model."""
    model = _make_mock_model(feature_names, predictions, probas)
    return ScoringModel(
        model=model,
        feature_names=feature_names or ["a", "b"],
        cat_feature_names=cat_feature_names or frozenset(),
        flavor=flavor,
    )


# ===========================================================================
# ModelScorer construction
# ===========================================================================


class TestModelScorerInit:
    def test_defaults(self):
        scorer = ModelScorer(source_type="run")
        assert scorer.source_type == "run"
        assert scorer.run_id == ""
        assert scorer.artifact_path == ""
        assert scorer.registered_model == ""
        assert scorer.version == "latest"
        assert scorer.task == "regression"
        assert scorer.output_col == "prediction"
        assert scorer.code == ""
        assert scorer.source_names == []
        assert scorer.source == "live"
        assert scorer.row_limit is None

    def test_custom_values(self):
        scorer = ModelScorer(
            source_type="registered",
            registered_model="my_model",
            version="3",
            task="classification",
            output_col="pred",
            code="x = 1",
            source_names=["df1", "df2"],
            source="test_batch",
            row_limit=100,
        )
        assert scorer.source_type == "registered"
        assert scorer.registered_model == "my_model"
        assert scorer.version == "3"
        assert scorer.task == "classification"
        assert scorer.source_names == ["df1", "df2"]
        assert scorer.row_limit == 100

    def test_source_names_none_becomes_empty_list(self):
        scorer = ModelScorer(source_type="run", source_names=None)
        assert scorer.source_names == []


# ===========================================================================
# ModelScorer.score — routing logic
# ===========================================================================


class TestModelScorerScore:
    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_live_scenario_uses_eager(self, mock_load, mock_score_eager):
        sm = _make_scoring_model()
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"x": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = scorer.score(lf)

        mock_score_eager.assert_called_once()
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert "prediction" in collected.columns

    @patch("haute._model_scorer._score_batched_standalone")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_non_live_scenario_uses_batched(self, mock_load, mock_batched):
        sm = _make_scoring_model()
        mock_load.return_value = sm
        mock_batched.return_value = pl.DataFrame({"x": [1]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", source="batch")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = scorer.score(lf)

        mock_batched.assert_called_once()
        assert isinstance(result, pl.LazyFrame)

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_row_limit_forces_eager(self, mock_load, mock_score_eager):
        """Even non-live scenario uses eager when row_limit is set."""
        sm = _make_scoring_model()
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"x": [1]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", source="batch", row_limit=10)
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = scorer.score(lf)

        mock_score_eager.assert_called_once()
        assert isinstance(result, pl.LazyFrame)

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_feature_intersection_raises_on_missing(self, mock_load, mock_score_eager):
        """Missing features raise FeatureMismatchError with clear diagnostics."""
        sm = _make_scoring_model(feature_names=["a", "b", "missing_col"])
        mock_load.return_value = sm

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        with pytest.raises(FeatureMismatchError, match="missing_col") as exc_info:
            scorer.score(lf)

        err = exc_info.value
        assert err.missing == ["missing_col"]
        assert "a" in err.available
        assert "b" in err.available

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_all_features_present_scores_successfully(self, mock_load, mock_score_eager):
        """When all features are present, scoring proceeds normally."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = scorer.score(lf)

        mock_score_eager.assert_called_once()
        call_args = mock_score_eager.call_args
        features = call_args[0][2]
        assert "a" in features
        assert "b" in features

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_empty_input_raises_when_features_missing(self, mock_load, mock_score_eager):
        """score() with no dfs raises FeatureMismatchError when model expects features."""
        sm = _make_scoring_model()
        mock_load.return_value = sm

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        with pytest.raises(FeatureMismatchError, match="Missing feature"):
            scorer.score()  # no dfs passed -- empty LazyFrame has no feature columns

    @patch("haute.executor._exec_user_code")
    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_user_code_applied_after_scoring(self, mock_load, mock_score_eager, mock_exec):
        """Post-processing user code should be applied after scoring."""
        sm = _make_scoring_model()
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"x": [1]}).lazy()
        mock_exec.return_value = pl.DataFrame({"result": [1]}).lazy()

        scorer = ModelScorer(
            source_type="run",
            run_id="abc",
            source="live",
            code="result = result * 2",
            source_names=["df"],
        )
        scorer.score(pl.DataFrame({"a": [1], "b": [2]}).lazy())

        mock_exec.assert_called_once()
        # Verify model is in extra_ns
        call_kwargs = mock_exec.call_args
        assert "model" in call_kwargs[1]["extra_ns"]


# ===========================================================================
# _sink_to_temp
# ===========================================================================


class TestSinkToTemp:
    def test_basic_sink(self):
        lf = pl.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]}).lazy()
        path = _sink_to_temp(lf)
        try:
            assert os.path.exists(path)
            assert path.endswith(".parquet")
            df = pl.read_parquet(path)
            assert len(df) == 3
            assert set(df.columns) == {"x", "y"}
        finally:
            os.unlink(path)

    def test_fallback_on_sink_failure(self):
        """If sink_parquet fails with a Polars error, fallback to collect+write_parquet."""
        lf = MagicMock(spec=pl.LazyFrame)
        lf.sink_parquet.side_effect = pl.exceptions.ComputeError("sink failed")

        # Set up the fallback path
        mock_df = pl.DataFrame({"x": [1]})
        lf.collect.return_value = mock_df

        path = _sink_to_temp(lf)
        try:
            assert os.path.exists(path)
            df = pl.read_parquet(path)
            assert len(df) == 1
        finally:
            os.unlink(path)


# ===========================================================================
# _batch_score_to_parquet
# ===========================================================================


class TestBatchScoreToParquet:
    def test_regression_scoring(self, tmp_path):
        """Batch scoring produces output parquet with prediction column."""
        input_path = str(tmp_path / "input.parquet")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
        df.write_parquet(input_path)

        sm = _make_scoring_model(
            feature_names=["a", "b"],
            predictions=np.array([0.1, 0.2, 0.3]),
        )

        out_path = _batch_score_to_parquet(
            sm,
            input_path,
            ["a", "b"],
            "pred",
            "regression",
        )

        try:
            result = pl.read_parquet(out_path)
            assert "pred" in result.columns
            assert len(result) == 3
        finally:
            os.unlink(out_path)

    def test_classification_with_proba(self, tmp_path):
        """Classification with predict_proba produces both pred and pred_proba columns."""
        input_path = str(tmp_path / "input.parquet")
        df = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        df.write_parquet(input_path)

        sm = _make_scoring_model(
            feature_names=["a", "b"],
            predictions=np.array([0, 1]),
            probas=np.array([[0.3, 0.7], [0.4, 0.6]]),
        )

        out_path = _batch_score_to_parquet(
            sm,
            input_path,
            ["a", "b"],
            "pred",
            "classification",
        )

        try:
            result = pl.read_parquet(out_path)
            assert "pred" in result.columns
            assert "pred_proba" in result.columns
            assert len(result) == 2
        finally:
            os.unlink(out_path)

    def test_classification_without_proba(self, tmp_path):
        """Classification model without predict_proba only produces pred column."""
        input_path = str(tmp_path / "input.parquet")
        df = pl.DataFrame({"a": [1.0], "b": [2.0]})
        df.write_parquet(input_path)

        sm = _make_scoring_model(
            feature_names=["a", "b"],
            predictions=np.array([1]),
        )
        # No predict_proba

        out_path = _batch_score_to_parquet(
            sm,
            input_path,
            ["a", "b"],
            "pred",
            "classification",
        )

        try:
            result = pl.read_parquet(out_path)
            assert "pred" in result.columns
            assert "pred_proba" not in result.columns
        finally:
            os.unlink(out_path)


# ===========================================================================
# ScoringModel
# ===========================================================================


class TestScoringModel:
    def test_catboost_flavor(self):
        """CatBoost ScoringModel with categorical features."""
        sm = _make_scoring_model(
            feature_names=["a", "b", "c"],
            cat_feature_names=frozenset({"c"}),
            flavor="catboost",
        )
        assert sm.feature_names == ["a", "b", "c"]
        assert sm.cat_feature_names == frozenset({"c"})
        assert sm.flavor == "catboost"

    def test_pyfunc_flavor(self):
        """Pyfunc ScoringModel with no categorical features."""
        sm = _make_scoring_model(
            feature_names=["x", "y"],
            flavor="pyfunc",
        )
        assert sm.feature_names == ["x", "y"]
        assert sm.cat_feature_names == frozenset()
        assert sm.flavor == "pyfunc"

    def test_predict_flattens(self):
        """predict() returns 1-D array regardless of model output shape."""
        sm = _make_scoring_model(predictions=np.array([[0.1], [0.2]]))
        result = sm.predict(np.array([[1, 2], [3, 4]]))
        assert result.ndim == 1
        assert len(result) == 2

    def test_predict_proba_returns_none_when_missing(self):
        """predict_proba returns None for models without it."""
        sm = _make_scoring_model()
        assert sm.predict_proba(np.array([[1, 2]])) is None

    def test_predict_proba_returns_array(self):
        """predict_proba returns ndarray when model supports it."""
        sm = _make_scoring_model(probas=np.array([[0.3, 0.7]]))
        result = sm.predict_proba(np.array([[1, 2]]))
        assert result is not None
        assert isinstance(result, np.ndarray)

    def test_getattr_proxies_to_raw_model(self):
        """Attribute access falls through to the underlying model."""
        sm = _make_scoring_model()
        # Access CatBoost-specific attribute via proxy
        assert sm.feature_names_ == ["a", "b"]

    def test_raw_model_property(self):
        """raw_model exposes the underlying model object."""
        sm = _make_scoring_model()
        assert sm.raw_model is sm._model


# ===========================================================================
# score_from_config
# ===========================================================================


class TestScoreFromConfig:
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_reads_config_and_delegates(self, mock_load, tmp_path):
        """score_from_config reads JSON config and scores via ModelScorer."""
        sm = _make_scoring_model(predictions=np.array([0.42]))
        mock_load.return_value = sm

        config_path = tmp_path / "config" / "model_scoring" / "test.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "abc123",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "pred",
                }
            )
        )

        lf = pl.DataFrame({"a": [1.0], "b": [2.0]}).lazy()
        result = score_from_config(lf, config=str(config_path), base_dir=str(tmp_path))
        collected = result.collect()

        assert "pred" in collected.columns
        mock_load.assert_called_once_with(
            source_type="run",
            run_id="abc123",
            artifact_path="model.cbm",
            registered_model="",
            version="latest",
            task="regression",
        )

    # ---------------------------------------------------------------
    # B18: base_dir parameter — resolve config relative to caller
    # ---------------------------------------------------------------

    @patch("haute._mlflow_io.load_mlflow_model")
    def test_base_dir_resolves_relative_config(self, mock_load, tmp_path):
        """With base_dir, a relative config path is resolved against base_dir, not CWD."""
        sm = _make_scoring_model(predictions=np.array([0.5]))
        mock_load.return_value = sm

        # Create config in tmp_path/config/model_scoring/test.json
        config_rel = "config/model_scoring/test.json"
        config_abs = tmp_path / config_rel
        config_abs.parent.mkdir(parents=True)
        config_abs.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "r1",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "pred",
                }
            )
        )

        lf = pl.DataFrame({"a": [1.0], "b": [2.0]}).lazy()
        # Call with base_dir pointing to tmp_path — even if CWD is different
        result = score_from_config(lf, config=config_rel, base_dir=str(tmp_path))
        collected = result.collect()
        assert "pred" in collected.columns

    @patch("haute._mlflow_io.load_mlflow_model")
    def test_base_dir_none_falls_back_to_cwd(self, mock_load, tmp_path, monkeypatch):
        """Without base_dir, config is resolved relative to CWD (backward compat)."""
        sm = _make_scoring_model(predictions=np.array([0.5]))
        mock_load.return_value = sm

        # Put config in tmp_path and chdir there
        config_rel = "config/model_scoring/test.json"
        config_abs = tmp_path / config_rel
        config_abs.parent.mkdir(parents=True)
        config_abs.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "r2",
                    "artifact_path": "model",
                    "task": "regression",
                    "output_column": "pred",
                }
            )
        )

        monkeypatch.chdir(tmp_path)
        lf = pl.DataFrame({"a": [1.0], "b": [2.0]}).lazy()
        result = score_from_config(lf, config=config_rel)
        collected = result.collect()
        assert "pred" in collected.columns

    @patch("haute._mlflow_io.load_mlflow_model")
    def test_base_dir_validates_absolute_config(self, mock_load, tmp_path):
        """Absolute config path outside base_dir is now rejected."""
        sm = _make_scoring_model(predictions=np.array([0.5]))
        mock_load.return_value = sm

        config_abs = tmp_path / "config" / "model_scoring" / "test.json"
        config_abs.parent.mkdir(parents=True)
        config_abs.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "r3",
                    "artifact_path": "model",
                    "task": "regression",
                    "output_column": "pred",
                }
            )
        )

        lf = pl.DataFrame({"a": [1.0], "b": [2.0]}).lazy()
        # Absolute path with a base_dir that doesn't contain it — now rejected
        with pytest.raises(ValueError, match="outside project root"):
            score_from_config(
                lf,
                config=str(config_abs),
                base_dir="/nonexistent",
            )

    def test_base_dir_with_missing_config_raises(self, tmp_path):
        """FileNotFoundError when base_dir + config doesn't exist."""
        import pytest as pt

        lf = pl.DataFrame({"a": [1.0]}).lazy()
        with pt.raises(FileNotFoundError):
            score_from_config(
                lf,
                config="config/missing.json",
                base_dir=str(tmp_path),
            )
