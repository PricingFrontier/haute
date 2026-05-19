"""Shared validation for Explore overview configuration."""

from __future__ import annotations

import math
from typing import Any

from haute.errors import ConfigError

EXPLORE_OVERVIEW_TOGGLE_KEYS: frozenset[str] = frozenset(
    {
        "dataset_header",
        "schema",
    }
)


def _is_round_trippable_overview_value(value: Any) -> bool:
    """Return whether *value* is safe to preserve under an unknown key."""

    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_round_trippable_overview_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_round_trippable_overview_value(item)
            for key, item in value.items()
        )
    return False


def validate_explore_overview(value: Any, *, context: str) -> dict[str, Any]:
    """Return a validated Explore overview dict.

    Known overview cards are boolean toggles. Unknown keys are preserved so a
    newer UI can round-trip through an older parser, but keys must be strings
    and the top-level value must be a dict. Empty dicts remain empty so callers
    can decide whether to omit the config entirely.
    """

    if not isinstance(value, dict):
        raise ConfigError(
            "Explore overview config must be a dict.",
            context=context,
            actual_type=type(value).__name__,
        )

    overview = dict(value)
    for key, item in overview.items():
        if not isinstance(key, str):
            raise ConfigError(
                "Explore overview config keys must be strings.",
                context=context,
                key=repr(key),
                key_type=type(key).__name__,
            )
        if key in EXPLORE_OVERVIEW_TOGGLE_KEYS and not isinstance(item, bool):
            raise ConfigError(
                "Explore known overview key toggle values must be booleans.",
                context=context,
                key=key,
                actual_type=type(item).__name__,
            )
        if key not in EXPLORE_OVERVIEW_TOGGLE_KEYS and not _is_round_trippable_overview_value(
            item
        ):
            raise ConfigError(
                "Explore unknown overview values must be simple literals "
                "that can round-trip through codegen.",
                context=context,
                key=key,
                actual_type=type(item).__name__,
            )
    return overview
