"""Shared graphs, training data, polling helpers and fixtures for the modelling route suites.

The modelling graphs, training data, fast training parameters, job polling and the autouse fixture
that keeps optional training diagnostics fast are used by several ``test_modelling_*`` modules, so
each imports them from this one place.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from tests.conftest import (
    make_edge,
    make_graph,
    make_ready_file_input_config,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _admitted_training_context_for_launch(job_id: str | None = None) -> ExecutionContext:
    """Build an admitted-like context for direct ``_launch_background`` calls."""
    return ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_limit_bytes=1_000,
        memory_baseline_bytes=500,
        rss_limit_bytes=1_500,
        memory_sampler=lambda: 600,
        admission_release=lambda: None,
    )


def _fast_training_params(**overrides: object) -> dict[str, object]:
    """Cheap-but-real CatBoost settings for endpoint tests."""
    params: dict[str, object] = {"iterations": 4, "depth": 2}
    params.update(overrides)
    return params


def _random_evaluation_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.2},
    }


def _evaluation_response_payload() -> dict[str, object]:
    selection_fits = [
        {
            "schema_version": 1,
            "fit_index": 0,
            "train_rows": 8,
            "validation_rows": 2,
            "metrics": {"rmse": 1.0},
        },
        {
            "schema_version": 1,
            "fit_index": 1,
            "train_rows": 8,
            "validation_rows": 2,
            "metrics": {"rmse": 3.0},
        },
    ]
    return {
        "schema_version": 1,
        "strategy": "random",
        "validation_method": "cross_validation",
        "validation_fit_count": 2,
        "fit_count": 3,
        "development_rows": 10,
        "final_test_rows": 2,
        "selection_fits": selection_fits,
        "selection_metrics": {
            "rmse": {
                "mean": 2.0,
                "stddev": 1.0,
                "min": 1.0,
                "max": 3.0,
                "fit_count": 2,
                "validation_rows": 4,
            }
        },
        "plan_sha256": "a" * 64,
        "results_sha256": "b" * 64,
        "plan_path": "outputs/model.evaluation-plan.json",
        "results_path": "outputs/model.evaluation-results.json",
        "report_path": "outputs/model.evaluation-report.json",
        "summary": {
            "development_rows": 10,
            "test_rows": 2,
            "validation_fit_count": 2,
        },
    }


def _completed_train_response(**overrides: object):
    from haute.schemas import TrainResponse

    values: dict[str, object] = {
        "status": "completed",
        "job_id": "test",
        "diagnostic_metrics": {"rmse": 0.12},
        "final_test_metrics": {"rmse": 0.12},
        "development_rows": 10,
        "final_test_rows": 2,
        "diagnostics_set": "final_test",
        "evaluation": _evaluation_response_payload(),
    }
    values.update(overrides)
    return TrainResponse(**values)


def _xgboost_gpu_graph(data_path: str) -> dict:
    """An XGBoost node set to train on the GPU (MOD-F06)."""
    graph = _make_modelling_graph(data_path, algorithm="xgboost", params={"num_boost_round": 5})
    node = next(node for node in graph["nodes"] if node["id"] == "train")
    node["data"]["config"].update({"loss_function": "RMSE", "device": "gpu"})
    return graph


def _make_modelling_graph(
    data_path: str,
    target: str = "y",
    weight: str | None = None,
    algorithm: str = "catboost",
    task: str = "regression",
    params: dict | None = None,
    evaluation: dict | None = None,
    feature_columns: list[str] | None = None,
) -> dict:
    """Build a simple 2-node graph: dataInput → modelling.

    Features are opt-in, so a tree model ticks ``feature_columns``; the default
    is the ``training_data`` fixture's features. Data with other columns passes
    its own list.
    """
    config: dict = {
        "target": target,
        "algorithm": algorithm,
        "task": task,
        "params": params or _fast_training_params(),
        "evaluation": evaluation
        or {
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "validation": {"method": "single", "size": 0.2},
        },
        "metrics": ["gini", "rmse"] if task == "regression" else ["auc", "logloss"],
    }
    if algorithm == "catboost":
        config["loss_function"] = "RMSE" if task == "regression" else "Logloss"
    if algorithm != "glm":
        config["feature_columns"] = ["x1", "x2"] if feature_columns is None else feature_columns
    if weight:
        config["weight"] = weight

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(data_path),
                    },
                },
                {
                    "id": "train",
                    "data": {"label": "train", "nodeType": "modelling", "config": config},
                },
            ],
            "edges": [make_edge("source", "train").model_dump()],
        }
    )
    return graph.model_dump()


def _make_joined_modelling_graph(tmp_path, rows: int, validate: str | None) -> dict:
    """quotes left-joined to competitor prices on quote_id, feeding a modelling node."""
    rng = np.random.RandomState(7)
    quotes = tmp_path / "quotes.parquet"
    competitor = tmp_path / "competitor.parquet"
    pl.DataFrame({"quote_id": range(rows), "x1": rng.randn(rows)}).write_parquet(quotes)
    pl.DataFrame({"quote_id": range(rows), "y": rng.rand(rows) * 10}).write_parquet(competitor)
    join_config: dict[str, object] = {
        "how": "left",
        "maintain_order": "left_right",
        "on": ["quote_id"],
    }
    if validate is not None:
        join_config["validate"] = validate
    model = _make_modelling_graph(str(quotes), feature_columns=["x1"])["nodes"][1]
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "quotes",
                    "data": {
                        "label": "quotes",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(quotes)),
                    },
                },
                {
                    "id": "competitor",
                    "data": {
                        "label": "competitor",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(competitor)),
                    },
                },
                {
                    "id": "competitor_join",
                    "data": {
                        "label": "competitor_join",
                        "nodeType": "edgeJoin",
                        "config": join_config,
                    },
                },
                model,
            ],
            "edges": [
                make_edge("quotes", "competitor_join", target_handle="base").model_dump(),
                make_edge("competitor", "competitor_join", target_handle="join").model_dump(),
                make_edge("competitor_join", "train").model_dump(),
            ],
        }
    )
    return graph.model_dump()


@pytest.fixture(autouse=True)
def _fast_optional_training_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint tests assert job state, metrics, and warnings, not optional charts."""
    monkeypatch.delattr("haute.modelling._algorithms.CatBoostAlgorithm.shap_values")
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])


