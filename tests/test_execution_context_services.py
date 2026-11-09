"""How the optimiser, training and deploy services use execution contexts.

The optimiser pipeline, setup, grid and auto-range jobs, training preparation and start, and deploy
scoring and pyfunc prediction forward or create admitted contexts and map admission and memory
failures to typed errors and HTTP 507.
"""

import time
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_context import (
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._project_context import current_project
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.deploy._request_rows import RequestRows
from haute.errors import (
    InsufficientMemoryError,
    RequestRefusedError,
)
from haute.graph_utils import NodeType
from tests._training_helpers import join_training_preparation
from tests.conftest import (
    make_edge,
    make_graph,
    make_output_config,
    make_ram_estimate,
)
from tests.optimiser_fixtures import setup_grid_stub

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


class _ImmediateThread:
    """Run a background target inline while preserving the Thread constructor API."""

    def __init__(self, *, target, daemon, args=(), kwargs=None, name=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon

    def start(self) -> None:
        self.target(*self.args, **self.kwargs)


def _auto_range_prepared(node: Any) -> tuple[Any, dict[str, Any]]:
    """What ``_prepare_frontier_auto_range`` returns: the node and the job's kwargs."""
    return node, {
        "config": {"objective": "expected_income", "constraints": {}},
        "mode": "online",
        "timeout": 10,
        "required_columns_by_node": {},
    }


def test_optimiser_auto_range_start_admits_nothing_in_the_request_thread(monkeypatch) -> None:
    """The background job admits, as solve setup does; the request thread never holds memory."""
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    def refuse(**_kwargs: Any) -> Any:
        raise AssertionError("auto-range start admitted in the request thread")

    monkeypatch.setattr("haute.routes._optimiser_service.admit_growth_grant", refuse)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    service = OptimiserSolveService(JobStore())
    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value=_auto_range_prepared(graph.node_map["opt"]),
        ),
        patch.object(service, "_launch_frontier_auto_range_background") as launch,
    ):
        started = service.start_frontier_auto_range(body)

    assert started.status == "started"
    assert "execution_context" not in launch.call_args.kwargs
    assert launch.call_args.kwargs["execution_token"] is not None


def test_train_prepare_training_data_forwards_execution_context(tmp_path) -> None:
    from haute.routes._training_preparation import (
        TrainingPreparationRequest,
        prepare_training_data,
    )

    config = {"target": "target", "feature_columns": ["feature"]}
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": dict(config),
                    },
                },
            ],
            "edges": [],
        }
    )
    job_id = "job-forwards-context"
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        frame = pl.DataFrame({"target": [1.0], "feature": [2.0]}).lazy()
        return {"model": frame}, ["model"], {}, {}

    tmp_parquet = str(tmp_path / "prepared.parquet")
    request = TrainingPreparationRequest(
        graph=graph,
        node_id="model",
        job_id=job_id,
        source="live",
        parquet_path=tmp_parquet,
        config=config,
    )
    with patch(
        "haute.routes._training_preparation.execute_lazy_graph", side_effect=fake_execute_lazy
    ):
        outcome = prepare_training_data(request, execution_context=context)

    assert outcome.failure is None
    assert captured["execution_context"] is context
    assert any(metric.name == "training_sink_write" for metric in context.metrics.snapshot())
    # The child's own payload is the one the supervisor persists on the job.
    stored_metrics = outcome.execution_metrics
    assert stored_metrics is not None
    assert stored_metrics["operation"] == "training"
    assert stored_metrics["job_id"] == job_id
    assert pl.read_parquet(tmp_parquet)["target"].to_list() == [1.0]


def test_deploy_score_graph_forwards_execution_context_to_lazy_executor() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"output": pl.DataFrame({"score": [0.25]}).lazy()}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
            execution_context=context,
        )

    assert result["score"].to_list() == [0.25]
    assert captured["execution_context"] is context
    assert captured["source"] == "live"
    assert any(metric.name == "deploy_collect" for metric in context.metrics.snapshot())


