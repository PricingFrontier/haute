"""Hand-calculated rating tests verifying preview and trace consistency."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._sandbox import set_project_root
from haute.executor import execute_graph
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.trace import execute_trace
from tests.conftest import make_edge, make_graph, make_source_node


def _rating_graph(tmp_path: Path) -> PipelineGraph:
    set_project_root(tmp_path)
    data = tmp_path / "data.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4"],
            "age": [17.0, 30.0, 65.0, None],
            "region": ["north", "south", "south", "west"],
        }
    ).write_parquet(data)

    factor = {
        "banding": "continuous",
        "column": "age",
        "outputColumn": "age_band",
        "rules": [
            {"op1": "<", "val1": 18, "assignment": "young"},
            {"op1": "<", "val1": 60, "assignment": "adult"},
        ],
        "default": "senior",
    }
    banding = GraphNode(
        id="band",
        data=NodeData(label="band", nodeType="banding", config={"factors": [factor]}),
    )

    table_a = {
        "name": "Age Factor",
        "factors": ["age_band"],
        "outputColumn": "age_factor",
        "entries": [
            {"age_band": "young", "value": 1.5},
            {"age_band": "adult", "value": 1.0},
            {"age_band": "senior", "value": 1.2},
        ],
    }
    table_b = {
        "name": "Region Factor",
        "factors": ["region"],
        "outputColumn": "region_factor",
        "entries": [
            {"region": "north", "value": 0.9},
            {"region": "south", "value": 1.1},
        ],
        "onMissing": "neutral",
    }
    combined = {
        "outputColumn": "premium",
        "operation": "multiply",
        "baseValue": 200.0,
    }
    rating = GraphNode(
        id="rate",
        data=NodeData(
            label="rate",
            nodeType="ratingStep",
            config={"tables": [table_a, table_b], "combinedOutputs": [combined]},
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
