"""How the optimiser executes its pipeline and builds the scenario grid.

``_execute_pipeline`` passes the scenario, preamble namespace and seed plan, releases the seed
plan, streams chunks and re-raises refusals; ``_build_grid`` routes through the bounded sink;
``_validate_and_project`` and ``_resolve_data_input_frame`` read the inputs the solve needs.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._polars_utils import STREAMING_CHUNK_SIZE_ENV
from haute.errors import ForbiddenError, RequestRefusedError
from haute.routes._optimiser_input import _optimiser_solve_required_columns_by_node
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _make_optimiser_graph,
    _make_scored_data,
    _optimiser_helper_graph,
    _snapshot_parquet_data_input,
    _wait_for_store_job_terminal,
)
from tests._project import use_project
from tests.conftest import make_edge, make_graph
from tests.optimiser_fixtures import (
    SOLVE_PROVENANCE,
    SOLVE_SCENARIO_GRID,
    setup_grid_stub,
)

# Fixtures shared with the other modules split from one suite.
scored_data = _optimiser_route_helpers.scored_data


# ---------------------------------------------------------------------------
# Issue 12: _execute_pipeline execution-path tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestExecutePipelineArgs:
    """Verify _execute_pipeline passes scenario, preamble_ns, and its seed plan."""

    def test_optimiser_solve_required_columns_seed_uses_configured_data_input(self, scored_data):
        """Solve/estimate seeds only the actual optimiser data input branch."""
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
                        "id": "side_source",
                        "data": {
                            "label": "side source",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(scored_data),
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "data_input": "source",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "opt").model_dump(),
                    make_edge("side_source", "opt").model_dump(),
                ],
            }
        )

        seeds = _optimiser_solve_required_columns_by_node(
            graph,
            "opt",
            graph.nodes[-1].data.config,
        )

        assert seeds == {
            "source": frozenset(
                {
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            )
        }

    def test_optimiser_solve_required_columns_rejects_unconnected_data_input(
        self,
        scored_data,
    ):
        """Solve/estimate reject a data_input that is not an optimiser parent."""

        graph_dict = _make_optimiser_graph(
            scored_data,
            config={
                "data_input": "missing_source",
            },
        )
        graph = make_graph(graph_dict)

        with pytest.raises(RequestRefusedError) as exc_info:
            _optimiser_solve_required_columns_by_node(
                graph,
                "opt",
                graph.nodes[-1].data.config,
            )

        assert exc_info.value.status_code == 400
        assert "data_input" in exc_info.value.detail
        assert "missing_source" in exc_info.value.detail

    def test_optimiser_solve_required_columns_ratebook_seeds_configured_data_input(
        self,
        scored_data,
    ):
        """Ratebook solve can project the solver input without pruning factors."""
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
                        "id": "banding",
                        "data": {
                            "label": "banding",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "banding": "breakpoints",
                                        "column": "territory",
                                        "output_column": "territory_band",
                                    }
                                ]
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "factor_columns": [["territory_band"]],
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

        seeds = _optimiser_solve_required_columns_by_node(
            graph,
            "opt",
            graph.nodes[-1].data.config,
        )

        assert seeds == {
            "source": frozenset(
                {
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            )
        }

    def test_optimiser_solve_required_columns_ratebook_shared_input_seeds_data_only(
        self,
        scored_data,
    ):
        """Shared ratebook factor columns are routed by the projection planner."""
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
                        "id": "opt",
                        "data": {
                            "label": "optimiser",
                            "nodeType": "optimiser",
                            "config": {
                                "mode": "ratebook",
                                "objective": "expected_income",
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "factor_columns": [["territory_band"]],
                                "banding_source": "source",
                                "data_input": "source",
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "opt").model_dump()],
            }
        )

        seeds = _optimiser_solve_required_columns_by_node(
            graph,
            "opt",
            graph.nodes[-1].data.config,
        )

        assert seeds == {
            "source": frozenset(
                {
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            )
        }

    def test_shared_multi_frame_source_seeds_reach_the_planner_without_failing(self):
        """Solve seeds for two frames of one source must survive strict projection.

        A node-keyed seed on the shared source is rejected by the strict
        planner (the source has two physical consumers), so the helper must
        seed the optimiser node itself; this drives the helper output through
        the real planner rather than hand-crafting the seed shape.
        """
        from haute._execution_context import ExecutionProfile
        from haute.projection import ProjectionRequest, plan

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "request",
                        "data": {
                            "label": "Quote_Input_1",
                            "nodeType": "apiInput",
                            "config": {
                                "contract": "opaque",
                                "tables": [
                                    {"label": "quote_info", "path": "$[:].quotes", "emit": True},
                                    {
                                        "label": "rating_factors",
                                        "path": "$[:].factors",
                                        "emit": True,
                                    },
                                ],
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "factor_columns": [["territory_band"]],
                                "data_input": "quote_info",
                                "banding_source": "rating_factors",
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e_quotes",
                        "source": "request",
                        "sourceHandle": "quote_info",
                        "target": "opt",
                    },
                    {
                        "id": "e_factors",
                        "source": "request",
                        "sourceHandle": "rating_factors",
                        "target": "opt",
                    },
                ],
            }
        )
        config = graph.nodes[-1].data.config

        seeds = _optimiser_solve_required_columns_by_node(graph, "opt", config)

        assert set(seeds) == {"opt"}
        projection = plan(
            ProjectionRequest(
                graph=graph,
                target_node_id="opt",
                profile=ExecutionProfile.OPTIMISER_SETUP,
                required_columns_by_node={
                    node_id: set(columns) for node_id, columns in seeds.items()
                },
            )
        )
        assert projection.needed_by_node["request"] is None

    def test_shared_multi_frame_setup_plans_no_capture(self):
        """A multi-frame apiInput feeding both optimiser inputs is built, never captured."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "request",
                        "data": {
                            "label": "Quote_Input_1",
                            "nodeType": "apiInput",
                            "config": {
                                "tables": [
                                    {"label": "quote_info", "path": "$[:].quotes", "emit": True},
                                    {
                                        "label": "rating_factors",
                                        "path": "$[:].factors",
                                        "emit": True,
                                    },
                                ],
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
                                "constraints": {},
                                "factor_columns": [["territory_band"]],
                                "data_input": "quote_info",
                                "banding_source": "rating_factors",
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e_quotes",
                        "source": "request",
                        "sourceHandle": "quote_info",
                        "target": "opt",
                    },
                    {
                        "id": "e_factors",
                        "source": "request",
                        "sourceHandle": "rating_factors",
                        "target": "opt",
                    },
                ],
            }
        )
        body = OptimiserSolveRequest(graph=graph, node_id="opt")
        service = OptimiserSolveService(JobStore())
        captured: dict = {}

        def fake_execute_lazy_graph(*_args, **kwargs):
            captured.update(kwargs)
            return ({"request": {}},)

        with (
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy_graph,
            ),
            contextlib.ExitStack() as resources,
        ):
            outputs = service._execute_pipeline(body, "job-1", resources)
            plan = captured["snapshot_plan"]

            assert outputs == {"request": {}}
            assert captured["prepare_inputs"] is False
            # Its tables have their own store: the plan builds it and captures nothing.
            assert plan.decision.captures == {}
            assert plan.decision.seeds == {}
            assert "request" in plan.decision.executed_node_ids

    def test_solve_passes_optimiser_seed_to_execute_pipeline(self, scored_data):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")
        scored_lf = pl.scan_parquet(scored_data)

        store = JobStore()
        service = OptimiserSolveService(store)
        launch_called = threading.Event()
        with (
            patch.object(
                service,
                "_execute_pipeline",
                return_value={"source": scored_lf},
            ) as execute,
            patch.object(service, "_build_grid", return_value=setup_grid_stub()),
            patch.object(
                service,
                "_launch_background",
                side_effect=lambda *args, **kwargs: launch_called.set(),
            ),
        ):
            response = service.start(body)
            assert launch_called.wait(timeout=10.0)

        assert response.status == "started"
        assert execute.call_args.kwargs["required_columns_by_node"] == {
            "source": frozenset(
                {
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            )
        }

    def test_solve_maps_bounded_streaming_collect_failure_to_422(self, scored_data):
        from haute.errors import BoundedMemoryUnsupportedError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")
        scored_lf = pl.scan_parquet(scored_data)
        error = BoundedMemoryUnsupportedError(
            "Bounded streaming collect failed",
            profile="optimiser_setup",
            cause="ComputeError",
        )

        store = JobStore()
        service = OptimiserSolveService(store)
        with (
            patch.object(service, "_execute_pipeline", return_value={"source": scored_lf}),
            patch(
                "haute.routes._optimiser_input.streaming_collect",
                side_effect=error,
            ),
            patch.object(service, "_build_grid") as build_grid,
            patch.object(service, "_launch_background") as launch_background,
        ):
            response = service.start(body)
            assert response.job_id is not None
            job = _wait_for_store_job_terminal(store, response.job_id)

        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert "bounded streaming mode" in job["message"]
        build_grid.assert_not_called()
        launch_background.assert_not_called()

    def test_estimate_passes_optimiser_seed_to_execute_pipeline(self, scored_data):
        from haute.routes import optimiser as optimiser_module
        from haute.schemas import OptimiserEstimateRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserEstimateRequest(graph=graph_dict, node_id="opt")
        scored_lf = pl.scan_parquet(scored_data)

        with patch.object(
            optimiser_module._solve_service,
            "_execute_pipeline",
            return_value={"source": scored_lf},
        ) as execute:
            metrics = optimiser_module._optimiser_input_metrics(body)

        assert metrics["expanded_row_count"] == 250
        assert execute.call_args.kwargs["target_node_id"] == "source"
        execution_context = execute.call_args.kwargs["execution_context"]
        assert execution_context.profile is ExecutionProfile.OPTIMISER_SETUP
        assert execution_context.admission is not None
        assert execution_context.lease.released is True
        assert execute.call_args.kwargs["required_columns_by_node"] == {
            "source": frozenset(
                {
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            )
        }

    def test_execute_pipeline_passes_scenario_and_seed_plan(self, scored_data):
        """The lazy execution receives scenario != 'live', the run's seed plan, and preamble_ns."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        # Capture the kwargs the lazy execution is called with.
        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            # Return (outputs_dict, exec_order, edge_map, label_map)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch(
                "haute.executor._resolve_batch_scenario",
                return_value="ism_scenario",
            ),
            patch(
                "haute.executor._compile_preamble",
                return_value={"helper": lambda x: x},
            ),
        ):
            service._execute_pipeline(body, job_id, resources)

        # source should come from _resolve_batch_scenario (not default "batch")
        assert captured["source"] == "ism_scenario"
        # preamble_ns is the dict returned by _compile_preamble
        assert captured["preamble_ns"] is not None
        assert "helper" in captured["preamble_ns"]
        # The run is planned against shared snapshots, whose inputs the plan
        # prepared; it has no checkpoint directory or private cache request.
        assert captured["snapshot_plan"] is not None
        assert captured["prepare_inputs"] is False
        assert "checkpoint_dir" not in captured
        assert "dataframe_cache_request" not in captured

    def test_execute_pipeline_forwards_required_columns_by_node(self, scored_data):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        seeds = {"opt": frozenset({"quote_id", "expected_income"})}
        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(
                body,
                job_id,
                resources,
                required_columns_by_node=seeds,
            )

        assert captured["required_columns_by_node"] == seeds

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_execute_pipeline_preserves_configured_intermediate_data_input(
        self,
        tmp_path,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        source_path = tmp_path / "source.parquet"
        banding_path = tmp_path / "banding.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "scenario_index": [0, 1],
                "scenario_value": [0.9, 1.1],
                "expected_income": [100.0, 120.0],
                "volume": [1.0, 2.0],
            }
        ).write_parquet(source_path)
        pl.DataFrame({"quote_id": ["q1"], "territory": ["north"]}).write_parquet(banding_path)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(source_path)),
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
                            "config": _snapshot_parquet_data_input(str(banding_path)),
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "factor_columns": [["territory"]],
                                "banding_source": "banding",
                                "data_input": "optimiser_input",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "optimiser_input").model_dump(),
                    make_edge("optimiser_input", "opt").model_dump(),
                    make_edge("banding", "opt").model_dump(),
                ],
            }
        ).model_dump()
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})

        with contextlib.ExitStack() as resources:
            outputs = service._execute_pipeline(body, job_id, resources)

            assert "optimiser_input" in outputs
            assert "banding" in outputs
            assert "opt" in outputs

    def test_execute_pipeline_contract_mismatch_returns_400(self, scored_data):

        from haute.errors import ContractMismatchError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=ContractMismatchError(
                    "Checkpoint projection references missing columns",
                    missing=["volume"],
                ),
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(RequestRefusedError) as exc_info:
                service._execute_pipeline(
                    body,
                    job_id,
                    resources,
                    required_columns_by_node={"source": frozenset({"volume"})},
                )

        assert exc_info.value.status_code == 400
        assert "volume" in exc_info.value.detail
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"

    def test_frontier_auto_range_passes_online_seed_to_execute_pipeline(self, scored_data):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph_dict, node_id="opt")
        scored_lf = pl.scan_parquet(scored_data)

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        with patch.object(
            service,
            "_execute_pipeline",
            return_value={"source": scored_lf},
        ) as execute:
            response = service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert response.status == "ok"
        assert execute.call_args.kwargs["required_columns_by_node"] == {
            "source": frozenset(
                {
                    "expected_income",
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "volume",
                }
            )
        }

    def test_execute_pipeline_defaults_to_batch_when_no_ism(self, scored_data):
        """When _resolve_batch_scenario returns None, scenario defaults to 'batch'."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, resources)

        assert captured["source"] == "batch"

    def test_execute_pipeline_preamble_ns_none_for_empty_preamble(self, scored_data):
        """When _compile_preamble returns empty/falsy, preamble_ns is None."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, resources)

        # Empty dict from _compile_preamble is falsy → preamble_ns should be None
        assert captured["preamble_ns"] is None


class TestBuildGridBoundedSink:
    """Verify _build_grid routes through the bounded sink helper."""

    def test_build_grid_uses_bounded_sink(self, tmp_path):
        """The optimiser staging write must not use the fallback-capable sink."""
        from haute._polars_utils import set_streaming_chunk_size

        set_streaming_chunk_size(1_000)

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

        def patched_bounded_sink(lf, path, **kw):
            collected = lf.collect(engine="streaming")
            collected.write_parquet(path)

        mock_grid = MagicMock(scenario_values=[0.9, 1.1])
        with (
            patch(
                "haute.routes._optimiser_input.bounded_sink",
                side_effect=patched_bounded_sink,
            ) as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=mock_grid,
            ) as mock_build,
        ):
            result = service._build_grid(scored_lf, ["volume"], config, "opt", job_id).grid

        assert mock_sink.call_count == 1
        # build_grid_from_parquet_chunked was called with correct chunking and column mappings
        assert mock_build.call_count == 1
        build_kwargs = mock_build.call_args
        assert build_kwargs.args[2] == 1_000
        assert build_kwargs.kwargs.get("objective") == "expected_income"
        assert build_kwargs.kwargs.get("scenario_value") == "scenario_value"
        assert "scenario_value_col" not in build_kwargs.kwargs
        assert result is mock_grid


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestExecutePipelineCleanup:
    """Verify the seed plan's lifecycle: the caller's stack holds it and releases it."""

    def test_execute_pipeline_holds_its_plan_on_the_callers_stack(self, scored_data):
        """The plan stays open while the caller reads the frames and closes with its stack."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        captured = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            lazy_outputs = service._execute_pipeline(body, job_id, resources)
            plan = captured["snapshot_plan"]
            assert not plan._closed

        assert isinstance(lazy_outputs, dict)
        assert plan._closed
        assert "checkpoint_dir" not in captured

    def test_execute_pipeline_error_leaves_its_plan_on_the_callers_stack(self, scored_data):
        """When the lazy execution raises, the caller's stack still closes the plan it opened."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        plans = []

        def failing_execute_lazy(*args, **kwargs):
            plans.append(kwargs["snapshot_plan"])
            raise RuntimeError("boom")

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=failing_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(RequestRefusedError):
                service._execute_pipeline(body, job_id, resources)
            assert not plans[0]._closed

        assert plans[0]._closed

    def test_execute_pipeline_error_raises_http_exception(self, scored_data):
        """Pipeline execution errors are wrapped in HTTPException(500)."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        def failing_execute_lazy(*args, **kwargs):
            raise RuntimeError("boom")

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=failing_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(RequestRefusedError) as exc_info:
                service._execute_pipeline(body, job_id, resources)
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Coverage expansion tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestExecutePipelineExtended:
    """Additional coverage for _execute_pipeline: preamble, streaming chunk."""

    def test_execute_pipeline_restores_chunk_size(self, scored_data):
        """When prev chunk size is set, it is restored after execution."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        captured_chunk_sizes = []

        def fake_execute_lazy(*args, **kwargs):
            import polars as pl

            captured_chunk_sizes.append(pl.Config.state().get(STREAMING_CHUNK_SIZE_ENV))
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, resources)

        # During execution, chunk size should have been set to 50000
        assert len(captured_chunk_sizes) == 1

    def test_execute_pipeline_exception_updates_job_store(self, scored_data):
        """Pipeline failure updates job store with error status."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=RuntimeError("pipeline broke"),
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(RequestRefusedError) as exc_info:
                service._execute_pipeline(body, job_id, resources)
            assert exc_info.value.status_code == 500

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert "pipeline" in job["message"].lower()

    def test_execute_pipeline_preserves_configured_optimiser_data_input(
        self,
        scored_data,
    ):
        """Configured data_input is consumed after graph execution, so preserve it."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph_dict = _make_optimiser_graph(
            scored_data,
            config={"data_input": "source"},
        )
        body = OptimiserFrontierAutoRangeRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})

        def fake_execute_lazy(*args, **kwargs):
            return ({"opt": MagicMock()}, [], {}, {})

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ) as execute,
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            service._execute_pipeline(body, job_id, resources)

        assert "source" in execute.call_args.kwargs["preserve_node_ids"]

    def test_execute_pipeline_uses_edited_helper_on_next_operation(
        self,
        tmp_path,
        monkeypatch,
    ):
        from haute.executor import _compile_preamble, execute_graph
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        use_project(tmp_path)
        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("", encoding="utf-8")
        (util_dir / "helpers.py").write_text("VALUE = 10\n", encoding="utf-8")

        scored_path = _make_scored_data(tmp_path)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(scored_path),
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "t",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = source.with_columns(v=pl.lit(VALUE))",
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "data_input": "t",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "t").model_dump(),
                    make_edge("t", "opt").model_dump(),
                ],
                "preamble": "from utility.helpers import VALUE\n",
                "source_file": str(tmp_path / "pipeline.py"),
            }
        )

        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserSolveRequest(graph=graph.model_dump(), node_id="opt")

        try:
            job_id_1 = store.create_job(
                {
                    "input_provenance": SOLVE_PROVENANCE,
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "status": "running",
                }
            )
            with contextlib.ExitStack() as resources:
                outputs = service._execute_pipeline(
                    body,
                    job_id_1,
                    resources,
                    target_node_id="t",
                )
                assert outputs["t"].collect()["v"][0] == 10

            # Edit helpers.py to VALUE = 200 (make the file size differ)
            (util_dir / "helpers.py").write_text(
                "VALUE = 200\n# longer file to ensure byte size differs\n",
                encoding="utf-8",
            )

            preview_res = execute_graph(graph, target_node_id="t")
            assert preview_res["t"].preview[0]["v"] == 200

            job_id_2 = store.create_job(
                {
                    "input_provenance": SOLVE_PROVENANCE,
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "status": "running",
                }
            )
            with contextlib.ExitStack() as resources:
                outputs_2 = service._execute_pipeline(
                    body,
                    job_id_2,
                    resources,
                    target_node_id="t",
                )
                assert outputs_2["t"].collect()["v"][0] == 200
        finally:
            _compile_preamble.cache_clear()

    def test_execute_pipeline_reuses_namespace_when_helper_is_unchanged(
        self,
        tmp_path,
        monkeypatch,
    ):
        from haute.executor import _compile_preamble
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        use_project(tmp_path)
        util_dir = tmp_path / "utility"
        util_dir.mkdir()
        (util_dir / "__init__.py").write_text("", encoding="utf-8")
        (util_dir / "helpers.py").write_text("VALUE = 10\n", encoding="utf-8")

        scored_path = _make_scored_data(tmp_path)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(scored_path),
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "t",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = source.with_columns(v=pl.lit(VALUE))",
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "data_input": "t",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "t").model_dump(),
                    make_edge("t", "opt").model_dump(),
                ],
                "preamble": "from utility.helpers import VALUE\n",
                "source_file": str(tmp_path / "pipeline.py"),
            }
        )

        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserSolveRequest(graph=graph.model_dump(), node_id="opt")

        _compile_preamble.cache_clear()
        initial_hits = _compile_preamble.cache_info().hits

        try:
            job_id_1 = store.create_job(
                {
                    "input_provenance": SOLVE_PROVENANCE,
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "status": "running",
                }
            )
            with contextlib.ExitStack() as resources:
                outputs_1 = service._execute_pipeline(
                    body,
                    job_id_1,
                    resources,
                    target_node_id="t",
                )
                val_1 = outputs_1["t"].collect()["v"][0]

            job_id_2 = store.create_job(
                {
                    "input_provenance": SOLVE_PROVENANCE,
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "status": "running",
                }
            )
            with contextlib.ExitStack() as resources:
                outputs_2 = service._execute_pipeline(
                    body,
                    job_id_2,
                    resources,
                    target_node_id="t",
                )
                val_2 = outputs_2["t"].collect()["v"][0]

            assert val_1 == val_2 == 10
            assert _compile_preamble.cache_info().hits > initial_hits
        finally:
            _compile_preamble.cache_clear()


class TestValidateAndProject:
    """Tests for _validate_and_project."""

    def test_missing_columns_error(self):
        """Missing required columns raises HTTPException with column names."""

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

        with pytest.raises(RequestRefusedError) as exc_info:
            service._validate_and_project(source_lf, config, job_id)
        assert exc_info.value.status_code == 400
        assert "volume" in exc_info.value.detail

    def test_column_casting_rejects_null_quote_ids_loudly(self):
        """Projection rejects invalid quote_id rows before grid construction."""
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

        with pytest.raises(RequestRefusedError) as exc_info:
            service._validate_and_project(source_lf, config, job_id)

        assert exc_info.value.status_code == 400
        assert str(exc_info.value.detail).startswith(
            "Null quote_id values found in optimiser input"
        )
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert job["message"] == exc_info.value.detail

    def test_numeric_quote_id_rejected(self):
        """Numeric quote IDs fail loudly instead of being coerced to strings."""

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

        with pytest.raises(RequestRefusedError) as exc_info:
            service._validate_and_project(source_lf, config, job_id)

        assert exc_info.value.status_code == 400
        assert "quote_id must be Utf8" in exc_info.value.detail
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"

    def test_empty_constraints_returns_no_constraint_cols(self):
        """With empty constraints dict, constraint_cols is empty list."""
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
        from haute._polars_utils import set_streaming_chunk_size

        set_streaming_chunk_size(1_024)

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
        }

        mock_grid = MagicMock(scenario_values=[0.9, 1.1])
        with (
            patch("haute.routes._optimiser_input.bounded_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=mock_grid,
            ) as mock_build,
        ):
            # Make bounded_sink actually write the file
            def do_sink(lf, path, **kw):
                lf.collect().write_parquet(path)

            mock_sink.side_effect = do_sink

            result = service._build_grid(scored_lf, ["vol"], config, "opt", job_id).grid

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

        assert not Path(parquet_path).exists()

    def test_build_grid_reads_in_the_pipeline_chunk_size(self, tmp_path):
        """The grid is built in chunks of the Pipeline Settings streaming chunk size."""
        from haute._polars_utils import set_streaming_chunk_size
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {},
            }
        )
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
        set_streaming_chunk_size(3)

        with (
            patch("haute.routes._optimiser_input.bounded_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=MagicMock(scenario_values=[0.9, 1.1]),
            ) as mock_build,
        ):
            mock_sink.side_effect = lambda lf, path, **_kw: lf.collect().write_parquet(path)
            service._build_grid(scored_lf, ["vol"], config, "opt", job_id)

        assert mock_build.call_args.args[2] == 3
        assert mock_build.call_args.kwargs["n_steps"] == 2
        assert store.get_job(job_id)["setup_chunking"]["optimiser_grid"] == {
            "policy": "pipeline_setting",
            "chunk_size": 3,
            "pipeline_chunk_size": 3,
            "n_steps": 2,
            "source": "optimiser_grid",
        }

    def test_ratebook_factor_contexts_read_in_the_pipeline_chunk_size(self, tmp_path) -> None:
        """Ratebook factor contexts are built in chunks of the pipeline's streaming chunk size."""
        from haute._polars_utils import set_streaming_chunk_size
        from haute.routes._optimiser_artifacts import (
            _cleanup_ratebook_factors_artifact,
            _persist_ratebook_factors_lazy_artifact,
        )
        from haute.routes._optimiser_solver import _build_ratebook_factor_contexts

        factors_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q3", "q4"],
                "region": ["north", "south", "north", "west"],
            }
        )
        handle = _persist_ratebook_factors_lazy_artifact(factors_df.lazy())
        assert handle is not None
        quote_grid = SimpleNamespace(quote_ids=["q1", "q2", "q3", "q4"], n_quotes=4)
        set_streaming_chunk_size(2)

        try:
            with patch(
                "price_contour.build_ratebook_factor_contexts_from_parquet_chunked",
                return_value=MagicMock(),
            ) as mock_build:
                _build_ratebook_factor_contexts(
                    handle,
                    quote_grid,
                    {"quote_id": "quote_id"},
                    [["region"]],
                )
        finally:
            _cleanup_ratebook_factors_artifact(handle)

        assert mock_build.call_args.args[2] == 2

    @pytest.mark.parametrize("chunk_size", [500_000, 4, 3, 2, 1])
    def test_build_grid_accepts_categorical_quote_id_with_real_price_contour(
        self,
        chunk_size,
    ):
        """The real grid builder accepts categorical quote IDs at any pipeline chunk size.

        A setting below one quote's three rows reads a whole quote per chunk.
        """
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
        config = {
            "objective": "income",
            "constraints": {"vol": {"min": 0.9}},
            "quote_id": "quote_key",
            "scenario_index": "scenario_step",
            "scenario_value": "price_factor",
        }
        from haute._polars_utils import set_streaming_chunk_size

        # One pipeline chunk holds the whole grid; smaller settings split quotes.
        set_streaming_chunk_size(chunk_size)

        scored_lf = pl.LazyFrame(
            {
                "quote_key": pl.Series(
                    ["q1", "q1", "q1", "q2", "q2", "q2"],
                    dtype=pl.Categorical,
                ),
                "scenario_step": pl.Series([0, 1, 2, 0, 1, 2], dtype=pl.Int32),
                "price_factor": pl.Series([0.9, 1.0, 1.1, 0.9, 1.0, 1.1], dtype=pl.Float32),
                "income": pl.Series([100.0, 105.0, 110.0, 200.0, 210.0, 220.0], dtype=pl.Float32),
                "vol": pl.Series([0.9, 0.87, 0.85, 0.95, 0.92, 0.90], dtype=pl.Float32),
            }
        )

        grid = service._build_grid(scored_lf, ["vol"], config, "opt", job_id).grid

        assert grid.quote_ids == ["q1", "q2"]
        assert grid.n_quotes == 2
        assert grid.n_steps == 3
        assert grid.scenario_values == pytest.approx([0.9, 1.0, 1.1])
        assert store.get_job(job_id)["setup_chunking"]["optimiser_grid"]["chunk_size"] == max(
            chunk_size, 3
        )

    def test_build_grid_failure_updates_job_store(self, tmp_path):
        """Grid construction failure updates job store with error."""

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
            patch("haute.routes._optimiser_input.bounded_sink") as mock_sink,
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                side_effect=RuntimeError("grid failed"),
            ),
        ):
            mock_sink.side_effect = lambda lf, path, **kw: lf.collect().write_parquet(path)

            with pytest.raises(RequestRefusedError) as exc_info:
                service._build_grid(scored_lf, [], config, "opt", job_id)
            assert exc_info.value.status_code == 500
            assert (
                exc_info.value.detail
                == "Grid construction failed. Check the server logs for details."
            )

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert job["message"] == "Grid construction failed. Check the server logs for details."

    def test_build_grid_bounded_sink_failure_is_http_422(self) -> None:
        """Bounded streaming incompatibility must not be reported as a generic 500."""

        from haute.errors import BoundedMemoryUnsupportedError
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
        scored_lf = pl.LazyFrame(
            {
                "quote_id": pl.Series(["q1"], dtype=pl.Utf8),
                "scenario_index": pl.Series([0], dtype=pl.Int32),
                "scenario_value": pl.Series([1.0], dtype=pl.Float32),
                "expected_income": pl.Series([100.0], dtype=pl.Float32),
            }
        )

        with patch(
            "haute.routes._optimiser_input.bounded_sink",
            side_effect=BoundedMemoryUnsupportedError("Bounded streaming sink failed"),
        ):
            with pytest.raises(RequestRefusedError) as exc_info:
                service._build_grid(
                    scored_lf,
                    [],
                    {"objective": "expected_income"},
                    "opt",
                    job_id,
                )

        assert exc_info.value.status_code == 422
        assert "bounded streaming mode" in exc_info.value.detail
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"


