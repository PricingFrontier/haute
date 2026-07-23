from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import polars as pl
import pytest

from haute._json_safe import non_finite_float_sentinel, row_to_json_safe
from haute._trace_correlation import (
    SchemaDiff,
    _CandidateIndicesState,
    _find_matching_row,
    _jsonify_row,
    _match_rows_vectorized,
    _RowMatchStatus,
)
from haute._trace_enrichment import _build_input_sources
from haute.errors import TraceCorrelationUnsupportedError
from haute.trace import TraceStep, _find_target_row_index


@pytest.mark.parametrize(
    ("count", "status", "state"),
    [
        (0, _RowMatchStatus.NO_MATCH, _CandidateIndicesState.AVAILABLE),
        (1, _RowMatchStatus.UNIQUE_STRICT, _CandidateIndicesState.AVAILABLE),
        (16, _RowMatchStatus.AMBIGUOUS, _CandidateIndicesState.AVAILABLE),
        (17, _RowMatchStatus.AMBIGUOUS, _CandidateIndicesState.TRUNCATED),
    ],
)
def test_vectorized_match_candidate_payload_is_exact_and_bounded(
    count: int,
    status: _RowMatchStatus,
    state: _CandidateIndicesState,
) -> None:
    frame = pl.DataFrame({"key": pl.Series([1] * count, dtype=pl.Int64)})

    result = _match_rows_vectorized(frame, {"key": 1}, ["key"])

    assert result.status is status
    assert result.candidate_count == count
    assert result.candidate_indices == tuple(range(min(count, 16)))
    assert result.candidate_indices_state is state


def test_vectorized_match_never_scans_rows_in_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pl.DataFrame({"key": list(range(10_000))})

    def fail_iter_rows(*_args, **_kwargs):
        raise AssertionError("correlation must not iterate a frame in Python")

    monkeypatch.setattr(pl.DataFrame, "iter_rows", fail_iter_rows)

    result = _match_rows_vectorized(frame, {"key": 9_999}, ["key"])

    assert result.status is _RowMatchStatus.UNIQUE_STRICT
    assert result.candidate_indices == (9_999,)


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        (0.0, 1e-12, True),
        (0.0, 1.0000001e-12, False),
        (1_000.0, 1_000.000001, True),
        (1_000.0, 1_000.0000011, False),
        (-1_000.0, -1_000.000001, True),
    ],
)
def test_finite_numeric_match_uses_one_pinned_tolerance_formula(
    actual: float,
    expected: float,
    matches: bool,
) -> None:
    result = _match_rows_vectorized(pl.DataFrame({"x": [actual]}), {"x": expected}, ["x"])
    assert (result.status is _RowMatchStatus.UNIQUE_STRICT) is matches


def test_boolean_is_not_numeric() -> None:
    result = _match_rows_vectorized(pl.DataFrame({"x": [True]}), {"x": 1}, ["x"])
    assert result.status is _RowMatchStatus.UNSUPPORTED_DTYPE
    assert result.candidate_count is None
    assert result.candidate_indices == ()
    assert result.candidate_indices_state is _CandidateIndicesState.UNAVAILABLE


def test_integer_float_boundary_and_unsafe_integer_string_contract() -> None:
    boundary = 2**53
    assert (
        _match_rows_vectorized(
            pl.DataFrame({"x": pl.Series([boundary], dtype=pl.Int64)}),
            {"x": float(boundary)},
            ["x"],
        ).status
        is _RowMatchStatus.UNIQUE_STRICT
    )
    assert (
        _match_rows_vectorized(
            pl.DataFrame({"x": pl.Series([boundary + 1], dtype=pl.Int64)}),
            {"x": float(boundary + 1)},
            ["x"],
        ).status
        is _RowMatchStatus.UNSUPPORTED_DTYPE
    )
    frame = pl.DataFrame({"x": pl.Series([boundary + 1], dtype=pl.Int64)})
    assert (
        _match_rows_vectorized(frame, {"x": str(boundary + 1)}, ["x"]).status
        is _RowMatchStatus.UNIQUE_STRICT
    )
    for malformed in (f"+{boundary + 1}", f"0{boundary + 1}", f" {boundary + 1}"):
        assert (
            _match_rows_vectorized(frame, {"x": malformed}, ["x"]).status
            is _RowMatchStatus.NO_MATCH
        )


