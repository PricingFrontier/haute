"""Chunk-local whitelist proofs: every admitted construct must be chunked == full.

PATH_TO_HIGHEST_STANDARD §A3: the chunk-local AST whitelist in
``haute.chunking`` may only admit constructs backed by a proof that executing
them through the REAL chunked path (``chunk_plan`` + ``collect_chunked``)
produces exactly the same frame as full execution (``_execute_lazy``).
A construct without such a proof is not whitelisted — it is rejected at plan
time (``ChunkPlanUnsupportedError``) so callers route to the existing full
(non-chunked) executor, which is always correct.

This module contains:

1. De-whitelist pins for the silent-wrongness constructs cited in
   CODE_REVIEW.md (``fill_null(strategy=...)``, ``is_in`` with a full-column
   haystack, ``min_horizontal`` over streamed NaN batches): chunk planning must
   reject them loudly AND the full path must produce the correct values that
   chunked execution used to corrupt.
2. AST-level whitelist unit tests for every de-whitelisted call shape.
3. ``test_whitelisted_construct_chunked_equals_full``: a hypothesis property
   proof per surviving whitelist entry, on randomized boundary-heavy frames
   (nulls/NaN/inf anywhere incl. chunk edges, single-row chunks).
4. A meta-test asserting every whitelist entry is covered by a proof case, so
   a new entry cannot land without its chunked==full proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
import polars.testing as plt
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._execute_lazy import _execute_lazy
from haute.chunking import (
    _ROW_LOCAL_DF_METHOD_NAMES,
    _ROW_LOCAL_EXPR_METHOD_NAMES,
    _ROW_LOCAL_POLARS_FUNCTIONS,
    ChunkPlanRequest,
    ChunkRunnerRequest,
    chunk_plan,
    collect_chunked,
    is_chunk_local_polars_code,
    iter_chunked_frames,
)
from haute.errors import ChunkPlanUnsupportedError
from haute.executor import _build_node_fn
from tests.conftest import make_edge, make_graph, make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _node(node_id: str, node_type: str, config: dict[str, object] | None = None):
    config = dict(config or {})
    if node_type == "dataInput" and "path" in config:
        suffix = Path(str(config["path"])).suffix.lower().lstrip(".")
        formats = {
            "jsonl": "ndjson",
            "ndjson": "ndjson",
            "arrow": "ipc",
            "feather": "ipc",
            "ipc": "ipc",
        }
        config = {
            **config,
            "inputType": "file",
            "format": formats.get(suffix, suffix),
            "cacheMode": "direct",
        }
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": node_type,
            "config": config,
        },
    }


def _xform_graph(
    source_path: Path,
    code: str,
    *,
    output_fields: list[str],
    contract: dict[str, list[str]] | None = None,
):
    """source(parquet) -> xform(polars user code) -> out(output)."""
    xform_config: dict[str, object] = {"code": code}
    if contract is not None:
        xform_config["contract"] = contract
    return make_graph(
        {
            "nodes": [
                _node("source", "dataInput", {"path": str(source_path)}),
                _node("xform", "polars", xform_config),
                _node("out", "output", make_output_config(output_fields)),
            ],
            "edges": [
                make_edge("source", "xform").model_dump(),
                make_edge("xform", "out").model_dump(),
            ],
        }
    )


# These §A3 proofs compare the chunked vs full execution of the *construct under
# test*, which lives in the ``xform`` node. They terminate at ``xform``, not at
# the downstream ``out`` (OUTPUT) node, because the v2 OUTPUT assembler is no
# longer a passthrough: it groups identical rows into one object and prunes
# all-null rows away (the 2026-06-16 empty-collection ruling), so its row count
# and even its column schema depend on the data and on how rows happen to split
# across chunks. That deliberate, chunk-size-sensitive transform would make
# chunked != full for reasons unrelated to the construct being proven. Comparing
# the ``xform`` frame proves exactly the row-locality property this module exists
# to defend.
def _chunk_plan(graph, *, chunk_size: int):
    return chunk_plan(ChunkPlanRequest(graph=graph, target_node_id="xform", chunk_size=chunk_size))


def _run_chunked(graph, *, chunk_size: int) -> pl.DataFrame:
    return collect_chunked(
        ChunkRunnerRequest(
            graph=graph,
            plan=_chunk_plan(graph, chunk_size=chunk_size),
            build_node_fn=_build_node_fn,
        ),
        allow_unbounded=True,
    )


def _run_full(graph) -> pl.DataFrame:
    outputs, *_ = _execute_lazy(
        graph,
        _build_node_fn,
        target_node_id="xform",
        source="batch",
    )
    return outputs["xform"].collect(engine="streaming")


# ---------------------------------------------------------------------------
# De-whitelist pins for the cited silent-wrongness constructs.
#
# Before the whitelist was tightened these graphs were granted a chunk plan
# and produced silently wrong values through the real chunked path:
#   fill_null(strategy='forward'): chunked x == [1.0, 1.0, None, 4.0]
#                                  full    x == [1.0, 1.0, 1.0,  4.0]
#   is_in(pl.col('b')):            chunked flag == [F, F, F, F]
#                                  full    flag == [F, F, T, T]
#   min_horizontal(i, f):          chunked r == [None, None, None, None, NaN]
#                                  full    r == [None, None, None, None, 0.0]
# Now planning must fail loudly (callers fall back to the full executor) and
# the full path must produce the correct values.
# ---------------------------------------------------------------------------


def test_fill_null_strategy_is_de_whitelisted_and_full_path_is_correct(
    tmp_path: Path,
) -> None:
    """Order-based fill strategies read across rows: a null at a chunk start
    has no fill source inside its chunk, so chunked != full."""
    source = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4"],
            "x": [1.0, None, None, 4.0],
        }
    ).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(pl.col('x').fill_null(strategy='forward'))",
        output_fields=["quote_id", "x"],
        contract={"inputs": ["x"], "outputs": []},
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="row-local"):
        _chunk_plan(graph, chunk_size=2)

    full = _run_full(graph)
    assert full["x"].to_list() == [1.0, 1.0, 1.0, 4.0]


def test_frame_level_fill_null_strategy_is_de_whitelisted_and_full_path_is_correct(
    tmp_path: Path,
) -> None:
    source = tmp_path / "quotes.parquet"
    pl.DataFrame({"x": [1.0, None, None, 4.0]}).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.fill_null(strategy='backward')",
        output_fields=["x"],
        contract={"inputs": ["x"], "outputs": []},
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="row-local"):
        _chunk_plan(graph, chunk_size=2)

    full = _run_full(graph)
    assert full["x"].to_list() == [1.0, 4.0, 4.0, 4.0]


def test_is_in_column_haystack_is_de_whitelisted_and_full_path_is_correct(
    tmp_path: Path,
) -> None:
    """``is_in(pl.col(...))`` treats the WHOLE column as the haystack: per
    chunk the haystack shrinks to the chunk's rows and membership flips."""
    source = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "a": [1, 2, 3, 4],
            "b": [3, 4, 998, 999],
        }
    ).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(flag=pl.col('a').is_in(pl.col('b')))",
        output_fields=["a", "flag"],
        contract={"inputs": ["a", "b"], "outputs": ["flag"]},
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="row-local"):
        _chunk_plan(graph, chunk_size=2)

    full = _run_full(graph)
    assert full["flag"].to_list() == [False, False, True, True]


