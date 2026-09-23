"""The MLflow pyfunc for every native model Haute trains.

A logged model is a package directory holding ``model<suffix>`` and its
``feature_contract.json``. :func:`_load_pyfunc` reads the contract's model
identity, loads the native file with the matching flavor, checks the file is
the model the contract describes, and scores through haute's own
:func:`haute._model_scorer.score_frame`, so ``mlflow.pyfunc.load_model``
predicts exactly what a Model Score node does. Classification returns the
original-label ``pred_label`` and the positive-class ``pred_proba`` that the
logged signature declares; regression returns the prediction vector.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from haute.errors import ConfigError
from haute.modelling._feature_contract import CONTRACT_FILENAME, load_contract
from haute.modelling._model_export import MODEL_FILE_SUFFIXES

_PREDICTION_COLUMN = "__haute_prediction__"
_MODEL_STEM = "model"


def package_native_model(model_path: Path, contract_path: Path, directory: Path) -> Path:
    """Copy a model file and its contract into one pyfunc package directory."""
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model_path, directory / f"{_MODEL_STEM}{model_path.suffix}")
    shutil.copyfile(contract_path, directory / CONTRACT_FILENAME)
    return directory


class NativePyfuncModel:
    """The object ``mlflow.pyfunc`` wraps for a haute-trained native model."""

    def __init__(self, package_dir: str) -> None:
        from haute._mlflow_io import load_local_model, verify_contract_identity

        package = Path(package_dir)
        contract = load_contract(package / CONTRACT_FILENAME)
        if contract.model is None:
            raise ConfigError(
                "This logged model's feature contract has no model identity; retrain and log "
                "it again."
            )
        suffix = MODEL_FILE_SUFFIXES.get(contract.model.algorithm)
        model_file = package / f"{_MODEL_STEM}{suffix}" if suffix else None
        if model_file is None or not model_file.is_file():
            raise ConfigError(
                f"This logged model package has no {contract.model.algorithm} model file; "
                "log the model again.",
                algorithm=contract.model.algorithm,
            )
        self._task = contract.task
        self._categorical_levels = dict(contract.categorical_levels)
        self._scoring = load_local_model(str(model_file), task=contract.task)
        verify_contract_identity(contract.model, self._scoring)

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
            flavor=self._scoring.flavor,
            task=self._task,
            output_col=_PREDICTION_COLUMN,
            batch=False,
            categorical_levels=self._categorical_levels or None,
            offset_column=self._scoring.offset_column,
        ).collect()
        if self._task == "classification":
            return pl.DataFrame(
                {
                    "pred_label": scored[_PREDICTION_COLUMN],
                    "pred_proba": scored[f"{_PREDICTION_COLUMN}_proba"],
                }
            ).to_pandas()
        return scored[_PREDICTION_COLUMN].to_numpy()


def _load_pyfunc(data_path: str) -> NativePyfuncModel:
    """MLflow's loader-module entry point."""
    return NativePyfuncModel(data_path)
