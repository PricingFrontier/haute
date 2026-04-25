"""Schema inference and deterministic flattening for nested JSON objects."""

from __future__ import annotations

from typing import Any

from haute.errors import HauteError


class JsonFlattenSchemaError(HauteError):
    """Raised when a JSON flatten schema cannot produce unambiguous columns."""


_TYPE_ORDER = ("bool", "int", "float", "str")
_RESERVED_SCHEMA_KEYS = frozenset({"$max", "$items"})


def _format_key_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _validate_json_object_key(key: str, path: str = "") -> None:
    """Reject JSON object keys that cannot map to unique dotted columns."""
    if not isinstance(key, str):
        raise JsonFlattenSchemaError(
            "JSON object keys must be strings",
            path=path,
            key=key,
        )
    message = "Unsupported JSON object key for flattening"
    if not key:
        raise JsonFlattenSchemaError(message, path=path, key=key)
    if "." in key:
        raise JsonFlattenSchemaError(message, path=path, key=key)
    if key in _RESERVED_SCHEMA_KEYS:
        raise JsonFlattenSchemaError(message, path=path, key=key)
    if key.isdecimal():
        raise JsonFlattenSchemaError(message, path=path, key=key)


def _validate_schema_node(  # pragma: no mutate
    spec: dict[str, Any] | str,  # pragma: no mutate
    path: str,
) -> None:
    if isinstance(spec, str):
        return
    if not isinstance(spec, dict):
        raise JsonFlattenSchemaError(
            "Flatten schema nodes must be type strings, objects, or array specs",
            path=path,
            node=spec,
        )

    if _RESERVED_SCHEMA_KEYS.intersection(spec):
        if "$max" not in spec or "$items" not in spec or len(spec) != 2:
            raise JsonFlattenSchemaError(
                "Array schema nodes must contain only $max and $items",
                path=path,
                keys=sorted(spec),
            )
        max_items = spec["$max"]
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 0:
            raise JsonFlattenSchemaError(
                "Array schema $max must be a non-negative integer",
                path=path,
                max=max_items,
            )
        _validate_schema_node(spec["$items"], f"{path}[]" if path else "[]")
        return

    for key, child_spec in spec.items():
        _validate_json_object_key(key, path)
        _validate_schema_node(child_spec, _format_key_path(path, key))


def _validate_flatten_schema(schema: dict[str, Any]) -> None:
    for key, spec in schema.items():
        _validate_json_object_key(key)
        _validate_schema_node(spec, key)


