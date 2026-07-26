from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._builders import _build_node_fn
from haute._execute_lazy import _execute_lazy
from haute._mlflow_io import ScoringModel
from haute._model_scorer import (
    ScoreWriteProjection,
    _batch_score_to_parquet,
    _project_scored_output,
    score_frame,
)
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from tests.conftest import make_file_input_config, make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _predicting_model(predictions: list[float]) -> MagicMock:
    model = MagicMock()
    model.predict.return_value = np.asarray(predictions, dtype=np.float64)
    return model


def _length_predicting_model() -> MagicMock:
    model = MagicMock()

    def predict(x_data) -> np.ndarray:
        return np.arange(len(x_data), dtype=np.float64)

    model.predict.side_effect = predict
    return model


def test_eager_scoring_prepares_prediction_from_feature_projection_only() -> None:
    features = ["feature_a", "feature_b"]
    input_frame = pl.DataFrame(
        {
            "feature_a": [1.0, 2.0, 3.0],
            "unused_0": [10.0, 20.0, 30.0],
            "feature_b": [4.0, 5.0, 6.0],
            "unused_1": [40.0, 50.0, 60.0],
        }
    )
    model = _predicting_model([0.1, 0.2, 0.3])

    def prepare_feature_projection(
        df: pl.DataFrame,
        received_features: list[str],
        *,
        cat_feature_names: frozenset[str],
        flavor: str,
    ) -> np.ndarray:
        assert df.columns == features
        assert received_features == features
        assert cat_feature_names == frozenset()
        assert flavor == "pyfunc"
        return df.to_numpy()

    with patch(
        "haute._mlflow_io._prepare_predict_frame",
        side_effect=prepare_feature_projection,
    ) as prepare:
        result = score_frame(
            model=model,
            lf=input_frame.lazy(),
            features=features,
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            output_col="prediction",
            batch=False,
        ).collect()

    prepare.assert_called_once()
    assert result.columns == ["feature_a", "unused_0", "feature_b", "unused_1", "prediction"]
    assert result["unused_1"].to_list() == [40.0, 50.0, 60.0]
    assert result["prediction"].to_list() == [0.1, 0.2, 0.3]


def test_eager_score_frame_honors_required_output_projection() -> None:
    input_frame = pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "feature": [1.0, 2.0],
            "unused": [10.0, 20.0],
        }
    )
    model = _predicting_model([0.1, 0.2])

    result = score_frame(
        model=model,
        lf=input_frame.lazy(),
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        output_col="prediction",
        batch=False,
        required_output_columns=frozenset({"quote_id", "prediction"}),
    ).collect()

    assert result.columns == ["quote_id", "prediction"]
    assert result["quote_id"].to_list() == ["q1", "q2"]
    assert result["prediction"].to_list() == [0.1, 0.2]


def test_eager_score_write_projection_none_preserves_full_scored_input() -> None:
    input_frame = pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "feature": [1.0, 2.0],
            "unused": [10.0, 20.0],
        }
    )
    model = _predicting_model([0.1, 0.2])

    result = score_frame(
        model=model,
        lf=input_frame.lazy(),
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        output_col="prediction",
        batch=False,
        write_projection=ScoreWriteProjection(),
    ).collect()

    assert result.columns == ["quote_id", "feature", "unused", "prediction"]
    assert result["unused"].to_list() == [10.0, 20.0]


