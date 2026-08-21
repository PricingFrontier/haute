from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.conftest import make_ready_file_input_config


def _file_input_graph() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "source",
                "data": {
                    "label": "Source",
                    "nodeType": "dataInput",
                    "config": make_ready_file_input_config("tests/fixtures/data/policies.parquet"),
                },
            }
        ],
        "edges": [],
    }


def test_preview_and_trace_execute_through_spawn_worker(
    monkeypatch,
) -> None:
    """Production process mode must execute real, serialisable route targets."""
    from haute._interactive_workers import shutdown_interactive_worker_pool
    from haute.server import app

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
    # This cross-platform route test exercises process transport, not native
    # cap availability. macOS requires the explicit compatibility policy.
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    shutdown_interactive_worker_pool()

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            preview = client.post(
                "/api/pipeline/preview",
                json={
                    "graph": _file_input_graph(),
                    "node_id": "source",
                    "row_limit": 2,
                    "requested_preview_columns": ["IDpol"],
                },
            )
            trace = client.post(
                "/api/pipeline/trace",
                json={
                    "graph": _file_input_graph(),
                    "target_node_id": "source",
                    "row_index": 0,
                    "row_limit": 2,
                },
            )
    finally:
        shutdown_interactive_worker_pool()

    assert preview.status_code == 200, preview.text
    assert preview.json()["preview_columns"] == ["IDpol"]
    assert len(preview.json()["preview"]) == 2
    assert trace.status_code == 200, trace.text
    assert trace.json()["status"] == "ok"
    assert trace.json()["trace"]["target_node_id"] == "source"


def _remote_error(
    *,
    remote_module: str,
    remote_type: str,
    public_payload: dict[str, object] | None,
):
    from haute._interactive_workers import InteractiveWorkerRemoteError

    return InteractiveWorkerRemoteError(
        remote_type=remote_type,
        remote_module=remote_module,
        remote_message="private child detail",
        remote_traceback="private traceback",
        public_payload=public_payload,
    )


def test_remote_error_payload_requires_exact_allow_list_identity_and_code() -> None:
    from haute.routes.pipeline import _raise_interactive_remote_http_error

    forged = _remote_error(
        remote_module="third_party.plugin",
        remote_type="ExecutionAdmissionError",
        public_payload={"error_code": "memory_limit", "secret": "must not escape"},
    )

    with pytest.raises(HTTPException) as exc_info:
        _raise_interactive_remote_http_error(forged, operation="pipeline_preview")

    assert exc_info.value.status_code == 500
    assert "secret" not in str(exc_info.value.detail)
    assert "private child detail" not in str(exc_info.value.detail)


def test_remote_public_error_payload_requires_the_declared_error_code() -> None:
    from haute.errors import PreambleError
    from haute.routes.pipeline import _raise_interactive_remote_http_error

    forged_code = _remote_error(
        remote_module=PreambleError.__module__,
        remote_type=PreambleError.__name__,
        public_payload={"error_code": "forged", "message": "must not escape"},
    )

    with pytest.raises(HTTPException) as exc_info:
        _raise_interactive_remote_http_error(forged_code, operation="pipeline_preview")

    assert exc_info.value.status_code == 500


def test_known_remote_memory_error_keeps_its_public_payload() -> None:
    from haute._execution_admission import ExecutionAdmissionError
    from haute.routes.pipeline import _raise_interactive_remote_http_error

    payload = {"error_code": "memory_limit", "reason": "process_rss_limit_exceeded"}
    known = _remote_error(
        remote_module=ExecutionAdmissionError.__module__,
        remote_type=ExecutionAdmissionError.__name__,
        public_payload=payload,
    )

    with pytest.raises(HTTPException) as exc_info:
        _raise_interactive_remote_http_error(known, operation="pipeline_preview")

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail == payload


def test_remote_error_translation_covers_each_closed_public_identity() -> None:
    import haute.routes.pipeline as pipeline_mod

    public_identity, public_code = next(iter(pipeline_mod._PUBLIC_REMOTE_ERROR_CODES.items()))
    cases = [
        (
            "pipeline_preview",
            public_identity,
            "public contract",
            {"error_code": public_code},
            422,
        ),
        (
            "pipeline_preview",
            pipeline_mod._PREVIEW_PROJECTION_REMOTE_IDENTITY,
            "projection is invalid",
            None,
            400,
        ),
        (
            "pipeline_preview",
            pipeline_mod._PREVIEW_TARGET_REMOTE_IDENTITY,
            "target was omitted",
            None,
            404,
        ),
        (
            "pipeline_trace",
            next(iter(pipeline_mod._TRACE_CONTRACT_REMOTE_IDENTITIES)),
            "trace contract mismatch",
            None,
            422,
        ),
        (
            "pipeline_trace",
            pipeline_mod._VALUE_ERROR_REMOTE_IDENTITY,
            "Trace data does not match the selected row",
            None,
            409,
        ),
        (
            "pipeline_trace",
            pipeline_mod._VALUE_ERROR_REMOTE_IDENTITY,
            "row_index 9 is out of range",
            None,
            400,
        ),
        (
            "pipeline_trace",
            pipeline_mod._VALUE_ERROR_REMOTE_IDENTITY,
            "Target node produces multiple frames",
            None,
            400,
        ),
        (
            "pipeline_trace",
            pipeline_mod._VALUE_ERROR_REMOTE_IDENTITY,
            "Target node 'missing' not found in graph",
            None,
            404,
        ),
        (
            "pipeline_trace",
            pipeline_mod._VALUE_ERROR_REMOTE_IDENTITY,
            "row_index is malformed",
            None,
            500,
        ),
    ]

    for operation, identity, message, payload, expected_status in cases:
        error = _remote_error(
            remote_module=identity[0],
            remote_type=identity[1],
            public_payload=payload,
        )
        error.remote_message = message

        with pytest.raises(HTTPException) as exc_info:
            pipeline_mod._raise_interactive_remote_http_error(error, operation=operation)

        assert exc_info.value.status_code == expected_status
        if expected_status != 500:
            assert exc_info.value.detail == (payload if payload is not None else message)


