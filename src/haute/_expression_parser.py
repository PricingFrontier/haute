"""AST-based expression parser for extracting human-readable formulas from Polars code.

Given a code string containing Polars ``with_columns`` calls and a target column
name, the parser returns a :class:`ParsedExpression` describing the formula in
human-readable text, its type, referenced columns, constants, and more.

Optionally, :func:`evaluate_expression` substitutes concrete values and computes
the result, returning an :class:`EvaluatedExpression`.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, cast

__all__ = [
    "ParsedExpression",
    "EvaluatedExpression",
    "parse_expression",
    "evaluate_expression",
    "parse_expression_chain",
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
# Unary minus binds looser than ** (so ``-a ** b`` is ``-(a ** b)`` and
# ``(-a) ** b`` needs parens) but tighter than multiplication.
_PREC_USUB = 7


# Signed 64-bit integer range. The trace evaluator computes integer arithmetic
# in unbounded Python ints, but Polars integer columns are fixed-width and wrap
# on overflow (Int64: max * 2 -> -2). Because the evaluator is dtype-unaware it
# cannot know the real column width (Int8/Int32/Int64/UInt64…), so rather than
# display a misleading big-integer it reports an out-of-range *integer* result
# as uncomputable (None) instead of guessing a wraparound.
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

_COLUMN_PRODUCER_METHODS: frozenset[str] = frozenset({"with_columns", "select"})
_CONTROL_FLOW_STATEMENT_TYPES: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.For,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
)


# Binary / comparison operator dispatch tables for the value evaluator. Hoisted
# to module scope so they are built once rather than on every evaluated node.
_EVAL_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
}

_EVAL_CMPOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


@lru_cache(maxsize=256)
def _cached_parse(code: str) -> ast.Module:
    """Parse *code* to an AST module, memoised on the source string.

    ``parse_expression``/``_compute_result``/``_evaluate_conditional_branches``/
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

    def _operand_prec(self, node: ast.AST) -> int:
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
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return _PREC_USUB
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
                try:
                    return self.convert(resolved)
                finally:
                    self._resolving.discard(name)
        if name == "None":
            return "None"
        if name == "True":
            return "True"
        if name == "False":
            return "False"
        # Unknown name: treat as opaque reference
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
                base_text = self.convert(receiver)
                over_args = []
                for a in node.args:
                    over_args.append(self.convert(a))
                for kw in node.keywords:
                    over_args.append(self.convert(kw.value))
                return f"{base_text}.over({', '.join(over_args)})"

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

    def _pl_col(self, node: ast.Call) -> str:
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            col_name = node.args[0].value
            if col_name not in self.columns:
                self.columns.append(col_name)
            return col_name
        elif node.args:
            # Dynamic column name — check symbol table
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in self._symbol_table:
                resolved = self._symbol_table[arg.id]
                if isinstance(resolved, ast.Constant) and isinstance(resolved.value, str):
                    col_name = resolved.value
                    if col_name not in self.columns:
                        self.columns.append(col_name)
                    return col_name
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
                    args_parts.append(col_name)
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
                        args_parts.append(col_name)
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
                    sub_converter = _ExprConverter(self._symbol_table)
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


def _build_safe_symbol_table(stmts: list[ast.stmt]) -> dict[str, ast.AST]:
    """Build a symbol table only from top-level simple assignments (not inside
    control flow). This avoids resolving variables assigned conditionally."""
    table: dict[str, ast.AST] = {}
    for stmt in stmts:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                if _is_df_assignment(stmt.value):
                    continue
                table[name] = stmt.value
        elif isinstance(stmt, ast.AugAssign):
            # Not safe to resolve augmented assignments simply, but we can
            # represent expr = expr <op> rhs by reconstructing the AST
            pass
    return table


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


