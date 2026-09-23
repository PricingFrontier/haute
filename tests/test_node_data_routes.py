"""Node-data routes: point, run, join, supersede, refresh, clear, delegate (CACHE-S03)."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import haute._chunked_writes
import haute._execute_lazy
import haute._node_snapshots as node_snapshots_module
from haute._data_points import DataPoint, DataPointResolver
from haute._hashing import content_hash
from haute._node_snapshots import (
    NodeSnapshotColumns,
    NodeSnapshotStore,
)
from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size
from haute.routes import _node_data_service as service_mod
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_TERMINAL = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


@pytest.fixture(autouse=True)
def _pinned_admission_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin modest budgets so these tests do not depend on the host's free RAM.

    Unpinned, a build reserves 70% of *available* RAM (22.5 GiB on this
    development host) from a process-wide in-flight budget that is itself
    derived from available RAM, so a second heavy operation live in the same
    process — or simply less free RAM later in a long parallel run — makes
    admission exceed that budget. The parent then never reaches its worker and
    every terminal status becomes ``memory_limited``: correct behaviour for a
    build memory cannot back, but not what these tests are about. Tests that
    exercise admission and memory limits raise or set their own, which still
    takes precedence over this.
    """
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "1024")
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", "1024")


def _valid_profile() -> Any:
    from haute.schemas import NodeDataProfile

    return NodeDataProfile(row_count=1, column_count=0, data_version="v", generated_at=1.0)


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (object(), "invalid result envelope"),
        (service_mod._ProfileWorkerOutcome(), "omitted its profile"),
        (
            service_mod._ProfileWorkerOutcome(
                failure_kind="contract", detail="bad", profile=object()
            ),
            "mixed success",
        ),
        (service_mod._ProfileWorkerOutcome(failure_kind="contract"), "omitted its detail"),
        (
            service_mod._ProfileWorkerOutcome(failure_kind="public_contract", detail="bad"),
            "invalid contract payload",
        ),
        (
            service_mod._ProfileWorkerOutcome(
                failure_kind="public_contract", detail="bad", payload={}, terminal_reason="bad"
            ),
            "invalid contract payload",
        ),
        (
            service_mod._ProfileWorkerOutcome(failure_kind="memory", detail="bad"),
            "invalid memory payload",
        ),
        (
            service_mod._ProfileWorkerOutcome(failure_kind="contract", detail=""),
            "omitted its detail",
        ),
        (
            service_mod._ProfileWorkerOutcome(
                failure_kind="memory", detail="bad", payload={"error_code": "wrong"}
            ),
            "invalid memory payload",
        ),
    ],
)
def test_validated_profile_rejects_invalid_envelopes(outcome: object, message: str) -> None:
    with pytest.raises(RuntimeError, match=message) as caught:
        service_mod._validated_profile_success(outcome)
    assert type(caught.value) is RuntimeError


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (object(), "invalid result envelope"),
        (service_mod._NodeSnapshotWorkerOutcome(), "omitted its publication outcome"),
        (
            service_mod._NodeSnapshotWorkerOutcome(outcome="published"),
            "outcome and generation disagree",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(outcome="superseded", generation_id="g"),
            "outcome and generation disagree",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(generation_id="g"),
            "omitted its publication outcome",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                outcome="published", generation_id="g", worker_evidence=[]
            ),
            "invalid evidence",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                failure_kind="contract", detail="bad", outcome="published"
            ),
            "mixed success",
        ),
        (service_mod._NodeSnapshotWorkerOutcome(failure_kind="contract"), "omitted its detail"),
        (
            service_mod._NodeSnapshotWorkerOutcome(failure_kind="contract", detail=""),
            "omitted its detail",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(failure_kind="public_contract", detail="bad"),
            "invalid contract payload",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                failure_kind="public_contract", detail="bad", payload={}
            ),
            "invalid contract payload",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                failure_kind="public_contract", detail="bad", payload={"error_code": "x"}
            ),
            "invalid contract payload",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                failure_kind="public_contract",
                detail="bad",
                payload={"error_code": "x", "error_detail": {}},
                terminal_reason="bad",
            ),
            "invalid contract payload",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(failure_kind="memory", detail="bad"),
            "invalid memory payload",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                failure_kind="memory", detail="bad", payload={"error_code": "wrong"}
            ),
            "invalid memory payload",
        ),
        (
            service_mod._NodeSnapshotWorkerOutcome(
                failure_kind="contract", detail="bad", payload={}
            ),
            "unexpected payload",
        ),
    ],
)
def test_validated_snapshot_rejects_invalid_envelopes(outcome: object, message: str) -> None:
    with pytest.raises(RuntimeError, match=message) as caught:
        service_mod._validated_worker_success(outcome)
    assert type(caught.value) is RuntimeError


@pytest.mark.parametrize(
    "outcome",
    [
        service_mod._ProfileWorkerOutcome(failure_kind="contract", detail="bad"),
        service_mod._ProfileWorkerOutcome(
            failure_kind="memory", detail="bad", payload={"error_code": "memory_limit"}
        ),
        service_mod._ProfileWorkerOutcome(
            failure_kind="public_contract",
            detail="bad",
            payload={"error_code": "x", "error_detail": {}},
            terminal_reason="contract_error",
        ),
        service_mod._ProfileWorkerOutcome(
            failure_kind="public_contract",
            detail="bad",
            payload={"error_code": "x", "error_detail": {}},
            terminal_reason="memory_limited",
        ),
    ],
)
def test_validated_profile_preserves_worker_errors(outcome: object) -> None:
    with pytest.raises(service_mod._WorkerReportedError) as caught:
        service_mod._validated_profile_success(outcome)
    assert caught.value.kind == outcome.failure_kind and caught.value.detail == "bad"
    assert caught.value.payload is outcome.payload
    assert caught.value.terminal_reason == outcome.terminal_reason


@pytest.mark.parametrize(
    "outcome",
    [
        service_mod._NodeSnapshotWorkerOutcome(failure_kind="contract", detail="bad"),
        service_mod._NodeSnapshotWorkerOutcome(
            failure_kind="memory", detail="bad", payload={"error_code": "memory_limit"}
        ),
        service_mod._NodeSnapshotWorkerOutcome(
            failure_kind="public_contract",
            detail="bad",
            payload={"error_code": "x", "error_detail": {}},
            terminal_reason="contract_error",
            worker_evidence={"captures": 1},
        ),
    ],
)
def test_validated_snapshot_preserves_worker_errors(outcome: object) -> None:
    with pytest.raises(service_mod._WorkerReportedError) as caught:
        service_mod._validated_worker_success(outcome)
    assert caught.value.kind == outcome.failure_kind and caught.value.detail == "bad"
    assert caught.value.payload is outcome.payload
    assert caught.value.terminal_reason == outcome.terminal_reason
    assert caught.value.worker_evidence is outcome.worker_evidence


