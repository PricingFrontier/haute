"""Ratebook-mode optimiser solves.

A ratebook solve runs through the route and through ``_solve_ratebook``, produces factor tables per
frontier point and records the solve result the save and apply paths read.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute.errors import InvalidRequestError, RequestRefusedError
from haute.routes._optimiser_solver import SolveContext
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _make_optimiser_graph,
    _optimiser_helper_graph,
    _per_factor_record,
    _poll_until_done,
    _ratebook_solve_result_namespace,
    _snapshot_parquet_data_input,
)
from tests.conftest import make_edge, make_graph
from tests.optimiser_fixtures import (
    SOLVE_PROVENANCE,
    SOLVE_SCENARIO_GRID,
    library_frontier_frame,
    make_ratebook_quote_results,
)

# Fixtures shared with the other modules split from one suite.
_in_solver_worker_context = _optimiser_route_helpers._in_solver_worker_context
scored_data = _optimiser_route_helpers.scored_data


# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


# ---------------------------------------------------------------------------
# Test data: build a scored DataFrame in the shape price-contour expects
# ---------------------------------------------------------------------------


@contextmanager
def _persisted_ratebook_factors_handle(factors_df: pl.DataFrame) -> Iterator[dict[str, object]]:
    """Yield a canonical ratebook factor handle and remove its artifact afterwards."""
    from haute.routes._optimiser_artifacts import (
        _cleanup_ratebook_factors_artifact,
        _persist_ratebook_factors_lazy_artifact,
    )

    handle = _persist_ratebook_factors_lazy_artifact(factors_df.lazy())
    assert handle is not None
    try:
        yield handle
    finally:
        _cleanup_ratebook_factors_artifact(handle)


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
    """Build a 3-node graph: dataInput → optimiser ← banding."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(data_path),
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(banding_data_path),
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