def test_deploy_batch_graph_routing_stays_live_for_source_switch(tmp_path) -> None:
    from haute.deploy._scorer import score_graph

    batch_path = tmp_path / "batch.parquet"
    pl.DataFrame({"origin": ["batch"], "value": [999]}).write_parquet(batch_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "live_src",
                    "data": {
                        "label": "Live API Request",
                        "nodeType": NodeType.API_INPUT.value,
                        "config": {"path": ""},
                    },
                },
                {
                    "id": "batch_src",
                    "data": {
                        "label": "batch_src",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {"path": "batch.parquet"},
                    },
                },
                {
                    "id": "switch",
                    "data": {
                        "label": "switch",
                        "nodeType": NodeType.LIVE_SWITCH.value,
                        "config": {
                            "input_scenario_map": {
                                "quotes": "live",
                                "batch_src": ExecutionProfile.DEPLOY_BATCH.value,
                            }
                        },
                    },
                },
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config(["origin", "value"]),
                    },
                },
            ],
            "edges": [
                make_edge("live_src", "switch")
                .model_copy(update={"sourceHandle": "quotes"})
                .model_dump(),
                make_edge("batch_src", "switch").model_dump(),
                make_edge("switch", "output").model_dump(),
            ],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_BATCH,
        memory_sampler=lambda: 1_000,
    )

    result = score_graph(
        graph,
        pl.DataFrame({"origin": ["live", "live"], "value": [1, 2]}),
        input_node_ids=["live_src"],
        output_node_id="output",
        artifact_paths={"batch_src__batch.parquet": str(batch_path)},
        execution_context=context,
    )

    assert result["origin"].to_list() == ["live", "live"]
    assert result["value"].to_list() == [1, 2]


def test_deploy_score_graph_final_collect_uses_streaming_engine() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        memory_sampler=lambda: 1_000,
    )

    class BackgroundQuery:
        def fetch(self):
            return pl.DataFrame({"score": [0.25]})

        def cancel(self):
            raise AssertionError("completed query must not be cancelled")

    class CollectingLazy:
        collect_kwargs = None

        def collect(self, **kwargs):
            self.collect_kwargs = kwargs
            return BackgroundQuery()

    output_lf = CollectingLazy()

    def fake_execute_lazy(*_args, **kwargs):
        assert kwargs["execution_context"] is context
        return {"output": output_lf}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
            execution_context=context,
        )

    assert result["score"].to_list() == [0.25]
    assert output_lf.collect_kwargs == {"engine": "streaming", "background": True}
    assert any(metric.name == "deploy_collect" for metric in context.metrics.snapshot())


def test_deploy_score_graph_final_collect_preserves_execution_context_memory_error() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        job_id="job-1",
        memory_sampler=lambda: 1_000,
    )
    memory_error = ExecutionMemoryLimitExceededError(
        "deploy",
        job_id="job-1",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    class FailingLazy:
        collect_kwargs = None

        def collect(self, **kwargs):
            self.collect_kwargs = kwargs
            raise memory_error

    output_lf = FailingLazy()

    def fake_execute_lazy(*_args, **_kwargs):
        return {"output": output_lf}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
            score_graph(
                graph,
                pl.DataFrame({"feature": [1]}),
                input_node_ids=[],
                output_node_id="output",
                execution_context=context,
            )

    assert exc_info.value is memory_error
    assert output_lf.collect_kwargs == {"engine": "streaming", "background": True}
    metrics = context.metrics.snapshot()
    assert [metric.name for metric in metrics] == ["deploy_collect"]
    assert metrics[0].node_id == "output"


def test_deploy_score_graph_creates_admitted_context_when_omitted(monkeypatch) -> None:
    from haute.deploy._scorer import score_graph

    monkeypatch.setenv("HAUTE_DEPLOY_LIVE_MEMORY_LIMIT_MB", "96")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 13_000)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"output": pl.DataFrame({"score": [0.25]}).lazy()}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
        )

    assert result["score"].to_list() == [0.25]
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.DEPLOY_LIVE
    assert context.memory_limit_bytes == 96 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 13_000


def test_deploy_score_graph_retain_admission_requires_caller_owned_context() -> None:
    from haute.deploy._scorer import score_graph

    with pytest.raises(ValueError, match="caller-owned execution_context"):
        score_graph(
            make_graph({"nodes": [], "edges": []}),
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
            retain_admission_on_success=True,
        )


