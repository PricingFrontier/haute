"""Low-code Polars steps: schema validation, rendering and input renaming.

A Transform node authored in step mode persists an ordered ``steps`` list in
its ``config/polars/<name>.json`` sidecar. This module is the single renderer
that turns that list into the Polars function body: the node data model
materialises ``config["code"]`` from it, the executor builder, codegen and the
parser validate input references through it, and the render endpoint shows
its output in the editor. Every consumer therefore executes exactly the same
program.

Rendering is a pure function of ``(steps, input_names)``. Structured steps
render to one line; free-code steps can span several lines. The renderer
records each step's inclusive line range. Output is a fixpoint of the polars
user-code extractor (no node-level ``return``, and the leading
``df = <input>`` line is authored code).

Validation fails loudly: any malformed, unknown or incomplete field raises
:class:`PolarsStepError` carrying the offending step index.
"""

from __future__ import annotations

import ast
import datetime as _dt
import keyword
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from haute._types import NodeType

__all__ = [
    "STEPPED_NODE_TYPES",
    "STEP_STARTS",
    "StepStart",
    "SteppedSurface",
    "is_stepped_config",
    "step_input_names",
    "stepped_surface_allows_input_references",
    "stepped_surface_for",
    "AGGREGATIONS",
    "JOIN_MAINTAIN_ORDER",
    "JOIN_VALIDATE",
    "JOIN_VALIDATED_HOW",
    "WINDOW_AGGREGATIONS",
    "WINDOW_ONLY_AGGREGATIONS",
    "BINARY_OPERATORS",
    "CAST_DTYPES",
    "FILL_STRATEGIES",
    "FUNCTIONS",
    "JOIN_HOW",
    "LITERAL_TYPES",
    "MAX_EXPR_DEPTH",
    "PIVOT_AGGREGATIONS",
    "OPERATORS",
    "STEPPED_TRANSFORM_INPUT_MAPPING_MESSAGE",
    "STEP_KINDS",
    "PolarsStepError",
    "RenderedSteps",
    "referenced_step_inputs",
    "rename_step_inputs",
    "render_polars_steps",
    "validate_polars_steps",
]


#: Why a stepped original transform refuses ``inputMapping``: its steps name
#: their inputs by edge name, so a rename rewrites the steps instead.
STEPPED_TRANSFORM_INPUT_MAPPING_MESSAGE = (
    "A stepped transform addresses its inputs by their edge names and cannot carry inputMapping."
)

#: Where ``df`` comes from when a step list starts: ``input`` means the first
#: step chooses an input and renders ``df = <input>`` (a transform); ``frame``
#: means the surface hands the steps a frame already bound to ``df`` (a Data
#: Input's opened snapshot), so no start step exists and an empty list is
#: simply no code.
StepStart = Literal["input", "frame"]
STEP_STARTS: tuple[StepStart, ...] = ("input", "frame")


@dataclass(frozen=True)
class SteppedSurface:
    """How one node type authors steps.

    ``start`` is the render mode. ``inputs`` is what ``join``/``concat`` steps
    may reference: ``edges`` for a surface whose code sees its incoming edges
    by name, ``none`` for one whose code sees only ``df``. Every path that
    renders a node's steps takes its eligible names from this table through
    :func:`step_input_names`, so the editor, the parser, codegen and execution
    agree on what a step may name.
    """

    start: StepStart
    inputs: Literal["edges", "none"]


STEPPED_NODE_TYPES: Mapping[NodeType, SteppedSurface] = MappingProxyType(
    {
        NodeType.POLARS: SteppedSurface(start="input", inputs="edges"),
        NodeType.DATA_INPUT: SteppedSurface(start="frame", inputs="none"),
        # The first input is already `df`; the other edges stay addressable.
        NodeType.EXTERNAL_FILE: SteppedSurface(start="frame", inputs="edges"),
        NodeType.RATING_STEP: SteppedSurface(start="frame", inputs="none"),
        NodeType.MODEL_SCORE: SteppedSurface(start="frame", inputs="none"),
        NodeType.SCENARIO_EXPANDER: SteppedSurface(start="frame", inputs="none"),
        # The single input is bound as df by codegen; steps live in the decorator.
        NodeType.EXPLORE: SteppedSurface(start="frame", inputs="none"),
    }
)


