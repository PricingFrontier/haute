"""One closed, receiver-aware registry of the Polars operations Haute analyses.

Two analysers used to keep private vocabularies of Polars names: the chunk
classifier in :mod:`haute.chunking` (which names are provably chunk-local) and
the lineage/cardinality analyser in :mod:`haute._column_lineage` (which names
have a program-model transfer).  A name could therefore be classified
differently by each.  This module is the single authority: every name is
registered once per receiver, carries its semantic class, its execution policy,
and the two independent admissions (``chunk_admitted``, ``lineage_supported``),
and the analysers derive their sets from here.

The registry is data, not behaviour: it must stay exactly equal to what the
analysers admitted before it existed.  ``chunk_admitted`` in particular means
"a chunked==full proof case exists in tests/test_chunk_whitelist_proofs.py",
so it can only be widened together with a new proof.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

__all__ = [
    "POLARS_OPERATIONS",
    "POLARS_SELECTORS_MODULE_FUNCTIONS",
    "POLARS_SELECTOR_CONSTRUCTORS",
    "OperationClass",
    "OperationPolicy",
    "OperationReceiver",
    "EXPRESSION_NAMESPACE_NAMES",
    "PolarsOperation",
    "SelectorForm",
    "chunk_admitted_names",
    "chunk_admitted_selector_constructors",
    "costly_expression_methods",
    "costly_frame_methods",
    "full_input_work",
    "lineage_supported_frame_methods",
    "materialisation_factor_basis_points",
    "measured_operation_names",
    "materialising_expression_methods",
    "materialising_frame_methods",
    "operation",
    "recompute_cost",
    "registered_names",
    "slice_transparent",
    "unbounded_expansion_expression_methods",
    "validate_operations",
    "validate_recompute_declarations",
]


# The attribute namespaces Polars exposes on an expression. A method reached
# through one (``pl.col("l").list.shift()``) works within each row's value, so it
# is never the same-named expression-level method.
EXPRESSION_NAMESPACE_NAMES = frozenset(
    {"str", "dt", "list", "arr", "struct", "cat", "bin", "name", "meta"}
)


class OperationReceiver(StrEnum):
    """What a registered name is called on."""

    FRAME = "frame"
    EXPR = "expr"
    NAMESPACE = "namespace"
    POLARS_FUNCTION = "polars_function"


class OperationClass(StrEnum):
    """Semantic effect of an operation on rows."""

    ROW_LOCAL = "row_local"
    ORDER_DEPENDENT = "order_dependent"
    ROW_EXPANDING = "row_expanding"
    FAN_IN_STATEFUL = "fan_in_stateful"
    OPAQUE = "opaque"


class OperationPolicy(StrEnum):
    """How execution treats the operation."""

    ROW_LOCAL = "row_local"
    """Chunk-local and streaming."""

    STREAMING = "streaming"
    """Runs through the lazy engine without a planner-inserted boundary.

    This is not a claim that the operation is memory-bounded: read
    :attr:`PolarsOperation.memory_evidence` for that. ``measured`` means a probe
    put the operation at or below the streaming floor; ``none`` means its policy
    is inherited rather than measured. An operation only moves to
    :attr:`MATERIALISATION_BOUNDARY` on measured evidence that it materialises.
    """

    MATERIALISATION_BOUNDARY = "materialisation_boundary"
    """The planner inserts an admitted materialisation boundary."""

    OPAQUE = "opaque"
    """Unknown effect; execution follows the conservative policy."""


Expansion = Literal["none", "bounded", "unbounded"]

MemoryEvidence = Literal["measured", "none"]
"""Whether EXEC-P07's peak-memory probe covered this operation.

``measured`` means the certification lane measured the operation's incremental
peak RSS against the streaming floor and the policy below records that result.
``none`` means the policy is inherited, not measured, and must not be read as
evidence of anything."""


@dataclass(frozen=True, slots=True)
class PolarsOperation:
    """One registered Polars operation."""

    receiver: OperationReceiver
    name: str
    namespace: str | None
    operation_class: OperationClass
    policy: OperationPolicy
    expansion: Expansion
    chunk_admitted: bool
    lineage_supported: bool
    memory_evidence: MemoryEvidence
    """Whether the EXEC-P07 probe measured this operation's peak memory."""

    materialisation_factor_basis_points: int
    """Operator memory multiplier applied on top of the rows x width estimate.

    100 basis points is "no operator surcharge". Values above that come from
    measured peak memory (see EXEC-P07): the measured peak minus the streaming
    floor, divided by the existing estimate for the same frame.
    """

    note: str
    costly_to_recompute: bool | None = None
    """Whether recomputing the operation is costly compared to a cache round-trip."""

    slice_transparent: bool | None = None
    """Whether Polars pushes a row slice through this operation unchanged."""


def _op(
    receiver: OperationReceiver,
    name: str,
    operation_class: OperationClass,
    policy: OperationPolicy,
    note: str,
    *,
    namespace: str | None = None,
    expansion: Expansion = "none",
    chunk_admitted: bool = False,
    lineage_supported: bool = False,
    materialisation_factor_basis_points: int = 100,
    memory_evidence: MemoryEvidence = "none",
    costly_to_recompute: bool | None = None,
    slice_transparent: bool | None = None,
) -> PolarsOperation:
    return PolarsOperation(
        receiver=receiver,
        name=name,
        namespace=namespace,
        operation_class=operation_class,
        policy=policy,
        expansion=expansion,
        chunk_admitted=chunk_admitted,
        lineage_supported=lineage_supported,
        materialisation_factor_basis_points=materialisation_factor_basis_points,
        memory_evidence=memory_evidence,
        note=note,
        costly_to_recompute=costly_to_recompute,
        slice_transparent=slice_transparent,
    )


_FRAME = OperationReceiver.FRAME
_EXPR = OperationReceiver.EXPR
_NS = OperationReceiver.NAMESPACE
_FN = OperationReceiver.POLARS_FUNCTION

_ROW_LOCAL = OperationClass.ROW_LOCAL
_ORDER_DEPENDENT = OperationClass.ORDER_DEPENDENT
_ROW_EXPANDING = OperationClass.ROW_EXPANDING
_FAN_IN = OperationClass.FAN_IN_STATEFUL
_OPAQUE_CLASS = OperationClass.OPAQUE

_P_ROW_LOCAL = OperationPolicy.ROW_LOCAL
_P_STREAMING = OperationPolicy.STREAMING
_P_BOUNDARY = OperationPolicy.MATERIALISATION_BOUNDARY
_P_OPAQUE = OperationPolicy.OPAQUE


