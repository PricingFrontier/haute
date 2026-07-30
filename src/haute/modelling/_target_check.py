"""Data-dependent target-column vs task gate for model training.

``training_target_task_issue`` mirrors ``training_objective_issue``
(`_train_config.py`): return an actionable, user-facing message — naming the
user-model objects involved (target column, task) and what to do next — or
``None`` when the pairing is valid. It is shared by the train route's
pre-dispatch validation (over the sunk training parquet) and
``TrainingJob._prepare_data`` (which also covers the CLI and exported-script
paths), so the two gates can never drift.

The motivating failure: a continuous target under ``task="classification"``
trained all the way to the metric stage and surfaced sklearn's bare
"ValueError: continuous format is not supported" — no target column, no task,
no fix.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl


def _has_fractional_values(
    data: pl.LazyFrame,
    target: str,
    collect: Callable[[pl.LazyFrame], pl.DataFrame],
) -> bool:
    """Return whether any finite, non-null target value has a fractional part."""
    column = pl.col(target)
    frame = collect(data.select((column.is_finite() & (column != column.floor())).any()))
    return bool(frame.item())


def training_target_task_issue(
    data: pl.LazyFrame,
    *,
    target: str,
    task: str,
    collect: Callable[[pl.LazyFrame], pl.DataFrame] | None = None,
) -> str | None:
    """Return an actionable message when the target's values cannot serve the task.

    A classification task needs a target holding discrete class labels:
    boolean, integer, string/categorical/enum, or a float column whose finite
    values are all integral (a materialised 0/1 flag). A float target with
    fractional values — or a target whose type cannot act as class labels at
    all — must gate here, with the target column and task named, rather than
    fall through to a library error stripped of that context.

    ``collect`` lets callers route the one boolean fractional-values scan
    through their own (streaming, execution-context-aware) collector; the
    default collects through Polars' streaming engine.
    """
    if str(task) != "classification":
        return None
    schema = data.collect_schema()
    if target not in schema:
        # A missing target column has its own gates (config validation and
        # TrainingJob._validate_columns); this check owns only the pairing.
        return None
    dtype = schema[target]
    base_type = dtype.base_type()
    if base_type == pl.Boolean or dtype.is_integer():
        return None
    if base_type in (pl.String, pl.Categorical, pl.Enum):
        return None
    if dtype.is_float():
        if collect is None:
            from haute._polars_utils import streaming_collect

            collect = streaming_collect
        if not _has_fractional_values(data, target, collect):
            return None
        return (
            f"Target column '{target}' contains continuous values, but the training "
            "task is classification. A classification target must hold discrete class "
            "labels (e.g. a 0/1 claim flag) — classification training and its metrics "
            "(AUC, log loss) are undefined on a continuous target. Choose a discrete "
            "target column, or set the task to regression to model a continuous target."
        )
    return (
        f"Target column '{target}' has type {dtype}, which cannot be used as "
        "classification class labels. Choose a target column with discrete class "
        "labels (e.g. a 0/1 claim flag or a category column), or set the task to "
        "regression."
    )
