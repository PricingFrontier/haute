"""Fail-closed column lineage for linear Polars frame programs.

The execution planner needs two related facts about user-authored Polars code:

* the exact output column set, when syntax and input schemas prove it; and
* the columns each named input must retain for a requested output.

This module models those facts compositionally.  Each accepted frame operation
has a forward schema transfer and a backward demand transfer.  Syntax outside
the closed model returns an unsupported result; callers must keep their visible
full-width boundary rather than guessing.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType

from haute._edge_join import narrow_join_parent_demand


class LineageOperationKind(StrEnum):
    """Closed operation vocabulary understood by the lineage interpreter."""

    SELECT = "select"
    WITH_COLUMNS = "with_columns"
    RENAME = "rename"
    READ_COLUMNS = "read_columns"
    ROW_ONLY = "row_only"
    SORT = "sort"
    UNIQUE = "unique"
    EXPLODE = "explode"
    GROUP_BY_AGG = "group_by_agg"
    JOIN = "join"


@dataclass(frozen=True, slots=True)
class LineageOperation:
    """One normalised frame operation in execution order."""

    kind: LineageOperationKind
    method: str
    output_to_inputs: tuple[tuple[str, frozenset[str]], ...] = ()
    referenced_columns: frozenset[str] = frozenset()
    renamed_columns: tuple[tuple[str, str], ...] = ()
    right_input: str | None = None
    key_pairs: tuple[tuple[str, str], ...] = ()
    how: str | None = None
    suffix: str | None = None
    subset_columns: frozenset[str] | None = frozenset()


@dataclass(frozen=True, slots=True)
class LinearFrameProgram:
    """A single live frame rooted at one named input."""

    root_input: str
    operations: tuple[LineageOperation, ...]


@dataclass(frozen=True, slots=True)
class ColumnLineageAnalysis:
    """Exact, immutable result returned to projection planning."""

    supported: bool
    exact_output_columns: frozenset[str] | None
    demands_by_input: Mapping[str, frozenset[str]]
    reason: str
    unsupported_operation: str | None = None


@dataclass(frozen=True, slots=True)
class _EvaluatedOperation:
    operation: LineageOperation
    before_schema: frozenset[str] | None
    after_schema: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class _ParseFailure:
    reason: str
    operation: str | None = None


_ROW_ONLY_METHODS = frozenset({"head", "tail", "limit", "slice"})
_SELECT_METHODS = frozenset({"select", "select_seq"})
_SUPPORTED_JOIN_HOW = frozenset({"inner", "left", "semi", "anti"})
_SCHEMA_DEPENDENT_PL_CALLS = frozenset(
    {
        "all",
        "exclude",
        "first",
        "last",
        "nth",
        "selectors",
    }
)
_HORIZONTAL_PL_CALL_OUTPUTS = {
    "all_horizontal",
    "any_horizontal",
    "cum_sum_horizontal",
    "max_horizontal",
    "mean_horizontal",
    "min_horizontal",
    "sum_horizontal",
}
_LITERAL_STRING_ARGUMENT_METHODS = frozenset(
    {
        # String namespace parsing formats are scalar configuration, not
        # column expressions.  Keep this registry closed: methods such as
        # ``then`` and ``over`` intentionally remain unsupported because a
        # bare string names another column there.
        "to_date",
        "to_datetime",
        "strptime",
    }
)


def _unsupported(reason: str, operation: str | None = None) -> ColumnLineageAnalysis:
    return ColumnLineageAnalysis(
        supported=False,
        exact_output_columns=None,
        demands_by_input=MappingProxyType({}),
        reason=reason,
        unsupported_operation=operation,
    )


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _literal_bool(node: ast.AST) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _literal_columns(node: ast.AST) -> frozenset[str] | None:
    name = _literal_string(node)
    if name is not None:
        return frozenset({name})
    if isinstance(node, (ast.List, ast.Tuple)):
        columns: set[str] = set()
        for element in node.elts:
            nested = _literal_columns(element)
            if nested is None:
                return None
            columns.update(nested)
        return frozenset(columns)
    col_name = _pl_col_name(node)
    if col_name is not None:
        return frozenset({col_name})
    return None


def _literal_string_dict(node: ast.AST) -> dict[str, str] | None:
    if not isinstance(node, ast.Dict):
        return None
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            return None
        source = _literal_string(key)
        target = _literal_string(value)
        if source is None or target is None or source in result:
            return None
        result[source] = target
    return result


def _pl_col_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "col"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pl"
        and len(node.args) == 1
        and not node.keywords
    ):
        return None
    name = _literal_string(node.args[0])
    if name is None or name == "*" or (name.startswith("^") and name.endswith("$")):
        return None
    return name


def _polars_call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "pl":
            return func.attr
    return None


def _has_direct_string_argument(node: ast.AST) -> bool:
    """Return whether an expression argument contains bare string syntax."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_has_direct_string_argument(element) for element in node.elts)
    return False


