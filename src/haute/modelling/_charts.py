"""Pure-SVG chart generation for model diagnostics.

All functions return SVG strings — zero dependencies beyond stdlib.
"""

from __future__ import annotations

import html
import math
from typing import Any

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

COLOR_BARS = "#D1D5DB"  # grey — exposure / count bars
COLOR_ACTUAL = "#2563EB"  # blue — actual line
COLOR_PREDICTED = "#F97316"  # orange — predicted line
COLOR_TRAIN = "#8B5CF6"  # purple — train loss
COLOR_EVAL = "#22C55E"  # green — eval loss
COLOR_BEST_ITER = "#EF4444"  # red — best iteration marker
COLOR_IMPORTANCE = "#2563EB"  # blue — importance bars
COLOR_SHAP = "#F97316"  # orange — SHAP bars
COLOR_AXIS = "#374151"
COLOR_GRID = "#E5E7EB"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    """XML/HTML-safe text."""
    return html.escape(str(text))


def _truncate_label(label: str, max_len: int = 25) -> str:
    if len(label) <= max_len:
        return label
    return label[: max_len - 1] + "…"


def _nice_ticks(min_val: float, max_val: float, n_ticks: int = 5) -> list[float]:
    """Generate clean tick values for an axis range."""
    if not math.isfinite(min_val) or not math.isfinite(max_val):
        return []
    if min_val == max_val:
        return [min_val]
    span = max_val - min_val
    raw_step = span / max(n_ticks - 1, 1)

    # Round step to a "nice" number
    magnitude = 10 ** math.floor(math.log10(max(abs(raw_step), 1e-15)))
    residual = raw_step / magnitude
    if residual <= 1.5:
        nice_step = 1 * magnitude
    elif residual <= 3.5:
        nice_step = 2 * magnitude
    elif residual <= 7.5:
        nice_step = 5 * magnitude
    else:
        nice_step = 10 * magnitude

    start = math.floor(min_val / nice_step) * nice_step
    ticks = []
    v = start
    while v <= max_val + nice_step * 0.01:
        ticks.append(round(v, 10))
        v += nice_step
    return ticks


def _format_tick(val: float) -> str:
    """Format a tick value: 1.23, 1.2k, 1.2M."""
    abs_val = abs(val)
    if abs_val == 0:
        return "0"
    if abs_val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if abs_val >= 1_000:
        return f"{val / 1_000:.1f}k"
    if abs_val >= 1:
        return f"{val:.2f}"
    return f"{val:.4g}"


