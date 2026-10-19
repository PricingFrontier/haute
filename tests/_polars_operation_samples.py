"""One sample expression for each registered expression-level operation.

The registry classifies source text, and Polars 2 can say whether an expression
is row-separable (``Expr.meta.is_row_separable()``), so the registry's semantic
classes are checked against Polars' own: every row-local operation builds a
row-separable expression, and no fan-in or order-dependent one does. A sample is
the shape the analysers admit; temporal parsing takes its format, since one
Polars infers from the column is not row-separable, and the chunk classifier
refuses that shape. An operation callable with no arguments needs no entry.
"""

from __future__ import annotations

SAMPLES: dict[tuple[str, str | None, str], str] = {
    # Expression methods.
    ("expr", None, "alias"): "pl.col('a').alias('b')",
    ("expr", None, "approx_quantile"): "pl.col('a').approx_quantile(0.5)",
    (
        "expr",
        None,
        "bin_intervals",
    ): "pl.col('a').bin_intervals([1, 2], labels=['lo', 'mid', 'hi'])",
    ("expr", None, "bin_quantiles"): "pl.col('a').bin_quantiles([0.5], labels=['lo', 'hi'])",
    ("expr", None, "bin_ranks"): "pl.col('a').bin_ranks([0.5], labels=['lo', 'hi'])",
    ("expr", None, "cast"): "pl.col('a').cast(pl.Float64)",
    ("expr", None, "ewm_mean"): "pl.col('a').ewm_mean(alpha=0.5)",
    ("expr", None, "fill_nan"): "pl.col('a').fill_nan(0.0)",
    ("expr", None, "fill_null"): "pl.col('a').fill_null(0)",
    ("expr", None, "is_between"): "pl.col('a').is_between(0, 1)",
    ("expr", None, "is_in"): "pl.col('a').is_in([1, 2])",
    ("expr", None, "otherwise"): "pl.when(pl.col('a') > 0).then(1).otherwise(0)",
    ("expr", None, "over"): "pl.col('a').sum().over('b')",
    ("expr", None, "pow"): "pl.col('a').pow(2)",
    ("expr", None, "quantile"): "pl.col('a').quantile(0.5)",
    ("expr", None, "replace"): "pl.col('a').replace({1: 2})",
    ("expr", None, "replace_strict"): "pl.col('a').replace_strict({1: 2}, default=0)",
    ("expr", None, "rolling_max"): "pl.col('a').rolling_max(3)",
    ("expr", None, "rolling_mean"): "pl.col('a').rolling_mean(3)",
    ("expr", None, "rolling_min"): "pl.col('a').rolling_min(3)",
    ("expr", None, "rolling_std"): "pl.col('a').rolling_std(3)",
    ("expr", None, "rolling_sum"): "pl.col('a').rolling_sum(3)",
    ("expr", None, "sort_by"): "pl.col('a').sort_by('b')",
    ("expr", None, "then"): "pl.when(pl.col('a') > 0).then(1)",
    # Namespace methods.
    ("namespace", "dt", "convert_time_zone"): "pl.col('a').dt.convert_time_zone('UTC')",
    ("namespace", "dt", "offset_by"): "pl.col('a').dt.offset_by('1d')",
    ("namespace", "dt", "replace_time_zone"): "pl.col('a').dt.replace_time_zone('UTC')",
    ("namespace", "dt", "round"): "pl.col('a').dt.round('1h')",
    ("namespace", "dt", "strftime"): "pl.col('a').dt.strftime('%Y')",
    ("namespace", "dt", "truncate"): "pl.col('a').dt.truncate('1d')",
    ("namespace", "ext", "to"): "pl.col('a').ext.to(pl.Extension('haute.sample', pl.Int64))",
    ("namespace", "map", "contains_key"): "pl.col('a').map.contains_key('k')",
    ("namespace", "map", "get"): "pl.col('a').map.get('k')",
    ("namespace", "str", "contains"): "pl.col('a').str.contains('x')",
    ("namespace", "str", "count_matches"): "pl.col('a').str.count_matches('x')",
    ("namespace", "str", "ends_with"): "pl.col('a').str.ends_with('x')",
    ("namespace", "str", "extract"): r"pl.col('a').str.extract(r'(\d)', 1)",
    ("namespace", "str", "extract_all"): r"pl.col('a').str.extract_all(r'\d')",
    ("namespace", "str", "extract_groups"): r"pl.col('a').str.extract_groups(r'(\d)')",
    ("namespace", "str", "find"): "pl.col('a').str.find('x')",
    ("namespace", "str", "json_path_match"): "pl.col('a').str.json_path_match('$.k')",
    ("namespace", "str", "pad_end"): "pl.col('a').str.pad_end(3)",
    ("namespace", "str", "pad_start"): "pl.col('a').str.pad_start(3)",
    ("namespace", "str", "replace"): "pl.col('a').str.replace('a', 'b')",
    ("namespace", "str", "replace_all"): "pl.col('a').str.replace_all('a', 'b')",
    ("namespace", "str", "slice"): "pl.col('a').str.slice(0, 2)",
    ("namespace", "str", "split"): "pl.col('a').str.split(',')",
    ("namespace", "str", "split_exact"): "pl.col('a').str.split_exact(',', 1)",
    ("namespace", "str", "splitn"): "pl.col('a').str.splitn(',', 2)",
    ("namespace", "str", "starts_with"): "pl.col('a').str.starts_with('x')",
    ("namespace", "str", "strip_prefix"): "pl.col('a').str.strip_prefix('x')",
    ("namespace", "str", "strip_suffix"): "pl.col('a').str.strip_suffix('x')",
    ("namespace", "str", "strptime"): "pl.col('a').str.strptime(pl.Date, '%Y-%m-%d')",
    ("namespace", "str", "to_date"): "pl.col('a').str.to_date('%Y-%m-%d')",
    ("namespace", "str", "to_datetime"): "pl.col('a').str.to_datetime('%Y-%m-%d %H:%M')",
    ("namespace", "str", "to_time"): "pl.col('a').str.to_time('%H:%M')",
    ("namespace", "str", "zfill"): "pl.col('a').str.zfill(3)",
    # ``pl`` functions.
    ("polars_function", None, "all_horizontal"): "pl.all_horizontal('a', 'b')",
    ("polars_function", None, "any_horizontal"): "pl.any_horizontal('a', 'b')",
    ("polars_function", None, "arg_sort_by"): "pl.arg_sort_by('a')",
    ("polars_function", None, "arg_where"): "pl.arg_where(pl.col('a') > 0)",
    ("polars_function", None, "business_day_count"): "pl.business_day_count('a', 'b')",
    ("polars_function", None, "coalesce"): "pl.coalesce('a', 'b')",
    ("polars_function", None, "col"): "pl.col('a')",
    ("polars_function", None, "concat_arr"): "pl.concat_arr('a', 'b')",
    ("polars_function", None, "concat_list"): "pl.concat_list('a', 'b')",
    ("polars_function", None, "concat_str"): "pl.concat_str('a', 'b')",
    (
        "polars_function",
        None,
        "cum_fold",
    ): "pl.cum_fold(pl.lit(0), lambda acc, x: acc + x, ['a', 'b'])",
    ("polars_function", None, "cum_reduce"): "pl.cum_reduce(lambda acc, x: acc + x, ['a', 'b'])",
    ("polars_function", None, "cum_sum_horizontal"): "pl.cum_sum_horizontal('a', 'b')",
    ("polars_function", None, "date"): "pl.date('y', 'm', 'd')",
    ("polars_function", None, "date_ranges"): "pl.date_ranges('a', 'b')",
    ("polars_function", None, "datetime"): "pl.datetime('y', 'm', 'd')",
    ("polars_function", None, "datetime_ranges"): "pl.datetime_ranges('a', 'b', interval='1h')",
    ("polars_function", None, "fold"): "pl.fold(pl.lit(0), lambda acc, x: acc + x, ['a', 'b'])",
    ("polars_function", None, "format"): "pl.format('{}-{}', 'a', 'b')",
    ("polars_function", None, "from_epoch"): "pl.from_epoch('a')",
    ("polars_function", None, "linear_spaces"): "pl.linear_spaces('a', 'b', 3)",
    ("polars_function", None, "lit"): "pl.lit(1)",
    ("polars_function", None, "max_horizontal"): "pl.max_horizontal('a', 'b')",
    ("polars_function", None, "mean_horizontal"): "pl.mean_horizontal('a', 'b')",
    ("polars_function", None, "min_horizontal"): "pl.min_horizontal('a', 'b')",
    ("polars_function", None, "reduce"): "pl.reduce(lambda acc, x: acc + x, ['a', 'b'])",
    ("polars_function", None, "struct"): "pl.struct('a', 'b')",
    ("polars_function", None, "sum_horizontal"): "pl.sum_horizontal('a', 'b')",
    ("polars_function", None, "when"): "pl.when(pl.col('a') > 0).then(1)",
}


def sample_expression(receiver: str, namespace: str | None, name: str) -> str:
    """The sample source of one registered operation's expression."""
    explicit = SAMPLES.get((receiver, namespace, name))
    if explicit is not None:
        return explicit
    if receiver == "expr":
        return f"pl.col('a').{name}()"
    if receiver == "namespace":
        return f"pl.col('a').{namespace}.{name}()"
    return f"pl.{name}()"