def test_validated_success_returns_same_success_envelope() -> None:
    profile = service_mod._ProfileWorkerOutcome(profile=_valid_profile())
    assert service_mod._validated_profile_success(profile) is profile.profile
    for outcome in (
        service_mod._NodeSnapshotWorkerOutcome(outcome="published", generation_id="g"),
        service_mod._NodeSnapshotWorkerOutcome(outcome="superseded"),
    ):
        assert service_mod._validated_worker_success(outcome) is outcome


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "policy_id": list(range(1000)),
            "premium": [float(value) for value in range(1000)],
            "region": ["north", "south"] * 500,
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    return haute_scratch


@pytest.fixture(autouse=True)
def _clean_node_data_jobs():
    """Isolate each test: no jobs before, and no job thread still holding memory after.

    A job releases its memory admission after it reports its terminal status, so a
    test that only polls for that status can leave the reservation held while the
    next test starts a job, which would then be refused admission.
    """
    from haute.routes.node_data import _node_data_service, _store

    _store.clear_all()
    yield
    for thread in list(_node_data_service._threads.values()):
        thread.join(60)
    _store.clear_all()


@pytest.fixture()
def in_process_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child_in_process(function, *args, **_kwargs):
        return function(*args)

    monkeypatch.setattr(service_mod, "run_isolated_worker", run_child_in_process)


def _write(path: Path, payload: bytes) -> None:
    """Write bytes to a path the caller derived from its sandbox."""
    path.write_bytes(payload)


def _graph(
    project: Path,
    *,
    join_code: str = "df = source.with_columns((pl.col('premium') * 2).alias('double'))",
    extra_nodes: list[dict[str, Any]] | None = None,
    extra_edges: list[Any] | None = None,
    source_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": source_config
                        or {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "join",
                    "data": {"label": "join", "nodeType": "polars", "config": {"code": join_code}},
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "banding",
                        "config": {
                            "factors": [
                                {
                                    "banding": "continuous",
                                    "column": "premium",
                                    "outputColumn": "band",
                                    "rules": [],
                                }
                            ]
                        },
                    },
                },
                {
                    "id": "explore",
                    "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                },
                *(extra_nodes or []),
            ],
            "edges": [
                make_edge("source", "join").model_dump(),
                make_edge("join", "banding").model_dump(),
                make_edge("join", "explore").model_dump(),
                *(extra_edges or []),
            ],
        }
    )
    return graph.model_dump()