def test_deploy_score_graph_can_retain_admission_after_success() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    released: list[str] = []
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        memory_sampler=lambda: 1,
        admission_release=lambda: released.append("released"),
    )

    with patch(
        "haute.deploy._scorer.execute_lazy_graph",
        return_value=(
            {"output": pl.DataFrame({"score": [0.25]}).lazy()},
            ["output"],
            {},
            {},
        ),
    ):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
            execution_context=context,
            retain_admission_on_success=True,
        )

    assert result["score"].to_list() == [0.25]
    assert released == []
    context.release_admission()
    assert released == ["released"]


def test_admit_deploy_execution_rejects_negative_row_count() -> None:
    from haute.deploy._scorer import admit_deploy_execution

    with pytest.raises(ValueError, match="row_count must be non-negative"):
        admit_deploy_execution(operation="deploy_quote", row_count=-1)


def _minimal_optimiser_setup_graph() -> PipelineGraph:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {
                            "input_type": "inline",
                            "format": "records",
                            "records": [
                                {
                                    "quote_id": "q1",
                                    "scenario_index": 0,
                                    "scenario_value": 1.0,
                                    "expected_income": 10.0,
                                }
                            ],
                        },
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {
                            "objective": "expected_income",
                            "constraints": {},
                            "data_input": "source",
                        },
                    },
                },
            ],
            "edges": [{"id": "e_source_opt", "source": "source", "target": "opt"}],
        }
    )


def test_optimiser_start_creates_admitted_setup_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "768")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 128 * 1024 * 1024,
    )
    graph = _minimal_optimiser_setup_graph()
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
            "volume": [1.0],
        }
    )
    service = OptimiserSolveService(JobStore())
    captured = {}

    def fake_execute_pipeline(*_args, **kwargs):
        captured["execution_context"] = kwargs["execution_context"]
        return {"source": scored_lf}

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", side_effect=fake_execute_pipeline),
        patch.object(service, "_validate_and_project", return_value=([], scored_lf)) as validate,
        patch.object(service, "_extract_factors", return_value=None) as extract,
        patch.object(service, "_build_grid", return_value=setup_grid_stub()) as build,
        patch.object(service, "_launch_background") as launch,
    ):
        response = service.start(body)

    assert response.status == "started"
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.OPTIMISER_SOLVE
    assert context.memory_limit_bytes == 768 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 128 * 1024 * 1024
    validate.assert_called_once()
    assert validate.call_args.kwargs["execution_context"] is context
    extract.assert_called_once()
    assert extract.call_args.kwargs["execution_context"] is context
    build.assert_called_once()
    assert build.call_args.kwargs["execution_context"] is context
    ctx_arg = launch.call_args.args[0]
    assert ctx_arg.execution_context is context
    assert ctx_arg.registration_already_active is True


def test_optimiser_cancel_during_setup_prevents_worker_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = _minimal_optimiser_setup_graph()
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)

    def cancel_during_grid(*_args, **_kwargs):
        job_id = next(iter(store.list_jobs()))
        service.cancel_solve(job_id)
        return object()

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", return_value={"source": scored_lf}),
        patch.object(service, "_validate_and_project", return_value=([], scored_lf)),
        patch.object(service, "_extract_factors", return_value=None),
        patch.object(service, "_build_grid", side_effect=cancel_during_grid),
        patch.object(service, "_launch_background") as launch,
    ):
        response = service.start(body)

    assert response.status == "started"
    launch.assert_not_called()
    job = next(iter(store.list_jobs().values()))
    assert job["status"] == "cancelled"
    assert job["terminal_reason"] == "cancelled"


