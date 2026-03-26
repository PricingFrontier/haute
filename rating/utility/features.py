"""Feature engineering helpers for insurance quote processing.

Provides utilities for:
- Date parsing and age calculation from nested JSON fields
- Additional driver feature extraction (ages, licence years, categorical fields)
- Add-on feature extraction (selected flags, counts)
- Column rename mappings (dot-notation → clean snake_case)
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

# ── Date helpers ──────────────────────────────────────────────────────


def to_date(col_name: str, fmt: str = "%Y-%m-%d") -> pl.Expr:
    """Parse a string column to a date."""
    return pl.col(col_name).str.to_date(fmt)


def years_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Whole years between two date expressions (floor)."""
    return ((later - earlier).dt.total_days() / 365.25).floor().cast(pl.Int64)


def months_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Calendar months between two date expressions."""
    return (later.dt.year() - earlier.dt.year()) * 12 + (later.dt.month() - earlier.dt.month())


def days_between(earlier: pl.Expr, later: pl.Expr) -> pl.Expr:
    """Days between two date expressions."""
    return (later - earlier).dt.total_days()


# ── String helpers ────────────────────────────────────────────────────


def postcode_area(col_name: str) -> pl.Expr:
    """Extract the outward code (first part) from a UK postcode."""
    return pl.col(col_name).str.split(" ").list.first()


# ── Column cleaning ──────────────────────────────────────────────────


def clean_columns(df: pl.LazyFrame) -> pl.LazyFrame:
    """Replace every ``.`` with ``_`` in column names.

    Example::
        df = clean_columns(quotes)
    """
    rename = {c: c.replace(".", "_") for c in df.collect_schema().names() if "." in c}
    return df.rename(rename) if rename else df


# ── Column matching ──────────────────────────────────────────────────


def cols_matching(all_cols: list[str], pattern_fn: Callable[[str], bool]) -> list[str]:
    """Return columns from *all_cols* where pattern_fn(col) is True."""
    return [c for c in all_cols if pattern_fn(c)]
