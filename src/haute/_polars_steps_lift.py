"""Rebuild a stepped Polars node's steps from its code (READ-21, READ-22).

This is the inverse of :func:`haute._polars_steps.render_node_steps`.
:func:`lift_steps` reads a body and returns the steps that render it;
:func:`canonical_steps` says, from the steps alone, which steps those are. The
specification records the canonical form and each ambiguous form
(``specs/codegen/structured-syntax-boundary.md``, Rebuilding steps from code).

How a body is read:

- The standard-library AST splits the body into its top-level statements. The
  lines between them are comments and blank lines, and each belongs to the
  statement below it.
- A statement becomes a step only when that step, placed after the steps
  rebuilt before it, renders exactly the statement's text. The comment and blank
  lines above it become the step's ``comment``. The reader proposes a step from
  the statement's syntax tree and the renderer confirms it, so nothing is ever
  unparsed over the code.
- Every other statement is code. A run of code statements, with the comments
  and blank lines among them, is one ``free_code`` step.
- The comment and blank lines after the last statement are a ``free_code`` step
  of their own, or join the code step before them.

Each rebuilt step gets an id made of its kind and its place in the list (such
as ``filter-3``), so the list renders as it stands and holds nothing the code
does not.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal

from haute._polars_steps import (
    _OPERAND_ARGUMENT_LITERALS,
    _STRING_OPERATORS,
    AGGREGATIONS,
    COMMENT_FIELD,
    DATE_PARSE_TARGETS,
    FUNCTIONS,
    LITERAL_TYPES,
    PolarsStepError,
    _code_bound_names,
    _decimal_text,
    render_node_steps,
    stepped_surface_for,
)
from haute._types import GLOBAL_CONSTANTS_NAME, NodeType

__all__ = ["StepLiftError", "canonical_steps", "lift_steps"]

Step = dict[str, Any]


class StepLiftError(ValueError):
    """Code that cannot be rebuilt as steps which render back to exactly that code."""


class _NoMatchError(Exception):
    """A statement, or part of one, is not what any step renders."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lift_steps(node_type: NodeType, code: str) -> list[Step]:
    """Rebuild the steps of a stepped *node_type* from *code*, its body.

    Each statement a step renders becomes that step, keeping the comment above
    it; each run of other statements, with the comments and blank lines among
    them, becomes one ``free_code`` step. The result renders to exactly *code*
    without its trailing newlines, or :class:`StepLiftError` is raised.
    """
    body = code.rstrip("\n")
    steps = _assemble(node_type, _units(body), partial(_recognised, _Reader()))
    try:
        rendered = render_node_steps(node_type, steps).code
    except PolarsStepError as exc:
        raise StepLiftError(f"The code does not rebuild as steps: {exc}") from None
    if rendered != body:
        raise StepLiftError("The rebuilt steps do not render the code exactly.")
    return steps


def canonical_steps(node_type: NodeType, steps: object) -> list[Step]:
    """The canonical form of a stepped *node_type*'s *steps*: what their code rebuilds as.

    A structured step's canonical form comes from the rules the specification
    records, not from reading its code back; the rendering only settles which
    statement each step owns, which comment lines sit above it, and whether the
    step still stands after the steps before it. A ``free_code`` step's
    statements are read as :func:`lift_steps` reads them. Invalid steps raise
    :class:`~haute._polars_steps.PolarsStepError`.
    """
    rendered = render_node_steps(node_type, steps)
    assert isinstance(steps, list)
    owner: dict[int, Mapping[str, Any]] = {}
    for step, (start, end) in zip(steps, rendered.step_lines, strict=True):
        for line in range(start, end + 1):
            owner[line] = step
    reader = _Reader()

    def choose(unit: _Unit, context: _Context) -> Step | None:
        step = owner[unit.first]
        if step["kind"] == "free_code":
            return _recognised(reader, unit, context)
        normal = _with_comment(_normal_step(step, context), unit)
        return normal if context.rendering(normal) == unit.text else None

    return _assemble(node_type, _units(rendered.code), choose)


# ---------------------------------------------------------------------------
# Statements and the comments above them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    """One top-level statement of a body with the comment and blank lines above it.

    ``first`` is the statement's first line (one-based); ``above`` the comment
    and blank lines between it and the statement before; ``own`` its own lines;
    ``nodes`` the statements on those lines; ``node`` the statement when it
    stands alone on its lines.
    """

    first: int
    above: tuple[str, ...]
    own: tuple[str, ...]
    nodes: tuple[ast.stmt, ...]

    @property
    def node(self) -> ast.stmt | None:
        return self.nodes[0] if len(self.nodes) == 1 else None

    @property
    def lines(self) -> tuple[str, ...]:
        return (*self.above, *self.own)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def comment(self) -> str | None:
        """The ``comment`` a step rebuilt from this unit keeps, or None for none."""
        return "".join(f"{line}\n" for line in self.above) or None


def _units(code: str) -> tuple[list[_Unit], list[str]]:
    """*code*'s statements, each with the lines above it, and the lines after the last."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        message = f"The code is not valid Python: {exc.msg} (line {exc.lineno})."
        raise StepLiftError(message) from None
    lines = code.split("\n") if code else []
    spans: list[tuple[int, int, list[ast.stmt]]] = []
    for statement in tree.body:
        decorators = getattr(statement, "decorator_list", [])
        start = min([statement.lineno, *(decorator.lineno for decorator in decorators)])
        end = statement.end_lineno or statement.lineno
        if spans and start <= spans[-1][1]:
            # Statements sharing a line (``a = 1; b = 2``) are read together, as code.
            spans[-1] = (spans[-1][0], max(spans[-1][1], end), [*spans[-1][2], statement])
        else:
            spans.append((start, end, [statement]))
    units: list[_Unit] = []
    cursor = 1
    for start, end, nodes in spans:
        above = _trivia(lines[cursor - 1 : start - 1])
        units.append(_Unit(start, tuple(above), tuple(lines[start - 1 : end]), tuple(nodes)))
        cursor = end + 1
    return units, _trivia(lines[cursor - 1 :])


