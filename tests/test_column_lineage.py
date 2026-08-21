"""Regression coverage for exact, AST-backed Polars column lineage."""

from __future__ import annotations

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._column_lineage import analyze_polars_cardinality, analyze_polars_lineage
from haute._user_exec import _exec_user_code


def test_unseeded_select_has_an_exact_output_and_minimal_unknown_input_demand() -> None:
    result = analyze_polars_lineage("df = rows.select(['a'])", {"rows": None})

    assert result.supported
    assert result.exact_output_columns == frozenset({"a"})
    assert result.demands_by_input == {"rows": frozenset({"a"})}


def test_cardinality_analysis_bounds_many_to_many_join_output_and_peak() -> None:
    result = analyze_polars_cardinality(
        "df = left.join(right, on='id', validate='m:m')",
        {"left": 4, "right": 3},
    )

    assert result.supported
    assert result.reason == "cardinality_proven"
    assert result.output_upper_bound == 12
    assert result.peak_upper_bound == 12
    assert result.evidence


def test_cardinality_analysis_uses_join_validate_to_tighten_the_bound() -> None:
    result = analyze_polars_cardinality(
        "df = left.join(right, on='id', validate='m:1')",
        {"left": 4, "right": 3},
    )

    assert result.supported
    assert result.output_upper_bound == 4
    assert result.peak_upper_bound == 4


@pytest.mark.parametrize(
    ("code", "expected_output", "expected_peak"),
    [
        ("df = left.join(right, on='id', how='right')", 12, 12),
        ("df = left.join(right, on='id', how='full')", 12, 12),
        ("df = left.join(right, how='cross')", 12, 12),
        ("df = left.join(right, on='id', how='semi')", 4, 4),
        ("df = left.join(right, on='id', how='anti')", 4, 4),
    ],
)
def test_cardinality_analysis_supports_every_closed_join_strategy(
    code: str,
    expected_output: int,
    expected_peak: int,
) -> None:
    result = analyze_polars_cardinality(code, {"left": 4, "right": 3})

    assert result.supported
    assert result.output_upper_bound == expected_output
    assert result.peak_upper_bound == expected_peak


def test_cardinality_analysis_group_by_never_increases_the_input_bound() -> None:
    result = analyze_polars_cardinality(
        "df = rows.group_by('group').agg(pl.col('amount').sum())",
        {"rows": 9},
    )

    assert result.supported
    assert result.output_upper_bound == 9
    assert result.peak_upper_bound == 9


def test_cardinality_analysis_fails_closed_for_explode() -> None:
    result = analyze_polars_cardinality("df = rows.explode('items')", {"rows": 3})

    assert not result.supported
    assert result.reason == "row_expansion_unbounded"
    assert result.unsupported_operation == "explode"


@pytest.mark.parametrize(
    "code",
    [
        "df = rows.select(pl.col('items').explode())",
        "df = rows.select(pl.int_range(0, 100).alias('generated'))",
        "df = rows.with_columns(pl.col('value').append(pl.col('value')).alias('twice'))",
    ],
)
def test_cardinality_analysis_fails_closed_for_row_expanding_expressions(code: str) -> None:
    result = analyze_polars_cardinality(code, {"rows": 3})

    assert not result.supported
    assert result.reason == "row_expansion_unbounded"


@pytest.mark.parametrize(
    "code",
    [
        "df = rows.select(pl.lit(1).alias('value'))",
        "df = rows.select(pl.len().alias('row_count'))",
        "df = rows.with_columns(pl.lit(1).alias('value'))",
    ],
)
def test_cardinality_analysis_accounts_for_scalar_expression_on_empty_frame(code: str) -> None:
    result = analyze_polars_cardinality(code, {"rows": 0})

    assert result.supported
    assert result.output_upper_bound == 1
    assert result.peak_upper_bound == 1


@pytest.mark.parametrize(
    "code",
    [
        "df = left.join(right, on='id', validate=contract)",
        "df = left.join(right, on='id', validate='not-a-contract')",
    ],
)
def test_cardinality_analysis_fails_closed_for_dynamic_or_invalid_validate(code: str) -> None:
    result = analyze_polars_cardinality(code, {"left": 3, "right": 3})

    assert not result.supported
    assert result.unsupported_operation == "join"


