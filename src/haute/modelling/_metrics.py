"""Metric computation for model evaluation."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import polars as pl

from haute.errors import HauteValidationError
from haute.modelling._row_order import frame_row_order

# Reserved key added to the ``compute_metrics`` payload whenever non-finite
# rows were filtered out before computing metrics (CODE_REVIEW 4b.11).  It
# rides alongside the metric values so the count reaches every surface the
# metrics do — the training result, the UI metrics table, and MLflow.
NON_FINITE_FILTERED_KEY = "non_finite_rows_filtered"


def _as_float_array(values: np.ndarray, *, name: str) -> np.ndarray:
    """Return a numeric array suitable for metrics and diagnostics."""
    arr = np.asarray(values)
    if arr.ndim != 1:
        raise HauteValidationError(f"{name} must be a one-dimensional array; got shape {arr.shape}")

    if np.issubdtype(arr.dtype, np.number) or np.issubdtype(arr.dtype, np.bool_):
        return arr.astype(float, copy=False)

    try:
        return arr.astype(float, copy=False)
    except (TypeError, ValueError) as exc:
        raise HauteValidationError(
            f"{name} must contain numeric values for metrics and diagnostics; got dtype {arr.dtype}"
        ) from exc


def _prepare_metric_arrays(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
    *,
    diagnostic: str | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, int, np.ndarray]:
    """Coerce numeric inputs and apply the shared finite-row mask.

    Boolean targets are intentionally supported as binary numeric targets:
    they are converted to ``0.0``/``1.0`` before metric computation.  Unsigned
    integer targets are also converted before sorting so Gini/Lorenz ordering
    cannot wrap on unary negation.
    """
    y_true_arr = _as_float_array(y_true, name="y_true")
    y_pred_arr = _as_float_array(y_pred, name="y_pred")
    if len(y_true_arr) != len(y_pred_arr):
        raise HauteValidationError(
            "y_true and y_pred must have the same length; "
            f"got {len(y_true_arr)} and {len(y_pred_arr)}"
        )

    weight_arr: np.ndarray | None = None
    if weight is not None:
        weight_arr = _as_float_array(weight, name="weight")
        if len(weight_arr) != len(y_true_arr):
            raise HauteValidationError(
                "weight must have the same length as y_true/y_pred; "
                f"got {len(weight_arr)} and {len(y_true_arr)}"
            )

    finite_mask = np.isfinite(y_true_arr) & np.isfinite(y_pred_arr)
    if weight_arr is not None:
        finite_mask &= np.isfinite(weight_arr)

    n_filtered = 0
    if not finite_mask.all():
        from haute._logging import get_logger

        logger = get_logger(component="modelling.metrics")
        total = len(finite_mask)
        n_filtered = int((~finite_mask).sum())
        log_fields: dict[str, Any] = {"count": n_filtered, "total": total}
        if diagnostic is not None:
            log_fields["diagnostic"] = diagnostic

        if n_filtered == total:
            logger.error("all_values_non_finite", original_count=total, **log_fields)
            if diagnostic is not None:
                raise HauteValidationError(
                    f"All {total} rows for diagnostic {diagnostic!r} have non-finite "
                    "actuals, predictions, or weights; diagnostic cannot be computed."
                )
            raise HauteValidationError(
                f"All {total} rows have non-finite actuals, predictions, or weights; "
                "metrics cannot be computed. Check the model output and the "
                "target, prediction, and weight columns for NaN/Inf values."
            )

        logger.warning("non_finite_values_filtered", **log_fields)
        y_true_arr = y_true_arr[finite_mask]
        y_pred_arr = y_pred_arr[finite_mask]
        if weight_arr is not None:
            weight_arr = weight_arr[finite_mask]

    return y_true_arr, y_pred_arr, weight_arr, n_filtered, finite_mask


#: The refusal for a Tweedie deviance requested without the power to measure it at.
TWEEDIE_DEVIANCE_NEEDS_POWER = (
    "tweedie_deviance needs a variance power: train with a Tweedie loss and its variance "
    "power, or choose another metric."
)


def compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    metric_names: list[str] | None = None,
    *,
    variance_power: float | None = None,
) -> dict[str, float]:
    """Compute requested metrics between actuals and predictions.

    All metrics support optional sample weights (exposure weighting
    is standard in insurance).

    Rows with non-finite actuals, predictions, or sample weights are
    excluded before any metric is computed (metrics over NaN/Inf would be
    meaningless), and the exclusion is SURFACED: the returned dict gains a
    ``"non_finite_rows_filtered"`` entry with the dropped-row count whenever
    it is non-zero, and a warning is logged.  If *every* row is non-finite a
    :class:`ValueError` is raised — silently returning empty/NaN metrics
    would hide a fundamentally broken model or dataset.

    Parameters
    ----------
    variance_power : float | None
        The variance power the model was trained with, which ``tweedie_deviance``
        is measured at; requesting that metric without one raises.
    """
    if metric_names is None:
        metric_names = ["gini", "rmse"]

    y_true, y_pred, weight, n_filtered, _ = _prepare_metric_arrays(y_true, y_pred, weight)

    results: dict[str, float] = {}
    for name in metric_names:
        fn = _METRIC_REGISTRY.get(name.lower())
        if fn is None:
            raise HauteValidationError(
                f"Unknown metric: {name}. Available: {list(_METRIC_REGISTRY.keys())}"
            )
        if name.lower() == "tweedie_deviance":
            if variance_power is None:
                raise HauteValidationError(TWEEDIE_DEVIANCE_NEEDS_POWER)
            results[name] = fn(y_true, y_pred, weight, variance_power=variance_power)
        else:
            results[name] = fn(y_true, y_pred, weight)
    if n_filtered:
        results[NON_FINITE_FILTERED_KEY] = float(n_filtered)
    return results


def _aggregated_lorenz_points(
    sort_key: np.ndarray,
    y_true: np.ndarray,
    weight: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Cumulative Lorenz points aggregated by unique *sort_key* value.

    Rows are ordered by *sort_key* descending and every tie group (rows
    sharing one exact key value) is collapsed into a single Lorenz segment
    — the standard tie-corrected formulation, equivalent to giving tied
    pairs half credit as in trapezoidal AUC / Somers' D.  Without the
    aggregation, ``argsort`` breaks ties by row position and any metric
    built on the curve depends on the incoming row order (CODE_REVIEW C6).

    Ties on the key are broken canonically by ``(y_true, weight)`` so the
    float accumulation order — and therefore every downstream value — is
    *exactly* invariant under row permutation, not merely up to rounding.

    Returns unnormalised ``(cum_weight, cum_loss)`` arrays, each starting
    at 0.0 (the Lorenz curve's origin) with one point per unique key value.
    """
    sort_key = _as_float_array(sort_key, name="sort_key")
    y_true = _as_float_array(y_true, name="y_true")
    weight = _as_float_array(weight, name="weight")

    # np.lexsort sorts by the last key first: sort_key descending, then the
    # canonical (y_true, weight) tie-break.
    order = np.lexsort((weight, y_true, -sort_key))
    key_sorted = sort_key[order]
    w_sorted = weight[order]
    loss_sorted = y_true[order] * w_sorted

    # Index of the last row of each tie group (exact-equality grouping).
    boundaries = np.nonzero(np.diff(key_sorted))[0]
    group_ends = np.append(boundaries, len(key_sorted) - 1)

    cum_weight = np.concatenate(([0.0], np.cumsum(w_sorted)[group_ends]))
    cum_loss = np.concatenate(([0.0], np.cumsum(loss_sorted)[group_ends]))
    return cum_weight, cum_loss


