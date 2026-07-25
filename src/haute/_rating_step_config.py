"""Rating-step config normalisation and lossless sidecar helpers."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

_MAX_RATING_FACTORS = 3


def _table_context(index: int) -> str:
    return f"ratingStep tables[{index}]"


def _entries_context(index: int) -> str:
    return f"{_table_context(index)}.entries"


def _validate_factors(table: dict[str, Any], table_index: int) -> list[str]:
    factors = table.get("factors")
    if not isinstance(factors, list):
        raise ValueError(f"{_table_context(table_index)}.factors must be a list")
    result: list[str] = []
    for factor_index, factor in enumerate(factors):
        if not isinstance(factor, str) or not factor.strip():
            raise ValueError(
                f"{_table_context(table_index)}.factors[{factor_index}] must be a column name"
            )
        if factor in result:
            raise ValueError(
                f"{_table_context(table_index)}.factors contains duplicate column {factor!r}"
            )
        result.append(factor)
    if len(result) > _MAX_RATING_FACTORS:
        raise ValueError(
            f"{_table_context(table_index)}.factors supports at most {_MAX_RATING_FACTORS} columns"
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
        raise ValueError(f"{_table_context(table_index)}.factorDtypes must be an object")

    # Late import avoids the module cycle: _rating imports this codec.
    from haute._rating import is_rating_dtype_descriptor

    for factor, descriptor in raw.items():
        if not isinstance(factor, str) or factor not in factors:
            raise ValueError(
                f"{_table_context(table_index)}.factorDtypes key {factor!r} "
                "must name a selected factor"
            )
        if not is_rating_dtype_descriptor(descriptor):
            raise ValueError(
                f"{_table_context(table_index)}.factorDtypes[{factor!r}] "
                "is not a valid rating dtype descriptor"
            )


def _validate_factor_value(value: Any, context: str) -> None:
    """Accept precisely the scalar values that JSON can retain in row entries."""
    if value is None or not isinstance(value, str | int | float | bool):
        raise ValueError(f"{context} must be a non-null JSON scalar")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{context} must be finite")


def _validate_rating_value(value: Any, context: str) -> None:
    if value is None or value == "":
        raise ValueError(f"{context} requires value")
    if isinstance(value, bool):
        raise ValueError(f"{context} must be numeric")
    if isinstance(value, int | float):
        if not math.isfinite(float(value)):
            raise ValueError(f"{context} must be finite")
        return
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{context} requires value")
        try:
            numeric_value = float(value)
        except ValueError as exc:
            raise ValueError(f"{context} must be numeric") from exc
        if not math.isfinite(numeric_value):
            raise ValueError(f"{context} must be finite")
        return
    raise ValueError(f"{context} must be a JSON string or number")


def _entry_value(row: dict[Any, Any], output_column: str, context: str) -> Any:
    has_value = "value" in row
    has_output_value = bool(output_column) and output_column in row and output_column != "value"
    if has_value and has_output_value and row["value"] != row[output_column]:
        raise ValueError(f"{context} contains both 'value' and {output_column!r}")
    if has_value:
        return row["value"]
    if has_output_value:
        return row[output_column]
    raise ValueError(f"{context} requires value")


def _insert_entry_value(target: dict[str, Any], keys: list[str], value: Any, context: str) -> None:
    """Legacy map-writing helper retained for private compatibility tests.

    Canonical sidecar writes do not call this helper.
    """
    branch = target
    for key in keys[:-1]:
        existing = branch.get(key)
        if existing is None:
            next_branch: dict[str, Any] = {}
            branch[key] = next_branch
            branch = next_branch
        elif isinstance(existing, dict):
            branch = existing
        else:
            raise ValueError(f"{context} key {keys!r} conflicts with an existing rating value")
    leaf_key = keys[-1]
    if leaf_key in branch:
        raise ValueError(f"duplicate {context} key {keys!r}")
    _validate_rating_value(value, f"{context} key {leaf_key!r}")
    branch[leaf_key] = value


def _validate_entry_row(
    row: Any, factors: list[str], table: dict[str, Any], table_index: int, row_index: int
) -> dict[str, Any]:
    context = f"{_entries_context(table_index)}[{row_index}]"
    if not isinstance(row, dict):
        raise ValueError(f"{context} must be an object")
    for factor in factors:
        if factor not in row:
            raise ValueError(f"{context} requires factor {factor!r}")
        _validate_factor_value(row[factor], f"{context} factor {factor!r}")
    output_column = str(table.get("outputColumn", "") or "")
    value = _entry_value(row, output_column, context)
    _validate_rating_value(value, context)
    result = deepcopy(row)
    if output_column and output_column != "value":
        result.pop(output_column, None)
    result["value"] = deepcopy(value)
    return result


def _normalise_entry_rows(
    rows: list[Any], factors: list[str], table: dict[str, Any], table_index: int
) -> list[dict[str, Any]]:
    """Validate rows without changing their order, scalar identity, or metadata."""
    return [
        _validate_entry_row(row, factors, table, table_index, row_index)
        for row_index, row in enumerate(rows)
    ]


def _sidecar_entry_factor_order(factors: list[str]) -> list[str]:
    """Historical three-factor maps were stored in editor-axis order."""
    return [factors[2], factors[1], factors[0]] if len(factors) == 3 else list(factors)


def _legacy_sort_key(value: Any) -> tuple[str, str]:
    """Make legacy-map migration reproducible regardless of dict insertion order."""
    return (type(value).__name__, repr(value))


def _normalise_legacy_map_key(value: Any) -> Any:
    """Retain the old map lookup semantics without touching canonical rows."""
    if not isinstance(value, str):
        return value
    try:
        numeric = float(value)
    except ValueError:
        return value
    if math.isfinite(numeric) and numeric.is_integer():
        return str(int(numeric))
    return value


def _expand_entries_map(
    entries: dict[Any, Any], factors: list[str], table_index: int
) -> list[dict[str, Any]]:
    """Read the former nested-map representation into canonical rows.

    This is deliberately read-only compatibility code: writers never call it
    to create maps.  JSON object keys are normally strings, but accepting
    other scalar Python keys makes malformed in-process configs fail with a
    useful factor-level message instead of a sorting/type error.
    """
    context = _entries_context(table_index)
    if not factors:
        if entries:
            raise ValueError(f"{_table_context(table_index)}.factors must be a non-empty list")
        return []

    expanded: list[dict[str, Any]] = []
    sidecar_factors = _sidecar_entry_factor_order(factors)

    def walk(branch: dict[Any, Any], depth: int, keys: list[Any]) -> None:
        factor = sidecar_factors[depth]
        seen: dict[Any, Any] = {}
        for raw_key in sorted(branch, key=_legacy_sort_key):
            key = _normalise_legacy_map_key(raw_key)
            _validate_factor_value(key, f"{context} factor {factor!r}")
            if key in seen:
                raise ValueError(
                    f"{context} factor {factor!r} compact key {raw_key!r} collides with "
                    f"existing key {seen[key]!r} after legacy key migration to {key!r}"
                )
            seen[key] = raw_key
            value = branch[raw_key]
            next_keys = [*keys, deepcopy(key)]
            if depth == len(sidecar_factors) - 1:
                if isinstance(value, dict):
                    raise ValueError(
                        f"{context} must have rating values at depth {len(sidecar_factors)}"
                    )
                if isinstance(value, list):
                    raise ValueError(f"{context} rating values must be scalar")
                _validate_rating_value(value, f"{context} key {raw_key!r}")
                sidecar_values = dict(zip(sidecar_factors, next_keys, strict=True))
                expanded.append(
                    {
                        **{factor_name: sidecar_values[factor_name] for factor_name in factors},
                        "value": deepcopy(value),
                    }
                )
            else:
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{context} must be nested to match {len(sidecar_factors)} factors"
                    )
                walk(value, depth + 1, next_keys)

    walk(entries, 0, [])
    return expanded


def _normalise_entries_for_table(table: dict[str, Any], table_index: int) -> dict[str, Any]:
    result = deepcopy(table)
    if "entries" not in result:
        if "factors" in result or "factorDtypes" in result:
            factors = _validate_factors(result, table_index)
            _validate_factor_dtypes(result, factors, table_index)
        return result
    entries = result["entries"]
    if entries is None:
        raise ValueError(f"{_entries_context(table_index)} must be a list or object")
    factors = _validate_factors(result, table_index)
    _validate_factor_dtypes(result, factors, table_index)
    if not factors and entries:
        raise ValueError(f"{_table_context(table_index)}.factors must be a non-empty list")
    if isinstance(entries, dict):
        result["entries"] = _expand_entries_map(entries, factors, table_index)
    elif isinstance(entries, list):
        result["entries"] = _normalise_entry_rows(entries, factors, result, table_index)
    else:
        raise ValueError(f"{_entries_context(table_index)} must be a list or object")
    return result


def expand_rating_step_config_from_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Validate canonical rows and migrate legacy nested entry maps on read."""
    result = deepcopy(config)
    tables = result.get("tables")
    if tables is None:
        return result
    if not isinstance(tables, list):
        raise ValueError("ratingStep tables must be a list")
    result["tables"] = [
        _normalise_entries_for_table(table, table_index)
        if isinstance(table, dict)
        else _raise_table_not_object(table_index)
        for table_index, table in enumerate(tables)
    ]
    return result


def _raise_table_not_object(table_index: int) -> Any:
    raise ValueError(f"ratingStep tables[{table_index}] must be an object")


def normalise_rating_tables(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return rating-step tables in the canonical ordered row-array shape."""
    tables = expand_rating_step_config_from_sidecar(config).get("tables")
    return [] if tables is None else list(tables)


def _compact_entry_rows(
    rows: list[Any], factors: list[str], table_index: int, output_column: str
) -> list[dict[str, Any]]:
    """Compatibility helper whose output is now canonical rows, never a map."""
    return _normalise_entry_rows(rows, factors, {"outputColumn": output_column}, table_index)


def _compact_table_for_sidecar(table: dict[str, Any], table_index: int) -> dict[str, Any]:
    """Validate and emit the one canonical, lossless persisted entry form."""
    return _normalise_entries_for_table(table, table_index)


def compact_rating_step_config_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Validate rating tables and persist entries as ordered row arrays."""
    return expand_rating_step_config_from_sidecar(config)