def test_is_in_frame_subscript_is_de_whitelisted(tmp_path: Path) -> None:
    """``is_in(frame[...])`` reads the full frame from inside a chunk.

    The batch paths hand user code LazyFrames, so this construct cannot even
    execute there (LazyFrames are not subscriptable) — yet the whitelist used
    to claim it chunk-safe and grant a plan that exploded mid-chunk.  Planning
    must reject it up front instead.
    """
    source = tmp_path / "quotes.parquet"
    pl.DataFrame({"a": [1, 2, 3, 4], "b": [3, 4, 998, 999]}).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(flag=pl.col('a').is_in(source['b']))",
        output_fields=["a", "flag"],
        contract={"inputs": ["a", "b"], "outputs": ["flag"]},
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="row-local"):
        _chunk_plan(graph, chunk_size=2)

    with pytest.raises(TypeError, match="not subscriptable"):
        _run_full(graph)


def test_min_horizontal_nan_stream_batch_is_de_whitelisted_and_full_path_is_correct(
    tmp_path: Path,
) -> None:
    """The chunk batch stream can preserve a NaN representation where
    ``min_horizontal`` returns NaN for ``(0, NaN)``; full execution returns
    0.0. The construct is therefore not proven chunk-local.
    """
    source = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "i": [None, None, None, None, 0],
            "f": [None, None, None, None, float("nan")],
            "s": [None, None, None, None, None],
        },
        schema={"i": pl.Int64, "f": pl.Float64, "s": pl.String},
    ).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(r=pl.min_horizontal(pl.col('i'), pl.col('f')))",
        output_fields=list(_WITH_R),
        contract={"inputs": ["i", "f"], "outputs": ["r"]},
    )

    with pytest.raises(ChunkPlanUnsupportedError, match="row-local"):
        _chunk_plan(graph, chunk_size=2)

    full = _run_full(graph)
    assert full["r"].to_list() == [None, None, None, None, 0.0]


