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
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import ScoringModel
from haute._model_scorer import (
    FeatureMismatchError,
    ModelScorer,
    _batch_score_to_parquet,
    _clear_feature_validation_cache,
    _format_feature_mismatch,
    _register_temp_cleanup,
    _run_score_pipeline,
    _sink_to_temp,
    _validate_features,
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
        assert scorer.reuse_loaded_model is False

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
        assert err.context["missing"] == ["missing_col"]
        assert "a" in err.context["available"]
        assert "b" in err.context["available"]

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_all_features_present_scores_successfully(self, mock_load, mock_score_eager):
        """When all features are present, scoring proceeds normally."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        scorer.score(lf)

        mock_score_eager.assert_called_once()
        call_args = mock_score_eager.call_args
        features = call_args[0][2]
        assert "a" in features
        assert "b" in features

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_default_loads_model_on_each_score_call(self, mock_load, mock_score_eager):
        """Default scorer semantics keep delegating model cache policy to MLflow IO."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        scorer.score(lf)
        scorer.score(lf)

        assert mock_load.call_count == 2
        assert mock_score_eager.call_count == 2

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_reuses_loaded_model_for_repeated_score_calls(self, mock_load, mock_score_eager):
        """Streaming chunk callers reuse one ModelScorer; model loading should be once."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_load.return_value = sm
        mock_score_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(
            source_type="run",
            run_id="abc",
            source="live",
            reuse_loaded_model=True,
        )
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        scorer.score(lf)
        scorer.score(lf)

        mock_load.assert_called_once()
        assert mock_score_eager.call_count == 2

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_reuse_loaded_model_retries_after_failed_first_load(
        self,
        mock_load,
        mock_score_eager,
    ):
        """A failed first load must not poison the scorer's local cache."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_load.side_effect = [RuntimeError("boom"), sm]
        mock_score_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(
            source_type="run",
            run_id="abc",
            source="live",
            reuse_loaded_model=True,
        )
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()

        with pytest.raises(RuntimeError, match="boom"):
            scorer.score(lf)
        scorer.score(lf)

        assert mock_load.call_count == 2
        mock_score_eager.assert_called_once()

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_reuse_loaded_model_is_thread_safe(self, mock_load, mock_score_eager):
        """Concurrent streaming chunk calls should share one first model load."""
        sm = _make_scoring_model(feature_names=["a", "b"])

        def slow_load(**_: Any) -> ScoringModel:
            time.sleep(0.05)
            return sm

        mock_load.side_effect = slow_load
        mock_score_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()
        scorer = ModelScorer(
            source_type="run",
            run_id="abc",
            source="live",
            reuse_loaded_model=True,
        )
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        start = threading.Barrier(4)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                start.wait(timeout=5)
                scorer.score(lf)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        mock_load.assert_called_once()
        assert mock_score_eager.call_count == 4

    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_empty_input_raises_when_features_missing(self, mock_load, mock_score_eager):
        """score() with no dfs raises FeatureMismatchError when model expects features."""
        sm = _make_scoring_model()
        mock_load.return_value = sm

        scorer = ModelScorer(source_type="run", run_id="abc", source="live")
        with pytest.raises(FeatureMismatchError, match="Missing feature"):
            scorer.score()  # no dfs passed -- empty LazyFrame has no feature columns

    @patch("haute._user_exec._exec_user_code")
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
        """Without base_dir, config is resolved relative to CWD."""
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


# ===========================================================================
# FeatureMismatchError — message formatting
# ===========================================================================


class TestFeatureMismatchError:
    def test_basic_missing_features_message(self):
        """Diagnostic message lists missing features."""
        msg = _format_feature_mismatch(
            expected=["a", "b", "c"],
            available=["a"],
            missing=["b", "c"],
        )
        assert "3 feature(s)" in msg
        assert "1 column(s)" in msg
        assert "Missing feature(s) (2):" in msg
        assert "  - b" in msg
        assert "  - c" in msg

    def test_truncation_at_20_missing(self):
        """When more than 20 features are missing, message truncates with '... and N more'."""
        missing = [f"feat_{i}" for i in range(25)]
        msg = _format_feature_mismatch(
            expected=missing,
            available=[],
            missing=missing,
        )
        assert "  - feat_0" in msg
        assert "  - feat_19" in msg
        assert "feat_20" not in msg.split("... and")[0]
        assert "... and 5 more" in msg

    def test_type_mismatches_in_message(self):
        """Type mismatch section appears when type_mismatches are provided."""
        msg = _format_feature_mismatch(
            expected=["a", "b"],
            available=["a", "b"],
            missing=[],
            type_mismatches=[("a", "categorical (String)", "Int64")],
        )
        assert "Type mismatch(es):" in msg
        assert "'a': model expects categorical (String), got Int64" in msg

    def test_no_missing_no_type_mismatch(self):
        """Message is clean when no missing features and no type mismatches."""
        msg = _format_feature_mismatch(
            expected=["a"],
            available=["a", "b"],
            missing=[],
        )
        assert "Missing feature(s)" not in msg
        assert "Type mismatch" not in msg
        assert "These features were expected" in msg


# ===========================================================================
# _validate_features
# ===========================================================================


class TestValidateFeatures:
    def test_all_features_present_no_cats(self):
        """Returns all features as usable, empty missing list."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        schema = pl.Schema({"a": pl.Float64, "b": pl.Float64, "extra": pl.Utf8})
        usable, missing = _validate_features(sm, schema)
        assert usable == ["a", "b"]
        assert missing == []

    def test_missing_features_raises(self):
        """Raises FeatureMismatchError when features are missing."""
        sm = _make_scoring_model(feature_names=["a", "b", "c"])
        schema = pl.Schema({"a": pl.Float64, "b": pl.Float64})
        with pytest.raises(FeatureMismatchError) as exc_info:
            _validate_features(sm, schema)
        assert exc_info.value.context["missing"] == ["c"]

    def test_cache_key_includes_model_feature_contract(self):
        """A reused object id must not reuse another model's validation result."""
        schema = pl.Schema({"a": pl.Float64, "b": pl.Float64})
        sm_ok = _make_scoring_model(feature_names=["a", "b"])
        sm_missing = _make_scoring_model(feature_names=["a", "b", "c"])

        _clear_feature_validation_cache()
        try:
            with patch("haute._model_scorer.id", return_value=123, create=True):
                assert _validate_features(sm_ok, schema) == (["a", "b"], [])
                with pytest.raises(FeatureMismatchError) as exc_info:
                    _validate_features(sm_missing, schema)
            assert exc_info.value.context["missing"] == ["c"]
        finally:
            _clear_feature_validation_cache()

    def test_cache_key_accepts_list_cat_feature_names(self):
        """Executor scaffolds may provide list categorical metadata."""

        class ScoringModelLike:
            feature_names = ["a"]
            cat_feature_names = ["a"]

        schema = pl.Schema({"a": pl.String})
        assert _validate_features(ScoringModelLike(), schema) == (["a"], [])

    def test_no_usable_features_raises(self):
        """Raises FeatureMismatchError when no features match at all."""
        sm = _make_scoring_model(feature_names=["x", "y"])
        schema = pl.Schema({"a": pl.Float64, "b": pl.Float64})
        with pytest.raises(FeatureMismatchError) as exc_info:
            _validate_features(sm, schema)
        assert exc_info.value.context["missing"] == ["x", "y"]

    def test_cat_feature_type_mismatch_raises(self):
        """Item #13: categorical feature with numeric dtype must raise —
        silent cast would let wrong predictions through."""
        sm = _make_scoring_model(
            feature_names=["a", "b"],
            cat_feature_names=frozenset({"a"}),
        )
        # 'a' is expected categorical but schema has it as Int64 (numeric)
        schema = pl.Schema({"a": pl.Int64, "b": pl.Float64})
        with pytest.raises(FeatureMismatchError) as exc_info:
            _validate_features(sm, schema)
        # Context must surface the offending column so log consumers can act.
        ctx = exc_info.value.context
        assert ctx.get("type_mismatches")
        offenders = [col for col, *_ in ctx["type_mismatches"]]
        assert "a" in offenders

    def test_cat_feature_string_type_no_mismatch(self):
        """Categorical feature with String dtype does not trigger mismatch."""
        sm = _make_scoring_model(
            feature_names=["a", "b"],
            cat_feature_names=frozenset({"a"}),
        )
        schema = pl.Schema({"a": pl.Utf8, "b": pl.Float64})
        usable, missing = _validate_features(sm, schema)
        assert usable == ["a", "b"]


# ===========================================================================
# _run_score_pipeline
# ===========================================================================


class TestRunScorePipeline:
    @patch("haute._mlflow_io._score_eager")
    def test_live_source_routes_to_eager(self, mock_eager):
        """source='live' calls _score_eager."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = _run_score_pipeline(
            sm, lf, task="regression", output_col="prediction", source="live"
        )
        mock_eager.assert_called_once()
        assert isinstance(result, pl.LazyFrame)

    @patch("haute._model_scorer._score_batched_standalone")
    def test_non_live_routes_to_batched(self, mock_batched):
        """Non-live source routes to _score_batched_standalone."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_batched.return_value = pl.DataFrame({"a": [1]}).lazy()

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        _run_score_pipeline(sm, lf, task="regression", output_col="prediction", source="batch")
        mock_batched.assert_called_once()

    @patch("haute._mlflow_io._score_eager")
    def test_row_limit_forces_eager(self, mock_eager):
        """row_limit forces eager path even with non-live source."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_eager.return_value = pl.DataFrame({"a": [1]}).lazy()

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        _run_score_pipeline(
            sm,
            lf,
            task="regression",
            output_col="prediction",
            source="batch",
            row_limit=100,
        )
        mock_eager.assert_called_once()

    @patch("haute._mlflow_io._score_eager")
    def test_generic_exception_propagates_unwrapped(self, mock_eager):
        """Non-FeatureMismatchError exceptions propagate with their real type.

        Previously every non-FMEE failure inside scoring was re-wrapped as
        ``FeatureMismatchError`` — this laundered the real error type
        (``RuntimeError`` from a corrupt artifact, ``AttributeError``
        from a broken predict surface, etc.) behind a misleading mismatch
        message.  Post-narrowing, the real exception surfaces so on-call
        engineers see the actual failure class.
        """
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_eager.side_effect = RuntimeError("CatBoost internal error")

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        with pytest.raises(RuntimeError, match="CatBoost internal error"):
            _run_score_pipeline(sm, lf, task="regression", output_col="prediction", source="live")

    @patch("haute._mlflow_io._score_eager")
    def test_feature_mismatch_error_reraised_directly(self, mock_eager):
        """FeatureMismatchError from scoring propagates unchanged.

        The explicit catch-and-rewrap was removed; FMEE now takes the
        same un-caught path as every other exception, which preserves
        the original instance and leaves ``__cause__`` as ``None``.
        """
        sm = _make_scoring_model(feature_names=["a", "b"])
        original_err = FeatureMismatchError(
            expected=["a", "b"],
            available=["a", "b"],
            missing=[],
        )
        mock_eager.side_effect = original_err

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        with pytest.raises(FeatureMismatchError) as exc_info:
            _run_score_pipeline(sm, lf, task="regression", output_col="prediction", source="live")
        # Should be the same error, not wrapped
        assert exc_info.value is original_err
        assert exc_info.value.__cause__ is None

    @patch("haute._user_exec._exec_user_code")
    @patch("haute._mlflow_io._score_eager")
    def test_post_processing_code_executed(self, mock_eager, mock_exec):
        """User code is executed after scoring."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()
        mock_exec.return_value = pl.DataFrame({"result": [42]}).lazy()

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        _run_score_pipeline(
            sm,
            lf,
            task="regression",
            output_col="prediction",
            source="live",
            code="result = result * 2",
            source_names=["df"],
        )
        mock_exec.assert_called_once()
        # Verify model in extra_ns
        call_kwargs = mock_exec.call_args[1]
        assert "model" in call_kwargs["extra_ns"]

    @patch("haute._user_exec._exec_user_code")
    @patch("haute._mlflow_io._score_eager")
    def test_source_names_none_becomes_empty_list_in_code(self, mock_eager, mock_exec):
        """source_names=None is converted to [] when passed to user code."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_eager.return_value = pl.DataFrame({"a": [1]}).lazy()
        mock_exec.return_value = pl.DataFrame({"r": [1]}).lazy()

        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        _run_score_pipeline(
            sm,
            lf,
            task="regression",
            output_col="pred",
            source="live",
            code="x=1",
            source_names=None,
        )
        # The second positional arg to _exec_user_code should be []
        call_args = mock_exec.call_args[0]
        assert call_args[1] == []


# ===========================================================================
# _register_temp_cleanup
# ===========================================================================


class TestRegisterTempCleanup:
    def test_registers_path_for_cleanup(self):
        """_register_temp_cleanup adds a path to the cleanup set."""
        import haute._model_scorer as mod

        original = mod._temp_files_to_clean.copy()
        try:
            _register_temp_cleanup("/fake/path/for_test.parquet")
            assert "/fake/path/for_test.parquet" in mod._temp_files_to_clean
        finally:
            mod._temp_files_to_clean.discard("/fake/path/for_test.parquet")
            mod._temp_files_to_clean.update(original & mod._temp_files_to_clean)


# ===========================================================================
# _score_batched_standalone
# ===========================================================================


class TestScoreBatchedStandalone:
    @patch("haute._mlflow_io._score_eager")
    @patch("haute._mlflow_io.load_mlflow_model")
    def test_batched_standalone_creates_temp_and_cleans_input(self, mock_load, mock_eager):
        """_score_batched method creates temp files and returns a LazyFrame."""
        sm = _make_scoring_model(
            feature_names=["a", "b"],
            predictions=np.array([0.1, 0.2]),
        )
        mock_load.return_value = sm

        scorer = ModelScorer(source_type="run", run_id="abc", source="batch")
        lf = pl.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]}).lazy()
        result = scorer._score_batched(sm, lf, ["a", "b"])
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert "prediction" in collected.columns
        assert len(collected) == 2


# ===========================================================================
# ModelScorer._score_eager
# ===========================================================================


class TestModelScorerScoreEager:
    @patch("haute._mlflow_io._score_eager")
    def test_score_eager_delegates(self, mock_eager):
        """_score_eager delegates to the shared helper."""
        sm = _make_scoring_model(feature_names=["a", "b"])
        mock_eager.return_value = pl.DataFrame({"a": [1], "prediction": [0.5]}).lazy()

        scorer = ModelScorer(source_type="run", run_id="abc", task="regression")
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = scorer._score_eager(sm, lf, ["a", "b"])

        mock_eager.assert_called_once_with(sm, lf, ["a", "b"], "prediction", "regression")
        assert isinstance(result, pl.LazyFrame)


# ===========================================================================
# _batch_score_to_parquet — empty input
# ===========================================================================


class TestBatchScoreToParquetMultiBatch:
    def test_multiple_batches_write_correctly(self, tmp_path):
        """Multiple batch iterations go through writer-already-initialized path."""
        import haute._model_scorer as mod

        # Use a tiny batch size to force multiple iterations
        original_batch_size = mod._SCORE_BATCH_SIZE
        mod._SCORE_BATCH_SIZE = 2  # Force 2 rows per batch

        input_path = str(tmp_path / "multi.parquet")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "b": [6.0, 7.0, 8.0, 9.0, 10.0]})
        df.write_parquet(input_path)

        sm = _make_scoring_model(feature_names=["a", "b"])
        # predict will be called multiple times, once per batch
        sm._model.predict.side_effect = [
            np.array([0.1, 0.2]),
            np.array([0.3, 0.4]),
            np.array([0.5]),
        ]

        try:
            out_path = _batch_score_to_parquet(
                sm,
                input_path,
                ["a", "b"],
                "pred",
                "regression",
            )
            result = pl.read_parquet(out_path)
            assert len(result) == 5
            assert "pred" in result.columns
            # predict should have been called 3 times (2+2+1)
            assert sm._model.predict.call_count == 3
            os.unlink(out_path)
        finally:
            mod._SCORE_BATCH_SIZE = original_batch_size


class TestBatchScoreToParquetSeriesConversion:
    def test_single_column_arrow_batch_handled(self, tmp_path):
        """Parquet with one column: pl.from_arrow may return Series; it gets converted."""
        input_path = str(tmp_path / "single_col.parquet")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        df.write_parquet(input_path)

        sm = _make_scoring_model(
            feature_names=["a"],
            predictions=np.array([0.1, 0.2, 0.3]),
        )

        # Patch pl.from_arrow to return a Series for the first call (simulating edge case)
        original_from_arrow = pl.from_arrow

        call_count = [0]

        def patched_from_arrow(batch):
            result = original_from_arrow(batch)
            call_count[0] += 1
            if isinstance(result, pl.DataFrame) and len(result.columns) == 1:
                return result.to_series(0)
            return result

        with patch("haute._model_scorer.pl.from_arrow", side_effect=patched_from_arrow):
            out_path = _batch_score_to_parquet(
                sm,
                input_path,
                ["a"],
                "pred",
                "regression",
            )

        try:
            result = pl.read_parquet(out_path)
            assert "pred" in result.columns
            assert len(result) == 3
        finally:
            os.unlink(out_path)


class TestBatchScoreToParquetEmpty:
    def test_empty_input_produces_empty_parquet_regression(self, tmp_path):
        """Empty input file produces valid empty parquet with correct schema."""
        input_path = str(tmp_path / "empty_input.parquet")
        df = pl.DataFrame(
            {"a": pl.Series([], dtype=pl.Float64), "b": pl.Series([], dtype=pl.Float64)}
        )
        df.write_parquet(input_path)

        sm = _make_scoring_model(
            feature_names=["a", "b"],
            predictions=np.array([]),
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
            assert len(result) == 0
            assert "pred" in result.columns
            assert "a" in result.columns
        finally:
            os.unlink(out_path)

    def test_empty_input_classification_includes_proba_col(self, tmp_path):
        """Empty classification input produces empty parquet with proba column."""
        input_path = str(tmp_path / "empty_cls.parquet")
        df = pl.DataFrame(
            {"a": pl.Series([], dtype=pl.Float64), "b": pl.Series([], dtype=pl.Float64)}
        )
        df.write_parquet(input_path)

        sm = _make_scoring_model(
            feature_names=["a", "b"],
            predictions=np.array([]),
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
            assert len(result) == 0
            assert "pred" in result.columns
            assert "pred_proba" in result.columns
        finally:
            os.unlink(out_path)