def _referenced_columns(node: ast.AST) -> frozenset[str] | None:
    """Return literal ``pl.col`` references, rejecting schema selectors."""
    columns: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        direct = _polars_call_name(child)
        if direct == "col":
            name = _pl_col_name(child)
            if name is None:
                return None
            columns.add(name)
        elif direct in _SCHEMA_DEPENDENT_PL_CALLS:
            return None
        elif direct in _HORIZONTAL_PL_CALL_OUTPUTS:
            for argument in child.args:
                literal = _literal_columns(argument)
                if literal is not None:
                    columns.update(literal)
        elif direct is None and (
            not isinstance(child.func, ast.Attribute) or isinstance(child.func.value, ast.Name)
        ):
            # A free function or foreign namespace can return an arbitrary
            # expression regardless of the explicit arguments passed to it.
            # Only Polars constructors and methods rooted in an expression are
            # closed enough for structural dependency analysis.
            return None
        elif direct is None and isinstance(child.func, ast.Attribute):
            method = child.func.attr
            is_name_suffix = (
                method == "suffix"
                and isinstance(child.func.value, ast.Attribute)
                and child.func.value.attr == "name"
            )
            if method == "alias" or is_name_suffix or method in _LITERAL_STRING_ARGUMENT_METHODS:
                continue
            # Polars expression methods are inconsistent about whether bare
            # strings are literals or column expressions (for example,
            # ``then('backup')`` reads a column while ``str.contains('x')``
            # consumes a literal).  Until a method has an explicit transfer,
            # rejecting direct string arguments is the only sound choice.
            if any(
                _has_direct_string_argument(argument)
                for argument in [
                    *child.args,
                    *(keyword.value for keyword in child.keywords),
                ]
            ):
                return None
        elif direct not in {None, "len", "lit", "when"}:
            # Strings accepted by many top-level Polars expression helpers
            # mean column names.  Unknown helpers with string arguments are
            # therefore schema-dependent until they receive an explicit rule.
            if any(
                isinstance(descendant, ast.Constant) and isinstance(descendant.value, str)
                for argument in child.args
                for descendant in ast.walk(argument)
            ):
                return None
    return frozenset(columns)


def _alias_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "alias"
        and len(node.args) == 1
        and not node.keywords
    ):
        return None
    return _literal_string(node.args[0])


def _expression_output_name(node: ast.AST) -> str | None:
    alias = _alias_name(node)
    if alias is not None:
        return alias
    column = _pl_col_name(node)
    if column is not None:
        return column
    if isinstance(node, ast.Call):
        direct = _polars_call_name(node)
        if direct == "lit":
            return "literal"
        if direct in _HORIZONTAL_PL_CALL_OUTPUTS:
            if not node.args:
                return None
            first_literal = _literal_string(node.args[0])
            return first_literal or _expression_output_name(node.args[0])
        if direct == "len":
            return "len"
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "suffix"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "name"
            and len(node.args) == 1
            and not node.keywords
        ):
            suffix = _literal_string(node.args[0])
            base_columns = [
                name
                for child in ast.walk(func.value.value)
                if (name := _pl_col_name(child)) is not None
            ]
            if suffix is not None and len(base_columns) == 1:
                return f"{base_columns[0]}{suffix}"
        # Ordinary expression methods preserve their receiver's root name.
        # Conditional builders (``pl.when(...).then(...).otherwise(...)``)
        # choose a branch name rather than the predicate name and therefore
        # require an explicit alias in the closed model.
        if any(
            isinstance(child, ast.Call) and _polars_call_name(child) == "when"
            for child in ast.walk(node)
        ):
            return None
        # Expressions reaching naming have already passed structural
        # dependency analysis, which rejects free-call syntax.
        assert isinstance(func, ast.Attribute)
        receiver: ast.AST = func.value
        while isinstance(receiver, ast.Attribute):
            receiver = receiver.value
        return _expression_output_name(receiver)
    if isinstance(node, (ast.BinOp, ast.Compare)):
        return _expression_output_name(node.left)
    if isinstance(node, ast.UnaryOp):
        return _expression_output_name(node.operand)
    if isinstance(node, ast.BoolOp) and node.values:
        return _expression_output_name(node.values[0])
    if isinstance(node, ast.Constant):
        return "literal"
    return None


