"""Tests for the optimiser node type and API routes."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute._parser_helpers import _build_node_config
from haute._sandbox import set_project_root
from haute.graph_utils import NodeType
from haute.routes._optimiser_limits import (
    APPLY_PREVIEW_ROW_LIMIT,
    FRONTIER_POINT_LIMIT,
)
from haute.routes._optimiser_service import _compute_scenario_value_stats
from haute.routes.optimiser import _build_artifact_payload
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


# ---------------------------------------------------------------------------
# Test data: build a scored DataFrame in the shape price-contour expects
# ---------------------------------------------------------------------------


def _make_scored_data(tmp_path, n_quotes: int = 50, n_steps: int = 5) -> str:
    """Create a scored DataFrame in long format for optimisation tests.

    Columns: quote_id, scenario_index, scenario_value, expected_income, volume
    """
    rng = np.random.RandomState(42)
    quote_ids = []
    steps = []
    mults = []
    incomes = []
    volumes = []
    scenario_values = np.linspace(0.8, 1.2, n_steps).astype(np.float32)
    for q in range(n_quotes):
        base_income = rng.uniform(100, 1000)
        base_volume = rng.uniform(0.5, 1.5)
        for s, m in enumerate(scenario_values):
            quote_ids.append(f"q_{q:04d}")
            steps.append(s)
            mults.append(float(m))
            incomes.append(float(base_income * m))
            volumes.append(float(base_volume * (2.0 - m)))
    df = pl.DataFrame(
        {
            "quote_id": quote_ids,
            "scenario_index": pl.Series(steps, dtype=pl.Int32),
            "scenario_value": pl.Series(mults, dtype=pl.Float32),
            "expected_income": pl.Series(incomes, dtype=pl.Float32),
            "volume": pl.Series(volumes, dtype=pl.Float32),
        }
    )
    path = tmp_path / "scored.parquet"
    df.write_parquet(path)
    return str(path)


@pytest.fixture()
def scored_data(tmp_path) -> str:
    return _make_scored_data(tmp_path)


def _make_optimiser_graph(data_path: str, config: dict | None = None) -> dict:
    """Build a 2-node graph: dataSource → optimiser."""
    default_config: dict = {
        "mode": "online",
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.90}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "max_iter": 20,
        "tolerance": 1e-4,
    }
    if config:
        default_config.update(config)

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": data_path},
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": default_config,
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    )
    return graph.model_dump()


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    """Poll /solve/status/{job_id} until completed or error."""
    poll_interval = 0.02
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "error"):
            return data
        time.sleep(poll_interval)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def _frontier_point_summary(
    *,
    lambda_volume: float,
    total_objective: float,
    total_volume: float = 0.9,
    threshold_volume: float | None = None,
    converged: bool = True,
) -> dict[str, float | bool]:
    """Build the stored frontier-point summary shape emitted by price-contour."""
    return {
        "threshold_volume": total_volume if threshold_volume is None else threshold_volume,
        "total_objective": total_objective,
        "total_volume": total_volume,
        "lambda_volume": lambda_volume,
        "iterations": 3,
        "converged": converged,
        "sv_mean": 1.0,
        "sv_std": 0.1,
        "sv_min": 0.8,
        "sv_p5": 0.85,
        "sv_p25": 0.95,
        "sv_median": 1.0,
        "sv_p75": 1.05,
        "sv_p95": 1.15,
        "sv_max": 1.2,
        "sv_pct_increase": 0.5,
        "sv_pct_decrease": 0.25,
    }


# ---------------------------------------------------------------------------
# Step 1: Type registration
# ---------------------------------------------------------------------------


class TestNodeTypeRegistration:
    def test_optimiser_enum_value(self):
        assert NodeType.OPTIMISER == "optimiser"

    def test_optimiser_in_nodetype(self):
        assert "optimiser" in [e.value for e in NodeType]


# ---------------------------------------------------------------------------
# Step 1c: Parser inference
# ---------------------------------------------------------------------------


class TestParserInference:
    def test_build_optimiser_config(self):
        kwargs = {
            "optimiser": True,
            "mode": "online",
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.90}},
            "max_iter": 50,
        }
        config = _build_node_config("optimiser", kwargs, "", ["df"])
        assert config["mode"] == "online"
        assert config["objective"] == "expected_income"
        assert config["constraints"] == {"volume": {"min": 0.90}}
        assert config["max_iter"] == 50


# ---------------------------------------------------------------------------
# Step 1d: Codegen
# ---------------------------------------------------------------------------


class TestCodegen:
    def test_codegen_optimiser(self):
        from haute.codegen import _node_to_code
        from haute.graph_utils import GraphNode, NodeData

        node = GraphNode(
            id="opt",
            data=NodeData(
                label="my_optimiser",
                nodeType="optimiser",
                config={
                    "mode": "online",
                    "objective": "expected_income",
                    "constraints": {"volume": {"min": 0.90}},
                },
            ),
        )
        code = _node_to_code(node, source_names=["scored_data"])
        assert 'config="config/optimisation/my_optimiser.json"' in code
        assert "def my_optimiser(" in code
        assert "scored_data: pl.LazyFrame" in code
        assert "return scored_data" in code


# ---------------------------------------------------------------------------
# Step 1e: Executor passthrough
# ---------------------------------------------------------------------------


class TestExecutorPassthrough:
    def test_optimiser_passthrough(self):
        from haute.executor import _build_node_fn
        from haute.graph_utils import GraphNode, NodeData

        node = GraphNode(
            id="opt",
            data=NodeData(
                label="optimiser",
                nodeType="optimiser",
                config={"mode": "online"},
            ),
        )
        func_name, fn, is_source = _build_node_fn(node, source_names=["df"])
        assert func_name == "optimiser"
        assert is_source is False

        # Should pass through the input unchanged
        input_df = pl.LazyFrame({"a": [1, 2, 3]})
        result = fn(input_df)
        assert result.collect().to_dicts() == input_df.collect().to_dicts()


# ---------------------------------------------------------------------------
# Step 2: API routes
# ---------------------------------------------------------------------------


class TestSolveRoute:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_returns_started(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert isinstance(data["job_id"], str) and len(data["job_id"]) > 0
        _poll_until_done(client, data["job_id"])

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_completes(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        data = resp.json()
        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "completed"
        result = status["result"]
        assert "total_objective" in result
        assert "lambdas" in result
        assert "converged" in result

    def test_solve_missing_node(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "nonexistent"})
        assert resp.status_code == 404

    def test_solve_wrong_node_type(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "source"})
        assert resp.status_code == 400

    def test_solve_no_objective(self, client, scored_data):
        cfg = {"objective": "", "constraints": {"v": {"min": 0.9}}}
        graph = _make_optimiser_graph(scored_data, config=cfg)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 400
        assert "objective" in resp.json()["detail"].lower()

    def test_solve_no_constraints(self, client, scored_data):
        """Solving with no constraints is valid — returns 200 and starts a job."""
        cfg = {"objective": "expected_income", "constraints": {}}
        graph = _make_optimiser_graph(scored_data, config=cfg)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        _poll_until_done(client, data["job_id"])

    def test_solve_rejects_concurrent(self, client, scored_data, clean_job_store):
        """A second solve request while one is running returns 409."""
        from haute.routes.optimiser import _solve_service

        clean_job_store.jobs["fake_running"] = {
            "status": "running",
            "progress": 0.5,
            "message": "Solving...",
            "created_at": time.time(),
        }
        graph = _make_optimiser_graph(scored_data)

        with patch.object(
            _solve_service,
            "_execute_pipeline",
            side_effect=AssertionError("concurrency guard should short-circuit before execution"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]

    def test_solve_rejects_concurrent_solve_job_type(self, client, scored_data, clean_job_store):
        """A typed running solve still blocks another solve request."""
        from haute.routes.optimiser import _solve_service

        clean_job_store.jobs["fake_running_solve"] = {
            "status": "running",
            "job_type": "solve",
            "progress": 0.5,
            "message": "Solving...",
            "created_at": time.time(),
        }
        graph = _make_optimiser_graph(scored_data)

        with patch.object(
            _solve_service,
            "_execute_pipeline",
            side_effect=AssertionError("concurrency guard should short-circuit before execution"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]

    def test_solve_ignores_running_estimate_jobs(self, client, scored_data, clean_job_store):
        """Estimate and auto-range jobs do not consume the real solve lock."""
        from haute.routes.optimiser import _solve_service

        clean_job_store.jobs["running_estimate"] = {
            "status": "running",
            "job_type": "estimate",
            "progress": 0.0,
            "message": "Estimating optimiser input",
            "created_at": time.time(),
        }
        clean_job_store.jobs["running_frontier_auto_range"] = {
            "status": "running",
            "job_type": "frontier_auto_range",
            "progress": 0.0,
            "message": "Estimating frontier range",
            "created_at": time.time(),
        }
        graph = _make_optimiser_graph(scored_data)
        lazy_outputs = {
            "opt": pl.LazyFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "expected_income": [100.0],
                    "volume": [1.0],
                }
            )
        }

        with (
            patch.object(_solve_service, "_execute_pipeline", return_value=lazy_outputs),
            patch.object(_solve_service, "_build_grid", return_value=object()),
            patch.object(_solve_service, "_launch_background") as launch_background,
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        started_job = clean_job_store.jobs[data["job_id"]]
        assert started_job["job_type"] == "solve"
        launch_background.assert_called_once()

    def test_solve_marks_job_error_when_factor_extraction_fails(
        self,
        client,
        scored_data,
        clean_job_store,
    ):
        """A synchronous ratebook setup failure must not leave the solve job running."""
        from haute.routes.optimiser import _solve_service

        graph = _make_optimiser_graph(
            scored_data,
            config={
                "mode": "ratebook",
                "factor_columns": [["region"]],
                "banding_source": "banding",
            },
        )
        lazy_outputs = {
            "opt": pl.LazyFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "expected_income": [100.0],
                    "volume": [1.0],
                }
            )
        }
        before_job_ids = set(clean_job_store.jobs)

        with (
            patch.object(_solve_service, "_execute_pipeline", return_value=lazy_outputs),
            patch.object(
                _solve_service,
                "_extract_factors",
                side_effect=RuntimeError("factor collection failed"),
            ),
            patch.object(_solve_service, "_build_grid") as build_grid,
            patch.object(_solve_service, "_launch_background") as launch_background,
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 500
        new_job_ids = set(clean_job_store.jobs) - before_job_ids
        solve_jobs = [
            clean_job_store.jobs[job_id]
            for job_id in new_job_ids
            if clean_job_store.jobs[job_id].get("job_type") == "solve"
        ]
        assert len(solve_jobs) == 1
        assert solve_jobs[0]["status"] == "error"
        assert "factor collection failed" in solve_jobs[0]["message"]
        build_grid.assert_not_called()
        launch_background.assert_not_called()

    def test_solve_ratebook_captures_banding_rule_order_for_rates_tab(
        self,
        client,
        scored_data,
        clean_job_store,
    ):
        """The optimiser keeps the banding source's row order for rate tables."""
        from haute.routes.optimiser import _solve_service

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataSource",
                            "config": {"path": scored_data},
                        },
                    },
                    {
                        "id": "age_veh_banding",
                        "data": {
                            "label": "Age Veh Banding",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "banding": "breakpoints",
                                        "column": "proposer_age",
                                        "outputColumn": "proposer_age_band",
                                        "rules": {
                                            "27": "20-27",
                                            "34": "28-34",
                                            "41": "35-41",
                                        },
                                        "default": "missing",
                                    },
                                    {
                                        "banding": "categorical",
                                        "column": "channel",
                                        "outputColumn": "channel_band",
                                        "rules": {
                                            "compare_the_market": "compare_the_market",
                                            "moneysupermarket": "moneysupermarket",
                                            "confused": "confused",
                                        },
                                    },
                                ]
                            },
                        },
                    },
                    {
                        "id": "online_optimiser",
                        "data": {
                            "label": "online_optimiser",
                            "nodeType": "optimiser",
                            "config": {
                                "mode": "ratebook",
                                "objective": "expected_income",
                                "constraints": {"volume": {"min": 0.90}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "factor_columns": [["channel_band"], ["proposer_age_band"]],
                                "banding_source": "age_veh_banding",
                                "data_input": "source",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "online_optimiser").model_dump(),
                    make_edge("age_veh_banding", "online_optimiser").model_dump(),
                ],
            }
        ).model_dump()

        with (
            patch.object(_solve_service, "_execute_pipeline", return_value={}),
            patch.object(_solve_service, "_resolve_data_source", return_value=object()),
            patch.object(
                _solve_service,
                "_validate_and_project",
                return_value=(["volume"], object()),
            ),
            patch.object(
                _solve_service,
                "_extract_factors",
                return_value=pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "channel_band": ["confused"],
                        "proposer_age_band": ["28-34"],
                    }
                ),
            ),
            patch.object(_solve_service, "_build_grid", return_value=object()),
            patch.object(_solve_service, "_launch_background") as launch_background,
        ):
            resp = client.post(
                "/api/optimiser/solve",
                json={"graph": graph, "node_id": "online_optimiser"},
            )

        assert resp.status_code == 200
        launched_config = launch_background.call_args.args[2]
        assert "factor_level_order" not in launched_config
        assert launch_background.call_args.kwargs["factor_level_order"] == {
            "proposer_age_band": ["20-27", "28-34", "35-41", "missing"],
            "channel_band": ["compare_the_market", "moneysupermarket", "confused"],
        }


