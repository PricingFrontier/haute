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
    offset_name: str | None = None,
    offset_type: str = "Float64",
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
        offset_name: Optional offset/exposure column the model was trained
            with. Declared as a required input so scoring payloads must carry
            it — the offset is part of every served prediction, never an
            optional extra. Must not shadow a feature name.
        offset_type: Polars dtype string for the offset column.

    Returns:
        A ``mlflow.models.ModelSignature`` whose input schema preserves the
        order of ``features`` (with the offset column, when present, appended
        after them).
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

    if offset_name is not None and offset_name in features:
        raise ValueError(
            f"offset column {offset_name!r} shadows a feature name; the offset "
            "is a separate model input, not a design-matrix feature"
        )

    input_specs: list[ColSpec | TensorSpec] = [
        ColSpec(type=_map_dtype(feature_types[f]), name=f) for f in features
    ]
    if offset_name is not None:
        input_specs.append(ColSpec(type=_map_dtype(offset_type), name=offset_name))
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
