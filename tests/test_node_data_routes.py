"""Node-data routes: point, run, join, supersede, refresh, clear, delegate (CACHE-S03)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

from haute._data_points import DataPoint, DataPointResolver
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
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
    pl.DataFrame({"premium": [1.0]}).write_parquet(artifact.data_path)
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
        _write(artifact.data_path, b"partial parquet")
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
    import haute._polars_utils as polars_utils

    entered = threading.Event()
    release = threading.Event()
    real_sink = polars_utils.bounded_sink

    def paused_after_preparation(*args, **kwargs):
        # The sink runs only after input preparation has published the snapshot.
        entered.set()
        assert release.wait(60)
        return real_sink(*args, **kwargs)

    monkeypatch.setattr(polars_utils, "bounded_sink", paused_after_preparation)

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
