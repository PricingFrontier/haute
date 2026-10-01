"""Rating-step config normalisation and lossless sidecar helpers."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from haute._validation_error import ConfigSettingError

_MAX_RATING_FACTORS = 3


def _table_context(index: int) -> str:
    return f"ratingStep tables[{index}]"


def _entries_context(index: int) -> str:
    return f"{_table_context(index)}.entries"


def _validate_factors(table: dict[str, Any], table_index: int) -> list[str]:
    factors = table.get("factors")
    if not isinstance(factors, list):
        raise ConfigSettingError(
            f"{_table_context(table_index)}.factors must be a list", setting="tables"
        )
    result: list[str] = []
    for factor_index, factor in enumerate(factors):
        if not isinstance(factor, str) or not factor.strip():
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factors[{factor_index}] must be a column name",
                setting="tables",
            )
        if factor in result:
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factors contains duplicate column {factor!r}",
                setting="tables",
            )
        result.append(factor)
    if len(result) > _MAX_RATING_FACTORS:
        raise ConfigSettingError(
            f"{_table_context(table_index)}.factors supports at most {_MAX_RATING_FACTORS} columns",
            setting="tables",
        )
    return result


def _validate_factor_dtypes(
    table: dict[str, Any],
    factors: list[str],
    table_index: int,
) -> None:
    """Validate optional backend-authored dtype metadata without inventing it."""
    if "factorDtypes" not in table:
        return
    raw = table["factorDtypes"]
    if not isinstance(raw, dict):
        raise ConfigSettingError(
            f"{_table_context(table_index)}.factorDtypes must be an object", setting="tables"
        )

    # Late import avoids the module cycle: _rating imports this codec.
    from haute._rating import is_rating_dtype_descriptor

    for factor, descriptor in raw.items():
        if not isinstance(factor, str) or factor not in factors:
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factorDtypes key {factor!r} "
                "must name a selected factor",
                setting="tables",
            )
        if not is_rating_dtype_descriptor(descriptor):
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factorDtypes[{factor!r}] "
                "is not a valid rating dtype descriptor",
                setting="tables",
            )


def _validate_factor_value(value: Any, context: str) -> None:
    """Accept precisely the scalar values that JSON can retain in row entries."""
    if value is None or not isinstance(value, str | int | float | bool):
        raise ConfigSettingError(f"{context} must be a non-null JSON scalar", setting="tables")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigSettingError(f"{context} must be finite", setting="tables")


def _validate_rating_value(value: Any, context: str) -> None:
    if value is None or value == "":
        raise ConfigSettingError(f"{context} requires value", setting="tables")
    if isinstance(value, bool):
        raise ConfigSettingError(f"{context} must be numeric", setting="tables")
    if isinstance(value, int | float):
        if not math.isfinite(float(value)):
            raise ConfigSettingError(f"{context} must be finite", setting="tables")
        return
    if isinstance(value, str):
        if not value.strip():
            raise ConfigSettingError(f"{context} requires value", setting="tables")
        try:
            numeric_value = float(value)
        except ValueError as exc:
            raise ConfigSettingError(f"{context} must be numeric", setting="tables") from exc
        if not math.isfinite(numeric_value):
            raise ConfigSettingError(f"{context} must be finite", setting="tables")
        return
    raise ConfigSettingError(f"{context} must be a JSON string or number", setting="tables")


def _validate_entry_row(
    row: Any, factors: list[str], table_index: int, row_index: int
) -> dict[str, Any]:
    context = f"{_entries_context(table_index)}[{row_index}]"
    if not isinstance(row, dict):
        raise ConfigSettingError(f"{context} must be an object", setting="tables")
    for factor in factors:
        if factor not in row:
            raise ConfigSettingError(
                f"{context} requires factor {factor!r}",
                setting="tables",
                fix=(
                    "A rating row holds each factor's value under the factor's name: "
                    f'{{"{factor}": <value>, "value": <number>}}, not factor_values.'
                ),
            )
        _validate_factor_value(row[factor], f"{context} factor {factor!r}")
    if "value" not in row:
        raise ConfigSettingError(f"{context} requires value", setting="tables")
    value = row["value"]
    _validate_rating_value(value, context)
    result = deepcopy(row)
    result["value"] = deepcopy(value)
    return result


def _normalise_entry_rows(
    rows: list[Any], factors: list[str], table_index: int
) -> list[dict[str, Any]]:
    """Validate rows without changing their order, scalar identity, or metadata."""
    return [
        _validate_entry_row(row, factors, table_index, row_index)
        for row_index, row in enumerate(rows)
    ]


def _normalise_entries_for_table(table: dict[str, Any], table_index: int) -> dict[str, Any]:
    result = deepcopy(table)
    if "entries" not in result:
        if "factors" in result or "factorDtypes" in result:
            factors = _validate_factors(result, table_index)
            _validate_factor_dtypes(result, factors, table_index)
        return result
    entries = result["entries"]
    if not isinstance(entries, list):
        raise ConfigSettingError(
            f"{_entries_context(table_index)} must be a list of row objects", setting="tables"
        )
    factors = _validate_factors(result, table_index)
    _validate_factor_dtypes(result, factors, table_index)
    if not factors and entries:
        raise ConfigSettingError(
            f"{_table_context(table_index)}.factors must be a non-empty list", setting="tables"
        )
    result["entries"] = _normalise_entry_rows(entries, factors, table_index)
    return result


def validate_unique_rating_table_outputs(tables: list[dict[str, Any]]) -> None:
    """Reject table outputs that would overwrite and then be combined twice."""
    first_index_by_output: dict[str, int] = {}
    for table_index, table in enumerate(tables):
        output_column = str(table.get("outputColumn", "") or "").strip()
        if not output_column:
            continue
        first_index = first_index_by_output.get(output_column)
        if first_index is not None:
            raise ConfigSettingError(
                f"ratingStep tables[{table_index}].outputColumn {output_column!r} "
                f"duplicates ratingStep tables[{first_index}].outputColumn",
                setting="tables",
            )
        first_index_by_output[output_column] = table_index


def normalise_rating_step_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate rating-step config in its canonical row-array form."""
    result = deepcopy(config)
    tables = result.get("tables")
    if tables is None:
        return result
    if not isinstance(tables, list):
        raise ConfigSettingError("ratingStep tables must be a list", setting="tables")
    normalised_tables = [
        _normalise_entries_for_table(table, table_index)
        if isinstance(table, dict)
        else _raise_table_not_object(table_index)
        for table_index, table in enumerate(tables)
    ]
    validate_unique_rating_table_outputs(normalised_tables)
    result["tables"] = normalised_tables
    return result


def _raise_table_not_object(table_index: int) -> Any:
    raise ConfigSettingError(
        f"ratingStep tables[{table_index}] must be an object", setting="tables"
    )


def normalise_rating_tables(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return rating-step tables in the canonical ordered row-array shape."""
    tables = normalise_rating_step_config(config).get("tables")
    return [] if tables is None else list(tables)
