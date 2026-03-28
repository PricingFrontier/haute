"""Waterfall data generation for sequential multiplicative/additive rating steps.

Given a list of rating steps (base, multiply, add), produces a waterfall
summary with cumulative values and deltas at each step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class WaterfallEntry:
    """A single entry in the waterfall."""

    label: str
    operation: str
    value: float
    delta: float
    cumulative: float


@dataclass
class WaterfallResult:
    """Complete waterfall summary."""

    entries: list[WaterfallEntry]
    final_value: float


def build_waterfall(steps: list[dict[str, Any]]) -> WaterfallResult | None:
    """Build a waterfall from a list of rating steps.

    Each step dict has keys: ``label``, ``operation`` (``"base"``,
    ``"multiply"``, ``"add"``), and ``value``.

    Returns ``None`` if fewer than 3 steps are provided (not enough
    for a meaningful waterfall).
    """
    if len(steps) < 3:
        return None

    entries: list[WaterfallEntry] = []
    cumulative = 0.0

    try:
        for step in steps:
            label = step.get("label", "")
            operation = step.get("operation", "base")
            raw_value = step.get("value", 0)
            try:
                value = float(raw_value)
            except (ValueError, TypeError):
                return None

            if operation == "base":
                cumulative = value
                delta = 0.0
            elif operation == "multiply":
                new_cumulative = cumulative * value
                delta = new_cumulative - cumulative
                cumulative = new_cumulative
            elif operation == "add":
                delta = value
                cumulative = cumulative + value
            else:
                delta = 0.0

            if not math.isfinite(cumulative):
                return None

            entries.append(
                WaterfallEntry(
                    label=label,
                    operation=operation,
                    value=value,
                    delta=delta,
                    cumulative=cumulative,
                )
            )
    except Exception:
        return None

    return WaterfallResult(entries=entries, final_value=cumulative)
