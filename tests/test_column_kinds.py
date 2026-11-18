"""Every Polars type has one column kind, and every reader handles a column as its kind.

``haute._polars_dtypes.column_kind`` is the one answer to what kind of column a type
is (specs/io-layer, "Column kinds"). The first half pins that answer: a sample of every
type class the installed Polars defines (read from Polars, so an upgrade adds its new
types and fails here until they have a sample), each kind agreeing with Polars' own
classifiers, and the shared case file the editor's rules are held to.

The second half sends a column of every type through every reader: the pivot, the
frame profile, the Banding statistics, a number Banding and a Rating Step, the
optimiser's analysis, a deploy request's values, the training target check, a trace's
values and the assistant's column profile. Each handles the column as its kind or
refuses it naming the column or its type. The readers are found from the code: a module
that asks Polars' classifiers or the kind module is driven here or exempted with its
reason, and one that is neither fails :func:`test_every_reader_is_driven_or_exempted`.
"""

from __future__ import annotations

import ast
import math
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._polars_dtypes import (
    DATE_KINDS,
    NUMBER_KINDS,
    TEXT_KINDS,
    base_type_kind,
    column_kind,
    integer_range,
    numpy_float_type,
    polars_type_kinds,
    rendered_column_kind,
)
from haute.errors import SchemaMismatchError
from tests._column_kind_samples import case_script, sample_column, sample_dtypes
from tests._source_files import REPO_ROOT, source_files

SAMPLES = sample_dtypes()
_FIXTURE = (
    REPO_ROOT / "frontend" / "src" / "utils" / "__tests__" / "fixtures" / "columnKindCases.json"
)


def _column_type_classes() -> set[type[pl.DataType]]:
    return {
        candidate
        for name in dir(pl)
        if isinstance(candidate := getattr(pl, name), type)
        and issubclass(candidate, pl.DataType)
        and candidate not in (pl.DataType, pl.Unknown)
    }


# ---------------------------------------------------------------------------
# The kind of every type
# ---------------------------------------------------------------------------


def test_every_type_class_polars_defines_has_a_sample() -> None:
    uncovered = sorted(
        cls.__name__
        for cls in _column_type_classes()
        if not any(isinstance(dtype, cls) for dtype in SAMPLES)
    )
    assert uncovered == [], (
        "Give each new Polars type a sample in scripts/regen_column_kind_cases.py and, if it "
        "fits none of Haute's kinds, a kind in haute._polars_dtypes.column_kind."
    )


def test_the_shared_cases_are_what_the_servers_rules_answer() -> None:
    script = case_script()
    on_disk = _FIXTURE.read_bytes().decode("utf-8")
    assert on_disk == script.canonical(script.build_fixture()), (
        "Run `uv run python scripts/regen_column_kind_cases.py`, then the editor's "
        "columnKinds.test.ts."
    )


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_a_kind_is_polars_own_classification(dtype: pl.DataType) -> None:
    kind = column_kind(dtype)
    assert (kind == "integer") == dtype.is_integer()
    assert (kind == "float") == dtype.is_float()
    assert (kind == "decimal") == dtype.is_decimal()
    assert (kind in NUMBER_KINDS) == dtype.is_numeric()
    assert (kind == "nested") == dtype.is_nested()
    assert (kind == "object") == dtype.is_object()
    assert (kind == "extension") == dtype.is_extension()
    # The class, the base name and the rendering all give the instance's kind.
    assert column_kind(dtype.base_type()) == kind
    assert base_type_kind(str(dtype.base_type())) == kind
    assert rendered_column_kind(str(dtype)) == kind


def test_every_float_width_is_a_float() -> None:
    assert {column_kind(dtype) for dtype in (pl.Float16, pl.Float32, pl.Float64)} == {"float"}


def test_the_table_the_browser_reads_holds_every_column_type_class() -> None:
    kinds = polars_type_kinds()
    assert {getattr(pl, name) for name in kinds} == _column_type_classes()
    assert all(kinds[name] == column_kind(getattr(pl, name)) for name in kinds)


