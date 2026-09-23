"""The streaming chunk size is one editor setting (EXEC-R01).

Polars keeps the chunk size as process-wide configuration. The editor server
holds one value, applies it when the pipeline settings change it, and every
execution started afterwards runs with it: on a server thread, in a newly
spawned worker, and on the next task of an already warm interactive worker.
Nothing locks the value across an execution, so an estimate never waits for a
running solve.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from pydantic import BaseModel

from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from tests.conftest import make_ready_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


@pytest.fixture(autouse=True)
def _start_from_default_chunk_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # Start from the default; the conftest fixture restores the process value.
    monkeypatch.delenv("POLARS_STREAMING_CHUNK_SIZE", raising=False)


def _report_chunk_size() -> int:
    return current_streaming_chunk_size()


def _report_pid_and_chunk_size() -> tuple[int, int]:
    return os.getpid(), current_streaming_chunk_size()


def test_the_settings_route_reads_and_changes_the_server_value(client) -> None:
    response = client.put("/api/execution-settings", json={"streaming_chunk_size": 123_000})

    assert response.status_code == 200
    assert response.json() == {"streaming_chunk_size": 123_000}
    assert current_streaming_chunk_size() == 123_000
    assert client.get("/api/execution-settings").json() == {"streaming_chunk_size": 123_000}


@pytest.mark.parametrize("value", [0, -1, 10_000_001, True, "many"])
def test_the_settings_route_refuses_an_invalid_value(client, value: object) -> None:
    set_streaming_chunk_size(250_000)

    response = client.put("/api/execution-settings", json={"streaming_chunk_size": value})

    assert response.status_code == 422
    assert current_streaming_chunk_size() == 250_000


def test_a_worker_spawned_after_a_change_runs_with_it() -> None:
    from haute._worker_isolation import run_isolated_worker

    set_streaming_chunk_size(234_000)

    assert run_isolated_worker(_report_chunk_size) == 234_000


def test_a_warm_worker_runs_its_next_task_with_a_changed_value() -> None:
    from haute._interactive_workers import InteractiveWorkerPool

    pool = InteractiveWorkerPool(size=1, polars_threads=2)
    try:
        set_streaming_chunk_size(111_000)
        first_pid, first = pool.run(
            _report_pid_and_chunk_size, affinity_key="chunk-size", timeout_seconds=60.0
        )
        set_streaming_chunk_size(222_000)
        second_pid, second = pool.run(
            _report_pid_and_chunk_size, affinity_key="chunk-size", timeout_seconds=60.0
        )
    finally:
        pool.close()

    assert second_pid == first_pid, "the second task must reuse the warm worker"
    assert (first, second) == (111_000, 222_000)


def test_no_request_model_carries_a_chunk_size() -> None:
    import haute.schemas as schemas

    carriers = sorted(
        name
        for name, value in vars(schemas).items()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and "streaming_chunk_size" in value.model_fields
    )

    assert carriers == ["ExecutionSettings"]


def _optimiser_graph(data_path: Path) -> dict:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType=NodeType.DATA_INPUT,
                    config=make_ready_file_input_config(str(data_path)),
                ),
            ),
            GraphNode(
                id="opt",
                data=NodeData(
                    label="opt",
                    nodeType="optimiser",
                    config={
                        "mode": "online",
                        "objective": "expected_income",
                        "constraints": {"volume": {"min": 0.90}},
                        "quote_id": "quote_id",
                        "scenario_index": "scenario_index",
                        "scenario_value": "scenario_value",
                        "max_iter": 5,
                        "tolerance": 1e-4,
                    },
                ),
            ),
        ],
        edges=[GraphEdge(id="e_src_opt", source="source", target="opt")],
    )
    return graph.model_dump()


@pytest.mark.timeout(120)
def test_an_estimate_completes_while_a_solve_is_running(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._sandbox import set_project_root
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService, SolveContext

    # Unpinned, the solve reserves 70% of available RAM from the in-flight
    # budget and admission refuses the estimate before it could wait on
    # anything. Modest budgets let both run, so the test sees only whether the
    # estimate waits for the solve.
    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_MB", "512")
    set_project_root(tmp_path)
    data_path = tmp_path / "data.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": [0, 1, 0, 1],
            "scenario_value": [0.9, 1.1, 0.9, 1.1],
            "expected_income": [10.0, 11.0, 12.0, 13.0],
            "volume": [1.0, 0.9, 1.0, 0.8],
        }
    ).write_parquet(data_path)
    graph = _optimiser_graph(data_path)

    store = JobStore()
    service = OptimiserSolveService(store=store)
    job_id = store.create_job({"status": "running", "job_type": "solve", "start_time": 0.0})
    solving = threading.Event()
    release = threading.Event()

    def blocking_solve(*_args: object, **_kwargs: object) -> None:
        solving.set()
        release.wait(timeout=60)

    outcome: dict[str, object] = {}

    def estimate() -> None:
        outcome["response"] = client.post(
            "/api/optimiser/estimate", json={"graph": graph, "node_id": "opt"}
        )

    with patch("haute.routes._optimiser_service._solve_online", side_effect=blocking_solve):
        service._launch_background(
            SolveContext(job_id=job_id, node_id="opt", mode="online"),
            config=graph["nodes"][1]["data"]["config"],
            quote_grid=MagicMock(),
            ratebook_factors_handle=None,
        )
        try:
            assert solving.wait(timeout=30), "the solve never started"
            worker = threading.Thread(target=estimate, daemon=True)
            worker.start()
            worker.join(timeout=30)
            assert not worker.is_alive(), "the estimate waited for the running solve"
        finally:
            release.set()

    response = outcome["response"]
    assert response.status_code == 200, response.text
    assert response.json()["quote_count"] == 2
