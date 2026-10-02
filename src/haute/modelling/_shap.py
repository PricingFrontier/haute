"""SHAP diagnostics shared by every family whose adapter exposes ``shap_values``.

The job samples the diagnostics partition once, the adapter returns the per-row
SHAP matrix for that sample, and :func:`shap_diagnostics` turns the matrix into
the two result views: the mean absolute SHAP value per feature and the beeswarm.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl

#: Rows SHAP is computed on, whatever the size of the diagnostics partition.
SHAP_SAMPLE_ROWS = 1_000
#: The leading sampled rows the beeswarm plots.
BEESWARM_ROWS = 500
#: The features the beeswarm plots, largest mean absolute SHAP value first.
BEESWARM_FEATURES = 20

_SAMPLE_SEED = 42


def shap_sample(df: pl.DataFrame) -> pl.DataFrame:
    """At most ``SHAP_SAMPLE_ROWS`` rows of *df* in a seeded random order.

    Shuffled even when *df* is under the cap, so the beeswarm's leading rows are a
    uniform sample rather than the partition's first (on a temporal split, oldest) rows.
    """
    return df.sample(n=min(df.height, SHAP_SAMPLE_ROWS), seed=_SAMPLE_SEED, shuffle=True)


def require_feature_order(model_features: Sequence[str], features: Sequence[str]) -> None:
    """Refuse SHAP columns whose order would not match the requested feature labels."""
    if list(model_features) != list(features):
        raise ValueError(
            "SHAP columns follow the model's feature order "
            f"{list(model_features)}, not the requested {list(features)}"
        )


def shap_diagnostics(
    values: np.ndarray,
    sample: pl.DataFrame,
    features: list[str],
    cat_features: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The mean-absolute SHAP summary and the beeswarm for one SHAP matrix.

    *values* holds one row per *sample* row and one column per feature, in
    *features* order. The summary is largest first with ties in feature order; the
    beeswarm takes the summary's first ``BEESWARM_FEATURES`` features over the
    first ``BEESWARM_ROWS`` sampled rows.
    """
    expected = (sample.height, len(features))
    if values.shape != expected:
        raise ValueError(
            f"SHAP values have shape {values.shape}; expected {expected} (sampled rows, features)"
        )
    mean_abs = np.abs(values).mean(axis=0)
    order = sorted(range(len(features)), key=lambda index: -mean_abs[index])
    summary = [
        {"feature": features[index], "mean_abs_shap": float(mean_abs[index])} for index in order
    ]
    plotted = sample.head(BEESWARM_ROWS)
    categorical = set(cat_features)
    beeswarm = [
        _beeswarm_feature(
            features[index],
            values[: plotted.height, index],
            plotted.get_column(features[index]),
            categorical=features[index] in categorical,
        )
        for index in order[:BEESWARM_FEATURES]
    ]
    return summary, beeswarm


def _beeswarm_feature(
    feature: str,
    shap_values: np.ndarray,
    column: pl.Series,
    *,
    categorical: bool,
) -> dict[str, Any]:
    """One beeswarm row: each plotted row's SHAP value, feature value and value rank."""
    if categorical:
        values: list[Any] = column.cast(pl.String).to_list()
        ranks: list[float | None] = [None] * len(values)
    else:
        # The cast the native encoders apply to a numeric feature (Boolean to 0/1).
        numeric = column.cast(pl.Float64).fill_nan(None)
        values = numeric.to_list()
        ranks = _value_ranks(numeric)
    return {
        "feature": feature,
        "kind": "categorical" if categorical else "numeric",
        "shap_values": shap_values.astype(np.float64).tolist(),
        "values": values,
        "value_ranks": ranks,
    }


def _value_ranks(values: pl.Series) -> list[float | None]:
    """Each value's average rank among the non-null values, from 0 (lowest) to 1 (highest).

    Null for a null value, and for every value when fewer than two distinct values occur.
    """
    ranks = values.rank(method="average").cast(pl.Float64).to_numpy()
    present = ranks[~np.isnan(ranks)]
    if present.size == 0 or present.min() == present.max():
        return [None] * len(ranks)
    scaled = np.round((ranks - present.min()) / (present.max() - present.min()), 3)
    return [None if np.isnan(rank) else float(rank) for rank in scaled]