def test_a_type_with_no_kind_is_refused_by_name() -> None:
    with pytest.raises(SchemaMismatchError, match="Unknown"):
        column_kind(pl.Unknown)
    with pytest.raises(SchemaMismatchError, match="Float8"):
        base_type_kind("Float8")


@pytest.mark.parametrize("dtype", [d for d in SAMPLES if column_kind(d) == "integer"], ids=str)
def test_an_integer_range_is_the_one_polars_states(dtype: pl.DataType) -> None:
    least, greatest = integer_range(dtype)
    assert pl.Series([least, greatest], dtype=dtype).to_list() == [least, greatest]
    for outside in (least - 1, greatest + 1):
        with pytest.raises((OverflowError, TypeError, pl.exceptions.PolarsError)):
            pl.Series([outside], dtype=dtype, strict=True)


@pytest.mark.parametrize("dtype", [d for d in SAMPLES if column_kind(d) == "float"], ids=str)
def test_a_float_converts_to_numpy_at_its_own_width(dtype: pl.DataType) -> None:
    held = pl.Series([0.1], dtype=dtype).item()
    assert numpy_float_type(dtype)(0.1) == held
    assert pl.Series([0.1], dtype=dtype).to_numpy().dtype.type is numpy_float_type(dtype)


def test_a_width_fact_is_refused_for_another_kind() -> None:
    with pytest.raises(SchemaMismatchError, match="Float64"):
        integer_range(pl.Float64)
    with pytest.raises(SchemaMismatchError, match="Int64"):
        numpy_float_type(pl.Int64)


# ---------------------------------------------------------------------------
# Every reader, sent a column of every type
# ---------------------------------------------------------------------------
#
# A sample column ``x`` holds six values of its kind: repeats, one null and, for a
# float, one NaN (tests/_column_kind_samples.py).


def _python_values(dtype: pl.DataType) -> list[Any]:
    return sample_column(dtype).to_list()


def _valid(values: list[Any]) -> list[Any]:
    """The values a number aggregate reads: neither null nor NaN."""
    return [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]


def _names(error: BaseException, *names: str) -> bool:
    text = " ".join(
        str(part) for part in (error, getattr(error, "detail", ""), getattr(error, "fields", ""))
    )
    return any(name in text for name in names)


@pytest.fixture
def context() -> Iterator[Any]:
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile

    admitted = create_admitted_execution_context(
        operation="column_kinds_test", profile=ExecutionProfile.EXPLORE_ANALYSIS
    )
    try:
        yield admitted
    finally:
        admitted.release_admission()


def _pivot(rows: list[str], values: list[tuple[str, str]]) -> SimpleNamespace:
    """A pivot calculation's spec: rows by *rows*, one value per ``(field, aggregation)``."""
    shown = {"number_format": "general", "decimal_places": None, "use_grouping": True}
    pivot = {
        "version": 1,
        "id": "pivot",
        "name": "pivot",
        "enabled": True,
        "filters": [],
        "columns": [],
        "rows": [
            {"id": f"row_{i}", "field": field, "sort": "ascending", **shown}
            for i, field in enumerate(rows)
        ],
        "values": [
            {
                "id": f"value_{i}",
                "field": field,
                "aggregation": aggregation,
                "reference": f"value_{i}",
                "display_name": aggregation,
                "sort_rows": "none",
                "color_scale": "none",
                "color_scale_split_by": None,
                **shown,
            }
            for i, (field, aggregation) in enumerate(values)
        ],
        "formulas": [],
        "options": {"row_grand_totals": True, "column_grand_totals": True, "sort_by": None},
    }
    pivot["value_order"] = [value["id"] for value in pivot["values"]]
    point = SimpleNamespace(source="live", data_version="v1")
    return SimpleNamespace(pivot=pivot, point=point, calculation_key="k")


def _run_pivot(context: Any, frame: pl.DataFrame, spec: SimpleNamespace) -> Any:
    from haute.routes._pivot_service import PivotService

    return PivotService.__new__(PivotService)._calculate_leased(spec, context, frame.lazy())


def _cell_values(result: Any, value_id: str) -> list[Any]:
    """Each row's value of *value_id*, the grand total last."""
    return [cell.value for cell in result.cells if cell.value_id == value_id]