def stepped_surface_for(node_type: NodeType) -> SteppedSurface:
    """The stepped surface of *node_type*; a type outside the table is an error."""
    try:
        return STEPPED_NODE_TYPES[node_type]
    except KeyError:
        raise ValueError(f"Node type {node_type.value!r} does not author steps.") from None


def step_input_names(node_type: NodeType, edge_names: Sequence[str]) -> list[str]:
    """The input names a stepped *node_type*'s steps may reference.

    *edge_names* are the node's incoming edge (or logical) names; they are
    returned as given for an ``edges`` surface and dropped for a ``none``
    surface, whose code runs with only ``df`` in scope.
    """
    surface = stepped_surface_for(node_type)
    return list(edge_names) if surface.inputs == "edges" else []


def is_stepped_config(node_type: NodeType, config: Mapping[str, object]) -> bool:
    """Whether *config* is authored as steps on a node type that supports them."""
    return node_type in STEPPED_NODE_TYPES and isinstance(config.get("steps"), list)


def stepped_surface_allows_input_references(node_type: NodeType) -> bool:
    """Whether a stepped *node_type*'s steps may name its incoming edges.

    Only such a surface needs its step references rewritten when an input is
    renamed (a submodel boundary, an Edge Join insertion, a node rename).
    """
    surface = STEPPED_NODE_TYPES.get(node_type)
    return surface is not None and surface.inputs == "edges"


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
    "pivot",
    "unpivot",
    "free_code",
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
    "matches": "matches",
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
    "quantile",
    "std",
    "var",
    "count",
    "n_unique",
    "first",
    "last",
    "len",
)
#: Window-only aggregates: positional and cumulative values within a partition.
WINDOW_ONLY_AGGREGATIONS: tuple[str, ...] = (
    "row_number",
    "cum_sum",
    "shift",
    "rank",
    "dense_rank",
    "forward_fill",
    "backward_fill",
)
WINDOW_AGGREGATIONS: tuple[str, ...] = (*AGGREGATIONS, *WINDOW_ONLY_AGGREGATIONS)
#: Aggregates a pivot cell may take; ``count`` is non-null values, ``len`` is rows.
PIVOT_AGGREGATIONS: tuple[str, ...] = (
    "sum",
    "mean",
    "min",
    "max",
    "median",
    "first",
    "last",
    "count",
    "len",
)
JOIN_VALIDATE: tuple[str, ...] = ("1:1", "m:1", "1:m", "m:m")
#: Join kinds Polars can validate; semi, anti, right and cross joins refuse ``validate``.
JOIN_VALIDATED_HOW: tuple[str, ...] = ("inner", "left", "full")
JOIN_MAINTAIN_ORDER: tuple[str, ...] = ("none", "left", "right", "left_right", "right_left")
CAST_DTYPES: tuple[str, ...] = (
    "Int8",
    "Int16",
    "Int32",
    "Int64",
    "UInt8",
    "UInt16",
    "UInt32",
    "UInt64",
    "Float32",
    "Float64",
    "String",
    "Boolean",
    "Date",
    "Datetime",
    "Categorical",
)
LITERAL_TYPES: tuple[str, ...] = ("number", "text", "boolean", "date", "null")
#: How deep expressions may nest as operands; a with_column expression is depth 1.
#: A formula chain nests one level per operator, so a dozen terms fit.
MAX_EXPR_DEPTH = 12
_PRECEDENCE: dict[str, int] = {"+": 1, "-": 1, "*": 2, "/": 2, "//": 2, "%": 2, "**": 3}

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
    "weekday": ((), "{r}.dt.weekday()"),
    "offset_by": (("text",), "{r}.dt.offset_by({0})"),
    "total_days": ((), "{r}.dt.total_days()"),
    "replace": (("text", "text"), "{r}.str.replace({0}, {1}, literal=True)"),
    "replace_all": (("text", "text"), "{r}.str.replace_all({0}, {1}, literal=True)"),
    "replace_regex": (("text", "text"), "{r}.str.replace_all({0}, {1})"),
    "slice": (("int", "integer"), "{r}.str.slice({0}, {1})"),
    "split_part": (("text", "integer"), "{r}.str.split({0}).list.get({1}, null_on_oob=True)"),
    "extract": (("text", "integer"), "{r}.str.extract({0}, {1})"),
    "try_cast": (("dtype",), "{r}.cast(pl.{0}, strict=False)"),
}

