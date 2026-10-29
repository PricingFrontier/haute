"""AST-based expression parser for extracting human-readable formulas from Polars code.

Given a code string containing Polars ``with_columns`` calls and a target column
name, the parser returns a :class:`ParsedExpression` describing the formula in
human-readable text, its type, referenced columns, constants, and more.

Optionally, :func:`evaluate_expression` substitutes concrete values and has
Polars compute the result on the traced row, returning an
:class:`EvaluatedExpression`.
"""

from __future__ import annotations

import ast
import copy
import datetime as _dt
import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, cast

import numpy as np
import polars as pl

__all__ = [
    "AssignmentPhases",
    "ParsedExpression",
    "EvaluatedExpression",
    "assigns_column",
    "assignment_phases",
    "copied_from",
    "parse_expression",
    "evaluate_expression",
    "parse_expression_chain",
    "renamed_from",
    "wrap_node_code",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParsedExpression:
    target_column: str
    expression_text: str
    expression_type: str  # arithmetic|conditional|horizontal_func|function_call|window|opaque
    referenced_columns: list[str]
    constants: list[Any]
    sub_expressions: list[ParsedExpression] = field(default_factory=list)
    source_line: int | None = None


@dataclass
class EvaluatedExpression(ParsedExpression):
    substituted_text: str = ""
    result_value: Any = None
    # Why ``result_value`` could not be computed from the traced row, if it could not.
    not_computable_reason: str | None = None
    input_values: dict[str, Any] = field(default_factory=dict)
    # Conditional branch tracking
    taken_branch: str | None = None
    taken_branch_index: int | None = None
    dimmed_branches: list[int] = field(default_factory=list)
    nested_branches: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Operator helpers
# ---------------------------------------------------------------------------

_OP_SYMBOLS: dict[type, str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
}

_CMP_SYMBOLS: dict[type, str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}

# Precedence table (higher = binds tighter)
_PREC: dict[type, int] = {
    ast.BitOr: 1,
    ast.BitXor: 2,
    ast.BitAnd: 3,
    ast.LShift: 4,
    ast.RShift: 4,
    ast.Add: 5,
    ast.Sub: 5,
    ast.Mult: 6,
    ast.Div: 6,
    ast.FloorDiv: 6,
    ast.Mod: 6,
    ast.Pow: 8,
}


def _op_prec(op: ast.operator) -> int:
    return _PREC.get(type(op), 5)


def _quote_str(val: str) -> str:
    """Render a string literal for the formula text: double-quoted with any
    embedded quotes/backslashes/control chars escaped (JSON string form), so a
    value like ``he said "hi"`` produces well-formed, unambiguous output."""
    return json.dumps(val, ensure_ascii=False)


# Effective precedence for non-BinOp operand kinds, used by the renderer to
# decide when a child expression must be parenthesised. Comparisons and boolean
# operators bind *looser* than any arithmetic operator, and a conditional
# (``a if c else b``) binds loosest of all, so each must be wrapped when it
# appears as an operand of a tighter operator.
_PREC_COMPARE = 0  # below BitOr(1); e.g. (a < b) * c must keep its parens
_PREC_BOOL_OR = -2
_PREC_BOOL_AND = -1
_PREC_IFEXP = -3
# Unary minus, plus and invert bind looser than ** (so ``-a ** b`` is
# ``-(a ** b)`` and ``(-a) ** b`` needs parens) but tighter than multiplication.
_PREC_USUB = 7
# ``not`` binds looser than a comparison (``not a == b`` is ``not (a == b)``)
# and tighter than ``and``.
_PREC_NOT = -0.5


_COLUMN_PRODUCER_METHODS: frozenset[str] = frozenset({"with_columns", "select"})
_CONTROL_FLOW_STATEMENT_TYPES: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.For,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
)


@lru_cache(maxsize=256)
def _cached_parse(code: str) -> ast.Module:
    """Parse *code* to an AST module, memoised on the source string.

    ``parse_expression``/``_locate_defining_expression``/
    ``parse_expression_chain`` each need the module AST for the *same* code
    string; parsing is the dominant cost and the result is treated as read-only
    by every consumer (symbol-table builders and converters only read the tree;
    substitution builds *new* nodes), so a shared cached parse is safe and
    removes the repeated ``ast.parse`` of identical source. Raises
    ``SyntaxError`` for invalid code, exactly like ``ast.parse``.
    """
    return ast.parse(code)


def _opaque(
    target_column: str,
    text: str = "",
    source_line: int | None = None,
) -> ParsedExpression:
    """Build an ``expression_type="opaque"`` :class:`ParsedExpression`.

    Every ``opaque`` result across this module shares the same empty
    ``referenced_columns``/``constants`` shape; this factory keeps them
    consistent in one place.
    """
    return ParsedExpression(
        target_column=target_column,
        expression_text=text,
        expression_type="opaque",
        referenced_columns=[],
        constants=[],
        source_line=source_line,
    )


def _collect_when_then_chain(node: ast.AST) -> list[dict[str, Any]]:
    """Walk backwards through a ``pl.when().then()…otherwise()`` chain.

    Returns ``[{"cond": ast, "then": ast}, …]`` in source order, with a
    trailing ``{"otherwise": ast}`` appended when the chain terminates in an
    ``.otherwise()``. Shared by the text converter and the value evaluator so
    the two walk the chain identically.
    """
    clauses: list[dict[str, Any]] = []
    otherwise_node: ast.AST | None = None
    current: ast.AST = node

    while True:
        if not isinstance(current, ast.Call) or not isinstance(current.func, ast.Attribute):
            break
        func_attr = current.func
        method = func_attr.attr

        if method == "otherwise":
            otherwise_node = current.args[0] if current.args else ast.Constant(value=None)
            current = func_attr.value
            continue

        if method == "then":
            then_node: ast.AST = current.args[0] if current.args else ast.Constant(value=None)
            when_call = func_attr.value
            if (
                isinstance(when_call, ast.Call)
                and isinstance(when_call.func, ast.Attribute)
                and when_call.func.attr == "when"
            ):
                cond_node = when_call.args[0] if when_call.args else None
                clauses.append({"cond": cond_node, "then": then_node})
                current = when_call.func.value
                if isinstance(current, ast.Name) and current.id == "pl":
                    break
                continue
            break

        if method == "alias":
            current = func_attr.value
            continue

        break

    clauses.reverse()
    if otherwise_node is not None:
        clauses.append({"otherwise": otherwise_node})
    return clauses


# ---------------------------------------------------------------------------
# Horizontal / top-level Polars functions
# ---------------------------------------------------------------------------

_HORIZONTAL_FUNCS = {
    "min_horizontal",
    "max_horizontal",
    "sum_horizontal",
    "mean_horizontal",
    "all_horizontal",
    "any_horizontal",
    "concat_str",
    "coalesce",
}


# ---------------------------------------------------------------------------
# Opaque method names (lambdas / UDFs)
# ---------------------------------------------------------------------------

_OPAQUE_METHODS = {"map_elements", "map_batches"}


# ---------------------------------------------------------------------------
# AST -> text converter
# ---------------------------------------------------------------------------