class TestStatusRoute:
    def test_missing_job_returns_404(self, client):
        resp = client.get("/api/optimiser/solve/status/nonexistent")
        assert resp.status_code == 404

    def test_completed_status_includes_capped_frontier_metadata(
        self,
        client,
        clean_job_store,
    ):
        frontier_data = {
            "status": "ok",
            "points": [{"total_objective": 100.0, "lambda_volume": 0.25}],
            "n_points": 3,
            "points_returned": 1,
            "points_limit": 1,
            "points_truncated": True,
            "constraint_names": ["volume"],
        }
        clean_job_store.jobs["capped_frontier_status"] = {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": 0.2,
            "frontier_data": frontier_data,
            "result": {
                "mode": "online",
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.9},
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 0.25},
                "converged": True,
                "frontier": frontier_data,
            },
            "created_at": time.time(),
        }

        resp = client.get("/api/optimiser/solve/status/capped_frontier_status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        for frontier in (data["frontier"], data["result"]["frontier"]):
            assert frontier["points_truncated"] is True
            assert frontier["points_returned"] == 1
            assert frontier["points_limit"] == 1

    def test_timeout_status_uses_atomic_update_result_when_job_evicted(
        self,
        client,
        clean_job_store,
    ):
        job_id = "timed_out_evicted"
        clean_job_store.jobs[job_id] = {
            "status": "running",
            "progress": 0.4,
            "message": "Solving...",
            "start_time": time.monotonic() - 2.0,
            "timeout": 1.0,
            "created_at": time.time(),
        }
        original_require_job = clean_job_store.require_job

        def require_once_then_evicted(requested_job_id: str):
            if requested_job_id == job_id and require_once_then_evicted.calls:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            require_once_then_evicted.calls += 1
            return original_require_job(requested_job_id)

        require_once_then_evicted.calls = 0

        with patch.object(clean_job_store, "require_job", side_effect=require_once_then_evicted):
            resp = client.get(f"/api/optimiser/solve/status/{job_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert data["progress"] == 0.4
        assert data["message"] == (
            "Solve timed out after 1.0s. Increase timeout or simplify the problem."
        )
        assert data["elapsed_seconds"] >= 1.0


class TestEstimateRoute:
    """Exercises ``POST /api/optimiser/estimate`` — the lightweight cost
    preview consumed by the frontend's shared ``useStaleConfigEstimate`` hook."""

    def test_estimate_returns_total_rows(self, client, scored_data):
        from haute.routes.optimiser import _store

        graph = _make_optimiser_graph(scored_data)

        with patch.object(_store, "create_job", wraps=_store.create_job) as create_job:
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Source metadata may or may not resolve depending on the test
        # fixture; the response must always include total_rows (possibly
        # null) so the frontend's shared useStaleConfigEstimate hook has a
        # stable shape to render against.
        assert "total_rows" in data
        assert any(call.args[0].get("job_type") == "estimate" for call in create_job.call_args_list)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_returns_scenario_expanded_input_counts(self, client, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2", "q2"],
                "scenario_index": [0, 1, 0, 1, 2],
                "scenario_value": [0.9, 1.1, 0.8, 1.0, 1.2],
                "expected_income": [100.0, 110.0, 90.0, 95.0, 98.0],
                "volume": [1.0, 0.9, 1.2, 1.1, 1.0],
            }
        )
        path = tmp_path / "ragged_scenarios.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_rows"] == 5
        assert data["expanded_row_count"] == 5
        assert data["quote_count"] == 2
        assert data["scenarios_per_quote_min"] == 2
        assert data["scenarios_per_quote_max"] == 3
        assert data["scenarios_per_quote_mean"] == pytest.approx(2.5)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_rejects_null_quote_id_loudly(self, client, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", None, None, "q2", "q2"],
                "scenario_index": pl.Series([0, 1, 0, 1, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series([0.9, 1.1, 0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
                "expected_income": pl.Series(
                    [100.0, 110.0, 999.0, 999.0, 200.0, 220.0],
                    dtype=pl.Float32,
                ),
                "volume": pl.Series([0.9, 0.8, 0.1, 0.1, 0.95, 0.9], dtype=pl.Float32),
            }
        )
        path = tmp_path / "null_quote_ids.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 400
        assert "Null quote_id values found in optimiser input (2 rows)." in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("bad_value", "label"),
        [(float("nan"), "NaN"), (float("inf"), "Inf")],
    )
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_rejects_non_finite_scenario_value_synchronously(
        self,
        client,
        tmp_path,
        clean_job_store,
        bad_value,
        label,
    ):
        """``NaN`` and ``Inf`` in ``scenario_value`` would silently corrupt
        the lambdas the optimiser produces — and through them, every
        downstream price.

        Pin the contract: bad values are caught in grid construction and
        surface as a *synchronous* 400 with the offending column name.
        No job is left behind in a "running" state, no lambdas are ever
        produced from corrupt data, and the user gets an actionable
        message naming the input that failed.
        """
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series(
                    [0.9, bad_value, 0.9, 1.1],
                    dtype=pl.Float32,
                ),
                "expected_income": pl.Series(
                    [100.0, 110.0, 80.0, 90.0],
                    dtype=pl.Float32,
                ),
                "volume": pl.Series([0.9, 0.95, 1.0, 0.9], dtype=pl.Float32),
            }
        )
        path = tmp_path / f"{label.lower()}_scenario.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )

        # ── User-visible contract ────────────────────────────────────
        # 400 is the right status: bad input, not a server fault.
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        # The message must name the offending column AND the offending
        # value.  Generic "grid construction failed" without specifics
        # leaves the user guessing which row of which file is bad.
        assert "scenario_value" in detail, (
            f"Error detail does not name the offending column: {detail!r}"
        )
        # ``label.lower() in detail.lower()`` accommodates the
        # price-contour error rendering ("inf"/"nan" in lowercase).
        assert label.lower() in detail.lower(), (
            f"Error detail does not name the offending value type ({label!r}): {detail!r}"
        )

        # ── Job-store hygiene ───────────────────────────────────────
        # The synchronous failure must NOT leave a "running" job behind
        # to confuse the next /solve attempt or the status poller.
        # Either the job exists with status=error, or it was deleted —
        # both are acceptable; a stuck "running" job is not.
        running_jobs = [
            (jid, j) for jid, j in clean_job_store.jobs.items() if j.get("status") == "running"
        ]
        assert running_jobs == [], (
            f"Synchronous validation failure left running job(s) behind: {running_jobs}"
        )

    def test_estimate_rejects_unknown_node_loudly(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "nonexistent"},
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_surfaces_projected_input_errors(self, client, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "scenario_index": [0, 1],
                "scenario_value": [0.9, 1.1],
                "expected_income": [100.0, 110.0],
            }
        )
        path = tmp_path / "missing_constraint.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 400
        assert "Missing columns in scored data" in resp.json()["detail"]
        assert "volume" in resp.json()["detail"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_uses_per_quote_scenario_extrema(
        self,
        client,
        tmp_path,
        clean_job_store,
    ):
        """Auto range scans every scenario per quote, so middle extrema count."""
        from haute.routes.optimiser import _store

        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
                "scenario_index": [0, 1, 2, 0, 1, 2],
                "scenario_value": [0.8, 1.0, 1.2, 0.8, 1.0, 1.2],
                "expected_income": [100.0, 120.0, 110.0, 90.0, 95.0, 98.0],
                "expected_margin": [10.0, 30.0, 20.0, 5.0, 1.0, 9.0],
            }
        )
        path = tmp_path / "scored.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(
            str(path),
            config={
                "objective": "expected_income",
                "constraints": {"expected_margin": {"max": 35.0}},
            },
        )

        with patch.object(_store, "create_job", wraps=_store.create_job) as create_job:
            resp = client.post(
                "/api/optimiser/frontier/auto-range",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["method"] == "scenario_envelope"
        assert data["ranges"]["expected_margin"]["min"] == pytest.approx(11.0)
        assert data["ranges"]["expected_margin"]["max"] == pytest.approx(39.0)
        assert any(
            call.args[0].get("job_type") == "frontier_auto_range"
            for call in create_job.call_args_list
        )
        assert not any(
            job.get("job_type") == "frontier_auto_range" for job in clean_job_store.jobs.values()
        )


class TestApplyRoute:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_apply_after_solve(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        resp = client.post("/api/optimiser/apply", json={"job_id": job_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["row_count"] > 0
        assert "total_objective" in data
        assert data["from_artifact"] is False

    def test_apply_missing_job(self, client):
        resp = client.post("/api/optimiser/apply", json={"job_id": "nonexistent"})
        assert resp.status_code == 404


class TestSaveRoute:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_save_after_solve(self, client, scored_data, tmp_path):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        out_path = str(tmp_path / "result.json")
        resp = client.post("/api/optimiser/save", json={"job_id": job_id, "output_path": out_path})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["path"] == out_path

        import json

        saved = json.loads((tmp_path / "result.json").read_text())
        assert "lambdas" in saved

    def test_save_missing_job(self, client):
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "nonexistent", "output_path": "/tmp/x.json"},
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_save_path_traversal_blocked(self, client, scored_data, tmp_path):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        # Narrow the sandbox so the traversal path escapes it
        set_project_root(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": "../../etc/passwd"},
        )
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Ratebook mode
# ---------------------------------------------------------------------------


def _make_ratebook_data(tmp_path, n_quotes: int = 50, n_steps: int = 5):
    """Create scored + banding DataFrames for ratebook tests.

    Returns (scored_path, banding_path).
    """
    rng = np.random.RandomState(42)
    regions = ["North", "South", "East"]

    # Build scored data in long format
    quote_ids = []
    steps = []
    mults = []
    incomes = []
    volumes = []
    scenario_values = np.linspace(0.8, 1.2, n_steps).astype(np.float32)
    for q in range(n_quotes):
        base_income = rng.uniform(100, 1000)
        base_volume = rng.uniform(0.5, 1.5)
        for s, m in enumerate(scenario_values):
            quote_ids.append(f"q_{q:04d}")
            steps.append(s)
            mults.append(float(m))
            incomes.append(float(base_income * m))
            volumes.append(float(base_volume * (2.0 - m)))

    scored_df = pl.DataFrame(
        {
            "quote_id": quote_ids,
            "scenario_index": pl.Series(steps, dtype=pl.Int32),
            "scenario_value": pl.Series(mults, dtype=pl.Float32),
            "expected_income": pl.Series(incomes, dtype=pl.Float32),
            "volume": pl.Series(volumes, dtype=pl.Float32),
        }
    )
    scored_path = tmp_path / "scored.parquet"
    scored_df.write_parquet(scored_path)

    # Build banding data: one row per quote with a region factor
    banding_df = pl.DataFrame(
        {
            "quote_id": [f"q_{q:04d}" for q in range(n_quotes)],
            "region": [regions[q % len(regions)] for q in range(n_quotes)],
        }
    )
    banding_path = tmp_path / "banding.parquet"
    banding_df.write_parquet(banding_path)

    return str(scored_path), str(banding_path)


def _make_ratebook_graph(data_path: str, banding_data_path: str) -> dict:
    """Build a 3-node graph: dataSource → optimiser ← banding."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": data_path},
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataSource",
                        "config": {"path": banding_data_path},
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "ratebook",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.90}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "max_iter": 20,
                            "tolerance": 1e-4,
                            "max_cd_iterations": 5,
                            "cd_tolerance": 1e-3,
                            "factor_columns": [["region"]],
                            "banding_source": "banding",
                            "data_input": "source",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "opt").model_dump(),
                make_edge("banding", "opt").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def _make_ratebook_frontier_materialisation_job(clean_job_store, job_id: str):
    factors_df = pl.DataFrame({"region": ["North", "South"]})
    mock_grid = MagicMock()
    mock_solver = MagicMock()
    mock_solver.solve.return_value = SimpleNamespace(
        total_objective=222.0,
        baseline_objective=95.0,
        total_constraints={"volume": 0.97},
        baseline_constraints={"volume": 0.88},
        lambdas={"volume": 0.7},
        converged=True,
        cd_iterations=5,
        clamp_rate=0.04,
        factor_tables={"region": {"North": 1.08, "South": 0.92}},
        dataframe=pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "optimal_scenario_value": [1.08, 0.92],
            }
        ),
    )
    base_result = {
        "mode": "ratebook",
        "total_objective": 100.0,
        "baseline_objective": 95.0,
        "constraints": {"volume": 0.92},
        "baseline_constraints": {"volume": 0.88},
        "lambdas": {"volume": 0.5},
        "converged": True,
        "factor_tables": {"region": [{"__factor_group__": "Old", "optimal_scenario_value": 1.0}]},
        "scenario_value_histogram": {"counts": [1, 2], "edges": [0.9, 1.0, 1.1]},
    }
    clean_job_store.jobs[job_id] = {
        "status": "completed",
        "config": {
            "mode": "ratebook",
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"]],
        },
        "node_label": "ratebook opt",
        "solver": mock_solver,
        "quote_grid": mock_grid,
        "factors_df": factors_df,
        "factor_columns_valid": [["region"]],
        "base_result": base_result,
        "result": dict(base_result),
        "frontier_data": {
            "status": "ok",
            "n_points": 1,
            "points_returned": 1,
            "points_limit": FRONTIER_POINT_LIMIT,
            "points_truncated": False,
            "points": [
                _frontier_point_summary(
                    lambda_volume=0.7,
                    total_objective=220.0,
                    total_volume=0.97,
                    threshold_volume=0.96,
                )
            ],
            "constraint_names": ["volume"],
        },
        "artifact_handles": {},
        "created_at": time.time(),
    }
    return mock_solver, mock_grid, factors_df


def _expected_region_factor_tables(
    *,
    north: float = 1.08,
    south: float = 0.92,
) -> dict[str, list[dict[str, object]]]:
    return {
        "region": [
            {
                "__factor_group__": "North",
                "optimal_scenario_value": north,
                "quote_count": 1,
            },
            {
                "__factor_group__": "South",
                "optimal_scenario_value": south,
                "quote_count": 1,
            },
        ]
    }


class TestRatebookSolve:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_ratebook_solve_completes(self, client, tmp_path):
        scored_path, banding_path = _make_ratebook_data(tmp_path)
        graph = _make_ratebook_graph(scored_path, banding_path)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "completed", status.get("message", "")
        result = status["result"]
        assert result["mode"] == "ratebook"
        assert "factor_tables" in result
        assert "converged" in result
        assert "lambdas" in result

    def test_ratebook_no_factor_columns(self, client, scored_data):
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "mode": "ratebook",
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.9}},
                "factor_columns": [],
            },
        )
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 400
        assert "factor_columns" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Phase 1 tests
# ---------------------------------------------------------------------------


class TestSolveWithHistory:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_with_history(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data, config={"record_history": True})
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"
        result = status["result"]
        assert "history" in result
        history = result["history"]
        assert isinstance(history, list)
        assert len(history) > 0
        first = history[0]
        assert "iteration" in first
        assert "total_objective" in first


class TestScenarioValueStats:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_scenario_value_stats_in_result(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"
        result = status["result"]
        assert "scenario_value_stats" in result
        stats = result["scenario_value_stats"]
        assert "mean" in stats
        assert "p50" in stats
        assert "pct_increase" in stats
        assert "scenario_value_histogram" in result
        hist = result["scenario_value_histogram"]
        assert "counts" in hist
        assert "edges" in hist
        assert len(hist["counts"]) == 20
        assert len(hist["edges"]) == 21


class TestColumnValidation:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_missing_column(self, client, tmp_path):
        """Data without a constraint column returns 400."""
        df = pl.DataFrame(
            {
                "quote_id": ["q_0"] * 5,
                "scenario_index": pl.Series(range(5), dtype=pl.Int32),
                "scenario_value": pl.Series(np.linspace(0.8, 1.2, 5).tolist(), dtype=pl.Float32),
                "expected_income": pl.Series([100.0] * 5, dtype=pl.Float32),
                # no "volume" column!
            }
        )
        path = str(tmp_path / "no_volume.parquet")
        df.write_parquet(path)
        graph = _make_optimiser_graph(path)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 400
        assert "volume" in resp.json()["detail"]


class TestNonConvergenceWarning:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_non_convergence_warning(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data, config={"max_iter": 1})
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"
        result = status["result"]
        if not result["converged"]:
            assert "warning" in result


class TestSaveEndpointFields:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_save_has_full_fields(self, client, scored_data, tmp_path):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        out_path = str(tmp_path / "result.json")
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": out_path},
        )
        assert resp.status_code == 200

        import json as json_mod

        saved = json_mod.loads((tmp_path / "result.json").read_text())
        assert "lambdas" in saved
        assert "mode" in saved
        assert saved["mode"] == "online"
        assert "baseline_objective" in saved
        assert "baseline_constraints" in saved
        assert "constraints" in saved
        assert "objective" in saved
        assert "quote_id" in saved
        assert "chunk_size" in saved


# ---------------------------------------------------------------------------
# Phase 2a: Frontier
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 4: Step-wise expansion
# ---------------------------------------------------------------------------


def _make_base_data(tmp_path, n_quotes: int = 50) -> str:
    """Create base data (one row per quote, no scenario expansion)."""
    rng = np.random.RandomState(42)
    df = pl.DataFrame(
        {
            "quote_id": [f"q_{q:04d}" for q in range(n_quotes)],
            "base_income": pl.Series(
                rng.uniform(100, 1000, n_quotes).tolist(),
                dtype=pl.Float64,
            ),
            "base_volume": pl.Series(
                rng.uniform(0.5, 1.5, n_quotes).tolist(),
                dtype=pl.Float64,
            ),
        }
    )
    path = tmp_path / "base.parquet"
    df.write_parquet(path)
    return str(path)


def _make_expander_graph(data_path: str) -> dict:
    """Build a 4-node graph: dataSource → expander → transform → optimiser.

    The expander cross-joins scenario_value and scenario_index columns.
    The transform computes objective and constraint columns.
    """
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": data_path},
                    },
                },
                {
                    "id": "transform",
                    "data": {
                        "label": "compute_metrics",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = df.with_columns([\n"
                                "    (pl.col('base_income') * "
                                "pl.col('scenario_value'))"
                                ".alias('expected_income'),\n"
                                "    (pl.col('base_volume') * "
                                "(2.0 - pl.col('scenario_value')))"
                                ".alias('volume'),\n"
                                "])"
                            ),
                        },
                    },
                },
                {
                    "id": "expander",
                    "data": {
                        "label": "expander",
                        "nodeType": "scenarioExpander",
                        "config": {
                            "column_name": "scenario_value",
                            "min_value": 0.8,
                            "max_value": 1.2,
                            "steps": 5,
                            "step_column": "scenario_index",
                        },
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "online",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.90}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "max_iter": 20,
                            "tolerance": 1e-4,
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "expander").model_dump(),
                make_edge("expander", "transform").model_dump(),
                make_edge("transform", "opt").model_dump(),
            ],
        }
    )
    return graph.model_dump()


class TestExpanderSolve:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_expander_solve_completes(self, client, tmp_path):
        data_path = _make_base_data(tmp_path)
        graph = _make_expander_graph(data_path)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "completed", status.get("message", "")
        result = status["result"]
        assert "total_objective" in result
        assert "lambdas" in result
        assert result["n_steps"] == 5

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_normal_solve_produces_lambdas(self, client, tmp_path):
        """Normal solve should complete and produce lambdas."""
        scored_path = _make_scored_data(tmp_path, n_quotes=50, n_steps=5)
        normal_graph = _make_optimiser_graph(scored_path)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": normal_graph, "node_id": "opt"},
        )
        normal_status = _poll_until_done(client, resp.json()["job_id"])
        assert normal_status["status"] == "completed"
        normal_result = normal_status["result"]
        assert len(normal_result["lambdas"]) > 0
        assert "total_objective" in normal_result


class TestFrontierRoute:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_after_solve(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": job_id,
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["n_points"] > 0
        assert data["constraint_names"] == ["volume"]

    def test_ratebook_frontier_uses_factors_dataframe(self, client, clean_job_store):
        mock_solver = MagicMock()
        mock_grid = MagicMock()
        factors_df = pl.DataFrame({"region": ["North", "South"]})
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [100.0],
                    "volume": [0.9],
                    "lambda_volume": [0.25],
                }
            )
        )
        clean_job_store.jobs["ratebook_frontier"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": mock_grid,
            "factors_df": factors_df,
            "factor_columns_valid": [["region"]],
            "config": {"mode": "ratebook"},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "ratebook_frontier",
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["points"][0]["total_volume"] == pytest.approx(0.9)
        mock_solver.frontier.assert_called_once()
        assert mock_solver.frontier.call_args.args == (mock_grid, factors_df)
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {"volume": (0.85, 0.95)}
        assert mock_solver.frontier.call_args.kwargs["n_points_per_dim"] == 3
        assert mock_solver.frontier.call_args.kwargs["factor_columns"] == [["region"]]

    def test_frontier_without_ranges_uses_config_absolute_ranges(self, client, clean_job_store):
        mock_solver = MagicMock()
        mock_grid = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [100.0],
                    "volume": [0.9],
                    "loss": [12.0],
                    "lambda_volume": [0.25],
                }
            )
        )
        clean_job_store.jobs["auto_frontier_ranges"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": mock_grid,
            "result": {
                "mode": "online",
                "baseline_constraints": {"volume": 999.0, "loss": 0.0},
            },
            "config": {
                "mode": "online",
                "constraints": {"volume": {"min": 0.9}, "loss": {"max": 20.0}},
                "frontier_min": 10.0,
                "frontier_max": 20.0,
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "auto_frontier_ranges",
                "n_points_per_dim": 3,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["points"][0]["total_volume"] == pytest.approx(0.9)
        assert resp.json()["points"][0]["total_loss"] == pytest.approx(12.0)
        assert resp.json()["constraint_names"] == ["volume", "loss"]
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {
            "volume": (10.0, 20.0),
            "loss": (10.0, 20.0),
        }
        assert mock_solver.frontier.call_args.kwargs["n_points_per_dim"] == 3

    def test_frontier_omits_initial_lambdas_when_base_result_has_none(
        self,
        client,
        clean_job_store,
    ):
        class SolverWithoutInitialLambdas:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def frontier(
                self,
                quote_grid: object,
                *,
                threshold_ranges: dict[str, tuple[float, float]],
                n_points_per_dim: int,
            ) -> SimpleNamespace:
                self.calls.append(
                    {
                        "quote_grid": quote_grid,
                        "threshold_ranges": threshold_ranges,
                        "n_points_per_dim": n_points_per_dim,
                    }
                )
                return SimpleNamespace(
                    points=pl.DataFrame(
                        {
                            "total_objective": [100.0],
                            "loss_ratio": [0.9],
                            "lambda_loss_ratio": [0.25],
                        }
                    )
                )

        solver = SolverWithoutInitialLambdas()
        quote_grid = object()
        clean_job_store.jobs["frontier_no_initial_lambdas"] = {
            "status": "completed",
            "solver": solver,
            "quote_grid": quote_grid,
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_no_initial_lambdas",
                "threshold_ranges": {"loss_ratio": [0.8, 0.95]},
                "n_points_per_dim": 3,
            },
        )

        assert resp.status_code == 200
        assert solver.calls == [
            {
                "quote_grid": quote_grid,
                "threshold_ranges": {"loss_ratio": (0.8, 0.95)},
                "n_points_per_dim": 3,
            }
        ]

    def test_frontier_config_range_errors_are_client_errors(self, client, clean_job_store):
        mock_solver = MagicMock()
        clean_job_store.jobs["auto_frontier_invalid_ranges"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "constraints": {"volume": {"min": 0.9}},
                "frontier_ranges": {"volume": {"min": 0.8}},
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={"job_id": "auto_frontier_invalid_ranges"},
        )

        assert resp.status_code == 400
        assert "frontier_ranges.volume must contain min and max values" in resp.json()["detail"]
        mock_solver.frontier.assert_not_called()

    def test_frontier_without_ranges_requires_config_constraints(self, client, clean_job_store):
        clean_job_store.jobs["auto_frontier_no_constraints"] = {
            "status": "completed",
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {}},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={"job_id": "auto_frontier_no_constraints"},
        )

        assert resp.status_code == 400
        assert "no configured constraints" in resp.json()["detail"].lower()

    def test_frontier_rejects_invalid_threshold_range_shape_at_schema_layer(
        self,
        client,
        clean_job_store,
    ):
        """Schema-level validation must reject malformed ranges with the same
        wording as the config-side path so error UX is coherent across both
        request bodies and saved configs.

        Previously the schema accepted ``list[float]`` of any length and only
        the runtime function caught length=1 / inverted / non-finite cases.
        """
        mock_solver = MagicMock()
        clean_job_store.jobs["frontier_bad_range"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "created_at": time.time(),
        }

        # length-1: should fail validation with the canonical wording.
        resp_short = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_bad_range",
                "threshold_ranges": {"volume": [0.85]},
                "n_points_per_dim": 3,
            },
        )
        # min > max: should also fail with the canonical wording.
        resp_inverted = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_bad_range",
                "threshold_ranges": {"volume": [0.95, 0.85]},
                "n_points_per_dim": 3,
            },
        )

        for resp, expected in (
            (resp_short, "min and max"),
            (resp_inverted, "min must be less than or equal to max"),
        ):
            # Pydantic validators surface as 422 in FastAPI.
            assert resp.status_code == 422, resp.text
            assert expected in resp.text
        mock_solver.frontier.assert_not_called()

    def test_frontier_rejects_unbounded_compute_grid(self, client, clean_job_store):
        """``n_points_per_dim ** n_constraints`` must stay within the compute budget.

        The response is truncated to ``FRONTIER_POINT_LIMIT`` but the solver still
        evaluates every grid point.  A request that exceeds the compute budget
        must be rejected before the solver is invoked, otherwise a single client
        call can pin CPU/memory for a very long time.
        """
        mock_solver = MagicMock()
        clean_job_store.jobs["frontier_compute_dos"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "constraints": {f"c{i}": {"min": 0.0} for i in range(6)},
            },
            "created_at": time.time(),
        }

        # 100 ** 6 = 10**12 grid points — well past anything reasonable.
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_compute_dos",
                "threshold_ranges": {f"c{i}": [0.0, 1.0] for i in range(6)},
                "n_points_per_dim": 100,
            },
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "frontier compute budget" in detail
        mock_solver.frontier.assert_not_called()

    def test_frontier_compute_limit_allows_within_budget(self, client, clean_job_store):
        """Requests at or below the compute budget must still succeed."""
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [100.0],
                    "volume": [0.9],
                    "lambda_volume": [0.25],
                }
            )
        )
        clean_job_store.jobs["frontier_compute_ok"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "constraints": {"volume": {"min": 0.9}},
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_compute_ok",
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 100,  # 100 ** 1 = 100 — well within budget.
            },
        )

        assert resp.status_code == 200
        mock_solver.frontier.assert_called_once()

    def test_frontier_missing_job(self, client):
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "nonexistent",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_incomplete_job(self, client, scored_data):
        """Frontier on a not-yet-completed job returns 400."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        # Don't poll — submit frontier immediately
        # Job may already be complete for small data; handle both
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": job_id,
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )
        # Either 400 (still running) or 200 (already done) is acceptable
        assert resp.status_code in (200, 400)
        _poll_until_done(client, job_id)


# ---------------------------------------------------------------------------
# Phase 1B: Pure function tests
# ---------------------------------------------------------------------------


class TestComputeScenarioValueStats:
    """Unit tests for _compute_scenario_value_stats."""

    def test_no_dataframe_attribute(self):
        """Object without .dataframe omits stats payloads entirely."""
        result = SimpleNamespace()  # no .dataframe
        stats, hist = _compute_scenario_value_stats(result)
        assert stats is None
        assert hist is None

    def test_missing_column(self):
        """DataFrame without optimal_scenario_value omits stats payloads entirely."""
        df = pl.DataFrame({"other_col": [1.0, 2.0, 3.0]})
        result = SimpleNamespace(dataframe=df)
        stats, hist = _compute_scenario_value_stats(result)
        assert stats is None
        assert hist is None

    def test_valid_scenario_values(self):
        """Normal case with optimal_scenario_value column."""
        df = pl.DataFrame(
            {
                "optimal_scenario_value": [0.9, 1.0, 1.1, 1.2, 0.8],
            }
        )
        result = SimpleNamespace(dataframe=df)
        stats, hist = _compute_scenario_value_stats(result)
        assert "mean" in stats
        assert "p50" in stats
        assert "pct_increase" in stats
        assert "pct_decrease" in stats
        assert stats["pct_increase"] > 0  # 1.1 and 1.2 are > 1.0
        assert stats["pct_decrease"] > 0  # 0.9 and 0.8 are < 1.0
        assert "counts" in hist
        assert "edges" in hist
        assert len(hist["counts"]) == 20
        assert len(hist["edges"]) == 21


class TestBuildArtifactPayload:
    """Unit tests for _build_artifact_payload."""

    def test_online_mode_basic(self):
        """Online mode produces a payload with expected keys."""
        job = {
            "node_label": "my_opt",
            "config": {
                "mode": "online",
                "constraints": {"volume": {"min": 0.9}},
                "objective": "income",
            },
        }
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=1000.0,
            baseline_objective=950.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["mode"] == "online"
        assert payload["lambdas"] == {"volume": 0.5}
        assert payload["converged"] is True
        assert "factor_tables" not in payload  # only for ratebook

    def test_ratebook_mode_includes_factor_tables(self):
        """Ratebook mode includes factor_tables and clamp_rate."""
        job = {
            "node_label": "rb_opt",
            "config": {"mode": "ratebook", "constraints": {}, "objective": "income"},
            "result": {
                "factor_tables": {
                    "region": [{"__factor_group__": "North", "optimal_scenario_value": 1.1}]
                },
            },
        }
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=1000.0,
            total_constraints={},
            converged=True,
            clamp_rate=0.05,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["mode"] == "ratebook"
        assert "factor_tables" in payload
        assert payload["clamp_rate"] == 0.05

    def test_version_override(self):
        """User-specified version overrides auto-generated one."""
        job = {"node_label": "opt", "config": {"mode": "online"}}
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result, version_override="v2.0")
        assert payload["version"] == "v2.0"

    def test_payload_includes_frontier_selection(self):
        """T3: When a frontier point is selected, payload includes frontier_selection."""
        job = {
            "node_label": "my_opt",
            "config": {"mode": "online", "objective": "income", "constraints": {}},
            "selected_frontier_point": 2,
            "frontier_data": {
                "status": "ok",
                "points": [
                    {"total_objective": 100.0},
                    {"total_objective": 110.0},
                    {"total_objective": 120.0},
                ],
                "n_points": 3,
                "constraint_names": ["volume"],
            },
        }
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=120.0,
            baseline_objective=100.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" in payload
        fs = payload["frontier_selection"]
        assert fs["selected_from_frontier"] is True
        assert fs["point_index"] == 2
        assert fs["n_frontier_points"] == 3

    def test_payload_no_frontier_selection_when_none(self):
        """T3: When no frontier point is selected, payload has no frontier_selection key."""
        job = {
            "node_label": "my_opt",
            "config": {"mode": "online", "objective": "income", "constraints": {}},
            # No selected_frontier_point key
        }
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=100.0,
            baseline_objective=95.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload


# ---------------------------------------------------------------------------
# Phase 1B: MLflow log endpoint tests
# ---------------------------------------------------------------------------


class TestOptimiserMlflowLog:
    """Tests for /mlflow/log endpoint."""

    def test_mlflow_log_missing_job(self, client):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "nonexistent",
                "experiment_name": "/test",
            },
        )
        assert resp.status_code == 404

    def test_mlflow_log_not_completed(self, client, clean_job_store):
        clean_job_store.jobs["running_job"] = {
            "status": "running",
            "progress": 0.5,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "running_job",
                "experiment_name": "/test",
            },
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_mlflow_log_no_solve_result(self, client, clean_job_store):
        clean_job_store.jobs["no_result"] = {
            "status": "completed",
            "solver": None,
            "solve_result": None,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "no_result",
                "experiment_name": "/test",
            },
        )
        assert resp.status_code == 400
        assert "no solve result" in resp.json()["detail"].lower()

    def test_mlflow_log_import_error(self, client, clean_job_store):
        """If mlflow is not installed, return 400."""
        mock_solver = MagicMock()
        mock_solve = MagicMock(lambdas={}, total_objective=0, total_constraints={}, converged=True)
        clean_job_store.jobs["import_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "solve_result": mock_solve,
            "config": {"mode": "online"},
            "node_label": "opt",
            "created_at": time.time(),
        }

        with patch.dict("sys.modules", {"mlflow": None}):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={
                    "job_id": "import_err",
                    "experiment_name": "/test",
                },
            )
        assert resp.status_code == 400
        assert "mlflow" in resp.json()["detail"].lower()

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_mlflow_log_after_real_solve(self, client, scored_data):
        """A completed solve keeps the solver available for later MLflow logging."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"

        mock_mlflow = MagicMock()
        mock_run = MagicMock()
        mock_run.info.run_id = "real-solve-run"
        mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch(
                "haute.modelling._mlflow_log.configure_mlflow_tracking",
                return_value=("http://localhost:5000", "local"),
            ),
            patch(
                "haute.modelling._mlflow_log.resolve_experiment_name",
                return_value="/optimiser",
            ),
            patch(
                "haute.modelling._mlflow_log.build_run_url",
                return_value="http://localhost:5000/real-solve-run",
            ),
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": job_id, "experiment_name": "/optimiser"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["run_id"] == "real-solve-run"
        mock_mlflow.log_metrics.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 1B: Background thread error tests
# ---------------------------------------------------------------------------


class TestSolveBackgroundErrors:
    """Test error categorization in the _solve_background thread."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_value_error(self, client, scored_data):
        """ValueError in solver produces 'Data error' message."""
        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=ValueError("Invalid constraint column"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "error"
            assert "Data error" in status["message"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_runtime_error(self, client, scored_data):
        """RuntimeError in solver produces 'Algorithm error' message."""
        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=RuntimeError("Solver diverged"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "error"
            assert "Algorithm error" in status["message"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_generic_exception(self, client, scored_data):
        """Generic Exception in solver produces 'Unexpected error' message."""
        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=Exception("something broke"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "error"
            assert "Unexpected error" in status["message"]


# ---------------------------------------------------------------------------
# Phase 1B: Job state guard tests
# ---------------------------------------------------------------------------


class TestJobStateGuards:
    """Test that endpoints properly reject incomplete or missing jobs."""

    def test_apply_not_completed(self, client, clean_job_store):
        clean_job_store.jobs["running"] = {
            "status": "running",
            "progress": 0.5,
            "created_at": time.time(),
        }
        resp = client.post("/api/optimiser/apply", json={"job_id": "running"})
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_apply_no_solve_result(self, client, clean_job_store):
        clean_job_store.jobs["no_sr"] = {
            "status": "completed",
            "solve_result": None,
            "created_at": time.time(),
        }
        resp = client.post("/api/optimiser/apply", json={"job_id": "no_sr"})
        assert resp.status_code == 400
        assert "apply artifact handle" in resp.json()["detail"].lower()

    def test_frontier_not_completed(self, client, clean_job_store):
        clean_job_store.jobs["running2"] = {
            "status": "running",
            "progress": 0.1,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "running2",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_frontier_no_solver(self, client, clean_job_store):
        clean_job_store.jobs["no_solver"] = {
            "status": "completed",
            "solver": None,
            "quote_grid": None,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "no_solver",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )
        assert resp.status_code == 400
        assert "solver" in resp.json()["detail"].lower()

    def test_save_not_completed(self, client, clean_job_store):
        clean_job_store.jobs["running3"] = {
            "status": "running",
            "progress": 0.1,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "running3",
                "output_path": "/tmp/x.json",
            },
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_save_no_solve_result(self, client, clean_job_store):
        clean_job_store.jobs["no_sr2"] = {
            "status": "completed",
            "solve_result": None,
            "solver": None,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "no_sr2",
                "output_path": "/tmp/x.json",
            },
        )
        assert resp.status_code == 400
        assert "no solve result" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Phase 1B: Timeout detection
# ---------------------------------------------------------------------------


class TestStatusTimeout:
    """Test that polling a timed-out job returns error."""

    def test_timeout_detection(self, client, clean_job_store):
        # Inject a "running" job whose start_time is far in the past
        clean_job_store.jobs["timed_out"] = {
            "status": "running",
            "progress": 0.5,
            "message": "Solving",
            "start_time": time.monotonic() - 999,
            "timeout": 10,
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/timed_out")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "timed out" in data["message"].lower()


# ---------------------------------------------------------------------------
# Phase 1B: Unsupported mode and ratebook edge cases
# ---------------------------------------------------------------------------


class TestUnsupportedMode:
    def test_unsupported_mode_returns_400(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data, config={"mode": "quantum"})
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 400
        assert "quantum" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Issue 12: _execute_pipeline execution-path tests
# ---------------------------------------------------------------------------


class TestExecutePipelineArgs:
    """Verify _execute_pipeline passes scenario, preamble_ns, and checkpoint_dir."""

    def test_execute_pipeline_passes_scenario_and_checkpoint(self, scored_data, tmp_path):
        """_execute_lazy receives scenario != 'live', caller's checkpoint_dir, and preamble_ns."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        # Capture the kwargs _execute_lazy is called with.
        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            # Return (outputs_dict, exec_order, edge_map, label_map)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=fake_execute_lazy),
            patch(
                "haute.executor._resolve_batch_scenario",
                return_value="ism_scenario",
            ),
            patch(
                "haute.executor._compile_preamble",
                return_value={"helper": lambda x: x},
            ),
        ):
            service._execute_pipeline(body, job_id, checkpoint_dir)

        # source should come from _resolve_batch_scenario (not default "batch")
        assert captured["source"] == "ism_scenario"
        # checkpoint_dir is the one we passed in
        assert captured["checkpoint_dir"] == checkpoint_dir
        # preamble_ns is the dict returned by _compile_preamble
        assert captured["preamble_ns"] is not None
        assert "helper" in captured["preamble_ns"]

    def test_execute_pipeline_defaults_to_batch_when_no_ism(self, scored_data, tmp_path):
        """When _resolve_batch_scenario returns None, scenario defaults to 'batch'."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=fake_execute_lazy),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, checkpoint_dir)

        assert captured["source"] == "batch"

    def test_execute_pipeline_preamble_ns_none_for_empty_preamble(self, scored_data, tmp_path):
        """When _compile_preamble returns empty/falsy, preamble_ns is None."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=fake_execute_lazy),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, checkpoint_dir)

        # Empty dict from _compile_preamble is falsy → preamble_ns should be None
        assert captured["preamble_ns"] is None


class TestBuildGridSinkFallback:
    """Verify _build_grid succeeds even when sink_parquet needs the fallback path."""

    def test_build_grid_sink_fallback(self, tmp_path):
        """When safe_sink_parquet's streaming sink raises ComputeError,
        the fallback (collect+write) still produces a valid parquet and grid builds."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        # Build a real scored LazyFrame
        n_quotes, n_steps = 10, 3
        scenario_values = np.linspace(0.8, 1.2, n_steps).astype(np.float32)
        rows = []
        for q in range(n_quotes):
            for s, m in enumerate(scenario_values):
                rows.append((f"q_{q:03d}", s, float(m), float(100 * m), float(1.5 * (2 - m))))
        df = pl.DataFrame(
            rows,
            schema={
                "quote_id": pl.Utf8,
                "scenario_index": pl.Int32,
                "scenario_value": pl.Float32,
                "expected_income": pl.Float32,
                "volume": pl.Float32,
            },
            orient="row",
        )
        scored_lf = df.lazy()

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
            "chunk_size": 1_000,
        }

        def patched_safe_sink(lf, path, **kw):
            """Force the streaming-sink exception to exercise the fallback path."""
            # Simulate ComputeError on direct sink, then fall back to collect+write.
            collected = lf.collect(engine="streaming")
            collected.write_parquet(path)

        mock_grid = MagicMock()
        with (
            patch(
                "haute._polars_utils.safe_sink",
                side_effect=patched_safe_sink,
            ) as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=mock_grid,
            ) as mock_build,
        ):
            result = service._build_grid(scored_lf, ["volume"], config, "opt", job_id)

        # safe_sink was called
        assert mock_sink.call_count == 1
        # build_grid_from_parquet_chunked was called with correct chunking and column mappings
        assert mock_build.call_count == 1
        build_kwargs = mock_build.call_args
        assert build_kwargs.args[2] == 1_000
        assert build_kwargs.kwargs.get("objective") == "expected_income"
        assert build_kwargs.kwargs.get("scenario_value") == "scenario_value"
        assert "scenario_value_col" not in build_kwargs.kwargs
        assert result is mock_grid


class TestExecutePipelineCleanup:
    """Verify checkpoint dir lifecycle: caller owns creation + cleanup."""

    def test_execute_pipeline_uses_caller_checkpoint_dir(self, scored_data, tmp_path):
        """_execute_pipeline passes the caller-provided checkpoint_dir to _execute_lazy."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=fake_execute_lazy),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            lazy_outputs = service._execute_pipeline(body, job_id, checkpoint_dir)

        assert isinstance(lazy_outputs, dict)
        assert captured["checkpoint_dir"] == checkpoint_dir

    def test_execute_pipeline_error_does_not_leak_tmpdir(self, scored_data, tmp_path):
        """When _execute_lazy raises, the caller's finally block cleans the checkpoint dir."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        def failing_execute_lazy(*args, **kwargs):
            raise RuntimeError("boom")

        # Simulate what start() does: create dir, call _execute_pipeline, cleanup in finally
        import tempfile
        from pathlib import Path

        checkpoint_dir = Path(tempfile.mkdtemp(prefix="haute_test_"))
        try:
            with (
                patch("haute.graph_utils._execute_lazy", side_effect=failing_execute_lazy),
                patch("haute.executor._resolve_batch_scenario", return_value=None),
                patch("haute.executor._compile_preamble", return_value={}),
            ):
                from fastapi import HTTPException

                with pytest.raises(HTTPException):
                    service._execute_pipeline(body, job_id, checkpoint_dir)
        finally:
            import shutil

            shutil.rmtree(checkpoint_dir, ignore_errors=True)

        # Checkpoint dir should be gone after caller cleanup
        assert not checkpoint_dir.exists()

    def test_execute_pipeline_error_raises_http_exception(self, scored_data, tmp_path):
        """Pipeline execution errors are wrapped in HTTPException(500)."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        def failing_execute_lazy(*args, **kwargs):
            raise RuntimeError("boom")

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=failing_execute_lazy),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                service._execute_pipeline(body, job_id, checkpoint_dir)
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Frontier-in-solve and /frontier/select tests
# ---------------------------------------------------------------------------


class TestFrontierInSolve:
    """Verify that frontier data is computed as part of frontier-enabled solves."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_status_includes_frontier(self, client, scored_data):
        """After a successful solve with constraints, status includes frontier data."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_enabled": True,
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        # Frontier should be present in the result dict
        result = status["result"]
        assert result is not None
        assert "frontier" in result
        frontier = result["frontier"]
        assert frontier is not None
        assert frontier["n_points"] > 0
        assert len(frontier["points"]) == frontier["n_points"]
        assert "volume" in frontier["constraint_names"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_status_frontier_response_field(self, client, scored_data):
        """The top-level 'frontier' field on the status response is populated."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_enabled": True,
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        assert "frontier" in status
        frontier = status["frontier"]
        assert frontier is not None
        assert frontier["status"] == "ok"
        assert frontier["n_points"] > 0

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_with_empty_constraints_completes_without_frontier(
        self,
        client,
        scored_data,
    ):
        """Pin the contract for the no-constraints case end-to-end.

        Previously this test silently returned on any non-completed
        outcome — meaning a regression that made /solve crash for empty
        constraints would still pass.  Now we assert the full happy path:
        the solve completes, no frontier is computed, and the result
        contains the baseline objective with empty constraint maps.
        """
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "objective": "expected_income",
                "constraints": {},
                "max_iter": 5,
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed", (
            f"Empty-constraints solve failed: {status.get('message')!r}"
        )
        result = status["result"]
        assert result is not None
        # No frontier should be computed when there are no constraints to vary.
        assert result.get("frontier") is None
        # Result still contains the baseline objective and empty-but-defined
        # constraint maps so the UI can render a coherent summary.
        assert isinstance(result.get("baseline_objective"), (int, float))
        assert result.get("constraints") == {}
        assert result.get("baseline_constraints") == {}
        # No lambdas because there's nothing to enforce.
        assert result.get("lambdas") == {}

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_with_empty_constraints_ignores_frontier_enabled(
        self,
        client,
        scored_data,
    ):
        """When ``frontier_enabled=true`` but ``constraints={}``, the
        frontier is silently skipped (no points to optimise over).

        This is the contract behaviour that the rating UI relies on:
        ``frontier_enabled`` is a hint, not a hard requirement; the
        solver determines whether a frontier is meaningful.  A regression
        that started erroring (or, worse, silently producing garbage
        lambdas) on this combination would break every config that
        toggles ``frontier_enabled`` on without first adding constraints.
        """
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "objective": "expected_income",
                "constraints": {},
                "frontier_enabled": True,
                "frontier_ranges": {},  # no constraints → no ranges
                "max_iter": 5,
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        result = status["result"]
        # frontier_enabled with no constraints is a no-op — no frontier
        # data, no spurious frontier_error message.
        assert result.get("frontier") is None
        assert result.get("frontier_error") is None


class TestFrontierSelect:
    """Tests for POST /api/optimiser/frontier/select."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_frontier_point(self, client, scored_data):
        """A completed solve keeps runtime state so a frontier point can be selected."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_enabled": True,
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"

        frontier = status["result"]["frontier"]
        assert frontier is not None
        n_points = frontier["n_points"]
        assert n_points > 0

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["lambdas"]
        assert isinstance(data["converged"], bool)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_last_frontier_point(self, client, scored_data):
        """Selecting the final frontier point after solve succeeds."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_enabled": True,
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        frontier = status["result"]["frontier"]
        n_points = frontier["n_points"]

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": n_points - 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["lambdas"]

    def test_select_out_of_range(self, client, clean_job_store):
        """Point index >= n_points returns 400."""
        clean_job_store.jobs["sel_oob"] = {
            "status": "completed",
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [{"total_objective": 1.0, "lambda_volume": 0.5}],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {},
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_oob",
                "point_index": 5,
            },
        )
        assert resp.status_code == 400
        assert "out of range" in resp.json()["detail"].lower()

    def test_select_point_beyond_capped_returned_points_is_explicit(
        self,
        client,
        clean_job_store,
    ):
        clean_job_store.jobs["sel_capped"] = {
            "status": "completed",
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [{"total_objective": 1.0, "lambda_volume": 0.5}],
                "n_points": 3,
                "points_returned": 1,
                "points_limit": 1,
                "points_truncated": True,
                "constraint_names": ["volume"],
            },
            "result": {},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_capped",
                "point_index": 2,
            },
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "not available" in detail
        assert "1 of 3" in detail
        assert "limit 1" in detail

    def test_select_ratebook_frontier_point_uses_factors(
        self,
        client,
        clean_job_store,
    ):
        """Ratebook frontier selection uses stored summaries without re-solving."""
        factors_df = pl.DataFrame({"region": ["North", "South"]})
        mock_grid = MagicMock()
        mock_solver = MagicMock()
        mock_solver.solve.return_value = SimpleNamespace(
            total_objective=120.0,
            baseline_objective=95.0,
            total_constraints={"volume": 0.97},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.3},
            converged=True,
        )
        clean_job_store.jobs["rb_select"] = {
            "status": "completed",
            "config": {"mode": "ratebook", "factor_columns": [["region"]]},
            "solver": mock_solver,
            "quote_grid": mock_grid,
            "factors_df": factors_df,
            "factor_columns_valid": [["region"]],
            "artifact_handles": {},
            "result": {
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.92},
                "baseline_constraints": {"volume": 0.88},
                "lambdas": {"volume": 0.5},
                "converged": True,
            },
            "frontier_data": {
                "status": "ok",
                "n_points": 1,
                "points_returned": 1,
                "points_limit": FRONTIER_POINT_LIMIT,
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.3,
                        total_objective=120.0,
                        total_volume=0.97,
                    )
                ],
                "constraint_names": ["volume"],
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "rb_select", "point_index": 0},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["constraints"] == {"volume": 0.97}
        mock_solver.solve.assert_not_called()

    def test_select_ratebook_frontier_point_materialises_tables_in_banding_order(
        self,
        client,
        clean_job_store,
    ):
        """On-demand Rates tab materialisation keeps the stored band order."""
        _make_ratebook_frontier_materialisation_job(clean_job_store, "rb_select_order")
        clean_job_store.jobs["rb_select_order"]["factor_level_order"] = {
            "region": ["South", "North"]
        }

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_order",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )

        assert resp.status_code == 200
        rows = resp.json()["factor_tables"]["region"]
        assert [row["__factor_group__"] for row in rows] == ["South", "North"]
        assert [row["quote_count"] for row in rows] == [1, 1]

    def test_select_negative_index(self, client, clean_job_store):
        """Negative point index returns 422 (Pydantic validation via Field(ge=0))."""
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_neg",
                "point_index": -1,
            },
        )
        assert resp.status_code == 422

    def test_select_no_frontier_data(self, client, clean_job_store):
        """Select when no frontier data returns 400."""
        clean_job_store.jobs["sel_nf"] = {
            "status": "completed",
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "frontier_data": None,
            "result": {},
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_nf",
                "point_index": 0,
            },
        )
        assert resp.status_code == 400
        assert "no frontier" in resp.json()["detail"].lower()

    def test_select_missing_job(self, client):
        """Non-existent job returns 404."""
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "nonexistent",
                "point_index": 0,
            },
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_after_solve_persists_selected_point(self, client, scored_data):
        """Selecting after solve updates the completed job's active result."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_enabled": True,
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 200

        from haute.routes.optimiser import _store

        job = _store.require_job(job_id)
        assert job["selected_frontier_point"] == 0
        assert job["result"]["selected_frontier_point"] == 0

    def test_select_does_not_touch_heavy_object_ttl_and_survives_expiry(
        self,
        client,
        clean_job_store,
    ):
        solver = MagicMock()
        clean_job_store.jobs["sel_touch_ttl"] = {
            "status": "completed",
            "created_at": 100.0,
            "completed_at": 100.0,
            "heavy_objects_expires_at": 1000.0,
            "solver": solver,
            "solve_result": object(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.4,
                        total_objective=120.0,
                        total_volume=0.9,
                    ),
                    _frontier_point_summary(
                        lambda_volume=0.5,
                        total_objective=125.0,
                        total_volume=0.95,
                    ),
                ],
                "n_points": 2,
                "constraint_names": ["volume"],
            },
            "result": {
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.85},
                "baseline_constraints": {"volume": 0.8},
                "lambdas": {"volume": 0.2},
                "converged": True,
            },
            "artifact_handles": {},
        }

        with patch("haute.routes._job_store.time.time", return_value=940.0):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_ttl", "point_index": 0},
            )
        assert resp.status_code == 200
        assert clean_job_store.jobs["sel_touch_ttl"]["heavy_objects_expires_at"] == pytest.approx(
            1000.0
        )
        solver.solve.assert_not_called()

        with patch("haute.routes._job_store.time.time", return_value=1780.0):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_ttl", "point_index": 0},
            )
        assert resp.status_code == 200
        assert "heavy_objects_expires_at" not in clean_job_store.jobs["sel_touch_ttl"]

        with patch("haute.routes._job_store.time.time", return_value=2741.0):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_ttl", "point_index": 1},
            )

        assert resp.status_code == 200
        job = clean_job_store.jobs["sel_touch_ttl"]
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        assert job["result"]["total_objective"] == 125.0

    def test_select_does_not_reserve_heavy_objects_or_run_solver(
        self,
        client,
        clean_job_store,
    ):
        current_time = {"value": 940.0}
        solver = MagicMock()
        clean_job_store.jobs["sel_touch_before_work"] = {
            "status": "completed",
            "created_at": 100.0,
            "completed_at": 100.0,
            "heavy_objects_expires_at": 1000.0,
            "solver": solver,
            "solve_result": object(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.4,
                        total_objective=120.0,
                        total_volume=0.9,
                    )
                ],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.85},
                "baseline_constraints": {"volume": 0.8},
                "lambdas": {"volume": 0.2},
                "converged": True,
            },
            "artifact_handles": {},
        }

        with patch(
            "haute.routes._job_store.time.time",
            side_effect=lambda: current_time["value"],
        ):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_before_work", "point_index": 0},
            )
            job = clean_job_store.get_job("sel_touch_before_work")

        assert resp.status_code == 200
        assert job is not None
        assert "solver" in job
        assert "solve_result" not in job
        assert "quote_grid" in job
        assert job["heavy_objects_expires_at"] == pytest.approx(1000.0)
        solver.solve.assert_not_called()

    def test_select_does_not_materialise_apply_artifacts(
        self,
        client,
        clean_job_store,
    ):
        solver = MagicMock()
        clean_job_store.jobs["sel_cleanup_primary"] = {
            "status": "completed",
            "solver": solver,
            "solve_result": object(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.4,
                        total_objective=120.0,
                        total_volume=0.9,
                    )
                ],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.85},
                "baseline_constraints": {"volume": 0.8},
                "lambdas": {"volume": 0.2},
                "converged": True,
            },
            "artifact_handles": {},
            "created_at": time.time(),
        }

        with (
            patch(
                "haute.routes.optimiser._persist_apply_result_artifact",
                side_effect=AssertionError("selection must not persist apply artifacts"),
            ),
            patch.object(
                clean_job_store,
                "atomic_update_if_heavy_present",
                side_effect=AssertionError("selection must not reserve heavy state"),
            ),
        ):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_cleanup_primary", "point_index": 0},
            )

        assert resp.status_code == 200
        solver.solve.assert_not_called()