_DIMENSION_KINDS = {"integer", "float", "decimal", "boolean", "text", "category"} | {
    "date",
    "datetime",
    "time",
    "null",
}


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_the_pivot_groups_by_a_column_of_every_kind_or_refuses_it(
    dtype: pl.DataType, context: Any
) -> None:
    from haute.routes._pivot_service import PivotContractError

    frame = pl.DataFrame([sample_column(dtype), pl.Series("n", [1] * 6)])
    spec = _pivot(["x"], [("n", "sum")])
    if column_kind(dtype) not in _DIMENSION_KINDS:
        with pytest.raises(PivotContractError) as refused:
            _run_pivot(context, frame, spec)
        assert refused.value.failure.dimensions["field"] == "x"
        return
    result = _run_pivot(context, frame, spec)
    # NaN is a group of its own, apart from the missing values.
    groups = Counter(
        "nan" if isinstance(v, float) and math.isnan(v) else v for v in _python_values(dtype)
    )
    assert sorted(_cell_values(result, "value_0")[:-1]) == sorted(groups.values())
    assert _cell_values(result, "value_0")[-1] == 6


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_the_pivot_sums_and_counts_a_column_as_its_kind_or_refuses_it(
    dtype: pl.DataType, context: Any
) -> None:
    from haute.routes._pivot_service import PivotContractError

    frame = pl.DataFrame([sample_column(dtype), pl.Series("g", ["all"] * 6)])
    valid = _valid(_python_values(dtype))
    if column_kind(dtype) not in NUMBER_KINDS:
        with pytest.raises(PivotContractError) as refused:
            _run_pivot(context, frame, _pivot(["g"], [("x", "sum")]))
        assert refused.value.failure.dimensions["field"] == "x"
        return
    result = _run_pivot(context, frame, _pivot(["g"], [("x", "sum"), ("x", "count")]))
    total = sum(valid, Decimal(0) if column_kind(dtype) == "decimal" else 0)
    assert float(_cell_values(result, "value_0")[0]) == pytest.approx(float(total))
    # A float's NaN is left out of the count, as a null is.
    assert _cell_values(result, "value_1")[0] == len(valid)


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_the_profile_reads_a_column_of_every_kind_as_its_kind(
    dtype: pl.DataType, context: Any
) -> None:
    from haute._frame_profile import _build_frame_stats

    frame = pl.LazyFrame([sample_column(dtype)])
    [stat] = _build_frame_stats(frame, frame.collect_schema(), execution_context=context).columns
    values = _python_values(dtype)
    kind = column_kind(dtype)
    assert stat.null_count == sum(v is None for v in values)
    assert stat.nan_count == (1 if kind == "float" else None)
    if kind in NUMBER_KINDS:
        assert stat.histogram is not None
        # The histogram bins the finite values only.
        assert sum(b.count for b in stat.histogram.bins) == len(_valid(values))
    else:
        assert stat.histogram is None


def _banding_stats(dtype: pl.DataType, mode: str, context: Any) -> Any:
    from haute.routes._banding_stats import BandingStatsService

    body = SimpleNamespace(
        factor={"banding": mode, "column": "x", "output_column": "band", "rules": []},
        histogram_bins=10,
        value_limit=50,
    )
    leased = SimpleNamespace(scan=pl.LazyFrame([sample_column(dtype)]), data_version="v1")
    service = BandingStatsService.__new__(BandingStatsService)
    return service._compute(body, "x", leased, context)


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_the_banding_statistics_measure_a_number_or_date_or_refuse_it(
    dtype: pl.DataType, context: Any
) -> None:
    from haute.errors import UnprocessableError

    kind = column_kind(dtype)
    if kind not in NUMBER_KINDS | DATE_KINDS:
        with pytest.raises(UnprocessableError) as refused:
            _banding_stats(dtype, "breakpoints", context)
        assert _names(refused.value, str(dtype))
        return
    stats = _banding_stats(dtype, "breakpoints", context)
    assert stats.null_count == 1
    assert stats.non_finite_count == (1 if kind == "float" else 0)
    assert sum(b.count for b in stats.bins) == len(_valid(_python_values(dtype)))