class _ExprConverter:
    """Walks a Python AST node representing a Polars expression and produces
    a human-readable text, collecting referenced columns and constants."""

    def __init__(self, symbol_table: dict[str, ast.AST] | None = None):
        self.columns: list[str] = []
        self.constants: list[Any] = []
        self.expr_type: str = "arithmetic"
        self.sub_expressions: list[ParsedExpression] = []
        self._symbol_table: dict[str, ast.AST] = symbol_table or {}
        self._is_opaque = False
        # Names currently being resolved through the symbol table; guards
        # against self-/mutually-referential top-level assignments recursing
        # forever (e.g. ``x = x + 1`` leaving a ``Name('x')`` in the table).
        self._resolving: set[str] = set()

    # -- public entry point --------------------------------------------------

    def convert(self, node: ast.AST, parent_op: ast.operator | None = None) -> str:
        if isinstance(node, ast.BinOp):
            return self._binop(node, parent_op)
        if isinstance(node, ast.UnaryOp):
            return self._unaryop(node)
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node)
        if isinstance(node, ast.Call):
            return self._call(node, parent_op)
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.Name):
            return self._name(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Subscript):
            return self._subscript(node)
        if isinstance(node, ast.Slice):
            return self._slice(node)
        if isinstance(node, ast.List):
            return self._list(node)
        if isinstance(node, ast.Tuple):
            return self._tuple(node)
        if isinstance(node, ast.Dict):
            return self._dict(node)
        if isinstance(node, ast.IfExp):
            return self._ifexp(node)
        if isinstance(node, ast.JoinedStr):
            return self._joinedstr(node)
        if isinstance(node, ast.Starred):
            return self.convert(node.value, parent_op)
        if isinstance(node, ast.Expr):
            return self.convert(node.value, parent_op)
        # Fallback
        return ast.dump(node)

    # -- helpers -------------------------------------------------------------

    def _is_pl_call(self, node: ast.Call, attr: str) -> bool:
        """Check if node is pl.<attr>(...)."""
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == attr:
            if isinstance(func.value, ast.Name) and func.value.id == "pl":
                return True
        return False

    def _is_method_call(self, node: ast.Call, method: str) -> bool:
        """Check if node is <expr>.<method>(...)."""
        return isinstance(node.func, ast.Attribute) and node.func.attr == method

    def _get_method_chain(self, node: ast.Call) -> tuple[ast.AST, list[tuple[str, ast.Call]]]:
        """Unwind a method chain, returning (root_expr, [(method_name, call_node), ...])."""
        chain: list[tuple[str, ast.Call]] = []
        current: ast.AST = node
        while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            chain.append((current.func.attr, current))
            current = current.func.value
        chain.reverse()
        return current, chain

    # -- node handlers -------------------------------------------------------

    def _operand_prec(self, node: ast.AST) -> float:
        """Effective binding precedence of *node* when it appears as an operand.

        Higher binds tighter. Atoms (names, calls, literals, subscripts) bind
        tightest; comparisons and boolean operators bind looser than any
        arithmetic operator and a conditional binds loosest, so each is
        parenthesised by the arithmetic/boolean renderers when nested inside a
        tighter operator.
        """
        if isinstance(node, ast.BinOp):
            return _op_prec(node.op)
        if isinstance(node, ast.Compare):
            return _PREC_COMPARE
        if isinstance(node, ast.BoolOp):
            return _PREC_BOOL_AND if isinstance(node.op, ast.And) else _PREC_BOOL_OR
        if isinstance(node, ast.IfExp):
            return _PREC_IFEXP
        if isinstance(node, ast.UnaryOp):
            return _PREC_NOT if isinstance(node.op, ast.Not) else _PREC_USUB
        return 100  # atom

    def _binop(self, node: ast.BinOp, parent_op: ast.operator | None = None) -> str:
        sym = _OP_SYMBOLS.get(type(node.op), "?")
        left_text = self.convert(node.left, node.op)
        right_text = self.convert(node.right, node.op)

        parent_prec = _op_prec(node.op)
        left_prec = self._operand_prec(node.left)
        right_prec = self._operand_prec(node.right)

        is_pow = isinstance(node.op, ast.Pow)
        # Left-associative non-commutative ops need parens on an equal-precedence
        # RIGHT child (``a - (b - c)``); the right-associative ``**`` instead
        # needs them on an equal-precedence LEFT child (``(a ** b) ** c``).
        right_non_commutative = (ast.Sub, ast.Div, ast.FloorDiv, ast.Mod)

        if left_prec < parent_prec or (left_prec == parent_prec and is_pow):
            left_text = f"({left_text})"
        if right_prec < parent_prec or (
            right_prec == parent_prec and isinstance(node.op, right_non_commutative)
        ):
            right_text = f"({right_text})"

        return f"{left_text} {sym} {right_text}"

    def _unaryop(self, node: ast.UnaryOp) -> str:
        operand = self.convert(node.operand)
        # A looser operand keeps its parentheses: ``-(x + z)`` is not ``-x + z``.
        if self._operand_prec(node.operand) < self._operand_prec(node):
            operand = f"({operand})"
        if isinstance(node.op, ast.USub):
            self.expr_type = "arithmetic"
            return f"-{operand}"
        if isinstance(node.op, ast.UAdd):
            return f"+{operand}"
        if isinstance(node.op, ast.Not):
            return f"not {operand}"
        if isinstance(node.op, ast.Invert):
            return f"~{operand}"
        return f"?{operand}"

    def _compare(self, node: ast.Compare) -> str:
        parts = [self.convert(node.left)]
        for op, comp in zip(node.ops, node.comparators):
            sym = _CMP_SYMBOLS.get(type(op), "?")
            parts.append(sym)
            parts.append(self.convert(comp))
        return " ".join(parts)

    def _boolop(self, node: ast.BoolOp) -> str:
        is_and = isinstance(node.op, ast.And)
        sym = "and" if is_and else "or"
        parent_prec = _PREC_BOOL_AND if is_and else _PREC_BOOL_OR
        parts = []
        for v in node.values:
            text = self.convert(v)
            # ``or`` binds looser than ``and``; an ``or`` (or a conditional)
            # nested inside an ``and`` must be parenthesised to preserve grouping.
            if self._operand_prec(v) < parent_prec:
                text = f"({text})"
            parts.append(text)
        return f" {sym} ".join(parts)

    def _constant(self, node: ast.Constant) -> str:
        val = node.value
        if val is None:
            return "None"
        if val is True:
            return "True"
        if val is False:
            return "False"
        if isinstance(val, str):
            return _quote_str(val)
        if isinstance(val, (int, float)):
            self.constants.append(val)
            return repr(val)
        return repr(val)

    def _name(self, node: ast.Name) -> str:
        name = node.id
        # Check symbol table for variable resolution
        if name in self._symbol_table:
            if name in self._resolving:
                # Cyclic self-reference; bail to an opaque atom instead of
                # recursing until RecursionError.
                self._is_opaque = True
                self.expr_type = "opaque"
                return name
            resolved = self._symbol_table[name]
            if isinstance(resolved, ast.AST):
                self._resolving.add(name)
                table = self._symbol_table
                # A binding was resolved when its statement ran: a name still in
                # it was not bound then, so no later binding is what it read.
                self._symbol_table = {}
                try:
                    return self.convert(resolved)
                finally:
                    self._symbol_table = table
                    self._resolving.discard(name)
        if name == "None":
            return "None"
        if name == "True":
            return "True"
        if name == "False":
            return "False"
        # Unknown name: treat as opaque reference
        return self._free_name_text(name)

    def _free_name_text(self, name: str) -> str:
        """How a name the node's code does not bind is written."""
        return name

    def _column_text(self, name: str) -> str:
        """How a column reference is written."""
        return name

    def _attribute(self, node: ast.Attribute) -> str:
        """Handle attribute access like pl.Float64."""
        if isinstance(node.value, ast.Name) and node.value.id == "pl":
            return node.attr
        val = self.convert(node.value)
        return f"{val}.{node.attr}"

    def _subscript(self, node: ast.Subscript) -> str:
        val = self.convert(node.value)
        sl = self.convert(node.slice)
        return f"{val}[{sl}]"

    def _slice(self, node: ast.Slice) -> str:
        lo = self.convert(node.lower) if node.lower is not None else ""
        hi = self.convert(node.upper) if node.upper is not None else ""
        if node.step is not None:
            return f"{lo}:{hi}:{self.convert(node.step)}"
        return f"{lo}:{hi}"

    def _list(self, node: ast.List) -> str:
        elts = [self.convert(e) for e in node.elts]
        return "[" + ", ".join(elts) + "]"

    def _tuple(self, node: ast.Tuple) -> str:
        elts = [self.convert(e) for e in node.elts]
        return "(" + ", ".join(elts) + ")"

    def _dict(self, node: ast.Dict) -> str:
        items = []
        for k, v in zip(node.keys, node.values):
            if k is not None:
                items.append(f"{self.convert(k)}: {self.convert(v)}")
            else:
                items.append(f"**{self.convert(v)}")
        return "{" + ", ".join(items) + "}"

    def _ifexp(self, node: ast.IfExp) -> str:
        body = self.convert(node.body)
        test = self.convert(node.test)
        orelse = self.convert(node.orelse)
        return f"{body} if {test} else {orelse}"

    def _joinedstr(self, node: ast.JoinedStr) -> str:
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                inner = self.convert(v.value)
                # Preserve conversion (!r/!s/!a) and format spec (:...) so the
                # rendered formula is not silently lossy.
                if v.conversion != -1:
                    inner += "!" + chr(v.conversion)
                if v.format_spec is not None:
                    inner += ":" + self._format_spec_text(v.format_spec)
                parts.append(f"{{{inner}}}")
            else:
                parts.append(self.convert(v))
        return 'f"' + "".join(parts) + '"'

    def _format_spec_text(self, spec: ast.expr) -> str:
        if not isinstance(spec, ast.JoinedStr):
            return ""
        out = []
        for p in spec.values:
            if isinstance(p, ast.Constant):
                out.append(str(p.value))
            elif isinstance(p, ast.FormattedValue):
                out.append(f"{{{self.convert(p.value)}}}")
        return "".join(out)

    # -- Polars-specific call handling ----------------------------------------

    def _call(self, node: ast.Call, parent_op: ast.operator | None = None) -> str:
        # Check if any argument is a lambda — mark as opaque
        for a in node.args:
            if isinstance(a, ast.Lambda):
                self._is_opaque = True
                self.expr_type = "opaque"

        # pl.col("name")
        if self._is_pl_call(node, "col"):
            return self._pl_col(node)

        # pl.lit(value)
        if self._is_pl_call(node, "lit"):
            return self._pl_lit(node)

        # pl.when(...)
        if self._is_pl_call(node, "when"):
            return self._format_when_entry(node)

        # pl.max_horizontal, pl.min_horizontal, etc.
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "pl" and node.func.attr in _HORIZONTAL_FUNCS:
                return self._horizontal_func(node, node.func.attr)

        # pl.format(...)
        if self._is_pl_call(node, "format"):
            return self._pl_format(node)

        # Method chain on expression: expr.method(...)
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            receiver = node.func.value

            # .alias() — should have been stripped already, but handle defensively
            if method_name == "alias":
                return self.convert(receiver)

            # Opaque methods
            if method_name in _OPAQUE_METHODS:
                self._is_opaque = True
                self.expr_type = "opaque"
                base_text = self.convert(receiver)
                return f"{base_text}.{method_name}(...)"

            # .when() on a .then() result (chained when/then)
            if method_name == "when":
                return self._format_when_entry(node)

            # .then() / .otherwise()
            if method_name in ("then", "otherwise"):
                return self._format_when_entry(node)

            # Namespace accessors: .str.method(), .dt.method(), .list.method(), .struct.method()
            if isinstance(receiver, ast.Attribute) and receiver.attr in (
                "str",
                "dt",
                "list",
                "struct",
                "cat",
                "arr",
                "name",
            ):
                ns = receiver.attr
                base_text = self.convert(receiver.value)
                args_text = self._format_call_args(node)
                return f"{base_text}.{ns}.{method_name}({args_text})"

            # .over() — window function
            if method_name == "over":
                return self._over(node)

            # .cast(pl.Type)
            if method_name == "cast":
                base_text = self.convert(receiver)
                if node.args:
                    type_text = self.convert(node.args[0])
                    return f"{base_text}.cast({type_text})"
                return f"{base_text}.cast()"

            # General method call: .fill_null(), .round(), .abs(), .clip(), etc.
            base_text = self.convert(receiver)
            args_text = self._format_call_args(node)
            return f"{base_text}.{method_name}({args_text})"

        # Bare function call: some_func(args) — opaque / function_call
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            opaque_builtins = {"eval", "exec", "getattr", "setattr", "globals", "locals"}
            # Check if it's in the symbol table (user-defined function) or an opaque builtin
            if func_name in self._symbol_table or func_name in opaque_builtins:
                self._is_opaque = True
                self.expr_type = "opaque"
            else:
                self.expr_type = "function_call"
            # Still extract columns from arguments
            args_parts = []
            for a in node.args:
                args_parts.append(self.convert(a))
            for kw in node.keywords:
                args_parts.append(f"{kw.arg}={self.convert(kw.value)}")
            return f"{func_name}({', '.join(args_parts)})"

        # Fallback
        return ast.dump(node)

    def _over(self, node: ast.Call) -> str:
        """A window call as written: ``x.sum().over("g")``."""
        assert isinstance(node.func, ast.Attribute)
        base_text = self.convert(node.func.value)
        over_args = []
        for a in node.args:
            over_args.append(self.convert(a))
        for kw in node.keywords:
            over_args.append(self.convert(kw.value))
        return f"{base_text}.over({', '.join(over_args)})"

    def _pl_col(self, node: ast.Call) -> str:
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            col_name = node.args[0].value
            if col_name not in self.columns:
                self.columns.append(col_name)
            return self._column_text(col_name)
        elif node.args:
            # Dynamic column name — check symbol table
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in self._symbol_table:
                resolved = self._symbol_table[arg.id]
                if isinstance(resolved, ast.Constant) and isinstance(resolved.value, str):
                    col_name = resolved.value
                    if col_name not in self.columns:
                        self.columns.append(col_name)
                    return self._column_text(col_name)
            # Opaque
            self._is_opaque = True
            self.expr_type = "opaque"
            return self.convert(arg)
        return "?"

    def _pl_lit(self, node: ast.Call) -> str:
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                val = arg.value
                if val is None:
                    self.constants.append(None)
                    return "None"
                if val is True:
                    self.constants.append(True)
                    return "True"
                if val is False:
                    self.constants.append(False)
                    return "False"
                if isinstance(val, str):
                    self.constants.append(val)
                    return _quote_str(val)
                if isinstance(val, (int, float)):
                    self.constants.append(val)
                    return repr(val)
                self.constants.append(val)
                return repr(val)
            if isinstance(arg, ast.Name) and arg.id == "None":
                self.constants.append(None)
                return "None"
            if isinstance(arg, ast.Name) and arg.id == "True":
                self.constants.append(True)
                return "True"
            if isinstance(arg, ast.Name) and arg.id == "False":
                self.constants.append(False)
                return "False"
            # Could be a variable
            return self.convert(arg)
        return "lit()"

    def _pl_format(self, node: ast.Call) -> str:
        self.expr_type = "horizontal_func"
        args_parts = []
        for i, a in enumerate(node.args):
            if i == 0:
                # First arg is format string
                args_parts.append(self.convert(a))
            else:
                # Subsequent string args are column names in pl.format
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    col_name = a.value
                    if col_name not in self.columns:
                        self.columns.append(col_name)
                    args_parts.append(self._column_text(col_name))
                else:
                    args_parts.append(self.convert(a))
        return f"format({', '.join(args_parts)})"

    def _horizontal_func(self, node: ast.Call, func_name: str) -> str:
        self.expr_type = "horizontal_func"
        args_parts = []
        for a in node.args:
            # Check if it's a list of string column names
            if isinstance(a, ast.List):
                for elt in a.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        col_name = elt.value
                        if col_name not in self.columns:
                            self.columns.append(col_name)
                        args_parts.append(self._column_text(col_name))
                    else:
                        args_parts.append(self.convert(elt))
                continue
            args_parts.append(self.convert(a))
        kw_parts = []
        for kw in node.keywords:
            kw_parts.append(f"{kw.arg}={self.convert(kw.value)}")
        all_parts = args_parts + kw_parts
        return f"{func_name}({', '.join(all_parts)})"

    def _format_when_entry(self, node: ast.Call) -> str:
        """Render any entry point into a when/then/otherwise chain.

        Handles ``pl.when(...)`` chains, a ``.when()`` chained after a
        ``.then()``, and a top-level ``.then()``/``.otherwise()`` uniformly.
        """
        self.expr_type = "conditional"
        clauses = _collect_when_then_chain(node)
        return self._format_when_clauses(clauses)

    def _format_when_clauses(self, clauses: list[dict[str, Any]]) -> str:
        parts = []
        for clause in clauses:
            if "cond" in clause:
                # Save a sub-converter for nested when/then inside then
                cond_text = self.convert(clause["cond"]) if clause["cond"] else "?"

                # Check if then value contains a nested conditional
                if self._has_nested_when(clause["then"]):
                    sub_converter = self._nested_converter()
                    then_text = sub_converter.convert(clause["then"])
                    sub_expr = ParsedExpression(
                        target_column="",
                        expression_text=then_text,
                        expression_type="conditional",
                        referenced_columns=sub_converter.columns,
                        constants=sub_converter.constants,
                    )
                    self.sub_expressions.append(sub_expr)
                    # Merge columns
                    for c in sub_converter.columns:
                        if c not in self.columns:
                            self.columns.append(c)
                    self.constants.extend(sub_converter.constants)
                else:
                    then_text = self.convert(clause["then"])

                parts.append(f"when {cond_text} then {then_text}")
            elif "otherwise" in clause:
                otherwise_text = self.convert(clause["otherwise"])
                parts.append(f"otherwise {otherwise_text}")

        return " ".join(parts)

    def _nested_converter(self) -> _ExprConverter:
        """The converter a nested conditional's branch is written by."""
        return _ExprConverter(self._symbol_table)

    def _has_nested_when(self, node: ast.AST) -> bool:
        """Check if node contains a pl.when() call."""
        if isinstance(node, ast.Call):
            if self._is_pl_call(node, "when"):
                return True
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("then", "otherwise", "when"):
                    return True
                return self._has_nested_when(node.func.value)
            for a in node.args:
                if self._has_nested_when(a):
                    return True
        return False

    def _format_call_args(self, node: ast.Call) -> str:
        """Format all args and kwargs of a Call node."""
        parts = []
        for a in node.args:
            parts.append(self.convert(a))
        for kw in node.keywords:
            if kw.arg:
                parts.append(f"{kw.arg}={self.convert(kw.value)}")
            else:
                parts.append(f"**{self.convert(kw.value)}")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Alias / target extraction helpers
