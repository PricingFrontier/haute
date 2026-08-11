"""End-to-end contracts for cached Explore pivot calculation."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

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
def _clean_pivot_state(_widen_sandbox_root):
    from haute.routes.explore import _explore_service, _store

    snapshot = dict(_store.jobs)
    yield
    _store.jobs.clear()
    _store.jobs.update(snapshot)
    _explore_service._report_cache.clear()
    try:
        from haute.routes.explore import _pivot_service
    except ImportError:
        return
    _pivot_service._result_cache.clear()


def _graph(path: Path) -> dict[str, Any]:
    return make_graph(
        {
            "source_file": str(path.with_name("pipeline.py")),
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
                            "path": str(path),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "explore",
                    "data": {"label": "Explore", "nodeType": "explore", "config": {}},
                },
            ],
            "edges": [make_edge("source", "explore").model_dump()],
        }
    ).model_dump()


def _pivot(**updates: Any) -> dict[str, Any]:
    pivot = {
        "version": 1,
        "id": "pivot_1",
        "name": "Claims pivot",
        "enabled": True,
        "filters": [],
        "columns": [{"id": "column_1", "field": "year"}],
        "rows": [{"id": "row_1", "field": "region"}],
        "values": [
            {
                "id": "sum_claims",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
            }
        ],
        "options": {"row_grand_totals": True, "column_grand_totals": True},
    }
    pivot.update(updates)
    return pivot


def _poll(client: TestClient, path: str, job_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{path}/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in _TERMINAL:
            return payload
        time.sleep(0.02)
    raise TimeoutError(job_id)


def _materialise(client: TestClient, graph: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/api/explore/run", json={"graph": graph, "node_id": "explore", "source": "live"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    if payload["status"] == "completed":
        return payload["result"]
    final = _poll(client, "/api/explore/status", payload["job_id"])
    assert final["status"] == "completed", final
    return final["result"]


def _run_pivot(
    client: TestClient,
    graph: dict[str, Any],
    pivot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    response = client.post(
        "/api/explore/pivots/run",
        json={"graph": graph, "node_id": "explore", "source": "live", "pivot": pivot},
    )
    assert response.status_code == 200, response.text
    started = response.json()
    if started["status"] == "completed":
        return started, started["result"]
    if started["status"] != "started":
        return started, None
    final = _poll(client, "/api/explore/pivots/status", started["job_id"])
    return final, final.get("result")


def _path_index(paths: list[dict[str, Any]], values: list[tuple[str, Any]] | None) -> int:
    if values is None:
        return next(index for index, path in enumerate(paths) if path["is_grand_total"])
    return next(
        index
        for index, path in enumerate(paths)
        if not path["is_grand_total"]
        and [(member["kind"], member["value"]) for member in path["members"]] == values
    )


def _cell(
    result: dict[str, Any],
    row: list[tuple[str, Any]] | None,
    column: list[tuple[str, Any]] | None,
    value_id: str,
) -> Any:
    row_index = _path_index(result["row_paths"], row)
    column_index = _path_index(result["column_paths"], column)
    return next(
        cell["value"]
        for cell in result["cells"]
        if cell["row_index"] == row_index
        and cell["column_index"] == column_index
        and cell["value_id"] == value_id
    )


def test_pivot_run_requires_exact_materialised_explore_cache(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)

    response = client.post(
        "/api/explore/pivots/run",
        json={"graph": _graph(path), "node_id": "explore", "source": "live", "pivot": _pivot()},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "cache_required",
        "job_id": None,
        "cached": False,
        "message": "Cache the Explore dataset before updating this pivot.",
        "result": None,
        "failure": {
            "reason_code": "cache_required",
            "message": "The full Explore dataset is not materialised.",
            "remediation": "Process and cache full data, then update the pivot.",
            "dimensions": {},
        },
    }


def test_pivot_calculates_filters_aggregations_repeated_values_and_grand_totals(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["North", "North", "North", "South", "South", None],
            "year": [2024, 2024, 2025, 2024, 2025, 2025],
            "product": ["A", "B", "A", "A", "A", "A"],
            "claims": [10.0, 20.0, None, 5.0, math.nan, 15.0],
            "policy": ["p1", "p2", "p1", "p3", "p3", None],
        }
    ).write_parquet(path)
    graph = _graph(path)
    report = _materialise(client, graph)
    values = [
        {"id": "sum_claims", "field": "claims", "aggregation": "sum", "display_name": "Sum"},
        {
            "id": "average_claims",
            "field": "claims",
            "aggregation": "average",
            "display_name": "Average",
        },
        {"id": "min_claims", "field": "claims", "aggregation": "min", "display_name": "Min"},
        {"id": "max_claims", "field": "claims", "aggregation": "max", "display_name": "Max"},
        {
            "id": "median_claims",
            "field": "claims",
            "aggregation": "median",
            "display_name": "Median",
        },
        {"id": "count_claims", "field": "claims", "aggregation": "count", "display_name": "Count"},
        {
            "id": "policies",
            "field": "policy",
            "aggregation": "distinct_count",
            "display_name": "Policies",
        },
        {"id": "sum_again", "field": "claims", "aggregation": "sum", "display_name": "Sum again"},
    ]
    pivot = _pivot(
        filters=[
            {
                "id": "filter_1",
                "field": "product",
                "members": [{"kind": "string", "value": "A"}],
            }
        ],
        values=values,
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert result["version"] == 1
    assert result["dataframe_cache_key"] == report["dataframe_cache_key"]
    assert [value["id"] for value in result["values"]] == [value["id"] for value in values]
    assert len(result["row_paths"]) == 4  # North, South, null, Grand total
    assert len(result["column_paths"]) == 3  # 2024, 2025, Grand total

    north = [("string", "North")]
    south = [("string", "South")]
    year_2024 = [("integer", "2024")]
    year_2025 = [("integer", "2025")]
    assert _cell(result, north, year_2024, "sum_claims") == 10.0
    assert _cell(result, north, year_2025, "sum_claims") is None
    assert _cell(result, south, year_2025, "sum_claims") is None
    assert _cell(result, north, year_2024, "average_claims") == 10.0
    assert _cell(result, north, year_2024, "median_claims") == 10.0
    assert _cell(result, north, year_2025, "count_claims") == 0
    assert _cell(result, north, None, "policies") == 1
    assert _cell(result, north, year_2024, "sum_again") == 10.0
    assert _cell(result, None, year_2024, "sum_claims") == 15.0
    assert _cell(result, None, None, "sum_claims") == 30.0
    assert result["execution_metrics"]["execution_strategy"]["profile"] == "explore_analysis"


def test_pivot_result_cache_ignores_name_visibility_and_value_display_name(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    first, result = _run_pivot(client, graph, _pivot())
    assert first["status"] == "completed"
    assert result is not None
    changed = _pivot(
        name="Renamed",
        enabled=False,
        values=[
            {
                "id": "sum_claims",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Renamed value",
            }
        ],
    )
    second, second_result = _run_pivot(client, graph, changed)

    assert second["status"] == "completed"
    assert second["cached"] is True
    assert second_result == result


def test_pivot_result_cache_keeps_card_identities_independent(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    first, first_result = _run_pivot(client, graph, _pivot())
    second, second_result = _run_pivot(client, graph, _pivot(id="pivot_2", name="Second pivot"))

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert first_result is not None
    assert second_result is not None
    assert first_result["pivot_id"] == "pivot_1"
    assert second_result["pivot_id"] == "pivot_2"


def test_pivot_calculation_does_not_materialise_cached_rows_as_python_records(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["North", "South"],
            "year": [2024, 2025],
            "claims": [10.0, 20.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    def reject_to_dicts(_frame: pl.DataFrame) -> list[dict[str, Any]]:
        raise AssertionError("pivot execution must aggregate in Polars")

    monkeypatch.setattr(pl.DataFrame, "to_dicts", reject_to_dicts)

    final, result = _run_pivot(client, graph, _pivot())

    assert final["status"] == "completed", final
    assert result is not None
    assert _cell(result, [("string", "North")], [("integer", "2024")], "sum_claims") == 10.0


def test_pivot_orders_multi_level_paths_by_typed_values(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["Zulu", "Alpha", "Alpha", None],
            "segment": [2, 10, 2, 1],
            "year": [2025, 2024, 2024, 2024],
            "quarter": ["Q2", "Q1", "Q2", "Q1"],
            "claims": [4.0, 3.0, 2.0, 1.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        rows=[
            {"id": "row_region", "field": "region"},
            {"id": "row_segment", "field": "segment"},
        ],
        columns=[
            {"id": "column_year", "field": "year"},
            {"id": "column_quarter", "field": "quarter"},
        ],
        options={"row_grand_totals": False, "column_grand_totals": False},
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert [
        [(member["kind"], member["value"]) for member in path["members"]]
        for path in result["row_paths"]
    ] == [
        [("string", "Alpha"), ("integer", "2")],
        [("string", "Alpha"), ("integer", "10")],
        [("string", "Zulu"), ("integer", "2")],
        [("null", None), ("integer", "1")],
    ]
    assert [
        [(member["kind"], member["value"]) for member in path["members"]]
        for path in result["column_paths"]
    ] == [
        [("integer", "2024"), ("string", "Q1")],
        [("integer", "2024"), ("string", "Q2")],
        [("integer", "2025"), ("string", "Q2")],
    ]


def test_pivot_job_can_be_cancelled_without_publishing_a_result(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes.explore import _pivot_service

    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    original = _pivot_service._calculate
    calculation_started = threading.Event()
    release_calculation = threading.Event()
    calculation_finished = threading.Event()

    def blocking_calculation(spec, context):
        calculation_started.set()
        try:
            assert release_calculation.wait(3)
            context.checkpoint(label="pivot_test_cancel")
            return original(spec, context)
        finally:
            calculation_finished.set()

    monkeypatch.setattr(_pivot_service, "_calculate", blocking_calculation)
    started = client.post(
        "/api/explore/pivots/run",
        json={"graph": graph, "node_id": "explore", "source": "live", "pivot": _pivot()},
    ).json()
    assert started["status"] == "started"
    assert calculation_started.wait(3)

    cancelled = client.post(f"/api/explore/pivots/cancel/{started['job_id']}").json()
    release_calculation.set()

    assert cancelled["status"] == "cancelled"
    assert cancelled["result"] is None
    assert calculation_finished.wait(3)


def test_newer_pivot_job_supersedes_only_the_same_card_family(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes.explore import _pivot_service

    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    original = _pivot_service._calculate
    first_started = threading.Event()
    first_finished = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def first_call_blocks(spec, context):
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_started.set()
            try:
                while True:
                    context.checkpoint(label="pivot_test_superseded")
                    time.sleep(0.01)
            finally:
                first_finished.set()
        return original(spec, context)

    monkeypatch.setattr(_pivot_service, "_calculate", first_call_blocks)
    first = client.post(
        "/api/explore/pivots/run",
        json={"graph": graph, "node_id": "explore", "source": "live", "pivot": _pivot()},
    ).json()
    assert first["status"] == "started"
    assert first_started.wait(3)

    updated = _pivot(
        values=[
            {
                "id": "sum_claims",
                "field": "claims",
                "aggregation": "average",
                "display_name": "Average claims",
            }
        ]
    )
    second = client.post(
        "/api/explore/pivots/run",
        json={"graph": graph, "node_id": "explore", "source": "live", "pivot": updated},
    ).json()
    assert second["status"] == "started"
    second_final = _poll(client, "/api/explore/pivots/status", second["job_id"])
    first_status = client.get(f"/api/explore/pivots/status/{first['job_id']}").json()

    assert second_final["status"] == "completed", second_final
    assert first_status["status"] == "superseded"
    assert first_status["result"] is None
    assert first_finished.wait(3)


def test_pivot_failure_is_typed_and_never_publishes_partial_result(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    final, result = _run_pivot(
        client,
        graph,
        _pivot(rows=[{"id": "row_1", "field": "missing"}]),
    )

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "invalid_pivot_field"
    assert final["failure"]["dimensions"] == {"field": "missing"}


def test_pivot_cardinality_limit_reports_measured_dimensions(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes._pivot_service as _pivot_service

    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {"region": ["North", "South"], "year": [2024, 2024], "claims": [10.0, 20.0]}
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    monkeypatch.setattr(_pivot_service, "MAX_ROW_GROUPS", 1)

    final, result = _run_pivot(client, graph, _pivot())

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "pivot_cardinality_limit"
    assert final["failure"]["dimensions"] == {
        "dimension": "row_groups",
        "actual": 2,
        "limit": 1,
    }


def test_pivot_members_are_typed_exact_and_cache_backed(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {"region": ["North", "North", None], "claims": [1.0, 2.0, math.nan]}
    ).write_parquet(path)
    graph = _graph(path)

    missing = client.post(
        "/api/explore/pivots/members",
        json={"graph": graph, "node_id": "explore", "source": "live", "field": "region"},
    )
    assert missing.status_code == 200
    assert missing.json()["status"] == "cache_required"

    _materialise(client, graph)
    response = client.post(
        "/api/explore/pivots/members",
        json={"graph": graph, "node_id": "explore", "source": "live", "field": "region"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["members"] == [
        {"key": {"kind": "string", "value": "North"}, "label": "North", "count": 2},
        {"key": {"kind": "null", "value": None}, "label": "(blank)", "count": 1},
    ]
