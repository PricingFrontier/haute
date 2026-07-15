"""Lazy environment-variable accessors for numeric tuning knobs.

Timeout/limit knobs must be read from ``os.environ`` at call time, never
frozen into module-level constants at import: a constant captured at import
silently ignores overrides set afterwards (programmatic server start, pytest
``monkeypatch.setenv``, uvicorn reload). A malformed value degrades to the
default with a warning instead of failing the request (or, worse, the
module import).
"""

from __future__ import annotations

import os

from haute._logging import get_logger

logger = get_logger(component="env")


def float_env(name: str, default: float) -> float:
    """Read ``name`` from the environment as a float, at call time.

    Returns ``default`` when the variable is unset or unparseable.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "invalid_env_value",
            name=name,
            value=raw,
            default=default,
        )
        return default


def int_env(name: str, default: int) -> int:
    """Read ``name`` from the environment as an int, at call time.

    Returns ``default`` when the variable is unset or unparseable.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "invalid_env_value",
            name=name,
            value=raw,
            default=default,
        )
        return default


def optional_int_env(name: str) -> int | None:
    """Read ``name`` from the environment as an int, at call time.

    Returns ``None`` when the variable is unset, empty, or unparseable.
    """
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "invalid_env_value",
            name=name,
            value=raw,
            default=None,
        )
        return None