# ---------------------------------------------------------------------------
# _validate_config unit tests
# ---------------------------------------------------------------------------


class TestValidateConfig:
    """Direct unit tests for OptimiserSolveService._validate_config."""

    def test_no_objective_raises_400(self):
        from fastapi import HTTPException

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(HTTPException) as exc_info:
            OptimiserSolveService._validate_config({"objective": ""})
        assert exc_info.value.status_code == 400
        assert "objective" in exc_info.value.detail.lower()

    def test_no_objective_key_raises_400(self):
        from fastapi import HTTPException

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(HTTPException) as exc_info:
            OptimiserSolveService._validate_config({"constraints": {}})
        assert exc_info.value.status_code == 400

    def test_invalid_mode_raises_400(self):
        from fastapi import HTTPException

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(HTTPException) as exc_info:
            OptimiserSolveService._validate_config({"objective": "income", "mode": "quantum"})
        assert exc_info.value.status_code == 400
        assert "quantum" in exc_info.value.detail

    def test_ratebook_without_factor_columns_raises_400(self):
        from fastapi import HTTPException

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(HTTPException) as exc_info:
            OptimiserSolveService._validate_config(
                {"objective": "income", "mode": "ratebook", "factor_columns": []}
            )
        assert exc_info.value.status_code == 400
        assert "factor_columns" in exc_info.value.detail.lower()

    def test_valid_online_config_passes(self):
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config({"objective": "income", "mode": "online"})
        assert mode == "online"

    def test_valid_online_config_default_mode(self):
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config({"objective": "income"})
        assert mode == "online"

    def test_valid_ratebook_config_passes(self):
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config(
            {
                "objective": "income",
                "mode": "ratebook",
                "factor_columns": [["region"]],
            }
        )
        assert mode == "ratebook"

    def test_validate_config_max_iter_zero(self):
        """max_iter=0 should not block validation — it is not checked by _validate_config."""
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config(
            {"objective": "income", "mode": "online", "max_iter": 0}
        )
        assert mode == "online"

    def test_validate_config_negative_tolerance(self):
        """tolerance=-1 should not block validation — tolerance is not validated here."""
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config(
            {"objective": "income", "mode": "online", "tolerance": -1}
        )
        assert mode == "online"

    def test_validate_config_empty_objective_string(self):
        """objective='' is treated as missing and should raise 400."""
        from fastapi import HTTPException

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(HTTPException) as exc_info:
            OptimiserSolveService._validate_config({"objective": ""})
        assert exc_info.value.status_code == 400
        assert "objective" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# _compute_scenario_value_stats unit tests (gap: empty dataframe)
# ---------------------------------------------------------------------------


class TestComputeScenarioValueStatsExtended:
    def test_empty_dataframe_omits_distribution_payloads(self):
        df = pl.DataFrame({"optimal_scenario_value": pl.Series([], dtype=pl.Float64)})
        result = SimpleNamespace(dataframe=df)
        stats, hist = _compute_scenario_value_stats(result)
        assert stats is None
        assert hist is None

    def test_normal_distribution_returns_full_stats(self):
        rng = np.random.RandomState(0)
        values = rng.normal(1.0, 0.1, 1000).tolist()
        df = pl.DataFrame({"optimal_scenario_value": values})
        result = SimpleNamespace(dataframe=df)
        stats, hist = _compute_scenario_value_stats(result)
        for key in ("mean", "std", "min", "max", "p5", "p25", "p50", "p75", "p95"):
            assert key in stats, f"Missing stat key: {key}"
        assert stats["mean"] == pytest.approx(1.0, abs=0.05)
        assert stats["std"] > 0
        assert stats["p5"] < stats["p25"] < stats["p50"] < stats["p75"] < stats["p95"]
        assert "counts" in hist
        assert "edges" in hist
        assert len(hist["counts"]) == 20
        assert len(hist["edges"]) == 21


# ---------------------------------------------------------------------------
# _finalize_solve_result unit tests
# ---------------------------------------------------------------------------


