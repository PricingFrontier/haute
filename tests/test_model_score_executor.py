"""Tests for MODEL_SCORE executor node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import ScoringModel, _artifact_cache_path
from haute.executor import _build_node_fn, execute_graph
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from tests.conftest import make_edge, make_file_input_config, make_graph

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _make_model_score_graph(
    *,
    source_type: str = "run",
    run_id: str = "abc123",
    artifact_path: str = "model.cbm",
    registered_model: str = "",
    version: str = "latest",
    task: str = "regression",
    output_column: str = "prediction",
    code: str = "",
    data_path: str = "",
) -> PipelineGraph:
    """Build a 2-node graph: dataInput → modelScore."""
    config = {
        "sourceType": source_type,
        "run_id": run_id,
        "artifact_path": artifact_path,
        "registered_model": registered_model,
        "version": version,
        "task": task,
        "output_column": output_column,
        "code": code,
    }
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(data_path),
                    },
                },
                {
                    "id": "score",
                    "data": {"label": "score", "nodeType": "modelScore", "config": config},
                },
            ],
            "edges": [make_edge("source", "score").model_dump()],
        }
    )


@pytest.fixture()
def sample_data(tmp_path):
    """Create a small parquet file with features."""
    df = pl.DataFrame(
        {
            "x1": [1.0, 2.0, 3.0, 4.0, 5.0],
            "x2": [10.0, 20.0, 30.0, 40.0, 50.0],
            "id": [1, 2, 3, 4, 5],
        }
    )
    path = tmp_path / "data.parquet"
    df.write_parquet(path)
    return str(path)


def _make_mock_model(task: str = "regression", feature_names: list[str] | None = None):
    """Create a ScoringModel wrapping a mock CatBoost model."""
    fnames = feature_names or ["x1", "x2"]
    model = MagicMock()
    model.feature_names_ = fnames
    model.predict.return_value = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    model.get_cat_feature_indices.return_value = []
    if task == "classification":
        model.predict_proba.return_value = np.array(
            [
                [0.9, 0.1],
                [0.8, 0.2],
                [0.3, 0.7],
                [0.6, 0.4],
                [0.2, 0.8],
            ]
        )
    else:
        del model.predict_proba
    return ScoringModel(
        model=model,
        feature_names=fnames,
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


# ---------------------------------------------------------------------------
# Auto-predict regression
# ---------------------------------------------------------------------------


class TestModelScoreAutoPredictRegression:
    def test_adds_prediction_column(self, sample_data):
        """Model score node adds prediction column to output."""
        mock_model = _make_mock_model("regression")

        graph = _make_model_score_graph(data_path=sample_data)
        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            results = execute_graph(graph, target_node_id="score", row_limit=100)

        assert results["score"].status == "ok"
        cols = [c.name for c in results["score"].columns]
        assert "prediction" in cols
        assert results["score"].row_count == 5

    def test_custom_output_column(self, sample_data):
        """Custom output_column name is used."""
        mock_model = _make_mock_model("regression")

        graph = _make_model_score_graph(data_path=sample_data, output_column="my_pred")
        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            results = execute_graph(graph, target_node_id="score", row_limit=100)

        cols = [c.name for c in results["score"].columns]
        assert "my_pred" in cols


# ---------------------------------------------------------------------------
# Classification with proba
# ---------------------------------------------------------------------------


class TestModelScoreClassification:
    def test_adds_proba_column(self, sample_data):
        """Classification model adds _proba column."""
        mock_model = _make_mock_model("classification")

        graph = _make_model_score_graph(
            data_path=sample_data,
            task="classification",
        )
        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            results = execute_graph(graph, target_node_id="score", row_limit=100)

        assert results["score"].status == "ok"
        cols = [c.name for c in results["score"].columns]
        assert "prediction" in cols
        assert "prediction_proba" in cols


# ---------------------------------------------------------------------------
# Passthrough when no config
# ---------------------------------------------------------------------------


class TestModelScorePassthrough:
    def test_empty_config_passthrough(self, sample_data):
        """Empty config (no sourceType) acts as passthrough."""
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": make_file_input_config(sample_data),
                        },
                    },
                    {
                        "id": "score",
                        "data": {"label": "score", "nodeType": "modelScore", "config": {}},
                    },
                ],
                "edges": [make_edge("source", "score").model_dump()],
            }
        )
        results = execute_graph(graph, target_node_id="score", row_limit=100)
        assert results["score"].status == "ok"
        # Same columns as source (passthrough)
        cols = [c.name for c in results["score"].columns]
        assert "x1" in cols
        assert "prediction" not in cols

    def test_run_without_run_id_passthrough(self, sample_data):
        """sourceType=run without run_id is a passthrough."""
        graph = _make_model_score_graph(
            data_path=sample_data,
            source_type="run",
            run_id="",
        )
        results = execute_graph(graph, target_node_id="score", row_limit=100)
        assert results["score"].status == "ok"
        cols = [c.name for c in results["score"].columns]
        assert "prediction" not in cols

    def test_registered_without_model_passthrough(self, sample_data):
        """sourceType=registered without registered_model is a passthrough."""
        graph = _make_model_score_graph(
            data_path=sample_data,
            source_type="registered",
            registered_model="",
            run_id="",
        )
        results = execute_graph(graph, target_node_id="score", row_limit=100)
        assert results["score"].status == "ok"
        cols = [c.name for c in results["score"].columns]
        assert "prediction" not in cols


# ---------------------------------------------------------------------------
# Missing features
# ---------------------------------------------------------------------------


class TestModelScoreMissingFeatures:
    def test_partial_features(self, sample_data):
        """Missing model features surface as a ``ContractMismatchError``.

        Column-contract enforcement (Phase 2 Wave 4 item #57) catches
        this before the node runs: the model's feature list is known
        from the loaded scoring model, so the referenced-column
        contract names ``x_missing`` — and the upstream frame doesn't
        have it, so the boundary check raises with the exact name.
        Previously this surfaced as a scorer-side runtime error
        captured in ``results["score"].error``; the new path is
        strictly louder and names the column in the exception itself.
        """
        import pytest

        from haute.errors import ContractMismatchError

        sm = _make_mock_model("regression", feature_names=["x1", "x2", "x_missing"])
        sm._model.predict.return_value = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

        graph = _make_model_score_graph(data_path=sample_data)
        with patch("haute._mlflow_io.load_mlflow_model", return_value=sm):
            with pytest.raises(ContractMismatchError) as excinfo:
                execute_graph(graph, target_node_id="score", row_limit=100)

        assert "x_missing" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Post-processing code
# ---------------------------------------------------------------------------


class TestModelScorePostProcessing:
    def test_with_postprocessing_code(self, sample_data):
        """User code modifies output after prediction."""
        mock_model = _make_mock_model("regression")

        graph = _make_model_score_graph(
            data_path=sample_data,
            code='df = df.with_columns(doubled=pl.col("prediction") * 2)',
        )
        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            results = execute_graph(graph, target_node_id="score", row_limit=100)

        assert results["score"].status == "ok"
        cols = [c.name for c in results["score"].columns]
        assert "prediction" in cols
        assert "doubled" in cols

    def test_model_accessible_in_postprocessing_scope(self, sample_data):
        """User post-processing code can access the loaded model object.

        The model is exposed to user code as a ``ScoringModel`` carrier with
        declared attributes (``feature_names``, ``cat_feature_names``,
        ``flavor``, ``raw_model``).  Flavor-native attribute names (e.g.
        CatBoost's ``feature_names_``) are reachable via ``model.raw_model``.
        """
        mock_model = _make_mock_model("regression")

        graph = _make_model_score_graph(
            data_path=sample_data,
            code="df = df.with_columns(n_features=pl.lit(len(model.feature_names)))",
        )
        with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
            results = execute_graph(graph, target_node_id="score", row_limit=100)

        assert results["score"].status == "ok"
        cols = [c.name for c in results["score"].columns]
        assert "n_features" in cols


# ---------------------------------------------------------------------------
# _build_node_fn unit tests
# ---------------------------------------------------------------------------


class TestBuildNodeFnModelScore:
    def test_build_node_fn_returns_passthrough_no_config(self):
        """_build_node_fn returns passthrough for empty modelScore config."""
        node = GraphNode(
            id="ms1",
            data=NodeData(label="scorer", nodeType="modelScore", config={}),
        )
        func_name, fn, is_source = _build_node_fn(node, source_names=["upstream"])
        assert func_name == "scorer"
        assert is_source is False
        # Passthrough returns input
        lf = pl.DataFrame({"a": [1]}).lazy()
        result = fn(lf)
        assert result.collect().to_dicts() == [{"a": 1}]

    def test_build_node_fn_returns_scoring_fn(self):
        """_build_node_fn returns a scoring function when config is present."""
        node = GraphNode(
            id="ms1",
            data=NodeData(
                label="scorer",
                nodeType="modelScore",
                config={
                    "sourceType": "run",
                    "run_id": "abc",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "pred",
                },
            ),
        )
        func_name, fn, is_source = _build_node_fn(node, source_names=["upstream"])
        assert func_name == "scorer"
        assert is_source is False
        # fn should be the model_score_fn (not passthrough) — callable
        assert callable(fn)


# ---------------------------------------------------------------------------
# Scenario-based scoring path selection
# ---------------------------------------------------------------------------


class TestModelScoreScenarioRouting:
    """scenario='live' → eager (in-memory), any other → batched (parquet)."""

    def test_live_scenario_uses_eager_path(self, sample_data):
        """Live scenario scores in-memory — no temp parquet files created."""
        sm = _make_mock_model("regression")

        graph = _make_model_score_graph(data_path=sample_data)

        with (
            patch("haute._mlflow_io.load_mlflow_model", return_value=sm),
            patch(
                "haute._model_scorer._sink_to_temp",
                side_effect=AssertionError("Live scenario should stay on the eager path"),
            ),
            patch(
                "haute._model_scorer._batch_score_to_parquet",
                side_effect=AssertionError("Live scenario should not batch-score via parquet"),
            ),
        ):
            results = execute_graph(graph, target_node_id="score", source="live")

        assert results["score"].status == "ok"
        assert results["score"].row_count == 5

        # Eager path calls predict once with the full dataset
        sm._model.predict.assert_called_once()

    def test_batch_scenario_uses_parquet_path(self, sample_data):
        """Non-live scenario sinks to parquet, batch-scores, then scans back."""
        sm = _make_mock_model("regression")

        graph = _make_model_score_graph(data_path=sample_data)

        with patch("haute._mlflow_io.load_mlflow_model", return_value=sm):
            results = execute_graph(graph, target_node_id="score", source="test_batch")

        assert results["score"].status == "ok"
        assert results["score"].row_count == 5
        cols = [c.name for c in results["score"].columns]
        assert "prediction" in cols

        # Batched path goes through _batch_score_to_parquet which calls predict
        # on chunks — still should have been called at least once
        assert sm._model.predict.call_count >= 1

    def test_row_limited_preview_uses_eager_path(self, sample_data):
        """Non-live scenario with row_limit still uses eager path (preview)."""
        mock_model = _make_mock_model("regression")

        graph = _make_model_score_graph(data_path=sample_data)

        with (
            patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model),
            patch(
                "haute._model_scorer._sink_to_temp",
                side_effect=AssertionError("Row-limited preview should stay on the eager path"),
            ),
            patch(
                "haute._model_scorer._batch_score_to_parquet",
                side_effect=AssertionError(
                    "Row-limited preview should not batch-score via parquet"
                ),
            ),
        ):
            results = execute_graph(
                graph,
                target_node_id="score",
                source="nb_batch",
                row_limit=1000,
            )

        assert results["score"].status == "ok"


# ---------------------------------------------------------------------------
# Shared scoring helpers
# ---------------------------------------------------------------------------


class TestScoreEager:
    """Tests for the shared _score_eager / _prepare_predict_frame helpers."""

    def _mock_scoring_model(self, task="regression", n_rows=5):
        model = MagicMock()
        model.feature_names_ = ["x1", "x2"]
        model.predict.return_value = np.arange(n_rows, dtype=float)
        model.get_cat_feature_indices.return_value = []
        if task == "classification":
            model.predict_proba.return_value = np.column_stack(
                [
                    np.linspace(0.9, 0.1, n_rows),
                    np.linspace(0.1, 0.9, n_rows),
                ]
            )
        else:
            del model.predict_proba
        return ScoringModel(
            model=model,
            feature_names=["x1", "x2"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )

    def test_score_eager_adds_prediction(self):
        from haute._mlflow_io import _score_eager

        sm = self._mock_scoring_model(n_rows=2)
        lf = pl.DataFrame({"x1": [1.0, 2.0], "x2": [10.0, 20.0]}).lazy()

        result = _score_eager(sm, lf, ["x1", "x2"])
        df = result.collect()
        assert "prediction" in df.columns
        assert len(df) == 2

    def test_score_eager_classification_proba(self):
        from haute._mlflow_io import _score_eager

        sm = self._mock_scoring_model(task="classification", n_rows=2)
        lf = pl.DataFrame({"x1": [1.0, 2.0], "x2": [10.0, 20.0]}).lazy()

        result = _score_eager(
            sm,
            lf,
            ["x1", "x2"],
            task="classification",
        )
        df = result.collect()
        assert "prediction" in df.columns
        assert "prediction_proba" in df.columns

    def test_score_eager_custom_output_col(self):
        from haute._mlflow_io import _score_eager

        sm = self._mock_scoring_model(n_rows=2)
        lf = pl.DataFrame({"x1": [1.0, 2.0], "x2": [10.0, 20.0]}).lazy()

        result = _score_eager(sm, lf, ["x1", "x2"], output_col="my_pred")
        assert "my_pred" in result.collect().columns


# ---------------------------------------------------------------------------
# Persistent disk artifact cache
# ---------------------------------------------------------------------------


class TestResolveArtifactLocal:
    """Tests for _resolve_artifact_local disk caching."""

    def test_disk_cache_hit(self, tmp_path, monkeypatch):
        """Returns cached path without calling download_artifacts."""
        from haute._mlflow_io import _resolve_artifact_local

        monkeypatch.chdir(tmp_path)
        cached_file = _artifact_cache_path(tmp_path / ".cache" / "models", "run123", "model.cbm")
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"fake model")

        mock_mlflow = MagicMock()
        result = _resolve_artifact_local(mock_mlflow, "run123", "model.cbm")

        assert result == str(cached_file)
        mock_mlflow.artifacts.download_artifacts.assert_not_called()

    def test_disk_cache_miss_downloads(self, tmp_path, monkeypatch):
        """Downloads artifact when not cached locally."""
        from pathlib import Path

        from haute._mlflow_io import _resolve_artifact_local

        monkeypatch.chdir(tmp_path)

        def fake_download(uri, dst_path):
            Path(dst_path).mkdir(parents=True, exist_ok=True)
            out = Path(dst_path) / "model.cbm"
            out.write_bytes(b"downloaded model")
            return str(out)

        mock_mlflow = MagicMock()
        mock_mlflow.artifacts.download_artifacts.side_effect = fake_download

        result = _resolve_artifact_local(mock_mlflow, "run456", "model.cbm")

        assert Path(result).is_file()
        mock_mlflow.artifacts.download_artifacts.assert_called_once()
