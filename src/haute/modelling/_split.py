"""Train/validation/holdout split strategies for model training."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

import polars as pl


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
            raise ValueError(f"validation_size must be between 0 and 1, got {self.validation_size}")
        if not 0 <= self.holdout_size < 1:
            raise ValueError(f"holdout_size must be between 0 and 1, got {self.holdout_size}")
        if self.validation_size + self.holdout_size >= 1:
            raise ValueError(
                f"validation_size ({self.validation_size}) + holdout_size ({self.holdout_size}) "
                f"must be less than 1"
            )
        if self.strategy == "temporal":
            if not self.date_column:
                raise ValueError("date_column is required for temporal split")
            if not self.cutoff_date:
                raise ValueError("cutoff_date is required for temporal split")
        if self.strategy == "group" and not self.group_column:
            raise ValueError("group_column is required for group split")


DEFAULT_SPLIT_DICT: dict[str, Any] = {
    "strategy": "random",
    "validation_size": 0.2,
    "seed": 42,
}


def split_data(
    df: pl.DataFrame,
    config: SplitConfig,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a DataFrame into train and test (validation) sets.

    Returns (train_df, test_df).  Does not handle holdout — use
    :func:`split_mask` for the full 3-way partition.
    """
    if len(df) == 0:
        raise ValueError("Cannot split an empty DataFrame")

    if config.strategy == "random":
        return _random_split(df, config.validation_size, config.seed)
    elif config.strategy == "temporal":
        assert config.date_column is not None
        assert config.cutoff_date is not None
        return _temporal_split(df, config.date_column, config.cutoff_date)
    elif config.strategy == "group":
        assert config.group_column is not None
        return _group_split(df, config.group_column, config.validation_size, config.seed)
    else:
        raise ValueError(f"Unknown split strategy: {config.strategy}")


# Partition constants
PARTITION_TRAIN = 0
PARTITION_VALIDATION = 1
PARTITION_HOLDOUT = 2
# Cross-validation temporal plans leave future rows outside the current
# expanding-window fold.  They must not be labelled as holdout: diagnostics
# deliberately prefer holdout and would then inspect the future.
PARTITION_UNUSED = 3


def split_mask(
    n_rows: int,
    config: SplitConfig,
    df: pl.DataFrame | None = None,
) -> pl.Series:
    """Return an Int8 Series with partition labels.

    Values: 0 = train, 1 = validation, 2 = holdout.
    When ``validation_size`` and/or ``holdout_size`` are 0, the
    corresponding partition is simply absent (all rows are train).

    ``df`` is only required for ``temporal`` and ``group`` strategies
    (which inspect column values).
    """
    if n_rows == 0:
        raise ValueError("Cannot split an empty DataFrame")

    if config.strategy == "random":
        return _random_mask(n_rows, config.validation_size, config.holdout_size, config.seed)
    elif config.strategy == "temporal":
        if df is None or config.date_column is None or config.cutoff_date is None:
            raise ValueError("Temporal split requires df, date_column, and cutoff_date")
        return _temporal_mask(
            df,
            config.date_column,
            config.cutoff_date,
            config.validation_size,
            config.holdout_size,
        )
    elif config.strategy == "group":
        if df is None or config.group_column is None:
            raise ValueError("Group split requires df and group_column")
        return _group_mask(
            df,
            config.group_column,
            config.validation_size,
            config.holdout_size,
            config.seed,
        )
    else:
        raise ValueError(f"Unknown split strategy: {config.strategy}")