def _trivia(lines: Sequence[str]) -> list[str]:
    """Lines between statements, which only comments and blank lines can be."""
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#"):
            raise StepLiftError(f"Cannot place the line {line!r} between statements.")
    return list(lines)


def _with_comment(step: Step, unit: _Unit) -> Step:
    """*step* keeping the comment lines above *unit*'s statement, and no other."""
    found = {key: value for key, value in step.items() if key != COMMENT_FIELD}
    comment = unit.comment
    if comment is not None:
        found[COMMENT_FIELD] = comment
    return found


# ---------------------------------------------------------------------------
# Reading a statement's syntax tree as a step
# ---------------------------------------------------------------------------

_BINARY: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
}
_COMPARISONS: dict[type[ast.cmpop], str] = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Gt: "gt",
    ast.GtE: "ge",
    ast.Lt: "lt",
    ast.LtE: "le",
}
_STRING_TESTS = ("starts_with", "ends_with")
_WINDOW_FILLS = {"forward": "forward_fill", "backward": "backward_fill"}
_RANKS = {"ordinal": "rank", "dense": "dense_rank"}
_PARSE_DTYPES = {parse: dtype for dtype, parse in DATE_PARSE_TARGETS.items()}
_PLACEHOLDER = "_haute_"
_RECEIVER = f"{_PLACEHOLDER}receiver"


def _argument(index: int) -> str:
    return f"{_PLACEHOLDER}argument_{index}"


@dataclass(frozen=True)
class _Call:
    """A method call ``receiver.method(*args, **keywords)``."""

    receiver: ast.expr
    args: list[ast.expr]
    keywords: dict[str, ast.expr]

    def keyword(self, name: str) -> ast.expr:
        if name not in self.keywords:
            raise _NoMatchError
        return self.keywords[name]


def _is_method(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    )


def _method(node: ast.AST, name: str) -> _Call:
    """*node* as a call of method *name*, or no match."""
    if not _is_method(node, name):
        raise _NoMatchError
    assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    if any(k.arg is None for k in node.keywords) or any(
        isinstance(arg, ast.Starred) for arg in node.args
    ):
        raise _NoMatchError
    return _Call(node.func.value, list(node.args), {str(k.arg): k.value for k in node.keywords})


def _pl(node: ast.AST, name: str) -> _Call:
    """*node* as a call of ``pl.<name>``, or no match."""
    call = _method(node, name)
    if not (isinstance(call.receiver, ast.Name) and call.receiver.id == "pl"):
        raise _NoMatchError
    return call


def _namespace(node: ast.AST, name: str, namespace: str) -> _Call:
    """*node* as ``<receiver>.<namespace>.<name>(...)``, with the receiver before the namespace."""
    call = _method(node, name)
    if not (isinstance(call.receiver, ast.Attribute) and call.receiver.attr == namespace):
        raise _NoMatchError
    return _Call(call.receiver.value, call.args, call.keywords)


def _only(call: _Call, args: int = 0, keywords: Sequence[str] = ()) -> _Call:
    """*call*, when it has *args* positional arguments and exactly the *keywords*."""
    if len(call.args) != args or set(call.keywords) != set(keywords):
        raise _NoMatchError
    return call


def _value(node: ast.AST) -> object:
    """A literal's value: a constant, or a negated number."""
    if isinstance(node, ast.Constant):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
    ):
        number = node.operand.value
        if type(number) in (int, float):
            assert isinstance(number, int | float)
            return -number
    raise _NoMatchError


def _text(node: ast.AST) -> str:
    value = _value(node)
    if not isinstance(value, str):
        raise _NoMatchError
    return value


def _flag(node: ast.AST) -> bool:
    value = _value(node)
    if not isinstance(value, bool):
        raise _NoMatchError
    return value


def _number(node: ast.AST) -> int | float:
    value = _value(node)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _NoMatchError
    return value


def _whole(node: ast.AST) -> int:
    value = _number(node)
    if not isinstance(value, int):
        raise _NoMatchError
    return value


def _texts(node: ast.AST) -> list[str]:
    if not isinstance(node, ast.List):
        raise _NoMatchError
    return [_text(item) for item in node.elts]


def _name(node: ast.AST) -> str:
    if not isinstance(node, ast.Name):
        raise _NoMatchError
    return node.id


def _literal(literal_type: str, value: object) -> Step:
    return {"kind": "literal", "type": literal_type, "value": value}


def _bare_literal(node: ast.AST) -> Step:
    """A number, text or true/false written as itself."""
    value = _value(node)
    if isinstance(value, bool):
        return _literal("boolean", value)
    if isinstance(value, int | float):
        return _literal("number", value)
    if isinstance(value, str):
        return _literal("text", value)
    raise _NoMatchError


def _column(node: ast.AST) -> str:
    """The name in ``pl.col("<name>")``."""
    return _text(_only(_pl(node, "col"), 1).args[0])


def _dtype(node: ast.AST) -> str:
    """The type name in ``pl.<Type>``."""
    if not (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "pl"
    ):
        raise _NoMatchError
    return node.attr


def _date(node: ast.AST) -> str:
    """The text of a date literal, ``pl.lit("<date>").str.to_date()``."""
    call = _only(_namespace(node, "to_date", "str"))
    return _text(_only(_pl(call.receiver, "lit"), 1).args[0])


def _constant(node: ast.AST) -> Step:
    """A global constant's read, ``global_constants.<name>``."""
    if not (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == GLOBAL_CONSTANTS_NAME
    ):
        raise _NoMatchError
    return {"kind": "constant", "name": node.attr}


def _attempt(read: Callable[[], Step]) -> Step | None:
    try:
        return read()
    except _NoMatchError:
        return None