def _banded(dtype: pl.DataType) -> pl.DataFrame:
    from haute._rating import apply_banding_factors

    factor = {
        "banding": "breakpoints",
        "column": "x",
        "output_column": "band",
        "rules": [{"boundary": "1.5", "label": "low"}, {"boundary": "", "label": "high"}],
        "default": "unknown",
    }
    return apply_banding_factors(pl.LazyFrame([sample_column(dtype)]), [factor]).collect()


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_number_banding_bands_a_number_and_nulls_a_floats_nan_or_refuses_it(
    dtype: pl.DataType,
) -> None:
    from haute._validation_error import ConfigSettingError

    kind = column_kind(dtype)
    if kind == "null":
        assert _banded(dtype)["band"].to_list() == ["unknown"] * 6
        return
    if kind not in NUMBER_KINDS:
        with pytest.raises(ConfigSettingError) as refused:
            _banded(dtype)
        assert _names(refused.value, str(dtype))
        return
    bands = _banded(dtype)["band"].to_list()
    expected = [
        "unknown"
        if v is None or (isinstance(v, float) and math.isnan(v))
        else "low"
        if v <= Decimal("1.5")
        else "high"
        for v in _python_values(dtype)
    ]
    assert bands == expected


def _rate_key(kind: str) -> Any:
    """The first sample value, as a Rating table entry spells it."""
    keys = {
        "integer": 1,
        "float": 1.0,
        "decimal": "1.50",
        "boolean": True,
        "text": "a",
        "category": "a",
        "date": "2024-01-01",
        "datetime": "2024-01-01 08:00:00",
        "time": "08:01:00",
        "duration": "PT1S",
    }
    return keys.get(kind, "a")


#: The Rating factor types a table refuses by name (``_RATING_PRIMITIVE_DESCRIPTOR_KINDS``):
#: a deliberate set, so a change to it is seen here.
_RATING_REFUSED = {"Float16", "UInt128", "Binary"}
_RATING_REFUSED_KINDS = {"nested", "object", "extension"}


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_a_rating_lookup_prices_a_factor_of_every_supported_type_or_refuses_it(
    dtype: pl.DataType,
) -> None:
    from haute._rating import apply_rating_step_from_config
    from haute._validation_error import ConfigSettingError

    kind = column_kind(dtype)
    config = {
        "tables": [
            {
                "factors": ["x"],
                "output_column": "rate",
                "entries": [{"x": _rate_key(kind), "value": 2.0}],
                "default_value": "1",
            }
        ]
    }
    frame = pl.LazyFrame([sample_column(dtype)])
    if str(dtype) in _RATING_REFUSED or kind in _RATING_REFUSED_KINDS:
        with pytest.raises(ConfigSettingError) as refused:
            apply_rating_step_from_config(frame, config).collect()
        assert _names(refused.value, str(dtype))
        return
    rates = apply_rating_step_from_config(frame, config).collect()["rate"].to_list()
    values = _python_values(dtype)
    assert rates == [2.0 if v is not None and v == values[0] else 1.0 for v in values]