def _row_local_expr(name: str, note: str) -> PolarsOperation:
    return _op(_EXPR, name, _ROW_LOCAL, _P_ROW_LOCAL, note, chunk_admitted=True)


def _order_dependent_expr(name: str, note: str) -> PolarsOperation:
    return _op(_EXPR, name, _ORDER_DEPENDENT, _P_STREAMING, note)


def _fan_in_expr(name: str, note: str) -> PolarsOperation:
    return _op(_EXPR, name, _FAN_IN, _P_STREAMING, note)


def _expanding_expr(name: str) -> PolarsOperation:
    return _op(
        _EXPR,
        name,
        _ROW_EXPANDING,
        _P_STREAMING,
        "variable-length Expr API: the result can outgrow the input frame",
        expansion="unbounded",
    )


def _admitted_ns(namespace: str, name: str) -> PolarsOperation:
    return _op(
        _NS,
        name,
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        f"proof: expr.{namespace}.{name} chunked==full case",
        namespace=namespace,
        chunk_admitted=True,
    )


def _unproven_ns(namespace: str, name: str) -> PolarsOperation:
    return _op(
        _NS,
        name,
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "literal-argument namespace method; not chunk-admitted: no chunked==full proof yet",
        namespace=namespace,
    )


def _admitted_fn(name: str, note: str) -> PolarsOperation:
    return _op(_FN, name, _ROW_LOCAL, _P_ROW_LOCAL, note, chunk_admitted=True)


def _row_local_fn(name: str, note: str) -> PolarsOperation:
    """A ``pl.`` helper whose every output row depends only on its input row."""
    return _op(
        _FN,
        name,
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        f"{note}; not chunk-admitted: no chunked==full proof yet",
    )


def _fan_in_fn(name: str, note: str) -> PolarsOperation:
    """A ``pl.`` helper that reduces the whole context, not one row at a time."""
    return _op(_FN, name, _FAN_IN, _P_STREAMING, note)


def _order_dependent_fn(name: str, note: str) -> PolarsOperation:
    """A ``pl.`` helper whose result depends on the whole input's order/positions."""
    return _op(_FN, name, _ORDER_DEPENDENT, _P_STREAMING, note)


def _opaque_fn(name: str) -> PolarsOperation:
    return _op(
        _FN,
        name,
        _OPAQUE_CLASS,
        _P_OPAQUE,
        "schema-dependent selector: expands against the ambient schema",
    )


def _opaque_frame(name: str) -> PolarsOperation:
    return _op(_FRAME, name, _OPAQUE_CLASS, _P_OPAQUE, "unknown row/memory effect")