_STEP_KEYS: dict[str, frozenset[str]] = {
    "source": frozenset({"input"}),
    "filter": frozenset({"match", "conditions"}),
    "with_column": frozenset({"name", "expr"}),
    "select": frozenset({"columns", "dtypes"}),
    "drop": frozenset({"columns", "dtypes"}),
    "rename": frozenset({"renames"}),
    "cast": frozenset({"casts"}),
    "sort": frozenset({"keys", "nullsLast"}),
    "unique": frozenset({"columns", "keep"}),
    "group_by": frozenset({"keys", "aggregations"}),
    "join": frozenset({"input", "how", "leftOn", "rightOn", "suffix", "validate", "maintainOrder"}),
    "concat": frozenset({"inputs", "how"}),
    "fill_null": frozenset({"columns", "fill"}),
    "limit": frozenset({"n"}),
    "variable": frozenset({"name", "value"}),
    "pivot": frozenset({"index", "on", "columns", "values", "agg"}),
    "unpivot": frozenset({"on", "index", "variableName", "valueName"}),
    "free_code": frozenset({"code"}),
}

#: Keys a step may omit; every other key in ``_STEP_KEYS`` is required.
_OPTIONAL_STEP_KEYS: dict[str, frozenset[str]] = {
    "join": frozenset({"validate", "maintainOrder"}),
    "select": frozenset({"dtypes"}),
    "drop": frozenset({"dtypes"}),
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RESERVED_NAMES = frozenset({"df", "pl"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_polars_steps(steps: object) -> list[dict[str, Any]]:
    """Validate the step schema without input-name checks and return the list.

    Field validation does not depend on the start mode, which only governs
    where a ``source`` step may appear at render time.
    """
    return _Renderer(steps, None, "input").steps


def render_polars_steps(
    steps: object,
    input_names: Sequence[str] | None = None,
    *,
    start: StepStart,
) -> RenderedSteps:
    """Render ``steps`` into the Polars function body.

    With ``input_names`` given, every input reference must be one of them;
    without it references are rendered as written. ``start`` says where ``df``
    comes from: ``input`` requires a leading ``source`` step (and refuses an
    empty list); ``frame`` refuses a ``source`` step anywhere and renders an
    empty list to empty code.
    """
    if start not in STEP_STARTS:
        raise ValueError(f"Unknown step start {start!r}; expected one of {STEP_STARTS!r}.")
    return _Renderer(steps, input_names, start).render()


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
        duplicates = sorted(name for name, count in Counter(targets).items() if count > 1)
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


def _needs_parentheses(child_op: object, parent: tuple[str, str] | None) -> bool:
    """Whether a nested formula must be bracketed inside its parent formula.

    A left operand binds first anyway, so it needs brackets only when its
    operator is weaker than the parent's (``(a + b) * c``). A right operand is
    always bracketed: dropping them would re-associate the evaluation
    (``a - (b + c)``, ``a * (b // c)``) or, for floats, change rounding.
    ``**`` is bracketed on both sides. Outside a formula (a function receiver,
    a conditional branch, a value position) a nested formula is always
    bracketed.
    """
    if parent is None or not isinstance(child_op, str):
        return True
    parent_op, side = parent
    if child_op not in _PRECEDENCE or parent_op not in _PRECEDENCE:
        return True
    if "**" in (child_op, parent_op) or side == "right":
        return True
    return _PRECEDENCE[child_op] < _PRECEDENCE[parent_op]


class _Renderer:
    def __init__(self, steps: object, input_names: Sequence[str] | None, start: StepStart) -> None:
        if not isinstance(steps, list):
            raise PolarsStepError("Steps must be a list.")
        self.input_names = None if input_names is None else frozenset(input_names)
        self.start = start
        self.variables: set[str] = set()
        self.index = 0
        self.depth = 0
        # Inside a grouped aggregation's row filter a nested aggregate would
        # mean the group's value, not the frame's, so nesting is refused there.
        self.plain_operands = 0
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
        missing = sorted(_STEP_KEYS[kind] - _OPTIONAL_STEP_KEYS.get(kind, frozenset()) - set(step))
        if missing:
            raise self.fail(f"Missing field(s) {missing!r} for a {kind} step.")
        return dict(step)

    def render(self) -> RenderedSteps:
        if not self.steps:
            if self.start == "frame":
                # The surface already bound df; no steps is simply no code.
                return RenderedSteps(code="", step_lines=())
            raise PolarsStepError("Choose the input to start from.")
        ids = [s["id"] for s in self.steps]
        duplicates = sorted(step_id for step_id, count in Counter(ids).items() if count > 1)
        if duplicates:
            raise PolarsStepError(
                f"Step ids must be unique; {duplicates!r} repeat.",
                step_index=ids.index(duplicates[0]),
            )
        lines: list[str] = []
        step_lines: list[tuple[int, int]] = []
        for index, step in enumerate(self.steps):
            self.index = index
            kind = step["kind"]
            if self.start == "frame":
                if kind == "source":
                    raise self.fail("This node starts from df; remove the start step.")
            elif index == 0 and kind != "source":
                raise self.fail("The first step must choose the input to start from.")
            elif index > 0 and kind == "source":
                raise self.fail("Only the first step can choose the input to start from.")
            start = len(lines) + 1
            lines.extend(getattr(self, f"_render_{kind}")(step).split("\n"))
            step_lines.append((start, len(lines)))
        code = "\n".join(lines)
        if any(step["kind"] == "free_code" for step in self.steps):
            # Also check the combined function scope: e.g. `global df` after
            # the source assignment, or imports valid only at module scope.
            body = "\n".join(f"    {line}" for line in lines)
            try:
                compile(f"def _steps():\n{body}\n", "<polars-steps>", "exec", dont_inherit=True)
            except SyntaxError as exc:
                line = (exc.lineno or 2) - 1
                self.index = next(
                    i for i, (start, end) in enumerate(step_lines) if start <= line <= end
                )
                start = step_lines[self.index][0]
                raise self.fail(f"Invalid Python on line {line - start + 1}: {exc.msg}.") from None
        return RenderedSteps(code=code, step_lines=tuple(step_lines))

    def _render_free_code(self, step: Mapping[str, Any]) -> str:
        code = self._str(step["code"], "Code").replace("\r\n", "\n").replace("\r", "\n").rstrip()
        try:
            tree = ast.parse(code)
            if not tree.body:
                raise self.fail("Write at least one Python statement.")
            # Compile without running anything. Module scope rejects return,
            # yield, await and stray loop control, while allowing local helpers.
            compile(tree, "<polars-step>", "exec", dont_inherit=True)
        except SyntaxError as exc:
            if exc.msg == "'return' outside function":
                raise self.fail("Assign the result to df instead of using return.") from None
            raise self.fail(f"Invalid Python on line {exc.lineno or 1}: {exc.msg}.") from None
        return code

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
        """A list of literal column names (no wildcard or regex patterns)."""
        if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
            raise self.fail(f"{label} must be a list of non-empty strings.")
        if not value and not allow_empty:
            raise self.fail(f"{label} must name at least one column.")
        if len(set(value)) != len(value):
            raise self.fail(f"{label} must not repeat a column.")
        for name in value:
            self._check_column_name(name, label)
        return list(value)

    def _check_column_name(self, name: str, label: str) -> None:
        # Polars reads ``*`` and ``^...$`` as selectors; the lineage model
        # refuses them in these positions, so the step refuses them first.
        if name == "*" or (name.startswith("^") and name.endswith("$")):
            raise self.fail(f"{label} must name a column, not the pattern {name!r}.")

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
        name = self._str(value, label)
        self._check_column_name(name, label)
        return f"pl.col({name!r})"

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
        if literal_type == "null":
            if "value" in operand and value is not None:
                raise self.fail(f"{label} of type null carries no value.")
            return literal_type, "None"
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            raise self.fail(f"{label} must be a date written as YYYY-MM-DD.")
        try:
            _dt.date.fromisoformat(value)
        except ValueError:
            raise self.fail(f"{label} {value!r} is not a real date.") from None
        return literal_type, f"pl.lit({value!r}).str.to_date()"

    def _operand(
        self,
        value: object,
        label: str,
        *,
        expr: bool,
        parent: tuple[str, str] | None = None,
    ) -> str:
        """Render an operand in value position (bare) or expression position.

        ``parent`` names the enclosing formula operator and side (``"left"`` or
        ``"right"``) so a nested formula is parenthesised only where Python's
        left-to-right evaluation would otherwise change it.
        """
        operand = self._object(value, label)
        kind = operand.get("kind")
        if kind == "column":
            self._keys(operand, ("kind", "name"), label)
            return self._column(operand.get("name"), f"{label} column")
        if kind == "literal":
            literal_type, rendered = self._literal(operand, label)
            if literal_type == "date" or (not expr and literal_type != "null"):
                return rendered
            return f"pl.lit({rendered})"
        if kind == "expr":
            # A nested expression; only a binary needs parentheses, every
            # other type is a call chain or an atom.
            if self.plain_operands:
                raise self.fail(f"{label} must be a plain value, column or variable here.")
            self._keys(operand, ("kind", "expr"), label)
            inner = self._object(operand.get("expr"), f"{label} expression")
            rendered_expr = self._expr(inner, f"{label} expression")
            if inner.get("type") != "binary" or _needs_parentheses(inner.get("op"), parent):
                return f"({rendered_expr})" if inner.get("type") == "binary" else rendered_expr
            return rendered_expr
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
            if operator == "matches":
                return f"{column}.str.contains({operand})"
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
            if literal_type == "null":
                raise self.fail(f"{label} cannot include null.")
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

    def _aggregate(
        self,
        column: object,
        agg: object,
        label: str,
        *,
        quantile: object = None,
        where: object = None,
    ) -> str:
        name, call = self._aggregate_call(agg, label, quantile)
        if where is None:
            if name == "len":
                return "pl.len()"
            return f"{self._column(column, f'{label} column')}{call}"
        where_group = self._object(where, f"{label} where")
        self._keys(where_group, ("match", "conditions"), f"{label} where")
        self.plain_operands += 1
        try:
            predicate = self._conditions(where_group, f"{label} where")
        finally:
            self.plain_operands -= 1
        if name == "len":
            return f"({predicate}).sum()"
        return f"{self._column(column, f'{label} column')}.filter({predicate}){call}"

    def _aggregate_call(self, agg: object, label: str, quantile: object) -> tuple[str, str]:
        """Return the aggregate name and its rendered method call."""
        name = self._choice(agg, AGGREGATIONS, f"{label} aggregation")
        if name == "quantile":
            if isinstance(quantile, bool) or not isinstance(quantile, (int, float)):
                raise self.fail(f"{label} quantile needs a number between 0 and 1.")
            if not 0 <= quantile <= 1:
                raise self.fail(f"{label} quantile needs a number between 0 and 1.")
            return name, f".quantile({quantile!r}, interpolation='linear')"
        return name, f".{name}()"

    def _formula_text(self, expr: Mapping[str, Any], label: str) -> None:
        """Accept the editor's formula text annotation: display only, never rendered."""
        if "text" in expr and not isinstance(expr["text"], str):
            raise self.fail(f"{label} formula text must be a string.")

    def _expr(self, value: object, label: str) -> str:
        self.depth += 1
        try:
            if self.depth > MAX_EXPR_DEPTH:
                raise self.fail(
                    f"Expressions nest more than {MAX_EXPR_DEPTH} levels deep; "
                    "compute part of the expression in an earlier step."
                )
            return self._expr_body(value, label)
        finally:
            self.depth -= 1

    def _expr_body(self, value: object, label: str) -> str:
        expr = self._object(value, label)
        kind = expr.get("type")
        if kind == "operand":
            self._keys(expr, ("type", "operand", "text"), label)
            self._formula_text(expr, label)
            return self._operand(expr.get("operand"), f"{label} operand", expr=True)
        if kind == "binary":
            self._keys(expr, ("type", "left", "op", "right", "text"), label)
            self._formula_text(expr, label)
            op = self._choice(expr.get("op"), BINARY_OPERATORS, f"{label} operator")
            left = self._operand(
                expr.get("left"), f"{label} left operand", expr=True, parent=(op, "left")
            )
            right = self._operand(
                expr.get("right"), f"{label} right operand", expr=False, parent=(op, "right")
            )
            return f"{left} {op} {right}"
        if kind == "function":
            self._keys(expr, ("type", "fn", "operand", "args", "text"), label)
            self._formula_text(expr, label)
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
            return self._window(expr, label)
        if kind == "concat":
            self._keys(expr, ("type", "parts", "separator"), label)
            parts = expr.get("parts")
            if not isinstance(parts, list) or len(parts) < 2:
                raise self.fail(f"{label} concat needs at least two parts.")
            separator = expr.get("separator", "")
            if not isinstance(separator, str):
                raise self.fail(f"{label} separator must be text.")
            rendered_parts = [
                self._operand(part, f"{label} part {i + 1}", expr=True)
                for i, part in enumerate(parts)
            ]
            return f"pl.concat_str([{', '.join(rendered_parts)}], separator={separator!r})"
        raise self.fail(f"Unknown expression type {kind!r}.")

    def _window(self, expr: Mapping[str, Any], label: str) -> str:
        self._keys(
            expr, ("type", "agg", "column", "over", "orderBy", "descending", "quantile"), label
        )
        over = self._str_list(expr.get("over"), f"{label} over", allow_empty=True)
        agg = self._choice(expr.get("agg"), WINDOW_AGGREGATIONS, f"{label} aggregation")
        descending = expr.get("descending", False)
        if not isinstance(descending, bool):
            raise self.fail(f"{label} descending must be true or false.")
        if agg == "row_number":
            # The idiom analysts write; the cardinality classifier recognises
            # a range bounded by ``pl.len()`` as one value per row.
            base = "pl.int_range(1, pl.len() + 1)"
        elif agg in AGGREGATIONS:
            base = self._aggregate(expr.get("column"), agg, label, quantile=expr.get("quantile"))
        else:
            column = self._column(expr.get("column"), f"{label} column")
            base = {
                "cum_sum": f"{column}.cum_sum()",
                "shift": f"{column}.shift(1)",
                "rank": f"{column}.rank(method='ordinal', descending={descending!r})",
                "dense_rank": f"{column}.rank(method='dense', descending={descending!r})",
                "forward_fill": f"{column}.fill_null(strategy='forward')",
                "backward_fill": f"{column}.fill_null(strategy='backward')",
            }[agg]
        order_by = expr.get("orderBy")
        if order_by is None:
            if not over:
                return base
            return f"{base}.over({over!r})"
        if not isinstance(order_by, list) or not order_by:
            raise self.fail(f"{label} order must list at least one column.")
        if not over:
            raise self.fail(
                f"{label} needs partition columns to order within; sort the frame instead."
            )
        columns: list[str] = []
        flags: list[bool] = []
        for i, entry in enumerate(order_by):
            entry = self._object(entry, f"{label} order {i + 1}")
            self._keys(entry, ("column", "descending"), f"{label} order {i + 1}")
            order_column = self._str(entry.get("column"), f"{label} order {i + 1} column")
            self._check_column_name(order_column, f"{label} order {i + 1} column")
            columns.append(order_column)
            flags.append(
                self._bool(entry.get("descending", False), f"{label} order {i + 1} descending")
            )
        if len(set(flags)) > 1:
            raise self.fail(f"{label} order columns must all share one direction.")
        return f"{base}.over({over!r}, order_by={columns!r}, descending={flags[0]!r})"

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
        if expected == "int":
            raw = operand.get("value")
            if literal_type != "number" or not isinstance(raw, int):
                raise self.fail(f"{label} must be a whole number.")
            return rendered
        if expected == "number":
            if literal_type != "number":
                raise self.fail(f"{label} must be a number.")
            return rendered
        if expected == "text":
            if literal_type != "text":
                raise self.fail(f"{label} must be text.")
            return rendered
        if expected == "scalar":
            if literal_type in ("date", "null"):
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

    def _dtype_selectors(self, step: Mapping[str, Any], columns: list[str]) -> list[str]:
        """Render one ``pl.col(<dtype>)`` per chosen dtype, minus the named columns."""
        if "dtypes" not in step:
            return []
        raw = step["dtypes"]
        if not isinstance(raw, list) or not all(isinstance(d, str) for d in raw):
            raise self.fail("Column types must be a list of type names.")
        dtypes = [self._choice(d, CAST_DTYPES, "Column type") for d in raw]
        if len(set(dtypes)) != len(dtypes):
            raise self.fail("Column types must not repeat a type.")
        exclude = "".join(f"{c!r}, " for c in columns).rstrip(", ")
        suffix = f".exclude({exclude})" if columns else ""
        return [f"pl.col(pl.{dtype}){suffix}" for dtype in dtypes]

    def _render_select(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=True)
        selectors = self._dtype_selectors(step, columns)
        if not columns and not selectors:
            raise self.fail("Name at least one column or column type to keep.")
        if not selectors:
            return f"df = df.select({columns!r})"
        parts = [repr(c) for c in columns] + selectors
        return f"df = df.select([{', '.join(parts)}])"

    def _render_drop(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=True)
        selectors = self._dtype_selectors(step, columns)
        if not columns and not selectors:
            raise self.fail("Name at least one column or column type to drop.")
        if not selectors:
            return f"df = df.drop({columns!r})"
        parts = ([repr(columns)] if columns else []) + selectors
        return f"df = df.drop({', '.join(parts)})"

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
            sort_column = self._str(entry.get("column"), f"Sort key {i + 1} column")
            self._check_column_name(sort_column, f"Sort key {i + 1} column")
            columns.append(sort_column)
            descending.append(self._bool(entry.get("descending"), f"Sort key {i + 1} descending"))
        nulls_last = self._bool(step["nullsLast"], "Nulls last")
        return f"df = df.sort({columns!r}, descending={descending!r}, nulls_last={nulls_last!r})"

    def _render_unique(self, step: Mapping[str, Any]) -> str:
        columns = self._str_list(step["columns"], "Columns", allow_empty=True)
        keep = self._choice(step["keep"], ("first", "last", "any", "none"), "Keep")
        subset = repr(columns) if columns else "None"
        return f"df = df.unique(subset={subset}, keep={keep!r}, maintain_order=True)"

    def _render_group_by(self, step: Mapping[str, Any]) -> str:
        keys = self._str_list(step["keys"], "Group keys", allow_empty=True)
        aggregations = step["aggregations"]
        if not isinstance(aggregations, list) or not aggregations:
            raise self.fail("Add at least one aggregation.")
        rendered: list[str] = []
        names: set[str] = set()
        for i, entry in enumerate(aggregations):
            entry = self._object(entry, f"Aggregation {i + 1}")
            label = f"Aggregation {i + 1}"
            self._keys(
                entry, ("column", "agg", "name", "where", "quantile", "dtype", "suffix"), label
            )
            if "dtype" in entry:
                rendered.append(self._dtype_aggregate(entry, label))
                continue
            if "suffix" in entry:
                raise self.fail(f"{label} takes a suffix only when it aggregates a column type.")
            name = self._str(entry.get("name"), f"{label} name")
            if name in names:
                raise self.fail(f"Aggregation name {name!r} is used more than once.")
            names.add(name)
            aggregate = self._aggregate(
                entry.get("column"),
                entry.get("agg"),
                label,
                quantile=entry.get("quantile"),
                where=entry.get("where"),
            )
            rendered.append(f"{aggregate}.alias({name!r})")
        if not keys:
            # A whole-frame summary: one row of aggregates.
            return f"df = df.select([{', '.join(rendered)}])"
        return f"df = df.group_by({keys!r}, maintain_order=True).agg([{', '.join(rendered)}])"

    def _dtype_aggregate(self, entry: Mapping[str, Any], label: str) -> str:
        """Aggregate every column of one dtype, naming outputs by suffix."""
        for key in ("column", "name", "where"):
            if key in entry:
                raise self.fail(f"{label} aggregates a column type, so it takes no {key}.")
        dtype = self._choice(entry.get("dtype"), CAST_DTYPES, f"{label} column type")
        suffix = self._str(entry.get("suffix"), f"{label} suffix")
        agg = self._choice(entry.get("agg"), AGGREGATIONS, f"{label} aggregation")
        if agg == "len":
            raise self.fail(f"{label} counts rows, which needs no column type.")
        _, call = self._aggregate_call(agg, label, entry.get("quantile"))
        return f"pl.col(pl.{dtype}){call}.name.suffix({suffix!r})"

    def _render_pivot(self, step: Mapping[str, Any]) -> str:
        """A fixed-column pivot lowered to a group-by the lineage model proves.

        Each output column aggregates the ``values`` column over the rows
        whose ``on`` value equals the entry's value, so the result matches
        ``pivot(on_columns=...)`` cell for cell (an empty cell is 0 for
        ``sum``/``len``/``count`` and null otherwise, in both).
        """
        index = self._str_list(step["index"], "Pivot index", allow_empty=False)
        on = self._column(step["on"], "Pivot on column")
        values = self._column(step["values"], "Pivot values column")
        agg = self._choice(step["agg"], PIVOT_AGGREGATIONS, "Pivot aggregation")
        entries = step["columns"]
        if not isinstance(entries, list) or not entries:
            raise self.fail("Add at least one pivot column.")
        rendered: list[str] = []
        names: set[str] = set()
        seen_values: set[object] = set()
        types: set[str] = set()
        for i, raw in enumerate(entries):
            entry = self._object(raw, f"Pivot column {i + 1}")
            self._keys(entry, ("value", "name"), f"Pivot column {i + 1}")
            name = self._str(entry.get("name"), f"Pivot column {i + 1} name")
            if name in names or name in index:
                raise self.fail(f"Pivot column name {name!r} is used more than once.")
            names.add(name)
            operand = self._object(entry.get("value"), f"Pivot column {i + 1} value")
            if operand.get("kind") != "literal":
                raise self.fail(f"Pivot column {i + 1} value must be a plain value.")
            literal_type, text = self._literal(operand, f"Pivot column {i + 1} value")
            if literal_type == "null":
                raise self.fail(f"Pivot column {i + 1} value cannot be null.")
            types.add(literal_type)
            # Compare typed values, not spellings: Python already equates 1 and
            # 1.0 and the signed zeros while keeping large integers exact.
            key = (literal_type, operand.get("value"))
            if key in seen_values:
                raise self.fail(f"Pivot column {i + 1} repeats the value {text}.")
            seen_values.add(key)
            cell = f"{values}.filter({on} == {text})"
            call = {"count": ".count()", "len": ".len()"}.get(agg, f".{agg}()")
            rendered.append(f"{cell}{call}.alias({name!r})")
        if len(types) != 1:
            raise self.fail("Pivot column values must all be the same type.")
        return f"df = df.group_by({index!r}, maintain_order=True).agg([{', '.join(rendered)}])"

    def _render_unpivot(self, step: Mapping[str, Any]) -> str:
        on = self._str_list(step["on"], "Unpivot columns", allow_empty=False)
        index = self._str_list(step["index"], "Unpivot index", allow_empty=True)
        variable = self._str(step["variableName"], "Unpivot name column")
        value = self._str(step["valueName"], "Unpivot value column")
        overlap = sorted(set(on) & set(index))
        if overlap:
            raise self.fail(f"Columns {overlap!r} cannot be both unpivoted and kept as index.")
        if variable == value:
            raise self.fail("The name column and the value column need different names.")
        for name in (variable, value):
            if name in index:
                raise self.fail(f"Output column {name!r} is already an index column.")
        return (
            f"df = df.unpivot(on={on!r}, index={index!r}, "
            f"variable_name={variable!r}, value_name={value!r})"
        )

    def _render_join(self, step: Mapping[str, Any]) -> str:
        other = self._input(step["input"], "Join input")
        how = self._choice(step["how"], JOIN_HOW, "Join type")
        left_on = self._str_list(step["leftOn"], "Left keys", allow_empty=True)
        right_on = self._str_list(step["rightOn"], "Right keys", allow_empty=True)
        suffix = self._str(step["suffix"], "Suffix")
        extra = ""
        if "validate" in step:
            validate = self._choice(step["validate"], JOIN_VALIDATE, "Join validation")
            if how not in JOIN_VALIDATED_HOW:
                raise self.fail("Only inner, left and full joins can validate their keys.")
            extra += f", validate={validate!r}"
        if "maintainOrder" in step:
            order = self._choice(step["maintainOrder"], JOIN_MAINTAIN_ORDER, "Join order")
            extra += f", maintain_order={order!r}"
        if how == "cross":
            if left_on or right_on:
                raise self.fail("A cross join takes no key columns.")
            return f"df = df.join({other}, how='cross', suffix={suffix!r}{extra})"
        if not left_on or len(left_on) != len(right_on):
            raise self.fail("Left and right key lists must be non-empty and the same length.")
        return (
            f"df = df.join({other}, left_on={left_on!r}, right_on={right_on!r}, "
            f"how={how!r}, suffix={suffix!r}{extra})"
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
        if literal_type in ("date", "null"):
            raise self.fail("A variable holds a number, text or true/false.")
        self.variables.add(name)
        return f"{name} = {rendered}"