@pytest.fixture()
def training_data(tmp_path) -> str:
    """Create a small parquet file for training tests."""
    rng = np.random.RandomState(42)
    n = 60
    df = pl.DataFrame(
        {
            "x1": rng.randn(n),
            "x2": rng.randn(n),
            "y": (rng.randn(n) * 2 + 1).clip(0),
        }
    )
    path = tmp_path / "train_data.parquet"
    df.write_parquet(path)
    return str(path)


_TERMINAL_JOB_STATUSES = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    """Poll /train/status/{job_id} until a terminal status, return final status."""
    poll_interval = 0.02
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/modelling/train/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_JOB_STATUSES:
            return data
        time.sleep(poll_interval)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Synchronous GLM schema gate on /train and /dispersion/estimate
# ---------------------------------------------------------------------------


@pytest.fixture()
def glm_collision_data(tmp_path) -> str:
    """Source whose unused ``x_sq`` column collides with an expression key."""
    path = tmp_path / "glm_collision.parquet"
    pl.DataFrame(
        {"x": [1.0, 2.0, 3.0], "x_sq": [1.0, 4.0, 9.0], "y": [1.0, 2.0, 3.0]}
    ).write_parquet(path)
    return str(path)


_MISSING_STEP_COUNT_PAYLOAD = {
    "error_code": "node_config_invalid",
    "message": "Scenario expander requires step_count (the number of grid values).",
    "setting": "step_count",
}


def _expander_without_step_count() -> dict:
    """A Scenario Expander node whose config omits its required grid size."""
    return {
        "id": "expander",
        "data": {
            "label": "expander",
            "nodeType": "scenarioExpander",
            "config": {"column_name": "price", "min_value": 0.1, "max_value": 0.3},
        },
    }


def _glm_schema_gate_graph(data_path: str | None, config: dict):
    """Data Input → Modelling graph; ``data_path=None`` leaves the model unfed."""
    nodes: list[dict] = []
    edges: list[dict] = []
    if data_path is not None:
        nodes.append(
            {
                "id": "source",
                "data": {
                    "label": "source",
                    "nodeType": "dataInput",
                    "config": make_ready_file_input_config(data_path),
                },
            }
        )
        edges.append(make_edge("source", "train").model_dump())
    nodes.append(
        {"id": "train", "data": {"label": "train", "nodeType": "modelling", "config": config}}
    )
    return make_graph({"nodes": nodes, "edges": edges})