_ENTRIES: tuple[PolarsOperation, ...] = (
    # ---------------------------------------------------------------- frame
    # Row-local frame methods.  Each ``chunk_admitted`` entry cites the
    # chunked==full property case in tests/test_chunk_whitelist_proofs.py.
    _op(
        _FRAME,
        "cast",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_cast (Categorical/Enum targets rejected by the shape validator)",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "drop",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_drop",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "drop_nulls",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_drop_nulls",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "filter",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_filter. streams: certified by the fresh-process lane against the scan "
        "control, at the streaming floor",
        chunk_admitted=True,
        lineage_supported=True,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "fill_nan",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_fill_nan (value-only signature in polars)",
        chunk_admitted=True,
    ),
    _op(
        _FRAME,
        "fill_null",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_fill_null_value (value form only; see shape validator)",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "rename",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_rename",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "select",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_select",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "select_seq",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "lineage treats it as select; not chunk-admitted: no chunked==full proof case yet",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "with_columns",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_with_columns",
        chunk_admitted=True,
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "with_columns_seq",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "proof: df_with_columns_seq (lineage parser accepts only with_columns)",
        chunk_admitted=True,
    ),
    _op(
        _FRAME,
        "unnest",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "expands a struct column into its fields, one output row per input row",
    ),
    # Order-dependent frame methods: the result depends on rows outside a chunk.
    _op(
        _FRAME,
        "head",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "row-count-only truncation; a chunk's head is not the frame's head",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "tail",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "row-count-only truncation from the end of the full frame",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "limit",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "alias of head; row-only lineage transfer",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "slice",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "positional window over the full frame; row-only lineage transfer",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "with_row_index",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "index values restart per chunk; lineage produces the named index column",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "sort",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "global ordering; lineage keeps the schema and demands the sort keys. "
        "materialises: certified by the fresh-process lane against the scan control and "
        "witnessed above the scan_head floor; the certified observed/(width x 3.0) ratio "
        "needs 300 basis points of margin",
        lineage_supported=True,
        materialisation_factor_basis_points=300,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "unique",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "duplicates can straddle a chunk boundary; lineage keeps the schema. "
        "materialises: certified by the fresh-process lane against the scan control and "
        "witnessed above the scan_head floor; the certified observed/(width x 3.0) ratio "
        "needs 350 basis points of margin",
        lineage_supported=True,
        materialisation_factor_basis_points=350,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "reverse",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "global row order. materialises: certified by the fresh-process lane against the "
        "scan control; the certified observed/(width x 3.0) ratio needs 250 basis points "
        "of margin",
        materialisation_factor_basis_points=250,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "shift",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "reads neighbouring rows. materialises: from Polars 1.44 the streaming shift "
        "buffers state that grows with the input, certified by the fresh-process lane "
        "against the scan control; the certified observed/(width x 3.0) ratio needs no "
        "margin",
        lineage_supported=True,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "gather",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "positional indices into the full frame; no evidence, streaming kept",
    ),
    _op(
        _FRAME,
        "sample",
        _ORDER_DEPENDENT,
        _P_STREAMING,
        "samples the full frame; no evidence, streaming kept",
    ),
    _op(
        _FRAME,
        "top_k",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "global ranking. materialises: certified by the fresh-process lane against the "
        "scan_head tiny-output control",
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "bottom_k",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "global ranking. materialises: certified by the fresh-process lane against the "
        "scan_head tiny-output control",
        memory_evidence="measured",
    ),
    # Row-expanding frame methods.
    _op(
        _FRAME,
        "explode",
        _ROW_EXPANDING,
        _P_BOUNDARY,
        "list lengths are data-dependent, so the expansion factor is unbounded and the "
        "estimate is therefore unavailable (row_expansion_unbounded); the factor is never "
        "applied. materialises: certified by the fresh-process lane against the scan "
        "control and witnessed above the scan_head floor",
        expansion="unbounded",
        lineage_supported=True,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "unpivot",
        _ROW_EXPANDING,
        _P_STREAMING,
        "expansion factor is the literal ``on`` column count, hence bounded. "
        "streams: certified by the fresh-process lane against the scan control, at the "
        "streaming floor despite the row expansion",
        expansion="bounded",
        lineage_supported=True,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "melt",
        _ROW_EXPANDING,
        _P_STREAMING,
        "alias of unpivot; measured as unpivot by the certification lane",
        expansion="unbounded",
        memory_evidence="measured",
    ),
    # Fan-in / stateful frame methods.
    _op(
        _FRAME,
        "group_by",
        _FAN_IN,
        _P_BOUNDARY,
        "aggregation state spans the whole frame; valid only at a materialisation boundary",
        lineage_supported=True,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "groupby",
        _FAN_IN,
        _P_BOUNDARY,
        "legacy group_by spelling; same materialisation boundary; measured as group_by by "
        "the fresh-process lane against the scan control",
        lineage_supported=True,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "agg",
        _FAN_IN,
        _P_STREAMING,
        "only meaningful as the second half of group_by().agg(); never a boundary on its own",
        lineage_supported=True,
    ),
    _op(
        _FRAME,
        "join",
        _FAN_IN,
        _P_BOUNDARY,
        "two-input fan-in; lineage routes demand per side and bounds cardinality by "
        "validation. materialises: certified by the fresh-process lane against the scan "
        "control and witnessed above the scan_head floor; the estimator already sums both "
        "ports' widths, and the certified observed/(width x 3.0) ratio needs 200 basis "
        "points of margin for a declared join",
        lineage_supported=True,
        materialisation_factor_basis_points=200,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "join_asof",
        _FAN_IN,
        _P_BOUNDARY,
        "ordered fan-in with no lineage transfer. materialises: certified by the "
        "fresh-process lane against the scan control (the plan includes a sort by the asof "
        "key) and witnessed by the port-swapped join_asof_big_right variant above the "
        "scan_head floor; the certified observed/(width x 3.0) ratio needs 250 basis "
        "points of margin",
        materialisation_factor_basis_points=250,
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "group_by_dynamic",
        _FAN_IN,
        _P_STREAMING,
        "temporal windows span chunk edges. streams: certified by the fresh-process lane "
        "against the scan control, below the streaming floor",
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "rolling",
        _FAN_IN,
        _P_STREAMING,
        "windows span chunk edges. streams: certified by the fresh-process lane against "
        "the scan control, below the streaming floor",
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "join_where",
        _FAN_IN,
        _P_STREAMING,
        "predicate fan-in with no lineage transfer; no evidence, streaming kept",
    ),
    _op(
        _FRAME,
        "merge_sorted",
        _FAN_IN,
        _P_STREAMING,
        "two-input ordered merge. streams: certified by the fresh-process lane against the "
        "scan control, below the streaming floor",
        memory_evidence="measured",
    ),
    _op(
        _FRAME,
        "pivot",
        _FAN_IN,
        _P_STREAMING,
        "output schema depends on the data; no evidence, streaming kept",
    ),
    _op(
        _FRAME,
        "upsample",
        _FAN_IN,
        _P_STREAMING,
        "fills across the full time axis; no evidence, streaming kept",
    ),
    _op(
        _FRAME,
        "interpolate",
        _FAN_IN,
        _P_STREAMING,
        "reads neighbouring rows across the frame. streams: about 1.1x the narrow "
        "passthrough floor at 1.5M rows by interleaved paired sampling (single samples "
        "range 0.9-1.4) once verification left the sampled process; an earlier 1.48 was "
        "the verification pass",
        memory_evidence="measured",
    ),
    # Opaque frame methods.
    _opaque_frame("collect"),
    _opaque_frame("collect_batches"),
    _opaque_frame("fetch"),
    _opaque_frame("pipe"),
    _opaque_frame("map_batches"),
    _opaque_frame("lazy"),
    _opaque_frame("sink_parquet"),
    _opaque_frame("sink_csv"),
    _opaque_frame("to_pandas"),
    _opaque_frame("to_numpy"),
    _opaque_frame("iter_rows"),
    _opaque_frame("rows"),
    _opaque_frame("partition_by"),
    _opaque_frame("with_context"),
    # ----------------------------------------------------------------- expr
    _row_local_expr("abs", "proof: expr_abs"),
    _row_local_expr("alias", "proof: expr_alias"),
    _row_local_expr("cast", "proof: expr_cast (Categorical/Enum rejected by the shape validator)"),
    _row_local_expr("ceil", "proof: expr_ceil"),
    _row_local_expr("clip", "proof: expr_clip"),
    _row_local_expr("exp", "proof: expr_exp"),
    _row_local_expr("fill_nan", "proof: expr_fill_nan (value-only signature in polars)"),
    _row_local_expr("fill_null", "proof: expr_fill_null_value (value form only)"),
    _row_local_expr("floor", "proof: expr_floor"),
    _row_local_expr("is_between", "proof: expr_is_between"),
    _row_local_expr("is_finite", "proof: expr_is_finite"),
    _row_local_expr("is_in", "proof: expr_is_in_literal (literal collections only)"),
    _row_local_expr("is_infinite", "proof: expr_is_infinite"),
    _row_local_expr("is_nan", "proof: expr_is_nan"),
    _row_local_expr("is_not_nan", "proof: expr_is_not_nan"),
    _row_local_expr("is_not_null", "proof: expr_is_not_null"),
    _row_local_expr("is_null", "proof: expr_is_null"),
    _row_local_expr("log", "proof: expr_log"),
    _row_local_expr("not_", "proof: expr_not"),
    _row_local_expr("otherwise", "proof: expr_when_then_otherwise"),
    _row_local_expr("replace", "proof: expr_replace (literal mapping only)"),
    _row_local_expr("round", "proof: expr_round"),
    _row_local_expr("sqrt", "proof: expr_sqrt"),
    _row_local_expr("then", "proof: expr_when_then_otherwise"),
    _op(
        _EXPR,
        "shift",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "reads neighbouring rows. materialises: a lag column over the full-width fact "
        "grows with the input like the frame method, certified by the fresh-process lane "
        "against the scan control and witnessed above the streaming ceiling or by growth "
        "at four times the rows; the certified observed/(width x 3.0) ratio needs no margin",
        memory_evidence="measured",
    ),
    _order_dependent_expr("cum_sum", "running total over the whole column"),
    _order_dependent_expr("cum_count", "running count over the whole column"),
    _order_dependent_expr("cum_max", "running extremum over the whole column"),
    _order_dependent_expr("cum_min", "running extremum over the whole column"),
    _order_dependent_expr("cum_prod", "running product over the whole column"),
    _op(
        _EXPR,
        "diff",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "reads neighbouring rows; Polars computes it as the column minus its shift. "
        "materialises: certified by the fresh-process lane against the scan control and "
        "witnessed above the streaming ceiling or by growth at four times the rows; the "
        "certified observed/(width x 3.0) ratio needs no margin",
        memory_evidence="measured",
    ),
    _order_dependent_expr("rank", "global ranking"),
    _order_dependent_expr("rolling_mean", "window spans chunk edges"),
    _order_dependent_expr("rolling_sum", "window spans chunk edges"),
    _order_dependent_expr("rolling_min", "window spans chunk edges"),
    _order_dependent_expr("rolling_max", "window spans chunk edges"),
    _order_dependent_expr("rolling_std", "window spans chunk edges"),
    _order_dependent_expr("ewm_mean", "carries state across the whole column"),
    _order_dependent_expr("forward_fill", "carries the previous non-null across chunk edges"),
    _order_dependent_expr("backward_fill", "carries the next non-null across chunk edges"),
    _order_dependent_expr("interpolate", "reads neighbouring rows"),
    _op(
        _EXPR,
        "pct_change",
        _ORDER_DEPENDENT,
        _P_BOUNDARY,
        "reads neighbouring rows. materialises: its extra memory grows about as fast as "
        "the whole frame, certified by the fresh-process lane against the scan control "
        "and witnessed above the streaming ceiling or by growth at four times the rows; "
        "the certified observed/(width x 3.0) ratio needs 150 basis points of margin",
        materialisation_factor_basis_points=150,
        memory_evidence="measured",
    ),
    _order_dependent_expr("arg_sort", "global ordering"),
    _order_dependent_expr("sort", "global ordering"),
    _order_dependent_expr("sort_by", "global ordering"),
    _order_dependent_expr("reverse", "global row order"),
    _order_dependent_expr("head", "positional truncation of the full column"),
    _order_dependent_expr("tail", "positional truncation of the full column"),
    _order_dependent_expr("first", "positional read of the full column"),
    _order_dependent_expr("last", "positional read of the full column"),
    _order_dependent_expr("implode", "collapses the whole column into one list"),
    _order_dependent_expr("unique", "duplicates can straddle a chunk boundary"),
    _order_dependent_expr("n_unique", "distinct count over the whole column"),
    _order_dependent_expr("value_counts", "counts over the whole column"),
    _op(
        _EXPR,
        "over",
        _FAN_IN,
        _P_BOUNDARY,
        "window partitions span the whole frame. materialises: certified by the "
        "fresh-process lane against the scan control and witnessed by the over_narrow "
        "variant above the scan_narrow floor; the certified observed/(width x 3.0) ratio "
        "needs 250 basis points of margin",
        materialisation_factor_basis_points=250,
        memory_evidence="measured",
    ),
    _fan_in_expr("mode", "reduction over the whole column"),
    _fan_in_expr("sum", "reduction over the whole column"),
    _fan_in_expr("mean", "reduction over the whole column"),
    _fan_in_expr("min", "reduction over the whole column"),
    _fan_in_expr("max", "reduction over the whole column"),
    _fan_in_expr("median", "reduction over the whole column"),
    _fan_in_expr("std", "reduction over the whole column"),
    _fan_in_expr("var", "reduction over the whole column"),
    _fan_in_expr("count", "reduction over the whole column"),
    _fan_in_expr("len", "reduction over the whole column"),
    _fan_in_expr("quantile", "reduction over the whole column"),
    _fan_in_expr("arg_max", "reduction over the whole column"),
    _fan_in_expr("arg_min", "reduction over the whole column"),
    _expanding_expr("append"),
    _expanding_expr("deserialize"),
    _expanding_expr("explode"),
    _expanding_expr("extend_constant"),
    _expanding_expr("flatten"),
    _expanding_expr("from_json"),
    _expanding_expr("gather"),
    _expanding_expr("hist"),
    _expanding_expr("sample"),
    _expanding_expr("search_sorted"),
    _op(
        _EXPR,
        "map_batches",
        _OPAQUE_CLASS,
        _P_OPAQUE,
        "user callback over a whole Series: arbitrary length and arbitrary effect",
        expansion="unbounded",
    ),
    _op(
        _EXPR,
        "pipe",
        _OPAQUE_CLASS,
        _P_OPAQUE,
        "user callback receives the expression: arbitrary result",
        expansion="unbounded",
    ),
    _op(
        _EXPR,
        "register_plugin",
        _OPAQUE_CLASS,
        _P_OPAQUE,
        "external plugin kernel: arbitrary length and arbitrary effect",
        expansion="unbounded",
    ),
    _op(
        _EXPR,
        "map_elements",
        _OPAQUE_CLASS,
        _P_OPAQUE,
        "user callback per element; row-preserving, so the cardinality analyser guards it "
        "by callback argument rather than by unbounded expansion",
    ),
    _op(
        _EXPR,
        "rolling_map",
        _OPAQUE_CLASS,
        _P_OPAQUE,
        "user callback per window; guarded by callback argument, not by expansion",
    ),
    # ------------------------------------------------------------ namespace
    _admitted_ns("str", "contains"),
    _admitted_ns("str", "ends_with"),
    _admitted_ns("str", "extract"),
    _admitted_ns("str", "len_chars"),
    _admitted_ns("str", "pad_end"),
    _admitted_ns("str", "pad_start"),
    _admitted_ns("str", "replace"),
    _admitted_ns("str", "replace_all"),
    _admitted_ns("str", "slice"),
    _admitted_ns("str", "split"),
    _admitted_ns("str", "starts_with"),
    _admitted_ns("str", "strip_chars"),
    _admitted_ns("str", "strip_prefix"),
    _admitted_ns("str", "strip_suffix"),
    _admitted_ns("str", "strptime"),
    _admitted_ns("str", "to_date"),
    _admitted_ns("str", "to_datetime"),
    _admitted_ns("str", "to_lowercase"),
    _admitted_ns("str", "to_time"),
    _admitted_ns("str", "to_uppercase"),
    _admitted_ns("str", "zfill"),
    _admitted_ns("dt", "date"),
    _admitted_ns("dt", "day"),
    _admitted_ns("dt", "epoch"),
    _admitted_ns("dt", "hour"),
    _admitted_ns("dt", "minute"),
    _admitted_ns("dt", "month"),
    _admitted_ns("dt", "offset_by"),
    _admitted_ns("dt", "ordinal_day"),
    _admitted_ns("dt", "quarter"),
    _admitted_ns("dt", "second"),
    _admitted_ns("dt", "strftime"),
    _admitted_ns("dt", "to_string"),
    _admitted_ns("dt", "truncate"),
    _admitted_ns("dt", "weekday"),
    _admitted_ns("dt", "year"),
    _unproven_ns("str", "count_matches"),
    _unproven_ns("str", "extract_all"),
    _unproven_ns("str", "extract_groups"),
    _unproven_ns("str", "find"),
    _unproven_ns("str", "json_path_match"),
    _unproven_ns("str", "split_exact"),
    _unproven_ns("str", "splitn"),
    _unproven_ns("str", "strip_chars_end"),
    _unproven_ns("str", "strip_chars_start"),
    _unproven_ns("dt", "convert_time_zone"),
    _unproven_ns("dt", "replace_time_zone"),
    _unproven_ns("dt", "round"),
    _op(
        _NS,
        "explode",
        _ROW_EXPANDING,
        _P_STREAMING,
        "list lengths are data-dependent, so the expansion factor is unbounded",
        namespace="list",
        expansion="unbounded",
    ),
    _op(
        _NS,
        "join",
        _FAN_IN,
        _P_STREAMING,
        "reduces the whole column to one value",
        namespace="str",
    ),
    _op(
        _NS,
        "concat",
        _FAN_IN,
        _P_STREAMING,
        "reduces the whole column to one value",
        namespace="str",
    ),
    # ------------------------------------------------------- polars function
    _admitted_fn("all_horizontal", "proof: fn_all_horizontal"),
    _admitted_fn("any_horizontal", "proof: fn_any_horizontal"),
    _admitted_fn("coalesce", "proof: fn_coalesce"),
    _admitted_fn("col", "proof: fn_col"),
    _admitted_fn("concat_str", "proof: fn_concat_str"),
    _admitted_fn("lit", "proof: fn_lit"),
    _admitted_fn("max_horizontal", "proof: fn_max_horizontal"),
    _admitted_fn("mean_horizontal", "proof: fn_mean_horizontal"),
    _admitted_fn("sum_horizontal", "proof: fn_sum_horizontal"),
    _admitted_fn("when", "proof: expr_when_then_otherwise"),
    _op(
        _FN,
        "min_horizontal",
        _ROW_LOCAL,
        _P_ROW_LOCAL,
        "not chunk-admitted: its NaN handling is chunk-sensitive (de-whitelist pin)",
    ),
    _order_dependent_fn(
        "arg_sort_by",
        "returns row positions of the whole input's sort order",
    ),
    _order_dependent_fn(
        "arg_where",
        "returns the positions of true values across the whole column",
    ),
    _fan_in_fn("len", "counts the rows of the whole context down to one value"),
    _row_local_fn("business_day_count", "per-row business-day difference of two date columns"),
    _row_local_fn("concat_arr", "per-row horizontal concatenation into a fixed-width array"),
    _row_local_fn("concat_list", "per-row horizontal concatenation into a list"),
    _row_local_fn("cum_fold", "per-row horizontal cumulative fold across the given columns"),
    _row_local_fn("cum_reduce", "per-row horizontal cumulative reduction across the given columns"),
    _row_local_fn("cum_sum_horizontal", "per-row horizontal cumulative sum"),
    _row_local_fn("date", "per-row Date constructor from year/month/day columns"),
    _row_local_fn("date_ranges", "per-row start/end pair to one list of dates"),
    _row_local_fn("datetime", "per-row Datetime constructor from component columns"),
    _row_local_fn("datetime_ranges", "per-row start/end pair to one list of datetimes"),
    _row_local_fn("duration", "per-row Duration constructor from component columns"),
    _row_local_fn("element", "the per-element placeholder inside list.eval"),
    _row_local_fn("fold", "per-row horizontal fold across the given columns"),
    _row_local_fn("format", "per-row string formatting of the given columns"),
    _row_local_fn("from_epoch", "per-row epoch offset to a temporal value"),
    _row_local_fn("int_ranges", "per-row start/end pair to one list of integers"),
    _row_local_fn("linear_spaces", "per-row start/end pair to one list of evenly spaced values"),
    _row_local_fn("reduce", "per-row horizontal reduction across the given columns"),
    _row_local_fn("struct", "per-row struct constructor from the given columns"),
    _row_local_fn("time", "per-row Time constructor from component columns"),
    _row_local_fn("time_ranges", "per-row start/end pair to one list of times"),
    _opaque_fn("all"),
    _opaque_fn("exclude"),
    _opaque_fn("first"),
    _opaque_fn("last"),
    _opaque_fn("nth"),
    _opaque_fn("selectors"),
)