def _segments_frames(dtype: pl.DataType) -> tuple[Any, int, Any]:
    from tests.test_optimiser_segments import _frames, _spec

    frames, n = _frames([0, 1, 2, 0, 1, 2], {"x": sample_column(dtype)})
    return frames, n, _spec(analysis=("x",))


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_the_optimiser_breaks_down_an_analysis_column_as_its_kind(dtype: pl.DataType) -> None:
    from haute.routes._optimiser_outcomes import _column_stats
    from haute.routes._optimiser_segments import segment_keys
    from tests.test_optimiser_segments import _run

    column = sample_column(dtype)
    # Polars counts no distinct values of an object column; the row count stands in.
    distinct = column.len() if column_kind(dtype) == "object" else column.n_unique()
    counts = {"approx_n_unique:x": distinct, "max_bytes:x": None}
    stats = _column_stats(pl.Schema({"x": dtype}), counts, ["x"])
    [key] = segment_keys(
        {"columns": ["x"], "column_stats": stats}, factor_columns=[], factor_tables={}
    )
    kind = column_kind(dtype)
    assert key.binning == ("numeric" if kind in NUMBER_KINDS else "categorical")
    if kind in {"duration", "nested", "object", "extension"}:
        # A breakdown labels a level by its text, which Polars 2 does not write for these.
        assert key.available is False
        assert key.unavailable_reason == f"{dtype.base_type()} values cannot be broken down."
        return
    frames, n, spec = _segments_frames(dtype)
    response = _run(frames, n, column="x", binning=key.binning, spec=spec)
    assert sum(row.quotes for row in response.rows) == 6
    missing = [row.quotes for row in response.rows if row.kind == "missing"]
    values = _python_values(dtype)
    if kind in NUMBER_KINDS:
        # A null and a float's NaN are both missing.
        assert missing == [len(values) - len(_valid(values))]


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_an_analysis_column_is_filtered_by_equality_as_its_kind_or_refused(
    dtype: pl.DataType,
) -> None:
    from haute.errors import RequestRefusedError
    from haute.routes._optimiser_quotes import QuoteFilters, quote_page

    frames, n, spec = _segments_frames(dtype)
    values = _python_values(dtype)
    kind = column_kind(dtype)
    value = 1.0 if kind == "float" else values[0]
    page = quote_page(spec, filters=QuoteFilters(analysis_equals=(("x", value),)))
    page.validate(spec)
    if kind not in TEXT_KINDS | {"integer", "float", "boolean"}:
        with pytest.raises(RequestRefusedError) as refused:
            page.run(frames, spec, n)
        assert _names(refused.value, f"'x' ({dtype})")
        return
    assert page.run(frames, spec, n).rows.height == values.count(values[0])
    missing = quote_page(spec, filters=QuoteFilters(analysis_equals=(("x", None),)))
    assert missing.run(frames, spec, n).rows.height == len(values) - len(_valid(values))


def _json_value(value: Any) -> Any:
    """*value* as a request's JSON carries it: a date or time as ISO text."""
    return value.isoformat() if hasattr(value, "isoformat") else value


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_a_deploy_request_carries_a_value_of_every_request_type_or_refuses_it(
    dtype: pl.DataType,
) -> None:
    from haute._json_values import REQUEST_DTYPES, json_value
    from haute.deploy._request_frame import request_frame_schema
    from haute.errors import DeployError

    if dtype.base_type() not in REQUEST_DTYPES:
        with pytest.raises(DeployError) as refused:
            request_frame_schema({"x": str(dtype)})
        assert _names(refused.value, str(dtype))
        return
    [declared] = request_frame_schema({"x": str(dtype)}).values()
    values = _python_values(dtype)
    held = json_value(_json_value(values[0]), declared)
    assert pl.Series([held], dtype=declared).to_list() == values[:1]
    if column_kind(dtype) == "integer":
        _, greatest = integer_range(dtype)
        with pytest.raises(ValueError, match="not"):
            json_value(greatest + 1, declared)


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_a_classification_target_of_every_kind_is_taken_or_refused_by_name(
    dtype: pl.DataType,
) -> None:
    from haute.modelling._target_check import training_target_task_issue

    frame = pl.LazyFrame([sample_column(dtype)])
    issue = training_target_task_issue(frame, target="x", task="classification", metrics=[])
    kind = column_kind(dtype)
    # The float samples hold whole numbers, which are class labels; the decimal's are not.
    if kind in {"integer", "float", "boolean", "null"} | TEXT_KINDS:
        assert issue is None
    else:
        assert issue is not None and "'x'" in issue


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_a_traced_value_is_shown_at_its_columns_own_width(dtype: pl.DataType) -> None:
    from haute._expression_parser import _display_values

    widened = float(np.float32(0.1))
    row = pl.DataFrame([sample_column(dtype)])
    [shown] = _display_values({"x": widened}, row).values()
    if column_kind(dtype) == "float":
        assert shown == float(str(numpy_float_type(dtype)(widened)))
    else:
        assert shown == widened


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_a_traced_value_matches_its_row_in_a_column_of_every_kind_or_says_why_not(
    dtype: pl.DataType,
) -> None:
    from haute._trace_correlation import _typed_value_match_expr

    values = _python_values(dtype)
    expression, reason = _typed_value_match_expr("x", values[0], dtype)
    # A trace matches a value of every kind but an object's and an extension's, which
    # Haute reads as its storage type before a pipeline sees it.
    if column_kind(dtype) in {"object", "extension"}:
        assert (expression, reason) == (None, "unsupported_dtype")
        return
    assert expression is not None, reason
    frame = pl.DataFrame([sample_column(dtype)])
    assert frame.filter(expression).height == values.count(values[0])