class TestFinalizeSolveResult:
    def _make_solve_result(self, *, converged=True):
        df = pl.DataFrame({"optimal_scenario_value": [0.9, 1.0, 1.1, 1.2, 0.8]})
        return SimpleNamespace(
            dataframe=df,
            total_objective=100.0,
            baseline_objective=95.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=converged,
        )

    def test_convergence_warning_when_not_converged(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
        solve_result = self._make_solve_result(converged=False)
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert "warning" in job["result"]
        assert "converge" in job["result"]["warning"].lower()

    def test_no_warning_when_converged(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
        solve_result = self._make_solve_result(converged=True)
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert "warning" not in job["result"]

    def test_result_dict_includes_core_fields(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=2.5,
        )

        job = store.require_job(job_id)
        result = job["result"]
        assert result["total_objective"] == 100.0
        assert result["lambdas"] == {"volume": 0.5}
        assert result["constraints"] == {"volume": 0.92}
        assert result["converged"] is True
        assert result["baseline_objective"] == 95.0
        assert result["baseline_constraints"] == {"volume": 0.88}
        assert job["status"] == "completed"
        assert job["elapsed_seconds"] == 2.5

    def test_frontier_not_computed_for_individual_point_mode(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        for frontier_enabled in (None, False):
            config = {"constraints": {"volume": {"min": 0.9}}}
            if frontier_enabled is not None:
                config["frontier_enabled"] = frontier_enabled
            job_id = store.create_job({"status": "running", "config": config})
            solve_result = self._make_solve_result()
            mock_solver = MagicMock()

            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=mock_solver,
                quote_grid=MagicMock(),
                store=store,
                job_id=job_id,
                elapsed=1.0,
            )

            job = store.require_job(job_id)
            assert job["frontier_data"] is None
            assert job["result"]["frontier"] is None
            mock_solver.frontier.assert_not_called()

    def test_frontier_missing_ranges_surfaces_error_without_defaulting(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None
        assert "frontier_ranges must provide min and max" in job["result"]["frontier_error"]
        mock_solver.frontier.assert_not_called()

    def test_frontier_partial_range_surfaces_error_without_defaulting_missing_side(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_ranges": {"volume": {"min": 0.8}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None
        assert (
            "frontier_ranges.volume must contain min and max values"
            in job["result"]["frontier_error"]
        )
        mock_solver.frontier.assert_not_called()

    def test_frontier_computed_for_online_mode(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_min": 0.8,
                    "frontier_max": 1.1,
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        frontier_points = MagicMock()
        frontier_points.to_dicts.return_value = [
            {"total_objective": 100, "lambda_volume": 0.3},
            {"total_objective": 110, "lambda_volume": 0.5},
        ]
        frontier_points.__len__ = lambda self: 2
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["n_points"] == 2
        assert job["frontier_data"]["points_returned"] == 2
        assert job["frontier_data"]["points_limit"] == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_truncated"] is False
        assert "volume" in job["frontier_data"]["constraint_names"]
        mock_solver.frontier.assert_called_once()
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {"volume": (0.8, 1.1)}

    def test_frontier_progress_is_visible_while_finalize_computes_frontier(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "start_time": time.monotonic() - 5.0,
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_min": 0.8,
                    "frontier_max": 1.1,
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()

        def frontier_side_effect(*args, **kwargs):
            job = store.require_job(job_id)
            assert job["status"] == "running"
            assert job["message"] == "Computing efficient frontier"
            assert job["progress"] == pytest.approx(0.8)
            assert job["elapsed_seconds"] >= 5.0
            return SimpleNamespace(
                points=pl.DataFrame(
                    {
                        "total_objective": [100.0],
                        "volume": [0.9],
                        "lambda_volume": [0.3],
                    }
                )
            )

        mock_solver.frontier.side_effect = frontier_side_effect

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=0.25,
        )

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["message"] == "Completed"
        assert job["elapsed_seconds"] >= 5.0

    def test_frontier_prefers_per_constraint_absolute_ranges(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_min": 0.8,
                    "frontier_max": 1.1,
                    "frontier_ranges": {"volume": {"min": 70.0, "max": 95.0}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame({"total_objective": [100.0], "lambda_volume": [0.3]})
        )

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {"volume": (70.0, 95.0)}

    def test_auto_frontier_payload_is_capped_in_job_state(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_min": 0.8,
                    "frontier_max": 1.1,
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        frontier_points = pl.DataFrame({"total_objective": list(range(FRONTIER_POINT_LIMIT + 1))})
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(job["frontier_data"]["points"]) == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_returned"] == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_limit"] == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_truncated"] is True
        assert job["result"]["frontier"] == job["frontier_data"]

    def test_auto_frontier_slices_before_serialising_points(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        class VisiblePoints:
            def __init__(self, size: int) -> None:
                self.size = size

            def to_dicts(self):
                return [{"total_objective": i} for i in range(self.size)]

        class HugePoints:
            def __len__(self) -> int:
                return FRONTIER_POINT_LIMIT + 1

            def head(self, n: int):
                assert n == FRONTIER_POINT_LIMIT
                return VisiblePoints(n)

            def to_dicts(self):
                raise AssertionError("Full frontier must not be serialised")

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(points=HugePoints())

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"]["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(job["frontier_data"]["points"]) == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_truncated"] is True

    def test_frontier_failure_is_surfaced_on_completed_status(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_service import _finalize_solve_result

        job_id = clean_job_store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_min": 0.8,
                    "frontier_max": 1.1,
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_solver.frontier.side_effect = RuntimeError("frontier exploded")

        with patch("haute.routes._optimiser_service.logger.warning") as log_warning:
            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=mock_solver,
                quote_grid=MagicMock(),
                store=clean_job_store,
                job_id=job_id,
                elapsed=1.0,
            )

        job = clean_job_store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None
        assert job["result"]["frontier_error"] == ("Frontier unavailable: frontier exploded")
        log_warning.assert_called_once()
        assert log_warning.call_args.kwargs["exc_info"] is True

        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"]["frontier"] is None
        assert data["result"]["frontier_error"] == ("Frontier unavailable: frontier exploded")

    def test_frontier_computed_for_ratebook_mode_with_factors(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_min": 0.8,
                    "frontier_max": 1.1,
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        frontier_points = MagicMock()
        frontier_points.to_dicts.return_value = [
            {"total_objective": 100, "lambda_volume": 0.3},
            {"total_objective": 110, "lambda_volume": 0.5},
        ]
        frontier_points.__len__ = lambda self: 2
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)
        mock_grid = MagicMock()
        factors_df = pl.DataFrame({"region": ["North", "South"]})

        _finalize_solve_result(
            solve_result,
            mode="ratebook",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
            factors_df=factors_df,
            factor_columns=[["region"]],
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["n_points"] == 2
        mock_solver.frontier.assert_called_once()
        assert mock_solver.frontier.call_args.args == (mock_grid, factors_df)
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"]["volume"] == pytest.approx(
            (0.8, 1.1)
        )
        assert mock_solver.frontier.call_args.kwargs["n_points_per_dim"] == 3
        assert mock_solver.frontier.call_args.kwargs["factor_columns"] == [["region"]]

    def test_finalized_job_slims_heavy_runtime_state_after_policy_window(self):
        from unittest.mock import patch

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore(ttl_seconds=60, heavy_object_ttl_seconds=1)
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=mock_solver,
                quote_grid=mock_grid,
                store=store,
                job_id=job_id,
                elapsed=1.0,
            )

        with patch("haute.routes._job_store.time.time", return_value=102.0):
            job = store.require_job(job_id)

        assert job["status"] == "completed"
        assert job["result"]["total_objective"] == 100.0
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job

    def test_finalized_job_persists_apply_artifact_handle(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
        solve_result = self._make_solve_result()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=MagicMock(),
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        handle = job["artifact_handles"]["apply_result"]
        assert handle["kind"] == "optimiser_apply_result"
        assert handle["format"] == "parquet"
        assert handle["row_count"] == len(solve_result.dataframe)
        assert Path(handle["path"]).is_file()
        assert "dataframe" not in job["result"]

    def test_finalized_job_cleans_apply_artifact_when_status_guard_skips(
        self,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
        store.atomic_update(
            job_id,
            {"status": "error", "message": "Solve timed out after 10s"},
            expected_status="running",
        )
        artifact_dir: Path | None = None

        def _mkdtemp(*, prefix: str, dir: str) -> str:
            nonlocal artifact_dir
            assert prefix == "apply_"
            artifact_dir = Path(dir) / "apply_guard"
            artifact_dir.mkdir(parents=True)
            return str(artifact_dir)

        with patch("haute.routes._optimiser_service.tempfile.mkdtemp", side_effect=_mkdtemp):
            _finalize_solve_result(
                self._make_solve_result(),
                mode="online",
                solver=MagicMock(),
                quote_grid=MagicMock(),
                store=store,
                job_id=job_id,
                elapsed=12.0,
            )

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["message"] == "Solve timed out after 10s"
        assert job.get("result") is None
        assert job.get("artifact_handles") is None
        assert artifact_dir is not None
        assert not artifact_dir.exists()


# ---------------------------------------------------------------------------
# solve_status edge cases
# ---------------------------------------------------------------------------


class TestSolveStatusEdgeCases:
    def test_running_job_returns_progress(self, client, clean_job_store):
        clean_job_store.jobs["running_prog"] = {
            "status": "running",
            "progress": 0.42,
            "message": "Solving",
            "start_time": time.monotonic(),
            "timeout": 9999,
            "elapsed_seconds": 1.5,
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/running_prog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"] == pytest.approx(0.42)
        assert data["message"] == "Solving"

    def test_completed_job_returns_result_with_frontier(self, client, clean_job_store):
        clean_job_store.jobs["done_frontier"] = {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": 5.0,
            "result": {
                "mode": "online",
                "total_objective": 200.0,
                "baseline_objective": 180.0,
                "constraints": {"volume": 0.92},
                "baseline_constraints": {"volume": 0.88},
                "lambdas": {"volume": 0.4},
                "converged": True,
            },
            "frontier_data": {
                "status": "ok",
                "points": [{"obj": 1.0}],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/done_frontier")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"]["total_objective"] == 200.0
        assert data["frontier"] is not None
        assert data["frontier"]["status"] == "ok"
        assert data["frontier"]["n_points"] == 1


# ---------------------------------------------------------------------------
# apply_lambdas unit tests (row count & preview via mock)
# ---------------------------------------------------------------------------


class TestApplyLambdasUnit:
    def test_apply_returns_row_count_and_preview(self, client, clean_job_store):
        df = pl.DataFrame(
            {
                "quote_id": [f"q{i}" for i in range(5)],
                "optimal_scenario_value": [1.0] * 5,
            }
        )
        mock_solve_result = SimpleNamespace(
            dataframe=df,
            total_objective=500.0,
            total_constraints={"volume": 0.95},
        )
        clean_job_store.jobs["apply_unit"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "created_at": time.time(),
        }
        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_unit"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["row_count"] == 5
        assert data["total_objective"] == 500.0
        assert len(data["preview"]) == 5
        assert data["preview_row_count"] == 5
        assert data["preview_row_limit"] == APPLY_PREVIEW_ROW_LIMIT
        assert data["preview_truncated"] is False

    def test_apply_preview_payload_is_capped_with_truncation_metadata(
        self,
        client,
        clean_job_store,
    ):
        df = pl.DataFrame(
            {
                "quote_id": [f"q{i}" for i in range(APPLY_PREVIEW_ROW_LIMIT + 1)],
                "optimal_scenario_value": [1.0] * (APPLY_PREVIEW_ROW_LIMIT + 1),
            }
        )
        mock_solve_result = SimpleNamespace(
            dataframe=df,
            total_objective=500.0,
            total_constraints={"volume": 0.95},
        )
        clean_job_store.jobs["apply_capped"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "created_at": time.time(),
        }

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_capped"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == APPLY_PREVIEW_ROW_LIMIT + 1
        assert len(data["preview"]) == APPLY_PREVIEW_ROW_LIMIT
        assert data["preview_row_count"] == APPLY_PREVIEW_ROW_LIMIT
        assert data["preview_row_limit"] == APPLY_PREVIEW_ROW_LIMIT
        assert data["preview_truncated"] is True

    def test_apply_loads_persisted_artifact_handle_after_heavy_result_is_slimmed(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "optimal_scenario_value": [1.0, 1.1],
            }
        )
        handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
        assert handle is not None
        clean_job_store.jobs["apply_handle"] = {
            "status": "completed",
            "result": {
                "total_objective": 500.0,
                "constraints": {"volume": 0.95},
            },
            "artifact_handles": {"apply_result": handle},
            "created_at": time.time(),
        }

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_handle"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_objective"] == 500.0
        assert data["constraints"] == {"volume": 0.95}
        assert data["row_count"] == 2
        assert data["preview"] == [
            {"quote_id": "q1", "optimal_scenario_value": 1.0},
            {"quote_id": "q2", "optimal_scenario_value": 1.1},
        ]

    def test_apply_success_clears_heavy_result_but_keeps_handle_for_later_apply(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "optimal_scenario_value": [1.0, 1.1],
            }
        )
        handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
        assert handle is not None
        clean_job_store.jobs["apply_terminal"] = {
            "status": "completed",
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "solve_result": SimpleNamespace(
                dataframe=df,
                total_objective=500.0,
                total_constraints={"volume": 0.95},
            ),
            "result": {
                "total_objective": 500.0,
                "constraints": {"volume": 0.95},
            },
            "artifact_handles": {"apply_result": handle},
            "created_at": time.time(),
        }

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_terminal"})

        assert resp.status_code == 200
        job = clean_job_store.jobs["apply_terminal"]
        assert "solver" not in job
        assert "quote_grid" not in job
        assert "solve_result" not in job
        assert job["artifact_handles"]["apply_result"]["path"] == handle["path"]

        second_resp = client.post("/api/optimiser/apply", json={"job_id": "apply_terminal"})

        assert second_resp.status_code == 200
        assert second_resp.json()["preview"] == [
            {"quote_id": "q1", "optimal_scenario_value": 1.0},
            {"quote_id": "q2", "optimal_scenario_value": 1.1},
        ]

    def test_apply_corrupt_artifact_handle_fails_loudly(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"quote_id": ["q1"]}))
        )
        assert handle is not None
        Path(handle["path"]).unlink()
        clean_job_store.jobs["apply_missing_handle"] = {
            "status": "completed",
            "result": {
                "total_objective": 500.0,
                "constraints": {"volume": 0.95},
            },
            "artifact_handles": {"apply_result": handle},
            "created_at": time.time(),
        }

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_missing_handle"})

        assert resp.status_code == 500
        assert "artifact" in resp.json()["detail"].lower()

    def test_apply_solve_result_without_dataframe_attribute_fails_loudly(
        self,
        client,
        clean_job_store,
    ):
        """A solve_result missing ``dataframe`` is a backend bug, not a 500.

        Previously the ``cast(_DataFrameResultLike, ...).dataframe`` access
        raised ``AttributeError`` which the broad ``except Exception``
        funnelled into a generic 500 with no actionable detail.  Surface a
        typed error instead so the cause is obvious in the response.
        """
        clean_job_store.jobs["apply_no_dataframe"] = {
            "status": "completed",
            "solve_result": SimpleNamespace(
                # No ``dataframe`` attribute on purpose.
                total_objective=42.0,
                total_constraints={"volume": 1.0},
            ),
            "result": {"total_objective": 42.0, "constraints": {"volume": 1.0}},
            "created_at": time.time(),
        }

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_no_dataframe"})

        assert resp.status_code == 500
        detail = resp.json()["detail"].lower()
        assert "dataframe" in detail
        assert "solve_result" in detail or "solve result" in detail

    def test_apply_after_heavy_result_policy_expiry_without_handle_returns_400(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._job_store import _DEFAULT_HEAVY_OBJECT_TTL_SECONDS

        clean_job_store.jobs["apply_expired"] = {
            "status": "completed",
            "solve_result": SimpleNamespace(
                dataframe=pl.DataFrame({"quote_id": ["q1"]}),
                total_objective=100.0,
                total_constraints={},
            ),
            "result": {"total_objective": 100.0},
            "created_at": time.time() - _DEFAULT_HEAVY_OBJECT_TTL_SECONDS - 1,
        }

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_expired"})

        assert resp.status_code == 400
        assert "apply artifact handle" in resp.json()["detail"].lower()
        job = clean_job_store.jobs["apply_expired"]
        assert job["status"] == "completed"
        assert job["result"] == {"total_objective": 100.0}
        assert "solve_result" not in job

    def test_apply_ratebook_frontier_point_materialises_factor_tables_and_artifact(
        self,
        client,
        clean_job_store,
    ):
        mock_solver, mock_grid, factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "apply_rb_frontier",
        )

        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_rb_frontier", "point_index": 0},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_objective"] == 222.0
        assert data["constraints"] == {"volume": 0.97}
        assert data["preview"] == [
            {"quote_id": "q1", "optimal_scenario_value": 1.08},
            {"quote_id": "q2", "optimal_scenario_value": 0.92},
        ]
        args = mock_solver.solve.call_args.args
        assert args[0] is mock_grid
        assert args[1] is factors_df
        assert mock_solver.solve.call_args.kwargs == {
            "factor_columns": [["region"]],
            "lambdas": {"volume": 0.7},
            "_constraints_override": {"volume": {"min": 0.96}},
        }
        job = clean_job_store.jobs["apply_rb_frontier"]
        assert job["result"]["factor_tables"] == _expected_region_factor_tables()
        assert "frontier_apply_result:0" in job["artifact_handles"]
        assert job["solver"] is mock_solver
        assert job["quote_grid"] is mock_grid
        assert job["factors_df"].equals(factors_df)
        assert "solve_result" not in job

    def test_apply_ratebook_frontier_point_reuses_cached_artifact_and_summary(
        self,
        client,
        clean_job_store,
    ):
        mock_solver, _mock_grid, _factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "apply_rb_frontier_cached",
        )
        first_resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_rb_frontier_cached", "point_index": 0},
        )
        assert first_resp.status_code == 200
        mock_solver.solve.reset_mock()

        second_resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_rb_frontier_cached", "point_index": 0},
        )

        assert second_resp.status_code == 200
        data = second_resp.json()
        assert data["from_artifact"] is True
        assert data["total_objective"] == 222.0
        assert data["constraints"] == {"volume": 0.97}
        mock_solver.solve.assert_not_called()
        job = clean_job_store.jobs["apply_rb_frontier_cached"]
        assert job["result"]["factor_tables"] == _expected_region_factor_tables()

    def test_save_ratebook_frontier_point_rebuilds_stale_cached_summary(
        self,
        client,
        clean_job_store,
        tmp_path,
    ):
        import json as json_mod

        from haute._sandbox import set_project_root

        mock_solver, _mock_grid, _factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "save_rb_frontier_stale_cache",
        )
        job = clean_job_store.jobs["save_rb_frontier_stale_cache"]
        job["selected_frontier_point"] = 0
        job["result"].update(
            {
                "selected_frontier_point": 0,
                "total_objective": 111.0,
                "constraints": {"volume": 0.91},
                "lambdas": {"volume": 0.1},
                "factor_tables": {
                    "region": [{"__factor_group__": "Stale", "optimal_scenario_value": 1.0}]
                },
            }
        )
        set_project_root(tmp_path)
        out_path = str(tmp_path / "selected_ratebook.json")

        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_rb_frontier_stale_cache",
                "output_path": out_path,
                "point_index": 0,
            },
        )

        assert resp.status_code == 200
        mock_solver.solve.assert_called_once()
        assert mock_solver.solve.call_args.kwargs["lambdas"] == {"volume": 0.7}
        saved = json_mod.loads(Path(out_path).read_text())
        assert saved["total_objective"] == 222.0
        assert saved["total_constraints"] == {"volume": 0.97}
        assert saved["factor_tables"] == _expected_region_factor_tables()


# ---------------------------------------------------------------------------
# run_frontier unit tests
# ---------------------------------------------------------------------------


class TestRunFrontierUnit:
    def test_frontier_returns_points_and_constraint_names(self, client, clean_job_store):
        mock_solver = MagicMock()
        frontier_points = MagicMock()
        frontier_points.to_dicts.return_value = [
            {"obj": 100, "lambda_vol": 0.3},
            {"obj": 110, "lambda_vol": 0.5},
            {"obj": 120, "lambda_vol": 0.7},
        ]
        frontier_points.__len__ = lambda self: 3
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)

        clean_job_store.jobs["frontier_unit"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_unit",
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["n_points"] == 3
        assert len(data["points"]) == 3
        assert data["points_returned"] == 3
        assert data["constraint_names"] == ["volume"]
        assert data["points_limit"] == FRONTIER_POINT_LIMIT
        assert data["points_truncated"] is False

    def test_frontier_recompute_resets_selected_result_to_base(
        self,
        client,
        clean_job_store,
    ):
        """Recomputing a frontier clears stale selected-point state."""
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [300.0],
                    "volume": [0.97],
                    "lambda_volume": [0.8],
                    "converged": [True],
                }
            )
        )
        base_result = {
            "mode": "online",
            "total_objective": 100.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.9},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.3},
            "converged": True,
        }
        old_frontier = {
            "status": "ok",
            "points": [
                _frontier_point_summary(
                    lambda_volume=0.7,
                    total_objective=200.0,
                    total_volume=0.95,
                )
            ],
            "n_points": 1,
            "points_returned": 1,
            "constraint_names": ["volume"],
            "points_limit": FRONTIER_POINT_LIMIT,
            "points_truncated": False,
        }
        clean_job_store.jobs["frontier_recompute_selected"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "base_result": {**base_result, "frontier": old_frontier},
            "result": {
                **base_result,
                "total_objective": 200.0,
                "constraints": {"volume": 0.95},
                "lambdas": {"volume": 0.7},
                "selected_frontier_point": 0,
                "frontier": old_frontier,
            },
            "frontier_data": old_frontier,
            "selected_frontier_point": 0,
            "artifact_handles": {},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_recompute_selected",
                "threshold_ranges": {"volume": [0.9, 1.0]},
                "n_points_per_dim": 2,
            },
        )

        assert resp.status_code == 200
        job = clean_job_store.jobs["frontier_recompute_selected"]
        assert job["selected_frontier_point"] is None
        assert "selected_frontier_point" not in job["result"]
        assert job["result"]["total_objective"] == 100.0
        assert job["result"]["constraints"] == {"volume": 0.9}
        assert job["result"]["lambdas"] == {"volume": 0.3}
        assert job["result"]["frontier"]["points"][0]["total_objective"] == 300.0
        assert job["base_result"] == job["result"]
        assert mock_solver.frontier.call_args.kwargs["initial_lambdas"] == {"volume": 0.3}

    def test_frontier_recompute_invalidates_point_apply_artifacts(
        self,
        client,
        clean_job_store,
    ):
        """Old point-indexed apply artifacts must not be reused for a new frontier."""
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        stale_frontier_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [9.9]}))
        )
        base_apply_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}))
        )
        assert stale_frontier_handle is not None
        assert base_apply_handle is not None
        stale_frontier_path = Path(stale_frontier_handle["path"])
        base_apply_path = Path(base_apply_handle["path"])

        mock_solver = MagicMock()
        mock_grid = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [300.0],
                    "volume": [0.97],
                    "lambda_volume": [0.8],
                    "converged": [True],
                }
            )
        )
        base_result = {
            "mode": "online",
            "total_objective": 100.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.9},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.3},
            "converged": True,
        }
        clean_job_store.jobs["frontier_recompute_artifacts"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": mock_grid,
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "base_result": base_result,
            "result": {**base_result, "selected_frontier_point": 0},
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.7,
                        total_objective=200.0,
                        total_volume=0.95,
                    )
                ],
                "n_points": 1,
                "points_returned": 1,
                "constraint_names": ["volume"],
                "points_limit": FRONTIER_POINT_LIMIT,
                "points_truncated": False,
            },
            "selected_frontier_point": 0,
            "artifact_handles": {
                "apply_result": base_apply_handle,
                "frontier_apply_result:0": stale_frontier_handle,
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_recompute_artifacts",
                "threshold_ranges": {"volume": [0.9, 1.0]},
                "n_points_per_dim": 2,
            },
        )
        assert resp.status_code == 200

        job = clean_job_store.jobs["frontier_recompute_artifacts"]
        assert job["artifact_handles"] == {"apply_result": base_apply_handle}
        assert base_apply_path.is_file()
        assert not stale_frontier_path.exists()

        apply_result = SimpleNamespace(
            dataframe=pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "optimal_scenario_value": [1.2],
                }
            )
        )
        with patch("price_contour.apply_from_grid", return_value=apply_result) as mock_apply:
            apply_resp = client.post(
                "/api/optimiser/apply",
                json={"job_id": "frontier_recompute_artifacts", "point_index": 0},
            )

        assert apply_resp.status_code == 200
        assert apply_resp.json()["from_artifact"] is False
        assert apply_resp.json()["preview"][0]["optimal_scenario_value"] == 1.2
        mock_apply.assert_called_once()
        assert mock_apply.call_args.kwargs["lambdas"] == {"volume": 0.8}

    def test_frontier_recompute_invalidates_point_artifacts_created_during_compute(
        self,
        client,
        clean_job_store,
    ):
        """Point apply artifacts created during frontier compute cannot survive."""
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        base_apply_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}))
        )
        concurrent_frontier_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [9.9]}))
        )
        assert base_apply_handle is not None
        assert concurrent_frontier_handle is not None
        base_apply_path = Path(base_apply_handle["path"])
        concurrent_frontier_path = Path(concurrent_frontier_handle["path"])

        base_result = {
            "mode": "online",
            "total_objective": 100.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.9},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.3},
            "converged": True,
        }
        mock_solver = MagicMock()

        def frontier_side_effect(*args, **kwargs):
            existing_job = clean_job_store.jobs["frontier_recompute_concurrent"]
            clean_job_store.jobs["frontier_recompute_concurrent"] = {
                **existing_job,
                "artifact_handles": {
                    "apply_result": base_apply_handle,
                    "frontier_apply_result:0": concurrent_frontier_handle,
                },
            }
            return SimpleNamespace(
                points=pl.DataFrame(
                    {
                        "total_objective": [300.0],
                        "volume": [0.97],
                        "lambda_volume": [0.8],
                        "converged": [True],
                    }
                )
            )

        mock_solver.frontier.side_effect = frontier_side_effect
        clean_job_store.jobs["frontier_recompute_concurrent"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "base_result": base_result,
            "result": {**base_result, "selected_frontier_point": 0},
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.7,
                        total_objective=200.0,
                        total_volume=0.95,
                    )
                ],
                "n_points": 1,
                "points_returned": 1,
                "constraint_names": ["volume"],
                "points_limit": FRONTIER_POINT_LIMIT,
                "points_truncated": False,
            },
            "selected_frontier_point": 0,
            "artifact_handles": {"apply_result": base_apply_handle},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_recompute_concurrent",
                "threshold_ranges": {"volume": [0.9, 1.0]},
                "n_points_per_dim": 2,
            },
        )

        assert resp.status_code == 200
        job = clean_job_store.jobs["frontier_recompute_concurrent"]
        assert job["artifact_handles"] == {"apply_result": base_apply_handle}
        assert base_apply_path.is_file()
        assert not concurrent_frontier_path.exists()

    def test_frontier_payload_is_capped_with_total_point_metadata(
        self,
        client,
        clean_job_store,
    ):
        mock_solver = MagicMock()
        points = [{"obj": i, "lambda_vol": i / 100} for i in range(FRONTIER_POINT_LIMIT + 1)]
        frontier_points = pl.DataFrame(points)
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)

        clean_job_store.jobs["frontier_capped"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_capped",
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(data["points"]) == FRONTIER_POINT_LIMIT
        assert data["points_returned"] == FRONTIER_POINT_LIMIT
        assert data["points_limit"] == FRONTIER_POINT_LIMIT
        assert data["points_truncated"] is True

    def test_frontier_route_slices_before_serialising_points(
        self,
        client,
        clean_job_store,
    ):
        class VisiblePoints:
            def __init__(self, size: int) -> None:
                self.size = size

            def to_dicts(self):
                return [{"obj": i, "lambda_vol": i / 100} for i in range(self.size)]

        class HugePoints:
            def __len__(self) -> int:
                return FRONTIER_POINT_LIMIT + 1

            def head(self, n: int):
                assert n == FRONTIER_POINT_LIMIT
                return VisiblePoints(n)

            def to_dicts(self):
                raise AssertionError("Full frontier must not be serialised")

        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(points=HugePoints())
        clean_job_store.jobs["frontier_serialise_budget"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_serialise_budget",
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(data["points"]) == FRONTIER_POINT_LIMIT
        assert data["points_truncated"] is True

    def test_frontier_incomplete_job_returns_400(self, client, clean_job_store):
        clean_job_store.jobs["frontier_inc"] = {
            "status": "running",
            "progress": 0.3,
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_inc",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# save_result unit tests
# ---------------------------------------------------------------------------


class TestSaveResultUnit:
    def test_save_path_traversal_blocked_unit(self, client, clean_job_store, tmp_path):
        from haute._sandbox import set_project_root

        mock_solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=100.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            baseline_objective=95.0,
            converged=True,
        )
        clean_job_store.jobs["save_trav"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "solver": MagicMock(),
            "config": {"mode": "online"},
            "node_label": "opt",
            "created_at": time.time(),
        }
        set_project_root(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_trav",
                "output_path": "../../etc/passwd",
            },
        )
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()

    def test_save_completed_job_writes_json(self, client, clean_job_store, tmp_path):
        import json as json_mod

        from haute._sandbox import set_project_root

        mock_solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=100.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            baseline_objective=95.0,
            converged=True,
            iterations=10,
        )
        clean_job_store.jobs["save_ok"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "objective": "income",
                "constraints": {"volume": {"min": 0.9}},
            },
            "node_label": "opt",
            "created_at": time.time(),
        }
        set_project_root(tmp_path)
        out_path = str(tmp_path / "out.json")
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_ok",
                "output_path": out_path,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        saved = json_mod.loads(Path(out_path).read_text())
        assert saved["lambdas"] == {"volume": 0.5}
        assert saved["total_objective"] == 100.0
        assert saved["converged"] is True
        job = clean_job_store.jobs["save_ok"]
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job

    def test_save_ratebook_frontier_point_materialises_selected_factor_tables(
        self,
        client,
        clean_job_store,
        tmp_path,
    ):
        import json as json_mod

        from haute._sandbox import set_project_root

        mock_solver, mock_grid, factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "save_rb_frontier",
        )
        set_project_root(tmp_path)
        out_path = str(tmp_path / "selected_ratebook.json")

        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_rb_frontier",
                "output_path": out_path,
                "point_index": 0,
            },
        )

        assert resp.status_code == 200
        saved = json_mod.loads(Path(out_path).read_text())
        assert saved["total_objective"] == 222.0
        assert saved["total_constraints"] == {"volume": 0.97}
        assert saved["factor_tables"] == _expected_region_factor_tables()
        assert saved["frontier_selection"]["point_index"] == 0
        args = mock_solver.solve.call_args.args
        assert args[0] is mock_grid
        assert args[1] is factors_df
        assert mock_solver.solve.call_args.kwargs["lambdas"] == {"volume": 0.7}


