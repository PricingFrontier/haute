"""Eager, lazy and sink graph execution under an execution context.

Eager execution records collect stages and swallows only ordinary node errors, lazy execution
checks cancellation before node work and records build and capture stages, and the sink forwards
its context to the lazy executor.
"""

from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_walker import CollectPolicy, walk_graph
from haute.errors import (
    ContractMismatchError,
    SchemaMismatchError,
)
from haute.execution import execute_lazy_graph
from haute.graph_utils import NodeType
from tests._project import use_project
from tests.conftest import (
    make_edge,
    make_file_output_config,
    make_graph,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _run_eager(
    graph: Any,
    build_node_fn: Any,
    *,
    target_node_id: str,
    swallow_errors: bool = False,
    execution_context: ExecutionContext | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """One eager execution: a display walk, recording node failures when asked."""
    walked = walk_graph(
        graph,
        build_node_fn,
        policy=CollectPolicy.display(record_failures=swallow_errors),
        target_node_id=target_node_id,
        execution_context=execution_context,
    )
    return walked.collected, walked.errors


def test_eager_graph_execution_records_collect_stages() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
                {
                    "id": "derived",
                    "data": {
                        "label": "derived",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
            ],
            "edges": [make_edge("source", "derived").model_dump()],
        }
    )
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 1_000,
    )

    def build_node_fn(node, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True
        return (
            node.id,
            lambda df: df.with_columns((pl.col("a") + 1).alias("b")),
            False,
        )

    outputs, _errors = _run_eager(
        graph, build_node_fn, target_node_id="derived", execution_context=context
    )

    assert outputs["derived"]["b"].to_list() == [2, 3]
    metrics = context.metrics.snapshot()
    assert [metric.node_id for metric in metrics] == ["source", "derived"]
    assert {metric.name for metric in metrics} == {"eager_collect"}


def test_eager_graph_execution_does_not_swallow_memory_budget_failures() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    samples = iter([50, 50, 100])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=90,
        memory_sampler=lambda: next(samples),
    )

    def build_node_fn(node, **_kwargs):
        return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True

    with pytest.raises(ExecutionMemoryLimitExceededError):
        _run_eager(
            graph,
            build_node_fn,
            target_node_id="source",
            swallow_errors=True,
            execution_context=context,
        )


def test_eager_graph_execution_does_not_swallow_cancellation() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    context.cancel()

    def build_node_fn(node, **_kwargs):
        return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True

    with pytest.raises(ExecutionCancelledError):
        _run_eager(
            graph,
            build_node_fn,
            target_node_id="source",
            swallow_errors=True,
            execution_context=context,
        )


@pytest.mark.parametrize(
    "error",
    [
        ContractMismatchError("bad contract", node_id="source"),
        SchemaMismatchError("bad schema", node_id="source"),
    ],
)
def test_eager_graph_execution_does_not_swallow_mismatches(error: Exception) -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def build_node_fn(node, **_kwargs):
        def raise_mismatch():
            raise error

        return node.id, raise_mismatch, True

    with pytest.raises(type(error), match=error.message):
        _run_eager(graph, build_node_fn, target_node_id="source", swallow_errors=True)


def test_eager_graph_execution_swallows_ordinary_node_errors() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def build_node_fn(node, **_kwargs):
        def raise_runtime_error():
            raise RuntimeError("ordinary node failure")

        return node.id, raise_runtime_error, True

    outputs, errors = _run_eager(graph, build_node_fn, target_node_id="source", swallow_errors=True)

    assert errors == {"source": "ordinary node failure"}
    assert outputs == {"source": None}


def test_lazy_graph_execution_checks_cancellation_before_node_work() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(operation="sink", profile=ExecutionProfile.LAZY_SINK)
    context.cancel()

    def build_node_fn(_node, **_kwargs):
        raise AssertionError("cancelled execution should not build node functions")

    with pytest.raises(ExecutionCancelledError):
        execute_lazy_graph(graph, build_node_fn, execution_context=context)


def test_lazy_graph_execution_records_build_and_capture_stages(tmp_path) -> None:
    from haute._node_snapshots import NodeSnapshotStore
    from haute._seed_plans import SeedPlanRequest, open_resolved_seed_plan

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                },
                {
                    "id": "mid",
                    "data": {
                        "label": "mid",
                        "nodeType": NodeType.POLARS.value,
                        "config": {"code": "df = df.with_columns(pl.col('a').rank().alias('r'))"},
                    },
                },
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
                {
                    "id": "both",
                    "data": {
                        "label": "both",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("source", "mid").model_dump(),
                make_edge("mid", "left").model_dump(),
                make_edge("mid", "right").model_dump(),
                make_edge("left", "both").model_dump(),
                make_edge("right", "both").model_dump(),
            ],
        }
    )
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1_000,
    )

    def build_node_fn(node, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True
        if node.id == "mid":
            return node.id, lambda df: df.with_columns((pl.col("a") + 1).alias("b")), False
        if node.id == "both":
            return node.id, lambda left, right: pl.concat([left, right]), False
        return node.id, lambda df: df.select("b"), False

    request = SeedPlanRequest(
        graph=graph,
        target_node_id="both",
        source="live",
        profile=ExecutionProfile.LAZY_SINK,
    )
    with open_resolved_seed_plan(request, store=NodeSnapshotStore(tmp_path)) as plan:
        outputs, *_ = execute_lazy_graph(
            graph,
            build_node_fn,
            target_node_id="both",
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
        )
        assert outputs["both"].collect()["b"].to_list() == [2, 3, 2, 3]
    metrics = context.metrics.snapshot()
    assert [metric.node_id for metric in metrics if metric.name == "lazy_build"] == [
        "source",
        "mid",
        "left",
        "right",
        "both",
    ]
    # The fan-out is materialised as a capture into the shared snapshot store.
    assert any(
        metric.name == "lazy_snapshot_capture" and metric.node_id == "mid" for metric in metrics
    )


def test_execute_sink_forwards_execution_context_to_lazy_executor(tmp_path) -> None:
    from haute.executor import write_data_output

    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_OUTPUT.value,
                        "config": make_file_output_config(output_path),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        from haute._graph_walker import WalkResult

        return WalkResult(frames={"sink": pl.DataFrame({"a": [1]}).lazy()})

    with patch("haute.executor.walk_graph", side_effect=fake_execute_lazy):
        result = write_data_output(graph, "sink", execution_context=context)

    assert result.status == "ok"
    assert captured["execution_context"] is context
    assert any(metric.name == "output_write" for metric in context.metrics.snapshot())
    assert result.execution_metrics is not None
    assert result.execution_metrics.profile == ExecutionProfile.LAZY_SINK.value


@pytest.mark.asyncio
async def test_sink_route_creates_lazy_sink_execution_context(monkeypatch, tmp_path) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest, WriteOutputResponse

    use_project(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 128 * 1024 * 1024,
    )
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_OUTPUT.value,
                        "config": make_file_output_config(output_path),
                    },
                },
            ],
            "edges": [],
        }
    )
    captured = {}

    def fake_execute_sink(*args, **_kwargs):
        captured["budget"] = args[7]
        return WriteOutputResponse(status="ok")

    with patch.object(
        pipeline_route,
        "_output_write_transaction",
        side_effect=fake_execute_sink,
    ):
        response = await pipeline_route.write_output_node(
            WriteOutputRequest(graph=graph, node_id="sink", source="batch")
        )

    assert response.status == "ok"
    assert captured["budget"].profile == ExecutionProfile.LAZY_SINK
    assert captured["budget"].memory_limit_bytes == 512 * 1024 * 1024
