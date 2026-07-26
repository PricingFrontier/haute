from __future__ import annotations

import threading
import time
from datetime import date, timedelta
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
                            "cacheMode": "direct",
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
                "overview": {"dataset_snapshot": True, "schema": True},
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


def test_explore_reuses_typed_report_cache_without_reexecuting_sources(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    spec = _explore_service._prepare_spec(ExploreRunRequest.model_validate(body))
    assert EXPLORE_CACHE_VERSION == 3
    assert spec.report_cache_key.startswith("explore:v3:")

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

    def fail_materialise(*args, **kwargs):  # pragma: no cover - assertion path only
        raise AssertionError("cached Explore report should not re-execute upstream sources")

    monkeypatch.setattr(_explore_service, "_materialise_and_summarise", fail_materialise)

    response = client.post("/api/explore/run", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["cached"] is True
    assert payload["result"]["dataframe_cache_key"] == spec.dataframe_cache_key
    assert payload["result"]["overview_summary"] == {
        "categorical_summary": [],
        "data_quality": {"issue_count": 0, "issues": []},
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

    worker_name = f"haute-explore-{started['job_id']}"
    for thread in threading.enumerate():
        if thread.name == worker_name:
            thread.join(timeout=5.0)
            assert not thread.is_alive()
            break


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
    ]
    assert summary.data_quality.issues[0].detail == "region worst at 25%"
    assert summary.data_quality.issue_count == 4


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
    original_streaming_collect = service_mod.streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(service_mod, "streaming_collect", counted_streaming_collect)
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
    original_streaming_collect = service_mod.streaming_collect

    def counted_streaming_collect(*args, **kwargs):
        calls.append(args[0])
        return original_streaming_collect(*args, **kwargs)

    monkeypatch.setattr(service_mod, "streaming_collect", counted_streaming_collect)
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