def test_score_frame_rejects_conflicting_projection_arguments() -> None:
    with pytest.raises(ValueError, match="Pass either required_output_columns"):
        score_frame(
            model=_predicting_model([0.1]),
            lf=pl.DataFrame({"feature": [1.0]}).lazy(),
            features=["feature"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            output_col="prediction",
            batch=False,
            required_output_columns=frozenset({"prediction"}),
            write_projection=ScoreWriteProjection(),
        )


def test_eager_score_write_projection_rejects_missing_passthrough_column() -> None:
    with pytest.raises(ValueError, match="missing passthrough columns"):
        score_frame(
            model=_predicting_model([0.1]),
            lf=pl.DataFrame({"feature": [1.0]}).lazy(),
            features=["feature"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            output_col="prediction",
            batch=False,
            write_projection=ScoreWriteProjection(
                passthrough_columns=frozenset({"missing_quote_id"})
            ),
        )


def test_eager_score_write_projection_rejects_required_column_not_produced() -> None:
    with pytest.raises(ValueError, match="not produced or preserved"):
        score_frame(
            model=_predicting_model([0.1]),
            lf=pl.DataFrame({"feature": [1.0]}).lazy(),
            features=["feature"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            output_col="prediction",
            batch=False,
            write_projection=ScoreWriteProjection(
                required_output_columns=frozenset({"missing_prediction"})
            ),
        )


def test_default_projection_includes_existing_probability_column() -> None:
    result = _project_scored_output(
        pl.DataFrame(
            {
                "quote_id": ["q1"],
                "prediction": [1],
                "prediction_proba": [0.75],
                "unused": [10],
            }
        ).lazy(),
        ScoreWriteProjection(passthrough_columns=frozenset({"quote_id"})),
        output_col="prediction",
    ).collect()

    assert result.columns == ["quote_id", "prediction", "prediction_proba"]


def test_eager_classification_projection_preserves_required_existing_proba() -> None:
    raw_model = MagicMock(spec=["predict"])
    raw_model.predict.return_value = np.asarray([0, 1], dtype=np.int64)
    input_frame = pl.DataFrame(
        {
            "feature": [1.0, 2.0],
            "prediction_proba": [0.25, 0.75],
            "unused": [10, 20],
        }
    )

    result = score_frame(
        model=raw_model,
        lf=input_frame.lazy(),
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        task="classification",
        output_col="prediction",
        batch=False,
        required_output_columns=frozenset({"prediction", "prediction_proba"}),
    ).collect()

    assert result.columns == ["prediction", "prediction_proba"]
    assert result["prediction_proba"].to_list() == [0.25, 0.75]
    assert result["prediction"].to_list() == [0, 1]


def _poisoned_column_lazy(base: pl.DataFrame, column: str, message: str) -> pl.LazyFrame:
    """Append a column whose computation raises if it is ever materialised."""
    return base.lazy().with_columns(
        pl.lit(1)
        .map_elements(
            lambda _value: (_ for _ in ()).throw(RuntimeError(message)),
            return_dtype=pl.Int64,
        )
        .alias(column)
    )


def test_eager_scoring_with_projection_never_computes_excluded_columns() -> None:
    """A concrete write projection prunes excluded columns from the single
    eager collection, so a poisoned column outside the projection is never
    computed — the eager path shares the batched path's input projection."""
    model = _predicting_model([0.1, 0.2])
    lf = _poisoned_column_lazy(
        pl.DataFrame({"feature": [1.0, 2.0], "quote_id": ["q1", "q2"]}),
        "excluded_raises",
        "excluded column was collected",
    )

    result = score_frame(
        model=model,
        lf=lf,
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        output_col="prediction",
        batch=False,
        required_output_columns=frozenset({"quote_id", "prediction"}),
    ).collect()

    model.predict.assert_called_once()
    assert result.columns == ["quote_id", "prediction"]
    assert result["prediction"].to_list() == [0.1, 0.2]


def test_eager_scoring_without_projection_materialises_input_once_at_score_time() -> None:
    """Without a projection every input column is part of the scored output,
    so the single materialisation happens at score time: a failing upstream
    column fails the score call itself.  Pre-W2-4a.2 the failure was deferred
    to a later collect of a lazy plan that re-executed the whole upstream —
    the mechanism that let order-unstable upstreams misalign predictions."""
    model = _predicting_model([0.1, 0.2])
    lf = _poisoned_column_lazy(
        pl.DataFrame({"feature": [1.0, 2.0]}),
        "poisoned",
        "poisoned column was collected",
    )

    with pytest.raises(RuntimeError, match="poisoned column was collected"):
        score_frame(
            model=model,
            lf=lf,
            features=["feature"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            output_col="prediction",
            batch=False,
        )

    model.predict.assert_not_called()


def test_batched_scoring_prepares_prediction_from_feature_projection_only(tmp_path) -> None:
    features = ["feature_a", "feature_b"]
    input_path = str(tmp_path / "wide_input.parquet")
    input_frame = pl.DataFrame(
        {
            "feature_a": [1.0, 2.0, 3.0],
            "unused_0": [10.0, 20.0, 30.0],
            "feature_b": [4.0, 5.0, 6.0],
            "unused_1": [40.0, 50.0, 60.0],
        }
    )
    input_frame.write_parquet(input_path)
    scoring_model = ScoringModel(
        _predicting_model([0.1, 0.2, 0.3]),
        feature_names=features,
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )

    def prepare_feature_projection(
        df: pl.DataFrame,
        received_features: list[str],
        *,
        cat_feature_names: frozenset[str],
        flavor: str,
    ) -> np.ndarray:
        assert df.columns == features
        assert received_features == features
        assert cat_feature_names == frozenset()
        assert flavor == "pyfunc"
        return df.to_numpy()

    with patch(
        "haute._mlflow_io._prepare_predict_frame",
        side_effect=prepare_feature_projection,
    ) as prepare:
        out_path = _batch_score_to_parquet(
            scoring_model,
            input_path,
            features,
            "prediction",
            "regression",
        )

    try:
        result = pl.read_parquet(out_path)
    finally:
        os.unlink(out_path)

    prepare.assert_called_once()
    assert result.columns == ["feature_a", "unused_0", "feature_b", "unused_1", "prediction"]
    assert result["unused_0"].to_list() == [10.0, 20.0, 30.0]
    assert result["prediction"].to_list() == [0.1, 0.2, 0.3]


def test_batched_scoring_projects_written_passthrough_columns(tmp_path) -> None:
    features = ["feature_a", "feature_b"]
    input_path = str(tmp_path / "wide_projected_input.parquet")
    pl.DataFrame(
        {
            "feature_a": [1.0, 2.0, 3.0],
            "quote_id": ["q1", "q2", "q3"],
            "unused_0": [10.0, 20.0, 30.0],
            "feature_b": [4.0, 5.0, 6.0],
            "unused_1": [40.0, 50.0, 60.0],
        }
    ).write_parquet(input_path)
    scoring_model = ScoringModel(
        _predicting_model([0.1, 0.2, 0.3]),
        feature_names=features,
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )

    out_path = _batch_score_to_parquet(
        scoring_model,
        input_path,
        features,
        "prediction",
        "regression",
        write_projection=ScoreWriteProjection(passthrough_columns=frozenset({"quote_id"})),
    )

    try:
        result = pl.read_parquet(out_path)
    finally:
        os.unlink(out_path)

    assert result.columns == ["quote_id", "prediction"]
    assert result["quote_id"].to_list() == ["q1", "q2", "q3"]
    assert result["prediction"].to_list() == [0.1, 0.2, 0.3]


def test_batched_scoring_projected_zero_row_schema_preserves_passthrough(tmp_path) -> None:
    input_path = str(tmp_path / "empty_projected_input.parquet")
    pl.DataFrame(
        {
            "feature": pl.Series([], dtype=pl.Float64),
            "quote_id": pl.Series([], dtype=pl.String),
            "unused": pl.Series([], dtype=pl.Int64),
        }
    ).write_parquet(input_path)
    scoring_model = ScoringModel(
        _predicting_model([]),
        feature_names=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )

    out_path = _batch_score_to_parquet(
        scoring_model,
        input_path,
        ["feature"],
        "prediction",
        "regression",
        write_projection=ScoreWriteProjection(passthrough_columns=frozenset({"quote_id"})),
    )

    try:
        result = pl.read_parquet(out_path)
    finally:
        os.unlink(out_path)

    assert result.columns == ["quote_id", "prediction"]
    assert result.schema["quote_id"] == pl.String
    assert result.schema["prediction"] == pl.Float64
    assert result.height == 0


def test_batched_score_frame_projects_temp_sink_to_features_and_required_passthrough() -> None:
    features = ["feature_a", "feature_b"]
    input_frame = pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "feature_a": [1.0, 2.0, 3.0],
            "unused_0": [10.0, 20.0, 30.0],
            "feature_b": [4.0, 5.0, 6.0],
            "unused_1": [40.0, 50.0, 60.0],
        }
    )
    model = _length_predicting_model()
    captured_sink_columns: list[list[str]] = []

    import haute._model_scorer as model_scorer

    real_sink = model_scorer._sink_to_temp

    def capture_projected_sink(lf: pl.LazyFrame, *, columns: frozenset[str] | None = None) -> str:
        path = real_sink(lf, columns=columns)
        captured_sink_columns.append(list(pl.read_parquet_schema(path).keys()))
        return path

    with patch("haute._model_scorer._sink_to_temp", side_effect=capture_projected_sink):
        result = score_frame(
            model=model,
            lf=input_frame.lazy(),
            features=features,
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            output_col="prediction",
            batch=True,
            required_output_columns=frozenset({"quote_id", "prediction"}),
        ).collect()

    assert captured_sink_columns == [["quote_id", "feature_a", "feature_b"]]
    assert result.columns == ["quote_id", "prediction"]
    assert result["quote_id"].to_list() == ["q1", "q2", "q3"]
    assert result["prediction"].to_list() == [0.0, 1.0, 2.0]


def test_batched_classification_projection_preserves_existing_proba_without_predict_proba() -> None:
    raw_model = MagicMock(spec=["predict"])
    raw_model.predict.return_value = np.asarray([0, 1], dtype=np.int64)
    input_frame = pl.DataFrame(
        {
            "feature": [1.0, 2.0],
            "prediction_proba": [0.25, 0.75],
            "unused": [10, 20],
        }
    )

    result = score_frame(
        model=raw_model,
        lf=input_frame.lazy(),
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        task="classification",
        output_col="prediction",
        batch=True,
        required_output_columns=frozenset({"prediction", "prediction_proba"}),
    ).collect()

    assert result.columns == ["prediction", "prediction_proba"]
    assert result["prediction_proba"].to_list() == [0.25, 0.75]
    assert result["prediction"].to_list() == [0, 1]


def test_batched_classification_write_projection_includes_generated_proba() -> None:
    raw_model = MagicMock(spec=["predict", "predict_proba"])
    raw_model.predict.return_value = np.asarray([0, 1], dtype=np.int64)
    raw_model.predict_proba.return_value = np.asarray(
        [[0.8, 0.2], [0.3, 0.7]],
        dtype=np.float64,
    )

    result = score_frame(
        model=raw_model,
        lf=pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "feature": [1.0, 2.0],
                "unused": [10, 20],
            }
        ).lazy(),
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        task="classification",
        output_col="prediction",
        batch=True,
        write_projection=ScoreWriteProjection(
            passthrough_columns=frozenset({"quote_id"}),
            required_output_columns=frozenset({"quote_id", "prediction", "prediction_proba"}),
        ),
    ).collect()

    assert result.columns == ["quote_id", "prediction", "prediction_proba"]
    assert result["prediction"].to_list() == [0, 1]
    assert result["prediction_proba"].to_list() == [0.2, 0.7]


