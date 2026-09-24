"""Shared validation for Explore overview configuration."""

from __future__ import annotations

from typing import Any

from haute.errors import ConfigError

EXPLORE_OVERVIEW_TOGGLE_KEYS: frozenset[str] = frozenset(
    {
        "dataset_snapshot",
        "data_quality",
        "numeric_summary",
        "categorical_summary",
        "schema",
    }
)


def validate_explore_overview(value: Any, *, context: str) -> dict[str, Any]:
    """Return a validated Explore overview dict.

    The overview maps each known card to a boolean toggle. Any other key is
    rejected by name: the canonical format has no forward-compatibility
    passthrough. Empty dicts remain empty so callers can decide whether to
    omit the config entirely.
    """

    if not isinstance(value, dict):
        raise ConfigError(
            "Explore overview config must be a dict.",
            context=context,
            actual_type=type(value).__name__,
        )

    overview: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConfigError(
                "Explore overview config keys must be strings.",
                context=context,
                key=repr(key),
                key_type=type(key).__name__,
            )
        if key not in EXPLORE_OVERVIEW_TOGGLE_KEYS:
            raise ConfigError(
                f"Explore overview has no card {key!r}; the cards are "
                f"{', '.join(sorted(EXPLORE_OVERVIEW_TOGGLE_KEYS))}.",
                context=context,
                key=key,
            )
        if not isinstance(item, bool):
            raise ConfigError(
                "Explore known overview key toggle values must be booleans.",
                context=context,
                key=key,
                actual_type=type(item).__name__,
            )
        overview[key] = item
    return overview
