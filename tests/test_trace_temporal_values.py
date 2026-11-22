"""A traced row whose timestamp has digits below a microsecond is found by its own values.

A Polars ``Datetime("ns")`` holds nanoseconds and a Python ``datetime`` microseconds, so a
row read into Python before it is matched again cannot equal its own row, and two rows
100 ns apart look the same (TRC-15). Each row is carried as its exact text instead.
"""

from __future__ import annotations

import polars as pl
import pytest

from haute._native_memory_limit import native_memory_backend_scope
from haute.graph_utils import GraphNode, NodeData, NodeType
from haute.trace import TraceResult, TraceStep, execute_trace
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_START_NS = 1_700_000_000_000_000_000
# The exact text of each start, and of the start 1 ns later the transform computes.
_EARLY, _LATE = "2023-11-14T22:13:20", "2023-11-14T22:13:20.000000100"
_EARLY_NEXT, _LATE_NEXT = "2023-11-14T22:13:20.000000001", "2023-11-14T22:13:20.000000101"
# The factor each start is rated with: the rating table writes its levels with a space.
_FACTORS = {_EARLY: 1.1, _LATE: 1.3}


@pytest.fixture(autouse=True)
def _hard_capped_native_memory():
    with native_memory_backend_scope("rlimit"):
        yield


def _step(result: TraceResult, node_id: str) -> TraceStep:
    [step] = [step for step in result.steps if step.node_id == node_id]
    return step


def _graph(tmp_path):
    """src -> shift (sorts, so a row is found by its values) -> rating (keyed by start)."""
    path = tmp_path / "policies.parquet"
    pl.DataFrame(
        {
            # The rows differ only below a microsecond.
            "start": pl.Series([_START_NS, _START_NS + 100]).cast(pl.Datetime("ns")),
            "base": [100.0, 100.0],
        }
    ).write_parquet(path)
    rating = {
        "tables": [
            {
                "factors": ["start"],
                "output_column": "start_factor",
                "default_value": "1.0",
                "entries": [
                    {"start": "2023-11-14 22:13:20", "value": _FACTORS[_EARLY]},
                    {"start": "2023-11-14 22:13:20.000000100", "value": _FACTORS[_LATE]},
                ],
            }
        ]
    }
    return make_graph(
        {
            "nodes": [
                make_source_node("src", str(path)),
                make_transform_node(
                    "shift",
                    "df = src.sort('start', descending=True).with_columns("
                    "next_start=pl.col('start') + pl.duration(nanoseconds=1))",
                ),
                GraphNode(
                    id="rating",
                    data=NodeData(label="rating", nodeType=NodeType.RATING_STEP, config=rating),
                ),
            ],
            "edges": [make_edge("src", "shift"), make_edge("shift", "rating")],
        }
    )


@pytest.mark.parametrize(("row_index", "start"), [(0, _LATE), (1, _EARLY)])
def test_each_row_is_traced_to_its_own_parent_rows(tmp_path, row_index, start):
    result = execute_trace(
        _graph(tmp_path), row_index=row_index, target_node_id="rating", column="start_factor"
    )

    assert [step.node_id for step in result.steps] == ["src", "shift", "rating"]
    assert result.correlation_diagnostics == []
    for node_id in ("src", "shift", "rating"):
        assert _step(result, node_id).output_values["start"] == start
    detail = _step(result, "rating").node_detail
    assert detail is not None
    [table] = detail["tables"]
    assert table["status"] == "matched"
    assert table["rate_value"] == _FACTORS[start]
    assert result.output_value == _FACTORS[start]


@pytest.mark.parametrize(
    ("row_index", "start", "next_start"), [(0, _LATE, _LATE_NEXT), (1, _EARLY, _EARLY_NEXT)]
)
def test_a_traced_value_is_shown_with_nine_digits(tmp_path, row_index, start, next_start):
    result = execute_trace(
        _graph(tmp_path), row_index=row_index, target_node_id="shift", column="next_start"
    )

    assert result.output_value == next_start
    [derivation] = _step(result, "shift").derivations
    assert derivation["result_value"] == next_start
    assert derivation["substituted_text"] == f'"{start}" + pl.duration(nanoseconds=1)'


def test_a_rating_step_keyed_by_a_duration_is_explained_with_its_level():
    """A duration's exact text is no level an analyst writes, so it is read back into its
    column's type and keyed as the rating join keys the column."""
    from haute._trace_enrichment import enrich_rating_step

    config = {
        "tables": [
            {
                "factors": ["wait"],
                "output_column": "wait_factor",
                "default_value": "1.0",
                "entries": [
                    {"wait": "PT0.0000001S", "value": 1.1},
                    {"wait": "PT0.0000002S", "value": 1.3},
                ],
            }
        ]
    }

    detail = enrich_rating_step(
        config,
        {"wait": "0:00:00.000000200"},
        {"wait_factor": 1.3},
        factor_input_dtypes={"wait": pl.Duration("ns")},
    )

    [table] = detail["tables"]
    assert (table["status"], table["rate_value"]) == ("matched", 1.3)


@pytest.mark.parametrize("row_index", [0, 1])
def test_a_date_and_time_breakpoint_credits_the_band_the_run_chose(tmp_path, row_index):
    """The breakpoint is compared with the traced value read back into its column's
    type, as the run compares the column, never through a Python ``datetime``."""
    path = tmp_path / "starts.parquet"
    pl.DataFrame(
        {"start": pl.Series([_START_NS, _START_NS + 100]).cast(pl.Datetime("ns"))}
    ).write_parquet(path)
    banding = {
        "factors": [
            {
                "column": "start",
                "output_column": "start_band",
                "banding": "breakpoints",
                "rules": {"2023-11-14 22:13:20": "until", "": "after"},
            }
        ]
    }
    graph = make_graph(
        {
            "nodes": [
                make_source_node("src", str(path)),
                GraphNode(
                    id="band",
                    data=NodeData(label="band", nodeType=NodeType.BANDING, config=banding),
                ),
            ],
            "edges": [make_edge("src", "band")],
        }
    )

    result = execute_trace(graph, row_index=row_index, target_node_id="band", column="start_band")

    step = _step(result, "band")
    assert step.output_values["start"] == (_EARLY, _LATE)[row_index]
    detail = step.node_detail
    assert detail is not None
    assert detail["matched_band"] == step.output_values["start_band"]
    assert detail["status"] == "matched"