def _gini(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Normalised Gini coefficient — measures rank ordering of predictions.

    Gini = 2 * AUC - 1 for binary targets, or the ratio of the area
    between the Lorenz curve and the line of equality.  Critical for
    insurance pricing — a model with Gini=0.5 means predictions
    meaningfully separate high- from low-risk.

    Tie-corrected: rows sharing one predicted value form a single Lorenz
    segment, so the score is independent of row order.  For binary targets
    this matches ``2 * roc_auc_score - 1`` exactly (trapezoidal AUC).

    Degenerate cases all return 0.0 — no ranking power is measurable:
    empty input, zero total weight, zero total loss, a constant predictor
    (single tie group), a constant target, and single-row input.
    """
    n = len(y_true)
    if n == 0:
        return 0.0

    w = weight if weight is not None else np.ones(n)

    cum_weight, cum_loss = _aggregated_lorenz_points(y_pred, y_true, w)
    total_weight = cum_weight[-1]
    total_loss = cum_loss[-1]

    if total_weight == 0 or total_loss == 0:
        return 0.0

    # Gini = 1 - 2 * area under the Lorenz curve (trapezoidal rule over the
    # tie-aggregated points, origin included).
    area = np.trapezoid(cum_loss / total_loss, cum_weight / total_weight)
    raw_gini = 1.0 - 2.0 * area

    # Normalise by the perfect model's Gini (sorted by actuals, with the
    # same tie aggregation so curve and scalar share one formulation).
    cum_weight_perfect, cum_loss_perfect = _aggregated_lorenz_points(y_true, y_true, w)
    area_perfect = np.trapezoid(cum_loss_perfect / total_loss, cum_weight_perfect / total_weight)
    perfect_gini = 1.0 - 2.0 * area_perfect

    if perfect_gini == 0:
        return 0.0

    # `+ 0.0` normalises IEEE -0.0 (a 0 numerator over the negative
    # perfect-model raw Gini) to +0.0.
    return float(raw_gini / perfect_gini) + 0.0


def _rmse(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Root Mean Squared Error (optionally weighted)."""
    residuals = y_true - y_pred
    if weight is not None:
        return float(np.sqrt(np.average(residuals**2, weights=weight)))
    return float(np.sqrt(np.mean(residuals**2)))


def _mae(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Mean Absolute Error (optionally weighted)."""
    residuals = np.abs(y_true - y_pred)
    if weight is not None:
        return float(np.average(residuals, weights=weight))
    return float(np.mean(residuals))


def _mse(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Mean Squared Error (optionally weighted)."""
    residuals = y_true - y_pred
    if weight is not None:
        return float(np.average(residuals**2, weights=weight))
    return float(np.mean(residuals**2))


def _r2(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """R-squared / coefficient of determination (optionally weighted)."""
    if weight is not None:
        ss_res = np.average((y_true - y_pred) ** 2, weights=weight)
        ss_tot = np.average((y_true - np.average(y_true, weights=weight)) ** 2, weights=weight)
    else:
        ss_res = np.mean((y_true - y_pred) ** 2)
        ss_tot = np.mean((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return 0.0
    return float(1 - ss_res / ss_tot)


def _auc(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Area Under the ROC Curve (binary classification)."""
    from sklearn.metrics import roc_auc_score

    if weight is not None:
        return float(roc_auc_score(y_true, y_pred, sample_weight=weight))
    return float(roc_auc_score(y_true, y_pred))


def _logloss(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Log Loss / binary cross-entropy."""
    from sklearn.metrics import log_loss

    if weight is not None:
        return float(log_loss(y_true, y_pred, sample_weight=weight))
    return float(log_loss(y_true, y_pred))


def _poisson_deviance(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Poisson deviance — appropriate for count/frequency models."""
    y_pred_safe = np.maximum(y_pred, 1e-10)
    dev = 2.0 * (y_true * np.log(np.maximum(y_true, 1e-10) / y_pred_safe) - (y_true - y_pred_safe))
    if weight is not None:
        return float(np.average(dev, weights=weight))
    return float(np.mean(dev))


def _gamma_deviance(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
) -> float:
    """Weighted Gamma deviance; defined only for strictly positive targets and predictions."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    if not np.all(y_true > 0):
        raise HauteValidationError(
            "Gamma deviance needs strictly positive targets; the target has zero or negative "
            "values. Filter them upstream or choose another metric."
        )
    if not np.all(y_pred > 0):
        raise HauteValidationError(
            "Gamma deviance needs strictly positive predictions; choose a log-link loss."
        )
    dev = 2.0 * (-np.log(y_true / y_pred) + (y_true - y_pred) / y_pred)
    if weight is not None:
        return float(np.average(dev, weights=weight))
    return float(np.mean(dev))


def _tweedie_deviance(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
    *,
    variance_power: float,
) -> float:
    """Tweedie deviance — generalises Poisson (p=1) and Gamma (p=2)."""
    p = variance_power
    y_pred_safe = np.maximum(y_pred, 1e-10)
    y_true_safe = np.maximum(y_true, 0.0)

    if abs(p - 1.0) < 1e-8:
        # Poisson case
        return _poisson_deviance(y_true, y_pred, weight)
    elif abs(p - 2.0) < 1e-8:
        # Gamma case
        log_ratio = np.log(np.maximum(y_true_safe, 1e-10) / y_pred_safe)
        diff_ratio = (y_true_safe - y_pred_safe) / y_pred_safe
        dev = 2.0 * (-log_ratio + diff_ratio)
    else:
        # Element-wise: term1 is 0 when y_true == 0 (avoids inf from 0^negative)
        term1 = np.where(
            y_true_safe > 0,
            np.power(y_true_safe, 2 - p) / ((1 - p) * (2 - p)),
            0.0,
        )
        term2 = y_true_safe * np.power(y_pred_safe, 1 - p) / (1 - p)
        term3 = np.power(y_pred_safe, 2 - p) / (2 - p)
        dev = 2.0 * (term1 - term2 + term3)

    if weight is not None:
        return float(np.average(dev, weights=weight))
    return float(np.mean(dev))


def compute_double_lift(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    """Compute actual-vs-predicted by decile for double-lift analysis.

    Returns a list of dicts: [{decile, actual, predicted, count}, ...]
    """
    n = len(y_true)
    if n == 0:
        return []

    y_true, y_pred, weight, _, _ = _prepare_metric_arrays(
        y_true,
        y_pred,
        weight,
        diagnostic="double_lift",
    )
    n = len(y_true)
    w = weight if weight is not None else np.ones(n)

    # Sort by predicted value; tied predictions by actual value then weight, so the
    # deciles hold the same rows whatever order they arrive in (as the Lorenz curve).
    order = np.lexsort((w, y_true, y_pred))
    y_true_sorted = y_true[order]
    y_pred_sorted = y_pred[order]
    w_sorted = w[order]

    # Split into bins
    bins = np.array_split(np.arange(n), n_bins)
    result = []
    for i, idx in enumerate(bins):
        if len(idx) == 0:
            continue
        bw = w_sorted[idx]
        total_w = bw.sum()
        if total_w == 0:
            continue
        actual = float(np.average(y_true_sorted[idx], weights=bw))
        predicted = float(np.average(y_pred_sorted[idx], weights=bw))
        result.append(
            {
                "decile": i + 1,
                "actual": round(actual, 6),
                "predicted": round(predicted, 6),
                "count": len(idx),
            }
        )
    return result


#: A number with at most this many distinct values charts one bin per value.
AVE_EACH_VALUE_LIMIT = 20
#: An ordered category (an Enum, or levels that all read as numbers) shows every level, in
#: order, up to this many.
AVE_ORDERED_LEVEL_LIMIT = 60
#: The significant figures a bin's exposure and averages are rounded to.
_AVE_FIGURES = 6


def compute_ave_per_feature(
    df: pl.DataFrame,
    features: list[str],
    cat_features: list[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    *,
    exposure: np.ndarray | None = None,
    n_bins: int = 10,
    max_features: int | None = None,
    max_categories: int = 15,
) -> list[dict[str, Any]]:
    """Compute Actual vs Expected (AvE) one-way analysis per feature.

    For each feature (up to *max_features* when set, in the order given —
    the caller should pre-sort by importance):
    - A numeric feature has one bin per value when it has at most
      ``AVE_EACH_VALUE_LIMIT`` distinct values, else up to *n_bins* bands of equal
      mass, closed on the right.
    - A categorical feature keeps an Enum's or its numbered levels' order, else shows
      every level in natural order up to *max_categories*, else its largest levels by
      mass and ``"Other (n levels)"``.
    - Missing values are always the last bin, ``"Missing"``.

    A row's mass is its weight (one without a weight), times its *exposure* when
    one is given. A bin's ``exposure`` is its summed mass, and its averages are
    its weighted actual and predicted totals over that mass. Pass *exposure* for
    a model that predicts a rate times an exposure (a log-link offset), so the
    averages are rates per unit of exposure; without it they are weighted means
    per row. It must be finite and positive on every row the non-finite filter
    keeps.

    Returns a list of dicts::

        {feature, type ("numeric"|"categorical"),
         grouping ("each value"|"equal exposure"|"in order"|"A–Z"|"by exposure"),
         bins: [{label, exposure, avg_actual, avg_predicted}]}
    """
    if len(features) == 0 or len(y_true) == 0:
        return []

    y_true, y_pred, weight, _, finite_mask = _prepare_metric_arrays(
        y_true,
        y_pred,
        weight,
        diagnostic="ave_per_feature",
    )
    if not bool(finite_mask.all()):
        df = df.filter(pl.Series(finite_mask))

    w = weight if weight is not None else np.ones(len(y_true))
    mass = w if exposure is None else w * _ave_exposure(exposure, finite_mask)
    cat_set = set(cat_features)
    results: list[dict[str, Any]] = []

    selected = features[:max_features] if max_features is not None else features
    for feat in selected:
        if feat not in df.columns:
            continue
        col = df[feat]
        is_cat = feat in cat_set

        if is_cat:
            bins, grouping = _ave_categorical_bins(
                col, y_true, y_pred, w, mass, max_categories, n_bins
            )
            feat_type = "categorical"
        else:
            values = col.to_numpy().astype(float)
            bins, grouping = _ave_numeric_bins(values, y_true, y_pred, w, mass, n_bins)
            feat_type = "numeric"

        results.append({"feature": feat, "type": feat_type, "grouping": grouping, "bins": bins})

    return results


def _ave_exposure(exposure: np.ndarray, finite_mask: np.ndarray) -> np.ndarray:
    """The exposure of the rows AvE keeps, refused unless finite and positive on each."""
    exposure_arr = _as_float_array(exposure, name="exposure")
    if len(exposure_arr) != len(finite_mask):
        raise HauteValidationError(
            "exposure must have the same length as y_true/y_pred; "
            f"got {len(exposure_arr)} and {len(finite_mask)}"
        )
    kept: np.ndarray = exposure_arr[finite_mask]
    invalid = int((~(np.isfinite(kept) & (kept > 0))).sum())
    if invalid:
        raise HauteValidationError(
            "The exposure for diagnostic 'ave_per_feature' must be finite and positive, but "
            f"{invalid:,} of {len(kept):,} rows are non-finite, zero, or negative."
        )
    return kept


def _ave_entry(label: str, mass: float, actual: float, predicted: float) -> dict[str, Any]:
    """A bin: its summed mass and its weighted actual and predicted averages per unit of it."""
    return {
        "label": label,
        "exposure": _significant(mass),
        "avg_actual": _significant(actual / mass),
        "avg_predicted": _significant(predicted / mass),
    }


def _significant(value: float) -> float:
    """*value* to the significant figures a bin reports."""
    return float(f"{value:.{_AVE_FIGURES}g}")


def _group_sums(
    group: np.ndarray,
    groups: int,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray,
    mass: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each group's mass and weighted actual and predicted totals, from a group per row."""
    return (
        np.bincount(group, weights=mass, minlength=groups),
        np.bincount(group, weights=y_true * weight, minlength=groups),
        np.bincount(group, weights=y_pred * weight, minlength=groups),
    )


def _ave_numeric_bins(
    values: np.ndarray,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray,
    mass: np.ndarray,
    n_bins: int,
) -> tuple[list[dict[str, Any]], str]:
    """A number's bins and their grouping: one bin per value when it has few, else bands of
    equal mass closed on the right, with NaN as the last bin, ``Missing``."""
    missing = np.isnan(values)
    valid = values[~missing]
    distinct = np.unique(valid)
    if distinct.size <= AVE_EACH_VALUE_LIMIT:
        edges, grouping = distinct[:-1], "each value"
    else:
        bands = min(n_bins, max(2, valid.size // 3))
        edges, grouping = _equal_mass_edges(valid, mass[~missing], bands), "equal exposure"
    # A value equal to an edge falls in the band below it: bands are closed on the right.
    group = np.searchsorted(edges, values, side="left")
    group[missing] = edges.size + 1
    total, actual, predicted = _group_sums(group, edges.size + 2, y_true, y_pred, weight, mass)
    bins: list[dict[str, Any]] = []
    for index in range(edges.size + 1):
        if total[index] == 0:
            continue
        held = values[group == index]
        low, high = float(held.min()), float(held.max())
        label = _value_label(low) if grouping == "each value" else _band_label(low, high)
        bins.append(_ave_entry(label, total[index], actual[index], predicted[index]))
    if total[-1] > 0:
        bins.append(_ave_entry("Missing", total[-1], actual[-1], predicted[-1]))
    return bins, grouping


def _equal_mass_edges(values: np.ndarray, mass: np.ndarray, bands: int) -> np.ndarray:
    """The upper edges of up to *bands* bands of equal mass over *values*.

    A band closes at the distinct value where the cumulative mass reaches its share, so a
    tied value is never split; a heavy value takes several shares and leaves fewer bands.
    """
    distinct, group = np.unique(values, return_inverse=True)
    cumulative = np.cumsum(np.bincount(group, weights=mass, minlength=distinct.size))
    if cumulative[-1] <= 0:
        return distinct[:0]
    shares = cumulative[-1] * np.arange(1, bands) / bands
    edges = np.unique(distinct[np.searchsorted(cumulative, shares, side="left")])
    inner: np.ndarray = edges[edges < distinct[-1]]
    return inner


def _value_label(value: float) -> str:
    """A value as a label: positional, with no exponent and no trailing zeros."""
    return str(np.format_float_positional(value, trim="-"))


def _band_label(low: float, high: float) -> str:
    """A band's label: the smallest and largest values it holds, to four significant figures."""
    first = _value_label(float(f"{low:.4g}"))
    last = _value_label(float(f"{high:.4g}"))
    return first if first == last else f"{first}–{last}"


def _ave_categorical_bins(
    col: pl.Series,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray,
    mass: np.ndarray,
    max_categories: int,
    n_bins: int,
) -> tuple[list[dict[str, Any]], str]:
    """A category's bins and their grouping, with nulls as the last bin, ``Missing``.

    An Enum keeps its order and levels that all read as numbers keep theirs, up to
    ``AVE_ORDERED_LEVEL_LIMIT`` levels; more numbered levels are joined into bands as for a
    number. Any other category shows every level in natural order up to *max_categories*,
    else its largest levels by mass and ``Other (n levels)``.
    """
    enum_order = list(col.dtype.categories) if isinstance(col.dtype, pl.Enum) else None
    # Dictionary codes avoid scheduling a separate group query for each feature. All
    # arrays are O(rows + levels), including high-cardinality columns. A null takes the
    # code after the last level, so it never meets a level of the same name.
    encoded = col.cast(pl.Utf8).to_arrow().dictionary_encode()
    labels = np.asarray(encoded.dictionary.to_pylist(), dtype=object)
    missing = len(labels)
    codes = encoded.indices.fill_null(missing).to_numpy()
    total, actual, predicted = _group_sums(codes, missing + 1, y_true, y_pred, weight, mass)
    present = np.flatnonzero(total[:missing] != 0)
    numbers = _level_numbers(labels[present])
    rest = present[:0]
    if enum_order is not None and present.size <= AVE_ORDERED_LEVEL_LIMIT:
        rank = {level: index for index, level in enumerate(enum_order)}
        kept = present[np.argsort([rank[level] for level in labels[present]], kind="stable")]
        grouping = "in order"
    elif numbers is not None and present.size <= AVE_ORDERED_LEVEL_LIMIT:
        kept, grouping = present[np.argsort(numbers, kind="stable")], "in order"
    elif numbers is not None:
        values = np.full(missing + 1, np.nan)
        values[present] = numbers
        return _ave_numeric_bins(values[codes], y_true, y_pred, weight, mass, n_bins)
    elif present.size <= max_categories:
        natural = sorted(present, key=lambda i: _natural_key(str(labels[i])))
        kept, grouping = np.asarray(natural, dtype=np.intp), "A–Z"
    else:
        # The largest levels by mass, equal masses in the order of their labels.
        by_mass = present[np.lexsort((labels[present], -total[present]))]
        kept, rest, grouping = by_mass[:max_categories], by_mass[max_categories:], "by exposure"
    bins = [_ave_entry(str(labels[i]), total[i], actual[i], predicted[i]) for i in kept]
    if rest.size:
        name = f"Other ({rest.size} level{'' if rest.size == 1 else 's'})"
        bins.append(_ave_entry(name, total[rest].sum(), actual[rest].sum(), predicted[rest].sum()))
    if total[missing] > 0:
        bins.append(_ave_entry("Missing", total[missing], actual[missing], predicted[missing]))
    return bins, grouping


def _level_numbers(labels: np.ndarray) -> np.ndarray | None:
    """The levels read as finite numbers, or None as soon as one does not read as one."""
    numbers = np.empty(labels.size)
    for index, label in enumerate(labels):
        try:
            numbers[index] = float(label)
        except ValueError:
            return None
    return numbers if labels.size and bool(np.isfinite(numbers).all()) else None


def _natural_key(label: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    """A label's natural order: case aside, runs of digits compare as numbers (B2 before B10)."""
    parts = re.split(r"([0-9]+)", label)
    return (
        tuple((0, int(part), "") if part.isdigit() else (1, 0, part.casefold()) for part in parts),
        label,
    )


def compute_residuals_histogram(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    n_bins: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Compute a weighted histogram of residuals (actual - predicted).

    Returns ``(bins, stats)`` where:

    - *bins*: ``[{bin_center, count, weighted_count}, ...]``
    - *stats*: ``{mean, std, skew, min, max}``
    """
    if len(y_true) == 0:
        return [], {"mean": 0.0, "std": 0.0, "skew": 0.0, "min": 0.0, "max": 0.0}

    y_true, y_pred, weight, _, _ = _prepare_metric_arrays(
        y_true,
        y_pred,
        weight,
        diagnostic="residuals_histogram",
    )
    residuals = y_true - y_pred
    w = weight if weight is not None else np.ones(len(residuals))

    # Histogram edges
    counts, edges = np.histogram(residuals, bins=n_bins)
    centers = (edges[:-1] + edges[1:]) / 2.0

    # Weighted counts via digitize + bincount (single O(n) pass)
    bin_indices = np.digitize(residuals, edges[1:-1])  # 0..n_bins-1
    weighted_counts = np.bincount(bin_indices, weights=w, minlength=n_bins)

    bins: list[dict[str, Any]] = [
        {
            "bin_center": round(float(centers[i]), 6),
            "count": int(counts[i]),
            "weighted_count": round(float(weighted_counts[i]), 6),
        }
        for i in range(len(counts))
    ]

    # Weighted statistics (scipy-free)
    total_w = w.sum()
    if total_w == 0:
        return bins, {"mean": 0.0, "std": 0.0, "skew": 0.0, "min": 0.0, "max": 0.0}

    w_mean = float(np.average(residuals, weights=w))
    deviations = residuals - w_mean
    w_var = float(np.average(deviations**2, weights=w))
    w_std = float(np.sqrt(w_var))

    # Weighted skewness
    if w_std > 0:
        w_skew = float(np.average((deviations / w_std) ** 3, weights=w))
    else:
        w_skew = 0.0

    stats = {
        "mean": round(w_mean, 6),
        "std": round(w_std, 6),
        "skew": round(w_skew, 6),
        "min": round(float(residuals.min()), 6),
        "max": round(float(residuals.max()), 6),
    }
    return bins, stats


def compute_actual_vs_predicted(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    max_points: int = 2000,
) -> list[dict[str, float]]:
    """Subsample actual vs predicted pairs for scatter plot.

    Returns ``[{actual, predicted, weight}, ...]`` with at most *max_points*
    entries.  Uses stratified subsampling by predicted decile to preserve
    the distribution shape.
    """
    n = len(y_true)
    if n == 0:
        return []

    y_true, y_pred, weight, _, _ = _prepare_metric_arrays(
        y_true,
        y_pred,
        weight,
        diagnostic="actual_vs_predicted",
    )
    n = len(y_true)
    w = weight if weight is not None else np.ones(n)
    # Points are listed, and sampled, over the rows ordered by prediction, actual and
    # weight: the same points whatever order the rows arrive in.
    canonical = np.lexsort((w, y_true, y_pred))

    if n <= max_points:
        return [
            {
                "actual": round(float(y_true[i]), 6),
                "predicted": round(float(y_pred[i]), 6),
                "weight": round(float(w[i]), 6),
            }
            for i in canonical
        ]

    rng = np.random.RandomState(42)

    # Split into 10 deciles by y_pred
    decile_edges = np.percentile(y_pred, np.linspace(0, 100, 11))
    per_decile = max_points // 10

    sampled_indices: list[int] = []
    for d in range(10):
        lo = decile_edges[d]
        hi = decile_edges[d + 1]
        if d < 9:
            mask = (y_pred >= lo) & (y_pred < hi)
        else:
            # Last decile includes the right edge
            mask = (y_pred >= lo) & (y_pred <= hi)
        indices = canonical[mask[canonical]]
        if len(indices) == 0:
            continue
        sample_size = min(per_decile, len(indices))
        replace = len(indices) < per_decile
        chosen = rng.choice(indices, size=sample_size, replace=replace)
        sampled_indices.extend(chosen.tolist())

    return [
        {
            "actual": round(float(y_true[i]), 6),
            "predicted": round(float(y_pred[i]), 6),
            "weight": round(float(w[i]), 6),
        }
        for i in sampled_indices
    ]


def compute_lorenz_curve(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    n_points: int = 200,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Compute Lorenz curves for model predictions and perfect model.

    Returns ``(model_curve, perfect_curve)`` where each is
    ``[{cum_weight_frac, cum_actual_frac}, ...]``.

    The model curve sorts by predicted (descending).
    The perfect curve sorts by actual (descending).
    Both include ``(0, 0)`` and ``(1, 1)`` endpoints.

    Tie-corrected: rows sharing one sort-key value collapse into a single
    curve point (one Lorenz segment per unique value), the same aggregation
    ``_gini`` uses — so the plotted curve and the Gini scalar can never
    disagree, and both are independent of row order (CODE_REVIEW C6).
    """
    n = len(y_true)
    if n == 0:
        return [{"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}], [
            {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}
        ]

    y_true, y_pred, weight, _, _ = _prepare_metric_arrays(
        y_true,
        y_pred,
        weight,
        diagnostic="lorenz_curve",
    )
    n = len(y_true)
    w = weight if weight is not None else np.ones(n)

    def _build_curve(sort_key: np.ndarray) -> list[dict[str, float]]:
        cum_w, cum_y = _aggregated_lorenz_points(sort_key, y_true, w)

        total_w = cum_w[-1]
        total_y = cum_y[-1]

        if total_w == 0 or total_y == 0:
            return [
                {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
                {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
            ]

        # (0, 0) is already the first aggregated point.
        cum_w_frac = cum_w / total_w
        cum_y_frac = cum_y / total_y

        # Downsample to n_points evenly spaced indices (always include first/last)
        total_len = len(cum_w_frac)
        if total_len <= n_points:
            indices = np.arange(total_len)
        else:
            indices = np.unique(np.round(np.linspace(0, total_len - 1, n_points)).astype(int))

        return [
            {
                "cum_weight_frac": round(float(cum_w_frac[i]), 6),
                "cum_actual_frac": round(float(cum_y_frac[i]), 6),
            }
            for i in indices
        ]

    model_curve = _build_curve(y_pred)
    perfect_curve = _build_curve(y_true)
    return model_curve, perfect_curve


class _PdpFeatureError(Exception):
    """Internal: a structural per-feature PDP failure with a stable category.

    Used for the non-exception failure modes (missing column, no values to
    grid) so the payload's ``error_type`` is a meaningful category rather
    than this class's name.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def compute_pdp(
    model: Any,
    algo: Any,
    df: pl.DataFrame,
    features: list[str],
    cat_features: list[str],
    *,
    n_grid: int = 50,
    max_sample: int = 500,
    offset: str | None = None,
    row_order: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Compute partial dependence plots for all features.

    *features* must contain the complete model inputs in training order.
    Callers may reorder the returned chart entries for presentation afterward.

    For each feature:

    - *Numeric*: create a grid of *n_grid* values (percentile-spaced from
      data; nearest-rank for an integer feature), cast to the feature's dtype and
      deduplicated, so every label is the value the model scored.
    - *Categorical*: use unique values (up to 30 most frequent).
    - For each grid value: replace the column, predict, and average.

    Returns ``[{feature, type, grid: [{value, avg_prediction}]}]`` in the
    same order as *features*.

    Per-feature failures are SURFACED, not swallowed (CODE_REVIEW 4b.10):
    a failed feature keeps its slot in the returned list as
    ``{feature, type, grid: [], error, error_type}`` — ``error_type`` is a
    reason category (``"missing_column"``, ``"empty_column"``, or the
    raising exception's class name) — and one warning names every failed
    feature.  If *every* feature fails, a :class:`RuntimeError` is raised so
    the caller records a real diagnostics error instead of rendering an
    empty PDP with no signal.
    """
    if df.is_empty() or len(features) == 0:
        return []

    # Subsample rows if needed, over the rows' canonical order (*row_order*, or the
    # frame's own), so the sample is the same rows whatever order *df* holds them in.
    n_rows = df.height
    if n_rows > max_sample:
        order = frame_row_order(df) if row_order is None else row_order
        rng = np.random.RandomState(42)
        idx = order[rng.choice(n_rows, size=max_sample, replace=False)]
        sample_df = df[idx]
    else:
        sample_df = df

    cat_set = set(cat_features)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for feat in features:
        is_cat = feat in cat_set
        feat_type = "categorical" if is_cat else "numeric"
        try:
            if feat not in sample_df.columns:
                raise _PdpFeatureError(
                    "missing_column",
                    f"feature {feat!r} is not present in the diagnostics frame",
                )

            if is_cat:
                # Categorical: unique values, top 30 by frequency
                val_counts = (
                    sample_df.select(pl.col(feat).cast(pl.Utf8))
                    .group_by(feat)
                    .len()
                    # Equal counts ordered by level, so the cut is the same on every run.
                    .sort(["len", feat], descending=[True, False], nulls_last=True)
                )
                grid_values = val_counts[feat].to_list()[:30]
            else:
                # Numeric: percentile-spaced grid over the column's physical values (a
                # date's days, a datetime's ticks), so a temporal feature's points are
                # numbers as an ordinary feature's are.
                dtype = sample_df[feat].dtype
                physical = sample_df[feat].to_physical()
                col_vals = physical.drop_nulls().to_numpy().astype(float)
                # NaN is missing too: one NaN would make every percentile, so the grid, NaN.
                col_vals = col_vals[~np.isnan(col_vals)]
                if len(col_vals) == 0:
                    raise _PdpFeatureError(
                        "empty_column",
                        f"feature {feat!r} holds only null and NaN values, so it has no PDP grid",
                    )
                percentiles = np.linspace(0, 100, n_grid)
                # Each point is labelled with the value the model scores there: an
                # integer (or temporal) feature takes observed values (nearest rank),
                # and every value is cast to the column's physical dtype before it is
                # labelled.
                raw_grid = np.percentile(
                    col_vals,
                    percentiles,
                    method="nearest" if physical.dtype.is_integer() else "linear",
                )
                grid_values = (
                    pl.Series(feat, np.unique(raw_grid))
                    .cast(physical.dtype)
                    .unique(maintain_order=True)
                    .to_list()
                )

            grid_entries: list[dict[str, Any]] = []
            for val in grid_values:
                if is_cat:
                    modified = sample_df.with_columns(
                        pl.lit(val).cast(sample_df[feat].dtype).alias(feat)
                    )
                else:
                    modified = sample_df.with_columns(
                        pl.lit(val, dtype=physical.dtype).cast(dtype).alias(feat)
                    )
                preds = algo.predict(model, modified, features, offset=offset)
                avg_pred = float(np.mean(preds))
                grid_entries.append(
                    {
                        "value": val if is_cat else round(float(val), 6),
                        "avg_prediction": round(avg_pred, 6),
                    }
                )

            results.append(
                {
                    "feature": feat,
                    "type": feat_type,
                    "grid": grid_entries,
                }
            )
        except Exception as exc:  # noqa: BLE001 — every per-feature failure is surfaced below
            category = exc.category if isinstance(exc, _PdpFeatureError) else type(exc).__name__
            # The failure keeps its slot in the payload (shape stays
            # frontend-guard compatible: string feature/type + list grid)
            # so a degraded PDP is visible, not silently partial.
            results.append(
                {
                    "feature": feat,
                    "type": feat_type,
                    "grid": [],
                    "error": str(exc),
                    "error_type": category,
                }
            )
            failures.append({"feature": feat, "error_type": category})

    if failures:
        from haute._logging import get_logger

        _logger = get_logger(component="modelling.metrics")
        if len(failures) == len(features):
            _logger.error("pdp_all_features_failed", count=len(failures), failed=failures)
            summary = "; ".join(f"{f['feature']} ({f['error_type']})" for f in failures[:10])
            if len(failures) > 10:
                summary += f"; … and {len(failures) - 10} more"
            raise RuntimeError(
                f"PDP computation failed for all {len(features)} features: {summary}"
            )
        _logger.warning("pdp_features_failed", count=len(failures), failed=failures)

    return results


_METRIC_REGISTRY: dict[str, Any] = {
    "gini": _gini,
    "rmse": _rmse,
    "mae": _mae,
    "mse": _mse,
    "r2": _r2,
    "auc": _auc,
    "logloss": _logloss,
    "poisson_deviance": _poisson_deviance,
    "tweedie_deviance": _tweedie_deviance,
    "gamma_deviance": _gamma_deviance,
}
