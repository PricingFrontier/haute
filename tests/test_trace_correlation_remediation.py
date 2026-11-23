from __future__ import annotations

import types
from datetime import UTC, date, datetime
from decimal import Decimal

import polars as pl
import pytest

from haute._json_safe import non_finite_float_sentinel, row_to_json_safe
from haute._python_syntax import method_call_sites
from haute._trace_correlation import (
    _CandidateIndicesState,
    _child_transform_may_reorder,
    _correlate_rows_posthoc,
    _find_matching_row,
    _jsonify_row,
    _match_rows_vectorized,
    _RowMatchStatus,
    _trace_values_match,
)
from haute.errors import TraceCorrelationUnsupportedError
from haute.trace import _find_target_row_index
from tests.conftest import head_frame_resolver


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


@pytest.mark.parametrize(
    ("column", "value"),
    [
        (pl.Series([[1, 2]]), ["a", "b"]),
        (pl.Series([{"a": 1}]), {"b": 1}),
        (pl.Series([[1, 2]], dtype=pl.Array(pl.Int64, 2)), [1, 2, 3]),
    ],
    ids=["list", "struct", "array"],
)
def test_a_nested_comparison_polars_refuses_is_reported_as_nested(column, value) -> None:
    result = _match_rows_vectorized(
        pl.DataFrame({"k": column, "j": [1]}), {"k": value, "j": 1}, ["k", "j"]
    )

    assert result.status is _RowMatchStatus.UNSUPPORTED_DTYPE
    assert result.reason_code == "incompatible_nested_schema"


def test_a_polars_failure_evaluating_the_strict_match_propagates(monkeypatch) -> None:
    import haute._trace_correlation as correlation

    def failing_expression(column, value, dtype, *, cast=None):
        # An expression Polars refuses only when it runs.
        return pl.col(column).cast(pl.String).str.to_integer(), None

    monkeypatch.setattr(correlation, "_typed_value_match_expr", failing_expression)

    with pytest.raises(pl.exceptions.ComputeError):
        _match_rows_vectorized(pl.DataFrame({"k": ["x"]}), {"k": "x"}, ["k"])


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

    assert _jsonify_row is row_to_json_safe
    assert _jsonify_row(row) == row_to_json_safe(row)


def _node(node_id: str, node_type: str = "generic", config: dict | None = None):
    return types.SimpleNamespace(
        id=node_id,
        data=types.SimpleNamespace(nodeType=node_type, config=config or {}),
    )


# ---------------------------------------------------------------------------
# F041 / F694 — positional fast-path must never attach a wrong parent row
# ---------------------------------------------------------------------------


