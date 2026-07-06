"""Rating-step config normalization and sidecar serialization helpers."""

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
        result.append(factor)
    if len(result) > _MAX_RATING_FACTORS:
        raise ValueError(
            f"{_table_context(table_index)}.factors supports at most {_MAX_RATING_FACTORS} columns"
        )
    return result


def _sidecar_entry_factor_order(factors: list[str]) -> list[str]:
    if len(factors) == 3:
        return [factors[2], factors[1], factors[0]]
    return list(factors)


def _canonical_sidecar_key(value: Any) -> str:
    """Canonical map key for a sidecar factor level.

    Symmetric with the compact-read side (:func:`_normalise_compact_sidecar_key`
    is an alias of this function), so the compact<->expand round trip is
    stable and any genuine key collision fails loudly on BOTH sides rather
    than compacting cleanly and then being rejected on expand.

    * Numbers use the engine's canonical factor-key form via
      ``normalise_rating_key`` (float ``25.0`` -> ``"25"``).  A plain
      ``str()`` here would persist ``25.0`` as ``"25.0"``, which the rating
      join canonicalises to ``"25"`` — the table would stop matching after
      one save/load cycle.
    * A string label that spells an int-like float (``"25.0"``, ``"1e2"``)
      collapses to the same canonical form, because a compact JSON object key
      cannot preserve the distinction between the number ``25.0`` and the
      label ``"25.0"``.  Leaving them distinct here produces a map the expand
      side would reject (write/read asymmetry).
    * All other string labels are kept verbatim.

    Late import: ``_rating`` imports this module at top level, so the shared
    helper is resolved at call time to keep the import graph acyclic.
    """
    from haute._rating import normalise_rating_key

    if isinstance(value, str):
        stripped = value.strip()
        if stripped and any(marker in stripped for marker in ".eE"):
            try:
                numeric_key = float(stripped)
            except ValueError:
                return value
            if math.isfinite(numeric_key):
                collapsed = normalise_rating_key(numeric_key)
                if collapsed is not None:
                    return collapsed
        return value

    key = normalise_rating_key(value)
    if key is None:
        raise ValueError("rating entry factor values must not be null")
    return key


def _normalise_compact_sidecar_key(raw_key: Any) -> str:
    """Canonical key for a compact sidecar map level (expand-read side).

    Identical to :func:`_canonical_sidecar_key`; kept as a named alias for the
    read path so both sides of the compact<->expand round trip are provably
    symmetric.  Compact JSON object keys are always strings, so a legacy save
    that wrote a float factor value with ``str()`` (``25.0``) is migrated to
    the canonical lookup key here just as the write side now produces it.
    """
    return _canonical_sidecar_key(raw_key)


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


def _insert_entry_value(
    target: dict[str, Any],
    keys: list[str],
    value: Any,
    context: str,
) -> None:
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


def _value_keys(output_column: str) -> set[str]:
    keys = {"value"}
    if output_column and output_column != "value":
        keys.add(output_column)
    return keys


def _normalise_entry_rows(
    rows: list[Any],
    factors: list[str],
    table: dict[str, Any],
    table_index: int,
) -> list[dict[str, Any]]:
    context = _entries_context(table_index)
    output_column = str(table.get("outputColumn", "") or "")
    canonical: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{context}[{row_index}] must be an object")
        value = _entry_value(row, output_column, f"{context}[{row_index}]")
        for factor in factors:
            if factor not in row or row[factor] is None:
                raise ValueError(f"{context}[{row_index}] requires factor {factor!r}")
        canonical.append(
            {
                **{factor: row[factor] for factor in factors},
                "value": value,
            }
        )
    return canonical


def _compact_entry_rows(
    rows: list[Any],
    factors: list[str],
    table_index: int,
    output_column: str,
) -> dict[str, Any]:
    context = _entries_context(table_index)
    if not factors:
        if rows:
            raise ValueError(f"{_table_context(table_index)}.factors must be a non-empty list")
        return {}

    compact: dict[str, Any] = {}
    allowed_keys = {*factors, *_value_keys(output_column)}
    sidecar_factors = _sidecar_entry_factor_order(factors)
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{context}[{row_index}] must be an object")
        unsupported_keys = sorted(str(key) for key in row if key not in allowed_keys)
        if unsupported_keys:
            raise ValueError(
                f"{context}[{row_index}] contains unsupported keys {unsupported_keys!r}"
            )
        keys: list[str] = []
        for factor in factors:
            if factor not in row or row[factor] is None:
                raise ValueError(f"{context}[{row_index}] requires factor {factor!r}")
        for factor in sidecar_factors:
            keys.append(_canonical_sidecar_key(row[factor]))
        value = _entry_value(row, output_column, f"{context}[{row_index}]")
        _insert_entry_value(compact, keys, value, context)
    return compact


