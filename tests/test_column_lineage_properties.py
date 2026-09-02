"""Differential properties for the closed Polars column-lineage model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from polars.testing import assert_frame_equal

from haute._column_lineage import (
    ColumnLineageAnalysis,
    analyze_polars_cardinality,
    analyze_polars_lineage,
)

ROWS = pl.DataFrame(
    {
        "a": [1, None, 3, 4],
        "b": [10, 20, None, 40],
        "c": [2, 1, 2, 1],
        "d": [0, 1, 0, 1],
        "s": ["x1", None, "axb", "zz"],
        "t": [date(2020, 1, 1), None, date(2021, 6, 2), date(2022, 12, 31)],
    }
)
NUMERIC_COLUMNS = ("a", "b", "c", "d")
# Row-count variants every generated program must stay bounded over.
EMPTY_ROWS = ROWS.clear()
NULL_ROWS = pl.DataFrame(
    {name: [None] * ROWS.height for name in ROWS.columns},
    schema=ROWS.schema,
)
NAN_ROWS = ROWS.with_columns(
    pl.col(name).cast(pl.Float64).fill_null(float("nan")) for name in NUMERIC_COLUMNS
)
GROUP_ROWS = pl.DataFrame({"g": ["x", "x", "y", "z"], "a": [1, 2, 3, 4], "b": [4, 3, 2, 1]})
LEFT_ROWS = pl.DataFrame(
    {
        "id": [1, 2, 3],
        "left_value": [10, 20, 30],
        "shared": [100, 200, 300],
        "left_unused": [0, 0, 0],
    }
)
RIGHT_ROWS = pl.DataFrame(
    {
        "id": [1, 2, 4],
        "right_value": [11, 21, 41],
        "shared": [101, 201, 401],
        "right_unused": [0, 0, 0],
    }
)


@dataclass(frozen=True)
class Program:
    code: str
    output_columns: tuple[str, ...]
    demand: tuple[str, ...]


def _run(code: str, **frames: pl.DataFrame) -> pl.DataFrame:
    scope: dict[str, Any] = {"pl": pl, **frames}
    exec(code, scope)  # noqa: S102 - generated test programs only.
    result = scope["df"]
    assert isinstance(result, pl.DataFrame)
    return result


def _assert_metadata(
    analysis: ColumnLineageAnalysis,
    input_schemas: dict[str, frozenset[str]],
    full: pl.DataFrame,
) -> None:
    assert analysis.supported
    assert analysis.exact_output_columns == frozenset(full.columns)
    for input_name, demand in analysis.demands_by_input.items():
        assert demand <= input_schemas[input_name]


def _assert_unordered_equal(left: pl.DataFrame, right: pl.DataFrame) -> None:
    assert left.schema == right.schema
    assert sorted(map(repr, left.rows())) == sorted(map(repr, right.rows()))


@st.composite
def _unary_programs(draw: st.DrawFn) -> Program:
    columns = ["a", "b", "c", "d", "s", "t"]
    # Generated arithmetic, filters and ``unpivot`` lists must only ever touch
    # columns whose dtype supports them, so the generator tracks kinds too.
    numeric = set(NUMERIC_COLUMNS)
    strings = {"s"}
    temporal = {"t"}
    lines = ["df = rows"]
    fresh = 0
    unpivoted = False
    for _ in range(draw(st.integers(min_value=1, max_value=6))):
        available = ["sort", "rows", "select", "rename", "drop_nulls", "with_row_index"]
        if numeric:
            available.extend(("filter", "with_columns"))
        if len(columns) > 1:
            available.append("drop")
        if strings:
            available.append("str_filter")
        if temporal:
            available.append("dt_year")
        if not unpivoted and numeric and len(columns) > len(numeric):
            available.append("unpivot")
        operation = draw(st.sampled_from(sorted(available)))
        if operation == "drop":
            column = draw(st.sampled_from(sorted(columns)))
            lines.append(f"df = df.drop({column!r})")
            columns.remove(column)
            numeric.discard(column)
            strings.discard(column)
            temporal.discard(column)
        elif operation == "drop_nulls":
            subset = draw(
                st.one_of(
                    st.none(),
                    st.lists(
                        st.sampled_from(sorted(columns)),
                        min_size=1,
                        max_size=len(columns),
                        unique=True,
                    ),
                )
            )
            argument = "" if subset is None else repr(sorted(subset))
            lines.append(f"df = df.drop_nulls({argument})")
        elif operation == "with_row_index":
            name = f"index_{fresh}"
            fresh += 1
            lines.append(f"df = df.with_row_index({name!r})")
            columns.append(name)
            numeric.add(name)
        elif operation == "str_filter":
            column = draw(st.sampled_from(sorted(strings)))
            method = draw(st.sampled_from(("contains", "starts_with")))
            lines.append(f"df = df.filter(pl.col({column!r}).str.{method}('x'))")
        elif operation == "dt_year":
            column = draw(st.sampled_from(sorted(temporal)))
            alias = f"year_{fresh}"
            fresh += 1
            lines.append(f"df = df.with_columns(pl.col({column!r}).dt.year().alias({alias!r}))")
            columns.append(alias)
            numeric.add(alias)
        elif operation == "unpivot":
            on = draw(
                st.lists(
                    st.sampled_from(sorted(numeric)),
                    min_size=1,
                    max_size=len(numeric),
                    unique=True,
                )
            )
            remaining = sorted(set(columns) - set(on))
            index = draw(
                st.lists(
                    st.sampled_from(remaining),
                    min_size=1,
                    max_size=len(remaining),
                    unique=True,
                )
            )
            lines.append(f"df = df.unpivot(on={sorted(on)!r}, index={sorted(index)!r})")
            unpivoted = True
            columns = [*sorted(index), "variable", "value"]
            numeric = (numeric & set(index)) | {"value"}
            strings = strings & set(index)
            temporal = temporal & set(index)
        elif operation == "filter":
            column = draw(st.sampled_from(sorted(numeric)))
            lines.append(f"df = df.filter(pl.col({column!r}) >= 0)")
        elif operation == "sort":
            column = draw(st.sampled_from(sorted(columns)))
            lines.append(f"df = df.sort({column!r})")
        elif operation == "rows":
            method = draw(st.sampled_from(("head", "tail", "slice")))
            call = f"{method}(2)" if method != "slice" else "slice(0, 2)"
            lines.append(f"df = df.{call}")
        elif operation == "with_columns":
            first = draw(st.sampled_from(sorted(numeric)))
            second = draw(st.sampled_from(sorted(numeric)))
            alias = f"derived_{fresh}"
            fresh += 1
            lines.append(
                f"df = df.with_columns((pl.col({first!r}) + pl.col({second!r})).alias({alias!r}))"
            )
            columns.append(alias)
            numeric.add(alias)
        elif operation == "select":
            selected = draw(
                st.lists(
                    st.sampled_from(sorted(columns)),
                    min_size=1,
                    max_size=len(columns),
                    unique=True,
                )
            )
            use_expression = draw(st.booleans())
            if use_expression and len(selected) >= 2 and len(numeric) >= 1:
                first, second = draw(
                    st.lists(st.sampled_from(sorted(numeric)), min_size=2, max_size=2)
                )
                alias = f"selected_{fresh}"
                fresh += 1
                selected = selected[:-1]
                expression = f"(pl.col({first!r}) + pl.col({second!r})).alias({alias!r})"
                arguments = ", ".join([*(repr(column) for column in selected), expression])
                columns = [*selected, alias]
                numeric = (numeric & set(selected)) | {alias}
            else:
                arguments = repr(selected)
                columns = selected
                numeric &= set(selected)
            lines.append(f"df = df.select({arguments})")
            strings &= set(columns)
            temporal &= set(columns)
        else:
            source = draw(st.sampled_from(sorted(columns)))
            target = f"renamed_{fresh}"
            fresh += 1
            lines.append(f"df = df.rename({{{source!r}: {target!r}}})")
            columns[columns.index(source)] = target
            for kind in (numeric, strings, temporal):
                if source in kind:
                    kind.discard(source)
                    kind.add(target)
    demand = tuple(
        draw(
            st.lists(
                st.sampled_from(sorted(columns)),
                min_size=1,
                max_size=len(columns),
                unique=True,
            )
        )
    )
    return Program("\n".join(lines), tuple(columns), demand)


@settings(max_examples=75, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=_unary_programs())
def test_unary_lineage_matches_projected_execution(program: Program) -> None:
    schema = frozenset(ROWS.columns)
    analysis = analyze_polars_lineage(program.code, {"rows": schema}, program.demand)
    full = _run(program.code, rows=ROWS)
    _assert_metadata(analysis, {"rows": schema}, full)

    projected = _run(
        program.code,
        rows=ROWS.select(sorted(analysis.demands_by_input["rows"])),
    )
    assert_frame_equal(projected.select(list(program.demand)), full.select(list(program.demand)))


@st.composite
def _group_programs(draw: st.DrawFn) -> Program:
    keys = draw(st.lists(st.sampled_from(("g", "a", "b")), min_size=1, max_size=2, unique=True))
    aggregate_count = draw(st.integers(min_value=1, max_value=2))
    expressions: list[str] = []
    aggregate_aliases: list[str] = []
    for index in range(aggregate_count):
        source = draw(st.sampled_from(("a", "b")))
        method = draw(st.sampled_from(("sum", "mean", "min", "max", "count")))
        alias = f"{method}_{source}_{index}"
        aggregate_aliases.append(alias)
        expressions.append(f"pl.col({source!r}).{method}().alias({alias!r})")
    output = [*keys, *aggregate_aliases]
    demand = tuple(
        draw(st.lists(st.sampled_from(output), min_size=1, max_size=len(output), unique=True))
    )
    return Program(
        "df = rows.group_by("
        + ", ".join(repr(key) for key in keys)
        + ").agg("
        + ", ".join(expressions)
        + ")",
        tuple(output),
        demand,
    )


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=_group_programs())
def test_group_by_lineage_matches_projected_execution(program: Program) -> None:
    schema = frozenset(GROUP_ROWS.columns)
    analysis = analyze_polars_lineage(program.code, {"rows": schema}, program.demand)
    full = _run(program.code, rows=GROUP_ROWS)
    _assert_metadata(analysis, {"rows": schema}, full)
    projected = _run(
        program.code,
        rows=GROUP_ROWS.select(sorted(analysis.demands_by_input["rows"])),
    )
    expected = full.select(list(program.demand))
    actual = projected.select(list(program.demand))
    # Polars does not promise group order unless ``maintain_order=True``. A
    # demanded subset may also omit one of several grouping keys, so sorting by
    # only the visible keys is not a canonical order when those keys repeat.
    _assert_unordered_equal(actual, expected)


@st.composite
def _join_programs(draw: st.DrawFn) -> Program:
    how = draw(st.sampled_from(("inner", "left", "semi", "anti")))
    explicit_suffix = draw(st.booleans())
    suffix = "_r" if explicit_suffix else "_right"
    suffix_argument = ", suffix='_r'" if explicit_suffix else ""
    code = f"df = left.join(right, on='id', how={how!r}{suffix_argument})"
    if how in {"semi", "anti"}:
        output = tuple(LEFT_ROWS.columns)
    else:
        output = (
            "id",
            "left_value",
            "shared",
            "left_unused",
            "right_value",
            f"shared{suffix}",
            "right_unused",
        )
    demand = tuple(
        draw(st.lists(st.sampled_from(output), min_size=1, max_size=len(output), unique=True))
    )
    return Program(code, output, demand)


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=_join_programs())
def test_join_lineage_matches_separately_projected_execution(program: Program) -> None:
    schemas = {"left": frozenset(LEFT_ROWS.columns), "right": frozenset(RIGHT_ROWS.columns)}
    analysis = analyze_polars_lineage(program.code, schemas, program.demand)
    full = _run(program.code, left=LEFT_ROWS, right=RIGHT_ROWS)
    _assert_metadata(analysis, schemas, full)
    projected = _run(
        program.code,
        left=LEFT_ROWS.select(sorted(analysis.demands_by_input["left"])),
        right=RIGHT_ROWS.select(sorted(analysis.demands_by_input["right"])),
    )
    expected = full.select(list(program.demand))
    actual = projected.select(list(program.demand))
    if "id" in program.demand:
        assert_frame_equal(actual.sort("id"), expected.sort("id"))
    else:
        _assert_unordered_equal(actual, expected)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=_unary_programs())
def test_supported_lineage_metadata_is_bounded_by_exact_schemas(program: Program) -> None:
    schema = frozenset(ROWS.columns)
    full = _run(program.code, rows=ROWS)
    analysis = analyze_polars_lineage(program.code, {"rows": schema}, program.demand)
    _assert_metadata(analysis, {"rows": schema}, full)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=_unary_programs())
def test_unary_cardinality_bounds_every_row_shape(program: Program) -> None:
    for frame in (ROWS, EMPTY_ROWS, NULL_ROWS, NAN_ROWS):
        analysis = analyze_polars_cardinality(program.code, {"rows": len(frame)})
        assert analysis.supported, analysis
        assert analysis.output_upper_bound is not None
        assert analysis.peak_upper_bound is not None
        assert len(_run(program.code, rows=frame)) <= analysis.output_upper_bound
        assert analysis.peak_upper_bound >= analysis.output_upper_bound
