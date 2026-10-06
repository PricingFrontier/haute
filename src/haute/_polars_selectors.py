"""Literal Polars column selectors rebuilt from user code and expanded by Polars.

``pl.all()``, ``pl.exclude(...)``, regex or dtype ``pl.col(...)``, and the
``polars.selectors`` functions choose their columns from the schema of the frame
they run against. The code analysers read syntax, so this module rebuilds a
selector written with literal arguments as the Polars object itself — calling
the constructors with those literals, never evaluating user code — and asks
Polars which columns it selects for a given schema.

A rebuilt object counts as a selector only when Polars reports it as a pure
column selection: ``~cs.numeric()`` selects the complement, while ``~pl.all()``
negates every value and is a computation over a selector instead.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl
import polars.selectors as cs

from haute._polars_operations import (
    POLARS_SELECTOR_CONSTRUCTORS,
    POLARS_SELECTORS_MODULE_FUNCTIONS,
)

__all__ = [
    "LiteralSelector",
    "expand_literal_selector",
    "literal_selector",
    "preamble_selector_aliases",
    "selector_root",
]


@dataclass(frozen=True, slots=True)
class LiteralSelector:
    """A pure column selection rebuilt from literal syntax."""

    source: str
    """The selector's source text, normalised by ``ast.unparse``."""

    dtype_dependent: bool
    positional: bool
    expression: pl.Expr = field(compare=False, repr=False)


class _NotLiteralError(Exception):
    """The syntax is outside the closed selector grammar."""


@dataclass(slots=True)
class _Flags:
    dtype_dependent: bool = False
    positional: bool = False


def preamble_selector_aliases(preamble: str) -> frozenset[str]:
    """Return names bound to ``polars.selectors`` by one top-level preamble import.

    A name counts only when a top-level ``import polars.selectors as <name>`` or
    ``from polars import selectors as <name>`` binds it and nothing else in the
    preamble binds it again.
    """
    try:
        tree = ast.parse(preamble or "")
    except SyntaxError:
        return frozenset()
    candidates: list[str] = []
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            candidates.extend(
                alias.asname
                for alias in statement.names
                if alias.name == "polars.selectors" and alias.asname
            )
        elif isinstance(statement, ast.ImportFrom) and statement.module == "polars":
            candidates.extend(
                alias.asname or alias.name for alias in statement.names if alias.name == "selectors"
            )
    bindings: dict[str, int] = {}
    for node in ast.walk(tree):
        for name in _bound_names(node):
            bindings[name] = bindings.get(name, 0) + 1
    return frozenset(name for name in candidates if bindings.get(name) == 1)


