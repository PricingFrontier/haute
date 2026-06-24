"""AST-based expression parser for extracting human-readable formulas from Polars code.

Given a code string containing Polars ``with_columns`` calls and a target column
name, the parser returns a :class:`ParsedExpression` describing the formula in
human-readable text, its type, referenced columns, constants, and more.

Optionally, :func:`evaluate_expression` substitutes concrete values and computes
the result, returning an :class:`EvaluatedExpression`.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass, field
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
    expression_type: str  # "arithmetic"|"conditional"|"horizontal_func"|"function_call"|"opaque"
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

    def _binop(self, node: ast.BinOp, parent_op: ast.operator | None = None) -> str:
        sym = _OP_SYMBOLS.get(type(node.op), "?")
        left_text = self.convert(node.left, node.op)
        right_text = self.convert(node.right, node.op)

        # Non-commutative operators need parens on the right when precedence is equal
        non_commutative = (ast.Sub, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)

        # Add parens if child has lower precedence than this op
        if isinstance(node.left, ast.BinOp) and _op_prec(node.left.op) < _op_prec(node.op):
            left_text = f"({left_text})"
        if isinstance(node.right, ast.BinOp):
            right_prec = _op_prec(node.right.op)
            parent_prec = _op_prec(node.op)
            if right_prec < parent_prec or (
                right_prec == parent_prec and isinstance(node.op, non_commutative)
            ):
                right_text = f"({right_text})"

        result = f"{left_text} {sym} {right_text}"
        return result

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
        sym = "and" if isinstance(node.op, ast.And) else "or"
        parts = [self.convert(v) for v in node.values]
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
            return f'"{val}"'
        if isinstance(val, (int, float)):
            self.constants.append(val)
            return repr(val)
        return repr(val)

    def _name(self, node: ast.Name) -> str:
        name = node.id
        # Check symbol table for variable resolution
        if name in self._symbol_table:
            resolved = self._symbol_table[name]
            if isinstance(resolved, ast.AST):
                return self.convert(resolved)
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
                parts.append(f"{{{self.convert(v.value)}}}")
            else:
                parts.append(self.convert(v))
        return 'f"' + "".join(parts) + '"'

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
            return self._pl_when_chain(node)

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
                return self._chained_when(node)

            # .then() / .otherwise()
            if method_name in ("then", "otherwise"):
                return self._when_continuation(node, method_name)

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
                    return f'"{val}"'
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

    def _pl_when_chain(self, node: ast.Call) -> str:
        """Parse pl.when(cond).then(val).otherwise(val) and chained variants."""
        self.expr_type = "conditional"
        # Collect all when/then/otherwise clauses by walking the chain
        clauses = self._collect_when_clauses(node)
        return self._format_when_clauses(clauses)

    def _chained_when(self, node: ast.Call) -> str:
        """Parse <expr>.when(cond) — chained when after a .then()."""
        self.expr_type = "conditional"
        clauses = self._collect_when_clauses(node)
        return self._format_when_clauses(clauses)

    def _when_continuation(self, node: ast.Call, method: str) -> str:
        """Handle .then() or .otherwise() at top level."""
        self.expr_type = "conditional"
        clauses = self._collect_when_clauses(node)
        return self._format_when_clauses(clauses)

    def _collect_when_clauses(self, node: ast.AST) -> list[dict[str, Any]]:
        """Walk backwards through a when/then/otherwise chain, collecting clauses.
        Returns list of dicts: [{"cond": ..., "then": ...}, ...]
        plus optional {"otherwise": ...}."""
        clauses: list[dict[str, Any]] = []
        otherwise_node = None
        current = node

        while True:
            if not isinstance(current, ast.Call) or not isinstance(current.func, ast.Attribute):
                break

            method = current.func.attr

            func_attr = current.func
            assert isinstance(func_attr, ast.Attribute)

            if method == "otherwise":
                otherwise_node = current.args[0] if current.args else (ast.Constant(value=None))
                current = func_attr.value
                continue

            if method == "then":
                then_node = current.args[0] if current.args else ast.Constant(value=None)
                # The receiver should be a when() call
                when_call = func_attr.value
                if (
                    isinstance(when_call, ast.Call)
                    and isinstance(when_call.func, ast.Attribute)
                    and when_call.func.attr == "when"
                ):
                    cond_node = when_call.args[0] if when_call.args else None
                    clauses.append({"cond": cond_node, "then": then_node})
                    # Continue up the chain (receiver of .when() could be another .then())
                    current = when_call.func.value
                    # If receiver is pl, we're done
                    if isinstance(current, ast.Name) and current.id == "pl":
                        break
                    continue
                else:
                    # .then() without preceding .when() — unusual
                    break

            if method == "when":
                # This is pl.when() at the top level (no .then() above it yet)
                # Shouldn't normally happen in a complete chain; but handle it
                break

            if method == "alias":
                current = func_attr.value
                continue

            break

        clauses.reverse()

        # Build text
        result_clauses = []
        for clause in clauses:
            result_clauses.append(clause)
        if otherwise_node is not None:
            result_clauses.append({"otherwise": otherwise_node})

        return result_clauses

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
                    parts.append(str(resolved.value))
                else:
                    return None
            elif isinstance(val_node, ast.Constant):
                parts.append(str(val_node.value))
            else:
                return None
        else:
            return None
    return "".join(parts)


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


def _build_symbol_table(tree: ast.Module) -> dict[str, ast.AST]:
    """Scan top-level assignments and build a mapping from variable names to AST nodes.
    Only resolves simple assignments (not inside if/for/try/etc.)."""
    table: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                # Skip dataframe assignments (df = df.with_columns(...))
                if _is_df_assignment(node.value):
                    continue
                table[name] = node.value
    return table


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


def _has_control_flow(stmts: list[ast.stmt]) -> bool:
    """Check if statements contain if/for/try/match/while/with at top level."""
    for stmt in stmts:
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
            return True
        if hasattr(ast, "Match") and isinstance(stmt, ast.Match):
            return True
        if hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar):
            return True
    return False


def _find_control_flow_assigned_vars(stmts: list[ast.stmt]) -> set[str]:
    """Find variable names assigned inside control flow blocks."""
    result: set[str] = set()
    for stmt in stmts:
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
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
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
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
            if child.func.attr == "with_columns":
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
# Variable-based expression list resolution
# ---------------------------------------------------------------------------


def _resolve_list_variable(name: str, table: dict[str, ast.AST]) -> list[ast.AST] | None:
    """If name is a variable assigned to a list of expressions, return them."""
    if name not in table:
        return None
    val = table[name]
    if isinstance(val, ast.List):
        return cast(list[ast.AST], val.elts)
    return None


# ---------------------------------------------------------------------------
# with_columns extraction
# ---------------------------------------------------------------------------


def _find_with_columns_calls(tree: ast.Module) -> list[tuple[ast.Call, int | None]]:
    """Find all with_columns() and select() calls in the AST, returning (call_node, line_number)."""
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("with_columns", "select"):
                lineno = getattr(node, "lineno", None)
                results.append((node, lineno))
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

    Returns an opaque expression (never raises) if parsing fails.
    """
    try:
        return _parse_expression_impl(code, target_column)
    except Exception:
        return ParsedExpression(
            target_column=target_column,
            expression_text=code if code else "",
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )


def _parse_expression_impl(code: str, target_column: str) -> ParsedExpression | None:
    if not code or not code.strip():
        return ParsedExpression(
            target_column=target_column,
            expression_text="",
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )

    # Strip BOM
    code = code.lstrip("\ufeff")

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ParsedExpression(
            target_column=target_column,
            expression_text=code,
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )

    stmts = tree.body
    if not stmts:
        return ParsedExpression(
            target_column=target_column,
            expression_text="",
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )

    # Check if the with_columns producing target is inside control flow
    if _has_control_flow_wrapping_target(stmts, target_column):
        return ParsedExpression(
            target_column=target_column,
            expression_text=code,
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )

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
        return ParsedExpression(
            target_column=target_column,
            expression_text=code,
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )

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
        return ParsedExpression(
            target_column=target_column,
            expression_text="",
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=None,
        )

    expr_node, source_line = best_match

    # Check if the expression references variables assigned in control flow
    if cf_assigned and _expr_references_vars(expr_node, cf_assigned, symbol_table):
        return ParsedExpression(
            target_column=target_column,
            expression_text=code,
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
            source_line=source_line,
        )

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


def _expr_references_vars(
    node: ast.AST,
    var_names: set[str],
    symbol_table: dict[str, ast.AST],
) -> bool:
    """Check if the expression AST node references any variable from var_names
    that is NOT in the symbol table (i.e., couldn't be resolved)."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in var_names and child.id not in symbol_table:
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
        ast.copy_location(node, new_node)
        return new_node
    if isinstance(node, ast.UnaryOp):
        new_operand = _substitute_names_in_ast(node.operand, table)
        if new_operand is node.operand:
            return node
        new_node_u = ast.UnaryOp(op=node.op, operand=cast(ast.expr, new_operand))
        ast.copy_location(node, new_node_u)
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
        ast.copy_location(node, new_node_c)
        return new_node_c
    if isinstance(node, ast.Attribute):
        new_value = _substitute_names_in_ast(node.value, table)
        if new_value is node.value:
            return node
        new_node_a = ast.Attribute(value=cast(ast.expr, new_value), attr=node.attr, ctx=node.ctx)
        ast.copy_location(node, new_node_a)
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
        ast.copy_location(node, new_node_cmp)
        return new_node_cmp
    if isinstance(node, ast.List):
        new_elts = [_substitute_names_in_ast(e, table) for e in node.elts]
        if all(n is o for n, o in zip(new_elts, node.elts)):
            return node
        new_node_l = ast.List(elts=cast(list[ast.expr], new_elts), ctx=node.ctx)
        ast.copy_location(node, new_node_l)
        return new_node_l
    if isinstance(node, ast.Starred):
        new_value = _substitute_names_in_ast(node.value, table)
        if new_value is node.value:
            return node
        new_node_s = ast.Starred(value=cast(ast.expr, new_value), ctx=node.ctx)
        ast.copy_location(node, new_node_s)
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
    the result.  Returns an :class:`EvaluatedExpression`."""
    try:
        return _evaluate_expression_impl(code, target_column, row_values, preamble_ns=preamble_ns)
    except Exception:
        parsed = parse_expression(code, target_column)
        if parsed is None:
            parsed = ParsedExpression(
                target_column=target_column,
                expression_text="",
                expression_type="opaque",
                referenced_columns=[],
                constants=[],
            )
        return EvaluatedExpression(
            target_column=parsed.target_column,
            expression_text=parsed.expression_text,
            expression_type=parsed.expression_type,
            referenced_columns=parsed.referenced_columns,
            constants=parsed.constants,
            sub_expressions=parsed.sub_expressions,
            source_line=parsed.source_line,
            substituted_text=parsed.expression_text,
            result_value=row_values.get(target_column),
            input_values={k: v for k, v in row_values.items() if k in parsed.referenced_columns},
        )