def test_batched_score_frame_does_not_collect_wide_unused_columns_when_projected() -> None:
    model = _length_predicting_model()
    lf = (
        pl.DataFrame({"quote_id": ["q1", "q2"], "feature": [1.0, 2.0]})
        .lazy()
        .with_columns(
            pl.lit(1)
            .map_elements(
                lambda _value: (_ for _ in ()).throw(RuntimeError("unused column collected")),
                return_dtype=pl.Int64,
            )
            .alias("unused_raises")
        )
    )

    result = score_frame(
        model=model,
        lf=lf,
        features=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        output_col="prediction",
        batch=True,
        required_output_columns=frozenset({"quote_id", "prediction"}),
    ).collect()

    assert result.columns == ["quote_id", "prediction"]
    assert result["prediction"].to_list() == [0.0, 1.0]


def test_batched_scoring_preserves_passthrough_columns_across_multiple_batches(tmp_path) -> None:
    import haute._model_scorer as model_scorer

    original_batch_size = model_scorer._SCORE_BATCH_SIZE
    model_scorer._SCORE_BATCH_SIZE = 2
    input_path = str(tmp_path / "multi_batch.parquet")
    input_frame = pl.DataFrame(
        {
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0],
            "passthrough": ["a", "b", "c", "d", "e"],
        }
    )
    input_frame.write_parquet(input_path)
    raw_model = MagicMock()
    raw_model.predict.side_effect = [
        np.asarray([0.1, 0.2]),
        np.asarray([0.3, 0.4]),
        np.asarray([0.5]),
    ]
    scoring_model = ScoringModel(
        raw_model,
        feature_names=["feature"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )

    try:
        out_path = _batch_score_to_parquet(
            scoring_model,
            input_path,
            ["feature"],
            "prediction",
            "regression",
        )
        result = pl.read_parquet(out_path)
    finally:
        model_scorer._SCORE_BATCH_SIZE = original_batch_size
        if "out_path" in locals():
            os.unlink(out_path)

    assert result.columns == ["feature", "passthrough", "prediction"]
    assert result["passthrough"].to_list() == ["a", "b", "c", "d", "e"]
    assert result["prediction"].to_list() == [0.1, 0.2, 0.3, 0.4, 0.5]


def test_lazy_batch_model_score_uses_downstream_required_output_projection(tmp_path) -> None:
    data_path = tmp_path / "policies.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "feature_a": [1.0, 2.0, 3.0],
            "feature_b": [4.0, 5.0, 6.0],
            **{f"unused_{i}": [i, i + 1, i + 2] for i in range(20)},
        }
    ).write_parquet(data_path)
    raw_model = _length_predicting_model()
    scoring_model = ScoringModel(
        raw_model,
        feature_names=["feature_a", "feature_b"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType="dataInput",
                    config=make_file_input_config(data_path),
                ),
            ),
            GraphNode(
                id="score",
                data=NodeData(
                    label="score",
                    nodeType="modelScore",
                    config={
                        "sourceType": "run",
                        "run_id": "run-123",
                        "task": "regression",
                        "output_column": "prediction",
                        "code": "",
                    },
                ),
            ),
            GraphNode(
                id="output",
                data=NodeData(
                    label="output",
                    nodeType="output",
                    config=make_output_config(["quote_id", "prediction"]),
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e_source_score", source="source", target="score"),
            GraphEdge(id="e_score_output", source="score", target="output"),
        ],
    )
    captured_sink_columns: list[list[str]] = []

    import haute._model_scorer as model_scorer

    real_sink = model_scorer._sink_to_temp

    def capture_projected_sink(lf: pl.LazyFrame, *, columns: frozenset[str] | None = None) -> str:
        path = real_sink(lf, columns=columns)
        captured_sink_columns.append(list(pl.read_parquet_schema(path).keys()))
        return path

    with (
        patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
        patch("haute._model_scorer._sink_to_temp", side_effect=capture_projected_sink),
    ):
        outputs, *_ = _execute_lazy(
            graph,
            _build_node_fn,
            target_node_id="output",
            source="batch",
        )

    result = outputs["output"].collect()

    assert captured_sink_columns == [["quote_id", "feature_a", "feature_b"]]
    assert result.columns == ["quote_id", "prediction"]
    assert result["quote_id"].to_list() == ["q1", "q2", "q3"]
    assert result["prediction"].to_list() == [0.0, 1.0, 2.0]