def _make_ratebook_intermediate_graph(data_path: str, banding_data_path: str) -> dict:
    """Build a ratebook graph whose configured solver inputs are intermediates."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(data_path),
                    },
                },
                {
                    "id": "optimiser_input",
                    "data": {
                        "label": "optimiser_input",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = source",
                            "contract": {"inputs": [], "outputs": []},
                        },
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(banding_data_path),
                    },
                },
                {
                    "id": "banding_source",
                    "data": {
                        "label": "banding_source",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = banding.select(['quote_id', 'region'])",
                            "contract": {
                                "inputs": ["quote_id", "region"],
                                "outputs": [],
                            },
                        },
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
                            "banding_source": "banding_source",
                            "data_input": "optimiser_input",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "optimiser_input").model_dump(),
                make_edge("optimiser_input", "opt").model_dump(),
                make_edge("banding", "banding_source").model_dump(),
                make_edge("banding_source", "opt").model_dump(),
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

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_ratebook_solve_reports_the_solved_grid_shape(self, client, tmp_path):
        """A real ratebook solve names the grid it scored, as an online solve
        does: the result's provenance strip reads N quotes x M scenario steps."""
        scored_path, banding_path = _make_ratebook_data(tmp_path, n_quotes=7, n_steps=4)
        graph = _make_ratebook_graph(scored_path, banding_path)
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 200

        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "completed", status.get("message", "")
        result = status["result"]
        assert result["mode"] == "ratebook"
        assert result["n_quotes"] == 7
        assert result["n_steps"] == 4

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_ratebook_solve_preserves_intermediate_data_input_and_banding_source(
        self,
        client,
        tmp_path,
    ):
        """Solve setup must retain configured intermediate inputs after execution."""
        scored_path, banding_path = _make_ratebook_data(tmp_path, n_quotes=12, n_steps=3)
        graph = _make_ratebook_intermediate_graph(scored_path, banding_path)

        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 200, resp.text
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "completed", status.get("message")
        result = status["result"]
        assert result["mode"] == "ratebook"
        assert set(result["factor_tables"]) == {"region"}

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
# Coverage expansion tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_in_solver_worker_context")
class TestSolveRatebookUnit:
    """Unit tests for _solve_ratebook."""

    def test_solve_ratebook_no_factors_df(self):
        """Ratebook mode without factors_df raises a typed input error."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _OptimiserSolveInputError, _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {},
            }
        )
        mock_grid = MagicMock()

        with pytest.raises(_OptimiserSolveInputError, match="banding source"):
            _solve_ratebook(
                SolveContext(
                    job_id=job_id,
                    node_id="opt",
                    mode="ratebook",
                    store=store,
                    start_time=time.monotonic(),
                ),
                quote_grid=mock_grid,
                config={},
                ratebook_factors_handle=None,
            )

    def test_solve_ratebook_requires_worker_start_time(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {},
            }
        )

        with pytest.raises(RuntimeError, match="SolveContext.start_time"):
            _solve_ratebook(
                SolveContext(
                    job_id=job_id,
                    node_id="opt",
                    mode="ratebook",
                    store=store,
                ),
                quote_grid=MagicMock(),
                config={},
                ratebook_factors_handle=None,
            )

    def test_solve_ratebook_invalid_factor_columns(self):
        """Ratebook mode with missing factor columns is a typed input error."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _OptimiserSolveInputError, _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {},
            }
        )
        mock_grid = MagicMock()
        factors_df = pl.DataFrame({"quote_id": ["q1"], "existing_col": ["A"]})

        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["nonexistent_col"]],
        }

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with pytest.raises(_OptimiserSolveInputError, match="Missing ratebook factor columns"):
                _solve_ratebook(
                    SolveContext(
                        job_id=job_id,
                        node_id="opt",
                        mode="ratebook",
                        store=store,
                        start_time=time.monotonic(),
                    ),
                    quote_grid=mock_grid,
                    config=config,
                    ratebook_factors_handle=factors_handle,
                )

    def test_solve_ratebook_success(self):
        """Ratebook solve succeeds with valid factors_df."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_artifacts import (
            _RATEBOOK_FACTORS_HANDLE_KEY,
            _scan_ratebook_factors_artifact,
        )
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 3

        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q3"],
                "region": ["North", "North", "East"],
            }
        )

        # 3b.9: real RatebookResult field set — no phantom ``dataframe``
        # (real ratebook solves never persist an apply artifact).
        mock_result = _ratebook_solve_result_namespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            lambdas={"volume": 0.5},
            cd_iterations=3,
            factor_tables={"region": {"North": 1.1, "East": 1.0}},
        )

        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"]],
            "quote_id": "quote_id",
        }

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with patch("price_contour.RatebookOptimiser") as mock_solver:
                mock_solver.return_value.solve.return_value = mock_result
                _solve_ratebook(
                    SolveContext(
                        job_id=job_id,
                        node_id="opt",
                        mode="ratebook",
                        store=store,
                        start_time=time.monotonic(),
                    ),
                    quote_grid=mock_grid,
                    config=config,
                    ratebook_factors_handle=factors_handle,
                )

            assert "chunk_size" not in mock_solver.call_args.kwargs
            job = store.require_job(job_id)
            assert job["status"] == "completed"
            assert "factor_tables" in job["result"]
            assert "region" in job["result"]["factor_tables"]
            assert job["result"]["factor_tables"]["region"] == [
                {"__factor_group__": "North", "optimal_scenario_value": 1.1, "quote_count": 2},
                {"__factor_group__": "East", "optimal_scenario_value": 1.0, "quote_count": 1},
            ]
            assert "factors_df" not in job
            assert "ratebook_factor_contexts" in job
            factors_handle = job["artifact_handles"][_RATEBOOK_FACTORS_HANDLE_KEY]
            assert _scan_ratebook_factors_artifact(factors_handle).collect().columns == [
                "quote_id",
                "region",
            ]
            # The collar is the grid's first and last scenario value (Q17).
            assert job["result"]["combined_factor_bounds"] == {"min": 0.9, "max": 1.1}
            assert job["result"]["clamp_rate"] == mock_result.clamp_rate
            # The CD trace is the library's per-factor records, by name.
            assert job["result"]["history"] is None
            assert job["result"]["ratebook_cd_trace"] == {
                "records": [
                    {
                        "cd_iteration": 1,
                        "factor": "region",
                        "factor_index": 0,
                        "total_objective": 100.0,
                        "total_constraints": {"volume": 0.92},
                        "lambdas": {"volume": 0.5},
                    }
                ],
                "truncated": False,
            }

    @staticmethod
    def _solve_ratebook_with(mock_result: SimpleNamespace) -> dict[str, Any]:
        """Run ``_solve_ratebook`` over a one-factor book with *mock_result*; the job."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 2
        factors_df = pl.DataFrame({"quote_id": ["q1", "q2"], "region": ["North", "South"]})
        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"]],
            "quote_id": "quote_id",
        }
        with (
            _persisted_ratebook_factors_handle(factors_df) as factors_handle,
            patch("price_contour.RatebookOptimiser") as mock_solver,
        ):
            mock_solver.return_value.solve.return_value = mock_result
            _solve_ratebook(
                SolveContext(
                    job_id=job_id,
                    node_id="opt",
                    mode="ratebook",
                    store=store,
                    start_time=time.monotonic(),
                ),
                quote_grid=mock_grid,
                config=config,
                ratebook_factors_handle=factors_handle,
            )
        return store.require_job(job_id)

    def test_ratebook_cd_trace_keeps_the_last_records_past_its_cap(self, monkeypatch):
        monkeypatch.setenv("HAUTE_OPTIMISER_CD_TRACE_LIMIT", "2")
        records = [
            _per_factor_record(cd_iteration=cd_pass, total_objective=float(cd_pass))
            for cd_pass in (1, 2, 3)
        ]

        job = self._solve_ratebook_with(
            _ratebook_solve_result_namespace(per_factor_results=records)
        )

        trace = job["result"]["ratebook_cd_trace"]
        assert trace["truncated"] is True
        assert [record["cd_iteration"] for record in trace["records"]] == [2, 3]

    def test_ratebook_cd_trace_at_its_cap_is_not_truncated(self, monkeypatch):
        monkeypatch.setenv("HAUTE_OPTIMISER_CD_TRACE_LIMIT", "2")
        records = [_per_factor_record(cd_iteration=cd_pass) for cd_pass in (1, 2)]

        job = self._solve_ratebook_with(
            _ratebook_solve_result_namespace(per_factor_results=records)
        )

        assert job["result"]["ratebook_cd_trace"]["truncated"] is False
        assert len(job["result"]["ratebook_cd_trace"]["records"]) == 2

    @pytest.mark.parametrize(
        ("records", "message"),
        [
            pytest.param([], "at least 1", id="no-records"),
            pytest.param(
                [_per_factor_record(total_constraints={"other": 1.0}, lambdas={"other": 0.1})],
                "constraint names",
                id="wrong-constraint-names",
            ),
            pytest.param(
                [_per_factor_record(total_objective=float("nan"))],
                "finite",
                id="non-finite-objective",
            ),
        ],
    )
    def test_malformed_ratebook_cd_trace_fails_the_solve_loudly(self, records, message):
        with pytest.raises(ValueError, match=message):
            self._solve_ratebook_with(_ratebook_solve_result_namespace(per_factor_results=records))

    def test_solve_ratebook_persists_its_quote_results_and_reports_them(self):
        """The REAL ``RatebookResult`` has no ``.dataframe`` (pinned by
        tests/test_optimiser_routes_real_library.py): a ratebook solve persists its
        canonical ``quote_results`` as the apply artifact and builds the as-solved
        adjustment report from it, counting the flagged quotes (OPT-V09C)."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_artifacts import (
            _APPLY_RESULT_HANDLE_KEY,
            _RATEBOOK_FACTORS_HANDLE_KEY,
        )
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "mode": "ratebook",
                    "constraints": {"volume": {"min": 0.9}},
                    "factor_columns": [["region"]],
                },
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 2
        factors_df = pl.DataFrame({"quote_id": ["q1", "q2"], "region": ["North", "South"]})
        config = {
            "objective": "income",
            "constraints": {},
            "factor_columns": [["region"]],
            "quote_id": "quote_id",
        }
        # q1's product 1.02 rounds to the 1.0 step; q2's lands on it.
        quote_results = make_ratebook_quote_results(["volume"])

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with patch("price_contour.RatebookOptimiser") as mock_solver:
                mock_solver.return_value.solve.return_value = _ratebook_solve_result_namespace(
                    factor_tables={"region": {"North": 1.08, "South": 0.92}},
                    quote_results=quote_results,
                )
                _solve_ratebook(
                    SolveContext(
                        job_id=job_id,
                        node_id="opt",
                        mode="ratebook",
                        store=store,
                        start_time=time.monotonic(),
                    ),
                    quote_grid=mock_grid,
                    config=config,
                    ratebook_factors_handle=factors_handle,
                )

            job = store.require_job(job_id)
            assert job["status"] == "completed"
            apply_path = job["artifact_handles"][_APPLY_RESULT_HANDLE_KEY]["path"]
            assert pl.read_parquet(apply_path).equals(quote_results)
            assert _RATEBOOK_FACTORS_HANDLE_KEY in job["artifact_handles"]
            report = job["result"]["adjustments"]
            assert job["result"]["diagnostics_errors"] == []
            assert [bar["quotes"] for bar in report["bars"]] == [0, 2, 0]
            assert report["deployed_factor_differs"] == 1

    def test_solve_ratebook_orders_factor_tables_by_banding_rule_order(self):
        """Ratebook rates are serialised in the source banding row order."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {}},
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3", "q4", "q5"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 5

        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q3", "q4", "q5"],
                "proposer_age_band": ["20-27", "28-34", "35-41", "missing", "solver_only"],
                "channel_band": ["direct", "broker", "direct", "broker", "direct"],
            }
        )
        # 3b.9: real RatebookResult field set — no phantom ``dataframe``.
        mock_result = _ratebook_solve_result_namespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            constraint_bounds={},
            baseline_constraints={},
            lambdas={},
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

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with patch("price_contour.RatebookOptimiser") as mock_solver:
                mock_solver.return_value.solve.return_value = mock_result
                _solve_ratebook(
                    SolveContext(
                        job_id=job_id,
                        node_id="opt",
                        mode="ratebook",
                        store=store,
                        start_time=time.monotonic(),
                    ),
                    quote_grid=mock_grid,
                    config=config,
                    ratebook_factors_handle=factors_handle,
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
        from haute.routes._optimiser_solver import _ratebook_factor_level_counts

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

    def test_ratebook_factor_level_counts_canonicalise_typed_levels(self):
        """3b.10: counts keys go through ``normalise_rating_key`` per
        component so they agree with the keys the apply-side rating join
        derives from frame values (Float64 25.0 -> "25", strings verbatim)."""
        from haute.routes._optimiser_solver import _ratebook_factor_level_counts

        factors_df = pl.DataFrame(
            {
                "age": [25.0, 25.0, 30.5],
                "vehicle_group": pl.Series([7, 7, 12], dtype=pl.Int64),
                "is_renewal": [True, False, True],
                "label": ["25.0", "young", "young"],
            }
        )

        counts = _ratebook_factor_level_counts(
            factors_df,
            [["age"], ["vehicle_group"], ["is_renewal"], ["label"], ["label", "age"]],
        )

        assert counts["age"] == {"25": 2, "30.5": 1}
        assert counts["vehicle_group"] == {"7": 2, "12": 1}
        assert counts["is_renewal"] == {"true": 2, "false": 1}
        # String labels stay verbatim — "25.0" is a label here, not a number.
        assert counts["label"] == {"25.0": 1, "young": 2}
        assert counts["label:age"] == {
            "25.0\x1f25": 1,
            "young\x1f25": 1,
            "young\x1f30.5": 1,
        }

    def test_ratebook_factor_level_counts_rejects_object_dtype(self):
        """Mixed Python object columns have no stable persisted dtype contract."""
        from haute.routes._optimiser_solver import _ratebook_factor_level_counts

        factors_df = pl.DataFrame([pl.Series("age", ["25", 25.0], dtype=pl.Object)])

        with pytest.raises(
            ValueError,
            match=r"unsupported rating factor dtype Object",
        ) as exc_info:
            _ratebook_factor_level_counts(factors_df, [["age"]])
        assert "Object" in str(exc_info.value)

    def test_serialise_ratebook_factor_tables_canonicalises_float_labels(self):
        """3b.10: solver-emitted "25.0" saves as the canonical "25" the apply
        join derives from a Float64 frame; non-integer floats keep their
        digits; never-numeric strings stay verbatim; quote counts attach to
        the canonical key."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"age": {"25.0": 1.1, "30.5": 0.9}, "region": {"young": 1.2}},
            {"age": {"25": 3, "30.5": 2}, "region": {"young": 4}},
            {},
            {
                "age": [{"column": "age", "dtype": {"kind": "Float64"}}],
                "region": [{"column": "region", "dtype": {"kind": "String"}}],
            },
        )

        assert serialised["age"] == [
            {"__factor_group__": "25", "optimal_scenario_value": 1.1, "quote_count": 3},
            {"__factor_group__": "30.5", "optimal_scenario_value": 0.9, "quote_count": 2},
        ]
        assert serialised["region"] == [
            {"__factor_group__": "young", "optimal_scenario_value": 1.2, "quote_count": 4},
        ]

    def test_serialise_ratebook_factor_tables_idempotent_for_canonical_labels(self):
        """3b.10: already-canonical labels are unchanged by canonicalisation."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"age": {"25": 1.1}},
            {"age": {"25": 3}},
            {},
            {"age": [{"column": "age", "dtype": {"kind": "Float64"}}]},
        )

        assert serialised["age"] == [
            {"__factor_group__": "25", "optimal_scenario_value": 1.1, "quote_count": 3},
        ]

    def test_serialise_ratebook_factor_tables_string_sourced_numeric_label_stays_verbatim(self):
        """3b.10: a Utf8 source column's literal "25.0" level has a verbatim
        counts key, so it must NOT collapse — the apply join keeps Utf8 frame
        values verbatim and would miss a collapsed label."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"age": {"25.0": 1.1}},
            {"age": {"25.0": 3}},
            {},
            {"age": [{"column": "age", "dtype": {"kind": "String"}}]},
        )

        assert serialised["age"] == [
            {"__factor_group__": "25.0", "optimal_scenario_value": 1.1, "quote_count": 3},
        ]

    def test_serialise_ratebook_factor_tables_canonicalises_composite_components(self):
        """3b.10: composite levels split on the unit separator and
        canonicalise per component — string parts verbatim, float parts
        collapsed."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"channel:age": {"online\x1f25.0": 1.1, "phone\x1f30.5": 0.9}},
            {"channel:age": {"online\x1f25": 2, "phone\x1f30.5": 1}},
            {},
            {
                "channel:age": [
                    {"column": "channel", "dtype": {"kind": "String"}},
                    {"column": "age", "dtype": {"kind": "Float64"}},
                ]
            },
        )

        assert [row["__factor_group__"] for row in serialised["channel:age"]] == [
            "online\x1f25",
            "phone\x1f30.5",
        ]
        assert [row["quote_count"] for row in serialised["channel:age"]] == [2, 1]

    def test_serialise_ratebook_factor_tables_uses_ordered_component_dtypes(self):
        """3b.10: neighbouring counted keys cannot confuse canonicalisation;
        each solver component is reconstructed through its exact dtype."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"code:age": {"25.0\x1f7.0": 1.1}},
            {"code:age": {"25.0\x1f7": 2, "25\x1f7": 5}},
            {},
            {
                "code:age": [
                    {"column": "code", "dtype": {"kind": "String"}},
                    {"column": "age", "dtype": {"kind": "Float64"}},
                ]
            },
        )

        assert serialised["code:age"] == [
            {"__factor_group__": "25.0\x1f7", "optimal_scenario_value": 1.1, "quote_count": 2},
        ]

    @pytest.mark.parametrize(
        ("dtypes", "expected"),
        [
            (
                [
                    {"column": "code", "dtype": {"kind": "Float64"}},
                    {"column": "age", "dtype": {"kind": "String"}},
                ],
                "25\x1f7.0",
            ),
            (
                [
                    {"column": "code", "dtype": {"kind": "String"}},
                    {"column": "age", "dtype": {"kind": "Float64"}},
                ],
                "25.0\x1f7",
            ),
        ],
    )
    def test_serialise_ratebook_factor_tables_has_no_candidate_ambiguity(
        self,
        dtypes,
        expected,
    ):
        """Exact component dtypes select one key even when both old
        heuristic candidates exist in the counts."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"code:age": {"25.0\x1f7.0": 1.1}},
            {"code:age": {"25\x1f7.0": 1, "25.0\x1f7": 1}},
            {},
            {"code:age": dtypes},
        )

        assert serialised["code:age"][0]["__factor_group__"] == expected

    def test_serialise_ratebook_factor_tables_rejects_colliding_levels(self):
        """3b.10 collision rule: a table carrying both "25" and "25.0"
        (possible only if the solver input mixed value types in one factor
        column) must fail loudly naming the colliding levels — never
        last-writer-wins."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        with pytest.raises(InvalidRequestError, match="collide|canonicalise") as exc_info:
            _serialise_ratebook_factor_tables(
                {"age": {"25": 1.1, "25.0": 1.2}},
                {"age": {"25": 3}},
                {},
                {"age": [{"column": "age", "dtype": {"kind": "Float64"}}]},
            )
        detail = str(exc_info.value)
        assert "'25'" in detail
        assert "'25.0'" in detail
        assert "age" in detail

    def test_serialise_ratebook_factor_tables_missing_canonical_count_fails(self):
        """3b.10: a level whose canonical forms are all absent from the
        counts still fails loudly (the pre-existing missing-counts
        contract)."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        with pytest.raises(ValueError, match="counts missing"):
            _serialise_ratebook_factor_tables(
                {"age": {"99.0": 1.0}},
                {"age": {"25": 1}},
                {},
                {"age": [{"column": "age", "dtype": {"kind": "Float64"}}]},
            )

    def test_serialise_ratebook_factor_tables_canonicalises_value_typed_levels(self):
        """3b.10: hand-built tables may key levels with raw values (a float
        25.0 instead of a label); these canonicalise exactly through
        ``normalise_rating_key`` and keep the loud missing-counts contract."""
        from haute.routes._optimiser_solver import _serialise_ratebook_factor_tables

        serialised = _serialise_ratebook_factor_tables(
            {"age": {25.0: 1.1}},
            {"age": {"25": 3}},
            {},
            {"age": [{"column": "age", "dtype": {"kind": "Float64"}}]},
        )
        assert serialised["age"] == [
            {"__factor_group__": "25", "optimal_scenario_value": 1.1, "quote_count": 3},
        ]

        with pytest.raises(ValueError, match="counts missing"):
            _serialise_ratebook_factor_tables(
                {"age": {99.0: 1.0}},
                {"age": {"25": 3}},
                {},
                {"age": [{"column": "age", "dtype": {"kind": "Float64"}}]},
            )

    def test_solve_ratebook_rejects_null_factor_values_before_solver(self):
        """Null banding levels should fail loudly before price-contour runs."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _OptimiserSolveInputError, _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2", "q3"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 3

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

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with patch("price_contour.RatebookOptimiser") as mock_solver:
                with pytest.raises(_OptimiserSolveInputError) as exc_info:
                    _solve_ratebook(
                        SolveContext(
                            job_id=job_id,
                            node_id="opt",
                            mode="ratebook",
                            store=store,
                            start_time=time.monotonic(),
                        ),
                        quote_grid=mock_grid,
                        config=config,
                        ratebook_factors_handle=factors_handle,
                    )

            detail = str(exc_info.value)
            assert "contains null values" in detail
            assert "region" in detail
            mock_solver.assert_not_called()

    def test_solve_ratebook_frontier_passes_prepared_factors(self):
        """Ratebook frontier-in-solve passes prepared factor contexts."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_artifacts import (
            _RATEBOOK_FACTORS_HANDLE_KEY,
            _scan_ratebook_factors_artifact,
        )
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 2
        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "region": ["North", "South"],
            }
        )
        # 3b.9: real RatebookResult field set — no phantom ``dataframe``.
        mock_result = _ratebook_solve_result_namespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            lambdas={"volume": 0.5},
            cd_iterations=3,
            factor_tables={"region": {"North": 1.0, "South": 1.0}},
        )
        frontier_points = library_frontier_frame(
            [
                {
                    "total_objective": 100.0,
                    "total_volume": 0.9,
                    "lambda_volume": 0.25,
                    "bound_volume": 0.9,
                    "converged": True,
                }
            ],
            constraint_names=["volume"],
            mode="ratebook",
        )
        config = {
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "factor_columns": [["region"]],
            "quote_id": "quote_id",
            "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
            "frontier_steps": 3,
        }

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with patch("price_contour.RatebookOptimiser") as mock_solver:
                solver = mock_solver.return_value
                solver.solve.return_value = mock_result
                solver.frontier.return_value = SimpleNamespace(points=frontier_points)
                _solve_ratebook(
                    SolveContext(
                        job_id=job_id,
                        node_id="opt",
                        mode="ratebook",
                        store=store,
                        start_time=time.monotonic(),
                    ),
                    quote_grid=mock_grid,
                    config=config,
                    ratebook_factors_handle=factors_handle,
                )

            assert mock_solver.call_args.kwargs["factor_columns"] == [["region"]]
            prepared_factors = solver.frontier.call_args.args[1]
            assert solver.frontier.call_args.args[0] is mock_grid
            assert prepared_factors.n_quotes == 2
            assert prepared_factors.factor_specs == [["region"]]
            assert solver.frontier.call_args.kwargs["threshold_ranges"]["volume"] == pytest.approx(
                (0.8, 1.1)
            )
            assert solver.frontier.call_args.kwargs["n_points_per_dim"] == 3
            assert solver.frontier.call_args.kwargs["factor_columns"] == [["region"]]
            job = store.require_job(job_id)
            assert job["result"]["frontier"]["n_points"] == 1
            assert "ratebook_factor_contexts" in job
            factors_handle = job["artifact_handles"][_RATEBOOK_FACTORS_HANDLE_KEY]
            assert _scan_ratebook_factors_artifact(factors_handle).collect().to_dicts() == [
                {"quote_id": "q1", "region": "North"},
                {"quote_id": "q2", "region": "South"},
            ]

    def test_solve_ratebook_custom_quote_id(self):
        """Ratebook solve with custom quote_id column renames it."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _solve_ratebook

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {}},
            }
        )

        mock_grid = MagicMock()
        mock_grid.quote_ids = ["q1", "q2"]
        mock_grid.scenario_values = [0.9, 1.0, 1.1]
        mock_grid.n_quotes = 2

        factors_df = pl.DataFrame(
            {
                "policy_id": ["q1", "q2"],
                "region": ["North", "South"],
            }
        )

        # 3b.9: real RatebookResult field set — no phantom ``dataframe``.
        mock_result = _ratebook_solve_result_namespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            constraint_bounds={},
            baseline_constraints={},
            lambdas={},
            cd_iterations=2,
            factor_tables={"region": {"North": 1.0, "South": 1.0}},
        )

        config = {
            "objective": "income",
            "constraints": {},
            "factor_columns": [["region"]],
            "quote_id": "policy_id",
        }

        with _persisted_ratebook_factors_handle(factors_df) as factors_handle:
            with patch("price_contour.RatebookOptimiser") as mock_solver:
                mock_solver.return_value.solve.return_value = mock_result
                _solve_ratebook(
                    SolveContext(
                        job_id=job_id,
                        node_id="opt",
                        mode="ratebook",
                        store=store,
                        start_time=time.monotonic(),
                    ),
                    quote_grid=mock_grid,
                    config=config,
                    ratebook_factors_handle=factors_handle,
                )

            job = store.require_job(job_id)
            assert job["status"] == "completed"

    def test_solve_ratebook_banding_source_resolution(self):
        """Ratebook _extract_factors resolves banding source from lazy outputs."""
        from haute.routes._optimiser_service import OptimiserSolveService

        mock_df = pl.DataFrame({"quote_id": ["q1"], "region": ["N"]})

        lazy_outputs = {"banding_node": mock_df.lazy()}
        config = {"banding_source": "banding_node", "factor_columns": [["region"]]}

        result = OptimiserSolveService._extract_factors(
            lazy_outputs, _optimiser_helper_graph("banding_node"), "opt", config, "ratebook"
        )
        assert result["kind"] == "optimiser_ratebook_factors"
        assert result["columns"] == ["quote_id", "region"]
        assert pl.read_parquet(result["path"]).to_dicts() == [{"quote_id": "q1", "region": "N"}]

    def test_extract_factors_online_returns_none(self):
        """_extract_factors returns None for online mode."""
        from haute.routes._optimiser_service import OptimiserSolveService

        result = OptimiserSolveService._extract_factors(
            {}, _optimiser_helper_graph(), "opt", {}, "online"
        )
        assert result is None

    def test_extract_factors_no_banding_source(self):
        """_extract_factors fails loudly when banding source not found."""
        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError, match="missing_node"):
            OptimiserSolveService._extract_factors(
                {"other_node": MagicMock()},
                _optimiser_helper_graph("missing_node"),
                "opt",
                {"banding_source": "missing_node", "factor_columns": [["region"]]},
                "ratebook",
            )

    def test_extract_factors_post_sink_checkpoint_memory_limit_cleans_artifact(self):
        """Regression for bug_001: if the post-sink checkpoint raises
        ExecutionMemoryLimitExceededError, the freshly-written factor
        artifact directory must be cleaned up before the exception
        propagates. Otherwise the caller's handle is never assigned and
        the artifact leaks past the per-job TemporaryDirectory.
        """
        from pathlib import Path

        from haute._execution_context import (
            ExecutionContext,
            ExecutionMemoryLimitExceededError,
            ExecutionProfile,
        )
        from haute.routes._optimiser_service import OptimiserSolveService

        mock_df = pl.DataFrame({"quote_id": ["q1", "q2"], "region": ["N", "S"]})
        lazy_outputs = {"banding_node": mock_df.lazy()}
        config = {"banding_source": "banding_node", "factor_columns": [["region"]]}

        # Sampler returns a low value until the sink completes; then jumps over
        # the limit so the post-sink checkpoint raises ExecutionMemoryLimitExceededError.
        rss_state: dict[str, int] = {"current": 100}
        execution_context = ExecutionContext(
            operation="optimiser_solve",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_limit_bytes=1_000,
            memory_baseline_bytes=100,
            rss_limit_bytes=1_100,
            memory_sampler=lambda: rss_state["current"],
        )

        captured: dict[str, Path] = {}
        from haute.routes import _optimiser_artifacts

        original_persist = _optimiser_artifacts._persist_ratebook_factors_lazy_artifact

        def recording_persist(factors_lf, **kwargs):
            handle = original_persist(factors_lf, **kwargs)
            captured["directory"] = Path(handle["directory"])
            # Simulate RSS climbing past the admitted ceiling after the sink.
            rss_state["current"] = 10_000
            return handle

        with (
            patch.object(
                _optimiser_artifacts, "_persist_ratebook_factors_lazy_artifact", recording_persist
            ),
            pytest.raises(ExecutionMemoryLimitExceededError),
        ):
            OptimiserSolveService._extract_factors(
                lazy_outputs,
                _optimiser_helper_graph("banding_node"),
                "opt",
                config,
                "ratebook",
                execution_context=execution_context,
            )

        assert "directory" in captured, "ratebook factor artifact should have been written"
        assert not captured["directory"].exists(), (
            "ratebook factor artifact directory must be cleaned up when the "
            "post-sink checkpoint raises"
        )

    def test_extract_factors_post_sink_checkpoint_cancellation_cleans_artifact(self):
        """Same as the memory-limit case but for ExecutionCancelledError."""
        from pathlib import Path

        from haute._execution_context import (
            ExecutionCancellationToken,
            ExecutionCancelledError,
            ExecutionContext,
            ExecutionProfile,
        )
        from haute.routes._optimiser_service import OptimiserSolveService

        mock_df = pl.DataFrame({"quote_id": ["q1"], "region": ["N"]})
        lazy_outputs = {"banding_node": mock_df.lazy()}
        config = {"banding_source": "banding_node", "factor_columns": [["region"]]}

        token = ExecutionCancellationToken()
        execution_context = ExecutionContext(
            operation="optimiser_solve",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            cancellation_token=token,
            memory_sampler=lambda: 1_000,
        )

        captured: dict[str, Path] = {}
        from haute.routes import _optimiser_artifacts

        original_persist = _optimiser_artifacts._persist_ratebook_factors_lazy_artifact

        def recording_persist(factors_lf, **kwargs):
            handle = original_persist(factors_lf, **kwargs)
            captured["directory"] = Path(handle["directory"])
            # Trigger cancellation between sink and checkpoint.
            token.cancel()
            return handle

        with (
            patch.object(
                _optimiser_artifacts, "_persist_ratebook_factors_lazy_artifact", recording_persist
            ),
            pytest.raises(ExecutionCancelledError),
        ):
            OptimiserSolveService._extract_factors(
                lazy_outputs,
                _optimiser_helper_graph("banding_node"),
                "opt",
                config,
                "ratebook",
                execution_context=execution_context,
            )

        assert "directory" in captured, "ratebook factor artifact should have been written"
        assert not captured["directory"].exists(), (
            "ratebook factor artifact directory must be cleaned up when the "
            "post-sink cancellation token fires"
        )