def _contains_with_columns_producing(node: ast.AST, target_column: str) -> bool:
    """Check if any node in subtree has with_columns that produces target_column."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in _COLUMN_PRODUCER_METHODS:
                # Check if any arg produces the target column
                for arg in child.args:
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

    def _span(item: tuple[ast.Call, int | None]) -> tuple[int, int, int, int]:
        n = item[0]
        return (
            getattr(n, "lineno", 0) or 0,
            getattr(n, "col_offset", 0) or 0,
            getattr(n, "end_lineno", 0) or 0,
            getattr(n, "end_col_offset", 0) or 0,
        )

    results.sort(key=_span)
    return results


def _extract_expressions_from_with_columns(
    call_node: ast.Call,
    symbol_table: dict[str, ast.AST],
) -> list[tuple[ast.AST, str | None, int | None]]:
    """Extract (expression_ast, alias_name, lineno) from a with_columns call."""
    results = []
    lineno = getattr(call_node, "lineno", None)

    for arg in call_node.args:
        actual_arg = arg
        # Handle Starred: *exprs
        if isinstance(arg, ast.Starred):
            actual_arg = arg.value
            # Resolve the variable
            if isinstance(actual_arg, ast.Name) and actual_arg.id in symbol_table:
                resolved = symbol_table[actual_arg.id]
                if isinstance(resolved, ast.List):
                    for elt in resolved.elts:
                        expr_node, alias_name = _strip_alias(elt, symbol_table)
                        results.append((expr_node, alias_name, lineno))
                    continue

        # If it's a variable name referencing a list
        if isinstance(actual_arg, ast.Name) and actual_arg.id in symbol_table:
            resolved = symbol_table[actual_arg.id]
            if isinstance(resolved, ast.List):
                for elt in resolved.elts:
                    expr_node, alias_name = _strip_alias(elt, symbol_table)
                    results.append((expr_node, alias_name, lineno))
                continue
            # It's a single expression variable
            expr_node, alias_name = _strip_alias(resolved, symbol_table)
            if alias_name is None:
                # Try stripping alias from the name usage context — the variable
                # itself might be used like: var.alias("x")
                # Actually the arg itself is a Name, check if it has .alias
                pass
            results.append((expr_node, alias_name, lineno))
            continue

        # Handle list comprehension — opaque
        if isinstance(actual_arg, ast.ListComp):
            # Can't resolve statically
            continue

        expr_node, alias_name = _strip_alias(actual_arg, symbol_table)
        if alias_name is None:
            auto_name = _infer_auto_name(expr_node)
            alias_name = auto_name
        results.append((expr_node, alias_name, lineno))

    for kw in call_node.keywords:
        if kw.arg is not None:
            results.append((kw.value, kw.arg, lineno))

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

    # Build symbol table (only from top-level assignments, not inside control flow)
    symbol_table = _build_safe_symbol_table(stmts)

    # Find variables assigned inside control flow (they are not statically resolvable)
    cf_assigned = _find_control_flow_assigned_vars(stmts)

    # Also handle reassignment chains: expr = pl.col("base"); expr = expr * pl.col("f")
    # by resolving them iteratively
    _resolve_reassignment_chains(stmts, symbol_table)

    # Find all with_columns calls
    wc_calls = _find_with_columns_calls(tree)

    if not wc_calls:
        # Check for pipe or other non-with_columns patterns
        return _opaque(target_column, code)

    # Search for the target column in with_columns calls (last match wins)
    best_match: tuple[ast.AST, int | None] | None = None

    for wc_call, lineno in wc_calls:
        exprs = _extract_expressions_from_with_columns(wc_call, symbol_table)
        for expr_node, alias_name, ln in exprs:
            if alias_name == target_column:
                best_match = (expr_node, ln)

    if best_match is None:
        # Try f-string alias matching or dynamic alias detection
        # Also check if any with_columns arg, when resolved through the symbol
        # table, might have the alias
        for wc_call, lineno in wc_calls:
            for arg in wc_call.args:
                if isinstance(arg, ast.Starred):
                    pass
                elif isinstance(arg, ast.Name) and arg.id in symbol_table:
                    resolved = symbol_table[arg.id]
                    if isinstance(resolved, ast.List):
                        for elt in resolved.elts:
                            inner, alias = _strip_alias(elt, symbol_table)
                            if alias == target_column:
                                best_match = (inner, lineno)
                                break
                    continue
                else:
                    pass
                if best_match:
                    break
            if best_match:
                break

    if best_match is None:
        # Target not found — check for no-alias expressions
        for wc_call, lineno in wc_calls:
            for arg in wc_call.args:
                if isinstance(arg, ast.Starred):
                    continue
                expr_node, alias_name = _strip_alias(arg, symbol_table)
                if alias_name is None:
                    auto = _infer_auto_name(expr_node)
                    if auto == target_column:
                        best_match = (expr_node, lineno)
                        break
            if best_match:
                break

    if best_match is None:
        return _opaque(target_column, "")

    expr_node, source_line = best_match

    # Check if the expression references variables assigned in control flow
    if cf_assigned and _expr_references_vars(expr_node, cf_assigned):
        return _opaque(target_column, code, source_line)

    # Convert the expression AST to text
    converter = _ExprConverter(symbol_table)
    expression_text = converter.convert(expr_node)
    referenced_columns = converter.columns
    constants = converter.constants
    expr_type = converter.expr_type
    sub_expressions = converter.sub_expressions

    if converter._is_opaque:
        expr_type = "opaque"

    return ParsedExpression(
        target_column=target_column,
        expression_text=expression_text,
        expression_type=expr_type,
        referenced_columns=referenced_columns,
        constants=constants,
        sub_expressions=sub_expressions,
        source_line=source_line,
    )


def _expr_references_vars(node: ast.AST, var_names: set[str]) -> bool:
    """Check if the expression references any control-flow-reassigned variable.

    A variable assigned inside control flow has an ambiguous value at the point
    of use, so the expression must be treated as opaque *even if* the same name
    also has a top-level binding — the top-level binding does not disambiguate
    which branch actually ran.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in var_names:
            return True
    return False


