"""Train/validation/holdout split strategies for model training."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import polars as pl

from haute.errors import HauteValidationError


@dataclass
class SplitConfig:
    """Configuration for train/validation/holdout splitting.

    Both ``validation_size`` and ``holdout_size`` are optional (0 = disabled).
    """

    strategy: Literal["random", "temporal", "group"] = "random"
    validation_size: float = 0.2
    holdout_size: float = 0.0
    seed: int = 42
    date_column: str | None = None
    cutoff_date: str | None = None
    group_column: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.validation_size < 1:
            raise HauteValidationError(
                f"validation_size must be between 0 and 1, got {self.validation_size}"
            )
        if not 0 <= self.holdout_size < 1:
            raise HauteValidationError(
                f"holdout_size must be between 0 and 1, got {self.holdout_size}"
            )
        if self.validation_size + self.holdout_size >= 1:
            raise HauteValidationError(
                f"validation_size ({self.validation_size}) + holdout_size ({self.holdout_size}) "
                f"must be less than 1"
            )
        if self.strategy == "temporal":
            if not self.date_column:
                raise HauteValidationError("date_column is required for temporal split")
            if not self.cutoff_date:
                raise HauteValidationError("cutoff_date is required for temporal split")
        if self.strategy == "group" and not self.group_column:
            raise HauteValidationError("group_column is required for group split")


DEFAULT_SPLIT_DICT: dict[str, Any] = {
    "strategy": "random",
    "validation_size": 0.2,
    "seed": 42,
}


# Partition constants
PARTITION_TRAIN = 0
PARTITION_VALIDATION = 1
PARTITION_HOLDOUT = 2
# Cross-validation temporal plans leave future rows outside the current
# expanding-window fold.  They must not be labelled as holdout: diagnostics
# deliberately prefer holdout and would then inspect the future.
PARTITION_UNUSED = 3


def needs_row_order(config: SplitConfig) -> bool:
    """Whether *config*'s mask selects rows by position, and so takes the canonical order.

    A random mask selects positions; a temporal mask does too when a holdout takes the
    most recent rows, at the boundary date the holdout ends inside. A temporal mask
    without a holdout, and a group mask, decide each row by its own values.
    """
    return config.strategy == "random" or (
        config.strategy == "temporal" and config.holdout_size > 0
    )


def split_mask(
    n_rows: int,
    config: SplitConfig,
    df: pl.DataFrame | None = None,
    *,
    row_order: np.ndarray | None = None,
) -> pl.Series:
    """Return an Int8 Series with partition labels.

    Values: 0 = train, 1 = validation, 2 = holdout.
    When ``validation_size`` and/or ``holdout_size`` are 0, the
    corresponding partition is simply absent (all rows are train).

    ``df`` is only required for ``temporal`` and ``group`` strategies
    (which inspect column values). A strategy that selects by position
    (:func:`needs_row_order`) requires *row_order*, the rows' source positions
    in canonical order (``haute.modelling._row_order``): the mask is drawn over
    that order and each label returned to its row's source position, so the
    same rows get the same partitions whatever order they arrive in.
    """
    if n_rows == 0:
        raise HauteValidationError("Cannot split an empty DataFrame")
    if not needs_row_order(config):
        if row_order is not None:
            raise HauteValidationError(
                f"A {config.strategy} split decides each row by its values and needs no row order"
            )
    elif row_order is None:
        raise HauteValidationError(
            f"A {config.strategy} split selects rows by position and needs the canonical row order"
        )
    elif len(row_order) != n_rows:
        raise HauteValidationError(
            f"The canonical row order holds {len(row_order)} rows; the split has {n_rows}"
        )

    if config.strategy == "random":
        assert row_order is not None  # checked above
        canonical = _random_mask(n_rows, config.validation_size, config.holdout_size, config.seed)
        return _in_source_order(canonical, row_order)
    elif config.strategy == "temporal":
        if df is None or config.date_column is None or config.cutoff_date is None:
            raise HauteValidationError("Temporal split requires df, date_column, and cutoff_date")
        if row_order is None:
            return _temporal_mask(
                df,
                config.date_column,
                config.cutoff_date,
                config.validation_size,
                config.holdout_size,
            )
        canonical = _temporal_mask(
            df[row_order],
            config.date_column,
            config.cutoff_date,
            config.validation_size,
            config.holdout_size,
        )
        return _in_source_order(canonical, row_order)
    elif config.strategy == "group":
        if df is None or config.group_column is None:
            raise HauteValidationError("Group split requires df and group_column")
        return _group_mask(
            df,
            config.group_column,
            config.validation_size,
            config.holdout_size,
            config.seed,
        )
    else:
        raise HauteValidationError(f"Unknown split strategy: {config.strategy}")


def _require_no_null_dates(df: pl.DataFrame, date_column: str) -> None:
    """Fail loud when the temporal date column contains nulls.

    A null date cannot be ordered against the cutoff, so there is no
    correct partition for the row. The previous behaviour silently routed
    null-date rows into validation, which biases the split because nulls
    cluster. The explicit
    policy is a contract error naming the column and the null count;
    callers must filter or impute null dates upstream.
    """
    null_count = df[date_column].null_count()
    if null_count:
        raise HauteValidationError(
            f"Temporal split: date column '{date_column}' contains "
            f"{null_count} null value(s) out of {len(df)} row(s). "
            "Every row needs a non-null date to be assigned a partition — "
            "filter out or impute null dates upstream before training."
        )


# ---------------------------------------------------------------------------
# Mask-only helpers (return Int8 Series: 0=train, 1=validation, 2=holdout)
# ---------------------------------------------------------------------------


def _in_source_order(canonical: pl.Series, row_order: np.ndarray) -> pl.Series:
    """Move labels drawn over the canonical order back to their rows' source positions."""
    labels = np.empty(len(canonical), dtype=np.int8)
    labels[row_order] = canonical.to_numpy()
    return pl.Series("_partition", labels)