class TestPositionalFastPath:
    def test_method_call_sites_report_exact_spans_in_source_order(self):
        sites = method_call_sites('left = df.join(other)\nright = df.sort("x")\n')

        assert [
            (
                site.name,
                site.start_line,
                site.start_column,
                site.end_line,
                site.end_column,
            )
            for site in sites
        ] == [
            ("join", 1, 7, 1, 21),
            ("sort", 2, 8, 2, 20),
        ]

    def test_reorder_classifier_ignores_comments_strings_and_longer_attributes(self):
        node = _node(
            "c",
            "polars",
            {
                "code": (
                    "note = '.sort( and .join('\n"
                    "# df = df.group_by('region').agg(pl.len())\n"
                    "method = df.sort\n"
                    "df = df.sorting_hat('label')\n"
                )
            },
        )

        assert _child_transform_may_reorder(node) is False

    def test_reorder_classifier_detects_exact_structured_calls(self):
        for method in (
            "sort",
            "sort_by",
            "reverse",
            "gather",
            "take",
            "sample",
            "shuffle",
            "join",
            "group_by",
            "groupby",
            "unique",
            "top_k",
            "bottom_k",
            "explode",
            "pivot",
            "cross_join",
        ):
            node = _node("c", "polars", {"code": f"df = df.{method}()"})
            assert _child_transform_may_reorder(node) is True, method

    def test_reorder_classifier_treats_invalid_source_as_unknown(self):
        node = _node("c", "polars", {"code": "df = ("})

        assert _child_transform_may_reorder(node) is True

    def test_reorder_with_no_shared_columns_marks_unresolved(self):
        """A rename that also reorders rows shares no column name with its
        parent; the old positional fast-path returned the wrong parent row.
        A reordering transform with nothing to verify against must be
        unresolved, not guessed."""
        eager = {
            "p": pl.DataFrame({"a": [10, 30]}),
            # b is a reordered rename of a: child row 0 (b=30) truly came
            # from parent row 1 (a=30), not parent row 0 (a=10).
            "c": pl.DataFrame({"b": [30, 10]}),
        }
        node_map = {
            "p": _node("p", "source"),
            "c": _node(
                "c",
                "polars",
                {"code": "df = df.select(pl.col('a').alias('b')).sort('b', descending=True)"},
            ),
        }

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            0,
            node_map=node_map,
            row_scope=head_frame_resolver(eager, {"c": ["p"]}, "c", node_map),
        )

        assert result["c"] == {"b": 30}
        # Must NOT be the positionally-aligned {'a': 10}.
        assert result["p"] is None

    def test_full_rename_preserving_order_resolves_positionally(self):
        """A pure rename/select (no reordering op) with no shared columns
        provably preserves row order, so the positional match is correct
        and the step must still resolve."""
        eager = {
            "p": pl.DataFrame({"old": [10, 20, 30]}),
            "c": pl.DataFrame({"new": [10, 20, 30]}),
        }
        node_map = {
            "p": _node("p", "source"),
            "c": _node("c", "polars", {"code": "df = df.rename({'old': 'new'})"}),
        }

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            2,
            node_map=node_map,
            row_scope=head_frame_resolver(eager, {"c": ["p"]}, "c", node_map),
        )

        assert result["p"] == {"old": 30}

    def test_single_parent_row_resolves_even_without_shared_columns(self):
        eager = {
            "p": pl.DataFrame({"a": [42]}),
            "c": pl.DataFrame({"b": [42]}),
        }
        # No code at all → conservatively "may reorder", but a single row
        # is unambiguous.
        node_map = {"p": _node("p", "dataInput"), "c": _node("c", "constant")}

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            0,
            node_map=node_map,
            row_scope=head_frame_resolver(eager, {"c": ["p"]}, "c", node_map),
        )

        assert result["p"] == {"a": 42}

    def test_nonunique_carried_key_falls_through_to_ambiguity(self):
        """When the carried shared key is not unique among parent rows a
        reorder could have swapped a different matching row into position;
        the fast path must fall through and report ambiguity, not guess."""
        eager = {
            "p": pl.DataFrame({"k": [1, 1], "extra": [5, 6]}),
            "c": pl.DataFrame({"k": [1, 1], "other": [9, 9]}),
        }
        node_map = {"p": _node("p", "dataInput"), "c": _node("c", "constant")}
        diagnostics: list[dict] = []

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            0,
            node_map=node_map,
            row_scope=head_frame_resolver(eager, {"c": ["p"]}, "c", node_map),
            diagnostics=diagnostics,
        )

        assert result["p"] is None
        assert any(d["code"] == "ambiguous_row_match" for d in diagnostics)

    def test_nonunique_carried_key_resolves_for_order_preserving_child(self):
        """Order-preserving transforms do not need a whole-frame uniqueness scan."""
        eager = {
            "p": pl.DataFrame({"k": [1, 1], "extra": [5, 6]}),
            "c": pl.DataFrame({"k": [1, 1], "extra": [5, 6], "other": [9, 9]}),
        }
        node_map = {
            "p": _node("p", "source"),
            "c": _node("c", "polars", {"code": "df = df.with_columns(other=pl.lit(9))"}),
        }

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            1,
            node_map=node_map,
            row_scope=head_frame_resolver(eager, {"c": ["p"]}, "c", node_map),
        )

        assert result["p"] == {"k": 1, "extra": 6}

    def test_unique_shared_key_still_accepts_positional_row(self):
        """The happy path is preserved: a unique carried key that matches
        the positional row is accepted."""
        eager = {
            "p": pl.DataFrame({"k": [1, 2], "extra": [5, 6]}),
            "c": pl.DataFrame({"k": [1, 2], "other": [9, 8]}),
        }
        node_map = {"p": _node("p", "dataInput"), "c": _node("c", "constant")}

        result = _correlate_rows_posthoc(
            eager,
            ["p", "c"],
            {"c": ["p"]},
            "c",
            1,
            node_map=node_map,
            row_scope=head_frame_resolver(eager, {"c": ["p"]}, "c", node_map),
        )

        assert result["p"] == {"k": 2, "extra": 6}


# ---------------------------------------------------------------------------
# F695 — near-zero float must be able to match exactly 0.0
# ---------------------------------------------------------------------------


class TestTraceValuesMatchNearZero:
    def test_tiny_value_matches_zero(self):
        assert _trace_values_match(1e-15, 0.0) is True
        assert _trace_values_match(0.0, 1e-15) is True

    def test_distinct_small_values_do_not_match(self):
        assert _trace_values_match(1.0, 0.0) is False
        assert _trace_values_match(0.5, 0.4) is False