# ---------------------------------------------------------------------------
# AST-level whitelist contract: de-whitelisted call shapes and frame leaks.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        pytest.param(
            "df = source.with_columns(pl.col('x').fill_null(strategy='forward'))",
            id="fill-null-strategy-kw",
        ),
        pytest.param("df = source.fill_null(strategy='backward')", id="df-fill-null-strategy"),
        pytest.param(
            "df = source.with_columns(pl.col('x').fill_null(None, 'mean'))",
            id="fill-null-positional-strategy",
        ),
        pytest.param(
            "df = source.with_columns(pl.col('x').fill_null(0, limit=2))",
            id="fill-null-limit",
        ),
        pytest.param("df = source.with_columns(pl.col('x').fill_null())", id="fill-null-empty"),
        pytest.param("df = source.fill_null(0, matches_supertype=False)", id="fill-null-extra-kw"),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in(pl.col('b')))",
            id="is-in-column-haystack",
        ),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in(source['b']))",
            id="is-in-frame-subscript",
        ),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in(pl.lit(5)))",
            id="is-in-non-collection",
        ),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in([pl.col('b')]))",
            id="is-in-non-literal-element",
        ),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in([1], nulls_equal=True))",
            id="is-in-keyword",
        ),
        pytest.param("df = source.with_columns(y=source['x'])", id="frame-subscript-column"),
        pytest.param("df = source.with_columns(y=source[0:2])", id="frame-subscript-slice"),
        pytest.param(
            "df = source.with_columns(pl.lit(source['b']).alias('c'))",
            id="frame-subscript-inside-lit",
        ),
        pytest.param("df = source.with_columns(y=source + 1)", id="frame-in-binop-arg"),
        pytest.param("df = source.filter(pl.col('x') > source)", id="frame-in-compare-arg"),
        pytest.param("df = source.with_columns(y=[source])", id="frame-in-list-arg"),
        pytest.param("df = source.rename({'a': source})", id="frame-in-dict-arg"),
        pytest.param(
            "df = source.with_columns(r=pl.min_horizontal(pl.col('i'), pl.col('f')))",
            id="min-horizontal",
        ),
        pytest.param(
            "df = source.with_columns(pl.col('s').cast(pl.Categorical).alias('c'))",
            id="cast-to-categorical-expr",
        ),
        pytest.param(
            "df = source.cast({'s': pl.Categorical})",
            id="cast-to-categorical-df",
        ),
        pytest.param(
            "df = source.with_columns(pl.col('s').cast(pl.Enum(['a', 'b'])).alias('c'))",
            id="cast-to-enum",
        ),
        pytest.param(
            "df = source.with_columns(pl.col('s').cast(pl.List(pl.Categorical)).alias('c'))",
            id="cast-to-nested-categorical",
        ),
    ],
)
def test_chunk_unsafe_constructs_are_not_whitelisted(code: str) -> None:
    assert not is_chunk_local_polars_code(code, frame_names=("source",))


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("df = source.with_columns(pl.col('x').fill_null(0))", id="fill-null-value"),
        pytest.param(
            "df = source.with_columns(pl.col('x').fill_null(value=0))",
            id="fill-null-value-kw",
        ),
        pytest.param(
            "df = source.with_columns(pl.col('x').fill_null(pl.col('y')))",
            id="fill-null-expr-value",
        ),
        pytest.param("df = source.fill_null(0)", id="df-fill-null-value"),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in([1, 2, -3]))",
            id="is-in-literal-list",
        ),
        pytest.param(
            "df = source.with_columns(flag=pl.col('a').is_in(('a', 'b')))",
            id="is-in-literal-tuple",
        ),
        pytest.param(
            "df = source.with_columns(flag=pl.col('s').is_in({'x', None}))",
            id="is-in-literal-set",
        ),
        pytest.param(
            "tmp = source.filter(pl.col('x') > 0)\ndf = tmp.with_columns(z=pl.col('x') + 1)",
            id="multi-statement-local-frames",
        ),
    ],
)
def test_chunk_safe_constructs_remain_whitelisted(code: str) -> None:
    assert is_chunk_local_polars_code(code, frame_names=("source",))