def _normalise_expression_outputs(
    call: ast.Call,
    *,
    allow_plain_strings: bool,
) -> tuple[tuple[str, frozenset[str]], ...] | None:
    outputs: list[tuple[str, frozenset[str]]] = []

    def append_expression(expression: ast.AST) -> bool:
        # Polars treats a bare string as a column expression in both
        # ``select`` and ``with_columns``.  Lists/tuples are accepted only by
        # select; with_columns requires each positional expression directly.
        name = _literal_string(expression)
        if name is not None:
            outputs.append((name, frozenset({name})))
            return True
        if allow_plain_strings:
            if isinstance(expression, (ast.List, ast.Tuple)):
                return all(append_expression(element) for element in expression.elts)
        references = _referenced_columns(expression)
        if references is None:
            return False
        output_name = _expression_output_name(expression)
        if output_name is not None:
            outputs.append((output_name, references))
            return True
        return False

    for expression in call.args:
        if not append_expression(expression):
            return None
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        bare_column = _literal_string(keyword.value)
        references = (
            frozenset({bare_column})
            if bare_column is not None
            else _referenced_columns(keyword.value)
        )
        if references is None:
            return None
        outputs.append((keyword.arg, references))
    names = [name for name, _references in outputs]
    if not outputs or len(names) != len(set(names)):
        return None
    return tuple(outputs)


def _argument_references(call: ast.Call) -> frozenset[str] | None:
    columns: set[str] = set()
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        references = _referenced_columns(argument)
        if references is None:
            return None
        columns.update(references)
    return frozenset(columns)


def _chain_root_name(expr: ast.AST) -> str | None:
    current = expr
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        current = current.func.value
    return current.id if isinstance(current, ast.Name) else None


