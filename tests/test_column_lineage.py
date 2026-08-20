"""Regression coverage for exact, AST-backed Polars column lineage."""

from __future__ import annotations

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._column_lineage import analyze_polars_lineage
from haute._user_exec import _exec_user_code


def test_unseeded_select_has_an_exact_output_and_minimal_unknown_input_demand() -> None:
    result = analyze_polars_lineage("df = rows.select(['a'])", {"rows": None})

    assert result.supported
    assert result.exact_output_columns == frozenset({"a"})
    assert result.demands_by_input == {"rows": frozenset({"a"})}


def test_row_count_select_expresses_an_exact_empty_column_demand() -> None:
    result = analyze_polars_lineage(
        "df = rows.select(pl.len().alias('row_count'))",
        {"rows": frozenset({"a", "b"})},
    )

    assert result.supported
    assert result.exact_output_columns == frozenset({"row_count"})
    assert result.demands_by_input == {"rows": frozenset()}


@pytest.mark.parametrize("code", ["df = rows", "df = df"])
def test_identity_program_propagates_a_seeded_demand(code: str) -> None:
    result = analyze_polars_lineage(code, {"rows": None}, {"a"})

    assert result.supported
    assert result.exact_output_columns is None
    assert result.demands_by_input == {"rows": frozenset({"a"})}


def test_unseeded_sort_then_select_retains_the_sort_key() -> None:
    result = analyze_polars_lineage("df = rows.sort('sort_key').select(['a'])", {"rows": None})

    assert result.supported
    assert result.exact_output_columns == frozenset({"a"})
    assert result.demands_by_input == {"rows": frozenset({"a", "sort_key"})}


def test_bare_string_with_columns_is_a_column_expression() -> None:
    result = analyze_polars_lineage(
        "df = rows.with_columns('a').select(['a'])",
        {"rows": frozenset({"a", "unused"})},
    )

    assert result.supported
    assert result.exact_output_columns == frozenset({"a"})
    assert result.demands_by_input == {"rows": frozenset({"a"})}


def test_named_bare_string_expression_demands_its_source_column() -> None:
    result = analyze_polars_lineage(
        "df = rows.with_columns(copy='a').select(['copy'])",
        {"rows": frozenset({"a", "unused"})},
    )

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"a"})}


def test_unregistered_expression_method_with_string_argument_fails_closed() -> None:
    result = analyze_polars_lineage(
        (
            "df = rows.with_columns("
            "pl.when(pl.col('a').is_null()).then('backup')"
            ".otherwise(pl.col('a')).alias('value'))"
        ),
        {"rows": frozenset({"a", "backup"})},
    )

    assert not result.supported
    assert result.reason == "dynamic_with_columns"


@pytest.mark.parametrize(
    "expression",
    [
        "custom(pl.col('a')).alias('value')",
        "(pl.col('a') > threshold()).alias('value')",
        "np.log(pl.col('a')).alias('value')",
    ],
)
def test_external_expression_calls_fail_closed(expression: str) -> None:
    result = analyze_polars_lineage(
        f"df = rows.with_columns({expression}).select(['value'])",
        {"rows": frozenset({"a", "unused"})},
    )

    assert not result.supported
    assert result.reason == "dynamic_with_columns"