def _evaluate_expression_impl(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    preamble_ns: dict[str, Any] | None = None,
) -> EvaluatedExpression:
    # Wrap dot-chain syntax so the parser sees valid Python
    if code.lstrip().startswith("."):
        code = f"df = (df\n{code})"
    elif (
        code and not code.lstrip().startswith("df") and "=" not in code.split("\n")[0].split("(")[0]
    ):
        code = f"df = (\n{code}\n)"

    # Merge preamble constants into row_values for evaluation.
    # Column values (row_values) take priority over preamble constants.
    effective_row = dict(preamble_ns or {})
    effective_row.update(row_values)

    # Detect window function: .over() in code
    is_window = ".over(" in code

    parsed = parse_expression(code, target_column)
    if parsed is None:
        parsed = ParsedExpression(
            target_column=target_column,
            expression_text="",
            expression_type="opaque",
            referenced_columns=[],
            constants=[],
        )

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
        tree = ast.parse(code_clean)
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


def _substitute_values(expression_text: str, values: dict[str, Any]) -> str:
    """Replace column names in expression text with their values."""
    result = expression_text
    # Sort by length (longest first) to avoid partial replacements
    for col_name in sorted(values.keys(), key=len, reverse=True):
        val = values[col_name]
        val_str = _format_value(val)
        # Replace whole-word occurrences of col_name
        # But column names can contain spaces and special chars, so use simple replacement
        # We need to be careful not to replace inside quoted strings
        result = _replace_column_name(result, col_name, val_str)
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
        return f'"{val}"'
    return str(val)


