"""The installed Polars' public operation surface, as the operation registry sees it.

A surface is what one registry receiver names: expression methods (``expr``),
frame methods (``frame``, LazyFrame and DataFrame), ``pl`` functions
(``polars_function``) and each expression namespace's methods
(``namespace.<name>``). ``tests/polars_unregistered_operations.json`` records
the public names of each surface the registry deliberately leaves unregistered,
so a Polars upgrade that adds or removes one fails the live-surface test until
the registry or the list is reviewed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from haute._polars_operations import EXPRESSION_NAMESPACE_NAMES


def _callables(owner: object) -> frozenset[str]:
    return frozenset(
        name
        for name in dir(owner)
        if not name.startswith("_") and callable(getattr(owner, name, None))
    )


# Registered names that are methods of Polars' chain objects rather than of an
# expression or a frame: ``pl.when(...).then(...).otherwise(...)`` and
# ``frame.group_by(...).agg(...)``.
_CHAIN_RECEIVERS: Mapping[tuple[str, str | None, str], Any] = {
    ("expr", None, "then"): pl.when(True),
    ("expr", None, "otherwise"): pl.when(True).then(1),
    ("frame", None, "agg"): pl.LazyFrame({"a": [1]}).group_by("a"),
}


def operation_exists(receiver: str, namespace: str | None, name: str) -> bool:
    """Whether the installed Polars has the operation a registry key names."""
    chain = _CHAIN_RECEIVERS.get((receiver, namespace, name))
    if chain is not None:
        return hasattr(chain, name)
    if receiver == "frame":
        return hasattr(pl.LazyFrame, name) or hasattr(pl.DataFrame, name)
    if receiver == "expr":
        return hasattr(pl.Expr, name) and name not in EXPRESSION_NAMESPACE_NAMES
    if receiver == "namespace":
        return namespace in EXPRESSION_NAMESPACE_NAMES and hasattr(
            getattr(pl.col("x"), namespace), name
        )
    if receiver == "polars_function":
        return hasattr(pl, name)
    raise ValueError(f"unknown receiver {receiver!r}")


def public_operations() -> dict[str, frozenset[str]]:
    """Every public callable on each surface the registry classifies."""
    surfaces = {
        "expr": _callables(pl.Expr) - EXPRESSION_NAMESPACE_NAMES,
        "frame": _callables(pl.LazyFrame) | _callables(pl.DataFrame),
        "polars_function": frozenset(
            name for name in _callables(pl) if not isinstance(getattr(pl, name), type)
        ),
    }
    expression = pl.col("x")
    for namespace in EXPRESSION_NAMESPACE_NAMES:
        surfaces[f"namespace.{namespace}"] = _callables(getattr(expression, namespace))
    return surfaces


def registered_operations(
    operations: Mapping[tuple[str, str | None, str], object],
) -> dict[str, frozenset[str]]:
    """The registry's names grouped by surface."""
    grouped: dict[str, set[str]] = {}
    for receiver, namespace, name in operations:
        surface = f"namespace.{namespace}" if receiver == "namespace" else receiver
        grouped.setdefault(surface, set()).add(name)
    return {surface: frozenset(names) for surface, names in grouped.items()}
