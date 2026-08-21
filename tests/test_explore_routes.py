from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

from haute._file_ops import atomic_write_text
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


_TERMINAL_JOB_STATUSES = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


@pytest.fixture(autouse=True)
def _clean_explore_state(_widen_sandbox_root):
    try:
        from haute.routes.explore import _explore_service, _store
    except ImportError:
        yield
        return

    job_snapshot = dict(_store.jobs)
    yield
    _store.jobs.clear()
    _store.jobs.update(job_snapshot)
    _explore_service._report_cache.clear()


def _poll_explore(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/explore/status/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in _TERMINAL_JOB_STATUSES:
            return payload
        time.sleep(0.02)
    raise TimeoutError(f"Explore job {job_id} did not finish within {timeout}s")


_DEFAULT_PREP_CODE = "df = source.with_columns((pl.col('premium') * 2).alias('double_premium'))"


def _explore_graph(
    data_path: str,
    *,
    extra_downstream_label: str = "ignored",
    explore_config: dict | None = None,
    prep_code: str = _DEFAULT_PREP_CODE,
) -> dict:
    graph = make_graph(
        {
            "source_file": str(Path(data_path).with_name("pipeline.py")),
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
                            "path": data_path,
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "prep",
                    "data": {
                        "label": "prep",
                        "nodeType": "polars",
                        "config": {"code": prep_code},
                    },
                },
                {
                    "id": "explore",
                    "data": {
                        "label": "Explore",
                        "nodeType": "explore",
                        "config": explore_config or {},
                    },
                },
                {
                    "id": "downstream",
                    "data": {
                        "label": extra_downstream_label,
                        "nodeType": "output",
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("source", "prep").model_dump(),
                make_edge("prep", "explore").model_dump(),
                make_edge("prep", "downstream").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def test_explore_run_returns_cache_descriptor(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "quote_id": [f"q{i:03d}" for i in range(150)],
            "premium": list(range(150)),
            "region": ["north", "south", None] * 50,
            "constant": ["same"] * 150,
        }
    ).write_parquet(path)

    response = client.post(
        "/api/explore/run",
        json={"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"},
    )

    assert response.status_code == 200
    started = response.json()
    assert started["status"] == "started"
    assert started["job_id"]

    final = _poll_explore(client, started["job_id"])

    assert final["status"] == "completed"
    report = final["result"]
    assert report["status"] == "ok"
    assert report["node_id"] == "explore"
    assert report["upstream_node_id"] == "prep"
    assert report["row_count"] == 150
    assert report["column_count"] == 5
    assert report["source"] == "live"
    assert report["dataframe_cache_key"]
    assert report["generated_at"] > 0
    assert report["overview_summary"]["data_quality"]["issue_count"] >= 1
    assert isinstance(report["overview_summary"]["categorical_summary"], list)


def test_explore_run_applies_node_polars_code_before_caching(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "quote_id": ["a", "b", "c"],
            "premium": [0, 10, 20],
        }
    ).write_parquet(path)

    response = client.post(
        "/api/explore/run",
        json={
            "graph": _explore_graph(
                str(path),
                explore_config={
                    "code": (
                        "df = df.filter(pl.col('premium') >= 10)"
                        ".with_columns((pl.col('premium') + 1).alias('premium_plus_one'))"
                    )
                },
            ),
            "node_id": "explore",
            "source": "live",
        },
    )

    assert response.status_code == 200
    started = response.json()
    final = _poll_explore(client, started["job_id"])

    assert final["status"] == "completed"
    report = final["result"]
    assert report["row_count"] == 2
    assert report["column_count"] == 4


def test_explore_cache_materialises_admitted_upstream_group_by(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "quote_id": ["a", "a", "b"],
            "premium": [10, 20, 40],
        }
    ).write_parquet(path)

    response = client.post(
        "/api/explore/run",
        json={
            "graph": _explore_graph(
                str(path),
                prep_code=(
                    "df = source.group_by('quote_id').agg(pl.col('premium').sum().alias('premium'))"
                ),
            ),
            "node_id": "explore",
            "source": "live",
        },
    )

    assert response.status_code == 200
    started = response.json()
    final = _poll_explore(client, started["job_id"])

    assert final["status"] == "completed", final
    report = final["result"]
    assert report["row_count"] == 2
    assert report["column_count"] == 2
    premium = next(column for column in report["columns"] if column["name"] == "premium")
    assert premium["min_value"] == "30"
    assert premium["max_value"] == "40"
    strategy = final["execution_metrics"]["execution_strategy"]
    assert strategy["profile"] == "explore_analysis"
    assert strategy["reason_code"] == "group_by_materialisation_admitted"
    assert strategy["estimated_peak_bytes"] <= strategy["headroom_bytes"]


def test_explore_reuses_completed_report_for_same_analysis_key(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    first_status = _poll_explore(client, first["job_id"])
    assert first_status["status"] == "completed"

    second_response = client.post("/api/explore/run", json=body)

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["status"] == "completed"
    assert second["cached"] is True
    assert second["result"] == first_status["result"]


def test_explore_cache_status_reports_missing_before_first_materialisation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    response = client.post("/api/explore/cache-status", json=body)

    assert response.status_code == 200
    assert response.json() == {
        "state": "missing",
        "message": "Explore data needs caching",
        "result": None,
    }


def test_explore_cache_status_restores_durable_dataset_after_process_caches_are_cleared(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    started = client.post("/api/explore/run", json=body).json()
    completed = _poll_explore(client, started["job_id"])
    assert completed["status"] == "completed"

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    assert spec.project_root == tmp_path.resolve()
    dataframe_key = spec.dataframe_cache_request.keys_by_node["explore"]
    assert spec.dataframe_cache_request.cache.get(dataframe_key) is not None

    # Model a local backend restart: both process-owned layers disappear, while
    # the project-local durable generation remains on disk.
    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()
    assert spec.dataframe_cache_request.cache.get(dataframe_key) is None

    response = client.post("/api/explore/cache-status", json=body)

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["state"] == "current"
    assert snapshot["message"] == "Explore data is cached"
    assert snapshot["result"] == completed["result"]

    restored_spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    restored_key = restored_spec.dataframe_cache_request.keys_by_node["explore"]
    assert restored_spec.dataframe_cache_request.cache.get(restored_key) is not None

    cache_hit = client.post("/api/explore/run", json=body).json()
    assert cache_hit["status"] == "completed"
    assert cache_hit["cached"] is True
    assert cache_hit["result"] == completed["result"]


def test_durable_explore_generation_is_retained_while_a_reader_holds_a_lease(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, first["job_id"])["status"] == "completed"
    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)

    with store.lease(spec.family_key, report_cache_key=spec.report_cache_key) as snapshot:
        assert snapshot is not None
        assert snapshot.state == "current"
        leased_generation_dir = snapshot.data_path.parent

        # Two readers may hold the same immutable generation. Releasing one
        # lease after a refresh must not retire the artifact out from under
        # the other reader.
        with store.lease(spec.family_key, report_cache_key=spec.report_cache_key) as nested:
            assert nested is not None
            assert nested.generation_id == snapshot.generation_id

            refreshed = client.post("/api/explore/run", json={**body, "refresh": True}).json()
            assert _poll_explore(client, refreshed["job_id"])["status"] == "completed"
            assert leased_generation_dir.exists()

        assert leased_generation_dir.exists()
        spec.dataframe_cache_request.cache.clear()
        restored = store.restore(
            snapshot,
            spec.dataframe_cache_request,
            node_id=spec.node_id,
        )
        assert restored.path.exists()
        assert restored.row_count == 2

    assert not leased_generation_dir.exists()


def test_explore_cache_status_reports_stale_for_changed_analysis_identity(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    first_body = {
        "graph": _explore_graph(str(path), explore_config={"code": "df = df"}),
        "node_id": "explore",
        "source": "live",
    }
    changed_body = {
        "graph": _explore_graph(
            str(path),
            explore_config={"code": "df = df.filter(pl.col('premium') > 10)"},
        ),
        "node_id": "explore",
        "source": "live",
    }

    started = client.post("/api/explore/run", json=first_body).json()
    assert _poll_explore(client, started["job_id"])["status"] == "completed"

    # A stale generation may carry an older report schema. Staleness must be
    # inspectable so the user can re-cache; only a current report is parsed as
    # today's typed ExploreCacheReport.
    first_spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(first_body))
    store = ExplorePersistentCacheStore(first_spec.project_root)
    family_dir = store._family_dir(first_spec.family_key)
    pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
    metadata_path = family_dir / "generations" / pointer["generation_id"] / "meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["report"] = {"legacy_report_schema": True}
    atomic_write_text(metadata_path, json.dumps(metadata))

    response = client.post("/api/explore/cache-status", json=changed_body)

    assert response.status_code == 200
    assert response.json() == {
        "state": "stale",
        "message": "Explore cache is stale",
        "result": None,
    }


def test_explore_cache_status_detects_changed_source_data_as_stale(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    started = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, started["job_id"])["status"] == "completed"

    pl.DataFrame({"quote_id": ["a", "b", "c"], "premium": [10, 20, 30]}).write_parquet(path)
    response = client.post("/api/explore/cache-status", json=body)

    assert response.status_code == 200
    assert response.json()["state"] == "stale"
    assert response.json()["result"] is None


def test_explore_refresh_bypasses_current_report_and_dataframe_caches(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    first_completed = _poll_explore(client, first["job_id"])
    assert client.post("/api/explore/run", json=body).json()["cached"] is True

    refreshed = client.post("/api/explore/run", json={**body, "refresh": True}).json()

    assert refreshed["status"] == "started"
    assert refreshed["cached"] is False
    refreshed_completed = _poll_explore(client, refreshed["job_id"])
    assert refreshed_completed["status"] == "completed"
    assert refreshed_completed["result"]["generated_at"] > first_completed["result"]["generated_at"]


def test_failed_explore_refresh_preserves_last_durable_generation(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from haute import _explore_cache as persistent_cache_module
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    first_completed = _poll_explore(client, first["job_id"])
    assert first_completed["status"] == "completed"

    original_atomic_write_text = persistent_cache_module.atomic_write_text

    def fail_pointer_publication(path: Path, data: str, encoding: str = "utf-8") -> None:
        if path.name == "current.json":
            raise OSError("forced durable pointer publication failure")
        original_atomic_write_text(path, data, encoding)

    monkeypatch.setattr(
        persistent_cache_module,
        "atomic_write_text",
        fail_pointer_publication,
    )

    refreshed = client.post("/api/explore/run", json={**body, "refresh": True}).json()
    failed = _poll_explore(client, refreshed["job_id"])
    assert failed["status"] == "error"
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

    assert failed["message"] == _INTERNAL_ERROR_DETAIL
    assert "forced durable pointer publication failure" not in str(failed)
    terminal_log = capfd.readouterr().out
    assert "explore_cache_failed" in terminal_log
    assert "forced durable pointer publication failure" in terminal_log

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    dataframe_key = spec.dataframe_cache_request.keys_by_node["explore"]
    assert spec.dataframe_cache_request.cache.get(dataframe_key) is None
    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()

    snapshot = client.post("/api/explore/cache-status", json=body)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "current"
    assert snapshot.json()["result"] == first_completed["result"]


def test_failed_parent_restore_never_selects_the_replacement_generation(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    first = client.post("/api/explore/run", json=body).json()
    first_completed = _poll_explore(client, first["job_id"])
    assert first_completed["status"] == "completed"

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)
    pointer_path = store._family_dir(spec.family_key) / "current.json"
    original_pointer = pointer_path.read_bytes()

    def fail_restore(*_args, **_kwargs):
        raise OSError("forced parent pre-publication restore failure")

    monkeypatch.setattr(ExplorePersistentCacheStore, "restore_publication", fail_restore)
    refreshed = client.post("/api/explore/run", json={**body, "refresh": True}).json()
    failed = _poll_explore(client, refreshed["job_id"])

    assert failed["status"] == "error"
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

    assert failed["message"] == _INTERNAL_ERROR_DETAIL
    assert "forced parent pre-publication restore failure" not in str(failed)
    terminal_log = capfd.readouterr().out
    assert "explore_cache_failed" in terminal_log
    assert "forced parent pre-publication restore failure" in terminal_log
    assert pointer_path.read_bytes() == original_pointer
    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()
    snapshot = client.post("/api/explore/cache-status", json=body)
    assert snapshot.status_code == 200
    assert snapshot.json()["result"] == first_completed["result"]


def test_committed_explore_refresh_survives_old_generation_cleanup_failure(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _explore_cache as persistent_cache_module
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, first["job_id"])["status"] == "completed"
    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)
    family_dir = store._family_dir(spec.family_key)
    old_pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
    old_generation_dir = family_dir / "generations" / old_pointer["generation_id"]
    original_rmtree = persistent_cache_module.shutil.rmtree

    def fail_old_generation_retirement(candidate: Path, *args, **kwargs) -> None:
        if Path(candidate) == old_generation_dir:
            raise OSError("forced old-generation cleanup failure")
        original_rmtree(candidate, *args, **kwargs)

    monkeypatch.setattr(persistent_cache_module.shutil, "rmtree", fail_old_generation_retirement)

    refreshed = client.post("/api/explore/run", json={**body, "refresh": True}).json()
    refreshed_completed = _poll_explore(client, refreshed["job_id"])

    assert refreshed_completed["status"] == "completed"
    new_pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
    assert new_pointer["generation_id"] != old_pointer["generation_id"]
    assert old_generation_dir.exists()

    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()
    snapshot = client.post("/api/explore/cache-status", json=body)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "current"
    assert snapshot.json()["result"] == refreshed_completed["result"]


def test_cancelled_explore_refresh_preserves_last_durable_generation(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import _explore_service as service_mod
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    first_completed = _poll_explore(client, first["job_id"])
    assert first_completed["status"] == "completed"

    entered = threading.Event()
    release = threading.Event()
    original_dispatch = service_mod.run_isolated_worker

    def gated_dispatch(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5.0)
        return original_dispatch(*args, **kwargs)

    monkeypatch.setattr(service_mod, "run_isolated_worker", gated_dispatch)

    refreshed = client.post("/api/explore/run", json={**body, "refresh": True}).json()
    assert refreshed["status"] == "started"
    assert entered.wait(timeout=5.0)

    cancelled = client.post(f"/api/explore/cancel/{refreshed['job_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "running"
    release.set()

    worker_name = f"haute-explore-{refreshed['job_id']}"
    for thread in threading.enumerate():
        if thread.name == worker_name:
            thread.join(timeout=5.0)
            assert not thread.is_alive()
            break

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()

    snapshot = client.post("/api/explore/cache-status", json=body)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "current"
    assert snapshot.json()["result"] == first_completed["result"]


@pytest.mark.parametrize("warm_process_caches", [False, True])
def test_explore_cache_status_fails_loudly_for_corrupt_selected_generation(
    client: TestClient,
    tmp_path: Path,
    warm_process_caches: bool,
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    started = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, started["job_id"])["status"] == "completed"

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)
    pointer = store._family_dir(spec.family_key) / "current.json"
    atomic_write_text(pointer, "not valid json")
    if not warm_process_caches:
        _explore_service._report_cache.clear()
        spec.dataframe_cache_request.cache.clear()

    response = client.post("/api/explore/cache-status", json=body)

    # The durable generation is inspected first, so warm process caches never
    # hide a corrupt selected generation.
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_explore_cache_status_rejects_hard_linked_generation_artifact(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    started = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, started["job_id"])["status"] == "completed"

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)
    family_dir = store._family_dir(spec.family_key)
    pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
    data_path = family_dir / "generations" / pointer["generation_id"] / "data.parquet"
    external = tmp_path / "externally-linked.parquet"
    external.write_bytes(data_path.read_bytes())
    data_path.unlink()
    try:
        os.link(external, data_path)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")

    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()
    response = client.post("/api/explore/cache-status", json=body)

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_explore_generation_validation_rejects_windows_reparse_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stat
    from types import SimpleNamespace

    from haute._explore_cache import ExplorePersistentCacheStore

    generation = tmp_path / "generation"
    generation.mkdir()
    metadata = generation / "meta.json"
    data = generation / "data.parquet"
    metadata.write_text("{}", encoding="utf-8")
    data.write_bytes(b"parquet")
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == generation:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_nlink=1,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(ValueError, match="non-regular"):
        ExplorePersistentCacheStore._validate_generation_files(generation, metadata, data)


@pytest.mark.parametrize("case", ["artifact_nonregular", "generation_escape", "artifact_escape"])
def test_explore_generation_validation_rejects_escaped_or_nonregular_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    import stat
    from types import SimpleNamespace

    from haute._explore_cache import ExplorePersistentCacheStore

    generation = tmp_path / "generation"
    generation.mkdir()
    metadata = generation / "meta.json"
    data = generation / "data.parquet"
    metadata.write_text("{}", encoding="utf-8")
    data.write_bytes(b"parquet")
    original_lstat = Path.lstat
    original_resolve = Path.resolve

    if case == "artifact_nonregular":

        def nonregular_lstat(path: Path):
            if path == data:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_nlink=1, st_file_attributes=0)
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", nonregular_lstat)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        escaped = outside / "artifact"
        escaped.write_bytes(b"outside")
        escaped_generation = outside / "generation"
        escaped_generation.mkdir()

        def escaped_resolve(path: Path, *, strict: bool = False) -> Path:
            if case == "generation_escape" and path == generation:
                return escaped_generation
            if case == "artifact_escape" and path == data:
                return escaped
            return original_resolve(path, strict=strict)

        monkeypatch.setattr(Path, "resolve", escaped_resolve)

    with pytest.raises(ValueError, match="non-regular|escapes"):
        ExplorePersistentCacheStore._validate_generation_files(generation, metadata, data)


@pytest.mark.parametrize(
    "corruption",
    [
        "family",
        "report_identity",
        "artifact_shape",
        "schema",
        "artifact_missing",
        "columns_not_map",
        "columns_nonstring",
        "digest",
    ],
)
def test_explore_cache_status_rejects_inconsistent_generation_metadata(
    client: TestClient,
    tmp_path: Path,
    corruption: str,
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    started = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, started["job_id"])["status"] == "completed"

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)
    family_dir = store._family_dir(spec.family_key)
    pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
    metadata_path = family_dir / "generations" / pointer["generation_id"] / "meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if corruption == "family":
        metadata["family"] = ["explore", "wrong.py", "explore", "live"]
    elif corruption == "report_identity":
        metadata["report"]["node_id"] = "another_explore"
    else:
        if corruption == "artifact_shape":
            metadata["artifact"]["row_count"] += 1
        elif corruption == "schema":
            metadata["schema_version"] = -1
        elif corruption == "artifact_missing":
            metadata["artifact"] = None
        elif corruption == "columns_not_map":
            metadata["artifact"]["columns"] = []
        elif corruption == "columns_nonstring":
            metadata["artifact"]["columns"] = {"quote_id": 1}
        else:
            metadata["artifact"]["digest"] = "0" * 64
    atomic_write_text(metadata_path, json.dumps(metadata))
    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()

    response = client.post("/api/explore/cache-status", json=body)

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_failed_durable_restore_removes_partial_process_cache_artifact(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _explore_cache as persistent_cache_module
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    started = client.post("/api/explore/run", json=body).json()
    assert _poll_explore(client, started["job_id"])["status"] == "completed"
    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    dataframe_key = spec.dataframe_cache_request.keys_by_node[spec.node_id]
    spec.dataframe_cache_request.cache.clear()
    _explore_service._report_cache.clear()
    target = spec.dataframe_cache_request.cache.path_for_key(dataframe_key)
    assert not target.exists()

    def leave_partial_copy_then_fail(_source: Path, destination: Path) -> None:
        Path(destination).write_bytes(b"partial parquet")
        raise OSError("forced durable restore copy failure")

    monkeypatch.setattr(persistent_cache_module.shutil, "copy2", leave_partial_copy_then_fail)

    response = client.post("/api/explore/cache-status", json=body)

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert not target.exists()


@pytest.mark.parametrize("operation", ["commit_publication", "discard_publication"])
def test_persistent_cache_rejects_publication_paths_outside_the_family(
    tmp_path: Path,
    operation: str,
) -> None:
    import uuid

    from haute._explore_cache import (
        ExplorePersistentCachePublication,
        ExplorePersistentCacheStore,
    )

    store = ExplorePersistentCacheStore(tmp_path)
    family_key = ("explore", str(tmp_path / "pipeline.py"), "explore", "live")
    generation_id = str(uuid.uuid4())
    publication = ExplorePersistentCachePublication(
        family_key=family_key,
        generation_id=generation_id,
        staging_path=tmp_path / "outside-staging",
        final_path=tmp_path / "outside-final",
    )

    with pytest.raises(ValueError, match="publication paths"):
        getattr(store, operation)(publication)


def test_explore_cache_status_reports_stale_durable_generation_despite_warm_process_caches(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    first_body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    second_prep = "df = source.with_columns((pl.col('premium') * 3).alias('triple_premium'))"
    second_body = {
        "graph": _explore_graph(str(path), prep_code=second_prep),
        "node_id": "explore",
        "source": "live",
    }

    first_started = client.post("/api/explore/run", json=first_body).json()
    assert _poll_explore(client, first_started["job_id"])["status"] == "completed"
    second_started = client.post("/api/explore/run", json=second_body).json()
    assert _poll_explore(client, second_started["job_id"])["status"] == "completed"

    # The first identity's report and dataframe are still warm in the process
    # caches, but the selected durable generation now belongs to the second
    # identity: the durable inspection must win and report stale.
    snapshot = client.post("/api/explore/cache-status", json=first_body)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "stale"


def test_explore_cache_status_falls_back_to_process_caches_without_durable_generation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    import shutil

    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    started = client.post("/api/explore/run", json=body).json()
    completed = _poll_explore(client, started["job_id"])
    assert completed["status"] == "completed"

    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    store = ExplorePersistentCacheStore(spec.project_root)
    family_dir = tmp_path / ".haute_cache" / "explore" / store.family_digest(spec.family_key)
    assert family_dir == store._family_dir(spec.family_key)
    shutil.rmtree(family_dir)

    snapshot = client.post("/api/explore/cache-status", json=body)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "current"
    assert snapshot.json()["result"] == completed["result"]

    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()
    snapshot = client.post("/api/explore/cache-status", json=body)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "missing"


def test_explore_downstream_edits_do_not_invalidate_analysis_dataframe_cache(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    first_body = {
        "graph": _explore_graph(str(path), extra_downstream_label="first"),
        "node_id": "explore",
        "source": "live",
    }
    second_body = {
        "graph": _explore_graph(str(path), extra_downstream_label="renamed"),
        "node_id": "explore",
        "source": "live",
    }

    first = client.post("/api/explore/run", json=first_body).json()
    first_status = _poll_explore(client, first["job_id"])
    assert first_status["status"] == "completed"
    first_key = _explore_service.prepare_spec(
        ExploreRunRequest.model_validate(first_body)
    ).dataframe_cache_key

    second = client.post("/api/explore/run", json=second_body).json()
    second_status = (
        {"result": second["result"], "status": second["status"]}
        if second["status"] == "completed"
        else _poll_explore(client, second["job_id"])
    )

    assert second_status["status"] == "completed"
    assert (
        _explore_service.prepare_spec(
            ExploreRunRequest.model_validate(second_body)
        ).dataframe_cache_key
        == first_key
    )


def test_explore_display_config_does_not_invalidate_analysis_dataframe_cache(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    data_config = {"code": "df = df.select(pl.all())"}
    first_body = {
        "graph": _explore_graph(str(path), explore_config=data_config),
        "node_id": "explore",
        "source": "live",
    }
    second_body = {
        "graph": _explore_graph(
            str(path),
            explore_config={
                **data_config,
                "overview": {"dataset_snapshot": True, "schema": True},
                "pivots": [{"id": "pivot_1"}],
                "charts": [{"id": "chart_1", "enabled": True}],
            },
        ),
        "node_id": "explore",
        "source": "live",
    }

    first = client.post("/api/explore/run", json=first_body).json()
    first_status = _poll_explore(client, first["job_id"])
    assert first_status["status"] == "completed"
    first_key = _explore_service.prepare_spec(
        ExploreRunRequest.model_validate(first_body)
    ).dataframe_cache_key

    second_response = client.post("/api/explore/run", json=second_body)

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["status"] == "completed"
    assert second["cached"] is True
    assert second["result"]["dataframe_cache_key"] == first_key
    assert (
        _explore_service.prepare_spec(
            ExploreRunRequest.model_validate(second_body)
        ).dataframe_cache_key
        == first_key
    )


def test_explore_reuses_typed_report_cache_without_reexecuting_sources(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from haute._polars_utils import read_parquet_metadata
    from haute.routes._explore_service import EXPLORE_CACHE_VERSION
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreCacheReport, ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {
        "graph": _explore_graph(str(path)),
        "node_id": "explore",
        "source": "live",
    }
    spec = _explore_service.prepare_spec(ExploreRunRequest.model_validate(body))
    assert EXPLORE_CACHE_VERSION == 5
    assert spec.report_cache_key.startswith("explore:v5:")

    dataframe_key = spec.dataframe_cache_request.keys_by_node["explore"]
    dataframe_cache = spec.dataframe_cache_request.cache
    with dataframe_cache.materialization_lock(dataframe_key):
        artifact_path = dataframe_cache.path_for_key(dataframe_key)
        pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(artifact_path)
        dataframe_cache.store_artifact(
            dataframe_key,
            artifact_path,
            read_parquet_metadata(artifact_path),
        )

    _explore_service._report_cache.put(
        spec.report_cache_key,
        ExploreCacheReport.model_validate(
            {
                "status": "ok",
                "node_id": "explore",
                "upstream_node_id": spec.upstream_node_id,
                "source": "live",
                "dataframe_cache_key": spec.dataframe_cache_key,
                "row_count": 1,
                "column_count": 2,
                "columns": [
                    {
                        "name": "premium",
                        "dtype": "Int64",
                        "kind": "Numeric",
                        "null_count": 0,
                        "distinct_count": 1,
                    }
                ],
                "generated_at": 123.0,
            }
        ),
    )

    response = client.post("/api/explore/run", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["cached"] is True
    assert payload["result"]["dataframe_cache_key"] == spec.dataframe_cache_key
    assert payload["result"]["overview_summary"] == {
        "categorical_summary": [],
        "data_quality": {
            "issue_count": 0,
            "issues": [],
            "duplicate_row_count": None,
            "duplicate_ratio": None,
        },
    }


def test_explore_code_config_change_invalidates_analysis_dataframe_cache(
    tmp_path: Path,
) -> None:
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)
    first_body = {
        "graph": _explore_graph(str(path), explore_config={"code": "df = df"}),
        "node_id": "explore",
        "source": "live",
    }
    second_body = {
        "graph": _explore_graph(
            str(path),
            explore_config={"code": "df = df.filter(pl.col('premium') > 10)"},
        ),
        "node_id": "explore",
        "source": "live",
    }

    assert (
        _explore_service.prepare_spec(
            ExploreRunRequest.model_validate(first_body)
        ).dataframe_cache_key
        != _explore_service.prepare_spec(
            ExploreRunRequest.model_validate(second_body)
        ).dataframe_cache_key
    )


@pytest.mark.parametrize("change", ["preamble", "source_file", "input_source"])
def test_explore_analysis_identity_includes_runtime_inputs(
    tmp_path: Path,
    change: str,
) -> None:
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    first_graph = _explore_graph(str(path))
    second_graph = _explore_graph(str(path))
    first_source = "live"
    second_source = "live"
    if change == "preamble":
        first_graph["preamble"] = "import polars as pl\nIDENTITY_MARKER = 1"
        second_graph["preamble"] = "import polars as pl\nIDENTITY_MARKER = 2"
    elif change == "source_file":
        first_pipeline = tmp_path / "first_pipeline.py"
        second_pipeline = tmp_path / "second_pipeline.py"
        first_pipeline.write_text("# first\n", encoding="utf-8")
        second_pipeline.write_text("# second\n", encoding="utf-8")
        first_graph["source_file"] = str(first_pipeline)
        second_graph["source_file"] = str(second_pipeline)
    else:
        second_source = "batch"

    first = _explore_service.prepare_spec(
        ExploreRunRequest.model_validate(
            {"graph": first_graph, "node_id": "explore", "source": first_source}
        )
    )
    second = _explore_service.prepare_spec(
        ExploreRunRequest.model_validate(
            {"graph": second_graph, "node_id": "explore", "source": second_source}
        )
    )

    assert first.dataframe_cache_key != second.dataframe_cache_key
    assert first.report_cache_key != second.report_cache_key


def test_explore_runs_for_different_sources_do_not_supersede_each_other(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    graph = _explore_graph(str(path))

    live = client.post(
        "/api/explore/run",
        json={"graph": graph, "node_id": "explore", "source": "live"},
    ).json()
    assert _poll_explore(client, live["job_id"])["status"] == "completed"
    batch = client.post(
        "/api/explore/run",
        json={"graph": graph, "node_id": "explore", "source": "batch"},
    ).json()
    assert _poll_explore(client, batch["job_id"])["status"] == "completed"


def test_explore_rejects_non_explore_node_before_execution(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    graph = _explore_graph(str(path))

    response = client.post(
        "/api/explore/run",
        json={"graph": graph, "node_id": "prep", "source": "live"},
    )

    assert response.status_code == 400
    assert "is not a explore node" in response.text


def test_explore_cancel_stops_in_flight_job(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Cancel must actually interrupt a running materialisation, not just flip status."""
    from haute.routes import _explore_service as service_mod

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a", "b"], "premium": [10, 20]}).write_parquet(path)

    # Gate the parent dispatch seam; child-local monkeypatches are not inherited
    # by the spawned worker process.
    gate = threading.Event()
    entered = threading.Event()
    original_dispatch = service_mod.run_isolated_worker

    def gated_dispatch(*args, **kwargs):
        entered.set()
        assert gate.wait(timeout=5.0)
        return original_dispatch(*args, **kwargs)

    monkeypatch.setattr(service_mod, "run_isolated_worker", gated_dispatch)

    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    started = client.post("/api/explore/run", json=body).json()
    assert started["status"] == "started"
    assert entered.wait(timeout=5.0)

    cancel_response = client.post(f"/api/explore/cancel/{started['job_id']}")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "running"

    # Release the gate so the worker thread exits and the fixture can clean up.
    gate.set()
    final = _poll_explore(client, started["job_id"], timeout=5.0)
    assert final["status"] == "cancelled"
    assert final["terminal_reason"] == "cancelled"

    worker_name = f"haute-explore-{started['job_id']}"
    for thread in threading.enumerate():
        if thread.name == worker_name:
            thread.join(timeout=5.0)
            assert not thread.is_alive()
            break


def test_explore_supersedes_an_in_flight_job(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A newer request for the same Explore family supersedes the older job."""
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    entered = threading.Event()
    release = threading.Event()
    from haute.routes import _explore_service as service_mod

    original_dispatch = service_mod.run_isolated_worker
    dispatches = 0

    def blocked_dispatch(*args, **kwargs):
        nonlocal dispatches
        dispatches += 1
        if dispatches == 1:
            entered.set()
            assert release.wait(timeout=5.0)
        return original_dispatch(*args, **kwargs)

    monkeypatch.setattr(service_mod, "run_isolated_worker", blocked_dispatch)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    first = client.post("/api/explore/run", json=body).json()
    assert entered.wait(timeout=5.0)
    second = client.post("/api/explore/run", json=body).json()

    assert first["status"] == "started"
    assert second["status"] == "started"
    first_status = client.get(f"/api/explore/status/{first['job_id']}")
    assert first_status.status_code == 200
    assert first_status.json()["status"] == "superseded"

    release.set()
    assert _poll_explore(client, second["job_id"], timeout=5.0)["status"] in _TERMINAL_JOB_STATUSES
    for job_id in (first["job_id"], second["job_id"]):
        worker_name = f"haute-explore-{job_id}"
        for thread in threading.enumerate():
            if thread.name == worker_name:
                thread.join(timeout=5.0)
                assert not thread.is_alive()
                break


def test_superseded_explore_job_cannot_select_its_prepared_generation(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer job wins even if the older job already prepared durable output."""

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    first_prepared = threading.Event()
    release_first = threading.Event()
    timeout_seconds = 30.0
    publications = []
    dispatch_failures: list[BaseException] = []
    publications_lock = threading.Lock()
    from haute._execution_context import ExecutionAdmission, ExecutionContext
    from haute.routes import _explore_service as service_mod

    original_dispatch = service_mod.run_isolated_worker

    def create_unreserved_context(*, operation, profile, job_id, cancellation_token):
        # This test exercises publication ordering, not host-specific admission
        # capacity. Give both workers deterministic admitted headroom so the
        # same latest-wins race is exercised on small and large CI hosts.
        headroom = 1 << 40
        admission = ExecutionAdmission(
            operation=operation,
            profile=profile,
            memory_limit_bytes=headroom,
            rss_at_admission_bytes=0,
            rss_limit_bytes=headroom,
            headroom_bytes=headroom,
            config_key="test_explore_publication_supersession",
        )
        return ExecutionContext(
            operation=operation,
            profile=profile,
            job_id=job_id,
            cancellation_token=cancellation_token,
            memory_limit_bytes=headroom,
            memory_baseline_bytes=0,
            rss_limit_bytes=headroom,
            admission=admission,
        )

    def prepare_with_first_job_gated(*args, **kwargs):
        publication = args[2]
        with publications_lock:
            publications.append(publication)
            call_number = len(publications)
        try:
            outcome = original_dispatch(*args, **kwargs)
        except BaseException as exc:
            if call_number == 1:
                dispatch_failures.append(exc)
                first_prepared.set()
            raise
        if call_number == 1:
            # Gate after the child has prepared and validated its private
            # staging generation, immediately before the parent can publish it.
            try:
                assert publication.staging_path.exists()
            except BaseException as exc:
                dispatch_failures.append(exc)
                first_prepared.set()
                raise
            first_prepared.set()
            assert release_first.wait(timeout=timeout_seconds)
        return outcome

    monkeypatch.setattr(
        service_mod,
        "run_isolated_worker",
        prepare_with_first_job_gated,
    )
    monkeypatch.setattr(
        service_mod,
        "create_admitted_execution_context",
        create_unreserved_context,
    )

    first = client.post("/api/explore/run", json=body).json()
    assert first_prepared.wait(timeout=timeout_seconds)
    assert not dispatch_failures, repr(dispatch_failures[0])
    second = client.post("/api/explore/run", json=body).json()
    try:
        second_completed = _poll_explore(client, second["job_id"], timeout=timeout_seconds)
        assert second_completed["status"] == "completed"
    finally:
        release_first.set()
    first_completed = _poll_explore(client, first["job_id"], timeout=timeout_seconds)
    assert first_completed["status"] == "superseded"
    for job_id in (first["job_id"], second["job_id"]):
        worker_name = f"haute-explore-{job_id}"
        for thread in threading.enumerate():
            if thread.name == worker_name:
                thread.join(timeout=timeout_seconds)
                assert not thread.is_alive()
                break

    assert len(publications) == 2
    first_publication, second_publication = publications
    assert not first_publication.staging_path.exists()
    assert not first_publication.final_path.exists()
    assert second_publication.final_path.exists()
    pointer_path = second_publication.final_path.parent.parent / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["generation_id"] == second_publication.generation_id


def test_explore_rejects_node_without_exactly_one_parent(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    graph = _explore_graph(str(path))
    graph["edges"] = [edge for edge in graph["edges"] if edge["target"] != "explore"]

    response = client.post(
        "/api/explore/run",
        json={"graph": graph, "node_id": "explore", "source": "live"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Explore node 'explore' must have exactly one upstream input (found 0)."
    )


def test_explore_status_unknown_job_is_404(client: TestClient) -> None:
    response = client.get("/api/explore/status/not-a-job")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("failure_kind", "expected_status"),
    [
        ("admission", "memory_limited"),
        ("memory", "memory_limited"),
        ("public_contract", "contract_error"),
        ("contract", "contract_error"),
        ("unexpected", "error"),
    ],
)
def test_explore_worker_maps_failures_to_typed_terminal_statuses(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_status: str,
) -> None:
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionMemoryLimitExceededError, ExecutionProfile
    from haute.errors import ContractMismatchError, GroupByExecutionUnsupportedError
    from haute.routes import _explore_service as service_module
    from haute.routes.explore import _explore_service

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}

    if failure_kind == "admission":
        failure: BaseException = ExecutionAdmissionError(
            "explore_cache",
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            memory_limit_bytes=1,
            rss_at_admission_bytes=2,
            reason="forced admission failure",
        )

        def fail_admission(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(service_module, "create_admitted_execution_context", fail_admission)
    else:
        if failure_kind == "memory":
            failure = ExecutionMemoryLimitExceededError(
                "explore_cache",
                rss_bytes=2,
                limit_bytes=1,
            )
        elif failure_kind == "public_contract":
            failure = GroupByExecutionUnsupportedError(
                "Group-by exceeds admitted Explore headroom",
                node_id="prep",
                operator="group_by",
                profile="explore_analysis",
                reason_code="estimated_peak_exceeds_headroom",
                remediation="Reduce the input or increase available memory.",
                estimated_peak_bytes=2,
                headroom_bytes=1,
            )
        elif failure_kind == "contract":
            failure = ContractMismatchError("forced Explore contract mismatch", node_id="prep")
        else:
            failure = RuntimeError("forced unexpected Explore failure")

        def fail_materialisation(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(
            service_module,
            "run_isolated_worker",
            fail_materialisation,
        )

    started = client.post("/api/explore/run", json=body)

    assert started.status_code == 200
    final = _poll_explore(client, started.json()["job_id"])
    assert final["status"] == expected_status
    assert final["terminal_reason"] == expected_status
    assert final["result"] is None
    if failure_kind == "public_contract":
        stored = _explore_service._store.require_job(started.json()["job_id"])
        assert stored["error_code"] == "group_by_execution_unsupported"
    elif failure_kind == "unexpected":
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        stored = _explore_service._store.require_job(started.json()["job_id"])
        assert final["message"] == _INTERNAL_ERROR_DETAIL
        assert stored["error"] == _INTERNAL_ERROR_DETAIL
        assert "forced unexpected Explore failure" not in str(final)


def test_explore_remote_worker_failure_is_sanitized(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._worker_isolation import IsolatedWorkerRemoteError
    from haute.routes import _explore_service as service_module
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
    from haute.routes.explore import _explore_service

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    secret = "database password appeared in the child exception"

    def fail_worker(*_args, **_kwargs):
        raise IsolatedWorkerRemoteError(
            remote_type="RuntimeError",
            remote_message=secret,
            remote_traceback=f"traceback containing {secret}",
        )

    monkeypatch.setattr(service_module, "run_isolated_worker", fail_worker)

    started = client.post("/api/explore/run", json=body)
    final = _poll_explore(client, started.json()["job_id"])
    stored = _explore_service._store.require_job(started.json()["job_id"])

    assert final["status"] == "error"
    assert final["terminal_reason"] == "error"
    assert final["message"] == _INTERNAL_ERROR_DETAIL
    assert stored["error"] == _INTERNAL_ERROR_DETAIL
    assert secret not in str(final)
    assert secret not in str(stored)


def test_explore_child_public_contract_envelope_preserves_stable_payload(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._worker_isolation import IsolatedWorkerRemoteError
    from haute.errors import GroupByExecutionUnsupportedError
    from haute.routes import _explore_service as service_module
    from haute.routes.explore import _explore_service

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    failure = GroupByExecutionUnsupportedError(
        "Group-by exceeds admitted Explore headroom",
        node_id="prep",
        operator="group_by",
        profile="explore_analysis",
        reason_code="estimated_peak_exceeds_headroom",
        remediation="Reduce the input or increase available memory.",
        estimated_peak_bytes=2,
        headroom_bytes=1,
    )

    def fail_child_materialisation(*_args, **_kwargs):
        raise failure

    def run_child_synchronously(function, *args, **_kwargs):
        try:
            return function(*args)
        except BaseException as exc:
            raise IsolatedWorkerRemoteError(
                remote_type=type(exc).__name__,
                remote_message=str(exc),
                remote_traceback="simulated child traceback",
            ) from None

    monkeypatch.setattr(
        service_module,
        "_materialise_and_summarise_worker",
        fail_child_materialisation,
    )
    monkeypatch.setattr(service_module, "run_isolated_worker", run_child_synchronously)

    started = client.post("/api/explore/run", json=body)
    assert started.status_code == 200
    final = _poll_explore(client, started.json()["job_id"])

    assert final["status"] == "contract_error"
    stored = _explore_service._store.require_job(started.json()["job_id"])
    assert stored["error_code"] == "group_by_execution_unsupported"
    assert stored["error_detail"]["node_id"] == "prep"


def test_explore_worker_envelope_rejects_unknown_failure_kind(tmp_path: Path) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes import _explore_service as service_module

    family_key = ("explore", str(tmp_path / "pipeline.py"), "explore", "live")
    publication = ExplorePersistentCacheStore(tmp_path).new_publication(family_key)
    outcome = service_module._ExploreWorkerOutcome(
        failure_kind="unknown",
        detail="corrupt worker outcome",
    )

    with pytest.raises(RuntimeError, match="unknown failure kind"):
        service_module._validated_explore_worker_success(
            outcome,
            expected_publication=publication,
        )


def _minimal_explore_report(*, dataframe_cache_key: str = "dataframe-key"):
    from haute.schemas import ExploreCacheReport

    return ExploreCacheReport(
        node_id="explore",
        upstream_node_id="source",
        source="live",
        dataframe_cache_key=dataframe_cache_key,
        row_count=1,
        column_count=1,
        columns=[],
        generated_at=1.0,
    )


def test_persistent_cache_publication_validates_metadata_and_artifact_integrity(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from haute._dataframe_execution_cache import (
        DataFrameExecutionCacheEntry,
        DataFrameExecutionCacheKey,
    )
    from haute._explore_cache import (
        ExplorePersistentCacheSnapshot,
        ExplorePersistentCacheStore,
    )
    from haute._polars_utils import read_parquet_metadata

    family = ("explore", str(tmp_path / "pipeline.py"), "explore", "live")
    report = _minimal_explore_report()
    source = tmp_path / "source.parquet"
    pl.DataFrame({"value": [1]}).write_parquet(source)
    metadata = read_parquet_metadata(source)
    key = DataFrameExecutionCacheKey(
        cache_key="dataframe-key",
        namespace="test",
        node_id="explore",
        lineage_fingerprint="lineage",
        source="live",
        profile="test",
        input_fingerprint="input",
    )
    entry = DataFrameExecutionCacheEntry(
        key=key,
        path=source,
        row_count=metadata["row_count"],
        column_count=metadata["column_count"],
        columns=metadata["columns"],
        size_bytes=metadata["size_bytes"],
        uncompressed_size_bytes=metadata["uncompressed_size_bytes"],
    )
    store = ExplorePersistentCacheStore(tmp_path)

    for corruption in ("schema", "identity", "artifact", "columns", "column_type", "digest"):
        publication = store.prepare_publication(
            family,
            report_cache_key="report-key",
            report=report,
            entry=entry,
            generation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        meta_path = publication.staging_path / "meta.json"
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        if corruption == "schema":
            payload["schema_version"] = 0
        elif corruption == "identity":
            payload["family"] = ["explore", "other", "explore", "live"]
        elif corruption == "artifact":
            payload["artifact"] = None
        elif corruption == "columns":
            payload["artifact"]["columns"] = []
        elif corruption == "column_type":
            payload["artifact"]["columns"] = {"value": 1}
        else:
            payload["artifact"]["digest"] = "0" * 64
        atomic_write_text(meta_path, json.dumps(payload))
        with pytest.raises(ValueError):
            store.validate_publication(publication, report_cache_key="report-key", report=report)
        store.discard_publication(publication)

    with pytest.raises(ValueError, match="invalid Explore cache family"):
        store.new_publication(("not-explore", "pipeline", "node", "live"))

    publication = store.prepare_publication(
        family,
        report_cache_key="report-key",
        report=report,
        entry=entry,
        generation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    artifact = store.validate_publication(publication, report_cache_key="report-key", report=report)

    class Cache:
        def materialization_lock(self, _key):
            from contextlib import nullcontext

            return nullcontext()

        def get(self, _key):
            return None

        def path_for_key(self, _key):
            return tmp_path / "restored.parquet"

        def store_artifact(self, *_args):
            return SimpleNamespace(restored=True)

    wrong_artifact = {**artifact, "digest": "0" * 64}
    snapshot = ExplorePersistentCacheSnapshot(
        state="current",
        generation_id=publication.generation_id,
        report_cache_key="report-key",
        report=report,
        data_path=publication.staging_path / "data.parquet",
        artifact_metadata=wrong_artifact,
    )
    request = SimpleNamespace(cache=Cache(), keys_by_node={"explore": key})
    with pytest.raises(Exception, match="digest"):
        store.restore(snapshot, request, node_id="explore")


def test_prepare_explore_spec_resolves_relative_pipeline_source_from_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from haute.routes import _explore_service as service_module
    from haute.schemas import ExploreRunRequest

    graph = _explore_graph(str(tmp_path / "quotes.parquet"))
    graph["source_file"] = "pipelines/rating.py"
    body = ExploreRunRequest.model_validate(
        {"graph": graph, "node_id": "explore", "source": "live"}
    )
    dataframe_request = SimpleNamespace(
        keys_by_node={"explore": SimpleNamespace(cache_key="dataframe-key")}
    )
    monkeypatch.setattr(
        service_module.execution_facade,
        "dataframe_graph_input_fingerprint",
        lambda *_args, **_kwargs: "input-fingerprint",
    )
    monkeypatch.setattr(
        service_module.execution_facade,
        "build_dataframe_execution_cache_request",
        lambda *_args, **_kwargs: dataframe_request,
    )
    monkeypatch.setattr(service_module, "_infer_project_root", lambda **_kwargs: tmp_path)

    spec = service_module._prepare_explore_spec(body)

    assert spec.project_root == tmp_path
    assert spec.family_key[1] == str((tmp_path / "pipelines" / "rating.py").resolve())

    filesystem_root = Path(tmp_path.anchor)
    rooted_graph = _explore_graph(str(tmp_path / "quotes.parquet"))
    rooted_graph["source_file"] = "pipelines/rooted.py"
    rooted_body = ExploreRunRequest.model_validate(
        {"graph": rooted_graph, "node_id": "explore", "source": "live"}
    )
    monkeypatch.setattr(
        service_module,
        "_infer_project_root",
        lambda **_kwargs: filesystem_root,
    )

    rooted_spec = service_module._prepare_explore_spec(rooted_body)

    assert rooted_spec.project_root == filesystem_root
    assert rooted_spec.family_key[1] == str((filesystem_root / "pipelines" / "rooted.py").resolve())


def test_cancel_completed_explore_job_is_an_idempotent_read() -> None:
    from haute.routes.explore import _explore_service, _store

    job_id = _store.create_job(
        {
            "kind": "explore",
            "status": "completed",
            "progress": 1.0,
            "message": "Explore cache ready",
        }
    )

    result = _explore_service.cancel(job_id)

    assert result.status == "completed"
    assert result.progress == 1.0


def test_materialise_explore_worker_builds_report_and_releases_child_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionContext, ExecutionProfile
    from haute.routes import _explore_service as service_module
    from haute.schemas import ExploreOverviewSummary, ExploreRunRequest

    body = ExploreRunRequest.model_validate(
        {
            "graph": _explore_graph(str(tmp_path / "quotes.parquet")),
            "node_id": "explore",
            "source": "live",
        }
    )
    spec = SimpleNamespace(
        node_id="explore",
        upstream_node_id="source",
        dataframe_cache_key="dataframe-key",
        dataframe_cache_request=object(),
    )
    releases: list[bool] = []
    context = ExecutionContext(
        operation="explore_cache",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_limit_bytes=1 << 40,
        admission_release=lambda: releases.append(True),
    )
    frame = pl.DataFrame({"value": [1]}).lazy()
    monkeypatch.setattr(
        service_module,
        "create_isolated_execution_context",
        lambda _budget: context,
    )
    monkeypatch.setattr(
        service_module.execution_facade,
        "execute_lazy_graph",
        lambda *_args, **_kwargs: ({"explore": frame}, [], {}, {}),
    )
    monkeypatch.setattr(
        service_module,
        "_build_frame_stats",
        lambda *_args, **_kwargs: service_module.ExploreFrameStats(
            row_count=1,
            columns=[],
            overview_summary=ExploreOverviewSummary(),
        ),
    )
    budget = IsolatedExecutionBudget(
        operation="explore_cache",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_limit_bytes=1 << 40,
        config_key="test",
        budget_policy="fixed_default",
    )

    report = service_module._materialise_and_summarise_worker(body, spec, budget)

    assert report.dataframe_cache_key == "dataframe-key"
    assert report.row_count == report.column_count == 1
    assert report.execution_metrics is not None
    assert releases == [True]


def test_materialise_explore_worker_releases_admission_when_target_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionContext, ExecutionProfile
    from haute.routes import _explore_service as service_module
    from haute.schemas import ExploreRunRequest

    body = ExploreRunRequest.model_validate(
        {
            "graph": _explore_graph(str(tmp_path / "quotes.parquet")),
            "node_id": "explore",
            "source": "live",
        }
    )
    releases: list[bool] = []
    context = ExecutionContext(
        operation="explore_cache",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_limit_bytes=1024,
        admission_release=lambda: releases.append(True),
    )
    monkeypatch.setattr(
        service_module,
        "create_isolated_execution_context",
        lambda _budget: context,
    )
    monkeypatch.setattr(
        service_module.execution_facade,
        "execute_lazy_graph",
        lambda *_args, **_kwargs: ({}, [], {}, {}),
    )
    budget = IsolatedExecutionBudget(
        operation="explore_cache",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_limit_bytes=1024,
        config_key="test",
        budget_policy="fixed_default",
    )

    with pytest.raises(ValueError, match="No data arrived"):
        service_module._materialise_and_summarise_worker(
            body,
            SimpleNamespace(
                node_id="explore",
                upstream_node_id="source",
                dataframe_cache_key="dataframe-key",
                dataframe_cache_request=object(),
            ),
            budget,
        )

    assert releases == [True]


@pytest.mark.parametrize(
    "case",
    ["family", "missing_entry", "publication", "success"],
)
def test_explore_worker_validates_every_parent_owned_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import uuid4

    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionProfile
    from haute._explore_cache import ExplorePersistentCachePublication
    from haute.routes import _explore_service as service_module
    from haute.schemas import ExploreRunRequest

    body = ExploreRunRequest.model_validate(
        {
            "graph": _explore_graph(str(tmp_path / "quotes.parquet")),
            "node_id": "explore",
            "source": "live",
        }
    )
    family = ("explore", str(tmp_path / "pipeline.py"), "explore", "live")
    generation_id = str(uuid4())
    publication = ExplorePersistentCachePublication(
        family_key=family,
        generation_id=generation_id,
        staging_path=tmp_path / f".staging-{generation_id}",
        final_path=tmp_path / "generations" / generation_id,
    )
    key = object()
    cache = Mock()
    cache.get.return_value = None if case == "missing_entry" else object()
    spec = SimpleNamespace(
        family_key=family,
        node_id="explore",
        report_cache_key="report-key",
        project_root=tmp_path,
        dataframe_cache_request=SimpleNamespace(
            keys_by_node={"explore": key},
            cache=cache,
        ),
    )
    store = Mock()
    store.prepare_publication.return_value = (
        ExplorePersistentCachePublication(
            family_key=family,
            generation_id=str(uuid4()),
            staging_path=tmp_path / ".unexpected",
            final_path=tmp_path / "unexpected",
        )
        if case == "publication"
        else publication
    )
    report = _minimal_explore_report()
    monkeypatch.setattr(service_module, "_prepare_explore_spec", lambda _body: spec)
    monkeypatch.setattr(
        service_module,
        "ExplorePersistentCacheStore",
        lambda _root: store,
    )
    monkeypatch.setattr(
        service_module,
        "_materialise_and_summarise_worker",
        lambda *_args: report,
    )
    budget = IsolatedExecutionBudget(
        operation="explore_cache",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_limit_bytes=1024,
        config_key="test",
        budget_policy="fixed_default",
    )
    selected = (
        ExplorePersistentCachePublication(
            family_key=("explore", "wrong.py", "explore", "live"),
            generation_id=generation_id,
            staging_path=publication.staging_path,
            final_path=publication.final_path,
        )
        if case == "family"
        else publication
    )

    if case == "family":
        with pytest.raises(ValueError, match="publication family"):
            service_module._run_explore_worker(body, selected, budget)
    elif case == "missing_entry":
        with pytest.raises(RuntimeError, match="did not materialise"):
            service_module._run_explore_worker(body, selected, budget)
    elif case == "publication":
        with pytest.raises(RuntimeError, match="unexpected publication path"):
            service_module._run_explore_worker(body, selected, budget)
    else:
        outcome = service_module._run_explore_worker(body, selected, budget)
        assert outcome.report is report
        assert outcome.publication == publication
        store.validate_publication.assert_called_once_with(
            publication,
            report_cache_key="report-key",
            report=report,
        )


@pytest.mark.parametrize(
    ("outcome_factory", "message"),
    [
        (lambda service, report, publication: object(), "invalid result envelope"),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                report=report,
                failure_kind="contract",
                detail="failed",
            ),
            "mixed success and failure",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                failure_kind="contract"
            ),
            "omitted its detail",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                failure_kind="public_contract",
                detail="failed",
                payload={"error_code": "bad"},
            ),
            "invalid public-contract payload",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                failure_kind="memory",
                detail="failed",
                payload={"error_code": "bad"},
            ),
            "invalid memory payload",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                failure_kind="contract",
                detail="failed",
                payload={},
            ),
            "unexpected payload",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                report=report,
                publication=publication,
                detail="stale detail",
            ),
            "success carried failure fields",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(
                publication=publication
            ),
            "omitted its validated report",
        ),
        (
            lambda service, report, publication: service._ExploreWorkerOutcome(report=report),
            "unexpected publication",
        ),
    ],
)
def test_explore_worker_success_envelope_is_fail_closed(
    tmp_path: Path,
    outcome_factory,
    message: str,
) -> None:
    from uuid import uuid4

    from haute._explore_cache import ExplorePersistentCachePublication
    from haute.routes import _explore_service as service_module

    family = ("explore", str(tmp_path / "pipeline.py"), "explore", "live")
    generation_id = str(uuid4())
    publication = ExplorePersistentCachePublication(
        family_key=family,
        generation_id=generation_id,
        staging_path=tmp_path / f".staging-{generation_id}",
        final_path=tmp_path / "generations" / generation_id,
    )
    outcome = outcome_factory(service_module, _minimal_explore_report(), publication)

    with pytest.raises(RuntimeError, match=message):
        service_module._validated_explore_worker_success(
            outcome,
            expected_publication=publication,
        )


@pytest.mark.parametrize("failure_kind", ["memory", "contract"])
def test_explore_child_failure_envelope_preserves_terminal_classification(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    from haute._execution_context import ExecutionMemoryLimitExceededError
    from haute._worker_isolation import IsolatedWorkerRemoteError
    from haute.errors import ContractMismatchError
    from haute.routes import _explore_service as service_module
    from haute.routes.explore import _explore_service

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    failure: BaseException
    if failure_kind == "memory":
        failure = ExecutionMemoryLimitExceededError(
            "explore_cache",
            rss_bytes=2,
            limit_bytes=1,
        )
        expected_status = "memory_limited"
    else:
        failure = ContractMismatchError("forced child contract mismatch", node_id="prep")
        expected_status = "contract_error"

    def fail_child_materialisation(*_args, **_kwargs):
        raise failure

    def run_child_synchronously(function, *args, **_kwargs):
        try:
            return function(*args)
        except BaseException as exc:
            raise IsolatedWorkerRemoteError(
                remote_type=type(exc).__name__,
                remote_message=str(exc),
                remote_traceback="simulated child traceback",
            ) from None

    monkeypatch.setattr(
        service_module,
        "_materialise_and_summarise_worker",
        fail_child_materialisation,
    )
    monkeypatch.setattr(service_module, "run_isolated_worker", run_child_synchronously)

    started = client.post("/api/explore/run", json=body)
    final = _poll_explore(client, started.json()["job_id"])

    assert final["status"] == expected_status
    stored = _explore_service._store.require_job(started.json()["job_id"])
    if failure_kind == "memory":
        assert stored["error_code"] == "memory_limit"
        assert stored["error_detail"]["memory_limit_bytes"] == 1
        assert stored["error_detail"]["rss_bytes"] == 2


def test_explore_staging_cleanup_failure_preserves_primary_job_failure(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._explore_cache import ExplorePersistentCacheStore
    from haute.routes import _explore_service as service_module

    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    discard_calls = 0

    def fail_worker(*_args, **_kwargs):
        raise RuntimeError("forced primary Explore failure")

    def fail_discard(_self, _publication):
        nonlocal discard_calls
        discard_calls += 1
        raise OSError("forced staging cleanup failure")

    monkeypatch.setattr(service_module, "run_isolated_worker", fail_worker)
    monkeypatch.setattr(ExplorePersistentCacheStore, "discard_publication", fail_discard)

    started = client.post("/api/explore/run", json=body)
    final = _poll_explore(client, started.json()["job_id"])

    assert final["status"] == "error"
    assert final["terminal_reason"] == "error"
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

    assert final["message"] == _INTERNAL_ERROR_DETAIL
    assert "forced primary Explore failure" not in str(final)
    assert discard_calls == 1


def test_explore_run_rejects_missing_node_before_execution(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)

    response = client.post(
        "/api/explore/run",
        json={"graph": _explore_graph(str(path)), "node_id": "missing", "source": "live"},
    )

    assert response.status_code == 404


def test_explore_rejects_node_with_multiple_parents(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": ["a"], "premium": [10]}).write_parquet(path)
    graph = _explore_graph(str(path))
    graph["nodes"].append(
        {
            "id": "second_parent",
            "type": "polars",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "second_parent",
                "nodeType": "polars",
                "config": {"code": "df = source"},
            },
        }
    )
    graph["edges"].append(make_edge("second_parent", "explore").model_dump())

    response = client.post(
        "/api/explore/run",
        json={"graph": graph, "node_id": "explore", "source": "live"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Explore node 'explore' must have exactly one upstream input (found 2)."
    )


# ---------------------------------------------------------------------------
# Per-column schema stats — populated by ``_materialise_and_summarise_worker`` so the
# UI can render a Schema Table card from the cache report without a second
# API call.
# ---------------------------------------------------------------------------


def _run_explore_and_get_columns(client: TestClient, data_path: str) -> list[dict]:
    # Identity prep so the Explore stats describe the source frame exactly,
    # making per-column assertions deterministic regardless of upstream wiring.
    response = client.post(
        "/api/explore/run",
        json={
            "graph": _explore_graph(data_path, prep_code="df = source"),
            "node_id": "explore",
            "source": "live",
        },
    )
    assert response.status_code == 200, response.text
    started = response.json()
    final = _poll_explore(client, started["job_id"])
    assert final["status"] == "completed", final
    return final["result"]["columns"]


def test_cache_report_includes_one_column_stat_per_column(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "tri.parquet"
    pl.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "score": [1.5, 2.5, 3.5],
        }
    ).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert len(columns) == 3
    assert [c["name"] for c in columns] == ["id", "name", "score"]
    assert [c["dtype"] for c in columns] == ["Int64", "String", "Float64"]


def test_null_count_matches_input(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "nulls.parquet"
    pl.DataFrame({"value": [1, None, 2, None, 3]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert len(columns) == 1
    assert columns[0]["null_count"] == 2


def test_distinct_count_matches_input(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "distinct.parquet"
    pl.DataFrame({"value": [1, 1, 2, 2, 3]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert columns[0]["distinct_count"] == 3


def test_nan_count_matches_input(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "nans.parquet"
    pl.DataFrame({"value": [1.0, float("nan"), float("nan"), None, 2.0]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert columns[0]["nan_count"] == 2
    assert columns[0]["null_count"] == 1


def test_min_value_truncated_at_80_chars_with_ellipsis(
    client: TestClient,
    tmp_path: Path,
) -> None:
    long_value = "x" * 200
    path = tmp_path / "long.parquet"
    pl.DataFrame({"value": [long_value]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    min_value = columns[0]["min_value"]
    assert min_value.endswith("…")
    assert len(min_value) == 81


def test_all_null_column_has_none_min_max_values(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "all_null.parquet"
    pl.DataFrame({"value": [None, None, None]}, schema={"value": pl.Utf8}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert columns[0]["min_value"] is None
    assert columns[0]["max_value"] is None


def test_column_order_matches_schema(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "order.parquet"
    pl.DataFrame({"c": [1], "a": [2], "b": [3]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert [c["name"] for c in columns] == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# _build_frame_stats — direct unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def explore_execution_context():
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile

    context = create_admitted_execution_context(
        operation="explore_cache_unit_test",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
    )
    try:
        yield context
    finally:
        context.release_admission()


def test_build_frame_stats_object_dtype_distinct_is_none(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    series = pl.Series("obj_col", [{"a": 1}, {"a": 2}, {"a": 3}], dtype=pl.Object)
    lf = series.to_frame().lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert len(stats) == 1
    assert stats[0].name == "obj_col"
    assert stats[0].distinct_count is None
    assert stats[0].null_count == 0


def test_build_frame_stats_struct_dtype_distinct_is_computed(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"s": [{"x": 1}, {"x": 2}, {"x": 1}]}).lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert len(stats) == 1
    assert stats[0].name == "s"
    assert stats[0].distinct_count == 2


def test_build_frame_stats_empty_schema_returns_empty_list(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.LazyFrame()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert stats == []


def test_build_frame_stats_profiles_text_and_temporal_columns(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "text": pl.Series("text", [None, "a", "three"], dtype=pl.String),
            "empty_text": pl.Series("empty_text", [None, None, None], dtype=pl.String),
            "day": [date(2024, 1, 1), date(2024, 1, 4), None],
            "instant": [datetime(2024, 1, 1, 8), datetime(2024, 1, 2, 10), None],
        }
    ).lazy()

    stats = _build_frame_stats(lf, lf.collect_schema(), execution_context=explore_execution_context)
    by_name = {column.name: column for column in stats.columns}
    assert (
        by_name["text"].text_min_length,
        by_name["text"].text_mean_length,
        by_name["text"].text_max_length,
    ) == (1, 3.0, 5)
    assert by_name["empty_text"].text_min_length is None
    assert by_name["empty_text"].text_mean_length is None
    assert by_name["empty_text"].text_max_length is None
    assert by_name["day"].temporal_span == "3 days, 0:00:00"
    assert by_name["instant"].temporal_span == "1 day, 2:00:00"


def test_build_frame_stats_profiles_cardinality_identifier_and_duplicates(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "policy_id": [f"p{index}" for index in range(51)] + ["p0"],
            "id_exact": list(range(52)),
        }
    ).lazy()
    stats = _build_frame_stats(lf, lf.collect_schema(), execution_context=explore_execution_context)
    by_name = {column.name: column for column in stats.columns}
    assert by_name["policy_id"].unique_ratio == 51 / 52
    assert by_name["policy_id"].is_high_cardinality is True
    assert by_name["policy_id"].is_identifier_candidate is False
    assert by_name["id_exact"].is_identifier_candidate is True
    summary = stats.overview_summary.data_quality
    assert summary.duplicate_row_count == 0
    assert summary.duplicate_ratio == 0


def test_build_frame_stats_profile_flag_boundaries(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "at_limit": [f"v{i}" for i in range(50)] + ["v0"],
            "above_limit": [f"v{i}" for i in range(51)],
            "numeric_above_limit": list(range(51)),
            "policy_id": list(range(51)),
            "nullable_id": pl.Series(
                "nullable_id",
                [*range(50), None],
                dtype=pl.Int64,
            ),
            "empty_id": pl.Series("empty_id", [None] * 51, dtype=pl.Int64),
        }
    ).lazy()
    stats = _build_frame_stats(lf, lf.collect_schema(), execution_context=explore_execution_context)
    by_name = {column.name: column for column in stats.columns}

    assert by_name["at_limit"].is_high_cardinality is False
    assert by_name["above_limit"].is_high_cardinality is True
    assert by_name["numeric_above_limit"].is_high_cardinality is False
    assert by_name["policy_id"].is_identifier_candidate is True
    assert by_name["nullable_id"].unique_ratio == 1
    assert by_name["nullable_id"].is_identifier_candidate is False
    assert by_name["empty_id"].unique_ratio is None
    assert by_name["empty_id"].is_identifier_candidate is False

    one_row = pl.DataFrame({"id": [1]}).lazy()
    one_row_stat = _build_frame_stats(
        one_row,
        one_row.collect_schema(),
        execution_context=explore_execution_context,
    ).columns[0]
    assert one_row_stat.unique_ratio == 1
    assert one_row_stat.is_identifier_candidate is False


@pytest.mark.parametrize(
    ("values", "severity"),
    [([1, 2, 1], "warning"), ([1, 1], "danger"), ([1, 1, 1], "danger")],
)
def test_build_frame_stats_reports_duplicate_rows(
    explore_execution_context, values, severity
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"value": values}).lazy()
    summary = _build_frame_stats(
        lf, lf.collect_schema(), execution_context=explore_execution_context
    ).overview_summary.data_quality
    assert summary.duplicate_row_count == len(values) - len(set(values))
    assert summary.duplicate_ratio == pytest.approx(summary.duplicate_row_count / len(values))
    duplicate_issue = summary.issues[-1]
    assert duplicate_issue.severity == severity
    assert "duplicate" in duplicate_issue.label


def test_build_frame_stats_leaves_duplicate_profile_unknown_for_object_dtype(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.Series("obj", [{"a": 1}, {"a": 1}], dtype=pl.Object).to_frame().lazy()
    summary = _build_frame_stats(
        lf, lf.collect_schema(), execution_context=explore_execution_context
    ).overview_summary.data_quality
    assert summary.duplicate_row_count is None
    assert summary.duplicate_ratio is None


def test_build_explore_frame_stats_includes_row_count(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"value": [1, 2, 3]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    assert [s.name for s in frame_stats.columns] == ["value"]


def test_build_frame_stats_includes_numeric_profile_fields(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "premium": [-10, 0, 25, None],
            "region": ["north", "south", "north", "west"],
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {column.name: column for column in frame_stats.columns}
    assert by_name["premium"].min_value == "-10"
    assert by_name["premium"].kind == "Numeric"
    assert by_name["premium"].p25_value == "-5"
    assert by_name["premium"].median_value == "0"
    assert by_name["premium"].mean_value == "5"
    assert by_name["premium"].p75_value == "12.5"
    assert by_name["premium"].max_value == "25"
    assert by_name["premium"].std_value == "18.0278"
    assert by_name["premium"].zero_count == 1
    assert by_name["premium"].negative_count == 1
    assert by_name["region"].min_value == "north"
    assert by_name["region"].kind == "Text"
    assert by_name["region"].max_value == "west"
    assert by_name["region"].mean_value is None
    assert by_name["region"].std_value is None
    assert by_name["region"].zero_count is None


def test_build_frame_stats_formats_boolean_min_max_to_match_value_counts(
    explore_execution_context,
) -> None:
    """Boolean min/max must share the lowercase casing of value_counts.

    A Boolean column appears in both the Schema card (min/max) and the
    Categorical card (value counts). If min/max rendered ``str(True)`` while
    value counts cast to String ("true"), the same column would read
    inconsistently across cards.
    """

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"renewal": [True, False, True]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.kind == "Boolean"
    assert column.min_value == "false"
    assert column.max_value == "true"

    [profile] = frame_stats.overview_summary.categorical_summary
    assert {item.value for item in profile.values} == {"true", "false"}


def test_build_frame_stats_keeps_all_null_numeric_profiles(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {"all_null": [None, None], "single_value": [None, 10.0]},
        schema={"all_null": pl.Float64, "single_value": pl.Float64},
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {column.name: column for column in frame_stats.columns}
    assert by_name["all_null"].min_value is None
    assert by_name["all_null"].p25_value is None
    assert by_name["all_null"].median_value is None
    assert by_name["all_null"].mean_value is None
    assert by_name["all_null"].p75_value is None
    assert by_name["all_null"].max_value is None
    assert by_name["all_null"].std_value is None
    assert by_name["all_null"].zero_count == 0
    assert by_name["all_null"].negative_count == 0
    assert by_name["single_value"].mean_value == "10"
    assert by_name["single_value"].std_value is None


def test_build_frame_stats_reports_nan_counts_for_float_columns_only(
    explore_execution_context,
) -> None:
    """NaN is a third bucket, distinct from null: valid / null / NaN.

    A stream that cannot distinguish string from int materialises non-numeric
    error/default values as NaN in a Float column. Polars ``null_count``
    ignores NaN, so without a dedicated count an all-NaN column looks fully
    populated. Non-float dtypes cannot hold NaN, so their ``nan_count`` is
    None ("not applicable"), mirroring ``zero_count`` on non-numeric columns.
    """

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "measure": [1.0, float("nan"), float("nan"), None],
            "volume": [1, 2, 3, 4],
            "label": ["a", "b", "c", None],
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {column.name: column for column in frame_stats.columns}
    assert by_name["measure"].nan_count == 2
    assert by_name["measure"].null_count == 1
    assert by_name["volume"].nan_count is None
    assert by_name["label"].nan_count is None


def test_build_frame_stats_flags_nan_columns_in_quality_summary(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "all_nan": [float("nan")] * 4,
            "some_nan": [1.0, float("nan"), 2.0, 3.0],
            "clean": [1.0, 2.0, 3.0, 4.0],
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    issues = frame_stats.overview_summary.data_quality.issues
    nan_issues = [issue for issue in issues if "NaN" in issue.label]
    assert len(nan_issues) == 1
    assert nan_issues[0].label == "2 numeric columns with NaN values"
    assert nan_issues[0].severity == "danger"
    assert nan_issues[0].detail == "all_nan worst at 100%"
    # NaN rows are not nulls: the missing-values issue must not fire here.
    assert not any("missing" in issue.label for issue in issues)


def test_build_frame_stats_nan_issue_is_warning_below_half(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"measure": [1.0, float("nan"), 3.0, 4.0]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [issue] = [
        candidate
        for candidate in frame_stats.overview_summary.data_quality.issues
        if "NaN" in candidate.label
    ]
    assert issue.severity == "warning"
    assert issue.label == "1 numeric column with NaN values"
    assert issue.detail == "measure worst at 25%"


def test_build_frame_stats_distinct_count_excludes_null_bucket(
    explore_execution_context,
) -> None:
    """``n_unique`` counts the null bucket; the displayed distinct must not."""

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"value": [1, 1, 2, None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.null_count == 1
    assert column.distinct_count == 2


def test_build_frame_stats_distinct_count_excludes_nan_bucket(
    explore_execution_context,
) -> None:
    """NaN is reported separately (nan_count), so it is not a distinct value.

    ``[1.0, 1.0, nan, None]`` has one valid value (1.0); the NaN and null
    buckets are each their own count and must not inflate distinct_count.
    """

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"value": [1.0, 1.0, float("nan"), None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.null_count == 1
    assert column.nan_count == 1
    assert column.distinct_count == 1


def test_build_frame_stats_single_valid_value_with_nan_is_not_constant(
    explore_execution_context,
) -> None:
    """A constant column has NO nulls and NO NaNs — every row the same valid value.

    One valid value plus NaN reads distinct == 1, but the NaN rows mean the
    column is not constant; the NaN issue is the right signal for it.
    """

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"rate": [5.0, 5.0, float("nan")]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.distinct_count == 1
    labels = [issue.label for issue in frame_stats.overview_summary.data_quality.issues]
    assert not any("constant" in label for label in labels)
    assert any("NaN" in label for label in labels)


def test_build_frame_stats_all_nan_column_is_not_flagged_constant(
    explore_execution_context,
) -> None:
    """An all-NaN column has zero distinct valid values, so it is not

    "constant / single-value" — the dedicated NaN issue is the right signal.
    """

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"all_nan": [float("nan")] * 4}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.distinct_count == 0
    labels = [issue.label for issue in frame_stats.overview_summary.data_quality.issues]
    assert not any("constant" in label for label in labels)
    assert any("NaN" in label for label in labels)


def test_build_frame_stats_single_valid_value_with_nulls_is_not_constant(
    explore_execution_context,
) -> None:
    """A single-valued column that also has nulls is NOT constant (Nick's ruling).

    Constant means every row holds the same valid value; the null rows make
    this a missing-values column instead, and that issue already covers it.
    """

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"segment": ["same", "same", None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [column] = frame_stats.columns
    assert column.distinct_count == 1
    labels = [issue.label for issue in frame_stats.overview_summary.data_quality.issues]
    assert not any("constant" in label for label in labels)
    assert any("missing" in label for label in labels)


def test_categorical_truncation_counts_null_bucket_as_a_group(
    explore_execution_context,
) -> None:
    """50 distinct values plus nulls is 51 value-count groups: truncated."""

    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"segment": [f"s{i:03d}" for i in range(50)] + [None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.distinct_count == 50
    assert profile.values_truncated is True
    assert len(profile.values) == 50


def test_build_frame_stats_includes_backend_overview_summary(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    row_count = 100
    lf = pl.DataFrame(
        {
            "policy_id": [f"p{i:03d}" for i in range(row_count)],
            "premium": list(range(-1, row_count - 1)),
            "region": [
                None if i < 25 else ("north" if i % 2 == 0 else "south") for i in range(row_count)
            ],
            "constant": ["same"] * row_count,
            "loss_ratio": [0] * row_count,
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    summary = frame_stats.overview_summary
    assert [issue.label for issue in summary.data_quality.issues] == [
        "1 column with missing values",
        "1 constant / single-value column",
        "1 numeric column with negatives",
        "1 mostly-zero numeric column",
        "1 high-cardinality column",
    ]
    assert summary.data_quality.issues[0].detail == "region worst at 25%"
    assert summary.data_quality.issue_count == 5


def test_build_frame_stats_includes_bounded_categorical_value_counts(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "premium": [10, 20, 30, 40],
            "region": ["north", "south", "north", None],
            "renewal": [True, False, True, True],
            "inception_date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 2, 1), None],
            "empty_segment": pl.Series("empty_segment", [None, None, None, None], dtype=pl.String),
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert set(profiles) == {"region", "renewal", "inception_date", "empty_segment"}
    # distinct_count is of non-null values only: {north, south} = 2, even
    # though the value-count groups also include the null bucket.
    assert profiles["region"].distinct_count == 2
    assert profiles["region"].expandable is True
    assert profiles["region"].values_truncated is False
    assert [(item.value, item.count) for item in profiles["region"].values] == [
        ("north", 2),
        ("south", 1),
        (None, 1),
    ]
    assert profiles["renewal"].expandable is True
    assert [(item.value, item.count) for item in profiles["renewal"].values] == [
        ("true", 3),
        ("false", 1),
    ]
    assert [(item.value, item.count) for item in profiles["inception_date"].values] == [
        ("2024-01-01", 2),
        ("2024-02-01", 1),
        (None, 1),
    ]
    assert [(item.value, item.count) for item in profiles["empty_segment"].values] == [
        (None, 4),
    ]


def test_build_frame_stats_survives_non_utf8_binary_column(
    explore_execution_context,
) -> None:
    """A Binary column holding non-UTF-8 bytes must not abort materialisation.

    Binary is admitted to the categorical value-count branch. A strict
    ``cast(pl.String)`` (or even ``strict=False``) aborts the entire batched
    ``streaming_collect`` on the first invalid byte sequence, taking down the
    whole frame. Undecodable bytes must instead map to the Unicode replacement
    character so the materialisation always completes.
    """
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "payload": pl.Series(
                "payload",
                [b"\xff\xfe", b"ok", b"ok", None],
                dtype=pl.Binary,
            ),
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 4
    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert "payload" in profiles
    values = {item.value: item.count for item in profiles["payload"].values}
    # Valid bytes decode to text; the two invalid bytes each become a
    # replacement character; nulls surface as a null bucket. Never a crash.
    assert values == {"ok": 2, "��": 1, None: 1}


def test_build_frame_stats_survives_duration_column(
    explore_execution_context,
) -> None:
    """A Duration column must not abort the whole Explore materialisation.

    Duration is temporal, so it is admitted to the categorical value-count
    branch — but Polars cannot ``cast(pl.Duration, pl.String)``, so the strict
    cast aborts the entire batched ``streaming_collect``, taking every other
    column's stats down with it. Duration values must instead be formatted
    leniently (like Binary) so the report always completes, with the column
    represented sensibly.
    """
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "premium": [10, 20, 30, 40],
            "wait": pl.Series(
                "wait",
                [timedelta(days=1), timedelta(hours=2), timedelta(hours=2), None],
                dtype=pl.Duration("us"),
            ),
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    # The whole report survives: both columns are present with core stats.
    assert frame_stats.row_count == 4
    stats = {column.name: column for column in frame_stats.columns}
    assert set(stats) == {"premium", "wait"}
    assert stats["premium"].mean_value == "25"
    assert stats["wait"].kind == "Temporal"
    assert stats["wait"].null_count == 1
    # {1 day, 2 hours} — distinct counts valid values only; the null bucket
    # is reported via null_count, not folded into distinct.
    assert stats["wait"].distinct_count == 2
    # Duration min/max already format via str(timedelta); labels match them.
    assert stats["wait"].min_value == "2:00:00"
    assert stats["wait"].max_value == "1 day, 0:00:00"
    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert "wait" in profiles
    values = {item.value: item.count for item in profiles["wait"].values}
    assert values == {"2:00:00": 2, "1 day, 0:00:00": 1, None: 1}


def test_build_frame_stats_expands_high_cardinality_categorical_columns_with_top_50_values(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "policy_id": (
                ["p000"] * 3 + ["p001"] * 2 + ["p002"] + [f"p{i:03d}" for i in range(3, 53)]
            )
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "policy_id"
    assert profile.distinct_count == 53
    assert profile.expandable is True
    assert profile.values_truncated is True
    assert len(profile.values) == 50
    assert [(item.value, item.count) for item in profile.values[:3]] == [
        ("p000", 3),
        ("p001", 2),
        ("p002", 1),
    ]
    assert [item.value for item in profile.values[-2:]] == ["p048", "p049"]
    assert "p050" not in {item.value for item in profile.values}


def test_build_frame_stats_returns_all_values_for_exactly_50_categorical_groups(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"segment": [f"s{i:03d}" for i in range(50)]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "segment"
    assert profile.distinct_count == 50
    assert profile.expandable is True
    assert profile.values_truncated is False
    assert len(profile.values) == 50
    assert [item.value for item in profile.values[:3]] == ["s000", "s001", "s002"]
    assert profile.values[-1].value == "s049"


def test_build_frame_stats_keeps_unsupported_categorical_profiles_unexpanded(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"codes": [["a"], ["b"], ["a"], None]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "codes"
    # {["a"], ["b"]} = 2 distinct non-null values (the None row is excluded).
    assert profile.distinct_count == 2
    assert profile.expandable is False
    assert profile.values_truncated is False
    assert profile.values == []


def test_build_frame_stats_does_not_mark_uncomputed_list_values_as_truncated(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"codes": [[str(index)] for index in range(51)]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.distinct_count == 51
    assert profile.expandable is False
    assert profile.values_truncated is False
    assert profile.values == []


def test_build_frame_stats_uses_display_label_groups_for_binary_truncation(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    # Every raw binary value is distinct, but all lossily decode to the same
    # display label. Truncation reflects labels returned to the UI, not raw bytes.
    lf = pl.DataFrame(
        {
            "payload": pl.Series(
                "payload",
                [b"\x80" + bytes([index]) for index in range(0x80, 0x80 + 51)],
                dtype=pl.Binary,
            )
        }
    ).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.distinct_count == 51
    assert profile.values_truncated is False
    assert [(item.value, item.count) for item in profile.values] == [("\ufffd" * 2, 51)]


def test_build_frame_stats_categorical_value_counts_handle_count_column_name(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"count": ["one", "two", "one"]}).lazy()

    frame_stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    [profile] = frame_stats.overview_summary.categorical_summary
    assert profile.field == "count"
    assert [(item.value, item.count) for item in profile.values] == [("one", 2), ("two", 1)]


def test_build_frame_stats_happy_path(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame(
        {
            "id": [1, 2, 3, 3],
            "name": ["alpha", "beta", None, "alpha"],
            "score": [1.5, 2.5, 3.5, 1.5],
        }
    ).lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    ).columns

    assert [s.name for s in stats] == ["id", "name", "score"]
    assert [s.dtype for s in stats] == ["Int64", "String", "Float64"]
    assert [s.kind for s in stats] == ["Numeric", "Text", "Numeric"]
    assert [s.null_count for s in stats] == [0, 1, 0]
    # "name" has a null row: 3 raw n_unique minus the null bucket == 2.
    assert [s.distinct_count for s in stats] == [3, 2, 3]
    assert [s.min_value for s in stats] == ["1", "alpha", "1.5"]
    assert [s.max_value for s in stats] == ["3", "beta", "3.5"]


def test_build_explore_frame_stats_uses_one_streaming_collect_without_categorical_counts(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute.routes import _explore_service as service_mod

    calls = []
    original_streaming_collect = service_mod.cancellable_streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(service_mod, "cancellable_streaming_collect", counted_streaming_collect)
    lf = pl.DataFrame({"value": [None, 1.0, 2.0]}).lazy()

    frame_stats = service_mod._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    assert len(calls) == 1


def test_build_explore_frame_stats_uses_single_batched_collect_for_bounded_value_counts(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute.routes import _explore_service as service_mod

    calls = []
    original_streaming_collect = service_mod.cancellable_streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(service_mod, "cancellable_streaming_collect", counted_streaming_collect)
    lf = pl.DataFrame(
        {
            "value": [None, "a", "b"],
            "channel": ["web", "broker", "web"],
        }
    ).lazy()

    frame_stats = service_mod._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert [(item.value, item.count) for item in profiles["value"].values] == [
        ("a", 1),
        ("b", 1),
        (None, 1),
    ]
    assert [(item.value, item.count) for item in profiles["channel"].values] == [
        ("web", 2),
        ("broker", 1),
    ]
    assert len(calls) == 1
    collect_plan = calls[0].explain()
    assert "UNION" not in collect_plan
    assert "CACHE" not in collect_plan


def test_build_explore_frame_stats_counts_categorical_values_without_wide_unpivot(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute.routes import _explore_service as service_mod

    def fail_unpivot(*args, **kwargs):  # pragma: no cover - assertion path only
        raise AssertionError("categorical value counts should not use wide unpivot")

    monkeypatch.setattr(pl.LazyFrame, "unpivot", fail_unpivot, raising=False)

    lf = pl.DataFrame(
        {
            "region": ["north", "south", "north", None],
            "channel": ["web", "broker", "web", "web"],
        }
    ).lazy()

    frame_stats = service_mod._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    profiles = {
        profile.field: profile for profile in frame_stats.overview_summary.categorical_summary
    }
    assert [(item.value, item.count) for item in profiles["region"].values] == [
        ("north", 2),
        ("south", 1),
        (None, 1),
    ]
    assert [(item.value, item.count) for item in profiles["channel"].values] == [
        ("web", 3),
        ("broker", 1),
    ]


def test_build_frame_stats_returns_row_count_with_column_stats(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_frame_stats

    lf = pl.DataFrame({"value": [1, 2, 3, 4]}).lazy()

    stats = _build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert stats.row_count == 4
    assert [s.name for s in stats.columns] == ["value"]