def _member_kind(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "text"
    raise _NoMatchError


def _decimal_value(text: str) -> int | float:
    """The number a decimal membership value's text renders from.

    A whole number reads as an integer, unless only a float renders its text
    (``-0``); any other text reads as a float.
    """
    choices: list[int | float] = [float(text)] if "." in text else [int(text), float(text)]
    for choice in choices:
        if _decimal_text(choice) == text:
            return choice
    raise _NoMatchError


def _unify(pattern: ast.AST, node: ast.AST, bound: dict[str, ast.AST | str]) -> bool:
    """Whether *node* has the shape of *pattern*, binding the pattern's placeholders.

    A placeholder is a name, or an attribute name after ``pl.``, spelled
    ``_haute_...`` in a function's template. A placeholder bound twice must bind
    the same syntax both times: ``divide`` names its divisor twice.
    """
    if isinstance(pattern, ast.Name) and pattern.id.startswith(_PLACEHOLDER):
        earlier = bound.setdefault(pattern.id, node)
        return earlier is node or (
            isinstance(earlier, ast.AST) and ast.dump(earlier) == ast.dump(node)
        )
    if isinstance(pattern, ast.Attribute) and pattern.attr.startswith(_PLACEHOLDER):
        if not isinstance(node, ast.Attribute):
            return False
        if bound.setdefault(pattern.attr, node.attr) != node.attr:
            return False
        return _unify(pattern.value, node.value, bound)
    if type(pattern) is not type(node):
        return False
    for name, expected in ast.iter_fields(pattern):
        if name == "ctx":
            continue
        actual = getattr(node, name, None)
        if isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                return False
            if not all(_same_field(e, a, bound) for e, a in zip(expected, actual, strict=True)):
                return False
        elif not _same_field(expected, actual, bound):
            return False
    return True


def _same_field(expected: object, actual: object, bound: dict[str, ast.AST | str]) -> bool:
    if isinstance(expected, ast.AST):
        return isinstance(actual, ast.AST) and _unify(expected, actual, bound)
    # ``0 == 0.0 == False``: a template's literal matches only its own type.
    return type(expected) is type(actual) and expected == actual


def _function_patterns() -> tuple[tuple[str, ast.expr, tuple[str, ...]], ...]:
    """Each function's rendering template as a syntax tree with placeholders."""
    patterns: list[tuple[str, ast.expr, tuple[str, ...]]] = []
    for fn, (argument_types, template) in FUNCTIONS.items():
        names = [_argument(index) for index in range(len(argument_types))]
        source = template.format(*names, r=_RECEIVER)
        patterns.append((fn, ast.parse(source, mode="eval").body, argument_types))
    return tuple(patterns)


_FUNCTION_PATTERNS = _function_patterns()


class _Reader:
    """Proposes the steps a statement may render from, read from its syntax tree.

    A reading raises :class:`_NoMatchError` for syntax no step renders. A proposal is
    only a candidate: the renderer decides whether it renders the statement's
    text exactly, and the first candidate that does is the step.
    """

    def candidates(self, statement: ast.stmt) -> list[Step]:
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            return []
        target = statement.targets[0].id
        value = statement.value
        if target != "df":
            variable = _attempt(partial(self._variable, target, value))
            return [] if variable is None else [variable]
        if isinstance(value, ast.Name):
            return [{"kind": "source", "input": value.id}]
        found = (_attempt(read) for read in self._readers(value))
        return [step for step in found if step is not None]

    def _readers(self, value: ast.expr) -> list[Callable[[], Step]]:
        if _is_method(value, "concat"):
            return [partial(self._concat, value)]
        if _is_method(value, "agg"):
            # A pivot renders as a group-by; where both render the code, it is the pivot.
            return [partial(self._pivot, value), partial(self._group_by, value)]
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)):
            return []
        readers: dict[str, list[Callable[[_Call], Step]]] = {
            "filter": [self._filter],
            "with_columns": [self._with_columns],
            "select": [self._select, self._summary],
            "drop": [self._drop],
            "rename": [self._rename],
            "sort": [self._sort],
            "unique": [self._unique],
            "join": [self._join],
            "head": [self._limit],
            "unpivot": [self._unpivot],
        }
        method = value.func.attr
        try:
            call = _method(value, method)
            if _name(call.receiver) != "df":
                return []
        except _NoMatchError:
            return []
        return [partial(read, call) for read in readers.get(method, [])]

    # -- steps -----------------------------------------------------------

    def _variable(self, name: str, value: ast.expr) -> Step:
        return {"kind": "variable", "name": name, "value": _bare_literal(value)}

    def _filter(self, call: _Call) -> Step:
        return {"kind": "filter", **self._group(_only(call, 1).args[0])}

    def _with_columns(self, call: _Call) -> Step:
        if not call.args or call.keywords:
            raise _NoMatchError
        first = call.args[0]
        if len(call.args) == 1 and _is_method(first, "alias"):
            alias = _only(_method(first, "alias"), 1)
            return {
                "kind": "with_column",
                "name": _text(alias.args[0]),
                "expr": self._expr(alias.receiver),
            }
        if len(call.args) == 1 and _is_method(first, "fill_null"):
            return self._fill_null(_method(first, "fill_null"))
        return self._cast(call.args)

    def _fill_null(self, call: _Call) -> Step:
        if _is_method(call.receiver, "all"):
            _only(_pl(call.receiver, "all"))
            columns: list[str] = []
        else:
            target = _pl(call.receiver, "col")
            if target.keywords or not target.args:
                raise _NoMatchError
            columns = [_text(arg) for arg in target.args]
        if call.keywords:
            strategy = _text(_only(call, 0, ("strategy",)).keywords["strategy"])
            fill: Step = {"kind": "strategy", "strategy": strategy}
        else:
            fill = {"kind": "value", "value": self._operand(_only(call, 1).args[0], expr=False)}
        return {"kind": "fill_null", "columns": columns, "fill": fill}

    def _cast(self, nodes: Sequence[ast.expr]) -> Step:
        casts: list[Step] = []
        strict: set[bool] = set()
        for node in nodes:
            call, entry = self._cast_entry(node)
            if len(call.args) != 1 or set(call.keywords) - {"strict"}:
                raise _NoMatchError
            if "strict" in call.keywords and _flag(call.keywords["strict"]):
                raise _NoMatchError  # a strict cast is written without the keyword
            strict.add("strict" not in call.keywords)
            casts.append(entry)
        if len(strict) != 1:
            raise _NoMatchError
        return {"kind": "cast", "casts": casts, "strict": strict.pop()}

    def _cast_entry(self, node: ast.expr) -> tuple[_Call, Step]:
        if _is_method(node, "cast"):
            call = _method(node, "cast")
            if not call.args:
                raise _NoMatchError
            return call, {"column": _column(call.receiver), "dtype": _dtype(call.args[0])}
        for parse, dtype in _PARSE_DTYPES.items():
            if _is_method(node, parse):
                call = _namespace(node, parse, "str")
                if not call.args:
                    raise _NoMatchError
                return call, {
                    "column": _column(call.receiver),
                    "dtype": dtype,
                    "format": _text(call.args[0]),
                }
        raise _NoMatchError

    def _columns_and_types(self, call: _Call) -> Step:
        if call.keywords:
            raise _NoMatchError
        columns: list[str] = []
        dtypes: list[str] = []
        for node in call.args:
            if isinstance(node, ast.Constant):
                columns.append(_text(node))
                continue
            selector = _method(node, "exclude").receiver if _is_method(node, "exclude") else node
            dtypes.append(_dtype(_only(_pl(selector, "col"), 1).args[0]))
        found: Step = {"columns": columns}
        if dtypes:
            found["dtypes"] = dtypes
        return found

    def _select(self, call: _Call) -> Step:
        return {"kind": "select", **self._columns_and_types(call)}

    def _drop(self, call: _Call) -> Step:
        return {"kind": "drop", **self._columns_and_types(call)}

    def _rename(self, call: _Call) -> Step:
        mapping = _only(call, 1).args[0]
        if not isinstance(mapping, ast.Dict):
            raise _NoMatchError
        renames: list[Step] = []
        for key, value in zip(mapping.keys, mapping.values, strict=True):
            if key is None:
                raise _NoMatchError
            renames.append({"from": _text(key), "to": _text(value)})
        return {"kind": "rename", "renames": renames}

    def _sort(self, call: _Call) -> Step:
        if set(call.keywords) - {"descending", "nulls_last", "maintain_order"}:
            raise _NoMatchError
        columns = [_text(arg) for arg in call.args]
        raw = call.keywords.get("descending")
        if raw is None:
            descending = [False] * len(columns)
        elif isinstance(raw, ast.List):
            descending = [_flag(item) for item in raw.elts]
        else:
            descending = [_flag(raw)] * len(columns)
        if len(descending) != len(columns):
            raise _NoMatchError
        nulls_last = call.keywords.get("nulls_last")
        return {
            "kind": "sort",
            "keys": [
                {"column": column, "descending": flag}
                for column, flag in zip(columns, descending, strict=True)
            ],
            "nullsLast": False if nulls_last is None else _flag(nulls_last),
        }

    def _unique(self, call: _Call) -> Step:
        if call.args or set(call.keywords) - {"subset", "keep", "maintain_order"}:
            raise _NoMatchError
        subset = call.keywords.get("subset")
        return {
            "kind": "unique",
            "columns": [] if subset is None else _texts(subset),
            "keep": _text(call.keyword("keep")),
        }

    def _join(self, call: _Call) -> Step:
        known = {"left_on", "right_on", "how", "suffix", "validate", "maintain_order"}
        if len(call.args) != 1 or set(call.keywords) - known:
            raise _NoMatchError
        step: Step = {
            "kind": "join",
            "input": _name(call.args[0]),
            "how": _text(call.keyword("how")),
            "leftOn": _texts(call.keywords["left_on"]) if "left_on" in call.keywords else [],
            "rightOn": _texts(call.keywords["right_on"]) if "right_on" in call.keywords else [],
            "suffix": _text(call.keyword("suffix")),
            "maintainOrder": _text(call.keyword("maintain_order")),
        }
        if "validate" in call.keywords:
            step["validate"] = _text(call.keywords["validate"])
        return step

    def _concat(self, value: ast.expr) -> Step:
        call = _only(_pl(value, "concat"), 1, ("how",))
        frames = call.args[0]
        if not isinstance(frames, ast.List) or not frames.elts or _name(frames.elts[0]) != "df":
            raise _NoMatchError
        return {
            "kind": "concat",
            "inputs": [_name(frame) for frame in frames.elts[1:]],
            "how": _text(call.keywords["how"]),
        }

    def _limit(self, call: _Call) -> Step:
        return {"kind": "limit", "n": _whole(_only(call, 1).args[0])}

    def _unpivot(self, call: _Call) -> Step:
        keywords = _only(call, 0, ("on", "index", "variable_name", "value_name")).keywords
        return {
            "kind": "unpivot",
            "on": _texts(keywords["on"]),
            "index": _texts(keywords["index"]),
            "variableName": _text(keywords["variable_name"]),
            "valueName": _text(keywords["value_name"]),
        }

    def _grouping(self, value: ast.expr) -> tuple[list[str], list[ast.expr]]:
        """The keys and aggregate nodes of ``df.group_by(...).agg(...)``."""
        agg = _method(value, "agg")
        group = _method(agg.receiver, "group_by")
        if (
            agg.keywords
            or not agg.args
            or not group.args
            or set(group.keywords) != {"maintain_order"}
            or _name(group.receiver) != "df"
        ):
            raise _NoMatchError
        return [_text(key) for key in group.args], agg.args

    def _group_by(self, value: ast.expr) -> Step:
        keys, nodes = self._grouping(value)
        return {"kind": "group_by", "keys": keys, "aggregations": self._aggregations(nodes)}

    def _summary(self, call: _Call) -> Step:
        """``df.select(...)`` of aggregates: a group-by with no keys."""
        if call.keywords or not call.args:
            raise _NoMatchError
        return {"kind": "group_by", "keys": [], "aggregations": self._aggregations(call.args)}

    def _pivot(self, value: ast.expr) -> Step:
        """A group-by whose every aggregate is one ``on`` value's cell, read as a pivot."""
        keys, nodes = self._grouping(value)
        shapes: set[tuple[str, str, str]] = set()
        columns: list[Step] = []
        for node in nodes:
            alias = _only(_method(node, "alias"), 1)
            cell = alias.receiver
            if not (isinstance(cell, ast.Call) and isinstance(cell.func, ast.Attribute)):
                raise _NoMatchError
            agg = cell.func.attr
            where = _only(_method(_only(_method(cell, agg)).receiver, "filter"), 1)
            test = where.args[0]
            if not (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
            ):
                raise _NoMatchError
            pivot_value = self._operand(test.comparators[0], expr=False)
            if pivot_value["kind"] != "literal":
                raise _NoMatchError
            shapes.add((_column(where.receiver), _column(test.left), agg))
            columns.append({"value": pivot_value, "name": _text(alias.args[0])})
        if len(shapes) != 1:
            raise _NoMatchError
        ((values, on, agg),) = shapes
        return {
            "kind": "pivot",
            "index": keys,
            "on": on,
            "values": values,
            "agg": agg,
            "columns": columns,
        }

    def _aggregations(self, nodes: Sequence[ast.expr]) -> list[Step]:
        return [self._aggregation(node) for node in nodes]

    def _aggregation(self, node: ast.expr) -> Step:
        if _is_method(node, "suffix"):
            suffix = _only(_method(node, "suffix"), 1)
            named = suffix.receiver
            if not (isinstance(named, ast.Attribute) and named.attr == "name"):
                raise _NoMatchError
            receiver, agg, quantile = self._aggregate(named.value)
            by_type: Step = {
                "dtype": _dtype(_only(_pl(receiver, "col"), 1).args[0]),
                "agg": agg,
                "suffix": _text(suffix.args[0]),
            }
            if quantile is not None:
                by_type["quantile"] = quantile
            return by_type
        alias = _only(_method(node, "alias"), 1)
        name = _text(alias.args[0])
        if _is_method(alias.receiver, "len"):
            _only(_pl(alias.receiver, "len"))
            return {"agg": "len", "name": name}
        receiver, agg, quantile = self._aggregate(alias.receiver)
        counted = self._counted(receiver, agg)
        if counted is not None:
            return {"agg": "len", "name": name, "where": counted}
        entry: Step = {"agg": agg, "name": name}
        if _is_method(receiver, "filter"):
            where = _only(_method(receiver, "filter"), 1)
            entry["where"] = self._group(where.args[0])
            receiver = where.receiver
        entry["column"] = _column(receiver)
        if quantile is not None:
            entry["quantile"] = quantile
        return entry

    def _aggregate(self, node: ast.expr) -> tuple[ast.expr, str, int | float | None]:
        """``<receiver>.<agg>()`` or a linear quantile: the receiver, aggregate and quantile."""
        if _is_method(node, "quantile"):
            call = _only(_method(node, "quantile"), 1, ("interpolation",))
            if _text(call.keywords["interpolation"]) != "linear":
                raise _NoMatchError
            return call.receiver, "quantile", _number(call.args[0])
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            raise _NoMatchError
        agg = node.func.attr
        if agg not in AGGREGATIONS or agg in ("len", "quantile"):
            raise _NoMatchError
        return _only(_method(node, agg)).receiver, agg, None

    def _counted(self, receiver: ast.expr, agg: str) -> Step | None:
        """The conditions of ``(<conditions>).sum()``, a count of the rows that pass them."""
        if agg != "sum" or _is_method(receiver, "filter"):
            return None
        return _attempt(partial(self._group, receiver))

    # -- conditions ------------------------------------------------------

    def _group(self, node: ast.expr) -> Step:
        """Conditions joined by ``&`` (all) or ``|`` (any); one condition reads as all."""
        match = "all"
        items = [node]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd | ast.BitOr):
            match = "all" if isinstance(node.op, ast.BitAnd) else "any"
            joiner = type(node.op)
            items = []
            current: ast.expr = node
            while isinstance(current, ast.BinOp) and isinstance(current.op, joiner):
                items.append(current.right)
                current = current.left
            items.append(current)
            items.reverse()
        return {"match": match, "conditions": [self._condition(item) for item in items]}

    def _condition(self, node: ast.expr) -> Step:
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or type(node.ops[0]) not in _COMPARISONS:
                raise _NoMatchError
            return {
                "column": _column(node.left),
                "operator": _COMPARISONS[type(node.ops[0])],
                "value": self._operand(node.comparators[0], expr=False),
            }
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return self._membership(node.operand, "not_in")
        if _is_method(node, "is_in"):
            return self._membership(node, "is_in")
        for operator in ("is_null", "is_not_null"):
            if _is_method(node, operator):
                column = _column(_only(_method(node, operator)).receiver)
                return {"column": column, "operator": operator}
        if _is_method(node, "contains"):
            call = _namespace(node, "contains", "str")
            if not call.keywords:
                return self._string_test(_only(call, 1), "matches")
            if not _flag(_only(call, 1, ("literal",)).keywords["literal"]):
                raise _NoMatchError
            return self._string_test(call, "contains")
        for operator in _STRING_TESTS:
            if _is_method(node, operator):
                return self._string_test(_only(_namespace(node, operator, "str"), 1), operator)
        raise _NoMatchError

    def _string_test(self, call: _Call, operator: str) -> Step:
        return {
            "column": _column(call.receiver),
            "operator": operator,
            "value": self._operand(call.args[0], expr=False, literals=("text",)),
        }

    def _membership(self, node: ast.expr, operator: str) -> Step:
        call = _only(_method(node, "is_in"), 1)
        condition: Step = {"column": _column(call.receiver), "operator": operator}
        members = call.args[0]
        if isinstance(members, ast.List):
            values = [_value(item) for item in members.elts]
            kinds = {_member_kind(value) for value in values}
            if len(kinds) != 1:
                raise _NoMatchError
            (kind,) = kinds
            if kind in ("integer", "float"):
                condition["numberType"] = kind
                kind = "number"
            condition["values"] = [_literal(kind, value) for value in values]
            return condition
        series = _only(_method(members, "implode")).receiver
        if _is_method(series, "to_decimal"):
            parse = _only(_namespace(series, "to_decimal", "str"), 0, ("scale",))
            texts = _texts(_only(_pl(parse.receiver, "Series"), 1).args[0])
            condition["values"] = [_literal("number", _decimal_value(text)) for text in texts]
            condition["numberType"] = "decimal"
            return condition
        parse = _only(_namespace(series, "to_date", "str"))
        texts = _texts(_only(_pl(parse.receiver, "Series"), 1).args[0])
        condition["values"] = [_literal("date", text) for text in texts]
        return condition

    # -- operands and expressions ----------------------------------------

    def _operand(
        self, node: ast.expr, *, expr: bool, literals: Sequence[str] = LITERAL_TYPES
    ) -> Step:
        """An operand the renderer wrote in expression position (``pl.lit``) or value position.

        *literals* are the literal types the slot takes. A literal of another
        type that renders here alike (a date, or ``pl.lit(None)``) can only be
        the nested operand expression that holds it.
        """
        plain = self._plain_operand(node, expr=expr)
        if plain is None:
            return {"kind": "expr", "expr": self._expr(node)}
        if plain["kind"] == "literal" and plain["type"] not in literals:
            return {"kind": "expr", "expr": {"type": "operand", "operand": plain}}
        return plain

    def _plain_operand(self, node: ast.expr, *, expr: bool) -> Step | None:
        """A column, literal, variable or constant as the position writes it, else None."""
        try:
            return {"kind": "column", "name": _column(node)}
        except _NoMatchError:
            pass
        try:
            return _literal("date", _date(node))
        except _NoMatchError:
            pass
        inner = node
        if _is_method(node, "lit"):
            try:
                inner = _only(_pl(node, "lit"), 1).args[0]
            except _NoMatchError:
                return None
            if isinstance(inner, ast.Constant) and inner.value is None:
                return _literal("null", None)  # a null is pl.lit(None) in either position
            if not expr:
                return None
        elif expr:
            return None
        if isinstance(inner, ast.Name):
            return {"kind": "variable", "name": inner.id}
        return _attempt(partial(_constant, inner)) or _attempt(partial(_bare_literal, inner))

    def _expr(self, node: ast.expr) -> Step:
        plain = self._plain_operand(node, expr=True)
        if plain is not None:
            return {"type": "operand", "operand": plain}
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return {
                "type": "binary",
                "left": self._operand(node.left, expr=True),
                "op": _BINARY[type(node.op)],
                "right": self._operand(node.right, expr=False),
            }
        if _is_method(node, "concat_str"):
            call = _only(_pl(node, "concat_str"), 1, ("separator",))
            if not isinstance(call.args[0], ast.List):
                raise _NoMatchError
            return {
                "type": "concat",
                "parts": [self._operand(part, expr=True) for part in call.args[0].elts],
                "separator": _text(call.keywords["separator"]),
            }
        if _is_method(node, "sum_horizontal"):
            call = _pl(node, "sum_horizontal")
            if call.keywords:
                raise _NoMatchError
            parts = [self._operand(part, expr=True) for part in call.args]
            return {"type": "sum_horizontal", "parts": parts}
        if _is_method(node, "otherwise"):
            return self._conditional(node)
        function = self._function(node)
        if function is not None:
            return function
        return self._window(node)

    def _function(self, node: ast.expr) -> Step | None:
        for fn, pattern, argument_types in _FUNCTION_PATTERNS:
            bound: dict[str, ast.AST | str] = {}
            if not _unify(pattern, node, bound):
                continue
            receiver = bound[_RECEIVER]
            assert isinstance(receiver, ast.expr)
            args = [
                self._function_arg(bound[_argument(index)], expected)
                for index, expected in enumerate(argument_types)
            ]
            operand = self._operand(receiver, expr=True)
            return {"type": "function", "fn": fn, "operand": operand, "args": args}
        return None

    def _function_arg(self, node: ast.AST | str, expected: str) -> Step:
        if isinstance(node, str):  # the type name in ``pl.<Type>``
            return _literal("text", node)
        assert isinstance(node, ast.expr)
        if expected in _OPERAND_ARGUMENT_LITERALS:
            return self._operand(node, expr=False, literals=_OPERAND_ARGUMENT_LITERALS[expected])
        return _attempt(partial(_constant, node)) or _bare_literal(node)

    def _conditional(self, node: ast.expr) -> Step:
        otherwise = _only(_method(node, "otherwise"), 1)
        branches: list[Step] = []
        chain: ast.expr = otherwise.receiver
        while True:
            then = _only(_method(chain, "then"), 1)
            when = _only(_method(then.receiver, "when"), 1)
            branches.append(
                {**self._group(when.args[0]), "then": self._operand(then.args[0], expr=True)}
            )
            if isinstance(when.receiver, ast.Name) and when.receiver.id == "pl":
                break
            chain = when.receiver
        branches.reverse()
        return {
            "type": "conditional",
            "branches": branches,
            "otherwise": self._operand(otherwise.args[0], expr=True),
        }

    def _window(self, node: ast.expr) -> Step:
        window: Step = {"type": "window", "over": []}
        if _is_method(node, "over"):
            call = _method(node, "over")
            if "order_by" in call.keywords:
                _only(call, 1, ("order_by", "descending"))
                flag = _flag(call.keywords["descending"])
                window["orderBy"] = [
                    {"column": column, "descending": flag}
                    for column in _texts(call.keywords["order_by"])
                ]
            else:
                _only(call, 1)
            window["over"] = _texts(call.args[0])
            node = call.receiver
        window.update(self._window_base(node))
        return window

    def _window_base(self, node: ast.expr) -> Step:
        if _is_method(node, "len"):
            _only(_pl(node, "len"))
            return {"agg": "len"}
        if _is_method(node, "int_range"):
            start, stop = _only(_pl(node, "int_range"), 2).args
            if (
                _whole(start) != 1
                or not isinstance(stop, ast.BinOp)
                or not isinstance(stop.op, ast.Add)
                or _whole(stop.right) != 1
            ):
                raise _NoMatchError
            _only(_pl(stop.left, "len"))
            return {"agg": "row_number"}
        if _is_method(node, "rank"):
            call = _only(_method(node, "rank"), 0, ("method", "descending"))
            method = _text(call.keywords["method"])
            if method not in _RANKS:
                raise _NoMatchError
            return {
                "agg": _RANKS[method],
                "of": self._window_value(call.receiver),
                "descending": _flag(call.keywords["descending"]),
            }
        if _is_method(node, "cum_sum"):
            receiver = _only(_method(node, "cum_sum")).receiver
            return {"agg": "cum_sum", "of": self._window_value(receiver)}
        if _is_method(node, "shift"):
            call = _only(_method(node, "shift"), 1)
            if _whole(call.args[0]) != 1:
                raise _NoMatchError
            return {"agg": "shift", "of": self._window_value(call.receiver)}
        if _is_method(node, "fill_null"):
            call = _only(_method(node, "fill_null"), 0, ("strategy",))
            strategy = _text(call.keywords["strategy"])
            if strategy not in _WINDOW_FILLS:
                raise _NoMatchError
            return {"agg": _WINDOW_FILLS[strategy], "of": self._window_value(call.receiver)}
        receiver, agg, quantile = self._aggregate(node)
        counted = self._counted(receiver, agg)
        if counted is not None:
            return {"agg": "len", "where": counted}
        found: Step = {"agg": agg}
        if _is_method(receiver, "filter"):
            where = _only(_method(receiver, "filter"), 1)
            found["where"] = self._group(where.args[0])
            receiver = where.receiver
        found["of"] = self._window_value(receiver)
        if quantile is not None:
            found["quantile"] = quantile
        return found

    def _window_value(self, node: ast.expr) -> Step:
        """A window's value: a column, or an expression whatever it holds."""
        operand = self._operand(node, expr=True)
        if operand["kind"] in ("column", "expr"):
            return operand
        return {"kind": "expr", "expr": {"type": "operand", "operand": operand}}