@pytest.mark.parametrize("dtype", SAMPLES, ids=str)
def test_the_assistant_profiles_a_column_of_every_kind_as_its_kind(dtype: pl.DataType) -> None:
    from haute.assistant._tools import _column_profile

    profile = _column_profile(pl.DataFrame([sample_column(dtype)]), "x", dtype)
    kind = column_kind(dtype)
    assert profile["null_count"] == sum(v is None for v in _python_values(dtype))
    if kind in TEXT_KINDS | {"boolean"}:
        assert sum(entry["count"] for entry in profile["values"]) == 6
    elif kind in NUMBER_KINDS | DATE_KINDS | {"time", "duration"}:
        assert "min" in profile and "max" in profile
    else:
        assert profile.get("values_withheld") == "unsupported_dtype"


# ---------------------------------------------------------------------------
# Every reader is found from the code
# ---------------------------------------------------------------------------

#: Polars' own classifiers of a type.
_CLASSIFIERS = frozenset(
    {
        "is_integer",
        "is_signed_integer",
        "is_unsigned_integer",
        "is_float",
        "is_decimal",
        "is_numeric",
        "is_temporal",
        "is_nested",
        "is_object",
        "is_extension",
    }
)
#: The kind module's answers to what kind a type is.
_KIND_API = frozenset(
    {
        "column_kind",
        "rendered_column_kind",
        "base_type_kind",
        "polars_type_kinds",
        "integer_range",
        "numpy_float_type",
        "NUMBER_KINDS",
        "DATE_KINDS",
        "TEXT_KINDS",
        "COLUMN_KINDS",
        "COLUMN_KIND_GROUPS",
    }
)

#: Each reader and the tests that send it a column of every type.
_DRIVEN: dict[str, tuple[str, ...]] = {
    "src/haute/routes/_pivot_service.py": (
        "test_the_pivot_groups_by_a_column_of_every_kind_or_refuses_it",
        "test_the_pivot_sums_and_counts_a_column_as_its_kind_or_refuses_it",
    ),
    "src/haute/_frame_profile.py": ("test_the_profile_reads_a_column_of_every_kind_as_its_kind",),
    "src/haute/routes/_banding_stats.py": (
        "test_the_banding_statistics_measure_a_number_or_date_or_refuse_it",
    ),
    "src/haute/_rating.py": (
        "test_number_banding_bands_a_number_and_nulls_a_floats_nan_or_refuses_it",
        "test_a_rating_lookup_prices_a_factor_of_every_supported_type_or_refuses_it",
    ),
    "src/haute/routes/_optimiser_outcomes.py": (
        "test_the_optimiser_breaks_down_an_analysis_column_as_its_kind",
    ),
    "src/haute/routes/_optimiser_segments.py": (
        "test_the_optimiser_breaks_down_an_analysis_column_as_its_kind",
    ),
    "src/haute/routes/_optimiser_quotes.py": (
        "test_an_analysis_column_is_filtered_by_equality_as_its_kind_or_refused",
    ),
    "src/haute/_json_values.py": (
        "test_a_deploy_request_carries_a_value_of_every_request_type_or_refuses_it",
    ),
    "src/haute/modelling/_target_check.py": (
        "test_a_classification_target_of_every_kind_is_taken_or_refused_by_name",
    ),
    "src/haute/_expression_parser.py": ("test_a_traced_value_is_shown_at_its_columns_own_width",),
    "src/haute/_trace_correlation.py": (
        "test_a_traced_value_matches_its_row_in_a_column_of_every_kind_or_says_why_not",
    ),
    "src/haute/assistant/_tools.py": (
        "test_the_assistant_profiles_a_column_of_every_kind_as_its_kind",
    ),
    # The editor's column lists mirror these, and the shared cases hold both sides to
    # them for every type: the cast table, the GLM classes and a Workbench column's fit.
    "src/haute/_polars_steps.py": ("test_the_shared_cases_are_what_the_servers_rules_answer",),
    "src/haute/modelling/_glm_terms.py": (
        "test_the_shared_cases_are_what_the_servers_rules_answer",
    ),
    "src/haute/_workbench_output.py": ("test_the_shared_cases_are_what_the_servers_rules_answer",),
}

