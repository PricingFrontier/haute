"""Optimiser node registration and the solve, status, apply and save routes.

The optimiser node type registers, generates code and passes data through the executor; ``/solve``
launches a background job whose status, timeout, cancellation and error categorisation are reported
through ``/solve/status``; ``/apply`` and ``/save`` reject incomplete jobs and map their failures;
and a solve runs end to end on the real solver.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._types import PipelineGraph
from haute.errors import InvalidRequestError, NotFoundError, RequestRefusedError
from haute.graph_utils import NodeType
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._optimiser_solver import SolveContext
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _anchor_result,
    _make_optimiser_graph,
    _make_scored_data,
    _poll_until_done,
    _snapshot_parquet_data_input,
    _wait_for_store_job_terminal,
)
from tests._project import use_project
from tests.conftest import make_edge, make_graph
from tests.job_store_support import replace_job, seed_job
from tests.optimiser_fixtures import (
    SOLVE_PROVENANCE,
    SOLVE_SCENARIO_GRID,
    make_completed_job,
    make_frontier_data,
    make_frontier_point,
    make_input_summary,
    make_online_apply_frame,
    setup_grid_stub,
    setup_launch_context,
    typed_frontier_point,
)

# Fixtures shared with the other modules split from one suite.
scored_data = _optimiser_route_helpers.scored_data


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


# ---------------------------------------------------------------------------
# Step 1d: Codegen
# ---------------------------------------------------------------------------


class TestCodegen:
    def test_codegen_optimiser(self, tmp_path: Path):
        """An optimiser is a declaration referencing its sidecar; when the saved
        file runs on its own, its decorator passes the scored frame through."""
        import runpy

        from haute._config_io import collect_node_configs
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
        assert code == (
            '@pipeline.optimiser(config="config/optimisation/my_optimiser.json")\n'
            "def my_optimiser(scored_data): ...\n"
        )

        module = tmp_path / "main.py"
        module.write_text(
            f'import haute\n\npipeline = haute.Pipeline("p")\n\n\n{code}', encoding="utf-8"
        )
        for rel_path, content in collect_node_configs(
            PipelineGraph(nodes=[node], edges=[])
        ).items():
            sidecar = tmp_path / rel_path
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(content, encoding="utf-8")
        scored = pl.DataFrame({"expected_income": [1.0]})
        assert runpy.run_path(str(module))["my_optimiser"](scored) is scored


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

    @pytest.mark.parametrize("key", ["timeout", "auto_range_timeout"])
    def test_solve_refuses_a_config_timeout(self, client, scored_data, key):
        """The time limit comes from the pipeline settings; a config cannot set one."""
        graph = _make_optimiser_graph(scored_data, config={key: 60})

        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 400
        assert f"does not declare: {key!r}" in resp.json()["detail"]

    @pytest.mark.parametrize(
        "route",
        [
            # A solve and an auto-range each read the optimisation time limit.
            "/api/optimiser/solve",
            "/api/optimiser/frontier/auto-range/start",
        ],
    )
    def test_an_invalid_settings_file_answers_409_rather_than_a_config_error(
        self, client, scored_data, monkeypatch, route
    ):
        """A broken settings file is the application's 409 with its message, not a 400."""
        from haute import _pipeline_settings

        message = (
            f"{_pipeline_settings.SETTINGS_PATH}: optimisation_time_limit_minutes must be "
            "a number of minutes greater than 0"
        )

        def refuse(_root):
            raise _pipeline_settings.PipelineSettingsError(message)

        monkeypatch.setattr(_pipeline_settings, "read_pipeline_settings", refuse)
        graph = _make_optimiser_graph(scored_data)

        resp = client.post(route, json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 409
        assert resp.json()["detail"] == message

    def test_solve_rejects_concurrent(self, client, scored_data, clean_job_store):
        """A second solve request while one is running returns 409."""
        from haute.routes.optimiser import _solve_service

        seed_job(
            clean_job_store,
            "fake_running",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Solving...",
                "created_at": time.time(),
            },
        )
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

        seed_job(
            clean_job_store,
            "fake_running_solve",
            {
                "status": "running",
                "job_type": "solve",
                "progress": 0.5,
                "message": "Solving...",
                "created_at": time.time(),
            },
        )
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

        seed_job(
            clean_job_store,
            "running_estimate",
            {
                "status": "running",
                "job_type": "estimate",
                "progress": 0.0,
                "message": "Estimating optimiser input",
                "created_at": time.time(),
            },
        )
        seed_job(
            clean_job_store,
            "running_frontier_auto_range",
            {
                "status": "running",
                "job_type": "frontier_auto_range",
                "progress": 0.0,
                "message": "Estimating frontier range",
                "created_at": time.time(),
            },
        )
        graph = _make_optimiser_graph(scored_data)
        lazy_outputs = {
            "source": pl.LazyFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "expected_income": [100.0],
                    "volume": [1.0],
                }
            )
        }

        launch_called = threading.Event()
        with (
            patch.object(_solve_service, "_execute_pipeline", return_value=lazy_outputs),
            patch.object(_solve_service, "_build_grid", return_value=setup_grid_stub()),
            patch.object(
                _solve_service,
                "_launch_background",
                side_effect=lambda *args, **kwargs: launch_called.set(),
            ) as launch_background,
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            assert launch_called.wait(timeout=10.0)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        started_job = clean_job_store.require_job(data["job_id"])
        assert started_job["job_type"] == "solve"
        launch_background.assert_called_once()

    def test_solve_returns_job_id_before_slow_setup_finishes(self, scored_data):
        """Large-data setup must be pollable instead of living inside the HTTP request."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph, node_id="opt")
        scored_lf = pl.scan_parquet(scored_data)
        store = JobStore()
        service = OptimiserSolveService(store)
        setup_started = threading.Event()
        release_setup = threading.Event()
        launch_called = threading.Event()

        def slow_execute(*args, **kwargs):
            setup_started.set()
            assert release_setup.wait(timeout=10.0), "setup was never released"
            return {"source": scored_lf}

        def mark_launched(*args, **kwargs):
            launch_called.set()

        with (
            patch.object(service, "_execute_pipeline", side_effect=slow_execute),
            patch.object(service, "_validate_and_project", return_value=(["volume"], scored_lf)),
            patch.object(service, "_extract_factors", return_value=None),
            patch.object(service, "_build_grid", return_value=setup_grid_stub()),
            patch.object(service, "_launch_background", side_effect=mark_launched),
        ):
            response = service.start(body)
            # ``start`` returned while setup is still blocked on release_setup,
            # which is what "off the HTTP request" means. Holding setup open
            # states that directly; an elapsed-time bound would only assert
            # that a loaded runner beat an arbitrary wall-clock margin.
            assert setup_started.wait(timeout=10.0)
            assert not launch_called.is_set()
            release_setup.set()
            assert launch_called.wait(timeout=10.0)

        assert response.status == "started"
        assert response.job_id is not None
        assert store.require_job(response.job_id)["status"] == "running"

    def test_solve_setup_http_failure_becomes_pollable_status(self, scored_data):
        """Setup failures after job creation must be visible to the poller."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)

        with (
            patch.object(
                service,
                "_execute_pipeline",
                side_effect=InvalidRequestError("bad setup"),
            ),
            patch.object(service, "_launch_background") as launch_background,
        ):
            response = service.start(body)
            assert response.job_id is not None
            job = _wait_for_store_job_terminal(store, response.job_id)

        assert response.status == "started"
        assert response.job_id is not None
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert job["http_status_code"] == 400
        assert job["error_detail"] == "bad setup"
        assert "bad setup" in job["message"]
        launch_background.assert_not_called()

    def test_setup_memory_limited_preparation_ends_memory_limited(self, scored_data):
        """A memory-limited preparation failure is not flattened to contract_error."""
        from haute.errors import InputPreparationError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )
        failure = InputPreparationError(
            "Preparing this Data Input's snapshot failed.",
            node_id="source",
            identity_digest="b" * 64,
            build_class="admitted_eager",
            reason_code="memory_limited",
            remediation="Give the execution more memory headroom and try again.",
        )

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=failure,
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._execute_pipeline(body, job_id, resources)

        assert exc_info.value.status_code == 507
        job = store.require_job(job_id)
        assert job["status"] == "memory_limited"
        assert job["terminal_reason"] == "memory_limited"
        assert job["error"] == str(failure)
        assert job["error_code"] == "memory_limit"
        assert job["http_status_code"] == 507
        assert job["error_detail"]["reason_code"] == "memory_limited"

    def test_setup_build_failed_preparation_ends_contract_error(self, scored_data):
        """A non-memory preparation failure still ends the job contract_error."""
        from haute.errors import InputPreparationError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )
        failure = InputPreparationError(
            "Preparing this Data Input's snapshot failed.",
            node_id="source",
            identity_digest="c" * 64,
            build_class="admitted_eager",
            reason_code="build_failed",
            remediation="Build this Data Input's snapshot and try again.",
        )

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=failure,
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._execute_pipeline(body, job_id, resources)

        assert exc_info.value.status_code == 422
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert job["http_status_code"] == 422

    def test_solve_marks_job_error_when_factor_extraction_fails(
        self,
        client,
        scored_data,
        clean_job_store,
    ):
        """A ratebook setup failure must not leave the solve job running."""
        from haute.routes.optimiser import _solve_service

        graph = _make_optimiser_graph(
            scored_data,
            config={
                "mode": "ratebook",
                "factor_columns": [["region"]],
                "banding_source": "source",
            },
        )
        lazy_outputs = {
            "source": pl.LazyFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "expected_income": [100.0],
                    "volume": [1.0],
                }
            )
        }
        before_job_ids = set(clean_job_store.list_jobs())

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
            assert resp.status_code == 200
            job = _wait_for_store_job_terminal(clean_job_store, resp.json()["job_id"])

        new_job_ids = set(clean_job_store.list_jobs()) - before_job_ids
        solve_jobs = [
            clean_job_store.require_job(job_id)
            for job_id in new_job_ids
            if clean_job_store.require_job(job_id).get("job_type") == "solve"
        ]
        assert len(solve_jobs) == 1
        assert solve_jobs[0] == job
        assert job["status"] == "error"
        assert "factor collection failed" in job["message"]
        build_grid.assert_not_called()
        launch_background.assert_not_called()

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_setup_contract_failure_records_typed_terminal_reason(
        self,
        client,
        scored_data,
        clean_job_store,
    ):
        """Setup helpers must not preempt lifecycle's contract-error taxonomy."""
        from haute.routes.optimiser import _solve_service

        graph = _make_optimiser_graph(scored_data)
        lazy_outputs = {
            "source": pl.LazyFrame(
                {
                    "quote_id": ["q1", "q1"],
                    "scenario_index": [0, 1],
                    "scenario_value": [0.9, 1.1],
                    "expected_income": [100.0, 110.0],
                }
            )
        }
        before_job_ids = set(clean_job_store.list_jobs())

        with patch.object(_solve_service, "_execute_pipeline", return_value=lazy_outputs):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            assert resp.status_code == 200
            job = _wait_for_store_job_terminal(clean_job_store, resp.json()["job_id"])

        new_job_ids = set(clean_job_store.list_jobs()) - before_job_ids
        solve_jobs = [
            clean_job_store.require_job(job_id)
            for job_id in new_job_ids
            if clean_job_store.require_job(job_id).get("job_type") == "solve"
        ]
        assert len(solve_jobs) == 1
        assert solve_jobs[0] == job
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert "Missing columns in scored data" in job["message"]

    def test_solve_setup_stopped_timeout_preserves_terminal_reason(self, scored_data):
        """Cooperative timed-out setup cancellation must not be relabelled as superseded."""
        from haute.routes._background_jobs import BackgroundJobStoppedError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        store = JobStore()
        service = OptimiserSolveService(store)
        graph = _make_optimiser_graph(scored_data)

        def stop_with_timeout(*args, **_kwargs):
            raise BackgroundJobStoppedError(args[1], "timed_out")

        with patch.object(service, "_execute_pipeline", side_effect=stop_with_timeout):
            response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))
            assert response.job_id is not None
            job = _wait_for_store_job_terminal(store, response.job_id)

        assert job["status"] == "timed_out"
        assert job["terminal_reason"] == "timed_out"
        assert job["execution_metrics"]["status"] == "timed_out"
        assert job["execution_metrics"]["terminal_reason"] == "timed_out"

    def test_record_execution_metrics_preserves_existing_terminal_reason(self) -> None:
        """Late metrics refreshes must not erase lifecycle terminal taxonomy."""
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )
        context = ExecutionContext(
            operation="optimiser_solve",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            job_id=job_id,
            memory_sampler=lambda: 1_000,
        )
        service._record_setup_failure(
            job_id,
            to="contract_error",
            message="bad contract",
            execution_context=context,
        )

        service._record_execution_metrics(job_id, context)

        metrics = store.require_job(job_id)["execution_metrics"]
        assert metrics["status"] == "contract_error"
        assert metrics["terminal_reason"] == "contract_error"

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
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(scored_data),
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
                                        "output_column": "proposer_age_band",
                                        "rules": [
                                            {"boundary": "27", "label": "20-27"},
                                            {"boundary": "34", "label": "28-34"},
                                            {"boundary": "41", "label": "35-41"},
                                        ],
                                        "default": "missing",
                                    },
                                    {
                                        "banding": "categorical",
                                        "column": "channel",
                                        "output_column": "channel_band",
                                        "rules": [
                                            {
                                                "value": "compare_the_market",
                                                "assignment": "compare_the_market",
                                            },
                                            {
                                                "value": "moneysupermarket",
                                                "assignment": "moneysupermarket",
                                            },
                                            {"value": "confused", "assignment": "confused"},
                                        ],
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
                                "banding_source": "Age_Veh_Banding",
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

        launched = threading.Event()

        def mark_launched(*args, **kwargs):
            launched.set()

        with (
            patch.object(_solve_service, "_execute_pipeline", return_value={}),
            patch.object(_solve_service, "_resolve_data_input_frame", return_value=object()),
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
            patch.object(_solve_service, "_build_grid", return_value=setup_grid_stub()),
            patch.object(
                _solve_service,
                "_launch_background",
                side_effect=mark_launched,
            ) as launch_background,
        ):
            resp = client.post(
                "/api/optimiser/solve",
                json={"graph": graph, "node_id": "online_optimiser"},
            )
            assert launched.wait(timeout=10.0)

        assert resp.status_code == 200
        launched_config = launch_background.call_args.kwargs["config"]
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
        frontier_data = make_frontier_data(
            [typed_frontier_point({"total_objective": 100.0, "lambda_volume": 0.25})],
            n_points=3,
            points_limit=1,
            points_truncated=True,
        )
        seed_job(
            clean_job_store,
            "capped_frontier_status",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
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
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.25},
                    "converged": True,
                    "frontier": frontier_data,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "segment_keys": [],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

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
        seed_job(
            clean_job_store,
            job_id,
            {
                "status": "running",
                "progress": 0.4,
                "message": "Solving...",
                "start_time": time.monotonic() - 2.0,
                "timeout": 1.0,
                "created_at": time.time(),
            },
        )
        original_require_job = clean_job_store.require_job

        def require_once_then_evicted(requested_job_id: str):
            if requested_job_id == job_id and require_once_then_evicted.calls:
                raise NotFoundError(f"Job '{job_id}' not found")
            require_once_then_evicted.calls += 1
            return original_require_job(requested_job_id)

        require_once_then_evicted.calls = 0

        with patch.object(clean_job_store, "require_job", side_effect=require_once_then_evicted):
            resp = client.get(f"/api/optimiser/solve/status/{job_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "timed_out"
        assert data["terminal_reason"] == "timed_out"
        assert data["progress"] == 0.4
        assert data["message"] == (
            "Solve timed out after 1.0s. Increase timeout or simplify the problem."
        )
        assert data["elapsed_seconds"] >= 1.0


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
        # The as-solved page is always read from the persisted apply artifact.
        assert data["from_artifact"] is True

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
        use_project(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": "../../etc/passwd"},
        )
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Phase 1 tests
# ---------------------------------------------------------------------------


class TestSolveWithHistory:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_with_history(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
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


class TestAdjustmentReport:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_adjustments_in_result(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"
        result = status["result"]
        report = result["adjustments"]
        assert [bar["optimal_step"] for bar in report["bars"]] == [
            step["optimal_step"] for step in result["scenario_grid"]
        ]
        assert sum(bar["quotes"] for bar in report["bars"]) == report["n_quotes"]
        assert report["weightings"][0]["key"] == "quotes"


class TestColumnValidation:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_missing_column(self, client, tmp_path):
        """Data without a constraint column returns a contract-error job."""
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
        assert resp.status_code == 200
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "contract_error"
        assert "volume" in status["message"]


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
        assert "chunk_size" not in saved


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
    """Build a 4-node graph: dataInput → expander → transform → optimiser.

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
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(data_path),
                    },
                },
                {
                    "id": "transform",
                    "data": {
                        "label": "compute_metrics",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = expander.with_columns([\n"
                                "    (pl.col('base_income') * "
                                "pl.col('scenario_value'))"
                                ".alias('expected_income'),\n"
                                "    (pl.col('base_volume') * "
                                "(2.0 - pl.col('scenario_value')))"
                                ".alias('volume'),\n"
                                "])"
                            ),
                            "contract": {
                                "inputs": [
                                    "base_income",
                                    "base_volume",
                                    "scenario_value",
                                ],
                                "outputs": ["expected_income", "volume"],
                            },
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
                            "step_count": 5,
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


# ---------------------------------------------------------------------------
# Phase 1B: Background thread error tests
# ---------------------------------------------------------------------------


class TestSolveBackgroundErrors:
    """Test error categorization in the _solve_background thread."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_untyped_value_error_is_unexpected(self, client, scored_data):
        """An untyped ValueError must not be mistaken for an input error."""
        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=ValueError("Invalid constraint column"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "error"
            assert status["terminal_reason"] == "error"
            assert status["message"] == "Unexpected error: Invalid constraint column"

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_untyped_runtime_error_is_unexpected(self, client, scored_data):
        """An untyped RuntimeError must not be mistaken for a solver error."""
        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=RuntimeError("Solver diverged"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "error"
            assert status["message"] == "Unexpected error: Solver diverged"

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_typed_input_error_is_contract_error(self, client, scored_data):
        from haute.routes._optimiser_solver import _OptimiserSolveInputError

        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=_OptimiserSolveInputError("missing factor column"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "contract_error"
            assert status["terminal_reason"] == "contract_error"
            assert status["message"] == "Data error: missing factor column"

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_typed_solver_error_is_algorithm_error(self, client, scored_data):
        from haute.routes._optimiser_solver import _OptimiserSolverExecutionError

        graph = _make_optimiser_graph(scored_data)
        with patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=_OptimiserSolverExecutionError("solver rejected grid"),
        ):
            resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "error"
            assert status["terminal_reason"] == "error"
            assert status["message"] == "Algorithm error: solver rejected grid"

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
        seed_job(
            clean_job_store,
            "running",
            {
                "status": "running",
                "progress": 0.5,
                "created_at": time.time(),
            },
        )
        resp = client.post("/api/optimiser/apply", json={"job_id": "running"})
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_apply_no_solve_result(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "no_sr",
            {
                "status": "completed",
                "solve_result": None,
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post("/api/optimiser/apply", json={"job_id": "no_sr"})
        assert resp.status_code == 400
        assert "apply artifact handle" in resp.json()["detail"].lower()

    def test_save_not_completed(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "running3",
            {
                "status": "running",
                "progress": 0.1,
                "created_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "running3",
                "output_path": "/tmp/x.json",
            },
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_save_without_a_result_summary_is_an_invariant_failure(self, client, clean_job_store):
        # Save publishes from the completion summary, never the heavy solve result;
        # a completed solve without one is a server invariant failure.
        seed_job(
            clean_job_store,
            "no_sr2",
            {
                "status": "completed",
                "solve_result": None,
                "solver": None,
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "no_sr2",
                "output_path": "x.json",
            },
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Job summary is missing"


# ---------------------------------------------------------------------------
# Phase 1B: Timeout detection
# ---------------------------------------------------------------------------


class TestStatusTimeout:
    """Test that polling a timed-out job returns error."""

    def test_timeout_detection(self, client, clean_job_store):
        # Inject a "running" job whose start_time is far in the past
        seed_job(
            clean_job_store,
            "timed_out",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Solving",
                "start_time": time.monotonic() - 999,
                "timeout": 10,
                "created_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/timed_out")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "timed_out"
        assert data["terminal_reason"] == "timed_out"
        assert "timed out" in data["message"].lower()


# ---------------------------------------------------------------------------
# Phase 1B: Unsupported mode and ratebook edge cases
# ---------------------------------------------------------------------------


class TestUnsupportedMode:
    def test_unsupported_mode_returns_400(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data, config={"mode": "quantum"})
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 400
        assert "setting 'mode': Input should be 'online' or 'ratebook'" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# solve_status edge cases
# ---------------------------------------------------------------------------


class TestSolveStatusEdgeCases:
    def test_running_job_returns_progress(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "running_prog",
            {
                "status": "running",
                "progress": 0.42,
                "message": "Solving",
                "start_time": time.monotonic(),
                "timeout": 9999,
                "elapsed_seconds": 1.5,
                "created_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/running_prog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"] == pytest.approx(0.42)
        assert data["message"] == "Solving"

    def test_completed_job_returns_result_with_frontier(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "done_frontier",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "progress": 1.0,
                "message": "Completed",
                "elapsed_seconds": 5.0,
                "result": {
                    "mode": "online",
                    "total_objective": 200.0,
                    "baseline_objective": 180.0,
                    "constraints": {"volume": 0.92},
                    "baseline_constraints": {"volume": 0.88},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.4},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "segment_keys": [],
                },
                "frontier_data": make_frontier_data([make_frontier_point()]),
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/done_frontier")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"]["total_objective"] == 200.0
        assert data["frontier"] is not None
        assert data["frontier"]["status"] == "ok"
        assert data["frontier"]["n_points"] == 1


# ---------------------------------------------------------------------------
# Coverage expansion tests
# ---------------------------------------------------------------------------


class TestSolveStatusTimeout:
    """Test timeout detection including atomic update guard."""

    def test_timeout_sets_error_with_elapsed(self, client, clean_job_store):
        """Timed-out job gets error status with elapsed_seconds."""
        start = time.monotonic() - 500
        seed_job(
            clean_job_store,
            "tout",
            {
                "status": "running",
                "progress": 0.3,
                "message": "Solving",
                "start_time": start,
                "timeout": 10,
                "created_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/tout")
        data = resp.json()
        assert data["status"] == "timed_out"
        assert data["terminal_reason"] == "timed_out"
        assert "timed out" in data["message"].lower()
        assert data["elapsed_seconds"] > 0

    def test_timeout_race_uses_current_job_when_guarded_write_skips(
        self,
        client,
        clean_job_store,
    ):
        """If a solve completes while timeout handling races, report the completion."""
        start = time.monotonic() - 500
        seed_job(
            clean_job_store,
            "tout_race",
            {
                "status": "running",
                "progress": 0.3,
                "message": "Solving",
                "start_time": start,
                "timeout": 10,
                "created_at": time.time(),
            },
        )

        def complete_elsewhere(*args, **kwargs):
            replace_job(
                clean_job_store,
                "tout_race",
                lambda _job: {
                    "status": "completed",
                    "terminal_reason": "completed",
                    "progress": 1.0,
                    "message": "Completed",
                    "elapsed_seconds": 12.0,
                    "result": {
                        "mode": "online",
                        "total_objective": 100.0,
                        "baseline_objective": 95.0,
                        "constraints": {},
                        "baseline_constraints": {},
                        "effective_bounds": {},
                        "lambdas": {},
                        "converged": True,
                        "diagnostics_errors": [],
                        "input_summary": make_input_summary(),
                        "scenario_grid": SOLVE_SCENARIO_GRID,
                        "segment_keys": [],
                    },
                    "created_at": time.time(),
                    "completed_at": time.time(),
                },
            )
            return clean_job_store.require_job("tout_race")

        with patch(
            "haute.routes.optimiser._solve_service.timeout_solve",
            side_effect=complete_elsewhere,
        ):
            resp = client.get("/api/optimiser/solve/status/tout_race")

        data = resp.json()
        assert data["status"] == "completed"
        assert data["message"] == "Completed"

    def test_running_job_not_timed_out(self, client, clean_job_store):
        """A running job with sufficient timeout remains running."""
        start_time = time.monotonic() - 5.0
        seed_job(
            clean_job_store,
            "not_tout",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Solving",
                "start_time": start_time,
                "timeout": 9999,
                "elapsed_seconds": 0.5,
                "created_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/not_tout")
        data = resp.json()
        assert data["status"] == "running"
        assert data["elapsed_seconds"] >= 5.0

    def test_running_job_without_timeout_is_not_timed_out(self, client, clean_job_store):
        """Long-running solves without an explicit timeout keep polling as running."""
        start_time = time.monotonic() - 10_000.0
        seed_job(
            clean_job_store,
            "no_timeout",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Solving",
                "start_time": start_time,
                "elapsed_seconds": 0.5,
                "created_at": time.time(),
            },
        )

        resp = client.get("/api/optimiser/solve/status/no_timeout")

        data = resp.json()
        assert data["status"] == "running"
        assert data["elapsed_seconds"] >= 10_000.0
        assert clean_job_store.require_job("no_timeout")["status"] == "running"

    def test_running_job_with_null_timeout_is_not_timed_out(self, client, clean_job_store):
        """A stored null timeout explicitly means no automatic solve deadline."""
        start_time = time.monotonic() - 10_000.0
        seed_job(
            clean_job_store,
            "null_timeout",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Solving",
                "start_time": start_time,
                "timeout": None,
                "created_at": time.time(),
            },
        )

        resp = client.get("/api/optimiser/solve/status/null_timeout")

        data = resp.json()
        assert data["status"] == "running"
        assert clean_job_store.require_job("null_timeout")["status"] == "running"

    def test_cancel_solve_marks_job_cancelled(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "cancel_me",
            {
                "status": "running",
                "job_type": "solve",
                "progress": 0.4,
                "message": "Solving",
                "start_time": time.monotonic() - 1,
                "created_at": time.time(),
            },
        )

        resp = client.post("/api/optimiser/solve/cancel/cancel_me")

        data = resp.json()
        assert resp.status_code == 200
        assert data["status"] == "cancelled"
        assert data["terminal_reason"] == "cancelled"
        assert clean_job_store.require_job("cancel_me")["terminal_reason"] == "cancelled"

    def test_completed_job_not_overwritten_by_timeout(self, client, clean_job_store):
        """Timeout checks should not regress a completed job back to error."""
        seed_job(
            clean_job_store,
            "done_past_timeout",
            {
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
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.5},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "segment_keys": [],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        resp = client.get("/api/optimiser/solve/status/done_past_timeout")
        data = resp.json()

        assert data["status"] == "completed"
        assert data["message"] == "Completed"
        assert "timed out" not in data["message"].lower()

    def test_status_completed_without_frontier(self, client, clean_job_store):
        """Completed job without frontier_data returns frontier=None."""
        seed_job(
            clean_job_store,
            "no_front",
            {
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
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.5},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "segment_keys": [],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/no_front")
        data = resp.json()
        assert data["status"] == "completed"
        assert data["frontier"] is None

    def test_status_error_fields_default(self, client, clean_job_store):
        """Job with minimal error fields returns sensible defaults."""
        seed_job(
            clean_job_store,
            "minimal",
            {
                "status": "error",
                "created_at": time.time(),
            },
        )
        resp = client.get("/api/optimiser/solve/status/minimal")
        data = resp.json()
        assert data["status"] == "error"
        assert data["progress"] == 0.0
        assert data["message"] == ""
        assert data["elapsed_seconds"] == 0.0


class TestLaunchBackground:
    """Tests for _launch_background error categorization."""

    def test_service_uses_one_cancellation_registry(self, clean_job_store):
        from haute.routes._background_jobs import CancellableJobRegistry
        from haute.routes._optimiser_service import OptimiserSolveService

        service = OptimiserSolveService(clean_job_store)

        registries = [
            value for value in vars(service).values() if isinstance(value, CancellableJobRegistry)
        ]
        assert len(registries) == 1

    @pytest.mark.parametrize("raw_value", ['"not-a-number"', "0", "-1"])
    def test_an_invalid_optimisation_time_limit_fails_loudly(
        self,
        tmp_path,
        raw_value,
    ):
        """An invalid limit in the settings file never silently removes the solve's limit."""
        from haute._pipeline_settings import PipelineSettingsError
        from haute.routes._optimiser_service import _optimisation_time_limit

        (tmp_path / ".haute").mkdir()
        (tmp_path / ".haute" / "pipeline-settings.json").write_text(
            f'{{"optimisation_time_limit_minutes": {raw_value}}}', encoding="utf-8"
        )
        use_project(tmp_path)

        with pytest.raises(PipelineSettingsError, match="optimisation_time_limit_minutes"):
            _optimisation_time_limit()

    @pytest.mark.parametrize("registered", [False, True])
    def test_a_launch_without_its_setup_context_fails_loudly(self, clean_job_store, registered):
        """OPT-R05: the solve never admits or registers itself; setup hands both over."""
        from haute.routes._optimiser_service import OptimiserSolveService

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )
        context = (
            SolveContext(job_id=job_id, node_id="opt", mode="online")
            if not registered
            else setup_launch_context(service, job_id)
        )
        if registered:
            context = dataclasses.replace(context, registration_already_active=False)

        with pytest.raises(RuntimeError, match="admitted, registered execution context"):
            service._launch_background(
                context, config={}, quote_grid=MagicMock(), ratebook_factors_handle=None
            )

    def test_background_sets_start_time_and_timeout(self, clean_job_store, tmp_path):
        """_launch_background sets start_time, and the settings' time limit, on the job."""
        from haute._pipeline_settings import update_pipeline_settings
        from haute.routes._optimiser_service import OptimiserSolveService

        update_pipeline_settings(tmp_path, {"optimisation_time_limit_minutes": 0.7})
        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        mock_grid = MagicMock()

        with patch("haute.routes._optimiser_service._solve_online"):
            service._launch_background(
                setup_launch_context(service, job_id),
                config={},
                quote_grid=mock_grid,
                ratebook_factors_handle=None,
            )
            # Give the thread time to start
            time.sleep(0.2)

        job = clean_job_store.require_job(job_id)
        assert job["timeout"] == 42
        assert "start_time" in job

    def test_background_default_timeout_is_disabled(self, clean_job_store):
        """Optimiser solves do not get an implicit wall-clock timeout."""
        from haute.routes._optimiser_service import OptimiserSolveService

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        with patch("haute.routes._optimiser_service._solve_online"):
            service._launch_background(
                setup_launch_context(service, job_id),
                config={},
                quote_grid=MagicMock(),
                ratebook_factors_handle=None,
            )
            time.sleep(0.2)

        job = clean_job_store.require_job(job_id)
        assert job["timeout"] is None
        assert "start_time" in job

    def test_background_start_failure_marks_job_error(self, clean_job_store):
        """A worker-start failure should not leave the job stuck in running."""
        from haute.routes._optimiser_service import OptimiserSolveService

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        with (
            patch(
                "haute.routes._optimiser_service.threading.Thread.start",
                side_effect=RuntimeError("thread boom"),
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._launch_background(
                setup_launch_context(service, job_id),
                config={"timeout": 42},
                quote_grid=MagicMock(),
                ratebook_factors_handle=None,
            )

        assert exc_info.value.status_code == 500
        job = clean_job_store.require_job(job_id)
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert "Failed to start optimiser worker" in job["message"]

    def test_late_completion_does_not_overwrite_timeout(self, clean_job_store):
        """Late solver progress/completion must not overwrite a timeout error."""
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.routes._optimiser_solver import _finalize_solve_result

        service = OptimiserSolveService(clean_job_store)
        job_id = clean_job_store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "progress": 0.0,
                "message": "Starting",
                "config": {},
            }
        )

        deferred_threads: list[object] = []

        class DeferredThread:
            def __init__(self, *, target, daemon, args=(), kwargs=None, name=None):
                self.target = functools.partial(target, *args, **(kwargs or {}))
                self.daemon = daemon
                deferred_threads.append(self)

            def start(self) -> None:
                return None

        def _late_solve(*args, **kwargs) -> None:
            solve_result = SimpleNamespace(
                total_objective=100.0,
                baseline_objective=95.0,
                total_constraints={"volume": 0.91},
                constraint_bounds={"volume": 0.9},
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
            service._launch_background(
                setup_launch_context(service, job_id),
                config={"timeout": 10},
                quote_grid=MagicMock(),
                ratebook_factors_handle=None,
            )

        assert len(deferred_threads) == 1

        from haute.routes._job_lifecycle import JobLifecycle

        JobLifecycle(clean_job_store).transition(
            job_id,
            to="error",
            message="Solve timed out after 10s",
            elapsed_seconds=10.0,
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
        """An unexpected failure reading the apply artifact is the sanitised 500."""
        from haute.routes._optimiser_artifacts import _persist_apply_frame_artifact

        handle = _persist_apply_frame_artifact(make_online_apply_frame(["q1"]))
        seed_job(
            clean_job_store,
            "apply_err",
            make_completed_job(
                artifact_handles={"apply_result": handle},
                solve_result=SimpleNamespace(dataframe=None),
            ),
        )
        with (
            patch(
                "haute.routes._optimiser_artifacts._scan_apply_result_artifact",
                side_effect=RuntimeError("boom"),
            ),
            patch("haute.server.logger.error") as log_error,
        ):
            resp = client.post("/api/optimiser/apply", json={"job_id": "apply_err"})

        assert resp.status_code == 500
        assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
        # The application handler logs it; the route no longer catches it.
        log_error.assert_called_once()
        assert log_error.call_args.args == ("unhandled_exception",)
        assert log_error.call_args.kwargs["error_class"] == "RuntimeError"
        assert log_error.call_args.kwargs["path"] == "/api/optimiser/apply"
        job = clean_job_store.require_job("apply_err")
        assert "solve_result" in job


class TestSaveExceptionPaths:
    """Test save endpoint OSError and generic Exception paths."""

    def test_save_neither_reads_nor_reserves_heavy_state(
        self,
        client,
        clean_job_store,
        tmp_path,
    ):
        """Saving publishes from the summary: the heavy window is not extended."""

        solve_result = MagicMock()
        seed_job(
            clean_job_store,
            "save_touch",
            {
                "status": "completed",
                "created_at": 100.0,
                "completed_at": 100.0,
                "heavy_objects_expires_at": 1000.0,
                "result": _anchor_result(),
                "solve_result": solve_result,
                "config": {"mode": "online"},
                "node_label": "opt",
            },
        )
        use_project(tmp_path)

        with patch("haute.routes._job_store.time.time", return_value=940.0):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_touch", "output_path": str(tmp_path / "out.json")},
            )
            job = clean_job_store.require_job("save_touch")

        assert resp.status_code == 200
        assert job["heavy_objects_expires_at"] == 1000.0
        assert job["solve_result"] is solve_result
        assert not solve_result.mock_calls

    def test_save_os_error(self, client, clean_job_store, tmp_path):
        """OSError during save returns 500 with filesystem error message."""

        seed_job(
            clean_job_store,
            "save_os",
            {
                "status": "completed",
                "result": _anchor_result(),
                "solve_result": MagicMock(),
                "solver": MagicMock(),
                "config": {"mode": "online"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        use_project(tmp_path)
        out_path = str(tmp_path / "out.json")

        with (
            patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")),
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
        job = clean_job_store.require_job("save_os")
        assert "solver" in job
        assert "solve_result" in job

    def test_save_generic_exception(self, client, clean_job_store, tmp_path):
        """Generic Exception during save returns 500."""

        seed_job(
            clean_job_store,
            "save_gen",
            {
                "status": "completed",
                "result": _anchor_result(),
                "solve_result": MagicMock(),
                "solver": MagicMock(),
                "config": {"mode": "online"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        use_project(tmp_path)
        out_path = str(tmp_path / "out.json")

        with (
            patch("pathlib.Path.write_bytes", side_effect=RuntimeError("unexpected")),
            patch("haute.server.logger.error") as log_error,
        ):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_gen", "output_path": out_path},
            )
        assert resp.status_code == 500
        assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
        # The application handler logs it; the route no longer catches it.
        log_error.assert_called_once()
        assert log_error.call_args.args == ("unhandled_exception",)
        assert log_error.call_args.kwargs["error_class"] == "RuntimeError"
        assert log_error.call_args.kwargs["path"] == "/api/optimiser/save"


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