# ---------------------------------------------------------------------------
# What the steps rebuilt so far let a statement render as
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    """What the body rebuilt so far changes about how a later step renders.

    Three things do: the start step; the variables, which the steps after them
    read and which cannot be defined twice; and the names the code above binds,
    which a variable may not take.
    """

    node_type: NodeType
    input_mode: bool
    position: int = 0
    source: Step | None = None
    variables: list[Step] = field(default_factory=list)
    code_names: set[str] = field(default_factory=set)

    def rendering(self, step: Step) -> str | None:
        """*step*'s text here, its comment included, or None when it cannot stand here."""
        if step["kind"] == "source":
            if not self.input_mode or self.position:
                return None
            program = [step]
        elif self.input_mode and self.source is None:
            return None
        elif step["kind"] == "variable" and step["name"] in self.code_names:
            return None
        else:
            program = [*([self.source] if self.source else []), *self.variables, step]
        numbered = [{**entry, "id": f"context-{i}"} for i, entry in enumerate(program)]
        try:
            rendered = render_node_steps(self.node_type, numbered)
        except ValueError:  # a PolarsStepError, or a layout outside the vocabulary
            return None
        # Everything after the step before it: its comment, then its statement.
        before = rendered.step_lines[-2][1] if len(program) > 1 else 0
        return "\n".join(rendered.code.split("\n")[before:])

    def accept(self, step: Step) -> None:
        if step["kind"] == "source":
            self.source = step
        elif step["kind"] == "variable":
            self.variables.append(step)

    def keep_as_code(self, unit: _Unit) -> None:
        for node in unit.nodes:
            self.code_names |= _code_bound_names(ast.unparse(node))