@pytest.mark.parametrize(
    "code",
    [
        "df = rows.with_columns(pl.col('value').map_elements(lambda x: x))",
        "df = rows.with_columns(pl.col('value').rolling_map(callback))",
    ],
)
def test_cardinality_analysis_rejects_callbacks_that_can_escape_row_bounds(code: str) -> None:
    result = analyze_polars_cardinality(code, {"rows": 3})

    assert not result.supported
    assert result.reason == "row_expansion_unbounded"


def test_cardinality_analysis_keeps_ordinary_expression_methods_row_bounded() -> None:
    result = analyze_polars_cardinality(
        "df = rows.with_columns(pl.col('value').cast(pl.String).alias('text'))",
        {"rows": 3},
    )

    assert result.supported
    assert result.output_upper_bound == 3


def test_cardinality_analysis_keeps_named_arguments_to_bounded_methods_bounded() -> None:
    result = analyze_polars_cardinality(
        "df = rows.with_columns(pl.col('value').cast(dtype).alias('text'))",
        {"rows": 3},
    )

    assert result.supported
    assert result.output_upper_bound == 3


def test_cardinality_analysis_leaves_bare_expression_calls_to_syntax_validation() -> None:
    result = analyze_polars_cardinality("df = rows.select(helper())", {"rows": 3})

    assert not result.supported
    assert result.reason == "dynamic_select"
    assert result.unsupported_operation == "select"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("df = left.join(right, how='cross', on='id')", "unsupported_join_semantics"),
        ("df = left.join(right, on='id', unknown=True)", "unsupported_join_option"),
    ],
)
def test_cardinality_analysis_rejects_closed_model_join_forms(code: str, reason: str) -> None:
    result = analyze_polars_cardinality(code, {"left": 2, "right": 3})

    assert not result.supported
    assert result.reason == reason


@pytest.mark.parametrize(
    ("code", "inputs"),
    [("", {"rows": 1}), ("df = rows", {}), ("df = rows", {"": 1}), ("df = rows", {"rows": True})],
)
def test_cardinality_analysis_rejects_invalid_proof_inputs(code: object, inputs: object) -> None:
    result = analyze_polars_cardinality(code, inputs)  # type: ignore[arg-type]

    assert not result.supported
    assert result.reason in {"empty_code", "invalid_inputs"}


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


def test_when_string_predicate_demands_its_column() -> None:
    code = "df = rows.select(pl.when('flag').then(pl.col('b')).otherwise(pl.col('c')).alias('x'))"
    result = analyze_polars_lineage(code, {"rows": frozenset({"flag", "b", "c", "unused"})})

    assert result.supported
    assert result.exact_output_columns == frozenset({"x"})
    assert result.demands_by_input == {"rows": frozenset({"flag", "b", "c"})}

    frame = pl.DataFrame({"flag": [True], "b": [1], "c": [2], "unused": [3]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_when_constraint_keywords_demand_their_columns() -> None:
    code = "df = rows.select(pl.when(flag=1).then(pl.col('b')).otherwise(pl.col('c')).alias('x'))"
    result = analyze_polars_lineage(code, {"rows": frozenset({"flag", "b", "c", "unused"})})

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"flag", "b", "c"})}

    frame = pl.DataFrame({"flag": [1], "b": [1], "c": [2], "unused": [3]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


@pytest.mark.parametrize(
    "expression",
    [
        "pl.when(helper).then(pl.col('b')).otherwise(pl.col('c')).alias('x')",
        "pl.when([helper]).then(pl.col('b')).otherwise(pl.col('c')).alias('x')",
        "pl.when('').then(pl.col('b')).otherwise(pl.col('c')).alias('x')",
    ],
)
def test_when_with_unseeable_predicate_fails_closed(expression: str) -> None:
    code = f"helper = 1\ndf = rows.select({expression})"
    result = analyze_polars_lineage(code, {"rows": frozenset({"flag", "b", "c"})})

    assert not result.supported
    assert result.reason == "dynamic_select"


@pytest.mark.parametrize(
    "expression",
    [
        "pl.max_horizontal(helper).alias('m')",
        "pl.max_horizontal(['a', helper]).alias('m')",
        "pl.max_horizontal(**helpers).alias('m')",
        "pl.max_horizontal('a', unknown='z').alias('m')",
        "pl.when(**helpers).then(pl.col('a')).otherwise(None).alias('m')",
    ],
)
def test_horizontal_helper_with_unseeable_argument_fails_closed(expression: str) -> None:
    code = f"helper = 'a'\ndf = rows.select({expression})"
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "z"})})

    assert not result.supported
    assert result.reason == "dynamic_select"