def _assert_unique_column_names(columns: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for column in columns:
        if column in seen and column not in duplicates:
            duplicates.append(column)
        seen.add(column)
    if duplicates:
        raise JsonFlattenSchemaError(
            "Flatten schema produces duplicate column names",
            columns=duplicates,
        )


def _infer_type(value: Any) -> str:
    """Return the schema type name for a scalar value."""
    # bool must be checked before int because bool is a subclass of int.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _wider_type(a: str, b: str) -> str:
    """Return the wider of two scalar types (int < float < str)."""
    return max((a, b), key=_TYPE_ORDER.index)


def _infer_schema_node(  # pragma: no mutate
    value: Any,
    _path: str = "",
) -> dict[str, Any] | str:  # pragma: no mutate
    """Build a schema node from a single JSON value."""
    if value is None:
        return "str"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            _validate_json_object_key(k, _path)
            result[k] = _infer_schema_node(v, _format_key_path(_path, k))
        return result
    if isinstance(value, list):
        items: dict[str, Any] | str = {}  # pragma: no mutate
        for item in value:
            items = _merge_schema_nodes(items, _infer_schema_node(item, f"{_path}[]"))
        return {"$max": len(value), "$items": items if items != {} else {}}
    return _infer_type(value)


def _merge_schema_nodes(
    a: dict[str, Any] | str,  # pragma: no mutate
    b: dict[str, Any] | str,  # pragma: no mutate
) -> dict[str, Any] | str:  # pragma: no mutate
    """Merge two schema nodes, widening types and unioning fields."""
    if a == {}:
        return b
    if b == {}:
        return a

    if isinstance(a, str) and isinstance(b, str):
        return _wider_type(a, b)

    if isinstance(a, dict) and isinstance(b, dict):
        a_is_array = "$max" in a
        b_is_array = "$max" in b

        if a_is_array and b_is_array:
            return {
                "$max": max(a["$max"], b["$max"]),
                "$items": _merge_schema_nodes(
                    a.get("$items", {}),
                    b.get("$items", {}),
                ),
            }
        if not a_is_array and not b_is_array:
            merged = dict(a)
            for k, v in b.items():
                merged[k] = _merge_schema_nodes(merged[k], v) if k in merged else v
            return merged
        if a_is_array != b_is_array:
            array_node = a if a_is_array else b
            other_node = b if a_is_array else a
            return {
                "$max": max(array_node["$max"], 1),
                "$items": _merge_schema_nodes(
                    array_node.get("$items", {}),
                    other_node,
                ),
            }

    if isinstance(a, dict) and "$max" in a and isinstance(b, str):
        return {
            "$max": max(a["$max"], 1),
            "$items": _merge_schema_nodes(a.get("$items", {}), b),
        }
    if isinstance(b, dict) and "$max" in b and isinstance(a, str):
        return {
            "$max": max(b["$max"], 1),
            "$items": _merge_schema_nodes(b.get("$items", {}), a),
        }

    return a if isinstance(a, dict) else b


def infer_schema(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Infer a flatten schema from one or more sample JSON dicts."""
    if not samples:
        return {}
    schema: dict[str, Any] | str = {}  # pragma: no mutate
    for sample in samples:
        schema = _merge_schema_nodes(schema, _infer_schema_node(sample))
    return schema if isinstance(schema, dict) else {}


def flatten(
    data: dict[str, Any] | None,  # pragma: no mutate
    schema: dict[str, Any],
    *,  # pragma: no mutate
    _prefix: str = "",
) -> dict[str, Any]:  # pragma: no mutate
    """Flatten a nested JSON dict using a schema."""
    _validate_flatten_schema(schema)
    _assert_unique_column_names(schema_columns(schema, _prefix=_prefix))
    if data is None:
        data = {}
    result: dict[str, Any] = {}

    def _flatten_node(  # pragma: no mutate
        value: Any,
        spec: dict[str, Any] | str,  # pragma: no mutate
        prefix: str,
    ) -> None:
        if isinstance(spec, str):
            result[prefix] = value
            return

        if "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                return
            if isinstance(value, list):
                raw_list = value
            elif value is None:
                raw_list = []
            else:
                raw_list = [value]
            for i in range(max_items):
                idx_key = f"{prefix}.{i + 1}"
                element = raw_list[i] if i < len(raw_list) else None
                _flatten_node(element, items_schema, idx_key)
            return

        raw_obj = value if isinstance(value, dict) else {}
        for key, child_spec in spec.items():
            child_prefix = _format_key_path(prefix, key)
            child_value = raw_obj.get(key) if isinstance(raw_obj, dict) else None
            _flatten_node(child_value, child_spec, child_prefix)

    for key, spec in schema.items():
        value = data.get(key) if isinstance(data, dict) else None
        _flatten_node(value, spec, f"{_prefix}.{key}" if _prefix else key)

    return result


def schema_columns(
    schema: dict[str, Any],
    *,  # pragma: no mutate
    _prefix: str = "",
) -> list[str]:
    """Return the flat column names a schema produces, in traversal order."""
    _validate_flatten_schema(schema)
    cols: list[str] = []

    def _schema_columns_node(  # pragma: no mutate
        spec: dict[str, Any] | str,  # pragma: no mutate
        prefix: str,
    ) -> None:
        if isinstance(spec, str):
            cols.append(prefix)
            return

        if "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                return
            for i in range(max_items):
                idx_key = f"{prefix}.{i + 1}"
                _schema_columns_node(items_schema, idx_key)
            return

        for key, child_spec in spec.items():
            child_prefix = _format_key_path(prefix, key)
            _schema_columns_node(child_spec, child_prefix)

    for key, spec in schema.items():
        _schema_columns_node(spec, f"{_prefix}.{key}" if _prefix else key)
    _assert_unique_column_names(cols)
    return cols


def _schema_leaf_types(
    schema: dict[str, Any],
    *,  # pragma: no mutate
    _prefix: str = "",
) -> list[tuple[str, str]]:
    """Return ``[(column_name, type_str), ...]`` from a flatten schema."""
    _validate_flatten_schema(schema)
    result: list[tuple[str, str]] = []

    def _leaf_types_node(  # pragma: no mutate
        spec: dict[str, Any] | str,  # pragma: no mutate
        prefix: str,
    ) -> None:
        if isinstance(spec, str):
            result.append((prefix, spec))
            return

        if "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                return
            for i in range(max_items):
                idx_key = f"{prefix}.{i + 1}"
                _leaf_types_node(items_schema, idx_key)
            return

        for key, child_spec in spec.items():
            child_prefix = _format_key_path(prefix, key)
            _leaf_types_node(child_spec, child_prefix)

    for key, spec in schema.items():
        full_key = f"{_prefix}.{key}" if _prefix else key
        _leaf_types_node(spec, full_key)
    _assert_unique_column_names([name for name, _type in result])
    return result