# ---------------------------------------------------------------------------
# §A3 proofs: chunked == full for every surviving whitelist entry.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WhitelistProofCase:
    """One whitelisted construct executed through the real chunked path."""

    code: str
    output_fields: tuple[str, ...]
    proves: frozenset[tuple[str, str]] = field(default_factory=frozenset)


def _proves(*entries: tuple[str, str]) -> frozenset[tuple[str, str]]:
    return frozenset(entries)


_BASE_FIELDS = ("i", "f", "s")
_WITH_R = (*_BASE_FIELDS, "r")


def _expr_case(code_fragment: str, *proves: tuple[str, str]) -> WhitelistProofCase:
    return WhitelistProofCase(
        code=f"df = source.with_columns({code_fragment})",
        output_fields=_WITH_R,
        proves=_proves(*proves),
    )


WHITELIST_PROOF_CASES: dict[str, WhitelistProofCase] = {
    # --- frame-level methods -------------------------------------------------
    "df_cast": WhitelistProofCase(
        "df = source.cast({'i': pl.Float64})", _BASE_FIELDS, _proves(("df", "cast"))
    ),
    "df_drop": WhitelistProofCase("df = source.drop('s')", ("i", "f"), _proves(("df", "drop"))),
    "df_drop_nulls": WhitelistProofCase(
        "df = source.drop_nulls()", _BASE_FIELDS, _proves(("df", "drop_nulls"))
    ),
    "df_drop_nulls_subset": WhitelistProofCase(
        "df = source.drop_nulls(subset=['i'])", _BASE_FIELDS, _proves(("df", "drop_nulls"))
    ),
    "df_filter": WhitelistProofCase(
        "df = source.filter((pl.col('i') > 0) & pl.col('f').is_not_null())",
        _BASE_FIELDS,
        _proves(("df", "filter")),
    ),
    "df_fill_nan": WhitelistProofCase(
        "df = source.fill_nan(0.0)", _BASE_FIELDS, _proves(("df", "fill_nan"))
    ),
    "df_fill_null_value": WhitelistProofCase(
        "df = source.fill_null(0)", _BASE_FIELDS, _proves(("df", "fill_null"))
    ),
    "df_rename": WhitelistProofCase(
        "df = source.rename({'i': 'i_renamed'})",
        ("i_renamed", "f", "s"),
        _proves(("df", "rename")),
    ),
    "df_select": WhitelistProofCase(
        "df = source.select(pl.col('i'), pl.col('f'))", ("i", "f"), _proves(("df", "select"))
    ),
    "df_with_columns": WhitelistProofCase(
        "df = source.with_columns(r=pl.col('i') * 2)",
        _WITH_R,
        _proves(("df", "with_columns")),
    ),
    "df_with_columns_seq": WhitelistProofCase(
        "df = source.with_columns_seq(r=pl.col('i') * 2)",
        _WITH_R,
        _proves(("df", "with_columns_seq")),
    ),
    # --- expression methods (all via with_columns; see _expr_case) ----------
    "expr_abs": _expr_case("r=pl.col('f').abs()", ("expr", "abs")),
    "expr_alias": _expr_case("pl.col('i').alias('r')", ("expr", "alias")),
    "expr_cast": _expr_case("r=pl.col('i').cast(pl.Float64)", ("expr", "cast")),
    "expr_ceil": _expr_case("r=pl.col('f').ceil()", ("expr", "ceil")),
    "expr_clip": _expr_case("r=pl.col('f').clip(-1.0, 1.5)", ("expr", "clip")),
    "expr_exp": _expr_case("r=pl.col('f').exp()", ("expr", "exp")),
    "expr_fill_nan": _expr_case("r=pl.col('f').fill_nan(0.0)", ("expr", "fill_nan")),
    "expr_fill_null_value": _expr_case("r=pl.col('i').fill_null(value=-1)", ("expr", "fill_null")),
    "expr_floor": _expr_case("r=pl.col('f').floor()", ("expr", "floor")),
    "expr_is_between": _expr_case("r=pl.col('i').is_between(-1, 3)", ("expr", "is_between")),
    "expr_is_finite": _expr_case("r=pl.col('f').is_finite()", ("expr", "is_finite")),
    "expr_is_in_literal": _expr_case("r=pl.col('i').is_in([1, 2, -3])", ("expr", "is_in")),
    "expr_is_infinite": _expr_case("r=pl.col('f').is_infinite()", ("expr", "is_infinite")),
    "expr_is_nan": _expr_case("r=pl.col('f').is_nan()", ("expr", "is_nan")),
    "expr_is_not_nan": _expr_case("r=pl.col('f').is_not_nan()", ("expr", "is_not_nan")),
    "expr_is_not_null": _expr_case("r=pl.col('i').is_not_null()", ("expr", "is_not_null")),
    "expr_is_null": _expr_case("r=pl.col('i').is_null()", ("expr", "is_null")),
    "expr_log": _expr_case("r=pl.col('f').log()", ("expr", "log")),
    "expr_not": _expr_case("r=pl.col('i').is_null().not_()", ("expr", "not_")),
    "expr_round": _expr_case("r=pl.col('f').round(1)", ("expr", "round")),
    "expr_sqrt": _expr_case("r=pl.col('f').sqrt()", ("expr", "sqrt")),
    "expr_when_then_otherwise": _expr_case(
        "r=pl.when(pl.col('i') > 1).then(pl.lit('hi')).otherwise(pl.lit('lo'))",
        ("expr", "then"),
        ("expr", "otherwise"),
        ("fn", "when"),
    ),
    # --- pl.* functions ------------------------------------------------------
    "fn_all_horizontal": _expr_case(
        "r=pl.all_horizontal(pl.col('i') > 0, pl.col('f') > 0)", ("fn", "all_horizontal")
    ),
    "fn_any_horizontal": _expr_case(
        "r=pl.any_horizontal(pl.col('i') > 0, pl.col('f') > 0)", ("fn", "any_horizontal")
    ),
    "fn_coalesce": _expr_case("r=pl.coalesce(pl.col('i'), pl.col('f'))", ("fn", "coalesce")),
    "fn_col": _expr_case("r=pl.col('i')", ("fn", "col")),
    "fn_concat_str": _expr_case(
        "r=pl.concat_str(pl.col('s'), pl.col('i').cast(pl.String), separator='-')",
        ("fn", "concat_str"),
    ),
    "fn_lit": _expr_case("r=pl.lit(7)", ("fn", "lit")),
    "fn_max_horizontal": _expr_case(
        "r=pl.max_horizontal(pl.col('i'), pl.col('f'))", ("fn", "max_horizontal")
    ),
}