def test_lazy_batch_model_score_uses_declared_transform_contract_for_projection(
    tmp_path,
) -> None:
    data_path = tmp_path / "optimiser_source.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "scenario_index": [0, 1, 2],
            "premium_multiplier": [0.9, 1.0, 1.1],
            "premium": [100.0, 200.0, 300.0],
            "burn_cost": [70.0, 140.0, 210.0],
            "difference_to_market": [0.8, 1.0, 1.2],
            "unused": [10, 11, 12],
        }
    ).write_parquet(data_path)
    scoring_model = ScoringModel(
        _length_predicting_model(),
        feature_names=["difference_to_market"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType="dataInput",
                    config=make_file_input_config(data_path),
                ),
            ),
            GraphNode(
                id="conversion_scoring",
                data=NodeData(
                    label="conversion_scoring",
                    nodeType="modelScore",
                    config={
                        "sourceType": "run",
                        "run_id": "run-123",
                        "task": "regression",
                        "output_column": "conversion_prediction",
                    },
                ),
            ),
            GraphNode(
                id="optimiser_input",
                data=NodeData(
                    label="optimiser_input",
                    nodeType="polars",
                    config={
                        "code": (
                            "df = conversion_scoring.with_columns("
                            "margin=pl.col('premium') - pl.col('burn_cost')"
                            ").with_columns("
                            "expected_margin=pl.col('margin') * "
                            "pl.col('conversion_prediction')"
                            ")"
                        ),
                        "contract": {
                            "inputs": [
                                "premium",
                                "burn_cost",
                                "conversion_prediction",
                            ],
                            "outputs": ["margin", "expected_margin"],
                        },
                    },
                ),
            ),
            GraphNode(
                id="online_optimiser",
                data=NodeData(
                    label="online_optimiser",
                    nodeType="optimiser",
                    config={"data_input": "optimiser_input"},
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e_source_score", source="source", target="conversion_scoring"),
            GraphEdge(
                id="e_score_optimiser_input",
                source="conversion_scoring",
                target="optimiser_input",
            ),
            GraphEdge(
                id="e_optimiser_input_online",
                source="optimiser_input",
                target="online_optimiser",
            ),
        ],
    )
    captured_sink_columns: list[list[str]] = []

    import haute._model_scorer as model_scorer

    real_sink = model_scorer._sink_to_temp

    def capture_projected_sink(
        lf: pl.LazyFrame,
        *,
        columns: frozenset[str] | None = None,
    ) -> str:
        path = real_sink(lf, columns=columns)
        captured_sink_columns.append(list(pl.read_parquet_schema(path).keys()))
        return path

    required = {
        "quote_id",
        "scenario_index",
        "premium_multiplier",
        "expected_margin",
        "conversion_prediction",
    }
    with (
        patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
        patch("haute._model_scorer._sink_to_temp", side_effect=capture_projected_sink),
    ):
        outputs, *_ = _execute_lazy(
            graph,
            _build_node_fn,
            target_node_id="online_optimiser",
            source="batch",
            required_columns_by_node={"optimiser_input": required},
        )

    result = outputs["online_optimiser"].collect()

    assert captured_sink_columns == [
        [
            "quote_id",
            "scenario_index",
            "premium_multiplier",
            "premium",
            "burn_cost",
            "difference_to_market",
        ]
    ]
    assert "unused" not in result.columns
    assert "difference_to_market" not in result.columns
    assert result["expected_margin"].to_list() == [0.0, 60.0, 180.0]


