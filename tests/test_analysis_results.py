"""Analysis results keyed by data version: the store, the profile job, synchronous analyses.

Covers CACHE-S04: one document per ``(point, data version, kind, version)``, the
data profile as an isolated-worker job with the shared failure envelope, and the
synchronous-analysis helper that memoises short analyses per data version.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import polars as pl
import pytest

from haute._analysis_results import AnalysisKey, AnalysisResultStore, SynchronousAnalysisCache
from haute._data_points import CacheRequiredError, DataPoint, DataPointResolver
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute.routes import _node_data_service as service_mod
from haute.schemas import ExploreOverviewSummary, NodeDataProfile
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64

_TERMINAL = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


def _rewrite(document: Path, text: str) -> None:
    """Overwrite one stored analysis document the caller found in its sandbox."""
    document.write_text(text, encoding="utf-8")


def _profile(data_version: str, *, row_count: int = 3) -> NodeDataProfile:
    return NodeDataProfile(
        row_count=row_count,
        column_count=1,
        columns=[],
        overview_summary=ExploreOverviewSummary(),
        data_version=data_version,
        generated_at=1.0,
    )


# --------------------------------------------------------------- the store


def test_a_stored_analysis_is_returned_only_for_its_own_data_version(tmp_path: Path) -> None:
    store = AnalysisResultStore(tmp_path)
    first = AnalysisKey(_DIGEST, "v1", "profile", 1)
    store.write(first, _profile("v1"))

    assert store.read(first, NodeDataProfile) == _profile("v1")
    assert store.read(AnalysisKey(_DIGEST, "v2", "profile", 1), NodeDataProfile) is None
    # The point moved on, so the superseded document is gone rather than idle.
    assert store.read(first, NodeDataProfile) is None


def test_reading_one_analysis_keeps_the_other_analyses_of_the_same_point(tmp_path: Path) -> None:
    store = AnalysisResultStore(tmp_path)
    store.write(AnalysisKey(_DIGEST, "v1", "profile", 1), _profile("v1"))
    store.write(AnalysisKey(_DIGEST, "v1", "profile", 2), _profile("v1", row_count=7))
    store.write(AnalysisKey(_OTHER_DIGEST, "v1", "profile", 1), _profile("v1", row_count=9))

    assert store.read(AnalysisKey(_DIGEST, "v2", "profile", 1), NodeDataProfile) is None

    assert store.read(AnalysisKey(_DIGEST, "v1", "profile", 2), NodeDataProfile) == _profile(
        "v1", row_count=7
    )
    assert store.read(AnalysisKey(_OTHER_DIGEST, "v1", "profile", 1), NodeDataProfile) == _profile(
        "v1", row_count=9
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "not json at all",
        json.dumps({"schema_version": 1, "result": {"row_count": 3}}),
        json.dumps(
            {
                "schema_version": 1,
                "point_digest": _DIGEST,
                "data_version": "v1",
                "kind": "profile",
                "version": 1,
                "result": {"row_count": "many"},
            }
        ),
        json.dumps(
            {
                "schema_version": 99,
                "point_digest": _DIGEST,
                "data_version": "v1",
                "kind": "profile",
                "version": 1,
                "result": _profile("v1").model_dump(mode="json"),
            }
        ),
    ],
    ids=["unparsable", "missing_key_fields", "invalid_result", "unknown_schema_version"],
)
def test_a_corrupt_analysis_document_is_discarded_and_never_returned(
    tmp_path: Path, corruption: str
) -> None:
    store = AnalysisResultStore(tmp_path)
    key = AnalysisKey(_DIGEST, "v1", "profile", 1)
    store.write(key, _profile("v1"))
    document = next((tmp_path / ".haute_cache" / "analyses").rglob("*.json"))
    _rewrite(document, corruption)

    assert store.read(key, NodeDataProfile) is None

    assert not document.exists()
    store.write(key, _profile("v1"))
    assert store.read(key, NodeDataProfile) == _profile("v1")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("point_digest", _OTHER_DIGEST),
        ("data_version", "v2"),
        ("kind", "levels"),
        ("version", 2),
    ],
)
def test_a_document_keyed_to_other_data_is_discarded_and_never_returned(
    tmp_path: Path, field: str, value: object
) -> None:
    """A valid document that belongs to another key must never be served for this one."""
    store = AnalysisResultStore(tmp_path)
    key = AnalysisKey(_DIGEST, "v1", "profile", 1)
    document = {
        "schema_version": 1,
        "point_digest": key.point_digest,
        "data_version": key.data_version,
        "kind": key.kind,
        "version": key.version,
        "result": _profile("v1").model_dump(mode="json"),
    }
    document[field] = value
    store.write(key, _profile("v1"))
    path = next((tmp_path / ".haute_cache" / "analyses").rglob("*.json"))
    _rewrite(path, json.dumps(document))

    assert store.read(key, NodeDataProfile) is None

    assert not path.exists()


def test_clearing_a_point_removes_every_analysis_of_that_point_only(tmp_path: Path) -> None:
    store = AnalysisResultStore(tmp_path)
    store.write(AnalysisKey(_DIGEST, "v1", "profile", 1), _profile("v1"))
    store.write(AnalysisKey(_DIGEST, "v2", "levels", 1), _profile("v2"))
    store.write(AnalysisKey(_OTHER_DIGEST, "v1", "profile", 1), _profile("v1"))

    store.clear_point(_DIGEST)

    assert store.read(AnalysisKey(_DIGEST, "v1", "profile", 1), NodeDataProfile) is None
    assert store.read(AnalysisKey(_DIGEST, "v2", "levels", 1), NodeDataProfile) is None
    assert store.read(AnalysisKey(_OTHER_DIGEST, "v1", "profile", 1), NodeDataProfile) is not None
    store.clear_point(_OTHER_DIGEST)


@pytest.mark.parametrize(
    ("point_digest", "data_version", "kind", "version"),
    [
        ("short", "v1", "profile", 1),
        (_DIGEST.upper(), "v1", "profile", 1),
        (_DIGEST, "", "profile", 1),
        (_DIGEST, "v1", "not an identifier", 1),
        (_DIGEST, "v1", "profile", 0),
        (_DIGEST, "v1", "profile", True),
    ],
)
def test_an_invalid_analysis_key_is_rejected(
    point_digest: str, data_version: str, kind: str, version: int
) -> None:
    with pytest.raises(ValueError):
        AnalysisKey(point_digest, data_version, kind, version)


def test_a_clear_that_cannot_delete_its_documents_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._analysis_results as analysis_module

    store = AnalysisResultStore(tmp_path)
    key = AnalysisKey(_DIGEST, "v1", "profile", 1)
    store.write(key, _profile("v1"))

    def refuse_deletion(_path, ignore_errors=False, **_kwargs):
        # Behaves like the real rmtree, so a caller that suppresses errors sees
        # a silent success rather than this failure.
        if ignore_errors:
            return
        raise PermissionError("the analysis directory is in use")

    with monkeypatch.context() as patched:
        patched.setattr(analysis_module.shutil, "rmtree", refuse_deletion)
        with pytest.raises(PermissionError):
            store.clear_point(_DIGEST)

    assert store.read(key, NodeDataProfile) == _profile("v1")


def test_clearing_an_invalid_point_digest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AnalysisResultStore(tmp_path).clear_point("../escape")


def test_a_synchronous_analysis_is_memoised_per_point_data_version_and_request() -> None:
    cache = SynchronousAnalysisCache(max_entries=4)
    one = SynchronousAnalysisCache.request_digest({"field": "region", "limit": 5})
    two = SynchronousAnalysisCache.request_digest({"field": "region", "limit": 6})
    cache.put(_DIGEST, "v1", one, "members")

    assert cache.get(_DIGEST, "v1", one) == "members"
    assert cache.get(_DIGEST, "v2", one) is None
    assert cache.get(_OTHER_DIGEST, "v1", one) is None
    assert cache.get(_DIGEST, "v1", two) is None
    assert one == SynchronousAnalysisCache.request_digest({"limit": 5, "field": "region"})

    cache.clear()
    assert cache.get(_DIGEST, "v1", one) is None


# ---------------------------------------------------------- the profile job


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "policy_id": list(range(100)),
            "premium": [float(value) for value in range(100)],
            "region": ["north", "south"] * 50,
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    return haute_scratch


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
    # Pinned so the limit does not move with the machine, at each profile's
    # own adaptive floor: below it, a real worker is capped under what the
    # policy calls a reasonable minimum and reports memory_limited for
    # reasons that have nothing to do with the test.
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "4096")
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", "4096")


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


def _graph(project: Path, *, source_config: dict[str, Any] | None = None) -> dict[str, Any]:
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
                    "data": {
                        "label": "join",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = source.with_columns("
                            "(pl.col('premium') * 2).alias('double'))"
                        },
                    },
                },
                {
                    "id": "explore",
                    "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                },
                {
                    # Reads the Data Input directly, so its point is the file.
                    "id": "band_source",
                    "data": {
                        "label": "band_source",
                        "nodeType": "banding",
                        "config": {"factors": []},
                    },
                },
            ],
            "edges": [
                make_edge("source", "join").model_dump(),
                make_edge("join", "explore").model_dump(),
                make_edge("source", "band_source").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def _body(graph: dict[str, Any], node_id: str, **extra: Any) -> dict[str, Any]:
    return {"graph": graph, "node_id": node_id, "source": "live", **extra}


def _poll(client: TestClient, job_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/node-data/status/{job_id}").json()
        if payload["status"] in _TERMINAL:
            return payload
        time.sleep(0.02)
    raise TimeoutError(f"node-data job {job_id} did not finish")


def _documents(project: Path) -> list[Path]:
    return sorted((project / ".haute_cache" / "analyses").rglob("*.json"))


def _cache_explore_data(client: TestClient, graph: dict[str, Any]) -> None:
    run = client.post("/api/node-data/run", json=_body(graph, "explore")).json()
    if run["status"] == "started":
        assert _poll(client, run["job_id"])["status"] == "completed"


def _profile_now(client: TestClient, graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Return the completed profile response, polling a started job to completion."""
    started = client.post("/api/node-data/profile", json=_body(graph, node_id)).json()
    if started["status"] != "started":
        return started
    assert _poll(client, started["job_id"])["status"] == "completed"
    return client.post("/api/node-data/profile", json=_body(graph, node_id)).json()