def test_every_whitelist_entry_has_a_proof_case() -> None:
    """§A3 enforcement: a whitelist entry without a chunked==full proof case
    cannot exist, and a proof tag without a whitelist entry is stale."""
    proven: set[tuple[str, str]] = set()
    for case in WHITELIST_PROOF_CASES.values():
        proven |= case.proves
    expected = (
        {("df", name) for name in _ROW_LOCAL_DF_METHOD_NAMES}
        | {("expr", name) for name in _ROW_LOCAL_EXPR_METHOD_NAMES}
        | {("fn", name) for name in _ROW_LOCAL_POLARS_FUNCTIONS}
    )
    assert proven == expected


def _frame_strategy() -> st.SearchStrategy[pl.DataFrame]:
    int_values = st.one_of(st.none(), st.integers(-3, 5))
    float_values = st.one_of(
        st.none(),
        st.sampled_from(
            [-2.5, -1.0, 0.0, 0.5, 1.0, 2.25, float("nan"), float("inf"), float("-inf")]
        ),
    )
    str_values = st.one_of(st.none(), st.sampled_from(["a", "b", "x", ""]))
    rows = st.lists(st.tuples(int_values, float_values, str_values), min_size=1, max_size=8)
    return rows.map(
        lambda drawn: pl.DataFrame(
            {
                "i": [row[0] for row in drawn],
                "f": [row[1] for row in drawn],
                "s": [row[2] for row in drawn],
            },
            schema={"i": pl.Int64, "f": pl.Float64, "s": pl.String},
        )
    )


