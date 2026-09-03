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
    POLARS_OPERATIONS,
    OperationClass,
    OperationPolicy,
    OperationReceiver,
    PolarsOperation,
    lineage_supported_frame_methods,
    materialisation_factor_basis_points,
    materialising_expression_methods,
    materialising_frame_methods,
    measured_operation_names,
    operation,
    registered_names,
    validate_operations,
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
        "shift",
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


def test_over_is_the_only_materialisation_boundary_expression_method() -> None:
    assert materialising_expression_methods() == {"over"}


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
        "shift",
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
    assert measured_operation_names(OperationReceiver.EXPR) == {"over"}


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