def _poll(client: TestClient, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/node-data/status/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in _TERMINAL:
            return payload
        time.sleep(0.02)
    raise TimeoutError(f"node-data job {job_id} did not finish")


def _body(graph: dict[str, Any], node_id: str, **extra: Any) -> dict[str, Any]:
    return {"graph": graph, "node_id": node_id, "source": "live", **extra}


def test_point_reports_a_missing_node_output_for_banding(client: TestClient, project: Path) -> None:
    response = client.post("/api/node-data/point", json=_body(_graph(project), "banding"))

    assert response.status_code == 200
    point = response.json()
    assert point["point"] == {"producer_node_id": "join", "port_label": None}
    assert point["slot_key"] == "join||live"
    assert (point["kind"], point["state"]) == ("node_output", "missing")
    assert point["demand"] == ["premium"]
    assert point["generation"] is None


def test_one_job_serves_explore_and_banding_on_the_same_parent(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    real_build = service_mod._build_node_snapshot

    def gated_build(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(service_mod, "_build_node_snapshot", gated_build)
    graph = _graph(project)

    first = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert first["status"] == "started"
    assert entered.wait(30)
    point = client.post("/api/node-data/point", json=_body(graph, "explore")).json()
    assert point["state"] == "building"
    assert point["job"]["job_id"] == first["job_id"]
    joined = client.post("/api/node-data/run", json=_body(graph, "explore")).json()
    assert (joined["status"], joined["job_id"]) == ("joined", first["job_id"])

    release.set()
    completed = _poll(client, first["job_id"])
    assert completed["status"] == "completed"
    assert completed["outcome"] == "published"
    again = client.post("/api/node-data/run", json=_body(graph, "explore")).json()
    assert (again["status"], again["cached"]) == ("completed", True)
    banding = client.post("/api/node-data/point", json=_body(graph, "banding")).json()
    explore = client.post("/api/node-data/point", json=_body(graph, "explore")).json()
    assert banding["state"] == explore["state"] == "current"
    assert explore["retention"] == "pinned"
    assert explore["row_count"] == 1000
    assert explore["generation"]["columns"] == "all"


def test_an_upstream_edit_makes_the_point_stale_and_a_run_publishes_the_new_signature(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project)
    started = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert _poll(client, started["job_id"])["status"] == "completed"
    edited = _graph(project, join_code="df = source.filter(pl.col('premium') > 10)")

    assert client.post("/api/node-data/point", json=_body(edited, "banding")).json()["state"] == (
        "stale"
    )
    rerun = client.post("/api/node-data/run", json=_body(edited, "banding")).json()
    assert rerun["status"] == "started"
    assert _poll(client, rerun["job_id"])["status"] == "completed"
    point = client.post("/api/node-data/point", json=_body(edited, "banding")).json()
    assert point["state"] == "current"
    assert point["row_count"] == 989


def test_a_different_signature_supersedes_the_running_build(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    real_build = service_mod._build_node_snapshot
    calls: list[int] = []

    def gated_first_build(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(30)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(service_mod, "_build_node_snapshot", gated_first_build)
    first = client.post("/api/node-data/run", json=_body(_graph(project), "banding")).json()
    assert entered.wait(30)
    edited = _graph(project, join_code="df = source.head(5)")

    second = client.post("/api/node-data/run", json=_body(edited, "banding")).json()

    assert second["status"] == "started"
    assert _poll(client, first["job_id"])["status"] == "superseded"
    release.set()
    assert _poll(client, second["job_id"])["status"] == "completed"
    assert client.post("/api/node-data/point", json=_body(edited, "banding")).json()["state"] == (
        "current"
    )


def test_refresh_recomputes_a_randomly_sampled_producer(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project, join_code="df = source.collect().sample(fraction=0.5).lazy()")

    def rows() -> list[int]:
        from haute._types import PipelineGraph

        resolver = DataPointResolver(
            PipelineGraph.model_validate(graph), source="live", store=NodeSnapshotStore(project)
        )
        with resolver.lease_frame(DataPoint("join", None), NodeSnapshotColumns.all()) as leased:
            return sorted(leased.scan.collect()["policy_id"].to_list())

    first = client.post("/api/node-data/run", json=_body(graph, "explore")).json()
    assert _poll(client, first["job_id"])["status"] == "completed"
    first_point = client.post("/api/node-data/point", json=_body(graph, "explore")).json()
    first_rows = rows()

    cached = client.post("/api/node-data/run", json=_body(graph, "explore")).json()
    assert (cached["status"], cached["cached"]) == ("completed", True)
    refreshed = client.post("/api/node-data/run", json=_body(graph, "explore", refresh=True)).json()
    assert refreshed["status"] == "started"
    assert _poll(client, refreshed["job_id"])["outcome"] == "published"
    second_point = client.post("/api/node-data/point", json=_body(graph, "explore")).json()

    assert second_point["generation"]["generation_id"] != first_point["generation"]["generation_id"]
    assert rows() != first_rows


def test_a_partial_point_becomes_current_and_pinned_after_a_run(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph_payload = _graph(project)
    from haute.schemas import NodeDataRequest

    graph = NodeDataRequest.model_validate(_body(graph_payload, "banding")).graph
    store = NodeSnapshotStore(project)
    resolver = DataPointResolver(graph, source="live", store=store)
    identity = resolver.node_output_slot("join").identity(resolver.node_output_signature("join"))
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"premium": [1.0]}).write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.of(["premium"]),
        dependencies={},
        explicit=False,
        profile="training_prep",
    ):
        pass

    banding = client.post("/api/node-data/point", json=_body(graph_payload, "banding")).json()
    explore = client.post("/api/node-data/point", json=_body(graph_payload, "explore")).json()
    assert (banding["state"], explore["state"]) == ("current", "partial")
    assert explore["retention"] == "automatic"

    run = client.post("/api/node-data/run", json=_body(graph_payload, "explore")).json()
    assert run["status"] == "started"
    assert _poll(client, run["job_id"])["status"] == "completed"
    explore = client.post("/api/node-data/point", json=_body(graph_payload, "explore")).json()
    assert (explore["state"], explore["retention"]) == ("current", "pinned")


def test_clear_removes_every_signature_of_the_slot(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project)
    edited = _graph(project, join_code="df = source.head(3)")
    for candidate in (graph, edited):
        run = client.post("/api/node-data/run", json=_body(candidate, "banding")).json()
        assert _poll(client, run["job_id"])["status"] == "completed"

    cleared = client.post("/api/node-data/clear", json=_body(graph, "banding"))

    assert cleared.status_code == 200
    assert cleared.json()["status"] == "cleared"
    assert cleared.json()["point"]["state"] == "missing"
    assert client.post("/api/node-data/point", json=_body(edited, "banding")).json()["state"] == (
        "missing"
    )
    assert list(project.glob(".haute_cache/inputs/*/generations/*")) == []


def test_worker_contract_and_memory_failures_use_the_job_failure_envelope(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionProfile
    from haute.errors import ContractMismatchError

    def contract_failure(*_args, **_kwargs):
        raise ContractMismatchError("declared column premium is missing")

    monkeypatch.setattr(service_mod, "_build_node_snapshot", contract_failure)
    graph = _graph(project)
    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    failed = _poll(client, run["job_id"])
    assert (failed["status"], failed["terminal_reason"]) == ("contract_error", "contract_error")

    def admission_failure(**_kwargs):
        raise ExecutionAdmissionError(
            "node_snapshot",
            profile=ExecutionProfile.NODE_SNAPSHOT,
            memory_limit_bytes=1,
            rss_at_admission_bytes=10,
            reason="memory_headroom",
        )

    monkeypatch.setattr(service_mod, "create_admitted_execution_context", admission_failure)
    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert _poll(client, run["job_id"])["status"] == "memory_limited"


def test_a_remote_worker_failure_reports_only_the_fixed_internal_detail(
    client: TestClient,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._worker_isolation import IsolatedWorkerRemoteError
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
    from haute.routes.node_data import _store

    secret = "database password appeared in the child exception"

    def fail_worker(*_args: Any, **_kwargs: Any) -> Any:
        raise IsolatedWorkerRemoteError(
            remote_type="RuntimeError",
            remote_message=secret,
            remote_traceback=f"traceback containing {secret}",
        )

    monkeypatch.setattr(service_mod, "run_isolated_worker", fail_worker)
    graph = _graph(project)

    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    final = _poll(client, run["job_id"])
    stored = _store.require_job(run["job_id"])

    assert (final["status"], final["terminal_reason"]) == ("error", "error")
    assert final["message"] == _INTERNAL_ERROR_DETAIL
    assert stored["error"] == _INTERNAL_ERROR_DETAIL
    assert secret not in str(final)
    assert secret not in str(stored)


def test_an_unexpected_parent_failure_reports_only_the_fixed_internal_detail(
    client: TestClient,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
    from haute.routes.node_data import _store

    secret = "C:/secret/path/to/pipeline.py line 42"

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(secret)

    monkeypatch.setattr(service_mod, "run_isolated_worker", fail)
    graph = _graph(project)

    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    final = _poll(client, run["job_id"])
    stored = _store.require_job(run["job_id"])

    assert (final["status"], final["terminal_reason"]) == ("error", "error")
    assert final["message"] == _INTERNAL_ERROR_DETAIL
    assert stored["error"] == _INTERNAL_ERROR_DETAIL
    assert secret not in str(final)
    assert secret not in str(stored)


def _sleeping_graph(project: Path) -> dict[str, Any]:
    graph = _graph(project, join_code="time.sleep(120)\ndf = source")
    graph["preamble"] = "import time\nimport polars as pl"
    return graph


def _wait_running(client: TestClient, job_id: str) -> None:
    status = client.get(f"/api/node-data/status/{job_id}").json()
    assert status["status"] == "running"


def test_cancelling_a_running_build_terminates_its_worker_without_publishing(
    client: TestClient, project: Path
) -> None:
    graph = _sleeping_graph(project)
    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    _wait_running(client, run["job_id"])
    time.sleep(1.0)

    response = client.post(f"/api/node-data/cancel/{run['job_id']}")
    assert response.status_code == 200
    started = time.monotonic()
    final = _poll(client, run["job_id"], timeout=60)

    assert final["status"] == "cancelled"
    assert time.monotonic() - started < 60
    assert list(project.glob(".haute_cache/inputs/*/generations/*")) == []
    assert list(project.glob(".haute_cache/inputs/*/.staging-*")) == []


def test_clear_waits_for_a_running_build_to_stop_before_clearing(
    client: TestClient, project: Path
) -> None:
    graph = _sleeping_graph(project)
    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    _wait_running(client, run["job_id"])
    time.sleep(1.0)

    cleared = client.post("/api/node-data/clear", json=_body(graph, "banding"))

    assert cleared.status_code == 200
    assert cleared.json()["point"]["state"] == "missing"
    assert client.get(f"/api/node-data/status/{run['job_id']}").json()["status"] == "cancelled"
    assert list(project.glob(".haute_cache/inputs/*/generations/*")) == []


def test_clear_does_not_let_a_build_paused_before_publication_repopulate_the_slot(
    client: TestClient,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._worker_isolation import IsolatedWorkerStoppedError

    entered = threading.Event()
    stop_requested = threading.Event()
    clearing = threading.Event()
    children: list[threading.Thread] = []
    real_build = service_mod._build_node_snapshot
    real_clear_slot = NodeSnapshotStore.clear_slot

    def pause_before_publication(request, execution_context):
        entered.set()
        while not clearing.is_set():
            if stop_requested.wait(0.01):
                # A kill lands a little after the stop request, as a real
                # process termination does; until then the build can proceed.
                if clearing.wait(0.5):
                    break
                raise RuntimeError("worker killed")
        # Reaching here means the slot was cleared while this build still ran.
        return real_build(request, execution_context)

    def observed_clear_slot(self, slot):
        clearing.set()
        return real_clear_slot(self, slot)

    def stoppable_worker(function, request, budget, *, config):
        result: dict[str, Any] = {}

        def run_child() -> None:
            try:
                result["value"] = function(request, budget)
            except BaseException as exc:  # noqa: BLE001 - surfaced to the parent below.
                result["error"] = exc

        child = threading.Thread(target=run_child, daemon=True)
        children.append(child)
        child.start()
        while child.is_alive():
            reason = config.stop_reason() if config.stop_reason is not None else None
            if reason is not None:
                stop_requested.set()
                child.join(30)
                raise IsolatedWorkerStoppedError(terminal_reason=reason)
            time.sleep(0.01)
        if "error" in result:
            raise result["error"]
        return result["value"]

    monkeypatch.setattr(service_mod, "_build_node_snapshot", pause_before_publication)
    monkeypatch.setattr(NodeSnapshotStore, "clear_slot", observed_clear_slot)
    monkeypatch.setattr(service_mod, "run_isolated_worker", stoppable_worker)
    graph = _graph(project)
    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert entered.wait(30)

    cleared = client.post("/api/node-data/clear", json=_body(graph, "banding")).json()
    for child in children:
        child.join(30)

    assert cleared["point"]["state"] == "missing"
    assert _poll(client, run["job_id"])["status"] == "cancelled"
    assert client.post("/api/node-data/point", json=_body(graph, "banding")).json()["state"] == (
        "missing"
    )
    assert list(project.glob(".haute_cache/inputs/*/generations/*")) == []


def test_simultaneous_identical_runs_share_one_job(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    real_build = service_mod._build_node_snapshot

    def gated_build(*args, **kwargs):
        assert release.wait(30)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(service_mod, "_build_node_snapshot", gated_build)
    graph = _graph(project)
    barrier = threading.Barrier(4)
    responses: list[dict[str, Any]] = []

    def post_run() -> None:
        barrier.wait()
        responses.append(client.post("/api/node-data/run", json=_body(graph, "explore")).json())

    threads = [threading.Thread(target=post_run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    release.set()

    assert sorted(response["status"] for response in responses) == [
        "joined",
        "joined",
        "joined",
        "started",
    ]
    assert len({response["job_id"] for response in responses}) == 1
    assert _poll(client, responses[0]["job_id"])["status"] == "completed"


def test_a_killed_workers_staging_is_discarded_by_the_parent(
    client: TestClient,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._worker_isolation import IsolatedWorkerStoppedError

    def killed_after_staging(function, request, budget, *, config):
        store = NodeSnapshotStore(request.project_root)
        resolver = DataPointResolver(request.graph, source=request.source, store=store)
        identity = resolver.node_output_slot(request.node_id).identity(
            resolver.node_output_signature(request.node_id)
        )
        artifact = store.stage_node_output(identity, staging_token=request.staging_token)
        _write(artifact.part_path(0), b"partial parquet")
        raise IsolatedWorkerStoppedError(terminal_reason="timed_out")

    monkeypatch.setattr(service_mod, "run_isolated_worker", killed_after_staging)

    from haute.routes.node_data import _node_data_service

    staging_at_terminal: list[list[Path]] = []
    original_fail = _node_data_service._fail_job

    def record_then_fail(*args: Any, **kwargs: Any) -> Any:
        # The staging must already be gone when the terminal status is
        # recorded, so a client that reads the outcome never sees it: this
        # pins the ordering instead of racing the job thread's cleanup.
        staging_at_terminal.append(list(project.glob(".haute_cache/inputs/*/.staging-*")))
        return original_fail(*args, **kwargs)

    monkeypatch.setattr(_node_data_service, "_fail_job", record_then_fail)
    run = client.post("/api/node-data/run", json=_body(_graph(project), "banding")).json()

    assert _poll(client, run["job_id"])["status"] == "timed_out"
    assert staging_at_terminal == [[]]
    assert list(project.glob(".haute_cache/inputs/*/.staging-*")) == []


def test_a_build_that_prepares_its_input_snapshot_completes_current(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    csv_path = project / "quotes.csv"
    pl.DataFrame({"policy_id": [1, 2], "premium": [1.0, 2.0]}).write_csv(csv_path)
    graph = _graph(
        project,
        source_config={
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": str(csv_path),
            "arguments": {},
        },
    )

    first = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert _poll(client, first["job_id"], timeout=120)["status"] == "completed"
    point = client.post("/api/node-data/point", json=_body(graph, "banding")).json()
    assert (point["state"], point["row_count"]) == ("current", 2)

    time.sleep(0.01)
    pl.DataFrame({"policy_id": [1, 2, 3], "premium": [1.0, 2.0, 3.0]}).write_csv(csv_path)
    assert client.post("/api/node-data/point", json=_body(graph, "banding")).json()["state"] == (
        "stale"
    )
    second = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert _poll(client, second["job_id"], timeout=120)["status"] == "completed"
    point = client.post("/api/node-data/point", json=_body(graph, "banding")).json()
    assert (point["state"], point["row_count"]) == ("current", 3)


def test_source_kinds_delegate_or_read_directly(client: TestClient, project: Path) -> None:
    graph = _graph(project)
    direct_nodes = [
        {
            "id": "band_source",
            "data": {
                "label": "band_source",
                "nodeType": "banding",
                "config": {"factors": []},
            },
        }
    ]
    direct = _graph(
        project,
        extra_nodes=direct_nodes,
        extra_edges=[make_edge("source", "band_source").model_dump()],
    )
    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0]}).write_csv(csv_path)
    snapshot = _graph(
        project,
        extra_nodes=direct_nodes,
        extra_edges=[make_edge("source", "band_source").model_dump()],
        source_config={"inputType": "file", "format": "csv", "mode": "scan", "path": str(csv_path)},
    )

    reads_directly = client.post("/api/node-data/run", json=_body(direct, "band_source")).json()
    delegated = client.post("/api/node-data/run", json=_body(snapshot, "band_source")).json()

    assert (reads_directly["status"], reads_directly["cached"]) == ("completed", True)
    assert reads_directly["point"]["reads_directly"] is True
    assert reads_directly["point"]["kind"] == "data_input"
    assert delegated["status"] == "delegated"
    assert delegated["point"]["build_endpoint"] == "/api/input-cache/build"
    assert delegated["point"]["state"] == "missing"
    del graph


def test_a_source_replaced_after_its_rows_were_read_is_never_published(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.execution as execution_module

    graph = _graph(project, join_code="df = source.collect().lazy()")
    real_execute = execution_module.execute_lazy_graph

    def execute_then_replace_source(*args, **kwargs):
        outputs = real_execute(*args, **kwargs)
        # The join already collected the old rows during execution.
        pl.DataFrame(
            {"policy_id": [101, 102, 103], "premium": [1.0, 2.0, 3.0], "region": ["a", "b", "c"]}
        ).write_parquet(project / "quotes.parquet")
        return outputs

    monkeypatch.setattr(execution_module, "execute_lazy_graph", execute_then_replace_source)
    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()

    final = _poll(client, run["job_id"])
    assert final["status"] == "contract_error"
    assert "inputs changed" in final["message"]
    point = client.post("/api/node-data/point", json=_body(graph, "banding")).json()
    assert point["state"] == "missing"
    assert list(project.glob(".haute_cache/inputs/*/generations/*")) == []


def test_cancelling_during_input_preparation_ends_the_job_cancelled(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._input_preparation as preparation_module
    from haute.errors import InputPreparationError

    entered = threading.Event()
    real_prepare = preparation_module.prepare_input_snapshots

    def cancellable_preparation(*args, execution_context, **kwargs):
        entered.set()
        deadline = time.monotonic() + 30
        while not execution_context.cancellation_token.cancelled:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        raise InputPreparationError(
            "Input preparation was cancelled.",
            node_id="source",
            identity_digest="0" * 64,
            build_class="bounded",
            reason_code="cancelled",
            remediation="Run the build again.",
        )

    monkeypatch.setattr(preparation_module, "prepare_input_snapshots", cancellable_preparation)
    run = client.post("/api/node-data/run", json=_body(_graph(project), "banding")).json()
    assert entered.wait(30)

    client.post(f"/api/node-data/cancel/{run['job_id']}")
    final = _poll(client, run["job_id"])

    assert (final["status"], final["terminal_reason"]) == ("cancelled", "cancelled")
    del real_prepare


def test_a_build_after_input_preparation_is_found_by_point_and_joined(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = project / "quotes.csv"
    pl.DataFrame({"policy_id": [1, 2], "premium": [1.0, 2.0]}).write_csv(csv_path)
    graph = _graph(
        project,
        source_config={
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": str(csv_path),
            "arguments": {},
        },
    )
    import haute._chunked_writes as chunked_writes
    import haute._polars_utils as polars_utils

    entered = threading.Event()
    release = threading.Event()

    def _paused(real: Any) -> Any:
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert release.wait(60)
            return real(*args, **kwargs)

        return _wrapper

    real_polars_bounded_sink = polars_utils.bounded_sink
    real_chunked_bounded_hashed_sink = chunked_writes.bounded_hashed_sink

    monkeypatch.setattr(polars_utils, "bounded_sink", _paused(real_polars_bounded_sink))
    monkeypatch.setattr(
        chunked_writes, "bounded_hashed_sink", _paused(real_chunked_bounded_hashed_sink)
    )

    started = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    assert entered.wait(120)
    point = client.post("/api/node-data/point", json=_body(graph, "banding")).json()
    joined = client.post("/api/node-data/run", json=_body(graph, "banding")).json()
    release.set()

    assert point["state"] == "building"
    assert point["job"]["job_id"] == started["job_id"]
    assert (joined["status"], joined["job_id"]) == ("joined", started["job_id"])
    assert _poll(client, started["job_id"], timeout=120)["status"] == "completed"
    assert client.post("/api/node-data/point", json=_body(graph, "banding")).json()["state"] == (
        "current"
    )


def test_run_rejects_an_invalid_api_input_port_with_400(client: TestClient, project: Path) -> None:
    import json as json_module

    records = project / "records.json"
    records.write_text(json_module.dumps([{"policy_id": 1}]), encoding="utf-8")
    table = {
        "path": "$[:]",
        "label": "policies",
        "emit": True,
        "columns": [
            {"name": "policy_id", "path": "$[:].policy_id", "type": "int", "selected": True}
        ],
    }
    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "api",
                        "nodeType": "apiInput",
                        "config": {"path": str(records), "tables": [table]},
                    },
                },
                {
                    "id": "band",
                    "data": {"label": "band", "nodeType": "banding", "config": {"factors": []}},
                },
            ],
            "edges": [
                {"id": "e", "source": "api", "target": "band", "sourceHandle": "removed_table"}
            ],
        }
    ).model_dump()

    for route in ("/api/node-data/point", "/api/node-data/run"):
        response = client.post(route, json=_body(graph, "band"))
        assert response.status_code == 400, route
        assert response.json()["detail"] == ("API Input 'api' has no table 'removed_table'.")


def test_invalid_consumer_wiring_is_a_400(client: TestClient, project: Path) -> None:
    graph = _graph(project, extra_edges=[make_edge("source", "banding").model_dump()])

    response = client.post("/api/node-data/point", json=_body(graph, "banding"))

    assert response.status_code == 400
    # The message names the node and what is wrong with its wiring, and is a
    # plain string like every other route's detail.
    assert response.json()["detail"] == (
        "Node 'banding' must have exactly one incoming connection to read its data (found 2)."
    )


def test_a_real_isolated_worker_builds_and_publishes(client: TestClient, project: Path) -> None:
    graph = _graph(project)

    run = client.post("/api/node-data/run", json=_body(graph, "banding")).json()

    assert _poll(client, run["job_id"], timeout=120)["status"] == "completed"
    point = client.post("/api/node-data/point", json=_body(graph, "banding")).json()
    assert point["state"] == "current"
    assert point["row_count"] == 1000


# ---------------------------------------------------------------------------
# Explicit builds seed and capture (CACHE-S07)
# ---------------------------------------------------------------------------


def _chain_graph(project: Path) -> dict[str, Any]:
    """``source → A → B → C``, each read by a blank Explore that caches it."""

    def polars(node_id: str, code: str) -> dict[str, Any]:
        return {
            "id": node_id,
            "data": {"label": node_id, "nodeType": "polars", "config": {"code": code}},
        }

    def explore(node_id: str) -> dict[str, Any]:
        return {"id": node_id, "data": {"label": node_id, "nodeType": "explore", "config": {}}}

    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                polars("A", "df = source.with_columns((pl.col('premium') * 2).alias('double'))"),
                polars("B", "df = A.filter(pl.col('premium') >= 0)"),
                polars("C", "df = B.with_columns(pl.lit(1).alias('one'))"),
                explore("explore_a"),
                explore("explore_b"),
                explore("explore_c"),
            ],
            "edges": [
                make_edge("source", "A").model_dump(),
                make_edge("A", "B").model_dump(),
                make_edge("B", "C").model_dump(),
                make_edge("A", "explore_a").model_dump(),
                make_edge("B", "explore_b").model_dump(),
                make_edge("C", "explore_c").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def _cache(
    client: TestClient, graph: dict[str, Any], consumer: str, **extra: Any
) -> dict[str, Any]:
    run = client.post("/api/node-data/run", json=_body(graph, consumer, **extra)).json()
    job = _poll(client, run["job_id"])
    assert job["status"] == "completed", str(job.get("error_detail") or job.get("error"))
    return job


def _resolver(project: Path, graph: dict[str, Any]) -> DataPointResolver:
    from haute._types import PipelineGraph

    return DataPointResolver(
        PipelineGraph.model_validate(graph), source="live", store=NodeSnapshotStore(project)
    )


def _state(project: Path, graph: dict[str, Any], node_id: str) -> str:
    resolution = _resolver(project, graph).resolve(
        DataPoint(node_id, None), NodeSnapshotColumns.all()
    )
    return resolution.state


def _digest(project: Path, graph: dict[str, Any], node_id: str) -> str:
    resolver = _resolver(project, graph)
    return (
        resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id)).digest
    )


def _dependencies(project: Path, graph: dict[str, Any], node_id: str) -> dict[str, str]:
    resolver = _resolver(project, graph)
    identity = resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))
    latest = resolver.store.latest_generation(identity)
    assert latest is not None
    return dict(latest.dependencies)


def _clear(client: TestClient, graph: dict[str, Any], *consumers: str) -> None:
    for consumer in consumers:
        assert client.post("/api/node-data/clear", json=_body(graph, consumer)).status_code == 200


@pytest.mark.usefixtures("in_process_worker")
def test_build_seeds_strictly_upstream_and_records_it(client: TestClient, project: Path) -> None:
    graph = _chain_graph(project)

    # Cache A, then B: B is built from A's snapshot and records it.
    a1 = _cache(client, graph, "explore_a")["generation_id"]
    built_b = _cache(client, graph, "explore_b")
    assert [seed["node_id"] for seed in built_b["execution_metrics"]["shared_snapshot_seeds"]] == [
        "A"
    ]
    assert _dependencies(project, graph, "B") == {_digest(project, graph, "A"): a1}
    assert (_state(project, graph, "A"), _state(project, graph, "B")) == ("current", "current")
    # Refreshing A to a new generation stales B.
    _cache(client, graph, "explore_a", refresh=True)
    assert _state(project, graph, "B") == "stale"

    # Cache A, then B, then clear A: B stays current.
    _clear(client, graph, "explore_a", "explore_b")
    _cache(client, graph, "explore_a")
    _cache(client, graph, "explore_b")
    _clear(client, graph, "explore_a")
    assert _state(project, graph, "B") == "current"

    # Cache B, then A: B was built from the source and stays current.
    _clear(client, graph, "explore_a", "explore_b")
    _cache(client, graph, "explore_b")
    _cache(client, graph, "explore_a")
    assert _dependencies(project, graph, "B") == {}
    assert _state(project, graph, "B") == "current"


@pytest.mark.usefixtures("in_process_worker")
def test_build_chain_refresh_stales_descendants(client: TestClient, project: Path) -> None:
    graph = _chain_graph(project)
    for consumer in ("explore_a", "explore_b", "explore_c"):
        _cache(client, graph, consumer)
    assert _dependencies(project, graph, "C").keys() == {
        _digest(project, graph, "A"),
        _digest(project, graph, "B"),
    }

    _cache(client, graph, "explore_a", refresh=True)

    assert _state(project, graph, "B") == "stale"
    assert _state(project, graph, "C") == "stale"


@pytest.mark.usefixtures("in_process_worker")
def test_refresh_build_seeds_nothing_and_captures(client: TestClient, project: Path) -> None:
    # ``A`` fans out and ``B`` feeds a join, so a build of ``J`` captures both.
    graph = _chain_graph(project)
    for node in graph["nodes"]:
        if node["id"] == "A":
            node["data"]["config"]["code"] += ".sort('policy_id')"
    graph["nodes"].append(
        {
            "id": "J",
            "data": {
                "label": "J",
                "nodeType": "polars",
                "config": {"code": "df = A.join(B, on='policy_id', how='left', validate='1:1')"},
            },
        }
    )
    graph["nodes"].append(
        {"id": "explore_j", "data": {"label": "explore_j", "nodeType": "explore", "config": {}}}
    )
    graph["edges"] += [
        make_edge("A", "J").model_dump(),
        make_edge("B", "J").model_dump(),
        make_edge("J", "explore_j").model_dump(),
    ]

    first = _cache(client, graph, "explore_j")["execution_metrics"]
    assert {
        capture["node_id"]: capture["outcome"] for capture in first["shared_snapshot_captures"]
    } == {
        "A": "published",
        "B": "published",
    }
    refreshed = _cache(client, graph, "explore_j", refresh=True)["execution_metrics"]
    assert refreshed["shared_snapshot_seeds"] == []
    assert {capture["node_id"] for capture in refreshed["shared_snapshot_captures"]} == {"A", "B"}


@pytest.mark.parametrize(
    "stopped",
    ["cancelled", "timed_out", "memory_limited"],
)
def test_terminated_build_worker_leaves_no_capture_staging(
    client: TestClient,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    stopped: str,
) -> None:
    from haute._worker_isolation import (
        IsolatedWorkerMemoryLimitExceededError,
        IsolatedWorkerStoppedError,
        IsolatedWorkerTimeoutError,
    )

    graph = _chain_graph(project)
    staged: list[Path] = []

    def killed_after_capture_staging(function, request, budget, *, config):
        # A capture of an intermediate node is staged under the plan's token
        # before the worker dies.
        store = NodeSnapshotStore(request.project_root)
        resolver = DataPointResolver(request.graph, source=request.source, store=store)
        identity = resolver.node_output_slot("A").identity(resolver.node_output_signature("A"))
        artifact = store.stage_node_output(identity, staging_token=request.seed_plan.staging_token)
        _write(artifact.part_path(0), b"partial parquet")
        staged.append(artifact.directory)
        if stopped == "timed_out":
            raise IsolatedWorkerTimeoutError(timeout_seconds=1.0)
        if stopped == "memory_limited":
            raise IsolatedWorkerMemoryLimitExceededError(rss_bytes=2048, rss_limit_bytes=1024)
        raise IsolatedWorkerStoppedError(terminal_reason="cancelled")

    monkeypatch.setattr(service_mod, "run_isolated_worker", killed_after_capture_staging)
    run = client.post("/api/node-data/run", json=_body(graph, "explore_b")).json()

    assert _poll(client, run["job_id"])["status"] == stopped
    assert len(staged) == 1
    assert not staged[0].exists()
    assert list(project.glob(".haute_cache/inputs/*/.staging-*")) == []


@pytest.mark.usefixtures("in_process_worker")
def test_a_build_whose_seed_is_replaced_before_it_publishes_is_not_reported_cached(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._node_snapshots as node_snapshots
    from haute._execution_context import ExecutionProfile

    graph = _chain_graph(project)
    _cache(client, graph, "explore_a")
    resolver = _resolver(project, graph)
    a_identity = resolver.node_output_slot("A").identity(resolver.node_output_signature("A"))
    refreshed: list[bool] = []

    def refresh_a_before_b_publishes(name: str) -> None:
        if name != "publish_before_recheck" or refreshed:
            return
        refreshed.append(True)
        store = NodeSnapshotStore(project)
        artifact = store.stage_node_output(a_identity)
        pl.DataFrame({"policy_id": [1]}).write_parquet(artifact.part_path(0))
        store.publish_node_output(
            a_identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
            refresh=True,
        ).close()

    monkeypatch.setattr(node_snapshots, "_fault_point", refresh_a_before_b_publishes)
    run = client.post("/api/node-data/run", json=_body(graph, "explore_b")).json()
    job = _poll(client, run["job_id"])

    assert refreshed == [True]
    assert job["status"] == "contract_error"
    assert "changed while its data was being cached" in job["message"]
    assert _state(project, graph, "B") == "missing"


@pytest.mark.usefixtures("in_process_worker")
def test_explicit_build_of_a_join_is_chunked_and_equals_native(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims_path = project / "claims.parquet"
    pl.DataFrame(
        {
            "policy_id": list(range(100)),
            "claim_amount": [float(value * 2) for value in range(100)],
        }
    ).write_parquet(claims_path)

    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "quotes",
                    "data": {
                        "label": "quotes",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "claims",
                    "data": {
                        "label": "claims",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(claims_path),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "join_node",
                    "data": {
                        "label": "join_node",
                        "nodeType": "edgeJoin",
                        "config": {
                            "how": "left",
                            "on": "policy_id",
                            "selected_columns": ["policy_id", "premium", "claim_amount"],
                            "column_renames": {"claim_amount": "loss"},
                        },
                    },
                },
                {
                    "id": "explore",
                    "data": {
                        "label": "explore",
                        "nodeType": "explore",
                        "config": {},
                    },
                },
            ],
            "edges": [
                # Edge to join handle listed FIRST
                make_edge("claims", "join_node", target_handle="join").model_dump(),
                # Edge to base handle listed SECOND
                make_edge("quotes", "join_node", target_handle="base").model_dump(),
                make_edge("join_node", "explore").model_dump(),
            ],
        }
    ).model_dump()

    # 100 rows in chunks of 40: three parts, without a query per two rows.
    monkeypatch.delenv("POLARS_STREAMING_CHUNK_SIZE", raising=False)
    set_streaming_chunk_size(40)
    job = _cache(client, graph, "explore")
    assert job["status"] == "completed"

    resolver = _resolver(project, graph)
    identity = resolver.node_output_slot("join_node").identity(
        resolver.node_output_signature("join_node")
    )
    gen = resolver.store.latest_generation(identity)
    assert gen is not None
    assert len(gen.generation.metadata.parts) > 1
    assert len(gen.generation.data_paths) > 1

    gen_df = gen.generation.lazy_frame.collect().sort("policy_id")
    expected = (
        pl.read_parquet(project / "quotes.parquet")
        .join(pl.read_parquet(claims_path), on="policy_id", how="left")
        .select(["policy_id", "premium", "claim_amount"])
        .rename({"claim_amount": "loss"})
        .sort("policy_id")
    )
    assert_frame_equal(gen_df.select(expected.columns), expected)


def test_explicit_build_publishes_with_write_time_digests(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, dict[str, str], tuple[Any, ...]]] = []
    real_describe_parts = node_snapshots_module.describe_parts

    def _recording_describe_parts(
        directory: Path,
        digests: Mapping[str, str] | None = None,
    ) -> tuple[Any, ...]:
        recorded_digests = dict(digests or {})
        result = real_describe_parts(directory, digests=digests)
        calls.append((directory, recorded_digests, result))
        return result

    monkeypatch.setattr(node_snapshots_module, "describe_parts", _recording_describe_parts)

    graph_dict = _graph(project)
    job = _cache(client, graph_dict, "explore")
    assert job["status"] == "completed"

    assert len(calls) >= 1
    for _directory, recorded_digests, parts in calls:
        part_names = {part.name for part in parts}
        assert set(recorded_digests.keys()) == part_names

    resolver = _resolver(project, graph_dict)
    identity = resolver.node_output_slot("join").identity(resolver.node_output_signature("join"))
    gen = resolver.store.latest_generation(identity)
    assert gen is not None
    assert len(gen.generation.metadata.parts) >= 1
    for part in gen.generation.metadata.parts:
        part_path = gen.generation.directory / part.name
        assert len(part.digest) == 16
        assert part.digest == content_hash(part_path)


def _captured_join_graph(project: Path, claims_path: Path) -> dict[str, Any]:
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "quotes",
                    "data": {
                        "label": "quotes",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "claims",
                    "data": {
                        "label": "claims",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(claims_path),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "join_node",
                    "data": {
                        "label": "join_node",
                        "nodeType": "edgeJoin",
                        "config": {
                            "how": "left",
                            "on": "policy_id",
                            "selected_columns": ["policy_id", "premium", "claim_amount"],
                            "column_renames": {"claim_amount": "loss"},
                        },
                    },
                },
                {
                    "id": "target_node",
                    "data": {
                        "label": "target_node",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = join_node.with_columns("
                                "(pl.col('premium') * 2).alias('double'))"
                            )
                        },
                    },
                },
                {
                    "id": "explore",
                    "data": {
                        "label": "explore",
                        "nodeType": "explore",
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("claims", "join_node", target_handle="join").model_dump(),
                make_edge("quotes", "join_node", target_handle="base").model_dump(),
                make_edge("join_node", "target_node").model_dump(),
                make_edge("target_node", "explore").model_dump(),
            ],
        }
    ).model_dump()


def test_an_explicit_build_and_its_captures_share_the_editor_chunk_size(
    client: TestClient,
    project: Path,
    in_process_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims_path = project / "claims.parquet"
    pl.DataFrame(
        {
            "policy_id": list(range(1000)),
            "claim_amount": [float(i * 10) for i in range(1000)],
        }
    ).write_parquet(claims_path)

    recorded: list[tuple[int, int | None]] = []
    real_write_parts = haute._chunked_writes.write_parts

    def _recording_write_parts(*args: Any, **kwargs: Any) -> Any:
        ambient = current_streaming_chunk_size()
        chunk_rows = kwargs.get("chunk_rows")
        recorded.append((ambient, chunk_rows))
        return real_write_parts(*args, **kwargs)

    monkeypatch.setattr(service_mod, "write_parts", _recording_write_parts, raising=False)
    monkeypatch.setattr(haute._execute_lazy, "write_parts", _recording_write_parts)
    monkeypatch.setattr(haute._chunked_writes, "write_parts", _recording_write_parts)

    monkeypatch.delenv("POLARS_STREAMING_CHUNK_SIZE", raising=False)
    editor_size = 200
    set_streaming_chunk_size(editor_size)

    graph = _captured_join_graph(project, claims_path)
    job = _cache(client, graph, "explore")
    assert job["status"] == "completed"

    assert len(recorded) >= 2
    for ambient, chunk_rows in recorded:
        effective_size = chunk_rows if chunk_rows is not None else ambient
        assert effective_size == editor_size
        assert ambient == editor_size

    captures = job["execution_metrics"]["shared_snapshot_captures"]
    join_capture = next(c for c in captures if c["node_id"] == "join_node")
    assert join_capture["write_chunk_rows"] == editor_size

    resolver = _resolver(project, graph)
    identity = resolver.node_output_slot("target_node").identity(
        resolver.node_output_signature("target_node")
    )
    gen = resolver.store.latest_generation(identity)
    assert gen is not None
    assert len(gen.generation.metadata.parts) > 1

    gen_df = gen.generation.lazy_frame.collect().sort("policy_id")
    expected = (
        pl.read_parquet(project / "quotes.parquet")
        .join(pl.read_parquet(claims_path), on="policy_id", how="left")
        .select(["policy_id", "premium", "claim_amount"])
        .rename({"claim_amount": "loss"})
        .with_columns((pl.col("premium") * 2).alias("double"))
        .sort("policy_id")
    )
    assert_frame_equal(gen_df.select(expected.columns), expected)


@pytest.mark.usefixtures("in_process_worker")
def test_explicit_build_of_a_chunk_local_filter_is_input_sliced_and_equals_native(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded_writes: list[haute._chunked_writes.ChunkedWrite] = []
    real_write_parts = haute._chunked_writes.write_parts

    def _recording_write_parts(*args: Any, **kwargs: Any) -> Any:
        written = real_write_parts(*args, **kwargs)
        recorded_writes.append(written)
        return written

    monkeypatch.setattr(service_mod, "write_parts", _recording_write_parts, raising=False)
    monkeypatch.setattr(haute._execute_lazy, "write_parts", _recording_write_parts)
    monkeypatch.setattr(haute._chunked_writes, "write_parts", _recording_write_parts)

    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "quotes",
                    "data": {
                        "label": "quotes",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "filter_node",
                    "data": {
                        "label": "filter_node",
                        "nodeType": "polars",
                        "config": {"code": "df = quotes.filter(pl.col('premium') > 200)"},
                    },
                },
                {
                    "id": "explore",
                    "data": {
                        "label": "explore",
                        "nodeType": "explore",
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("quotes", "filter_node").model_dump(),
                make_edge("filter_node", "explore").model_dump(),
            ],
        }
    ).model_dump()

    monkeypatch.delenv("POLARS_STREAMING_CHUNK_SIZE", raising=False)
    set_streaming_chunk_size(200)
    job = _cache(client, graph, "explore")
    assert job["status"] == "completed"

    assert len(recorded_writes) == 1
    write = recorded_writes[0]
    assert write.strategy == "input_sliced"
    assert write.input_slices == 5
    assert write.chunks == 5

    resolver = _resolver(project, graph)
    identity = resolver.node_output_slot("filter_node").identity(
        resolver.node_output_signature("filter_node")
    )
    gen = resolver.store.latest_generation(identity)
    assert gen is not None
    assert len(gen.generation.metadata.parts) == 5
    assert len(gen.generation.data_paths) == 5

    gen_df = gen.generation.lazy_frame.collect().sort("policy_id")
    expected = (
        pl.read_parquet(project / "quotes.parquet")
        .filter(pl.col("premium") > 200)
        .sort("policy_id")
    )
    assert_frame_equal(gen_df, expected)