class TestResolveDataInputFrame:
    """Tests for _resolve_data_input_frame."""

    def test_uses_data_input_id(self):
        """When data_input is set and present, uses that output."""
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

        mock_lf = MagicMock()
        lazy_outputs = {"data_node": mock_lf, "opt": MagicMock()}
        config = {"data_input": "data_node"}

        result = service._resolve_data_input_frame(
            lazy_outputs, _optimiser_helper_graph("data_node"), config, "opt", job_id
        )
        assert result is mock_lf

    def test_node_output_without_incoming_edge_raises_400(self):
        """An optimiser without an incoming edge cannot use its own output as data."""

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

        mock_lf = MagicMock()
        lazy_outputs = {"opt": mock_lf}
        config = {}

        with pytest.raises(RequestRefusedError) as exc_info:
            service._resolve_data_input_frame(
                lazy_outputs, _optimiser_helper_graph(), config, "opt", job_id
            )

        assert exc_info.value.status_code == 400
        assert "no data" in exc_info.value.detail.lower()
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"

    def test_configured_data_input_missing_raises_400(self):
        """A configured data_input must be present instead of falling back."""

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

        lazy_outputs = {"opt": MagicMock()}
        config = {"data_input": "missing_data"}

        with pytest.raises(RequestRefusedError) as exc_info:
            service._resolve_data_input_frame(
                lazy_outputs, _optimiser_helper_graph("missing_data"), config, "opt", job_id
            )

        assert exc_info.value.status_code == 400
        assert "missing_data" in exc_info.value.detail
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"

    def test_no_data_raises_400(self):
        """When no data arrives at the node, raises HTTPException."""

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

        lazy_outputs = {}
        config = {}

        with pytest.raises(RequestRefusedError) as exc_info:
            service._resolve_data_input_frame(
                lazy_outputs, _optimiser_helper_graph(), config, "opt", job_id
            )

        assert exc_info.value.status_code == 400
        assert "no data" in exc_info.value.detail.lower()
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestExecutePipelineRefusalPassthrough:
    """Test that a refusal raised inside _execute_pipeline is re-raised directly."""

    def test_http_exception_passthrough(self, scored_data):
        """A refusal from the lazy execution is re-raised, not wrapped."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        graph_dict = _make_optimiser_graph(scored_data)
        body = OptimiserSolveRequest(graph=graph_dict, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
            }
        )

        original_exc = ForbiddenError("forbidden")

        with (
            contextlib.ExitStack() as resources,
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                side_effect=original_exc,
            ),
            patch("haute.executor._resolve_batch_scenario", return_value=None),
            patch("haute.executor._compile_preamble", return_value={}),
        ):
            with pytest.raises(RequestRefusedError) as exc_info:
                service._execute_pipeline(body, job_id, resources)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "forbidden"


class TestBuildGridRefusalPassthrough:
    """Test that a refusal in _build_grid is re-raised."""

    def test_http_exception_passthrough(self, tmp_path):
        """A refusal from build_grid_from_parquet_chunked is re-raised."""

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

        original_exc = ForbiddenError("not allowed")

        with (
            patch("haute.routes._optimiser_input.bounded_sink") as mock_sink,
            patch("price_contour.build_grid_from_parquet_chunked", side_effect=original_exc),
        ):
            mock_sink.side_effect = lambda lf, path, **kw: lf.collect().write_parquet(path)

            with pytest.raises(RequestRefusedError) as exc_info:
                service._build_grid(scored_lf, [], config, "opt", job_id)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "not allowed"