@pytest.mark.parametrize(
    ("actual", "token", "matches"),
    [
        (float("nan"), "nan", True),
        (float("inf"), "inf", True),
        (float("-inf"), "-inf", True),
        (float("inf"), "-inf", False),
        (1.0, "inf", False),
    ],
)
def test_non_finite_values_match_only_their_canonical_sentinel(
    actual: float,
    token: str,
    matches: bool,
) -> None:
    expected = non_finite_float_sentinel(
        {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}[token]
    )
    result = _match_rows_vectorized(pl.DataFrame({"x": [actual]}), {"x": expected}, ["x"])
    assert (result.status is _RowMatchStatus.UNIQUE_STRICT) is matches


def test_temporal_decimal_and_nested_values_use_typed_native_equality() -> None:
    aware = datetime(2026, 7, 22, 10, 30, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "day": pl.Series([date(2026, 7, 22)], dtype=pl.Date),
            "instant": pl.Series([aware], dtype=pl.Datetime("us", "UTC")),
            "amount": pl.Series([Decimal("1.23")], dtype=pl.Decimal(10, 2)),
            "items": pl.Series([[1, 2]], dtype=pl.List(pl.Int64)),
            "record": pl.Series([{"a": 1}], dtype=pl.Struct({"a": pl.Int64})),
        }
    )
    expected = {
        "day": "2026-07-22",
        "instant": "2026-07-22T10:30:00+00:00",
        "amount": "1.230",
        "items": [1, 2],
        "record": {"a": 1},
    }

    result = _match_rows_vectorized(frame, expected, list(expected))

    assert result.status is _RowMatchStatus.UNIQUE_STRICT


def test_aware_naive_datetime_and_decimal_float_are_unsupported() -> None:
    aware_frame = pl.DataFrame(
        {
            "instant": pl.Series(
                [datetime(2026, 7, 22, tzinfo=UTC)],
                dtype=pl.Datetime("us", "UTC"),
            )
        }
    )
    assert (
        _match_rows_vectorized(
            aware_frame,
            {"instant": "2026-07-22T00:00:00"},
            ["instant"],
        ).status
        is _RowMatchStatus.UNSUPPORTED_DTYPE
    )
    decimal_frame = pl.DataFrame({"amount": pl.Series([Decimal("1.23")], dtype=pl.Decimal(10, 2))})
    assert (
        _match_rows_vectorized(decimal_frame, {"amount": 1.23}, ["amount"]).status
        is _RowMatchStatus.UNSUPPORTED_DTYPE
    )


def test_object_key_is_unsupported_and_target_relocation_raises_typed_error() -> None:
    value = object()
    frame = pl.DataFrame({"opaque": pl.Series([value], dtype=pl.Object)})

    result = _match_rows_vectorized(frame, {"opaque": str(value)}, ["opaque"])
    assert result.status is _RowMatchStatus.UNSUPPORTED_DTYPE

    with pytest.raises(TraceCorrelationUnsupportedError) as exc_info:
        _find_target_row_index(frame, {"opaque": str(value)}, node_id="target")

    exc = exc_info.value
    assert exc.error_code == "trace_correlation_unsupported"
    assert exc.node_id == "target"
    assert exc.key_columns == ("opaque",)
    assert exc.dtypes == ("Object",)
    assert exc.reason_code == "unsupported_dtype"


def test_unique_relaxed_match_emits_explicit_low_confidence_diagnostic() -> None:
    diagnostics: list[dict[str, object]] = []
    frame = pl.DataFrame({"id": [1, 2], "label": ["a", "b"]})

    row, index = _find_matching_row(
        frame,
        {"id": 1, "label": "changed"},
        diagnostics=diagnostics,
        node_id="parent",
        child_node_id="child",
    )

    assert index == 0
    assert row == {"id": 1, "label": "a"}
    assert diagnostics == [
        {
            "code": "low_confidence_relaxed_match",
            "severity": "warning",
            "reason": "strict_keys_no_match_best_subset",
            "message": (
                "Row correlation for node 'parent' used a relaxed match "
                "on columns ['id']; omitted columns ['label']."
            ),
            "node_id": "parent",
            "child_node_id": "child",
            "match_strategy": "relaxed",
            "match_columns": ["id"],
            "ignored_columns": ["label"],
            "matched_row_count": 1,
            "matched_row_indices": [0],
            "strict_key_columns": ["id", "label"],
            "effective_key_columns": ["id"],
            "omitted_key_columns": ["label"],
            "candidate_count": 1,
            "candidate_indices": [0],
            "candidate_indices_state": "available",
        }
    ]


