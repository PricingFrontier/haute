"""Metric computation for model evaluation."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


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

    Parameters
    ----------
    variance_power : float | None
        If provided, passed to ``tweedie_deviance`` so the metric matches
        the variance power the model was trained with (default 1.5).
    """
    if metric_names is None:
        metric_names = ["gini", "rmse"]

    # Guard against NaN/Inf values that would produce misleading metrics
    finite_mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if not finite_mask.all():
        from haute._logging import get_logger

        _logger = get_logger(component="modelling.metrics")
        n_dropped = int((~finite_mask).sum())
        _logger.warning("non_finite_values_filtered", count=n_dropped, total=len(finite_mask))
        y_true = y_true[finite_mask]
        y_pred = y_pred[finite_mask]
        if weight is not None:
            weight = weight[finite_mask]
        if len(y_true) == 0:
            _logger.error("all_values_non_finite", original_count=len(finite_mask))
            return {name: float("nan") for name in metric_names}

    results: dict[str, float] = {}
    for name in metric_names:
        fn = _METRIC_REGISTRY.get(name.lower())
        if fn is None:
            raise ValueError(f"Unknown metric: {name}. Available: {list(_METRIC_REGISTRY.keys())}")
        if name.lower() == "tweedie_deviance" and variance_power is not None:
            results[name] = fn(y_true, y_pred, weight, variance_power=variance_power)
        else:
            results[name] = fn(y_true, y_pred, weight)
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


