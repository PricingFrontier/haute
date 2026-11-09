"""How the pipeline, preview, sink and trace routes use execution contexts.

The preview and sink routes create admitted contexts, map admission, memory, timeout and streaming
failures to their HTTP statuses and release admission when a timed-out worker ends; pipeline
loading falls back across candidates; and the trace route and JSON reads map their failures.
"""

import asyncio
import threading
import time
from typing import cast
from unittest.mock import Mock, patch

import polars as pl
import pytest
from fastapi import Request, Response

from haute._execution_context import (
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._pipeline_recovery import pipeline_document_fingerprint
from haute._project_context import current_project
from haute.errors import (
    ContractMismatchError,
    SchemaMismatchError,
    TraceTargetNotFoundError,
)
from haute.graph_utils import NodeType
from tests._project import project_app, use_project
from tests.conftest import (
    make_file_output_config,
    make_graph,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


class _ConnectedClient:
    async def is_disconnected(self) -> bool:
        return False


# A request whose client stays connected, for calling the preview route directly.
_CONNECTED = cast(Request, _ConnectedClient())


@pytest.mark.asyncio
async def test_write_output_route_rejects_unconfigured_data_output(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_OUTPUT.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    with patch.object(
        pipeline_route,
        "_output_write_transaction",
        side_effect=AssertionError("invalid Data Output must not execute"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.write_output_node(
                WriteOutputRequest(graph=graph, node_id="sink", source="batch")
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid Data Output configuration."


@pytest.mark.asyncio
async def test_get_pipeline_falls_back_after_indexed_and_scanned_load_failures(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    indexed = tmp_path / "indexed.py"
    scanned_bad = tmp_path / "scanned_bad.py"
    scanned_match = tmp_path / "scanned_match.py"
    from haute._pipeline_recovery import empty_pipeline_editor_document

    document = empty_pipeline_editor_document().model_copy(
        update={
            "pipeline_name": "rating",
            "source_file": "scanned_match.py",
            "source_revision": "revision-rating",
            "has_authored_content": True,
        }
    )
    loaded_paths: list[str] = []
    expected_root = current_project().root

    def load(path, *, project_root):
        assert project_root == expected_root
        loaded_paths.append(path.name)
        if path in {indexed, scanned_bad}:
            raise RuntimeError(f"{path.name} is temporarily unparseable")
        return document

    monkeypatch.setattr(pipeline_route, "lookup_pipeline_by_name", lambda _name: indexed)
    monkeypatch.setattr(pipeline_route, "discover_pipelines", lambda: [scanned_bad, scanned_match])
    monkeypatch.setattr(pipeline_route, "load_pipeline_editor_document", load)
    response = Response()

    result = await pipeline_route.get_pipeline("rating", response)

    assert result is document
    assert loaded_paths == ["indexed.py", "scanned_bad.py", "scanned_match.py"]
    assert response.headers[pipeline_route.DOCUMENT_FINGERPRINT_HEADER] == (
        pipeline_document_fingerprint(document.model_dump(mode="json", by_alias=True))
    )


@pytest.mark.asyncio
async def test_get_pipeline_reraises_indexed_load_error_after_all_candidates_fail(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    indexed = tmp_path / "indexed.py"
    scanned_bad = tmp_path / "scanned_bad.py"

    use_project(tmp_path)

    def load(path, *, project_root):
        assert project_root == tmp_path
        raise RuntimeError(f"{path.name} failed")

    monkeypatch.setattr(pipeline_route, "lookup_pipeline_by_name", lambda _name: indexed)
    monkeypatch.setattr(pipeline_route, "discover_pipelines", lambda: [indexed, scanned_bad])
    monkeypatch.setattr(pipeline_route, "load_pipeline_editor_document", load)

    with pytest.raises(RuntimeError, match="indexed.py failed"):
        await pipeline_route.get_pipeline("rating", Response())


@pytest.mark.asyncio
async def test_get_first_pipeline_reraises_first_candidate_load_error(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    use_project(tmp_path)

    def load(path, *, project_root):
        assert project_root == tmp_path
        raise RuntimeError(f"{path.name} failed")

    monkeypatch.setattr(pipeline_route, "discover_pipelines", lambda: [first, second])
    monkeypatch.setattr(pipeline_route, "load_pipeline_editor_document", load)

    with pytest.raises(RuntimeError, match="first.py failed"):
        await pipeline_route.get_first_pipeline(Response())


@pytest.mark.asyncio
async def test_get_first_pipeline_keeps_first_authored_empty_document(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    use_project(tmp_path)
    empty_first = tmp_path / "empty_first.py"
    empty_second = tmp_path / "empty_second.py"
    broken = tmp_path / "broken.py"
    from haute._pipeline_recovery import empty_pipeline_editor_document

    first_document = empty_pipeline_editor_document().model_copy(
        update={
            "pipeline_name": "empty_first",
            "source_file": "empty_first.py",
            "source_revision": "revision-first",
            "has_authored_content": True,
        }
    )
    second_document = empty_pipeline_editor_document().model_copy(
        update={
            "pipeline_name": "empty_second",
            "source_file": "empty_second.py",
            "source_revision": "revision-second",
            "has_authored_content": True,
        }
    )

    def load(path, *, project_root):
        assert project_root == tmp_path
        if path == empty_first:
            return first_document
        if path == empty_second:
            return second_document
        raise RuntimeError("broken pipeline")

    monkeypatch.setattr(
        pipeline_route,
        "discover_pipelines",
        lambda: [empty_first, empty_second, broken],
    )
    monkeypatch.setattr(pipeline_route, "load_pipeline_editor_document", load)

    response = Response()

    result = await pipeline_route.get_first_pipeline(response)

    assert result is first_document
    assert result.source_file == "empty_first.py"
    assert response.headers[pipeline_route.DOCUMENT_FINGERPRINT_HEADER] == (
        pipeline_document_fingerprint(first_document.model_dump(mode="json", by_alias=True))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_status", "expected_detail"),
    [
        (
            "Target node 'missing' not found in graph",
            404,
            "Target node 'missing' not found in graph",
        ),
        (
            "unexpected trace failure",
            500,
            "Operation failed. Check the server logs for details.",
        ),
    ],
)
async def test_trace_route_maps_target_not_found_and_unknown_value_errors(
    monkeypatch,
    message: str,
    expected_status: int,
    expected_detail: str,
) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import TraceRequest

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

    def raise_value_error(*_args, **_kwargs):
        # The trace refuses a missing target by type; any other ValueError is a
        # failure of the server.
        if expected_status == 404:
            raise TraceTargetNotFoundError(message)
        raise ValueError(message)

    monkeypatch.setattr(pipeline_route, "execute_trace", raise_value_error)

    request = TraceRequest(seed_plan=[], graph=graph, row_index=0, target_node_id="source")
    response = _app_client().post("/api/pipeline/trace", json=request.model_dump(mode="json"))

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_trace_route_maps_contract_mismatch_to_http_422(monkeypatch) -> None:
    from fastapi import HTTPException

    from haute.errors import ContractMismatchError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import TraceRequest

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

    def raise_contract_mismatch(*_args, **_kwargs):
        raise ContractMismatchError("bad contract", node_id="source")

    monkeypatch.setattr(pipeline_route, "execute_trace", raise_contract_mismatch)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.trace_row(TraceRequest(seed_plan=[], graph=graph, row_index=0))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "bad contract (node_id=source)"


@pytest.mark.asyncio
async def test_read_json_file_maps_unexpected_read_failure_to_internal_error(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    use_project(tmp_path)
    monkeypatch.setattr(
        pipeline_route,
        "read_user_text",
        lambda _path: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    response = _app_client().post("/api/pipeline/read-json", json={"path": "payload.json"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Operation failed. Check the server logs for details."


@pytest.mark.asyncio
async def test_preview_route_creates_admitted_preview_execution_context(
    monkeypatch, pipeline_settings
) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import ColumnInfo, NodeResult, PreviewNodeRequest

    pipeline_settings(preview_memory_gb=384 / 1024)
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 96 * 1024 * 1024,
    )
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
    captured = {}

    def fake_execute_graph(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "source": NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                columns=[ColumnInfo(name="a", dtype="Int64")],
                available_columns=[ColumnInfo(name="a", dtype="Int64")],
                preview=[{"a": 1}],
                preview_columns=["a"],
                preview_row_count=1,
                preview_row_limit=100,
            )
        }

    with patch.object(pipeline_route, "execute_graph", side_effect=fake_execute_graph):
        response = await pipeline_route.preview_node(
            PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
        )

    assert response.status == "ok"
    assert captured["execution_context"].profile == ExecutionProfile.PREVIEW_EAGER
    assert captured["execution_context"].memory_limit_bytes == 384 * 1024 * 1024
    assert response.execution_metrics is not None
    assert response.execution_metrics.admission is not None
    assert response.execution_metrics.admission.profile == "preview_eager"
    assert response.execution_metrics.admission.memory_limit_bytes == 384 * 1024 * 1024


@pytest.mark.asyncio
async def test_preview_route_admits_when_warm_process_rss_exceeds_operation_budget(
    monkeypatch,
    pipeline_settings,
) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import ColumnInfo, NodeResult, PreviewNodeRequest

    gib = 1024 * 1024 * 1024
    pipeline_settings(preview_memory_gb=384 / 1024)
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 2 * gib,
    )
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
    captured = {}

    def fake_execute_graph(*_args, **kwargs):
        captured["execution_context"] = kwargs["execution_context"]
        return {
            "source": NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                columns=[ColumnInfo(name="a", dtype="int")],
                available_columns=[ColumnInfo(name="a", dtype="int")],
                preview=[{"a": 1}],
                preview_columns=["a"],
                preview_row_count=1,
                preview_row_limit=100,
            )
        }

    with patch.object(pipeline_route, "execute_graph", side_effect=fake_execute_graph):
        response = await pipeline_route.preview_node(
            PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
        )

    assert response.status == "ok"
    context = captured["execution_context"]
    assert context.budget.memory_baseline_bytes == 2 * gib
    assert context.rss_limit_bytes == 2 * gib + 384 * 1024 * 1024


@pytest.mark.asyncio
async def test_preview_route_maps_admission_failure_to_http_507(
    monkeypatch, pipeline_settings
) -> None:
    from haute.schemas import PreviewNodeRequest

    pipeline_settings(preview_memory_gb=64 / 1024)
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
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

    request = PreviewNodeRequest(graph=graph, node_id="source")
    response = _app_client().post("/api/pipeline/preview", json=request.model_dump(mode="json"))

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert detail["error_code"] == "memory_limit"
    assert detail["profile"] == "preview_eager"
    assert detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_preview_route_cancels_execution_context_on_timeout(
    monkeypatch, pipeline_settings
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import NodeResult, PreviewNodeRequest

    pipeline_settings(preview_memory_gb=512 / 1024, pipeline_time_limit_minutes=0.05 / 60)
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
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
    started = threading.Event()
    cancel_seen = threading.Event()

    def slow_execute_graph(*_args, **kwargs):
        context = kwargs["execution_context"]
        target = kwargs["target_node_id"]
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not context.cancellation_token.cancelled:
            time.sleep(0.005)
        if context.cancellation_token.cancelled:
            cancel_seen.set()
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"a": 1}],
            )
        }

    with patch.object(pipeline_route, "execute_graph", side_effect=slow_execute_graph):
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.preview_node(
                PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
            )

    assert exc_info.value.status_code == 504
    assert started.wait(2)
    assert cancel_seen.wait(2)


class _LeavingClient:
    """A client that disconnects as soon as it is asked."""

    async def is_disconnected(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_a_disconnected_preview_that_times_out_keeps_admission_until_its_thread_ends(
    monkeypatch,
    pipeline_settings,
) -> None:
    """The client leaves, the thread ignores cancellation past its timeout: its
    admission must still be released only once the thread finishes."""
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import NodeResult, PreviewNodeRequest

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "thread")
    pipeline_settings(pipeline_time_limit_minutes=0.6 / 60)  # past the watcher's first poll
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
    release_calls = 0
    release_lock = threading.Lock()
    release_worker = threading.Event()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    preview_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission_release=release_admission,
    )

    def stubborn_execute_graph(*_args, **kwargs):
        # Never reaches a cancellation checkpoint before its timeout.
        assert release_worker.wait(5), "preview worker was not released"
        return {kwargs["target_node_id"]: NodeResult(status="ok", row_count=0, column_count=0)}

    monkeypatch.setattr(
        pipeline_route, "create_admitted_execution_context", lambda *_a, **_k: preview_context
    )
    monkeypatch.setattr(pipeline_route, "execute_graph", stubborn_execute_graph)

    try:
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.preview_node(
                PreviewNodeRequest(graph=graph, node_id="source"),
                cast(Request, _LeavingClient()),
            )
        assert exc_info.value.status_code == 504
        with release_lock:
            assert release_calls == 0, "admission released while the thread still runs"

        release_worker.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with release_lock:
                if release_calls == 1:
                    break
            await asyncio.sleep(0.005)
    finally:
        release_worker.set()

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_preview_route_releases_admission_after_timed_out_worker_finishes(
    monkeypatch,
    pipeline_settings,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import NodeResult, PreviewNodeRequest

    pipeline_settings(pipeline_time_limit_minutes=0.05 / 60)
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
    release_calls = 0
    release_lock = threading.Lock()
    worker_started = threading.Event()
    release_worker = threading.Event()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    preview_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission_release=release_admission,
    )

    def create_context(*_args, **_kwargs) -> ExecutionContext:
        return preview_context

    def slow_execute_graph(*_args, **kwargs):
        target = kwargs["target_node_id"]
        worker_started.set()
        assert release_worker.wait(2), "preview worker was not released"
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"a": 1}],
            )
        }

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        create_context,
    )
    monkeypatch.setattr(pipeline_route, "execute_graph", slow_execute_graph)

    try:
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.preview_node(
                PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
            )

        assert exc_info.value.status_code == 504
        assert worker_started.wait(2)
        with release_lock:
            assert release_calls == 0

        release_worker.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with release_lock:
                if release_calls == 1:
                    break
            await asyncio.sleep(0.005)
    finally:
        release_worker.set()

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_preview_route_releases_admission_when_timeout_task_already_finished(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.routes._timeouts import BlockingWorkTimeoutError
    from haute.schemas import PreviewNodeRequest

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
    release_calls = 0
    release_lock = threading.Lock()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    preview_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission_release=release_admission,
    )

    async def raise_finished_timeout(*_args, **_kwargs):
        background_task = asyncio.get_running_loop().create_future()
        background_task.set_result(None)
        raise BlockingWorkTimeoutError("pipeline_preview", 0.01, background_task)

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        lambda *_args, **_kwargs: preview_context,
    )
    monkeypatch.setattr(
        pipeline_route,
        "run_blocking_with_response_timeout",
        raise_finished_timeout,
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.preview_node(
            PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
        )

    assert exc_info.value.status_code == 504
    with release_lock:
        assert release_calls == 0

    await asyncio.sleep(0)

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_preview_route_maps_timeout_without_execution_context_to_http_504(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.routes._timeouts import BlockingWorkTimeoutError
    from haute.schemas import PreviewNodeRequest

    class TimeoutBeforeWorker:
        async def run_latest(self, *_args, **_kwargs):
            background_task = asyncio.get_running_loop().create_future()
            raise BlockingWorkTimeoutError("pipeline_preview", 0.01, background_task)

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
    monkeypatch.setattr(pipeline_route, "_preview_supersession", TimeoutBeforeWorker())

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.preview_node(
            PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
        )

    assert exc_info.value.status_code == 504
    assert "Preview execution timed out" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ContractMismatchError, SchemaMismatchError])