# ---------------------------------------------------------------------------


def _strip_alias(
    node: ast.AST,
    symbol_table: dict[str, ast.AST] | None = None,
) -> tuple[ast.AST, str | None]:
    """If node is ``expr.alias(name)``, return (expr, name). Otherwise (node, None)."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "alias"
    ):
        alias_name = None
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                alias_name = arg.value
            elif isinstance(arg, ast.JoinedStr):
                # f-string alias — try to evaluate
                alias_name = _try_eval_fstring(arg, symbol_table)
        return node.func.value, alias_name
    return node, None


def _try_eval_fstring(
    node: ast.JoinedStr,
    symbol_table: dict[str, ast.AST] | None = None,
) -> str | None:
    """Best-effort static evaluation of an f-string using the symbol table."""
    if symbol_table is None:
        return None
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value))
        elif isinstance(v, ast.FormattedValue):
            val_node = v.value
            if isinstance(val_node, ast.Name) and val_node.id in symbol_table:
                resolved = symbol_table[val_node.id]
                if isinstance(resolved, ast.Constant):
                    resolved_val: Any = resolved.value
                else:
                    return None
            elif isinstance(val_node, ast.Constant):
                resolved_val = val_node.value
            else:
                return None
            # Apply the conversion (!r/!s/!a) and format spec so the resolved
            # alias matches Polars' actual output column name rather than a
            # bare str() of the value.
            if v.conversion == 114:  # !r
                resolved_val = repr(resolved_val)
            elif v.conversion == 115:  # !s
                resolved_val = str(resolved_val)
            elif v.conversion == 97:  # !a
                resolved_val = ascii(resolved_val)
            if v.format_spec is not None:
                spec = _static_format_spec(v.format_spec, symbol_table)
                if spec is None:
                    return None
                try:
                    parts.append(format(resolved_val, spec))
                except (ValueError, TypeError):
                    return None
            else:
                parts.append(str(resolved_val))
        else:
            return None
    return "".join(parts)


def _static_format_spec(
    spec: ast.expr,
    symbol_table: dict[str, ast.AST],
) -> str | None:
    """Statically resolve an f-string format spec to text, or None if dynamic."""
    if not isinstance(spec, ast.JoinedStr):
        return None
    out = []
    for p in spec.values:
        if isinstance(p, ast.Constant):
            out.append(str(p.value))
        elif isinstance(p, ast.FormattedValue):
            inner = p.value
            if isinstance(inner, ast.Name) and inner.id in symbol_table:
                resolved = symbol_table[inner.id]
                if isinstance(resolved, ast.Constant):
                    out.append(str(resolved.value))
                    continue
            if isinstance(inner, ast.Constant):
                out.append(str(inner.value))
                continue
            return None
        else:
            return None
    return "".join(out)


def _infer_auto_name(node: ast.AST) -> str | None:
    """Guess the auto-generated column name for an expression without alias.
    Polars uses the first column name for simple expressions."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "col"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pl"
            ):
                if node.args and isinstance(node.args[0], ast.Constant):
                    val = node.args[0].value
                    return val if isinstance(val, str) else None
    if isinstance(node, ast.BinOp):
        return _infer_auto_name(node.left)
    return None


