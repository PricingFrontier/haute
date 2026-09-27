"""Chunk-local classification of user code and expressions.

Decides whether a node's Polars code or an expression is provably row-local,
so running it on row slices of a frame gives the same rows as running it on
the whole frame. The engine's bounded writes and trace correlation rely on it.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from haute._polars_call_shapes import (
    is_literal_collection,
    is_literal_scalar,
    is_pl_dtype_reference,
    replace_call_has_literal_mapping,
    replace_strict_call_has_literal_mapping,
)
from haute._polars_operations import (
    EXPRESSION_NAMESPACE_NAMES,
    POLARS_OPERATIONS,
    OperationClass,
    OperationReceiver,
    chunk_admitted_names,
)
from haute._polars_selectors import literal_selector

__all__ = [
    "ChunkLocalDecision",
    "classify_chunk_local_polars_code",
    "classify_row_local_expression",
    "is_chunk_local_polars_code",
]


# ---------------------------------------------------------------------------
# Chunk-local user-code whitelist (PATH_TO_HIGHEST_STANDARD §A3).
#
# Every admitted construct below cites the chunked==full proof that keeps it
# admitted: a hypothesis property test in tests/test_chunk_whitelist_proofs.py
# (``test_whitelisted_construct_chunked_equals_full[<proof id>]``) that runs
# the construct through the REAL chunk runner against full lazy execution on
# randomized, boundary-heavy frames.  A construct without a passing proof is
# not whitelisted; rejected code fails chunk planning loudly and callers route
# to the existing full (non-chunked) executor, which is always correct.
# ---------------------------------------------------------------------------
_ROW_LOCAL_DF_METHOD_NAMES = chunk_admitted_names(OperationReceiver.FRAME)
_ROW_LOCAL_EXPR_METHOD_NAMES = chunk_admitted_names(OperationReceiver.EXPR)
_ROW_LOCAL_POLARS_FUNCTIONS = chunk_admitted_names(OperationReceiver.POLARS_FUNCTION)
# Attribute namespaces that polars exposes on an expression.  Recognising all of
# them (not just the admitted ones) keeps ``expr.<ns>.<method>()`` classified as
# ``unsupported_namespace_method`` instead of falling into the generic
# "unsupported expression" bucket.
_ROW_LOCAL_NAMESPACE_NAMES = EXPRESSION_NAMESPACE_NAMES
# Per-namespace admitted methods.  Each entry cites a proof case in
# tests/test_chunk_whitelist_proofs.py (tag ``("expr.<ns>", "<method>")``) in
# its registry ``note``.
_ROW_LOCAL_NAMESPACE_METHOD_NAMES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        namespace: chunk_admitted_names(OperationReceiver.NAMESPACE, namespace)
        for namespace in ("str", "dt")
    }
)


@dataclass(frozen=True, slots=True)
class _RowLocalAdmission:
    """The operation names one classification admits, by receiver."""

    frame_methods: frozenset[str]
    expr_methods: frozenset[str]
    polars_functions: frozenset[str]
    namespace_methods: Mapping[str, frozenset[str]]


def _row_semantic_names(
    receiver: OperationReceiver, namespace: str | None = None
) -> frozenset[str]:
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.operation_class is OperationClass.ROW_LOCAL
        and entry.receiver is receiver
        and entry.namespace == namespace
    )


# Chunked execution admits only constructs with a chunked==full proof.
_CHUNK_PROVEN_ADMISSION = _RowLocalAdmission(
    frame_methods=_ROW_LOCAL_DF_METHOD_NAMES,
    expr_methods=_ROW_LOCAL_EXPR_METHOD_NAMES,
    polars_functions=_ROW_LOCAL_POLARS_FUNCTIONS,
    namespace_methods=_ROW_LOCAL_NAMESPACE_METHOD_NAMES,
)
# One row determines an expression's value when every operation in it is
# registered row-local, proven for chunking or not. ``when`` chained on a
# conditional (``pl.when(a).then(x).when(b)``) opens another arm, as ``pl.when``.
_ROW_SEMANTICS_ADMISSION = _RowLocalAdmission(
    frame_methods=_row_semantic_names(OperationReceiver.FRAME),
    expr_methods=_row_semantic_names(OperationReceiver.EXPR) | {"when"},
    polars_functions=_row_semantic_names(OperationReceiver.POLARS_FUNCTION),
    namespace_methods=MappingProxyType(
        {
            namespace: _row_semantic_names(OperationReceiver.NAMESPACE, namespace)
            for namespace in sorted(
                {
                    entry.namespace
                    for entry in POLARS_OPERATIONS.values()
                    if entry.namespace is not None
                }
            )
        }
    ),
)


def _fill_null_call_is_chunk_local(call: ast.Call) -> bool:
    """Admit only the literal-value form: ``fill_null(<value>)`` / ``fill_null(value=...)``.

    Strategy fills (``forward``/``backward``/``min``/``max``/``mean``/...) read
    across rows, so a chunk boundary changes the result; ``limit`` and
    positional strategies ride along with them.  De-whitelist pin:
    ``test_fill_null_strategy_is_de_whitelisted_and_full_path_is_correct``.
    """
    if call.keywords:
        return not call.args and len(call.keywords) == 1 and call.keywords[0].arg == "value"
    return len(call.args) == 1


_CATEGORICAL_CAST_DTYPE_NAMES = frozenset({"Categorical", "Enum"})


def _cast_call_is_chunk_local(call: ast.Call) -> bool:
    """Reject ``cast`` to ``pl.Categorical``/``pl.Enum`` (including nested).

    A Categorical/Enum physical encoding depends on the ambient global string
    cache: a value whose first appearance lands in a later chunk can be assigned
    a different physical code than under full execution, so a downstream sort or
    join on the column silently diverges.  Rejecting the cast fails chunk
    planning loudly and routes to the always-correct full executor.
    De-whitelist pin: ``test_chunk_unsafe_constructs_are_not_whitelisted``
    (``cast-to-categorical*`` cases).
    """
    for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
        for sub in ast.walk(argument):
            if isinstance(sub, ast.Attribute) and sub.attr in _CATEGORICAL_CAST_DTYPE_NAMES:
                return False
    return True


def _is_in_call_is_chunk_local(call: ast.Call) -> bool:
    """Admit only membership against a literal collection: ``is_in([<literals>])``.

    ``is_in(pl.col(...))`` / ``is_in(frame[...])`` use the FULL column as the
    haystack; inside a chunk the haystack silently shrinks to the chunk's rows.
    De-whitelist pin:
    ``test_is_in_column_haystack_is_de_whitelisted_and_full_path_is_correct``.
    """
    if call.keywords or len(call.args) != 1:
        return False
    collection = call.args[0]
    if not isinstance(collection, ast.List | ast.Tuple | ast.Set):
        return False
    return all(is_literal_scalar(element) for element in collection.elts)


def _namespace_call_args_are_literal(call: ast.Call) -> bool:
    """Namespace methods accept only constants, literal collections, or ``pl`` dtypes.

    Anything else (an expression, a frame, or a bare name) could smuggle a
    column reference into the argument, which is not provably chunk-local.
    """
    for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
        if is_literal_scalar(argument) or is_literal_collection(argument):
            continue
        if is_pl_dtype_reference(argument):
            continue
        return False
    return True


# ``str`` parsing methods whose format polars INFERS from the data when it is
# omitted.  Inference is per-frame, so two chunks can infer two different
# formats: with ``strict=False`` a chunk parses values the full frame nulls, and
# with ``strict=True`` chunked execution can succeed where full execution raises.
# The value is the positional index the ``format`` argument occupies.
_TEMPORAL_PARSE_FORMAT_POSITIONS: Mapping[str, int] = MappingProxyType(
    {
        "to_date": 0,
        "to_datetime": 0,
        "to_time": 0,
        "strptime": 1,
    }
)


def _temporal_parse_call_has_literal_format(call: ast.Call, *, position: int) -> bool:
    """Require an explicit, non-empty literal string ``format`` for data-parsed temporals."""
    if len(call.args) > position:
        candidate: ast.expr | None = call.args[position]
    else:
        candidate = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "format"),
            None,
        )
    return (
        isinstance(candidate, ast.Constant)
        and isinstance(candidate.value, str)
        and bool(candidate.value)
    )


# The closed set of materially distinct call SHAPES each shape-validated method
# admits.  ``tests/test_chunk_whitelist_proofs.py`` requires one chunked==full
# proof per declared shape, so a validator cannot widen what it admits without
# a proof for the newly admitted shape.  Keys are ``"<proof kind>.<method>"``.
_ADMITTED_CALL_SHAPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "df.cast": frozenset({"non_categorical"}),
        "expr.cast": frozenset({"non_categorical"}),
        "df.fill_null": frozenset({"value"}),
        "expr.fill_null": frozenset({"value"}),
        "expr.is_in": frozenset({"literal_collection"}),
        "expr.replace": frozenset({"mapping", "old_new"}),
        **{
            f"expr.str.{method}": frozenset({"explicit_format_lenient", "explicit_format_strict"})
            for method in _TEMPORAL_PARSE_FORMAT_POSITIONS
        },
    }
)


# Methods whose bare name is not enough to prove chunk-locality: the call
# SHAPE must also be constrained before the generic argument walk runs.
_CHUNK_LOCAL_CALL_SHAPE_VALIDATORS: Mapping[str, Callable[[ast.Call], bool]] = MappingProxyType(
    {
        "cast": _cast_call_is_chunk_local,
        "fill_null": _fill_null_call_is_chunk_local,
        "is_in": _is_in_call_is_chunk_local,
        "replace": replace_call_has_literal_mapping,
        "replace_strict": replace_strict_call_has_literal_mapping,
    }
)


@dataclass(slots=True)
class _ChunkLocalTrace:
    """Records the FIRST classifier failure in source order and never overwrites it."""

    reason: str | None = None
    blocking_operator: str | None = None
    line: int | None = None
    column: int | None = None
    admission: _RowLocalAdmission = _CHUNK_PROVEN_ADMISSION

    def record(self, reason: str, blocking_operator: str | None, node: ast.AST) -> None:
        if self.reason is not None:
            return
        self.reason = reason
        self.blocking_operator = blocking_operator
        line = getattr(node, "lineno", None)
        column = getattr(node, "col_offset", None)
        self.line = line
        self.column = None if column is None else column + 1


def _embedded_frame_name(
    value: ast.expr,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
) -> str | None:
    for sub in ast.walk(value):
        if isinstance(sub, ast.Name) and (sub.id in allowed_frames or sub.id in local_frames):
            return sub.id
    return None


def _source_ordered(values: Iterable[ast.expr | None]) -> tuple[ast.expr, ...]:
    """Order sibling child expressions by source position, not by AST field order.

    ``ast`` field order is not source order: an ``IfExp`` stores ``test`` before
    the textually earlier ``body``, and a ``Dict`` stores every key before any
    value.  Walking in field order would make the trace report a later blocker
    as "the first blocking construct in source order".  Nodes without positions
    keep their field order.
    """
    present = [value for value in values if value is not None]
    if not all(
        getattr(value, "lineno", None) is not None
        and getattr(value, "col_offset", None) is not None
        for value in present
    ):
        return tuple(present)
    return tuple(sorted(present, key=lambda value: (value.lineno, value.col_offset)))


def _row_local_subexprs_are_supported(
    values: Iterable[ast.expr | None],
    *,
    allowed_frames: set[str],
    local_frames: set[str],
    selector_aliases: frozenset[str],
    trace: _ChunkLocalTrace,
) -> bool:
    """Return whether every sub-expression is row-local and frame-free.

    Frame references are only chunk-safe as method-chain receivers.  Embedded
    anywhere else (call argument, subscript, operand, collection element) they
    read the FULL frame under full execution but only the chunk under chunked
    execution, so they are rejected here.
    """
    for value in _source_ordered(values):
        supported, derived_from_frame = _row_local_expr_is_supported(
            value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        )
        if not supported:
            return False
        if derived_from_frame:
            trace.record(
                "frame_embedded_in_expression",
                _embedded_frame_name(
                    value, allowed_frames=allowed_frames, local_frames=local_frames
                ),
                value,
            )
            return False
    return True


@dataclass(frozen=True, slots=True)
class ChunkLocalDecision:
    """Structured chunk-eligibility decision for one block of user code.

    ``reason`` is a closed vocabulary (see the execution-engine specification);
    ``blocking_operator`` names the method, function, frame, or AST node that
    stopped the walk, and ``line``/``column`` are its 1-based source location.
    """

    eligible: bool
    reason: str
    blocking_operator: str | None = None
    line: int | None = None
    column: int | None = None


def classify_chunk_local_polars_code(
    code: object,
    *,
    frame_names: Iterable[str] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
) -> ChunkLocalDecision:
    """Classify user Polars code for independent per-chunk application.

    The closed AST allowlists are the sole authority: there is no textual
    prefilter, so a construct is admitted only if the receiver-aware walk
    recognises it, and every rejection carries a closed reason, the blocking
    operator, and a 1-based source location.
    """
    return _classify_row_local(
        code,
        frame_names=frame_names,
        selector_aliases=selector_aliases,
        admission=_CHUNK_PROVEN_ADMISSION,
    )


def classify_row_local_expression(
    source: str,
    *,
    selector_aliases: frozenset[str] = frozenset(),
) -> ChunkLocalDecision:
    """Classify whether one row determines a Polars expression's value.

    The walk is the chunk classifier's, with the same argument guards, but it
    admits every operation the operation registry classes as row-local rather
    than only those proven for chunked execution. An expression holding a
    window, aggregation, shift, rank, cumulative, or unregistered operation is
    rejected with that operator named.
    """
    return _classify_row_local(
        f"df = df.with_columns(__haute_expression__=({source}))",
        frame_names=("df",),
        selector_aliases=selector_aliases,
        admission=_ROW_SEMANTICS_ADMISSION,
    )


def _classify_row_local(
    code: object,
    *,
    frame_names: Iterable[str] | None,
    selector_aliases: frozenset[str],
    admission: _RowLocalAdmission,
) -> ChunkLocalDecision:
    if not isinstance(code, str) or not code.strip():
        return ChunkLocalDecision(eligible=True, reason="empty_code")
    allowed_frames = {name for name in (frame_names or ()) if name}
    if not allowed_frames:
        return ChunkLocalDecision(eligible=False, reason="no_frame_names")
    try:
        module = ast.parse(code)
    except SyntaxError as exc:
        return ChunkLocalDecision(
            eligible=False,
            reason="syntax_error",
            line=exc.lineno,
            column=exc.offset,
        )
    trace = _ChunkLocalTrace(admission=admission)
    local_frames: set[str] = set()
    # A ``polars.selectors`` alias the code rebinds no longer names the module.
    selector_aliases = frozenset(selector_aliases) - {
        node.id
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    for stmt in module.body:
        if not _row_local_stmt_is_supported(
            stmt,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        ):
            return ChunkLocalDecision(
                eligible=False,
                reason=trace.reason or "unsupported_expression",
                blocking_operator=trace.blocking_operator,
                line=trace.line,
                column=trace.column,
            )
    return ChunkLocalDecision(eligible=True, reason="eligible")


def is_chunk_local_polars_code(
    code: object,
    *,
    frame_names: Iterable[str] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
) -> bool:
    """Return whether user Polars code is safe to apply independently per chunk."""

    return classify_chunk_local_polars_code(
        code, frame_names=frame_names, selector_aliases=selector_aliases
    ).eligible


def _row_local_call_is_supported(
    call: ast.Call,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
    selector_aliases: frozenset[str],
    trace: _ChunkLocalTrace,
) -> tuple[bool, bool]:
    func = call.func
    if not isinstance(func, ast.Attribute):
        trace.record("unsupported_expression", type(call).__name__, call)
        return False, False
    if literal_selector(call, aliases=selector_aliases) is not None:
        # A literal selector picks the same columns in every chunk: each chunk
        # shares the frame's schema.
        return True, False
    method_name = func.attr
    if isinstance(func.value, ast.Name) and func.value.id == "pl":
        if method_name not in trace.admission.polars_functions:
            trace.record("unsupported_polars_function", f"pl.{method_name}", func)
            return False, False
        args_supported = _row_local_subexprs_are_supported(
            (*call.args, *(keyword.value for keyword in call.keywords)),
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        )
        return args_supported, False
    if isinstance(func.value, ast.Attribute) and func.value.attr in _ROW_LOCAL_NAMESPACE_NAMES:
        return _row_local_namespace_call_is_supported(
            call,
            namespace=func.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        )
    receiver_supported, receiver_derived = _row_local_expr_is_supported(
        func.value,
        allowed_frames=allowed_frames,
        local_frames=local_frames,
        trace=trace,
        selector_aliases=selector_aliases,
    )
    if not receiver_supported:
        return False, False
    if receiver_derived:
        if method_name not in trace.admission.frame_methods:
            trace.record("unsupported_frame_method", method_name, func)
            return False, False
    elif method_name not in trace.admission.expr_methods:
        trace.record("unsupported_expression_method", method_name, func)
        return False, False
    shape_validator = _CHUNK_LOCAL_CALL_SHAPE_VALIDATORS.get(method_name)
    if shape_validator is not None and not shape_validator(call):
        trace.record("unsupported_call_shape", method_name, call)
        return False, False
    args_supported = _row_local_subexprs_are_supported(
        (*call.args, *(keyword.value for keyword in call.keywords)),
        allowed_frames=allowed_frames,
        local_frames=local_frames,
        trace=trace,
        selector_aliases=selector_aliases,
    )
    return args_supported, receiver_derived


def _row_local_namespace_call_is_supported(
    call: ast.Call,
    *,
    namespace: ast.Attribute,
    allowed_frames: set[str],
    local_frames: set[str],
    selector_aliases: frozenset[str],
    trace: _ChunkLocalTrace,
) -> tuple[bool, bool]:
    """Classify ``<expr>.<namespace>.<method>(...)`` against the namespace allowlist."""
    method_name = call.func.attr if isinstance(call.func, ast.Attribute) else ""
    qualified = f"{namespace.attr}.{method_name}"
    receiver_supported, receiver_derived = _row_local_expr_is_supported(
        namespace.value,
        allowed_frames=allowed_frames,
        local_frames=local_frames,
        trace=trace,
        selector_aliases=selector_aliases,
    )
    if not receiver_supported:
        return False, False
    if receiver_derived:
        # A frame exposes no expression namespaces: treat it as unadmitted.
        trace.record("unsupported_namespace_method", qualified, namespace)
        return False, False
    if method_name not in trace.admission.namespace_methods.get(namespace.attr, frozenset()):
        trace.record("unsupported_namespace_method", qualified, namespace)
        return False, False
    if not _namespace_call_args_are_literal(call):
        trace.record("unsupported_call_shape", method_name, call)
        return False, False
    format_position = (
        _TEMPORAL_PARSE_FORMAT_POSITIONS.get(method_name) if namespace.attr == "str" else None
    )
    if format_position is not None and not _temporal_parse_call_has_literal_format(
        call, position=format_position
    ):
        trace.record("unsupported_call_shape", method_name, call)
        return False, False
    return True, False


def _row_local_expr_is_supported(
    node: ast.AST,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
    selector_aliases: frozenset[str],
    trace: _ChunkLocalTrace,
) -> tuple[bool, bool]:
    if isinstance(node, ast.Call):
        return _row_local_call_is_supported(
            node,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        )
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "pl":
            return True, False
        trace.record("unsupported_expression", type(node).__name__, node)
        return False, False
    if isinstance(node, ast.Name):
        is_frame = node.id in allowed_frames or node.id in local_frames
        if not is_frame:
            trace.record("unsupported_expression", type(node).__name__, node)
        return is_frame, is_frame
    if isinstance(node, ast.Constant):
        return True, False
    if isinstance(node, ast.BinOp | ast.UnaryOp) and (
        literal_selector(node, aliases=selector_aliases) is not None
    ):
        return True, False
    # Composite expressions must be row-local AND frame-free.  A frame name is
    # only chunk-safe as a method-chain receiver; embedded in a subscript
    # (``frame["col"]``), operand, or collection it reads the full frame under
    # full execution but only the current chunk under chunked execution, which
    # silently diverges - so _row_local_subexprs_are_supported rejects it.
    if isinstance(node, ast.BinOp):
        children: tuple[ast.expr | None, ...] = (node.left, node.right)
    elif isinstance(node, ast.UnaryOp):
        children = (node.operand,)
    elif isinstance(node, ast.BoolOp):
        children = tuple(node.values)
    elif isinstance(node, ast.Compare):
        children = (node.left, *node.comparators)
    elif isinstance(node, ast.IfExp):
        children = (node.test, node.body, node.orelse)
    elif isinstance(node, ast.List | ast.Tuple | ast.Set):
        children = tuple(node.elts)
    elif isinstance(node, ast.Dict):
        children = (*node.keys, *node.values)
    elif isinstance(node, ast.Subscript):
        children = (node.value, node.slice)
    elif isinstance(node, ast.Slice):
        children = (node.lower, node.upper, node.step)
    else:
        trace.record("unsupported_expression", type(node).__name__, node)
        return False, False
    return _row_local_subexprs_are_supported(
        children,
        allowed_frames=allowed_frames,
        local_frames=local_frames,
        trace=trace,
        selector_aliases=selector_aliases,
    ), False


def _row_local_stmt_is_supported(
    stmt: ast.stmt,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
    selector_aliases: frozenset[str],
    trace: _ChunkLocalTrace,
) -> bool:
    if isinstance(stmt, ast.Assign | ast.AnnAssign):
        targets: list[ast.expr] = (
            list(stmt.targets) if isinstance(stmt, ast.Assign) else [stmt.target]
        )
        if not all(isinstance(target, ast.Name) for target in targets):
            trace.record("assignment_not_frame_derived", ast.unparse(targets[0]), stmt)
            return False
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if stmt.value is None:
            trace.record("assignment_not_frame_derived", names[0], stmt)
            return False
        supported, derived_from_frame = _row_local_expr_is_supported(
            stmt.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        )
        if not supported:
            return False
        if not derived_from_frame:
            trace.record("assignment_not_frame_derived", names[0], stmt)
            return False
        local_frames.update(names)
        return True
    if isinstance(stmt, ast.Expr):
        supported, derived_from_frame = _row_local_expr_is_supported(
            stmt.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
            trace=trace,
            selector_aliases=selector_aliases,
        )
        if not supported:
            return False
        if not derived_from_frame:
            trace.record("unsupported_statement", type(stmt).__name__, stmt)
            return False
        return True
    trace.record("unsupported_statement", type(stmt).__name__, stmt)
    return False
