"""Rating and banding helpers extracted from executor.

This module contains the pure-logic functions for applying banding rules
and rating table lookups to Polars frames.  They are used by
``executor._build_node_fn`` but have no dependency on the executor
module itself, keeping the dependency graph acyclic.
"""

from __future__ import annotations

import math
import operator
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

import polars as pl

from haute._banding_config import (
    normalise_banding_factors,
    normalise_banding_rules,
)
from haute._logging import get_logger
from haute._rating_step_config import (
    normalise_rating_tables,
    validate_unique_rating_table_outputs,
)
from haute._types import _Frame
from haute.errors import RatingExtremaUndefinedError, RatingFactorMissingError

logger = get_logger(component="rating")

# ---------------------------------------------------------------------------
# Banding
# ---------------------------------------------------------------------------

SUPPORTED_BANDING_OPERATORS = MappingProxyType(
    {
        "<": operator.lt,
        "<=": operator.le,
        ">": operator.gt,
        ">=": operator.ge,
        "=": operator.eq,
        "==": operator.eq,
    }
)
SUPPORTED_BANDING_TYPES = frozenset({"continuous", "categorical", "breakpoints"})


def _banding_rule_comparators(rule: dict[str, Any]) -> list[tuple[str, float]]:
    """Return the validated operator/threshold pairs usable by one rule."""
    comparators: list[tuple[str, float]] = []
    for suffix in ("1", "2"):
        op = str(rule.get(f"op{suffix}", "") or "").strip()
        val = rule.get(f"val{suffix}")
        if not op or val is None or val == "":
            continue
        evaluator = SUPPORTED_BANDING_OPERATORS.get(op)
        if evaluator is None:
            raise ValueError(f"Banding rule has unsupported operator '{op}' for op{suffix}")
        try:
            num = float(val)
        except (ValueError, TypeError):
            raise ValueError(f"Banding rule has non-numeric threshold '{val}' for op{suffix}")
        if not math.isfinite(num):
            raise ValueError(f"Banding rule has non-finite threshold '{val}' for op{suffix}")
        comparators.append((op, num))
    return comparators


def _banding_condition(col: pl.Expr, rule: dict[str, Any]) -> pl.Expr | None:
    """Build a Polars boolean expression from a continuous banding rule."""
    parts: list[pl.Expr] = [
        SUPPORTED_BANDING_OPERATORS[op](col, threshold)
        for op, threshold in _banding_rule_comparators(rule)
    ]
    if not parts:
        return None
    result = parts[0]
    for p in parts[1:]:
        result = result & p
    return result


def _apply_banding(
    lf: _Frame,
    column: str,
    output_column: str,
    banding_type: str,
    rules: list[dict[str, Any]] | dict[str, Any],
    default: Any = None,
    right_closed: bool = True,
) -> _Frame:
    """Apply banding rules to a column, producing a new output column.

    Continuous rules use operator/value pairs to define ranges::

        {"op1": ">", "val1": 0, "op2": "<=", "val2": 25, "assignment": "0-25"}

    Categorical rules map exact values to groups::

        {"value": "Semi-detached House", "assignment": "House"}
    """
    rules = normalise_banding_rules(banding_type, rules)
    has_configured_rules = bool(rules)
    col = pl.col(column)
    default_lit = pl.lit(default) if default is not None else pl.lit(None, dtype=pl.Utf8)

    if banding_type == "categorical":
        # Build a remap dict: value → assignment
        remap: dict[str, str] = {}
        for rule in rules:
            val = rule.get("value", "")
            assignment = rule.get("assignment", "")
            if (val is not None and val != "") and (assignment is not None and assignment != ""):
                remap[str(val)] = str(assignment)
        if not remap:
            if has_configured_rules:
                raise ValueError(f"Banding output {output_column!r} has no usable categorical rule")
            return lf
        cat_expr = col.cast(pl.Utf8).replace_strict(remap, default=default_lit).alias(output_column)
        return lf.with_columns(cat_expr)

    # Breakpoints mode: convert to continuous rules first
    if banding_type == "breakpoints":
        rules = _breakpoints_to_rules(rules, right_closed=right_closed)

    # For continuous banding, sanitize NaN/Inf in float columns so they
    # don't match arbitrary rules — they fall cleanly to the default.
    if hasattr(lf, "collect_schema"):
        schema = lf.collect_schema()
    else:
        schema = dict(zip(lf.columns, lf.dtypes))  # type: ignore[assignment]
    col_dtype = schema.get(column)
    if col_dtype in (pl.Float32, pl.Float64):
        # Build the NaN/Inf-safe expression LOCALLY and feed it into the
        # rule chain below.  Aliasing it back onto the source ``column``
        # would overwrite NaN/Inf for every downstream node, not just this
        # banding output — only ``output_column`` may be added/changed.
        col = pl.when(col.is_nan() | col.is_infinite()).then(pl.lit(None)).otherwise(col)

    # Continuous: build a when/then chain
    chain: Any = None
    for rule in rules:
        cond = _banding_condition(col, rule)
        if cond is None:
            continue
        assignment = str(rule.get("assignment", ""))
        if not assignment:
            continue
        branch = pl.when(cond).then(pl.lit(assignment))
        chain = branch if chain is None else chain.when(cond).then(pl.lit(assignment))

    if chain is None:
        if has_configured_rules:
            raise ValueError(f"Banding output {output_column!r} has no usable continuous rule")
        return lf
    final_expr = chain.otherwise(default_lit).alias(output_column)
    return lf.with_columns(final_expr)


