"""Canonical environment parsing for positive numeric tuning knobs.

Each helper reads ``os.environ`` once when invoked. Callers decide when resolution
happens: request-time accessors remain live, while intentionally process-wide cache
budgets may resolve during module import. In both cases, unset variables use their
documented default. Explicitly configured values must be finite and positive;
invalid configuration raises ``RuntimeError`` rather than weakening a safety limit.
"""

from __future__ import annotations

import os
from math import isfinite


def float_env(name: str, default: float) -> float:
    """Read ``name`` from the environment as a float, at call time.

    Returns ``default`` when the variable is unset. Configured values must be
    finite and greater than zero.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a finite number greater than 0") from exc
    if not isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a finite number greater than 0")
    return value


def int_env(name: str, default: int) -> int:
    """Read ``name`` from the environment as an int, at call time.

    Returns ``default`` when the variable is unset. Configured values must be
    positive integers.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def optional_int_env(name: str) -> int | None:
    """Read ``name`` from the environment as an int, at call time.

    Returns ``None`` only when the variable is unset. Configured values must be
    positive integers.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value
