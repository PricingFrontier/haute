"""Shared categorical encoding for the native-dataset model families.

XGBoost reads a pandas categorical's *codes*, not its labels, so a scoring
frame whose categories are listed in another order silently scores the wrong
level. Every new-family adapter therefore builds its categorical columns from
one stored level list: the non-null levels, in stored order, are the pandas
categories; a null becomes the engine's native missing value (code ``-1``),
never a category; and a literal string such as ``"__missing__"`` is an
ordinary level. A value outside the levels fails loudly instead of being
scored as missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl

from haute.errors import HauteValidationError

CategoricalLevels = Mapping[str, Sequence[str | None]]


def fit_categorical_levels(
    frame: pl.DataFrame,
    cat_features: Sequence[str],
    declared: CategoricalLevels | None,
) -> dict[str, list[str | None]]:
    """The level list each categorical feature is encoded against.

    An authored domain is schema and wins; otherwise the levels are the
    distinct non-null values of the *training* frame, sorted, with ``None``
    appended when the training frame holds nulls.
    """
    levels: dict[str, list[str | None]] = {}
    for column in cat_features:
        if declared is not None and column in declared:
            levels[column] = list(declared[column])
            continue
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
    context: str,
) -> Any:
    """A pandas frame of *features* with categoricals encoded against *levels*.

    Raises ``HauteValidationError`` naming the feature and values when a
    categorical holds a value outside its levels.
    """
    import pandas as pd

    columns: dict[str, Any] = {}
    for name in features:
        series = frame.get_column(name)
        if name in levels:
            categories = [level for level in levels[name] if level is not None]
            values = series.cast(pl.String)
            unseen = sorted(
                {value for value in values.unique().to_list() if value is not None}
                - set(categories)
            )
            if unseen:
                shown = ", ".join(repr(value) for value in unseen[:5])
                more = f" and {len(unseen) - 5} more" if len(unseen) > 5 else ""
                raise HauteValidationError(
                    f"{context}: feature '{name}' has values the model was not trained on: "
                    f"{shown}{more}. Declare the categorical domain upstream or retrain with "
                    "these values."
                )
            columns[name] = pd.Categorical(values.to_list(), categories=categories)
        else:
            columns[name] = series.cast(pl.Float64).to_numpy()
    return pd.DataFrame(columns, columns=list(features))