def test_horizontal_helper_accepts_scalar_keyword_configuration() -> None:
    code = "df = rows.select(pl.sum_horizontal('a', 'b', ignore_nulls=True).alias('total'))"
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "b", "unused"})})

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"a", "b"})}

    frame = pl.DataFrame({"a": [1], "b": [2], "unused": [3]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_filter_string_predicate_demands_its_column() -> None:
    code = "df = rows.filter('flag').select(['b'])"
    result = analyze_polars_lineage(code, {"rows": frozenset({"flag", "b", "unused"})})

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"flag", "b"})}

    frame = pl.DataFrame({"flag": [True, False], "b": [1, 2], "unused": [3, 4]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_filter_with_unseeable_predicate_fails_closed() -> None:
    result = analyze_polars_lineage(
        "helper = 'flag'\ndf = rows.filter(helper)",
        {"rows": frozenset({"flag", "b"})},
    )

    assert not result.supported
    assert result.reason == "dynamic_filter"


def test_expression_keyword_outputs_demand_their_references() -> None:
    code = (
        "df = rows.with_columns(doubled=pl.col('a') * 2)"
        ".group_by('g').agg(total=pl.col('doubled').sum())"
    )
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "g", "unused"})})

    assert result.supported
    assert result.exact_output_columns == frozenset({"g", "total"})
    assert result.demands_by_input == {"rows": frozenset({"a", "g"})}

    frame = pl.DataFrame({"a": [1, 2], "g": ["x", "x"], "unused": [3, 4]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_when_scalar_predicate_and_literal_horizontal_arguments_stay_supported() -> None:
    code = (
        "df = rows.select("
        "pl.when(True).then(pl.col('b')).otherwise(pl.col('c')).alias('x'), "
        "pl.max_horizontal('a', 1).alias('m'))"
    )
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "b", "c", "unused"})})

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"a", "b", "c"})}

    frame = pl.DataFrame({"a": [0], "b": [1], "c": [2], "unused": [3]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_horizontal_expression_keyword_contributes_through_the_walk() -> None:
    # An expression keyword is a runtime TypeError either way; the analysis
    # stays sound because the nested reference is still demanded.
    result = analyze_polars_lineage(
        "df = rows.select(pl.max_horizontal('a', b=pl.col('z')))",
        {"rows": frozenset({"a", "z"})},
    )

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"a", "z"})}


def test_unknown_helper_with_clean_scalar_arguments_stays_supported() -> None:
    result = analyze_polars_lineage(
        "df = rows.select(pl.int_range(0, 5).alias('idx'))",
        {"rows": frozenset({"a"})},
    )

    assert result.supported
    assert result.exact_output_columns == frozenset({"idx"})
    assert result.demands_by_input == {"rows": frozenset()}


def test_filter_constraint_keywords_demand_their_columns() -> None:
    code = "df = rows.filter(flag=1).select(['b'])"
    result = analyze_polars_lineage(code, {"rows": frozenset({"flag", "b", "unused"})})

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"flag", "b"})}

    frame = pl.DataFrame({"flag": [1, 2], "b": [1, 2], "unused": [3, 4]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("df = rows.filter(flag=pl.all())", "dynamic_filter"),
        ("df = rows.with_columns(y=pl.all())", "dynamic_with_columns"),
        ("helper = 'a'\ndf = rows.group_by('g').agg(total=helper)", "dynamic_aggregate"),
        ("df = rows.group_by('g').agg(total=pl.all())", "dynamic_aggregate"),
        (
            "df = rows.select((pl.col('a') + f'{suffix}').alias('y'))",
            "dynamic_select",
        ),
    ],
)
def test_schema_dependent_keyword_values_fail_closed(code: str, reason: str) -> None:
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "flag", "g"})})

    assert not result.supported
    assert result.reason == reason


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        # ``or`` returns an operand outright, so a truthy helper string
        # bypasses the expression entirely and reads its own column.
        ("helper = 'a'\ndf = rows.select(copy=helper or pl.col('b'))", "dynamic_select"),
        (
            "prefix = 'fl'\nsuffix = 'ag'\ndf = rows.select("
            "pl.when(prefix + suffix).then(pl.col('b')).otherwise(pl.col('c')).alias('x'))",
            "dynamic_select",
        ),
        ("helper = 'a'\ndf = rows.select(pl.struct([helper]).alias('s'))", "dynamic_select"),
        ("df = rows.select('a'.upper())", "dynamic_select"),
        ("df = rows.select(imported.str.upper().alias('y'))", "dynamic_select"),
        (
            "helper = 'b'\ndf = rows.select("
            "pl.when(pl.col('flag') if helper else helper).then(pl.col('b'))"
            ".otherwise(None).alias('x'))",
            "dynamic_select",
        ),
    ],
)
def test_python_value_expressions_cannot_smuggle_column_names(code: str, reason: str) -> None:
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "b", "c", "flag"})})

    assert not result.supported
    assert result.reason == reason