def _frame_chain_calls(expr: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    current = expr
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        calls.append(current)
        current = current.func.value
    # _chain_root_name has already proved the terminal root for this chain.
    assert isinstance(current, ast.Name)
    calls.reverse()
    return calls


def _parse_group_by_agg(
    group_call: ast.Call,
    aggregate_call: ast.Call,
) -> LineageOperation | _ParseFailure:
    key_nodes = list(group_call.args)
    for keyword in group_call.keywords:
        if keyword.arg == "by":
            key_nodes.append(keyword.value)
        elif keyword.arg == "maintain_order" and _literal_bool(keyword.value) is not None:
            continue
        else:
            return _ParseFailure("dynamic_group_by", "group_by")
    keys: set[str] = set()
    for node in key_nodes:
        parsed = _literal_columns(node)
        if parsed is None:
            return _ParseFailure("dynamic_group_by", "group_by")
        keys.update(parsed)
    if not keys:
        return _ParseFailure("dynamic_group_by", "group_by")

    expressions: list[tuple[str, frozenset[str]]] = []
    positional: list[ast.AST] = []
    for expression in aggregate_call.args:
        if isinstance(expression, (ast.List, ast.Tuple)):
            positional.extend(expression.elts)
        else:
            positional.append(expression)
    for positional_expression in positional:
        output = _alias_name(positional_expression)
        references = _referenced_columns(positional_expression)
        if output is None or references is None:
            return _ParseFailure("dynamic_aggregate", "agg")
        expressions.append((output, references))
    for keyword in aggregate_call.keywords:
        if keyword.arg is None:
            return _ParseFailure("dynamic_aggregate", "agg")
        bare_column = _literal_string(keyword.value)
        references = (
            frozenset({bare_column})
            if bare_column is not None
            else _referenced_columns(keyword.value)
        )
        if references is None:
            return _ParseFailure("dynamic_aggregate", "agg")
        expressions.append((keyword.arg, references))
    output_names = [name for name, _references in expressions]
    if len(output_names) != len(set(output_names)) or set(output_names) & keys:
        return _ParseFailure("ambiguous_aggregate_output", "agg")
    return LineageOperation(
        kind=LineageOperationKind.GROUP_BY_AGG,
        method="group_by.agg",
        output_to_inputs=tuple([(key, frozenset({key})) for key in sorted(keys)] + expressions),
        referenced_columns=frozenset(
            keys | {column for _name, refs in expressions for column in refs}
        ),
    )


def _parse_join(call: ast.Call, input_names: frozenset[str]) -> LineageOperation | _ParseFailure:
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
        return _ParseFailure("dynamic_join_input", "join")
    right_input = call.args[0].id
    if right_input not in input_names:
        return _ParseFailure("unknown_join_input", "join")

    how = "inner"
    suffix = "_right"
    on_node: ast.AST | None = None
    left_on_node: ast.AST | None = None
    right_on_node: ast.AST | None = None
    for keyword in call.keywords:
        if keyword.arg == "how":
            how = _literal_string(keyword.value) or ""
        elif keyword.arg == "suffix":
            suffix = _literal_string(keyword.value) or ""
        elif keyword.arg == "on":
            on_node = keyword.value
        elif keyword.arg == "left_on":
            left_on_node = keyword.value
        elif keyword.arg == "right_on":
            right_on_node = keyword.value
        else:
            return _ParseFailure("unsupported_join_option", "join")
    if how not in _SUPPORTED_JOIN_HOW or not suffix:
        return _ParseFailure("unsupported_join_semantics", "join")
    if on_node is not None and (left_on_node is not None or right_on_node is not None):
        return _ParseFailure("ambiguous_join_keys", "join")
    if on_node is not None:
        left_keys = right_keys = _literal_columns(on_node)
    elif left_on_node is not None and right_on_node is not None:
        left_keys = _literal_columns(left_on_node)
        right_keys = _literal_columns(right_on_node)
    else:
        return _ParseFailure("dynamic_join_keys", "join")
    if left_keys is None or right_keys is None or len(left_keys) != len(right_keys):
        return _ParseFailure("dynamic_join_keys", "join")

    # Literal list order matters when left/right key names differ. Recover it
    # directly instead of pairing unordered sets.
    def ordered(node: ast.AST) -> list[str]:
        name = _literal_string(node)
        if name is not None:
            return [name]
        col = _pl_col_name(node)
        if col is not None:
            return [col]
        # _literal_columns above has established that every remaining node is
        # a nested literal sequence of the same closed forms.
        assert isinstance(node, (ast.List, ast.Tuple))
        return [column for element in node.elts for column in ordered(element)]

    left_order = ordered(on_node if on_node is not None else left_on_node)  # type: ignore[arg-type]
    right_order = ordered(on_node if on_node is not None else right_on_node)  # type: ignore[arg-type]
    if len(left_order) != len(right_order):
        return _ParseFailure("dynamic_join_keys", "join")
    return LineageOperation(
        kind=LineageOperationKind.JOIN,
        method="join",
        right_input=right_input,
        key_pairs=tuple(zip(left_order, right_order, strict=True)),
        how=how,
        suffix=suffix,
    )


def _parse_call_sequence(
    calls: list[ast.Call],
    input_names: frozenset[str],
) -> tuple[list[LineageOperation], _ParseFailure | None]:
    operations: list[LineageOperation] = []
    index = 0
    while index < len(calls):
        call = calls[index]
        func = call.func
        # _frame_chain_calls constructs this sequence exclusively from
        # attribute calls.
        assert isinstance(func, ast.Attribute)
        method = func.attr
        if method in {"group_by", "groupby"}:
            if index + 1 >= len(calls):
                return [], _ParseFailure("incomplete_group_by", method)
            aggregate = calls[index + 1]
            if not isinstance(aggregate.func, ast.Attribute) or aggregate.func.attr != "agg":
                return [], _ParseFailure("incomplete_group_by", method)
            parsed_group = _parse_group_by_agg(call, aggregate)
            if isinstance(parsed_group, _ParseFailure):
                return [], parsed_group
            operations.append(parsed_group)
            index += 2
            continue
        if method == "agg":
            return [], _ParseFailure("orphan_aggregate", method)
        if method in _SELECT_METHODS:
            outputs = _normalise_expression_outputs(call, allow_plain_strings=True)
            if outputs is None:
                return [], _ParseFailure("dynamic_select", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.SELECT,
                    method=method,
                    output_to_inputs=outputs,
                )
            )
        elif method == "with_columns":
            outputs = _normalise_expression_outputs(call, allow_plain_strings=False)
            if outputs is None:
                return [], _ParseFailure("dynamic_with_columns", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.WITH_COLUMNS,
                    method=method,
                    output_to_inputs=outputs,
                    referenced_columns=frozenset(
                        column for _name, refs in outputs for column in refs
                    ),
                )
            )
        elif method == "rename":
            if len(call.args) != 1:
                return [], _ParseFailure("dynamic_rename", method)
            mapping = _literal_string_dict(call.args[0])
            strict = True
            for keyword in call.keywords:
                if keyword.arg != "strict" or _literal_bool(keyword.value) is None:
                    return [], _ParseFailure("dynamic_rename", method)
                strict = bool(_literal_bool(keyword.value))
            if mapping is None or not strict:
                return [], _ParseFailure("dynamic_rename", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.RENAME,
                    method=method,
                    renamed_columns=tuple(
                        (source, target) for source, target in mapping.items() if source != target
                    ),
                )
            )
        elif method in {"filter", "fill_null"}:
            refs = _argument_references(call)
            if refs is None or any(keyword.arg is None for keyword in call.keywords):
                return [], _ParseFailure(f"dynamic_{method}", method)
            if method == "filter":
                refs = frozenset(set(refs) | {kw.arg for kw in call.keywords if kw.arg})
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.READ_COLUMNS,
                    method=method,
                    referenced_columns=refs,
                )
            )
        elif method in _ROW_ONLY_METHODS:
            operations.append(LineageOperation(kind=LineageOperationKind.ROW_ONLY, method=method))
        elif method == "sort":
            by_nodes = list(call.args)
            for keyword in call.keywords:
                if keyword.arg == "by":
                    by_nodes.append(keyword.value)
                elif keyword.arg in {
                    "descending",
                    "nulls_last",
                    "maintain_order",
                    "multithreaded",
                }:
                    continue
                else:
                    return [], _ParseFailure("dynamic_sort", method)
            sort_columns: set[str] = set()
            for by_node in by_nodes:
                parsed = _literal_columns(by_node)
                if parsed is None:
                    return [], _ParseFailure("dynamic_sort", method)
                sort_columns.update(parsed)
            if not sort_columns:
                return [], _ParseFailure("dynamic_sort", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.SORT,
                    method=method,
                    referenced_columns=frozenset(sort_columns),
                )
            )
        elif method == "unique":
            subset_node: ast.AST | None = call.args[0] if call.args else None
            if len(call.args) > 1:
                return [], _ParseFailure("dynamic_unique", method)
            for keyword in call.keywords:
                if keyword.arg == "subset":
                    if subset_node is not None:
                        return [], _ParseFailure("dynamic_unique", method)
                    subset_node = keyword.value
                elif keyword.arg in {"keep", "maintain_order"}:
                    continue
                else:
                    return [], _ParseFailure("dynamic_unique", method)
            subset = None if subset_node is None else _literal_columns(subset_node)
            if subset_node is not None and subset is None:
                return [], _ParseFailure("dynamic_unique", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.UNIQUE,
                    method=method,
                    subset_columns=subset,
                )
            )
        elif method == "explode":
            explode_columns: set[str] = set()
            for node in call.args:
                parsed = _literal_columns(node)
                if parsed is None:
                    return [], _ParseFailure("dynamic_explode", method)
                explode_columns.update(parsed)
            for keyword in call.keywords:
                if keyword.arg != "columns":
                    return [], _ParseFailure("dynamic_explode", method)
                parsed = _literal_columns(keyword.value)
                if parsed is None:
                    return [], _ParseFailure("dynamic_explode", method)
                explode_columns.update(parsed)
            if not explode_columns:
                return [], _ParseFailure("dynamic_explode", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.EXPLODE,
                    method=method,
                    referenced_columns=frozenset(explode_columns),
                )
            )
        elif method == "join":
            parsed_join = _parse_join(call, input_names)
            if isinstance(parsed_join, _ParseFailure):
                return [], parsed_join
            operations.append(parsed_join)
        else:
            return [], _ParseFailure("unsupported_operation", method)
        index += 1
    return operations, None


