from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl

from haute._builders import _build_node_fn
from haute._execute_lazy import _compute_needed_columns, _execute_lazy, _prepare_graph
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.parser import parse_pipeline_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RATING_PIPELINE = PROJECT_ROOT / "rating" / "main.py"


def _rating_graph() -> PipelineGraph:
    return parse_pipeline_file(RATING_PIPELINE)


def _node_by_label(graph: PipelineGraph, label: str) -> GraphNode:
    return next(node for node in graph.nodes if node.data.label == label)


def test_rating_optimiser_input_declares_online_solver_shape() -> None:
    graph = _rating_graph()
    optimiser_input = _node_by_label(graph, "optimiser_input")
    premium = _node_by_label(graph, "premium")
    competitor_features_scenarios = _node_by_label(graph, "competitor_features_scenarios")
    online_optimiser = _node_by_label(graph, "online_optimiser")

    online_config = online_optimiser.data.config
    premium_contract = premium.data.config["contract"]
    assert set(premium_contract["inputs"]) == {"premium"}
    assert set(premium_contract["outputs"]) == {"premium_multiplier", "scenario_index"}

    competitor_contract = competitor_features_scenarios.data.config["contract"]
    assert set(competitor_contract["inputs"]) == {"premium", "competitor_premium"}
    assert set(competitor_contract["outputs"]) == {"difference_to_market"}

    optimiser_input_contract = optimiser_input.data.config["contract"]
    assert set(optimiser_input_contract["inputs"]) == {
        "premium",
        "burn_cost",
        "conversion_prediction",
    }
    assert set(optimiser_input_contract["outputs"]) == {"margin", online_config["objective"]}
    assert "selected_columns" not in optimiser_input.data.config


def test_rating_online_auto_range_projection_crosses_join_fan_in() -> None:
    graph = _rating_graph()
    online_optimiser = _node_by_label(graph, "online_optimiser")
    online_config = online_optimiser.data.config
    required = {
        str(online_config.get("quote_id", "quote_id")),
        *[str(cname) for cname in (online_config.get("constraints") or {})],
    }

    node_map, order, parents_of, _ = _prepare_graph(
        graph,
        target_node_id=online_optimiser.id,
        source="nb_batch",
    )
    children_of = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            children_of[pid].append(nid)

    mock_model = MagicMock()
    mock_model.feature_names = ["difference_to_market"]

    with patch("haute._mlflow_io.load_mlflow_model", return_value=mock_model):
        needed = _compute_needed_columns(
            order,
            children_of,
            node_map,
            required_columns_by_node={"optimiser_input": required},
        )

    assert needed["join_premiums"] == {
        "quote_id",
        "premium",
        "competitor_premium",
        "burn_cost",
    }
    assert needed["join_policy_data"] == {"quote_id", "policy_id", "competitor_premium"}
    assert needed["quoted_premiums"] == {"quote_id", "premium"}
    assert needed["join_scoring"] == {"quote_id", "competitor_premium"}
    assert needed["policy_data"] == {"quote_id", "policy_id"}
    assert needed["competitor_scoring"] == {"quote_id", "competitor_premium"}


def test_rating_optimiser_input_contract_preserves_shared_output_shape(tmp_path) -> None:
    parsed_graph = _rating_graph()
    parsed_optimiser_input = _node_by_label(parsed_graph, "optimiser_input")
    parsed_online_optimiser = _node_by_label(parsed_graph, "online_optimiser")

    input_path = tmp_path / "conversion_scoring_output.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "scenario_index": pl.Series([0, 1], dtype=pl.Int32),
            "premium_multiplier": pl.Series([0.9, 1.1], dtype=pl.Float32),
            "premium": [100.0, 200.0],
            "burn_cost": [60.0, 140.0],
            "conversion_prediction": [0.25, 0.5],
            "unused_wide_column": ["drop me", "drop me too"],
        }
    ).write_parquet(input_path)

    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="conversion_scoring",
                data=NodeData(
                    label="conversion_scoring",
                    nodeType="dataSource",
                    config={"path": str(input_path)},
                ),
            ),
            GraphNode(
                id="optimiser_input",
                data=parsed_optimiser_input.data.model_copy(
                    update={"config": deepcopy(parsed_optimiser_input.data.config)}
                ),
            ),
            GraphNode(
                id="online_optimiser",
                data=parsed_online_optimiser.data.model_copy(
                    update={"config": deepcopy(parsed_online_optimiser.data.config)}
                ),
            ),
        ],
        edges=[
            GraphEdge(
                id="e_conversion_scoring_optimiser_input",
                source="conversion_scoring",
                target="optimiser_input",
            ),
            GraphEdge(
                id="e_optimiser_input_online_optimiser",
                source="optimiser_input",
                target="online_optimiser",
            ),
        ],
        source_file=str(RATING_PIPELINE),
    )

    outputs, *_ = _execute_lazy(
        graph,
        _build_node_fn,
        target_node_id="online_optimiser",
        enforce_contracts=True,
    )

    result = outputs["online_optimiser"].collect()

    assert result.columns == [
        "quote_id",
        "scenario_index",
        "premium_multiplier",
        "premium",
        "burn_cost",
        "conversion_prediction",
        "unused_wide_column",
        "margin",
        "expected_margin",
    ]
    assert result["margin"].to_list() == [40.0, 60.0]
    assert result["expected_margin"].to_list() == [10.0, 30.0]
