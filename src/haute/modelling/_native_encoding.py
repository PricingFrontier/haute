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
handling of a category it was not trained on (``unseen``), and the scorer
reports it.
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
    declared: CategoricalLevels | None,
) -> dict[str, list[str | None]]:
    """The level list each categorical feature is encoded against.

    An authored domain is schema and wins, and the *training* frame must not
    hold a value it omits; otherwise the levels are the distinct non-null
    values of the training frame, sorted, with ``None`` appended when the
    training frame holds nulls.
    """
    levels: dict[str, list[str | None]] = {}
    for column in cat_features:
        series = frame.get_column(column).cast(pl.String)
        values = sorted(value for value in series.unique().to_list() if value is not None)
        if declared is not None and column in declared:
            domain = list(declared[column])
            outside = sorted(set(values) - {level for level in domain if level is not None})
            if outside:
                shown = ", ".join(repr(value) for value in outside[:5])
                more = f" and {len(outside) - 5} more" if len(outside) > 5 else ""
                raise HauteValidationError(
                    f"Categorical feature '{column}' has training values outside its declared "
                    f"categorical_levels: {shown}{more}. Add them to the declaration or remove "
                    "those rows upstream."
                )
            levels[column] = domain
            continue
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