@lru_cache(maxsize=512)
def _parse_program(
    code: str,
    input_names: frozenset[str],
) -> LinearFrameProgram | _ParseFailure:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _ParseFailure("syntax_error")

    root_input: str | None = None
    operations: list[LineageOperation] = []
    started_operations = False
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            return _ParseFailure("non_linear_control_flow", type(statement).__name__)
        target = statement.targets[0]
        if not isinstance(target, ast.Name):
            return _ParseFailure("non_linear_assignment")
        if target.id != "df":
            # Literal/scalar helpers are harmless, but frame-dependent helpers
            # would create a second lineage the closed model cannot represent.
            if any(
                isinstance(child, ast.Name) and child.id in (set(input_names) | {"df"})
                for child in ast.walk(statement.value)
            ):
                return _ParseFailure("frame_dependent_helper")
            if any(isinstance(child, ast.Call) for child in ast.walk(statement.value)):
                return _ParseFailure("dynamic_helper")
            continue

        if isinstance(statement.value, ast.Name):
            if started_operations:
                return _ParseFailure("unknown_frame_root")
            if statement.value.id in input_names:
                root_input = statement.value.id
            elif statement.value.id == "df" and root_input is None and len(input_names) == 1:
                root_input = next(iter(input_names))
            elif statement.value.id != "df" or root_input is None:
                return _ParseFailure("unknown_frame_root")
            continue

        root = _chain_root_name(statement.value)
        if root is None:
            return _ParseFailure("unknown_frame_root")
        if started_operations:
            if root != "df":
                return _ParseFailure("frame_root_reset")
        elif root == "df":
            if root_input is None:
                if len(input_names) != 1:
                    return _ParseFailure("ambiguous_frame_root")
                # Historical code sometimes spells the sole input as ``df``.
                # Runtime validation remains authoritative; retaining this
                # conservative alias keeps projection behavior compatible.
                root_input = next(iter(input_names))
        elif root in input_names:
            root_input = root
        else:
            return _ParseFailure("unknown_frame_root")
        calls = _frame_chain_calls(statement.value)
        parsed, failure = _parse_call_sequence(calls, input_names)
        if failure is not None:
            return failure
        operations.extend(parsed)
        started_operations = True

    if root_input is None:
        return _ParseFailure("no_frame_root")
    return LinearFrameProgram(root_input=root_input, operations=tuple(operations))