def _placeholder_svg(width: int, height: int, message: str) -> str:
    """Fallback SVG for empty or invalid data."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#F9FAFB" rx="4"/>'
        f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
        f'dominant-baseline="middle" fill="{COLOR_AXIS}" font-size="13" '
        f'font-family="system-ui, sans-serif">{_escape(message)}</text>'
        f"</svg>"
    )


# ---------------------------------------------------------------------------
# Shared dual-axis chart renderer
# ---------------------------------------------------------------------------


def _render_dual_axis_chart(
    *,
    title: str,
    x_labels: list[Any],
    bar_values: list[float],
    bar_label: str,
    line_series: dict[str, tuple[list[float], str]],
    width: int = 600,
    height: int = 360,
    bottom_margin: int = 50,
    rotate_labels: bool = False,
) -> str:
    """Shared dual-axis chart: bars on the right axis, lines on the left.

    Parameters
    ----------
    title:       Chart title.
    x_labels:    Labels for each bar/point on the x-axis.
    bar_values:  Values for the bar series (right axis).
    bar_label:   Legend label for bars (e.g. "Count", "Exposure").
    line_series: ``{label: (values, color)}`` for each line series.
    width/height: SVG dimensions.
    bottom_margin: Bottom margin (taller for rotated labels).
    rotate_labels: Rotate x-axis labels -45° when True.
    """
    margin = {"top": 30, "right": 55, "bottom": bottom_margin, "left": 55}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    n = len(x_labels)
    bar_w = max(plot_w / n * 0.6, 4)
    gap = plot_w / n

    max_bar = max(bar_values) if bar_values else 1
    if max_bar == 0:
        max_bar = 1

    all_line_vals = [v for vals, _c in line_series.values() for v in vals]
    y_min = min(all_line_vals) if all_line_vals else 0
    y_max = max(all_line_vals) if all_line_vals else 1
    if y_min == y_max:
        y_min -= 0.5
        y_max += 0.5
    y_pad = (y_max - y_min) * 0.1
    y_min -= y_pad
    y_max += y_pad

    def x_pos(i: int) -> float:
        return margin["left"] + gap * i + gap / 2

    def y_bar(v: float) -> float:
        return margin["top"] + plot_h * (1 - v / max_bar)

    def y_line(v: float) -> float:
        return margin["top"] + plot_h * (1 - (v - y_min) / (y_max - y_min))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white" rx="4"/>',
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="600" fill="{COLOR_AXIS}">{_escape(title)}</text>',
    ]

    # Grid lines + left-axis ticks
    for tick in _nice_ticks(y_min, y_max, 5):
        yp = y_line(tick)
        if margin["top"] <= yp <= margin["top"] + plot_h:
            parts.append(
                f'<line x1="{margin["left"]}" y1="{yp:.1f}" '
                f'x2="{margin["left"] + plot_w}" y2="{yp:.1f}" '
                f'stroke="{COLOR_GRID}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{margin["left"] - 5}" y="{yp:.1f}" '
                f'text-anchor="end" dominant-baseline="middle" '
                f'font-size="10" fill="{COLOR_AXIS}">{_format_tick(tick)}</text>'
            )

    # Bars
    for i, bv in enumerate(bar_values):
        bx = x_pos(i) - bar_w / 2
        by = y_bar(bv)
        bh = max(margin["top"] + plot_h - by, 0)
        parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
            f'height="{bh:.1f}" fill="{COLOR_BARS}" rx="1"/>'
        )

    # Line series
    for _label, (values, color) in line_series.items():
        if n > 1:
            pts = " ".join(f"{x_pos(i):.1f},{y_line(values[i]):.1f}" for i in range(n))
            parts.append(
                f'<polyline points="{pts}" fill="none" '
                f'stroke="{color}" stroke-width="2" stroke-linejoin="round"/>'
            )
        for i in range(n):
            parts.append(
                f'<circle cx="{x_pos(i):.1f}" cy="{y_line(values[i]):.1f}" r="3" fill="{color}"/>'
            )

    # X-axis labels
    label_y = margin["top"] + plot_h + 14
    for i, label in enumerate(x_labels):
        display = _truncate_label(str(label), 15)
        xp = x_pos(i)
        if rotate_labels:
            parts.append(
                f'<text x="{xp:.1f}" y="{label_y}" '
                f'text-anchor="end" font-size="9" fill="{COLOR_AXIS}" '
                f'transform="rotate(-45, {xp:.1f}, {label_y})">'
                f"{_escape(display)}</text>"
            )
        else:
            parts.append(
                f'<text x="{xp:.1f}" y="{label_y + 2}" '
                f'text-anchor="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{_escape(str(label))}</text>"
            )

    # Right-axis label for bars
    parts.append(
        f'<text x="{width - 5}" y="{margin["top"] + plot_h // 2}" '
        f'text-anchor="end" font-size="10" fill="{COLOR_BARS}" '
        f'transform="rotate(-90, {width - 5}, {margin["top"] + plot_h // 2})">'
        f"{_escape(bar_label)}</text>"
    )

    # Legend
    lx = margin["left"] + 10
    ly = margin["top"] + 12
    offset = 0
    for label, (_vals, color) in line_series.items():
        parts.append(
            f'<rect x="{lx + offset}" y="{ly - 4}" width="10" height="3" fill="{color}"/>'
            f'<text x="{lx + offset + 14}" y="{ly}" font-size="10" fill="{COLOR_AXIS}">'
            f"{_escape(label)}</text>"
        )
        offset += len(label) * 7 + 24
    parts.append(
        f'<rect x="{lx + offset}" y="{ly - 6}" width="10" height="10" fill="{COLOR_BARS}" rx="1"/>'
        f'<text x="{lx + offset + 14}" y="{ly}" font-size="10" fill="{COLOR_AXIS}">'
        f"{_escape(bar_label)}</text>"
    )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. Double lift chart
# ---------------------------------------------------------------------------


def render_double_lift_svg(double_lift: list[dict[str, Any]]) -> str:
    """Dual-axis double-lift chart: bars for count, lines for actual/predicted."""
    if not double_lift:
        return _placeholder_svg(600, 360, "No double-lift data")

    return _render_dual_axis_chart(
        title="Double Lift (Actual vs Predicted by Decile)",
        x_labels=[d["decile"] for d in double_lift],
        bar_values=[d["count"] for d in double_lift],
        bar_label="Count",
        line_series={
            "Actual": ([d["actual"] for d in double_lift], COLOR_ACTUAL),
            "Predicted": ([d["predicted"] for d in double_lift], COLOR_PREDICTED),
        },
    )


# ---------------------------------------------------------------------------
# 2. Loss curve chart
# ---------------------------------------------------------------------------


def render_loss_curve_svg(
    loss_history: list[dict[str, float]],
    best_iteration: int | None = None,
) -> str:
    """Loss curve: train (purple), eval (green), best iteration (red dashed)."""
    width, height = 600, 320
    if not loss_history:
        return _placeholder_svg(width, height, "No loss history data")

    margin = {"top": 30, "right": 20, "bottom": 40, "left": 60}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    # Subsample if >300 points
    data = loss_history
    if len(data) > 300:
        step = len(data) / 300
        indices = set()
        for i in range(300):
            indices.add(int(i * step))
        indices.add(0)
        indices.add(len(data) - 1)
        if best_iteration is not None and 0 <= best_iteration < len(data):
            indices.add(best_iteration)
        sorted_idx = sorted(indices)
        data = [loss_history[i] for i in sorted_idx]

    # Extract series
    iterations = [d.get("iteration", i) for i, d in enumerate(data)]
    train_keys = [k for k in data[0] if k.startswith("train_")]
    eval_keys = [k for k in data[0] if k.startswith("eval_")]
    train_key = train_keys[0] if train_keys else None
    eval_key = eval_keys[0] if eval_keys else None

    train_vals = [d[train_key] for d in data] if train_key else []
    eval_vals = [d[eval_key] for d in data] if eval_key else []

    all_vals = train_vals + eval_vals
    if not all_vals:
        return _placeholder_svg(width, height, "No loss values found")

    y_min_val = min(all_vals)
    y_max_val = max(all_vals)
    if y_min_val == y_max_val:
        y_min_val -= 0.5
        y_max_val += 0.5
    y_pad = (y_max_val - y_min_val) * 0.05
    y_min_val -= y_pad
    y_max_val += y_pad

    x_min = min(iterations)
    x_max = max(iterations)
    if x_min == x_max:
        x_max = x_min + 1

    def x_pos(v: float) -> float:
        return margin["left"] + plot_w * (v - x_min) / (x_max - x_min)

    def y_pos(v: float) -> float:
        return margin["top"] + plot_h * (1 - (v - y_min_val) / (y_max_val - y_min_val))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white" rx="4"/>',
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="600" fill="{COLOR_AXIS}">Training Loss Curve</text>',
    ]

    # Grid
    for tick in _nice_ticks(y_min_val, y_max_val, 5):
        yp = y_pos(tick)
        if margin["top"] <= yp <= margin["top"] + plot_h:
            parts.append(
                f'<line x1="{margin["left"]}" y1="{yp:.1f}" '
                f'x2="{margin["left"] + plot_w}" y2="{yp:.1f}" '
                f'stroke="{COLOR_GRID}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{margin["left"] - 5}" y="{yp:.1f}" '
                f'text-anchor="end" dominant-baseline="middle" '
                f'font-size="10" fill="{COLOR_AXIS}">{_format_tick(tick)}</text>'
            )

    # Train line
    if train_vals:
        points = " ".join(
            f"{x_pos(iterations[i]):.1f},{y_pos(train_vals[i]):.1f}" for i in range(len(data))
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{COLOR_TRAIN}" stroke-width="2"/>'
        )

    # Eval line
    if eval_vals:
        points = " ".join(
            f"{x_pos(iterations[i]):.1f},{y_pos(eval_vals[i]):.1f}" for i in range(len(data))
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{COLOR_EVAL}" stroke-width="2"/>'
        )

    # Best iteration line
    if best_iteration is not None:
        clamped = min(best_iteration, x_max)
        bx = x_pos(clamped)
        parts.append(
            f'<line x1="{bx:.1f}" y1="{margin["top"]}" '
            f'x2="{bx:.1f}" y2="{margin["top"] + plot_h}" '
            f'stroke="{COLOR_BEST_ITER}" stroke-width="1.5" '
            f'stroke-dasharray="6,3"/>'
        )
        parts.append(
            f'<text x="{bx:.1f}" y="{margin["top"] - 4}" '
            f'text-anchor="middle" font-size="9" fill="{COLOR_BEST_ITER}">'
            f"best={best_iteration}</text>"
        )

    # X-axis labels
    for tick in _nice_ticks(x_min, x_max, 6):
        xp = x_pos(tick)
        if margin["left"] <= xp <= margin["left"] + plot_w:
            parts.append(
                f'<text x="{xp:.1f}" y="{margin["top"] + plot_h + 16}" '
                f'text-anchor="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{int(tick)}</text>"
            )

    # Legend
    lx = margin["left"] + 10
    ly = margin["top"] + 12
    if train_key:
        parts.append(
            f'<rect x="{lx}" y="{ly - 4}" width="10" height="3" fill="{COLOR_TRAIN}"/>'
            f'<text x="{lx + 14}" y="{ly}" font-size="10" fill="{COLOR_AXIS}">Train</text>'
        )
    if eval_key:
        parts.append(
            f'<rect x="{lx + 55}" y="{ly - 4}" width="10" height="3" fill="{COLOR_EVAL}"/>'
            f'<text x="{lx + 69}" y="{ly}" font-size="10" fill="{COLOR_AXIS}">Eval</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 3. Horizontal bar chart (feature importance / SHAP)
# ---------------------------------------------------------------------------


def render_horizontal_bars_svg(
    data: list[dict[str, Any]],
    name_key: str,
    value_key: str,
    *,
    title: str = "",
    color: str = COLOR_IMPORTANCE,
    max_items: int = 15,
) -> str:
    """Horizontal bar chart — shared by feature importance and SHAP."""
    if not data:
        return _placeholder_svg(400, 200, f"No {title.lower() or 'data'}")

    items = sorted(data, key=lambda d: d.get(value_key, 0), reverse=True)[:max_items]
    n = len(items)
    bar_height = 20
    gap = 6
    margin = {"top": 30, "right": 20, "bottom": 10, "left": 150}
    total_height = margin["top"] + n * (bar_height + gap) + margin["bottom"]
    width = 500
    plot_w = width - margin["left"] - margin["right"]

    max_val = max(abs(d[value_key]) for d in items) if items else 1
    if max_val == 0:
        max_val = 1

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_height}" '
        f'viewBox="0 0 {width} {total_height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{total_height}" fill="white" rx="4"/>',
    ]

    if title:
        parts.append(
            f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
            f'font-weight="600" fill="{COLOR_AXIS}">{_escape(title)}</text>'
        )

    for i, item in enumerate(items):
        y = margin["top"] + i * (bar_height + gap)
        val = abs(item[value_key])
        bar_w = plot_w * val / max_val

        label = _truncate_label(str(item[name_key]))
        parts.append(
            f'<text x="{margin["left"] - 5}" y="{y + bar_height / 2 + 1}" '
            f'text-anchor="end" dominant-baseline="middle" '
            f'font-size="11" fill="{COLOR_AXIS}">{_escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{margin["left"]}" y="{y}" width="{bar_w:.1f}" '
            f'height="{bar_height}" fill="{color}" rx="2"/>'
        )
        parts.append(
            f'<text x="{margin["left"] + bar_w + 4}" y="{y + bar_height / 2 + 1}" '
            f'dominant-baseline="middle" font-size="10" fill="{COLOR_AXIS}">'
            f"{item[value_key]:.4f}</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 4. AvE per-feature chart (dual axis: bars + lines)
# ---------------------------------------------------------------------------


def render_ave_feature_svg(
    feature_name: str,
    bins: list[dict[str, Any]],
) -> str:
    """Dual-axis AvE chart for a single feature."""
    if not bins:
        return _placeholder_svg(600, 320, f"No data for {_escape(feature_name)}")

    labels = [b["label"] for b in bins]
    any_long = any(len(str(lab)) > 5 for lab in labels)

    return _render_dual_axis_chart(
        title=feature_name,
        x_labels=labels,
        bar_values=[b["exposure"] for b in bins],
        bar_label="Exposure",
        line_series={
            "Actual": ([b["avg_actual"] for b in bins], COLOR_ACTUAL),
            "Predicted": ([b["avg_predicted"] for b in bins], COLOR_PREDICTED),
        },
        height=320,
        bottom_margin=70,
        rotate_labels=any_long,
    )


# ---------------------------------------------------------------------------
# 5. Lorenz curve chart
# ---------------------------------------------------------------------------

COLOR_DIAGONAL = "#9CA3AF"  # light grey — diagonal reference


def render_lorenz_curve_svg(
    model_curve: list[dict[str, float]],
    perfect_curve: list[dict[str, float]],
) -> str:
    """Lorenz curve: model (blue) vs perfect (green) with diagonal reference."""
    width, height = 420, 400
    if not model_curve and not perfect_curve:
        return _placeholder_svg(width, height, "No Lorenz curve data")

    margin = {"top": 30, "right": 20, "bottom": 45, "left": 50}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    def x_pos(v: float) -> float:
        return margin["left"] + plot_w * v

    def y_pos(v: float) -> float:
        return margin["top"] + plot_h * (1 - v)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white" rx="4"/>',
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="600" fill="{COLOR_AXIS}">Lorenz Curve</text>',
    ]

    # Grid + axis labels (0%, 20%, 40%, 60%, 80%, 100%)
    for tick in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
        yp, xp = y_pos(tick), x_pos(tick)
        parts.append(
            f'<line x1="{margin["left"]}" y1="{yp:.1f}" '
            f'x2="{margin["left"] + plot_w}" y2="{yp:.1f}" '
            f'stroke="{COLOR_GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{margin["left"] - 5}" y="{yp:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" font-size="10" fill="{COLOR_AXIS}">'
            f"{tick:.0%}</text>"
        )
        parts.append(
            f'<text x="{xp:.1f}" y="{margin["top"] + plot_h + 16}" '
            f'text-anchor="middle" font-size="10" fill="{COLOR_AXIS}">'
            f"{tick:.0%}</text>"
        )

    # Diagonal reference (random model)
    parts.append(
        f'<line x1="{x_pos(0):.1f}" y1="{y_pos(0):.1f}" '
        f'x2="{x_pos(1):.1f}" y2="{y_pos(1):.1f}" '
        f'stroke="{COLOR_DIAGONAL}" stroke-width="1" stroke-dasharray="4,3"/>'
    )

    # Model curve (blue)
    if model_curve:
        pts = " ".join(
            f"{x_pos(p['cum_weight_frac']):.1f},{y_pos(p['cum_actual_frac']):.1f}"
            for p in model_curve
        )
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{COLOR_ACTUAL}" stroke-width="2"/>'
        )

    # Perfect curve (green)
    if perfect_curve:
        pts = " ".join(
            f"{x_pos(p['cum_weight_frac']):.1f},{y_pos(p['cum_actual_frac']):.1f}"
            for p in perfect_curve
        )
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{COLOR_EVAL}" stroke-width="2"/>'
        )

    # Legend
    lx, ly = margin["left"] + 10, margin["top"] + 14
    parts.append(
        f'<rect x="{lx}" y="{ly - 4}" width="10" height="3" fill="{COLOR_ACTUAL}"/>'
        f'<text x="{lx + 14}" y="{ly}" font-size="10" fill="{COLOR_AXIS}">Model</text>'
    )
    parts.append(
        f'<rect x="{lx + 55}" y="{ly - 4}" width="10" height="3" fill="{COLOR_EVAL}"/>'
        f'<text x="{lx + 69}" y="{ly}" font-size="10" fill="{COLOR_AXIS}">Perfect</text>'
    )

    # X-axis label
    parts.append(
        f'<text x="{width // 2}" y="{height - 5}" text-anchor="middle" '
        f'font-size="11" fill="{COLOR_AXIS}">Cumulative weight fraction</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 6. Residuals histogram
# ---------------------------------------------------------------------------


def render_residuals_svg(
    histogram: list[dict[str, Any]],
    stats: dict[str, float] | None = None,
) -> str:
    """Vertical bar histogram of residuals with optional stats annotation."""
    width, height = 600, 320
    if not histogram:
        return _placeholder_svg(width, height, "No residuals data")

    margin = {"top": 30, "right": 20, "bottom": 40, "left": 60}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    centers = [b["bin_center"] for b in histogram]
    wc = [b["weighted_count"] for b in histogram]
    x_min, x_max = min(centers), max(centers)
    y_max = max(wc) if wc else 1
    if x_min == x_max:
        x_min -= 1
        x_max += 1
    if y_max == 0:
        y_max = 1

    n = len(histogram)
    bar_w = max(plot_w / n * 0.85, 2)

    def x_pos(v: float) -> float:
        return float(margin["left"] + plot_w * (v - x_min) / (x_max - x_min))

    def y_pos(v: float) -> float:
        return float(margin["top"] + plot_h * (1 - v / y_max))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white" rx="4"/>',
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="600" fill="{COLOR_AXIS}">Residual Distribution</text>',
    ]

    # Y-axis grid
    for tick in _nice_ticks(0, y_max, 5):
        yp = y_pos(tick)
        if margin["top"] <= yp <= margin["top"] + plot_h:
            parts.append(
                f'<line x1="{margin["left"]}" y1="{yp:.1f}" '
                f'x2="{margin["left"] + plot_w}" y2="{yp:.1f}" '
                f'stroke="{COLOR_GRID}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{margin["left"] - 5}" y="{yp:.1f}" text-anchor="end" '
                f'dominant-baseline="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{_format_tick(tick)}</text>"
            )

    # Bars
    baseline_y = margin["top"] + plot_h
    for b in histogram:
        bx = x_pos(b["bin_center"]) - bar_w / 2
        by = y_pos(b["weighted_count"])
        bh = max(baseline_y - by, 0)
        parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
            f'height="{bh:.1f}" fill="{COLOR_ACTUAL}" rx="1"/>'
        )

    # X-axis ticks
    for tick in _nice_ticks(x_min, x_max, 6):
        xp = x_pos(tick)
        if margin["left"] <= xp <= margin["left"] + plot_w:
            parts.append(
                f'<text x="{xp:.1f}" y="{margin["top"] + plot_h + 16}" '
                f'text-anchor="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{_format_tick(tick)}</text>"
            )

    # Stats annotation (top-right)
    if stats:
        sx = margin["left"] + plot_w - 5
        sy = margin["top"] + 10
        lines = [
            f"mean={stats.get('mean', 0):.4g}",
            f"std={stats.get('std', 0):.4g}",
            f"skew={stats.get('skew', 0):.4g}",
        ]
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="{sx}" y="{sy + j * 14}" text-anchor="end" '
                f'font-size="10" fill="{COLOR_AXIS}">{_escape(line)}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 7. Actual vs Predicted scatter
# ---------------------------------------------------------------------------

COLOR_DOT = "#2563EB"  # blue, same as COLOR_ACTUAL


def render_scatter_svg(
    points: list[dict[str, float]],
) -> str:
    """Actual vs predicted scatter plot with diagonal reference."""
    width, height = 420, 400
    if not points:
        return _placeholder_svg(width, height, "No scatter data")

    margin = {"top": 30, "right": 20, "bottom": 45, "left": 55}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    actuals = [p["actual"] for p in points]
    preds = [p["predicted"] for p in points]
    all_vals = actuals + preds
    v_min, v_max = min(all_vals), max(all_vals)
    if v_min == v_max:
        v_min -= 1
        v_max += 1
    pad = (v_max - v_min) * 0.05
    v_min -= pad
    v_max += pad

    def x_pos(v: float) -> float:
        return margin["left"] + plot_w * (v - v_min) / (v_max - v_min)

    def y_pos(v: float) -> float:
        return margin["top"] + plot_h * (1 - (v - v_min) / (v_max - v_min))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white" rx="4"/>',
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="600" fill="{COLOR_AXIS}">Actual vs Predicted</text>',
    ]

    # Grid
    ticks = _nice_ticks(v_min, v_max, 5)
    for tick in ticks:
        yp, xp = y_pos(tick), x_pos(tick)
        if margin["top"] <= yp <= margin["top"] + plot_h:
            parts.append(
                f'<line x1="{margin["left"]}" y1="{yp:.1f}" '
                f'x2="{margin["left"] + plot_w}" y2="{yp:.1f}" '
                f'stroke="{COLOR_GRID}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{margin["left"] - 5}" y="{yp:.1f}" text-anchor="end" '
                f'dominant-baseline="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{_format_tick(tick)}</text>"
            )
        if margin["left"] <= xp <= margin["left"] + plot_w:
            parts.append(
                f'<text x="{xp:.1f}" y="{margin["top"] + plot_h + 16}" '
                f'text-anchor="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{_format_tick(tick)}</text>"
            )

    # Diagonal reference
    parts.append(
        f'<line x1="{x_pos(v_min):.1f}" y1="{y_pos(v_min):.1f}" '
        f'x2="{x_pos(v_max):.1f}" y2="{y_pos(v_max):.1f}" '
        f'stroke="{COLOR_DIAGONAL}" stroke-width="1" stroke-dasharray="4,3"/>'
    )

    # Dots (semi-transparent for overlap)
    for p in points:
        parts.append(
            f'<circle cx="{x_pos(p["predicted"]):.1f}" cy="{y_pos(p["actual"]):.1f}" '
            f'r="2.5" fill="{COLOR_DOT}" opacity="0.35"/>'
        )

    # Axis labels
    parts.append(
        f'<text x="{width // 2}" y="{height - 5}" text-anchor="middle" '
        f'font-size="11" fill="{COLOR_AXIS}">Predicted</text>'
    )
    parts.append(
        f'<text x="12" y="{margin["top"] + plot_h // 2}" text-anchor="middle" '
        f'font-size="11" fill="{COLOR_AXIS}" '
        f'transform="rotate(-90, 12, {margin["top"] + plot_h // 2})">Actual</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 8. Partial Dependence Plot (per feature)
# ---------------------------------------------------------------------------


def render_pdp_feature_svg(
    feature_name: str,
    grid: list[dict[str, Any]],
    feat_type: str,
) -> str:
    """PDP chart for one feature: line for numeric, bars for categorical."""
    width, height = 600, 280
    if not grid:
        return _placeholder_svg(width, height, f"No PDP data for {_escape(feature_name)}")

    margin = {"top": 30, "right": 20, "bottom": 50, "left": 60}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    values = [g["value"] for g in grid]
    preds = [g["avg_prediction"] for g in grid]
    y_min, y_max = min(preds), max(preds)
    if y_min == y_max:
        y_min -= 0.5
        y_max += 0.5
    y_pad = (y_max - y_min) * 0.1
    y_min -= y_pad
    y_max += y_pad

    def y_pos(v: float) -> float:
        return float(margin["top"] + plot_h * (1 - (v - y_min) / (y_max - y_min)))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white" rx="4"/>',
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="600" fill="{COLOR_AXIS}">{_escape(feature_name)}</text>',
    ]

    # Y-axis grid
    for tick in _nice_ticks(y_min, y_max, 5):
        yp = y_pos(tick)
        if margin["top"] <= yp <= margin["top"] + plot_h:
            parts.append(
                f'<line x1="{margin["left"]}" y1="{yp:.1f}" '
                f'x2="{margin["left"] + plot_w}" y2="{yp:.1f}" '
                f'stroke="{COLOR_GRID}" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{margin["left"] - 5}" y="{yp:.1f}" text-anchor="end" '
                f'dominant-baseline="middle" font-size="10" fill="{COLOR_AXIS}">'
                f"{_format_tick(tick)}</text>"
            )

    n = len(grid)
    if feat_type == "categorical":
        # Bar chart
        gap = plot_w / n
        bar_w = max(gap * 0.6, 4)
        for i, g in enumerate(grid):
            cx = margin["left"] + gap * i + gap / 2
            by = y_pos(g["avg_prediction"])
            baseline = y_pos(y_min)
            bh = max(baseline - by, 0)
            parts.append(
                f'<rect x="{cx - bar_w / 2:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
                f'height="{bh:.1f}" fill="{COLOR_ACTUAL}" rx="2"/>'
            )
            label = _truncate_label(str(g["value"]), 12)
            parts.append(
                f'<text x="{cx:.1f}" y="{margin["top"] + plot_h + 14}" '
                f'text-anchor="end" font-size="9" fill="{COLOR_AXIS}" '
                f'transform="rotate(-45, {cx:.1f}, {margin["top"] + plot_h + 14})">'
                f"{_escape(label)}</text>"
            )
    else:
        # Line chart — numeric values on X axis
        num_vals = [float(v) for v in values]
        x_min_v, x_max_v = min(num_vals), max(num_vals)
        if x_min_v == x_max_v:
            x_min_v -= 1
            x_max_v += 1

        def x_pos(v: float) -> float:
            return margin["left"] + plot_w * (v - x_min_v) / (x_max_v - x_min_v)

        # Line
        pts = " ".join(f"{x_pos(num_vals[i]):.1f},{y_pos(preds[i]):.1f}" for i in range(n))
        parts.append(
            f'<polyline points="{pts}" fill="none" '
            f'stroke="{COLOR_ACTUAL}" stroke-width="2" stroke-linejoin="round"/>'
        )
        for i in range(n):
            parts.append(
                f'<circle cx="{x_pos(num_vals[i]):.1f}" cy="{y_pos(preds[i]):.1f}" '
                f'r="2.5" fill="{COLOR_ACTUAL}"/>'
            )

        # X-axis ticks
        for tick in _nice_ticks(x_min_v, x_max_v, 6):
            xp = x_pos(tick)
            if margin["left"] <= xp <= margin["left"] + plot_w:
                parts.append(
                    f'<text x="{xp:.1f}" y="{margin["top"] + plot_h + 16}" '
                    f'text-anchor="middle" font-size="10" fill="{COLOR_AXIS}">'
                    f"{_format_tick(tick)}</text>"
                )

    parts.append("</svg>")
    return "\n".join(parts)