def _recognised(reader: _Reader, unit: _Unit, context: _Context) -> Step | None:
    """The step a unit's statement renders from, keeping its comment, or None for code."""
    if unit.node is None:
        return None
    for candidate in reader.candidates(unit.node):
        step = _with_comment(candidate, unit)
        if context.rendering(step) == unit.text:
            return step
    return None


def _assemble(
    node_type: NodeType,
    read: tuple[list[_Unit], list[str]],
    choose: Callable[[_Unit, _Context], Step | None],
) -> list[Step]:
    """A step for each unit *choose* recognises, and one code step for each run of the rest.

    The lines after the last statement join the code step before them, or are a
    code step of their own.
    """
    units, below = read
    context = _Context(node_type, input_mode=stepped_surface_for(node_type).start == "input")
    steps: list[Step] = []
    code: list[str] = []
    for unit in units:
        context.position = len(steps) + (1 if code else 0)
        step = choose(unit, context)
        if step is None:
            code.extend(unit.lines)
            context.keep_as_code(unit)
            continue
        if code:
            steps.append({"kind": "free_code", "code": "\n".join(code)})
            code = []
        steps.append(step)
        context.accept(step)
    code.extend(below)
    if code:
        steps.append({"kind": "free_code", "code": "\n".join(code)})
    return [{"id": f"{step['kind']}-{i + 1}", **step} for i, step in enumerate(steps)]