async def test_preview_route_returns_error_response_for_mismatch(monkeypatch, error_type) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import PreviewNodeRequest

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

    def raise_mismatch(*_args, **_kwargs):
        raise error_type("bad preview contract", node_id="source")

    monkeypatch.setattr(pipeline_route, "execute_graph", raise_mismatch)

    response = await pipeline_route.preview_node(
        PreviewNodeRequest(graph=graph, node_id="source"), _CONNECTED
    )

    assert response.node_id == "source"
    assert response.status == "error"
    assert response.error == "bad preview contract (node_id=source)"


@pytest.mark.asyncio
async def test_sink_route_maps_admission_failure_to_http_507(monkeypatch, tmp_path) -> None:
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_SINK_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
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

    request = WriteOutputRequest(graph=graph, node_id="sink", source="batch")
    response = _app_client().post(
        "/api/pipeline/write-output", json=request.model_dump(mode="json")
    )

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert detail["error_code"] == "memory_limit"
    assert detail["profile"] == "lazy_sink"
    assert detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_sink_route_maps_execution_memory_budget_failure_to_http_507(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
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
    memory_error = ExecutionMemoryLimitExceededError(
        "pipeline_write_output",
        rss_bytes=150,
        limit_bytes=100,
        baseline_rss_bytes=0,
        rss_limit_bytes=100,
        reason="process_rss_limit_exceeded",
    )

    def raise_memory_budget(*_args, **_kwargs):
        raise memory_error

    monkeypatch.setattr(pipeline_route, "_output_write_transaction", raise_memory_budget)

    request = WriteOutputRequest(graph=graph, node_id="sink", source="batch")
    response = _app_client().post(
        "/api/pipeline/write-output", json=request.model_dump(mode="json")
    )

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert detail["error_code"] == "memory_limit"
    assert detail["operation"] == "pipeline_write_output"
    assert detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_sink_route_maps_bounded_streaming_failure_to_http_422(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.errors import BoundedMemoryUnsupportedError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
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

    def unsupported_sink(*_args, **_kwargs):
        raise pipeline_route._OutputWriteWorkerError(
            "bounded",
            str(BoundedMemoryUnsupportedError("Bounded streaming sink failed", path="sink")),
        )

    with patch.object(
        pipeline_route,
        "_output_write_transaction",
        side_effect=unsupported_sink,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.write_output_node(
                WriteOutputRequest(graph=graph, node_id="sink", source="batch")
            )

    assert exc_info.value.status_code == 422
    assert "Bounded streaming sink failed" in exc_info.value.detail


@pytest.mark.asyncio
async def test_preview_route_maps_execution_memory_budget_failure_to_http_507(
    monkeypatch,
) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import PreviewNodeRequest

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
    memory_error = ExecutionMemoryLimitExceededError(
        "pipeline_preview",
        rss_bytes=150,
        limit_bytes=100,
        baseline_rss_bytes=0,
        rss_limit_bytes=100,
        reason="process_rss_limit_exceeded",
    )

    def raise_memory_budget(*_args, **_kwargs):
        raise memory_error

    monkeypatch.setattr(pipeline_route, "execute_graph", raise_memory_budget)

    request = PreviewNodeRequest(graph=graph, node_id="source")
    response = _app_client().post("/api/pipeline/preview", json=request.model_dump(mode="json"))

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert detail["error_code"] == "memory_limit"
    assert detail["operation"] == "pipeline_preview"
    assert detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_sink_route_maps_timeout_before_execution_context_to_http_504(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_OUTPUT.value,
                        "config": make_file_output_config(tmp_path / "sink.parquet"),
                    },
                },
            ],
            "edges": [],
        }
    )

    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("execution slot timed out")

    monkeypatch.setattr(pipeline_route, "create_admitted_execution_context", raise_timeout)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.write_output_node(
            WriteOutputRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 504
    assert "Sink execution timed out" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sink_route_releases_admission_before_isolated_timeout_returns(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute._execution_admission import IsolatedExecutionBudget
    from haute._worker_failures import WorkerTimeoutError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
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
    release_calls = 0
    release_lock = threading.Lock()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    sink_context = ExecutionContext(
        operation="pipeline_write_output",
        profile=ExecutionProfile.LAZY_SINK,
        admission_release=release_admission,
    )

    async def raise_timeout(*_args, **_kwargs):
        raise WorkerTimeoutError(timeout_seconds=0.01)

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        lambda *_args, **_kwargs: sink_context,
    )
    monkeypatch.setattr(
        pipeline_route,
        "isolated_execution_budget",
        lambda _context: IsolatedExecutionBudget(
            operation="pipeline_write_output",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=1024,
            config_key="test",
            budget_policy="test",
        ),
    )
    monkeypatch.setattr(
        pipeline_route,
        "run_cancellable_worker_transaction",
        raise_timeout,
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.write_output_node(
            WriteOutputRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 504
    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_sink_route_maps_cancelled_isolated_worker_without_deferred_release(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute._execution_admission import IsolatedExecutionBudget
    from haute._worker_failures import WorkerStoppedError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
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
    release_calls = 0
    release_lock = threading.Lock()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    sink_context = ExecutionContext(
        operation="pipeline_write_output",
        profile=ExecutionProfile.LAZY_SINK,
        admission_release=release_admission,
    )

    async def raise_cancelled(*_args, **_kwargs):
        raise WorkerStoppedError(terminal_reason="cancelled")

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        lambda *_args, **_kwargs: sink_context,
    )
    monkeypatch.setattr(
        pipeline_route,
        "isolated_execution_budget",
        lambda _context: IsolatedExecutionBudget(
            operation="pipeline_write_output",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=1024,
            config_key="test",
            budget_policy="test",
        ),
    )
    monkeypatch.setattr(
        pipeline_route,
        "run_cancellable_worker_transaction",
        raise_cancelled,
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.write_output_node(
            WriteOutputRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 409
    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_sink_route_does_not_fall_back_after_isolated_timeout(
    monkeypatch, tmp_path, pipeline_settings
) -> None:
    from fastapi import HTTPException

    from haute._worker_failures import WorkerTimeoutError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import WriteOutputRequest

    use_project(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    pipeline_settings(pipeline_time_limit_minutes=0.05 / 60)
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

    async def timed_out(*_args, **_kwargs):
        raise WorkerTimeoutError(timeout_seconds=0.05)

    fallback = Mock(side_effect=AssertionError("thread fallback must not run"))
    monkeypatch.setattr(pipeline_route, "run_cancellable_worker_transaction", timed_out)
    monkeypatch.setattr(pipeline_route, "run_blocking_with_response_timeout", fallback)

    with patch.object(pipeline_route, "_output_write_transaction") as transaction:
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.write_output_node(
                WriteOutputRequest(graph=graph, node_id="sink", source="batch")
            )

    assert exc_info.value.status_code == 504
    transaction.assert_not_called()
    fallback.assert_not_called()


def test_optimiser_execute_pipeline_forwards_execution_context() -> None:
    import contextlib

    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    context = ExecutionContext(
        operation="optimiser",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        job_id=job_id,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"opt": pl.DataFrame({"a": [1]}).lazy()}, ["opt"], {}, {}

    with (
        contextlib.ExitStack() as resources,
        patch("haute.routes._optimiser_service.execute_lazy_graph", side_effect=fake_execute_lazy),
        patch("haute.executor._resolve_batch_scenario", return_value="batch"),
        patch("haute.executor._compile_preamble", return_value={}),
    ):
        service._execute_pipeline(
            body,
            job_id,
            resources,
            execution_context=context,
        )

    assert captured["execution_context"] is context
    stored_metrics = store.require_job(job_id)["execution_metrics"]
    assert stored_metrics["operation"] == "optimiser"
    assert stored_metrics["job_id"] == job_id


def _app_client():
    """A client for the application, so its exception handlers answer."""
    from fastapi.testclient import TestClient

    app = project_app()

    return TestClient(app, raise_server_exceptions=False)