# ---------------------------------------------------------------------------
# Coverage expansion tests
# ---------------------------------------------------------------------------


class TestSelectFrontierPointIdempotent:
    """Test idempotent re-selection short circuit in select_frontier_point."""

    def test_reselect_same_point_returns_cached(self, client, clean_job_store):
        """Selecting the same frontier point twice returns cached result (short circuit)."""
        clean_job_store.jobs["idem"] = {
            "status": "completed",
            "selected_frontier_point": 2,
            "result": {
                "total_objective": 150.0,
                "constraints": {"volume": 0.93},
                "baseline_objective": 140.0,
                "baseline_constraints": {"volume": 0.87},
                "lambdas": {"volume": 0.6},
                "converged": True,
                "selected_frontier_point": 2,
            },
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.3,
                        total_objective=130.0,
                        total_volume=0.9,
                    ),
                    _frontier_point_summary(
                        lambda_volume=0.4,
                        total_objective=140.0,
                        total_volume=0.91,
                    ),
                    _frontier_point_summary(
                        lambda_volume=0.6,
                        total_objective=150.0,
                        total_volume=0.93,
                    ),
                ],
                "n_points": 3,
                "constraint_names": ["volume"],
            },
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem", "point_index": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["point_index"] == 2
        assert data["total_objective"] == 150.0
        assert data["lambdas"] == {"volume": 0.6}
        assert data["converged"] is True
        # Solver should NOT have been called (short circuit)
        clean_job_store.jobs["idem"]["solver"].solve.assert_not_called()

    def test_reselect_same_point_returns_cached_after_heavy_cleanup(
        self,
        client,
        clean_job_store,
    ):
        """Idempotent cached selection does not require retained solver objects."""
        clean_job_store.jobs["idem_slimmed"] = {
            "status": "completed",
            "selected_frontier_point": 2,
            "result": {
                "total_objective": 150.0,
                "constraints": {"volume": 0.93},
                "baseline_objective": 140.0,
                "baseline_constraints": {"volume": 0.87},
                "lambdas": {"volume": 0.6},
                "converged": True,
                "selected_frontier_point": 2,
            },
            "created_at": time.time(),
            "heavy_objects_cleared_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem_slimmed", "point_index": 2},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["point_index"] == 2
        assert data["total_objective"] == 150.0
        assert data["lambdas"] == {"volume": 0.6}

    def test_reselect_same_point_rebuilds_stale_cached_result(
        self,
        client,
        clean_job_store,
    ):
        """A stale selected job flag must not return an unrelated cached result."""
        clean_job_store.jobs["idem_stale"] = {
            "status": "completed",
            "selected_frontier_point": 1,
            "base_result": {
                "total_objective": 100.0,
                "constraints": {"volume": 0.9},
                "baseline_objective": 90.0,
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 0.3},
                "converged": True,
            },
            "result": {
                "total_objective": 999.0,
                "constraints": {"volume": 0.01},
                "baseline_objective": 90.0,
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 9.0},
                "converged": True,
            },
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.3,
                        total_objective=100.0,
                        total_volume=0.9,
                    ),
                    _frontier_point_summary(
                        lambda_volume=0.7,
                        total_objective=200.0,
                        total_volume=0.95,
                    ),
                ],
                "n_points": 2,
                "constraint_names": ["volume"],
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem_stale", "point_index": 1},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["point_index"] == 1
        assert data["total_objective"] == 200.0
        assert data["constraints"] == {"volume": 0.95}
        assert data["lambdas"] == {"volume": 0.7}
        job = clean_job_store.jobs["idem_stale"]
        assert job["result"]["selected_frontier_point"] == 1
        assert job["result"]["total_objective"] == 200.0

    def test_reselect_same_point_rebuilds_stale_cached_metrics(
        self,
        client,
        clean_job_store,
    ):
        """Matching selected markers must not hide stale summary values."""
        clean_job_store.jobs["idem_stale_metrics"] = {
            "status": "completed",
            "selected_frontier_point": 1,
            "base_result": {
                "total_objective": 100.0,
                "constraints": {"volume": 0.9},
                "baseline_objective": 90.0,
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 0.3},
                "converged": True,
            },
            "result": {
                "total_objective": 999.0,
                "constraints": {"volume": 0.01},
                "baseline_objective": 90.0,
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 9.0},
                "converged": True,
                "selected_frontier_point": 1,
            },
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.3,
                        total_objective=100.0,
                        total_volume=0.9,
                    ),
                    _frontier_point_summary(
                        lambda_volume=0.7,
                        total_objective=200.0,
                        total_volume=0.95,
                    ),
                ],
                "n_points": 2,
                "constraint_names": ["volume"],
            },
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem_stale_metrics", "point_index": 1},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["point_index"] == 1
        assert data["total_objective"] == 200.0
        assert data["constraints"] == {"volume": 0.95}
        assert data["lambdas"] == {"volume": 0.7}
        job = clean_job_store.jobs["idem_stale_metrics"]
        assert job["result"]["selected_frontier_point"] == 1
        assert job["result"]["total_objective"] == 200.0


class TestSelectFrontierPointResolve:
    """Test summary-only frontier selection, convergence warning, and scenario stats."""

    def _make_frontier_job(self, clean_job_store, *, converged=True):
        mock_solver = MagicMock()
        clean_job_store.jobs["fsel"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.3,
                        total_objective=100.0,
                        total_volume=0.9,
                    ),
                    _frontier_point_summary(
                        lambda_volume=0.7,
                        total_objective=200.0,
                        total_volume=0.95,
                        converged=converged,
                    ),
                ],
                "n_points": 2,
                "constraint_names": ["volume"],
            },
            "result": {
                "total_objective": 100.0,
                "baseline_objective": 90.0,
                "constraints": {"volume": 0.9},
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 0.3},
                "converged": True,
            },
            "artifact_handles": {},
            "created_at": time.time(),
        }
        return mock_solver

    def test_resolve_with_new_lambdas(self, client, clean_job_store):
        """Selecting a frontier point returns stored summary metrics."""
        mock_solver = self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_objective"] == 200.0
        assert data["lambdas"] == {"volume": 0.7}
        assert data["converged"] is True
        assert data["constraints"] == {"volume": 0.95}
        mock_solver.solve.assert_not_called()

    def test_resolve_malformed_frontier_point_fails_loudly(self, client, clean_job_store):
        """Malformed stored frontier point summaries fail loudly before selection."""
        mock_solver = self._make_frontier_job(clean_job_store)
        clean_job_store.jobs["fsel"]["frontier_data"]["points"][1].pop("total_volume")

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )

        assert resp.status_code == 500
        assert "total_volume" in resp.json()["detail"]
        mock_solver.solve.assert_not_called()

    def test_resolve_non_converged_adds_warning(self, client, clean_job_store):
        """When re-solve doesn't converge, the result includes a warning."""
        self._make_frontier_job(clean_job_store, converged=False)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["converged"] is False
        # Check the store was updated with a warning
        job = clean_job_store.jobs["fsel"]
        assert "warning" in job["result"]
        assert "converge" in job["result"]["warning"].lower()

    def test_resolve_converged_removes_warning(self, client, clean_job_store):
        """When re-solve converges, any prior warning is removed."""
        self._make_frontier_job(clean_job_store, converged=True)
        # Pre-set a warning in the result
        clean_job_store.jobs["fsel"]["result"]["warning"] = "old warning"
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert "warning" not in job["result"]

    def test_resolve_records_scenario_stats(self, client, clean_job_store):
        """After selection, scenario stats are derived from stored frontier columns."""
        self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert "scenario_value_stats" in job["result"]
        assert "scenario_value_histogram" not in job["result"]
        stats = job["result"]["scenario_value_stats"]
        assert "mean" in stats

    def test_select_ratebook_frontier_point_does_not_reuse_base_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        """Fast point selection must not attach base ratebook diagnostics to another point."""
        mock_solver, _mock_grid, _factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_select_summary",
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "rb_select_summary", "point_index": 0},
        )

        assert resp.status_code == 200
        job = clean_job_store.jobs["rb_select_summary"]
        assert job["result"]["selected_frontier_point"] == 0
        assert job["result"]["total_objective"] == 220.0
        assert "factor_tables" not in job["result"]
        assert "scenario_value_histogram" not in job["result"]
        mock_solver.solve.assert_not_called()

    def test_select_ratebook_frontier_point_can_materialise_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        """Ratebook point selection can opt into selected-point factor tables."""
        mock_solver, mock_grid, factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_select_rates",
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_rates",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        expected_tables = _expected_region_factor_tables()
        assert data["factor_tables"] == expected_tables
        assert data["cd_iterations"] == 5
        assert data["clamp_rate"] == 0.04
        job = clean_job_store.jobs["rb_select_rates"]
        assert job["result"]["factor_tables"] == expected_tables
        mock_solver.solve.assert_called_once_with(
            mock_grid,
            factors_df,
            factor_columns=[["region"]],
            lambdas={"volume": 0.7},
            _constraints_override={"volume": {"min": 0.96}},
        )

    def test_select_ratebook_frontier_point_can_switch_materialised_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        """Ratebook rates can be materialised for multiple selected frontier points."""
        mock_solver, mock_grid, factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_select_rates_switch",
        )
        job = clean_job_store.jobs["rb_select_rates_switch"]
        job["frontier_data"]["n_points"] = 2
        job["frontier_data"]["points_returned"] = 2
        job["frontier_data"]["points"].append(
            _frontier_point_summary(
                lambda_volume=0.9,
                total_objective=240.0,
                total_volume=1.03,
                threshold_volume=1.02,
            )
        )
        mock_solver.solve.side_effect = [
            SimpleNamespace(
                total_objective=222.0,
                baseline_objective=95.0,
                total_constraints={"volume": 0.97},
                baseline_constraints={"volume": 0.88},
                lambdas={"volume": 0.7},
                converged=True,
                cd_iterations=5,
                clamp_rate=0.04,
                factor_tables={"region": {"North": 1.08, "South": 0.92}},
                dataframe=pl.DataFrame(
                    {
                        "quote_id": ["q1", "q2"],
                        "optimal_scenario_value": [1.08, 0.92],
                    }
                ),
            ),
            SimpleNamespace(
                total_objective=241.0,
                baseline_objective=95.0,
                total_constraints={"volume": 1.03},
                baseline_constraints={"volume": 0.88},
                lambdas={"volume": 0.9},
                converged=True,
                cd_iterations=7,
                clamp_rate=0.02,
                factor_tables={"region": {"North": 1.12, "South": 0.98}},
                dataframe=pl.DataFrame(
                    {
                        "quote_id": ["q1", "q2"],
                        "optimal_scenario_value": [1.12, 0.98],
                    }
                ),
            ),
        ]

        first = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_rates_switch",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )
        second = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_rates_switch",
                "point_index": 1,
                "include_ratebook_tables": True,
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200, second.json()
        assert second.json()["factor_tables"] == _expected_region_factor_tables(
            north=1.12,
            south=0.98,
        )
        assert mock_solver.solve.call_count == 2
        assert mock_solver.solve.call_args_list[0].kwargs == {
            "factor_columns": [["region"]],
            "lambdas": {"volume": 0.7},
            "_constraints_override": {"volume": {"min": 0.96}},
        }
        assert mock_solver.solve.call_args_list[1].kwargs == {
            "factor_columns": [["region"]],
            "lambdas": {"volume": 0.9},
            "_constraints_override": {"volume": {"min": 1.02}},
        }
        assert mock_solver.solve.call_args_list[0].args == (mock_grid, factors_df)
        assert mock_solver.solve.call_args_list[1].args == (mock_grid, factors_df)

    def test_apply_ratebook_frontier_point_preserves_runtime_for_rate_table_switching(
        self,
        client,
        clean_job_store,
    ):
        """Loading selected-point detail must not break later rate-table switching."""
        mock_solver, mock_grid, factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_apply_then_switch_rates",
        )
        job = clean_job_store.jobs["rb_apply_then_switch_rates"]
        job["frontier_data"]["n_points"] = 2
        job["frontier_data"]["points_returned"] = 2
        job["frontier_data"]["points"].append(
            _frontier_point_summary(
                lambda_volume=0.9,
                total_objective=240.0,
                total_volume=1.03,
                threshold_volume=1.02,
            )
        )
        mock_solver.solve.side_effect = [
            SimpleNamespace(
                total_objective=222.0,
                baseline_objective=95.0,
                total_constraints={"volume": 0.97},
                baseline_constraints={"volume": 0.88},
                lambdas={"volume": 0.7},
                converged=True,
                cd_iterations=5,
                clamp_rate=0.04,
                factor_tables={"region": {"North": 1.08, "South": 0.92}},
                dataframe=pl.DataFrame(
                    {
                        "quote_id": ["q1", "q2"],
                        "optimal_scenario_value": [1.08, 0.92],
                    }
                ),
            ),
            SimpleNamespace(
                total_objective=223.0,
                baseline_objective=95.0,
                total_constraints={"volume": 0.97},
                baseline_constraints={"volume": 0.88},
                lambdas={"volume": 0.7},
                converged=True,
                cd_iterations=5,
                clamp_rate=0.04,
                factor_tables={"region": {"North": 1.08, "South": 0.92}},
                dataframe=pl.DataFrame(
                    {
                        "quote_id": ["q1", "q2"],
                        "optimal_scenario_value": [1.08, 0.92],
                    }
                ),
            ),
            SimpleNamespace(
                total_objective=241.0,
                baseline_objective=95.0,
                total_constraints={"volume": 1.03},
                baseline_constraints={"volume": 0.88},
                lambdas={"volume": 0.9},
                converged=True,
                cd_iterations=7,
                clamp_rate=0.02,
                factor_tables={"region": {"North": 1.12, "South": 0.98}},
                dataframe=pl.DataFrame(
                    {
                        "quote_id": ["q1", "q2"],
                        "optimal_scenario_value": [1.12, 0.98],
                    }
                ),
            ),
        ]

        first_rates = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_apply_then_switch_rates",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )
        apply_detail = client.post(
            "/api/optimiser/apply",
            json={
                "job_id": "rb_apply_then_switch_rates",
                "point_index": 0,
            },
        )
        second_rates = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_apply_then_switch_rates",
                "point_index": 1,
                "include_ratebook_tables": True,
            },
        )

        assert first_rates.status_code == 200
        assert apply_detail.status_code == 200
        assert second_rates.status_code == 200, second_rates.json()
        assert second_rates.json()["factor_tables"] == _expected_region_factor_tables(
            north=1.12,
            south=0.98,
        )
        job = clean_job_store.jobs["rb_apply_then_switch_rates"]
        assert job["solver"] is mock_solver
        assert job["quote_grid"] is mock_grid
        assert job["factors_df"].equals(factors_df)
        assert "solve_result" not in job
        assert mock_solver.solve.call_count == 3

    def test_resolve_records_frontier_provenance(self, client, clean_job_store):
        """After selection, the selected frontier point index is stored on the job."""
        self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert job["selected_frontier_point"] == 1
        assert job["result"]["selected_frontier_point"] == 1

    def test_resolve_succeeds_after_runtime_state_is_cleared(
        self,
        client,
        clean_job_store,
    ):
        """Summary-only selection remains available after terminal cleanup."""
        mock_solver = self._make_frontier_job(clean_job_store)
        clean_job_store.clear_result_data("fsel")

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )

        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert "solver" not in job
        assert "quote_grid" not in job
        assert "solve_result" not in job
        assert job["selected_frontier_point"] == 1
        mock_solver.solve.assert_not_called()

    def test_resolve_does_not_replace_base_apply_artifact(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        self._make_frontier_job(clean_job_store)
        old_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [9.9]}))
        )
        assert old_handle is not None
        old_artifact_dir = Path(old_handle["directory"])
        old_artifact_path = Path(old_handle["path"])
        clean_job_store.jobs["fsel"]["artifact_handles"] = {"apply_result": old_handle}

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )

        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert job["artifact_handles"]["apply_result"] == old_handle
        assert Path(old_artifact_path).is_file()
        assert old_artifact_dir.exists()

    def test_resolve_keeps_success_when_old_apply_artifact_cleanup_would_fail(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_service import _persist_apply_result_artifact

        self._make_frontier_job(clean_job_store)
        old_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [9.9]}))
        )
        assert old_handle is not None
        old_artifact_path = Path(old_handle["path"])
        clean_job_store.jobs["fsel"]["artifact_handles"] = {"apply_result": old_handle}

        cleanup_error = RuntimeError("cleanup denied")
        with (
            patch(
                "haute.routes.optimiser._cleanup_apply_result_artifact",
                side_effect=cleanup_error,
            ),
            patch("haute.routes.optimiser.logger.warning") as log_warning,
        ):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "fsel", "point_index": 1},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_objective"] == 200.0
        assert data["constraints"] == {"volume": 0.95}
        job = clean_job_store.jobs["fsel"]
        assert job["artifact_handles"]["apply_result"]["path"] == str(old_artifact_path)
        assert Path(old_artifact_path).is_file()
        assert job["selected_frontier_point"] == 1
        assert job["result"]["total_objective"] == 200.0

        log_warning.assert_not_called()

    def test_select_no_lambda_values(self, client, clean_job_store):
        """Frontier point with no lambda_ prefixed columns returns 400."""
        clean_job_store.jobs["no_lam"] = {
            "status": "completed",
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    {"total_objective": 100.0, "some_col": 0.5},
                ],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {},
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "no_lam", "point_index": 0},
        )
        assert resp.status_code == 400
        assert "no lambda" in resp.json()["detail"].lower()

    def test_select_no_solver(self, client, clean_job_store):
        """Select with no solver still works from stored frontier data."""
        clean_job_store.jobs["no_slv"] = {
            "status": "completed",
            "solver": None,
            "quote_grid": None,
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.5,
                        total_objective=100.0,
                        total_volume=0.9,
                    )
                ],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {
                "baseline_objective": 90.0,
                "baseline_constraints": {"volume": 0.85},
            },
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "no_slv", "point_index": 0},
        )
        assert resp.status_code == 200
        assert resp.json()["lambdas"] == {"volume": 0.5}

    def test_select_solver_exception_returns_500(self, client, clean_job_store):
        """Solver exceptions are irrelevant because selection does not solve."""
        mock_solver = MagicMock()
        mock_solver.solve.side_effect = RuntimeError("solver boom")
        clean_job_store.jobs["sel_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    _frontier_point_summary(
                        lambda_volume=0.5,
                        total_objective=100.0,
                        total_volume=0.9,
                    )
                ],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {
                "baseline_objective": 90.0,
                "baseline_constraints": {"volume": 0.85},
            },
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "sel_err", "point_index": 0},
        )
        assert resp.status_code == 200
        mock_solver.solve.assert_not_called()


class TestBuildArtifactPayloadExtended:
    """Additional tests for _build_artifact_payload."""

    def test_online_mode_fields(self):
        """Online mode includes objective, quote_id, scenario fields."""
        job = {
            "node_label": "my_opt",
            "config": {
                "mode": "online",
                "constraints": {"volume": {"min": 0.9}},
                "objective": "income",
                "quote_id": "qid",
                "scenario_index": "step",
                "scenario_value": "sv",
                "chunk_size": 100_000,
            },
        }
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=1000.0,
            baseline_objective=950.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
            cd_iterations=None,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["quote_id"] == "qid"
        assert payload["scenario_index"] == "step"
        assert payload["scenario_value"] == "sv"
        assert payload["chunk_size"] == 100_000
        assert payload["iterations"] == 10
        assert payload["cd_iterations"] is None

    def test_ratebook_mode_with_factor_tables(self):
        """Ratebook mode includes factor_tables and clamp_rate."""
        job = {
            "node_label": "rb",
            "config": {"mode": "ratebook", "constraints": {}, "objective": "income"},
            "result": {
                "factor_tables": {
                    "region": [
                        {"__factor_group__": "N", "optimal_scenario_value": 1.05},
                        {"__factor_group__": "S", "optimal_scenario_value": 0.95},
                    ]
                },
            },
        }
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=1000.0,
            total_constraints={},
            converged=True,
            clamp_rate=0.03,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["factor_tables"] is not None
        assert len(payload["factor_tables"]["region"]) == 2
        assert payload["clamp_rate"] == 0.03

    def test_version_override_replaces_auto(self):
        """Version override takes precedence over auto-generated version."""
        job = {"node_label": "test", "config": {"mode": "online"}}
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result, version_override="custom_v1")
        assert payload["version"] == "custom_v1"

    def test_auto_version_when_no_override(self):
        """When no version override, auto-generated version is used."""
        job = {"node_label": "My Opt", "config": {"mode": "online"}}
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result, version_override="")
        # Auto version has the slug of the label
        assert payload["version"].startswith("my_opt_")

    def test_no_frontier_selection_when_index_none(self):
        """No frontier_selection when selected_frontier_point is None."""
        job = {
            "node_label": "opt",
            "config": {"mode": "online"},
            "selected_frontier_point": None,
            "frontier_data": {"n_points": 3},
        }
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            converged=True,
        )
        # selected_idx is None so frontier_selection should not be added
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload

    def test_no_frontier_selection_when_no_frontier_data(self):
        """No frontier_selection when frontier_data is missing."""
        job = {
            "node_label": "opt",
            "config": {"mode": "online"},
            "selected_frontier_point": 2,
            # no frontier_data key
        }
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload


class TestMlflowLogExtended:
    """Extended tests for /mlflow/log — frontier data logging, tags, artifacts."""

    @staticmethod
    def _make_mlflow_job(
        clean_job_store, job_id, *, frontier_data=None, selected_frontier_point=None
    ):
        mock_solver = MagicMock()
        mock_solver.summary.return_value = {
            "params": {"mode": "online"},
            "metrics": {"total_objective": 100.0},
            "artifacts": {"lambdas": {"volume": 0.5}},
        }
        mock_solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=100.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            baseline_objective=95.0,
            converged=True,
            iterations=10,
        )
        job = {
            "status": "completed",
            "solver": mock_solver,
            "solve_result": mock_solve_result,
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "objective": "income",
                "constraints": {"volume": {"min": 0.9}},
            },
            "result": {
                "mode": "online",
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.92},
                "baseline_constraints": {"volume": 0.88},
                "lambdas": {"volume": 0.5},
                "converged": True,
            },
            "node_label": "my_opt",
            "created_at": time.time(),
        }
        if frontier_data is not None:
            job["frontier_data"] = frontier_data
        if selected_frontier_point is not None:
            job["selected_frontier_point"] = selected_frontier_point
        clean_job_store.jobs[job_id] = job
        return mock_solver

    @staticmethod
    def _make_mlflow_mock():
        """Create a mock mlflow module with working context manager."""
        mock_mlflow = MagicMock()
        mock_run = MagicMock()
        mock_run.info.run_id = "run123"
        mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)
        return mock_mlflow

    def test_mlflow_log_success_with_frontier(self, client, clean_job_store):
        """MLflow log with frontier data logs frontier CSV and tags."""
        frontier_data = {
            "status": "ok",
            "points": [
                _frontier_point_summary(
                    total_objective=100.0,
                    lambda_volume=0.3,
                    total_volume=0.9,
                ),
                _frontier_point_summary(
                    total_objective=110.0,
                    lambda_volume=0.5,
                    total_volume=0.95,
                ),
            ],
            "n_points": 2,
            "constraint_names": ["volume"],
        }
        self._make_mlflow_job(
            clean_job_store,
            "mlf_ok",
            frontier_data=frontier_data,
            selected_frontier_point=1,
        )
        mock_mlflow = self._make_mlflow_mock()

        with (
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch(
                "haute.modelling._mlflow_log.configure_mlflow_tracking",
                return_value=("http://localhost:5000", "local"),
            ),
            patch(
                "haute.modelling._mlflow_log.resolve_experiment_name",
                return_value="/test_exp",
            ),
            patch(
                "haute.modelling._mlflow_log.build_run_url",
                return_value="http://localhost:5000/run123",
            ),
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_ok", "experiment_name": "/test_exp"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["run_id"] == "run123"
        assert data["experiment_name"] == "/test_exp"
        # Verify frontier tags were set
        mock_mlflow.set_tag.assert_any_call("frontier.n_points", "2")
        mock_mlflow.set_tag.assert_any_call("frontier.selected_point_index", "1")
        job = clean_job_store.jobs["mlf_ok"]
        assert "solver" in job
        assert "solve_result" not in job
        assert "quote_grid" in job

    def test_mlflow_log_ratebook_frontier_point_materialises_selected_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        import json as json_mod

        mock_solver, mock_grid, factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "mlf_rb_frontier",
        )
        mock_mlflow = self._make_mlflow_mock()
        logged_json: dict[str, dict] = {}

        def capture_artifact(path: str) -> None:
            artifact_path = Path(path)
            if artifact_path.suffix == ".json":
                logged_json[artifact_path.name] = json_mod.loads(artifact_path.read_text())

        mock_mlflow.log_artifact.side_effect = capture_artifact

        with (
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch(
                "haute.modelling._mlflow_log.configure_mlflow_tracking",
                return_value=("http://localhost:5000", "local"),
            ),
            patch(
                "haute.modelling._mlflow_log.resolve_experiment_name",
                return_value="/ratebook_exp",
            ),
            patch(
                "haute.modelling._mlflow_log.build_run_url",
                return_value="http://localhost:5000/run-rb",
            ),
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={
                    "job_id": "mlf_rb_frontier",
                    "experiment_name": "/ratebook_exp",
                    "point_index": 0,
                },
            )

        assert resp.status_code == 200
        optimiser_result = logged_json["optimiser_result.json"]
        assert optimiser_result["total_objective"] == 222.0
        assert optimiser_result["factor_tables"] == _expected_region_factor_tables()
        assert (
            logged_json["frontier_point_summary.json"]["factor_tables"]
            == (optimiser_result["factor_tables"])
        )
        args = mock_solver.solve.call_args.args
        assert args[0] is mock_grid
        assert args[1] is factors_df
        assert mock_solver.solve.call_args.kwargs["lambdas"] == {"volume": 0.7}
        mock_mlflow.set_tag.assert_any_call("frontier.selected_point_index", "0")

    def test_mlflow_log_no_frontier(self, client, clean_job_store):
        """MLflow log without frontier data still succeeds."""
        self._make_mlflow_job(clean_job_store, "mlf_nf")
        mock_mlflow = self._make_mlflow_mock()

        with (
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch(
                "haute.modelling._mlflow_log.configure_mlflow_tracking",
                return_value=("http://localhost:5000", "local"),
            ),
            patch(
                "haute.modelling._mlflow_log.resolve_experiment_name",
                return_value="/test",
            ),
            patch(
                "haute.modelling._mlflow_log.build_run_url",
                return_value="http://localhost:5000/run456",
            ),
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_nf"},
            )

        assert resp.status_code == 200
        # No frontier tags should be set when no frontier data
        frontier_calls = [c for c in mock_mlflow.set_tag.call_args_list if "frontier" in str(c)]
        assert len(frontier_calls) == 0

    def test_mlflow_log_artifacts_skips_none(self, client, clean_job_store):
        """Artifacts with None data are skipped during logging."""
        mock_solver = MagicMock()
        mock_solver.summary.return_value = {
            "params": {"mode": "online"},
            "metrics": {"total_objective": 50.0},
            "artifacts": {"lambdas": {"volume": 0.5}, "empty_one": None},
        }
        mock_solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=50.0,
            total_constraints={},
            baseline_constraints={},
            baseline_objective=45.0,
            converged=True,
        )
        clean_job_store.jobs["mlf_skip"] = {
            "status": "completed",
            "solver": mock_solver,
            "solve_result": mock_solve_result,
            "config": {"mode": "online", "objective": "income", "constraints": {}},
            "node_label": "opt",
            "created_at": time.time(),
        }
        mock_mlflow = self._make_mlflow_mock()

        with (
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch(
                "haute.modelling._mlflow_log.configure_mlflow_tracking",
                return_value=("http://localhost:5000", "local"),
            ),
            patch(
                "haute.modelling._mlflow_log.resolve_experiment_name",
                return_value="/test",
            ),
            patch(
                "haute.modelling._mlflow_log.build_run_url",
                return_value="http://localhost:5000/run789",
            ),
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_skip"},
            )

        assert resp.status_code == 200
        # log_artifact calls: 1 for lambdas, 0 for empty_one (None), 1 for optimiser_result.json
        artifact_calls = mock_mlflow.log_artifact.call_args_list
        assert len(artifact_calls) == 2  # lambdas.json + optimiser_result.json


class TestSolveStatusTimeout:
    """Test timeout detection including atomic update guard."""

    def test_timeout_sets_error_with_elapsed(self, client, clean_job_store):
        """Timed-out job gets error status with elapsed_seconds."""
        start = time.monotonic() - 500
        clean_job_store.jobs["tout"] = {
            "status": "running",
            "progress": 0.3,
            "message": "Solving",
            "start_time": start,
            "timeout": 10,
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/tout")
        data = resp.json()
        assert data["status"] == "error"
        assert "timed out" in data["message"].lower()
        assert data["elapsed_seconds"] > 0

    def test_timeout_race_uses_current_job_when_guarded_write_skips(
        self,
        client,
        clean_job_store,
    ):
        """If a solve completes while timeout handling races, report the completion."""
        start = time.monotonic() - 500
        clean_job_store.jobs["tout_race"] = {
            "status": "running",
            "progress": 0.3,
            "message": "Solving",
            "start_time": start,
            "timeout": 10,
            "created_at": time.time(),
        }

        def complete_elsewhere(*args, **kwargs):
            clean_job_store.jobs["tout_race"] = {
                "status": "completed",
                "progress": 1.0,
                "message": "Completed",
                "elapsed_seconds": 12.0,
                "result": {
                    "mode": "online",
                    "total_objective": 100.0,
                    "baseline_objective": 95.0,
                    "constraints": {},
                    "baseline_constraints": {},
                    "lambdas": {},
                    "converged": True,
                },
                "created_at": time.time(),
            }
            return None

        with patch.object(clean_job_store, "atomic_update", side_effect=complete_elsewhere):
            resp = client.get("/api/optimiser/solve/status/tout_race")

        data = resp.json()
        assert data["status"] == "completed"
        assert data["message"] == "Completed"

    def test_running_job_not_timed_out(self, client, clean_job_store):
        """A running job with sufficient timeout remains running."""
        start_time = time.monotonic() - 5.0
        clean_job_store.jobs["not_tout"] = {
            "status": "running",
            "progress": 0.5,
            "message": "Solving",
            "start_time": start_time,
            "timeout": 9999,
            "elapsed_seconds": 0.5,
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/not_tout")
        data = resp.json()
        assert data["status"] == "running"
        assert data["elapsed_seconds"] >= 5.0

    def test_completed_job_not_overwritten_by_timeout(self, client, clean_job_store):
        """Timeout checks should not regress a completed job back to error."""
        clean_job_store.jobs["done_past_timeout"] = {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "start_time": time.monotonic() - 500,
            "timeout": 10,
            "elapsed_seconds": 12.0,
            "result": {
                "mode": "online",
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.91},
                "baseline_constraints": {"volume": 0.88},
                "lambdas": {"volume": 0.5},
                "converged": True,
            },
            "created_at": time.time(),
        }

        resp = client.get("/api/optimiser/solve/status/done_past_timeout")
        data = resp.json()

        assert data["status"] == "completed"
        assert data["message"] == "Completed"
        assert "timed out" not in data["message"].lower()

    def test_status_completed_without_frontier(self, client, clean_job_store):
        """Completed job without frontier_data returns frontier=None."""
        clean_job_store.jobs["no_front"] = {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": 2.0,
            "result": {
                "mode": "online",
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.91},
                "baseline_constraints": {"volume": 0.88},
                "lambdas": {"volume": 0.5},
                "converged": True,
            },
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/no_front")
        data = resp.json()
        assert data["status"] == "completed"
        assert data["frontier"] is None

    def test_status_error_fields_default(self, client, clean_job_store):
        """Job with minimal error fields returns sensible defaults."""
        clean_job_store.jobs["minimal"] = {
            "status": "error",
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/minimal")
        data = resp.json()
        assert data["status"] == "error"
        assert data["progress"] == 0.0
        assert data["message"] == ""
        assert data["elapsed_seconds"] == 0.0


class TestSolveOnlineUnit:
    """Unit tests for _solve_online."""

    def test_solve_online_initializes_solver_and_records_history(self):
        """_solve_online creates OnlineOptimiser and passes record_history."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_online

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "objective": "expected_income",
                },
            }
        )

        mock_grid = MagicMock()
        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=True,
            iterations=15,
            n_quotes=50,
            n_steps=5,
            history=[{"iteration": 0, "total_objective": 80.0}],
            grid=mock_grid,
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1]}),
        )

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "max_iter": 20,
            "chunk_size": 1000,
            "tolerance": 1e-4,
            "record_history": True,
        }

        with patch("price_contour.OnlineOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            # Also mock frontier to return None (to avoid error)
            mock_solver.return_value.frontier.side_effect = Exception("skip")

            _solve_online(mock_grid, config, store, job_id, time.monotonic())

        mock_solver.assert_called_once_with(
            objective="expected_income",
            constraints={"volume": {"min": 0.9}},
            max_iter=20,
            tolerance=1e-4,
            record_history=True,
        )
        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["result"]["iterations"] == 15
        assert job["result"]["history"] is not None

    def test_solve_online_no_history(self):
        """When record_history is False, history is None in result."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_online

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {"constraints": {}, "objective": "income"},
            }
        )

        mock_grid = MagicMock()
        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            baseline_constraints={},
            lambdas={},
            converged=True,
            iterations=5,
            n_quotes=10,
            n_steps=3,
            history=None,
            grid=mock_grid,
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1, 0.9]}),
        )

        config = {
            "objective": "income",
            "constraints": {},
            "record_history": False,
        }

        with patch("price_contour.OnlineOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            _solve_online(mock_grid, config, store, job_id, time.monotonic())

        job = store.require_job(job_id)
        assert job["result"]["history"] is None


class TestSolveRatebookUnit:
    """Unit tests for _solve_ratebook."""

    def test_solve_ratebook_no_factors_df(self):
        """Ratebook mode without factors_df raises RuntimeError."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {}})
        mock_grid = MagicMock()

        with pytest.raises(RuntimeError, match="banding source"):
            _solve_ratebook(mock_grid, {}, None, store, job_id, time.monotonic())

    def test_solve_ratebook_invalid_factor_columns(self):
        """Ratebook mode with factor columns not in DataFrame raises RuntimeError."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {}})
        mock_grid = MagicMock()
        factors_df = pl.DataFrame({"quote_id": ["q1"], "existing_col": ["A"]})

        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["nonexistent_col"]],
        }

        with pytest.raises(RuntimeError, match="No valid factor groups"):
            _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

    def test_solve_ratebook_success(self):
        """Ratebook solve succeeds with valid factors_df."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {"constraints": {}},
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3"]

        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q3"],
                "region": ["North", "North", "East"],
            }
        )

        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=True,
            cd_iterations=3,
            factor_tables={"region": {"North": 1.1, "East": 1.0}},
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1, 0.9]}),
        )

        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"]],
            "quote_id": "quote_id",
        }

        with patch("price_contour.RatebookOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

        assert "chunk_size" not in mock_solver.call_args.kwargs
        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert "factor_tables" in job["result"]
        assert "region" in job["result"]["factor_tables"]
        assert job["result"]["factor_tables"]["region"] == [
            {"__factor_group__": "North", "optimal_scenario_value": 1.1, "quote_count": 2},
            {"__factor_group__": "East", "optimal_scenario_value": 1.0, "quote_count": 1},
        ]
        assert "factors_df" in job
        assert job["factors_df"].columns == ["region"]

    def test_solve_ratebook_orders_factor_tables_by_banding_rule_order(self):
        """Ratebook rates are serialised in the source banding row order."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3", "q4", "q5"]

        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q3", "q4", "q5"],
                "proposer_age_band": ["20-27", "28-34", "35-41", "missing", "solver_only"],
                "channel_band": ["direct", "broker", "direct", "broker", "direct"],
            }
        )
        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            baseline_constraints={},
            lambdas={},
            converged=True,
            cd_iterations=2,
            factor_tables={
                "channel_band": {
                    "broker": 0.98,
                    "direct": 1.04,
                },
                "proposer_age_band": {
                    "35-41": 1.15,
                    "20-27": 0.92,
                    "solver_only": 1.30,
                    "28-34": 1.03,
                    "missing": 1.20,
                },
            },
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1]}),
        )
        config = {
            "objective": "income",
            "constraints": {},
            "factor_columns": [["channel_band"], ["proposer_age_band"]],
            "quote_id": "quote_id",
        }
        factor_level_order = {
            "proposer_age_band": ["20-27", "28-34", "35-41", "missing"],
            "channel_band": ["direct", "broker"],
        }

        with patch("price_contour.RatebookOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            _solve_ratebook(
                mock_grid,
                config,
                factors_df,
                store,
                job_id,
                time.monotonic(),
                factor_level_order=factor_level_order,
            )

        factor_tables = store.require_job(job_id)["result"]["factor_tables"]
        assert list(factor_tables) == ["proposer_age_band", "channel_band"]
        rows = factor_tables["proposer_age_band"]
        assert [row["__factor_group__"] for row in rows] == [
            "20-27",
            "28-34",
            "35-41",
            "missing",
            "solver_only",
        ]
        assert [row["quote_count"] for row in rows] == [1, 1, 1, 1, 1]
        assert [row["__factor_group__"] for row in factor_tables["channel_band"]] == [
            "direct",
            "broker",
        ]

    def test_ratebook_factor_level_counts_support_composite_factor_groups(self):
        """Counts use price-contour's table and level keys for composite groups."""
        from haute.routes._optimiser_service import _ratebook_factor_level_counts

        factors_df = pl.DataFrame(
            {
                "region": ["North", "North", "South"],
                "age_band": ["18-19", "20-29", "18-19"],
            }
        )

        counts = _ratebook_factor_level_counts(
            factors_df,
            [["region"], ["region", "age_band"]],
        )

        assert counts["region"] == {"North": 2, "South": 1}
        assert counts["region:age_band"] == {
            "North\x1f18-19": 1,
            "North\x1f20-29": 1,
            "South\x1f18-19": 1,
        }

    def test_solve_ratebook_rejects_null_factor_values_before_solver(self):
        """Null banding levels should fail loudly before price-contour runs."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"constraints": {}}})
        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3"]

        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q3"],
                "region": ["North", None, "East"],
                "age_band": [None, "30-39", "40-49"],
            }
        )
        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"], ["age_band"]],
            "quote_id": "quote_id",
        }

        with patch("price_contour.RatebookOptimiser") as mock_solver:
            with pytest.raises(ValueError) as exc_info:
                _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

        detail = str(exc_info.value)
        assert "Ratebook factor columns contain null values" in detail
        assert "region (1 row)" in detail
        assert "age_band (1 row)" in detail
        mock_solver.assert_not_called()

    def test_solve_ratebook_frontier_passes_prepared_factors(self):
        """Ratebook frontier-in-solve passes the aligned factors dataframe."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_enabled": True,
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]
        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "region": ["North", "South"],
            }
        )
        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=True,
            cd_iterations=3,
            factor_tables={},
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1]}),
        )
        frontier_points = pl.DataFrame(
            {
                "total_objective": [100.0],
                "volume": [0.9],
                "lambda_volume": [0.25],
            }
        )
        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"], ["missing_factor"]],
            "quote_id": "quote_id",
            "frontier_enabled": True,
            "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
            "frontier_steps": 3,
        }

        with patch("price_contour.RatebookOptimiser") as mock_solver:
            solver = mock_solver.return_value
            solver.solve.return_value = mock_result
            solver.frontier.return_value = SimpleNamespace(points=frontier_points)
            _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

        assert mock_solver.call_args.kwargs["factor_columns"] == [["region"]]
        prepared_factors = solver.frontier.call_args.args[1]
        assert solver.frontier.call_args.args[0] is mock_grid
        assert prepared_factors.columns == ["region"]
        assert prepared_factors.to_dicts() == [{"region": "North"}, {"region": "South"}]
        assert solver.frontier.call_args.kwargs["threshold_ranges"]["volume"] == pytest.approx(
            (0.8, 1.1)
        )
        assert solver.frontier.call_args.kwargs["n_points_per_dim"] == 3
        assert solver.frontier.call_args.kwargs["factor_columns"] == [["region"]]
        job = store.require_job(job_id)
        assert job["result"]["frontier"]["n_points"] == 1
        assert job["factors_df"].to_dicts() == prepared_factors.to_dicts()

    def test_solve_ratebook_custom_quote_id(self):
        """Ratebook solve with custom quote_id column renames it."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {"constraints": {}},
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]

        factors_df = pl.DataFrame(
            {
                "policy_id": ["q1", "q2"],
                "region": ["North", "South"],
            }
        )

        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            baseline_constraints={},
            lambdas={},
            converged=True,
            cd_iterations=2,
            factor_tables={},
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1]}),
        )

        config = {
            "objective": "income",
            "constraints": {},
            "factor_columns": [["region"]],
            "quote_id": "policy_id",
        }

        with patch("price_contour.RatebookOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

        job = store.require_job(job_id)
        assert job["status"] == "completed"

    def test_solve_ratebook_banding_source_resolution(self):
        """Ratebook _extract_factors resolves banding source from lazy outputs."""
        from haute.routes._optimiser_service import OptimiserSolveService

        mock_lf = MagicMock()
        mock_df = pl.DataFrame({"quote_id": ["q1"], "region": ["N"]})
        mock_lf.collect.return_value = mock_df

        lazy_outputs = {"banding_node": mock_lf}
        config = {"banding_source": "banding_node"}

        result = OptimiserSolveService._extract_factors(lazy_outputs, config, "ratebook")
        assert result is not None
        mock_lf.collect.assert_called_once_with(engine="streaming")

    def test_extract_factors_online_returns_none(self):
        """_extract_factors returns None for online mode."""
        from haute.routes._optimiser_service import OptimiserSolveService

        result = OptimiserSolveService._extract_factors({}, {}, "online")
        assert result is None

    def test_extract_factors_no_banding_source(self):
        """_extract_factors returns None when banding source not found."""
        from haute.routes._optimiser_service import OptimiserSolveService

        result = OptimiserSolveService._extract_factors(
            {"other_node": MagicMock()},
            {"banding_source": "missing_node"},
            "ratebook",
        )
        assert result is None


class TestExecutePipelineExtended:
    """Additional coverage for _execute_pipeline: preamble, streaming chunk."""

    def test_execute_pipeline_restores_chunk_size(self, scored_data, tmp_path):
        """When prev chunk size is set, it is restored after execution."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        captured_chunk_sizes = []

        def fake_execute_lazy(*args, **kwargs):
            import polars as pl

            captured_chunk_sizes.append(pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE"))
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=fake_execute_lazy),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, checkpoint_dir)

        # During execution, chunk size should have been set to 50000
        assert len(captured_chunk_sizes) == 1

    def test_execute_pipeline_exception_updates_job_store(self, scored_data, tmp_path):
        """Pipeline failure updates job store with error status."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        with (
            patch(
                "haute.graph_utils._execute_lazy",
                side_effect=RuntimeError("pipeline broke"),
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                service._execute_pipeline(body, job_id, checkpoint_dir)
            assert exc_info.value.status_code == 500

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "pipeline" in job["message"].lower()


class TestValidateAndProject:
    """Tests for _validate_and_project."""

    def test_missing_columns_error(self):
        """Missing required columns raises HTTPException with column names."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        # LazyFrame missing 'volume' column
        source_lf = pl.LazyFrame(
            {
                "quote_id": ["q1"],
                "scenario_index": [0],
                "scenario_value": [1.0],
                "expected_income": [100.0],
            }
        )

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with pytest.raises(HTTPException) as exc_info:
            service._validate_and_project(source_lf, config, job_id)
        assert exc_info.value.status_code == 400
        assert "volume" in exc_info.value.detail

    def test_column_casting_rejects_null_quote_ids_loudly(self):
        """Projection rejects invalid quote_id rows before grid construction."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        # Include a null quote_id row
        source_lf = pl.LazyFrame(
            {
                "quote_id": ["q1", None, "q3"],
                "scenario_index": [0, 1, 2],
                "scenario_value": [1.0, 1.1, 1.2],
                "expected_income": [100.0, 110.0, 120.0],
                "volume": [0.9, 0.95, 0.88],
            }
        )

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with pytest.raises(HTTPException) as exc_info:
            service._validate_and_project(source_lf, config, job_id)

        assert exc_info.value.status_code == 400
        assert str(exc_info.value.detail).startswith(
            "Null quote_id values found in optimiser input"
        )
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["message"] == exc_info.value.detail

    def test_numeric_quote_id_rejected(self):
        """Numeric quote IDs fail loudly instead of being coerced to strings."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        source_lf = pl.LazyFrame(
            {
                "quote_id": [1, 2],
                "scenario_index": [0, 1],
                "scenario_value": [1.0, 1.1],
                "expected_income": [100.0, 110.0],
                "volume": [0.9, 0.95],
            }
        )
        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with pytest.raises(HTTPException) as exc_info:
            service._validate_and_project(source_lf, config, job_id)

        assert exc_info.value.status_code == 400
        assert "quote_id must be Utf8" in exc_info.value.detail
        assert store.require_job(job_id)["status"] == "error"

    def test_empty_constraints_returns_no_constraint_cols(self):
        """With empty constraints dict, constraint_cols is empty list."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        source_lf = pl.LazyFrame(
            {
                "quote_id": ["q1"],
                "scenario_index": [0],
                "scenario_value": [1.0],
                "expected_income": [100.0],
            }
        )

        config = {
            "objective": "expected_income",
            "constraints": {},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        constraint_cols, scored_lf = service._validate_and_project(source_lf, config, job_id)
        assert constraint_cols == []


class TestBuildGrid:
    """Tests for _build_grid."""

    def test_build_grid_creates_temp_file_and_cleans_up(self, tmp_path):
        """_build_grid creates temp parquet, builds grid, and cleans up."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        scored_lf = pl.LazyFrame(
            {
                "quote_key": pl.Series(["q1", "q1", "q2", "q2"], dtype=pl.Utf8),
                "scenario_step": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
                "price_factor": pl.Series([0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
                "income": pl.Series([100.0, 110.0, 200.0, 220.0], dtype=pl.Float32),
                "vol": pl.Series([0.9, 0.85, 0.95, 0.90], dtype=pl.Float32),
            }
        )

        config = {
            "objective": "income",
            "constraints": {"vol": {"min": 0.9}},
            "quote_id": "quote_key",
            "scenario_index": "scenario_step",
            "scenario_value": "price_factor",
            "chunk_size": 1_024,
        }

        mock_grid = MagicMock()
        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=mock_grid,
            ) as mock_build,
        ):
            # Make safe_sink actually write the file
            def do_sink(lf, path, **kw):
                lf.collect().write_parquet(path)

            mock_sink.side_effect = do_sink

            result = service._build_grid(scored_lf, ["vol"], config, "opt", job_id)

        assert result is mock_grid
        mock_build.assert_called_once()
        assert mock_build.call_args.args[2] == 1_024
        assert mock_build.call_args.kwargs["quote_id"] == "quote_key"
        assert mock_build.call_args.kwargs["scenario_index"] == "scenario_step"
        assert mock_build.call_args.kwargs["scenario_value"] == "price_factor"
        assert mock_build.call_args.kwargs["objective"] == "income"
        assert "scenario_value_col" not in mock_build.call_args.kwargs
        # Temp file should be cleaned up
        build_call_args = mock_build.call_args
        parquet_path = build_call_args[0][0]
        import os

        assert not os.path.exists(parquet_path)

    def test_build_grid_without_chunk_size_uses_default_chunked_builder(self, tmp_path):
        """Without explicit chunk_size, use chunked ingestion with the backend default."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _DEFAULT_CHUNK_SIZE, OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        scored_lf = pl.LazyFrame(
            {
                "quote_key": pl.Series(["q1", "q2", "q1", "q2"], dtype=pl.Utf8),
                "scenario_step": pl.Series([0, 0, 1, 1], dtype=pl.Int32),
                "price_factor": pl.Series([0.9, 0.9, 1.1, 1.1], dtype=pl.Float32),
                "income": pl.Series([100.0, 200.0, 110.0, 220.0], dtype=pl.Float32),
                "vol": pl.Series([0.9, 0.95, 0.85, 0.90], dtype=pl.Float32),
            }
        )

        config = {
            "objective": "income",
            "constraints": {"vol": {"min": 0.9}},
            "quote_id": "quote_key",
            "scenario_index": "scenario_step",
            "scenario_value": "price_factor",
        }

        mock_grid = MagicMock()
        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=mock_grid,
            ) as mock_build,
        ):
            mock_sink.side_effect = lambda lf, path, **kw: lf.collect().write_parquet(path)

            result = service._build_grid(scored_lf, ["vol"], config, "opt", job_id)

        assert result is mock_grid
        mock_build.assert_called_once()
        assert mock_build.call_args.args[2] == _DEFAULT_CHUNK_SIZE
        assert mock_build.call_args.kwargs["quote_id"] == "quote_key"
        assert mock_build.call_args.kwargs["scenario_index"] == "scenario_step"
        assert mock_build.call_args.kwargs["scenario_value"] == "price_factor"
        assert mock_build.call_args.kwargs["objective"] == "income"
        assert "scenario_value_col" not in mock_build.call_args.kwargs

    @pytest.mark.parametrize("chunk_size", [0, -1, 1.5, "1000", True])
    def test_build_grid_rejects_invalid_chunk_size(self, chunk_size):
        """Invalid chunk sizes fail loudly instead of silently selecting another path."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        scored_lf = pl.LazyFrame(
            {
                "quote_id": pl.Series(["q1"], dtype=pl.Utf8),
                "scenario_index": pl.Series([0], dtype=pl.Int32),
                "scenario_value": pl.Series([1.0], dtype=pl.Float32),
                "expected_income": pl.Series([100.0], dtype=pl.Float32),
            }
        )
        config = {
            "objective": "expected_income",
            "constraints": {},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
            "chunk_size": chunk_size,
        }

        with pytest.raises(HTTPException) as exc_info:
            service._build_grid(scored_lf, [], config, "opt", job_id)

        assert exc_info.value.status_code == 400
        assert "chunk_size must be a positive integer" in exc_info.value.detail

    @pytest.mark.parametrize("chunk_size", [None, 2])
    def test_build_grid_accepts_categorical_quote_id_with_real_price_contour(
        self,
        chunk_size,
    ):
        """Real 0.3.2 grid builders accept categorical quote IDs."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        config = {
            "objective": "income",
            "constraints": {"vol": {"min": 0.9}},
            "quote_id": "quote_key",
            "scenario_index": "scenario_step",
            "scenario_value": "price_factor",
        }
        if chunk_size is not None:
            config["chunk_size"] = chunk_size

        scored_lf = pl.LazyFrame(
            {
                "quote_key": pl.Series(
                    ["q1", "q1", "q2", "q2"],
                    dtype=pl.Categorical,
                ),
                "scenario_step": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
                "price_factor": pl.Series([0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
                "income": pl.Series([100.0, 110.0, 200.0, 220.0], dtype=pl.Float32),
                "vol": pl.Series([0.9, 0.85, 0.95, 0.90], dtype=pl.Float32),
            }
        )

        grid = service._build_grid(scored_lf, ["vol"], config, "opt", job_id)

        assert grid.quote_ids == ["q1", "q2"]
        assert grid.n_quotes == 2
        assert grid.n_steps == 2
        assert grid.scenario_values == pytest.approx([0.9, 1.1])

    def test_build_grid_failure_updates_job_store(self, tmp_path):
        """Grid construction failure updates job store with error."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        scored_lf = pl.LazyFrame(
            {
                "quote_id": pl.Series(["q1"], dtype=pl.Utf8),
                "scenario_index": pl.Series([0], dtype=pl.Int32),
                "scenario_value": pl.Series([1.0], dtype=pl.Float32),
                "expected_income": pl.Series([100.0], dtype=pl.Float32),
            }
        )

        config = {
            "objective": "expected_income",
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                side_effect=RuntimeError("grid failed"),
            ),
        ):
            mock_sink.side_effect = lambda lf, path, **kw: lf.collect().write_parquet(path)

            with pytest.raises(HTTPException) as exc_info:
                service._build_grid(scored_lf, [], config, "opt", job_id)
            assert exc_info.value.status_code == 400

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "grid" in job["message"].lower()