# ---------------------------------------------------------------------------
# The canonical form of a structured step
# ---------------------------------------------------------------------------

#: Where an operand renders: ``expr`` writes a literal, variable or constant
#: inside ``pl.lit``, ``value`` writes it bare, ``window`` is a window's value
#: (a column or an expression), and ``plain`` is a function argument that takes
#: only a value or a constant.
_Position = Literal["expr", "value", "window", "plain"]


def _normal_step(step: Mapping[str, Any], context: _Context) -> Step:
    kind = step["kind"]
    # The comment comes from the lines above the statement (``_with_comment``).
    dropped = ("id", COMMENT_FIELD)
    normal: Step = {k: copy.deepcopy(v) for k, v in step.items() if k not in dropped}
    if kind == "filter":
        normal.update(_normal_group(step))
    elif kind == "with_column":
        normal["expr"] = _normal_expr(step["expr"])
    elif kind in ("select", "drop"):
        if not step.get("dtypes"):
            normal.pop("dtypes", None)
    elif kind == "fill_null" and step["fill"]["kind"] == "value":
        normal["fill"] = {"kind": "value", "value": _normal_operand(step["fill"]["value"], "value")}
    elif kind == "pivot":
        normal["columns"] = [
            {"value": _normal_literal(entry["value"]), "name": entry["name"]}
            for entry in step["columns"]
        ]
    elif kind == "group_by":
        normal["aggregations"] = [_normal_aggregation(entry) for entry in step["aggregations"]]
        pivot = _as_pivot(normal)
        if pivot is not None:
            as_group_by = context.rendering(normal)
            if as_group_by is not None and context.rendering(pivot) == as_group_by:
                return pivot
    return normal


