"""Literal Polars column selectors: recognition, purity, and schema expansion."""

from __future__ import annotations

import ast

import polars as pl
import polars.selectors as cs
import pytest

from haute._polars_selectors import (
    expand_literal_selector,
    literal_selector,
    preamble_selector_aliases,
    selector_root,
)

_SCHEMA = pl.Schema(
    {"id": pl.String, "a": pl.Int64, "a2": pl.Float64, "b": pl.String, "c": pl.Boolean}
)
_ALIASES = frozenset({"cs"})


def _node(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def _polars_columns(expression: pl.Expr) -> tuple[str, ...]:
    return tuple(pl.LazyFrame(schema=_SCHEMA).select(expression).collect_schema().names())


@pytest.mark.parametrize(
    ("source", "expression", "dtype_dependent"),
    [
        ("pl.all()", pl.all(), False),
        ("pl.exclude('b')", pl.exclude("b"), False),
        ("pl.exclude(['a', 'b'])", pl.exclude(["a", "b"]), False),
        ("pl.exclude(pl.String)", pl.exclude(pl.String), True),
        ("pl.col('^a.*$')", pl.col("^a.*$"), False),
        ("pl.col('*')", pl.col("*"), False),
        ("pl.col(pl.Int64)", pl.col(pl.Int64), True),
        ("pl.col([pl.Int64, pl.Float64])", pl.col([pl.Int64, pl.Float64]), True),
        ("pl.all().exclude('id')", pl.all().exclude("id"), False),
        ("cs.numeric()", cs.numeric(), True),
        ("cs.starts_with('a')", cs.starts_with("a"), False),
        ("cs.by_name('a', 'c')", cs.by_name("a", "c"), False),
        ("~cs.numeric()", ~cs.numeric(), True),
        ("cs.numeric() - cs.by_name('a')", cs.numeric() - cs.by_name("a"), True),
        ("cs.string() | cs.by_name('c')", cs.string() | cs.by_name("c"), True),
    ],
)
def test_non_positional_selectors_expand_to_the_columns_polars_selects(
    source: str, expression: pl.Expr, dtype_dependent: bool
) -> None:
    selector = literal_selector(_node(source), aliases=_ALIASES)

    assert selector is not None
    assert selector.dtype_dependent is dtype_dependent
    assert not selector.positional
    expected = _polars_columns(expression)
    assert expand_literal_selector(selector, _SCHEMA.names(), dict(_SCHEMA)) == expected
    if dtype_dependent:
        assert expand_literal_selector(selector, _SCHEMA.names(), None) is None
    else:
        assert expand_literal_selector(selector, _SCHEMA.names(), None) == expected


@pytest.mark.parametrize(
    "source",
    ["pl.nth(1)", "pl.nth([0, 2])", "pl.first()", "pl.last()", "cs.by_index(0, 2)", "cs.first()"],
)
def test_positional_selectors_are_recognised_but_not_expanded(source: str) -> None:
    selector = literal_selector(_node(source), aliases=_ALIASES)

    assert selector is not None
    assert selector.positional
    assert expand_literal_selector(selector, _SCHEMA.names(), dict(_SCHEMA)) is None


@pytest.mark.parametrize(
    "source",
    [
        "pl.col('a')",
        "pl.col('a', 'b')",
        "pl.exclude(names)",
        "pl.col(f'^{prefix}.*$')",
        "pl.nth(index)",
        "pl.first('a')",
        "~pl.all()",
        "pl.all() - pl.col('a')",
        "cs.numeric() * 2",
        "cs.by_name('a').fill_null(0)",
        "cs.starts_with(prefix)",
        "polars_selectors.numeric()",
        "pl.all().sum()",
    ],
)
def test_expressions_that_are_not_pure_literal_selections_are_rejected(source: str) -> None:
    assert literal_selector(_node(source), aliases=_ALIASES) is None


def test_polars_selectors_need_a_preamble_alias() -> None:
    assert literal_selector(_node("cs.numeric()"), aliases=frozenset()) is None


@pytest.mark.parametrize(
    ("preamble", "aliases"),
    [
        ("import polars.selectors as cs", {"cs"}),
        ("from polars import selectors as sel", {"sel"}),
        ("import polars as pl\nimport polars.selectors as cs\nimport numpy as np", {"cs"}),
        ("import polars.selectors as cs\ncs = None", set()),
        ("import polars.selectors as cs\ndef cs():\n    pass", set()),
        ("if True:\n    import polars.selectors as cs", set()),
        ("import polars.selectors", set()),
        ("import polars.selectors as cs\nimport polars.selectors as cs", set()),
        ("import polars.selectors as", set()),
        ("", set()),
    ],
)
def test_preamble_aliases_are_single_top_level_selector_imports(
    preamble: str, aliases: set[str]
) -> None:
    assert preamble_selector_aliases(preamble) == frozenset(aliases)


@pytest.mark.parametrize(
    ("source", "root"),
    [
        ("pl.all().fill_null(0)", "pl.all()"),
        ("pl.exclude('id') * 2", "pl.exclude('id')"),
        ("(pl.col('^a.*$') + 1).alias('x')", "pl.col('^a.*$')"),
        ("~pl.all()", "pl.all()"),
        ("cs.numeric().fill_null(0).name.suffix('_filled')", "cs.numeric()"),
        ("pl.exclude('cap').clip(upper_bound='cap')", "pl.exclude('cap')"),
    ],
)
def test_selector_root_finds_the_selector_a_computation_starts_from(source: str, root: str) -> None:
    found = selector_root(_node(source), aliases=_ALIASES)

    assert found is not None
    selector, node = found
    assert ast.unparse(node) == ast.unparse(_node(root))
    root_selector = literal_selector(_node(root), aliases=_ALIASES)
    assert root_selector is not None
    assert selector.source == root_selector.source


@pytest.mark.parametrize(
    "source",
    ["pl.col('a') * 2", "pl.sum_horizontal(pl.all())", "pl.lit(1) + pl.all()", "pl.all()"],
)
def test_selector_root_is_none_without_a_computation_rooted_at_a_selector(source: str) -> None:
    assert selector_root(_node(source), aliases=_ALIASES) is None
