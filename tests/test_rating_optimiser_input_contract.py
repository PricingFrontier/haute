from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._builders import _build_node_fn
from haute._execute_lazy import _compute_needed_columns, _execute_lazy, _prepare_graph
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.parser import parse_pipeline_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RATING_PIPELINE = PROJECT_ROOT / "rating" / "main.py"

# Mirrors the runtime policies schema order in the rating fixture. The model
# scorer validates relative feature order because CatBoost categorical indices
# are positional, so this intentionally differs from the decorator contract.
_RATING_COMPETITOR_MODEL_FEATURE_ORDER = (
    "cover_type",
    "voluntary_excess",
    "compulsory_excess",
    "ncd_years",
    "annual_mileage",
    "proposer_licence_held_years",
    "insurance_group",
    "estimated_value",
    "city",
    "proposer_age",
)

_RATING_MODEL_SPECS_BY_ARTIFACT = {
    "avg_top_5.cbm": (
        _RATING_COMPETITOR_MODEL_FEATURE_ORDER,
        frozenset({"cover_type", "city"}),
        "catboost",
    ),
    "conversion.rsglm": (
        ("difference_to_market",),
        frozenset(),
        "rustystats",
    ),
}


def _rating_graph() -> PipelineGraph:
    return parse_pipeline_file(RATING_PIPELINE, flatten=True)


def _rating_gui_graph() -> PipelineGraph:
    return parse_pipeline_file(RATING_PIPELINE, flatten=False)


def _node_by_label(graph: PipelineGraph, label: str) -> GraphNode:
    return next(node for node in graph.nodes if node.data.label == label)


def _rating_gui_graph_payload() -> dict:
    return _rating_gui_graph().model_dump(mode="json")


def _assert_rating_gui_graph_was_flattened(graph: PipelineGraph) -> None:
    node_ids = {node.id for node in graph.nodes}
    assert "submodel__model_stuff" not in node_ids
    assert {"sale_flag", "competitor_features", "premium"}.issubset(node_ids)
    assert all(
        edge.source != "submodel__model_stuff" and edge.target != "submodel__model_stuff"
        for edge in graph.edges
    )


class _ConstantPredictionModel:
    def __init__(self, value: float) -> None:
        self._value = value

    def predict(self, x_data) -> list[float]:
        return [self._value] * len(x_data)


def _fake_rating_model_loader(graph: PipelineGraph):
    from haute._mlflow_io import ScoringModel

    specs_by_artifact: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {}
    for node in graph.nodes:
        if node.data.nodeType != NodeType.MODEL_SCORE:
            continue
        artifact_path = str(node.data.config["artifact_path"])
        contract = node.data.config["contract"]
        contract_inputs = [str(name) for name in contract["inputs"]]
        if artifact_path not in _RATING_MODEL_SPECS_BY_ARTIFACT:
            raise AssertionError(f"Fake rating model spec missing for {artifact_path!r}")
        feature_names, cat_feature_names, flavor = _RATING_MODEL_SPECS_BY_ARTIFACT[artifact_path]
        if Counter(feature_names) != Counter(contract_inputs):
            raise AssertionError(
                f"Fake rating model features for {artifact_path!r} do not match contract inputs"
            )
        specs_by_artifact[artifact_path] = (
            feature_names,
            cat_feature_names,
            flavor,
        )

    loaded_artifacts: set[str] = set()

    def load_model(*, artifact_path: str, **_kwargs):
        if artifact_path not in specs_by_artifact:
            raise AssertionError(f"Unexpected rating model artifact: {artifact_path}")
        loaded_artifacts.add(artifact_path)
        feature_names, cat_feature_names, flavor = specs_by_artifact[artifact_path]
        return ScoringModel(
            model=_ConstantPredictionModel(0.5),
            feature_names=list(feature_names),
            cat_feature_names=cat_feature_names,
            flavor=flavor,
        )

    return load_model, loaded_artifacts


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

    assert needed["sale_flag"] == {
        "quote_id",
        "premium",
        "competitor_premium",
        "burn_cost",
    }
    assert needed["premium"] == {
        "quote_id",
        "premium",
        "competitor_premium",
        "burn_cost",
    }
    assert needed["join_premiums"] == {
        "quote_id",
        "premium",
        "competitor_premium",
        "policy_id",
    }
    assert needed["join_policy_data"] is None
    assert needed["quoted_premiums"] is None


def test_rating_online_projection_does_not_push_stale_contract_outputs_into_join() -> None:
    graph = _rating_graph()
    for label in {"sale_flag", "premium"}:
        _node_by_label(graph, label).data.config["contract"] = {
            "inputs": [],
            "outputs": [],
        }

    node_map, order, parents_of, _ = _prepare_graph(
        graph,
        target_node_id="optimiser_input",
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
            required_columns_by_node={
                "optimiser_input": {
                    "quote_id",
                    "scenario_index",
                    "premium_multiplier",
                    "conversion_prediction",
                    "expected_margin",
                }
            },
        )

    assert needed["join_premiums"] is None


