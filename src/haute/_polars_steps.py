"""Low-code Polars steps: schema validation, rendering and input renaming.

A Transform node authored in step mode persists an ordered ``steps`` list in
its ``config/polars/<name>.json`` sidecar. This module is the single renderer
that turns that list into the Polars function body: the node data model
materialises ``config["code"]`` from it, the executor builder, codegen and the
parser validate input references through it, and the render endpoint shows
its output in the editor. Every consumer therefore executes exactly the same
program.

Rendering is a pure function of ``(steps, input_names)``. One step renders to
exactly one statement on exactly one line, so a step's line number is its
index plus one and the output is a fixpoint of the polars user-code extractor
(nothing to unwrap, no trailing ``return``, and a leading ``df = <input>``
line is authored code).

Validation fails loudly: any malformed, unknown or incomplete field raises
:class:`PolarsStepError` carrying the offending step index.
"""

from __future__ import annotations

import datetime as _dt
import keyword
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AGGREGATIONS",
    "BINARY_OPERATORS",
    "CAST_DTYPES",
    "FILL_STRATEGIES",
    "FUNCTIONS",
    "JOIN_HOW",
    "LITERAL_TYPES",
    "OPERATORS",
    "STEP_KINDS",
    "PolarsStepError",
    "RenderedSteps",
    "referenced_step_inputs",
    "rename_step_inputs",
    "render_polars_steps",
    "validate_polars_steps",
]


class PolarsStepError(ValueError):
    """A step list that cannot be rendered.

    ``step_index`` is the zero-based index of the offending step, or ``None``
    for a list-level problem. ``str(error)`` prefixes the message with the
    one-based step number so it reads naturally in the editor and in run-time
    errors.
    """

    def __init__(self, message: str, *, step_index: int | None = None) -> None:
        self.message = message
        self.step_index = step_index
        super().__init__(f"Step {step_index + 1}: {message}" if step_index is not None else message)


@dataclass(frozen=True)
class RenderedSteps:
    """The rendered function body and the 1-based inclusive line range per step."""

    code: str
    step_lines: tuple[tuple[int, int], ...]


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

STEP_KINDS: tuple[str, ...] = (
    "source",
    "filter",
    "with_column",
    "select",
    "drop",
    "rename",
    "cast",
    "sort",
    "unique",
    "group_by",
    "join",
    "concat",
    "fill_null",
    "limit",
    "variable",
)

_COMPARISON_OPERATORS: dict[str, str] = {
    "eq": "==",
    "ne": "!=",
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
}
_NO_VALUE_OPERATORS: dict[str, str] = {"is_null": "is_null", "is_not_null": "is_not_null"}
_MEMBERSHIP_OPERATORS: frozenset[str] = frozenset({"is_in", "not_in"})
_STRING_OPERATORS: dict[str, str] = {
    "contains": "contains",
    "starts_with": "starts_with",
    "ends_with": "ends_with",
}
OPERATORS: tuple[str, ...] = (
    *_COMPARISON_OPERATORS,
    *_NO_VALUE_OPERATORS,
    *sorted(_MEMBERSHIP_OPERATORS),
    *_STRING_OPERATORS,
)

BINARY_OPERATORS: tuple[str, ...] = ("+", "-", "*", "/", "//", "%", "**")
JOIN_HOW: tuple[str, ...] = ("inner", "left", "right", "full", "semi", "anti", "cross")
FILL_STRATEGIES: tuple[str, ...] = ("forward", "backward", "min", "max", "mean", "zero", "one")
AGGREGATIONS: tuple[str, ...] = (
    "sum",
    "mean",
    "min",
    "max",
    "median",
    "std",
    "var",
    "count",
    "n_unique",
    "first",
    "last",
    "len",
)
CAST_DTYPES: tuple[str, ...] = (
    "Int64",
    "Float64",
    "String",
    "Boolean",
    "Date",
    "Datetime",
    "Categorical",
)
LITERAL_TYPES: tuple[str, ...] = ("number", "text", "boolean", "date")