def test_optimiser_start_maps_admission_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
    graph = _minimal_optimiser_setup_graph()

    store = JobStore()
    service = OptimiserSolveService(store)

    with patch.object(service, "_execute_pipeline") as execute_pipeline:
        with patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread):
            response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    execute_pipeline.assert_not_called()
    job = next(iter(store.list_jobs().values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["error_detail"]["profile"] == "optimiser_solve"
    assert job["error_detail"]["reason"] == "process_rss_limit_exceeded"
    # The job's message is the curated one the refusal carries, naming the operation.
    assert job["message"] == job["error_detail"]["message"]
    assert job["message"].startswith("Optimiser setup was not started")


def test_optimiser_start_maps_runtime_memory_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_BYTES", "512")
    samples = iter([1, 1, 600])
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: next(samples))
    graph = _minimal_optimiser_setup_graph()
    service = OptimiserSolveService(JobStore())
    memory_error = ExecutionMemoryLimitExceededError(
        "optimiser_solve",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", side_effect=memory_error),
    ):
        response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    job = next(iter(service._store.list_jobs().values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["error_detail"]["operation"] == "optimiser_solve"
    assert job["error_detail"]["reason"] == "rss_exceeds_memory_limit"


def test_optimiser_start_records_setup_stage_metrics_when_memory_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_BYTES", "512")
    samples = iter([1, 1, 600])
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: next(samples))
    graph = _minimal_optimiser_setup_graph()
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", return_value={"source": scored_lf}),
    ):
        response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    job = next(iter(store.list_jobs().values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    metrics = job["execution_metrics"]
    assert metrics["status"] == "memory_limited"
    assert metrics["terminal_reason"] == "memory_limited"
    assert "optimiser_validate_and_project" in metrics["stage_elapsed_ms"]


def test_optimiser_start_preserves_typed_memory_http_exception_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = _minimal_optimiser_setup_graph()
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)
    memory_payload = {"error_code": "memory_limit", "reason": "rss_exceeds_memory_limit"}

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", return_value={"source": scored_lf}),
        patch.object(
            service,
            "_validate_and_project",
            side_effect=InsufficientMemoryError(memory_payload),
        ),
    ):
        response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    job = next(iter(store.list_jobs().values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_detail"] == memory_payload
    metrics = job["execution_metrics"]
    assert metrics["status"] == "memory_limited"
    assert metrics["terminal_reason"] == "memory_limited"
    assert metrics["memory_limit_bytes"] == 512 * 1024 * 1024
    assert metrics["admission"]["profile"] == ExecutionProfile.OPTIMISER_SOLVE.value


def test_optimiser_extract_factors_sinks_without_projected_frame_budget() -> None:
    from haute.routes._optimiser_service import OptimiserSolveService

    context = ExecutionContext(
        operation="optimiser_solve",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        job_id="job-1",
        memory_limit_bytes=1,
        memory_baseline_bytes=0,
        rss_limit_bytes=1,
        memory_sampler=lambda: 0,
    )

    handle = OptimiserSolveService._extract_factors(
        {"band": pl.LazyFrame({"quote_id": ["q1"], "factor": ["A"]})},
        PipelineGraph(
            nodes=[
                GraphNode(id="band", data=NodeData(label="band", nodeType="dataInput")),
                GraphNode(id="opt", data=NodeData(label="opt", nodeType="optimiser")),
            ],
            edges=[GraphEdge(id="e_band_opt", source="band", target="opt")],
        ),
        "opt",
        {"banding_source": "band", "factor_columns": [["factor"]]},
        "ratebook",
        execution_context=context,
    )

    assert handle["row_count"] == 1
    assert context.metrics.snapshot()[0].name == "optimiser_extract_factors"


def test_optimiser_build_grid_preserves_memory_limit_error() -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService

    service = OptimiserSolveService(JobStore())
    job_id = service._store.create_job({"status": "running"})
    memory_error = ExecutionMemoryLimitExceededError(
        "optimiser_solve",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with patch("haute.routes._optimiser_input.bounded_sink", side_effect=memory_error):
        with pytest.raises(ExecutionMemoryLimitExceededError):
            service._build_grid(
                pl.LazyFrame({"quote_id": ["q1"], "scenario_index": [0]}),
                [],
                {"objective": "objective"},
                "opt",
                job_id,
            )


def test_auto_range_cancel_reaches_the_background_jobs_execution_token() -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    service = OptimiserSolveService(JobStore())
    captured: dict[str, Any] = {}

    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value=_auto_range_prepared(graph.nodes[0]),
        ),
        patch.object(
            service,
            "_launch_frontier_auto_range_background",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ),
    ):
        response = service.start_frontier_auto_range(body)

    assert response.status == "started"
    token = captured["execution_token"]
    assert not token.cancelled
    service.cancel_frontier_auto_range(response.job_id)
    assert token.cancelled


def test_auto_range_duplicate_start_reuses_running_job_without_relaunching() -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    store = JobStore()
    service = OptimiserSolveService(store)
    captured_tokens = []

    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value=_auto_range_prepared(graph.nodes[0]),
        ),
        patch.object(
            service,
            "_launch_frontier_auto_range_background",
            side_effect=lambda *_args, **kwargs: captured_tokens.append(kwargs["execution_token"]),
        ),
    ):
        first = service.start_frontier_auto_range(body)
        second = service.start_frontier_auto_range(body)

    assert second.job_id == first.job_id
    assert store.require_job(first.job_id)["status"] == "running"
    assert len(captured_tokens) == 1
    assert not captured_tokens[0].cancelled


