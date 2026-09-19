"""The shared Polars operation registry is the single classification authority."""

from __future__ import annotations

import pytest

from haute._column_lineage import (
    _LINEAGE_FRAME_METHODS,
    _ROW_EXPANDING_EXPRESSION_METHODS,
    analyze_polars_cardinality,
    analyze_polars_lineage,
)
from haute._polars_operations import (
    _RECOMPUTE_COST_DECLARATIONS,
    POLARS_OPERATIONS,
    OperationClass,
    OperationPolicy,
    OperationReceiver,
    PolarsOperation,
    _op,
    _resolve_recompute_facts,
    costly_expression_methods,
    costly_frame_methods,
    full_input_work,
    lineage_supported_frame_methods,
    materialisation_factor_basis_points,
    materialising_expression_methods,
    materialising_frame_methods,
    measured_operation_names,
    operation,
    recompute_cost,
    registered_names,
    slice_transparent,
    validate_operations,
    validate_recompute_declarations,
)
from haute.chunking import (
    _ROW_LOCAL_DF_METHOD_NAMES,
    _ROW_LOCAL_EXPR_METHOD_NAMES,
    _ROW_LOCAL_NAMESPACE_METHOD_NAMES,
    _ROW_LOCAL_POLARS_FUNCTIONS,
    classify_chunk_local_polars_code,
)


