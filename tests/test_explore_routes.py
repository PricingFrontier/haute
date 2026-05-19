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


_DEFAULT_PREP_CODE = (
    "df = source.with_columns((pl.col('premium') * 2).alias('double_premium'))"
)


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
                        "nodeType": "dataSource",
                        "config": {"path": data_path},
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


def test_explore_overview_config_does_not_invalidate_analysis_dataframe_cache(
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
                "overview": {"dataset_header": True, "schema": True},
            },
        ),
        "node_id": "explore",
        "source": "live",
    }

    first = client.post("/api/explore/run", json=first_body).json()
    first_status = _poll_explore(client, first["job_id"])
    assert first_status["status"] == "completed"
    first_key = _explore_service._prepare_spec(
        ExploreRunRequest.model_validate(first_body)
    ).dataframe_cache_key

    second_response = client.post("/api/explore/run", json=second_body)

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["status"] == "completed"
    assert second["cached"] is True
    assert second["result"]["dataframe_cache_key"] == first_key
    assert (
        _explore_service._prepare_spec(
            ExploreRunRequest.model_validate(second_body)
        ).dataframe_cache_key
        == first_key
    )


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
        _explore_service._prepare_spec(
            ExploreRunRequest.model_validate(first_body)
        ).dataframe_cache_key
        != _explore_service._prepare_spec(
            ExploreRunRequest.model_validate(second_body)
        ).dataframe_cache_key
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


# ---------------------------------------------------------------------------
# Per-column schema stats — populated by ``_materialise_and_summarise`` so the
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


def test_example_value_is_first_non_null_stringified(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path_a = tmp_path / "a.parquet"
    pl.DataFrame({"value": ["a", "b", "c"]}).write_parquet(path_a)

    columns_a = _run_explore_and_get_columns(client, str(path_a))
    assert columns_a[0]["example_value"] == "a"

    path_b = tmp_path / "b.parquet"
    pl.DataFrame({"value": [None, "b", "c"]}).write_parquet(path_b)

    columns_b = _run_explore_and_get_columns(client, str(path_b))
    assert columns_b[0]["example_value"] == "b"


def test_example_value_truncated_at_80_chars_with_ellipsis(
    client: TestClient,
    tmp_path: Path,
) -> None:
    long_value = "x" * 200
    path = tmp_path / "long.parquet"
    pl.DataFrame({"value": [long_value]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    example = columns[0]["example_value"]
    assert example.endswith("…")
    assert len(example) == 81


def test_all_null_column_has_none_example_value(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "all_null.parquet"
    pl.DataFrame({"value": [None, None, None]}, schema={"value": pl.Utf8}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert columns[0]["example_value"] is None


def test_example_value_uses_first_non_null_beyond_initial_rows(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = pl.DataFrame(
        {"value": [None] * 25 + ["late"]},
        schema={"value": pl.Utf8},
    ).lazy()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert stats[0].example_value == "late"


def test_column_order_matches_schema(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "order.parquet"
    pl.DataFrame({"c": [1], "a": [2], "b": [3]}).write_parquet(path)

    columns = _run_explore_and_get_columns(client, str(path))

    assert [c["name"] for c in columns] == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# _build_column_stats — direct unit tests
#
# These pin the five-line invariant from ``_build_column_stats`` (the function
# behind the Schema Table card) without an HTTP round-trip.  Real ``LazyFrame``
# inputs are used: Polars makes them cheap and mocking its internals would
# couple the test to private APIs.
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


def test_build_column_stats_object_dtype_distinct_is_none(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_column_stats

    series = pl.Series("obj_col", [{"a": 1}, {"a": 2}, {"a": 3}], dtype=pl.Object)
    lf = series.to_frame().lazy()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert len(stats) == 1
    assert stats[0].name == "obj_col"
    assert stats[0].distinct_count is None
    assert stats[0].null_count == 0


def test_build_column_stats_struct_dtype_distinct_is_computed(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = pl.DataFrame({"s": [{"x": 1}, {"x": 2}, {"x": 1}]}).lazy()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert len(stats) == 1
    assert stats[0].name == "s"
    assert stats[0].distinct_count == 2


def test_build_column_stats_empty_schema_returns_empty_list(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = pl.LazyFrame()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert stats == []


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


def test_build_column_stats_happy_path(explore_execution_context) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = pl.DataFrame(
        {
            "id": [1, 2, 3, 3],
            "name": ["alpha", "beta", None, "alpha"],
            "score": [1.5, 2.5, 3.5, 1.5],
        }
    ).lazy()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert [s.name for s in stats] == ["id", "name", "score"]
    assert [s.dtype for s in stats] == ["Int64", "String", "Float64"]
    assert [s.null_count for s in stats] == [0, 1, 0]
    assert [s.distinct_count for s in stats] == [3, 3, 3]
    assert [s.example_value for s in stats] == ["1", "alpha", "1.5"]


def test_build_column_stats_normalises_list_and_array_examples(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = pl.DataFrame(
        {
            "list_value": [[1, 2], None],
            "array_value": pl.Series(
                "array_value",
                [[3, 4], None],
                dtype=pl.Array(pl.Int64, 2),
            ),
        }
    ).lazy()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    by_name = {s.name: s for s in stats}
    assert by_name["list_value"].example_value == "[1, 2]"
    assert by_name["array_value"].example_value == "[3, 4]"
    assert "Series:" not in by_name["list_value"].example_value
    assert "Series:" not in by_name["array_value"].example_value


def test_build_explore_frame_stats_uses_one_streaming_collect(
    explore_execution_context,
    monkeypatch,
) -> None:
    from haute.routes import _explore_service as service_mod

    calls = []
    original_streaming_collect = service_mod.streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(service_mod, "streaming_collect", counted_streaming_collect)
    lf = pl.DataFrame({"value": [None, "a", "b"]}).lazy()

    frame_stats = service_mod._build_frame_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert frame_stats.row_count == 3
    assert frame_stats.columns[0].example_value == "a"
    assert len(calls) == 1


def test_build_column_stats_finds_first_non_null_beyond_initial_rows(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = pl.DataFrame(
        {"value": [None] * 25 + ["eventual_value"]},
        schema={"value": pl.Utf8},
    ).lazy()

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )

    assert stats[0].example_value == "eventual_value"


def test_build_column_stats_formats_nested_examples_as_compact_values(
    explore_execution_context,
) -> None:
    from haute.routes._explore_service import _build_column_stats

    lf = (
        pl.DataFrame(
            {
                "list_col": [[1, 2], [3, 4]],
                "array_col": [[5, 6], [7, 8]],
                "struct_col": [{"x": 1}, {"x": 2}],
            }
        )
        .with_columns(pl.col("array_col").cast(pl.Array(pl.Int64, 2)))
        .lazy()
    )

    stats = _build_column_stats(
        lf,
        lf.collect_schema(),
        execution_context=explore_execution_context,
    )
    examples = {s.name: s.example_value for s in stats}

    assert examples == {
        "list_col": "[1, 2]",
        "array_col": "[5, 6]",
        "struct_col": '{"x": 1}',
    }


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