_RECOMPUTE_COST_DECLARATIONS: Mapping[tuple[OperationReceiver, str | None, str], bool] = (
    MappingProxyType(
        {
            # Order-dependent costly: expr arg_sort, rank, sort, sort_by, unique, n_unique,
            # value_counts; frame sort, top_k, bottom_k, unique; polars_function arg_sort_by.
            (OperationReceiver.EXPR, None, "arg_sort"): True,
            (OperationReceiver.EXPR, None, "rank"): True,
            (OperationReceiver.EXPR, None, "sort"): True,
            (OperationReceiver.EXPR, None, "sort_by"): True,
            (OperationReceiver.EXPR, None, "unique"): True,
            (OperationReceiver.EXPR, None, "n_unique"): True,
            (OperationReceiver.EXPR, None, "value_counts"): True,
            (OperationReceiver.FRAME, None, "sort"): True,
            (OperationReceiver.FRAME, None, "top_k"): True,
            (OperationReceiver.FRAME, None, "bottom_k"): True,
            (OperationReceiver.FRAME, None, "unique"): True,
            (OperationReceiver.POLARS_FUNCTION, None, "arg_sort_by"): True,
            # Order-dependent cheap: expr backward_fill, forward_fill, cum_count, cum_max,
            # cum_min, cum_prod, cum_sum, diff, pct_change, shift, reverse, ewm_mean,
            # rolling_max, rolling_mean, rolling_min, rolling_std, rolling_sum, interpolate,
            # implode, first, last, head, tail; frame gather, head, limit, slice, tail,
            # sample, reverse, shift, with_row_index; polars_function arg_where.
            (OperationReceiver.EXPR, None, "backward_fill"): False,
            (OperationReceiver.EXPR, None, "forward_fill"): False,
            (OperationReceiver.EXPR, None, "cum_count"): False,
            (OperationReceiver.EXPR, None, "cum_max"): False,
            (OperationReceiver.EXPR, None, "cum_min"): False,
            (OperationReceiver.EXPR, None, "cum_prod"): False,
            (OperationReceiver.EXPR, None, "cum_sum"): False,
            (OperationReceiver.EXPR, None, "diff"): False,
            (OperationReceiver.EXPR, None, "pct_change"): False,
            (OperationReceiver.EXPR, None, "shift"): False,
            (OperationReceiver.EXPR, None, "reverse"): False,
            (OperationReceiver.EXPR, None, "ewm_mean"): False,
            (OperationReceiver.EXPR, None, "rolling_max"): False,
            (OperationReceiver.EXPR, None, "rolling_mean"): False,
            (OperationReceiver.EXPR, None, "rolling_min"): False,
            (OperationReceiver.EXPR, None, "rolling_std"): False,
            (OperationReceiver.EXPR, None, "rolling_sum"): False,
            (OperationReceiver.EXPR, None, "interpolate"): False,
            (OperationReceiver.EXPR, None, "implode"): False,
            (OperationReceiver.EXPR, None, "first"): False,
            (OperationReceiver.EXPR, None, "last"): False,
            (OperationReceiver.EXPR, None, "head"): False,
            (OperationReceiver.EXPR, None, "tail"): False,
            (OperationReceiver.FRAME, None, "gather"): False,
            (OperationReceiver.FRAME, None, "head"): False,
            (OperationReceiver.FRAME, None, "limit"): False,
            (OperationReceiver.FRAME, None, "slice"): False,
            (OperationReceiver.FRAME, None, "tail"): False,
            (OperationReceiver.FRAME, None, "sample"): False,
            (OperationReceiver.FRAME, None, "reverse"): False,
            (OperationReceiver.FRAME, None, "shift"): False,
            (OperationReceiver.FRAME, None, "with_row_index"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "arg_where"): False,
            # Fan-in costly: expr over, median, mode, quantile; frame agg, group_by,
            # groupby, group_by_dynamic, join, join_asof, join_where, merge_sorted,
            # pivot, rolling, upsample.
            (OperationReceiver.EXPR, None, "over"): True,
            (OperationReceiver.EXPR, None, "median"): True,
            (OperationReceiver.EXPR, None, "mode"): True,
            (OperationReceiver.EXPR, None, "quantile"): True,
            (OperationReceiver.FRAME, None, "agg"): True,
            (OperationReceiver.FRAME, None, "group_by"): True,
            (OperationReceiver.FRAME, None, "groupby"): True,
            (OperationReceiver.FRAME, None, "group_by_dynamic"): True,
            (OperationReceiver.FRAME, None, "join"): True,
            (OperationReceiver.FRAME, None, "join_asof"): True,
            (OperationReceiver.FRAME, None, "join_where"): True,
            (OperationReceiver.FRAME, None, "merge_sorted"): True,
            (OperationReceiver.FRAME, None, "pivot"): True,
            (OperationReceiver.FRAME, None, "rolling"): True,
            (OperationReceiver.FRAME, None, "upsample"): True,
            # Fan-in cheap: expr sum, mean, min, max, count, len, std, var, arg_max,
            # arg_min; frame interpolate; namespace str concat, namespace str join;
            # polars_function len.
            (OperationReceiver.EXPR, None, "sum"): False,
            (OperationReceiver.EXPR, None, "mean"): False,
            (OperationReceiver.EXPR, None, "min"): False,
            (OperationReceiver.EXPR, None, "max"): False,
            (OperationReceiver.EXPR, None, "count"): False,
            (OperationReceiver.EXPR, None, "len"): False,
            (OperationReceiver.EXPR, None, "std"): False,
            (OperationReceiver.EXPR, None, "var"): False,
            (OperationReceiver.EXPR, None, "arg_max"): False,
            (OperationReceiver.EXPR, None, "arg_min"): False,
            (OperationReceiver.FRAME, None, "interpolate"): False,
            (OperationReceiver.NAMESPACE, "str", "concat"): False,
            (OperationReceiver.NAMESPACE, "str", "join"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "len"): False,
            # Opaque costly: expr map_elements, map_batches, pipe, register_plugin,
            # rolling_map; frame collect, collect_batches, fetch, iter_rows, lazy,
            # map_batches, partition_by, pipe, rows, sink_csv, sink_parquet, to_numpy,
            # to_pandas, with_context.
            (OperationReceiver.EXPR, None, "map_elements"): True,
            (OperationReceiver.EXPR, None, "map_batches"): True,
            (OperationReceiver.EXPR, None, "pipe"): True,
            (OperationReceiver.EXPR, None, "register_plugin"): True,
            (OperationReceiver.EXPR, None, "rolling_map"): True,
            (OperationReceiver.FRAME, None, "collect"): True,
            (OperationReceiver.FRAME, None, "collect_batches"): True,
            (OperationReceiver.FRAME, None, "fetch"): True,
            (OperationReceiver.FRAME, None, "iter_rows"): True,
            (OperationReceiver.FRAME, None, "lazy"): True,
            (OperationReceiver.FRAME, None, "map_batches"): True,
            (OperationReceiver.FRAME, None, "partition_by"): True,
            (OperationReceiver.FRAME, None, "pipe"): True,
            (OperationReceiver.FRAME, None, "rows"): True,
            (OperationReceiver.FRAME, None, "sink_csv"): True,
            (OperationReceiver.FRAME, None, "sink_parquet"): True,
            (OperationReceiver.FRAME, None, "to_numpy"): True,
            (OperationReceiver.FRAME, None, "to_pandas"): True,
            (OperationReceiver.FRAME, None, "with_context"): True,
            # Opaque cheap: polars_function all, exclude, first, last, nth, selectors.
            (OperationReceiver.POLARS_FUNCTION, None, "all"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "exclude"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "first"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "last"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "nth"): False,
            (OperationReceiver.POLARS_FUNCTION, None, "selectors"): False,
        }
    )
)