def test_registered_string_date_format_is_scalar_configuration() -> None:
    code = (
        "df = rows.with_columns("
        "pl.col('date_of_birth').str.to_date('%Y-%m-%d').dt.year()"
        ".alias('birth_year')).select(['birth_year'])"
    )
    result = analyze_polars_lineage(
        code,
        {"rows": frozenset({"date_of_birth", "unused"})},
    )

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"date_of_birth"})}

    frame = pl.DataFrame({"date_of_birth": ["2000-01-02"], "unused": [1]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_composed_transforms_and_two_joins_route_each_parent_minimally() -> None:
    code = """\
df = left.with_columns((pl.col('amount') * pl.col('rate')).alias('priced'))
df = df.join(middle, on='id')
df = df.join(right, left_on='right_id', right_on='id')
df = df.select(['name', 'priced', 'middle_value', 'right_value'])
"""
    result = analyze_polars_lineage(
        code,
        {
            "left": frozenset({"id", "name", "amount", "rate", "right_id", "ignored"}),
            "middle": frozenset({"id", "middle_value", "ignored_middle"}),
            "right": frozenset({"id", "right_value", "ignored_right"}),
        },
    )

    assert result.supported
    assert result.exact_output_columns == frozenset(
        {"name", "priced", "middle_value", "right_value"}
    )
    assert result.demands_by_input == {
        "left": frozenset({"id", "name", "amount", "rate", "right_id"}),
        "middle": frozenset({"id", "middle_value"}),
        "right": frozenset({"id", "right_value"}),
    }


def test_group_by_aggregate_sort_select_composes() -> None:
    code = """\
df = rows.group_by('group').agg(
    pl.col('amount').sum().alias('total'),
    pl.col('weight').mean().alias('mean_weight'),
).sort('total').select(['group', 'total'])
"""
    result = analyze_polars_lineage(
        code,
        {"rows": frozenset({"group", "amount", "weight", "ignored"})},
    )

    assert result.supported
    assert result.exact_output_columns == frozenset({"group", "total"})
    assert result.demands_by_input == {"rows": frozenset({"group", "amount", "weight"})}


def test_unique_subset_and_explode_contribute_cardinality_columns() -> None:
    result = analyze_polars_lineage(
        "df = rows.unique(subset=['entity_id']).explode('items').select(['value'])",
        {"rows": frozenset({"entity_id", "items", "value", "unused"})},
    )

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"entity_id", "items", "value"})}


def test_unique_without_subset_requires_the_complete_exact_input_schema() -> None:
    result = analyze_polars_lineage(
        "df = rows.unique().select(['value'])",
        {"rows": frozenset({"entity_id", "value", "unused"})},
    )

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"entity_id", "value", "unused"})}


def test_unknown_schema_rename_and_regex_selector_fail_closed() -> None:
    rename = analyze_polars_lineage(
        "df = rows.rename({'raw': 'value'}).select(['value'])",
        {"rows": None},
    )
    selector = analyze_polars_lineage(
        "df = rows.select(pl.col('^value.*$'))",
        {"rows": frozenset({"value_a", "value_b"})},
    )

    assert not rename.supported
    assert rename.reason == "rename_schema_ambiguous"
    assert not selector.supported
    assert selector.reason == "dynamic_select"


@pytest.mark.parametrize(
    "code",
    [
        "df = rows.sort(sort_key).select(['a'])",
        "if enabled:\n    df = rows.select(['a'])",
    ],
)
def test_dynamic_operations_and_control_flow_fail_closed(code: str) -> None:
    result = analyze_polars_lineage(code, {"rows": None})

    assert not result.supported
    assert result.demands_by_input == {}
    assert result.unsupported_operation is not None


@pytest.mark.parametrize(
    ("code", "frames"),
    [
        (
            "df = rows.sort('sort_key').select(['a'])",
            {"rows": pl.DataFrame({"a": [2, 1], "sort_key": [1, 2], "unused": [0, 0]})},
        ),
        (
            (
                "df = rows.with_columns((pl.col('a') + pl.col('b')).alias('sum'))"
                ".filter(pl.col('sum') > 2).select(['sum'])"
            ),
            {"rows": pl.DataFrame({"a": [1, 2], "b": [2, 1], "unused": [0, 0]})},
        ),
        (
            "df = rows.with_columns(copy='a').select(['copy'])",
            {"rows": pl.DataFrame({"a": [1, 2], "unused": [0, 0]})},
        ),
        (
            (
                "df = rows.group_by('group').agg(pl.col('amount').sum().alias('total'))"
                ".sort('group').select(['group', 'total'])"
            ),
            {
                "rows": pl.DataFrame(
                    {"group": ["a", "a", "b"], "amount": [1, 2, 3], "unused": [0, 0, 0]}
                )
            },
        ),
        (
            (
                "df = left.join(middle, on='id').join(right, on='id')"
                ".select(['left_value', 'middle_value', 'right_value'])"
            ),
            {
                "left": pl.DataFrame({"id": [1, 2], "left_value": [10, 20], "unused_left": [0, 0]}),
                "middle": pl.DataFrame(
                    {"id": [1, 2], "middle_value": [30, 40], "unused_middle": [0, 0]}
                ),
                "right": pl.DataFrame(
                    {"id": [1, 2], "right_value": [50, 60], "unused_right": [0, 0]}
                ),
            },
        ),
        (
            (
                "df = rows.unique(subset=['entity_id'], maintain_order=True)"
                ".explode('items').sort('entity_id')"
                ".select(['entity_id', 'items', 'value'])"
            ),
            {
                "rows": pl.DataFrame(
                    {
                        "entity_id": [2, 1],
                        "items": [[20, 21], [10]],
                        "value": [200, 100],
                        "unused": [0, 0],
                    }
                )
            },
        ),
    ],
)
def test_projected_inputs_execute_identically_to_full_inputs(
    code: str, frames: dict[str, pl.DataFrame]
) -> None:
    result = analyze_polars_lineage(
        code, {name: frozenset(frame.columns) for name, frame in frames.items()}
    )
    assert result.supported

    names = list(frames)
    full = _exec_user_code(code, names, tuple(frame.lazy() for frame in frames.values())).collect()
    projected = _exec_user_code(
        code,
        names,
        tuple(frames[name].select(sorted(result.demands_by_input[name])).lazy() for name in names),
    ).collect()

    assert_frame_equal(projected, full)