def test_auto_range_background_memory_limit_status_exposes_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    node = graph.nodes[0]
    store = JobStore()
    service = OptimiserSolveService(store)
    memory_error = ExecutionMemoryLimitExceededError(
        "frontier_auto_range",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value=_auto_range_prepared(node),
        ),
        patch.object(service, "_execute_pipeline", side_effect=memory_error),
    ):
        started = service.start_frontier_auto_range(body)
        deadline = time.monotonic() + 2
        status = service.frontier_auto_range_status(started.job_id)
        while status.status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            status = service.frontier_auto_range_status(started.job_id)

    assert status.status == "memory_limited"
    assert status.terminal_reason == "memory_limited"
    assert status.error_code == "memory_limit"
    assert status.http_status_code == 507
    assert status.error_detail is not None
    assert status.error_detail.error_code == "memory_limit"
    assert status.error_detail.operation == "frontier_auto_range"
    assert status.error_detail.reason == "rss_exceeds_memory_limit"
    assert status.execution_metrics is not None
    assert status.execution_metrics.terminal_reason == "memory_limited"
    job = store.require_job(started.job_id)
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["http_status_code"] == 507


def test_auto_range_background_preserves_typed_memory_http_exception_status() -> None:

    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
    payload = {
        "error_code": "memory_limit",
        "operation": "frontier_auto_range",
        "job_id": job_id,
        "memory_limit_bytes": 512,
        "rss_bytes": 600,
        "reason": "rss_exceeds_memory_limit",
    }

    with patch.object(
        service,
        "_execute_pipeline",
        side_effect=InsufficientMemoryError(payload),
    ):
        with pytest.raises(RequestRefusedError) as exc_info:
            service._run_frontier_auto_range_job(
                body,
                job_id,
                **_auto_range_prepared(graph.nodes[0])[1],
            )

    assert exc_info.value.status_code == 507
    status = service.frontier_auto_range_status(job_id)
    assert status.status == "memory_limited"
    assert status.terminal_reason == "memory_limited"
    assert status.error_code == "memory_limit"
    assert status.http_status_code == 507
    assert status.error_detail is not None
    assert status.error_detail.job_id == job_id
    assert status.error_detail.reason == "rss_exceeds_memory_limit"
    assert status.execution_metrics is not None
    assert status.execution_metrics.terminal_reason == "memory_limited"


def test_training_start_creates_admitted_training_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._training_lifecycle import TrainService
    from haute.schemas import TrainRequest

    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "1024")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 256 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {
                            "target": "target",
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                            "feature_columns": ["feature"],
                            "evaluation": {
                                "schema_version": 1,
                                "strategy": "random",
                                "seed": 42,
                                "validation": {"method": "single", "size": 0.2},
                            },
                        },
                    },
                },
            ],
            "edges": [],
        }
    )
    body = TrainRequest(graph=graph, node_id="model")
    service = TrainService(JobStore())
    captured = {}
    tmp_parquet = tmp_path / "training.parquet"
    tmp_parquet.write_bytes(b"placeholder")

    def fake_execute_and_sink(*_args, **kwargs):
        captured["execution_context"] = kwargs["execution_context"]
        return str(tmp_parquet)

    with (
        patch.object(service, "_compile_preamble", return_value={}),
        patch.object(service, "_estimate_ram", return_value=make_ram_estimate()),
        patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
        patch.object(service, "_execute_and_sink", side_effect=fake_execute_and_sink),
        patch.object(service, "_launch_background"),
    ):
        response = service.start(body)
        join_training_preparation(response.job_id)

    assert response.status == "started"
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.TRAINING_PREP
    assert context.memory_limit_bytes == 1024 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 256 * 1024 * 1024
    context.release_admission()
    service._training_jobs.release(response.job_id)