def _breakpoints_to_rules(
    breakpoints: list[dict[str, Any]] | dict[str, Any],
    right_closed: bool = True,
) -> list[dict[str, Any]]:
    """Convert breakpoint-format rules to continuous banding rules.

    Each breakpoint has a ``boundary`` (numeric string) and a ``label``.
    The last breakpoint may have an empty boundary to create an open-ended rule.

    When *right_closed* is True, intervals are ``(lower, upper]`` — the first
    rule uses ``<=`` for its upper bound and subsequent rules use ``>`` / ``<=``.
    When False, intervals are ``[lower, upper)`` using ``>=`` / ``<``.
    """
    breakpoints = normalise_banding_rules("breakpoints", breakpoints)
    if not breakpoints:
        return []

    # Separate breakpoints with boundaries from the open-ended tail
    bounded: list[dict[str, Any]] = []
    open_ended: dict[str, Any] | None = None
    open_ended_count = 0
    for bp in breakpoints:
        boundary = str(bp.get("boundary", "") or "").strip()
        label = str(bp.get("label", "") or "")
        if not boundary:
            open_ended = bp
            open_ended_count += 1
        else:
            try:
                num = float(boundary)
            except (ValueError, TypeError):
                raise ValueError(f"Breakpoint has non-numeric boundary '{boundary}'")
            if not math.isfinite(num):
                raise ValueError(f"Breakpoint has non-finite boundary '{boundary}'")
            bounded.append({"boundary": num, "label": label})

    # Reject more than one open-ended boundary: only the last would ever win,
    # so extras would be silently dropped (fail loud instead).
    if open_ended_count > 1:
        raise ValueError(
            "A breakpoints factor may have at most one open-ended boundary "
            f"(empty boundary); found {open_ended_count}"
        )

    # An open-ended boundary needs at least one bounded breakpoint to anchor
    # its lower edge.  A sole open-ended breakpoint would otherwise produce no
    # rules at all and silently emit no output column — fail loud instead.
    if open_ended is not None and not bounded:
        raise ValueError(
            "A breakpoints factor with only an open-ended boundary (no bounded "
            "breakpoints) cannot define any interval; add at least one bounded "
            "breakpoint"
        )

    # Sort by boundary value
    bounded.sort(key=lambda b: b["boundary"])

    # Reject duplicate boundaries — they produce empty intervals
    seen_boundaries: set[float] = set()
    for entry in bounded:
        if entry["boundary"] in seen_boundaries:
            raise ValueError(f"Duplicate breakpoint boundary '{entry['boundary']}'")
        seen_boundaries.add(entry["boundary"])

    rules: list[dict[str, Any]] = []
    prev_boundary: float | None = None

    for entry in bounded:
        b = entry["boundary"]
        label = entry["label"]
        rule: dict[str, Any] = {"assignment": label}

        if prev_boundary is None:
            # First rule: everything up to b
            if right_closed:
                rule["op1"] = "<="
                rule["val1"] = b
            else:
                rule["op1"] = "<"
                rule["val1"] = b
        else:
            # Middle rule: from prev_boundary to b
            if right_closed:
                rule["op1"] = ">"
                rule["val1"] = prev_boundary
                rule["op2"] = "<="
                rule["val2"] = b
            else:
                rule["op1"] = ">="
                rule["val1"] = prev_boundary
                rule["op2"] = "<"
                rule["val2"] = b

        rules.append(rule)
        prev_boundary = b

    # Open-ended tail
    if open_ended is not None and prev_boundary is not None:
        label = str(open_ended.get("label", "") or "")
        if right_closed:
            rules.append({"op1": ">", "val1": prev_boundary, "assignment": label})
        else:
            rules.append({"op1": ">=", "val1": prev_boundary, "assignment": label})

    return rules


def _normalise_banding_factors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``factors`` list from banding config."""
    return normalise_banding_factors(config)


def validate_banding_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and return canonical banding factors.

    A factor without a column, output column, or rules remains a supported
    draft no-op. Once all three are configured, the discriminant and rule
    shape must be executable rather than being silently skipped at runtime.
    """

    factors = normalise_banding_factors(config)
    for index, factor in enumerate(factors):
        configured_type = str(factor.get("banding", "") or "").strip()
        column = str(factor.get("column", "") or "").strip()
        output_column = str(factor.get("outputColumn", "") or "").strip()
        configured_rules = factor.get("rules", []) or []

        if not column or not output_column or not configured_rules:
            continue
        banding_type = configured_type or "continuous"
        if banding_type not in SUPPORTED_BANDING_TYPES:
            allowed = ", ".join(sorted(SUPPORTED_BANDING_TYPES))
            raise ValueError(
                f"Banding factor {index} has unsupported banding type "
                f"{banding_type!r}; expected one of: {allowed}"
            )
        rules = normalise_banding_rules(banding_type, configured_rules)
        if not rules:
            continue

        if banding_type == "categorical":
            usable = any(
                rule.get("value") not in (None, "") and rule.get("assignment") not in (None, "")
                for rule in rules
            )
            if not usable:
                raise ValueError(f"Banding output {output_column!r} has no usable categorical rule")
            continue

        continuous_rules = (
            _breakpoints_to_rules(
                rules,
                right_closed=bool(factor.get("rightClosed", True)),
            )
            if banding_type == "breakpoints"
            else rules
        )
        usable = False
        for rule in continuous_rules:
            comparators = _banding_rule_comparators(rule)
            assignment = rule.get("assignment")
            if comparators and assignment not in (None, ""):
                usable = True
        if not usable:
            raise ValueError(f"Banding output {output_column!r} has no usable continuous rule")
    return factors


def _apply_banding_factors(lf: _Frame, factors: Iterable[dict[str, Any]]) -> _Frame:
    """Apply normalised banding *factors* to a frame, in order.

    The single application loop shared by the executor's banding node
    builder (``_builders._build_banding``) and the generated-code entry
    point :func:`apply_banding_from_config`, so the GUI canvas and a
    standalone run of the saved file band identically.

    Factors missing a column, output column, or rules are skipped — the
    node is a passthrough for those factors (matching the executor's
    long-standing semantics; an empty config is a documented no-op).
    """
    for factor in factors:
        col = factor.get("column", "")
        out = factor.get("outputColumn", "")
        rules = factor.get("rules", []) or []
        if not col or not out or not rules:
            continue
        lf = _apply_banding(
            lf,
            col,
            out,
            factor.get("banding", "continuous"),
            rules,
            factor.get("default"),
            right_closed=factor.get("rightClosed", True),
        )
    return lf