def _normal_literal(literal: Mapping[str, Any]) -> Step:
    value = None if literal["type"] == "null" else literal["value"]
    return _literal(literal["type"], value)


def _normal_group(group: Mapping[str, Any]) -> Step:
    conditions = group["conditions"]
    return {
        "match": "all" if len(conditions) == 1 else group["match"],
        "conditions": [_normal_condition(condition) for condition in conditions],
    }


def _normal_condition(condition: Mapping[str, Any]) -> Step:
    normal: Step = copy.deepcopy(dict(condition))
    if "value" in condition:
        text_only = condition["operator"] in _STRING_OPERATORS
        literals = ("text",) if text_only else LITERAL_TYPES
        normal["value"] = _normal_operand(condition["value"], "value", literals)
    if "values" in condition:
        number_type = condition.get("numberType")
        normal["values"] = [
            _normal_literal(item)
            if number_type is None
            else _literal("number", _member_number(item["value"], number_type))
            for item in condition["values"]
        ]
    return normal


def _member_number(value: int | float, number_type: str) -> int | float:
    """A membership number as its column's number type renders it."""
    if number_type == "integer":
        return int(value)
    if number_type == "float":
        return float(value)
    return _decimal_value(_decimal_text(value))


def _normal_operand(
    operand: Mapping[str, Any], position: _Position, literals: Sequence[str] = LITERAL_TYPES
) -> Step:
    """*operand* in canonical form, where *position* renders it and *literals* may stand."""
    kind = operand["kind"]
    if kind == "literal":
        return _normal_literal(operand)
    if kind != "expr":
        return dict(operand)
    inner = _normal_expr(operand["expr"])
    if inner["type"] == "operand" and _renders_alike(inner["operand"], position, literals):
        return dict(inner["operand"])
    return {"kind": "expr", "expr": inner}