@pytest.mark.parametrize(
    ("code", "inputs", "demand", "output", "demands"),
    [
        (
            "df = rows.select(pl.col('a').len())",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        ("df = rows.select(pl.len())", {"rows": frozenset({"a"})}, None, {"len"}, {"rows": set()}),
        ("df = rows.select(pl.lit(1))", {"rows": None}, None, {"literal"}, {"rows": set()}),
        ("df = rows.select(True)", {"rows": None}, None, {"literal"}, {"rows": set()}),
        (
            "df = rows.select(pl.col('a').str.to_uppercase())",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        (
            "df = rows.select(pl.col('a') & pl.col('b'))",
            {"rows": frozenset({"a", "b"})},
            None,
            {"a"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.select(pl.col('a') | pl.col('b'))",
            {"rows": frozenset({"a", "b"})},
            None,
            {"a"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.select(pl.col('a').name.suffix(suffix))",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        (
            "df = rows.select(pl.sum_horizontal(pl.col('a') + pl.col('b')))",
            {"rows": frozenset({"a", "b"})},
            None,
            {"a"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.select(pl.col('a') and pl.col('b'))",
            {"rows": frozenset({"a", "b"})},
            None,
            {"a"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.select(-pl.col('a'))",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        (
            "df = rows.select((pl.col('a') + 1) > 2)",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        (
            "df = rows.select(pl.col('a').str.to_date('%Y').name.suffix('_date'))",
            {"rows": frozenset({"a"})},
            None,
            {"a_date"},
            {"rows": {"a"}},
        ),
        (
            "df = rows.select(pl.sum_horizontal('a', 'b'))",
            {"rows": frozenset({"a", "b"})},
            None,
            {"a"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.select(pl.all_horizontal(pl.col('a'), pl.col('b')).alias('both'))",
            {"rows": frozenset({"a", "b"})},
            None,
            {"both"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.select(['a', ('b',)])",
            {"rows": frozenset({"a", "b"})},
            None,
            {"a", "b"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.with_columns(pl.lit(1).alias('one')).select('one')",
            {"rows": None},
            None,
            {"one"},
            {"rows": set()},
        ),
        (
            "df = rows.rename({'a': 'b'}, strict=True).select('b')",
            {"rows": frozenset({"a", "x"})},
            None,
            {"b"},
            {"rows": {"a"}},
        ),
        (
            (
                "df = rows.filter(pl.col('a') > 0).fill_null(0).head(1).tail(1)"
                ".limit(1).slice(0, 1).select('b')"
            ),
            {"rows": frozenset({"a", "b"})},
            None,
            {"b"},
            {"rows": {"a", "b"}},
        ),
        (
            (
                "df = rows.sort(by=['a'], descending=True, nulls_last=False, "
                "maintain_order=True, multithreaded=True).select('b')"
            ),
            {"rows": frozenset({"a", "b"})},
            None,
            {"b"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.unique(['a'], keep='first', maintain_order=True).select('b')",
            {"rows": frozenset({"a", "b"})},
            None,
            {"b"},
            {"rows": {"a", "b"}},
        ),
        (
            "df = rows.explode(columns=['items']).select('a')",
            {"rows": frozenset({"a", "items"})},
            None,
            {"a"},
            {"rows": {"a", "items"}},
        ),
        (
            (
                "df = rows.group_by(by=['g'], maintain_order=True).agg("
                "[pl.col('a').sum().alias('total')], copied='b')"
            ),
            {"rows": frozenset({"g", "a", "b"})},
            None,
            {"g", "total", "copied"},
            {"rows": {"g", "a", "b"}},
        ),
        (
            "df = left.join(right, on=pl.col('id'), how='left', suffix='_r').select(['a', 'b'])",
            {"left": frozenset({"id", "a"}), "right": frozenset({"id", "b"})},
            None,
            {"a", "b"},
            {"left": {"id", "a"}, "right": {"id", "b"}},
        ),
        (
            (
                "df = left.join(right, left_on=['lid', 'x'], right_on=['rid', 'y'], "
                "how='inner').select(['a', 'b'])"
            ),
            {"left": frozenset({"lid", "x", "a"}), "right": frozenset({"rid", "y", "b"})},
            None,
            {"a", "b"},
            {"left": {"lid", "x", "a"}, "right": {"rid", "y", "b"}},
        ),
        (
            "df = left.join(right, on='id', how='semi').select('a')",
            {"left": frozenset({"id", "a"}), "right": frozenset({"id", "b"})},
            None,
            {"a"},
            {"left": {"id", "a"}, "right": {"id"}},
        ),
        (
            "df = left.join(right, on='id', how='anti').select('a')",
            {"left": frozenset({"id", "a"}), "right": frozenset({"id", "b"})},
            None,
            {"a"},
            {"left": {"id", "a"}, "right": {"id"}},
        ),
        (
            "import polars as pl\n'comment'\nvalue = 1\ndf = rows.select('a')",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        (
            "df = rows\ndf = df.select('a')",
            {"rows": frozenset({"a"})},
            None,
            {"a"},
            {"rows": {"a"}},
        ),
        ("df = rows\ndf = df", {"rows": None}, ["a"], None, {"rows": {"a"}}),
        ("df = df.select('a')", {"rows": frozenset({"a"})}, None, {"a"}, {"rows": {"a"}}),
    ],
)
def test_closed_operations_have_exact_structured_lineage(
    code, inputs, demand, output, demands
) -> None:
    result = analyze_polars_lineage(code, inputs, demand)
    assert result.supported, result
    assert result.reason == "lineage_proven"
    assert result.unsupported_operation is None
    assert result.exact_output_columns == (None if output is None else frozenset(output))
    assert result.demands_by_input == {
        name: frozenset(columns) for name, columns in demands.items()
    }


@pytest.mark.parametrize(
    ("code", "inputs", "demand", "reason", "operation"),
    [
        ("df = rows.select()", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select('a', 'a')", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select(**values)", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select(pl.sum_horizontal())", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select(pl.concat_str('a'))", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select(pl.col('*'))", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select(pl.all())", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.select(pl.concat_str())", {"rows": None}, None, "dynamic_select", "select"),
        (
            "df = rows.select(pl.col('a').is_in(['x']))",
            {"rows": None},
            None,
            "dynamic_select",
            "select",
        ),
        ("df = rows.select(value=custom())", {"rows": None}, None, "dynamic_select", "select"),
        ("df = rows.sort(['a', columns])", {"rows": None}, None, "dynamic_sort", "sort"),
        (
            "df = rows.with_columns(['a'])",
            {"rows": None},
            None,
            "dynamic_with_columns",
            "with_columns",
        ),
        (
            "df = rows.with_columns(**values)",
            {"rows": None},
            None,
            "dynamic_with_columns",
            "with_columns",
        ),
        (
            "df = rows.select(pl.when(pl.col('a')).then(pl.col('b')).otherwise(pl.col('c')))",
            {"rows": frozenset({"a", "b", "c"})},
            None,
            "dynamic_select",
            "select",
        ),
        (
            "df = rows.rename({'a': 'b'}, strict=False)",
            {"rows": frozenset({"a"})},
            None,
            "dynamic_rename",
            "rename",
        ),
        (
            "df = rows.rename({'a': 'b'}, unknown=True)",
            {"rows": frozenset({"a"})},
            None,
            "dynamic_rename",
            "rename",
        ),
        (
            "df = rows.rename({'a': 'b'}, {'c': 'd'})",
            {"rows": None},
            None,
            "dynamic_rename",
            "rename",
        ),
        (
            "df = rows.rename({'a': 'b', 'c': 'b'})",
            {"rows": None},
            ["b"],
            "rename_schema_ambiguous",
            "rename",
        ),
        (
            "df = rows.rename({'a': 'b', 'a': 'c'})",
            {"rows": None},
            ["b"],
            "dynamic_rename",
            "rename",
        ),
        ("df = rows.rename({**mapping})", {"rows": None}, ["a"], "dynamic_rename", "rename"),
        ("df = rows.rename(['a'])", {"rows": None}, ["a"], "dynamic_rename", "rename"),
        (
            "df = rows.rename({'a': 'b'}, strict=1)",
            {"rows": None},
            ["b"],
            "dynamic_rename",
            "rename",
        ),
        (
            "df = rows.filter(pl.col('a'), **opts)",
            {"rows": frozenset({"a"})},
            None,
            "dynamic_filter",
            "filter",
        ),
        ("df = rows.fill_null(custom())", {"rows": None}, None, "dynamic_fill_null", "fill_null"),
        ("df = rows.sort()", {"rows": None}, None, "dynamic_sort", "sort"),
        ("df = rows.sort('a', unstable=True)", {"rows": None}, None, "dynamic_sort", "sort"),
        ("df = rows.unique('a', 'b')", {"rows": None}, None, "dynamic_unique", "unique"),
        ("df = rows.unique('a', subset='b')", {"rows": None}, None, "dynamic_unique", "unique"),
        ("df = rows.unique(foo=True)", {"rows": None}, None, "dynamic_unique", "unique"),
        ("df = rows.unique(subset=columns)", {"rows": None}, None, "dynamic_unique", "unique"),
        ("df = rows.explode()", {"rows": None}, None, "dynamic_explode", "explode"),
        ("df = rows.explode(a='b')", {"rows": None}, None, "dynamic_explode", "explode"),
        ("df = rows.explode(columns=columns)", {"rows": None}, None, "dynamic_explode", "explode"),
        ("df = rows.explode(columns)", {"rows": None}, None, "dynamic_explode", "explode"),
        (
            "df = rows.group_by().agg(pl.col('a').sum().alias('x'))",
            {"rows": None},
            None,
            "dynamic_group_by",
            "group_by",
        ),
        (
            "df = rows.group_by('g', bad=True).agg(pl.col('a').sum().alias('x'))",
            {"rows": None},
            None,
            "dynamic_group_by",
            "group_by",
        ),
        (
            "df = rows.group_by(g).agg(pl.col('a').sum().alias('x'))",
            {"rows": None},
            None,
            "dynamic_group_by",
            "group_by",
        ),
        (
            "df = rows.group_by('g').agg(pl.col('a').sum())",
            {"rows": None},
            None,
            "dynamic_aggregate",
            "agg",
        ),
        ("df = rows.group_by('g').agg(custom())", {"rows": None}, None, "dynamic_aggregate", "agg"),
        (
            "df = rows.group_by('g').agg(value=custom())",
            {"rows": None},
            None,
            "dynamic_aggregate",
            "agg",
        ),
        ("df = rows.group_by('g').agg(**values)", {"rows": None}, None, "dynamic_aggregate", "agg"),
        (
            "df = rows.group_by('g').agg(pl.col('a').sum().alias('g'))",
            {"rows": None},
            None,
            "ambiguous_aggregate_output",
            "agg",
        ),
        ("df = rows.group_by('g')", {"rows": None}, None, "incomplete_group_by", "group_by"),
        (
            "df = rows.group_by('g').select('g')",
            {"rows": None},
            None,
            "incomplete_group_by",
            "group_by",
        ),
        ("df = rows.agg(pl.col('a'))", {"rows": None}, None, "orphan_aggregate", "agg"),
        ("df = left.join(other, on='id')", {"left": None}, None, "unknown_join_input", "join"),
        (
            "df = left.join(right, 'id')",
            {"left": None, "right": None},
            None,
            "dynamic_join_input",
            "join",
        ),
        (
            "df = left.join(right, on='id', left_on='id', right_on='id')",
            {"left": None, "right": None},
            None,
            "ambiguous_join_keys",
            "join",
        ),
        ("df = left.join(right)", {"left": None, "right": None}, None, "dynamic_join_keys", "join"),
        (
            "df = left.join(right, how='outer', on='id')",
            {"left": None, "right": None},
            None,
            "unsupported_join_semantics",
            "join",
        ),
        (
            "df = left.join(right, on='id', validate='1:1')",
            {"left": None, "right": None},
            None,
            "unsupported_join_option",
            "join",
        ),
        (
            "df = left.join(right, on=['a', 'b'], left_on='a')",
            {"left": None, "right": None},
            None,
            "ambiguous_join_keys",
            "join",
        ),
        (
            "df = left.join(right, left_on=['a'], right_on=['x', 'y'])",
            {"left": None, "right": None},
            None,
            "dynamic_join_keys",
            "join",
        ),
        (
            "df = left.join(right, left_on=['a', 'a'], right_on='x')",
            {"left": None, "right": None},
            None,
            "dynamic_join_keys",
            "join",
        ),
        (
            "df = left.join(right, on=key)",
            {"left": None, "right": None},
            None,
            "dynamic_join_keys",
            "join",
        ),
        (
            "df = left.join(right, on='id')",
            {"left": None, "right": None},
            None,
            "join_schema_unknown",
            "join",
        ),
        (
            "df = left.join(right, on='id')",
            {"left": frozenset({"id", "v", "v_right"}), "right": frozenset({"id", "v"})},
            None,
            "join_schema_ambiguous",
            "join",
        ),
        (
            "df = rows.select('missing')",
            {"rows": frozenset({"a"})},
            None,
            "operation_input_missing",
            "select",
        ),
        (
            "df = rows.rename({'missing': 'b'})",
            {"rows": frozenset({"a"})},
            None,
            "invalid_rename",
            "rename",
        ),
        (
            "df = rows.rename({'a': 'b'})",
            {"rows": frozenset({"a", "b"})},
            None,
            "invalid_rename",
            "rename",
        ),
        ("df = rows", {"rows": None}, None, "output_schema_unknown", None),
        ("pass", {"rows": None}, None, "no_frame_root", None),
        (
            "df = rows.rename({'a': 'b'})",
            {"rows": None},
            ["b"],
            "rename_schema_ambiguous",
            "rename",
        ),
        (
            "df = rows.join(right, on='id')",
            {"rows": frozenset({"a"}), "right": frozenset({"id"})},
            None,
            "join_schema_ambiguous",
            "join",
        ),
        ("df = rows.unique()", {"rows": None}, ["a"], "unique_schema_unknown", "unique"),
        (
            "df = rows.select('a')",
            {"rows": frozenset({"a"})},
            ["missing"],
            "demand_outside_output_schema",
            None,
        ),
        ("df = rows.select('a')", {"rows": frozenset({"a"})}, [""], "invalid_output_demand", None),
        ("if True:\n    df = rows", {"rows": None}, None, "non_linear_control_flow", "If"),
        ("(df,) = (rows,)", {"rows": None}, None, "non_linear_assignment", None),
        ("x = rows", {"rows": None}, None, "frame_dependent_helper", None),
        ("x = custom()\ndf = rows", {"rows": None}, None, "dynamic_helper", None),
        ("df = unknown.select('a')", {"rows": None}, None, "unknown_frame_root", None),
        (
            "df = left.select('a')\ndf = right.select('b')",
            {"left": None, "right": None},
            None,
            "frame_root_reset",
            None,
        ),
        ("df = rows.select('a')\ndf = df", {"rows": None}, None, "unknown_frame_root", None),
        ("df = 1", {"rows": None}, None, "unknown_frame_root", None),
        ("df = df.select('a')", {"left": None, "right": None}, None, "ambiguous_frame_root", None),
        ("df = df", {"left": None, "right": None}, None, "unknown_frame_root", None),
        ("df = rows.unsupported()", {"rows": None}, None, "unsupported_operation", "unsupported"),
        ("df = rows.select('a'", {"rows": None}, None, "syntax_error", None),
    ],
)
def test_rejected_syntax_has_a_precise_fail_closed_reason(
    code, inputs, demand, reason, operation
) -> None:
    result = analyze_polars_lineage(code, inputs, demand)
    assert not result.supported
    assert result.reason == reason
    assert result.unsupported_operation == operation
    assert result.exact_output_columns is None
    assert result.demands_by_input == {}


@pytest.mark.parametrize(
    ("code", "inputs", "reason"),
    [
        ("", {"rows": None}, "empty_code"),
        ("df = rows", {}, "invalid_inputs"),
        ("df = rows", {"": None}, "invalid_inputs"),
    ],
)
def test_invalid_public_inputs_fail_closed(code, inputs, reason) -> None:
    result = analyze_polars_lineage(code, inputs)
    assert not result.supported
    assert result.reason == reason


def test_unknown_schema_rename_permutation_translates_an_explicit_demand() -> None:
    result = analyze_polars_lineage("df = rows.rename({'a': 'b', 'b': 'a'})", {"rows": None}, ["a"])

    assert result.supported
    assert result.exact_output_columns is None
    assert result.demands_by_input == {"rows": frozenset({"b"})}