def _random_split(
    df: pl.DataFrame,
    validation_size: float,
    seed: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split by random sampling with seed for reproducibility."""
    n = len(df)
    train_n = int(n * (1 - validation_size))

    # Add row index, sample train indices, derive test via anti-join
    indexed = df.with_row_index("__split_idx__")
    train = indexed.sample(n=train_n, seed=seed).sort("__split_idx__")
    test = indexed.join(train.select("__split_idx__"), on="__split_idx__", how="anti").sort(
        "__split_idx__"
    )
    return train.drop("__split_idx__"), test.drop("__split_idx__")


def _require_no_null_dates(df: pl.DataFrame, date_column: str) -> None:
    """Fail loud when the temporal date column contains nulls.

    A null date cannot be ordered against the cutoff, so there is no
    correct partition for the row. The previous behaviour silently routed
    null-date rows into validation (mask path) or dropped them (split
    path) — both bias the split because nulls cluster. The explicit
    policy is a contract error naming the column and the null count;
    callers must filter or impute null dates upstream.
    """
    null_count = df[date_column].null_count()
    if null_count:
        raise ValueError(
            f"Temporal split: date column '{date_column}' contains "
            f"{null_count} null value(s) out of {len(df)} row(s). "
            "Every row needs a non-null date to be assigned a partition — "
            "filter out or impute null dates upstream before training."
        )


def _temporal_split(
    df: pl.DataFrame,
    date_column: str,
    cutoff_date: str,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split by date column: train before cutoff, test on or after.

    Raises ``ValueError`` if the date column contains nulls — see
    :func:`_require_no_null_dates`.
    """
    if date_column not in df.columns:
        raise ValueError(f"Date column '{date_column}' not found in DataFrame")
    _require_no_null_dates(df, date_column)

    cutoff = pl.lit(cutoff_date).str.to_date()
    date_col = pl.col(date_column)

    # Cast to Date if needed for comparison
    if df[date_column].dtype == pl.Utf8:
        date_col = date_col.str.to_date()

    train = df.filter(date_col < cutoff)
    test = df.filter(date_col >= cutoff)
    return train, test


def _assign_group_split(
    unique_groups: list,
    validation_size: float,
    seed: int,
) -> set[str]:
    """Deterministically assign groups to the test set via MD5 hashing.

    Returns the set of group values (as strings) assigned to test.
    """
    test_groups: set[str] = set()
    for g in sorted(str(v) for v in unique_groups):
        h = hashlib.md5(f"{seed}:{g}".encode()).hexdigest()
        # Use first 8 hex chars as a fraction
        frac = int(h[:8], 16) / 0x100000000
        if frac < validation_size:
            test_groups.add(g)

    # If no groups assigned to test (small dataset), force at least one
    if not test_groups and len(unique_groups) > 1:
        test_groups.add(str(sorted(str(v) for v in unique_groups)[0]))

    return test_groups


def _group_split(
    df: pl.DataFrame,
    group_column: str,
    validation_size: float,
    seed: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split by hashing group values — all rows in a group go to the same set."""
    if group_column not in df.columns:
        raise ValueError(f"Group column '{group_column}' not found in DataFrame")

    unique_groups = df[group_column].unique().to_list()
    test_groups = _assign_group_split(unique_groups, validation_size, seed)

    is_test = pl.col(group_column).cast(pl.Utf8).is_in(list(test_groups))
    train = df.filter(~is_test)
    test = df.filter(is_test)
    return train, test


# ---------------------------------------------------------------------------
# Mask-only helpers (return Int8 Series: 0=train, 1=validation, 2=holdout)
# ---------------------------------------------------------------------------


def _random_mask(
    n_rows: int,
    validation_size: float,
    holdout_size: float,
    seed: int,
) -> pl.Series:
    """Int8 partition mask via random shuffle."""
    import numpy as np

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
    data.

    Raises ``ValueError`` if the date column contains nulls — see
    :func:`_require_no_null_dates`.
    """
    if date_column not in df.columns:
        raise ValueError(f"Date column '{date_column}' not found in DataFrame")
    _require_no_null_dates(df, date_column)
    cutoff = pl.lit(cutoff_date).str.to_date()
    date_col = pl.col(date_column)
    if df[date_column].dtype == pl.Utf8:
        date_col = date_col.str.to_date()

    is_train = df.select((date_col < cutoff).alias("_t"))["_t"]
    n_total = len(df)
    n_train = int(is_train.sum())
    n_non_train = n_total - n_train

    import numpy as np

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
        sort_order = non_train_dates.arg_sort().to_numpy()
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
        raise ValueError(f"Group column '{group_column}' not found in DataFrame")

    import numpy as np

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
