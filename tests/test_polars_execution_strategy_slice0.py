from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from tests.conftest import (
    make_edge,
    make_graph,
    make_ready_file_input_config,
)

_OPAQUE_PROJECTION_ERROR = "User-code projection requires a concrete node contract"


def test_routes_use_execution_facade_for_projection_planning() -> None:
    route_dir = Path("src/haute/routes")
    offenders = [
        path
        for path in route_dir.glob("*.py")
        if "haute.projection" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


@pytest.fixture()
def _route_job_store_snapshots() -> Iterator[None]:
    from haute.routes.modelling import _store as training_store
    from haute.routes.optimiser import _store as optimiser_store

    training_snapshot = dict(training_store.jobs)
    optimiser_snapshot = dict(optimiser_store.jobs)
    yield
    training_store.jobs.clear()
    training_store.jobs.update(training_snapshot)
    optimiser_store.jobs.clear()
    optimiser_store.jobs.update(optimiser_snapshot)


def _write_competitor_training_inputs(tmp_path: Path) -> tuple[str, str]:
    policies_path = tmp_path / "policies.parquet"
    competitor_path = tmp_path / "competitor_insights.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4"],
            "policy_id": ["p1", "p2", "p3", "p4"],
            "driver_age": [33, 47, 28, 61],
            "vehicle_age": [2, 8, 4, 12],
            "premium": [410.0, 520.0, 390.0, 690.0],
            "target": [1.0, 0.0, 1.0, 0.0],
        }
    ).write_parquet(policies_path)
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4"],
            "market_rank": [1, 4, 2, 5],
            "market_premium": [399.0, 545.0, 402.0, 710.0],
        }
    ).write_parquet(competitor_path)
    return str(policies_path), str(competitor_path)


def _make_avg_top_5_competitor_join_graph(
    policies_path: str,
    competitor_path: str,
) -> dict:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "policies",
                    "data": {
                        "label": "policies",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(policies_path),
                    },
                },
                {
                    "id": "competitor_insights",
                    "data": {
                        "label": "competitor_insights",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(competitor_path),
                    },
                },
                {
                    "id": "competitor_join",
                    "data": {
                        "label": "competitor_join",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = policies.join("
                                "competitor_insights, on='quote_id', how='inner')"
                            )
                        },
                    },
                },
                {
                    "id": "avg_top_5",
                    "data": {
                        "label": "avg_top_5",
                        "nodeType": "modelling",
                        "config": {
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                            "target": "target",
                            "exclude": ["quote_id", "policy_id"],
                            "params": {"iterations": 1, "depth": 1},
                        },
                    },
                },
            ],
            "edges": [
                make_edge("policies", "competitor_join").model_dump(),
                make_edge("competitor_insights", "competitor_join").model_dump(),
                make_edge("competitor_join", "avg_top_5").model_dump(),
            ],
        }
    )
    return graph.model_dump()


@pytest.mark.usefixtures("_widen_sandbox_root", "_route_job_store_snapshots")
def test_catboost_avg_top_5_training_allows_contract_free_competitor_join(
    client,
    tmp_path: Path,
) -> None:
    policies_path, competitor_path = _write_competitor_training_inputs(tmp_path)
    graph = _make_avg_top_5_competitor_join_graph(policies_path, competitor_path)

    with patch("haute.routes._train_service.TrainService._launch_background"):
        resp = client.post(
            "/api/modelling/train",
            json={"graph": graph, "node_id": "avg_top_5"},
        )

    assert resp.status_code == 200, resp.text
    assert _OPAQUE_PROJECTION_ERROR not in resp.text
    assert resp.json()["status"] == "started"
    assert isinstance(resp.json()["job_id"], str)


def _write_optimiser_input(tmp_path: Path) -> str:
    path = tmp_path / "optimiser_input.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "premium_multiplier": pl.Series([0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
            "expected_margin": pl.Series([80.0, 100.0, 75.0, 95.0], dtype=pl.Float32),
            "conversion_prediction": pl.Series([0.20, 0.30, 0.25, 0.35], dtype=pl.Float32),
            "unused_payload": ["drop-a", "drop-b", "drop-c", "drop-d"],
        }
    ).write_parquet(path)
    return str(path)


def _write_ratebook_banding_input(tmp_path: Path) -> str:
    path = tmp_path / "age_veh_banding.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "channel_band": ["direct", "broker"],
            "proposer_age_band": ["30_40", "40_50"],
            "vehicle_age_band": ["0_5", "5_10"],
            "unused_factor_payload": [10, 20],
        }
    ).write_parquet(path)
    return str(path)


def _optimiser_config(mode: str) -> dict:
    config: dict = {
        "mode": mode,
        "objective": "expected_margin",
        "constraints": {"conversion_prediction": {"min": 0.0}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "premium_multiplier",
        "data_input": "optimiser_input",
    }
    if mode == "ratebook":
        config.update(
            {
                "banding_source": "age_veh_banding",
                "factor_columns": [
                    ["channel_band"],
                    ["proposer_age_band"],
                    ["vehicle_age_band"],
                ],
            }
        )
    return config


def _make_optimiser_estimate_graph(
    optimiser_input_path: str,
    *,
    mode: str,
    banding_path: str | None = None,
) -> dict:
    nodes = [
        {
            "id": "optimiser_input",
            "data": {
                "label": "optimiser_input",
                "nodeType": "dataInput",
                "config": make_ready_file_input_config(
                    optimiser_input_path,
                    contract="opaque",
                    code=(
                        "df = df.with_columns("
                        "source_projection_probe=pl.col('expected_margin') * 0)"
                    ),
                ),
            },
        },
        {
            "id": f"{mode}_optimiser" if mode != "online" else "online_optimiser",
            "data": {
                "label": f"{mode}_optimiser" if mode != "online" else "online_optimiser",
                "nodeType": "optimiser",
                "config": _optimiser_config(mode),
            },
        },
    ]
    edges = [make_edge("optimiser_input", nodes[-1]["id"]).model_dump()]
    if mode == "ratebook":
        assert banding_path is not None
        nodes.insert(
            1,
            {
                "id": "age_veh_banding",
                "data": {
                    "label": "age_veh_banding",
                    "nodeType": "dataInput",
                    "config": make_ready_file_input_config(banding_path),
                },
            },
        )
        edges.append(make_edge("age_veh_banding", "ratebook_optimiser").model_dump())

    graph = make_graph({"nodes": nodes, "edges": edges})
    return graph.model_dump()


@pytest.mark.parametrize(
    ("mode", "node_id"),
    [("online", "online_optimiser"), ("ratebook", "ratebook_optimiser")],
)
@pytest.mark.usefixtures("_widen_sandbox_root", "_route_job_store_snapshots")
def test_optimiser_estimate_setup_allows_opaque_data_source_projection(
    client,
    tmp_path: Path,
    mode: str,
    node_id: str,
) -> None:
    optimiser_input_path = _write_optimiser_input(tmp_path)
    banding_path = _write_ratebook_banding_input(tmp_path) if mode == "ratebook" else None
    graph = _make_optimiser_estimate_graph(
        optimiser_input_path,
        mode=mode,
        banding_path=banding_path,
    )

    resp = client.post(
        "/api/optimiser/estimate",
        json={"graph": graph, "node_id": node_id},
    )

    assert resp.status_code == 200, resp.text
    assert _OPAQUE_PROJECTION_ERROR not in resp.text
    data = resp.json()
    assert data["expanded_row_count"] == 4
    assert data["quote_count"] == 2
    assert data["scenarios_per_quote_min"] == 2
    assert data["scenarios_per_quote_max"] == 2