def test_trace_row_serialization_is_the_preview_json_safe_boundary() -> None:
    row = {
        "day": date(2026, 7, 22),
        "unsafe": 2**53,
        "nested": {"values": [float("inf"), Decimal("1.25")]},
        "opaque": object(),
    }

    assert _jsonify_row(row) == row_to_json_safe(row)


def _enrichment_step(
    node_id: str,
    *,
    added: tuple[str, ...] = (),
    modified: tuple[str, ...] = (),
    input_values: dict[str, object] | None = None,
    output_values: dict[str, object] | None = None,
) -> TraceStep:
    return TraceStep(
        node_id=node_id,
        node_name=node_id,
        node_type="polars",
        schema_diff=SchemaDiff(
            columns_added=list(added),
            columns_removed=[],
            columns_modified=list(modified),
            columns_passed=[],
        ),
        input_values=input_values or {},
        output_values=output_values or {},
    )


def test_enrichment_completed_result_memo_reuses_sibling_concern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._trace_enrichment as enrichment

    producer = _enrichment_step(
        "producer",
        added=("derived",),
        input_values={"raw": 2},
        output_values={"derived": 4},
    )
    left = _enrichment_step("left", input_values={"derived": 4})
    right = _enrichment_step("right", input_values={"derived": 4})
    steps = [producer, left, right]
    node_map = {
        "producer": SimpleNamespace(
            data=SimpleNamespace(config={"code": "df = df.with_columns(derived=pl.col('raw')*2)"})
        ),
        "left": SimpleNamespace(data=SimpleNamespace(config={})),
        "right": SimpleNamespace(data=SimpleNamespace(config={})),
    }
    calls = 0

    def parse_expression(_code, _column):
        return SimpleNamespace(expression_text="raw * 2", referenced_columns=[])

    def evaluate_expression(_code, _column, _values, *, preamble_ns=None):
        nonlocal calls
        del preamble_ns
        calls += 1
        return SimpleNamespace(substituted_text="2 * 2", result_value=4)

    monkeypatch.setattr(
        enrichment,
        "_trace_module",
        lambda: SimpleNamespace(
            parse_expression=parse_expression,
            evaluate_expression=evaluate_expression,
            enrich_banding=lambda *_args, **_kwargs: {},
        ),
    )
    memo: dict[tuple[object, ...], dict[str, object]] = {}

    first = _build_input_sources(
        ["derived"],
        left,
        steps,
        node_map,
        None,
        completed_memo=memo,
        frame_identity=(("producer", "", 1),),
    )
    second = _build_input_sources(
        ["derived"],
        right,
        steps,
        node_map,
        None,
        completed_memo=memo,
        frame_identity=(("producer", "", 1),),
    )

    assert first == second
    assert first is not second
    assert calls == 1


def test_enrichment_failures_are_not_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute._trace_enrichment as enrichment

    producer = _enrichment_step("producer", added=("derived",), output_values={"derived": 4})
    consumer = _enrichment_step("consumer", input_values={"derived": 4})
    node_map = {
        "producer": SimpleNamespace(data=SimpleNamespace(config={"code": "broken"})),
        "consumer": SimpleNamespace(data=SimpleNamespace(config={})),
    }
    calls = 0

    def fail_evaluate(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(
        enrichment,
        "_trace_module",
        lambda: SimpleNamespace(
            parse_expression=lambda *_args: SimpleNamespace(
                expression_text="broken", referenced_columns=[]
            ),
            evaluate_expression=fail_evaluate,
            enrich_banding=lambda *_args, **_kwargs: {},
        ),
    )
    memo: dict[tuple[object, ...], dict[str, object]] = {}

    for _ in range(2):
        result = _build_input_sources(
            ["derived"],
            consumer,
            [producer, consumer],
            node_map,
            None,
            completed_memo=memo,
        )
        assert result["derived"]["error_type"] == "RuntimeError"

    assert calls == 2
    assert memo == {}


def test_enrichment_active_path_cycle_is_structured_not_memoized() -> None:
    producer = _enrichment_step("producer", added=("derived",), output_values={"derived": 4})
    consumer = _enrichment_step("consumer", input_values={"derived": 4})
    node_map = {
        "producer": SimpleNamespace(data=SimpleNamespace(config={})),
        "consumer": SimpleNamespace(data=SimpleNamespace(config={})),
    }
    memo: dict[tuple[object, ...], dict[str, object]] = {}

    result = _build_input_sources(
        ["derived"],
        consumer,
        [producer, consumer],
        node_map,
        None,
        completed_memo=memo,
        active_path=(("producer", "derived"),),
    )

    assert result["derived"]["error_type"] == "TraceEnrichmentCycle"
    assert memo == {}