def test_profiling_an_uncached_point_asks_for_the_whole_dataset(
    client: TestClient, project: Path
) -> None:
    response = client.post("/api/node-data/profile", json=_body(_graph(project), "explore"))

    assert response.status_code == 200
    payload = response.json()
    assert (payload["status"], payload["result"], payload["job_id"]) == (
        "cache_required",
        None,
        None,
    )
    assert payload["point"]["state"] == "missing"
    assert _documents(project) == []


def test_the_data_profile_is_computed_once_and_then_served_from_the_store(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._frame_profile as frame_profile

    computed = []
    real_stats = frame_profile._build_frame_stats

    def counted_stats(*args, **kwargs):
        computed.append(1)
        return real_stats(*args, **kwargs)

    monkeypatch.setattr(frame_profile, "_build_frame_stats", counted_stats)
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert started["status"] == "started"
    status = _poll(client, started["job_id"])

    assert status["status"] == "completed"
    assert status["profile"]["row_count"] == 100
    assert status["profile"]["column_count"] == 4
    assert {column["name"] for column in status["profile"]["columns"]} == {
        "policy_id",
        "premium",
        "region",
        "double",
    }
    served = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert (served["status"], served["job_id"]) == ("completed", None)
    assert served["result"] == status["profile"]
    assert computed == [1]
    assert len(_documents(project)) == 1


def test_a_second_request_joins_the_running_profile_of_the_same_data(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._frame_profile as frame_profile

    entered = threading.Event()
    release = threading.Event()
    real_stats = frame_profile._build_frame_stats

    def gated_stats(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return real_stats(*args, **kwargs)

    monkeypatch.setattr(frame_profile, "_build_frame_stats", gated_stats)
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert entered.wait(30)
    joined = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()

    assert (joined["status"], joined["job_id"]) == ("joined", started["job_id"])
    release.set()
    assert _poll(client, started["job_id"])["status"] == "completed"
    assert len(_documents(project)) == 1


def test_a_refreshed_point_never_returns_the_previous_profile(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project)
    _cache_explore_data(client, graph)
    first = _profile_now(client, graph, "explore")
    assert first["status"] == "completed"

    time.sleep(0.01)
    pl.DataFrame(
        {"policy_id": [1, 2], "premium": [1.0, 2.0], "region": ["north", "south"]}
    ).write_parquet(project / "quotes.parquet")
    rebuild = client.post("/api/node-data/run", json=_body(graph, "explore")).json()
    assert _poll(client, rebuild["job_id"])["status"] == "completed"

    stale = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert stale["status"] == "started"
    assert _poll(client, stale["job_id"])["status"] == "completed"
    second = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert second["result"]["row_count"] == 2
    assert second["result"]["data_version"] != first["result"]["data_version"]
    assert len(_documents(project)) == 1


def test_a_profile_that_is_not_admitted_reports_a_memory_limit_and_stores_nothing(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionProfile

    graph = _graph(project)
    _cache_explore_data(client, graph)

    def refuse_admission(*, operation: str, profile: ExecutionProfile, **_kwargs):
        raise ExecutionAdmissionError(
            operation,
            profile=profile,
            memory_limit_bytes=1,
            rss_at_admission_bytes=None,
            reason="in_flight_limit_reached",
        )

    monkeypatch.setattr(service_mod, "create_admitted_execution_context", refuse_admission)
    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()

    final = _poll(client, started["job_id"])
    assert (final["status"], final["terminal_reason"]) == ("memory_limited", "memory_limited")
    assert final["profile"] is None
    assert final["error_code"] == "memory_limit"
    assert final["error_detail"]["reason"] == "in_flight_limit_reached"
    assert _documents(project) == []


def test_a_profile_over_its_memory_budget_reports_a_memory_limit_and_stores_nothing(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._frame_profile as frame_profile
    from haute._execution_context import ExecutionMemoryLimitExceededError

    def exceed_memory(*_args, **_kwargs):
        raise ExecutionMemoryLimitExceededError(
            "node_data_profile", rss_bytes=4_000_000_000, limit_bytes=1_000_000
        )

    monkeypatch.setattr(frame_profile, "_build_frame_stats", exceed_memory)
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()

    final = _poll(client, started["job_id"])
    assert (final["status"], final["terminal_reason"]) == ("memory_limited", "memory_limited")
    assert final["error_code"] == "memory_limit"
    assert final["error_detail"]["error_code"] == "memory_limit"
    assert _documents(project) == []


def test_cancelling_a_profile_ends_it_cancelled_and_stores_nothing(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._frame_profile as frame_profile

    entered = threading.Event()
    release = threading.Event()
    real_stats = frame_profile._build_frame_stats

    def gated_stats(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return real_stats(*args, **kwargs)

    monkeypatch.setattr(frame_profile, "_build_frame_stats", gated_stats)
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert entered.wait(30)
    client.post(f"/api/node-data/cancel/{started['job_id']}")
    release.set()

    final = _poll(client, started["job_id"])
    assert (final["status"], final["terminal_reason"]) == ("cancelled", "cancelled")
    assert _documents(project) == []
    # The cancelled profile left nothing behind, so the next request recomputes.
    again = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert again["status"] == "started"
    assert _poll(client, again["job_id"])["status"] == "completed"


def test_clearing_a_point_removes_its_stored_profile(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project)
    _cache_explore_data(client, graph)
    assert _profile_now(client, graph, "explore")["status"] == "completed"
    assert len(_documents(project)) == 1

    cleared = client.post("/api/node-data/clear", json=_body(graph, "explore")).json()

    assert cleared["status"] == "cleared"
    assert _documents(project) == []
    assert (
        client.post("/api/node-data/profile", json=_body(graph, "explore")).json()["status"]
        == "cache_required"
    )


def test_clearing_a_point_beats_a_profile_paused_before_publication(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleared point must not be repopulated by the profile that was already computed."""
    entered = threading.Event()
    release = threading.Event()
    real_validate = service_mod._validated_profile_success

    def paused_before_publication(outcome):
        profile = real_validate(outcome)
        entered.set()
        assert release.wait(30)
        return profile

    monkeypatch.setattr(service_mod, "_validated_profile_success", paused_before_publication)
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert entered.wait(30)
    cleared = client.post("/api/node-data/clear", json=_body(graph, "explore")).json()
    assert cleared["status"] == "cleared"
    release.set()

    final = _poll(client, started["job_id"])
    assert (final["status"], final["terminal_reason"]) == ("cancelled", "cancelled")
    assert final["profile"] is None
    assert _documents(project) == []


def test_a_clear_never_interleaves_with_a_profile_that_is_publishing(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clear must not delete around a publication and leave the document behind."""
    from haute._analysis_results import AnalysisResultStore

    entered = threading.Event()
    release = threading.Event()

    class PausingStore(AnalysisResultStore):
        def write(self, key: Any, result: Any) -> None:
            entered.set()
            assert release.wait(30)
            super().write(key, result)

    monkeypatch.setattr(service_mod, "AnalysisResultStore", PausingStore)
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert entered.wait(30)
    cleared: dict[str, Any] = {}

    def clear_the_point() -> None:
        cleared.update(client.post("/api/node-data/clear", json=_body(graph, "explore")).json())

    clearing = threading.Thread(target=clear_the_point, name="clear-during-publication")
    clearing.start()
    time.sleep(0.2)
    release.set()
    clearing.join(60)

    assert _poll(client, started["job_id"])["status"] in {"completed", "cancelled"}
    assert cleared["status"] == "cleared"
    # Whichever order the two operations serialise in, the cleared point keeps
    # no analysis of the data that has just been removed.
    assert _documents(project) == []
    assert (
        client.post("/api/node-data/profile", json=_body(graph, "explore")).json()["status"]
        == "cache_required"
    )


def test_a_clear_that_cannot_remove_the_analyses_reports_the_failure(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._analysis_results as analysis_module

    graph = _graph(project)
    _cache_explore_data(client, graph)
    assert _profile_now(client, graph, "explore")["status"] == "completed"

    def refuse_deletion(_path, ignore_errors=False, **_kwargs):
        # Behaves like the real rmtree, so a caller that suppresses errors sees
        # a silent success rather than this failure.
        if ignore_errors:
            return
        raise PermissionError("the analysis directory is in use")

    with monkeypatch.context() as patched:
        patched.setattr(analysis_module.shutil, "rmtree", refuse_deletion)
        response = client.post("/api/node-data/clear", json=_body(graph, "explore"))

    assert response.status_code == 500
    assert len(_documents(project)) == 1
    # The snapshot is untouched, so a retried clear still has something to clear.
    assert (
        client.post("/api/node-data/point", json=_body(graph, "explore")).json()["state"]
        == "current"
    )
    assert client.post("/api/node-data/clear", json=_body(graph, "explore")).json()["status"] == (
        "cleared"
    )


def test_a_direct_file_profile_becomes_unavailable_after_the_file_is_rewritten(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project)
    first = _profile_now(client, graph, "band_source")

    assert first["status"] == "completed"
    assert first["point"]["reads_directly"] is True
    assert first["result"]["row_count"] == 100
    time.sleep(0.01)
    pl.DataFrame(
        {"policy_id": [1, 2, 3], "premium": [1.0, 2.0, 3.0], "region": ["north"] * 3}
    ).write_parquet(project / "quotes.parquet")

    stale = client.post("/api/node-data/profile", json=_body(graph, "band_source")).json()
    assert stale["status"] == "started"
    assert _poll(client, stale["job_id"])["status"] == "completed"
    assert _profile_now(client, graph, "band_source")["result"]["row_count"] == 3
    assert len(_documents(project)) == 1


def test_a_direct_file_profile_becomes_unavailable_after_the_selection_changes(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    graph = _graph(project)
    first = _profile_now(client, graph, "band_source")
    assert first["status"] == "completed"

    renamed = _graph(
        project,
        source_config={
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": str(project / "quotes.parquet"),
            "arguments": {},
            "column_renames": {"premium": "gross_premium"},
        },
    )

    after = client.post("/api/node-data/profile", json=_body(renamed, "band_source")).json()
    assert after["status"] == "started"
    assert _poll(client, after["job_id"])["status"] == "completed"
    assert (
        _profile_now(client, renamed, "band_source")["result"]["data_version"]
        != first["result"]["data_version"]
    )


def test_a_direct_file_rewritten_while_it_is_profiled_is_reported_as_changed(
    client: TestClient, project: Path, in_process_worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._frame_profile as frame_profile

    real_stats = frame_profile._build_frame_stats

    def rewrite_then_profile(*args, **kwargs):
        stats = real_stats(*args, **kwargs)
        time.sleep(0.01)
        pl.DataFrame({"policy_id": [7], "premium": [1.0], "region": ["north"]}).write_parquet(
            project / "quotes.parquet"
        )
        return stats

    monkeypatch.setattr(frame_profile, "_build_frame_stats", rewrite_then_profile)
    graph = _graph(project)

    started = client.post("/api/node-data/profile", json=_body(graph, "band_source")).json()

    final = _poll(client, started["job_id"])
    assert final["status"] == "contract_error"
    assert final["error_code"] == "node_data_changed"
    assert _documents(project) == []


# ------------------------------------------------- synchronous analyses


def _resolver(project: Path, graph_payload: dict[str, Any]) -> DataPointResolver:
    from haute.schemas import NodeDataRequest

    graph = NodeDataRequest.model_validate(_body(graph_payload, "join")).graph
    return DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))


def _row_count(leased, context) -> int:
    from haute._polars_utils import cancellable_streaming_collect

    frame = cancellable_streaming_collect(
        leased.scan.select(pl.len().alias("rows")), execution_context=context
    )
    return int(frame.item(0, 0))


def test_a_synchronous_analysis_runs_once_for_each_data_version(project: Path) -> None:
    from haute.routes._synchronous_analysis import run_synchronous_analysis

    cache = SynchronousAnalysisCache()
    calls: list[int] = []

    def counted(leased, context) -> int:
        calls.append(1)
        return _row_count(leased, context)

    def analyse(resolver: DataPointResolver) -> int:
        return run_synchronous_analysis(
            resolver,
            DataPoint("source", None),
            NodeSnapshotColumns.all(),
            operation="test_rows",
            request={"analysis": "rows"},
            cache=cache,
            compute=counted,
        )

    resolver = _resolver(project, _graph(project))
    assert analyse(resolver) == 100
    assert analyse(resolver) == 100
    assert calls == [1]

    time.sleep(0.01)
    pl.DataFrame({"policy_id": [1], "premium": [1.0], "region": ["north"]}).write_parquet(
        project / "quotes.parquet"
    )
    assert analyse(_resolver(project, _graph(project))) == 1
    assert calls == [1, 1]


def test_a_synchronous_analysis_of_an_uncached_point_reports_that_a_cache_is_required(
    project: Path,
) -> None:
    from haute.routes._synchronous_analysis import run_synchronous_analysis

    resolver = _resolver(project, _graph(project))

    with pytest.raises(CacheRequiredError):
        run_synchronous_analysis(
            resolver,
            DataPoint("join", None),
            NodeSnapshotColumns.all(),
            operation="test_rows",
            request={"analysis": "rows"},
            cache=SynchronousAnalysisCache(),
            compute=_row_count,
        )


@pytest.mark.parametrize("failure", ["admission", "memory"])
def test_a_synchronous_analysis_reports_a_memory_failure_as_507(
    project: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from fastapi import HTTPException

    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionMemoryLimitExceededError, ExecutionProfile
    from haute.routes import _synchronous_analysis as sync_mod

    def refuse_admission(*, operation: str, profile: ExecutionProfile, **_kwargs):
        raise ExecutionAdmissionError(
            operation,
            profile=profile,
            memory_limit_bytes=1,
            rss_at_admission_bytes=None,
            reason="in_flight_limit_reached",
        )

    def exceed_memory(_leased, _context) -> int:
        raise ExecutionMemoryLimitExceededError(
            "test_rows", rss_bytes=4_000_000_000, limit_bytes=1_000_000
        )

    if failure == "admission":
        monkeypatch.setattr(sync_mod, "create_admitted_execution_context", refuse_admission)
    compute = _row_count if failure == "admission" else exceed_memory

    with pytest.raises(HTTPException) as raised:
        sync_mod.run_synchronous_analysis(
            _resolver(project, _graph(project)),
            DataPoint("source", None),
            NodeSnapshotColumns.all(),
            operation="test_rows",
            request={"analysis": "rows"},
            cache=SynchronousAnalysisCache(),
            compute=compute,
        )

    assert raised.value.status_code == 507
    assert isinstance(raised.value.detail, dict)
    assert raised.value.detail["error_code"] in {"memory_limit", "admission_refused"}


async def test_a_disconnected_client_cancels_its_synchronous_analysis() -> None:
    from fastapi import HTTPException, Request

    from haute._execution_context import ExecutionCancellationToken
    from haute.routes._synchronous_analysis import run_until_disconnected

    class _DisconnectingRequest:
        def __init__(self) -> None:
            self.polls = 0

        async def is_disconnected(self) -> bool:
            self.polls += 1
            return self.polls > 1

    observed: list[str] = []

    def analysis(token: ExecutionCancellationToken) -> int:
        deadline = time.monotonic() + 30
        while not token.cancelled:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        observed.append("cancelled")
        return 0

    request = _DisconnectingRequest()
    with pytest.raises(HTTPException) as raised:
        await run_until_disconnected(cast(Request, request), analysis, poll_seconds=0.01)

    assert raised.value.status_code == 499
    assert observed == ["cancelled"]


async def test_cancelling_the_request_task_stops_its_analysis_first() -> None:
    """An abandoned request must not leave its analysis holding admission."""
    import asyncio

    from fastapi import Request

    from haute._execution_context import ExecutionCancellationToken
    from haute.routes._synchronous_analysis import run_until_disconnected

    class _ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    running = threading.Event()
    observed: list[str] = []

    def analysis(token: ExecutionCancellationToken) -> int:
        running.set()
        deadline = time.monotonic() + 30
        while not token.cancelled:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        time.sleep(0.05)
        observed.append("released")
        return 0

    task = asyncio.ensure_future(
        run_until_disconnected(cast(Request, _ConnectedRequest()), analysis, poll_seconds=0.01)
    )
    assert await asyncio.to_thread(running.wait, 30)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert observed == ["released"]


async def test_an_analysis_that_finishes_first_is_returned_to_the_connected_client() -> None:
    from fastapi import Request

    from haute._execution_context import ExecutionCancellationToken
    from haute.routes._synchronous_analysis import run_until_disconnected

    class _ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    def analysis(_token: ExecutionCancellationToken) -> int:
        return 42

    result = await run_until_disconnected(
        cast(Request, _ConnectedRequest()), analysis, poll_seconds=0.01
    )

    assert result == 42


def test_a_real_isolated_worker_profiles_the_point(client: TestClient, project: Path) -> None:
    graph = _graph(project)
    _cache_explore_data(client, graph)

    started = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()

    assert started["status"] == "started"
    status = _poll(client, started["job_id"], timeout=180)
    assert status["status"] == "completed"
    assert status["profile"]["row_count"] == 100
    assert status["execution_metrics"] is not None
    served = client.post("/api/node-data/profile", json=_body(graph, "explore")).json()
    assert served["result"]["column_count"] == 4


# ------------------------------------- per-column statistics through the route


def _profiled_columns(
    client: TestClient, project: Path, frame: pl.DataFrame, name: str
) -> list[dict[str, Any]]:
    """Profile a Data Input read directly, and return its per-column statistics.

    A consumer wired straight to a Data Input has no node output to build, so
    this profiles the source frame itself: the statistics describe exactly the
    columns written here.
    """
    path = project / f"{name}.parquet"
    frame.write_parquet(path)
    graph = _graph(
        project,
        source_config={
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": str(path),
            "arguments": {},
        },
    )
    response = _profile_now(client, graph, "band_source")
    assert response["status"] == "completed", response
    return response["result"]["columns"]


def test_a_profile_describes_every_column_of_the_source(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    columns = _profiled_columns(
        client,
        project,
        pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"], "score": [1.5, 2.5, 3.5]}),
        "tri",
    )

    assert [column["name"] for column in columns] == ["id", "name", "score"]
    assert [column["dtype"] for column in columns] == ["Int64", "String", "Float64"]


def test_a_profile_counts_nulls_distincts_and_nans_over_the_whole_column(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    nulls = _profiled_columns(client, project, pl.DataFrame({"value": [1, None, 2, None, 3]}), "n")
    distincts = _profiled_columns(client, project, pl.DataFrame({"value": [1, 1, 2, 2, 3]}), "d")
    nans = _profiled_columns(
        client,
        project,
        pl.DataFrame({"value": [1.0, float("nan"), float("nan"), None, 2.0]}),
        "f",
    )

    assert nulls[0]["null_count"] == 2
    assert distincts[0]["distinct_count"] == 3
    assert (nans[0]["nan_count"], nans[0]["null_count"]) == (2, 1)


def test_a_profile_truncates_a_long_value_and_reports_an_all_null_column(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    long_value = _profiled_columns(client, project, pl.DataFrame({"value": ["x" * 200]}), "long")[
        0
    ]["min_value"]
    all_null = _profiled_columns(
        client,
        project,
        pl.DataFrame({"value": [None, None, None]}, schema={"value": pl.Utf8}),
        "empty",
    )[0]

    assert long_value.endswith("…")
    assert len(long_value) == 81
    assert (all_null["min_value"], all_null["max_value"]) == (None, None)


def test_a_profile_keeps_the_schema_column_order(
    client: TestClient, project: Path, in_process_worker: None
) -> None:
    columns = _profiled_columns(client, project, pl.DataFrame({"c": [1], "a": [2], "b": [3]}), "o")

    assert [column["name"] for column in columns] == ["c", "a", "b"]


@pytest.mark.parametrize("failure", [False, True])
def test_profile_parent_cleans_worker_scratch_after_return(client, project, monkeypatch, failure):
    observed = []

    def worker(function, request, budget, **_kwargs):
        directory = Path(request.scratch_directory)
        assert directory.is_dir()
        (directory / "partial.parquet").write_bytes(b"partial")
        observed.append(directory)
        if failure:
            raise RuntimeError("worker terminated")
        return function(request, budget)

    monkeypatch.setattr(service_mod, "run_isolated_worker", worker)
    response = client.post(
        "/api/node-data/profile", json=_body(_graph(project), "band_source")
    ).json()
    assert response["status"] == "started"
    status = _poll(client, response["job_id"])
    assert status["status"] == ("error" if failure else "completed")
    from haute.routes.node_data import _node_data_service

    for thread in list(_node_data_service._threads.values()):
        thread.join(10)
    assert len(observed) == 1
    assert not observed[0].exists()