def _resolve_reassignment_chains(stmts: list[ast.stmt], table: dict[str, ast.AST]) -> None:
    """Handle patterns like:
        expr = pl.col("base")
        expr = expr * pl.col("factor_a")
        expr = expr * pl.col("factor_b")
    by processing assignments sequentially and updating the symbol table.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                if _is_df_assignment(stmt.value):
                    continue
                # Resolve any references to existing symbol table entries in the value
                resolved = _substitute_names_in_ast(stmt.value, table)
                table[name] = resolved


def _substitute_names_in_ast(node: ast.AST, table: dict[str, ast.AST]) -> ast.AST:
    """Recursively substitute Name nodes with their values from the symbol table.
    Returns a new AST (or the same node if no substitution needed)."""
    if isinstance(node, ast.Name):
        if node.id in table:
            return table[node.id]
        return node
    if isinstance(node, ast.BinOp):
        new_left = _substitute_names_in_ast(node.left, table)
        new_right = _substitute_names_in_ast(node.right, table)
        if new_left is node.left and new_right is node.right:
            return node
        new_node = ast.BinOp(
            left=cast(ast.expr, new_left),
            op=node.op,
            right=cast(ast.expr, new_right),
        )
        ast.copy_location(new_node, node)
        return new_node
    if isinstance(node, ast.UnaryOp):
        new_operand = _substitute_names_in_ast(node.operand, table)
        if new_operand is node.operand:
            return node
        new_node_u = ast.UnaryOp(op=node.op, operand=cast(ast.expr, new_operand))
        ast.copy_location(new_node_u, node)
        return new_node_u
    if isinstance(node, ast.Call):
        new_args = [_substitute_names_in_ast(a, table) for a in node.args]
        new_keywords = []
        for kw in node.keywords:
            new_val = _substitute_names_in_ast(kw.value, table)
            if new_val is kw.value:
                new_keywords.append(kw)
            else:
                new_kw = ast.keyword(arg=kw.arg, value=cast(ast.expr, new_val))
                new_keywords.append(new_kw)
        new_func = _substitute_names_in_ast(node.func, table)
        if (
            new_func is node.func
            and all(n is o for n, o in zip(new_args, node.args))
            and all(n is o for n, o in zip(new_keywords, node.keywords))
        ):
            return node
        new_node_c = ast.Call(
            func=cast(ast.expr, new_func),
            args=cast(list[ast.expr], new_args),
            keywords=new_keywords,
        )
        ast.copy_location(new_node_c, node)
        return new_node_c
    if isinstance(node, ast.Attribute):
        new_value = _substitute_names_in_ast(node.value, table)
        if new_value is node.value:
            return node
        new_node_a = ast.Attribute(value=cast(ast.expr, new_value), attr=node.attr, ctx=node.ctx)
        ast.copy_location(new_node_a, node)
        return new_node_a
    if isinstance(node, ast.Compare):
        new_left = _substitute_names_in_ast(node.left, table)
        new_comps = [_substitute_names_in_ast(c, table) for c in node.comparators]
        if new_left is node.left and all(n is o for n, o in zip(new_comps, node.comparators)):
            return node
        new_node_cmp = ast.Compare(
            left=cast(ast.expr, new_left),
            ops=node.ops,
            comparators=cast(list[ast.expr], new_comps),
        )
        ast.copy_location(new_node_cmp, node)
        return new_node_cmp
    if isinstance(node, ast.List):
        new_elts = [_substitute_names_in_ast(e, table) for e in node.elts]
        if all(n is o for n, o in zip(new_elts, node.elts)):
            return node
        new_node_l = ast.List(elts=cast(list[ast.expr], new_elts), ctx=node.ctx)
        ast.copy_location(new_node_l, node)
        return new_node_l
    if isinstance(node, ast.Starred):
        new_value = _substitute_names_in_ast(node.value, table)
        if new_value is node.value:
            return node
        new_node_s = ast.Starred(value=cast(ast.expr, new_value), ctx=node.ctx)
        ast.copy_location(new_node_s, node)
        return new_node_s
    return node


# ---------------------------------------------------------------------------
# evaluate_expression
# ---------------------------------------------------------------------------


def evaluate_expression(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    preamble_ns: dict[str, Any] | None = None,
) -> EvaluatedExpression:
    """Parse *code*, substitute *row_values* for referenced columns, and compute
    the result.  Returns an :class:`EvaluatedExpression`.

    Failures are **not** laundered into a fabricated result: previously any
    exception here fell back to ``result_value=row_values.get(target_column)``,
    i.e. it displayed the engine's *observed* output as if the trace evaluator
    had computed it, making an evaluator bug look self-consistent. Instead the
    exception propagates to the enrichment caller, which records a visible error
    marker on the step. Fail loud, never guess.
    """
    return _evaluate_expression_impl(code, target_column, row_values, preamble_ns=preamble_ns)


def _wrap_expression_code(code: str) -> str:
    """Make expression snippets parseable without corrupting assignments."""
    code_clean = code.lstrip("\ufeff")
    if code_clean.startswith("."):
        return f"df = (df\n{code_clean})"
    first_line_prefix = code_clean.split("\n", 1)[0].split("(", 1)[0]
    if code_clean and not code_clean.startswith("df") and "=" not in first_line_prefix:
        return f"df = (\n{code_clean}\n)"
    return code_clean


def _evaluate_expression_impl(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    preamble_ns: dict[str, Any] | None = None,
) -> EvaluatedExpression:
    code = _wrap_expression_code(code)

    # Merge preamble constants into row_values for evaluation.
    # Column values (row_values) take priority over preamble constants.
    effective_row = dict(preamble_ns or {})
    effective_row.update(row_values)

    # Detect window function: .over() in code
    is_window = ".over(" in code

    parsed = parse_expression(code, target_column)
    if parsed is None:
        parsed = _opaque(target_column, "")

    # Window function handling
    if is_window:
        parsed_expr_type = "window"
        # Extract partition columns from .over() calls
        _add_window_partition_cols(code, parsed)
    else:
        parsed_expr_type = parsed.expression_type

    # Build input_values: only cols that are referenced
    input_values = {k: v for k, v in effective_row.items() if k in parsed.referenced_columns}

    # Build substituted text
    if is_window:
        substituted_text = _build_window_description(code, target_column, effective_row, parsed)
    else:
        substituted_text = _substitute_values(parsed.expression_text, input_values)

    # Resolve preamble constants in substituted text
    if preamble_ns:
        for name, val in preamble_ns.items():
            if name not in row_values:
                # Replace unresolved preamble constant names with their values
                substituted_text = _replace_column_name(substituted_text, name, _format_value(val))

    # Compute result
    result_value = _compute_result(code, target_column, effective_row, parsed)

    # Conditional branch tracking
    taken_branch: str | None = None
    taken_branch_index: int | None = None
    dimmed_branches: list[int] = []
    nested_branches: list[str] = []

    if parsed.expression_type == "conditional":
        branch_info = _evaluate_conditional_branches(code, target_column, effective_row)
        taken_branch = branch_info.get("taken_branch")
        taken_branch_index = branch_info.get("taken_branch_index")
        dimmed_branches = branch_info.get("dimmed_branches", [])
        nested_branches = branch_info.get("nested_branches", [])

    return EvaluatedExpression(
        target_column=parsed.target_column,
        expression_text=parsed.expression_text,
        expression_type=parsed_expr_type,
        referenced_columns=parsed.referenced_columns,
        constants=parsed.constants,
        sub_expressions=parsed.sub_expressions,
        source_line=parsed.source_line,
        substituted_text=substituted_text,
        result_value=result_value,
        input_values=input_values,
        taken_branch=taken_branch,
        taken_branch_index=taken_branch_index,
        dimmed_branches=dimmed_branches,
        nested_branches=nested_branches,
    )


def _add_window_partition_cols(code: str, parsed: ParsedExpression) -> None:
    """Extract partition columns from .over() calls and add to referenced_columns."""
    # Match .over('col') or .over("col")
    over_pattern = re.findall(r"\.over\(\s*['\"](\w+)['\"]\s*\)", code)
    for col in over_pattern:
        if col not in parsed.referenced_columns:
            parsed.referenced_columns.append(col)


def _build_window_description(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    parsed: ParsedExpression,
) -> str:
    """Build a human-readable description for window functions."""
    # Extract the aggregation function and column
    # e.g., pl.col('premium').sum().over('region') -> "sum of premium over region"
    agg_funcs = re.findall(r"\.(\w+)\(\)\s*\.over\(", code)
    col_match = re.findall(r"pl\.col\(['\"](\w+)['\"]\)", code)
    over_match = re.findall(r"\.over\(\s*['\"](\w+)['\"]\s*\)", code)

    agg_func = agg_funcs[0] if agg_funcs else "aggregate"
    agg_col = col_match[0] if col_match else "column"
    part_col = over_match[0] if over_match else "partition"

    return f"{agg_func} of {agg_col} over {part_col}"


def _evaluate_conditional_branches(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a conditional expression and determine which branch was taken."""
    code_clean = code.lstrip("\ufeff")
    try:
        tree = _cached_parse(code_clean)
    except SyntaxError:
        return {}

    stmts = tree.body
    symbol_table = _build_safe_symbol_table(stmts)
    _resolve_reassignment_chains(stmts, symbol_table)

    wc_calls = _find_with_columns_calls(tree)
    best_match: ast.AST | None = None

    for wc_call, lineno in wc_calls:
        exprs = _extract_expressions_from_with_columns(wc_call, symbol_table)
        for expr_node, alias_name, ln in exprs:
            if alias_name == target_column:
                best_match = expr_node

    if best_match is None:
        return {}

    # Use _BranchTrackingEvaluator (defined after _ExprEvaluator below)
    evaluator = _BranchTrackingEvaluator(row_values, symbol_table)
    evaluator.evaluate(best_match)
    return {
        "taken_branch": evaluator.taken_branch,
        "taken_branch_index": evaluator.taken_branch_index,
        "dimmed_branches": evaluator.dimmed_branches,
        "nested_branches": evaluator.nested_branches,
    }