# ---------------------------------------------------------------------------
# Symbol table builder (variable resolution)
# ---------------------------------------------------------------------------


_OWN_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _names_assigned_by(node: ast.AST) -> set[str]:
    """The names *node* assigns a value in the code's own scope.

    An import binds a module and a definition a callable, so neither counts.
    """
    if isinstance(node, _OWN_SCOPES):
        return set()
    if isinstance(node, ast.Name):
        return set() if isinstance(node.ctx, ast.Load) else {node.id}
    names: set[str] = set()
    for child in ast.iter_child_nodes(node):
        names |= _names_assigned_by(child)
    return names


def _simple_binding(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """``name = value`` or ``name: type = value``, the bindings a table can follow."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        if isinstance(target, ast.Name):
            return target.id, stmt.value
    if (
        isinstance(stmt, ast.AnnAssign)
        and stmt.value is not None
        and isinstance(stmt.target, ast.Name)
    ):
        return stmt.target.id, stmt.value
    return None


def _statement_index(stmts: list[ast.stmt], node: ast.AST) -> int:
    """The index of the top-level statement *node* sits in."""
    position = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
    for index, stmt in enumerate(stmts):
        start = (stmt.lineno, stmt.col_offset)
        end = (stmt.end_lineno or stmt.lineno, stmt.end_col_offset or 0)
        if start <= position <= end:
            return index
    return len(stmts)


def _bind_statement(stmt: ast.stmt, table: dict[str, ast.AST], unprovable: set[str]) -> None:
    """Fold one top-level statement into the names the code has bound.

    A simple assignment binds its value with the names it reads resolved as
    they stand, so a later rebinding never changes it; a binding that reads a
    name whose value is unknown is unknown too. Any other assignment (inside
    control flow, augmented, unpacking) leaves its names unknown.
    """
    binding = _simple_binding(stmt)
    if binding is None:
        for name in _names_assigned_by(stmt):
            table.pop(name, None)
            unprovable.add(name)
        return
    name, value = binding
    if _is_df_assignment(value):
        return
    resolved = _substitute_names_in_ast(value, table)
    table[name] = resolved
    if any(isinstance(sub, ast.Name) and sub.id in unprovable for sub in ast.walk(resolved)):
        unprovable.add(name)
    else:
        unprovable.discard(name)


@lru_cache(maxsize=256)
def _calls_with_bindings(
    code: str,
) -> tuple[tuple[ast.Call, int | None, dict[str, ast.AST], frozenset[str]], ...]:
    """Every ``with_columns``/``select`` call of *code* in execution order, with
    the names bound before its statement and those whose value there is unknown.

    The tables are shared through the cache: callers read them, never change them.
    """
    tree = _cached_parse(code)
    stmts = tree.body
    calls = _find_with_columns_calls(tree)
    indices = [_statement_index(stmts, call) for call, _lineno in calls]
    wanted = set(indices)
    snapshots: dict[int, tuple[dict[str, ast.AST], frozenset[str]]] = {}
    table: dict[str, ast.AST] = {}
    unprovable: set[str] = set()
    for index in range(len(stmts) + 1):
        if index in wanted:
            snapshots[index] = (dict(table), frozenset(unprovable))
        if index < len(stmts):
            _bind_statement(stmts[index], table, unprovable)
    return tuple((call, lineno, *snapshots[index]) for (call, lineno), index in zip(calls, indices))


def _is_df_assignment(value: ast.AST) -> bool:
    """Check if value looks like df.with_columns(...) or similar DF operation."""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        if value.func.attr in (
            "with_columns",
            "select",
            "filter",
            "sort",
            "group_by",
            "join",
            "pipe",
            "rename",
            "drop",
            "unique",
            "sample",
        ):
            return True
    return False


def _find_control_flow_assigned_vars(stmts: list[ast.stmt]) -> set[str]:
    """Find variable names assigned inside control flow blocks."""
    result: set[str] = set()
    for stmt in stmts:
        if isinstance(stmt, _CONTROL_FLOW_STATEMENT_TYPES):
            _collect_assigned_names(stmt, result)
        if hasattr(ast, "Match") and isinstance(stmt, ast.Match):
            _collect_assigned_names(stmt, result)
    return result


def _collect_assigned_names(node: ast.AST, names: set[str]) -> None:
    """Collect all assigned variable names within a subtree."""
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            for t in child.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(child, ast.AugAssign):
            if isinstance(child.target, ast.Name):
                names.add(child.target.id)


def _has_control_flow_wrapping_target(stmts: list[ast.stmt], target_column: str) -> bool:
    """Check if with_columns producing target_column is inside control flow."""
    for stmt in stmts:
        if isinstance(stmt, _CONTROL_FLOW_STATEMENT_TYPES):
            if _contains_with_columns_producing(stmt, target_column):
                return True
        if hasattr(ast, "Match") and isinstance(stmt, ast.Match):
            if _contains_with_columns_producing(stmt, target_column):
                return True
    return False


# A ``with_columns``/``select`` call takes its expressions one by one or as
# one list or tuple of them.
_EXPRESSION_SEQUENCES = (ast.List, ast.Tuple)


def _spread(args: list[ast.expr]) -> list[ast.expr]:
    """A call's positional arguments with a literal list or tuple spread into its items."""
    items: list[ast.expr] = []
    for arg in args:
        if isinstance(arg, _EXPRESSION_SEQUENCES):
            items.extend(arg.elts)
        else:
            items.append(arg)
    return items


def _contains_with_columns_producing(node: ast.AST, target_column: str) -> bool:
    """Check if any node in subtree has with_columns that produces target_column."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in _COLUMN_PRODUCER_METHODS:
                # Check if any arg produces the target column
                for arg in _spread(child.args):
                    _, alias = _strip_alias(arg)
                    if alias == target_column:
                        return True
                for kw in child.keywords:
                    if kw.arg == target_column:
                        return True
    return False


# ---------------------------------------------------------------------------
# with_columns extraction
# ---------------------------------------------------------------------------


def _source_span(node: ast.AST) -> tuple[int, int, int, int]:
    return (
        getattr(node, "lineno", 0) or 0,
        getattr(node, "col_offset", 0) or 0,
        getattr(node, "end_lineno", 0) or 0,
        getattr(node, "end_col_offset", 0) or 0,
    )


def _find_with_columns_calls(tree: ast.Module) -> list[tuple[ast.Call, int | None]]:
    """Find all with_columns()/select() calls, ordered by execution order.

    ``ast.walk`` yields nodes breadth-first (parent before child), which for a
    chained ``df.with_columns(A).with_columns(B)`` visits the OUTER call (B)
    before the nested inner call (A). Consumers use "last match wins" to pick
    the effective definition, so the list must be in execution order. Chained
    calls share a start position but the outer/last-applied call spans further,
    so ordering by (start, end) source span puts the earlier-applied call first
    and the effective (outermost) call last. Separate statements order by line.
    """
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _COLUMN_PRODUCER_METHODS:
                lineno = getattr(node, "lineno", None)
                results.append((node, lineno))
    results.sort(key=lambda item: _source_span(item[0]))
    return results


def _extract_expressions_from_with_columns(
    call_node: ast.Call,
    symbol_table: dict[str, ast.AST],
) -> list[tuple[ast.AST, str | None, int | None, str | None]]:
    """Extract (expression_ast, alias_name, lineno, bound_name) from a with_columns call.

    ``bound_name`` is the variable an argument names when the expression is that
    variable's binding, already resolved where it was bound; ``None`` for an
    expression written in the call.
    """
    results: list[tuple[ast.AST, str | None, int | None, str | None]] = []
    lineno = getattr(call_node, "lineno", None)

    for arg in call_node.args:
        actual_arg = arg.value if isinstance(arg, ast.Starred) else arg
        # A variable naming a list of expressions, or one expression
        if isinstance(actual_arg, ast.Name) and actual_arg.id in symbol_table:
            bound = actual_arg.id
            resolved = symbol_table[bound]
            if isinstance(resolved, _EXPRESSION_SEQUENCES):
                for elt in resolved.elts:
                    expr_node, alias_name = _strip_alias(elt, symbol_table)
                    results.append((expr_node, alias_name, lineno, bound))
                continue
            expr_node, alias_name = _strip_alias(resolved, symbol_table)
            results.append((expr_node, alias_name, lineno, bound))
            continue

        # Handle list comprehension — opaque
        if isinstance(actual_arg, ast.ListComp):
            # Can't resolve statically
            continue

        for item in _spread([actual_arg]):
            expr_node, alias_name = _strip_alias(item, symbol_table)
            if alias_name is None:
                alias_name = _infer_auto_name(expr_node)
            results.append((expr_node, alias_name, lineno, None))

    for kw in call_node.keywords:
        if kw.arg is not None:
            results.append((kw.value, kw.arg, lineno, None))

    return results


# ---------------------------------------------------------------------------
# Main parse_expression
# ---------------------------------------------------------------------------


def parse_expression(code: str, target_column: str) -> ParsedExpression | None:
    """Parse a code string and extract a :class:`ParsedExpression` for *target_column*.

    Returns an opaque expression (never raises) if parsing fails. This is the
    honest "we could not statically understand this code" signal (it carries the
    original source text, not a laundered value), and the enrichment caller
    surfaces it as-is; it is deliberately distinct from the value-computation
    path, which fails loud rather than substituting an observed value.
    """
    try:
        return _parse_expression_impl(code, target_column)
    except Exception:
        return _opaque(target_column, code if code else "")


@dataclass(frozen=True)
class _Definition:
    """The expression that last assigns a column, and the names in force where it runs.

    ``symbol_table`` resolves the names the expression reads: none when it is
    a variable's binding (``bound_name``), which was resolved where it was bound.
    """

    node: ast.AST
    source_line: int | None
    symbol_table: dict[str, ast.AST]
    unprovable: frozenset[str]
    bound_name: str | None

    def unknown_name(self, unknown: Collection[str]) -> str | None:
        """The first name the expression reads whose value is not known, if any.

        A binding that captured an unknown value is unknown itself, so reading
        it counts even after the captured name is bound again.
        """
        if self.bound_name is not None and self.bound_name in unknown:
            return self.bound_name
        return next(
            (
                sub.id
                for sub in ast.walk(self.node)
                if isinstance(sub, ast.Name) and sub.id in unknown
            ),
            None,
        )

    def reads_unknown(self, unknown: Collection[str]) -> bool:
        """Whether the expression reads a name whose value is not known."""
        return self.unknown_name(unknown) is not None


def _root_expression(definition: _Definition) -> ast.AST:
    """The expression *definition*'s node stands for: a variable's binding when it names one."""
    node = definition.node
    if isinstance(node, ast.Name) and node.id in definition.symbol_table:
        node = _strip_alias(definition.symbol_table[node.id])[0]
    return node


def _find_definition(code: str, target_column: str) -> _Definition | None:
    """The last ``with_columns``/``select`` expression writing *target_column*.

    An unaliased expression is named after its leftmost column, as Polars
    names it. The expression's names resolve through the bindings in force at
    its own statement. *code* must parse.
    """
    best: _Definition | None = None
    for call, _lineno, table, unprovable in _calls_with_bindings(code):
        for expr_node, alias_name, line, bound in _extract_expressions_from_with_columns(
            call, table
        ):
            if alias_name == target_column:
                best = _Definition(expr_node, line, {} if bound else table, unprovable, bound)
    return best


def _definition_in(code: str, target_column: str) -> _Definition | None:
    code = code.lstrip("\ufeff")
    try:
        _cached_parse(code)
    except SyntaxError:
        return None
    return _find_definition(code, target_column)


def _parse_expression_impl(code: str, target_column: str) -> ParsedExpression | None:
    if not code or not code.strip():
        return _opaque(target_column, "")

    # Strip BOM
    code = code.lstrip("\ufeff")

    try:
        tree = _cached_parse(code)
    except SyntaxError:
        return _opaque(target_column, code)

    stmts = tree.body
    if not stmts:
        return _opaque(target_column, "")

    # Check if the with_columns producing target is inside control flow
    if _has_control_flow_wrapping_target(stmts, target_column):
        return _opaque(target_column, code)

    if not _find_with_columns_calls(tree):
        # Check for pipe or other non-with_columns patterns
        return _opaque(target_column, code)

    definition = _find_definition(code, target_column)
    if definition is None:
        return _opaque(target_column, "")

    # A name assigned inside control flow, or rebound before the call in a way
    # the code does not show statically, has no value the trace can prove.
    if definition.reads_unknown(_find_control_flow_assigned_vars(stmts) | definition.unprovable):
        return _opaque(target_column, code, definition.source_line)

    # Convert the expression AST to text
    converter = _ExprConverter(definition.symbol_table)
    expression_text = converter.convert(definition.node)
    expr_type = "opaque" if converter._is_opaque else converter.expr_type

    return ParsedExpression(
        target_column=target_column,
        expression_text=expression_text,
        expression_type=expr_type,
        referenced_columns=converter.columns,
        constants=converter.constants,
        sub_expressions=converter.sub_expressions,
        source_line=definition.source_line,
    )


def _substitute_names_in_ast(node: ast.AST, table: dict[str, ast.AST]) -> ast.AST:
    """*node* with every name *table* binds replaced by its value, as a new tree.

    Each substituted value keeps the source position of the name it replaces,
    so the result compiles.
    """
    if not table:
        return node
    return cast(ast.AST, _NameResolver(table).visit(copy.deepcopy(node)))


# ---------------------------------------------------------------------------
# evaluate_expression
# ---------------------------------------------------------------------------


def evaluate_expression(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    preamble_ns: dict[str, Any] | None = None,
    *,
    row: pl.DataFrame | None = None,
) -> EvaluatedExpression:
    """Parse *code*, substitute *row_values* for referenced columns, and have
    Polars compute the result on the traced row.

    *row* is the traced row as a one-row frame in the dtypes the pipeline gave
    it. Without it the row is built from *row_values*, which must then be native
    Python values for Polars to infer dtypes from. A frame holding no row means
    the trace could not recover it, and the result is not computable.

    Polars computes an expression the row-locality classifier proves reads only
    its own row. Otherwise ``result_value`` stays ``None`` and
    ``not_computable_reason`` says why: the expression needs other rows (a
    window, aggregation, shift, rank or cumulative form, named), it uses a name
    the trace cannot resolve, or no expression assigns the column. A Polars
    failure on a computable expression propagates to the enrichment caller,
    which records a visible error marker on the step. Fail loud, never guess.
    """
    return _evaluate_expression_impl(code, target_column, row_values, preamble_ns, row)


def wrap_node_code(code: str) -> str:
    """Node code as the module the parser reads, as the executor runs it.

    A dot chain (``.filter(...)``, indented or not) continues ``df``; a bare
    expression becomes ``df``'s value; a leading BOM goes. Code that starts
    with ``df`` or assigns on its first line is left as written, so the
    wrapping never corrupts an assignment. The one wrapper every reader of a
    node's code uses.
    """
    code = code.lstrip("\ufeff")
    head = code.strip()
    if head.startswith("."):
        return f"df = (df\n{code})"
    first_line_prefix = head.split("\n", 1)[0].split("(", 1)[0]
    if head and not head.startswith("df") and "=" not in first_line_prefix:
        return f"df = (\n{code}\n)"
    return code


_GLOBAL_CONSTANT_READ = re.compile(r"\bglobal_constants\.([A-Za-z][A-Za-z0-9_]*)\b")


def _global_constant_values(namespace: Mapping[str, Any], code: str) -> dict[str, Any]:
    """The scalar values of the constants *code* reads that resolve in *namespace*."""
    constants = namespace.get("global_constants")
    if constants is None:
        return {}
    values: dict[str, Any] = {}
    for name in set(_GLOBAL_CONSTANT_READ.findall(code)):
        try:
            value = getattr(constants, name)
        except Exception:  # noqa: BLE001 - an unresolved read stays as written in the trace
            continue
        if isinstance(value, (bool, int, float, str, _dt.date)):
            values[name] = value
    return values


class _InlineGlobalConstants(ast.NodeTransformer):
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = values

    def _date_read(self, node: ast.AST) -> _dt.date | None:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "global_constants"
            and isinstance(self._values.get(node.attr), _dt.date)
        ):
            return cast(_dt.date, self._values[node.attr])
        return None

    @staticmethod
    def _date_expression(value: _dt.date, node: ast.AST) -> ast.AST:
        # A date is a literal of the run: Polars builds it per row.
        built = f"pl.date({value.year}, {value.month}, {value.day})"
        return ast.copy_location(ast.parse(built, mode="eval").body, node)

    @staticmethod
    def _is_date_dtype(node: ast.AST | None) -> bool:
        """Whether *node* is no dtype at all, ``None`` or ``pl.Date``: what a date infers."""
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return True
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "Date"
            and isinstance(node.value, ast.Name)
            and node.value.id == "pl"
        )

    def visit_Call(self, node: ast.Call) -> ast.AST:
        # The step renderer writes a constant in expression position as
        # ``pl.lit(global_constants.<name>)``; a date is already an expression,
        # so that call becomes it. A call that names another dtype is left as
        # written, so the trace reports it as not computable rather than
        # guessing how ``pl.lit`` would apply the dtype.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "lit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pl"
        ):
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            argument = node.args[0] if node.args else keywords.get("value")
            value = None if argument is None else self._date_read(argument)
            if value is not None:
                dtype = node.args[1] if len(node.args) > 1 else keywords.get("dtype")
                equivalent = (
                    len(node.args) <= 2
                    and set(keywords) <= {"value", "dtype"}
                    and self._is_date_dtype(dtype)
                )
                return self._date_expression(value, node) if equivalent else node
        return self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        date = self._date_read(node)
        if date is not None:
            return self._date_expression(date, node)
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "global_constants"
            and node.attr in self._values
        ):
            return ast.copy_location(ast.Constant(self._values[node.attr]), node)
        return self.generic_visit(node)


def _evaluate_expression_impl(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    preamble_ns: dict[str, Any] | None,
    row: pl.DataFrame | None,
) -> EvaluatedExpression:
    code = wrap_node_code(code)
    namespace = dict(preamble_ns or {})
    constant_values = _global_constant_values(namespace, code)

    # Literal preamble constants display beside the row's values; the row wins.
    literals = {
        name: value for name, value in namespace.items() if _literal_node(value) is not None
    }
    effective_row = {**literals, **row_values}

    parsed = parse_expression(code, target_column)
    if parsed is None:
        parsed = _opaque(target_column, "")
    definition = _definition_in(code, target_column)
    resolved = None if definition is None else _resolve_names(definition)

    # A window is the located expression's own: another column's window in the
    # same node says nothing about this one. A variable it reads counts as
    # what the variable is bound to.
    is_window = resolved is not None and _is_window(resolved)
    referenced_columns = list(parsed.referenced_columns)
    if resolved is not None and is_window:
        for column in _window_partition_columns(resolved):
            if column not in referenced_columns:
                referenced_columns.append(column)

    # Build input_values: only cols that are referenced
    input_values = {k: v for k, v in effective_row.items() if k in referenced_columns}

    unprovable = definition.unprovable if definition is not None else frozenset()
    if definition is None:
        substituted_text = parsed.expression_text
    else:
        substituted_text = _ValueRenderer(
            definition.symbol_table,
            columns=_display_values(input_values, row),
            names={name: value for name, value in literals.items() if name not in unprovable},
            constants=constant_values,
            root=_root_expression(definition),
        ).convert(definition.node)

    # A name the code rebound out of sight before the call, or a binding that
    # captured one, has no value to compute with, whatever the preamble binds
    # the name to.
    unknown = None if definition is None else definition.unknown_name(unprovable)
    evaluation_ns = {name: value for name, value in namespace.items() if name not in unprovable}
    traced_row = _native_row(row_values) if row is None else row
    node = None if resolved is None else _inline_global_constants(resolved, constant_values)
    computed = (
        _RowValue(reason="expression_not_located")
        if node is None
        else _RowValue(reason=f"unresolved_name: {unknown}")
        if unknown is not None
        else _row_value(node, traced_row, evaluation_ns)
    )

    branches: dict[str, Any] = {}
    if node is not None and unknown is None and parsed.expression_type == "conditional":
        branches = _branch_selection(node, traced_row, evaluation_ns) or {}

    return EvaluatedExpression(
        target_column=parsed.target_column,
        expression_text=parsed.expression_text,
        expression_type="window" if is_window else parsed.expression_type,
        referenced_columns=referenced_columns,
        constants=parsed.constants,
        sub_expressions=parsed.sub_expressions,
        source_line=parsed.source_line,
        substituted_text=substituted_text,
        result_value=computed.value,
        not_computable_reason=computed.reason,
        input_values=input_values,
        taken_branch=branches.get("taken_branch"),
        taken_branch_index=branches.get("taken_branch_index"),
        dimmed_branches=branches.get("dimmed_branches", []),
        nested_branches=branches.get("nested_branches", []),
    )


def _inline_global_constants(node: ast.AST, values: Mapping[str, Any]) -> ast.AST:
    """*node* with each resolved ``global_constants.<name>`` read replaced by its literal.

    A constant is a scalar literal of the run, so inlining it lets the row-local
    evaluation compute the formula; the displayed formula keeps the name.
    """
    if not values:
        return node
    return cast(ast.AST, ast.fix_missing_locations(_InlineGlobalConstants(values).visit(node)))


def _is_over_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "over"
    )


def _is_window(node: ast.AST) -> bool:
    """Whether *node* computes a window: an ``.over()`` call anywhere in it."""
    return any(_is_over_call(sub) for sub in ast.walk(node))


def _partition_arguments(node: ast.Call) -> list[ast.expr]:
    """What an ``.over()`` call partitions by, a list spread into its items."""
    arguments = [*node.args, *(kw.value for kw in node.keywords if kw.arg == "partition_by")]
    items: list[ast.expr] = []
    for argument in arguments:
        if isinstance(argument, (ast.List, ast.Tuple)):
            items.extend(argument.elts)
        else:
            items.append(argument)
    return items


def _string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _window_partition_columns(node: ast.AST) -> list[str]:
    """The columns the windows in *node* partition by when named as strings.

    A ``pl.col`` partition is a column reference the converter already counts.
    """
    columns: list[str] = []
    for sub in ast.walk(node):
        if not _is_over_call(sub):
            continue
        assert isinstance(sub, ast.Call)
        for argument in _partition_arguments(sub):
            name = _string_constant(argument)
            if name is not None and name not in columns:
                columns.append(name)
    return columns


def _window_description(node: ast.Call, symbol_table: dict[str, ast.AST]) -> str:
    """A window call in words, from its syntax tree: ``sum of premium over region``."""
    names = _ExprConverter(symbol_table)

    def written(argument: ast.AST) -> str:
        name = _string_constant(argument)
        return name if name is not None else names.convert(argument)

    partitions = ", ".join(written(argument) for argument in _partition_arguments(node))
    assert isinstance(node.func, ast.Attribute)
    receiver = node.func.value
    computed = names.convert(receiver)
    if (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Attribute)
        and not names._is_pl_call(receiver, "col")
    ):
        method = receiver.func.attr
        base = receiver.func.value
        if isinstance(base, ast.Name) and base.id == "pl":
            # ``pl.sum("x")``: the function's own arguments name what it reads.
            operand = ", ".join(written(argument) for argument in receiver.args)
        else:
            operand = names.convert(base)
            if names._operand_prec(base) < 100:
                operand = f"({operand})"
        computed = f"{method} of {operand}" if operand else method
    return f"{computed} over {partitions}" if partitions else computed


class _ValueRenderer(_ExprConverter):
    """A located expression written again with the traced row's values in it.

    Only the syntax tree's column references, its free names bound to literal
    constants and its global constant reads take a value: quoted literals,
    method names and keywords stay as written, and every operator keeps the
    grouping the tree gives it. A window is described in words, since one
    row's value would misstate what it computed.
    """

    def __init__(
        self,
        symbol_table: dict[str, ast.AST],
        *,
        columns: Mapping[str, Any],
        names: Mapping[str, Any],
        constants: Mapping[str, Any],
        root: ast.AST | None = None,
    ) -> None:
        super().__init__(symbol_table)
        # The expression the rendering stands for: a window there is the whole
        # formula, anywhere else a part of it.
        self._root = root
        self._column_values = columns
        self._name_values = names
        self._constant_values = constants

    def _nested_converter(self) -> _ExprConverter:
        return _ValueRenderer(
            self._symbol_table,
            columns=self._column_values,
            names=self._name_values,
            constants=self._constant_values,
        )

    def _column_text(self, name: str) -> str:
        if name in self._column_values:
            return _format_value(self._column_values[name])
        return name

    def _free_name_text(self, name: str) -> str:
        if name in self._name_values:
            return _format_value(self._name_values[name])
        return name

    def _attribute(self, node: ast.Attribute) -> str:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "global_constants"
            and node.attr in self._constant_values
        ):
            return _format_value(self._constant_values[node.attr])
        return super()._attribute(node)

    def _over(self, node: ast.Call) -> str:
        description = _window_description(node, self._symbol_table)
        return description if node is self._root else f"({description})"


def _display_values(values: dict[str, Any], row: pl.DataFrame | None) -> dict[str, Any]:
    """*values* as their columns' dtypes hold them, for display.

    A Float32 value arrives widened to a Python float (``528.0900268554688``);
    shown with its float32 digits (``528.09``) it reads as the pipeline stored
    it. Only the text changes: the values computed on stay exact.
    """
    if row is None:
        return values
    return {
        name: float(str(np.float32(value)))
        if isinstance(value, float) and row.schema.get(name) == pl.Float32
        else value
        for name, value in values.items()
    }


def _format_value(val: Any) -> str:
    """Format a value for display in substituted text."""
    if val is None:
        return "None"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        if math.isnan(val):
            return "NaN"
        if math.isinf(val):
            return "inf" if val > 0 else "-inf"
        return str(val)
    if isinstance(val, str):
        return _quote_str(val)
    return str(val)


# ---------------------------------------------------------------------------
# Polars evaluation on the traced row
# ---------------------------------------------------------------------------

_TRACE_VALUE_COLUMN = "__haute_trace_value__"

TRACED_ROW_UNAVAILABLE = "traced_row_unavailable"
"""Reason for a value the trace could not recover the traced row to compute."""


@dataclass(frozen=True)
class _RowValue:
    """An expression's value on the traced row, or why one row cannot give it."""

    value: Any = None
    reason: str | None = None


def _native_row(row_values: Mapping[str, Any]) -> pl.DataFrame:
    """One row built from native Python values, with dtypes Polars infers from them."""
    return pl.DataFrame([dict(row_values)])


@dataclass(frozen=True)
class AssignmentPhases:
    """The node's assignments on either side of the call that assigns one column.

    Every expression in one ``with_columns`` call reads the frame as it was
    before that call, so a column assigned by the same or a later call is read
    at its earlier value, not the node's output. ``unresolved`` is set when an
    expression in that call or a later one writes a column whose name cannot be
    determined statically, so any column may be among them;
    ``unresolved_before`` when such a write comes in an earlier call, so any
    column may have been rewritten before the call reads it.
    """

    before: frozenset[str]
    at_or_after: frozenset[str]
    unresolved: bool = False
    unresolved_before: bool = False


# Namespaces whose methods keep the expression's name (``struct.field`` and
# ``name.*`` rename it).
_NAME_PRESERVING_NAMESPACES = frozenset({"arr", "bin", "cat", "dt", "list", "str"})


def _implicit_output_name(node: ast.AST) -> str | None:
    """The column an unaliased expression writes, when its leftmost input names it.

    Polars names such an expression after its leftmost input column: methods,
    arithmetic and the name-keeping namespaces keep that name. An expression
    that renames, selects several columns, or starts from another function is
    left unknown.
    """
    current = node
    while True:
        if isinstance(current, ast.BinOp):
            current = current.left
            continue
        if isinstance(current, ast.UnaryOp):
            current = current.operand
            continue
        if not isinstance(current, ast.Call) or not isinstance(current.func, ast.Attribute):
            return None
        receiver = current.func.value
        if current.func.attr == "alias":
            alias = current.args[0] if current.args else None
            if isinstance(alias, ast.Constant) and isinstance(alias.value, str):
                return alias.value
            return None
        if isinstance(receiver, ast.Name) and receiver.id == "pl":
            if current.func.attr == "lit":
                return "literal"
            if current.func.attr != "col" or current.keywords or len(current.args) != 1:
                return None
            name = current.args[0]
            if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
                return None
            return None if name.value.startswith("^") else name.value
        if isinstance(receiver, ast.Attribute) and receiver.attr in _NAME_PRESERVING_NAMESPACES:
            current = receiver.value
            continue
        current = receiver


def _call_writes(call: ast.Call, symbol_table: dict[str, ast.AST]) -> tuple[frozenset[str], bool]:
    """The columns one ``with_columns`` call writes, and whether some write has no static name.

    Only an explicit name counts: a keyword, a literal ``.alias()``, or the
    strict :func:`_implicit_output_name`. The display parser's first-column
    guess never does, since ``pl.col("a", "b") + 1`` writes both columns.
    """
    items: list[ast.AST] = []
    unknown = False
    for arg in call.args:
        value = arg.value if isinstance(arg, ast.Starred) else arg
        bound = symbol_table.get(value.id) if isinstance(value, ast.Name) else None
        if isinstance(bound, _EXPRESSION_SEQUENCES):
            items.extend(bound.elts)
        elif isinstance(value, _EXPRESSION_SEQUENCES):
            items.extend(value.elts)
        elif isinstance(arg, ast.Starred) or isinstance(value, (ast.ListComp, ast.GeneratorExp)):
            unknown = True
        else:
            items.append(bound if bound is not None else value)
    names: set[str] = set()
    for item in items:
        expr, alias = _strip_alias(item, symbol_table)
        name = (
            alias
            if alias is not None
            else None
            if expr is not item
            else _implicit_output_name(expr)
        )
        if name is None:
            unknown = True
        else:
            names.add(name)
    for keyword in call.keywords:
        if keyword.arg is None:
            unknown = True
        else:
            names.add(keyword.arg)
    return frozenset(names), unknown


def assignment_phases(code: str, target_column: str) -> AssignmentPhases | None:
    """Split the columns *code* assigns around the last call assigning *target_column*."""
    code = wrap_node_code(code)
    try:
        _cached_parse(code)
    except SyntaxError:
        return None
    calls = [_call_writes(call, table) for call, _line, table, _ in _calls_with_bindings(code)]
    target_calls = [
        index for index, (names, _unknown) in enumerate(calls) if target_column in names
    ]
    if not target_calls:
        return None
    last = target_calls[-1]
    return AssignmentPhases(
        before=frozenset().union(*(names for names, _unknown in calls[:last])),
        at_or_after=frozenset().union(*(names for names, _unknown in calls[last:])),
        unresolved=any(unknown for _names, unknown in calls[last:]),
        unresolved_before=any(unknown for _names, unknown in calls[:last]),
    )


def _locate_defining_expression(code: str, target_column: str) -> ast.AST | None:
    """The expression that last assigns *target_column*, with same-node names resolved."""
    definition = _definition_in(code, target_column)
    return None if definition is None else _resolve_names(definition)


def _resolve_names(definition: _Definition) -> ast.AST:
    """*definition*'s expression with the names bound where it runs replaced by their values."""
    return cast(
        ast.AST,
        _NameResolver(definition.symbol_table).visit(copy.deepcopy(definition.node)),
    )


def assigns_column(code: str, column: str) -> bool:
    """Whether *code* assigns *column*: a ``with_columns`` or ``select`` call writes it.

    Read from the syntax tree, so a comment, a string or a comparison naming
    the column is not an assignment.
    """
    return bool(code.strip()) and (
        assignment_phases(code, column) is not None
        or _locate_defining_expression(code, column) is not None
    )


def renamed_from(code: str, column: str) -> str | None:
    """The column a ``rename`` call's literal mapping renames to *column*.

    Only a ``rename`` call's own mapping counts, never another call's dict
    (``replace({...})``). The last such call wins.
    """
    try:
        tree = _cached_parse(code.lstrip("\ufeff"))
    except SyntaxError:
        return None
    calls = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rename"
        ),
        key=_source_span,
    )
    original: str | None = None
    for call in calls:
        mapping = (
            call.args[0]
            if call.args
            else next((kw.value for kw in call.keywords if kw.arg == "mapping"), None)
        )
        if not isinstance(mapping, ast.Dict):
            continue
        for key, value in zip(mapping.keys, mapping.values):
            old = None if key is None else _string_constant(key)
            if old is not None and _string_constant(value) == column:
                original = old
    return original