def _random_mask(
    n_rows: int,
    validation_size: float,
    holdout_size: float,
    seed: int,
) -> pl.Series:
    """Int8 partition mask via random shuffle."""
    rng = np.random.default_rng(seed)
    indices = rng.permutation(n_rows)

    n_holdout = int(n_rows * holdout_size)
    n_validation = int(n_rows * validation_size)

    partition = np.zeros(n_rows, dtype=np.int8)
    # Holdout gets the last slice of shuffled indices
    if n_holdout > 0:
        partition[indices[-n_holdout:]] = PARTITION_HOLDOUT
    # Validation gets the slice before holdout
    if n_validation > 0:
        start = n_rows - n_holdout - n_validation
        partition[indices[start : n_rows - n_holdout]] = PARTITION_VALIDATION
    return pl.Series("_partition", partition)


def _temporal_mask(
    df: pl.DataFrame,
    date_column: str,
    cutoff_date: str,
    validation_size: float,
    holdout_size: float,
) -> pl.Series:
    """Int8 partition mask by date ordering.

    For temporal splits, holdout = most recent data, validation = next most
    recent, train = oldest.  The cutoff_date separates train from the rest;
    validation and holdout are then split proportionally within the post-cutoff
    data. Rows of the date the holdout ends inside keep their order in *df*
    (a stable sort), which the caller makes the canonical order.

    Raises ``ValueError`` if the date column contains nulls — see
    :func:`_require_no_null_dates`.
    """
    if date_column not in df.columns:
        raise HauteValidationError(f"Date column '{date_column}' not found in DataFrame")
    _require_no_null_dates(df, date_column)
    cutoff = pl.lit(cutoff_date).str.to_date()
    date_col = pl.col(date_column)
    if df[date_column].dtype == pl.Utf8:
        date_col = date_col.str.to_date()

    is_train = df.select((date_col < cutoff).alias("_t"))["_t"]
    n_total = len(df)
    n_train = int(is_train.sum())
    n_non_train = n_total - n_train

    partition = np.where(is_train.to_numpy(), PARTITION_TRAIN, PARTITION_VALIDATION).astype(np.int8)

    # Split the non-train portion into validation and holdout
    if holdout_size > 0 and n_non_train > 0:
        total = validation_size + holdout_size
        holdout_frac = holdout_size / total if total > 0 else 1.0
        # Sort non-train rows by date — holdout = most recent
        non_train_indices = np.where(~is_train.to_numpy())[0]
        # Get date values for sorting
        dates = df[date_column]
        if dates.dtype == pl.Utf8:
            dates = dates.str.to_date()
        non_train_dates = dates.gather(non_train_indices.tolist())
        sort_order = np.argsort(non_train_dates.to_numpy(), kind="stable")
        sorted_non_train = non_train_indices[sort_order]
        n_holdout = int(len(sorted_non_train) * holdout_frac)
        if n_holdout > 0:
            partition[sorted_non_train[-n_holdout:]] = PARTITION_HOLDOUT

    # If no validation desired, reclassify validation rows as train
    if validation_size == 0:
        partition[partition == PARTITION_VALIDATION] = PARTITION_TRAIN

    return pl.Series("_partition", partition)


def _group_mask(
    df: pl.DataFrame,
    group_column: str,
    validation_size: float,
    holdout_size: float,
    seed: int,
) -> pl.Series:
    """Int8 partition mask by group hashing."""
    if group_column not in df.columns:
        raise HauteValidationError(f"Group column '{group_column}' not found in DataFrame")

    unique_groups = sorted(str(v) for v in df[group_column].unique().to_list())
    total_frac = validation_size + holdout_size

    # Assign each group to a partition via deterministic hashing
    group_partition: dict[str, int] = {}
    for g in unique_groups:
        h = hashlib.md5(f"{seed}:{g}".encode()).hexdigest()
        frac = int(h[:8], 16) / 0x100000000
        if holdout_size > 0 and frac < holdout_size:
            group_partition[g] = PARTITION_HOLDOUT
        elif total_frac > 0 and frac < total_frac:
            group_partition[g] = PARTITION_VALIDATION
        else:
            group_partition[g] = PARTITION_TRAIN

    # Ensure at least one group in validation if requested and no groups assigned
    no_validation = PARTITION_VALIDATION not in group_partition.values()
    if validation_size > 0 and no_validation and len(unique_groups) > 1:
        # Pick the first train group
        for g in unique_groups:
            if group_partition[g] == PARTITION_TRAIN:
                group_partition[g] = PARTITION_VALIDATION
                break

    # Map groups to partition labels
    group_col_str = df[group_column].cast(pl.Utf8)
    partition = np.array(
        [group_partition.get(str(v), PARTITION_TRAIN) for v in group_col_str.to_list()],
        dtype=np.int8,
    )
    return pl.Series("_partition", partition)