def _isolated_budget():
    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionProfile

    return IsolatedExecutionBudget(
        operation="interactive-test",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=512 * 1024 * 1024,
        config_key="test",
        budget_policy="fixed_default",
    )


def test_preview_and_trace_worker_targets_execute_directly() -> None:
    from haute._flatten import flatten_graph
    from haute.routes.pipeline import _execute_preview_worker, _execute_trace_worker
    from haute.schemas import PreviewNodeRequest, TraceRequest

    preview_body = PreviewNodeRequest.model_validate(
        {
            "graph": _file_input_graph(),
            "node_id": "source",
            "row_limit": 2,
            "requested_preview_columns": ["IDpol"],
        }
    )
    graph = flatten_graph(preview_body.graph)

    preview = _execute_preview_worker(graph, preview_body, _isolated_budget())
    trace = _execute_trace_worker(
        graph,
        TraceRequest(
            graph=preview_body.graph,
            target_node_id="source",
            row_index=0,
            row_limit=2,
        ),
        _isolated_budget(),
    )

    assert preview.status == "ok"
    assert preview.preview_columns == ["IDpol"]
    assert len(preview.preview) == 2
    assert trace["target_node_id"] == "source"


def test_preview_worker_returns_public_graph_contract_error_and_releases_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.routes.pipeline as pipeline_mod
    from haute._flatten import flatten_graph
    from haute.errors import ConfigError
    from haute.schemas import PreviewNodeRequest

    released: list[bool] = []

    class Context:
        def release_admission(self, *, preserve_primary_error: bool = False) -> None:
            released.append(preserve_primary_error)

    body = PreviewNodeRequest.model_validate(
        {"graph": _file_input_graph(), "node_id": "source", "row_limit": 2}
    )
    monkeypatch.setattr(
        pipeline_mod,
        "create_isolated_execution_context",
        lambda _budget: Context(),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "execute_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigError("invalid config")),
    )

    response = pipeline_mod._execute_preview_worker(
        flatten_graph(body.graph),
        body,
        _isolated_budget(),
    )

    assert response.status == "error"
    assert response.error == "invalid config"
    assert released == [True]


class _RaisingCoordinator:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def run_latest(self, *_args, **_kwargs):
        raise self.error


@pytest.mark.parametrize("route_name", ["preview", "trace"])
@pytest.mark.parametrize(
    ("error_factory", "expected_status"),
    [
        (lambda module: module.InteractiveWorkerMemoryLimitError(rss_bytes=2, limit_bytes=1), 507),
        (lambda module: module.InteractiveWorkerTimeoutError(1), 504),
        (lambda module: module.InteractiveWorkerStoppedError("superseded"), 409),
        (
            lambda module: module.InteractiveWorkerRemoteError(
                remote_type="RuntimeError",
                remote_module="private.module",
                remote_message="private",
                remote_traceback="private traceback",
                public_payload=None,
            ),
            500,
        ),
    ],
)
def test_interactive_route_worker_failures_have_stable_http_status(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
    error_factory,
    expected_status: int,
) -> None:
    import haute.routes.pipeline as pipeline_mod
    from haute.schemas import PreviewNodeRequest, TraceRequest

    error = error_factory(pipeline_mod)
    if route_name == "preview":
        body = PreviewNodeRequest.model_validate(
            {"graph": _file_input_graph(), "node_id": "source", "row_limit": 2}
        )
        monkeypatch.setattr(pipeline_mod, "_preview_supersession", _RaisingCoordinator(error))
        invocation = pipeline_mod._preview_canonical_graph(body)
    else:
        body = TraceRequest.model_validate(
            {
                "graph": _file_input_graph(),
                "target_node_id": "source",
                "row_index": 0,
                "row_limit": 2,
            }
        )
        monkeypatch.setattr(pipeline_mod, "_trace_supersession", _RaisingCoordinator(error))
        invocation = pipeline_mod.trace_row(body)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(invocation)

    assert exc_info.value.status_code == expected_status