def _entry(**overrides: object) -> PolarsOperation:
    fields: dict[str, object] = {
        "receiver": OperationReceiver.FRAME,
        "name": "probe",
        "namespace": None,
        "operation_class": OperationClass.ROW_LOCAL,
        "policy": OperationPolicy.ROW_LOCAL,
        "expansion": "none",
        "chunk_admitted": False,
        "lineage_supported": False,
        "materialisation_factor_basis_points": 100,
        "memory_evidence": "none",
        "note": "probe entry",
    }
    fields.update(overrides)
    return PolarsOperation(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------- structure


def test_registry_is_frozen_and_keyed_by_receiver_namespace_and_name() -> None:
    with pytest.raises(TypeError):
        POLARS_OPERATIONS["frame", None, "nope"] = _entry()  # type: ignore[index]
    for key, entry in POLARS_OPERATIONS.items():
        assert key == (entry.receiver.value, entry.namespace, entry.name)
    assert len(POLARS_OPERATIONS) == len(set(POLARS_OPERATIONS))


def test_same_name_can_carry_different_classes_per_receiver() -> None:
    expr_sort = operation(OperationReceiver.EXPR, "sort")
    frame_sort = operation(OperationReceiver.FRAME, "sort")
    assert expr_sort is not None and frame_sort is not None
    assert expr_sort.operation_class is OperationClass.ORDER_DEPENDENT
    assert frame_sort.operation_class is OperationClass.ORDER_DEPENDENT
    # A namespace name is only found under its namespace.
    assert operation(OperationReceiver.NAMESPACE, "sort", "list") is None
    assert operation(OperationReceiver.NAMESPACE, "explode", "list") is not None
    assert operation(OperationReceiver.NAMESPACE, "explode") is None
    assert "explode" in registered_names(OperationReceiver.NAMESPACE, "list")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param(
            {"operation_class": OperationClass.ORDER_DEPENDENT, "chunk_admitted": True},
            "must be row-local",
            id="chunk_admitted_non_row_local",
        ),
        pytest.param(
            {"policy": OperationPolicy.MATERIALISATION_BOUNDARY},
            "must be order-dependent, row-expanding, or fan-in/stateful",
            id="boundary_on_row_local_class",
        ),
        pytest.param(
            {
                "operation_class": OperationClass.FAN_IN_STATEFUL,
                "policy": OperationPolicy.MATERIALISATION_BOUNDARY,
            },
            "may only be set from a measured peak",
            id="boundary_without_measured_evidence",
        ),
        pytest.param(
            {
                "operation_class": OperationClass.FAN_IN_STATEFUL,
                "policy": OperationPolicy.MATERIALISATION_BOUNDARY,
                "materialisation_factor_basis_points": 50,
                "memory_evidence": "measured",
            },
            "can never shrink it below 100 basis points",
            id="boundary_factor_below_base",
        ),
        pytest.param(
            {"materialisation_factor_basis_points": 200},
            "must not carry an operator memory factor",
            id="factor_on_streaming_policy",
        ),
        pytest.param(
            {"expansion": "unbounded"},
            "cannot expand rows",
            id="expansion_on_non_expanding_class",
        ),
        pytest.param({"note": ""}, "one-line rationale", id="missing_note"),
    ],
)
def test_validation_rejects_contradictory_entries(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        validate_operations((_entry(**overrides),))


def test_validation_rejects_duplicate_keys() -> None:
    with pytest.raises(RuntimeError, match="Duplicate Polars operation"):
        validate_operations((_entry(), _entry()))


# -------------------------------------------------------------- consistency


def test_chunking_derived_sets_are_pinned_to_literal_admissions() -> None:
    """Pinned literally: a registry change must update this test consciously."""
    assert _ROW_LOCAL_DF_METHOD_NAMES == frozenset(
        {
            "cast",
            "drop",
            "drop_nulls",
            "fill_nan",
            "fill_null",
            "filter",
            "rename",
            "select",
            "with_columns",
            "with_columns_seq",
        }
    )
    assert _ROW_LOCAL_EXPR_METHOD_NAMES == frozenset(
        {
            "abs",
            "alias",
            "cast",
            "ceil",
            "clip",
            "exp",
            "fill_nan",
            "fill_null",
            "floor",
            "is_between",
            "is_finite",
            "is_in",
            "is_infinite",
            "is_nan",
            "is_not_nan",
            "is_not_null",
            "is_null",
            "log",
            "not_",
            "otherwise",
            "replace",
            "round",
            "sqrt",
            "then",
        }
    )
    assert _ROW_LOCAL_POLARS_FUNCTIONS == frozenset(
        {
            "all_horizontal",
            "any_horizontal",
            "coalesce",
            "col",
            "concat_str",
            "lit",
            "max_horizontal",
            "mean_horizontal",
            "sum_horizontal",
            "when",
        }
    )
    assert dict(_ROW_LOCAL_NAMESPACE_METHOD_NAMES) == {
        "str": frozenset(
            {
                "contains",
                "ends_with",
                "extract",
                "len_chars",
                "pad_end",
                "pad_start",
                "replace",
                "replace_all",
                "slice",
                "split",
                "starts_with",
                "strip_chars",
                "strip_prefix",
                "strip_suffix",
                "strptime",
                "to_date",
                "to_datetime",
                "to_lowercase",
                "to_time",
                "to_uppercase",
                "zfill",
            }
        ),
        "dt": frozenset(
            {
                "date",
                "day",
                "epoch",
                "hour",
                "minute",
                "month",
                "offset_by",
                "ordinal_day",
                "quarter",
                "second",
                "strftime",
                "to_string",
                "truncate",
                "weekday",
                "year",
            }
        ),
    }


def test_every_chunk_admitted_entry_is_row_local() -> None:
    for entry in POLARS_OPERATIONS.values():
        if entry.chunk_admitted:
            assert entry.operation_class is OperationClass.ROW_LOCAL
            assert entry.policy is OperationPolicy.ROW_LOCAL
            assert entry.expansion == "none"


def test_row_bound_polars_functions_are_not_conflated_with_row_local() -> None:
    """Output rows <= input rows is not the same property as row-locality."""
    for name in ("len", "arg_sort_by", "arg_where"):
        entry = operation(OperationReceiver.POLARS_FUNCTION, name)
        assert entry is not None, name
        assert entry.operation_class is not OperationClass.ROW_LOCAL, name
        assert entry.policy is OperationPolicy.STREAMING, name
        assert not entry.chunk_admitted, name

    assert operation(OperationReceiver.POLARS_FUNCTION, "len").operation_class is (  # type: ignore[union-attr]
        OperationClass.FAN_IN_STATEFUL
    )
    for name in ("arg_sort_by", "arg_where"):
        assert operation(OperationReceiver.POLARS_FUNCTION, name).operation_class is (  # type: ignore[union-attr]
            OperationClass.ORDER_DEPENDENT
        )


_REDUCTION_FUNCTION_NAMES = frozenset({"len", "count", "sum", "mean", "min", "max", "n_unique"})


def test_no_reduction_or_arg_polars_function_is_classified_row_local() -> None:
    for entry in POLARS_OPERATIONS.values():
        if entry.receiver is not OperationReceiver.POLARS_FUNCTION:
            continue
        if entry.name in _REDUCTION_FUNCTION_NAMES or entry.name.startswith("arg_"):
            assert entry.operation_class is not OperationClass.ROW_LOCAL, entry.name


def test_lineage_unbounded_expansion_set_is_pinned_to_literal_names() -> None:
    """Pinned literally: a registry change must update this test consciously."""
    assert _ROW_EXPANDING_EXPRESSION_METHODS == frozenset(
        {
            "append",
            "deserialize",
            "explode",
            "extend_constant",
            "flatten",
            "from_json",
            "gather",
            "hist",
            "map_batches",
            "pipe",
            "register_plugin",
            "sample",
            "search_sorted",
        }
    )


def test_materialisation_boundary_frame_methods_are_the_measured_global_operations() -> None:
    """EXEC-P07 admits exactly the frame methods measured to materialise."""
    assert materialising_frame_methods() == {
        "bottom_k",
        "explode",
        "group_by",
        "groupby",
        "join",
        "join_asof",
        "reverse",
        "shift",
        "sort",
        "top_k",
        "unique",
    }


def test_measured_streaming_global_operations_are_not_boundaries() -> None:
    """Operations measured at or below the streaming floor keep streaming."""
    for name in (
        "unpivot",
        "melt",
        "rolling",
        "group_by_dynamic",
        "merge_sorted",
        "interpolate",
        "filter",
        "join_where",
        "pivot",
        "upsample",
        "gather",
        "sample",
    ):
        entry = operation(OperationReceiver.FRAME, name)
        assert entry is not None, name
        assert entry.policy is not OperationPolicy.MATERIALISATION_BOUNDARY, name


def test_windows_and_neighbouring_row_methods_are_the_boundary_expression_methods() -> None:
    assert materialising_expression_methods() == {"over", "shift", "diff", "pct_change"}


def test_expression_namespace_names_are_polars_expression_namespaces() -> None:
    import polars as pl

    from haute._polars_operations import EXPRESSION_NAMESPACE_NAMES

    expression = pl.col("a")
    for name in EXPRESSION_NAMESPACE_NAMES:
        namespace = getattr(expression, name)
        assert not callable(namespace), name
    assert {"list", "arr"} <= EXPRESSION_NAMESPACE_NAMES
    assert hasattr(expression.list, "shift") and hasattr(expression.list, "diff")
    assert hasattr(expression.arr, "shift")


def test_boundary_operator_memory_factors_are_pinned_to_the_evidence() -> None:
    """The factors come from measured peaks; changing one must be deliberate."""
    assert {
        name: materialisation_factor_basis_points(name)
        for name in (
            *materialising_frame_methods(),
            *materialising_expression_methods(),
        )
    } == {
        "sort": 300,
        "unique": 350,
        "join": 200,
        "join_asof": 250,
        "over": 250,
        "top_k": 100,
        "bottom_k": 100,
        "reverse": 250,
        "shift": 100,
        "diff": 100,
        "pct_change": 150,
        "group_by": 100,
        "groupby": 100,
        # explode's estimate is unavailable, so its factor is never applied.
        "explode": 100,
    }


def test_every_boundary_entry_carries_an_evidence_class_in_its_note() -> None:
    for entry in POLARS_OPERATIONS.values():
        if entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY:
            assert "materialises:" in entry.note or "materialisation boundary" in entry.note, (
                entry.name
            )


def test_streaming_frame_methods_record_evidence_or_its_absence() -> None:
    """Every global operation EXEC-P07 measured cites its evidence class."""
    for name in (
        "unpivot",
        "melt",
        "rolling",
        "group_by_dynamic",
        "merge_sorted",
        "interpolate",
        "filter",
        "join_where",
        "pivot",
        "upsample",
        "gather",
        "sample",
    ):
        entry = operation(OperationReceiver.FRAME, name)
        assert entry is not None, name
        assert entry.policy is OperationPolicy.STREAMING or entry.policy is (
            OperationPolicy.ROW_LOCAL
        ), name
        assert (
            "streams:" in entry.note
            or "no evidence, streaming kept" in entry.note
            or "measured as unpivot" in entry.note
        ), entry.name


def test_unregistered_operator_carries_no_memory_surcharge() -> None:
    assert materialisation_factor_basis_points("with_columns") == 100
    assert materialisation_factor_basis_points("not_a_polars_operation") == 100


def test_lineage_supported_frame_methods_match_the_parser_vocabulary() -> None:
    assert lineage_supported_frame_methods() == _LINEAGE_FRAME_METHODS


# --------------------------------------------- analysers agree with registry


def test_row_local_admitted_frame_method_is_chunk_eligible() -> None:
    entry = operation(OperationReceiver.FRAME, "with_columns")
    assert entry is not None and entry.chunk_admitted
    decision = classify_chunk_local_polars_code(
        "df = df.with_columns(pl.col('a').abs().alias('b'))",
        frame_names=["df"],
    )
    assert decision.eligible


def test_order_dependent_frame_method_is_chunk_rejected_but_lineage_supported() -> None:
    entry = operation(OperationReceiver.FRAME, "sort")
    assert entry is not None
    assert entry.operation_class is OperationClass.ORDER_DEPENDENT
    assert not entry.chunk_admitted
    assert entry.lineage_supported

    decision = classify_chunk_local_polars_code("df = df.sort('a')", frame_names=["df"])
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"

    analysis = analyze_polars_lineage("df = df.sort('a')", {"df": frozenset({"a", "b"})})
    assert analysis.supported


def test_row_expanding_expression_method_makes_cardinality_unavailable() -> None:
    entry = operation(OperationReceiver.EXPR, "explode")
    assert entry is not None
    assert entry.operation_class is OperationClass.ROW_EXPANDING
    assert entry.expansion == "unbounded"

    analysis = analyze_polars_cardinality("df = df.select(pl.col('a').explode())", {"df": 10})
    assert not analysis.supported
    assert analysis.reason == "row_expansion_unbounded"


def test_opaque_method_is_chunk_rejected_and_lineage_unsupported() -> None:
    entry = operation(OperationReceiver.FRAME, "collect")
    assert entry is not None
    assert entry.operation_class is OperationClass.OPAQUE
    assert entry.policy is OperationPolicy.OPAQUE
    assert not entry.chunk_admitted
    assert not entry.lineage_supported

    decision = classify_chunk_local_polars_code("df = df.collect()", frame_names=["df"])
    assert not decision.eligible
    assert decision.blocking_operator == "collect"

    analysis = analyze_polars_lineage("df = df.collect()", {"df": frozenset({"a"})})
    assert not analysis.supported
    assert analysis.unsupported_operation == "collect"


def test_every_measured_operation_is_named_by_the_evidence_accessor() -> None:
    """The certification lane derives its candidate set from this accessor."""
    assert measured_operation_names(OperationReceiver.FRAME) == {
        "sort",
        "unique",
        "join",
        "join_asof",
        "top_k",
        "bottom_k",
        "reverse",
        "explode",
        "unpivot",
        "rolling",
        "group_by_dynamic",
        "shift",
        "merge_sorted",
        "interpolate",
        "filter",
        "group_by",
        "groupby",
        "melt",
    }
    assert measured_operation_names(OperationReceiver.EXPR) == {
        "over",
        "shift",
        "diff",
        "pct_change",
    }


def test_every_materialisation_boundary_carries_measured_evidence() -> None:
    """A boundary policy is an evidence claim, so it cannot be asserted."""
    for entry in POLARS_OPERATIONS.values():
        if entry.policy is OperationPolicy.MATERIALISATION_BOUNDARY:
            assert entry.memory_evidence == "measured", entry.name


def test_unmeasured_operations_declare_no_evidence() -> None:
    for name in ("join_where", "pivot", "upsample", "gather", "sample"):
        entry = operation(OperationReceiver.FRAME, name)
        assert entry is not None, name
        assert entry.memory_evidence == "none", name


# --------------------------------------------- recompute cost and transparency


def test_every_operation_declares_recompute_cost_and_slice_transparency() -> None:
    for entry in POLARS_OPERATIONS.values():
        assert type(entry.costly_to_recompute) is bool
        assert type(entry.slice_transparent) is bool

    for op_class in (
        OperationClass.ORDER_DEPENDENT,
        OperationClass.FAN_IN_STATEFUL,
        OperationClass.OPAQUE,
    ):
        policy = (
            OperationPolicy.OPAQUE
            if op_class is OperationClass.OPAQUE
            else OperationPolicy.STREAMING
        )
        fresh = _op(
            OperationReceiver.EXPR,
            "fresh_probe_op",
            op_class,
            policy,
            "fresh probe note",
        )
        with pytest.raises(
            RuntimeError,
            match="order-dependent, fan-in, and opaque operations must declare",
        ):
            _resolve_recompute_facts((fresh,))

    bad_declarations = dict(_RECOMPUTE_COST_DECLARATIONS)
    bad_declarations[(OperationReceiver.FRAME, None, "stale_missing_entry")] = True
    with pytest.raises(RuntimeError, match="names no existing Polars operation"):
        validate_recompute_declarations(declarations=bad_declarations)


@pytest.mark.parametrize(
    ("receiver", "name", "namespace", "expected"),
    [
        # Cheap examples
        (OperationReceiver.FRAME, "explode", None, False),
        (OperationReceiver.FRAME, "reverse", None, False),
        (OperationReceiver.FRAME, "shift", None, False),
        (OperationReceiver.EXPR, "diff", None, False),
        (OperationReceiver.EXPR, "pct_change", None, False),
        (OperationReceiver.EXPR, "cum_sum", None, False),
        (OperationReceiver.FRAME, "head", None, False),
        (OperationReceiver.FRAME, "slice", None, False),
        (OperationReceiver.EXPR, "sum", None, False),
        (OperationReceiver.POLARS_FUNCTION, "all", None, False),
        # Costly examples
        (OperationReceiver.FRAME, "group_by", None, True),
        (OperationReceiver.FRAME, "sort", None, True),
        (OperationReceiver.EXPR, "sort", None, True),
        (OperationReceiver.EXPR, "sort_by", None, True),
        (OperationReceiver.POLARS_FUNCTION, "arg_sort_by", None, True),
        (OperationReceiver.EXPR, "rank", None, True),
        (OperationReceiver.FRAME, "unique", None, True),
        (OperationReceiver.EXPR, "unique", None, True),
        (OperationReceiver.EXPR, "n_unique", None, True),
        (OperationReceiver.FRAME, "top_k", None, True),
        (OperationReceiver.FRAME, "bottom_k", None, True),
        (OperationReceiver.FRAME, "join", None, True),
        (OperationReceiver.FRAME, "join_asof", None, True),
        (OperationReceiver.EXPR, "over", None, True),
        (OperationReceiver.FRAME, "pivot", None, True),
        (OperationReceiver.FRAME, "rolling", None, True),
        (OperationReceiver.EXPR, "median", None, True),
        (OperationReceiver.EXPR, "map_elements", None, True),
    ],
)
def test_recompute_cost_examples(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None,
    expected: bool,
) -> None:
    assert recompute_cost(receiver, name, namespace) is expected
    if expected and namespace is None:
        if receiver is OperationReceiver.FRAME:
            assert name in costly_frame_methods()
        elif receiver is OperationReceiver.EXPR:
            assert name in costly_expression_methods()


def test_recompute_cost_is_independent_of_memory_policy() -> None:
    explode = operation(OperationReceiver.FRAME, "explode")
    assert explode is not None
    assert explode.policy is OperationPolicy.MATERIALISATION_BOUNDARY
    assert explode.costly_to_recompute is False

    expr_sort = operation(OperationReceiver.EXPR, "sort")
    assert expr_sort is not None
    assert expr_sort.policy is OperationPolicy.STREAMING
    assert expr_sort.costly_to_recompute is True

    for receiver, name in (
        (OperationReceiver.FRAME, "explode"),
        (OperationReceiver.FRAME, "reverse"),
        (OperationReceiver.FRAME, "shift"),
        (OperationReceiver.EXPR, "diff"),
        (OperationReceiver.EXPR, "pct_change"),
    ):
        entry = operation(receiver, name)
        assert entry is not None, f"{receiver}.{name}"
        assert entry.costly_to_recompute is False
        assert not entry.chunk_admitted

    for name in ("head", "slice", "with_row_index"):
        entry = operation(OperationReceiver.FRAME, name)
        assert entry is not None, f"frame.{name}"
        assert not entry.chunk_admitted


_TRANSPARENT_CASES: list[tuple[OperationReceiver, str, str | None, bool]] = [
    # Transparent
    (OperationReceiver.FRAME, "select", None, True),
    (OperationReceiver.FRAME, "with_columns", None, True),
    (OperationReceiver.FRAME, "rename", None, True),
    (OperationReceiver.FRAME, "drop", None, True),
    (OperationReceiver.FRAME, "cast", None, True),
    (OperationReceiver.FRAME, "fill_null", None, True),
    (OperationReceiver.FRAME, "fill_nan", None, True),
    (OperationReceiver.FRAME, "unnest", None, True),
    (OperationReceiver.POLARS_FUNCTION, "col", None, True),
    (OperationReceiver.POLARS_FUNCTION, "lit", None, True),
    (OperationReceiver.POLARS_FUNCTION, "when", None, True),
    (OperationReceiver.POLARS_FUNCTION, "concat_str", None, True),
    (OperationReceiver.POLARS_FUNCTION, "all", None, True),
    (OperationReceiver.POLARS_FUNCTION, "first", None, True),
    (OperationReceiver.POLARS_FUNCTION, "last", None, True),
    (OperationReceiver.POLARS_FUNCTION, "nth", None, True),
    (OperationReceiver.POLARS_FUNCTION, "exclude", None, True),
    (OperationReceiver.EXPR, "alias", None, True),
    (OperationReceiver.EXPR, "cast", None, True),
    (OperationReceiver.EXPR, "is_in", None, True),
    (OperationReceiver.NAMESPACE, "to_lowercase", "str", True),
    (OperationReceiver.NAMESPACE, "year", "dt", True),
    # Not transparent
    (OperationReceiver.FRAME, "filter", None, False),
    (OperationReceiver.FRAME, "drop_nulls", None, False),
    (OperationReceiver.FRAME, "explode", None, False),
    (OperationReceiver.FRAME, "head", None, False),
    (OperationReceiver.FRAME, "sample", None, False),
    (OperationReceiver.FRAME, "with_row_index", None, False),
    (OperationReceiver.EXPR, "over", None, False),
    (OperationReceiver.EXPR, "shift", None, False),
    (OperationReceiver.EXPR, "first", None, False),
    (OperationReceiver.EXPR, "sum", None, False),
    (OperationReceiver.POLARS_FUNCTION, "len", None, False),
]


@pytest.mark.parametrize(
    ("receiver", "name", "namespace", "expected"),
    _TRANSPARENT_CASES,
)
def test_slice_transparent_examples(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None,
    expected: bool,
) -> None:
    assert slice_transparent(receiver, name, namespace) is expected


@pytest.mark.parametrize(
    ("receiver", "name", "namespace", "expected"),
    [
        # Full-input work
        (OperationReceiver.FRAME, "sort", None, True),
        (OperationReceiver.EXPR, "sort", None, True),
        (OperationReceiver.FRAME, "unique", None, True),
        (OperationReceiver.EXPR, "unique", None, True),
        (OperationReceiver.EXPR, "rank", None, True),
        (OperationReceiver.EXPR, "n_unique", None, True),
        (OperationReceiver.EXPR, "value_counts", None, True),
        (OperationReceiver.FRAME, "group_by", None, True),
        (OperationReceiver.FRAME, "join", None, True),
        (OperationReceiver.EXPR, "over", None, True),
        (OperationReceiver.FRAME, "pivot", None, True),
        (OperationReceiver.FRAME, "rolling", None, True),
        (OperationReceiver.EXPR, "median", None, True),
        # Not full-input work
        (OperationReceiver.EXPR, "map_elements", None, False),
        (OperationReceiver.EXPR, "map_batches", None, False),
        (OperationReceiver.FRAME, "pipe", None, False),
        (OperationReceiver.FRAME, "explode", None, False),
        (OperationReceiver.FRAME, "shift", None, False),
        (OperationReceiver.EXPR, "sum", None, False),
    ],
)
def test_full_input_work_examples(
    receiver: OperationReceiver,
    name: str,
    namespace: str | None,
    expected: bool,
) -> None:
    assert full_input_work(receiver, name, namespace) is expected


def test_full_input_work_returns_none_for_unregistered_name() -> None:
    assert full_input_work(OperationReceiver.FRAME, "unregistered_op") is None
    assert recompute_cost(OperationReceiver.FRAME, "unregistered_op") is None
    assert slice_transparent(OperationReceiver.FRAME, "unregistered_op") is None
