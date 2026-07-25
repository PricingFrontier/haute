from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph
from haute.schemas import NodeResult, TraceResponse
from haute.trace import TraceResult, execute_trace, trace_result_to_dict
from haute.trace import _cache as _trace_cache

pytestmark = [pytest.mark.perf, pytest.mark.usefixtures("_widen_sandbox_root")]

_ROW_LIMIT = 3_000
_MAX_PREVIEW_ROWS = 128
_TARGET_NODE = "premium"
_EXPECTED_NODE_IDS = ("source", "features", "freq", "sev", "join", "premium")


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=config),
    )


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"e-{source}-{target}", source=source, target=target)


def _direct_parquet_input(path: Path) -> dict[str, Any]:
    return {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "cacheMode": "direct",
        "path": str(path),
        "arguments": {},
    }


def _write_preview_trace_source(tmp_path: Path, rows: int = _ROW_LIMIT) -> Path:
    path = tmp_path / "preview_trace_perf.parquet"
    pl.DataFrame(
        {
            "policy_id": list(range(rows)),
            "age": [18 + (idx % 70) for idx in range(rows)],
            "base": [100.0 + float((idx * 7) % 900) for idx in range(rows)],
            "exposure": [0.5 + float(idx % 12) / 12.0 for idx in range(rows)],
            "territory": [f"T{idx % 9}" for idx in range(rows)],
            "claim_count": [idx % 5 for idx in range(rows)],
        }
    ).write_parquet(path)
    return path


def _preview_trace_graph(tmp_path: Path) -> PipelineGraph:
    source_path = _write_preview_trace_source(tmp_path)
    return PipelineGraph(
        nodes=[
            _node(
                "source",
                NodeType.DATA_INPUT,
                _direct_parquet_input(source_path),
            ),
            _node(
                "features",
                NodeType.POLARS,
                {
                    "code": """
df = df.with_columns(
    age_factor=(pl.col("age") / 100.0 + 1.0),
    base_exposure=pl.col("base") * pl.col("exposure"),
    territory_key=pl.col("territory"),
)
""",
                },
            ),
            _node(
                "freq",
                NodeType.POLARS,
                {
                    "code": """
df = df.with_columns(
    freq=(pl.col("claim_count") + 1) * pl.col("age_factor"),
).select(["policy_id", "territory_key", "freq"])
""",
                },
            ),
            _node(
                "sev",
                NodeType.POLARS,
                {
                    "code": """
df = df.with_columns(
    severity=pl.col("base_exposure") * ((pl.col("policy_id") % 17) + 1),
).select(["policy_id", "territory_key", "severity"])
""",
                },
            ),
            _node(
                "join",
                NodeType.POLARS,
                {
                    "code": """
df = freq.join(sev, on=["policy_id", "territory_key"], how="inner")
""",
                },
            ),
            _node(
                "premium",
                NodeType.POLARS,
                {
                    "code": """
df = df.with_columns(
    premium=(pl.col("freq") * pl.col("severity")).round(4),
    risk_bucket=pl.when(pl.col("severity") > 1000.0)
        .then(pl.lit("high"))
        .otherwise(pl.lit("standard")),
).sort("policy_id")
""",
                },
            ),
        ],
        edges=[
            _edge("source", "features"),
            _edge("features", "freq"),
            _edge("features", "sev"),
            _edge("freq", "join"),
            _edge("sev", "join"),
            _edge("join", "premium"),
        ],
    )


def _linear_trace_graph(tmp_path: Path) -> tuple[PipelineGraph, str, str]:
    source_path = _write_preview_trace_source(tmp_path)
    nodes = [
        _node(
            "source",
            NodeType.DATA_INPUT,
            _direct_parquet_input(source_path),
        )
    ]
    edges: list[GraphEdge] = []
    parent = "source"
    input_column = "base"
    for index in range(8):
        node_id = f"linear_{index}"
        output_column = f"derived_{index}"
        nodes.append(
            _node(
                node_id,
                NodeType.POLARS,
                {
                    "code": (
                        "df = df.with_columns("
                        f'{output_column}=pl.col("{input_column}") * 1.01 + {index}'
                        ")"
                    )
                },
            )
        )
        edges.append(_edge(parent, node_id))
        parent = node_id
        input_column = output_column
    return PipelineGraph(nodes=nodes, edges=edges), parent, input_column


