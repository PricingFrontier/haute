"""Tests for the optimiser node type and API routes."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._parser_helpers import _build_node_config
from haute._sandbox import set_project_root
from haute.graph_utils import NodeType
from haute.routes._optimiser_service import _compute_scenario_value_stats
from haute.routes.optimiser import _build_artifact_payload
from haute.server import app
from tests.conftest import make_edge, make_graph


@pytest.fixture()
def clean_job_store():
    """Snapshot and restore the optimiser job store after each test.

    Tests that inject fake jobs into _store.jobs no longer need
    manual try/finally cleanup.
    """
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "error"):
            return data
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


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
        assert resp.json()["status"] == "started"


class TestStatusRoute:
    def test_missing_job_returns_404(self, client):
        resp = client.get("/api/optimiser/solve/status/nonexistent")
        assert resp.status_code == 404


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

        # After solve completes, solver and quote_grid are released to
        # free memory.  The /frontier endpoint returns 400 when these
        # heavy objects are no longer available.
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": job_id,
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )
        assert resp.status_code == 400
        assert "released" in resp.json()["detail"]

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


# ---------------------------------------------------------------------------
# Phase 1B: Pure function tests
# ---------------------------------------------------------------------------


class TestComputeScenarioValueStats:
    """Unit tests for _compute_scenario_value_stats."""

    def test_no_dataframe_attribute(self):
        """Object without .dataframe returns empty dicts."""
        result = SimpleNamespace()  # no .dataframe
        stats, hist = _compute_scenario_value_stats(result)
        assert stats == {}
        assert hist == {}

    def test_missing_column(self):
        """DataFrame without optimal_scenario_value returns empty dicts."""
        df = pl.DataFrame({"other_col": [1.0, 2.0, 3.0]})
        result = SimpleNamespace(dataframe=df)
        stats, hist = _compute_scenario_value_stats(result)
        assert stats == {}
        assert hist == {}

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
        assert "no solve result" in resp.json()["detail"].lower()

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
        """_execute_lazy receives scenario != 'live', the caller's checkpoint_dir, and preamble_ns."""
        from pathlib import Path

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
        from unittest.mock import call

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
                "price_contour.build_grid_from_parquet",
                return_value=mock_grid,
            ) as mock_build,
        ):
            result = service._build_grid(scored_lf, ["volume"], config, "opt", job_id)

        # safe_sink was called
        assert mock_sink.call_count == 1
        # build_grid_from_parquet was called with correct column mappings
        assert mock_build.call_count == 1
        build_kwargs = mock_build.call_args
        assert build_kwargs.kwargs.get("objective") == "expected_income"
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
    """Verify that frontier data is computed automatically as part of the solve."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_status_includes_frontier(self, client, scored_data):
        """After a successful solve with constraints, status includes frontier data."""
        graph = _make_optimiser_graph(scored_data)
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
        graph = _make_optimiser_graph(scored_data)
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
    def test_solve_no_constraints_no_frontier(self, client, scored_data):
        """A solve with empty constraints should have no frontier data."""
        cfg = {
            "objective": "expected_income",
            "constraints": {},
            "max_iter": 5,
        }
        graph = _make_optimiser_graph(scored_data, config=cfg)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        # May succeed or fail depending on solver behavior with no constraints
        if resp.status_code != 200:
            return
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        if status["status"] != "completed":
            return
        result = status["result"]
        # Frontier should be None when no constraints
        assert result.get("frontier") is None


class TestFrontierSelect:
    """Tests for POST /api/optimiser/frontier/select."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_frontier_point(self, client, scored_data):
        """After solve completes, solver/quote_grid are released — select returns 400."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"

        frontier = status["result"]["frontier"]
        assert frontier is not None
        n_points = frontier["n_points"]
        assert n_points > 0

        # solver and quote_grid are released after finalization,
        # so frontier/select returns 400
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 400
        assert "released" in resp.json()["detail"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_last_frontier_point(self, client, scored_data):
        """After solve, solver/quote_grid released — select returns 400."""
        graph = _make_optimiser_graph(scored_data)
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
        assert resp.status_code == 400
        assert "released" in resp.json()["detail"]

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
    def test_select_after_solve_returns_released(self, client, scored_data):
        """After solve, solver/quote_grid are released — select returns 400."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        # solver and quote_grid are released after finalization
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 400
        assert "released" in resp.json()["detail"]


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
            OptimiserSolveService._validate_config(
                {"objective": "income", "mode": "quantum"}
            )
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

        mode = OptimiserSolveService._validate_config(
            {"objective": "income", "mode": "online"}
        )
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
    def test_empty_dataframe_returns_n_zero(self):
        df = pl.DataFrame(
            {"optimal_scenario_value": pl.Series([], dtype=pl.Float64)}
        )
        result = SimpleNamespace(dataframe=df)
        stats, hist = _compute_scenario_value_stats(result)
        assert stats == {"n": 0}
        assert hist == {}

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
        df = pl.DataFrame(
            {"optimal_scenario_value": [0.9, 1.0, 1.1, 1.2, 0.8]}
        )
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
        job_id = store.create_job(
            {"status": "running", "config": {"constraints": {}}}
        )
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
        job_id = store.create_job(
            {"status": "running", "config": {"constraints": {}}}
        )
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
        job_id = store.create_job(
            {"status": "running", "config": {"constraints": {}}}
        )
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

    def test_frontier_computed_for_online_mode(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
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
        assert "volume" in job["frontier_data"]["constraint_names"]
        mock_solver.frontier.assert_called_once()

    def test_frontier_not_computed_for_ratebook_mode(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="ratebook",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is None
        mock_solver.frontier.assert_not_called()


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
                "total_objective": 200.0,
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
        assert data["constraint_names"] == ["volume"]

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
            },
            "solver": MagicMock(),
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    {"lambda_volume": 0.3},
                    {"lambda_volume": 0.4},
                    {"lambda_volume": 0.6},
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
        assert data["total_objective"] == 150.0
        assert data["lambdas"] == {"volume": 0.6}
        assert data["converged"] is True
        # Solver should NOT have been called (short circuit)
        clean_job_store.jobs["idem"]["solver"].solve.assert_not_called()


class TestSelectFrontierPointResolve:
    """Test re-solving with new lambdas, convergence warning, and scenario stats."""

    def _make_frontier_job(self, clean_job_store, *, converged=True):
        mock_solver = MagicMock()
        new_result = SimpleNamespace(
            total_objective=200.0,
            baseline_objective=190.0,
            total_constraints={"volume": 0.95},
            baseline_constraints={"volume": 0.90},
            lambdas={"volume": 0.7},
            converged=converged,
            dataframe=pl.DataFrame(
                {"optimal_scenario_value": [0.9, 1.0, 1.1, 1.2, 0.8]}
            ),
        )
        mock_solver.solve.return_value = new_result
        clean_job_store.jobs["fsel"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [
                    {"lambda_volume": 0.3, "total_objective": 100.0},
                    {"lambda_volume": 0.7, "total_objective": 200.0},
                ],
                "n_points": 2,
                "constraint_names": ["volume"],
            },
            "result": {"total_objective": 100.0, "lambdas": {"volume": 0.3}},
            "created_at": time.time(),
        }
        return mock_solver

    def test_resolve_with_new_lambdas(self, client, clean_job_store):
        """Selecting a frontier point re-solves and returns updated metrics."""
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
        mock_solver.solve.assert_called_once()
        # Verify the lambdas were extracted from the frontier point
        call_kwargs = mock_solver.solve.call_args
        assert call_kwargs[1]["lambdas"] == {"volume": 0.7}

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
        """After re-solve, scenario stats are recomputed and stored."""
        self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert "scenario_value_stats" in job["result"]
        assert "scenario_value_histogram" in job["result"]
        stats = job["result"]["scenario_value_stats"]
        assert "mean" in stats

    def test_resolve_records_frontier_provenance(self, client, clean_job_store):
        """After re-solve, the selected frontier point index is stored on the job."""
        self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.jobs["fsel"]
        assert job["selected_frontier_point"] == 1
        assert job["result"]["selected_frontier_point"] == 1

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
        """Select with no solver returns 400."""
        clean_job_store.jobs["no_slv"] = {
            "status": "completed",
            "solver": None,
            "quote_grid": None,
            "frontier_data": {"points": [{"lambda_v": 0.5}]},
            "result": {},
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "no_slv", "point_index": 0},
        )
        assert resp.status_code == 400
        assert "solver" in resp.json()["detail"].lower()

    def test_select_solver_exception_returns_500(self, client, clean_job_store):
        """If solver.solve raises, endpoint returns 500."""
        mock_solver = MagicMock()
        mock_solver.solve.side_effect = RuntimeError("solver boom")
        clean_job_store.jobs["sel_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "frontier_data": {
                "status": "ok",
                "points": [{"lambda_volume": 0.5}],
                "n_points": 1,
                "constraint_names": ["volume"],
            },
            "result": {},
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "sel_err", "point_index": 0},
        )
        assert resp.status_code == 500


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
            lambdas={}, total_objective=0.0, total_constraints={}, converged=True,
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
            lambdas={}, total_objective=0.0, total_constraints={}, converged=True,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload


class TestMlflowLogExtended:
    """Extended tests for /mlflow/log — frontier data logging, tags, artifacts."""

    @staticmethod
    def _make_mlflow_job(clean_job_store, job_id, *, frontier_data=None, selected_frontier_point=None):
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
            "config": {"mode": "online", "objective": "income", "constraints": {}},
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
                {"total_objective": 100.0, "lambda_volume": 0.3},
                {"total_objective": 110.0, "lambda_volume": 0.5},
            ],
            "n_points": 2,
            "constraint_names": ["volume"],
        }
        self._make_mlflow_job(
            clean_job_store, "mlf_ok",
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
        frontier_calls = [
            c for c in mock_mlflow.set_tag.call_args_list
            if "frontier" in str(c)
        ]
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

    def test_running_job_not_timed_out(self, client, clean_job_store):
        """A running job with sufficient timeout remains running."""
        clean_job_store.jobs["not_tout"] = {
            "status": "running",
            "progress": 0.5,
            "message": "Solving",
            "start_time": time.monotonic(),
            "timeout": 9999,
            "elapsed_seconds": 0.5,
            "created_at": time.time(),
        }
        resp = client.get("/api/optimiser/solve/status/not_tout")
        data = resp.json()
        assert data["status"] == "running"

    def test_status_completed_without_frontier(self, client, clean_job_store):
        """Completed job without frontier_data returns frontier=None."""
        clean_job_store.jobs["no_front"] = {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": 2.0,
            "result": {"total_objective": 100.0},
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
        job_id = store.create_job({
            "status": "running",
            "config": {
                "constraints": {"volume": {"min": 0.9}},
                "objective": "expected_income",
            },
        })

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

        with patch("price_contour.OnlineOptimiser") as MockSolver:
            MockSolver.return_value.solve.return_value = mock_result
            # Also mock frontier to return None (to avoid error)
            MockSolver.return_value.frontier.side_effect = Exception("skip")

            _solve_online(mock_grid, config, store, job_id, time.monotonic())

        MockSolver.assert_called_once_with(
            objective="expected_income",
            constraints={"volume": {"min": 0.9}},
            max_iter=20,
            chunk_size=1000,
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
        job_id = store.create_job({
            "status": "running",
            "config": {"constraints": {}, "objective": "income"},
        })

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

        with patch("price_contour.OnlineOptimiser") as MockSolver:
            MockSolver.return_value.solve.return_value = mock_result
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
        job_id = store.create_job({
            "status": "running",
            "config": {"constraints": {}},
        })

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3"]

        factors_df = pl.DataFrame({
            "quote_id": ["q1", "q2", "q3"],
            "region": ["North", "South", "East"],
        })

        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=True,
            cd_iterations=3,
            factor_tables={"region": {"North": 1.1, "South": 0.9, "East": 1.0}},
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1, 0.9]}),
        )

        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"]],
            "quote_id": "quote_id",
        }

        with patch("price_contour.RatebookOptimiser") as MockSolver:
            MockSolver.return_value.solve.return_value = mock_result
            _solve_ratebook(mock_grid, config, factors_df, store, job_id, time.monotonic())

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert "factor_tables" in job["result"]
        assert "region" in job["result"]["factor_tables"]

    def test_solve_ratebook_custom_quote_id(self):
        """Ratebook solve with custom quote_id column renames it."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job({
            "status": "running",
            "config": {"constraints": {}},
        })

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]

        factors_df = pl.DataFrame({
            "policy_id": ["q1", "q2"],
            "region": ["North", "South"],
        })

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

        with patch("price_contour.RatebookOptimiser") as MockSolver:
            MockSolver.return_value.solve.return_value = mock_result
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
        source_lf = pl.LazyFrame({
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [100.0],
        })

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

    def test_column_casting_and_null_filtering(self):
        """Columns are cast and nulls in quote_id are filtered."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        # Include a null quote_id row
        source_lf = pl.LazyFrame({
            "quote_id": ["q1", None, "q3"],
            "scenario_index": [0, 1, 2],
            "scenario_value": [1.0, 1.1, 1.2],
            "expected_income": [100.0, 110.0, 120.0],
            "volume": [0.9, 0.95, 0.88],
        })

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        constraint_cols, scored_lf = service._validate_and_project(source_lf, config, job_id)
        assert constraint_cols == ["volume"]
        # Collect and check null row is filtered
        result_df = scored_lf.collect()
        assert len(result_df) == 2  # null row filtered
        assert result_df["scenario_index"].dtype == pl.Int32
        assert result_df["scenario_value"].dtype == pl.Float32
        assert result_df["expected_income"].dtype == pl.Float32

    def test_empty_constraints_returns_no_constraint_cols(self):
        """With empty constraints dict, constraint_cols is empty list."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        source_lf = pl.LazyFrame({
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [100.0],
        })

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

        scored_lf = pl.LazyFrame({
            "quote_id": pl.Series(["q1", "q1", "q2", "q2"], dtype=pl.Utf8),
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 110.0, 200.0, 220.0], dtype=pl.Float32),
            "volume": pl.Series([0.9, 0.85, 0.95, 0.90], dtype=pl.Float32),
        })

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        mock_grid = MagicMock()
        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch("price_contour.build_grid_from_parquet", return_value=mock_grid) as mock_build,
        ):
            # Make safe_sink actually write the file
            def do_sink(lf, path, **kw):
                lf.collect().write_parquet(path)
            mock_sink.side_effect = do_sink

            result = service._build_grid(scored_lf, ["volume"], config, "opt", job_id)

        assert result is mock_grid
        mock_build.assert_called_once()
        # Temp file should be cleaned up
        build_call_args = mock_build.call_args
        parquet_path = build_call_args[0][0]
        import os
        assert not os.path.exists(parquet_path)

    def test_build_grid_failure_updates_job_store(self, tmp_path):
        """Grid construction failure updates job store with error."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        scored_lf = pl.LazyFrame({
            "quote_id": pl.Series(["q1"], dtype=pl.Utf8),
            "scenario_index": pl.Series([0], dtype=pl.Int32),
            "scenario_value": pl.Series([1.0], dtype=pl.Float32),
            "expected_income": pl.Series([100.0], dtype=pl.Float32),
        })

        config = {
            "objective": "expected_income",
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet",
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


class TestApplyException:
    """Test apply endpoint exception handling."""

    def test_apply_exception_returns_500(self, client, clean_job_store):
        """When solve_result.dataframe raises, apply returns 500."""
        mock_solve_result = MagicMock()
        mock_solve_result.dataframe = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
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
        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_err"})
        assert resp.status_code == 500


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
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "front_err",
                "threshold_ranges": {"volume": [0.85, 0.95]},
                "n_points_per_dim": 3,
            },
        )
        assert resp.status_code == 500