def _rename_schema(
    schema: frozenset[str],
    mapping: Mapping[str, str],
) -> frozenset[str] | None:
    if not set(mapping) <= set(schema):
        return None
    output = {mapping.get(column, column) for column in schema}
    if len(output) != len(schema):
        return None
    return frozenset(output)


def _join_output_schema(
    left_schema: frozenset[str],
    right_schema: frozenset[str],
    operation: LineageOperation,
) -> frozenset[str] | None:
    left_keys = {left for left, _right in operation.key_pairs}
    right_keys = {right for _left, right in operation.key_pairs}
    if not left_keys <= set(left_schema) or not right_keys <= set(right_schema):
        return None
    if operation.how in {"semi", "anti"}:
        return left_schema
    assert operation.suffix is not None
    output = set(left_schema)
    for column in set(right_schema) - right_keys:
        emitted = f"{column}{operation.suffix}" if column in left_schema else column
        if emitted in output:
            return None
        output.add(emitted)
    return frozenset(output)


def _evaluate_program(
    program: LinearFrameProgram,
    input_schemas: Mapping[str, frozenset[str] | None],
) -> tuple[tuple[_EvaluatedOperation, ...], frozenset[str] | None] | _ParseFailure:
    schema = input_schemas[program.root_input]
    evaluated: list[_EvaluatedOperation] = []
    for operation in program.operations:
        before = schema
        if schema is not None:
            required_from_current: set[str] = set(operation.referenced_columns)
            if operation.kind in {
                LineageOperationKind.SELECT,
                LineageOperationKind.WITH_COLUMNS,
                LineageOperationKind.GROUP_BY_AGG,
            }:
                required_from_current.update(
                    column for _name, refs in operation.output_to_inputs for column in refs
                )
            if (
                operation.kind is LineageOperationKind.UNIQUE
                and operation.subset_columns is not None
            ):
                required_from_current.update(operation.subset_columns)
            if required_from_current - set(schema):
                return _ParseFailure("operation_input_missing", operation.method)
        if operation.kind is LineageOperationKind.SELECT:
            schema = frozenset(name for name, _refs in operation.output_to_inputs)
        elif operation.kind is LineageOperationKind.WITH_COLUMNS:
            if schema is not None:
                schema = frozenset(
                    set(schema) | {name for name, _refs in operation.output_to_inputs}
                )
        elif operation.kind is LineageOperationKind.RENAME:
            mapping = dict(operation.renamed_columns)
            if schema is not None:
                schema = _rename_schema(schema, mapping)
                if schema is None:
                    return _ParseFailure("invalid_rename", operation.method)
        elif operation.kind is LineageOperationKind.GROUP_BY_AGG:
            schema = frozenset(name for name, _refs in operation.output_to_inputs)
        elif operation.kind is LineageOperationKind.JOIN:
            assert operation.right_input is not None
            right_schema = input_schemas[operation.right_input]
            if schema is None or right_schema is None:
                return _ParseFailure("join_schema_unknown", operation.method)
            schema = _join_output_schema(schema, right_schema, operation)
            if schema is None:
                return _ParseFailure("join_schema_ambiguous", operation.method)
        # Read/row/sort/unique/explode preserve the schema.
        evaluated.append(
            _EvaluatedOperation(
                operation=operation,
                before_schema=before,
                after_schema=schema,
            )
        )
    return tuple(evaluated), schema