def _resolve_recompute_facts(
    entries: tuple[PolarsOperation, ...],
    declarations: Mapping[
        tuple[OperationReceiver, str | None, str], bool
    ] = _RECOMPUTE_COST_DECLARATIONS,
) -> tuple[PolarsOperation, ...]:
    resolved: list[PolarsOperation] = []
    for entry in entries:
        key = (entry.receiver, entry.namespace, entry.name)
        if entry.costly_to_recompute is not None:
            cost = entry.costly_to_recompute
        elif key in declarations:
            cost = declarations[key]
        elif entry.operation_class in (OperationClass.ROW_LOCAL, OperationClass.ROW_EXPANDING):
            cost = False
        else:
            raise RuntimeError(
                f"Operation {(entry.receiver.value, entry.namespace, entry.name)!r} must declare "
                "its recompute cost: order-dependent, fan-in, and opaque operations must "
                "declare their recompute cost."
            )

        if entry.slice_transparent is not None:
            transparent = entry.slice_transparent
        elif entry.operation_class is OperationClass.ROW_LOCAL:
            if entry.receiver is OperationReceiver.FRAME and entry.name in ("filter", "drop_nulls"):
                transparent = False
            else:
                transparent = True
        elif entry.receiver is OperationReceiver.POLARS_FUNCTION and entry.name in (
            "all",
            "exclude",
            "first",
            "last",
            "nth",
            "selectors",
        ):
            transparent = True
        else:
            transparent = False

        resolved.append(replace(entry, costly_to_recompute=cost, slice_transparent=transparent))
    return tuple(resolved)