def copied_from(code: str, column: str) -> str | None:
    """The column *code*'s last assignment of *column* copies unchanged: ``pl.col("old")``."""
    node = _locate_defining_expression(code, column)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "col"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pl"
        and len(node.args) == 1
        and not node.keywords
    ):
        original = _string_constant(node.args[0])
        if original is not None and original != column:
            return original
    return None


class _NameResolver(ast.NodeTransformer):
    """Replace names bound earlier in the node's code with the values bound to them."""

    def __init__(self, bindings: Mapping[str, ast.AST]) -> None:
        self._bindings = bindings

    def visit_Name(self, node: ast.Name) -> ast.AST:
        bound = self._bindings.get(node.id)
        if bound is None or not isinstance(node.ctx, ast.Load):
            return node
        return ast.copy_location(copy.deepcopy(bound), node)


def _literal_node(value: object) -> ast.expr | None:
    """*value* as the literal the code could have written, if it is one."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return ast.Constant(value=value)
    if isinstance(value, (list, tuple)):
        elements = [_literal_node(item) for item in value]
        if all(element is not None for element in elements):
            items = cast(list[ast.expr], elements)
            return ast.List(elts=items) if isinstance(value, list) else ast.Tuple(elts=items)
    return None


def _row_value(node: ast.AST, row: pl.DataFrame, namespace: Mapping[str, Any]) -> _RowValue:
    """*node*'s value on the traced *row*, computed by Polars.

    Only an expression the row-locality classifier proves reads nothing but its
    own row is computed here: a window, aggregation, shift, rank, or cumulative
    form evaluated on one row would show a value the pipeline never produced,
    so it is reported as not computable from one row instead, naming the
    operator that needs other rows. Literal constants in *namespace* are
    inlined first so they classify as the literals they are.
    """
    if row.height != 1:
        return _RowValue(reason=TRACED_ROW_UNAVAILABLE)
    literals: dict[str, ast.AST] = {}
    for name, value in namespace.items():
        literal = _literal_node(value)
        if literal is not None:
            literals[name] = literal
    resolved = cast(ast.AST, _NameResolver(literals).visit(copy.deepcopy(node)))
    bound = {sub.arg for sub in ast.walk(resolved) if isinstance(sub, ast.arg)} | {
        sub.id
        for sub in ast.walk(resolved)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)
    }
    free_names = [
        sub.id
        for sub in ast.walk(resolved)
        if isinstance(sub, ast.Name) and sub.id != "pl" and sub.id not in bound
    ]
    unresolved = [name for name in free_names if name not in namespace]
    if unresolved:
        return _RowValue(reason=f"unresolved_name: {unresolved[0]}")
    source = ast.unparse(resolved)

    from haute.chunking import classify_row_local_expression

    decision = classify_row_local_expression(source)
    if not decision.eligible:
        # A helper or module name blocks as a bare ``Name``/``Call`` node; name it.
        operator = decision.blocking_operator
        if operator in {"Name", "Call"} and free_names:
            operator = free_names[0]
        return _RowValue(reason=f"not_row_local: {operator or decision.reason}")

    from haute._sandbox import safe_globals, validate_user_code

    validate_user_code(source)
    globals_ns = safe_globals(
        pl=pl,
        **{k: v for k, v in namespace.items() if k != "df" and not k.startswith("__")},
    )
    built = eval(source, globals_ns)
    expression = built if isinstance(built, pl.Expr) else pl.lit(built)
    missing = [name for name in expression.meta.root_names() if name not in row.columns]
    if missing:
        return _RowValue(reason=f"column_unavailable: {missing[0]}")
    # ``to_list`` gives native values: ``item()`` would return a Series for a
    # List or Array cell.
    value = row.select(expression.alias(_TRACE_VALUE_COLUMN)).to_series().to_list()[0]
    return _RowValue(value=value)


def _branch_selection(
    node: ast.AST, row: pl.DataFrame, namespace: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Which arm of the ``pl.when`` chain at *node* the traced row takes.

    Each condition is evaluated by Polars on the row in source order; the first
    that is true is taken, and ``otherwise`` when none is. A condition that
    cannot be computed from one row leaves the branch unknown (``None``). A
    taken arm that is itself a ``when`` chain adds its own selection to
    ``nested_branches``.
    """
    clauses = _collect_when_then_chain(node)
    if not clauses:
        return None
    total = sum(1 for clause in clauses if "cond" in clause) + (
        1 if any("otherwise" in clause for clause in clauses) else 0
    )
    for index, clause in enumerate(clauses):
        if "cond" in clause:
            if clause["cond"] is None:
                continue
            outcome = _row_value(clause["cond"], row, namespace)
            if outcome.reason is not None:
                return None
            if outcome.value:
                return _taken_branch("then", index, total, clause["then"], row, namespace)
        elif "otherwise" in clause:
            return _taken_branch("otherwise", total - 1, total, clause["otherwise"], row, namespace)
    return {}


