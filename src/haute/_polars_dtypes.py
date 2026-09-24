"""The one dtype vocabulary: a serialisable, struct-capable polars dtype codec.

Every place Haute names a polars dtype in text goes through this module.
Data-input/data-output node configs carry schema declarations (``schema``,
``schema_overrides``, ``hive_schema`` arguments) as JSON, and API-input
flat-file sources declare column dtypes the same way. Scalar dtypes are plain
strings (``"int64"``, ``"String"``). Parametric and nested dtypes use a
structured JSON spec, because struct capability is the point: JSON at full
width needs ``Struct``/``List`` columns as first-class declarable types.

Spec grammar (each ``<spec>`` is a string or an object)::

    "Int64" | "str" | "datetime" | ...            scalar alias or polars name
    {"type": "List",   "inner": <spec>}
    {"type": "Array",  "inner": <spec>, "size": 3}
    {"type": "Struct", "fields": {"a": <spec>, "b": <spec>}}
    {"type": "Decimal", "precision": 38, "scale": 2}
    {"type": "Datetime", "time_unit": "us", "time_zone": null}
    {"type": "Duration", "time_unit": "us"}
    {"type": "Enum", "categories": ["a", "b"]}
    {"type": "Categorical"}
    {"type": "Int64"}                              object form of any scalar

``parse_dtype`` and ``dtype_to_spec`` are exact inverses over the supported
lattice (round-trip tested), so editors can display what configs persist. A
name outside the grammar is rejected; it is never looked up as an arbitrary
attribute of the polars module.

Consumers that need a coarser vocabulary get a named view here rather than a
private table of their own: the rating descriptor renames this codec's keys
(``haute._rating``), and the feature-contract and MLflow views below serve
training, deploy scoring and MLflow signatures.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, cast

import polars as pl

from haute.errors import SchemaMismatchError

_TimeUnit = Literal["ms", "us", "ns"]

# Scalar string aliases, matched case-insensitively.
POLARS_DTYPE_ALIASES: Mapping[str, pl.DataType | type[pl.DataType]] = {
    "bool": pl.Boolean,
    "boolean": pl.Boolean,
    "date": pl.Date,
    "datetime": pl.Datetime,
    "duration": pl.Duration,
    "time": pl.Time,
    "float32": pl.Float32,
    "float64": pl.Float64,
    "float": pl.Float64,
    "int8": pl.Int8,
    "int16": pl.Int16,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "int": pl.Int64,
    "string": pl.String,
    "str": pl.String,
    "uint8": pl.UInt8,
    "uint16": pl.UInt16,
    "uint32": pl.UInt32,
    "uint64": pl.UInt64,
    "utf8": pl.String,
    "binary": pl.Binary,
    "null": pl.Null,
    "categorical": pl.Categorical,
    "decimal": pl.Decimal,
}

_TIME_UNITS = ("ms", "us", "ns")


def _scalar_dtype_from_string(name: str, *, column: str | None) -> pl.DataType | type[pl.DataType]:
    key = name.strip()
    alias = POLARS_DTYPE_ALIASES.get(key.lower())
    if alias is not None:
        return alias
    candidate = getattr(pl, key, None)
    if isinstance(candidate, type) and issubclass(candidate, pl.DataType):
        return candidate
    if isinstance(candidate, pl.DataType):
        return candidate
    raise SchemaMismatchError(
        "Unsupported declared dtype.",
        column=column,
        dtype=name,
    )


def _require(spec: Mapping[str, Any], key: str, *, column: str | None) -> Any:
    if key not in spec:
        raise SchemaMismatchError(
            f"Dtype spec {spec.get('type')!r} requires {key!r}.",
            column=column,
            dtype=str(dict(spec)),
        )
    return spec[key]


def _check_keys(spec: Mapping[str, Any], allowed: set[str], *, column: str | None) -> None:
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise SchemaMismatchError(
            "Unknown keys in dtype spec.",
            column=column,
            dtype=str(dict(spec)),
            unknown_keys=unknown,
        )


def _time_unit(spec: Mapping[str, Any], *, column: str | None) -> _TimeUnit:
    unit = spec.get("time_unit", "us")
    if unit not in _TIME_UNITS:
        raise SchemaMismatchError(
            f"Dtype time_unit must be one of {_TIME_UNITS}.",
            column=column,
            dtype=str(dict(spec)),
        )
    return cast(_TimeUnit, unit)


def parse_dtype(spec: Any, *, column: str | None = None) -> pl.DataType | type[pl.DataType]:
    """Parse a serialisable dtype spec into a polars dtype.

    Raises :class:`~haute.errors.SchemaMismatchError` on anything outside the
    documented grammar — never guesses.
    """
    if isinstance(spec, pl.DataType) or (isinstance(spec, type) and issubclass(spec, pl.DataType)):
        return spec  # already a polars dtype (internal callers)
    if isinstance(spec, str):
        return _scalar_dtype_from_string(spec, column=column)
    if not isinstance(spec, Mapping):
        raise SchemaMismatchError(
            "Dtype spec must be a string or an object.",
            column=column,
            dtype=repr(spec),
        )

    kind = spec.get("type")
    if not isinstance(kind, str) or not kind:
        raise SchemaMismatchError(
            "Dtype spec object requires a 'type' string.",
            column=column,
            dtype=str(dict(spec)),
        )

    if kind == "List":
        _check_keys(spec, {"type", "inner"}, column=column)
        return pl.List(parse_dtype(_require(spec, "inner", column=column), column=column))
    if kind == "Array":
        _check_keys(spec, {"type", "inner", "size"}, column=column)
        size = _require(spec, "size", column=column)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise SchemaMismatchError(
                "Array dtype spec requires a positive integer 'size'.",
                column=column,
                dtype=str(dict(spec)),
            )
        inner = parse_dtype(_require(spec, "inner", column=column), column=column)
        return pl.Array(inner, size)
    if kind == "Struct":
        _check_keys(spec, {"type", "fields"}, column=column)
        fields = _require(spec, "fields", column=column)
        if not isinstance(fields, Mapping) or not fields:
            raise SchemaMismatchError(
                "Struct dtype spec requires a non-empty 'fields' object.",
                column=column,
                dtype=str(dict(spec)),
            )
        return pl.Struct(
            {
                name: parse_dtype(sub, column=f"{column}.{name}" if column else name)
                for name, sub in fields.items()
            }
        )
    if kind == "Decimal":
        _check_keys(spec, {"type", "precision", "scale"}, column=column)
        precision = spec.get("precision")
        scale = spec.get("scale", 0)
        return pl.Decimal(precision=precision, scale=scale)
    if kind == "Datetime":
        _check_keys(spec, {"type", "time_unit", "time_zone"}, column=column)
        return pl.Datetime(
            time_unit=_time_unit(spec, column=column), time_zone=spec.get("time_zone")
        )
    if kind == "Duration":
        _check_keys(spec, {"type", "time_unit"}, column=column)
        return pl.Duration(time_unit=_time_unit(spec, column=column))
    if kind == "Enum":
        _check_keys(spec, {"type", "categories"}, column=column)
        categories = _require(spec, "categories", column=column)
        if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
            raise SchemaMismatchError(
                "Enum dtype spec requires 'categories' as a list of strings.",
                column=column,
                dtype=str(dict(spec)),
            )
        return pl.Enum(categories)
    if kind == "Categorical":
        _check_keys(spec, {"type"}, column=column)
        return pl.Categorical()

    # Object form of a scalar name: {"type": "Int64"}.
    _check_keys(spec, {"type"}, column=column)
    return _scalar_dtype_from_string(kind, column=column)


def dtype_to_spec(dtype: Any) -> Any:
    """Inverse of :func:`parse_dtype` — a JSON-serialisable spec for *dtype*.

    Accepts a ``pl.DataType`` instance or class (polars' ``.inner``/``.dtype``
    accessors return either).
    """
    if isinstance(dtype, type):
        dtype = dtype()

    if isinstance(dtype, pl.List):
        return {"type": "List", "inner": dtype_to_spec(dtype.inner)}
    if isinstance(dtype, pl.Array):
        return {"type": "Array", "inner": dtype_to_spec(dtype.inner), "size": dtype.size}
    if isinstance(dtype, pl.Struct):
        return {
            "type": "Struct",
            "fields": {field.name: dtype_to_spec(field.dtype) for field in dtype.fields},
        }
    if isinstance(dtype, pl.Decimal):
        return {"type": "Decimal", "precision": dtype.precision, "scale": dtype.scale}
    if isinstance(dtype, pl.Datetime):
        return {"type": "Datetime", "time_unit": dtype.time_unit, "time_zone": dtype.time_zone}
    if isinstance(dtype, pl.Duration):
        return {"type": "Duration", "time_unit": dtype.time_unit}
    if isinstance(dtype, pl.Enum):
        return {"type": "Enum", "categories": list(dtype.categories)}
    if isinstance(dtype, pl.Categorical):
        return {"type": "Categorical"}
    return str(dtype)


def parse_schema_mapping(raw: Any, *, argument: str) -> dict[str, Any]:
    """Decode a ``{column: dtype-spec}`` mapping argument.

    Returns a dict of column name → polars dtype, preserving order (order is
    meaningful for a full ``schema``).
    """
    if not isinstance(raw, Mapping):
        raise SchemaMismatchError(
            f"Argument {argument!r} must be a mapping of column name to dtype spec.",
        )
    decoded: dict[str, Any] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name:
            raise SchemaMismatchError(
                f"Argument {argument!r} column names must be non-empty strings.",
            )
        decoded[name] = parse_dtype(spec, column=name)
    return decoded


# ---------------------------------------------------------------------------
# Feature-contract view
# ---------------------------------------------------------------------------
#
# A model's feature contract records one coarse name per feature. Training
# writes it and deploy scoring rebuilds it from the live frame to compare, so
# both must render a dtype identically — which is why the rendering lives here.

_CONTRACT_MLFLOW_TYPES: Mapping[str, str] = {
    "Int64": "long",
    "Float64": "double",
    "String": "string",
    "Boolean": "boolean",
}
CONTRACT_SCALAR_NAMES: tuple[str, ...] = tuple(sorted(_CONTRACT_MLFLOW_TYPES))

_CONTRACT_DATETIME = re.compile(
    r"Datetime(?:\(time_unit='(?:ns|us|ms)', time_zone=(?:None|'[^']*')\))?\Z"
)
_CONTRACT_DECIMAL = re.compile(r"Decimal(?:\(precision=(?:\d+|None), scale=(?:\d+|None)\))?\Z")


def contract_dtype_name(dtype: Any) -> str:
    """The feature-contract name of *dtype*.

    Collapses polars' integer and float widths to ``Int64``/``Float64`` and the
    string family (``String``, ``Categorical``) to ``String`` — the scalar
    names an MLflow signature understands. Every other dtype keeps polars' own
    full rendering (``Date``, a parameterised
    ``Datetime(time_unit=..., time_zone=...)``, ``Decimal(...)``) so the place
    that consumes the contract can validate it deliberately.
    """
    if dtype == pl.Boolean:
        return "Boolean"
    if dtype in (pl.String, pl.Categorical):
        return "String"
    if dtype.is_integer():
        return "Int64"
    if dtype.is_float():
        return "Float64"
    return str(dtype)


def contract_mlflow_type_name(name: str) -> str | None:
    """The MLflow view of a feature-contract name.

    Returns the ``mlflow.types.DataType`` member name for *name*, or ``None``
    when MLflow has no exact type for it. ``Date`` and every canonical
    ``Datetime`` rendering map to ``datetime``; a lookalike does not.
    """
    mapped = _CONTRACT_MLFLOW_TYPES.get(name)
    if mapped is not None:
        return mapped
    if name == "Date" or _CONTRACT_DATETIME.fullmatch(name):
        return "datetime"
    return None


def is_contract_decimal_name(name: str) -> bool:
    """Whether *name* is a feature-contract ``Decimal`` rendering."""
    return _CONTRACT_DECIMAL.fullmatch(name) is not None


# ---------------------------------------------------------------------------
# Rendered-dtype MLflow view
# ---------------------------------------------------------------------------
#
# A deploy manifest records each input and output column as polars' own
# rendering, ``str(dtype)``, and a deployed model's signature describes that
# schema exactly: widths are kept (``Int32`` is ``integer``, ``Float32`` is
# ``float``), unlike the feature contract, which collapses them.

_RENDERED_DTYPE_MLFLOW_TYPES: Mapping[str, str] = {
    "Int8": "integer",
    "Int16": "integer",
    "Int32": "integer",
    "Int64": "long",
    "UInt8": "integer",
    "UInt16": "integer",
    "UInt32": "long",
    "UInt64": "long",
    "Float32": "float",
    "Float64": "double",
    "String": "string",
    "Utf8": "string",
    "Categorical": "string",
    "Enum": "string",
    "Boolean": "boolean",
    "Date": "datetime",
    "Datetime": "datetime",
}


def rendered_dtype_mlflow_type_name(rendered: str) -> str | None:
    """The MLflow view of a rendered polars dtype (``str(dtype)``).

    Returns the ``mlflow.types.DataType`` member name for the rendering's base
    type — its parameters, such as a ``Datetime`` time unit, do not change the
    MLflow type — or ``None`` when MLflow has no type for it.
    """
    return _RENDERED_DTYPE_MLFLOW_TYPES.get(rendered.split("(", 1)[0])