_EXPANSION_ALLOWED_CLASSES = frozenset({OperationClass.ROW_EXPANDING, OperationClass.OPAQUE})
_ROW_LOCAL_OR_OPAQUE_CLASSES = frozenset({OperationClass.ROW_LOCAL, OperationClass.OPAQUE})


def validate_recompute_declarations(
    entries: tuple[PolarsOperation, ...] = _ENTRIES,
    declarations: Mapping[
        tuple[OperationReceiver, str | None, str], bool
    ] = _RECOMPUTE_COST_DECLARATIONS,
) -> None:
    """Validate that recompute declarations match registered operations without contradiction."""
    entry_keys = {(entry.receiver, entry.namespace, entry.name) for entry in entries}
    for key in declarations:
        if key not in entry_keys:
            raise RuntimeError(f"Declaration table key {key!r} names no existing Polars operation.")
    for entry in entries:
        key = (entry.receiver, entry.namespace, entry.name)
        if key in declarations and entry.costly_to_recompute is not None:
            raise RuntimeError(
                f"Operation {key!r} carries both an explicit costly_to_recompute on the entry "
                "and an entry in _RECOMPUTE_COST_DECLARATIONS."
            )
    resolved = _resolve_recompute_facts(entries, declarations)
    for entry in resolved:
        op_key = (entry.receiver.value, entry.namespace, entry.name)
        if type(entry.costly_to_recompute) is not bool:
            raise RuntimeError(
                f"Operation {op_key!r} has resolved "
                f"costly_to_recompute={entry.costly_to_recompute!r}, expected bool."
            )
        if type(entry.slice_transparent) is not bool:
            raise RuntimeError(
                f"Operation {op_key!r} has resolved "
                f"slice_transparent={entry.slice_transparent!r}, expected bool."
            )


