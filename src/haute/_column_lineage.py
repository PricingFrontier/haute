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
import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType

import polars as pl

from haute._cardinality import join_cardinality_upper_bound, normalise_join_validation
from haute._edge_join import narrow_join_parent_demand
from haute._polars_operations import unbounded_expansion_expression_methods
from haute._polars_selectors import (
    LiteralSelector,
    expand_literal_selector,
    literal_selector,
    selector_root,
)


class LineageOperationKind(StrEnum):
    """Closed operation vocabulary understood by the lineage interpreter."""

    SELECT = "select"
    WITH_COLUMNS = "with_columns"
    RENAME = "rename"
    READ_COLUMNS = "read_columns"
    ROW_ONLY = "row_only"
    DROP = "drop"
    DROP_NULLS = "drop_nulls"
    WITH_ROW_INDEX = "with_row_index"
    UNPIVOT = "unpivot"
    SORT = "sort"
    UNIQUE = "unique"
    EXPLODE = "explode"
    GROUP_BY_AGG = "group_by_agg"
    JOIN = "join"


class SelectorItemRole(StrEnum):
    """How an expanded selector contributes to its operation."""

    OUTPUT = "output"
    """One output per expanded column, or one named output for a single column."""

    HELPER_OUTPUT = "helper_output"
    """One horizontal-helper output that reads every expanded column."""

    REFERENCES = "references"
    """Columns a predicate reads."""

    COLUMNS = "columns"
    """Columns a frame method names (``drop``, ``unique`` subset, ``sort`` keys)."""


@dataclass(frozen=True, slots=True)
class SelectorItem:
    """A selector-bearing part of an operation, expanded against its input schema.

    ``references`` are the columns the rest of the expression reads, found by
    analysing the whole expression with each selector replaced by a placeholder
    column. ``output_name`` names a single output (a keyword, an alias, or a
    helper's first argument); ``None`` emits one output per expanded column, or
    names a helper after its first selector's only column.
    """

    role: SelectorItemRole
    selectors: tuple[LiteralSelector, ...]
    references: frozenset[str] = frozenset()
    output_name: str | None = None
    prefix: str = ""
    suffix: str = ""
    pure: bool = False
    exclude_group_keys: bool = False


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
    validate: str | None = None
    suffix: str | None = None
    subset_columns: frozenset[str] | None = frozenset()
    strict: bool = True
    produced_columns: tuple[str, ...] = ()
    index_columns: frozenset[str] | None = None
    selector_items: tuple[SelectorItem, ...] = ()
    """Selector-bearing parts, expanded by ``_evaluate_program`` into the fields above."""
    identity_outputs: frozenset[str] = frozenset()
    """``select``/``with_columns`` outputs that are the unchanged input column."""
    group_keys: frozenset[str] = frozenset()


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


_NO_OPERANDS: Mapping[str, int] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RowCardinalityAnalysis:
    """Finite row-count proof for one accepted linear frame program."""

    supported: bool
    output_upper_bound: int | None
    peak_upper_bound: int | None
    evidence: tuple[str, ...]
    reason: str
    unsupported_operation: str | None = None
    operand_peak_rows: int | None = None
    """Largest frame any single operation in the program *consumes*.

    A join holds its input ports rather than its output, so a memory estimate
    for the join itself is sized from this rather than from the output bound.
    In a chain the later join consumes the earlier join's result, so that
    result — the many-to-many product, if the keys are undeclared — is included
    here and the chain is not mistaken for its original ports.
    """

    has_cross_join: bool = False
    """Whether the program contains a cross join.

    EXEC-P07 measured inner/left/asof joins; a cross join's peak was never
    probed, so it must not inherit their admission.
    """

    depends_on_many_to_many_join: bool = False
    """Whether the program contains a join with no bounding uniqueness contract.

    A join declared ``1:1``, ``1:m`` or ``m:1`` cannot emit more rows than one
    of its operands, so the operand it holds bounds it. Without a declared
    contract — or with ``m:m`` — the only bound is the row product, which the
    certification lane measured to be a real over-run of the input-sized figure.
    """

    operand_reference_counts: Mapping[str, int] = field(default_factory=lambda: _NO_OPERANDS)
    """How many logical join operands each input name supplies.

    One graph edge can be resident more than once: ``df.join(df, ...)`` holds
    the same frame as both ports, and a lookup joined twice in a chain is held
    twice. The estimator charges each port's width once per reference, so a
    self-join is not costed as a single port.
    """


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
_CARDINALITY_ROW_ONLY_METHODS = frozenset({"reverse", "top_k", "bottom_k", "interpolate"})
# Every ``join_asof`` argument that cannot change its "at most one right row per
# left row" contract. Anything else leaves the closed model.
_JOIN_ASOF_KEYWORDS = frozenset(
    {
        "on",
        "left_on",
        "right_on",
        "by",
        "by_left",
        "by_right",
        "strategy",
        "suffix",
        "tolerance",
        "allow_parallel",
        "force_parallel",
        "coalesce",
        "allow_exact_matches",
        "check_sortedness",
    }
)
_SELECT_METHODS = frozenset({"select", "select_seq"})
_SUPPORTED_JOIN_HOW = frozenset({"inner", "left", "semi", "anti"})
_CARDINALITY_JOIN_HOW = frozenset({"inner", "left", "right", "full", "semi", "anti", "cross"})
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
# Methods whose bare string arguments the pinned Polars parses with
# ``str_as_lit=True`` or as plain format/configuration strings, keyed by the
# receiver namespace they must be called on.  The match is receiver-aware, so a
# same-named method on another namespace does not inherit the registration.
# Keep this registry closed: ``then``, ``over``, ``is_in``, ``contains_any``,
# ``to_integer`` and friends intentionally stay out because a bare string names
# another column there.  ``tests/test_column_lineage.py`` audits every entry
# against the pinned Polars source.
_LITERAL_STRING_ARGUMENT_METHODS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "str": frozenset(
            {
                "contains",
                "count_matches",
                "ends_with",
                "extract",
                "extract_all",
                "extract_groups",
                "find",
                "json_path_match",
                "replace",
                "replace_all",
                "split",
                "split_exact",
                "splitn",
                "starts_with",
                "strip_chars",
                "strip_chars_end",
                "strip_chars_start",
                "strip_prefix",
                "strip_suffix",
                "strptime",
                "to_date",
                "to_datetime",
                "to_time",
            }
        ),
        "dt": frozenset(
            {
                "convert_time_zone",
                "offset_by",
                "replace_time_zone",
                "round",
                "strftime",
                "to_string",
                "truncate",
            }
        ),
    }
)
# Plain ``Expr`` methods (not on a namespace) whose every argument the pinned
# Polars parses with ``str_as_lit=True``: a mapping, list, or string passed to
# them is a literal value, never a column. Audited against the Polars source in
# ``tests/test_column_lineage.py`` like the registry above; ``sort_by`` and
# friends stay out because they read strings and collection members as columns.
_LITERAL_ARGUMENT_EXPRESSION_METHODS = frozenset({"replace", "replace_strict"})

# These expression operations can construct more outer rows than their input
# frame supplies. The closed list lives in the shared Polars operation registry
# so the chunk classifier and this analyser cannot classify a name differently;
# it makes the cardinality proof independent from column-lineage support (an
# expression can have exact column dependencies while still being unsafe to
# size by input row count). Polars is pinned; dependency upgrades must audit
# additions to Expr's variable-length API before extending the registry or the
# safe direct-call set.
_ROW_EXPANDING_EXPRESSION_METHODS = unbounded_expansion_expression_methods()