def test_rating_online_estimate_executes_gui_submodel_graph() -> None:
    from haute.routes.optimiser import OptimiserEstimateRequest, _optimiser_input_metrics

    graph = _rating_gui_graph()
    load_model, loaded_artifacts = _fake_rating_model_loader(graph)

    with patch("haute._mlflow_io.load_mlflow_model", side_effect=load_model):
        metrics = _optimiser_input_metrics(
            OptimiserEstimateRequest(graph=graph, node_id="online_optimiser"),
        )

    assert loaded_artifacts == set(_RATING_MODEL_SPECS_BY_ARTIFACT)
    assert metrics["quote_count"] == 1_000_000
    assert metrics["scenarios_per_quote_min"] == 21
    assert metrics["scenarios_per_quote_max"] == 21
    assert metrics["expanded_row_count"] == 21_000_000


def test_rating_online_estimate_route_flattens_gui_submodel_graph(
    client,
    clean_job_store,
) -> None:
    del clean_job_store
    observed: dict[str, bool] = {}

    def fake_source_metadata(graph: PipelineGraph, node_id: str, source: str) -> tuple[int, int]:
        assert node_id == "online_optimiser"
        assert source == "live"
        _assert_rating_gui_graph_was_flattened(graph)
        observed["source_metadata"] = True
        return 1_000_000, 84

    def fake_metrics(body) -> dict[str, int | float | None]:
        _assert_rating_gui_graph_was_flattened(body.graph)
        observed["metrics"] = True
        return {
            "quote_count": 1_000_000,
            "scenarios_per_quote_min": 21,
            "scenarios_per_quote_max": 21,
            "scenarios_per_quote_mean": 21.0,
            "expanded_row_count": 21_000_000,
        }

    with (
        patch("haute._ram_estimate._ancestor_source_metadata", side_effect=fake_source_metadata),
        patch("haute.routes.optimiser._optimiser_input_metrics", side_effect=fake_metrics),
    ):
        response = client.post(
            "/api/optimiser/estimate",
            json={"graph": _rating_gui_graph_payload(), "node_id": "online_optimiser"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "total_rows": 1_000_000,
        "quote_count": 1_000_000,
        "scenarios_per_quote_min": 21,
        "scenarios_per_quote_max": 21,
        "scenarios_per_quote_mean": 21.0,
        "expanded_row_count": 21_000_000,
    }
    assert observed == {"source_metadata": True, "metrics": True}


@pytest.mark.parametrize(
    ("endpoint", "service_method", "service_response"),
    [
        (
            "/api/optimiser/solve",
            "start",
            "OptimiserSolveResponse",
        ),
        (
            "/api/optimiser/frontier/auto-range",
            "estimate_frontier_auto_range",
            "OptimiserFrontierAutoRangeResponse",
        ),
        (
            "/api/optimiser/frontier/auto-range/start",
            "start_frontier_auto_range",
            "OptimiserFrontierAutoRangeStartResponse",
        ),
    ],
)
def test_rating_online_optimiser_routes_flatten_gui_submodel_graph_before_service_handoff(
    endpoint: str,
    service_method: str,
    service_response: str,
    client,
    clean_job_store,
) -> None:
    del clean_job_store
    from haute.routes import optimiser as optimiser_module
    from haute.schemas import (
        OptimiserFrontierAutoRangeResponse,
        OptimiserFrontierAutoRangeStartResponse,
        OptimiserSolveResponse,
    )

    responses = {
        "OptimiserSolveResponse": OptimiserSolveResponse(status="started", job_id="solve-job"),
        "OptimiserFrontierAutoRangeResponse": OptimiserFrontierAutoRangeResponse(
            status="ok",
            ranges={},
            warning=None,
        ),
        "OptimiserFrontierAutoRangeStartResponse": OptimiserFrontierAutoRangeStartResponse(
            status="started",
            job_id="range-job",
        ),
    }
    observed: dict[str, bool] = {}

    def fake_service_call(body):
        _assert_rating_gui_graph_was_flattened(body.graph)
        observed["service"] = True
        return responses[service_response]

    with patch.object(
        optimiser_module._solve_service,
        service_method,
        side_effect=fake_service_call,
    ):
        response = client.post(
            endpoint,
            json={"graph": _rating_gui_graph_payload(), "node_id": "online_optimiser"},
        )

    assert response.status_code == 200
    assert observed == {"service": True}


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