#: ``fn -> (argument literal types, render template)``. ``{r}`` is the
#: receiver rendered in expression position; ``{0}``/``{1}`` are bare literals.
FUNCTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "abs": ((), "{r}.abs()"),
    "floor": ((), "{r}.floor()"),
    "ceil": ((), "{r}.ceil()"),
    "sqrt": ((), "{r}.sqrt()"),
    "log": ((), "{r}.log()"),
    "exp": ((), "{r}.exp()"),
    "round": (("integer",), "{r}.round({0})"),
    "clip": (("number", "number"), "{r}.clip({0}, {1})"),
    "fill_null": (("scalar",), "{r}.fill_null({0})"),
    "cast": (("dtype",), "{r}.cast(pl.{0})"),
    "upper": ((), "{r}.str.to_uppercase()"),
    "lower": ((), "{r}.str.to_lowercase()"),
    "strip": ((), "{r}.str.strip_chars()"),
    "length": ((), "{r}.str.len_chars()"),
    "year": ((), "{r}.dt.year()"),
    "month": ((), "{r}.dt.month()"),
    "day": ((), "{r}.dt.day()"),
}

_STEP_KEYS: dict[str, frozenset[str]] = {
    "source": frozenset({"input"}),
    "filter": frozenset({"match", "conditions"}),
    "with_column": frozenset({"name", "expr"}),
    "select": frozenset({"columns"}),
    "drop": frozenset({"columns"}),
    "rename": frozenset({"renames"}),
    "cast": frozenset({"casts"}),
    "sort": frozenset({"keys", "nullsLast"}),
    "unique": frozenset({"columns", "keep"}),
    "group_by": frozenset({"keys", "aggregations"}),
    "join": frozenset({"input", "how", "leftOn", "rightOn", "suffix"}),
    "concat": frozenset({"inputs", "how"}),
    "fill_null": frozenset({"columns", "fill"}),
    "limit": frozenset({"n"}),
    "variable": frozenset({"name", "value"}),
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RESERVED_NAMES = frozenset({"df", "pl"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_polars_steps(steps: object) -> list[dict[str, Any]]:
    """Validate the step schema without input-name checks and return the list."""
    return _Renderer(steps, None).steps


def render_polars_steps(
    steps: object,
    input_names: Sequence[str] | None = None,
) -> RenderedSteps:
    """Render ``steps`` into the Polars function body.

    With ``input_names`` given, every input reference must be one of them;
    without it references are rendered as written.
    """
    return _Renderer(steps, input_names).render()


def referenced_step_inputs(steps: object) -> list[str]:
    """Return the distinct input names a valid step list references, in order."""
    seen: dict[str, None] = {}
    for step in validate_polars_steps(steps):
        for name in _step_input_references(step):
            seen.setdefault(name, None)
    return list(seen)


def rename_step_inputs(steps: object, renames: Mapping[str, str]) -> list[dict[str, Any]]:
    """Return a copy of ``steps`` with input references mapped through ``renames``.

    Raises :class:`PolarsStepError` when two referenced inputs would end up
    with the same name.
    """
    validated = validate_polars_steps(steps)
    referenced = referenced_step_inputs(validated)
    renamed = {name: renames.get(name, name) for name in referenced}
    targets = list(renamed.values())
    if len(set(targets)) != len(targets):
        duplicates = sorted({t for t in targets if targets.count(t) > 1})
        raise PolarsStepError(
            f"Renaming inputs would make {duplicates!r} refer to more than one input."
        )
    out: list[dict[str, Any]] = []
    for step in validated:
        copy = dict(step)
        kind = step["kind"]
        if kind in ("source", "join"):
            copy["input"] = renamed[step["input"]]
        elif kind == "concat":
            copy["inputs"] = [renamed[name] for name in step["inputs"]]
        out.append(copy)
    return out


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _step_input_references(step: Mapping[str, Any]) -> list[str]:
    kind = step["kind"]
    if kind in ("source", "join"):
        return [step["input"]]
    if kind == "concat":
        return list(step["inputs"])
    return []


class _Renderer:
    def __init__(self, steps: object, input_names: Sequence[str] | None) -> None:
        if not isinstance(steps, list):
            raise PolarsStepError("Steps must be a list.")
        self.input_names = None if input_names is None else frozenset(input_names)
        self.variables: set[str] = set()
        self.index = 0
        self.steps: list[dict[str, Any]] = [self._validate_step(i, s) for i, s in enumerate(steps)]

    # -- validation ------------------------------------------------------

    def fail(self, message: str) -> PolarsStepError:
        return PolarsStepError(message, step_index=self.index)

    def _validate_step(self, index: int, step: object) -> dict[str, Any]:
        self.index = index
        if not isinstance(step, dict):
            raise self.fail("Each step must be an object.")
        kind = step.get("kind")
        if not isinstance(kind, str) or kind not in _STEP_KEYS:
            raise self.fail(f"Unknown step kind {kind!r}.")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id:
            raise self.fail("Each step needs a non-empty string id.")
        allowed = _STEP_KEYS[kind] | {"id", "kind"}
        unknown = sorted(set(step) - allowed)
        if unknown:
            raise self.fail(f"Unknown field(s) {unknown!r} for a {kind} step.")
        missing = sorted(_STEP_KEYS[kind] - set(step))
        if missing:
            raise self.fail(f"Missing field(s) {missing!r} for a {kind} step.")
        return dict(step)

    def render(self) -> RenderedSteps:
        if not self.steps:
            raise PolarsStepError("Choose the input to start from.")
        ids = [s["id"] for s in self.steps]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise PolarsStepError(
                f"Step ids must be unique; {duplicates!r} repeat.",
                step_index=ids.index(duplicates[0]),
            )
        lines: list[str] = []
        for index, step in enumerate(self.steps):
            self.index = index
            kind = step["kind"]
            if index == 0 and kind != "source":
                raise self.fail("The first step must choose the input to start from.")
            if index > 0 and kind == "source":
                raise self.fail("Only the first step can choose the input to start from.")
            lines.append(getattr(self, f"_render_{kind}")(step))
        step_lines = tuple((i + 1, i + 1) for i in range(len(lines)))
        return RenderedSteps(code="\n".join(lines), step_lines=step_lines)

    # -- field helpers ---------------------------------------------------

    def _str(self, value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise self.fail(f"{label} must be a non-empty string.")
        return value

    def _bool(self, value: object, label: str) -> bool:
        if not isinstance(value, bool):
            raise self.fail(f"{label} must be true or false.")
        return value

    def _str_list(self, value: object, label: str, *, allow_empty: bool) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
            raise self.fail(f"{label} must be a list of non-empty strings.")
        if not value and not allow_empty:
            raise self.fail(f"{label} must name at least one column.")
        if len(set(value)) != len(value):
            raise self.fail(f"{label} must not repeat a column.")
        return list(value)

    def _choice(self, value: object, choices: Sequence[str], label: str) -> str:
        if not isinstance(value, str) or value not in choices:
            raise self.fail(f"{label} must be one of {list(choices)!r}, got {value!r}.")
        return value

    def _object(self, value: object, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise self.fail(f"{label} must be an object.")
        return value

    def _keys(self, obj: Mapping[str, Any], allowed: Sequence[str], label: str) -> None:
        unknown = sorted(set(obj) - set(allowed))
        if unknown:
            raise self.fail(f"Unknown field(s) {unknown!r} in {label}.")

    def _input(self, value: object, label: str) -> str:
        name = self._str(value, label)
        if not _IDENTIFIER.match(name) or keyword.iskeyword(name):
            raise self.fail(f"{label} {name!r} is not a valid input name.")
        if self.input_names is not None and name not in self.input_names:
            known = ", ".join(sorted(self.input_names)) or "none"
            raise self.fail(f"Unknown input {name!r}; connected inputs: {known}.")
        return name

    def _column(self, value: object, label: str = "Column") -> str:
        return f"pl.col({self._str(value, label)!r})"

    # -- literals and operands -------------------------------------------

    def _literal(self, operand: Mapping[str, Any], label: str) -> tuple[str, str]:
        """Return ``(literal_type, bare_render)`` for a literal operand."""
        self._keys(operand, ("kind", "type", "value"), label)
        literal_type = self._choice(operand.get("type"), LITERAL_TYPES, f"{label} type")
        value = operand.get("value")
        if literal_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise self.fail(f"{label} must be a number.")
            if isinstance(value, float) and not math.isfinite(value):
                raise self.fail(f"{label} must be a finite number.")
            return literal_type, repr(value)
        if literal_type == "text":
            if not isinstance(value, str):
                raise self.fail(f"{label} must be text.")
            return literal_type, repr(value)
        if literal_type == "boolean":
            if not isinstance(value, bool):
                raise self.fail(f"{label} must be true or false.")
            return literal_type, repr(value)
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            raise self.fail(f"{label} must be a date written as YYYY-MM-DD.")
        try:
            _dt.date.fromisoformat(value)
        except ValueError:
            raise self.fail(f"{label} {value!r} is not a real date.") from None
        return literal_type, f"pl.lit({value!r}).str.to_date()"

    def _operand(self, value: object, label: str, *, expr: bool) -> str:
        """Render an operand in value position (bare) or expression position."""
        operand = self._object(value, label)
        kind = operand.get("kind")
        if kind == "column":
            self._keys(operand, ("kind", "name"), label)
            return self._column(operand.get("name"), f"{label} column")
        if kind == "literal":
            literal_type, rendered = self._literal(operand, label)
            if literal_type == "date" or not expr:
                return rendered
            return f"pl.lit({rendered})"
        if kind == "variable":
            self._keys(operand, ("kind", "name"), label)
            name = self._str(operand.get("name"), f"{label} variable")
            if name not in self.variables:
                raise self.fail(f"Variable {name!r} is not defined by an earlier step.")
            return f"pl.lit({name})" if expr else name
        raise self.fail(f"{label} must be a column, a value or a variable, got {kind!r}.")

    # -- conditions ------------------------------------------------------

    def _condition(self, value: object, label: str) -> str:
        condition = self._object(value, label)
        self._keys(condition, ("column", "operator", "value", "values"), label)
        column = self._column(condition.get("column"), f"{label} column")
        operator = self._choice(condition.get("operator"), OPERATORS, f"{label} operator")
        has_value = "value" in condition
        has_values = "values" in condition
        if operator in _COMPARISON_OPERATORS or operator in _STRING_OPERATORS:
            if not has_value or has_values:
                raise self.fail(f"{label} operator {operator!r} needs a single value.")
            raw_value = condition["value"]
            if (
                operator in _STRING_OPERATORS
                and isinstance(raw_value, dict)
                and raw_value.get("kind") == "literal"
                and raw_value.get("type") != "text"
            ):
                raise self.fail(f"{label} operator {operator!r} needs a text value.")
            operand = self._operand(raw_value, f"{label} value", expr=False)
            if operator in _COMPARISON_OPERATORS:
                return f"{column} {_COMPARISON_OPERATORS[operator]} {operand}"
            if operator == "contains":
                return f"{column}.str.contains({operand}, literal=True)"
            return f"{column}.str.{_STRING_OPERATORS[operator]}({operand})"
        if operator in _NO_VALUE_OPERATORS:
            if has_value or has_values:
                raise self.fail(f"{label} operator {operator!r} takes no value.")
            return f"{column}.{_NO_VALUE_OPERATORS[operator]}()"
        if has_value or not has_values:
            raise self.fail(f"{label} operator {operator!r} needs a list of values.")
        members = self._members(condition["values"], f"{label} values")
        rendered = f"{column}.is_in({members})"
        return rendered if operator == "is_in" else f"~{rendered}"

    def _members(self, value: object, label: str) -> str:
        if not isinstance(value, list) or not value:
            raise self.fail(f"{label} must list at least one value.")
        rendered: list[str] = []
        types: set[str] = set()
        raw_dates: list[str] = []
        for item in value:
            operand = self._object(item, label)
            if operand.get("kind") != "literal":
                raise self.fail(f"{label} must be plain values.")
            literal_type, text = self._literal(operand, label)
            types.add(literal_type)
            rendered.append(text)
            if literal_type == "date":
                raw_dates.append(str(operand["value"]))
        if len(types) != 1:
            raise self.fail(f"{label} must all be the same type.")
        if types == {"date"}:
            return f"pl.Series({raw_dates!r}).str.to_date()"
        return f"[{', '.join(rendered)}]"

    def _conditions(self, step: Mapping[str, Any], label: str) -> str:
        match = self._choice(step.get("match"), ("all", "any"), f"{label} match")
        conditions = step.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise self.fail("Add at least one condition.")
        rendered = [
            f"({self._condition(c, f'{label} condition {i + 1}')})"
            for i, c in enumerate(conditions)
        ]
        joiner = " & " if match == "all" else " | "
        return joiner.join(rendered)

    # -- aggregations and expressions ------------------------------------

    def _aggregate(self, column: object, agg: object, label: str) -> str:
        name = self._choice(agg, AGGREGATIONS, f"{label} aggregation")
        if name == "len":
            return "pl.len()"
        return f"{self._column(column, f'{label} column')}.{name}()"

    def _expr(self, value: object, label: str) -> str:
        expr = self._object(value, label)
        kind = expr.get("type")
        if kind == "operand":
            self._keys(expr, ("type", "operand"), label)
            return self._operand(expr.get("operand"), f"{label} operand", expr=True)
        if kind == "binary":
            self._keys(expr, ("type", "left", "op", "right"), label)
            op = self._choice(expr.get("op"), BINARY_OPERATORS, f"{label} operator")
            left = self._operand(expr.get("left"), f"{label} left operand", expr=True)
            right = self._operand(expr.get("right"), f"{label} right operand", expr=False)
            return f"{left} {op} {right}"
        if kind == "function":
            self._keys(expr, ("type", "fn", "operand", "args"), label)
            fn = self._choice(expr.get("fn"), tuple(FUNCTIONS), f"{label} function")
            receiver = self._operand(expr.get("operand"), f"{label} operand", expr=True)
            arg_types, template = FUNCTIONS[fn]
            args = expr.get("args")
            if not isinstance(args, list) or len(args) != len(arg_types):
                raise self.fail(
                    f"Function {fn!r} takes {len(arg_types)} argument(s), got "
                    f"{len(args) if isinstance(args, list) else 'no list'}."
                )
            rendered_args = [
                self._function_arg(arg, expected, f"{label} {fn} argument {i + 1}")
                for i, (arg, expected) in enumerate(zip(args, arg_types, strict=True))
            ]
            return template.format(*rendered_args, r=receiver)
        if kind == "conditional":
            self._keys(expr, ("type", "match", "conditions", "then", "otherwise"), label)
            conditions = self._conditions(expr, label)
            then = self._operand(expr.get("then"), f"{label} then", expr=True)
            otherwise = self._operand(expr.get("otherwise"), f"{label} otherwise", expr=True)
            return f"pl.when({conditions}).then({then}).otherwise({otherwise})"
        if kind == "window":
            self._keys(expr, ("type", "agg", "column", "over"), label)
            over = self._str_list(expr.get("over"), f"{label} over", allow_empty=False)
            aggregate = self._aggregate(expr.get("column"), expr.get("agg"), label)
            return f"{aggregate}.over({over!r})"
        raise self.fail(f"Unknown expression type {kind!r}.")

    def _function_arg(self, value: object, expected: str, label: str) -> str:
        operand = self._object(value, label)
        if operand.get("kind") != "literal":
            raise self.fail(f"{label} must be a plain value.")
        literal_type, rendered = self._literal(operand, label)
        if expected == "integer":
            raw = operand.get("value")
            if literal_type != "number" or not isinstance(raw, int) or raw < 0:
                raise self.fail(f"{label} must be a whole number of zero or more.")
            return rendered
        if expected == "number":
            if literal_type != "number":
                raise self.fail(f"{label} must be a number.")
            return rendered
        if expected == "scalar":
            if literal_type == "date":
                raise self.fail(f"{label} must be a number, text or true/false.")
            return rendered
        if literal_type != "text" or operand.get("value") not in CAST_DTYPES:
            raise self.fail(f"{label} must be one of {list(CAST_DTYPES)!r}.")
        return str(operand["value"])

    # -- steps -----------------------------------------------------------

    def _render_source(self, step: Mapping[str, Any]) -> str:
        return f"df = {self._input(step['input'], 'Input')}"

    def _render_filter(self, step: Mapping[str, Any]) -> str:
        return f"df = df.filter({self._conditions(step, 'Filter')})"

    def _render_with_column(self, step: Mapping[str, Any]) -> str:
        name = self._str(step["name"], "Column name")
        return f"df = df.with_columns(({self._expr(step['expr'], 'Expression')}).alias({name!r}))"

    def _render_select(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=False)
        return f"df = df.select({columns!r})"

    def _render_drop(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=False)
        return f"df = df.drop({columns!r})"

    def _render_rename(self, step: Mapping[str, Any]) -> str:
        renames = step["renames"]
        if not isinstance(renames, list) or not renames:
            raise self.fail("Add at least one rename.")
        pairs: list[str] = []
        seen: set[str] = set()
        for i, entry in enumerate(renames):
            entry = self._object(entry, f"Rename {i + 1}")
            self._keys(entry, ("from", "to"), f"Rename {i + 1}")
            source = self._str(entry.get("from"), f"Rename {i + 1} from")
            target = self._str(entry.get("to"), f"Rename {i + 1} to")
            if source in seen:
                raise self.fail(f"Column {source!r} is renamed more than once.")
            seen.add(source)
            pairs.append(f"{source!r}: {target!r}")
        return f"df = df.rename({{{', '.join(pairs)}}})"

    def _render_cast(self, step: Mapping[str, Any]) -> str:
        casts = step["casts"]
        if not isinstance(casts, list) or not casts:
            raise self.fail("Add at least one column to cast.")
        rendered: list[str] = []
        for i, entry in enumerate(casts):
            entry = self._object(entry, f"Cast {i + 1}")
            self._keys(entry, ("column", "dtype"), f"Cast {i + 1}")
            column = self._column(entry.get("column"), f"Cast {i + 1} column")
            dtype = self._choice(entry.get("dtype"), CAST_DTYPES, f"Cast {i + 1} type")
            rendered.append(f"{column}.cast(pl.{dtype})")
        return f"df = df.with_columns({', '.join(rendered)})"

    def _render_sort(self, step: Mapping[str, Any]) -> str:
        keys = step["keys"]
        if not isinstance(keys, list) or not keys:
            raise self.fail("Add at least one sort column.")
        columns: list[str] = []
        descending: list[bool] = []
        for i, entry in enumerate(keys):
            entry = self._object(entry, f"Sort key {i + 1}")
            self._keys(entry, ("column", "descending"), f"Sort key {i + 1}")
            columns.append(self._str(entry.get("column"), f"Sort key {i + 1} column"))
            descending.append(self._bool(entry.get("descending"), f"Sort key {i + 1} descending"))
        nulls_last = self._bool(step["nullsLast"], "Nulls last")
        return f"df = df.sort({columns!r}, descending={descending!r}, nulls_last={nulls_last!r})"

    def _render_unique(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=True)
        keep = self._choice(step["keep"], ("first", "last", "any"), "Keep")
        subset = repr(columns) if columns else "None"
        return f"df = df.unique(subset={subset}, keep={keep!r}, maintain_order=True)"

    def _render_group_by(self, step: Mapping[str, Any]) -> str:
        keys = self._str_list(step["keys"], "Group keys", allow_empty=False)
        aggregations = step["aggregations"]
        if not isinstance(aggregations, list) or not aggregations:
            raise self.fail("Add at least one aggregation.")
        rendered: list[str] = []
        names: set[str] = set()
        for i, entry in enumerate(aggregations):
            entry = self._object(entry, f"Aggregation {i + 1}")
            self._keys(entry, ("column", "agg", "name"), f"Aggregation {i + 1}")
            name = self._str(entry.get("name"), f"Aggregation {i + 1} name")
            if name in names:
                raise self.fail(f"Aggregation name {name!r} is used more than once.")
            names.add(name)
            aggregate = self._aggregate(
                entry.get("column"), entry.get("agg"), f"Aggregation {i + 1}"
            )
            rendered.append(f"{aggregate}.alias({name!r})")
        return f"df = df.group_by({keys!r}, maintain_order=True).agg([{', '.join(rendered)}])"

    def _render_join(self, step: Mapping[str, Any]) -> str:
        other = self._input(step["input"], "Join input")
        how = self._choice(step["how"], JOIN_HOW, "Join type")
        left_on = self._str_list(step["leftOn"], "Left keys", allow_empty=True)
        right_on = self._str_list(step["rightOn"], "Right keys", allow_empty=True)
        suffix = self._str(step["suffix"], "Suffix")
        if how == "cross":
            if left_on or right_on:
                raise self.fail("A cross join takes no key columns.")
            return f"df = df.join({other}, how='cross', suffix={suffix!r})"
        if not left_on or len(left_on) != len(right_on):
            raise self.fail("Left and right key lists must be non-empty and the same length.")
        return (
            f"df = df.join({other}, left_on={left_on!r}, right_on={right_on!r}, "
            f"how={how!r}, suffix={suffix!r})"
        )

    def _render_concat(self, step: Mapping[str, Any]) -> str:
        inputs = step["inputs"]
        if not isinstance(inputs, list) or not inputs:
            raise self.fail("Choose at least one input to append.")
        names = [self._input(name, f"Concat input {i + 1}") for i, name in enumerate(inputs)]
        if len(set(names)) != len(names):
            raise self.fail("Each input can be appended once.")
        how = self._choice(step["how"], ("vertical", "diagonal"), "Concat type")
        return f"df = pl.concat([df, {', '.join(names)}], how={how!r})"

    def _render_fill_null(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=True)
        target = f"pl.col({columns!r})" if columns else "pl.all()"
        fill = self._object(step["fill"], "Fill")
        kind = fill.get("kind")
        if kind == "value":
            self._keys(fill, ("kind", "value"), "Fill")
            value = self._operand(fill.get("value"), "Fill value", expr=False)
            return f"df = df.with_columns({target}.fill_null({value}))"
        if kind == "strategy":
            self._keys(fill, ("kind", "strategy"), "Fill")
            strategy = self._choice(fill.get("strategy"), FILL_STRATEGIES, "Fill strategy")
            return f"df = df.with_columns({target}.fill_null(strategy={strategy!r}))"
        raise self.fail(f"Fill must be a value or a strategy, got {kind!r}.")

    def _render_limit(self, step: Mapping[str, Any]) -> str:
        n = step["n"]
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise self.fail("Row limit must be a whole number greater than zero.")
        return f"df = df.head({n})"

    def _render_variable(self, step: Mapping[str, Any]) -> str:
        name = self._str(step["name"], "Variable name")
        if not _IDENTIFIER.match(name) or keyword.iskeyword(name) or name in _RESERVED_NAMES:
            raise self.fail(f"Variable name {name!r} is not a valid name.")
        if name in self.variables:
            raise self.fail(f"Variable {name!r} is already defined.")
        if self.input_names is not None and name in self.input_names:
            raise self.fail(f"Variable name {name!r} is already an input name.")
        operand = self._object(step["value"], "Variable value")
        if operand.get("kind") != "literal":
            raise self.fail("A variable holds a plain value.")
        literal_type, rendered = self._literal(operand, "Variable value")
        if literal_type == "date":
            raise self.fail("A variable holds a number, text or true/false.")
        self.variables.add(name)
        return f"{name} = {rendered}"