def _compute_result(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    parsed: ParsedExpression,
) -> Any:
    """Compute the result of the expression given row values.
    Uses AST-based evaluation for safety."""
    try:
        return _compute_result_impl(code, target_column, row_values, parsed)
    except Exception:
        return row_values.get(target_column)


def _compute_result_impl(
    code: str,
    target_column: str,
    row_values: dict[str, Any],
    parsed: ParsedExpression,
) -> Any:
    """Reparse and evaluate the AST expression with concrete values."""
    code_clean = code.lstrip("\ufeff")
    try:
        tree = ast.parse(code_clean)
    except SyntaxError:
        return row_values.get(target_column)

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
        return row_values.get(target_column)

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
        if isinstance(node, ast.Name):
            return self._name(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Expr):
            return self.evaluate(node.value)
        return None

    def _binop(self, node: ast.BinOp) -> Any:
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)
        if left is None or right is None:
            return None
        op_map = {
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
        fn = op_map.get(type(node.op))
        if fn:
            return fn(left, right)
        return None

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
            return ~val
        return None

    def _compare(self, node: ast.Compare) -> Any:
        left = self.evaluate(node.left)
        for op, comp in zip(node.ops, node.comparators):
            right = self.evaluate(comp)
            if left is None or right is None:
                return None
            cmp_map = {
                ast.Eq: operator.eq,
                ast.NotEq: operator.ne,
                ast.Lt: operator.lt,
                ast.LtE: operator.le,
                ast.Gt: operator.gt,
                ast.GtE: operator.ge,
            }
            fn = cmp_map.get(type(op))
            if fn:
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

            # .round(n)
            if method == "round":
                val = self.evaluate(receiver)
                if val is not None and node.args:
                    n = self.evaluate(node.args[0])
                    if n is not None:
                        return round(val, n)
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
                if lower is not None:
                    val = max(val, lower)
                if upper is not None:
                    val = min(val, upper)
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
                        if node.args:
                            pattern = self.evaluate(node.args[0])
                            return pattern in base_val if pattern else False
                return None

            # .is_null()
            if method == "is_null":
                val = self.evaluate(receiver)
                return val is None

            # .is_not_null()
            if method == "is_not_null":
                val = self.evaluate(receiver)
                return val is not None

            # .is_between(lower, upper)
            if method == "is_between":
                val = self.evaluate(receiver)
                if val is not None and len(node.args) >= 2:
                    lo = self.evaluate(node.args[0])
                    hi = self.evaluate(node.args[1])
                    if lo is not None and hi is not None:
                        return lo <= val <= hi
                return None

            # .is_in(values)
            if method == "is_in":
                val = self.evaluate(receiver)
                if node.args:
                    values = self.evaluate(node.args[0])
                    if isinstance(values, list) and val is not None:
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

            # .log()
            if method == "log":
                val = self.evaluate(receiver)
                if val is not None and val > 0:
                    return math.log(val)
                return None

            # .sqrt()
            if method == "sqrt":
                val = self.evaluate(receiver)
                if val is not None and val >= 0:
                    return math.sqrt(val)
                return None

            # .when() on expression result (chained when)
            if method == "when":
                return self._eval_chained_when(node)

            # .then() / .otherwise()
            if method in ("then", "otherwise"):
                return self._eval_when_from_then_or_otherwise(node)

            # .replace_strict() / .replace()
            if method in ("replace_strict", "replace"):
                return self._eval_replace(node, method)

            # Default: try to evaluate receiver
            return self.evaluate(receiver)

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
        clauses: list[dict[str, Any]] = []
        otherwise_val = None
        current = node

        while True:
            if not isinstance(current, ast.Call) or not isinstance(current.func, ast.Attribute):
                break
            func_attr = current.func
            method = func_attr.attr

            if method == "otherwise":
                otherwise_val = current.args[0] if current.args else ast.Constant(value=None)
                current = func_attr.value
                continue

            if method == "then":
                then_node = current.args[0] if current.args else ast.Constant(value=None)
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
        return clauses + ([{"otherwise": otherwise_val}] if otherwise_val is not None else [])

    def _eval_clauses(self, clauses: list[dict[str, Any]]) -> Any:
        for clause in clauses:
            if "cond" in clause:
                cond_val = self.evaluate(clause["cond"]) if clause["cond"] else False
                if cond_val:
                    return self.evaluate(clause["then"])
            elif "otherwise" in clause:
                return self.evaluate(clause["otherwise"])
        return None

    def _eval_horizontal(self, node: ast.Call, func_name: str) -> Any:
        values = []
        for a in node.args:
            if isinstance(a, ast.List):
                for elt in a.elts:
                    values.append(self.evaluate(elt))
            else:
                values.append(self.evaluate(a))

        # Filter None values for min/max/sum
        non_none = [v for v in values if v is not None]
        if not non_none:
            return None

        if func_name == "max_horizontal":
            return max(non_none)
        if func_name == "min_horizontal":
            return min(non_none)
        if func_name == "sum_horizontal":
            return sum(non_none)
        if func_name == "mean_horizontal":
            return sum(non_none) / len(non_none)
        if func_name == "coalesce":
            for v in values:
                if v is not None:
                    return v
            return None
        return None

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

    def _eval_replace(self, node: ast.Call, method: str) -> Any:
        base_val = self.evaluate(cast(ast.Attribute, node.func).value)
        if not node.args:
            return base_val
        mapping_node = node.args[0]
        # Try to evaluate mapping as a dict
        if isinstance(mapping_node, ast.Dict):
            mapping = {}
            for k, v in zip(mapping_node.keys, mapping_node.values):
                if k is not None:
                    mapping[self.evaluate(k)] = self.evaluate(v)
            if base_val in mapping:
                return mapping[base_val]
            # Check for default kwarg
            for kw in node.keywords:
                if kw.arg == "default":
                    return self.evaluate(kw.value)
            return base_val
        elif isinstance(mapping_node, ast.Name) and mapping_node.id in self._symbol_table:
            resolved = self._symbol_table[mapping_node.id]
            if isinstance(resolved, ast.Dict):
                mapping = {}
                for k, v in zip(resolved.keys, resolved.values):
                    if k is not None:
                        mapping[self.evaluate(k)] = self.evaluate(v)
                if base_val in mapping:
                    return mapping[base_val]
                for kw in node.keywords:
                    if kw.arg == "default":
                        return self.evaluate(kw.value)
        return base_val


class _BranchTrackingEvaluator(_ExprEvaluator):
    """Extends _ExprEvaluator to track which conditional branch was taken."""

    def __init__(self, row_values: dict[str, Any], symbol_table: dict[str, ast.AST] | None = None):
        super().__init__(row_values, symbol_table)
        self.taken_branch: str | None = None
        self.taken_branch_index: int | None = None
        self.dimmed_branches: list[int] = []
        self.nested_branches: list[str] = []
        self._is_outer = True  # Track if this is the outermost conditional

    def _eval_clauses(self, clauses: list[dict[str, Any]]) -> Any:
        # Count branches: each "cond" clause is a branch, "otherwise" is the last
        total_branches = sum(1 for c in clauses if "cond" in c) + (
            1 if any("otherwise" in c for c in clauses) else 0
        )
        is_outer = self._is_outer

        for i, clause in enumerate(clauses):
            if "cond" in clause:
                cond_val = self.evaluate(clause["cond"]) if clause["cond"] else False
                if cond_val:
                    # Check if then value contains a nested conditional
                    then_node = clause["then"]
                    has_nested = self._check_nested_when(then_node)

                    if is_outer:
                        self.taken_branch = "then"
                        self.taken_branch_index = i
                        self.dimmed_branches = [j for j in range(total_branches) if j != i]
                        self._is_outer = False

                    result = self.evaluate(then_node)

                    if has_nested and is_outer:
                        # The nested evaluator's branch info becomes nested_branches
                        self.nested_branches.append(self.taken_branch or "then")
                        # Reset outer taken_branch to "then" for the outer level
                        self.taken_branch = "then"
                        self.taken_branch_index = i
                        self.dimmed_branches = [j for j in range(total_branches) if j != i]

                    return result
            elif "otherwise" in clause:
                otherwise_idx = total_branches - 1
                if is_outer:
                    self.taken_branch = "otherwise"
                    self.taken_branch_index = otherwise_idx
                    self.dimmed_branches = [j for j in range(total_branches) if j != otherwise_idx]
                return self.evaluate(clause["otherwise"])
        return None

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

    def _eval_when_from_then_or_otherwise(self, node: ast.Call) -> Any:
        """Override to track nested branches."""
        clauses = self._collect_eval_clauses(node)
        return self._eval_clauses(clauses)


# ---------------------------------------------------------------------------
# Intra-node dependency chain
# ---------------------------------------------------------------------------


def parse_expression_chain(code: str, target_column: str) -> list[ParsedExpression] | None:
    """Walk backward through sequential with_columns calls to find dependencies.

    If *target_column* references column ``X``, and ``X`` was created in an earlier
    ``with_columns`` in the same code, include that expression in the chain.
    Returns the chain in dependency order (earliest first).
    """
    try:
        return _parse_expression_chain_impl(code, target_column)
    except Exception:
        # Graceful fallback
        parsed = parse_expression(code, target_column)
        if parsed is not None:
            return [parsed]
        return []


def _parse_expression_chain_impl(code: str, target_column: str) -> list[ParsedExpression]:
    """Implementation of parse_expression_chain."""
    code_clean = code.lstrip("\ufeff")

    # Wrap dot-chain code
    if code_clean.startswith("."):
        code_wrapped = f"df = (df\n{code_clean})"
    elif code_clean and "df" not in code_clean.split("=")[0]:
        code_wrapped = f"df = (\n{code_clean}\n)"
    else:
        code_wrapped = code_clean

    try:
        tree = ast.parse(code_wrapped)
    except SyntaxError:
        parsed = parse_expression(code_wrapped, target_column)
        return [parsed] if parsed else []

    stmts = tree.body
    symbol_table = _build_safe_symbol_table(stmts)
    _resolve_reassignment_chains(stmts, symbol_table)

    wc_calls = _find_with_columns_calls(tree)

    # Collect all column definitions across with_columns calls
    all_defs: list[tuple[str, ast.AST, list[str], int]] = []

    for wc_idx, (wc_call, lineno) in enumerate(wc_calls):
        exprs = _extract_expressions_from_with_columns(wc_call, symbol_table)
        for expr_node, alias_name, ln in exprs:
            if alias_name is None:
                alias_name = _infer_auto_name(expr_node)
            if alias_name is None:
                continue
            converter = _ExprConverter(symbol_table)
            converter.convert(expr_node)
            refs = converter.columns
            all_defs.append((alias_name, expr_node, refs, wc_idx))

    # Build a mapping from column name to its definition
    col_defs: dict[str, tuple[ast.AST, list[str], int]] = {}
    for col_name, expr_node, refs, wc_idx in all_defs:
        col_defs[col_name] = (expr_node, refs, wc_idx)

    if target_column not in col_defs:
        return []

    # Walk backward to find the dependency chain
    chain_cols: list[str] = []
    visited: set[str] = set()

    def _walk_deps(col: str) -> None:
        if col in visited:
            return
        visited.add(col)
        if col not in col_defs:
            return
        _, refs, _ = col_defs[col]
        for ref in refs:
            if ref in col_defs and ref != col:
                _walk_deps(ref)
        chain_cols.append(col)

    _walk_deps(target_column)

    # Build ParsedExpression for each in order
    chain: list[ParsedExpression] = []
    for col in chain_cols:
        parsed = parse_expression(code_wrapped, col)
        if parsed is not None:
            chain.append(parsed)

    return chain if chain else []