def test_lazy_batch_model_score_applies_stale_selected_columns_after_scoring(
    tmp_path,
) -> None:
    data_path = tmp_path / "policies_selected.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "feature_a": [1.0, 2.0, 3.0],
            "feature_b": [4.0, 5.0, 6.0],
            "unused": [10, 11, 12],
        }
    ).write_parquet(data_path)
    scoring_model = ScoringModel(
        _length_predicting_model(),
        feature_names=["feature_a", "feature_b"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType="dataInput",
                    config=make_file_input_config(data_path),
                ),
            ),
            GraphNode(
                id="score",
                data=NodeData(
                    label="score",
                    nodeType="modelScore",
                    config={
                        "sourceType": "run",
                        "run_id": "run-123",
                        "task": "regression",
                        "output_column": "prediction",
                        "selected_columns": ["quote_id", "prediction", "stale"],
                    },
                ),
            ),
            GraphNode(
                id="output",
                data=NodeData(
                    label="output",
                    nodeType="output",
                    config=make_output_config([]),
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e_source_score", source="source", target="score"),
            GraphEdge(id="e_score_output", source="score", target="output"),
        ],
    )
    captured_sink_columns: list[list[str]] = []

    import haute._model_scorer as model_scorer

    real_sink = model_scorer._sink_to_temp

    def capture_projected_sink(
        lf: pl.LazyFrame,
        *,
        columns: frozenset[str] | None = None,
    ) -> str:
        path = real_sink(lf, columns=columns)
        captured_sink_columns.append(list(pl.read_parquet_schema(path).keys()))
        return path

    def build_node_fn(node: GraphNode, **kwargs):
        # The opaque (empty-mapping) OUTPUT only restores the projection demand;
        # under v2 the real builder would assemble an empty document, so pass the
        # score node's frame through here to observe its applied selected_columns.
        if node.data.nodeType == NodeType.OUTPUT:
            return node.id, lambda *dfs: dfs[0], False
        return _build_node_fn(node, **kwargs)

    with (
        patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
        patch("haute._model_scorer._sink_to_temp", side_effect=capture_projected_sink),
    ):
        outputs, *_ = _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="output",
            source="batch",
        )

    result = outputs["output"].collect()

    assert captured_sink_columns == [["quote_id", "feature_a", "feature_b", "unused"]]
    assert result.columns == ["quote_id", "prediction"]