# The frame methods ``_parse_call_sequence`` accepts. Exposed so the registry
# audit can assert it agrees exactly with ``lineage_supported`` frame entries.
_LINEAGE_FRAME_METHODS = frozenset(
    {
        "agg",
        "cast",
        "drop",
        "drop_nulls",
        "explode",
        "fill_null",
        "filter",
        "group_by",
        "groupby",
        "head",
        "join",
        "limit",
        "rename",
        "select",
        "select_seq",
        "shift",
        "slice",
        "sort",
        "tail",
        "unique",
        "unpivot",
        "with_columns",
        "with_row_index",
    }
)
_ROW_BOUND_SAFE_POLARS_CALLS = frozenset(
    {
        # Column selectors name columns; they never change the row count.
        "all",
        "exclude",
        "first",
        "last",
        "nth",
        "arg_sort_by",
        "arg_where",
        "business_day_count",
        "coalesce",
        "col",
        "concat_arr",
        "concat_list",
        "concat_str",
        "cum_fold",
        "cum_reduce",
        "date",
        "date_ranges",
        "datetime",
        "datetime_ranges",
        "duration",
        "element",
        "fold",
        "format",
        "from_epoch",
        "int_ranges",
        "len",
        "linear_spaces",
        "lit",
        "reduce",
        "struct",
        "time",
        "time_ranges",
        "when",
        *_HORIZONTAL_PL_CALL_OUTPUTS,
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


def _unsupported_cardinality(
    reason: str,
    operation: str | None = None,
) -> RowCardinalityAnalysis:
    return RowCardinalityAnalysis(
        supported=False,
        output_upper_bound=None,
        peak_upper_bound=None,
        evidence=(),
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


def _literal_column_name(node: ast.AST) -> str | None:
    """Return a bare string Polars reads as exactly one column name.

    ``*`` and ``^...$`` are selector syntax everywhere Polars accepts a bare
    column name (``select``, ``drop``, ``drop_nulls``, ``sort``, ``unique``,
    ``explode``, ``group_by``, lazy ``unpivot``, horizontal helpers), expanding
    to zero or many columns at runtime. They therefore never prove one literal
    column, and every consumer that reads a bare string as a column must fail
    closed on them rather than size or project a single made-up name.
    """
    name = _literal_string(node)
    if name is None or name == "*" or (name.startswith("^") and name.endswith("$")):
        return None
    return name


def _literal_columns(node: ast.AST) -> frozenset[str] | None:
    name = _literal_column_name(node)
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
    return _literal_column_name(node.args[0])


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


def _may_pass_a_string_argument(node: ast.AST) -> bool:
    """Return whether an expression-method argument could hand Polars a string.

    Polars reads a string held in a variable exactly as it reads a literal
    one, so a name, attribute, or subscript is treated as string syntax.
    Polars iterates lists, tuples, sets, and mapping keys into column
    expressions, so their members are checked too; dtypes and lambdas are
    never strings.
    """
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_may_pass_a_string_argument(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return any(
            key is None or _may_pass_a_string_argument(key) or _may_pass_a_string_argument(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Lambda) or _is_polars_dtype(node):
        return False
    return _may_evaluate_to_python_string(node)


def _guarantees_expression(node: ast.AST) -> bool:
    """Whether *node*'s runtime value is certain to be a Polars expression.

    Call syntax reaching this predicate has already passed (or will fail) the
    structural walk, whose accepted vocabulary only produces expressions or
    raises. Operators return an expression whenever either operand is one.
    """
    if isinstance(node, ast.Call):
        return True
    if isinstance(node, ast.BinOp):
        return _guarantees_expression(node.left) or _guarantees_expression(node.right)
    if isinstance(node, ast.UnaryOp):
        return _guarantees_expression(node.operand)
    return False


def _may_evaluate_to_python_string(node: ast.AST) -> bool:
    """Whether *node* could silently evaluate to a Python string at runtime.

    Polars parses a Python string in expression position as a column name, so
    any value that can be a string smuggles a reference the structural walk
    cannot see (helpers, imported names, string arithmetic, short-circuit
    operators returning an operand). Values that are provably expressions, or
    whose misuse raises at runtime either way, are safe.
    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.BoolOp):
        # ``or``/``and`` return one operand outright; an expression operand
        # evaluated for truth raises, which is fail-visible either way.
        return any(_may_evaluate_to_python_string(value) for value in node.values)
    if isinstance(node, ast.IfExp):
        return _may_evaluate_to_python_string(node.body) or _may_evaluate_to_python_string(
            node.orelse
        )
    if isinstance(node, ast.BinOp):
        if _guarantees_expression(node.left) or _guarantees_expression(node.right):
            return False
        return _may_evaluate_to_python_string(node.left) or _may_evaluate_to_python_string(
            node.right
        )
    if isinstance(node, (ast.UnaryOp, ast.Compare, ast.Call)):
        # Never a string: operators/comparisons yield numbers, booleans, or
        # expressions, and accepted call syntax yields expressions or raises.
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        # A sequence is not itself a string; consumers that read strings out
        # of sequences classify the elements explicitly.
        return False
    return True


def _opaque_helper_argument(node: ast.AST) -> bool:
    """Whether an unregistered-helper argument could carry a column name.

    Unknown helpers may read strings — including strings inside sequences —
    as column names, so both explicit string constants anywhere in the
    argument and any value that could evaluate to a string are opaque.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_opaque_helper_argument(element) for element in node.elts)
    return _may_evaluate_to_python_string(node) or any(
        isinstance(descendant, ast.Constant) and isinstance(descendant.value, str)
        for descendant in ast.walk(node)
    )


def _collect_string_expression_columns(node: ast.AST, columns: set[str]) -> bool:
    """Classify one string-as-column argument, collecting bare references.

    Bare strings (including inside sequences) read the named column here.
    Expression arguments contribute their references through the surrounding
    walk. Anything that could silently evaluate to a Python string fails
    closed rather than under-demanding.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            name = _literal_column_name(node)
            if name is None:
                return False
            columns.add(name)
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_collect_string_expression_columns(element, columns) for element in node.elts)
    return not _may_evaluate_to_python_string(node)


def _when_columns(call: ast.Call) -> frozenset[str] | None:
    """Return the columns ``pl.when`` itself reads, rejecting opaque predicates.

    A bare string predicate and every keyword-constraint name each read one
    column. Constraint values compare as literals, so they carry no reference.
    """
    columns: set[str] = set()
    for argument in call.args:
        if not _collect_string_expression_columns(argument, columns):
            return None
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        columns.add(keyword.arg)
    return frozenset(columns)


def _over_columns(call: ast.Call) -> frozenset[str] | None:
    """Return the columns one ``Expr.over`` partitions (and orders) by.

    Only ``partition_by`` and ``order_by`` are accepted, positionally or by
    keyword, and only as literal column names. Every other keyword is refused:
    ``mapping_strategy='explode'`` in particular changes the row count, which
    the row-bounded expression model must never admit silently.
    """
    columns: set[str] = set()
    nodes: list[ast.AST] = list(call.args)
    for keyword in call.keywords:
        if keyword.arg not in {"partition_by", "order_by"}:
            return None
        nodes.append(keyword.value)
    if not nodes:
        return None
    for node in nodes:
        parsed = _literal_columns(node)
        if parsed is None:
            return None
        columns.update(parsed)
    return frozenset(columns)


def _horizontal_columns(call: ast.Call) -> frozenset[str] | None:
    """Return literal column references passed to one horizontal helper.

    Strings and string sequences name columns for these helpers; keyword
    arguments are scalar configuration and must not be able to carry one.
    """
    columns: set[str] = set()
    for argument in call.args:
        if not _collect_string_expression_columns(argument, columns):
            return None
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        if _may_evaluate_to_python_string(keyword.value):
            return None
    return frozenset(columns)


def _referenced_columns(node: ast.AST, *, rows_only: bool = False) -> frozenset[str] | None:
    """Return literal column references, rejecting schema selectors.

    Beyond ``pl.col``, bare strings read columns wherever the closed model
    knows Polars parses them as one (horizontal helpers, ``pl.when``
    predicates and constraint names). A runtime-formatted string could
    evaluate to any column name, so f-strings reject the whole expression.

    ``rows_only`` is for row-count proofs, which never use the references: a
    variable that names another column to an expression method changes which
    column is read but not the row count, so only literal string arguments
    (whose meaning is still unaudited) are refused there.
    """
    string_argument = _has_direct_string_argument if rows_only else _may_pass_a_string_argument
    columns: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.JoinedStr):
            return None
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
            horizontal = _horizontal_columns(child)
            if horizontal is None:
                return None
            columns.update(horizontal)
        elif direct == "when":
            predicate_columns = _when_columns(child)
            if predicate_columns is None:
                return None
            columns.update(predicate_columns)
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
            # A method chain is only attributable when it is rooted in a
            # provable expression. A string, helper, or imported root would
            # make the whole chain a Python-level value ("a".upper() is the
            # column A to a select) the walk cannot account for.
            chain_root: ast.AST = child.func.value
            while isinstance(chain_root, ast.Attribute):
                chain_root = chain_root.value
            if _may_evaluate_to_python_string(chain_root):
                return None
            is_name_suffix = (
                method == "suffix"
                and isinstance(child.func.value, ast.Attribute)
                and child.func.value.attr == "name"
            )
            is_literal_argument_method = (
                method in _LITERAL_STRING_ARGUMENT_METHODS.get(child.func.value.attr, frozenset())
                if isinstance(child.func.value, ast.Attribute)
                else method in _LITERAL_ARGUMENT_EXPRESSION_METHODS
            )
            if method == "alias" or is_name_suffix or is_literal_argument_method:
                continue
            if method == "over":
                # ``over`` names partition (and ordering) columns, so its bare
                # strings are column references rather than literals. Any other
                # keyword — ``mapping_strategy`` above all, which can expand
                # rows — leaves the closed model.
                partitions = _over_columns(child)
                if partitions is None:
                    return None
                columns.update(partitions)
                continue
            # Polars expression methods are inconsistent about whether bare
            # strings are literals or column expressions (for example,
            # ``then('backup')`` reads a column while ``str.contains('x')``
            # consumes a literal).  Until a method has an explicit transfer,
            # rejecting any argument that can be a string is the only sound
            # choice.
            if any(
                string_argument(argument)
                for argument in [
                    *child.args,
                    *(keyword.value for keyword in child.keywords),
                ]
            ):
                return None
        elif direct not in {None, "len", "lit"}:
            # Strings accepted by many top-level Polars expression helpers
            # mean column names, and a value that can evaluate to a string —
            # directly or inside a sequence — can smuggle one. Unknown
            # helpers with either, positionally or as keyword values, are
            # schema-dependent until they receive an explicit rule; only
            # ``pl.lit`` treats every argument as a literal.
            for argument in [*child.args, *(keyword.value for keyword in child.keywords)]:
                if _opaque_helper_argument(argument):
                    return None
    return frozenset(columns)


def _expression_has_unbounded_row_effect(node: ast.AST) -> bool:
    """Reject expression syntax whose successful result may outgrow the frame.

    Top-level Polars constructors are allow-listed because a new constructor can
    manufacture an arbitrary-length Series. Expr methods are row-bounded by
    default except the audited variable-length and user-callback operations.
    Nested list/struct namespace methods retain the outer row count; their
    ``explode``/``flatten`` exits are still caught by method name.
    """

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        direct = _polars_call_name(child)
        if direct is not None:
            if direct not in _ROW_BOUND_SAFE_POLARS_CALLS:
                return True
            continue
        if isinstance(child.func, ast.Attribute):
            if child.func.attr in _ROW_EXPANDING_EXPRESSION_METHODS:
                return True
            # Foreign/free calls were already rejected by
            # _referenced_columns; lambdas and named callbacks passed into an
            # Expr method need an explicit guard because they are not calls in
            # the AST themselves.
            if any(isinstance(argument, (ast.Lambda, ast.Name)) for argument in child.args):
                if child.func.attr in {"map_elements", "rolling_map"}:
                    return True
    return False


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


def _is_scalar_literal(node: ast.AST) -> bool:
    """Whether *node* is a literal scalar, optionally signed (``0``, ``-1``, ``'x'``)."""
    if isinstance(node, ast.UnaryOp):
        node = node.operand
    return isinstance(node, ast.Constant)


def _expression_output_name(node: ast.AST) -> str | None:
    column = _pl_col_name(node)
    if column is not None:
        return column
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _NAME_CHANGING_METHOD_NAMES:
            # Only a literal ``alias`` names the output; a computed alias or a
            # ``pipe`` callback renames it to a value the syntax cannot read.
            return _alias_name(node)
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
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and func.value.attr in _NAME_CHANGING_NAMESPACE_NAMES
        ):
            # The pinned Polars appends ``name.suffix`` to the output name of the
            # expression it closes (``pl.col('a').alias('x')`` becomes ``x_s``,
            # ``pl.lit(1) + pl.col('a')`` becomes ``literal_s``). Every other
            # ``name`` or ``struct`` method renames or splits the output
            # unreadably.
            if func.value.attr != "name" or func.attr != "suffix":
                return None
            suffix = (
                _literal_string(node.args[0]) if len(node.args) == 1 and not node.keywords else None
            )
            base = _expression_output_name(func.value.value)
            if suffix is None or base is None:
                return None
            return f"{base}{suffix}"
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
    if isinstance(node, ast.BinOp):
        # Reflected arithmetic (``2 * pl.col('a')``) is named ``literal`` too.
        return _expression_output_name(node.left)
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            return None
        if _is_scalar_literal(node.left):
            # ``0 < pl.col('a')`` runs as the expression's reflected comparison,
            # which keeps the expression's name.
            return _expression_output_name(node.comparators[0])
        if _guarantees_expression(node.left):
            return _expression_output_name(node.left)
        return None
    if isinstance(node, ast.UnaryOp):
        return _expression_output_name(node.operand)
    if isinstance(node, ast.BoolOp) and node.values:
        return _expression_output_name(node.values[0])
    if isinstance(node, ast.Constant):
        return "literal"
    return None


_SELECTOR_PLACEHOLDER = "\x00haute-selector-{}"


def _selector_nodes(node: ast.AST, aliases: frozenset[str]) -> list[ast.AST]:
    """Return the maximal literal-selector subtrees of *node*, in source order."""
    if isinstance(node, (ast.Call, ast.BinOp, ast.UnaryOp)) and (
        literal_selector(node, aliases=aliases) is not None
    ):
        return [node]
    found: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        found.extend(_selector_nodes(child, aliases))
    return found


def _placeholder(index: int) -> str:
    return _SELECTOR_PLACEHOLDER.format(index)


def _substitute_selectors(node: ast.AST, targets: Sequence[ast.AST]) -> ast.AST:
    """Copy *node* with each target selector replaced by a placeholder ``pl.col``."""
    positions = {id(target): index for index, target in enumerate(targets)}

    def rebuild(current: ast.AST) -> ast.AST:
        index = positions.get(id(current))
        if index is not None:
            return ast.Call(
                func=ast.Attribute(value=ast.Name(id="pl", ctx=ast.Load()), attr="col"),
                args=[ast.Constant(_placeholder(index))],
                keywords=[],
            )
        clone = copy.copy(current)
        for field_name, value in ast.iter_fields(current):
            if isinstance(value, list):
                setattr(
                    clone,
                    field_name,
                    [rebuild(item) if isinstance(item, ast.AST) else item for item in value],
                )
            elif isinstance(value, ast.AST):
                setattr(clone, field_name, rebuild(value))
        return clone

    return rebuild(node)


_NAME_CHANGING_METHOD_NAMES = frozenset({"alias", "pipe"})
_NAME_CHANGING_NAMESPACE_NAMES = frozenset({"name", "struct"})


@dataclass(frozen=True, slots=True)
class _SelectorNaming:
    """How a selector-rooted expression names its outputs."""

    selector: LiteralSelector
    root: ast.AST
    inner: ast.AST
    """The expression without its terminal naming call."""
    alias: str | None
    prefix: str
    suffix: str


def _receiver_chain_reaches(expression: ast.AST, target: ast.AST) -> bool:
    """Whether *target* is reached by following receivers, left operands, and operands."""
    current = expression
    while current is not target:
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            receiver: ast.AST = current.func.value
            if isinstance(receiver, ast.Attribute) and not isinstance(receiver.value, ast.Name):
                receiver = receiver.value
            current = receiver
        elif isinstance(current, ast.BinOp):
            current = current.left
        elif isinstance(current, ast.UnaryOp):
            current = current.operand
        else:
            return False
    return True


def _selector_naming(expression: ast.AST, aliases: frozenset[str]) -> _SelectorNaming | None:
    """Resolve the output naming of an expression rooted at a literal selector.

    Only the outermost call may name the outputs (``alias``, ``.name.prefix``,
    ``.name.suffix`` with a literal argument). A naming step anywhere deeper in
    the chain — an intermediate ``alias``, any other ``.name`` or ``.struct``
    method, or ``pipe`` — renames outputs in a way the per-column expansion
    cannot follow, so the expression is refused.
    """
    inner = expression
    alias: str | None = None
    prefix = suffix = ""
    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
        func = inner.func
        literal = (
            _literal_string(inner.args[0]) if len(inner.args) == 1 and not inner.keywords else None
        )
        if func.attr == "alias":
            if literal is None:
                return None
            alias = literal
            inner = func.value
        elif isinstance(func.value, ast.Attribute) and func.value.attr == "name":
            if func.attr not in {"prefix", "suffix"} or literal is None:
                return None
            prefix, suffix = (literal, "") if func.attr == "prefix" else ("", literal)
            inner = func.value.value
    pure = literal_selector(inner, aliases=aliases)
    if pure is not None:
        return _SelectorNaming(pure, inner, inner, alias, prefix, suffix)
    found = selector_root(inner, aliases=aliases)
    if found is None:
        return None
    selector, root = found
    current = inner
    while current is not root:
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            if current.func.attr in _NAME_CHANGING_METHOD_NAMES:
                return None
            receiver: ast.AST = current.func.value
            if isinstance(receiver, ast.Attribute) and not isinstance(receiver.value, ast.Name):
                if receiver.attr in _NAME_CHANGING_NAMESPACE_NAMES:
                    return None
                receiver = receiver.value
            current = receiver
        elif isinstance(current, ast.BinOp):
            current = current.left
        else:
            # ``selector_root`` reached the root only through receivers, left
            # operands, and unary operands, so this step is a unary operand.
            assert isinstance(current, ast.UnaryOp)
            current = current.operand
    return _SelectorNaming(selector, root, inner, alias, prefix, suffix)


def _selector_expression_item(
    expression: ast.AST,
    aliases: frozenset[str],
    *,
    method: str,
    keyword: str | None = None,
) -> SelectorItem | _ParseFailure | None:
    """Parse a selector-bearing output expression, or return ``None`` without one.

    A failure is either ``selector_nested`` (a selector outside the shapes the
    model expands) or the operation's ordinary ``dynamic_<method>`` reason when
    the whole expression, analysed with placeholder columns, is not provable.
    """
    nodes = _selector_nodes(expression, aliases)
    if not nodes:
        return None
    dynamic = _ParseFailure(f"dynamic_{method}", method)
    selectors = tuple(literal_selector(node, aliases=aliases) for node in nodes)
    assert all(selector is not None for selector in selectors)
    resolved = tuple(selector for selector in selectors if selector is not None)
    if nodes == [expression]:
        return SelectorItem(
            SelectorItemRole.OUTPUT, resolved, output_name=keyword, pure=keyword is None
        )

    naming = _selector_naming(expression, aliases)
    if naming is not None and len(nodes) == 1 and naming.root is nodes[0]:
        if keyword is not None and (naming.alias is not None or naming.prefix or naming.suffix):
            return dynamic
        if naming.inner is naming.root:
            # A renamed pure selection: every output reads only its own column.
            return SelectorItem(
                SelectorItemRole.OUTPUT,
                resolved,
                output_name=keyword or naming.alias,
                prefix=naming.prefix,
                suffix=naming.suffix,
            )
        substituted = _substitute_selectors(naming.inner, nodes)
        references = _referenced_columns(substituted)
        # With no deeper naming step the computation keeps its root's name.
        if references is None or _expression_output_name(substituted) != _placeholder(0):
            return dynamic
        return SelectorItem(
            SelectorItemRole.OUTPUT,
            resolved,
            references=references - {_placeholder(0)},
            output_name=keyword or naming.alias,
            prefix=naming.prefix,
            suffix=naming.suffix,
        )

    inner = expression
    helper = inner
    alias = _alias_name(inner)
    if alias is not None:
        assert isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
        helper = inner.func.value
    if (
        isinstance(helper, ast.Call)
        and _polars_call_name(helper) in _HORIZONTAL_PL_CALL_OUTPUTS
        and helper.args
        and all(any(node is argument for argument in helper.args) for node in nodes)
    ):
        first_is_selector = any(node is helper.args[0] for node in nodes)
        if first_is_selector:
            ordered = [helper.args[0], *[node for node in nodes if node is not helper.args[0]]]
        else:
            ordered = list(nodes)
        substituted = _substitute_selectors(inner, ordered)
        references = _referenced_columns(substituted)
        if references is None:
            return dynamic
        output_name = keyword or alias
        if output_name is None and not first_is_selector:
            output_name = _expression_output_name(substituted)
            if output_name is None:
                return dynamic
        ordered_selectors = tuple(literal_selector(node, aliases=aliases) for node in ordered)
        return SelectorItem(
            SelectorItemRole.HELPER_OUTPUT,
            tuple(selector for selector in ordered_selectors if selector is not None),
            references=references - {_placeholder(index) for index in range(len(ordered))},
            output_name=output_name,
        )
    if len(nodes) == 1 and _receiver_chain_reaches(expression, nodes[0]):
        # Rooted at its only selector but named in a way the expansion cannot follow.
        return dynamic
    return _ParseFailure("selector_nested", method)


def _selector_reference_item(
    expression: ast.AST, aliases: frozenset[str], *, rows_only: bool = False
) -> SelectorItem | None | _ParseFailure:
    """Parse a predicate that reads selector columns, or ``None`` without one."""
    nodes = _selector_nodes(expression, aliases)
    if not nodes:
        return None
    substituted = _substitute_selectors(expression, nodes)
    references = _referenced_columns(substituted, rows_only=rows_only)
    if references is None:
        return _ParseFailure("dynamic_filter", "filter")
    selectors = tuple(literal_selector(node, aliases=aliases) for node in nodes)
    return SelectorItem(
        SelectorItemRole.REFERENCES,
        tuple(selector for selector in selectors if selector is not None),
        references=references - {_placeholder(index) for index in range(len(nodes))},
    )


def _selector_column_item(node: ast.AST, aliases: frozenset[str]) -> SelectorItem | None:
    """Parse a frame-method column argument that is a literal selector."""
    selector = literal_selector(node, aliases=aliases)
    if selector is None:
        return None
    return SelectorItem(SelectorItemRole.COLUMNS, (selector,))


def _readable_with_selectors(expression: ast.AST, aliases: frozenset[str]) -> bool:
    """Whether an expression's references are provable once selectors become columns."""
    if _is_column_selector_call(expression) or _literal_column_name(expression) is not None:
        return True
    if isinstance(expression, (ast.List, ast.Tuple)):
        return all(_readable_with_selectors(element, aliases) for element in expression.elts)
    nodes = _selector_nodes(expression, aliases)
    candidate = _substitute_selectors(expression, nodes) if nodes else expression
    return _referenced_columns(candidate, rows_only=True) is not None


def _normalise_expression_outputs(
    call: ast.Call,
    *,
    allow_plain_strings: bool,
    selector_aliases: frozenset[str] = frozenset(),
) -> (
    tuple[tuple[tuple[str, frozenset[str]], ...], tuple[SelectorItem, ...], frozenset[str]]
    | _ParseFailure
    | None
):
    """Return outputs, selector items, and identity outputs of ``select``/``with_columns``."""
    method = call.func.attr if isinstance(call.func, ast.Attribute) else "select"
    method = "select" if method == "select_seq" else method
    outputs: list[tuple[str, frozenset[str]]] = []
    items: list[SelectorItem] = []
    identity: set[str] = set()
    failure: list[_ParseFailure] = []

    def append_expression(expression: ast.AST) -> bool:
        # Polars treats a bare string as a column expression in both
        # ``select`` and ``with_columns``.  Lists/tuples are accepted only by
        # select; with_columns requires each positional expression directly.
        # A selector string (``*``/regex) is not one column and fails below.
        name = _literal_column_name(expression)
        if name is not None:
            outputs.append((name, frozenset({name})))
            identity.add(name)
            return True
        if allow_plain_strings:
            if isinstance(expression, (ast.List, ast.Tuple)):
                return all(append_expression(element) for element in expression.elts)
        if _may_evaluate_to_python_string(expression):
            return False
        item = _selector_expression_item(expression, selector_aliases, method=method)
        if isinstance(item, _ParseFailure):
            failure.append(item)
            return False
        if item is not None:
            items.append(item)
            return True
        references = _referenced_columns(expression)
        if references is None:
            return False
        output_name = _expression_output_name(expression)
        if output_name is not None:
            outputs.append((output_name, references))
            if _pl_col_name(expression) == output_name:
                identity.add(output_name)
            return True
        return False

    for expression in call.args:
        if not append_expression(expression):
            return failure[0] if failure else None
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        bare_column = _literal_column_name(keyword.value)
        if bare_column is not None:
            references = frozenset({bare_column})
            if bare_column == keyword.arg:
                identity.add(bare_column)
        elif _may_evaluate_to_python_string(keyword.value):
            return None
        else:
            keyword_item = _selector_expression_item(
                keyword.value, selector_aliases, method=method, keyword=keyword.arg
            )
            if isinstance(keyword_item, _ParseFailure):
                return keyword_item
            if keyword_item is not None:
                items.append(keyword_item)
                continue
            maybe_references = _referenced_columns(keyword.value)
            if maybe_references is None:
                return None
            references = maybe_references
            if _pl_col_name(keyword.value) == keyword.arg:
                identity.add(keyword.arg)
        outputs.append((keyword.arg, references))
    names = [name for name, _references in outputs]
    if (not outputs and not items) or len(names) != len(set(names)):
        return None
    return tuple(outputs), tuple(items), frozenset(identity)


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
    selector_aliases: frozenset[str] = frozenset(),
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
    aggregate_items: list[SelectorItem] = []
    positional: list[ast.AST] = []
    for expression in aggregate_call.args:
        if isinstance(expression, (ast.List, ast.Tuple)):
            positional.extend(expression.elts)
        else:
            positional.append(expression)
    for positional_expression in positional:
        aggregate_item = _selector_expression_item(
            positional_expression, selector_aliases, method="aggregate"
        )
        if isinstance(aggregate_item, _ParseFailure):
            return aggregate_item
        if aggregate_item is not None:
            aggregate_items.append(replace(aggregate_item, exclude_group_keys=True))
            continue
        output = _alias_name(positional_expression)
        references = _referenced_columns(positional_expression)
        if output is None or references is None:
            return _ParseFailure("dynamic_aggregate", "agg")
        expressions.append((output, references))
    for keyword in aggregate_call.keywords:
        if keyword.arg is None:
            return _ParseFailure("dynamic_aggregate", "agg")
        bare_column = _literal_column_name(keyword.value)
        if bare_column is not None:
            references = frozenset({bare_column})
        elif _may_evaluate_to_python_string(keyword.value):
            return _ParseFailure("dynamic_aggregate", "agg")
        else:
            keyword_item = _selector_expression_item(
                keyword.value, selector_aliases, method="aggregate", keyword=keyword.arg
            )
            if isinstance(keyword_item, _ParseFailure):
                return keyword_item
            if keyword_item is not None:
                aggregate_items.append(replace(keyword_item, exclude_group_keys=True))
                continue
            maybe_references = _referenced_columns(keyword.value)
            if maybe_references is None:
                return _ParseFailure("dynamic_aggregate", "agg")
            references = maybe_references
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
        selector_items=tuple(aggregate_items),
        group_keys=frozenset(keys),
    )


def _parse_join(
    call: ast.Call,
    input_names: frozenset[str],
    *,
    cardinality_only: bool = False,
) -> LineageOperation | _ParseFailure:
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
        return _ParseFailure("dynamic_join_input", "join")
    right_input = call.args[0].id
    if right_input not in input_names:
        return _ParseFailure("unknown_join_input", "join")

    how = "inner"
    validate = "m:m"
    suffix = "_right"
    on_node: ast.AST | None = None
    left_on_node: ast.AST | None = None
    right_on_node: ast.AST | None = None
    for keyword in call.keywords:
        if keyword.arg == "how":
            how = _literal_string(keyword.value) or ""
        elif keyword.arg == "validate":
            literal_validate = _literal_string(keyword.value)
            if literal_validate is None:
                return _ParseFailure("dynamic_join_validate", "join")
            try:
                validate = normalise_join_validation(literal_validate)
            except (TypeError, ValueError):
                return _ParseFailure("unsupported_join_semantics", "join")
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
    supported_how = _CARDINALITY_JOIN_HOW if cardinality_only else _SUPPORTED_JOIN_HOW
    if how not in supported_how or (not cardinality_only and not suffix):
        return _ParseFailure("unsupported_join_semantics", "join")
    if cardinality_only and how == "cross":
        if any(node is not None for node in (on_node, left_on_node, right_on_node)):
            return _ParseFailure("unsupported_join_semantics", "join")
        return LineageOperation(
            kind=LineageOperationKind.JOIN,
            method="join",
            right_input=right_input,
            how=how,
            validate=validate,
            suffix=suffix,
        )
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
        validate=validate,
        suffix=suffix,
    )


_ROW_NON_INCREASING_FRAME_METHODS = frozenset(
    {
        "sort",
        "reverse",
        "top_k",
        "bottom_k",
        "head",
        "tail",
        "limit",
        "slice",
        "unique",
        "filter",
        "drop",
        "drop_nulls",
        "rename",
        "cast",
        "with_row_index",
    }
)

# A projection is row-non-increasing only when it projects: ``select`` and
# ``with_columns`` evaluate arbitrary expressions, and a range constructor
# (``select(pl.int_range(0, 1_000_000))``) synthesises rows out of nothing.
_ROW_NON_INCREASING_PROJECTION_METHODS = frozenset({"select", "with_columns"})


def _is_plain_column_expression(node: ast.AST) -> bool:
    """Whether ``node`` only ever names an existing column.

    A bare string, ``pl.col('x')``, and either of those with a trailing
    ``.alias(...)`` read a column and cannot change the frame's height.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_is_plain_column_expression(element) for element in node.elts)
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and bool(node.value)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Attribute):
            return False
        if node.func.attr == "alias":
            return (
                len(node.args) == 1
                and not node.keywords
                and _literal_string(node.args[0]) is not None
                and _is_plain_column_expression(node.func.value)
            )
        return _polars_call_name(node) == "col" and _pl_col_name(node) is not None
    return False


def _projects_only_existing_columns(call: ast.Call) -> bool:
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    return bool(arguments) and all(_is_plain_column_expression(node) for node in arguments)


def _row_non_increasing_chain_input(
    node: ast.AST,
    input_names: frozenset[str],
) -> str | None:
    """Return the input name bounding ``node``'s row count, or ``None``.

    A right-hand operand is accepted when it is an input frame, optionally
    followed by methods that can only preserve or reduce its rows. That input's
    row count is then an upper bound for the operand, which is all a peak-memory
    bound needs. Anything else (an expanding call, an unknown helper) is left
    unresolved rather than guessed at.
    """
    current = node
    while isinstance(current, ast.Call):
        if not isinstance(current.func, ast.Attribute):
            return None
        method = current.func.attr
        if method in _ROW_NON_INCREASING_PROJECTION_METHODS:
            if not _projects_only_existing_columns(current):
                return None
        elif method not in _ROW_NON_INCREASING_FRAME_METHODS:
            return None
        current = current.func.value
    if not isinstance(current, ast.Name) or current.id not in input_names:
        return None
    return current.id


def _parse_join_asof(
    call: ast.Call,
    input_names: frozenset[str],
) -> LineageOperation | _ParseFailure:
    """Bound an as-of join's row count (cardinality analysis only).

    A Polars as-of join matches each left row against at most one right row, so
    its output row count is exactly a left join's with a unique right key,
    whatever its keys, ``by`` groups, ``strategy``, or ``tolerance`` say — and
    the right frame is still resident, so it enters the peak. Column lineage
    has no transfer for it and never reaches here, so the projection planner
    keeps the boundary at complete width.
    """
    if len(call.args) != 1:
        return _ParseFailure("dynamic_join_asof_input", "join_asof")
    right_input = _row_non_increasing_chain_input(call.args[0], input_names)
    if right_input is None:
        return _ParseFailure("dynamic_join_asof_input", "join_asof")
    for keyword in call.keywords:
        if keyword.arg not in _JOIN_ASOF_KEYWORDS:
            return _ParseFailure("unsupported_join_asof_option", "join_asof")
    return LineageOperation(
        kind=LineageOperationKind.JOIN,
        method="join_asof",
        right_input=right_input,
        how="left",
        validate="m:1",
        suffix="_right",
    )


def _is_literal_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


_COLUMN_SELECTOR_POLARS_CALLS = frozenset({"all", "exclude"})


def _is_column_selector_call(node: ast.AST) -> bool:
    """``pl.all()`` / ``pl.exclude(<literals>)`` select existing columns.

    Column lineage cannot name what they expand to, but for a row count that
    is irrelevant: a selector emits exactly the rows it reads. Only the
    literal forms qualify; a computed exclusion list is not admitted.
    """
    return (
        isinstance(node, ast.Call)
        and _polars_call_name(node) in _COLUMN_SELECTOR_POLARS_CALLS
        and not node.keywords
        and all(_literal_columns(argument) is not None for argument in node.args)
    )


def _is_polars_dtype(node: ast.AST) -> bool:
    """Return whether ``node`` is a ``pl.<Name>`` dtype or a literal dtype call.

    ``pl.Int64`` and ``pl.Datetime('us')`` name a dtype; anything else (a name
    the analyser cannot see, a computed dtype) is not admitted.
    """
    if isinstance(node, ast.Call):
        if not all(isinstance(argument, ast.Constant) for argument in node.args):
            return False
        if not all(isinstance(keyword.value, ast.Constant) for keyword in node.keywords):
            return False
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "pl"
    )


def _parse_cast(call: ast.Call) -> LineageOperation | _ParseFailure:
    """``df.cast(...)`` keeps every column name and every row.

    A dict mapping demands the columns it names -- Polars raises
    ``ColumnNotFound`` for a mapped column that is not there -- while a
    whole-frame cast demands nothing beyond what the downstream code asks for.
    """
    failure = _ParseFailure("dynamic_cast", "cast")
    if len(call.args) != 1:
        return failure
    for keyword in call.keywords:
        if keyword.arg != "strict" or _literal_bool(keyword.value) is None:
            return failure
    argument = call.args[0]
    referenced: frozenset[str] = frozenset()
    if isinstance(argument, ast.Dict):
        mapped: set[str] = set()
        for key, value in zip(argument.keys, argument.values, strict=True):
            if key is None:
                return failure
            name = _literal_string(key)
            if name is None or not _is_polars_dtype(value):
                return failure
            mapped.add(name)
        if not mapped:
            return failure
        referenced = frozenset(mapped)
    elif not _is_polars_dtype(argument):
        return failure
    return LineageOperation(
        kind=LineageOperationKind.READ_COLUMNS,
        method="cast",
        referenced_columns=referenced,
    )


def _parse_shift(call: ast.Call) -> LineageOperation | _ParseFailure:
    """``df.shift(n)`` moves values along the frame without changing its shape."""
    failure = _ParseFailure("dynamic_shift", "shift")
    if len(call.args) > 1:
        return failure
    periods: ast.AST | None = call.args[0] if call.args else None
    for keyword in call.keywords:
        if keyword.arg == "n" and periods is None:
            periods = keyword.value
        elif keyword.arg == "fill_value":
            if not isinstance(keyword.value, ast.Constant):
                return failure
        else:
            return failure
    if periods is None:
        return failure
    if not (
        isinstance(periods, ast.Constant)
        and isinstance(periods.value, int)
        and not isinstance(periods.value, bool)
    ):
        return failure
    return LineageOperation(kind=LineageOperationKind.ROW_ONLY, method="shift")


def _parse_drop(
    call: ast.Call, selector_aliases: frozenset[str] = frozenset()
) -> LineageOperation | _ParseFailure:
    dropped: set[str] = set()
    items: list[SelectorItem] = []
    strict = True
    for node in call.args:
        item = _selector_column_item(node, selector_aliases)
        if item is not None:
            items.append(item)
            continue
        parsed = _literal_columns(node)
        if parsed is None:
            return _ParseFailure("dynamic_drop", "drop")
        dropped.update(parsed)
    for keyword in call.keywords:
        literal = None if keyword.arg != "strict" else _literal_bool(keyword.value)
        if literal is None:
            return _ParseFailure("dynamic_drop", "drop")
        strict = literal
    if not dropped and not items:
        return _ParseFailure("dynamic_drop", "drop")
    return LineageOperation(
        kind=LineageOperationKind.DROP,
        method="drop",
        subset_columns=frozenset(dropped),
        # Polars requires every named column to exist for a strict drop.
        referenced_columns=frozenset(dropped) if strict else frozenset(),
        strict=strict,
        selector_items=tuple(items),
    )


def _parse_drop_nulls(
    call: ast.Call, selector_aliases: frozenset[str] = frozenset()
) -> LineageOperation | _ParseFailure:
    if len(call.args) > 1:
        return _ParseFailure("dynamic_drop_nulls", "drop_nulls")
    subset_node: ast.AST | None = call.args[0] if call.args else None
    for keyword in call.keywords:
        if keyword.arg != "subset" or subset_node is not None:
            return _ParseFailure("dynamic_drop_nulls", "drop_nulls")
        subset_node = keyword.value
    subset: frozenset[str] | None = None
    item: SelectorItem | None = None
    if subset_node is not None and not _is_literal_none(subset_node):
        item = _selector_column_item(subset_node, selector_aliases)
        if item is not None:
            subset = frozenset()
        else:
            subset = _literal_columns(subset_node)
            if subset is None:
                return _ParseFailure("dynamic_drop_nulls", "drop_nulls")
    return LineageOperation(
        kind=LineageOperationKind.DROP_NULLS,
        method="drop_nulls",
        subset_columns=subset,
        referenced_columns=subset if subset is not None else frozenset(),
        selector_items=() if item is None else (item,),
    )


def _parse_with_row_index(call: ast.Call) -> LineageOperation | _ParseFailure:
    failure = _ParseFailure("dynamic_with_row_index", "with_row_index")
    if len(call.args) > 2:
        return failure
    name_node: ast.AST | None = call.args[0] if call.args else None
    offset_node: ast.AST | None = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "name" and name_node is None:
            name_node = keyword.value
        elif keyword.arg == "offset" and offset_node is None:
            offset_node = keyword.value
        else:
            return failure
    name = "index" if name_node is None else _literal_string(name_node)
    if name is None:
        return failure
    if offset_node is not None and not (
        isinstance(offset_node, ast.Constant)
        and isinstance(offset_node.value, int)
        and not isinstance(offset_node.value, bool)
        and offset_node.value >= 0
    ):
        return failure
    return LineageOperation(
        kind=LineageOperationKind.WITH_ROW_INDEX,
        method="with_row_index",
        produced_columns=(name,),
    )


def _parse_unpivot(call: ast.Call, *, cardinality_only: bool) -> LineageOperation | _ParseFailure:
    failure = _ParseFailure("dynamic_unpivot", "unpivot")
    if len(call.args) > 1:
        return failure
    on_node: ast.AST | None = call.args[0] if call.args else None
    index_node: ast.AST | None = None
    variable_node: ast.AST | None = None
    value_node: ast.AST | None = None
    for keyword in call.keywords:
        if keyword.arg == "on" and on_node is None:
            on_node = keyword.value
        elif keyword.arg == "index" and index_node is None:
            index_node = keyword.value
        elif keyword.arg == "variable_name" and variable_node is None:
            variable_node = keyword.value
        elif keyword.arg == "value_name" and value_node is None:
            value_node = keyword.value
        else:
            return failure

    on: frozenset[str] | None = None
    if on_node is not None and not _is_literal_none(on_node):
        on = _literal_columns(on_node)
        if not on:
            return failure
    index: frozenset[str] = frozenset()
    if index_node is not None and not _is_literal_none(index_node):
        literal_index = _literal_columns(index_node)
        if literal_index is None:
            return failure
        index = literal_index
    names: list[str] = []
    for node, default in ((variable_node, "variable"), (value_node, "value")):
        if node is None or _is_literal_none(node):
            names.append(default)
            continue
        literal_name = _literal_string(node)
        if literal_name is None:
            return failure
        names.append(literal_name)
    variable_name, value_name = names
    if on is None and cardinality_only:
        # Cardinality analysis never receives an input schema, so an omitted
        # ``on`` list has no resolvable column count.
        return failure
    if (
        variable_name == value_name
        or {variable_name, value_name} & index
        or (on is not None and on & index)
    ):
        return _ParseFailure("invalid_unpivot", "unpivot")
    return LineageOperation(
        kind=LineageOperationKind.UNPIVOT,
        method="unpivot",
        subset_columns=on,
        index_columns=index,
        produced_columns=(variable_name, value_name),
        referenced_columns=(on or frozenset()) | index,
    )


def _parse_call_sequence(
    calls: list[ast.Call],
    input_names: frozenset[str],
    *,
    cardinality_only: bool = False,
    selector_aliases: frozenset[str] = frozenset(),
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
            if cardinality_only:
                # Whatever expressions define the groups or aggregates, a
                # successful Polars group-by aggregation emits no more rows
                # than it consumes. Column lineage remains deliberately
                # stricter because it must also name every output.
                operations.append(
                    LineageOperation(
                        kind=LineageOperationKind.GROUP_BY_AGG,
                        method="group_by.agg",
                    )
                )
            else:
                parsed_group = _parse_group_by_agg(call, aggregate, selector_aliases)
                if isinstance(parsed_group, _ParseFailure):
                    return [], parsed_group
                operations.append(parsed_group)
            index += 2
            continue
        if method == "agg":
            return [], _ParseFailure("orphan_aggregate", method)
        if method in _SELECT_METHODS:
            if cardinality_only and any(
                _expression_has_unbounded_row_effect(expression)
                for expression in [
                    *call.args,
                    *(keyword.value for keyword in call.keywords),
                ]
            ):
                return [], _ParseFailure("row_expansion_unbounded", method)
            normalised = _normalise_expression_outputs(
                call, allow_plain_strings=True, selector_aliases=selector_aliases
            )
            if isinstance(normalised, _ParseFailure):
                if not cardinality_only:
                    return [], normalised
                normalised = None
            outputs, select_items, select_identity = (
                normalised if normalised is not None else (None, (), frozenset())
            )
            if outputs is None:
                # Row counts do not need the output names: a selection whose
                # expressions are all readable and row-bounded emits at most
                # the current height (one row over an empty frame for a
                # scalar), so a selector such as ``pl.all()`` is still proven.
                # Column lineage needs every name and stays stricter, and an
                # unreadable expression (a call the analyser cannot see
                # through) is rejected on both paths.
                readable = cardinality_only and all(
                    _readable_with_selectors(expression, selector_aliases)
                    for expression in [
                        *call.args,
                        *(keyword.value for keyword in call.keywords),
                    ]
                )
                if not readable:
                    return [], _ParseFailure("dynamic_select", method)
                outputs = ()
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.SELECT,
                    method=method,
                    output_to_inputs=outputs,
                    selector_items=select_items,
                    identity_outputs=select_identity,
                )
            )
        elif method == "with_columns":
            if cardinality_only and any(
                _expression_has_unbounded_row_effect(expression)
                for expression in [
                    *call.args,
                    *(keyword.value for keyword in call.keywords),
                ]
            ):
                return [], _ParseFailure("row_expansion_unbounded", method)
            normalised = _normalise_expression_outputs(
                call, allow_plain_strings=False, selector_aliases=selector_aliases
            )
            if cardinality_only and (normalised is None or isinstance(normalised, _ParseFailure)):
                # Row counts do not need output names (see ``select`` above).
                if not all(
                    _readable_with_selectors(expression, selector_aliases)
                    for expression in [
                        *call.args,
                        *(keyword.value for keyword in call.keywords),
                    ]
                ):
                    return [], _ParseFailure("dynamic_with_columns", method)
                normalised = ((), (), frozenset())
            if isinstance(normalised, _ParseFailure):
                return [], normalised
            if normalised is None:
                return [], _ParseFailure("dynamic_with_columns", method)
            with_outputs, with_items, with_identity = normalised
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.WITH_COLUMNS,
                    method=method,
                    output_to_inputs=with_outputs,
                    referenced_columns=frozenset(
                        column for _name, refs in with_outputs for column in refs
                    ),
                    selector_items=with_items,
                    identity_outputs=with_identity,
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
                    # A strict rename requires every named source at runtime,
                    # including identity pairs the schema transfer drops.
                    referenced_columns=frozenset(mapping),
                )
            )
        elif method in {"filter", "fill_null"}:
            if any(keyword.arg is None for keyword in call.keywords):
                return [], _ParseFailure(f"dynamic_{method}", method)
            collected: set[str] = set()
            predicate_items: list[SelectorItem] = []
            failed = False
            for argument in call.args:
                if method == "filter":
                    # A bare string predicate reads that boolean column, and
                    # an unseeable value could evaluate to one. ``fill_null``
                    # values are literals, so only ``filter`` needs either.
                    predicate = _literal_column_name(argument)
                    if predicate is not None:
                        collected.add(predicate)
                        continue
                    if _may_evaluate_to_python_string(argument):
                        failed = True
                        break
                    predicate_item = _selector_reference_item(
                        argument, selector_aliases, rows_only=cardinality_only
                    )
                    if isinstance(predicate_item, _ParseFailure):
                        return [], predicate_item
                    if predicate_item is not None:
                        predicate_items.append(predicate_item)
                        collected.update(predicate_item.references)
                        continue
                argument_references = _referenced_columns(argument, rows_only=cardinality_only)
                if argument_references is None:
                    failed = True
                    break
                collected.update(argument_references)
            if not failed:
                for keyword in call.keywords:
                    keyword_references = _referenced_columns(
                        keyword.value, rows_only=cardinality_only
                    )
                    if keyword_references is None:
                        failed = True
                        break
                    collected.update(keyword_references)
            if failed:
                return [], _ParseFailure(f"dynamic_{method}", method)
            if method == "filter":
                collected.update(kw.arg for kw in call.keywords if kw.arg)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.READ_COLUMNS,
                    method=method,
                    referenced_columns=frozenset(collected),
                    selector_items=tuple(predicate_items),
                )
            )
        elif method == "cast":
            parsed_cast = _parse_cast(call)
            if isinstance(parsed_cast, _ParseFailure):
                return [], parsed_cast
            operations.append(parsed_cast)
        elif method == "shift":
            parsed_shift = _parse_shift(call)
            if isinstance(parsed_shift, _ParseFailure):
                return [], parsed_shift
            operations.append(parsed_shift)
        elif method in _ROW_ONLY_METHODS:
            operations.append(LineageOperation(kind=LineageOperationKind.ROW_ONLY, method=method))
        elif cardinality_only and method in _CARDINALITY_ROW_ONLY_METHODS:
            # Row counts only: ``reverse`` permutes the frame and ``top_k`` /
            # ``bottom_k`` truncate it, so the current bound still holds. Column
            # lineage stays stricter because it would also have to demand the
            # ranking key, which these cardinality-only entries never resolve.
            operations.append(LineageOperation(kind=LineageOperationKind.ROW_ONLY, method=method))
        elif cardinality_only and method == "join_asof":
            parsed_asof = _parse_join_asof(call, input_names)
            if isinstance(parsed_asof, _ParseFailure):
                return [], parsed_asof
            operations.append(parsed_asof)
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
            sort_items: list[SelectorItem] = []
            for by_node in by_nodes:
                sort_item = _selector_column_item(by_node, selector_aliases)
                if sort_item is not None:
                    sort_items.append(sort_item)
                    continue
                parsed = _literal_columns(by_node)
                if parsed is None:
                    return [], _ParseFailure("dynamic_sort", method)
                sort_columns.update(parsed)
            if not sort_columns and not sort_items:
                return [], _ParseFailure("dynamic_sort", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.SORT,
                    method=method,
                    referenced_columns=frozenset(sort_columns),
                    selector_items=tuple(sort_items),
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
            unique_item = (
                None
                if subset_node is None
                else _selector_column_item(subset_node, selector_aliases)
            )
            subset: frozenset[str] | None
            if unique_item is not None:
                subset = frozenset()
            else:
                subset = None if subset_node is None else _literal_columns(subset_node)
                if subset_node is not None and subset is None:
                    return [], _ParseFailure("dynamic_unique", method)
            operations.append(
                LineageOperation(
                    kind=LineageOperationKind.UNIQUE,
                    method=method,
                    subset_columns=subset,
                    selector_items=() if unique_item is None else (unique_item,),
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
        elif method in {"drop", "drop_nulls", "with_row_index", "unpivot"}:
            if method == "drop":
                parsed_frame = _parse_drop(call, selector_aliases)
            elif method == "drop_nulls":
                parsed_frame = _parse_drop_nulls(call, selector_aliases)
            elif method == "with_row_index":
                parsed_frame = _parse_with_row_index(call)
            else:
                parsed_frame = _parse_unpivot(call, cardinality_only=cardinality_only)
            if isinstance(parsed_frame, _ParseFailure):
                return [], parsed_frame
            operations.append(parsed_frame)
        elif method == "join":
            parsed_join = _parse_join(call, input_names, cardinality_only=cardinality_only)
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
    cardinality_only: bool = False,
    selector_aliases: frozenset[str] = frozenset(),
    df_input: str | None = None,
    value_names: frozenset[str] = frozenset(),
) -> LinearFrameProgram | _ParseFailure:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _ParseFailure("syntax_error")

    # The input ``df`` holds before the code assigns it: the node's own binding
    # when it has one, otherwise the sole input. Historical code sometimes spells
    # a sole input as ``df``; runtime validation remains authoritative, and this
    # conservative alias keeps projection behavior compatible.
    implicit_df = df_input
    if implicit_df is None and len(input_names) == 1:
        implicit_df = next(iter(input_names))
    # Names the analyser can see. Anything else (a preamble name, an import, a
    # builtin) could hold a Polars expression reading columns the walk never
    # sees, so column lineage refuses a program that uses one. Row counts do
    # not depend on which columns such an expression reads.
    resolved = {"pl", "df", *input_names, *selector_aliases, *value_names}
    unresolved_name: str | None = None
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
            if _first_unresolved_name(statement.value, frozenset(resolved)) is None:
                resolved.add(target.id)
            else:
                resolved.discard(target.id)
            continue

        if not cardinality_only and unresolved_name is None:
            unresolved_name = _first_unresolved_name(statement.value, frozenset(resolved))
        if isinstance(statement.value, ast.Name):
            if started_operations:
                return _ParseFailure("unknown_frame_root")
            if statement.value.id in input_names:
                root_input = statement.value.id
            elif statement.value.id == "df" and root_input is None and implicit_df is not None:
                root_input = implicit_df
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
                if implicit_df is None:
                    return _ParseFailure("ambiguous_frame_root")
                root_input = implicit_df
        elif root in input_names:
            root_input = root
        else:
            return _ParseFailure("unknown_frame_root")
        calls = _frame_chain_calls(statement.value)
        parsed, failure = _parse_call_sequence(
            calls,
            input_names,
            cardinality_only=cardinality_only,
            selector_aliases=selector_aliases,
        )
        if failure is not None:
            return failure
        operations.extend(parsed)
        started_operations = True

    if root_input is None:
        return _ParseFailure("no_frame_root")
    if unresolved_name is not None:
        return _ParseFailure("unresolved_name")
    return LinearFrameProgram(root_input=root_input, operations=tuple(operations))


def _first_unresolved_name(node: ast.AST, resolved: frozenset[str]) -> str | None:
    """Return the first name in *node* outside *resolved*, or ``None``.

    A lambda's own parameters resolve inside its body; its defaults are
    evaluated where the lambda is written.
    """
    if isinstance(node, ast.Name):
        return None if node.id in resolved else node.id
    if isinstance(node, ast.Lambda):
        arguments = node.args
        for default in [*arguments.defaults, *arguments.kw_defaults]:
            if default is not None and (found := _first_unresolved_name(default, resolved)):
                return found
        parameters = {
            argument.arg
            for argument in [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *(extra for extra in (arguments.vararg, arguments.kwarg) if extra is not None),
            ]
        }
        return _first_unresolved_name(node.body, resolved | parameters)
    for child in ast.iter_child_nodes(node):
        if (found := _first_unresolved_name(child, resolved)) is not None:
            return found
    return None


def _rename_schema(
    schema: frozenset[str],
    mapping: Mapping[str, str],
) -> frozenset[str] | None:
    """Transfer a known schema through a rename, detecting target collisions.

    Source existence is owned by the forward transfer, which validates every
    rename source (identity pairs included) before calling this.
    """
    assert set(mapping) <= set(schema)
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


def _ensure_root_carrier(
    program: LinearFrameProgram,
    evaluated: Sequence[_EvaluatedOperation],
    input_schemas: Mapping[str, frozenset[str] | None],
    root_schema: frozenset[str],
    root_demand: set[str],
    input_dtypes: Mapping[str, Mapping[str, pl.DataType]] | None = None,
) -> bool:
    """Widen the root demand until the projected frame is never empty.

    The program is re-evaluated over the projected root schema (the demand) and
    compared step by step with the full evaluation. At the first step where the
    full frame still has columns but the projected frame has none, the first
    root column present in the full frame at that step, in sorted order, joins
    the demand; a root column that has already left the frame cannot carry
    rows there. Demanding an extra column never changes a result, so the loop
    only ever widens. Returns ``False`` when the projected program cannot be
    evaluated at all, which the caller reports as unsupported.
    """
    candidates = sorted(root_schema - root_demand)
    if not root_demand and candidates:
        root_demand.add(candidates.pop(0))
    while True:
        projected = _evaluate_program(
            program,
            {**input_schemas, program.root_input: frozenset(root_demand)},
            input_dtypes,
        )
        if isinstance(projected, _ParseFailure):
            return False
        empty_step = next(
            (
                index
                for index, (full, narrow) in enumerate(zip(evaluated, projected[0], strict=True))
                if full.after_schema and narrow.after_schema is not None and not narrow.after_schema
            ),
            None,
        )
        if empty_step is None or not candidates:
            return True
        full_after = evaluated[empty_step].after_schema or frozenset()
        present = [column for column in candidates if column in full_after]
        carrier = present[0] if present else candidates[0]
        candidates.remove(carrier)
        root_demand.add(carrier)


ROW_CARRIER_SCHEMA_UNKNOWN_REASON = "row_carrier_schema_unknown"
# Operations after which a carrier column of unknown name may be gone: any
# ``drop`` can name it (strictly or not, even after the code overwrote it), and
# the others replace every column with named outputs.
_CARRIER_REMOVING_KINDS = frozenset(
    {
        LineageOperationKind.DROP,
        LineageOperationKind.SELECT,
        LineageOperationKind.GROUP_BY_AGG,
        LineageOperationKind.UNPIVOT,
    }
)


def _step_emptying_an_unknown_root(
    program: LinearFrameProgram,
    evaluated: Sequence[_EvaluatedOperation],
    input_schemas: Mapping[str, frozenset[str] | None],
    root_demand: frozenset[str],
    input_dtypes: Mapping[str, Mapping[str, pl.DataType]] | None,
) -> tuple[str, str] | None:
    """Return the unsupported reason when a schema-less root can lose its rows.

    Without the root schema no carrier column can be chosen, so the program is
    re-evaluated over the demanded root columns alone. An empty demand is
    carried by the one column the edge or scan keeps for it, whose name is
    unknown, so that carrier only counts until the first operation that could
    remove it. At the first step where the narrowed frame can have no columns
    while the full frame is not provably empty, the narrowed run would lose
    every row.
    """
    projected = _evaluate_program(
        program,
        {**input_schemas, program.root_input: root_demand},
        input_dtypes,
    )
    if isinstance(projected, _ParseFailure):
        return "carrier_unresolvable", program.operations[-1].method
    carrier_kept = not root_demand
    for full, narrow in zip(evaluated, projected[0], strict=True):
        if narrow.operation.kind in _CARRIER_REMOVING_KINDS:
            carrier_kept = False
        if (
            not carrier_kept
            and narrow.after_schema is not None
            and not narrow.after_schema
            and full.after_schema != frozenset()
        ):
            return ROW_CARRIER_SCHEMA_UNKNOWN_REASON, full.operation.method
    return None


_DtypeMap = dict[str, "pl.DataType | None"]

# A named output (keyword or alias) over a multi-column selector is rejected
# with the operation's ordinary reason for an unprovable expression; clashing
# output names keep the operation's duplicate-output reason.
_DYNAMIC_OUTPUT_REASONS = {
    LineageOperationKind.SELECT: "dynamic_select",
    LineageOperationKind.WITH_COLUMNS: "dynamic_with_columns",
    LineageOperationKind.GROUP_BY_AGG: "dynamic_aggregate",
}
_DUPLICATE_OUTPUT_REASONS = {
    LineageOperationKind.SELECT: "dynamic_select",
    LineageOperationKind.WITH_COLUMNS: "dynamic_with_columns",
    LineageOperationKind.GROUP_BY_AGG: "ambiguous_aggregate_output",
}


def _resolve_selector_items(
    operation: LineageOperation,
    schema: frozenset[str] | None,
    dtypes: Mapping[str, pl.DataType | None] | None,
) -> LineageOperation | _ParseFailure:
    """Expand every selector item of *operation* against its exact input schema."""
    method = operation.method
    if schema is None:
        return _ParseFailure("selector_schema_unknown", method)
    outputs = list(operation.output_to_inputs)
    references = set(operation.referenced_columns)
    named_columns: set[str] = set()
    identity = set(operation.identity_outputs)
    for item in operation.selector_items:
        columns = schema - operation.group_keys if item.exclude_group_keys else schema
        expansions: list[tuple[str, ...]] = []
        for selector in item.selectors:
            if selector.positional:
                return _ParseFailure("selector_order_unknown", method)
            expanded = expand_literal_selector(selector, columns, dtypes)
            if expanded is None:
                return _ParseFailure(
                    "selector_dtypes_unknown"
                    if selector.dtype_dependent
                    else "selector_unexpandable",
                    method,
                )
            expansions.append(expanded)
        expanded_columns = {column for expansion in expansions for column in expansion}
        if item.role is SelectorItemRole.OUTPUT:
            expansion = expansions[0]
            if item.output_name is None:
                for column in expansion:
                    outputs.append(
                        (
                            f"{item.prefix}{column}{item.suffix}",
                            frozenset({column}) | item.references,
                        )
                    )
                    if item.pure:
                        identity.add(column)
            else:
                if len(expansion) != 1:
                    return _ParseFailure(_DYNAMIC_OUTPUT_REASONS[operation.kind], method)
                outputs.append((item.output_name, frozenset(expansion) | item.references))
            references |= expanded_columns | item.references
        elif item.role is SelectorItemRole.HELPER_OUTPUT:
            name = item.output_name
            if name is None:
                if len(expansions[0]) != 1:
                    return _ParseFailure("selector_order_unknown", method)
                name = expansions[0][0]
            outputs.append((name, frozenset(expanded_columns) | item.references))
            references |= expanded_columns | item.references
        elif item.role is SelectorItemRole.REFERENCES:
            references |= expanded_columns | item.references
        else:
            named_columns |= expanded_columns

    names = [name for name, _refs in outputs]
    if len(names) != len(set(names)) or (
        operation.kind is LineageOperationKind.GROUP_BY_AGG
        and len(set(names) - operation.group_keys) != len(names) - len(operation.group_keys)
    ):
        return _ParseFailure(
            _DUPLICATE_OUTPUT_REASONS.get(operation.kind, f"dynamic_{method}"), method
        )
    if operation.kind is LineageOperationKind.SORT:
        references |= named_columns
        if not references:
            return _ParseFailure("dynamic_sort", method)
        return replace(operation, referenced_columns=frozenset(references), selector_items=())
    if operation.kind is LineageOperationKind.DROP:
        assert operation.subset_columns is not None
        # A selector drops only columns that exist, so unlike a strict drop by
        # name it demands none of them.
        return replace(
            operation,
            subset_columns=operation.subset_columns | named_columns,
            selector_items=(),
        )
    if operation.kind in {LineageOperationKind.UNIQUE, LineageOperationKind.DROP_NULLS}:
        subset = (operation.subset_columns or frozenset()) | named_columns
        return replace(
            operation,
            subset_columns=subset,
            referenced_columns=(
                subset if operation.kind is LineageOperationKind.DROP_NULLS else frozenset()
            ),
            selector_items=(),
        )
    if operation.kind is LineageOperationKind.WITH_COLUMNS:
        references = {column for _name, refs in outputs for column in refs}
    return replace(
        operation,
        output_to_inputs=tuple(outputs),
        referenced_columns=frozenset(references),
        identity_outputs=frozenset(identity),
        selector_items=(),
    )


def _transfer_dtypes(
    operation: LineageOperation,
    before_schema: frozenset[str] | None,
    after_schema: frozenset[str] | None,
    dtypes: _DtypeMap | None,
    input_dtypes: Mapping[str, Mapping[str, pl.DataType]] | None,
) -> _DtypeMap | None:
    """Carry column dtypes through an operation; computed columns become unknown."""
    if dtypes is None or after_schema is None:
        return None
    kind = operation.kind
    if kind in {LineageOperationKind.SELECT, LineageOperationKind.WITH_COLUMNS}:
        assigned = {name for name, _refs in operation.output_to_inputs}
        return {
            column: (
                dtypes.get(column)
                if column in operation.identity_outputs or column not in assigned
                else None
            )
            for column in after_schema
        }
    if kind is LineageOperationKind.RENAME:
        mapping = dict(operation.renamed_columns)
        renamed = {mapping.get(column, column): dtype for column, dtype in dtypes.items()}
        return {column: renamed.get(column) for column in after_schema}
    if kind is LineageOperationKind.GROUP_BY_AGG:
        return {
            column: dtypes.get(column) if column in operation.group_keys else None
            for column in after_schema
        }
    if kind is LineageOperationKind.JOIN:
        right: Mapping[str, pl.DataType] = (input_dtypes or {}).get(
            operation.right_input or ""
        ) or {}
        suffix = operation.suffix or ""
        result: _DtypeMap = {}
        for column in after_schema:
            if before_schema is not None and column in before_schema:
                result[column] = dtypes.get(column)
            elif column in right:
                result[column] = right[column]
            elif suffix and column.endswith(suffix) and column[: -len(suffix)] in right:
                result[column] = right[column[: -len(suffix)]]
            else:
                result[column] = None
        return result
    if kind is LineageOperationKind.WITH_ROW_INDEX:
        return {
            column: pl.UInt32() if column in operation.produced_columns else dtypes.get(column)
            for column in after_schema
        }
    preserving = {
        LineageOperationKind.ROW_ONLY,
        LineageOperationKind.SORT,
        LineageOperationKind.UNIQUE,
        LineageOperationKind.DROP,
        LineageOperationKind.DROP_NULLS,
    }
    if kind in preserving or (
        kind is LineageOperationKind.READ_COLUMNS and operation.method == "filter"
    ):
        return {column: dtypes.get(column) for column in after_schema}
    return {column: None for column in after_schema}


def _evaluate_program(
    program: LinearFrameProgram,
    input_schemas: Mapping[str, frozenset[str] | None],
    input_dtypes: Mapping[str, Mapping[str, pl.DataType]] | None = None,
) -> tuple[tuple[_EvaluatedOperation, ...], frozenset[str] | None] | _ParseFailure:
    schema = input_schemas[program.root_input]
    root_dtypes = None if input_dtypes is None else input_dtypes.get(program.root_input)
    dtypes: _DtypeMap | None = (
        None
        if root_dtypes is None or schema is None
        else {column: root_dtypes.get(column) for column in schema}
    )
    evaluated: list[_EvaluatedOperation] = []
    for original in program.operations:
        operation = original
        if operation.selector_items:
            resolved = _resolve_selector_items(operation, schema, dtypes)
            if isinstance(resolved, _ParseFailure):
                return resolved
            operation = resolved
        before = schema
        if schema is not None:
            # Rename validates its own sources below so a missing source keeps
            # the precise ``invalid_rename`` reason.
            required_from_current: set[str] = (
                set()
                if operation.kind is LineageOperationKind.RENAME
                else set(operation.referenced_columns)
            )
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
                # Every source — including identity pairs the schema transfer
                # drops — must exist for the strict runtime rename.
                if set(operation.referenced_columns) - set(schema):
                    return _ParseFailure("invalid_rename", operation.method)
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
        elif operation.kind is LineageOperationKind.DROP:
            if schema is not None:
                # A strict drop's dropped columns are already validated above
                # through ``referenced_columns``.
                assert operation.subset_columns is not None
                schema = schema - operation.subset_columns
        elif operation.kind is LineageOperationKind.WITH_ROW_INDEX:
            if schema is None:
                # Polars raises DuplicateError when the index name already
                # exists. Without an exact schema, projecting that column away
                # could turn the failure into a success, so fail closed.
                return _ParseFailure("with_row_index_schema_unknown", operation.method)
            index_name = operation.produced_columns[0]
            if index_name in schema:
                return _ParseFailure("invalid_with_row_index", operation.method)
            schema = schema | {index_name}
        elif operation.kind is LineageOperationKind.UNPIVOT:
            assert operation.index_columns is not None
            produced = frozenset(operation.produced_columns)
            if schema is not None:
                resolved_on = (
                    operation.subset_columns
                    if operation.subset_columns is not None
                    else schema - operation.index_columns
                )
                if not resolved_on:
                    return _ParseFailure("invalid_unpivot", operation.method)
                schema = operation.index_columns | produced
            elif operation.subset_columns is not None:
                # Literal ``on``/``index`` name the whole output regardless of
                # the upstream schema.
                schema = operation.index_columns | produced
        # Read/row/sort/unique/explode/drop_nulls preserve the schema.
        dtypes = _transfer_dtypes(operation, before, schema, dtypes, input_dtypes)
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
    # pass through unchanged.  A strict rename still requires every source to
    # exist at runtime, so all mapping sources stay demanded exactly as in the
    # known-schema branch.
    return {unknown_reverse.get(column, column) for column in demand} | set(mapping)


def analyze_polars_lineage(
    code: str,
    inputs: Mapping[str, frozenset[str] | None],
    demanded_output: Iterable[str] | None = None,
    *,
    input_dtypes: Mapping[str, Mapping[str, pl.DataType]] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
    df_input: str | None = None,
    value_names: frozenset[str] = frozenset(),
) -> ColumnLineageAnalysis:
    """Prove exact output schema and per-input demand for linear Polars code.

    ``None`` input schemas are allowed for operations whose ownership does not
    depend on the complete schema.  A join requires exact schemas on both sides
    so collision/suffix ownership can be attributed mechanically.

    ``df_input`` names the input the code sees as ``df`` before assigning it,
    for nodes whose builder binds their first input frame to ``df``. ``df``
    is never a join operand, since after the first statement it is the live
    frame rather than that input.

    ``value_names`` are names the node binds to values that are never Polars
    expressions or frames (an External File's loaded ``obj``). Every other name
    the code uses must be an input, ``pl``, a selector alias, a lambda
    parameter, or a helper built only from those; otherwise the program is
    ``unresolved_name``.
    """
    if not isinstance(code, str) or not code.strip():
        return _unsupported("empty_code")
    if not inputs or any(not isinstance(name, str) or not name for name in inputs):
        return _unsupported("invalid_inputs")
    if df_input is not None and df_input not in inputs:
        return _unsupported("invalid_inputs")
    normalised_inputs = {
        name: None if columns is None else frozenset(columns) for name, columns in inputs.items()
    }
    program = _parse_program(
        code,
        frozenset(normalised_inputs),
        False,
        frozenset(selector_aliases),
        df_input,
        frozenset(value_names),
    )
    if isinstance(program, _ParseFailure):
        return _unsupported(program.reason, program.operation)
    evaluated_result = _evaluate_program(program, normalised_inputs, input_dtypes)
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
            # Identity pairs are dropped from the schema transfer but the
            # strict runtime rename still requires those sources to exist.
            demand = translated | set(operation.referenced_columns)
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
        elif operation.kind is LineageOperationKind.DROP:
            assert operation.subset_columns is not None
            demand = (demand - set(operation.subset_columns)) | set(operation.referenced_columns)
        elif operation.kind is LineageOperationKind.DROP_NULLS:
            if operation.subset_columns is None:
                if item.before_schema is None:
                    return _unsupported("drop_nulls_schema_unknown", operation.method)
                demand |= set(item.before_schema)
            else:
                demand |= set(operation.subset_columns)
        elif operation.kind is LineageOperationKind.WITH_ROW_INDEX:
            demand -= {operation.produced_columns[0]}
        elif operation.kind is LineageOperationKind.UNPIVOT:
            assert operation.index_columns is not None
            if operation.subset_columns is not None:
                # Polars evaluates every ``on`` column regardless of which
                # unpivoted values a later operation still needs.
                demand = set(operation.index_columns) | set(operation.subset_columns)
            elif item.before_schema is None:
                return _unsupported("unpivot_schema_unknown", operation.method)
            else:
                demand = set(item.before_schema)
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
            if routed is None:
                # The shared router refuses a suffixed name that is also a real
                # column when its base name exists on both sides, even as a key.
                return _unsupported("join_schema_ambiguous", operation.method)
            left_demand, right_demand = routed
            demand = left_demand
            demands_by_input[operation.right_input].update(right_demand)
        elif operation.kind is LineageOperationKind.ROW_ONLY:
            continue
        else:  # pragma: no cover - enum exhaustiveness guard
            return _unsupported("unsupported_operation", operation.method)

    demands_by_input[program.root_input].update(demand)
    # The root input's rows form the output, so the projected frame must carry
    # at least one column wherever the full frame does: a zero-column frame is
    # an empty frame, so a demand that names only generated columns (a row
    # index, a bare row count), or only columns the program later drops, would
    # otherwise lose every row in silence. Other inputs keep an exact empty
    # demand (an unused port has no rows to carry; a joined port always demands
    # its keys).
    root_demand = demands_by_input[program.root_input]
    root_schema = normalised_inputs.get(program.root_input)
    if root_schema is None:
        emptying = _step_emptying_an_unknown_root(
            program,
            evaluated,
            normalised_inputs,
            frozenset(root_demand),
            input_dtypes,
        )
        if emptying is not None:
            return _unsupported(*emptying)
    elif root_schema and not _ensure_root_carrier(
        program,
        evaluated,
        normalised_inputs,
        root_schema,
        root_demand,
        input_dtypes,
    ):
        return _unsupported("carrier_unresolvable", program.operations[-1].method)
    return ColumnLineageAnalysis(
        supported=True,
        exact_output_columns=exact_output,
        demands_by_input=MappingProxyType(
            {name: frozenset(columns) for name, columns in demands_by_input.items()}
        ),
        reason="lineage_proven",
        unsupported_operation=None,
    )


def analyze_polars_cardinality(
    code: str,
    inputs: Mapping[str, int],
    *,
    selector_aliases: frozenset[str] = frozenset(),
) -> RowCardinalityAnalysis:
    """Prove finite output and intermediate row-count bounds for Polars code.

    The proof deliberately reuses the closed AST program accepted by column
    lineage. Operations that can only preserve or reduce rows keep the current
    bound. Joins use their declared Polars uniqueness contract. Any operation
    with unbounded row expansion, or syntax outside the closed model, returns
    an unsupported result instead of inventing a multiplier.
    """

    if not isinstance(code, str) or not code.strip():
        return _unsupported_cardinality("empty_code")
    if not inputs or any(not isinstance(name, str) or not name for name in inputs):
        return _unsupported_cardinality("invalid_inputs")
    if any(
        not isinstance(bound, int) or isinstance(bound, bool) or bound < 0
        for bound in inputs.values()
    ):
        return _unsupported_cardinality("invalid_inputs")

    normalised_inputs = dict(inputs)
    program = _parse_program(code, frozenset(normalised_inputs), True, frozenset(selector_aliases))
    if isinstance(program, _ParseFailure):
        return _unsupported_cardinality(program.reason, program.operation)

    current = normalised_inputs[program.root_input]
    peak = current
    operand_peak = current
    has_cross_join = False
    depends_on_many_to_many_join = False
    # The root input is the initial left operand of the whole program.
    operand_references: dict[str, int] = {program.root_input: 1}
    evidence: list[str] = [
        f"cardinality_root_input={program.root_input}",
        f"cardinality_root_upper_bound={current}",
    ]
    for index, operation in enumerate(program.operations):
        if operation.kind is LineageOperationKind.EXPLODE:
            return _unsupported_cardinality("row_expansion_unbounded", operation.method)
        if operation.kind is LineageOperationKind.JOIN:
            assert operation.right_input is not None
            assert operation.how is not None
            if operation.how == "cross":
                has_cross_join = True
            # An absent ``validate=`` normalises to ``m:m`` at parse time.
            if operation.validate == "m:m":
                # Nothing bounds this join by an operand, so only the row
                # product bounds it.
                depends_on_many_to_many_join = True
            operand_references[operation.right_input] = (
                operand_references.get(operation.right_input, 0) + 1
            )
            # The frame this join consumes is whatever the chain holds now, not
            # the program's original root input.
            operand_peak = max(operand_peak, current, normalised_inputs[operation.right_input])
            bound = join_cardinality_upper_bound(
                current,
                normalised_inputs[operation.right_input],
                how=operation.how,
                validate=operation.validate,
            )
            current = bound.max_rows
            peak = max(peak, normalised_inputs[operation.right_input], current)
            evidence.extend(f"operation[{index}].{item}" for item in bound.evidence)
        elif operation.kind in {LineageOperationKind.SELECT, LineageOperationKind.WITH_COLUMNS}:
            operand_peak = max(operand_peak, current)
            # A scalar expression materialises one row even over an empty
            # frame. For non-empty inputs the accepted expression vocabulary
            # is bounded by the current height.
            current = max(current, 1)
            peak = max(peak, current)
            evidence.append(f"operation[{index}].scalar_empty_frame_upper_bound={current}")
        elif operation.kind is LineageOperationKind.UNPIVOT:
            operand_peak = max(operand_peak, current)
            # Cardinality parsing accepts ``unpivot`` only with a literal
            # non-empty ``on`` list, which is exactly the expansion factor.
            assert operation.subset_columns is not None
            factor = len(operation.subset_columns)
            current = current * factor
            peak = max(peak, current)
            evidence.append(f"operation[{index}].unpivot_factor={factor}")
        # Every other accepted operation — drop, drop_nulls, with_row_index
        # included — is row-preserving or row-reducing.

    evidence.extend(
        (
            f"cardinality_output_upper_bound={current}",
            f"cardinality_peak_upper_bound={peak}",
        )
    )
    return RowCardinalityAnalysis(
        supported=True,
        output_upper_bound=current,
        peak_upper_bound=peak,
        evidence=tuple(evidence),
        reason="cardinality_proven",
        unsupported_operation=None,
        operand_peak_rows=operand_peak,
        has_cross_join=has_cross_join,
        depends_on_many_to_many_join=depends_on_many_to_many_join,
        operand_reference_counts=MappingProxyType(dict(operand_references)),
    )


@dataclass(frozen=True, slots=True)
class CarriedColumnProof:
    """What a program proves about the input columns it carries unchanged.

    ``root_input`` is the input whose columns keep their names and values when
    another input's columns collide. ``assigned`` are output columns the program
    may rewrite, ``join_keys`` maps each joined input to the literal same-name
    ``on`` keys of the join that brought it in (an output key column holds that
    input's matching value), and ``carried_only`` (when set) is the only set of
    columns carried at all — a grouping carries just its keys.
    """

    root_input: str
    assigned: frozenset[str]
    join_keys: Mapping[str, frozenset[str]]
    carried_only: frozenset[str] | None = None


# Frame methods that never change a surviving row's values.
_VALUE_PRESERVING_FRAME_METHODS = frozenset(
    {"filter", "sort", "head", "tail", "limit", "slice", "unique", "drop", "drop_nulls", "lazy"}
)
_ASSIGNING_FRAME_METHODS = frozenset({"with_columns", "with_columns_seq"})
_ROW_PRESERVING_JOINS = frozenset({"inner", "left", "semi", "anti"})
_ALLOWED_JOIN_OPTIONS = frozenset(
    {"on", "left_on", "right_on", "how", "suffix", "validate", "coalesce", "maintain_order"}
)


def _is_polars_call(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pl"
    )


# Expression methods and namespaces whose output name is not the receiver's:
# ``pipe`` may alias, ``.name`` renames, and ``.struct`` emits field columns.
_NAME_CHANGING_EXPRESSION_METHODS = frozenset({"pipe"})
_NAME_CHANGING_NAMESPACES = frozenset({"name", "struct"})


def _plain_column_name(name: str) -> bool:
    """Whether a column string names one column rather than a regex or wildcard."""
    return name != "*" and not (name.startswith("^") and name.endswith("$"))


def _expression_output(node: ast.AST) -> tuple[str, bool] | None:
    """Return an expression's output column name and whether it is a bare column.

    ``None`` means the output name cannot be read from the syntax (selectors,
    regex or wildcard columns, ``.name`` or ``.struct`` rewrites, ``pipe``,
    ``when``/``then`` without ``alias``, variables).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value, True) if _plain_column_name(node.value) else None
    if isinstance(node, ast.BinOp):
        left = _expression_output(node.left)
        return None if left is None else (left[0], False)
    if isinstance(node, ast.UnaryOp):
        operand = _expression_output(node.operand)
        return None if operand is None else (operand[0], False)
    if not isinstance(node, ast.Call):
        return None
    if _is_polars_call(node, "col"):
        if len(node.args) == 1 and not node.keywords:
            argument = node.args[0]
            if (
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and _plain_column_name(argument.value)
            ):
                return argument.value, True
        return None
    if _is_polars_call(node, "lit"):
        return "literal", False
    if not isinstance(node.func, ast.Attribute):
        return None
    receiver = node.func.value
    if node.func.attr == "alias":
        if len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            name = node.args[0].value
            return (name, False) if isinstance(name, str) else None
        return None
    if node.func.attr in _NAME_CHANGING_EXPRESSION_METHODS:
        return None
    if isinstance(receiver, ast.Attribute) and receiver.attr in _NAME_CHANGING_NAMESPACES:
        return None
    if isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.AST):
        # Namespace methods (``.str.to_uppercase()``) keep the root name.
        inner = _expression_output(receiver.value)
        return None if inner is None else (inner[0], False)
    inner = _expression_output(receiver)
    return None if inner is None else (inner[0], False)


def _selects_unchanged_columns(node: ast.AST) -> bool:
    """Whether an expression argument only selects columns, keeping their values.

    ``pl.all()``, ``pl.exclude(<names>)``, and a literal list of names select
    columns without naming each output; none of them rewrites a value.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        return _literal_names(node) is not None
    if _is_polars_call(node, "all"):
        assert isinstance(node, ast.Call)
        return not node.args and not node.keywords
    if _is_polars_call(node, "exclude"):
        assert isinstance(node, ast.Call)
        return (
            bool(node.args)
            and not node.keywords
            and all(_literal_names(argument) is not None for argument in node.args)
        )
    return False


def _literal_names(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        names = [element.value for element in node.elts if isinstance(element, ast.Constant)]
        if len(names) == len(node.elts) and all(isinstance(name, str) for name in names):
            return [str(name) for name in names]
    return None


def _literal_frame_columns(node: ast.AST) -> list[str] | None:
    """Return the columns of an inline ``pl.DataFrame({...})`` / ``pl.LazyFrame({...})``."""
    while (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "lazy"
        and not node.args
        and not node.keywords
    ):
        node = node.func.value
    if not (_is_polars_call(node, "DataFrame") or _is_polars_call(node, "LazyFrame")):
        return None
    assert isinstance(node, ast.Call)
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Dict):
        return None
    columns = [key.value for key in node.args[0].keys if isinstance(key, ast.Constant)]
    if len(columns) != len(node.args[0].keys) or not all(isinstance(c, str) for c in columns):
        return None
    return [str(column) for column in columns]


def _selector_assignment(
    argument: ast.AST,
    aliases: frozenset[str],
    *,
    superset: set[str] | None,
    root_dtypes: Mapping[str, pl.DataType] | None,
) -> set[str] | None:
    """Return the columns a computation rooted at a literal selector may write.

    ``superset`` holds every column the frame can have at this point, so a
    name-based selector expanded over it names every column the expression can
    rewrite. A dtype-dependent selector needs ``root_dtypes`` (the frame is still
    the unchanged root input); a positional one is never resolved.
    """
    naming = _selector_naming(argument, aliases)
    if naming is None or len(_selector_nodes(naming.inner, aliases)) != 1:
        return None
    if naming.alias is not None:
        return {naming.alias}
    prefix, suffix = naming.prefix, naming.suffix
    if naming.inner is naming.root and not (prefix or suffix):
        return set()
    selector = naming.selector
    if selector.positional or superset is None:
        return None
    if selector.dtype_dependent:
        if root_dtypes is None:
            return None
        expanded = expand_literal_selector(selector, list(root_dtypes), root_dtypes)
    else:
        expanded = expand_literal_selector(selector, superset, None)
    if expanded is None:
        return None
    return {f"{prefix}{column}{suffix}" for column in expanded}


def carried_column_proof(
    code: str,
    input_names: Iterable[str],
    *,
    input_columns: Mapping[str, Iterable[str]] | None = None,
    input_dtypes: Mapping[str, Mapping[str, pl.DataType]] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
    input_aliases: Mapping[str, str] | None = None,
) -> CarriedColumnProof | None:
    """Prove which input columns a Polars program carries through unchanged.

    The proof is syntactic and closed: every frame method must be one whose
    effect on values is known, and every column an expression writes must be
    named in the syntax. Anything else returns ``None`` — a trace then reports
    the step as unproven instead of matching on a value the code may have
    rewritten. A literal selector that only selects writes nothing; a
    computation rooted at one writes its expansion over the program's proven
    column superset (``input_columns`` plus every column the program assigned or
    a join suffixed), which ``input_columns`` must be supplied to resolve.
    ``input_aliases`` maps a logical name the code uses (an ``inputMapping``
    entry) to the input name it stands for; the proof reports input names.
    """
    aliases = dict(input_aliases or {})
    input_set = frozenset(input_names)
    names = input_set | frozenset(aliases)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    root_input: str | None = None
    assigned: set[str] = set()
    join_keys: dict[str, frozenset[str]] = {}
    carried_only: set[str] | None = None
    superset: set[str] | None = (
        None
        if input_columns is None
        else {column for columns in input_columns.values() for column in columns}
    )
    frame_changed = False
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
            or statement.targets[0].id != "df"
        ):
            return None
        root = _chain_root_name(statement.value)
        if root is None:
            return None
        if root == "df" and root_input is None:
            if len(input_set) != 1:
                return None
            root = next(iter(input_set))
        if root != "df":
            if root not in names or root_input is not None:
                return None
            root_input = aliases.get(root, root)
        calls = _frame_chain_calls(statement.value) if isinstance(statement.value, ast.Call) else []
        index = 0
        while index < len(calls):
            call = calls[index]
            assert isinstance(call.func, ast.Attribute)
            method = call.func.attr
            if method in _VALUE_PRESERVING_FRAME_METHODS:
                pass
            elif method in _ASSIGNING_FRAME_METHODS or method == "select":
                outputs: list[tuple[str, bool]] = []
                for argument in call.args:
                    if _selects_unchanged_columns(argument):
                        continue
                    if _selector_nodes(argument, selector_aliases):
                        root_name = root_input or (
                            next(iter(input_set)) if len(input_set) == 1 else None
                        )
                        root_dtypes = (
                            None
                            if frame_changed or input_dtypes is None or root_name is None
                            else input_dtypes.get(root_name)
                        )
                        written = _selector_assignment(
                            argument,
                            selector_aliases,
                            superset=None if superset is None else superset | assigned,
                            root_dtypes=root_dtypes,
                        )
                        if written is None:
                            return None
                        outputs.extend((name, False) for name in written)
                        continue
                    output = _expression_output(argument)
                    if output is None:
                        return None
                    outputs.append(output)
                outputs.extend((keyword.arg, False) for keyword in call.keywords if keyword.arg)
                if any(keyword.arg is None for keyword in call.keywords):
                    return None
                written_names = {name for name, bare in outputs if not bare}
                assigned.update(written_names)
                frame_changed = frame_changed or bool(written_names)
            elif method == "with_row_index":
                index_name = call.args[0] if call.args else None
                for keyword in call.keywords:
                    if keyword.arg == "name":
                        index_name = keyword.value
                if index_name is None:
                    assigned.add("index")
                elif isinstance(index_name, ast.Constant) and isinstance(index_name.value, str):
                    assigned.add(index_name.value)
                else:
                    return None
                frame_changed = True
            elif method == "rename":
                if len(call.args) != 1 or not isinstance(call.args[0], ast.Dict):
                    return None
                for key, value in zip(call.args[0].keys, call.args[0].values, strict=True):
                    if not (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        return None
                    assigned.update((key.value, value.value))
                frame_changed = True
            elif method in {"join", "cross_join"}:
                if not call.args:
                    return None
                right = call.args[0]
                right_input: str | None = None
                right_columns: list[str] | None
                if isinstance(right, ast.Name):
                    # A non-root input joined twice has two rows behind one output
                    # row; its carried values would mix them.
                    if right.id not in names or aliases.get(right.id, right.id) in join_keys:
                        return None
                    right_input = aliases.get(right.id, right.id)
                    join_keys[right_input] = frozenset()
                    right_columns = (
                        None
                        if input_columns is None or right_input not in input_columns
                        else list(input_columns[right_input])
                    )
                else:
                    literal_columns = _literal_frame_columns(right)
                    if literal_columns is None:
                        return None
                    assigned.update(literal_columns)
                    right_columns = list(literal_columns)
                options = {keyword.arg: keyword.value for keyword in call.keywords}
                if None in options or not set(options) <= _ALLOWED_JOIN_OPTIONS:
                    return None
                frame_changed = True
                # Colliding right-side columns arrive suffixed; the superset keeps
                # every name a later selector could match.
                suffix_node = options.get("suffix")
                join_suffix = "_right" if suffix_node is None else _literal_string(suffix_node)
                if superset is not None:
                    if join_suffix is None or right_columns is None:
                        superset = None
                    else:
                        superset |= set(right_columns)
                        superset |= {f"{column}{join_suffix}" for column in right_columns}
                how_node = options.get("how")
                how = "cross" if method == "cross_join" else "inner"
                if how_node is not None:
                    if not isinstance(how_node, ast.Constant) or not isinstance(
                        how_node.value, str
                    ):
                        return None
                    how = how_node.value
                if how not in _ROW_PRESERVING_JOINS and how != "cross":
                    return None
                on = options.get("on", call.args[1] if len(call.args) > 1 else None)
                if on is not None:
                    on_names = _literal_names(on)
                    if on_names is None:
                        return None
                    if right_input is not None:
                        join_keys[right_input] = frozenset(on_names)
            elif method == "group_by":
                if index + 1 >= len(calls) or calls[index + 1].func.attr != "agg":  # type: ignore[attr-defined]
                    return None
                group_keys: list[str] = []
                for argument in call.args:
                    literal = _literal_names(argument)
                    if literal is None:
                        return None
                    group_keys.extend(literal)
                if call.keywords or not group_keys:
                    return None
                carried_only = (
                    set(group_keys) if carried_only is None else carried_only & set(group_keys)
                )
                frame_changed = True
                index += 1
            else:
                return None
            index += 1
    if root_input is None:
        if len(input_set) != 1:
            return None
        root_input = next(iter(input_set))
    return CarriedColumnProof(
        root_input,
        frozenset(assigned),
        MappingProxyType(dict(join_keys)),
        None if carried_only is None else frozenset(carried_only),
    )


__all__ = [
    "CarriedColumnProof",
    "ColumnLineageAnalysis",
    "LinearFrameProgram",
    "LineageOperation",
    "LineageOperationKind",
    "RowCardinalityAnalysis",
    "analyze_polars_cardinality",
    "analyze_polars_lineage",
    "carried_column_proof",
]