def test_training_start_maps_admission_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._training_lifecycle import TrainService
    from haute.schemas import TrainRequest

    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_TRAINING_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {
                            "target": "target",
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                            "feature_columns": ["feature"],
                            "evaluation": {
                                "schema_version": 1,
                                "strategy": "random",
                                "seed": 42,
                                "validation": {"method": "single", "size": 0.2},
                            },
                        },
                    },
                },
            ],
            "edges": [],
        }
    )
    store = JobStore()
    service = TrainService(store)

    with (
        patch.object(service, "_compile_preamble", return_value={}),
        patch.object(service, "_estimate_ram", return_value=make_ram_estimate()),
        patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
        patch.object(service, "_execute_and_sink") as execute_and_sink,
    ):
        response = service.start(TrainRequest(graph=graph, node_id="model"))
        join_training_preparation(response.job_id)

    execute_and_sink.assert_not_called()
    job = store.require_job(response.job_id)
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_code"] == "memory_limit"
    assert job["error_detail"]["profile"] == "training_prep"
    assert job["error_detail"]["reason"] == "process_rss_limit_exceeded"
    # The public message/error is the shared curated admission wording; the
    # internal reason string stays in error_detail only.
    assert job["error"] == (
        "Training was not started because the server does not have enough free "
        "memory for it (the server is already using 65.0 MiB of its 64.0 MiB "
        "memory cap). Wait for other work to finish, reduce the data size, or "
        "run on a server with more memory, then try again."
    )


def test_training_start_maps_runtime_memory_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._training_lifecycle import TrainService
    from haute.schemas import TrainRequest

    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {
                            "target": "target",
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                            "feature_columns": ["feature"],
                            "evaluation": {
                                "schema_version": 1,
                                "strategy": "random",
                                "seed": 42,
                                "validation": {"method": "single", "size": 0.2},
                            },
                        },
                    },
                },
            ],
            "edges": [],
        }
    )
    service = TrainService(JobStore())
    memory_error = ExecutionMemoryLimitExceededError(
        "training_pipeline",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with (
        patch.object(service, "_compile_preamble", return_value={}),
        patch.object(service, "_estimate_ram", return_value=make_ram_estimate()),
        patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
        patch.object(service, "_execute_and_sink", side_effect=memory_error),
    ):
        response = service.start(TrainRequest(graph=graph, node_id="model"))
        join_training_preparation(response.job_id)

    job = service._store.require_job(response.job_id)
    assert job["status"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_code"] == "memory_limit"
    assert job["error_detail"]["operation"] == "training_pipeline"
    assert job["error_detail"]["reason"] == "rss_exceeds_memory_limit"


def test_deploy_pyfunc_predict_creates_admitted_live_context(monkeypatch) -> None:
    import pandas as pd

    from haute.deploy._model_code import HauteModel

    monkeypatch.setenv("HAUTE_DEPLOY_LIVE_MEMORY_LIMIT_MB", "128")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 11_000)

    model = HauteModel()
    model._graph = make_graph({"nodes": [], "edges": []})
    model._project = current_project()
    model._input_node_ids = ["src"]
    model._output_node_id = "out"
    model._artifact_paths = {}
    model._output_fields = None
    model._request_rows = RequestRows("in_request_order")
    model._omittable = frozenset()
    model._input_schema = {"feature": pl.Float64()}
    expected_result = pl.DataFrame({"score": [0.25]})

    with patch("haute.deploy._scorer.score_graph", return_value=expected_result) as score:
        result = model.predict(object(), pd.DataFrame({"feature": [1.0]}))

    assert result["score"].to_list() == [0.25]
    context = score.call_args.kwargs["execution_context"]
    assert context.profile == ExecutionProfile.DEPLOY_LIVE
    assert context.memory_limit_bytes == 128 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 11_000