def test_when_and_horizontal_expression_sequences_stay_supported() -> None:
    code = (
        "df = rows.select("
        "pl.when([pl.col('flag')]).then(pl.col('b')).otherwise(pl.col('c')).alias('x'), "
        "pl.when(['gate']).then(pl.col('b')).otherwise(pl.col('c')).alias('y'), "
        "pl.sum_horizontal([pl.col('a'), pl.lit(1)]).alias('m'))"
    )
    result = analyze_polars_lineage(
        code,
        {"rows": frozenset({"a", "b", "c", "flag", "gate", "unused"})},
    )

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"a", "b", "c", "flag", "gate"})}

    frame = pl.DataFrame(
        {"a": [1], "b": [2], "c": [3], "flag": [True], "gate": [False], "unused": [4]}
    )
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_operator_string_literals_beside_expressions_stay_supported() -> None:
    code = "df = rows.select((pl.col('a') + '_sfx').alias('tagged'))"
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "unused"})})

    assert result.supported
    assert result.demands_by_input == {"rows": frozenset({"a"})}

    frame = pl.DataFrame({"a": ["x"], "unused": [1]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)


def test_identity_rename_pairs_stay_demanded() -> None:
    code = "df = rows.rename({'a': 'a'}).select(['c'])"
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "c", "unused"})})

    assert result.supported
    # A strict rename requires its identity source at runtime even though the
    # schema transfer treats the pair as a no-op.
    assert result.demands_by_input == {"rows": frozenset({"a", "c"})}

    frame = pl.DataFrame({"a": [1], "c": [2], "unused": [3]})
    full = _exec_user_code(code, ["rows"], (frame.lazy(),)).collect()
    projected = _exec_user_code(
        code,
        ["rows"],
        (frame.select(sorted(result.demands_by_input["rows"])).lazy(),),
    ).collect()
    assert_frame_equal(projected, full)

    unknown_schema = analyze_polars_lineage(code, {"rows": None}, ["c"])
    assert unknown_schema.supported
    assert unknown_schema.demands_by_input == {"rows": frozenset({"a", "c"})}


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("df = rows.select(('a' + 'b'))", "dynamic_select"),
        ("helper = 'a'\ndf = rows.with_columns(copy=helper)", "dynamic_with_columns"),
        ("df = rows.select(pl.col(f'{1}'))", "dynamic_select"),
        ("df = rows.select(pl.struct(total='a').alias('s'))", "dynamic_select"),
    ],
)
def test_value_level_string_construction_fails_closed(code: str, reason: str) -> None:
    # A Python-level string can reach Polars as a column name the structural
    # walk never saw; each spelling must reject rather than under-demand.
    result = analyze_polars_lineage(code, {"rows": frozenset({"a", "b", "ab"})})

    assert not result.supported
    assert result.reason == reason


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
            "join_schema_unknown",
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
    # The demanded output "a" translates to its source "b", and the strict
    # rename additionally requires every mapping source at runtime.
    assert result.demands_by_input == {"rows": frozenset({"a", "b"})}


def test_unknown_schema_rename_permutation_demands_every_mapping_source() -> None:
    result = analyze_polars_lineage("df = rows.rename({'a': 'b', 'b': 'a'})", {"rows": None}, ["c"])

    assert result.supported
    # A demand outside the permutation passes through, but projecting away the
    # rename sources would make the strict runtime rename fail on missing
    # columns, so they stay demanded exactly as in the known-schema branch.
    assert result.demands_by_input == {"rows": frozenset({"a", "b", "c"})}