@pytest.mark.parametrize("case_id", sorted(WHITELIST_PROOF_CASES))
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_whitelisted_construct_chunked_equals_full(
    case_id: str,
    data: st.DataObject,
    tmp_path: Path,
) -> None:
    case = WHITELIST_PROOF_CASES[case_id]
    assert is_chunk_local_polars_code(case.code, frame_names=("source",)), (
        f"Proof case {case_id!r} is no longer whitelisted; delete the stale proof "
        "or restore the whitelist entry."
    )

    frame = data.draw(_frame_strategy(), label="frame")
    chunk_size = data.draw(st.integers(1, frame.height + 1), label="chunk_size")
    source = tmp_path / "src.parquet"
    frame.write_parquet(source)
    graph = _xform_graph(source, case.code, output_fields=list(case.output_fields))

    chunked = _run_chunked(graph, chunk_size=chunk_size)
    full = _run_full(graph)

    if full.height == 0:
        # The runner never emits empty batches; an all-filtered result is the
        # empty concat.  Schemas cannot be compared against zero batches.
        assert chunked.height == 0
    else:
        plt.assert_frame_equal(chunked, full, check_exact=True)


# ---------------------------------------------------------------------------
# Dtype-specific and compositional proofs.
#
# The property harness above exercises each construct in isolation on an
# Int64/Float64/String frame.  These deterministic pins extend coverage to
# (1) temporal (``Date``) and ``Decimal`` dtypes flowing through cast-bearing
# constructs, and (2) two constructs composed in a single chain where a value's
# first appearance lands in a later chunk -- the exact shapes a whole-frame
# operation would silently diverge on if it were not truly row-local.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 5, 7])
def test_temporal_and_decimal_composition_chunked_equals_full(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    """A cast on a temporal column composed with a filter must be chunk-local
    for ``Date``/``Decimal`` columns, including values first seen in later
    chunks."""
    source = tmp_path / "temporal.parquet"
    pl.DataFrame(
        {
            "i": [3, 1, 2, 1, 3, 2, 4],
            "d": [
                date(2020, 1, 3),
                date(2020, 1, 1),
                None,
                date(2020, 1, 1),
                date(2020, 1, 3),
                None,
                date(2021, 6, 30),
            ],
            "dec": [
                Decimal("1.50"),
                Decimal("2.25"),
                None,
                Decimal("2.25"),
                Decimal("1.50"),
                None,
                Decimal("9.99"),
            ],
        },
        schema={"i": pl.Int64, "d": pl.Date, "dec": pl.Decimal(scale=2)},
    ).write_parquet(source)
    code = (
        "df = source.with_columns("
        "day=pl.col('d').cast(pl.Int32), dec_present=pl.col('dec').is_not_null()"
        ").filter(pl.col('i') >= 1)"
    )
    assert is_chunk_local_polars_code(code, frame_names=("source",))
    # Include the cast-derived columns (``day``/``dec_present``) in the compared
    # output so the chunked==full assertion inspects the cast/is_not_null OUTPUT
    # values across chunk boundaries -- not merely the trailing filter's
    # row-locality -- making this dtype-specific composition proof load-bearing.
    graph = _xform_graph(source, code, output_fields=["i", "d", "dec", "day", "dec_present"])

    chunked = _run_chunked(graph, chunk_size=chunk_size)
    full = _run_full(graph)

    plt.assert_frame_equal(chunked, full, check_exact=True)


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 5, 7])
def test_two_construct_expression_composition_chunked_equals_full(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    """A ``when/then/otherwise`` composed with ``abs``/``round`` and a trailing
    ``filter`` must stay row-local across chunk boundaries."""
    source = tmp_path / "composition.parquet"
    pl.DataFrame(
        {
            "i": [1, 2, 3, 4, 5, 6],
            "f": [1.0, -2.0, 3.55, -4.0, 5.05, -6.0],
        },
        schema={"i": pl.Int64, "f": pl.Float64},
    ).write_parquet(source)
    code = (
        "df = source.with_columns("
        "r=pl.when(pl.col('f') > 0).then(pl.col('f').abs().round(1))"
        ".otherwise(pl.lit(0.0))"
        ").filter(pl.col('i') <= 5)"
    )
    assert is_chunk_local_polars_code(code, frame_names=("source",))
    graph = _xform_graph(source, code, output_fields=["i", "f", "r"])

    chunked = _run_chunked(graph, chunk_size=chunk_size)
    full = _run_full(graph)

    plt.assert_frame_equal(chunked, full, check_exact=True)


# ---------------------------------------------------------------------------
# Deterministic boundary pins for the restricted survivors.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 5])
def test_value_fill_null_chunked_equals_full_with_nulls_at_chunk_edges(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    source = tmp_path / "quotes.parquet"
    pl.DataFrame({"x": [1.0, None, None, 4.0]}).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(pl.col('x').fill_null(0.0))",
        output_fields=["x"],
    )

    chunked = _run_chunked(graph, chunk_size=chunk_size)
    full = _run_full(graph)

    assert full["x"].to_list() == [1.0, 0.0, 0.0, 4.0]
    plt.assert_frame_equal(chunked, full, check_exact=True)


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 5])
def test_literal_is_in_chunked_equals_full_across_chunk_boundaries(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    source = tmp_path / "quotes.parquet"
    pl.DataFrame({"a": [1, 2, 3, 4]}).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(flag=pl.col('a').is_in([3, 4]))",
        output_fields=["a", "flag"],
    )

    chunked = _run_chunked(graph, chunk_size=chunk_size)
    full = _run_full(graph)

    assert full["flag"].to_list() == [False, False, True, True]
    plt.assert_frame_equal(chunked, full, check_exact=True)


def test_chunk_runner_emits_no_batches_for_empty_source(tmp_path: Path) -> None:
    """Empty-source boundary: the runner yields zero batches (it never emits
    empty frames) while the full path produces a 0-row frame."""
    source = tmp_path / "quotes.parquet"
    pl.DataFrame(schema={"i": pl.Int64, "f": pl.Float64, "s": pl.String}).write_parquet(source)
    graph = _xform_graph(
        source,
        "df = source.with_columns(r=pl.col('i') * 2)",
        output_fields=["i", "f", "s", "r"],
    )

    batches = list(
        iter_chunked_frames(
            ChunkRunnerRequest(
                graph=graph,
                plan=_chunk_plan(graph, chunk_size=2),
                build_node_fn=_build_node_fn,
            )
        )
    )

    assert batches == []
    assert _run_full(graph).height == 0
