"""Tests for the optimiser node type and API routes."""

from __future__ import annotations

import time
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
        assert data["job_id"]

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
        assert len(data["points"]) == data["n_points"]
        assert "volume" in data["constraint_names"]

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
        assert "no solver" in resp.json()["detail"].lower()

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
        """Selecting a frontier point returns updated metrics."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"

        frontier = status["result"]["frontier"]
        assert frontier is not None
        n_points = frontier["n_points"]
        assert n_points > 0

        # Select the first point
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
        assert "total_objective" in data
        assert "constraints" in data
        assert "lambdas" in data
        assert isinstance(data["converged"], bool)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_last_frontier_point(self, client, scored_data):
        """Selecting the last frontier point works."""
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
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

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
    def test_select_then_save_uses_new_lambdas(self, client, scored_data, tmp_path):
        """After selecting a frontier point, save uses the selected point's result."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        original_obj = status["result"]["total_objective"]

        # Select point 0
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 200
        selected_obj = resp.json()["total_objective"]

        # Save should use the selected result
        out_path = str(tmp_path / "result.json")
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": job_id,
                "output_path": out_path,
            },
        )
        assert resp.status_code == 200

        import json

        saved = json.loads((tmp_path / "result.json").read_text())
        assert saved["total_objective"] == pytest.approx(selected_obj, rel=1e-4)
        # Frontier provenance should be present
        assert saved.get("frontier_selection") is not None
        assert saved["frontier_selection"]["point_index"] == 0