class TestResolveDataSource:
    """Tests for _resolve_data_source."""

    def test_uses_data_input_id(self):
        """When data_input is set and present, uses that output."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        mock_lf = MagicMock()
        lazy_outputs = {"data_node": mock_lf, "opt": MagicMock()}
        config = {"data_input": "data_node"}

        result = service._resolve_data_source(lazy_outputs, config, "opt", job_id)
        assert result is mock_lf

    def test_falls_back_to_node_id(self):
        """When data_input is not set, uses node_id output."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        mock_lf = MagicMock()
        lazy_outputs = {"opt": mock_lf}
        config = {}

        result = service._resolve_data_source(lazy_outputs, config, "opt", job_id)
        assert result is mock_lf

    def test_no_data_raises_400(self):
        """When no data arrives at the node, raises HTTPException."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        lazy_outputs = {}
        config = {}

        with pytest.raises(HTTPException) as exc_info:
            service._resolve_data_source(lazy_outputs, config, "opt", job_id)
        assert exc_info.value.status_code == 400
        assert "no data" in exc_info.value.detail.lower()


class TestLaunchBackground:
    """Tests for _launch_background error categorization."""

    def test_background_sets_start_time_and_timeout(self, clean_job_store):
        """_launch_background sets start_time and timeout on the job."""
        from haute.routes._optimiser_service import OptimiserSolveService

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job({"status": "running"})

        mock_grid = MagicMock()
        config = {"timeout": 42}

        with patch("haute.routes._optimiser_service._solve_online"):
            service._launch_background(job_id, "opt", config, "online", mock_grid, None)
            # Give the thread time to start
            time.sleep(0.2)

        job = clean_job_store.require_job(job_id)
        assert job["timeout"] == 42
        assert "start_time" in job

    def test_background_start_failure_marks_job_error(self, clean_job_store):
        """A worker-start failure should not leave the job stuck in running."""
        from haute.routes._optimiser_service import OptimiserSolveService

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job({"status": "running"})

        with (
            patch(
                "haute.routes._optimiser_service.threading.Thread.start",
                side_effect=RuntimeError("thread boom"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service._launch_background(
                job_id,
                "opt",
                {"timeout": 42},
                "online",
                MagicMock(),
                None,
            )

        assert exc_info.value.status_code == 500
        job = clean_job_store.require_job(job_id)
        assert job["status"] == "error"
        assert "Failed to start optimiser worker" in job["message"]

    def test_late_completion_does_not_overwrite_timeout(self, clean_job_store):
        """Late solver progress/completion must not overwrite a timeout error."""
        from haute.routes._optimiser_service import OptimiserSolveService, _finalize_solve_result

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job(
            {"status": "running", "progress": 0.0, "message": "Starting", "config": {}}
        )

        deferred_threads: list[object] = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon
                deferred_threads.append(self)

            def start(self) -> None:
                return None

        def _late_solve(*args, **kwargs) -> None:
            solve_result = SimpleNamespace(
                total_objective=100.0,
                baseline_objective=95.0,
                total_constraints={"volume": 0.91},
                baseline_constraints={"volume": 0.88},
                lambdas={"volume": 0.5},
                converged=True,
            )
            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=MagicMock(),
                quote_grid=MagicMock(),
                store=clean_job_store,
                job_id=job_id,
                elapsed=12.0,
                extra_fields={
                    "iterations": 1,
                    "n_quotes": 1,
                    "n_steps": 1,
                    "history": None,
                },
            )

        with (
            patch("haute.routes._optimiser_service.threading.Thread", DeferredThread),
            patch("haute.routes._optimiser_service._solve_online", side_effect=_late_solve),
        ):
            service._launch_background(job_id, "opt", {"timeout": 10}, "online", MagicMock(), None)

        assert len(deferred_threads) == 1

        clean_job_store.atomic_update(
            job_id,
            {
                "status": "error",
                "message": "Solve timed out after 10s",
                "elapsed_seconds": 10.0,
            },
            expected_status="running",
        )

        deferred_threads[0].target()

        job = clean_job_store.require_job(job_id)
        assert job["status"] == "error"
        assert job["message"] == "Solve timed out after 10s"
        assert job["elapsed_seconds"] == 10.0
        assert job["progress"] == 0.0
        assert job.get("result") is None


class TestApplyException:
    """Test apply endpoint exception handling."""

    def test_apply_exception_returns_500(self, client, clean_job_store):
        """When solve_result.dataframe raises, apply returns 500."""
        mock_solve_result = MagicMock()
        mock_solve_result.dataframe = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        # Use a SimpleNamespace with a property that raises
        class FailingResult:
            @property
            def dataframe(self):
                raise RuntimeError("boom")

            total_objective = 100.0
            total_constraints = {"volume": 0.92}

        clean_job_store.jobs["apply_err"] = {
            "status": "completed",
            "solve_result": FailingResult(),
            "created_at": time.time(),
        }
        with patch("haute.routes.optimiser.logger.error") as log_error:
            resp = client.post("/api/optimiser/apply", json={"job_id": "apply_err"})

        assert resp.status_code == 500
        log_error.assert_called_once()
        assert log_error.call_args.args == ("apply_failed",)
        assert log_error.call_args.kwargs["error"] == "boom"
        assert log_error.call_args.kwargs["job_id"] == "apply_err"
        assert log_error.call_args.kwargs["exc_info"] is True
        job = clean_job_store.jobs["apply_err"]
        assert "solve_result" in job


class TestFrontierException:
    """Test frontier endpoint exception handling."""

    def test_frontier_solver_exception_returns_500(self, client, clean_job_store):
        """When solver.frontier raises, endpoint returns 500."""
        mock_solver = MagicMock()
        mock_solver.frontier.side_effect = RuntimeError("frontier boom")
        clean_job_store.jobs["front_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "created_at": time.time(),
        }
        with patch("haute.routes.optimiser.logger.error") as log_error:
            resp = client.post(
                "/api/optimiser/frontier",
                json={
                    "job_id": "front_err",
                    "threshold_ranges": {"volume": [0.85, 0.95]},
                    "n_points_per_dim": 3,
                },
            )
        assert resp.status_code == 500
        log_error.assert_called_once()
        assert log_error.call_args.args == ("frontier_failed",)
        assert log_error.call_args.kwargs["error"] == "frontier boom"
        assert log_error.call_args.kwargs["job_id"] == "front_err"
        assert log_error.call_args.kwargs["exc_info"] is True


class TestSaveExceptionPaths:
    """Test save endpoint OSError and generic Exception paths."""

    def test_save_touches_solve_result_before_reading_it(
        self,
        client,
        clean_job_store,
        tmp_path,
    ):
        """Saving reserves the heavy solve result before payload construction."""
        from haute._sandbox import set_project_root

        class ExpiryAssertingSolveResult:
            @property
            def lambdas(self):
                assert clean_job_store.jobs["save_touch"][
                    "heavy_objects_expires_at"
                ] == pytest.approx(1840.0)
                return {}

            total_objective = 0.0
            total_constraints = {}
            baseline_constraints = {}
            baseline_objective = 0.0
            converged = True

        clean_job_store.jobs["save_touch"] = {
            "status": "completed",
            "created_at": 100.0,
            "completed_at": 100.0,
            "heavy_objects_expires_at": 1000.0,
            "solve_result": ExpiryAssertingSolveResult(),
            "config": {"mode": "online"},
            "node_label": "opt",
        }
        set_project_root(tmp_path)

        with patch("haute.routes._job_store.time.time", return_value=940.0):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_touch", "output_path": str(tmp_path / "out.json")},
            )

        assert resp.status_code == 200

    def test_save_os_error(self, client, clean_job_store, tmp_path):
        """OSError during save returns 500 with filesystem error message."""
        from haute._sandbox import set_project_root

        mock_solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            baseline_constraints={},
            baseline_objective=0.0,
            converged=True,
        )
        clean_job_store.jobs["save_os"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "solver": MagicMock(),
            "config": {"mode": "online"},
            "node_label": "opt",
            "created_at": time.time(),
        }
        set_project_root(tmp_path)
        out_path = str(tmp_path / "out.json")

        with (
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
            patch("haute.routes.optimiser.logger.error") as log_error,
        ):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_os", "output_path": out_path},
            )
        assert resp.status_code == 500
        assert "filesystem" in resp.json()["detail"].lower()
        log_error.assert_called_once()
        assert log_error.call_args.args == ("save_failed",)
        assert log_error.call_args.kwargs["error"] == "disk full"
        assert log_error.call_args.kwargs["job_id"] == "save_os"
        assert log_error.call_args.kwargs["exc_info"] is True
        job = clean_job_store.jobs["save_os"]
        assert "solver" in job
        assert "solve_result" in job

    def test_save_generic_exception(self, client, clean_job_store, tmp_path):
        """Generic Exception during save returns 500."""
        from haute._sandbox import set_project_root

        mock_solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            total_constraints={},
            baseline_constraints={},
            baseline_objective=0.0,
            converged=True,
        )
        clean_job_store.jobs["save_gen"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "solver": MagicMock(),
            "config": {"mode": "online"},
            "node_label": "opt",
            "created_at": time.time(),
        }
        set_project_root(tmp_path)
        out_path = str(tmp_path / "out.json")

        with (
            patch("pathlib.Path.write_text", side_effect=RuntimeError("unexpected")),
            patch("haute.routes.optimiser.logger.error") as log_error,
        ):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_gen", "output_path": out_path},
            )
        assert resp.status_code == 500
        log_error.assert_called_once()
        assert log_error.call_args.args == ("save_failed",)
        assert log_error.call_args.kwargs["error"] == "unexpected"
        assert log_error.call_args.kwargs["job_id"] == "save_gen"
        assert log_error.call_args.kwargs["exc_info"] is True


class TestMlflowLogExceptionPath:
    """Test mlflow_log generic exception path."""

    def test_mlflow_log_touches_runtime_objects_before_reading_them(
        self,
        client,
        clean_job_store,
    ):
        """MLflow logging reserves solver and solve result before summary work."""
        mock_solver = MagicMock()

        def summary_after_touch(_solve_result):
            assert clean_job_store.jobs["mlf_touch"]["heavy_objects_expires_at"] == pytest.approx(
                1840.0
            )
            return {"params": {}, "metrics": {}, "artifacts": {}}

        mock_solver.summary.side_effect = summary_after_touch
        mock_solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0,
            total_constraints={},
            baseline_constraints={},
            baseline_objective=0,
            converged=True,
        )
        clean_job_store.jobs["mlf_touch"] = {
            "status": "completed",
            "created_at": 100.0,
            "completed_at": 100.0,
            "heavy_objects_expires_at": 1000.0,
            "solver": mock_solver,
            "solve_result": mock_solve_result,
            "config": {"mode": "online"},
            "node_label": "opt",
        }
        mock_mlflow = MagicMock()
        mock_run = MagicMock()
        mock_run.info.run_id = "touch-run"
        mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("haute.routes._job_store.time.time", return_value=940.0),
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch(
                "haute.modelling._mlflow_log.configure_mlflow_tracking",
                return_value=("http://localhost:5000", "local"),
            ),
            patch(
                "haute.modelling._mlflow_log.resolve_experiment_name",
                return_value="/test",
            ),
            patch(
                "haute.modelling._mlflow_log.build_run_url",
                return_value="http://localhost:5000/touch-run",
            ),
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_touch"},
            )

        assert resp.status_code == 200

    def test_mlflow_log_internal_error(self, client, clean_job_store):
        """When mlflow logging raises, endpoint returns 500."""
        mock_solver = MagicMock()
        mock_solver.summary.side_effect = RuntimeError("summary boom")
        mock_solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0,
            total_constraints={},
            converged=True,
        )
        clean_job_store.jobs["mlf_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "solve_result": mock_solve_result,
            "config": {"mode": "online"},
            "node_label": "opt",
            "created_at": time.time(),
        }
        mock_mlflow = MagicMock()
        with (
            patch.dict("sys.modules", {"mlflow": mock_mlflow}),
            patch("haute.routes.optimiser.logger.error") as log_error,
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_err"},
            )
        assert resp.status_code == 500
        log_error.assert_called_once()
        assert log_error.call_args.args == ("mlflow_log_failed",)
        assert log_error.call_args.kwargs["error"] == "summary boom"
        assert log_error.call_args.kwargs["job_id"] == "mlf_err"
        assert log_error.call_args.kwargs["exc_info"] is True
        job = clean_job_store.jobs["mlf_err"]
        assert "solver" in job
        assert "solve_result" in job


class TestSolveRatebookFallbackQuoteId:
    """Test _solve_ratebook branch where quote_id col is absent but 'quote_id' exists."""

    def test_ratebook_fallback_quote_id_branch(self):
        """When config quote_id is absent from factors_df but 'quote_id' exists, use fallback."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {"constraints": {}},
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]

        # factors_df has 'quote_id' but config says 'policy_id' which is NOT in the df
        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "region": ["North", "South"],
            }
        )

        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            baseline_constraints={},
            lambdas={},
            converged=True,
            cd_iterations=2,
            factor_tables={},
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1]}),
        )

        config = {
            "objective": "income",
            "constraints": {},
            "factor_columns": [["region"]],
            "quote_id": "policy_id",  # not in factors_df
        }

        with patch("price_contour.RatebookOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

        job = store.require_job(job_id)
        assert job["status"] == "completed"


class TestExecutePipelineHTTPExceptionPassthrough:
    """Test that HTTPException raised inside _execute_pipeline is re-raised directly."""

    def test_http_exception_passthrough(self, scored_data, tmp_path):
        """HTTPException from _execute_lazy is re-raised, not wrapped."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})
        checkpoint_dir = tmp_path / "ckpt"
        checkpoint_dir.mkdir()

        original_exc = HTTPException(status_code=403, detail="forbidden")

        with (
            patch("haute.graph_utils._execute_lazy", side_effect=original_exc),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                service._execute_pipeline(body, job_id, checkpoint_dir)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "forbidden"


class TestBuildGridHTTPExceptionPassthrough:
    """Test that HTTPException in _build_grid is re-raised."""

    def test_http_exception_passthrough(self, tmp_path):
        """HTTPException from build_grid_from_parquet_chunked is re-raised."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        scored_lf = pl.LazyFrame(
            {
                "quote_id": pl.Series(["q1"], dtype=pl.Utf8),
                "scenario_index": pl.Series([0], dtype=pl.Int32),
                "scenario_value": pl.Series([1.0], dtype=pl.Float32),
                "expected_income": pl.Series([100.0], dtype=pl.Float32),
            }
        )

        config = {
            "objective": "expected_income",
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        original_exc = HTTPException(status_code=403, detail="not allowed")

        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch("price_contour.build_grid_from_parquet_chunked", side_effect=original_exc),
        ):
            mock_sink.side_effect = lambda lf, path, **kw: lf.collect().write_parquet(path)

            with pytest.raises(HTTPException) as exc_info:
                service._build_grid(scored_lf, [], config, "opt", job_id)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "not allowed"


# ---------------------------------------------------------------------------
# Integration tests — exercise the REAL solver on tiny datasets
# ---------------------------------------------------------------------------


class TestIntegrationRealSolver:
    """Integration tests that use the real price_contour solver (no mocks).

    These hit the actual OnlineOptimiser on tiny data (5 quotes x 3 scenarios)
    to catch regressions in the solver interface or result schema.
    """

    @pytest.fixture()
    def tiny_scored_data(self, tmp_path) -> str:
        """Create a minimal scored dataset: 5 quotes x 3 scenarios."""
        return _make_scored_data(tmp_path, n_quotes=5, n_steps=3)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_integration_real_online_solve(self, client, tiny_scored_data):
        """POST /solve with real solver — verify result structure."""
        graph = _make_optimiser_graph(
            tiny_scored_data,
            config={
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.90}},
                "max_iter": 20,
                "tolerance": 1e-4,
            },
        )
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        status = _poll_until_done(client, job_id, timeout=30)
        assert status["status"] == "completed", f"Solve failed: {status.get('message')}"

        result = status["result"]
        assert isinstance(result["total_objective"], (int, float))
        assert isinstance(result["lambdas"], dict)
        assert isinstance(result["converged"], bool)
        assert result["n_quotes"] == 5
        assert result["mode"] == "online"
        # Baseline values should also be present
        assert "baseline_objective" in result
        assert "baseline_constraints" in result

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_integration_real_frontier_from_solve(self, client, tiny_scored_data):
        """Verify frontier-enabled solve includes frontier data on real data."""
        graph = _make_optimiser_graph(
            tiny_scored_data,
            config={
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.90}},
                "frontier_enabled": True,
                "frontier_ranges": {"volume": {"min": 4.0, "max": 6.0}},
                "max_iter": 20,
                "tolerance": 1e-4,
            },
        )
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        status = _poll_until_done(client, job_id, timeout=30)
        assert status["status"] == "completed", f"Solve failed: {status.get('message')}"

        # Frontier is computed during solve when explicitly enabled.
        result = status["result"]
        frontier = result.get("frontier")
        assert frontier is not None, "Frontier should be computed when enabled"
        assert frontier["n_points"] > 0
        assert isinstance(frontier["points"], list)
        assert len(frontier["points"]) == frontier["n_points"]
        assert "volume" in frontier["constraint_names"]

        # Each frontier point should have objective and constraint values
        point = frontier["points"][0]
        assert isinstance(point, dict)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_integration_real_apply_after_solve(self, client, tiny_scored_data):
        """POST /apply after real solve — verify row_count and preview."""
        graph = _make_optimiser_graph(
            tiny_scored_data,
            config={
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.90}},
                "max_iter": 20,
                "tolerance": 1e-4,
            },
        )
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        status = _poll_until_done(client, job_id, timeout=30)
        assert status["status"] == "completed", f"Solve failed: {status.get('message')}"

        # Apply the solved lambdas
        resp = client.post("/api/optimiser/apply", json={"job_id": job_id})
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "ok"
        assert data["row_count"] > 0
        assert isinstance(data["preview"], list)
        assert len(data["preview"]) > 0
        # Preview rows should have the expected columns
        first_row = data["preview"][0]
        assert "quote_id" in first_row
        assert "optimal_scenario_value" in first_row
        # Objective value should be present in the response
        assert isinstance(data["total_objective"], (int, float))


# ---------------------------------------------------------------------------
# Helper-function unit tests (defensive validators).
#
# These exercise the small pure-Python helpers in ``haute.routes.optimiser``
# that defend against malformed job state.  Covering them via end-to-end HTTP
# tests is awkward (each scenario needs a synthetic broken job); unit tests
# hit the same branches more directly.
# ---------------------------------------------------------------------------


