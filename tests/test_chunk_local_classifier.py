"""The retained chunk-local classifiers: closed reasons, operators, source locations.

``classify_chunk_local_polars_code`` and ``classify_row_local_expression`` are
used by lazy execution, the expression parser and trace correlation. Their
chunked==full proofs live in ``tests/test_chunk_whitelist_proofs.py``.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute.chunking import (
    classify_chunk_local_polars_code,
    classify_row_local_expression,
    is_chunk_local_polars_code,
)
from haute.projection import materialising_operators_by_node, prepare_graph
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
        }
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": node_type,
            "config": config,
        },
    }


def _write_projected_source(tmp_path: Path, *, extra_columns: int = 0) -> Path:
    path = tmp_path / f"projected_{extra_columns}.parquet"
    data: dict[str, list[object]] = {
        "quote_id": ["q1", "q2", "q3", "q4"],
        "premium": [100.0, 200.0, 300.0, 400.0],
    }
    for index in range(extra_columns):
        data[f"feature_{index}"] = [float(index)] * 4
    pl.DataFrame(data).write_parquet(path)
    return path


def test_chunk_group_by_evidence_is_receiver_aware(tmp_path: Path) -> None:
    """Materialising-operator ``group_by`` evidence ignores non-frame receivers."""
    path = _write_projected_source(tmp_path)

    def _prepared(code: str):
        graph = make_graph(
            {
                "nodes": [
                    _node("source", "dataInput", {"path": str(path)}),
                    _node("shape", "polars", {"code": code}),
                    _node("out", "output", make_output_config(["premium"])),
                ],
                "edges": [
                    make_edge("source", "shape").model_dump(),
                    make_edge("shape", "out").model_dump(),
                ],
            }
        )
        return prepare_graph(graph, "out", source="live")

    expression_prepared = _prepared(
        "stats = pl.col('premium').list.group_by('quote_id')\ndf = df.filter(pl.col('premium') > 0)"
    )
    assert not materialising_operators_by_node(
        expression_prepared.order,
        expression_prepared.node_map,
        relevant_edges=expression_prepared.relevant_edges,
    )

    # An unbound name may be a preamble frame, so its group-by is evidence.
    preamble_prepared = _prepared(
        "stats = lookup.group_by('quote_id')\ndf = df.filter(pl.col('premium') > 0)"
    )
    assert dict(
        materialising_operators_by_node(
            preamble_prepared.order,
            preamble_prepared.node_map,
            relevant_edges=preamble_prepared.relevant_edges,
        )
    ) == {"shape": "group_by"}

    frame_prepared = _prepared(
        "df = df.group_by('quote_id').agg(pl.col('premium').sum().alias('premium'))"
    )
    assert dict(
        materialising_operators_by_node(
            frame_prepared.order,
            frame_prepared.node_map,
            relevant_edges=frame_prepared.relevant_edges,
        )
    ) == {"shape": "group_by"}


# ---------------------------------------------------------------------------
# Chunk-local classifier: closed reasons, blocking operators, source locations.
#
# The classifier is a receiver-aware AST walk with no textual prefilter, so
# comments and string literals cannot change a verdict and every rejection
# names the construct that stopped the walk.
# ---------------------------------------------------------------------------


def _classify(code: str):
    return classify_chunk_local_polars_code(code, frame_names=("df",))


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("# never .sort( here\ndf = df.filter(pl.col('a') > 0)", id="comment"),
        pytest.param("df = df.filter(pl.col('a') != '.sort(')", id="string-literal"),
    ],
)
def test_comments_and_string_literals_do_not_change_eligibility(code: str) -> None:
    decision = _classify(code)
    assert decision.eligible
    assert decision.reason == "eligible"
    assert decision.blocking_operator is None


def test_classifier_reports_unsupported_frame_method() -> None:
    decision = _classify("df = df.sort('a')")
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.line == 1
    assert decision.column is not None


def test_classifier_reports_unsupported_namespace_method() -> None:
    decision = _classify("df = df.with_columns(pl.col('a').list.sort().alias('b'))")
    assert not decision.eligible
    assert decision.reason == "unsupported_namespace_method"
    assert decision.blocking_operator == "list.sort"
    assert decision.line == 1


def test_classifier_admits_whitelisted_string_namespace_method() -> None:
    decision = _classify("df = df.filter(pl.col('s').str.contains('x'))")
    assert decision.eligible
    assert decision.reason == "eligible"


def test_classifier_rejects_namespace_method_with_expression_argument() -> None:
    decision = _classify("df = df.filter(pl.col('s').str.contains(pl.col('t')))")
    assert not decision.eligible
    assert decision.reason == "unsupported_call_shape"
    assert decision.blocking_operator == "contains"
    assert decision.line == 1


def test_classifier_rejects_map_elements_with_a_location() -> None:
    decision = _classify("df = df.with_columns(pl.col('a').map_elements(lambda v: v))")
    assert not decision.eligible
    assert decision.blocking_operator == "map_elements"
    assert decision.line == 1
    assert decision.column is not None


def test_classifier_reports_unsupported_statement() -> None:
    decision = _classify("for x in range(2):\n    df = df")
    assert not decision.eligible
    assert decision.reason == "unsupported_statement"
    assert decision.blocking_operator == "For"
    assert decision.line == 1


def test_classifier_reports_frame_embedded_in_expression() -> None:
    decision = _classify("df = df.with_columns(y=df)")
    assert not decision.eligible
    assert decision.reason == "frame_embedded_in_expression"
    assert decision.blocking_operator == "df"
    assert decision.line == 1


def test_classifier_reports_the_first_blocking_construct_in_source_order() -> None:
    first = _classify("df = df.sort('a')\ndf = df.with_columns(pl.col('a').list.sort())")
    assert first.reason == "unsupported_frame_method"
    assert first.blocking_operator == "sort"
    assert first.line == 1

    swapped = _classify("df = df.with_columns(pl.col('a').list.sort())\ndf = df.sort('a')")
    assert swapped.reason == "unsupported_namespace_method"
    assert swapped.blocking_operator == "list.sort"
    assert swapped.line == 1


def test_classifier_reports_the_textually_first_blocking_dict_entry() -> None:
    """AST field order visits every ``Dict`` key before any value, so a blocking
    VALUE that precedes a blocking KEY must still be the one reported."""
    code = "df = df.rename({'a': df.sort('x'), df.list.sort(): 'b'})"
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.line == 1
    assert decision.column == code.index("df.sort(") + 1


@pytest.mark.parametrize(
    ("expression", "chunk_blocker"),
    [
        pytest.param(
            "pl.when(pl.col('x') > 5).then(pl.lit('a')).when(pl.col('x') > 0)"
            ".then(pl.lit('b')).otherwise(pl.lit('c'))",
            "when",
            id="chained-when",
        ),
        pytest.param("pl.min_horizontal(pl.col('a'), pl.col('b'))", "pl.min_horizontal", id="min"),
        pytest.param("pl.format('{} {}', pl.col('a'), pl.col('b'))", "pl.format", id="format"),
    ],
)
def test_row_semantics_admit_row_local_operations_chunking_has_not_proven(
    expression: str, chunk_blocker: str
) -> None:
    """One row determines these, though chunked execution still rejects them."""
    assert classify_row_local_expression(expression).eligible
    chunk = _classify(f"df = df.with_columns(v=({expression}))")
    assert not chunk.eligible
    assert chunk.blocking_operator == chunk_blocker


@pytest.mark.parametrize(
    ("expression", "reason", "operator"),
    [
        ("pl.col('x').sum().over('g')", "unsupported_expression_method", "sum"),
        ("pl.col('x').shift(1)", "unsupported_expression_method", "shift"),
        ("pl.col('x').cum_sum()", "unsupported_expression_method", "cum_sum"),
        ("pl.col('x').fill_null(strategy='forward')", "unsupported_call_shape", "fill_null"),
        ("pl.col('d').str.to_date()", "unsupported_call_shape", "to_date"),
    ],
)
def test_row_semantics_reject_what_needs_other_rows_naming_the_operator(
    expression: str, reason: str, operator: str
) -> None:
    decision = classify_row_local_expression(expression)
    assert not decision.eligible
    assert decision.reason == reason
    assert decision.blocking_operator == operator


def test_classifier_reports_the_textually_first_ifexp_branch() -> None:
    """An ``IfExp`` stores ``test`` before the textually earlier ``body``."""
    code = "df = df.with_columns(y=df.sort('x') if df.unique() else 1)"
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.line == 1
    assert decision.column == code.index("df.sort(") + 1


def test_classifier_reports_the_textually_first_call_argument() -> None:
    """A blocking positional argument that follows an earlier blocking argument
    must not be reported ahead of it."""
    code = "df = df.select(df.sort('x'), df.unique())"
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == "unsupported_frame_method"
    assert decision.blocking_operator == "sort"
    assert decision.column == code.index("df.sort(") + 1


def test_classifier_reports_a_positional_argument_before_a_later_keyword() -> None:
    decision = _classify("df = df.select(df.sort('x'), y=df.unique())")
    assert decision.blocking_operator == "sort"

    swapped = _classify("df = df.select(df.unique(), y=df.sort('x'))")
    assert swapped.blocking_operator == "unique"


@pytest.mark.parametrize(
    "code",
    [
        "df = df.sort('premium')",
        "df = df.unique(subset=['quote_id'])",
        "df = df.reverse()",
        "df = df.shift(1)",
        "df = df.top_k(5, by='premium')",
        "df = df.bottom_k(5, by='premium')",
        "df = df.explode('l')",
        "df = df.with_columns(pl.col('premium').sum().over('quote_id').alias('total'))",
        "df = df.with_columns(pl.col('premium').shift(1).alias('previous'))",
        "df = df.with_columns(pl.col('premium').diff().alias('change'))",
        "df = df.with_columns(pl.col('premium').pct_change().alias('change_rate'))",
        "df = df.join(df, on='quote_id')",
        "df = df.join_asof(df, on='premium')",
    ],
)
def test_every_materialisation_boundary_operator_is_not_chunk_local(code: str) -> None:
    """EXEC-P07 boundaries materialise the whole frame, so the classifier admits none."""
    decision = _classify(code)
    assert not decision.eligible
    assert decision.blocking_operator is not None


def test_the_chunk_suffix_table_covers_every_registered_boundary_method() -> None:
    """A newly registered boundary cannot slip past the classifier untested."""
    from haute._polars_operations import materialising_frame_methods

    covered = {
        "sort",
        "unique",
        "reverse",
        "shift",
        "top_k",
        "bottom_k",
        "explode",
        "join",
        "join_asof",
        # group_by has its own dedicated evidence test above.
        "group_by",
        "groupby",
    }
    assert materialising_frame_methods() <= covered


@pytest.mark.parametrize(
    ("code", "reason", "operator"),
    [
        (
            "df = df.with_columns(pl.col('a').replace(**mapping))",
            "unsupported_call_shape",
            "replace",
        ),
        ("df = df.with_columns(pl.col('a').replace(old=[1]))", "unsupported_call_shape", "replace"),
        (
            "df = df.with_columns(pl.col('a').replace(old=values, new=[1]))",
            "unsupported_call_shape",
            "replace",
        ),
        ("df = helper(df)", "unsupported_expression", "Call"),
        ("df = unknown.str.contains('x')", "unsupported_expression", "Name"),
        ("df = df.str.contains('x')", "unsupported_namespace_method", "str.contains"),
        ("df = df.foo", "unsupported_expression", "Attribute"),
        ("df = lambda x: x", "unsupported_expression", "Lambda"),
        ("df[0] = df", "assignment_not_frame_derived", "df[0]"),
        ("df: object", "assignment_not_frame_derived", "df"),
        ("df = 1", "assignment_not_frame_derived", "df"),
        ("helper(df)", "unsupported_expression", "Call"),
        ("pl.col('a')", "unsupported_statement", "Expr"),
    ],
)
def test_classifier_rejects_unproven_public_shapes(code: str, reason: str, operator: str) -> None:
    decision = _classify(code)
    assert not decision.eligible
    assert decision.reason == reason
    assert decision.blocking_operator == operator


def test_classifier_admits_frame_derived_expression_statement() -> None:
    decision = _classify("df.filter(pl.col('a') > 0)")
    assert decision.eligible
    assert decision.reason == "eligible"


def test_chunk_classifier_defensive_helpers_preserve_their_contracts() -> None:
    import ast

    from haute.chunking import _ChunkLocalTrace, _embedded_frame_name, _source_ordered

    trace = _ChunkLocalTrace()
    first, second = ast.parse("first\nsecond").body
    trace.record("first", "one", first)
    trace.record("second", "two", second)
    assert (trace.reason, trace.blocking_operator, trace.line) == ("first", "one", 1)
    assert (
        _embedded_frame_name(
            ast.parse("other + 1", mode="eval").body, allowed_frames={"df"}, local_frames={"tmp"}
        )
        is None
    )
    left = ast.Name(id="left")
    right = ast.Name(id="right")
    assert _source_ordered((right, left)) == (right, left)
    assert classify_chunk_local_polars_code("df = df", frame_names=()).reason == "no_frame_names"


def test_chunk_local_polars_guard_accepts_row_local_and_rejects_global() -> None:
    assert is_chunk_local_polars_code(
        "df = source.with_columns(y=pl.col('x') * 2).filter(pl.col('x') > 0)",
        frame_names=("source",),
    )
    assert not is_chunk_local_polars_code(
        "df = source.group_by('quote_id').agg(pl.col('x').sum())",
        frame_names=("source",),
    )
    assert not is_chunk_local_polars_code(
        "df = GLOBAL_LAZY_FRAME.with_columns(y=pl.col('x') * 2)",
        frame_names=("source",),
    )
    assert not is_chunk_local_polars_code(
        "df = source.with_columns(flag=pl.col('x').is_in(source.select('y')))",
        frame_names=("source",),
    )