def _taken_branch(
    name: str,
    index: int,
    total: int,
    arm: ast.AST,
    row: pl.DataFrame,
    namespace: Mapping[str, Any],
) -> dict[str, Any]:
    nested: list[str] = []
    if _is_when_chain(arm):
        inner = _branch_selection(arm, row, namespace)
        if inner and inner.get("taken_branch") is not None:
            nested = [inner["taken_branch"], *inner.get("nested_branches", [])]
    return {
        "taken_branch": name,
        "taken_branch_index": index,
        "dimmed_branches": [other for other in range(total) if other != index],
        "nested_branches": nested,
    }


def _is_when_chain(node: ast.AST) -> bool:
    """Whether *node*'s method chain is, or ends in, a ``pl.when`` conditional."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr in ("then", "otherwise"):
        return True
    if node.func.attr == "when":
        return isinstance(node.func.value, ast.Name) and node.func.value.id == "pl"
    return _is_when_chain(node.func.value)


def parse_expression_chain(code: str, target_column: str) -> list[ParsedExpression] | None:
    """Walk backward through sequential with_columns calls to find dependencies.

    If *target_column* references column ``X``, and ``X`` was created in an earlier
    ``with_columns`` in the same code, include that expression in the chain.
    Returns the chain in dependency order (earliest first).

    Failures propagate rather than degrading to a laundered single-element
    chain; the enrichment caller records a visible error on the chain field.
    """
    return _parse_expression_chain_impl(code, target_column)


def _parse_expression_chain_impl(code: str, target_column: str) -> list[ParsedExpression]:
    """Implementation of parse_expression_chain."""
    code_wrapped = wrap_node_code(code)

    try:
        _cached_parse(code_wrapped)
    except SyntaxError:
        parsed = parse_expression(code_wrapped, target_column)
        return [parsed] if parsed else []

    # Parse each column definition ONCE, reusing the single tree + converter
    # output, instead of re-invoking parse_expression (a full reparse) per
    # chain element. "Last match wins" keeps the effective (outermost)
    # definition, matching _find_with_columns_calls' execution ordering. Each
    # call reads the names bound before its own statement.
    parsed_by_col: dict[str, ParsedExpression] = {}
    refs_by_col: dict[str, list[str]] = {}

    for wc_call, _lineno, symbol_table, _unprovable in _calls_with_bindings(code_wrapped):
        exprs = _extract_expressions_from_with_columns(wc_call, symbol_table)
        for expr_node, alias_name, ln, bound in exprs:
            if alias_name is None:
                alias_name = _infer_auto_name(expr_node)
            if alias_name is None:
                continue
            # A variable's binding was resolved where it was bound.
            converter = _ExprConverter({} if bound else symbol_table)
            text = converter.convert(expr_node)
            expr_type = "opaque" if converter._is_opaque else converter.expr_type
            parsed_by_col[alias_name] = ParsedExpression(
                target_column=alias_name,
                expression_text=text,
                expression_type=expr_type,
                referenced_columns=converter.columns,
                constants=converter.constants,
                sub_expressions=converter.sub_expressions,
                source_line=ln,
            )
            refs_by_col[alias_name] = converter.columns

    if target_column not in parsed_by_col:
        return []

    # Walk backward to find the dependency chain
    chain_cols: list[str] = []
    visited: set[str] = set()

    def _walk_deps(col: str) -> None:
        if col in visited:
            return
        visited.add(col)
        if col not in refs_by_col:
            return
        for ref in refs_by_col[col]:
            if ref in refs_by_col and ref != col:
                _walk_deps(ref)
        chain_cols.append(col)

    _walk_deps(target_column)

    return [parsed_by_col[col] for col in chain_cols if col in parsed_by_col]