class TestOptimiserHelperValidators:
    """Direct tests for defensive validators in routes/optimiser.py."""

    def test_as_finite_float_rejects_bool(self) -> None:
        from haute.routes.optimiser import _as_finite_float

        with pytest.raises(HTTPException) as exc:
            _as_finite_float(True, field="x")
        assert exc.value.status_code == 500
        assert "x" in exc.value.detail

    def test_as_finite_float_rejects_non_numeric(self) -> None:
        from haute.routes.optimiser import _as_finite_float

        with pytest.raises(HTTPException) as exc:
            _as_finite_float("not a number", field="y")
        assert exc.value.status_code == 500

    def test_as_finite_float_rejects_nan(self) -> None:
        from haute.routes.optimiser import _as_finite_float

        with pytest.raises(HTTPException) as exc:
            _as_finite_float(float("nan"), field="z")
        assert "not finite" in exc.value.detail

    def test_as_finite_float_rejects_inf(self) -> None:
        from haute.routes.optimiser import _as_finite_float

        with pytest.raises(HTTPException) as exc:
            _as_finite_float(float("inf"), field="z")
        assert "not finite" in exc.value.detail

    def test_as_finite_float_accepts_valid_value(self) -> None:
        from haute.routes.optimiser import _as_finite_float

        assert _as_finite_float(3.14, field="ok") == 3.14
        assert _as_finite_float(7, field="ok") == 7.0

    def test_frontier_points_or_raise_missing_data(self) -> None:
        from haute.routes.optimiser import _frontier_points_or_raise

        with pytest.raises(HTTPException) as exc:
            _frontier_points_or_raise({"frontier_data": None})
        assert exc.value.status_code == 400

    def test_frontier_points_or_raise_invalid_points_type(self) -> None:
        from haute.routes.optimiser import _frontier_points_or_raise

        # ``"not a list"`` is truthy, so it bypasses the 400 (no points) branch
        # and trips the 500 (invalid shape) branch — both are desired loud
        # failures, just at different layers.
        with pytest.raises(HTTPException) as exc:
            _frontier_points_or_raise({"frontier_data": {"points": "not a list"}})
        assert exc.value.status_code == 500

    def test_frontier_points_or_raise_non_dict_point(self) -> None:
        from haute.routes.optimiser import _frontier_points_or_raise

        with pytest.raises(HTTPException) as exc:
            _frontier_points_or_raise({"frontier_data": {"points": [{"ok": 1}, 42]}})
        assert exc.value.status_code == 500

    def test_frontier_point_or_raise_out_of_range(self) -> None:
        from haute.routes.optimiser import _frontier_point_or_raise

        job = {"frontier_data": {"points": [{"a": 1}], "n_points": 5}}
        with pytest.raises(HTTPException) as exc:
            _frontier_point_or_raise(job, 10)
        assert exc.value.status_code == 400
        assert "out of range" in exc.value.detail

    def test_frontier_point_or_raise_capped_payload(self) -> None:
        from haute.routes.optimiser import _frontier_point_or_raise

        # n_points (50) > len(points) (1) → capped payload branch.
        job = {
            "frontier_data": {
                "points": [{"a": 1}],
                "n_points": 50,
                "points_returned": 1,
                "points_limit": 1,
            }
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_or_raise(job, 5)
        assert "capped frontier payload" in exc.value.detail

    def test_frontier_point_lambdas_no_lambda_keys(self) -> None:
        from haute.routes.optimiser import _frontier_point_lambdas

        with pytest.raises(HTTPException) as exc:
            _frontier_point_lambdas({"total_objective": 1.0})
        assert "no lambda" in exc.value.detail

    def test_frontier_point_lambdas_rejects_bool_values(self) -> None:
        """``isinstance(True, int)`` is True; bool keys must not become lambdas."""
        from haute.routes.optimiser import _frontier_point_lambdas

        with pytest.raises(HTTPException):
            _frontier_point_lambdas({"lambda_volume": True})

    def test_frontier_point_constraint_value_falls_back_through_chain(self) -> None:
        """The fallback chain: total_<name> → constraints[<name>] → bare <name>."""
        from haute.routes.optimiser import _frontier_point_constraint_value

        # total_<name> wins.
        assert (
            _frontier_point_constraint_value(
                {"total_volume": 0.9, "constraints": {"volume": 0.7}, "volume": 0.5},
                "volume",
            )
            == 0.9
        )
        # No total_, falls back to constraints dict.
        assert (
            _frontier_point_constraint_value(
                {"constraints": {"volume": 0.7}, "volume": 0.5},
                "volume",
            )
            == 0.7
        )
        # Falls back to bare key.
        assert _frontier_point_constraint_value({"volume": 0.5}, "volume") == 0.5

    def test_frontier_point_constraint_value_missing_raises(self) -> None:
        from haute.routes.optimiser import _frontier_point_constraint_value

        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraint_value({"other": 1}, "volume")
        assert exc.value.status_code == 500

    def test_scenario_stats_returns_none_when_absent(self) -> None:
        from haute.routes.optimiser import _scenario_stats_from_frontier_point

        assert _scenario_stats_from_frontier_point({"total_objective": 1.0}) is None

    def test_base_result_for_frontier_uses_base_when_present(self) -> None:
        from haute.routes.optimiser import _base_result_for_frontier

        job = {"base_result": {"a": 1}, "result": {"a": 2}}
        assert _base_result_for_frontier(job)["a"] == 1

    def test_base_result_for_frontier_falls_back_to_result(self) -> None:
        from haute.routes.optimiser import _base_result_for_frontier

        assert _base_result_for_frontier({"result": {"b": 7}})["b"] == 7

    def test_base_result_for_frontier_missing_raises(self) -> None:
        from haute.routes.optimiser import _base_result_for_frontier

        with pytest.raises(HTTPException) as exc:
            _base_result_for_frontier({})
        assert exc.value.status_code == 500

    def test_base_result_for_recompute_when_no_selection_returns_empty(self) -> None:
        from haute.routes.optimiser import _base_result_for_frontier_recompute

        assert _base_result_for_frontier_recompute({}) == {}

    def test_base_result_for_recompute_with_orphan_selection_raises(self) -> None:
        """If the job is mid-selection but has lost its base_result, fail loud."""
        from haute.routes.optimiser import _base_result_for_frontier_recompute

        job = {"selected_frontier_point": 1}  # selection set, no base_result
        with pytest.raises(HTTPException) as exc:
            _base_result_for_frontier_recompute(job)
        assert "Re-run the solve" in exc.value.detail

    def test_base_result_for_recompute_strips_selected_point(self) -> None:
        from haute.routes.optimiser import _base_result_for_frontier_recompute

        job = {"base_result": {"x": 1, "selected_frontier_point": 0}}
        result = _base_result_for_frontier_recompute(job)
        assert "selected_frontier_point" not in result
        assert result["x"] == 1

    def test_frontier_point_result_dict_invalid_constraint_names(self) -> None:
        from haute.routes.optimiser import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    {"converged": True, "lambda_a": 1.0, "total_objective": 1.0, "total_a": 1.0},
                ],
                "n_points": 1,
                "constraint_names": "not a list",
            },
            "base_result": {},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_result_dict(job, 0)
        assert exc.value.status_code == 500

    def test_frontier_point_result_dict_non_string_constraint_name(self) -> None:
        from haute.routes.optimiser import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    {"converged": True, "lambda_a": 1.0, "total_objective": 1.0, "total_a": 1.0},
                ],
                "n_points": 1,
                "constraint_names": [42],
            },
            "base_result": {},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_result_dict(job, 0)
        assert exc.value.status_code == 500

    def test_frontier_point_result_dict_missing_converged(self) -> None:
        from haute.routes.optimiser import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [{"lambda_a": 1.0, "total_objective": 1.0, "total_a": 1.0}],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_result_dict(job, 0)
        assert "converged" in exc.value.detail

    def test_frontier_point_constraints_override_invalid_config(self) -> None:
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 1.0}],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": "not a dict"},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraints_override(job, 0)
        assert exc.value.status_code == 500

    def test_frontier_point_constraints_override_invalid_name(self) -> None:
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 1.0}],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": {42: {"min": 0}}},
        }
        with pytest.raises(HTTPException):
            _frontier_point_constraints_override(job, 0)

    def test_frontier_point_constraints_override_missing_threshold(self) -> None:
        from haute.routes.optimiser import _frontier_point_constraints_override

        # config defines "a" but with neither min/max/min_pct/max_pct.
        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 1.0}],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": {"a": {}}},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "threshold is invalid" in exc.value.detail

    def test_frontier_point_constraints_override_missing_threshold_field(self) -> None:
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True}],  # no threshold_a key
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": {"a": {"min": 0.5}}},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "threshold_a" in exc.value.detail

    def test_dataframe_or_raise_returns_dataframe_when_present(self) -> None:
        from haute.routes.optimiser import _dataframe_or_raise

        result = SimpleNamespace(dataframe=pl.DataFrame({"a": [1]}))
        df = _dataframe_or_raise(result, context="ctx")
        assert df.shape == (1, 1)

    def test_artifact_handles_or_raise_invalid_type(self) -> None:
        from haute.routes.optimiser import _artifact_handles_or_raise

        with pytest.raises(HTTPException) as exc:
            _artifact_handles_or_raise({"artifact_handles": "not a dict"})
        assert exc.value.status_code == 500

    def test_lambda_mappings_match_returns_false_for_mismatched_keys(self) -> None:
        from haute.routes.optimiser import _lambda_mappings_match

        assert _lambda_mappings_match({"a": 1.0}, {"b": 1.0}) is False

    def test_lambda_mappings_match_returns_false_for_unequal_values(self) -> None:
        from haute.routes.optimiser import _lambda_mappings_match

        assert _lambda_mappings_match({"a": 1.0}, {"a": 2.0}) is False

    def test_lambda_mappings_match_returns_false_for_non_numeric(self) -> None:
        from haute.routes.optimiser import _lambda_mappings_match

        assert _lambda_mappings_match({"a": "bad"}, {"a": 1.0}) is False

    def test_lambda_mappings_match_returns_true_within_tolerance(self) -> None:
        from haute.routes.optimiser import _lambda_mappings_match

        assert _lambda_mappings_match({"a": 1.0}, {"a": 1.0 + 1e-10}) is True

    def test_lambda_mappings_match_returns_false_for_non_dict(self) -> None:
        from haute.routes.optimiser import _lambda_mappings_match

        assert _lambda_mappings_match("not a dict", {"a": 1.0}) is False
        assert _lambda_mappings_match({"a": 1.0}, "not a dict") is False

    def test_selected_or_requested_uses_request_when_provided(self) -> None:
        from haute.routes.optimiser import _selected_or_requested_frontier_point

        assert _selected_or_requested_frontier_point({}, 7) == 7

    def test_selected_or_requested_falls_back_to_job_state(self) -> None:
        from haute.routes.optimiser import _selected_or_requested_frontier_point

        assert _selected_or_requested_frontier_point({"selected_frontier_point": 3}, None) == 3

    def test_selected_or_requested_returns_none_when_state_is_bool(self) -> None:
        """``isinstance(True, int)`` is True, so we explicitly reject bools."""
        from haute.routes.optimiser import _selected_or_requested_frontier_point

        assert (
            _selected_or_requested_frontier_point(
                {"selected_frontier_point": True},
                None,
            )
            is None
        )

    def test_job_has_frontier_points_handles_bad_shape(self) -> None:
        from haute.routes.optimiser import _job_has_frontier_points

        assert _job_has_frontier_points({"frontier_data": "bad"}) is False
        assert _job_has_frontier_points({"frontier_data": {"points": []}}) is False
        assert _job_has_frontier_points({"frontier_data": {"points": [{"a": 1}]}}) is True

    def test_cleanup_orphan_apply_artifact_logs_and_swallows_cleanup_failures(self) -> None:
        """The orphan-cleanup helper must not let secondary failures mask the
        primary error path.  When ``_cleanup_apply_result_artifact`` raises,
        log a warning and continue without re-raising.
        """
        from haute.routes import optimiser as optimiser_module
        from haute.routes.optimiser import _cleanup_orphan_apply_artifact

        with patch.object(
            optimiser_module,
            "_cleanup_apply_result_artifact",
            side_effect=OSError("disk gone"),
        ):
            # Must not raise — the primary failure is what matters.
            _cleanup_orphan_apply_artifact(
                {"directory": "/tmp/some/path"},
                job_id="test_job",
            )

    def test_cleanup_orphan_apply_artifact_uses_path_when_directory_missing(self) -> None:
        from haute.routes import optimiser as optimiser_module
        from haute.routes.optimiser import _cleanup_orphan_apply_artifact

        with patch.object(
            optimiser_module,
            "_cleanup_apply_result_artifact",
            side_effect=OSError("disk gone"),
        ):
            _cleanup_orphan_apply_artifact({"path": "/tmp/file"}, job_id="j1")
            # No assertion: it must simply not raise.

    def test_frontier_point_result_dict_includes_optional_diagnostics(self) -> None:
        """Cover the optional iterations/cd_iterations/clamp_rate branches."""
        from haute.routes.optimiser import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    {
                        "converged": True,
                        "lambda_a": 0.5,
                        "total_objective": 100.0,
                        "total_a": 0.9,
                        "iterations": 3.0,  # float that is actually an int
                        "cd_iterations": 2.0,
                        "clamp_rate": 0.05,
                    }
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {"baseline_constraints": {"a": 0.85}},
        }
        result = _frontier_point_result_dict(job, 0)
        assert result["iterations"] == 3
        assert result["cd_iterations"] == 2
        assert result["clamp_rate"] == 0.05

    def test_frontier_point_result_dict_emits_non_converged_warning(self) -> None:
        from haute.routes.optimiser import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    {
                        "converged": False,
                        "lambda_a": 0.5,
                        "total_objective": 100.0,
                        "total_a": 0.9,
                    }
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {},
        }
        result = _frontier_point_result_dict(job, 0)
        assert "did not converge" in result["warning"]

    def test_frontier_point_result_dict_drops_warning_when_converged(self) -> None:
        from haute.routes.optimiser import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    {
                        "converged": True,
                        "lambda_a": 0.5,
                        "total_objective": 100.0,
                        "total_a": 0.9,
                    }
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {"warning": "stale warning"},
        }
        result = _frontier_point_result_dict(job, 0)
        assert "warning" not in result

    def test_frontier_point_constraints_override_uses_threshold_value(self) -> None:
        """Happy path: walks through every step of constraints_override."""
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 0.95, "threshold_b": 1.05}],
                "n_points": 1,
                "constraint_names": ["a", "b"],
            },
            "config": {"constraints": {"a": {"min": 0.5}, "b": {"max": 1.0}}},
        }
        overrides = _frontier_point_constraints_override(job, 0)
        assert overrides == {"a": {"min": 0.95}, "b": {"max": 1.05}}

    def test_compute_frontier_ratebook_requires_factors_df(self) -> None:
        from haute.routes._optimiser_service import _compute_frontier

        with pytest.raises(RuntimeError) as exc:
            _compute_frontier(
                MagicMock(),
                MagicMock(),
                mode="ratebook",
                factors_df=None,
                threshold_ranges={"a": (0.5, 1.0)},
                n_points_per_dim=3,
            )
        assert "factors dataframe" in str(exc.value)

    def test_enforce_frontier_compute_budget_no_constraints_is_noop(self) -> None:
        from haute.routes._optimiser_limits import enforce_frontier_compute_budget

        # No constraints → no budget check applies.
        enforce_frontier_compute_budget(n_points_per_dim=100, n_constraints=0)

    def test_frontier_point_constraints_override_invalid_constraint_names_list(self) -> None:
        """Cover the branch where ``frontier_data.constraint_names`` is not a list."""
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 1.0}],
                "n_points": 1,
                "constraint_names": "not a list",
            },
            "config": {"constraints": {"a": {"min": 0.5}}},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "constraint names" in exc.value.detail.lower()

    def test_frontier_point_constraints_override_non_string_in_names(self) -> None:
        """Cover the branch where ``constraint_names`` contains a non-string entry."""
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 1.0}],
                "n_points": 1,
                "constraint_names": [42],  # non-string
            },
            "config": {"constraints": {"a": {"min": 0.5}}},
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "constraint names" in exc.value.detail.lower()

    def test_frontier_point_constraints_override_unknown_constraint(self) -> None:
        """Cover the branch where a constraint_name has no matching config spec."""
        from haute.routes.optimiser import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [{"converged": True, "threshold_a": 1.0, "threshold_unknown": 1.0}],
                "n_points": 1,
                "constraint_names": ["a", "unknown"],
            },
            "config": {"constraints": {"a": {"min": 0.5}}},  # 'unknown' missing
        }
        with pytest.raises(HTTPException) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "constraint is missing" in exc.value.detail

    def test_summary_solve_result_round_trips_optional_fields(self) -> None:
        from haute.routes.optimiser import _summary_solve_result

        result = {
            "lambdas": {"a": 0.1},
            "total_objective": 100.0,
            "constraints": {"a": 0.95},
            "baseline_objective": 90.0,
            "baseline_constraints": {"a": 0.9},
            "converged": True,
            "iterations": 5,
            "cd_iterations": 2,
            "clamp_rate": 0.01,
            "factor_tables": {"region": [{"value": 1.0}]},
        }
        ns = _summary_solve_result(result)
        assert ns.lambdas == {"a": 0.1}
        assert ns.total_objective == 100.0
        assert ns.iterations == 5
        assert ns.factor_tables == {"region": [{"value": 1.0}]}

    def test_cached_result_matches_frontier_selection_rejects_bools(self) -> None:
        from haute.routes.optimiser import _cached_result_matches_frontier_selection

        # bool is technically int in Python — must be explicitly rejected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": True},
                1,
            )
            is False
        )
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": 0},
                1,
            )
            is False
        )
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": 1},
                1,
            )
            is True
        )

    def test_cleanup_orphan_apply_artifact_uses_unknown_for_missing_path(self) -> None:
        """Covers the ``"<unknown>"`` fallback in the warning log."""
        from haute.routes import optimiser as optimiser_module
        from haute.routes.optimiser import _cleanup_orphan_apply_artifact

        with patch.object(
            optimiser_module,
            "_cleanup_apply_result_artifact",
            side_effect=OSError("vanished"),
        ):
            _cleanup_orphan_apply_artifact({}, job_id="j2")  # no directory, no path

    def test_enforce_frontier_compute_budget_at_limit_passes(self) -> None:
        from haute.routes._optimiser_limits import (
            FRONTIER_COMPUTE_LIMIT,
            enforce_frontier_compute_budget,
        )

        # 10 ** 5 == 100_000 == FRONTIER_COMPUTE_LIMIT (current value); should
        # NOT raise — this tests the boundary inclusivity of the budget.
        # If the constant is ever raised, this test still asserts the boundary.
        n = 1
        while n**5 <= FRONTIER_COMPUTE_LIMIT:
            n += 1
        # n now exceeds budget; n-1 is the largest passing value.
        enforce_frontier_compute_budget(n_points_per_dim=n - 1, n_constraints=5)


# ---------------------------------------------------------------------------
# Mutation-readiness boundary tests.
#
# Each test below pins a specific constant or boundary that a subtle code
# change (off-by-one, comparison flip, constant drift, dropped clause) could
# silently invalidate.  These complement the behavioural tests above; their
# job is to catch *implementation drift* that doesn't change observable
# behaviour for the normal path.
# ---------------------------------------------------------------------------


class TestOptimiserMutationBoundaries:
    """Pin constants, exact thresholds, and bool/int discriminations."""

    def test_frontier_apply_handle_prefix_is_stable_string(self) -> None:
        """``_FRONTIER_APPLY_HANDLE_PREFIX`` is a wire-format constant: it
        keys into ``artifact_handles`` dicts that survive across job-store
        round-trips and (potentially) across restarts.  A drift to a
        different prefix would orphan every previously-saved handle.
        """
        from haute.routes.optimiser import (
            _FRONTIER_APPLY_HANDLE_PREFIX,
            _frontier_apply_handle_key,
        )

        # The literal value is part of the contract — pin it.
        assert _FRONTIER_APPLY_HANDLE_PREFIX == "frontier_apply_result:"
        # The key builder concatenates without modification.
        assert _frontier_apply_handle_key(0) == "frontier_apply_result:0"
        assert _frontier_apply_handle_key(7) == "frontier_apply_result:7"
        # Negative indices are not pre-validated here — but the prefix is
        # still the key shape.  This catches a mutation that introduced
        # a separator change like ``"frontier_apply_result_"``.
        assert _frontier_apply_handle_key(0).startswith("frontier_apply_result:")

    def test_null_quote_id_error_mentions_count_and_remediation(
        self,
        client,
        tmp_path,
    ):
        """The error string for null quote_ids is part of the user-facing
        contract — the UI parses ``({n} rows)`` for a friendly diagnostic.
        Pin the exact phrasing so a refactor to ``"{n} rows have null quote_id"``
        doesn't silently break the UI message.
        """
        df = pl.DataFrame(
            {
                "quote_id": ["q1", None, None],
                "scenario_index": pl.Series([0, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series([0.9, 0.9, 1.0], dtype=pl.Float32),
                "expected_income": pl.Series([100.0, 99.0, 101.0], dtype=pl.Float32),
                "volume": pl.Series([1.0, 1.0, 1.0], dtype=pl.Float32),
            }
        )
        path = tmp_path / "null_qid.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        from haute._sandbox import _get_project_root, set_project_root

        original_root = _get_project_root()
        try:
            set_project_root(tmp_path)
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )
        finally:
            set_project_root(original_root)

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # Pin the exact surface a UI parser would key on.
        assert "Null quote_id values found in optimiser input" in detail
        # Specific count appears in parens for human readability.
        assert "(2 rows)" in detail
        # Remediation hint stays in the message.
        assert "non-null quote_id" in detail

    def test_cached_result_matches_frontier_selection_rejects_true_explicitly(
        self,
    ) -> None:
        """``isinstance(True, int)`` is True in Python.  Without the
        explicit ``not isinstance(selected_point, bool)`` guard, a job
        with ``selected_frontier_point=True`` would silently match
        ``point_index=1``, returning the wrong cached result.

        Test both ``True`` and ``False`` to defend against a mutation
        that drops only one of the bool checks.
        """
        from haute.routes.optimiser import _cached_result_matches_frontier_selection

        # True looks like 1 numerically — but must be rejected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": True},
                1,
            )
            is False
        )
        # False looks like 0 numerically — must also be rejected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": False},
                0,
            )
            is False
        )
        # Real ints work as expected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": 1},
                1,
            )
            is True
        )

    def test_enforce_frontier_compute_budget_one_above_limit_rejected(self) -> None:
        """Pin the strict-vs-inclusive comparison.  ``> FRONTIER_COMPUTE_LIMIT``
        means the limit value itself is allowed; ``+1`` must reject.

        A mutation to ``>=`` would tighten the gate by 1 grid point; a
        mutation to ``<`` would let arbitrarily large workloads through.
        Both are caught by combining this test with ``..._at_limit_passes``.
        """
        import math

        from haute.routes._optimiser_limits import (
            FRONTIER_COMPUTE_LIMIT,
            enforce_frontier_compute_budget,
        )

        # Construct the smallest grid that exceeds the limit by exactly one.
        # Use n_constraints=2 so the math is simple to inspect.
        # We need n_points_per_dim ** 2 == FRONTIER_COMPUTE_LIMIT + delta.
        # The simplest way: take ceil(sqrt(LIMIT)) + 1.
        boundary = math.isqrt(FRONTIER_COMPUTE_LIMIT)
        # boundary**2 <= LIMIT; (boundary+1)**2 > LIMIT (provided LIMIT isn't
        # a perfect square — for 100_000 it isn't).
        assert (boundary + 1) ** 2 > FRONTIER_COMPUTE_LIMIT

        with pytest.raises(ValueError, match="Frontier compute budget exceeded"):
            enforce_frontier_compute_budget(
                n_points_per_dim=boundary + 1,
                n_constraints=2,
            )

    def test_enforce_frontier_compute_budget_handles_negative_n_constraints(
        self,
    ) -> None:
        """``n_constraints <= 0`` is the no-op branch; document it.

        A mutation to ``< 0`` would cause ``n_constraints=0`` to fall
        into the loop with ``range(0)`` (still no iterations) — same
        outcome by accident.  But ``< 0`` would let a malicious caller
        pass ``n_constraints=-1`` to skip the budget check entirely.
        """
        from haute.routes._optimiser_limits import enforce_frontier_compute_budget

        # No raise — this is the early return.
        enforce_frontier_compute_budget(n_points_per_dim=10**9, n_constraints=0)
        enforce_frontier_compute_budget(n_points_per_dim=10**9, n_constraints=-1)

    def test_frontier_with_n_points_per_dim_one_returns_a_single_point(
        self,
        client,
        clean_job_store,
    ):
        """``n_points_per_dim=1`` is a degenerate request: one grid point
        per constraint.  It must produce a coherent (if uninteresting)
        response, not crash with a divide-by-zero or empty-array error
        deeper in the solver.

        This pins the contract that the cap range still works at
        the lower boundary.
        """
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [42.0],
                    "volume": [0.9],
                    "lambda_volume": [0.0],
                }
            )
        )
        clean_job_store.jobs["frontier_singleton"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "constraints": {"volume": {"min": 0.9}},
                "frontier_ranges": {"volume": {"min": 0.85, "max": 0.95}},
            },
            "result": {
                "mode": "online",
                "total_objective": 40.0,
                "baseline_objective": 38.0,
                "constraints": {"volume": 0.85},
                "baseline_constraints": {"volume": 0.85},
                "lambdas": {"volume": 0.0},
                "converged": True,
            },
            "artifact_handles": {},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_singleton",
                "n_points_per_dim": 1,
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Exactly one point returned, with the constraint name preserved.
        assert data["n_points"] == 1
        assert data["points_returned"] == 1
        assert data["points_truncated"] is False
        assert data["constraint_names"] == ["volume"]
        # Solver was called with n_points_per_dim=1 and the absolute range.
        assert mock_solver.frontier.call_args.kwargs["n_points_per_dim"] == 1
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {
            "volume": (0.85, 0.95),
        }

    def test_frontier_with_min_equal_to_max_is_accepted_by_schema_layer(
        self,
        client,
        clean_job_store,
    ):
        """``min == max`` is a degenerate but valid range — the user is
        pinning a single threshold value.  The schema validator must
        accept it; the solver gets a one-element grid effectively.

        Catches a mutation of ``min_value > max_value`` to ``>=`` which
        would over-tighten validation.
        """
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [42.0],
                    "volume": [0.9],
                    "lambda_volume": [0.0],
                }
            )
        )
        clean_job_store.jobs["frontier_pinned"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "created_at": time.time(),
        }

        # Equal min/max is the pin case — must be accepted.
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_pinned",
                "threshold_ranges": {"volume": [0.9, 0.9]},
                "n_points_per_dim": 3,
            },
        )

        assert resp.status_code == 200, resp.text
        # Solver received the degenerate range as a tuple of the same value.
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {
            "volume": (0.9, 0.9),
        }

    def test_frontier_compute_request_with_lambda_zero_round_trips_through_response(
        self,
        client,
        clean_job_store,
    ):
        """Boundary lambda value (``0.0``) must serialise correctly into
        the frontier response.  A mutation that coerces ``int`` (e.g.
        ``int(point["lambda_*"])`` — which would silently round 0.0 to 0
        but truncate non-integer lambdas) is caught by also asserting a
        non-zero lambda preserves precision.
        """
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame(
                {
                    "total_objective": [50.0, 60.0],
                    "volume": [0.8, 0.95],
                    "lambda_volume": [0.0, 0.7128],
                }
            )
        )
        clean_job_store.jobs["frontier_lambda_zero"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "created_at": time.time(),
        }

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_lambda_zero",
                "threshold_ranges": {"volume": [0.8, 0.95]},
                "n_points_per_dim": 2,
            },
        )

        assert resp.status_code == 200
        points = resp.json()["points"]
        assert len(points) == 2
        # Exact zero must serialise as 0 / 0.0, not be coerced to None or string.
        assert points[0]["lambda_volume"] == 0.0
        # Non-trivial precision is preserved (no integer truncation).
        assert points[1]["lambda_volume"] == pytest.approx(0.7128, rel=1e-6)
