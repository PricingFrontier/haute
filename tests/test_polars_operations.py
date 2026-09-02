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
    chunk_admitted_names,
    lineage_supported_frame_methods,
    materialising_frame_methods,
    operation,
    registered_names,
    unbounded_expansion_expression_methods,
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
            "must be fan-in/stateful",
            id="boundary_non_fan_in",
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


def test_chunking_derived_sets_equal_registry_admissions() -> None:
    assert _ROW_LOCAL_DF_METHOD_NAMES == chunk_admitted_names(OperationReceiver.FRAME)
    assert _ROW_LOCAL_EXPR_METHOD_NAMES == chunk_admitted_names(OperationReceiver.EXPR)
    assert _ROW_LOCAL_POLARS_FUNCTIONS == chunk_admitted_names(OperationReceiver.POLARS_FUNCTION)
    assert dict(_ROW_LOCAL_NAMESPACE_METHOD_NAMES) == {
        namespace: chunk_admitted_names(OperationReceiver.NAMESPACE, namespace)
        for namespace in ("str", "dt")
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


def test_lineage_unbounded_expansion_set_comes_from_the_registry() -> None:
    assert _ROW_EXPANDING_EXPRESSION_METHODS == unbounded_expansion_expression_methods()


def test_group_by_is_the_only_materialisation_boundary() -> None:
    assert materialising_frame_methods() == {"group_by", "groupby"}


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
