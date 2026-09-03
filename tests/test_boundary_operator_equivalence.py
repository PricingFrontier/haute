"""Full-versus-planned equivalence for every EXEC-P07 materialisation boundary.

EXEC-P07 admits a set of global Polars operations as materialisation boundaries.
Admitting an operator changes *how* the engine runs it — the planner inserts a
boundary and the executor materialises there — so each one needs a proof that
the boundary does not change *what* it computes.

Every test here runs one operator through the real lazy executor with an
admitted execution context, and compares the collected frame against the plain
Polars lazy evaluation of the same operations. Each graph places the operator
mid-graph, with a row-local node downstream, so the boundary is genuinely
materialised inside a longer plan rather than at the sink. Each test also
asserts through the executed graph's own diagnostic that the boundary was
planned at that node, and that the executor really wrote that node's
checkpoint under ``checkpoint_dir`` -- so none of them can pass on a
non-boundary path, or on a boundary that silently stayed lazy.

The four properties, per the roadmap acceptance:

===================  ===========================================================
property             operators proved here
===================  ===========================================================
ordering             sort, reverse, top_k, bottom_k (exact in-order frame equality)
schema               every operator (identical column names and dtypes)
row multiplicity     unique, join (duplicate left keys), join_asof, explode, over
multi-input columns  join inner, join left (both ports' columns and suffixed
                     collisions retained with identical values); join_asof
                     (both ports' columns, no colliding name exercised)
===================  ===========================================================

The fixtures are deliberately tiny: these are correctness tests, not memory
tests. The memory evidence lives in the performance certification lane.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._native_memory_limit import native_memory_backend_scope
from haute.execution import execute_lazy_graph
from haute.executor import _build_node_fn
from tests.conftest import make_edge, make_graph, make_ready_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_ROWS = 240

# The row-local tail that forces the boundary to sit mid-graph. It reads a
# column every operator preserves, so one tail serves every case.
_TAIL_CODE = "df = op.with_columns((pl.col('premium') * 2).alias('doubled'))"


def _tail(frame: pl.LazyFrame) -> pl.LazyFrame:
    """The plain-Polars twin of ``_TAIL_CODE``."""
    return frame.with_columns((pl.col("premium") * 2).alias("doubled"))


# ---------------------------------------------------------------------------
# Fixtures on disk
# ---------------------------------------------------------------------------


def _write_left(path: Path) -> None:
    """The main frame: duplicate join keys, duplicate rows, and a list column."""
    pl.DataFrame(
        {
            # 60 distinct keys over 240 rows: every key repeats, so a join on it
            # is genuinely many-to-one and ``unique`` genuinely drops rows.
            "key": [f"k{index % 60}" for index in range(_ROWS)],
            "segment": [f"seg-{index % 4}" for index in range(_ROWS)],
            # 241 is prime and larger than _ROWS, so this is a permutation:
            # every premium is distinct and top_k/bottom_k are deterministic.
            "premium": [float((index * 97) % 241) for index in range(_ROWS)],
            "extra": list(range(_ROWS)),
            "value": [float(index % 13) for index in range(_ROWS)],
            # Interior nulls in a monotone column: something for interpolate to
            # fill, with non-null values on both sides of every gap.
            "gappy": [None if index % 5 in (1, 2) else float(index) for index in range(_ROWS)],
            "ts": [date(2024, 1, 1) + timedelta(days=index % 90) for index in range(_ROWS)],
            "items": [list(range(index % 4)) for index in range(_ROWS)],
        },
        schema={
            "key": pl.String,
            "segment": pl.String,
            "premium": pl.Float64,
            "extra": pl.Int64,
            "value": pl.Float64,
            "gappy": pl.Float64,
            "ts": pl.Date,
            "items": pl.List(pl.Int64),
        },
    ).sort("ts").write_parquet(path)


def _write_right(path: Path) -> None:
    """A lookup frame: unique on ``key``, and a ``value`` column that collides."""
    keys = 60
    pl.DataFrame(
        {
            "key": [f"k{index}" for index in range(keys)],
            # Collides with the left frame's ``value``, so a join must suffix it.
            "value": [float(index * 3) for index in range(keys)],
            "factor": [float(index) / 4 for index in range(keys)],
        },
        schema={"key": pl.String, "value": pl.Float64, "factor": pl.Float64},
    ).write_parquet(path)


def _write_spare(path: Path) -> None:
    """A second parent for the single-input cases; the operator never reads it."""
    pl.DataFrame({"spare": [1, 2, 3]}, schema={"spare": pl.Int64}).write_parquet(path)


def _write_asof_right(path: Path) -> None:
    """A time-ordered lookup for ``join_asof``; Polars requires sorted inputs."""
    days = 90
    pl.DataFrame(
        {
            "ts": [date(2024, 1, 1) + timedelta(days=index) for index in range(0, days, 3)],
            "rate": [float(index) / 8 for index in range(0, days, 3)],
        },
        schema={"ts": pl.Date, "rate": pl.Float64},
    ).write_parquet(path)


# ---------------------------------------------------------------------------
# Graph construction and execution
# ---------------------------------------------------------------------------


def _source(node_id: str, path: Path) -> dict:
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": "dataInput",
            "config": make_ready_file_input_config(path),
        },
    }


def _polars(node_id: str, code: str) -> dict:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": "polars", "config": {"code": code}},
    }


def _boundary_graph(sources: dict[str, Path], operator_code: str):
    """``source(s) -> op -> tail``, so the boundary is materialised mid-graph."""
    return make_graph(
        {
            "nodes": [
                *[_source(node_id, path) for node_id, path in sources.items()],
                _polars("op", operator_code),
                _polars("tail", _TAIL_CODE),
            ],
            "edges": [
                *[make_edge(node_id, "op").model_dump() for node_id in sources],
                make_edge("op", "tail").model_dump(),
            ],
        }
    )


def _execute(graph, context: ExecutionContext, checkpoint_dir: Path) -> pl.DataFrame:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    frames, *_ = execute_lazy_graph(
        graph,
        _build_node_fn,
        target_node_id="tail",
        execution_context=context,
        checkpoint_dir=checkpoint_dir,
    )
    frame = frames["tail"]
    return frame.collect() if isinstance(frame, pl.LazyFrame) else frame


def _assert_boundary_was_checkpointed(checkpoint_dir: Path) -> None:
    """The boundary node's frame really was written to disk.

    Every graph here gives the boundary node two parents, which is one of the
    executor's structural checkpoint triggers, so what this proves is that the
    two-parent trigger fired and the node was materialised to a checkpoint --
    not, on its own, that the operator was planned as a boundary. That claim is
    ``_assert_boundary_was_planned``'s.
    """
    written = sorted(path.name for path in checkpoint_dir.glob("*.parquet"))
    assert "op.parquet" in written, written
    assert (checkpoint_dir / "op.parquet").stat().st_size > 0


def _assert_boundary_was_planned(context: ExecutionContext, operator: str) -> None:
    """Fail unless the run really took the materialisation-boundary path."""
    result = context.projection_plan
    assert result is not None
    assert "op" in result.projection_plan.materialisation_boundaries
    assert result.diagnostic.blocking_node_id == "op"
    assert result.diagnostic.blocking_operator == operator


def _assert_same_schema(planned: pl.DataFrame, expected: pl.DataFrame) -> None:
    assert planned.columns == expected.columns
    assert planned.dtypes == expected.dtypes


def _assert_same_multiset(planned: pl.DataFrame, expected: pl.DataFrame) -> None:
    """Row counts and row contents match, ignoring an undefined row order."""
    _assert_same_schema(planned, expected)
    assert planned.height == expected.height
    order = planned.columns
    assert planned.sort(order).equals(expected.sort(order))


def _run_single_input(
    tmp_path: Path,
    operator_code: str,
    operator: str,
    *,
    native_cap: bool = False,
) -> tuple[pl.DataFrame, pl.LazyFrame]:
    """Execute a one-input boundary graph and return (planned, plain source)."""
    left_path = tmp_path / "left.parquet"
    spare_path = tmp_path / "spare.parquet"
    _write_left(left_path)
    _write_spare(spare_path)
    checkpoint_dir = tmp_path / "checkpoints"
    # ``spare`` is never referenced by the operator code. It is wired in so the
    # boundary node has two parents, which is what makes the executor checkpoint
    # it: without a second parent the boundary would stay lazy and the
    # checkpoint assertion below could not observe anything.
    graph = _boundary_graph({"src": left_path, "spare": spare_path}, operator_code)
    context = create_admitted_execution_context(
        operation=f"equivalence_{operator}",
        profile=ExecutionProfile.LAZY_SINK,
    )

    if native_cap:
        # ``explode`` expands rows by a data-dependent factor, so it has no
        # estimate; a hard worker cap is the documented way to run it anyway.
        with native_memory_backend_scope("rlimit"):
            planned = _execute(graph, context, checkpoint_dir)
    else:
        planned = _execute(graph, context, checkpoint_dir)

    _assert_boundary_was_planned(context, operator)
    _assert_boundary_was_checkpointed(checkpoint_dir)
    return planned, pl.scan_parquet(left_path)


# ---------------------------------------------------------------------------
# Ordering (and schema): sort, reverse, top_k, bottom_k
# ---------------------------------------------------------------------------


def test_sort_boundary_preserves_ordering_and_schema(tmp_path: Path) -> None:
    """Proves: ordering, schema."""
    planned, source = _run_single_input(tmp_path, "df = src.sort('premium', 'extra')", "sort")

    expected = _tail(source.sort("premium", "extra")).collect()
    _assert_same_schema(planned, expected)
    # Compared in order: a boundary that permuted rows would fail here.
    assert planned.equals(expected)


def test_reverse_boundary_preserves_ordering_and_schema(tmp_path: Path) -> None:
    """Proves: ordering, schema."""
    planned, source = _run_single_input(tmp_path, "df = src.reverse()", "reverse")

    expected = _tail(source.reverse()).collect()
    _assert_same_schema(planned, expected)
    assert planned.equals(expected)


def test_top_k_boundary_preserves_ordering_row_count_and_schema(tmp_path: Path) -> None:
    """Proves: ordering, row multiplicity, schema."""
    planned, source = _run_single_input(tmp_path, "df = src.top_k(25, by='premium')", "top_k")

    expected = _tail(source.top_k(25, by="premium")).collect()
    _assert_same_schema(planned, expected)
    assert planned.height == 25
    assert planned.equals(expected)


def test_bottom_k_boundary_preserves_ordering_row_count_and_schema(tmp_path: Path) -> None:
    """Proves: ordering, row multiplicity, schema."""
    planned, source = _run_single_input(tmp_path, "df = src.bottom_k(25, by='premium')", "bottom_k")

    expected = _tail(source.bottom_k(25, by="premium")).collect()
    _assert_same_schema(planned, expected)
    assert planned.height == 25
    assert planned.equals(expected)


# ---------------------------------------------------------------------------
# Row multiplicity: unique, explode
# ---------------------------------------------------------------------------


def test_unique_boundary_preserves_row_multiplicity_and_schema(tmp_path: Path) -> None:
    """Proves: row multiplicity, schema."""
    planned, source = _run_single_input(tmp_path, "df = src.unique(subset=['key'])", "unique")

    expected = _tail(source.unique(subset=["key"])).collect()
    # The fixture repeats every key, so this really does drop rows.
    assert planned.height == 60
    assert planned.height < _ROWS
    _assert_same_multiset(planned, expected)


def test_explode_boundary_preserves_row_multiplicity_and_schema(tmp_path: Path) -> None:
    """Proves: row multiplicity, schema (under a native cap: no estimate exists)."""
    planned, source = _run_single_input(
        tmp_path, "df = src.explode('items')", "explode", native_cap=True
    )

    expected = _tail(source.explode("items")).collect()
    # Lists of length 0-3: an empty list still yields one null row, so the
    # output is neither the input height nor a fixed multiple of it.
    assert planned.height == expected.height
    assert planned.height != _ROWS
    _assert_same_multiset(planned, expected)


def test_interpolate_streams_and_is_not_planned_as_a_boundary(tmp_path: Path) -> None:
    """A negative control: EXEC-P07 measured ``interpolate`` at the streaming floor.

    It is here because it reads neighbouring rows and looks like a boundary. The
    measurement says otherwise, so this asserts the planner does *not* insert a
    boundary, while still proving the executed result matches plain Polars.
    """
    left_path = tmp_path / "left.parquet"
    spare_path = tmp_path / "spare.parquet"
    _write_left(left_path)
    _write_spare(spare_path)
    graph = _boundary_graph({"src": left_path, "spare": spare_path}, "df = src.interpolate()")
    context = create_admitted_execution_context(
        operation="equivalence_interpolate",
        profile=ExecutionProfile.LAZY_SINK,
    )

    planned = _execute(graph, context, tmp_path / "checkpoints")

    result = context.projection_plan
    assert result is not None
    assert "op" not in result.projection_plan.materialisation_boundaries
    assert result.diagnostic.blocking_operator != "interpolate"

    source = pl.scan_parquet(left_path)
    expected = _tail(source.interpolate()).collect()
    assert planned.height == _ROWS
    # The fixture really does have gaps for the operator to fill.
    assert source.collect()["gappy"].null_count() > 0
    assert planned["gappy"].null_count() < _ROWS
    _assert_same_multiset(planned, expected)


# ---------------------------------------------------------------------------
# Window expressions: over
# ---------------------------------------------------------------------------


def test_over_boundary_preserves_values_row_count_and_schema(tmp_path: Path) -> None:
    """Proves: row multiplicity, schema (and the window values themselves)."""
    code = "df = src.with_columns(pl.col('premium').sum().over('segment').alias('segment_total'))"
    planned, source = _run_single_input(tmp_path, code, "over")

    expected = _tail(
        source.with_columns(pl.col("premium").sum().over("segment").alias("segment_total"))
    ).collect()
    assert planned.height == _ROWS
    assert "segment_total" in planned.columns
    _assert_same_multiset(planned, expected)


# ---------------------------------------------------------------------------
# Multi-input: join (inner and left), join_asof
# ---------------------------------------------------------------------------


def _run_two_input(
    tmp_path: Path,
    operator_code: str,
    operator: str,
    *,
    right_writer=_write_right,
    right_name: str = "right.parquet",
) -> tuple[pl.DataFrame, pl.LazyFrame, pl.LazyFrame]:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / right_name
    _write_left(left_path)
    right_writer(right_path)
    checkpoint_dir = tmp_path / "checkpoints"
    graph = _boundary_graph({"left": left_path, "right": right_path}, operator_code)
    context = create_admitted_execution_context(
        operation=f"equivalence_{operator}",
        profile=ExecutionProfile.LAZY_SINK,
    )

    planned = _execute(graph, context, checkpoint_dir)

    _assert_boundary_was_planned(context, operator)
    _assert_boundary_was_checkpointed(checkpoint_dir)
    return planned, pl.scan_parquet(left_path), pl.scan_parquet(right_path)


def _assert_both_ports_retained(planned: pl.DataFrame, expected: pl.DataFrame) -> None:
    """Every left and right column survives, suffixed collisions included."""
    for column in ("key", "segment", "premium", "extra", "value", "gappy", "ts", "items"):
        assert column in planned.columns, column
    for column in ("value_right", "factor"):
        assert column in planned.columns, column
    for column in planned.columns:
        assert planned[column].equals(expected[column]), column


def test_inner_join_boundary_retains_both_ports_and_row_multiplicity(tmp_path: Path) -> None:
    """Proves: row multiplicity, multi-input column retention, schema."""
    code = "df = left.join(right, on='key', how='inner', validate='m:1')"
    planned, left, right = _run_two_input(tmp_path, code, "join")

    expected = _tail(left.join(right, on="key", how="inner", validate="m:1")).collect()
    # Duplicate left keys against a unique right key: many-to-one keeps every
    # left row exactly once, which is the multiplicity a boundary could break.
    assert planned.height == _ROWS
    _assert_same_multiset(planned, expected)
    _assert_both_ports_retained(planned.sort(planned.columns), expected.sort(expected.columns))


def test_left_join_boundary_retains_both_ports_and_row_multiplicity(tmp_path: Path) -> None:
    """Proves: row multiplicity, multi-input column retention, schema."""
    code = "df = left.join(right, on='key', how='left', validate='m:1')"
    planned, left, right = _run_two_input(tmp_path, code, "join")

    expected = _tail(left.join(right, on="key", how="left", validate="m:1")).collect()
    assert planned.height == _ROWS
    _assert_same_multiset(planned, expected)
    _assert_both_ports_retained(planned.sort(planned.columns), expected.sort(expected.columns))


def test_join_asof_boundary_retains_both_ports_and_row_multiplicity(tmp_path: Path) -> None:
    """Proves: row multiplicity, multi-input column retention, schema."""
    code = "df = left.join_asof(right, on='ts')"
    planned, left, right = _run_two_input(
        tmp_path,
        code,
        "join_asof",
        right_writer=_write_asof_right,
        right_name="asof_right.parquet",
    )

    expected = _tail(left.join_asof(right, on="ts")).collect()
    # An as-of join matches at most one right row per left row.
    assert planned.height == _ROWS
    _assert_same_multiset(planned, expected)
    for column in ("key", "segment", "premium", "extra", "value", "gappy", "ts", "items", "rate"):
        assert column in planned.columns, column
