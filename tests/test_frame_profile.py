"""The data profile computation: per-column statistics and the overview summary.

These are direct unit tests of ``haute._frame_profile``, the analysis the shared
profile job runs for any data point (CACHE-S04). The route-level coverage of
that job lives in ``tests/test_analysis_results.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import pytest

if TYPE_CHECKING:
    pass


@pytest.fixture
def explore_execution_context():
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile

    context = create_admitted_execution_context(
        operation="explore_cache_unit_test",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
    )
    try:
        yield context
    finally:
        context.release_admission()


def test_build_frame_stats_object_dtype_distinct_is_none(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    series = pl.Series("obj_col", [{"a": 1}, {"a": 2}, {"a": 3}], dtype=pl.Object)
    lf = series.to_frame().lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert len(stats) == 1
    assert stats[0].name == "obj_col"
    assert stats[0].distinct_count is None
    assert stats[0].null_count == 0


def test_build_frame_stats_struct_dtype_distinct_is_computed(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"s": [{"x": 1}, {"x": 2}, {"x": 1}]}).lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert len(stats) == 1
    assert stats[0].name == "s"
    assert stats[0].distinct_count == 2


def test_build_frame_stats_empty_schema_returns_empty_list(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.LazyFrame()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert stats == []


def test_build_frame_stats_profiles_text_and_temporal_columns(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "text": pl.Series("text", [None, "a", "three"], dtype=pl.String),
            "empty_text": pl.Series("empty_text", [None, None, None], dtype=pl.String),
            "day": [date(2024, 1, 1), date(2024, 1, 4), None],
            "instant": [datetime(2024, 1, 1, 8), datetime(2024, 1, 2, 10), None],
        }
    ).lazy()

    stats = _build_frame_stats(lf, lf.collect_schema(), execution_context=explore_execution_context)
    by_name = {column.name: column for column in stats.columns}
    assert (
        by_name["text"].text_min_length,
        by_name["text"].text_mean_length,
        by_name["text"].text_max_length,
    ) == (1, 3.0, 5)
    assert by_name["empty_text"].text_min_length is None
    assert by_name["empty_text"].text_mean_length is None
    assert by_name["empty_text"].text_max_length is None
    assert by_name["day"].temporal_span == "3 days, 0:00:00"
    assert by_name["instant"].temporal_span == "1 day, 2:00:00"


def test_build_frame_stats_profiles_cardinality_identifier_and_duplicates(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "policy_id": [f"p{index}" for index in range(51)] + ["p0"],
            "id_exact": list(range(52)),
        }
    ).lazy()
    stats = _build_frame_stats(lf, lf.collect_schema(), execution_context=explore_execution_context)
    by_name = {column.name: column for column in stats.columns}
    assert by_name["policy_id"].unique_ratio == 51 / 52
    assert by_name["policy_id"].is_high_cardinality is True
    assert by_name["policy_id"].is_identifier_candidate is False
    assert by_name["id_exact"].is_identifier_candidate is True
    summary = stats.overview_summary.data_quality
    assert summary.duplicate_row_count == 0
    assert summary.duplicate_ratio == 0


def test_build_frame_stats_profile_flag_boundaries(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "at_limit": [f"v{i}" for i in range(50)] + ["v0"],
            "above_limit": [f"v{i}" for i in range(51)],
            "numeric_above_limit": list(range(51)),
            "policy_id": list(range(51)),
            "nullable_id": pl.Series(
                "nullable_id",
                [*range(50), None],
                dtype=pl.Int64,
            ),
            "empty_id": pl.Series("empty_id", [None] * 51, dtype=pl.Int64),
        }
    ).lazy()
    stats = _build_frame_stats(lf, lf.collect_schema(), execution_context=explore_execution_context)
    by_name = {column.name: column for column in stats.columns}

    assert by_name["at_limit"].is_high_cardinality is False
    assert by_name["above_limit"].is_high_cardinality is True
    assert by_name["numeric_above_limit"].is_high_cardinality is False
    assert by_name["policy_id"].is_identifier_candidate is True
    assert by_name["nullable_id"].unique_ratio == 1
    assert by_name["nullable_id"].is_identifier_candidate is False
    assert by_name["empty_id"].unique_ratio is None
    assert by_name["empty_id"].is_identifier_candidate is False

    one_row = pl.DataFrame({"id": [1]}).lazy()
    one_row_stat = _build_frame_stats(
        one_row,
        one_row.collect_schema(),
        execution_context=explore_execution_context,
    ).columns[0]
    assert one_row_stat.unique_ratio == 1
    assert one_row_stat.is_identifier_candidate is False


@pytest.mark.parametrize(
    ("values", "severity"),
    [([1, 2, 1], "warning"), ([1, 1], "danger"), ([1, 1, 1], "danger")],
)
def test_build_frame_stats_reports_duplicate_rows(
    explore_execution_context, values, severity
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"value": values}).lazy()
    summary = _build_frame_stats(
        lf, lf.collect_schema(), execution_context=explore_execution_context
    ).overview_summary.data_quality
    assert summary.duplicate_row_count == len(values) - len(set(values))
    assert summary.duplicate_ratio == pytest.approx(summary.duplicate_row_count / len(values))
    duplicate_issue = summary.issues[-1]
    assert duplicate_issue.severity == severity
    assert "duplicate" in duplicate_issue.label


def test_build_frame_stats_leaves_duplicate_profile_unknown_for_object_dtype(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.Series("obj", [{"a": 1}, {"a": 1}], dtype=pl.Object).to_frame().lazy()
    summary = _build_frame_stats(
        lf, lf.collect_schema(), execution_context=explore_execution_context
    ).overview_summary.data_quality
    assert summary.duplicate_row_count is None
    assert summary.duplicate_ratio is None


def test_build_explore_frame_stats_includes_row_count(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"value": [1, 2, 3]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    assert [s.name for s in frame_stats.columns] == ["value"]


def test_build_frame_stats_includes_numeric_profile_fields(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "premium": [-10, 0, 25, None],
            "region": ["north", "south", "north", "west"],
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {column.name: column for column in frame_stats.columns}
    assert by_name["premium"].min_value == "-10"
    assert by_name["premium"].kind == "Numeric"
    assert by_name["premium"].p25_value == "-5"
    assert by_name["premium"].median_value == "0"
    assert by_name["premium"].mean_value == "5"
    assert by_name["premium"].p75_value == "12.5"
    assert by_name["premium"].max_value == "25"
    assert by_name["premium"].std_value == "18.0278"
    assert by_name["premium"].zero_count == 1
    assert by_name["premium"].negative_count == 1
    assert by_name["region"].min_value == "north"
    assert by_name["region"].kind == "Text"
    assert by_name["region"].max_value == "west"
    assert by_name["region"].mean_value is None
    assert by_name["region"].std_value is None
    assert by_name["region"].zero_count is None


def test_build_frame_stats_formats_boolean_min_max_to_match_value_counts(
    explore_execution_context,
) -> None:
    """Boolean min/max must share the lowercase casing of value_counts.

    A Boolean column appears in both the Schema card (min/max) and the
    Categorical card (value counts). If min/max rendered ``str(True)`` while
    value counts cast to String ("true"), the same column would read
    inconsistently across cards.
    """

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"renewal": [True, False, True]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.kind == "Boolean"
    assert column.min_value == "false"
    assert column.max_value == "true"

    [profile] = frame_stats.overview_summary.categorical_summary
    assert {item.value for item in profile.values} == {"true", "false"}


def test_build_frame_stats_keeps_all_null_numeric_profiles(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {"all_null": [None, None], "single_value": [None, 10.0]},
        schema={"all_null": pl.Float64, "single_value": pl.Float64},
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {column.name: column for column in frame_stats.columns}
    assert by_name["all_null"].min_value is None
    assert by_name["all_null"].p25_value is None
    assert by_name["all_null"].median_value is None
    assert by_name["all_null"].mean_value is None
    assert by_name["all_null"].p75_value is None
    assert by_name["all_null"].max_value is None
    assert by_name["all_null"].std_value is None
    assert by_name["all_null"].zero_count == 0
    assert by_name["all_null"].negative_count == 0
    assert by_name["single_value"].mean_value == "10"
    assert by_name["single_value"].std_value is None


def test_build_frame_stats_reports_nan_counts_for_float_columns_only(
    explore_execution_context,
) -> None:
    """NaN is a third bucket, distinct from null: valid / null / NaN.

    A stream that cannot distinguish string from int materialises non-numeric
    error/default values as NaN in a Float column. Polars ``null_count``
    ignores NaN, so without a dedicated count an all-NaN column looks fully
    populated. Non-float dtypes cannot hold NaN, so their ``nan_count`` is
    None ("not applicable"), mirroring ``zero_count`` on non-numeric columns.
    """

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "measure": [1.0, float("nan"), float("nan"), None],
            "volume": [1, 2, 3, 4],
            "label": ["a", "b", "c", None],
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {column.name: column for column in frame_stats.columns}
    assert by_name["measure"].nan_count == 2
    assert by_name["measure"].null_count == 1
    assert by_name["volume"].nan_count is None
    assert by_name["label"].nan_count is None


def test_build_frame_stats_flags_nan_columns_in_quality_summary(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "all_nan": [float("nan")] * 4,
            "some_nan": [1.0, float("nan"), 2.0, 3.0],
            "clean": [1.0, 2.0, 3.0, 4.0],
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    issues = frame_stats.overview_summary.data_quality.issues
    nan_issues = [issue for issue in issues if "NaN" in issue.label]
    assert len(nan_issues) == 1
    assert nan_issues[0].label == "2 numeric columns with NaN values"
    assert nan_issues[0].severity == "danger"
    assert nan_issues[0].detail == "all_nan worst at 100%"
    # NaN rows are not nulls: the missing-values issue must not fire here.
    assert not any("missing" in issue.label for issue in issues)


def test_build_frame_stats_nan_issue_is_warning_below_half(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"measure": [1.0, float("nan"), 3.0, 4.0]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [issue] = [
        candidate
        for candidate in frame_stats.overview_summary.data_quality.issues
        if "NaN" in candidate.label
    ]
    assert issue.severity == "warning"
    assert issue.label == "1 numeric column with NaN values"
    assert issue.detail == "measure worst at 25%"


def test_build_frame_stats_distinct_count_excludes_null_bucket(
    explore_execution_context,
) -> None:
    """``n_unique`` counts the null bucket; the displayed distinct must not."""

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"value": [1, 1, 2, None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.null_count == 1
    assert column.distinct_count == 2


def test_build_frame_stats_distinct_count_excludes_nan_bucket(
    explore_execution_context,
) -> None:
    """NaN is reported separately (nan_count), so it is not a distinct value.

    ``[1.0, 1.0, nan, None]`` has one valid value (1.0); the NaN and null
    buckets are each their own count and must not inflate distinct_count.
    """

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"value": [1.0, 1.0, float("nan"), None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.null_count == 1
    assert column.nan_count == 1
    assert column.distinct_count == 1


def test_build_frame_stats_single_valid_value_with_nan_is_not_constant(
    explore_execution_context,
) -> None:
    """A constant column has NO nulls and NO NaNs — every row the same valid value.

    One valid value plus NaN reads distinct == 1, but the NaN rows mean the
    column is not constant; the NaN issue is the right signal for it.
    """

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"rate": [5.0, 5.0, float("nan")]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.distinct_count == 1
    labels = [issue.label for issue in frame_stats.overview_summary.data_quality.issues]
    assert not any("constant" in label for label in labels)
    assert any("NaN" in label for label in labels)


def test_build_frame_stats_all_nan_column_is_not_flagged_constant(
    explore_execution_context,
) -> None:
    """An all-NaN column has zero distinct valid values, so it is not

    "constant / single-value" — the dedicated NaN issue is the right signal.
    """

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"all_nan": [float("nan")] * 4}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.distinct_count == 0
    labels = [issue.label for issue in frame_stats.overview_summary.data_quality.issues]
    assert not any("constant" in label for label in labels)
    assert any("NaN" in label for label in labels)


def test_build_frame_stats_single_valid_value_with_nulls_is_not_constant(
    explore_execution_context,
) -> None:
    """A single-valued column that also has nulls is NOT constant (Nick's ruling).

    Constant means every row holds the same valid value; the null rows make
    this a missing-values column instead, and that issue already covers it.
    """

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"segment": ["same", "same", None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.distinct_count == 1
    labels = [issue.label for issue in frame_stats.overview_summary.data_quality.issues]
    assert not any("constant" in label for label in labels)
    assert any("missing" in label for label in labels)


def test_categorical_truncation_counts_null_bucket_as_a_group(
    explore_execution_context,
) -> None:
    """50 distinct values plus nulls is 51 value-count groups: truncated."""

    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"segment": [f"s{i:03d}" for i in range(50)] + [None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.distinct_count == 50
    assert profile.values_truncated is True
    assert len(profile.values) == 50


def test_build_frame_stats_includes_backend_overview_summary(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    row_count = 100
    lf = pl.DataFrame(
        {
            "policy_id": [f"p{i:03d}" for i in range(row_count)],
            "premium": list(range(-1, row_count - 1)),
            "region": [
                None if i < 25 else ("north" if i % 2 == 0 else "south") for i in range(row_count)
            ],
            "constant": ["same"] * row_count,
            "loss_ratio": [0] * row_count,
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    summary = frame_stats.overview_summary
    assert [issue.label for issue in summary.data_quality.issues] == [
        "1 column with missing values",
        "1 constant / single-value column",
        "1 numeric column with negatives",
        "1 mostly-zero numeric column",
        "1 high-cardinality column",
    ]
    assert summary.data_quality.issues[0].detail == "region worst at 25%"
    assert summary.data_quality.issue_count == 5


def test_build_frame_stats_includes_bounded_categorical_value_counts(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "premium": [10, 20, 30, 40],
            "region": ["north", "south", "north", None],
            "renewal": [True, False, True, True],
            "inception_date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 2, 1), None],
            "empty_segment": pl.Series("empty_segment", [None, None, None, None], dtype=pl.String),
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert set(profiles) == {"region", "renewal", "inception_date", "empty_segment"}
    # distinct_count is of non-null values only: {north, south} = 2, even
    # though the value-count groups also include the null bucket.
    assert profiles["region"].distinct_count == 2
    assert profiles["region"].expandable is True
    assert profiles["region"].values_truncated is False
    assert [(item.value, item.count) for item in profiles["region"].values] == [
        ("north", 2),
        ("south", 1),
        (None, 1),
    ]
    assert profiles["renewal"].expandable is True
    assert [(item.value, item.count) for item in profiles["renewal"].values] == [
        ("true", 3),
        ("false", 1),
    ]
    assert [(item.value, item.count) for item in profiles["inception_date"].values] == [
        ("2024-01-01", 2),
        ("2024-02-01", 1),
        (None, 1),
    ]
    assert [(item.value, item.count) for item in profiles["empty_segment"].values] == [
        (None, 4),
    ]


def test_build_frame_stats_survives_non_utf8_binary_column(
    explore_execution_context,
) -> None:
    """A Binary column holding non-UTF-8 bytes must not abort materialisation.

    Binary is admitted to the categorical value-count branch. A strict
    ``cast(pl.String)`` (or even ``strict=False``) aborts the entire batched
    ``streaming_collect`` on the first invalid byte sequence, taking down the
    whole frame. Undecodable bytes must instead map to the Unicode replacement
    character so the materialisation always completes.
    """
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "payload": pl.Series(
                "payload",
                [b"\xff\xfe", b"ok", b"ok", None],
                dtype=pl.Binary,
            ),
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 4
    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert "payload" in profiles
    values = {item.value: item.count for item in profiles["payload"].values}
    # Valid bytes decode to text; the two invalid bytes each become a
    # replacement character; nulls surface as a null bucket. Never a crash.
    assert values == {"ok": 2, "��": 1, None: 1}


def test_build_frame_stats_survives_duration_column(
    explore_execution_context,
) -> None:
    """A Duration column must not abort the whole Explore materialisation.

    Duration is temporal, so it is admitted to the categorical value-count
    branch — but Polars cannot ``cast(pl.Duration, pl.String)``, so the strict
    cast aborts the entire batched ``streaming_collect``, taking every other
    column's stats down with it. Duration values must instead be formatted
    leniently (like Binary) so the report always completes, with the column
    represented sensibly.
    """
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "premium": [10, 20, 30, 40],
            "wait": pl.Series(
                "wait",
                [timedelta(days=1), timedelta(hours=2), timedelta(hours=2), None],
                dtype=pl.Duration("us"),
            ),
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    # The whole report survives: both columns are present with core stats.
    assert frame_stats.row_count == 4
    stats = {column.name: column for column in frame_stats.columns}
    assert set(stats) == {"premium", "wait"}
    assert stats["premium"].mean_value == "25"
    assert stats["wait"].kind == "Temporal"
    assert stats["wait"].null_count == 1
    # {1 day, 2 hours} — distinct counts valid values only; the null bucket
    # is reported via null_count, not folded into distinct.
    assert stats["wait"].distinct_count == 2
    # Duration min/max already format via str(timedelta); labels match them.
    assert stats["wait"].min_value == "2:00:00"
    assert stats["wait"].max_value == "1 day, 0:00:00"
    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert "wait" in profiles
    values = {item.value: item.count for item in profiles["wait"].values}
    assert values == {"2:00:00": 2, "1 day, 0:00:00": 1, None: 1}


def test_build_frame_stats_expands_high_cardinality_categorical_columns_with_top_50_values(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "policy_id": (
                ["p000"] * 3 + ["p001"] * 2 + ["p002"] + [f"p{i:03d}" for i in range(3, 53)]
            )
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "policy_id"
    assert profile.distinct_count == 53
    assert profile.expandable is True
    assert profile.values_truncated is True
    assert len(profile.values) == 50
    assert [(item.value, item.count) for item in profile.values[:3]] == [
        ("p000", 3),
        ("p001", 2),
        ("p002", 1),
    ]
    assert [item.value for item in profile.values[-2:]] == ["p048", "p049"]
    assert "p050" not in {item.value for item in profile.values}


def test_build_frame_stats_returns_all_values_for_exactly_50_categorical_groups(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"segment": [f"s{i:03d}" for i in range(50)]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "segment"
    assert profile.distinct_count == 50
    assert profile.expandable is True
    assert profile.values_truncated is False
    assert len(profile.values) == 50
    assert [item.value for item in profile.values[:3]] == ["s000", "s001", "s002"]
    assert profile.values[-1].value == "s049"


def test_build_frame_stats_keeps_unsupported_categorical_profiles_unexpanded(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"codes": [["a"], ["b"], ["a"], None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "codes"
    # {["a"], ["b"]} = 2 distinct non-null values (the None row is excluded).
    assert profile.distinct_count == 2
    assert profile.expandable is False
    assert profile.values_truncated is False
    assert profile.values == []


def test_build_frame_stats_does_not_mark_uncomputed_list_values_as_truncated(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"codes": [[str(index)] for index in range(51)]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.distinct_count == 51
    assert profile.expandable is False
    assert profile.values_truncated is False
    assert profile.values == []


def test_build_frame_stats_uses_display_label_groups_for_binary_truncation(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    # Every raw binary value is distinct, but all lossily decode to the same
    # display label. Truncation reflects labels returned to the UI, not raw bytes.
    lf = pl.DataFrame(
        {
            "payload": pl.Series(
                "payload",
                [b"\x80" + bytes([index]) for index in range(0x80, 0x80 + 51)],
                dtype=pl.Binary,
            )
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.distinct_count == 51
    assert profile.values_truncated is False
    assert [(item.value, item.count) for item in profile.values] == [("\ufffd" * 2, 51)]


def test_build_frame_stats_categorical_value_counts_handle_count_column_name(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"count": ["one", "two", "one"]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "count"
    assert [(item.value, item.count) for item in profile.values] == [("one", 2), ("two", 1)]


def test_build_frame_stats_happy_path(explore_execution_context) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame(
        {
            "id": [1, 2, 3, 3],
            "name": ["alpha", "beta", None, "alpha"],
            "score": [1.5, 2.5, 3.5, 1.5],
        }
    ).lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert [s.name for s in stats] == ["id", "name", "score"]
    assert [s.dtype for s in stats] == ["Int64", "String", "Float64"]
    assert [s.kind for s in stats] == ["Numeric", "Text", "Numeric"]
    assert [s.null_count for s in stats] == [0, 1, 0]
    # "name" has a null row: 3 raw n_unique minus the null bucket == 2.
    assert [s.distinct_count for s in stats] == [3, 2, 3]
    assert [s.min_value for s in stats] == ["1", "alpha", "1.5"]
    assert [s.max_value for s in stats] == ["3", "beta", "3.5"]


@pytest.mark.parametrize("has_unique_column", [True, False])
def test_wide_frame_profiles_columns_in_bounded_sequential_batches(
    explore_execution_context, monkeypatch, has_unique_column
) -> None:
    from haute import _frame_profile

    calls = []
    collect = _frame_profile.cancellable_streaming_collect

    def checked_collect(query, **kwargs):
        names = query.collect_schema().names()
        calls.append(names)
        assert sum(name.startswith("null::") for name in names) <= 8
        if "unique_rows" in names:
            assert names == ["unique_rows"]
        return collect(query, **kwargs)

    monkeypatch.setattr(_frame_profile, "cancellable_streaming_collect", checked_collect)
    data = {f"value_{index}": [1.0, 1.0, None, 3.0] for index in range(17)}
    if has_unique_column:
        data["value_16"] = [1.0, 2.0, None, 4.0]
    lf = pl.DataFrame(data).lazy()

    result = _frame_profile._build_frame_stats(
        lf, lf.collect_schema(), execution_context=explore_execution_context
    )

    assert result.row_count == 4
    assert [column.name for column in result.columns] == list(data)
    assert all(column.null_count == 1 for column in result.columns)
    assert result.columns[0].median_value == "1"
    assert result.columns[0].distinct_count == 2
    assert result.overview_summary.data_quality.duplicate_row_count == (
        0 if has_unique_column else 1
    )
    assert len(calls) == (3 if has_unique_column else 4)


def test_build_explore_frame_stats_uses_one_streaming_collect_without_categorical_counts(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute import _frame_profile

    calls = []
    original_streaming_collect = _frame_profile.cancellable_streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(_frame_profile, "cancellable_streaming_collect", counted_streaming_collect)
    lf = pl.DataFrame({"value": [None, 1.0, 2.0]}).lazy()

    frame_stats = _frame_profile._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    assert len(calls) == 1


def test_build_explore_frame_stats_uses_single_batched_collect_for_bounded_value_counts(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute import _frame_profile

    calls = []
    original_streaming_collect = _frame_profile.cancellable_streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(_frame_profile, "cancellable_streaming_collect", counted_streaming_collect)
    lf = pl.DataFrame(
        {
            "value": [None, "a", "b"],
            "channel": ["web", "broker", "web"],
        }
    ).lazy()

    frame_stats = _frame_profile._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert [(item.value, item.count) for item in profiles["value"].values] == [
        ("a", 1),
        ("b", 1),
        (None, 1),
    ]
    assert [(item.value, item.count) for item in profiles["channel"].values] == [
        ("web", 2),
        ("broker", 1),
    ]
    assert len(calls) == 1
    collect_plan = calls[0].explain()
    assert "UNION" not in collect_plan
    assert "CACHE" not in collect_plan


def test_build_explore_frame_stats_counts_categorical_values_without_wide_unpivot(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute import _frame_profile

    def fail_unpivot(*args, **kwargs):  # pragma: no cover - assertion path only
        raise AssertionError("categorical value counts should not use wide unpivot")

    monkeypatch.setattr(pl.LazyFrame, "unpivot", fail_unpivot, raising=False)

    lf = pl.DataFrame(
        {
            "region": ["north", "south", "north", None],
            "channel": ["web", "broker", "web", "web"],
        }
    ).lazy()

    frame_stats = _frame_profile._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert [(item.value, item.count) for item in profiles["region"].values] == [
        ("north", 2),
        ("south", 1),
        (None, 1),
    ]
    assert [(item.value, item.count) for item in profiles["channel"].values] == [
        ("web", 3),
        ("broker", 1),
    ]


def test_build_frame_stats_returns_row_count_with_column_stats(
    explore_execution_context,
) -> None:
    from haute._frame_profile import _build_frame_stats

    lf = pl.DataFrame({"value": [1, 2, 3, 4]}).lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert stats.row_count == 4
    assert [s.name for s in stats.columns] == ["value"]


@pytest.mark.parametrize("dtype", [pl.Float64, pl.Categorical, pl.List(pl.Int64)])
def test_partitioned_whole_row_distinct_is_exact(
    tmp_path, monkeypatch, explore_execution_context, dtype
):
    from haute import _frame_profile as module

    if dtype == pl.Float64:
        values = [0.0, -0.0, float("nan"), float("nan"), None, None, 1.0, 1.0]
    elif dtype == pl.Categorical:
        values = ["a", "a", "b", "b", None, None, "c", "c"]
    else:
        values = [[1], [1], [2, 3], [2, 3], None, None, [], []]
    frame = pl.DataFrame(
        {
            "value": pl.Series(values, dtype=dtype),
            "__haute_profile_bucket": [1, 1, 2, 2, 3, 3, 4, 4],
        }
    )
    monkeypatch.setattr(module, "_PROFILE_DISTINCT_PARTITION_ROWS", 2, raising=False)
    result = module._build_frame_stats(
        frame.lazy(),
        frame.schema,
        execution_context=explore_execution_context,
        scratch_directory=tmp_path,
    )
    assert (
        result.overview_summary.data_quality.duplicate_row_count == frame.height - frame.n_unique()
    )
    parts = list(tmp_path.rglob("*.parquet"))
    assert parts
    for path in parts:
        assert pl.read_parquet_schema(path) == frame.schema


def test_partitioned_distinct_all_identical_and_disk_failure(
    tmp_path, monkeypatch, explore_execution_context
):
    from haute import _frame_profile as module

    monkeypatch.setattr(module, "_PROFILE_DISTINCT_PARTITION_ROWS", 2, raising=False)
    frame = pl.DataFrame({"a": [1] * 20, "b": ["x"] * 20})
    result = module._build_frame_stats(
        frame.lazy(),
        frame.schema,
        execution_context=explore_execution_context,
        scratch_directory=tmp_path / "success",
    )
    assert result.overview_summary.data_quality.duplicate_row_count == 19
    from haute import _file_ops

    def no_space(*_args, **_kwargs):
        raise OSError("no disk space")

    monkeypatch.setattr(_file_ops, "ensure_disk_headroom", no_space)
    with pytest.raises(OSError, match="no disk space"):
        module._build_frame_stats(
            frame.lazy(),
            frame.schema,
            execution_context=explore_execution_context,
            scratch_directory=tmp_path / "failure",
        )