_PYTHON_NUMBER = "calls float.is_integer() on a Python number, not a column type"
#: Each module that asks a kind but is not driven here, and why.
_NOT_DRIVEN: dict[str, str] = {
    "src/haute/deploy/_impact.py": (
        "compares the prediction columns of an endpoint's JSON answers, which Polars reads "
        "as integers, floats, text, booleans or nulls and never another kind"
    ),
    "src/haute/_model_scorer.py": (
        "refuses a number column where a model expects a categorical, by Polars' is_numeric()"
    ),
    "src/haute/_trace_enrichment.py": (
        "rounds a traced comparison through a float narrower than the Float64 trace boundary, "
        "by Polars' is_float()"
    ),
    "src/haute/assistant/_application.py": (
        "refuses an assistant edit that bands a float or temporal column categorically, by "
        "Polars' is_float() and is_temporal()"
    ),
    "src/haute/modelling/_metrics.py": (
        "chooses a partial-dependence grid's percentile method by Polars' is_integer()"
    ),
    "src/haute/routes/_explore_relationships.py": (
        "asks Polars' is_numeric() and is_nested() of a relationship's features and weight "
        "(tests/test_explore_relationships.py)"
    ),
    "src/haute/routes/_optimiser_input.py": (
        "checks the solver's own objective and constraint columns for non-finite values by "
        "Polars' is_float(), before they are cast to the solver's Float32"
    ),
    "src/haute/_pipeline_settings.py": _PYTHON_NUMBER,
    "src/haute/_workbench_export.py": _PYTHON_NUMBER,
    "src/haute/_workbench_tables.py": _PYTHON_NUMBER,
    "src/haute/_workbench_test_quotes.py": _PYTHON_NUMBER,
    "src/haute/modelling/_training_job.py": _PYTHON_NUMBER,
    "src/haute/modelling/_tuning.py": _PYTHON_NUMBER,
}


def _asks_a_kind(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _CLASSIFIERS
            and not node.args
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "haute._polars_dtypes"
            and any(alias.name in _KIND_API for alias in node.names)
        ):
            return True
    return False


def _readers() -> set[str]:
    root = REPO_ROOT / "src" / "haute"
    return {
        rel
        for path in source_files(root)
        if (rel := path.relative_to(REPO_ROOT).as_posix()) != "src/haute/_polars_dtypes.py"
        and _asks_a_kind(path.read_text(encoding="utf-8"))
    }


@pytest.mark.meta
def test_every_reader_is_driven_or_exempted() -> None:
    readers = _readers()
    undriven = sorted(readers - set(_DRIVEN) - set(_NOT_DRIVEN))
    assert not undriven, (
        "Each module that asks what kind a column is gets a test here sending it a column "
        f"of every Polars type, or an entry in _NOT_DRIVEN with its reason: {undriven}"
    )
    stale = sorted((set(_DRIVEN) | set(_NOT_DRIVEN)) - readers)
    assert not stale, f"These modules no longer ask a kind: {stale}"
    assert not set(_DRIVEN) & set(_NOT_DRIVEN)


def test_every_driver_named_is_a_test_here() -> None:
    names = {name for tests in _DRIVEN.values() for name in tests}
    assert sorted(name for name in names if not callable(globals().get(name))) == []


@pytest.mark.parametrize(
    ("source", "asks"),
    [
        ("dtype.is_float()", True),
        ("from haute._polars_dtypes import column_kind", True),
        ("from haute._polars_dtypes import parse_dtype", False),
        ("value.is_float", False),
    ],
)
def test_a_reader_is_found_by_what_it_asks(source: str, asks: bool) -> None:
    assert _asks_a_kind(source) is asks