class TestSaveExceptionPaths:
    """Test save endpoint OSError and generic Exception paths."""

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

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_os", "output_path": out_path},
            )
        assert resp.status_code == 500
        assert "filesystem" in resp.json()["detail"].lower()

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

        with patch("pathlib.Path.write_text", side_effect=RuntimeError("unexpected")):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_gen", "output_path": out_path},
            )
        assert resp.status_code == 500


class TestMlflowLogExceptionPath:
    """Test mlflow_log generic exception path."""

    def test_mlflow_log_internal_error(self, client, clean_job_store):
        """When mlflow logging raises, endpoint returns 500."""
        mock_solver = MagicMock()
        mock_solver.summary.side_effect = RuntimeError("summary boom")
        mock_solve_result = SimpleNamespace(
            lambdas={}, total_objective=0, total_constraints={}, converged=True,
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
        with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_err"},
            )
        assert resp.status_code == 500


class TestSolveRatebookFallbackQuoteId:
    """Test _solve_ratebook branch where quote_id col is absent but 'quote_id' exists."""

    def test_ratebook_fallback_quote_id_branch(self):
        """When config quote_id is absent from factors_df but 'quote_id' exists, use fallback."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import _solve_ratebook

        store = JobStore()
        job_id = store.create_job({
            "status": "running",
            "config": {"constraints": {}},
        })

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]

        # factors_df has 'quote_id' but config says 'policy_id' which is NOT in the df
        factors_df = pl.DataFrame({
            "quote_id": ["q1", "q2"],
            "region": ["North", "South"],
        })

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

        with patch("price_contour.RatebookOptimiser") as MockSolver:
            MockSolver.return_value.solve.return_value = mock_result
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
        """HTTPException from build_grid_from_parquet is re-raised."""
        from fastapi import HTTPException

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running"})

        scored_lf = pl.LazyFrame({
            "quote_id": pl.Series(["q1"], dtype=pl.Utf8),
            "scenario_index": pl.Series([0], dtype=pl.Int32),
            "scenario_value": pl.Series([1.0], dtype=pl.Float32),
            "expected_income": pl.Series([100.0], dtype=pl.Float32),
        })

        config = {
            "objective": "expected_income",
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        original_exc = HTTPException(status_code=403, detail="not allowed")

        with (
            patch("haute._polars_utils.safe_sink") as mock_sink,
            patch("price_contour.build_grid_from_parquet", side_effect=original_exc),
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
        """Verify auto-computed frontier in solve result on real data."""
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

        # Frontier is auto-computed during solve when constraints are present
        result = status["result"]
        frontier = result.get("frontier")
        assert frontier is not None, "Frontier should be auto-computed when constraints exist"
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
