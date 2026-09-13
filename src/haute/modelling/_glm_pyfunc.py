"""MLflow pyfunc loader for RustyStats GLM models logged by haute.

``mlflow.pyfunc.load_model`` calls :func:`_load_pyfunc` with the logged ``.rsglm``
file. Predictions go through haute's own :func:`haute._model_scorer.score_frame`
RustyStats path, so a model loaded outside haute predicts exactly what a Model
Score node does.
"""

from __future__ import annotations

from typing import Any

_PREDICTION_COLUMN = "__haute_glm_prediction__"


class GLMPyfuncModel:
    """The object ``mlflow.pyfunc`` wraps for a haute GLM."""

    def __init__(self, model_path: str) -> None:
        from haute._mlflow_io import load_local_model

        self._scoring = load_local_model(model_path)

    def predict(self, model_input: Any, params: dict[str, Any] | None = None) -> Any:
        import polars as pl

        from haute._model_scorer import score_frame

        del params
        frame = (
            model_input if isinstance(model_input, pl.DataFrame) else pl.from_pandas(model_input)
        )
        scored = score_frame(
            model=self._scoring.raw_model,
            lf=frame.lazy(),
            features=list(self._scoring.feature_names),
            cat_feature_names=self._scoring.cat_feature_names,
            flavor="rustystats",
            output_col=_PREDICTION_COLUMN,
            batch=False,
            offset_column=self._scoring.offset_column,
        ).collect()
        return scored[_PREDICTION_COLUMN].to_numpy()


def _load_pyfunc(data_path: str) -> GLMPyfuncModel:
    """MLflow's loader-module entry point."""
    return GLMPyfuncModel(data_path)