def _expand_entries_map(
    entries: dict[Any, Any],
    factors: list[str],
    table_index: int,
) -> list[dict[str, Any]]:
    context = _entries_context(table_index)
    if not factors:
        if entries:
            raise ValueError(f"{_table_context(table_index)}.factors must be a non-empty list")
        return []

    expanded: list[dict[str, Any]] = []
    sidecar_factors = _sidecar_entry_factor_order(factors)

    def walk(branch: dict[Any, Any], depth: int, keys: list[str]) -> None:
        seen: dict[str, Any] = {}
        factor = sidecar_factors[depth]
        for raw_key, value in branch.items():
            key = _normalise_compact_sidecar_key(raw_key)
            if key in seen:
                raise ValueError(
                    f"{context} factor {factor!r} compact key {str(raw_key)!r} "
                    f"collides with existing key {str(seen[key])!r} after legacy "
                    f"key migration to {key!r}"
                )
            seen[key] = raw_key
            next_keys = [*keys, key]
            at_leaf = depth == len(sidecar_factors) - 1
            if at_leaf:
                if isinstance(value, dict):
                    raise ValueError(
                        f"{context} must have rating values at depth {len(sidecar_factors)}"
                    )
                if isinstance(value, list):
                    raise ValueError(f"{context} rating values must be scalar")
                _validate_rating_value(value, f"{context} key {key!r}")
                sidecar_values = dict(zip(sidecar_factors, next_keys))
                expanded.append(
                    {
                        **{factor: sidecar_values[factor] for factor in factors},
                        "value": value,
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


def _normalise_entries_for_table(
    table: dict[str, Any],
    table_index: int,
) -> dict[str, Any]:
    result = deepcopy(table)
    if "entries" not in result:
        return result

    entries = result["entries"]
    if entries is None:
        raise ValueError(f"{_entries_context(table_index)} must be a list or object")

    # Validate factor structural validity independently of whether entries is
    # empty, so an invalid factor list is never silently accepted just because
    # the table has no entries yet.
    factors = _validate_factors(result, table_index)
    if entries == []:
        result["entries"] = []
        return result

    if isinstance(entries, dict):
        result["entries"] = _expand_entries_map(entries, factors, table_index)
        return result
    if isinstance(entries, list):
        result["entries"] = _normalise_entry_rows(entries, factors, result, table_index)
        return result
    raise ValueError(f"{_entries_context(table_index)} must be a list or object")


def expand_rating_step_config_from_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Expand compact sidecar entry maps into canonical rating table rows."""
    result = deepcopy(config)
    tables = result.get("tables")
    if tables is None:
        return result
    if not isinstance(tables, list):
        raise ValueError("ratingStep tables must be a list")

    expanded: list[dict[str, Any]] = []
    for table_index, table in enumerate(tables):
        if not isinstance(table, dict):
            raise ValueError(f"ratingStep tables[{table_index}] must be an object")
        expanded.append(_normalise_entries_for_table(table, table_index))
    result["tables"] = expanded
    return result


def normalise_rating_tables(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return rating-step tables in the canonical row-array shape."""
    expanded_config = expand_rating_step_config_from_sidecar(config)
    tables = expanded_config.get("tables")
    if tables is None:
        return []
    # expand_rating_step_config_from_sidecar has already rejected a non-list
    # (non-None) ``tables`` and only ever stores a list, so ``tables`` here is
    # guaranteed to be a list.
    return list(tables)


def _compact_table_for_sidecar(table: dict[str, Any], table_index: int) -> dict[str, Any]:
    result = deepcopy(table)
    if "entries" not in result:
        return result

    entries = result["entries"]
    if entries is None:
        raise ValueError(f"{_entries_context(table_index)} must be a list or object")

    # Validate factor structural validity independently of whether entries is
    # empty (fail loud on an invalid factor list even for an empty table).
    factors = _validate_factors(result, table_index)
    if entries == []:
        result["entries"] = {} if factors else []
        return result

    if isinstance(entries, dict):
        rows = _expand_entries_map(entries, factors, table_index)
        output_column = str(result.get("outputColumn", "") or "")
        result["entries"] = _compact_entry_rows(rows, factors, table_index, output_column)
        return result
    if isinstance(entries, list):
        output_column = str(result.get("outputColumn", "") or "")
        result["entries"] = _compact_entry_rows(entries, factors, table_index, output_column)
        return result
    raise ValueError(f"{_entries_context(table_index)} must be a list or object")


def compact_rating_step_config_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Compact rating table rows into nested factor-value maps for JSON sidecars."""
    result = deepcopy(config)
    tables = result.get("tables")
    if tables is None:
        return result
    if not isinstance(tables, list):
        raise ValueError("ratingStep tables must be a list")

    compacted: list[dict[str, Any]] = []
    for table_index, table in enumerate(tables):
        if not isinstance(table, dict):
            raise ValueError(f"ratingStep tables[{table_index}] must be an object")
        compacted.append(_compact_table_for_sidecar(table, table_index))
    result["tables"] = compacted
    return result