def _record_perf_evidence(
    request: pytest.FixtureRequest,
    **evidence: object,
) -> None:
    request.node.user_properties.append(("haute_perf_evidence", evidence))


def _serialize_and_validate_trace(result: TraceResult) -> dict[str, Any]:
    payload = trace_result_to_dict(result)
    TraceResponse.model_validate({"status": "ok", "trace": payload})
    return payload


def _single_node_graph_payload() -> dict[str, Any]:
    graph = PipelineGraph(
        nodes=[
            _node(
                "target",
                NodeType.POLARS,
                {},
            ),
        ],
        edges=[],
    )
    return graph.model_dump()


def _count_executor_node_calls(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    import haute.executor as executor_mod

    original_build_node_fn = executor_mod._build_node_fn
    calls: Counter[str] = Counter()

    def counting_build_node_fn(
        node: GraphNode,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[str, Callable[..., Any], bool]:
        func_name, func, is_source = original_build_node_fn(node, *args, **kwargs)

        def counted_func(*func_args: Any, **func_kwargs: Any) -> Any:
            calls[node.id] += 1
            return func(*func_args, **func_kwargs)

        return func_name, counted_func, is_source

    monkeypatch.setattr(executor_mod, "_build_node_fn", counting_build_node_fn)
    return calls


def test_preview_warm_cache_avoids_reexecuting_representative_dag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    graph = _preview_trace_graph(tmp_path)
    node_calls = _count_executor_node_calls(monkeypatch)

    start = time.perf_counter()
    cold = execute_graph(
        graph,
        target_node_id=_TARGET_NODE,
        row_limit=_ROW_LIMIT,
        max_preview_rows=_MAX_PREVIEW_ROWS,
        target_preview_only=True,
        requested_preview_columns=["policy_id", "premium", "risk_bucket"],
    )
    cold_seconds = time.perf_counter() - start

    start = time.perf_counter()
    warm = execute_graph(
        graph,
        target_node_id=_TARGET_NODE,
        row_limit=_ROW_LIMIT,
        max_preview_rows=_MAX_PREVIEW_ROWS,
        target_preview_only=True,
        requested_preview_columns=["policy_id", "premium", "risk_bucket"],
    )
    warm_seconds = time.perf_counter() - start

    assert cold[_TARGET_NODE].status == "ok"
    assert warm[_TARGET_NODE].status == "ok"
    assert warm[_TARGET_NODE].preview == cold[_TARGET_NODE].preview
    assert warm[_TARGET_NODE].row_count == _ROW_LIMIT

    assert node_calls == Counter(dict.fromkeys(_EXPECTED_NODE_IDS, 1))
    preview_fp = _preview_cache.fingerprint
    assert preview_fp is not None
    cache_entry = _preview_cache.try_get(preview_fp)
    assert cache_entry is not None
    assert tuple(cache_entry["order"]) == _EXPECTED_NODE_IDS
    assert set(cache_entry["eager_outputs"]) == {_TARGET_NODE}
    assert _preview_cache.stats()["bytes"] > 0

    _record_perf_evidence(
        request,
        graph_shape="join",
        rows=_ROW_LIMIT,
        cold_execution_ms=round(cold_seconds * 1000, 3),
        preview_cache_hit_ms=round(warm_seconds * 1000, 3),
    )

    assert warm_seconds < 0.5, (
        f"warm preview took {warm_seconds:.3f}s after a {cold_seconds:.3f}s cold run"
    )


@pytest.mark.parametrize("graph_shape", ["linear", "join"])
def test_trace_cold_execution_records_stage_costs(
    graph_shape: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    import haute.trace as trace_mod

    if graph_shape == "linear":
        graph, target_node_id, column = _linear_trace_graph(tmp_path)
    else:
        graph = _preview_trace_graph(tmp_path)
        target_node_id = _TARGET_NODE
        column = "premium"

    correlation_seconds = 0.0
    original_correlate = trace_mod._correlate_rows_posthoc

    def timed_correlate(*args: Any, **kwargs: Any) -> Any:
        nonlocal correlation_seconds
        start = time.perf_counter()
        try:
            return original_correlate(*args, **kwargs)
        finally:
            correlation_seconds += time.perf_counter() - start

    monkeypatch.setattr(trace_mod, "_correlate_rows_posthoc", timed_correlate)

    start = time.perf_counter()
    result = execute_trace(
        graph,
        row_index=37,
        target_node_id=target_node_id,
        column=column,
        row_limit=_ROW_LIMIT,
    )
    total_seconds = time.perf_counter() - start

    start = time.perf_counter()
    payload = _serialize_and_validate_trace(result)
    serialization_seconds = time.perf_counter() - start

    assert result.execution_origin == "fresh_execution"
    assert payload["output_value"] == result.output_value
    assert result.steps
    _record_perf_evidence(
        request,
        graph_shape=graph_shape,
        rows=_ROW_LIMIT,
        cold_trace_ms=round(total_seconds * 1000, 3),
        correlation_ms=round(correlation_seconds * 1000, 3),
        serialization_ms=round(serialization_seconds * 1000, 3),
        steps=len(result.steps),
    )

    assert total_seconds < 2.0, f"{graph_shape} cold trace took {total_seconds:.3f}s"
    assert correlation_seconds < 0.5, f"{graph_shape} correlation took {correlation_seconds:.3f}s"
    assert serialization_seconds < 0.2, (
        f"{graph_shape} serialization took {serialization_seconds:.3f}s"
    )


def test_trace_reuses_preview_cache_then_hits_trace_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    import haute.trace as trace_mod

    graph = _preview_trace_graph(tmp_path)
    preview = execute_graph(
        graph,
        target_node_id=_TARGET_NODE,
        row_limit=_ROW_LIMIT,
        max_preview_rows=_MAX_PREVIEW_ROWS,
    )
    assert preview[_TARGET_NODE].status == "ok"
    preview_lookups: list[str] = []

    class RecordingPreview:
        def try_get(self, fingerprint: str) -> dict[str, Any] | None:
            preview_lookups.append(fingerprint)
            return _preview_cache.try_get(fingerprint)

    preview_reader = RecordingPreview()

    calls = {"materialize": 0, "cold_execute": 0}
    correlation_seconds: list[float] = []
    original_materialize = trace_mod._materialize_eager_outputs
    original_correlate = trace_mod._correlate_rows_posthoc

    def counting_materialize(*args: Any, **kwargs: Any) -> Any:
        calls["materialize"] += 1
        return original_materialize(*args, **kwargs)

    def forbidden_cold_execute(*args: Any, **kwargs: Any) -> Any:
        calls["cold_execute"] += 1
        raise AssertionError("trace should reuse preview outputs, not execute the DAG")

    def timed_correlate(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return original_correlate(*args, **kwargs)
        finally:
            correlation_seconds.append(time.perf_counter() - start)

    monkeypatch.setattr(trace_mod, "_materialize_eager_outputs", counting_materialize)
    monkeypatch.setattr(trace_mod, "_execute_eager_core", forbidden_cold_execute)
    monkeypatch.setattr(trace_mod, "_correlate_rows_posthoc", timed_correlate)

    start = time.perf_counter()
    first = execute_trace(
        graph,
        row_index=7,
        target_node_id=_TARGET_NODE,
        column="premium",
        row_limit=_ROW_LIMIT,
        row_values=preview[_TARGET_NODE].preview[7],
        preview=preview_reader,
    )
    first_seconds = time.perf_counter() - start
    start = time.perf_counter()
    first_payload = _serialize_and_validate_trace(first)
    first_serialization_seconds = time.perf_counter() - start

    start = time.perf_counter()
    second = execute_trace(
        graph,
        row_index=19,
        target_node_id=_TARGET_NODE,
        column="risk_bucket",
        row_limit=_ROW_LIMIT,
        row_values=preview[_TARGET_NODE].preview[19],
        preview=preview_reader,
    )
    second_seconds = time.perf_counter() - start
    start = time.perf_counter()
    second_payload = _serialize_and_validate_trace(second)
    second_serialization_seconds = time.perf_counter() - start

    assert calls == {"materialize": 1, "cold_execute": 0}
    assert len(preview_lookups) == 1
    assert len(correlation_seconds) == 2
    assert first.output_value == preview[_TARGET_NODE].preview[7]["premium"]
    assert second.output_value == preview[_TARGET_NODE].preview[19]["risk_bucket"]
    assert first_payload["output_value"] == first.output_value
    assert second_payload["output_value"] == second.output_value
    assert first.execution_origin == "preview_cache"
    assert second.execution_origin == "trace_cache"
    assert {"source", "features", "freq", "sev", "join", "premium"}.issubset(
        {step.node_id for step in first.steps}
    )
    assert _trace_cache.stats()["entries"] == 1

    _record_perf_evidence(
        request,
        graph_shape="join",
        rows=_ROW_LIMIT,
        preview_reuse_ms=round(first_seconds * 1000, 3),
        preview_reuse_correlation_ms=round(correlation_seconds[0] * 1000, 3),
        preview_reuse_serialization_ms=round(first_serialization_seconds * 1000, 3),
        trace_cache_hit_ms=round(second_seconds * 1000, 3),
        trace_cache_correlation_ms=round(correlation_seconds[1] * 1000, 3),
        trace_cache_serialization_ms=round(second_serialization_seconds * 1000, 3),
    )

    assert first_seconds < 0.8, f"preview-backed first trace took {first_seconds:.3f}s"
    assert second_seconds < 0.3, f"trace-cache hit took {second_seconds:.3f}s"


def test_multi_frame_correlation_records_cost(
    request: pytest.FixtureRequest,
) -> None:
    from haute._trace_correlation import _correlate_rows_posthoc

    policies = pl.DataFrame(
        {
            "policy_id": list(range(_ROW_LIMIT)),
            "premium": [100.0 + index / 10 for index in range(_ROW_LIMIT)],
        }
    )
    drivers = pl.DataFrame(
        {
            "policy_id": list(range(_ROW_LIMIT)),
            "driver_id": [f"D{index}" for index in range(_ROW_LIMIT)],
        }
    )
    target = policies.with_columns(
        traced_premium=pl.col("premium") * 1.1,
    )
    eager_outputs: dict[str, Any] = {
        "api": {"policies": policies, "drivers": drivers},
        "target": target,
    }
    node_map = {
        "api": _node("api", NodeType.API_INPUT, {}),
        "target": _node("target", NodeType.POLARS, {}),
    }
    diagnostics: list[dict[str, Any]] = []
    unresolved: dict[str, tuple[str, int]] = {}

    start = time.perf_counter()
    rows = _correlate_rows_posthoc(
        eager_outputs,
        ["api", "target"],
        {"api": [], "target": ["api"]},
        "target",
        1777,
        node_map=node_map,
        diagnostics=diagnostics,
        unresolved=unresolved,
        source_frames_of={("api", "target"): ["policies"]},
        traced_column="premium",
    )
    correlation_seconds = time.perf_counter() - start

    assert rows["api"] == {
        "policy_id": 1777,
        "premium": policies[1777, "premium"],
    }
    assert diagnostics == []
    assert unresolved == {}
    _record_perf_evidence(
        request,
        graph_shape="multi-frame",
        rows=_ROW_LIMIT,
        frames=2,
        correlation_ms=round(correlation_seconds * 1000, 3),
    )
    assert correlation_seconds < 0.5, f"multi-frame correlation took {correlation_seconds:.3f}s"


async def _wait_for_thread_event(event: threading.Event, label: str) -> None:
    assert await asyncio.to_thread(event.wait, 2), f"timed out waiting for {label}"


async def _wait_for_latest_generation(coordinator: Any, expected: int) -> None:
    deadline = time.perf_counter() + 2
    while time.perf_counter() < deadline:
        states = list((await coordinator.snapshot_for_tests()).values())
        latest = max((state.latest_generation for state in states), default=0)
        if latest >= expected:
            return
        await asyncio.sleep(0)
    pytest.fail(f"supersession generation did not reach {expected}")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["preview", "trace"])
async def test_route_supersession_rejects_obsolete_preview_and_trace_work(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.routes.pipeline as route_mod
    from haute.routes._supersession import SupersessionCoordinator
    from haute.server import app

    request_count = 6
    coordinator = SupersessionCoordinator()
    started = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def enter_worker() -> int:
        nonlocal active, call_count, max_active
        with state_lock:
            call_count += 1
            call_number = call_count
            active += 1
            max_active = max(max_active, active)
        started.set()
        return call_number

    def leave_worker() -> None:
        nonlocal active
        with state_lock:
            active -= 1

    if operation == "preview":
        monkeypatch.setattr(route_mod, "_preview_supersession", coordinator)
        monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(1))

        def slow_preview(*args: Any, **kwargs: Any) -> dict[str, NodeResult]:
            call_number = enter_worker()
            try:
                assert release.wait(2), "preview worker release timed out"
                return {
                    kwargs["target_node_id"]: NodeResult(
                        status="ok",
                        row_count=1,
                        column_count=1,
                        preview=[{"call": call_number}],
                    )
                }
            finally:
                leave_worker()

        monkeypatch.setattr(route_mod, "execute_graph", slow_preview)
        endpoint = "/api/pipeline/preview"
        payload = {
            "graph": _single_node_graph_payload(),
            "node_id": "target",
            "row_limit": 100,
            "source": "live",
        }
    else:
        monkeypatch.setattr(route_mod, "_trace_supersession", coordinator)
        monkeypatch.setattr(route_mod, "_trace_work_slots", asyncio.Semaphore(1))

        def slow_trace(*args: Any, **kwargs: Any) -> TraceResult:
            call_number = enter_worker()
            try:
                assert release.wait(2), "trace worker release timed out"
                return TraceResult(
                    target_node_id="target",
                    row_index=0,
                    column=None,
                    output_value={"call": call_number},
                    steps=[],
                    total_nodes_in_pipeline=1,
                    nodes_in_trace=0,
                )
            finally:
                leave_worker()

        monkeypatch.setattr(route_mod, "execute_trace", slow_trace)
        endpoint = "/api/pipeline/trace"
        payload = {
            "graph": _single_node_graph_payload(),
            "target_node_id": "target",
            "row_index": 0,
            "row_limit": 100,
            "source": "live",
        }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:

        async def post() -> httpx.Response:
            return await client.post(endpoint, json=payload)

        first = asyncio.create_task(post())
        await _wait_for_thread_event(started, f"first {operation} worker")
        rest = [asyncio.create_task(post()) for _ in range(request_count - 1)]
        await _wait_for_latest_generation(coordinator, request_count)
        release.set()
        responses = await asyncio.gather(first, *rest)

    statuses = [response.status_code for response in responses]
    assert statuses.count(200) == 1
    assert statuses.count(409) == request_count - 1
    assert all(
        "superseded" in response.json()["detail"].lower()
        for response in responses
        if response.status_code == 409
    )
    with state_lock:
        assert call_count == 2
        assert max_active == 1
        assert active == 0
    assert await coordinator.snapshot_for_tests() == {}
