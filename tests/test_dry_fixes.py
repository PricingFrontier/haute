"""Tests for DRY refactors D6, D7, D12, D13.

D6  — _finalize_solve_result extracted from _solve_online / _solve_ratebook
D7  — _dc_to_pydantic auto-converts dataclasses → Pydantic models
D12 — PreviewNodeResponse inherits from NodeResult
D13 — LogExperimentResponse and OptimiserMlflowLogResponse share MlflowLogResponse
"""

from __future__ import annotations

from typing import Any

from haute.routes._job_store import JobStore

# ──────────────────────────────────────────────────────────────────────
# D6: _finalize_solve_result
# ──────────────────────────────────────────────────────────────────────


class _FakeSolveResult:
    """Minimal duck-type of SolveResultLike for testing _finalize_solve_result."""

    def __init__(
        self,
        *,
        converged: bool = True,
        total_objective: float = 100.0,
        baseline_objective: float = 95.0,
        total_constraints: dict[str, float] | None = None,
        baseline_constraints: dict[str, float] | None = None,
        lambdas: dict[str, float] | None = None,
    ) -> None:
        self.converged = converged
        self.total_objective = total_objective
        self.baseline_objective = baseline_objective
        self.total_constraints = total_constraints or {"loss": 1.0}
        self.baseline_constraints = baseline_constraints or {"loss": 0.9}
        self.lambdas = lambdas or {"loss": 0.5}


class TestFinalizeOnline:
    """D6: _finalize_solve_result for online mode."""

    def test_shared_keys_present(self) -> None:
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(converged=True)
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="online",
            solver="fake_solver",
            quote_grid="fake_grid",
            store=store,
            job_id=job_id,
            elapsed=1.23,
        )
        job = store.get_job(job_id)
        rd = job["result"]
        assert rd["mode"] == "online"
        assert rd["total_objective"] == 100.0
        assert rd["baseline_objective"] == 95.0
        assert rd["constraints"] == {"loss": 1.0}
        assert rd["baseline_constraints"] == {"loss": 0.9}
        assert rd["lambdas"] == {"loss": 0.5}
        assert rd["converged"] is True
        assert "warning" not in rd

    def test_extra_fields_merged(self) -> None:
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult()
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="online",
            solver="s",
            quote_grid="g",
            store=store,
            job_id=job_id,
            elapsed=0.5,
            extra_fields={"iterations": 42, "n_quotes": 100},
        )
        job = store.get_job(job_id)
        rd = job["result"]
        assert rd["iterations"] == 42
        assert rd["n_quotes"] == 100

    def test_non_converged_adds_warning(self) -> None:
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(converged=False)
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="ratebook",
            solver="s",
            quote_grid="g",
            store=store,
            job_id=job_id,
            elapsed=2.0,
        )
        job = store.get_job(job_id)
        rd = job["result"]
        assert rd["converged"] is False
        assert "warning" in rd
        assert "not converge" in rd["warning"]

    def test_job_status_fields(self) -> None:
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult()
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="online",
            solver="my_solver",
            quote_grid="my_grid",
            store=store,
            job_id=job_id,
            elapsed=3.14,
        )
        job = store.get_job(job_id)
        assert job["status"] == "completed"
        assert job["progress"] == 1.0
        assert job["message"] == "Completed"
        assert job["elapsed_seconds"] == 3.14
        # solver and quote_grid are intentionally cleared after finalization
        # to free memory (they can be 100s of MB for large solves)
        assert "solver" not in job
        assert "quote_grid" not in job
        assert job["solve_result"] is result

    def test_scenario_value_stats_populated_when_dataframe_present(self) -> None:
        """When solve_result has a dataframe with optimal_scenario_value,
        stats and histogram should be populated."""
        import numpy as np

        from haute.routes._optimiser_service import _finalize_solve_result

        class ResultWithDF(_FakeSolveResult):
            def __init__(self, **kw: Any) -> None:
                super().__init__(**kw)
                # Minimal duck-type of a column
                self.dataframe = type(
                    "DF",
                    (),
                    {
                        "columns": ["optimal_scenario_value"],
                        "__getitem__": lambda self, key: type(
                            "Col",
                            (),
                            {
                                "mean": lambda s: 1.05,
                                "std": lambda s: 0.1,
                                "min": lambda s: 0.9,
                                "max": lambda s: 1.2,
                                "quantile": lambda s, q: 1.0 + q * 0.1,
                                "sum": lambda s: 5,
                                "__gt__": lambda s, v: type("Mask", (), {"sum": lambda s: 3})(),
                                "__lt__": lambda s, v: type("Mask", (), {"sum": lambda s: 2})(),
                                "__len__": lambda s: 10,
                                "to_numpy": lambda s: np.array(
                                    [1.0, 1.05, 0.95, 1.1, 0.98, 1.02, 1.03, 0.97, 1.01, 1.04]
                                ),
                            },
                        )(),
                    },
                )()

        result = ResultWithDF()
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="online",
            solver="s",
            quote_grid="g",
            store=store,
            job_id=job_id,
            elapsed=0.1,
        )
        job = store.get_job(job_id)
        rd = job["result"]
        assert rd["scenario_value_stats"]
        assert rd["scenario_value_histogram"]

    def test_no_extra_fields_when_none(self) -> None:
        """extra_fields=None should not add any extra keys."""
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult()
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="online",
            solver="s",
            quote_grid="g",
            store=store,
            job_id=job_id,
            elapsed=0.1,
            extra_fields=None,
        )
        job = store.get_job(job_id)
        rd = job["result"]
        # Should not have mode-specific keys like iterations or cd_iterations
        assert "iterations" not in rd
        assert "cd_iterations" not in rd


