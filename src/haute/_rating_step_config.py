"""Rating-step config normalisation and lossless sidecar helpers."""

from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from typing import Any

from haute._validation_error import ConfigSettingError

_MAX_RATING_FACTORS = 3
#: The key every rating row holds its rate under, beside its factors' levels, so no
#: factor may share it: the row would keep one of the two.
RATE_KEY = "value"


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
        if factor == RATE_KEY:
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factors[{factor_index}] {factor!r} is the key "
                "every rating row holds its rate under, so the factor's levels would be lost",
                setting="tables",
                fix=(
                    "Rename the column upstream, in a Transform node for example, and use the "
                    "renamed column as the factor."
                ),
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
    if "factor_dtypes" not in table:
        return
    raw = table["factor_dtypes"]
    if not isinstance(raw, dict):
        raise ConfigSettingError(
            f"{_table_context(table_index)}.factor_dtypes must be an object", setting="tables"
        )

    # Late import avoids the module cycle: _rating imports this codec.
    from haute._rating import is_rating_dtype_descriptor

    for factor, descriptor in raw.items():
        if not isinstance(factor, str) or factor not in factors:
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factor_dtypes key {factor!r} "
                "must name a selected factor",
                setting="tables",
            )
        if not is_rating_dtype_descriptor(descriptor):
            from haute._config_validation import camel_case_key_message

            camel_case = camel_case_key_message(
                f"{_table_context(table_index)}.factor_dtypes[{factor!r}]",
                list(descriptor) if isinstance(descriptor, dict) else [],
            )
            if camel_case is not None:
                raise ConfigSettingError(camel_case, setting="tables")
            raise ConfigSettingError(
                f"{_table_context(table_index)}.factor_dtypes[{factor!r}] "
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
        if "factors" in result or "factor_dtypes" in result:
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
    _refuse_levels_written_twice(result, factors)
    return result


def _refuse_levels_written_twice(table: dict[str, Any], factors: list[str]) -> None:
    """Refuse two entries whose levels are identical as written (READ-06)."""
    keys = [
        tuple(json.dumps(row[factor], sort_keys=True) for factor in factors)
        for row in table["entries"]
    ]
    counts = Counter(keys)
    if all(count == 1 for count in counts.values()):
        return
    from haute._rating import RatingDuplicateEntriesError  # cycle: _rating imports this codec

    raise RatingDuplicateEntriesError(
        table,
        [
            {factor: row[factor] for factor in factors}
            for row, key in zip(table["entries"], keys, strict=True)
            if counts[key] > 1
        ],
    )


def refuse_levels_equal_under_factor_dtypes(config: dict[str, Any]) -> None:
    """Refuse a table whose entries' levels match once converted to its ``factor_dtypes``.

    The save's check (READ-06): a table that records a type for each factor is
    held to the run's own conversion, so ``"25.0"`` and ``"25.00"`` for a
    numeric factor are refused before a run meets them. A table without
    ``factor_dtypes`` for every factor is checked when it runs, against the
    frame's types. Any other failure of the conversion is the run's to report.
    """
    import polars as pl

    from haute._rating import (  # cycle: _rating imports this codec
        RatingDuplicateEntriesError,
        rating_dtype_from_descriptor,
        rating_table_lookup,
    )
    from haute.errors import HauteError

    for table in config.get("tables") or []:
        dtypes = table.get("factor_dtypes") if isinstance(table, dict) else None
        factors = table.get("factors") if isinstance(table, dict) else None
        if not isinstance(dtypes, dict) or not isinstance(factors, list):
            continue
        if not factors or any(factor not in dtypes for factor in factors):
            continue
        try:
            schema = {factor: rating_dtype_from_descriptor(dtypes[factor]) for factor in factors}
            rating_table_lookup(table, schema)
        except RatingDuplicateEntriesError:
            raise
        except (ValueError, TypeError, HauteError, pl.exceptions.PolarsError):
            continue


# ---------------------------------------------------------------------------
# The sidecar's grid (READ-26)
# ---------------------------------------------------------------------------

#: The conversion a rating sidecar still in the row-array form needs.
GRID_CONVERSION = "scripts/one_off/read_26_rating_grid.py"


def _level_key(level: Any) -> str:
    """A level's identity as written: ``25`` and ``"25"`` stay distinct."""
    return json.dumps(level, sort_keys=True)


def grid_from_entries(table: dict[str, Any]) -> dict[str, Any]:
    """*table* as its sidecar writes it: ``rows`` (and ``columns``) in place of ``entries``.

    With one factor each row is a level and its rate. With more, ``columns`` lists the
    last factor's levels in the order the entries first give them, and each row holds one
    combination of the other factors, in that order, then one rate per column, ``null``
    where no entry is. A rate of 0 is a rate. Levels keep their JSON type.
    """
    # ``default_value`` is written as the editor holds it, text, which the engine reads.
    result = {key: value for key, value in table.items() if key != "entries"}
    entries = table.get("entries")
    factors = table.get("factors") or []
    if not isinstance(entries, list) or not factors:
        return result
    # A grid holds each entry's levels and rate alone: another key an entry holds would be
    # lost, so it is refused.
    known = {*factors, "value"}
    for entry in entries:
        extra = sorted(str(key) for key in entry if key not in known)
        if extra:
            raise ConfigSettingError(
                f"Rating table {table.get('name') or table.get('output_column')!r} has an entry "
                f"holding {extra[0]!r}, which a rating table's grid of levels and rates cannot "
                "keep: remove it from the entry.",
                setting="tables",
            )
    if len(factors) == 1:
        (factor,) = factors
        result["rows"] = [[entry[factor], entry["value"]] for entry in entries]
        return result
    *leading, last = factors
    columns: dict[str, Any] = {}
    rows: dict[str, list[Any]] = {}
    cells: dict[tuple[str, str], Any] = {}
    for entry in entries:
        column = _level_key(entry[last])
        columns.setdefault(column, entry[last])
        row = _level_key([entry[factor] for factor in leading])
        rows.setdefault(row, [entry[factor] for factor in leading])
        cells[(row, column)] = entry["value"]
    result["columns"] = list(columns.values())
    result["rows"] = [
        [*levels, *(cells.get((row, column)) for column in columns)] for row, levels in rows.items()
    ]
    return result


def entries_from_grid(table: dict[str, Any], table_index: int) -> dict[str, Any]:
    """*table* as its sidecar holds it, its grid read back into ``entries``: a ``null`` cell
    no entry. A table in the old row-array form is refused, naming the conversion."""
    context = _table_context(table_index)
    if "entries" in table:
        raise ConfigSettingError(
            f"{context} is in the row-array form rating tables had before they were written as "
            f"a grid. Convert the project's rating sidecars with {GRID_CONVERSION}.",
            setting="tables",
        )
    result = {key: value for key, value in table.items() if key not in ("columns", "rows")}
    rows = table.get("rows")
    if rows is None:
        if "columns" in table:
            raise ConfigSettingError(f"{context} has columns but no rows", setting="tables")
        return result
    factors = table.get("factors")
    if not isinstance(factors, list) or not factors:
        raise ConfigSettingError(f"{context}.factors must be a non-empty list", setting="tables")
    if not isinstance(rows, list):
        raise ConfigSettingError(f"{context}.rows must be a list", setting="tables")
    entries: list[dict[str, Any]] = []
    if len(factors) == 1:
        (factor,) = factors
        if "columns" in table:
            raise ConfigSettingError(
                f"{context} has one factor, so its rows are a level and a rate, with no columns",
                setting="tables",
            )
        for row_index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 2:
                raise ConfigSettingError(
                    f"{context}.rows[{row_index}] must be a level and its rate", setting="tables"
                )
            if row[1] is not None:
                entries.append({factor: row[0], RATE_KEY: row[1]})
        result["entries"] = entries
        return result
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise ConfigSettingError(
            f"{context}.columns must list the levels of its last factor", setting="tables"
        )
    *leading, last = factors
    width = len(leading) + len(columns)
    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != width:
            raise ConfigSettingError(
                f"{context}.rows[{row_index}] must hold {len(leading)} level(s) and "
                f"{len(columns)} rate(s)",
                setting="tables",
            )
        levels = dict(zip(leading, row[: len(leading)], strict=True))
        for column, rate in zip(columns, row[len(leading) :], strict=True):
            if rate is not None:
                entries.append({**levels, last: column, RATE_KEY: rate})
    result["entries"] = entries
    return result


def compact_rating_config_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """A validated rating-step config with each table written as its grid."""
    tables = config.get("tables")
    if not isinstance(tables, list):
        return config
    return {
        **config,
        "tables": [
            grid_from_entries(table) if isinstance(table, dict) else table for table in tables
        ],
    }


def expand_rating_config_from_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """A rating-step sidecar with each table's grid read back into its entries."""
    tables = config.get("tables")
    if not isinstance(tables, list):
        return config
    return {
        **config,
        "tables": [
            entries_from_grid(table, index) if isinstance(table, dict) else table
            for index, table in enumerate(tables)
        ],
    }


def validate_unique_rating_table_outputs(tables: list[dict[str, Any]]) -> None:
    """Reject table outputs that would overwrite and then be combined twice."""
    first_index_by_output: dict[str, int] = {}
    for table_index, table in enumerate(tables):
        output_column = str(table.get("output_column", "") or "").strip()
        if not output_column:
            continue
        first_index = first_index_by_output.get(output_column)
        if first_index is not None:
            raise ConfigSettingError(
                f"ratingStep tables[{table_index}].output_column {output_column!r} "
                f"duplicates ratingStep tables[{first_index}].output_column",
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
