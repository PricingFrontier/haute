from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl

from haute._mlflow_io import ScoringModel
from haute._model_scorer import _batch_score_to_parquet, score_frame


def _predicting_model(predictions: list[float]) -> MagicMock:
    model = MagicMock()
    model.predict.return_value = np.asarray(predictions, dtype=np.float64)
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


def test_eager_scoring_does_not_collect_unused_columns_for_prediction() -> None:
    model = _predicting_model([0.1, 0.2])
    lf = (
        pl.DataFrame({"feature": [1.0, 2.0]})
        .lazy()
        .with_columns(
            pl.lit(1)
            .map_elements(
                lambda _value: (_ for _ in ()).throw(RuntimeError("unused column was collected")),
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
        batch=False,
    )

    assert isinstance(result, pl.LazyFrame)
    model.predict.assert_called_once()


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