def validate_operations(entries: tuple[PolarsOperation, ...] = _ENTRIES) -> None:
    """Fail loudly at import if a registry entry contradicts its class.

    ``expansion`` is permitted on :attr:`OperationClass.OPAQUE` as well as
    :attr:`OperationClass.ROW_EXPANDING`: a user-callback operation is both
    unknown in effect and able to change the row count.
    """
    seen: set[tuple[str, str | None, str]] = set()
    for entry in entries:
        key = (entry.receiver.value, entry.namespace, entry.name)
        if key in seen:
            raise RuntimeError(f"Duplicate Polars operation registration: {key!r}")
        seen.add(key)
        if entry.chunk_admitted and entry.operation_class is not OperationClass.ROW_LOCAL:
            raise RuntimeError(
                f"Chunk-admitted operation {key!r} must be row-local, "
                f"not {entry.operation_class.value!r}."
            )
        if (
            entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY
            and entry.operation_class in _ROW_LOCAL_OR_OPAQUE_CLASSES
        ):
            raise RuntimeError(
                f"Materialisation-boundary operation {key!r} must be order-dependent, "
                f"row-expanding, or fan-in/stateful, not {entry.operation_class.value!r}."
            )
        factor = entry.materialisation_factor_basis_points
        if not isinstance(factor, int) or isinstance(factor, bool) or factor < 100:
            raise RuntimeError(
                f"Operation {key!r} declares materialisation_factor_basis_points={factor!r}; "
                "the operator memory factor is a whole multiple of the base estimate and "
                "can never shrink it below 100 basis points."
            )
        if entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY and (
            entry.memory_evidence != "measured"
        ):
            raise RuntimeError(
                f"Materialisation-boundary operation {key!r} declares "
                f"memory_evidence={entry.memory_evidence!r}. A boundary policy is an "
                "evidence claim: it may only be set from a measured peak."
            )
        if entry.policy is not OperationPolicy.MATERIALISATION_BOUNDARY and factor != 100:
            raise RuntimeError(
                f"Operation {key!r} is not a materialisation boundary, so it must not carry "
                f"an operator memory factor (got {factor})."
            )
        if entry.expansion != "none" and entry.operation_class not in _EXPANSION_ALLOWED_CLASSES:
            raise RuntimeError(
                f"Operation {key!r} declares expansion {entry.expansion!r} but its class "
                f"{entry.operation_class.value!r} cannot expand rows."
            )
        if not entry.note:
            raise RuntimeError(f"Operation {key!r} must carry a one-line rationale.")


validate_operations()
validate_recompute_declarations()


POLARS_OPERATIONS: Mapping[tuple[str, str | None, str], PolarsOperation] = MappingProxyType(
    {
        (entry.receiver.value, entry.namespace, entry.name): entry
        for entry in _resolve_recompute_facts(_ENTRIES)
    }
)


def operation(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None = None,
) -> PolarsOperation | None:
    """Return the registered operation, or ``None`` when the name is unknown."""
    return POLARS_OPERATIONS.get((receiver.value, namespace, name))


def registered_names(
    receiver: OperationReceiver,
    namespace: str | None = None,
) -> frozenset[str]:
    """Return every registered name for one receiver (and namespace)."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is receiver and entry.namespace == namespace
    )


def chunk_admitted_names(
    receiver: OperationReceiver,
    namespace: str | None = None,
) -> frozenset[str]:
    """Return the names with a chunked==full proof for one receiver."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.chunk_admitted and entry.receiver is receiver and entry.namespace == namespace
    )