def _tweedie_deviance(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None,
    variance_power: float = 1.5,
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

    w = weight if weight is not None else np.ones(n)

    # Sort by predicted value
    order = np.argsort(y_pred)
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


def compute_ave_per_feature(
    df: pl.DataFrame,
    features: list[str],
    cat_features: list[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray | None = None,
    *,
    n_bins: int = 10,
    max_features: int | None = None,
    max_categories: int = 15,
) -> list[dict[str, Any]]:
    """Compute Actual vs Expected (AvE) one-way analysis per feature.

    For each feature (up to *max_features* when set, in the order given —
    the caller should pre-sort by importance):
    - Numeric features are quantile-binned into *n_bins*.
    - Categorical features are grouped by category (top *max_categories*
      by weight, remainder lumped into ``"Other"``).

    Returns a list of dicts::

        {feature, type ("numeric"|"categorical"),
         bins: [{label, exposure, avg_actual, avg_predicted}]}
    """
    if len(features) == 0 or len(y_true) == 0:
        return []

    w = weight if weight is not None else np.ones(len(y_true))
    cat_set = set(cat_features)
    results: list[dict[str, Any]] = []

    selected = features[:max_features] if max_features is not None else features
    for feat in selected:
        if feat not in df.columns:
            continue
        col = df[feat]
        is_cat = feat in cat_set

        if is_cat:
            bins = _ave_categorical_bins(col, y_true, y_pred, w, max_categories)
            feat_type = "categorical"
        else:
            bins = _ave_numeric_bins(col, y_true, y_pred, w, n_bins)
            feat_type = "numeric"

        results.append({"feature": feat, "type": feat_type, "bins": bins})

    return results


def _ave_numeric_bins(
    col: pl.Series,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray,
    n_bins: int,
) -> list[dict[str, Any]]:
    """Bin a numeric feature by quantile and compute weighted AvE per bin."""
    vals = col.to_numpy().astype(float)
    n = len(vals)

    # Separate NaN mask
    nan_mask = np.isnan(vals)
    valid_mask = ~nan_mask

    valid_count = int(valid_mask.sum())
    bins: list[dict[str, Any]] = []

    if valid_count > 0:
        effective_bins = min(n_bins, max(2, valid_count // 3))

        # Compute quantile edges on valid values
        valid_vals = vals[valid_mask]
        quantiles = np.linspace(0, 100, effective_bins + 1)
        edges = np.unique(np.percentile(valid_vals, quantiles))

        # Digitize: assign each valid value to a bin (1-indexed)
        bin_indices = np.digitize(valid_vals, edges[1:-1])  # 0 to len(edges)-2

        # Build a full-length bin assignment (-1 for NaN)
        full_bins = np.full(n, -1, dtype=int)
        full_bins[valid_mask] = bin_indices

        actual_n_bins = int(bin_indices.max()) + 1 if len(bin_indices) > 0 else 0
        for b in range(actual_n_bins):
            mask = full_bins == b
            if not mask.any():
                continue
            bw = weight[mask]
            total_w = bw.sum()
            if total_w == 0:
                continue

            bin_vals = vals[mask]
            lo, hi = float(np.nanmin(bin_vals)), float(np.nanmax(bin_vals))
            label = f"{lo:.4g}–{hi:.4g}" if lo != hi else f"{lo:.4g}"

            bins.append(
                {
                    "label": label,
                    "exposure": round(float(total_w), 4),
                    "avg_actual": round(float(np.average(y_true[mask], weights=bw)), 6),
                    "avg_predicted": round(float(np.average(y_pred[mask], weights=bw)), 6),
                }
            )

    # NaN bin
    if nan_mask.any():
        bw = weight[nan_mask]
        total_w = bw.sum()
        if total_w > 0:
            bins.append(
                {
                    "label": "Missing",
                    "exposure": round(float(total_w), 4),
                    "avg_actual": round(float(np.average(y_true[nan_mask], weights=bw)), 6),
                    "avg_predicted": round(float(np.average(y_pred[nan_mask], weights=bw)), 6),
                }
            )

    return bins


def _ave_categorical_bins(
    col: pl.Series,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weight: np.ndarray,
    max_categories: int,
) -> list[dict[str, Any]]:
    """Group a categorical feature by category and compute weighted AvE."""
    if len(col) == 0:
        return []

    # Vectorized groupby: no Python row loops
    tmp = pl.DataFrame(
        {
            "cat": col.cast(pl.Utf8).fill_null("__MISSING__"),
            "w": weight,
            "wa": y_true * weight,
            "wp": y_pred * weight,
        }
    )
    grouped = (
        tmp.group_by("cat")
        .agg(
            pl.col("w").sum().alias("exposure"),
            pl.col("wa").sum().alias("sum_actual"),
            pl.col("wp").sum().alias("sum_predicted"),
        )
        .sort("exposure", descending=True)
    )

    top = grouped.head(max_categories)
    remainder = grouped.slice(max_categories)

    bins: list[dict[str, Any]] = []
    for row in top.iter_rows(named=True):
        if row["exposure"] == 0:
            continue
        label = "Missing" if row["cat"] == "__MISSING__" else row["cat"]
        bins.append(
            {
                "label": label,
                "exposure": round(row["exposure"], 4),
                "avg_actual": round(row["sum_actual"] / row["exposure"], 6),
                "avg_predicted": round(row["sum_predicted"] / row["exposure"], 6),
            }
        )

    # Lump remaining categories into "Other"
    if remainder.height > 0:
        other_exposure = float(remainder["exposure"].sum())
        if other_exposure > 0:
            bins.append(
                {
                    "label": "Other",
                    "exposure": round(other_exposure, 4),
                    "avg_actual": round(float(remainder["sum_actual"].sum()) / other_exposure, 6),
                    "avg_predicted": round(
                        float(remainder["sum_predicted"].sum()) / other_exposure, 6
                    ),
                }
            )

    return bins


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

    w = weight if weight is not None else np.ones(n)

    if n <= max_points:
        return [
            {
                "actual": round(float(y_true[i]), 6),
                "predicted": round(float(y_pred[i]), 6),
                "weight": round(float(w[i]), 6),
            }
            for i in range(n)
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
        indices = np.where(mask)[0]
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


def compute_pdp(
    model: Any,
    algo: Any,
    df: pl.DataFrame,
    features: list[str],
    cat_features: list[str],
    *,
    n_grid: int = 50,
    max_sample: int = 500,
) -> list[dict[str, Any]]:
    """Compute partial dependence plots for all features.

    For each feature:

    - *Numeric*: create a grid of *n_grid* values (percentile-spaced from
      data), then ``np.unique`` to deduplicate.
    - *Categorical*: use unique values (up to 30 most frequent).
    - For each grid value: replace the column, predict, and average.

    Returns ``[{feature, type, grid: [{value, avg_prediction}]}]`` in the
    same order as *features*.
    """
    if df.is_empty() or len(features) == 0:
        return []

    # Subsample rows if needed
    n_rows = df.height
    if n_rows > max_sample:
        rng = np.random.RandomState(42)
        idx = rng.choice(n_rows, size=max_sample, replace=False)
        sample_df = df[idx.tolist()]
    else:
        sample_df = df

    cat_set = set(cat_features)
    results: list[dict[str, Any]] = []

    for feat in features:
        try:
            if feat not in sample_df.columns:
                continue

            is_cat = feat in cat_set

            if is_cat:
                # Categorical: unique values, top 30 by frequency
                val_counts = (
                    sample_df.select(pl.col(feat).cast(pl.Utf8))
                    .group_by(feat)
                    .len()
                    .sort("len", descending=True)
                )
                grid_values = val_counts[feat].to_list()[:30]
                feat_type = "categorical"
            else:
                # Numeric: percentile-spaced grid
                col_vals = sample_df[feat].drop_nulls().to_numpy().astype(float)
                if len(col_vals) == 0:
                    continue
                percentiles = np.linspace(0, 100, n_grid)
                raw_grid = np.percentile(col_vals, percentiles)
                grid_values = np.unique(raw_grid).tolist()
                feat_type = "numeric"

            grid_entries: list[dict[str, Any]] = []
            for val in grid_values:
                if is_cat:
                    modified = sample_df.with_columns(
                        pl.lit(val).cast(sample_df[feat].dtype).alias(feat)
                    )
                else:
                    modified = sample_df.with_columns(
                        pl.lit(float(val)).cast(sample_df[feat].dtype).alias(feat)
                    )
                preds = algo.predict(model, modified, features)
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
        except Exception:  # noqa: BLE001
            # Defensive: skip features that fail (e.g. unsupported dtype)
            continue

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
}