class TestFinalizeRatebook:
    """D6: _finalize_solve_result for ratebook mode."""

    def test_ratebook_extra_fields(self) -> None:
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(converged=True)
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        _finalize_solve_result(
            result,
            mode="ratebook",
            solver="rb_solver",
            quote_grid="rb_grid",
            store=store,
            job_id=job_id,
            elapsed=5.0,
            extra_fields={
                "cd_iterations": 7,
                "factor_tables": {
                    "age": [{"__factor_group__": "young", "optimal_scenario_value": 1.1}]
                },
                "clamp_rate": 0.05,
                "history": None,
            },
        )
        job = store.get_job(job_id)
        rd = job["result"]
        assert rd["mode"] == "ratebook"
        assert rd["cd_iterations"] == 7
        assert rd["factor_tables"]["age"][0]["__factor_group__"] == "young"
        assert rd["clamp_rate"] == 0.05
        assert rd["history"] is None


# ──────────────────────────────────────────────────────────────────────
# T2: _finalize_solve_result — frontier computation
# ──────────────────────────────────────────────────────────────────────


class TestFinalizeFrontier:
    """T2: Frontier computation within _finalize_solve_result."""

    def test_computes_frontier_when_online_with_constraints(self) -> None:
        """Online mode + constraints → frontier_data populated on the job."""
        from unittest.mock import MagicMock

        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(
            converged=True,
            baseline_constraints={"loss": 0.9},
        )
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "mode": "online",
                    "constraints": {"loss": {"max": 1.05}},
                },
            }
        )

        # Mock solver with a frontier() method that returns a FrontierResult
        mock_solver = MagicMock()
        mock_points = MagicMock()
        mock_points.to_dicts.return_value = [
            {"total_objective": 100.0, "total_loss": 0.92, "lambda_loss": 0.01},
            {"total_objective": 105.0, "total_loss": 0.95, "lambda_loss": 0.02},
        ]
        mock_points.__len__ = lambda self: 2
        mock_frontier_result = MagicMock()
        mock_frontier_result.points = mock_points
        mock_solver.frontier.return_value = mock_frontier_result

        _finalize_solve_result(
            result,
            mode="online",
            solver=mock_solver,
            quote_grid="fake_grid",
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.get_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["status"] == "ok"
        assert job["frontier_data"]["n_points"] == 2
        assert len(job["frontier_data"]["points"]) == 2
        assert "loss" in job["frontier_data"]["constraint_names"]
        # Also stored in result dict for the frontend
        assert job["result"]["frontier"] is not None

    def test_frontier_skipped_for_ratebook(self) -> None:
        """Ratebook mode → frontier_data is None."""
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(converged=True)
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "mode": "ratebook",
                    "constraints": {"loss": {"max": 1.05}},
                },
            }
        )

        _finalize_solve_result(
            result,
            mode="ratebook",
            solver="fake_solver",
            quote_grid="fake_grid",
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.get_job(job_id)
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None

    def test_frontier_skipped_no_constraints(self) -> None:
        """Online mode + empty constraints → frontier_data is None."""
        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(converged=True)
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "mode": "online",
                    "constraints": {},
                },
            }
        )

        _finalize_solve_result(
            result,
            mode="online",
            solver="fake_solver",
            quote_grid="fake_grid",
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.get_job(job_id)
        assert job["frontier_data"] is None

    def test_frontier_exception_non_fatal(self) -> None:
        """solver.frontier() raising does not fail the solve — status is still completed."""
        from unittest.mock import MagicMock

        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(
            converged=True,
            baseline_constraints={"loss": 0.9},
        )
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "mode": "online",
                    "constraints": {"loss": {"max": 1.05}},
                },
            }
        )

        mock_solver = MagicMock()
        mock_solver.frontier.side_effect = RuntimeError("Frontier blew up")

        _finalize_solve_result(
            result,
            mode="online",
            solver=mock_solver,
            quote_grid="fake_grid",
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.get_job(job_id)
        assert job["status"] == "completed"
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None

    def test_frontier_skips_zero_baseline(self) -> None:
        """A constraint with baseline=0 is excluded from frontier ranges."""
        from unittest.mock import MagicMock

        from haute.routes._optimiser_service import _finalize_solve_result

        result = _FakeSolveResult(
            converged=True,
            baseline_constraints={"loss": 0.9, "zero_cstr": 0.0},
        )
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "mode": "online",
                    "constraints": {
                        "loss": {"max": 1.05},
                        "zero_cstr": {"max": 1.0},
                    },
                },
            }
        )

        mock_solver = MagicMock()
        mock_points = MagicMock()
        mock_points.to_dicts.return_value = [
            {"total_objective": 100.0, "total_loss": 0.92, "lambda_loss": 0.01},
        ]
        mock_points.__len__ = lambda self: 1
        mock_frontier_result = MagicMock()
        mock_frontier_result.points = mock_points
        mock_solver.frontier.return_value = mock_frontier_result

        _finalize_solve_result(
            result,
            mode="online",
            solver=mock_solver,
            quote_grid="fake_grid",
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        # The frontier was called with ranges that exclude zero_cstr
        call_kwargs = mock_solver.frontier.call_args
        ranges = call_kwargs[1]["threshold_ranges"] if call_kwargs[1] else call_kwargs[0][1]
        # Only "loss" should be in ranges, not "zero_cstr"
        assert "loss" in ranges
        assert "zero_cstr" not in ranges

        job = store.get_job(job_id)
        assert job["frontier_data"] is not None
        assert "loss" in job["frontier_data"]["constraint_names"]


# ──────────────────────────────────────────────────────────────────────
# D7: Pydantic-native git result types (item #74)
# ──────────────────────────────────────────────────────────────────────
#
# The ``_git`` module returns ``GitStatusResponse``, ``GitSaveResponse``, etc.
# directly — the former dataclass → Pydantic shim was dead code and has been
# removed, and the historical ``GitStatus`` / ``BranchInfo`` / ``SaveResult``
# aliases have been renamed to their canonical response-model names since
# nothing outside ``_git`` itself was using the alias form.
# Dedicated tests for the contract live in ``tests/test_git_routes_pydantic.py``.


# ──────────────────────────────────────────────────────────────────────
# D12: PreviewNodeResponse inherits from NodeResult
# ──────────────────────────────────────────────────────────────────────


class TestPreviewNodeResponseInheritance:
    """D12: PreviewNodeResponse should inherit all NodeResult fields."""

    def test_is_subclass(self) -> None:
        from haute.schemas import NodeResult, PreviewNodeResponse

        assert issubclass(PreviewNodeResponse, NodeResult)

    def test_inherits_all_node_result_fields(self) -> None:
        from haute.schemas import NodeResult, PreviewNodeResponse

        node_fields = set(NodeResult.model_fields.keys())
        preview_fields = set(PreviewNodeResponse.model_fields.keys())
        assert node_fields.issubset(preview_fields), (
            f"Missing fields: {node_fields - preview_fields}"
        )

    def test_serialization_includes_inherited_and_own_fields(self) -> None:
        from haute.schemas import ColumnInfo, PreviewNodeResponse

        resp = PreviewNodeResponse(
            node_id="n1",
            status="ok",
            row_count=42,
            column_count=3,
            columns=[ColumnInfo(name="a", dtype="Int64")],
            preview=[{"a": 1}],
            error=None,
            timing_ms=12.5,
            memory_bytes=1024,
            timings=[],
            memory=[],
            node_statuses={"n1": "ok", "n2": "ok"},
        )
        d = resp.model_dump()
        # Inherited from NodeResult
        assert d["status"] == "ok"
        assert d["row_count"] == 42
        assert d["column_count"] == 3
        assert d["columns"] == [{"name": "a", "dtype": "Int64"}]
        assert d["timing_ms"] == 12.5
        assert d["memory_bytes"] == 1024
        assert d["error"] is None
        # Own fields
        assert d["node_id"] == "n1"
        assert d["node_statuses"] == {"n1": "ok", "n2": "ok"}

    def test_deserialization_from_dict(self) -> None:
        from haute.schemas import PreviewNodeResponse

        data = {
            "node_id": "n2",
            "status": "error",
            "error": "bad column",
            "error_line": 5,
            "row_count": 0,
        }
        resp = PreviewNodeResponse.model_validate(data)
        assert resp.node_id == "n2"
        assert resp.status == "error"
        assert resp.error == "bad column"
        assert resp.error_line == 5
        assert resp.row_count == 0

    def test_defaults_from_node_result(self) -> None:
        """Fields with defaults in NodeResult should have same defaults."""
        from haute.schemas import PreviewNodeResponse

        resp = PreviewNodeResponse(node_id="n1", status="ok")
        assert resp.row_count == 0
        assert resp.column_count == 0
        assert resp.columns == []
        assert resp.preview == []
        assert resp.error is None
        assert resp.timing_ms == 0
        assert resp.memory_bytes == 0
        assert resp.schema_warnings == []

    def test_node_result_standalone_still_works(self) -> None:
        """NodeResult should still work independently (not broken by inheritance)."""
        from haute.schemas import NodeResult

        nr = NodeResult(status="ok", row_count=10, column_count=2)
        d = nr.model_dump()
        assert d["status"] == "ok"
        assert d["row_count"] == 10
        assert "node_id" not in d  # Not in NodeResult


# ──────────────────────────────────────────────────────────────────────
# D13: MlflowLogResponse shared base
# ──────────────────────────────────────────────────────────────────────


class TestMlflowLogResponseSharedBase:
    """D13: LogExperimentResponse and OptimiserMlflowLogResponse share a base."""

    def test_both_subclass_base(self) -> None:
        from haute.schemas import (
            LogExperimentResponse,
            MlflowLogResponse,
            OptimiserMlflowLogResponse,
        )

        assert issubclass(LogExperimentResponse, MlflowLogResponse)
        assert issubclass(OptimiserMlflowLogResponse, MlflowLogResponse)

    def test_identical_fields(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        log_fields = set(LogExperimentResponse.model_fields.keys())
        opt_fields = set(OptimiserMlflowLogResponse.model_fields.keys())
        assert log_fields == opt_fields

    def test_identical_serialization(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        kwargs = {
            "status": "ok",
            "backend": "databricks",
            "experiment_name": "/my/experiment",
            "run_id": "abc123",
            "run_url": "https://example.com/run/abc123",
            "tracking_uri": "databricks",
        }
        log_resp = LogExperimentResponse(**kwargs)
        opt_resp = OptimiserMlflowLogResponse(**kwargs)
        assert log_resp.model_dump() == opt_resp.model_dump()

    def test_error_case_serialization(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        kwargs = {
            "status": "error",
            "error": "MLflow server unreachable",
        }
        log_resp = LogExperimentResponse(**kwargs)
        opt_resp = OptimiserMlflowLogResponse(**kwargs)
        assert log_resp.model_dump() == opt_resp.model_dump()
        assert log_resp.error == "MLflow server unreachable"

    def test_defaults(self) -> None:
        from haute.schemas import MlflowLogResponse

        resp = MlflowLogResponse(status="ok")
        assert resp.backend == ""
        assert resp.experiment_name == ""
        assert resp.run_id is None
        assert resp.run_url is None
        assert resp.tracking_uri == ""
        assert resp.error is None

    def test_run_id_accepts_string_and_none(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        # String run_id
        r1 = LogExperimentResponse(status="ok", run_id="run123")
        assert r1.run_id == "run123"

        # None run_id (default)
        r2 = OptimiserMlflowLogResponse(status="ok")
        assert r2.run_id is None

    def test_base_model_dump_matches_children(self) -> None:
        """Base class should produce same JSON shape as children."""
        from haute.schemas import (
            LogExperimentResponse,
            MlflowLogResponse,
            OptimiserMlflowLogResponse,
        )

        kwargs = {"status": "ok", "backend": "local", "run_id": "x"}
        base = MlflowLogResponse(**kwargs)
        child1 = LogExperimentResponse(**kwargs)
        child2 = OptimiserMlflowLogResponse(**kwargs)
        assert base.model_dump() == child1.model_dump() == child2.model_dump()
