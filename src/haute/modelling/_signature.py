"""Build mlflow.models.ModelSignature with loud validation.

Unlike ``haute.deploy._mlflow._build_signature`` this helper raises on unknown
polars dtypes and missing metadata instead of silently coercing to string.
"""

from __future__ import annotations

from typing import Literal

from mlflow.models import ModelSignature
from mlflow.types import ColSpec, DataType, Schema, TensorSpec

_POLARS_TO_MLFLOW: dict[str, DataType] = {
    "Int64": DataType.long,
    "Float64": DataType.double,
    "String": DataType.string,
    "Boolean": DataType.boolean,
}


def _map_dtype(dtype: str) -> DataType:
    if dtype not in _POLARS_TO_MLFLOW:
        raise ValueError(
            f"Unknown polars dtype {dtype!r}. Supported dtypes: {sorted(_POLARS_TO_MLFLOW)}"
        )
    return _POLARS_TO_MLFLOW[dtype]


def build_signature(
    features: list[str],
    feature_types: dict[str, str],
    categorical_features: list[str],
    target_name: str,
    target_type: str,
    task: Literal["classification", "regression"] = "regression",
) -> ModelSignature:
    """Build an MLflow ModelSignature for a haute training run.

    Args:
        features: Ordered list of input feature column names. Must be non-empty.
        feature_types: Mapping of feature name to polars dtype string. Every
            feature in ``features`` must have an entry.
        categorical_features: Subset of ``features`` to be treated as
            categorical. Names not in ``features`` raise.
        target_name: Name of the target column (kept for symmetry; not
            embedded in the output schema column names).
        target_type: Polars dtype string for the target column.
        task: ``"regression"`` (single ``pred`` output) or ``"classification"``
            (``pred_label`` + ``pred_proba`` outputs).

    Returns:
        A ``mlflow.models.ModelSignature`` whose input schema preserves the
        order of ``features``.
    """
    if not features:
        raise ValueError("features must be non-empty; a signature needs at least one input")

    missing = [f for f in features if f not in feature_types]
    if missing:
        raise ValueError(
            f"Missing dtype in feature_types for: {missing}. "
            f"Every feature must have an explicit polars dtype."
        )

    extras = [c for c in categorical_features if c not in features]
    if extras:
        raise ValueError(f"categorical_features contains names not in features: {extras}")

    input_specs: list[ColSpec | TensorSpec] = [
        ColSpec(type=_map_dtype(feature_types[f]), name=f) for f in features
    ]
    inputs = Schema(input_specs)

    target_dtype = _map_dtype(target_type)
    if task == "regression":
        outputs = Schema([ColSpec(type=target_dtype, name="pred")])
    else:
        outputs = Schema(
            [
                ColSpec(type=target_dtype, name="pred_label"),
                ColSpec(type=DataType.double, name="pred_proba"),
            ]
        )

    return ModelSignature(inputs=inputs, outputs=outputs)