def unbounded_expansion_expression_methods() -> frozenset[str]:
    """Return the ``Expr`` methods whose result can outgrow the input frame."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is OperationReceiver.EXPR and entry.expansion == "unbounded"
    )


def materialising_frame_methods() -> frozenset[str]:
    """Return the frame methods the planner must place at a materialisation boundary."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is OperationReceiver.FRAME
        and entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY
    )


def measured_operation_names(receiver: OperationReceiver) -> frozenset[str]:
    """Return the names whose memory policy comes from a measured peak.

    The certification lane derives its candidate set from this, so an operation
    can never claim measured evidence the lane does not actually re-measure.
    """
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is receiver and entry.memory_evidence == "measured"
    )


def materialising_expression_methods() -> frozenset[str]:
    """Return the ``Expr`` methods that materialise the frame they run over.

    A window expression has no frame receiver, so the planner matches these by
    attribute anywhere in a node's expressions rather than by receiver fact.
    """
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is OperationReceiver.EXPR
        and entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY
    )


def costly_frame_methods() -> frozenset[str]:
    """Return the frame methods that are costly to recompute."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is OperationReceiver.FRAME
        and entry.namespace is None
        and entry.costly_to_recompute
    )


def costly_expression_methods() -> frozenset[str]:
    """Return the expression methods that are costly to recompute."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is OperationReceiver.EXPR
        and entry.namespace is None
        and entry.costly_to_recompute
    )


def recompute_cost(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None = None,
) -> bool | None:
    """Return whether the operation is costly to recompute, or None if unregistered."""
    entry = operation(receiver, name, namespace)
    return entry.costly_to_recompute if entry is not None else None


def slice_transparent(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None = None,
) -> bool | None:
    """Return whether Polars pushes a slice through this operation, or None if unregistered."""
    entry = operation(receiver, name, namespace)
    return entry.slice_transparent if entry is not None else None


def full_input_work(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None = None,
) -> bool | None:
    """Return whether the operation is full-input work, or None if unregistered."""
    entry = operation(receiver, name, namespace)
    if entry is None:
        return None
    return bool(entry.costly_to_recompute and entry.operation_class is not OperationClass.OPAQUE)


def materialisation_factor_basis_points(operator: str) -> int:
    """Return the operator memory factor the estimator multiplies its estimate by.

    ``operator`` is a boundary operator name as the planner records it, so a
    frame method is looked up first and an expression method second. An
    unregistered name carries no surcharge.
    """
    for receiver in (OperationReceiver.FRAME, OperationReceiver.EXPR):
        entry = operation(receiver, operator)
        if entry is not None and entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY:
            return entry.materialisation_factor_basis_points
    return 100


def lineage_supported_frame_methods() -> frozenset[str]:
    """Return the frame methods the lineage program model has a transfer for."""
    return frozenset(
        entry.name
        for entry in POLARS_OPERATIONS.values()
        if entry.receiver is OperationReceiver.FRAME and entry.lineage_supported
    )


@dataclass(frozen=True, slots=True)
class SelectorForm:
    """One Polars column-selector constructor the selector grammar rebuilds.

    ``dtype_dependent`` marks a form that chooses columns by dtype (``exclude``
    and ``col`` decide it from their arguments), ``positional`` a form that
    chooses by position, and ``chunk_admitted`` a form with a chunked==full
    proof case in tests/test_chunk_whitelist_proofs.py.
    """

    name: str
    dtype_dependent: bool
    positional: bool
    chunk_admitted: bool
    note: str


def _selector(
    name: str,
    note: str,
    *,
    dtype_dependent: bool = False,
    positional: bool = False,
    chunk_admitted: bool = True,
) -> SelectorForm:
    return SelectorForm(name, dtype_dependent, positional, chunk_admitted, note)


# ``pl.<name>`` constructors that select columns. ``first``/``last`` select only
# without arguments; with arguments they are order-dependent aggregations and
# stay registered as opaque functions above.
POLARS_SELECTOR_CONSTRUCTORS: Mapping[str, SelectorForm] = MappingProxyType(
    {
        form.name: form
        for form in (
            _selector("all", "proof: selector_all"),
            _selector("exclude", "proof: selector_exclude; dtype arguments read dtypes"),
            _selector("nth", "proof: selector_nth", positional=True),
            _selector("first", "proof: selector_first; argument-free only", positional=True),
            _selector("last", "proof: selector_last; argument-free only", positional=True),
            _selector("col", "proof: selector_col_regex; regex, wildcard, or dtype argument only"),
        )
    }
)

_NAME_SELECTOR_FUNCTIONS = (
    "all",
    "alpha",
    "alphanumeric",
    "by_name",
    "contains",
    "digit",
    "empty",
    "ends_with",
    "exclude",
    "matches",
    "starts_with",
)
_POSITIONAL_SELECTOR_FUNCTIONS = ("by_index", "first", "last")
_DTYPE_SELECTOR_FUNCTIONS = (
    "array",
    "binary",
    "boolean",
    "by_dtype",
    "categorical",
    "date",
    "datetime",
    "decimal",
    "duration",
    "enum",
    "float",
    "integer",
    "list",
    "nested",
    "numeric",
    "object",
    "signed_integer",
    "string",
    "struct",
    "temporal",
    "time",
    "unsigned_integer",
)

# ``polars.selectors`` functions, reached through a preamble import alias.
# Chunk admission for the whole module rests on one proof case: every selector
# expands against the schema, which every chunk shares.
POLARS_SELECTORS_MODULE_FUNCTIONS: Mapping[str, SelectorForm] = MappingProxyType(
    {
        **{
            name: _selector(name, "proof: selector_module", chunk_admitted=True)
            for name in _NAME_SELECTOR_FUNCTIONS
        },
        **{
            name: _selector(name, "proof: selector_module", positional=True)
            for name in _POSITIONAL_SELECTOR_FUNCTIONS
        },
        **{
            name: _selector(name, "proof: selector_module", dtype_dependent=True)
            for name in _DTYPE_SELECTOR_FUNCTIONS
        },
    }
)


def chunk_admitted_selector_constructors() -> frozenset[str]:
    """Return the ``pl`` selector constructors with a chunked==full proof."""
    return frozenset(
        name for name, form in POLARS_SELECTOR_CONSTRUCTORS.items() if form.chunk_admitted
    )
