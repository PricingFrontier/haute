"""Shared categorical encoding for the native-dataset model families.

XGBoost reads a pandas categorical's *codes*, not its labels, so a scoring
frame whose categories are listed in another order silently scores the wrong
level. Every new-family adapter therefore builds its categorical columns from
one stored level list: the non-null levels, in stored order, are the pandas
categories; a null becomes the engine's native missing value (code ``-1``),
never a category; and a literal string such as ``"__missing__"`` is an
ordinary level.

A value outside the levels never fails: one new value must not stop a
production scoring run. It reaches the library through the library's own
handling of a category it was not trained on (``unseen``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

import polars as pl

from haute.errors import HauteValidationError

CategoricalLevels = Mapping[str, Sequence[str | None]]
#: How a value outside the levels reaches the library: ``missing`` as the
#: native missing code (XGBoost refuses an unknown label, and LightGBM turns
#: one into a missing value itself), ``label`` as an extra category after the
#: levels (EBM scores an unknown label in its unknown bin, not its missing bin).
UnseenEncoding = Literal["missing", "label"]


def fit_categorical_levels(
    frame: pl.DataFrame,
    cat_features: Sequence[str],
) -> dict[str, list[str | None]]:
    """The level list each categorical feature is encoded against.

    The distinct non-null values of the *training* frame, sorted, with ``None``
    appended when the training frame holds nulls. The model file records them.
    """
    levels: dict[str, list[str | None]] = {}
    for column in cat_features:
        series = frame.get_column(column).cast(pl.String)
        values = sorted(value for value in series.unique().to_list() if value is not None)
        if "" in values:
            raise HauteValidationError(
                f"Categorical feature '{column}' has empty-string values, which a categorical "
                "domain cannot hold. Replace them upstream, for example with null."
            )
        levels[column] = [*values, *([None] if series.null_count() else [])]
    return levels


def encode_frame(
    frame: pl.DataFrame,
    features: Sequence[str],
    levels: CategoricalLevels,
    *,
    unseen: UnseenEncoding,
) -> Any:
    """A pandas frame of *features* with categoricals encoded against *levels*.

    A value outside a feature's levels is encoded as *unseen* says; the
    fitted levels keep their codes either way.
    """
    import pandas as pd

    columns: dict[str, Any] = {}
    for name in features:
        series = frame.get_column(name)
        if name in levels:
            categories = [level for level in levels[name] if level is not None]
            values = series.cast(pl.String)
            if unseen == "label":
                known = set(categories)
                categories = categories + sorted(
                    value
                    for value in values.unique().to_list()
                    if value is not None and value not in known
                )
            columns[name] = pd.Categorical(values.to_list(), categories=categories)
        else:
            columns[name] = series.cast(pl.Float64).to_numpy()
    return pd.DataFrame(columns, columns=list(features))


def native_categories(
    levels: Mapping[str, Sequence[Any]],
) -> tuple[dict[str, list[str | None]], dict[str, list[Any]]]:
    """Recorded category values as the string levels Haute encodes, and the originals.

    A library trained outside Haute can record non-string categories (integer
    vehicle groups); Haute reads every categorical input as text, so it encodes
    against their string forms and :func:`restore_native_categories` puts the
    library's own values back. Only features whose values differ are returned.
    """
    strings: dict[str, list[str | None]] = {}
    native: dict[str, list[Any]] = {}
    for name, values in levels.items():
        strings[name] = [None if value is None else str(value) for value in values]
        if any(value is not None and not isinstance(value, str) for value in values):
            native[name] = list(values)
    return strings, native


def restore_native_categories(encoded: Any, native: Mapping[str, Sequence[Any]]) -> Any:
    """Rename encoded categoricals back to the values their library recorded, codes kept."""
    for name, values in native.items():
        encoded[name] = encoded[name].cat.rename_categories(
            [value for value in values if value is not None]
        )
    return encoded
