"""SHAP diagnostics shared by every family whose adapter exposes ``shap_values``.

The job samples the diagnostics partition once, the adapter returns the per-row
SHAP matrix for that sample, and :func:`shap_diagnostics` turns the matrix into
the result views: the mean absolute SHAP value per feature, the beeswarm, and a
SHAP curve per feature.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

#: Rows SHAP is computed on, whatever the size of the diagnostics partition.
SHAP_SAMPLE_ROWS = 5_000
#: The leading sampled rows the beeswarm plots.
BEESWARM_ROWS = 2_000
#: The features the beeswarm plots, largest mean absolute SHAP value first.
BEESWARM_FEATURES = 20
#: The most quantile bands a numeric feature's curve has.
CURVE_BANDS = 20
#: The most frequent levels a categorical feature's curve keeps.
CURVE_LEVELS = 30

_SAMPLE_SEED = 42
#: Significant figures the beeswarm sends its SHAP and numeric values with.
_SHAP_FIGURES = 4
_VALUE_FIGURES = 6


@dataclass(frozen=True)
class ShapViews:
    """Every SHAP view of one run, built from one SHAP matrix."""

    summary: list[dict[str, Any]]
    beeswarm: list[dict[str, Any]]
    curves: list[dict[str, Any]]


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
) -> ShapViews:
    """The summary, beeswarm and curves for one SHAP matrix.

    *values* holds one row per *sample* row and one column per feature, in
    *features* order. The summary is largest first with ties in feature order; the
    beeswarm takes the summary's first ``BEESWARM_FEATURES`` features over the
    first ``BEESWARM_ROWS`` sampled rows; the curves cover every feature over every
    sampled row, in summary order.
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
    curves = [
        _categorical_curve(features[index], values[:, index], sample.get_column(features[index]))
        if features[index] in categorical
        else _numeric_curve(features[index], values[:, index], sample.get_column(features[index]))
        for index in order
    ]
    return ShapViews(summary=summary, beeswarm=beeswarm, curves=curves)


def _significant(values: Sequence[float | None], figures: int) -> list[float | None]:
    """*values* rounded to *figures* significant figures, nulls kept."""
    return [None if value is None else float(f"{value:.{figures}g}") for value in values]


def _numeric_values(column: pl.Series) -> pl.Series:
    """The cast the native encoders apply to a numeric feature (Boolean to 0/1), NaN as null."""
    return column.cast(pl.Float64).fill_nan(None)


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
        numeric = _numeric_values(column)
        values = _significant(numeric.to_list(), _VALUE_FIGURES)
        ranks = _value_ranks(numeric)
    return {
        "feature": feature,
        "kind": "categorical" if categorical else "numeric",
        "shap_values": _significant(shap_values.astype(np.float64).tolist(), _SHAP_FIGURES),
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


def _curve_point(
    shap_values: np.ndarray,
    *,
    value: float | str | None,
    low: float | None = None,
    high: float | None = None,
) -> dict[str, Any]:
    """One curve group: its rows, mean SHAP value and 10th and 90th percentile SHAP values."""
    p10, p90 = np.percentile(shap_values, [10, 90])
    return {
        "value": value,
        "low": low,
        "high": high,
        "rows": int(shap_values.size),
        "mean_shap": float(shap_values.mean()),
        "p10_shap": float(p10),
        "p90_shap": float(p90),
    }


def _numeric_curve(feature: str, shap_values: np.ndarray, column: pl.Series) -> dict[str, Any]:
    """A numeric feature's curve: one point per value or per quantile band, missing rows last."""
    numeric = _numeric_values(column)
    missing = numeric.is_null().to_numpy()
    present = ~missing
    x = numeric.to_numpy()[present]
    shap = shap_values[present]
    distinct = np.unique(x)
    if distinct.size <= CURVE_BANDS:
        band = np.searchsorted(distinct, x)
    else:
        cuts = np.unique(np.percentile(x, np.linspace(0, 100, CURVE_BANDS + 1)))
        # A value on an inner cut point falls in the upper band.
        band = np.searchsorted(cuts[1:-1], x, side="right")
    points = []
    for index in np.unique(band):
        members = band == index
        band_values = x[members]
        low, high = float(band_values.min()), float(band_values.max())
        # Clipped: the mean of equal values can land an ulp outside them.
        mean = float(np.clip(band_values.mean(), low, high))
        points.append(_curve_point(shap[members], value=mean, low=low, high=high))
    if missing.any():
        points.append(_curve_point(shap_values[missing], value=None))
    return {"feature": feature, "kind": "numeric", "points": points, "levels_omitted": 0}


def _categorical_curve(feature: str, shap_values: np.ndarray, column: pl.Series) -> dict[str, Any]:
    """A categorical feature's curve: its most frequent levels, most rows first."""
    levels = column.cast(pl.String).to_list()
    rows_by_level: dict[str | None, list[int]] = {}
    for row, level in enumerate(levels):
        rows_by_level.setdefault(level, []).append(row)
    ranked = sorted(
        rows_by_level.items(),
        # Most rows first; ties in level order, the missing level after named ones.
        key=lambda item: (-len(item[1]), item[0] is None, item[0] or ""),
    )
    points = [
        _curve_point(shap_values[np.asarray(rows)], value=level)
        for level, rows in ranked[:CURVE_LEVELS]
    ]
    return {
        "feature": feature,
        "kind": "categorical",
        "points": points,
        "levels_omitted": max(0, len(ranked) - CURVE_LEVELS),
    }