def _renders_alike(
    operand: Mapping[str, Any], position: _Position, literals: Sequence[str]
) -> bool:
    """Whether *operand* may stand in *position* and renders there as inside an expression."""
    if operand["kind"] in ("column", "expr"):
        return True
    if operand["kind"] == "literal" and operand["type"] not in literals:
        return False
    if position == "expr":
        return True
    return (
        position == "value" and operand["kind"] == "literal" and operand["type"] in ("date", "null")
    )


def _normal_expr(expr: Mapping[str, Any]) -> Step:
    kind = expr["type"]
    if kind == "operand":
        operand = _normal_operand(expr["operand"], "expr")
        if operand["kind"] == "expr":
            return dict(operand["expr"])
        return {"type": "operand", "operand": operand}
    if kind == "binary":
        return {
            "type": "binary",
            "left": _normal_operand(expr["left"], "expr"),
            "op": expr["op"],
            "right": _normal_operand(expr["right"], "value"),
        }
    if kind == "function":
        argument_types = FUNCTIONS[expr["fn"]][0]
        return {
            "type": "function",
            "fn": expr["fn"],
            "operand": _normal_operand(expr["operand"], "expr"),
            "args": [
                _normal_operand(arg, "value", _OPERAND_ARGUMENT_LITERALS[expected])
                if expected in _OPERAND_ARGUMENT_LITERALS
                else _normal_operand(arg, "plain")
                for arg, expected in zip(expr["args"], argument_types, strict=True)
            ],
        }
    if kind == "conditional":
        return {
            "type": "conditional",
            "branches": [
                {**_normal_group(branch), "then": _normal_operand(branch["then"], "expr")}
                for branch in expr["branches"]
            ],
            "otherwise": _normal_operand(expr["otherwise"], "expr"),
        }
    if kind == "window":
        return _normal_window(expr)
    parts = [_normal_operand(part, "expr") for part in expr["parts"]]
    if kind == "concat":
        return {"type": "concat", "parts": parts, "separator": expr.get("separator", "")}
    return {"type": "sum_horizontal", "parts": parts}


def _normal_window(expr: Mapping[str, Any]) -> Step:
    agg = expr["agg"]
    normal: Step = {"type": "window", "agg": agg, "over": list(expr["over"])}
    if "of" in expr:
        normal["of"] = _normal_operand(expr["of"], "window")
    if agg == "quantile":
        normal["quantile"] = expr["quantile"]
    if agg in _RANKS.values():
        normal["descending"] = expr.get("descending", False)
    if expr.get("orderBy") is not None:
        normal["orderBy"] = [
            {"column": entry["column"], "descending": entry.get("descending", False)}
            for entry in expr["orderBy"]
        ]
    if expr.get("where") is not None:
        normal["where"] = _normal_group(expr["where"])
    return normal


def _normal_aggregation(entry: Mapping[str, Any]) -> Step:
    agg = entry["agg"]
    if "dtype" in entry:
        normal: Step = {"dtype": entry["dtype"], "agg": agg, "suffix": entry["suffix"]}
    else:
        normal = {"agg": agg, "name": entry["name"]}
        if agg != "len":
            normal["column"] = entry["column"]
        if entry.get("where") is not None:
            normal["where"] = _normal_group(entry["where"])
    if agg == "quantile":
        normal["quantile"] = entry["quantile"]
    return normal


def _as_pivot(group_by: Mapping[str, Any]) -> Step | None:
    """The pivot a group-by reads as when each aggregate is one ``on`` value's cell."""
    if not group_by["keys"]:
        return None
    shapes: set[tuple[str, str, str]] = set()
    columns: list[Step] = []
    for entry in group_by["aggregations"]:
        where = entry.get("where")
        if "dtype" in entry or entry["agg"] == "len" or where is None:
            return None
        if len(where["conditions"]) != 1:
            return None
        condition = where["conditions"][0]
        value = condition.get("value")
        if condition["operator"] != "eq" or value is None or value["kind"] != "literal":
            return None
        shapes.add((entry["column"], condition["column"], entry["agg"]))
        columns.append({"value": value, "name": entry["name"]})
    if len(shapes) != 1:
        return None
    ((values, on, agg),) = shapes
    return {
        "kind": "pivot",
        "index": list(group_by["keys"]),
        "on": on,
        "values": values,
        "agg": agg,
        "columns": columns,
    }