_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _substitute_values(expression_text: str, values: dict[str, Any]) -> str:
    """Replace column names in expression text with their values.

    Identifier-like names are substituted in a SINGLE left-to-right pass via one
    combined word-boundary regex, so a value inserted for one column can never be
    re-scanned and corrupted by a later (shorter) column name matching a word
    inside it. Longest names are tried first so ``ab`` cannot shadow ``abc``.
    """
    if not values:
        return expression_text

    ident = {k: v for k, v in values.items() if _IDENT_RE.match(k)}
    other = {k: v for k, v in values.items() if k not in ident}

    result = expression_text
    if ident:
        names = sorted(ident.keys(), key=len, reverse=True)
        pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
        result = re.sub(pattern, lambda m: _format_value(ident[m.group(0)]), result)

    # Names with spaces/special chars fall back to literal replacement (they are
    # not the case the single-pass guard targets).
    for col_name in sorted(other.keys(), key=len, reverse=True):
        result = result.replace(col_name, _format_value(other[col_name]))
    return result


def _replace_column_name(text: str, col_name: str, replacement: str) -> str:
    """Replace column name in expression text, being careful with word boundaries."""
    # Use regex with word boundaries for simple alphanumeric names
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", col_name):
        pattern = r"\b" + re.escape(col_name) + r"\b"
        return re.sub(pattern, replacement, text)
    else:
        # For names with spaces/special chars, do exact replacement
        return text.replace(col_name, replacement)


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


def _compute_result(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    parsed: ParsedExpression,
) -> Any:
    """Compute the result of the expression given row values.

    Uses AST-based evaluation. Evaluator failures are deliberately **not**
    caught here: previously any exception fell back to
    ``row_values.get(target_column)``, laundering the engine's observed output
    into the trace as if the evaluator had computed it and masking evaluator
    bugs as self-consistent. Failures now propagate to the enrichment caller,
    which records a visible error. (A genuine "cannot locate the defining
    expression" is still reported as the observed value below, since there is
    no computation to be wrong about.)
    """
    return _compute_result_impl(code, target_column, row_values, parsed)


def _compute_result_impl(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    parsed: ParsedExpression,
) -> Any:
    """Reparse and evaluate the AST expression with concrete values."""
    code_clean = code.lstrip("\ufeff")
    try:
        tree = _cached_parse(code_clean)
    except SyntaxError:
        return None

    stmts = tree.body
    symbol_table = _build_safe_symbol_table(stmts)
    _resolve_reassignment_chains(stmts, symbol_table)

    wc_calls = _find_with_columns_calls(tree)
    best_match: ast.AST | None = None

    for wc_call, lineno in wc_calls:
        exprs = _extract_expressions_from_with_columns(wc_call, symbol_table)
        for expr_node, alias_name, ln in exprs:
            if alias_name == target_column:
                best_match = expr_node

    if best_match is None:
        # Try no-alias match
        for wc_call, lineno in wc_calls:
            for arg in wc_call.args:
                expr_node, alias_name = _strip_alias(arg, symbol_table)
                if alias_name is None:
                    auto = _infer_auto_name(expr_node)
                    if auto == target_column:
                        best_match = expr_node
                        break
            if best_match:
                break

    if best_match is None:
        return None

    evaluator = _ExprEvaluator(row_values, symbol_table)
    return evaluator.evaluate(best_match)