def _bound_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return [node.id]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.Import):
        return [alias.asname or alias.name.split(".", 1)[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [alias.asname or alias.name for alias in node.names if alias.name != "*"]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [node.name]
    return []


def literal_selector(node: ast.AST, *, aliases: frozenset[str]) -> LiteralSelector | None:
    """Rebuild *node* as a pure Polars column selection, or return ``None``."""
    flags = _Flags()
    try:
        expression = _rebuild(node, aliases, flags)
        pure = expression.meta.is_column_selection(allow_aliasing=False)
    except Exception:
        return None
    if not pure:
        return None
    return LiteralSelector(
        source=ast.unparse(node),
        dtype_dependent=flags.dtype_dependent,
        positional=flags.positional,
        expression=expression,
    )


def selector_root(
    node: ast.AST, *, aliases: frozenset[str]
) -> tuple[LiteralSelector, ast.AST] | None:
    """Return the literal selector a computation starts from, with its node.

    The root is found by following method receivers (through expression
    namespaces such as ``.str`` or ``.name``), the left operand of a binary
    operation, and the operand of a unary operation. An expression that is
    itself a literal selector has no root.
    """
    if literal_selector(node, aliases=aliases) is not None:
        return None
    current = node
    while True:
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            receiver: ast.AST = current.func.value
            if isinstance(receiver, ast.Attribute) and not isinstance(receiver.value, ast.Name):
                # ``<expr>.str.method()`` / ``<expr>.name.suffix()``: step past the namespace.
                receiver = receiver.value
        elif isinstance(current, ast.BinOp):
            receiver = current.left
        elif isinstance(current, ast.UnaryOp):
            receiver = current.operand
        else:
            return None
        if isinstance(receiver, ast.Name):
            return None
        selector = literal_selector(receiver, aliases=aliases)
        if selector is not None:
            return selector, receiver
        current = receiver


def expand_literal_selector(
    selector: LiteralSelector,
    columns: Collection[str],
    dtypes: Mapping[str, pl.DataType | None] | None,
) -> tuple[str, ...] | None:
    """Return the columns *selector* selects, or ``None`` when that is unknown.

    Positional selectors need a column order the analysers do not track, and a
    dtype-dependent selector needs a dtype for every column.
    """
    if selector.positional:
        return None
    # An unordered collection is expanded in sorted order so results are stable.
    ordered = sorted(columns) if isinstance(columns, (set, frozenset)) else list(columns)
    if selector.dtype_dependent:
        if dtypes is None:
            return None
        resolved: list[tuple[str, pl.DataType]] = []
        for name in ordered:
            dtype = dtypes.get(name)
            if dtype is None:
                return None
            resolved.append((name, dtype))
        schema = pl.Schema(resolved)
    else:
        schema = pl.Schema([(name, pl.Null()) for name in ordered])
    try:
        return tuple(cs.expand_selector(schema, selector.expression, strict=False))
    except Exception:
        return None


def _rebuild(node: ast.AST, aliases: frozenset[str], flags: _Flags) -> Any:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
        return ~_rebuild(node.operand, aliases, flags)
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Sub, ast.BitOr, ast.BitAnd, ast.BitXor)
    ):
        left = _rebuild(node.left, aliases, flags)
        right = _rebuild(node.right, aliases, flags)
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.BitOr):
            return left | right
        if isinstance(node.op, ast.BitAnd):
            return left & right
        return left ^ right
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        raise _NotLiteralError
    receiver = node.func.value
    name = node.func.attr
    if isinstance(receiver, ast.Name) and receiver.id == "pl":
        return _rebuild_constructor(node, name, flags)
    if isinstance(receiver, ast.Name) and receiver.id in aliases:
        form = POLARS_SELECTORS_MODULE_FUNCTIONS.get(name)
        if form is None:
            raise _NotLiteralError
        args, kwargs = _literal_arguments(node, flags)
        flags.dtype_dependent |= form.dtype_dependent
        flags.positional |= form.positional
        return getattr(cs, name)(*args, **kwargs)
    if name == "exclude":
        base = _rebuild(receiver, aliases, flags)
        args, kwargs = _literal_arguments(node, flags)
        if kwargs:
            raise _NotLiteralError
        return base.exclude(*args)
    raise _NotLiteralError


def _rebuild_constructor(node: ast.Call, name: str, flags: _Flags) -> Any:
    form = POLARS_SELECTOR_CONSTRUCTORS.get(name)
    if form is None:
        raise _NotLiteralError
    args, kwargs = _literal_arguments(node, flags)
    if kwargs:
        raise _NotLiteralError
    if name in {"all", "first", "last"}:
        if args:
            raise _NotLiteralError
    elif name == "nth":
        if not args or not all(_is_int_or_int_list(argument) for argument in args):
            raise _NotLiteralError
    elif name == "exclude":
        if not args:
            raise _NotLiteralError
    elif name == "col":
        if len(args) != 1 or not _is_selecting_col_argument(args[0]):
            raise _NotLiteralError
    flags.positional |= form.positional
    return getattr(pl, name)(*args)


def _is_int_or_int_list(value: object) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_is_int_or_int_list(item) for item in value)
    return isinstance(value, int) and not isinstance(value, bool)


def _is_selecting_col_argument(value: object) -> bool:
    if isinstance(value, str):
        return value == "*" or (value.startswith("^") and value.endswith("$"))
    if isinstance(value, list):
        return bool(value) and all(_is_dtype(item) for item in value)
    return _is_dtype(value)


def _is_dtype(value: object) -> bool:
    return isinstance(value, pl.DataType) or (
        isinstance(value, type) and issubclass(value, pl.DataType)
    )


def _literal_arguments(node: ast.Call, flags: _Flags) -> tuple[list[Any], dict[str, Any]]:
    args = [_literal_value(argument, flags) for argument in node.args]
    kwargs: dict[str, Any] = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            raise _NotLiteralError
        kwargs[keyword.arg] = _literal_value(keyword.value, flags)
    return args, kwargs


def _literal_value(node: ast.AST, flags: _Flags) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    if isinstance(node, ast.Constant) and node.value is None:
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal_value(element, flags) for element in node.elts]
    dtype = _literal_dtype(node, flags)
    if dtype is not None:
        flags.dtype_dependent = True
        return dtype
    raise _NotLiteralError


def _literal_dtype(node: ast.AST, flags: _Flags) -> Any:
    target = node.func if isinstance(node, ast.Call) else node
    if not (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "pl"
    ):
        return None
    candidate = getattr(pl, target.attr, None)
    if not (isinstance(candidate, type) and issubclass(candidate, pl.DataType)):
        return None
    if not isinstance(node, ast.Call):
        return candidate
    args, kwargs = _literal_arguments(node, flags)
    return candidate(*args, **kwargs)
