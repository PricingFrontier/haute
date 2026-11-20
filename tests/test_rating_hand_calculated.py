"""Hand-calculated rating tests verifying preview and trace consistency."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute.executor import execute_graph
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.trace import execute_trace
from tests._project import use_project
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node


def _rating_graph(tmp_path: Path) -> PipelineGraph:
    use_project(tmp_path)
    data = tmp_path / "data.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4"],
            "age": [17.0, 30.0, 65.0, None],
            "region": ["north", "south", "south", "west"],
        }
    ).write_parquet(data)

    factor = {
        "banding": "breakpoints",
        "column": "age",
        "output_column": "age_band",
        "rules": [
            {"boundary": "18", "label": "young"},
            {"boundary": "60", "label": "adult"},
        ],
        "right_closed": False,
        "default": "senior",
    }
    banding = GraphNode(
        id="band",
        data=NodeData(label="band", nodeType="banding", config={"factors": [factor]}),
    )

    table_a = {
        "factors": ["age_band"],
        "output_column": "age_factor",
        "entries": [
            {"age_band": "young", "value": 1.5},
            {"age_band": "adult", "value": 1.0},
            {"age_band": "senior", "value": 1.2},
        ],
    }
    table_b = {
        "factors": ["region"],
        "output_column": "region_factor",
        "entries": [
            {"region": "north", "value": 0.9},
            {"region": "south", "value": 1.1},
        ],
    }
    combined = {
        "output_column": "premium",
        "operation": "multiply",
        "base_value": 200.0,
    }
    rating = GraphNode(
        id="rate",
        data=NodeData(
            label="rate",
            nodeType="ratingStep",
            config={"tables": [table_a, table_b], "combined_outputs": [combined]},
        ),
    )

    return make_graph(
        {
            "nodes": [make_source_node("src", str(data)), banding, rating],
            "edges": [make_edge("src", "band"), make_edge("band", "rate")],
        }
    )


def test_rating_preview_matches_hand_calculated_premiums(tmp_path: Path) -> None:
    graph = _rating_graph(tmp_path)
    results = execute_graph(graph, target_node_id="rate")
    preview = results["rate"].preview

    # Hand-calculated expectations:
    # q1: age 17 -> "young" -> 1.5; north 0.9 -> 200.0 * 1.5 * 0.9 = 270.0
    # q2: age 30 -> "adult" -> 1.0; south 1.1 -> 200.0 * 1.0 * 1.1 = 220.0
    # q3: age 65 -> "senior" -> 1.2; south 1.1 -> 200.0 * 1.2 * 1.1 = 264.0
    # q4: age null -> default band "senior" -> 1.2; west misses table_b -> neutral 1.0
    #     -> 200.0 * 1.2 * 1.0 = 240.0
    expected = [
        {
            "quote_id": "q1",
            "age": 17.0,
            "region": "north",
            "age_band": "young",
            "age_factor": 1.5,
            "region_factor": 0.9,
            "premium": 270.0,
        },
        {
            "quote_id": "q2",
            "age": 30.0,
            "region": "south",
            "age_band": "adult",
            "age_factor": 1.0,
            "region_factor": 1.1,
            "premium": 220.0,
        },
        {
            "quote_id": "q3",
            "age": 65.0,
            "region": "south",
            "age_band": "senior",
            "age_factor": 1.2,
            "region_factor": 1.1,
            "premium": 264.0,
        },
        {
            "quote_id": "q4",
            "age": None,
            "region": "west",
            "age_band": "senior",
            "age_factor": 1.2,
            # Miss contract: src/haute/_rating.py:896-898, test_rating_key_agreement.py:952
            "region_factor": None,
            "premium": 240.0,
        },
    ]

    assert len(preview) == len(expected)
    for row, exp in zip(preview, expected):
        assert row["quote_id"] == exp["quote_id"]
        assert row["age"] == exp["age"]
        assert row["region"] == exp["region"]
        assert row["age_band"] == exp["age_band"]
        assert row["age_factor"] == pytest.approx(exp["age_factor"], abs=1e-9)
        if exp["region_factor"] is None:
            assert row["region_factor"] is None
        else:
            assert row["region_factor"] == pytest.approx(exp["region_factor"], abs=1e-9)
        assert row["premium"] == pytest.approx(exp["premium"], abs=1e-9)


def test_rating_trace_matches_hand_calculated_premiums(tmp_path: Path) -> None:
    graph = _rating_graph(tmp_path)
    results = execute_graph(graph, target_node_id="rate")
    preview = results["rate"].preview

    # q3 (row_index=2): 200.0 * 1.2 * 1.1 = 264.0
    result_q3 = execute_trace(graph, row_index=2, target_node_id="rate", column="premium")
    assert result_q3.output_value == pytest.approx(264.0, abs=1e-9)
    assert result_q3.output_value == pytest.approx(preview[2]["premium"], abs=1e-9)

    step_q3 = next(s for s in result_q3.steps if s.node_id == "rate")
    assert step_q3.node_detail is not None
    tables_q3 = step_q3.node_detail["tables"]
    assert tables_q3[0]["status"] == "matched"
    assert tables_q3[0]["matched_entry"]["value"] == pytest.approx(1.2, abs=1e-9)
    assert tables_q3[1]["status"] == "matched"
    assert tables_q3[1]["matched_entry"]["value"] == pytest.approx(1.1, abs=1e-9)

    # q4 (row_index=3): 200.0 * 1.2 * 1.0 = 240.0
    result_q4 = execute_trace(graph, row_index=3, target_node_id="rate", column="premium")
    assert result_q4.output_value == pytest.approx(240.0, abs=1e-9)
    assert result_q4.output_value == pytest.approx(preview[3]["premium"], abs=1e-9)

    step_q4 = next(s for s in result_q4.steps if s.node_id == "rate")
    assert step_q4.node_detail is not None
    tables_q4 = step_q4.node_detail["tables"]
    assert tables_q4[0]["status"] == "matched"
    assert tables_q4[0]["matched_entry"]["value"] == pytest.approx(1.2, abs=1e-9)
    assert tables_q4[1]["status"] == "no_match"
    assert tables_q4[1]["matched"] is False


def _rating_only_graph(tmp_path: Path, table: dict, transform: str = "") -> PipelineGraph:
    use_project(tmp_path)
    data = tmp_path / "data.parquet"
    pl.DataFrame({"quote_id": ["q1", "q2"], "region": ["north", "south"]}).write_parquet(data)
    rating = GraphNode(
        id="rate",
        data=NodeData(label="rate", nodeType="ratingStep", config={"tables": [table]}),
    )
    nodes = [make_source_node("src", str(data)), rating]
    edges = [make_edge("src", "rate")]
    if transform:
        nodes.insert(1, make_transform_node("add", transform))
        edges = [make_edge("src", "add"), make_edge("add", "rate")]
    return make_graph({"nodes": nodes, "edges": edges})


def test_a_table_with_a_default_and_no_entries_prices_every_row_in_the_preview(
    tmp_path: Path,
) -> None:
    """RTG-04: the node's contract promises the column, and the Default fills it."""
    table = {
        "factors": ["region"],
        "output_column": "region_factor",
        "entries": [],
        "default_value": 15,
    }
    preview = execute_graph(_rating_only_graph(tmp_path, table), target_node_id="rate")[
        "rate"
    ].preview
    assert [row["region_factor"] for row in preview] == [15.0, 15.0]


def test_a_factor_column_of_null_type_prices_every_row_at_the_default_in_the_preview(
    tmp_path: Path,
) -> None:
    """RTG-06: no entry can match a Null column, so no two entries collide on it."""
    table = {
        "factors": ["territory"],
        "output_column": "territory_factor",
        "entries": [{"territory": "NW", "value": 1.1}, {"territory": "SE", "value": 1.2}],
        "default_value": 1.0,
    }
    graph = _rating_only_graph(
        tmp_path, table, transform='df = src.with_columns(pl.lit(None).alias("territory"))'
    )
    result = execute_graph(graph, target_node_id="rate")["rate"]
    assert result.status == "ok", result.error
    assert [row["territory_factor"] for row in result.preview] == [1.0, 1.0]