def apply_banding_from_config(
    lf: _Frame,
    config: dict[str, Any] | str | PathLike[str],
    *,
    base_dir: str | Path | None = None,
) -> _Frame:
    """Apply a banding JSON/dict config to a frame.

    The generated-code twin of the executor's banding builder — saved
    pipeline files embed ``apply_banding_from_config(df, "config/banding/
    <name>.json", base_dir=...)`` so a standalone ``pipeline.run()`` bands
    exactly like the GUI executor.  Mirrors
    :func:`apply_rating_step_from_config`.
    """
    if isinstance(config, dict):
        resolved_config = config
    else:
        from haute._config_io import load_node_config

        config_path = config if isinstance(config, str) else Path(config)
        resolved_config = load_node_config(
            config_path, base_dir=Path(base_dir) if base_dir else None
        )

    return _apply_banding_factors(lf, _normalise_banding_factors(resolved_config))


# ---------------------------------------------------------------------------
# Rating tables
# ---------------------------------------------------------------------------

_LOOKUP_VAL = "__haute_lookup_val__"
_SUPPORTED_COMBINE_OPERATIONS = frozenset({"multiply", "add", "min", "max"})
_SUPPORTED_ON_MISSING = frozenset({"error", "neutral"})
_MISS_KEY_DISPLAY_CAP = 10
# Int-like floats collapse to integer digit strings only inside the Int64
# range, where the cast on the engine side is exact and lossless.
_INT64_MIN = -(2**63)
_INT64_MAX_EXCL = 2**63
_DURATION_KEY_RE = re.compile(
    r"^(?P<sign>-)?P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)
_DURATION_UNITS_PER_SECOND = MappingProxyType(
    {
        "ms": Decimal(1_000),
        "us": Decimal(1_000_000),
        "ns": Decimal(1_000_000_000),
    }
)
_PRIMITIVE_RATING_DTYPE_NAMES: Mapping[object, str] = MappingProxyType(
    {
        pl.Int8: "Int8",
        pl.Int16: "Int16",
        pl.Int32: "Int32",
        pl.Int64: "Int64",
        pl.Int128: "Int128",
        pl.UInt8: "UInt8",
        pl.UInt16: "UInt16",
        pl.UInt32: "UInt32",
        pl.UInt64: "UInt64",
        pl.Float32: "Float32",
        pl.Float64: "Float64",
        pl.Boolean: "Boolean",
        pl.String: "String",
        pl.Categorical: "Categorical",
        pl.Date: "Date",
        pl.Time: "Time",
        pl.Null: "Null",
    }
)
_RATING_PRIMITIVE_DESCRIPTOR_KINDS = frozenset(
    {
        "Int8",
        "Int16",
        "Int32",
        "Int64",
        "Int128",
        "UInt8",
        "UInt16",
        "UInt32",
        "UInt64",
        "Float32",
        "Float64",
        "Boolean",
        "String",
        "Categorical",
        "Date",
        "Time",
        "Null",
    }
)
_RATING_PRIMITIVE_DTYPES: Mapping[str, pl.DataType] = MappingProxyType(
    {name: cast(pl.DataType, dtype) for dtype, name in _PRIMITIVE_RATING_DTYPE_NAMES.items()}
)


class RatingTableMissError(ValueError):
    """A rating-table lookup left rows without a matching entry.

    Raised at materialisation time when a table has no usable
    ``defaultValue`` and ``onMissing`` is ``"error"`` (the default).
    The message names the table, the missing key(s) (capped at
    ``_MISS_KEY_DISPLAY_CAP``) and the affected row count.
    """


def rating_dtype_descriptor(dtype: pl.DataType) -> dict[str, Any]:
    """Return the stable JSON descriptor for a supported rating-factor dtype."""
    if dtype == pl.Categorical:
        return {"kind": "Categorical"}
    primitive = _PRIMITIVE_RATING_DTYPE_NAMES.get(dtype)
    if primitive is not None:
        return {"kind": primitive}
    if isinstance(dtype, pl.Datetime):
        return {
            "kind": "Datetime",
            "timeUnit": dtype.time_unit,
            "timeZone": dtype.time_zone,
        }
    if isinstance(dtype, pl.Duration):
        return {"kind": "Duration", "timeUnit": dtype.time_unit}
    if isinstance(dtype, pl.Decimal):
        return {
            "kind": "Decimal",
            "precision": dtype.precision,
            "scale": dtype.scale,
        }
    if isinstance(dtype, pl.Enum):
        return {
            "kind": "Enum",
            "categories": dtype.categories.to_list(),
        }
    raise ValueError(f"unsupported rating factor dtype {dtype}")


def is_rating_dtype_descriptor(value: object) -> bool:
    """Return whether *value* is one exact stable rating dtype descriptor."""
    if not isinstance(value, dict):
        return False
    kind = value.get("kind")
    if kind in _RATING_PRIMITIVE_DESCRIPTOR_KINDS:
        return set(value) == {"kind"}
    if kind == "Datetime":
        return (
            set(value) == {"kind", "timeUnit", "timeZone"}
            and value.get("timeUnit") in {"ms", "us", "ns"}
            and (value.get("timeZone") is None or isinstance(value.get("timeZone"), str))
        )
    if kind == "Duration":
        return set(value) == {"kind", "timeUnit"} and value.get("timeUnit") in {"ms", "us", "ns"}
    if kind == "Decimal":
        precision = value.get("precision")
        scale = value.get("scale")
        return (
            set(value) == {"kind", "precision", "scale"}
            and (
                precision is None
                or (isinstance(precision, int) and not isinstance(precision, bool))
            )
            and isinstance(scale, int)
            and not isinstance(scale, bool)
        )
    if kind == "Enum":
        categories = value.get("categories")
        return (
            set(value) == {"kind", "categories"}
            and isinstance(categories, list)
            and all(isinstance(category, str) for category in categories)
            and len(categories) == len(set(categories))
        )
    return False


def rating_dtype_from_descriptor(descriptor: object) -> pl.DataType:
    """Reconstruct a supported rating-factor dtype from its JSON descriptor."""
    if not is_rating_dtype_descriptor(descriptor):
        raise ValueError(f"invalid rating factor dtype descriptor {descriptor!r}")

    # The predicate above narrows the descriptor's shape at runtime.  Keep
    # the individual reads here so this remains the exact inverse of
    # ``rating_dtype_descriptor`` rather than accepting loosely shaped JSON.
    assert isinstance(descriptor, dict)
    kind = descriptor["kind"]
    assert isinstance(kind, str)
    if kind in _RATING_PRIMITIVE_DESCRIPTOR_KINDS:
        return _RATING_PRIMITIVE_DTYPES[kind]
    if kind == "Datetime":
        time_unit = cast(Literal["ms", "us", "ns"], descriptor["timeUnit"])
        time_zone = descriptor["timeZone"]
        assert time_zone is None or isinstance(time_zone, str)
        return pl.Datetime(time_unit, time_zone)
    if kind == "Duration":
        time_unit = cast(Literal["ms", "us", "ns"], descriptor["timeUnit"])
        return pl.Duration(time_unit)
    if kind == "Decimal":
        precision = descriptor["precision"]
        scale = descriptor["scale"]
        assert precision is None or isinstance(precision, int)
        assert isinstance(scale, int)
        return pl.Decimal(precision, scale)

    assert kind == "Enum"
    categories = descriptor["categories"]
    assert isinstance(categories, list)
    return pl.Enum(categories)


def _duration_key_to_physical(value: str, time_unit: str) -> int:
    """Parse Polars' ISO-8601 duration display into an exact physical value."""
    match = _DURATION_KEY_RE.fullmatch(value)
    if match is None or not any(
        match.group(name) is not None for name in ("days", "hours", "minutes", "seconds")
    ):
        raise ValueError(f"invalid ISO-8601 duration rating key {value!r}")
    try:
        seconds = (
            Decimal(match.group("days") or 0) * 86_400
            + Decimal(match.group("hours") or 0) * 3_600
            + Decimal(match.group("minutes") or 0) * 60
            + Decimal(match.group("seconds") or 0)
        )
        physical = seconds * _DURATION_UNITS_PER_SECOND[time_unit]
    except (InvalidOperation, KeyError) as exc:
        raise ValueError(f"invalid {time_unit!r} duration rating key {value!r}") from exc
    if physical != physical.to_integral_value():
        raise ValueError(
            f"duration rating key {value!r} is not exactly representable as {time_unit}"
        )
    result = int(physical)
    return -result if match.group("sign") else result


def _coerce_rating_lookup_expr(
    name: str,
    source_dtype: pl.DataType,
    target_dtype: pl.DataType,
) -> pl.Expr:
    """Strictly coerce a sidecar factor column through its input-frame dtype."""
    rating_dtype_descriptor(target_dtype)
    col = pl.col(name)
    if target_dtype == pl.Null:
        return pl.lit(None, dtype=pl.Null).alias(name)
    if target_dtype == pl.Boolean and source_dtype == pl.String:
        return col.replace_strict(
            {"true": True, "false": False},
            return_dtype=pl.Boolean,
        ).alias(name)
    if target_dtype == pl.Time and source_dtype == pl.String:
        return col.str.to_time(strict=True).alias(name)
    if isinstance(target_dtype, pl.Datetime) and source_dtype == pl.String:
        return col.str.to_datetime(
            time_unit=target_dtype.time_unit,
            time_zone=target_dtype.time_zone,
            strict=True,
        ).alias(name)
    if isinstance(target_dtype, pl.Duration) and source_dtype == pl.String:
        time_unit = target_dtype.time_unit
        return (
            col.map_elements(
                lambda value: _duration_key_to_physical(value, time_unit),
                return_dtype=pl.Int64,
            )
            .cast(target_dtype)
            .alias(name)
        )
    return col.cast(target_dtype, strict=True).alias(name)


def normalise_rating_key(
    value: Any,
    dtype: pl.DataType,
) -> str | None:
    """Canonicalise one scalar through the runtime's exact typed key path.

    Supplying the originating dtype is mandatory at persistence and trace
    boundaries, where Python/JSON may otherwise widen a Float32 or erase
    categorical and temporal type information.
    """
    rating_dtype_descriptor(dtype)
    raw = pl.Series("__haute_rating_key__", [value])
    source_dtype = raw.dtype

    # This is the eager scalar equivalent of _coerce_rating_lookup_expr.
    # Keep the expression implementation as the engine oracle for frame
    # operations; the scalar path must merely apply those same branches.
    if dtype == pl.Null:
        typed = pl.Series(raw.name, [None], dtype=pl.Null)
    elif dtype == pl.Boolean and source_dtype == pl.String:
        typed = raw.replace_strict({"true": True, "false": False}, return_dtype=pl.Boolean)
    elif dtype == pl.Time and source_dtype == pl.String:
        typed = raw.str.to_time(strict=True)
    elif isinstance(dtype, pl.Datetime) and source_dtype == pl.String:
        typed = raw.str.to_datetime(
            time_unit=dtype.time_unit,
            time_zone=dtype.time_zone,
            strict=True,
        )
    elif isinstance(dtype, pl.Duration) and source_dtype == pl.String:
        typed = raw.map_elements(
            lambda item: _duration_key_to_physical(item, dtype.time_unit),
            return_dtype=pl.Int64,
        ).cast(dtype)
    else:
        typed = raw.cast(dtype, strict=True)

    # This is the eager scalar equivalent of _rating_key_expr.
    if dtype in (pl.Float32, pl.Float64):
        item = typed.item()
        if item is None:
            return None
        is_int_like = (
            typed.is_finite().item()
            and item == typed.round().item()
            and _INT64_MIN <= item < _INT64_MAX_EXCL
        )
        key = (
            typed.cast(pl.Int64, strict=False).cast(pl.String).item()
            if is_int_like
            else typed.cast(pl.String).item()
        )
    elif dtype == pl.Time or isinstance(dtype, pl.Duration):
        key = typed.dt.to_string().item()
    else:
        key = typed.cast(pl.String).item()
    return None if key is None else str(key)


def _rating_key_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """Expression twin of :func:`normalise_rating_key` for a frame column.

    Applied to *both* sides of the rating lookup join, so the engine is
    internally consistent by construction; agreement with the Python
    mirror is pinned by ``tests/test_rating_key_agreement.py``.

    Float widths remain native.  The Python mirror reconstructs its scalar
    through the supplied originating dtype before evaluating this expression,
    so Python/JSON widening cannot alter a Float32 key.

    ``Decimal`` (and other exact) columns retain their declared representation
    in the final ``Utf8`` key. Lookup entries are coerced through that declared
    dtype before this expression runs, so equivalent authored forms such as
    ``"25.5"`` and ``"25.50"`` share the same scale-2 key.
    """
    rating_dtype_descriptor(dtype)
    col = pl.col(name)
    if dtype in (pl.Float32, pl.Float64):
        int_like = (
            col.is_finite()
            & (col == col.round())
            & (col >= float(_INT64_MIN))
            & (col < float(_INT64_MAX_EXCL))
        )
        # strict=False: when/then evaluates the cast on every row, including
        # rows routed to otherwise (e.g. 1e300); the condition guarantees the
        # nulled overflows are never selected.
        return (
            pl.when(int_like)
            .then(col.cast(pl.Int64, strict=False).cast(pl.Utf8))
            .otherwise(col.cast(pl.Utf8))
            .alias(name)
        )
    if dtype == pl.Time or isinstance(dtype, pl.Duration):
        return col.dt.to_string().alias(name)
    return col.cast(pl.Utf8).alias(name)


def _frame_schema(lf: _Frame) -> Any:
    if hasattr(lf, "collect_schema"):
        return lf.collect_schema()
    return dict(zip(lf.columns, lf.dtypes))


def _schema_names(schema: Any) -> list[str]:
    names = getattr(schema, "names", None)
    if callable(names):
        return list(names())
    return list(schema.keys())


def _is_unsupported_factor_dtype(dtype: pl.DataType) -> bool:
    try:
        rating_dtype_descriptor(dtype)
    except ValueError:
        return True
    return False


def _validate_supported_factor_dtypes(
    original_dtypes: dict[str, pl.DataType],
    *,
    table_label: str,
) -> None:
    for factor, dtype in original_dtypes.items():
        if _is_unsupported_factor_dtype(dtype):
            raise ValueError(
                f"Rating table {table_label!r} factor {factor!r} has unsupported dtype {dtype}. "
                "Supported scalar dtypes are Int8, Int16, Int32, Int64, Int128, "
                "UInt8, UInt16, UInt32, UInt64, Float32, Float64, Boolean, String, "
                "Categorical, Enum, Date, Time, Datetime, Duration, Decimal, and Null. "
                "Cast this factor upstream to a supported scalar dtype."
            )


def _normalise_on_missing(value: object) -> str:
    """Return a validated rating-table miss policy ("error" | "neutral")."""
    if value is None:
        return "error"
    normalised = str(value).strip()
    if not normalised:
        return "error"
    if normalised not in _SUPPORTED_ON_MISSING:
        raise ValueError(
            f"Unsupported rating table onMissing {normalised!r}; "
            f"expected one of {sorted(_SUPPORTED_ON_MISSING)!r}"
        )
    return normalised


def _apply_rating_miss_guard(
    lf: _Frame,
    factors: list[str],
    *,
    key_columns: list[str] | None = None,
    lookup_value_column: str = _LOOKUP_VAL,
    table_label: str,
    output_col: str,
    on_missing: str,
    default_note: str = "",
) -> _Frame:
    """Validate lookup misses at a projection-safe lazy-plan barrier.

    Projection, predicate, and slice pushdown stop at this barrier so a caller
    cannot accidentally prune the validation by selecting a different output
    column. The callback returns each batch unchanged and remains streamable.
    """

    resolved_key_columns = key_columns if key_columns is not None else factors
    if len(resolved_key_columns) != len(factors):
        raise ValueError("rating miss-guard key columns must align with public factors")

    def _check(frame: pl.DataFrame) -> pl.DataFrame:
        values = frame[lookup_value_column]
        miss_mask = values.is_null()
        miss_count = int(miss_mask.sum())
        if not miss_count:
            return frame
        missed = (
            frame.filter(miss_mask)
            .select(resolved_key_columns)
            .rename(dict(zip(resolved_key_columns, factors)))
            .unique(maintain_order=True)
        )
        shown = missed.head(_MISS_KEY_DISPLAY_CAP).to_dicts()
        if on_missing == "neutral":
            logger.warning(
                "rating_table_lookup_misses",
                table=table_label,
                output_column=output_col,
                miss_count=miss_count,
                row_count=frame.height,
                distinct_missing_keys=missed.height,
                missing_keys=shown,
            )
            return frame
        shown_text = ", ".join(str(key) for key in shown)
        raise RatingTableMissError(
            f"Rating table {table_label!r} (output column {output_col!r}): "
            f"{miss_count} of {frame.height} row(s) had no matching entry for "
            f"factor(s) {factors!r}. Missing key(s): {shown_text} "
            f"(showing {len(shown)} of {missed.height} distinct).{default_note} "
            "Add the missing level(s) to the table, set a numeric "
            '\'defaultValue\', or set "onMissing": "neutral" to accept '
            "neutral pricing for misses."
        )

    if isinstance(lf, pl.DataFrame):
        return _check(lf)
    return lf.map_batches(
        _check,
        predicate_pushdown=False,
        projection_pushdown=False,
        slice_pushdown=False,
        streamable=True,
    )


def _normalise_combine_operation(operation: object) -> str:
    """Return a validated rating combine operation."""
    normalised = str(operation or "multiply")
    if normalised not in _SUPPORTED_COMBINE_OPERATIONS:
        raise ValueError(
            f"Unsupported rating combine operation {normalised!r}; "
            f"expected one of {sorted(_SUPPORTED_COMBINE_OPERATIONS)!r}"
        )
    return normalised


def _rating_internal_column(occupied: set[str], stem: str) -> str:
    """Reserve a collision-free internal column name."""
    candidate = stem
    while candidate in occupied:
        candidate = f"_{candidate}"
    occupied.add(candidate)
    return candidate


def _apply_rating_table(
    lf: _Frame,
    table: dict[str, Any],
    *,
    input_schema: Any | None = None,
) -> _Frame:
    """Apply a single rating table lookup via a Polars left join.

    *table* must contain ``factors`` (list of column names to join on),
    ``outputColumn``, ``entries`` (list of dicts with one key per factor
    plus a ``value`` key), and optionally ``defaultValue`` and
    ``onMissing``.

    Both join sides are canonicalised with :func:`_rating_key_expr`, so
    int-like float keys (``25.0``) match string-keyed entries (``"25"``)
    deterministically.

    Miss policy (3a.3 — breaking change, release-noted): a lookup miss
    with no usable ``defaultValue`` raises :class:`RatingTableMissError`
    at materialisation.  ``"onMissing": "neutral"`` opts back in to the
    old behaviour explicitly — the table output stays null (combined
    outputs fill the operation's neutral element) and every miss is
    counted and logged at WARNING.  A usable ``defaultValue`` always
    fills misses with no error or warning.
    """
    raw_factors = table.get("factors")
    raw_entries = table.get("entries")
    factors = [] if raw_factors is None else raw_factors
    entries = [] if raw_entries is None else raw_entries
    if not isinstance(factors, list):
        raise ValueError("rating table factors must be a list")
    if not isinstance(entries, list):
        raise ValueError("rating table entries must be a list")
    if entries and not factors:
        raise ValueError("rating table entries require a non-empty factors list")
    output_col: str = table.get("outputColumn", "")
    default_raw = table.get("defaultValue")
    on_missing = _normalise_on_missing(table.get("onMissing"))

    if not factors or not output_col:
        return lf

    # Validate the declared input contract before every incomplete-table guard.
    # An entry-less lookup is still a no-op, but it must never hide a typo in a
    # required factor name.
    frame_schema = input_schema if input_schema is not None else _frame_schema(lf)
    existing_cols = set(_schema_names(frame_schema))
    table_label = output_col
    for factor in factors:
        if factor not in existing_cols:
            raise RatingFactorMissingError(
                f"Rating table {table_label!r} requires input factor {factor!r}, "
                "but that column is absent from the input schema",
                table=table_label,
                factor=factor,
            )

    if not entries:
        return lf

    # These are cheap structural passthrough checks.  They intentionally
    # precede lookup construction: a malformed lookup entry remains a
    # configuration no-op when its declared input factors do exist.
    entry_cols: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_cols.update(entry.keys())
    if "value" not in entry_cols or any(factor not in entry_cols for factor in factors):
        return lf

    # Parse the default up front (B13: tolerate non-numeric/non-finite
    # values) — whether a usable default exists decides if the miss guard
    # is wired into the plan at all, and an unusable one is named in the
    # miss error instead of being silently ignored.
    has_default = bool(default_raw is not None and str(default_raw).strip())
    try:
        default_val: float | None = float(str(default_raw)) if has_default else None
    except (ValueError, TypeError):
        default_val = None
    # Reject inf/nan — they corrupt downstream arithmetic silently
    if default_val is not None and not math.isfinite(default_val):
        default_val = None
    default_note = ""
    if has_default and default_val is None:
        default_note = (
            f" Note: configured defaultValue {default_raw!r} is not a usable "
            "finite number and was ignored."
        )

    original_dtypes = {factor: frame_schema[factor] for factor in factors}
    _validate_supported_factor_dtypes(
        original_dtypes,
        table_label=table_label,
    )

    # Build the lookup eagerly: rating tables are small configuration data,
    # and strict factor/value conversion should fail before the lazy input
    # plan is returned to a caller.
    lookup = pl.DataFrame(entries)
    if "value" not in lookup.columns:
        return lf
    lookup = lookup.with_columns(pl.col("value").cast(pl.Float64, strict=True))

    # Reject NaN/Inf in rating table entries — they corrupt pricing silently
    _bad_count = lookup.filter(pl.col("value").is_nan() | pl.col("value").is_infinite()).height
    if _bad_count:
        raise ValueError(
            f"Rating table for '{output_col}' contains {_bad_count} NaN or Inf entries"
        )
    # Reject null entry values too (3a.3): sidecar validation has always
    # required a value per entry, and a null rate would neutral-fill in
    # combined outputs exactly like a miss — but invisibly, because after
    # the join it is indistinguishable from one.  Rejecting nulls here
    # keeps the miss guard's "no matching entry" diagnosis always true.
    _null_count = lookup.get_column("value").null_count()
    if _null_count:
        raise ValueError(
            f"Rating table for '{output_col}' contains {_null_count} null entry "
            "value(s); every entry requires a finite numeric value"
        )

    # B15: Select only factor columns + "value" to avoid polluting the main
    # frame with extra keys that may be present in the entries dicts.
    # Guard: if any factor column is missing from entries, config is invalid.
    missing = [f for f in factors if f not in lookup.columns]
    if missing:
        return lf
    lookup = lookup.select([*factors, "value"])

    occupied = existing_cols | entry_cols | {output_col}
    key_columns = [
        _rating_internal_column(occupied, f"__haute_rating_key_{index}__")
        for index, _factor in enumerate(factors)
    ]
    lookup_value_column = _rating_internal_column(occupied, _LOOKUP_VAL)

    # Entry scalars are first coerced through the exact originating input
    # dtype.  Canonical keys are then built from that typed value on both
    # sides of the join; JSON scalar widening therefore cannot change a key.
    lookup_schema = lookup.schema
    lookup = lookup.with_columns(
        [
            _coerce_rating_lookup_expr(
                factor,
                lookup_schema[factor],
                original_dtypes[factor],
            )
            for factor in factors
        ]
    ).select(
        [
            *[
                _rating_key_expr(factor, original_dtypes[factor]).alias(key_column)
                for factor, key_column in zip(factors, key_columns)
            ],
            pl.col("value"),
        ]
    )

    # B14: Deduplicate the factor keys so a left join cannot fan out rows.
    # keep="last" preserves the last-authored entry within a duplicate-key
    # group, matching trace enrichment which walks entries in reverse to
    # report the same winning row. Deduplication happens after strict
    # originating-dtype coercion and key generation, so representational
    # aliases such as Float64 entry strings "25.0" and "25.00" form one group.
    lookup = lookup.unique(subset=key_columns, keep="last")

    # Rename "value" to an internal name to avoid collision with any
    # input "value" column in the input frame (Bug #1/#2).
    lookup = lookup.rename({"value": lookup_value_column})

    # Source factors stay untouched.  Temporary keys are collision-free and
    # removed after the lookup.
    lf = lf.with_columns(
        [
            _rating_key_expr(factor, original_dtypes[factor]).alias(key_column)
            for factor, key_column in zip(factors, key_columns)
        ]
    )

    # Left join.  Preserve the input row order explicitly because Polars
    # streaming joins may otherwise emit hash-partition order.
    lf = lf.join(lookup.lazy(), on=key_columns, how="left", maintain_order="left")

    # Miss guard (3a.3): only when no usable default exists — a usable
    # defaultValue fills every miss below, so nothing can be silent.
    # Diagnostics relabel temporary keys with the public factor names.
    if default_val is None:
        lf = _apply_rating_miss_guard(
            lf,
            factors,
            key_columns=key_columns,
            lookup_value_column=lookup_value_column,
            table_label=table_label,
            output_col=output_col,
            on_missing=on_missing,
            default_note=default_note,
        )

    # Rename value → outputColumn, apply default
    if default_val is not None:
        lf = lf.with_columns(
            pl.col(lookup_value_column).fill_null(default_val).alias(output_col),
        )
    else:
        lf = lf.with_columns(pl.col(lookup_value_column).alias(output_col))

    lf = lf.drop([*key_columns, lookup_value_column])

    return lf


def _combine_rating_columns(
    lf: _Frame,
    columns: list[str],
    operation: str,
    output_col: str,
) -> _Frame:
    """Combine multiple rating table output columns into a single column.

    Supported operations: multiply (default), add, min, max.

    Null inputs fold in as the operation's neutral element (1.0 multiply,
    0.0 add; min/max skip nulls horizontally).  In the rating-step path
    nulls can only reach this point when a table explicitly opted in with
    ``"onMissing": "neutral"`` — every such miss has already been counted
    and logged by the miss guard in ``_apply_rating_table`` (3a.3);
    misses are otherwise rejected loudly there.
    """
    operation = _normalise_combine_operation(operation)
    seen_columns: set[str] = set()
    duplicate_columns: list[str] = []
    for column in columns:
        if column in seen_columns and column not in duplicate_columns:
            duplicate_columns.append(column)
        seen_columns.add(column)
    if duplicate_columns:
        raise ValueError(
            f"Rating output {output_col!r} cannot combine duplicate participant "
            f"column(s) {duplicate_columns!r}"
        )
    if output_col in seen_columns:
        raise ValueError(
            f"Rating output column {output_col!r} cannot overwrite a participant column"
        )
    if not columns:
        return lf
    if len(columns) == 1:
        expr = pl.col(columns[0])
        if operation not in {"min", "max"}:
            return lf.with_columns(expr.alias(output_col))
        return lf.with_columns(_rating_extrema_expr(expr, columns, operation, output_col))

    if operation == "add":
        # fill_null(0.0) for add: an opted-in miss contributes nothing.
        expr = pl.col(columns[0]).fill_null(0.0)
        for c in columns[1:]:
            expr = expr + pl.col(c).fill_null(0.0)
    elif operation == "min":
        expr = pl.min_horizontal(*[pl.col(c) for c in columns])
    elif operation == "max":
        expr = pl.max_horizontal(*[pl.col(c) for c in columns])
    else:  # multiply (default)
        # fill_null(1.0) for multiply: an opted-in miss has no effect (neutral element).
        expr = pl.col(columns[0]).fill_null(1.0)
        for c in columns[1:]:
            expr = expr * pl.col(c).fill_null(1.0)

    if operation in {"min", "max"}:
        expr = _rating_extrema_expr(expr, columns, operation, output_col)
    else:
        expr = expr.alias(output_col)
    return lf.with_columns(expr)


def _rating_extrema_expr(
    extrema: pl.Expr,
    columns: list[str],
    operation: str,
    output_col: str,
) -> pl.Expr:
    """Return an extrema expression that fails for rows with no value.

    The batch UDF is deliberately embedded in the output expression: it must
    run only when the lazy result materialises, and cannot be pruned as an
    unrelated validation side-column.
    """

    message = (
        f"Rating {operation} output {output_col!r} is undefined for a row "
        "where every participating value is null"
    )

    class _LazyRatingExtremaUndefinedError(RatingExtremaUndefinedError):
        """Carry public error fields through Polars' positional recreation."""

        def __init__(
            self,
            error_message: str | None = None,
            error_output_column: str | None = None,
            error_operation: str | None = None,
        ) -> None:
            error_message = error_message or message
            error_output_column = error_output_column or output_col
            error_operation = error_operation or operation
            super().__init__(
                error_message,
                output_column=error_output_column,
                operation=error_operation,
            )
            self.args = (error_message, error_output_column, error_operation)

    def _require_value(batch: pl.Series) -> pl.Series:
        values = batch.struct.unnest()
        undefined = values.select(pl.all_horizontal(pl.all().is_null())).to_series()
        if bool(undefined.any()):
            raise _LazyRatingExtremaUndefinedError(message, output_col, operation)
        return pl.Series("", [False] * len(batch), dtype=pl.Boolean)

    guard = pl.struct(columns).map_batches(
        _require_value,
        return_dtype=pl.Boolean,
        is_elementwise=True,
    )
    return pl.when(guard).then(pl.lit(None)).otherwise(extrema).alias(output_col)


def _normalise_combined_outputs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return strictly validated canonical combined-output definitions."""
    raw_outputs = config.get("combinedOutputs")
    if raw_outputs is None or raw_outputs == []:
        return []
    if not isinstance(raw_outputs, list):
        raise ValueError("ratingStep combinedOutputs must be a list")

    outputs: list[dict[str, Any]] = []
    seen_output_cols: set[str] = {
        str(t.get("outputColumn", "") or "").strip()
        for t in config.get("tables", []) or []
        if str(t.get("outputColumn", "") or "").strip()
    }
    for idx, item in enumerate(raw_outputs):
        if not isinstance(item, dict):
            raise ValueError(f"ratingStep combinedOutputs[{idx}] must be an object")
        output_col = str(item.get("outputColumn", "") or "").strip()
        if not output_col:
            raise ValueError(f"ratingStep combinedOutputs[{idx}] requires outputColumn")
        if output_col in seen_output_cols:
            raise ValueError(
                f"ratingStep combinedOutputs[{idx}].outputColumn {output_col!r} "
                "duplicates another rating output column"
            )
        seen_output_cols.add(output_col)
        operation = _normalise_combine_operation(item.get("operation", "multiply"))

        base_raw = item.get("baseValue")
        if (
            base_raw is None
            or isinstance(base_raw, bool)
            or (isinstance(base_raw, str) and not base_raw.strip())
        ):
            raise ValueError(f"ratingStep combinedOutputs[{idx}] requires baseValue")
        try:
            base_value = float(base_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"ratingStep combinedOutputs[{idx}].baseValue must be numeric"
            ) from exc
        if not math.isfinite(base_value):
            raise ValueError(f"ratingStep combinedOutputs[{idx}].baseValue must be finite")
        outputs.append(
            {
                "outputColumn": output_col,
                "operation": operation,
                "baseValue": base_value,
            }
        )
    return outputs


def _combine_rating_output(
    lf: _Frame,
    columns: list[str],
    operation: str,
    output_col: str,
    base_value: float | None = None,
    input_schema: Any | None = None,
) -> _Frame:
    """Combine table outputs, optionally including a numeric base value."""
    if base_value is None:
        return _combine_rating_columns(lf, columns, operation, output_col)
    base_col = f"__haute_rating_base_{output_col}__"
    existing_cols = (
        set(_schema_names(input_schema))
        if input_schema is not None
        else set(_schema_names(_frame_schema(lf)))
    )
    while base_col in columns or base_col in existing_cols:
        base_col = f"_{base_col}"
    with_base = lf.with_columns(pl.lit(base_value, dtype=pl.Float64).alias(base_col))
    combined = _combine_rating_columns(with_base, [base_col, *columns], operation, output_col)
    return combined.drop(base_col)


def _rating_table_skip_reason(table: dict[str, Any]) -> str | None:
    """Why :func:`_apply_rating_table` passes *table* through, or ``None``.

    Mirrors that function's passthrough guards: a table with no factors, no
    entries, no output column, entries lacking a ``value`` key, or a factor
    column absent from every entry is a documented no-op and produces no
    output column.  Returns a short human-readable reason for the skip, or
    ``None`` when the table materialises its output column.  The reason lets
    the rating-step loop log an *observable* skip (F082) instead of silently
    dropping a configured output column from combined outputs.
    """
    factors: list[str] = table.get("factors", []) or []
    entries: list[dict[str, Any]] = table.get("entries", []) or []
    output_col = table.get("outputColumn", "")
    if not factors:
        return "no factors"
    if not entries:
        return "no entries"
    if not output_col:
        return "no outputColumn"
    entry_cols: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_cols.update(entry.keys())
    if "value" not in entry_cols:
        return "entries have no 'value' key"
    missing = [f for f in factors if f not in entry_cols]
    if missing:
        return f"factor column(s) {missing!r} absent from every entry"
    return None


def _rating_table_materialises(table: dict[str, Any]) -> bool:
    """Whether :func:`_apply_rating_table` will add *table*'s output column.

    Used so the rating-step loop never registers a phantom output column for
    combining/reference when the table was skipped.
    """
    return _rating_table_skip_reason(table) is None


def _apply_rating_step_outputs(
    lf: _Frame,
    tables: list[dict[str, Any]],
    combined_outputs: list[dict[str, Any]],
) -> _Frame:
    """Apply rating tables and combined outputs to a frame."""
    if isinstance(lf, pl.DataFrame):
        lf = lf.lazy()

    # Keep this assertion at the execution boundary as well as the config
    # codec: internal callers can pass already-expanded table lists directly.
    validate_unique_rating_table_outputs(tables)

    # Resolve the frame schema once and thread it through every table so each
    # _apply_rating_table avoids re-running collect_schema() on a growing lazy
    # plan (O(N^2) -> O(N)).  Each materialised table adds its Float64 output
    # column to this local view for subsequent tables.
    schema: dict[str, Any] = dict(_frame_schema(lf))

    out_cols: list[str] = []
    for table in tables:
        lf = _apply_rating_table(lf, table, input_schema=schema)
        output_col = str(table.get("outputColumn", "") or "").strip()
        # A table with no output column is a deliberately disabled/passthrough
        # node — it was never asked to contribute a column, so stay quiet.
        if not output_col:
            continue
        # Only register the output column when the table actually materialised
        # it.  An incomplete table is a documented passthrough; registering a
        # phantom column would make a downstream combined output combine or
        # reference a column that never appears in the frame.
        skip_reason = _rating_table_skip_reason(table)
        if skip_reason is None:
            out_cols.append(output_col)
            schema[output_col] = pl.Float64
        else:
            # F082 (fail loud): the author configured an outputColumn but the
            # table is incomplete, so it produced nothing and is omitted from
            # combined outputs.  Log the skip so the omission is observable
            # instead of a silently dropped column.
            logger.warning(
                "rating_table_skipped_incomplete",
                table=output_col,
                output_column=output_col,
                reason=skip_reason,
            )

    for combined in combined_outputs:
        lf = _combine_rating_output(
            lf,
            out_cols,
            combined["operation"],
            combined["outputColumn"],
            combined["baseValue"],
            input_schema=schema,
        )
        if combined["baseValue"] is not None or out_cols:
            schema[combined["outputColumn"]] = pl.Float64
    return lf


def apply_rating_step_from_config(
    lf: _Frame,
    config: dict[str, Any] | str | PathLike[str],
    *,
    base_dir: str | Path | None = None,
) -> _Frame:
    """Apply a rating-step JSON/dict config before custom post-processing code."""
    if isinstance(config, dict):
        resolved_config = config
    else:
        from haute._config_io import load_node_config

        config_path = config if isinstance(config, str) else Path(config)
        resolved_config = load_node_config(
            config_path, base_dir=Path(base_dir) if base_dir else None
        )

    return _apply_rating_step_outputs(
        lf,
        normalise_rating_tables(resolved_config),
        _normalise_combined_outputs(resolved_config),
    )
