from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

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


def _explore_graph(
    data_path: str,
    *,
    extra_downstream_label: str = "ignored",
    explore_config: dict | None = None,
) -> dict:
    graph = make_graph(
        {
            "source_file": str(Path(data_path).with_name("pipeline.py")),
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": data_path},
                    },
                },
                {
                    "id": "prep",
                    "data": {
                        "label": "prep",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = source.with_columns("
                                "(pl.col('premium') * 2).alias('double_premium'))"
                            )
                        },
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
    first_key = _explore_service._prepare_spec(
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
        _explore_service._prepare_spec(
            ExploreRunRequest.model_validate(second_body)
        ).dataframe_cache_key
        == first_key
    )


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

    # Make the worker block until we tell it to proceed, so we can cancel mid-flight.
    gate = threading.Event()
    original_collect = service_mod.streaming_collect

    def gated_collect(*args, **kwargs):
        if not gate.is_set():
            gate.wait(timeout=5.0)
        return original_collect(*args, **kwargs)

    monkeypatch.setattr(service_mod, "streaming_collect", gated_collect)

    body = {"graph": _explore_graph(str(path)), "node_id": "explore", "source": "live"}
    started = client.post("/api/explore/run", json=body).json()
    assert started["status"] == "started"

    cancel_response = client.post(f"/api/explore/cancel/{started['job_id']}")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    # Release the gate so the worker thread exits and the fixture can clean up.
    gate.set()
    final = _poll_explore(client, started["job_id"], timeout=5.0)
    assert final["status"] == "cancelled"
    assert final["terminal_reason"] == "cancelled"


def test_explore_status_unknown_job_is_404(client: TestClient) -> None:
    response = client.get("/api/explore/status/not-a-job")

    assert response.status_code == 404