def _translate_rename_demand(
    demand: set[str],
    mapping: Mapping[str, str],
    before_schema: frozenset[str] | None,
) -> set[str] | None:
    if before_schema is not None:
        after_schema = _rename_schema(before_schema, mapping)
        assert after_schema is not None
        # The caller validates an explicit demand against its exact schema.
        assert demand <= set(after_schema)
        known_reverse = {mapping.get(column, column): column for column in before_schema}
        return {known_reverse[column] for column in demand} | set(mapping)

    unknown_reverse: dict[str, str] = {}
    for source, target in mapping.items():
        if target in unknown_reverse:
            return None
        unknown_reverse[target] = source
    # Without an exact schema, a target that is not simultaneously renamed
    # away could collide with an unknown existing input column.
    if set(unknown_reverse) - set(mapping):
        return None
    # The containment check above, combined with the one-to-one reverse map,
    # proves this mapping is a permutation of its source names.  Consequently
    # every mapped output has a unique predecessor; all other demanded names
    # pass through unchanged.
    return {unknown_reverse.get(column, column) for column in demand}


def analyze_polars_lineage(
    code: str,
    inputs: Mapping[str, frozenset[str] | None],
    demanded_output: Iterable[str] | None = None,
) -> ColumnLineageAnalysis:
    """Prove exact output schema and per-input demand for linear Polars code.

    ``None`` input schemas are allowed for operations whose ownership does not
    depend on the complete schema.  A join requires exact schemas on both sides
    so collision/suffix ownership can be attributed mechanically.
    """
    if not isinstance(code, str) or not code.strip():
        return _unsupported("empty_code")
    if not inputs or any(not isinstance(name, str) or not name for name in inputs):
        return _unsupported("invalid_inputs")
    normalised_inputs = {
        name: None if columns is None else frozenset(columns) for name, columns in inputs.items()
    }
    program = _parse_program(code, frozenset(normalised_inputs))
    if isinstance(program, _ParseFailure):
        return _unsupported(program.reason, program.operation)
    evaluated_result = _evaluate_program(program, normalised_inputs)
    if isinstance(evaluated_result, _ParseFailure):
        return _unsupported(evaluated_result.reason, evaluated_result.operation)
    evaluated, exact_output = evaluated_result

    if demanded_output is None:
        if exact_output is None:
            return _unsupported("output_schema_unknown")
        demand = set(exact_output)
    else:
        demand = set(demanded_output)
        if any(not isinstance(column, str) or not column for column in demand):
            return _unsupported("invalid_output_demand")
        if exact_output is not None and not demand <= set(exact_output):
            return _unsupported("demand_outside_output_schema")

    demands_by_input: dict[str, set[str]] = {name: set() for name in normalised_inputs}
    for item in reversed(evaluated):
        operation = item.operation
        if operation.kind is LineageOperationKind.SELECT:
            mapping = dict(operation.output_to_inputs)
            # Exact-output validation (or the forward SELECT transfer) proves
            # every backward demand is a selected output.
            assert demand <= set(mapping)
            # Polars evaluates every selected expression, even if a later
            # operation no longer needs that output.
            demand = {column for refs in mapping.values() for column in refs}
        elif operation.kind is LineageOperationKind.WITH_COLUMNS:
            mapping = dict(operation.output_to_inputs)
            demand = (demand - set(mapping)) | {
                column for refs in mapping.values() for column in refs
            }
        elif operation.kind is LineageOperationKind.RENAME:
            translated = _translate_rename_demand(
                demand,
                dict(operation.renamed_columns),
                item.before_schema,
            )
            if translated is None:
                return _unsupported("rename_schema_ambiguous", operation.method)
            demand = translated
        elif operation.kind in {
            LineageOperationKind.READ_COLUMNS,
            LineageOperationKind.SORT,
            LineageOperationKind.EXPLODE,
        }:
            demand |= set(operation.referenced_columns)
        elif operation.kind is LineageOperationKind.UNIQUE:
            if operation.subset_columns is None:
                if item.before_schema is None:
                    return _unsupported("unique_schema_unknown", operation.method)
                demand |= set(item.before_schema)
            else:
                demand |= set(operation.subset_columns)
        elif operation.kind is LineageOperationKind.GROUP_BY_AGG:
            outputs = {name for name, _refs in operation.output_to_inputs}
            # As above, the group-by transfer is exact and validates demand.
            assert demand <= outputs
            demand = set(operation.referenced_columns)
        elif operation.kind is LineageOperationKind.JOIN:
            assert item.before_schema is not None
            assert operation.right_input is not None
            right_schema = normalised_inputs[operation.right_input]
            assert right_schema is not None
            routed = narrow_join_parent_demand(
                demand,
                left_keys={left for left, _right in operation.key_pairs},
                right_keys={right for _left, right in operation.key_pairs},
                left_schema=set(item.before_schema),
                right_schema=set(right_schema),
                how=operation.how or "",
                suffix=operation.suffix or "",
            )
            # Exact schemas and exact output validation make routing total.
            assert routed is not None
            left_demand, right_demand = routed
            demand = left_demand
            demands_by_input[operation.right_input].update(right_demand)
        elif operation.kind is LineageOperationKind.ROW_ONLY:
            continue
        else:  # pragma: no cover - enum exhaustiveness guard
            return _unsupported("unsupported_operation", operation.method)

    demands_by_input[program.root_input].update(demand)
    return ColumnLineageAnalysis(
        supported=True,
        exact_output_columns=exact_output,
        demands_by_input=MappingProxyType(
            {name: frozenset(columns) for name, columns in demands_by_input.items()}
        ),
        reason="lineage_proven",
        unsupported_operation=None,
    )


__all__ = [
    "ColumnLineageAnalysis",
    "LinearFrameProgram",
    "LineageOperation",
    "LineageOperationKind",
    "analyze_polars_lineage",
]