class _ExprEvaluator:
    """Evaluate a Polars expression AST with concrete values."""

    def __init__(self, row_values: dict[str, Any], symbol_table: dict[str, ast.AST] | None = None):
        self.row_values = row_values
        self._symbol_table = symbol_table or {}

    def evaluate(self, node: ast.AST) -> Any:
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.UnaryOp):
            return self._unaryop(node)
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.List):
            return [self.evaluate(e) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self.evaluate(e) for e in node.elts)
        if isinstance(node, ast.Set):
            return {self.evaluate(e) for e in node.elts}
        if isinstance(node, ast.Name):
            return self._name(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Expr):
            return self.evaluate(node.value)
        return None

    @staticmethod
    def _is_bool_kleene_operand(left: Any, right: Any) -> bool:
        """Whether ``&``/``|`` should use boolean Kleene logic for these operands.

        True when at least one operand is a concrete bool, or both are null
        (``null & null`` is null under Kleene). Integer operands fall through to
        bitwise semantics.
        """
        if isinstance(left, bool) or isinstance(right, bool):
            return True
        return left is None and right is None

    @staticmethod
    def _divide_by_zero(op_type: type, left: Any) -> Any:
        """Mirror Polars' division-by-zero: ±inf/nan for floats, null for ints.

        - float ``x / 0`` and ``x // 0`` -> ``copysign(inf, x)`` (``0/0`` -> nan);
          Polars' true-division always promotes to float, so ``int / 0`` is inf too.
        - float ``x % 0`` -> nan.
        - integer ``//`` and ``%`` by zero -> null (Polars).
        """
        left_is_float = isinstance(left, float)
        if op_type is ast.Div:
            if left == 0:
                return math.nan
            return math.copysign(math.inf, left)
        if op_type is ast.FloorDiv:
            if not left_is_float:
                return None  # integer floordiv by zero -> Polars null
            if left == 0:
                return math.nan
            return math.copysign(math.inf, left)
        # Mod
        if not left_is_float:
            return None  # integer modulo by zero -> Polars null
        return math.nan

    @staticmethod
    def _pow(left: Any, right: Any) -> Any:
        """Power mirroring Polars float semantics.

        A negative base with a non-integer exponent is NaN in Polars' float
        domain, where Python would return a complex number.
        """
        if left < 0 and isinstance(right, float) and not right.is_integer():
            return math.nan
        result = operator.pow(left, right)
        if isinstance(result, complex):
            return math.nan
        return result

    def _binop(self, node: ast.BinOp) -> Any:
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)
        op_type = type(node.op)

        # Kleene three-valued boolean logic for & / | — must run BEFORE the
        # generic null short-circuit: `False & null` is False and `True | null`
        # is True in Polars, not null.
        if op_type is ast.BitAnd and self._is_bool_kleene_operand(left, right):
            if left is False or right is False:
                return False
            if left is None or right is None:
                return None
            return bool(left) and bool(right)
        if op_type is ast.BitOr and self._is_bool_kleene_operand(left, right):
            if left is True or right is True:
                return True
            if left is None or right is None:
                return None
            return bool(left) or bool(right)

        # Every other operator propagates null.
        if left is None or right is None:
            return None

        # Division/floor-division/modulo by zero: Polars yields ±inf/nan/null
        # rather than raising ZeroDivisionError.
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            return self._divide_by_zero(op_type, left)

        if op_type is ast.Pow:
            result = self._pow(left, right)
        else:
            fn = _EVAL_BINOPS.get(op_type)
            if fn is None:
                return None
            result = fn(left, right)

        # Polars integer columns are fixed-width and wrap on overflow, but the
        # evaluator is dtype-unaware and computes in unbounded Python ints. An
        # out-of-int64-range *integer* result would display a wildly wrong
        # big-integer, so report it as uncomputable (None) instead of guessing a
        # wraparound width we cannot know.
        if type(result) is int and not (_INT64_MIN <= result <= _INT64_MAX):
            return None
        return result

    def _unaryop(self, node: ast.UnaryOp) -> Any:
        val = self.evaluate(node.operand)
        if val is None:
            return None
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        if isinstance(node.op, ast.Not):
            return not val
        if isinstance(node.op, ast.Invert):
            # Polars ~ on a boolean is logical negation; Python bitwise-not would
            # render ~True as -2.
            if isinstance(val, bool):
                return not val
            return ~val
        return None

    def _compare(self, node: ast.Compare) -> Any:
        left = self.evaluate(node.left)
        for op, comp in zip(node.ops, node.comparators):
            right = self.evaluate(comp)
            if left is None or right is None:
                return None
            fn = _EVAL_CMPOPS.get(type(op))
            if fn is None:
                # Unsupported comparison operator (is / is not / in / not in):
                # report unknown rather than a spurious True.
                return None
            if not fn(left, right):
                return False
            left = right
        return True

    def _boolop(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result = True
            for val_node in node.values:
                v = self.evaluate(val_node)
                if not v:
                    return False
                result = v
            return result
        else:  # Or
            for val_node in node.values:
                v = self.evaluate(val_node)
                if v:
                    return v
            return False

    def _name(self, node: ast.Name) -> Any:
        name = node.id
        if name == "None":
            return None
        if name == "True":
            return True
        if name == "False":
            return False
        if name in self._symbol_table:
            return self.evaluate(self._symbol_table[name])
        return self.row_values.get(name)

    def _attribute(self, node: ast.Attribute) -> Any:
        if isinstance(node.value, ast.Name) and node.value.id == "pl":
            # pl.Float64, pl.Int32 etc — not a value
            return None
        return None

    def _str_contains(self, base_val: str, node: ast.Call) -> Any:
        """Mirror ``pl.Expr.str.contains(pattern, literal=False)``.

        Polars treats *pattern* as a REGEX by default (so ``"a.c"`` matches
        ``"abc"``); only ``literal=True`` falls back to a plain substring test.
        The previous implementation did a substring match unconditionally, so
        the trace disagreed with the engine for any regex pattern.
        """
        if not node.args:
            return None
        pattern = self.evaluate(node.args[0])
        if pattern is None or not isinstance(pattern, str):
            return None
        literal = False
        # Signature: str.contains(pattern, literal=False) — 2nd positional or kw.
        if len(node.args) >= 2:
            lit = self.evaluate(node.args[1])
            if lit is not None:
                literal = bool(lit)
        for kw in node.keywords:
            if kw.arg == "literal":
                lit = self.evaluate(kw.value)
                if lit is not None:
                    literal = bool(lit)
        if literal:
            return pattern in base_val
        try:
            return re.search(pattern, base_val) is not None
        except re.error:
            return None

    def _call(self, node: ast.Call) -> Any:
        # pl.col("name")
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "pl":
                attr = node.func.attr
                if attr == "col":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        col_name = node.args[0].value
                        if isinstance(col_name, str):
                            return self.row_values.get(col_name)
                        return None
                    elif node.args and isinstance(node.args[0], ast.Name):
                        # Resolve variable
                        var_name = node.args[0].id
                        if var_name in self._symbol_table:
                            resolved = self._symbol_table[var_name]
                            if isinstance(resolved, ast.Constant) and isinstance(
                                resolved.value, str
                            ):
                                return self.row_values.get(resolved.value)
                    return None
                if attr == "lit":
                    if node.args:
                        return self.evaluate(node.args[0])
                    return None
                if attr == "when":
                    return self._eval_when_chain(node)
                if attr in _HORIZONTAL_FUNCS:
                    return self._eval_horizontal(node, attr)
                if attr == "format":
                    return self._eval_format(node)

        # Method call on expression
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver = node.func.value

            if method == "alias":
                return self.evaluate(receiver)

            # .cast() — identity for evaluation
            if method == "cast":
                return self.evaluate(receiver)

            # .fill_null(value)
            if method == "fill_null":
                val = self.evaluate(receiver)
                if val is None and node.args:
                    return self.evaluate(node.args[0])
                return val

            # .fill_nan(value)
            if method == "fill_nan":
                val = self.evaluate(receiver)
                if val is not None and isinstance(val, float) and math.isnan(val):
                    if node.args:
                        return self.evaluate(node.args[0])
                return val

            # .round(n) — mirror Polars exactly.
            #
            # Polars computes ``round(v * 10**n) / 10**n`` on the f64 value with
            # half-to-EVEN tie breaking. This is NOT the same as Python's
            # decimal-accurate two-arg ``round(v, n)``: the scale-by-10**n step
            # inherits float-multiply error, so e.g. round(2.675, 2) -> 2.68
            # under Polars but 2.67 under ``round(2.675, 2)``. (The 2026-06-24
            # coverage audit's "half-away-from-zero" note does not hold for the
            # pinned Polars 1.39 — it rounds half-to-even; see the cross-checked
            # regression tests in test_expression_parser_polars_parity.py.)
            if method == "round":
                val = self.evaluate(receiver)
                if val is None:
                    return None
                n = 0
                if node.args:
                    n_arg = self.evaluate(node.args[0])
                    if n_arg is None:
                        return val
                    n = n_arg
                try:
                    factor = 10.0**n
                    return round(val * factor) / factor
                except (TypeError, ValueError, OverflowError):
                    return val

            # .abs()
            if method == "abs":
                val = self.evaluate(receiver)
                if val is not None:
                    return abs(val)
                return None

            # .clip(lower, upper)
            if method == "clip":
                val = self.evaluate(receiver)
                if val is None:
                    return None
                lower = None
                upper = None
                for a in node.args:
                    if lower is None:
                        lower = self.evaluate(a)
                    else:
                        upper = self.evaluate(a)
                for kw in node.keywords:
                    if kw.arg == "lower_bound":
                        lower = self.evaluate(kw.value)
                    elif kw.arg == "upper_bound":
                        upper = self.evaluate(kw.value)
                # Polars checks the lower bound FIRST: a value below `lower`
                # clamps up to `lower`, otherwise a value above `upper` clamps
                # down to `upper`. With contradictory bounds (lower > upper) the
                # lower check wins, which sequential min/max would get wrong.
                if lower is not None and val < lower:
                    return lower
                if upper is not None and val > upper:
                    return upper
                return val

            # .dt.year(), .dt.month(), .dt.day()
            if isinstance(receiver, ast.Attribute) and receiver.attr == "dt":
                base_val = self.evaluate(receiver.value)
                if base_val is not None:
                    if method == "year":
                        return getattr(base_val, "year", None)
                    if method == "month":
                        return getattr(base_val, "month", None)
                    if method == "day":
                        return getattr(base_val, "day", None)
                    if method == "total_days":
                        if hasattr(base_val, "days"):
                            return base_val.days
                return None

            # .str.to_lowercase(), etc.
            if isinstance(receiver, ast.Attribute) and receiver.attr == "str":
                base_val = self.evaluate(receiver.value)
                if base_val is not None and isinstance(base_val, str):
                    if method == "to_lowercase":
                        return base_val.lower()
                    if method == "to_uppercase":
                        return base_val.upper()
                    if method == "contains":
                        return self._str_contains(base_val, node)
                return None

            # .is_null()
            if method == "is_null":
                val = self.evaluate(receiver)
                return val is None

            # .is_not_null()
            if method == "is_not_null":
                val = self.evaluate(receiver)
                return val is not None

            # .is_between(lower, upper, closed="both")
            # Polars honours the ``closed`` bound: both | left | right | none.
            if method == "is_between":
                val = self.evaluate(receiver)
                if val is not None and len(node.args) >= 2:
                    lo = self.evaluate(node.args[0])
                    hi = self.evaluate(node.args[1])
                    if lo is None or hi is None:
                        return None
                    closed = "both"
                    if len(node.args) >= 3:
                        c = self.evaluate(node.args[2])
                        if isinstance(c, str):
                            closed = c
                    for kw in node.keywords:
                        if kw.arg == "closed":
                            c = self.evaluate(kw.value)
                            if isinstance(c, str):
                                closed = c
                    left_ok = lo <= val if closed in ("both", "left") else lo < val
                    right_ok = val <= hi if closed in ("both", "right") else val < hi
                    return left_ok and right_ok
                return None

            # .is_in(values) — values may be a list/tuple/set literal.
            if method == "is_in":
                val = self.evaluate(receiver)
                if node.args and val is not None:
                    values = self.evaluate(node.args[0])
                    if isinstance(values, (list, tuple, set)):
                        return val in values
                return None

            # .sum(), .mean(), .min(), .max() — aggregation, return value as-is for single row
            if method in (
                "sum",
                "mean",
                "min",
                "max",
                "count",
                "first",
                "last",
                "std",
                "var",
                "median",
                "null_count",
                "n_unique",
            ):
                return self.evaluate(receiver)

            # .over() — for evaluation, just return the base value
            if method == "over":
                return self.evaluate(receiver)

            # .shift(), .diff() — can't evaluate meaningfully for single row
            if method in ("shift", "diff"):
                return self.evaluate(receiver)

            # .log() — Polars float domain: log(0) -> -inf, log(<0) -> NaN.
            if method == "log":
                val = self.evaluate(receiver)
                if val is None:
                    return None
                if val > 0:
                    return math.log(val)
                if val == 0:
                    return -math.inf
                return math.nan

            # .sqrt() — Polars float domain: sqrt(<0) -> NaN (not null).
            if method == "sqrt":
                val = self.evaluate(receiver)
                if val is None:
                    return None
                if val >= 0:
                    return math.sqrt(val)
                return math.nan

            # .when() on expression result (chained when)
            if method == "when":
                return self._eval_chained_when(node)

            # .then() / .otherwise()
            if method in ("then", "otherwise"):
                return self._eval_when_from_then_or_otherwise(node)

            # .replace_strict() / .replace()
            if method in ("replace_strict", "replace"):
                return self._eval_replace(node, method)

            # Unsupported methods are unknown, never identity operations.
            return None

        # Bare function call
        if isinstance(node.func, ast.Name):
            # Can't evaluate user functions
            return None

        return None

    def _eval_when_chain(self, when_node: ast.Call) -> Any:
        """This should never be called directly — the when is always part of
        a then/otherwise chain. Find the full chain by searching the parent."""
        return None

    def _eval_when_from_then_or_otherwise(self, node: ast.Call) -> Any:
        """Given a .then() or .otherwise() node, walk back to find the full chain."""
        clauses = self._collect_eval_clauses(node)
        return self._eval_clauses(clauses)

    def _eval_chained_when(self, node: ast.Call) -> Any:
        """Handle .when() on a then result."""
        # This is typically part of a larger chain; walk up
        return None

    def _collect_eval_clauses(self, node: ast.AST) -> list[dict[str, Any]]:
        """Collect when/then/otherwise clauses for evaluation."""
        return _collect_when_then_chain(node)

    def _eval_clauses(self, clauses: list[dict[str, Any]]) -> Any:
        for clause in clauses:
            if "cond" in clause:
                cond_val = self.evaluate(clause["cond"]) if clause["cond"] else False
                if cond_val:
                    return self.evaluate(clause["then"])
            elif "otherwise" in clause:
                return self.evaluate(clause["otherwise"])
        return None

    @staticmethod
    def _is_nan(v: Any) -> bool:
        return isinstance(v, float) and math.isnan(v)

    def _eval_horizontal_arg(self, node: ast.AST) -> Any:
        # A bare string argument to a horizontal function is a COLUMN NAME in
        # Polars (e.g. ``pl.sum_horizontal("a", "b")``), not a literal string.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self.row_values.get(node.value)
        return self.evaluate(node)

    def _eval_horizontal(self, node: ast.Call, func_name: str) -> Any:
        values = []
        for a in node.args:
            if isinstance(a, ast.List):
                for elt in a.elts:
                    values.append(self._eval_horizontal_arg(elt))
            else:
                values.append(self._eval_horizontal_arg(a))

        if func_name == "concat_str":
            return self._eval_concat_str(node, values)

        if func_name == "coalesce":
            for v in values:
                if v is not None:
                    return v
            return None

        # Boolean horizontal reductions follow Kleene logic across the row.
        if func_name == "all_horizontal":
            if any(v is False for v in values):
                return False
            if any(v is None for v in values):
                return None
            return all(bool(v) for v in values)
        if func_name == "any_horizontal":
            if any(v is True for v in values):
                return True
            if any(v is None for v in values):
                return None
            return any(bool(v) for v in values)

        # Numeric reductions ignore nulls (Polars).
        non_none = [v for v in values if v is not None]
        if not non_none:
            return None

        if func_name == "max_horizontal":
            # Polars treats NaN as the maximum, so any NaN propagates. Bare
            # Python max() would instead be argument-order dependent.
            if any(self._is_nan(v) for v in non_none):
                return math.nan
            return max(non_none)
        if func_name == "min_horizontal":
            # Polars ignores NaN for the minimum (only NaN -> NaN).
            numbers = [v for v in non_none if not self._is_nan(v)]
            if not numbers:
                return math.nan
            return min(numbers)
        if func_name == "sum_horizontal":
            return sum(non_none)
        if func_name == "mean_horizontal":
            return sum(non_none) / len(non_none)
        return None

    def _eval_concat_str(self, node: ast.Call, values: list[Any]) -> Any:
        """Mirror ``pl.concat_str``: join stringified values with ``separator``.

        With the default ``ignore_nulls=False`` any null makes the whole result
        null; otherwise nulls are dropped before joining.
        """
        separator = ""
        ignore_nulls = False
        for kw in node.keywords:
            if kw.arg == "separator":
                sep = self.evaluate(kw.value)
                # Polars requires a str separator; a non-str here is a malformed
                # authored expression. Fail loud rather than coercing to "".
                if not isinstance(sep, str):
                    raise ValueError(f"concat_str: separator must be a str, got {sep!r}")
                separator = sep
            elif kw.arg == "ignore_nulls":
                ig = self.evaluate(kw.value)
                # ignore_nulls must be a bool; a null/non-bool must not be
                # silently truthiness-coerced (bool is a subclass of int, so
                # True/False pass this check while ints/None/strings do not).
                if not isinstance(ig, bool):
                    raise ValueError(f"concat_str: ignore_nulls must be a bool, got {ig!r}")
                ignore_nulls = ig
        if not ignore_nulls and any(v is None for v in values):
            return None
        parts = [str(v) for v in values if v is not None]
        return separator.join(parts)

    def _eval_format(self, node: ast.Call) -> Any:
        if not node.args:
            return None
        fmt = self.evaluate(node.args[0])
        if not isinstance(fmt, str):
            return None
        vals = [self.evaluate(a) for a in node.args[1:]]
        try:
            return fmt.format(*vals)
        except Exception:
            return None

    def _resolve_replace_mapping(self, mapping_node: ast.AST) -> dict[Any, Any] | None:
        """Evaluate a ``replace``/``replace_strict`` mapping (dict literal or a
        symbol-table variable bound to one), or None if it is not a dict."""
        node: ast.AST = mapping_node
        if isinstance(node, ast.Name) and node.id in self._symbol_table:
            node = self._symbol_table[node.id]
        if isinstance(node, ast.Dict):
            mapping: dict[Any, Any] = {}
            for k, v in zip(node.keys, node.values):
                if k is not None:
                    mapping[self.evaluate(k)] = self.evaluate(v)
            return mapping
        return None

    def _eval_replace(self, node: ast.Call, method: str) -> Any:
        base_val = self.evaluate(cast(ast.Attribute, node.func).value)
        if not node.args:
            return base_val
        mapping = self._resolve_replace_mapping(node.args[0])
        if mapping is None:
            return base_val
        if base_val in mapping:
            return mapping[base_val]
        # Unmapped value: an explicit default kwarg wins for both variants.
        for kw in node.keywords:
            if kw.arg == "default":
                return self.evaluate(kw.value)
        if method == "replace_strict":
            # Polars raises InvalidOperationError for an incomplete
            # replace_strict mapping with no default. Fail loud rather than
            # silently returning the original value (which would diverge from
            # the engine and mislead the trace).
            raise ValueError(
                "replace_strict: incomplete mapping — no replacement for "
                f"{base_val!r} and no default provided"
            )
        # Non-strict replace leaves unmapped values unchanged.
        return base_val


class _BranchTrackingEvaluator(_ExprEvaluator):
    """Extends _ExprEvaluator to track which conditional branch was taken."""

    def __init__(self, row_values: dict[str, Any], symbol_table: dict[str, ast.AST] | None = None):
        super().__init__(row_values, symbol_table)
        self.taken_branch: str | None = None
        self.taken_branch_index: int | None = None
        self.dimmed_branches: list[int] = []
        self.nested_branches: list[str] = []

    def _eval_clauses(self, clauses: list[dict[str, Any]]) -> Any:
        # Count branches: each "cond" clause is a branch, "otherwise" is the last.
        total_branches = sum(1 for c in clauses if "cond" in c) + (
            1 if any("otherwise" in c for c in clauses) else 0
        )
        for i, clause in enumerate(clauses):
            if "cond" in clause:
                cond_val = self.evaluate(clause["cond"]) if clause["cond"] else False
                if cond_val:
                    return self._take_branch(clause["then"], "then", i, total_branches)
            elif "otherwise" in clause:
                otherwise_idx = total_branches - 1
                return self._take_branch(
                    clause["otherwise"], "otherwise", otherwise_idx, total_branches
                )
        return None

    def _take_branch(self, node: ast.AST, branch_name: str, index: int, total: int) -> Any:
        """Record this level's branch selection and evaluate the chosen value.

        When the chosen value is itself a nested when/then chain (in *either* a
        ``then`` or an ``otherwise`` arm), it is evaluated with a fresh
        sub-tracker so the inner branch is captured in ``nested_branches``
        without corrupting this level's ``taken_branch``/index/``dimmed``.
        """
        # Only the outermost selection populates the primary metadata; this
        # method records once (taken_branch stays None until the first hit).
        if self.taken_branch is None:
            self.taken_branch = branch_name
            self.taken_branch_index = index
            self.dimmed_branches = [j for j in range(total) if j != index]

        if self._check_nested_when(node):
            sub = _BranchTrackingEvaluator(self.row_values, self._symbol_table)
            result = sub.evaluate(node)
            if sub.taken_branch is not None:
                self.nested_branches.append(sub.taken_branch)
            self.nested_branches.extend(sub.nested_branches)
            return result
        return self.evaluate(node)

    def _check_nested_when(self, node: ast.AST) -> bool:
        """Check if the node contains a nested when/then chain."""
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("then", "otherwise"):
                    return True
                if node.func.attr == "when":
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "pl":
                        return True
                return self._check_nested_when(node.func.value)
            for a in node.args:
                if self._check_nested_when(a):
                    return True
        return False


# ---------------------------------------------------------------------------
# Intra-node dependency chain
# ---------------------------------------------------------------------------


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
    code_wrapped = _wrap_expression_code(code)

    try:
        tree = _cached_parse(code_wrapped)
    except SyntaxError:
        parsed = parse_expression(code_wrapped, target_column)
        return [parsed] if parsed else []

    stmts = tree.body
    symbol_table = _build_safe_symbol_table(stmts)
    _resolve_reassignment_chains(stmts, symbol_table)

    wc_calls = _find_with_columns_calls(tree)

    # Parse each column definition ONCE, reusing the single tree + converter
    # output, instead of re-invoking parse_expression (a full reparse) per
    # chain element. "Last match wins" keeps the effective (outermost)
    # definition, matching _find_with_columns_calls' execution ordering.
    parsed_by_col: dict[str, ParsedExpression] = {}
    refs_by_col: dict[str, list[str]] = {}

    for wc_call, lineno in wc_calls:
        exprs = _extract_expressions_from_with_columns(wc_call, symbol_table)
        for expr_node, alias_name, ln in exprs:
            if alias_name is None:
                alias_name = _infer_auto_name(expr_node)
            if alias_name is None:
                continue
            converter = _ExprConverter(symbol_table)
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
