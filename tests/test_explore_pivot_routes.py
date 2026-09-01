"""End-to-end contracts for cached Explore pivot calculation."""

from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
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

    _store.clear_all()
    yield
    _store.clear_all()
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
                "reference": "claims_sum",
                "display_name": "Claims",
            }
        ],
        "formulas": [],
        "options": {"row_grand_totals": True, "column_grand_totals": True},
    }
    pivot.update(updates)
    for placement in [*pivot["columns"], *pivot["rows"], *pivot["values"], *pivot["formulas"]]:
        placement.setdefault("number_format", "general")
        placement.setdefault("decimal_places", None)
        placement.setdefault("use_grouping", True)
    for value in pivot["values"]:
        value.setdefault("color_scale_split_by", None)
    pivot.setdefault(
        "value_order",
        [
            *(value["id"] for value in pivot["values"]),
            *(formula["id"] for formula in pivot["formulas"]),
        ],
    )
    pivot["options"].setdefault("sort_by", None)
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


def _materialise(
    client: TestClient, graph: dict[str, Any], *, source: str = "live"
) -> dict[str, Any]:
    response = client.post(
        "/api/explore/run", json={"graph": graph, "node_id": "explore", "source": source}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    if payload["status"] == "completed":
        return payload["result"]
    final = _poll(client, "/api/explore/status", payload["job_id"])
    assert final["status"] == "completed", final
    return final["result"]


def _wait_for_pivot_worker(job_id: str, timeout: float = 5.0) -> None:
    """Wait for a pivot job's worker thread to finish its terminal handling.

    A blocked worker observes cancellation or supersession asynchronously;
    returning before it completes would let test teardown remove the job
    record while the worker is still mid-transition.
    """
    from haute.routes.explore import _pivot_service

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _pivot_service._completion_lock:
            if job_id not in _pivot_service._completion_events:
                return
        time.sleep(0.01)
    raise TimeoutError(job_id)


def _run_pivot(
    client: TestClient,
    graph: dict[str, Any],
    pivot: dict[str, Any],
    *,
    source: str = "live",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    response = client.post(
        "/api/explore/pivots/run",
        json={"graph": graph, "node_id": "explore", "source": source, "pivot": pivot},
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


def test_pivot_uses_durable_explore_cache_restored_after_process_restart(
    client: TestClient,
    tmp_path: Path,
) -> None:
    from haute.routes.explore import _explore_service
    from haute.schemas import ExploreRunRequest

    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["North", "South"],
            "year": [2024, 2024],
            "claims": [10.0, 20.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    request = ExploreRunRequest.model_validate(
        {"graph": graph, "node_id": "explore", "source": "live"}
    )
    spec = _explore_service.prepare_spec(request)
    _explore_service._report_cache.clear()
    spec.dataframe_cache_request.cache.clear()

    snapshot = client.post(
        "/api/explore/cache-status",
        json={"graph": graph, "node_id": "explore", "source": "live"},
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "current"

    final, result = _run_pivot(client, graph, _pivot())

    assert final["status"] == "completed"
    assert result is not None
    assert _cell(result, [("string", "North")], [("integer", "2024")], "sum_claims") == 10.0


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
        {
            "id": "sum_claims",
            "field": "claims",
            "aggregation": "sum",
            "reference": "claims_sum",
            "display_name": "Sum",
        },
        {
            "id": "average_claims",
            "field": "claims",
            "aggregation": "average",
            "reference": "claims_mean",
            "display_name": "Average",
        },
        {
            "id": "min_claims",
            "field": "claims",
            "aggregation": "min",
            "reference": "claims_min",
            "display_name": "Min",
        },
        {
            "id": "max_claims",
            "field": "claims",
            "aggregation": "max",
            "reference": "claims_max",
            "display_name": "Max",
        },
        {
            "id": "median_claims",
            "field": "claims",
            "aggregation": "median",
            "reference": "claims_median",
            "display_name": "Median",
        },
        {
            "id": "count_claims",
            "field": "claims",
            "aggregation": "count",
            "reference": "claims_count",
            "display_name": "Count",
        },
        {
            "id": "policies",
            "field": "policy",
            "aggregation": "distinct_count",
            "reference": "policy_distinct_count",
            "display_name": "Policies",
        },
        {
            "id": "sum_again",
            "field": "claims",
            "aggregation": "sum",
            "reference": "claims_sum_2",
            "display_name": "Sum again",
        },
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


def test_pivot_formulas_aggregate_source_fields_without_visible_values_and_recalculate_totals(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import _pivot_service as pivot_module

    compiled_formula_ids: list[str] = []
    original_formula_expression = pivot_module._formula_expression

    def tracked_formula_expression(formula):
        compiled_formula_ids.append(formula["id"])
        return original_formula_expression(formula)

    monkeypatch.setattr(pivot_module, "_formula_expression", tracked_formula_expression)
    path = tmp_path / "exposure-formula.parquet"
    pl.DataFrame(
        {
            "region": ["North", "North", "North", "South"],
            "year": [2024, 2024, 2025, 2024],
            "exposure": [10.0, 30.0, 10.0, 50.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[],
        formulas=[
            {
                "id": "formula_1",
                "reference": "exposure_ratio",
                "display_name": "Exposure ratio",
                "expression": 'pl.col("exposure").sum() / pl.col("exposure").mean()',
                "number_format": "number",
                "decimal_places": 2,
                "use_grouping": True,
            },
            {
                "id": "formula_2",
                "reference": "exposure_percentage",
                "display_name": "Exposure percentage",
                "expression": '(pl.col("exposure").sum() / pl.col("exposure").mean()) * 100',
            },
        ],
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert result["values"] == [
        {"id": "formula_1", "field": "exposure_ratio", "aggregation": "formula"},
        {"id": "formula_2", "field": "exposure_percentage", "aggregation": "formula"},
    ]
    assert (
        _cell(
            result,
            [("string", "North")],
            [("integer", "2024")],
            "formula_1",
        )
        == 2.0
    )
    assert _cell(result, [("string", "North")], None, "formula_1") == 3.0
    assert _cell(result, None, None, "formula_1") == 4.0
    assert _cell(result, [("string", "North")], [("integer", "2024")], "formula_2") == 200.0
    assert _cell(result, None, None, "formula_2") == 400.0
    assert compiled_formula_ids == ["formula_1", "formula_2"]


def test_pivot_formula_allows_a_grouped_field_to_also_be_a_value(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "grouped-value-formula.parquet"
    pl.DataFrame({"region": ["North", "North"], "year": [2024, 2025]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[
            {
                "id": "sum_year",
                "field": "year",
                "aggregation": "sum",
                "reference": "year_sum",
                "display_name": "Sum year",
            }
        ],
        formulas=[
            {
                "id": "double_year",
                "reference": "double_year",
                "display_name": "Double year",
                "expression": 'pl.col("year").sum() * 2',
            }
        ],
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert _cell(result, [("string", "North")], [("integer", "2024")], "sum_year") == 2024
    assert _cell(result, [("string", "North")], [("integer", "2024")], "double_year") == 4048


def test_pivot_formula_reference_matching_a_grouped_field_uses_an_internal_alias(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "formula-reference-matches-group.parquet"
    pl.DataFrame(
        {"region": ["North", "North", "South"], "year": [2024, 2025, 2024], "claims": [2, 3, 5]}
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[],
        formulas=[
            {
                "id": "formula_1",
                "reference": "region",
                "display_name": "Claims total",
                "expression": 'pl.col("claims").sum()',
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            }
        ],
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert _cell(result, [("string", "North")], [("integer", "2024")], "formula_1") == 2
    assert _cell(result, [("string", "North")], [("integer", "2025")], "formula_1") == 3
    assert _cell(result, [("string", "South")], [("integer", "2024")], "formula_1") == 5


def test_pivot_value_order_controls_mixed_result_and_cell_order_without_affecting_value_sort(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "ordered-outputs.parquet"
    pl.DataFrame(
        {
            "region": ["North", "South"],
            "year": [2024, 2024],
            "claims": [10.0, 20.0],
            "paid": [100.0, 50.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[
            {
                "id": "sum_claims",
                "field": "claims",
                "aggregation": "sum",
                "reference": "claims_sum",
                "display_name": "Claims",
            },
            {
                "id": "sum_paid",
                "field": "paid",
                "aggregation": "sum",
                "reference": "paid_sum",
                "display_name": "Paid",
                "sort_rows": "descending",
            },
        ],
        formulas=[
            {
                "id": "total_amount",
                "reference": "total_amount",
                "display_name": "Total amount",
                "expression": 'pl.col("claims").sum() + pl.col("paid").sum()',
            }
        ],
        value_order=["total_amount", "sum_paid", "sum_claims"],
        options={"row_grand_totals": False, "column_grand_totals": False, "sort_by": "sum_paid"},
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert [value["id"] for value in result["values"]] == [
        "total_amount",
        "sum_paid",
        "sum_claims",
    ]
    assert [path["members"][0]["value"] for path in result["row_paths"]] == ["North", "South"]
    first_cell_ids = [
        cell["value_id"]
        for cell in result["cells"]
        if cell["row_index"] == 0 and cell["column_index"] == 0
    ]
    assert first_cell_ids == ["total_amount", "sum_paid", "sum_claims"]
    assert _cell(result, [("string", "North")], [("integer", "2024")], "total_amount") == 110.0
    assert _cell(result, [("string", "North")], [("integer", "2024")], "sum_paid") == 100.0
    assert _cell(result, [("string", "North")], [("integer", "2024")], "sum_claims") == 10.0

    reordered_final, reordered = _run_pivot(
        client,
        graph,
        {**pivot, "value_order": ["sum_claims", "sum_paid", "total_amount"]},
    )

    assert reordered_final["status"] == "completed", reordered_final
    assert reordered is not None
    assert reordered["calculation_key"] != result["calculation_key"]


@pytest.mark.parametrize(
    "expression",
    [
        'pl.concat_list(pl.col("claims").sum())',
        'pl.col("claims") * 2',
        "42",
        "pl.col(",
    ],
)
def test_pivot_formula_failures_are_typed(
    client: TestClient,
    tmp_path: Path,
    expression: str,
) -> None:
    path = tmp_path / "invalid-formula.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        formulas=[
            {
                "id": "formula_1",
                "reference": "calculated",
                "display_name": "Calculated",
                "expression": expression,
            }
        ]
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "invalid_pivot_formula"
    assert final["failure"]["dimensions"] == {"formula_id": "formula_1"}


def test_pivot_formula_runtime_failure_is_typed(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-formula-failure.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": ["not-a-number"]}).write_parquet(
        path
    )
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[],
        formulas=[
            {
                "id": "formula_1",
                "reference": "calculated",
                "display_name": "Calculated",
                "expression": 'pl.col("claims").cast(pl.Int64).sum()',
            }
        ],
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"] == {
        "reason_code": "invalid_pivot_formula",
        "message": "A pivot formula failed while evaluating the grouped data.",
        "remediation": "Check the selected formulas against the source field values and types.",
        "dimensions": {"formula_ids": "formula_1"},
    }


def test_pivot_formula_metadata_failure_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    from haute.routes import _pivot_service as pivot_module

    class BrokenMetadata:
        @staticmethod
        def root_names() -> list[str]:
            raise RuntimeError("metadata unavailable")

    class BrokenExpression:
        meta = BrokenMetadata()

    def broken_formula_expression(_formula):
        return BrokenExpression()

    monkeypatch.setattr(pivot_module, "_formula_expression", broken_formula_expression)
    formula = {
        "id": "formula_1",
        "reference": "calculated",
        "display_name": "Calculated",
        "expression": 'pl.col("claims").sum()',
    }

    with pytest.raises(pivot_module.PivotContractError) as caught:
        pivot_module._compile_formulas([formula], {"claims": pl.Float64})

    assert caught.value.failure.model_dump() == {
        "reason_code": "invalid_pivot_formula",
        "message": "Pivot formula is invalid.",
        "remediation": "Use a safe Python expression that returns one Polars expression.",
        "dimensions": {"formula_id": "formula_1"},
    }


def test_pivot_formula_missing_source_field_is_typed_and_remediable(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid-formula-reference.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        formulas=[
            {
                "id": "formula_1",
                "reference": "calculated",
                "display_name": "Calculated",
                "expression": 'pl.col("missing_claims").sum()',
            },
        ]
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "invalid_pivot_formula"
    assert "missing_claims" in final["failure"]["message"]
    assert "missing_claims" in final["failure"]["remediation"]
    assert final["failure"]["dimensions"] == {
        "formula_id": "formula_1",
        "missing_fields": "missing_claims",
    }


def test_pivot_formula_cannot_depend_on_another_formula_output(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "ordered-formula-inputs.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    final, result = _run_pivot(
        client,
        graph,
        _pivot(
            formulas=[
                {
                    "id": "formula_1",
                    "reference": "valid_result",
                    "display_name": "Valid result",
                    "expression": 'pl.col("claims").sum()',
                },
                {
                    "id": "formula_2",
                    "reference": "depends_on_earlier_formula",
                    "display_name": "Depends on earlier formula",
                    "expression": 'pl.col("valid_result").sum() + 1',
                },
            ]
        ),
    )

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "invalid_pivot_formula"
    assert final["failure"]["dimensions"] == {
        "formula_id": "formula_2",
        "missing_fields": "valid_result",
    }


def test_pivot_normalises_binary_and_duration_min_max_results(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "scalar-values.parquet"
    pl.DataFrame(
        {
            "region": ["All", "All"],
            "year": [2024, 2024],
            "binary_value": pl.Series([b"b", b"a"], dtype=pl.Binary),
            "duration_value": pl.Series(
                [timedelta(seconds=2), timedelta(seconds=1)],
                dtype=pl.Duration("us"),
            ),
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[
            {
                "id": "binary_min",
                "field": "binary_value",
                "aggregation": "min",
                "reference": "binary_value_min",
                "display_name": "Binary min",
            },
            {
                "id": "binary_max",
                "field": "binary_value",
                "aggregation": "max",
                "reference": "binary_value_max",
                "display_name": "Binary max",
            },
            {
                "id": "duration_min",
                "field": "duration_value",
                "aggregation": "min",
                "reference": "duration_value_min",
                "display_name": "Duration min",
            },
            {
                "id": "duration_max",
                "field": "duration_value",
                "aggregation": "max",
                "reference": "duration_value_max",
                "display_name": "Duration max",
            },
        ]
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    row = [("string", "All")]
    column = [("integer", "2024")]
    assert _cell(result, row, column, "binary_min") == "a"
    assert _cell(result, row, column, "binary_max") == "b"
    assert _cell(result, row, column, "duration_min") == "0:00:01"
    assert _cell(result, row, column, "duration_max") == "0:00:02"


def test_pivot_member_search_matches_the_column_lowercase_transform(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "members-case.parquet"
    pl.DataFrame(
        {
            "region": ["Straße", "North"],
            "year": [2024, 2024],
            "claims": [1.0, 2.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    response = client.post(
        "/api/explore/pivots/members",
        json={
            "graph": graph,
            "node_id": "explore",
            "source": "live",
            "field": "region",
            "search": "Straße",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    # The query is lowered with the same transform Polars applies to the
    # column ("straße"); a casefolded query ("strasse") would never match.
    assert [member["label"] for member in payload["members"]] == ["Straße"]


def test_pivot_renders_integers_beyond_js_safe_range_as_decimal_strings(
    client: TestClient,
    tmp_path: Path,
) -> None:
    path = tmp_path / "big-integers.parquet"
    beyond_js_safe = 2**53 + 111
    pl.DataFrame(
        {
            "region": ["A", "B"],
            "year": [2024, 2024],
            "claims": pl.Series([beyond_js_safe, 7], dtype=pl.Int64),
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        values=[
            {
                "id": "max_claims",
                "field": "claims",
                "aggregation": "max",
                "reference": "claims_max",
                "display_name": "Max claims",
            }
        ],
        options={"row_grand_totals": False, "column_grand_totals": False},
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    column = [("integer", "2024")]
    # A JS JSON.parse would round the raw integer; the canonical decimal string
    # keeps it exact, while safe integers remain JSON numbers.
    assert _cell(result, [("string", "A")], column, "max_claims") == str(beyond_js_safe)
    small = _cell(result, [("string", "B")], column, "max_claims")
    assert small == 7
    assert isinstance(small, int)


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
                "reference": "claims_sum",
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


def test_pivot_applies_only_the_selected_row_direction_and_keeps_missing_last(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["Zulu", "Alpha", "Alpha", None],
            "segment": [2, 10, 2, 1],
            "year": [2024, 2024, 2024, 2024],
            "claims": [4.0, 3.0, 2.0, 1.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        rows=[
            {"id": "row_region", "field": "region", "sort": "descending"},
            {"id": "row_segment", "field": "segment", "sort": "descending"},
        ],
        options={
            "row_grand_totals": False,
            "column_grand_totals": False,
            "sort_by": "row_region",
        },
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    assert [
        [(member["kind"], member["value"]) for member in path["members"]]
        for path in result["row_paths"]
    ] == [
        [("string", "Zulu"), ("integer", "2")],
        [("string", "Alpha"), ("integer", "2")],
        [("string", "Alpha"), ("integer", "10")],
        [("null", None), ("integer", "1")],
    ]


def test_pivot_sorts_rows_by_correct_value_total_without_displaying_totals(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["A", "A", "A", "B", "B", "C", "D"],
            "year": [2024, 2024, 2025, 2024, 2025, 2024, 2024],
            "claims": [0.0, 100.0, 0.0, 30.0, 30.0, None, 30.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    pivot = _pivot(
        rows=[{"id": "row_region", "field": "region", "sort": "descending"}],
        values=[
            {
                "id": "average_claims",
                "field": "claims",
                "aggregation": "average",
                "reference": "claims_mean",
                "display_name": "Average claims",
                "sort_rows": "descending",
                "color_scale": "none",
            }
        ],
        options={
            "row_grand_totals": False,
            "column_grand_totals": False,
            "sort_by": "average_claims",
        },
    )

    final, result = _run_pivot(client, graph, pivot)

    assert final["status"] == "completed", final
    assert result is not None
    # A's displayed year averages sum to 50 while B's sum to 60, but the
    # correct all-years averages are 33.33 and 30. Value sorting must use the
    # re-aggregated row total rather than combining displayed cells. D ties B,
    # so their order follows the default ascending Row-label tie-breaker.
    assert [path["members"][0]["value"] for path in result["row_paths"]] == [
        "A",
        "B",
        "D",
        "C",
    ]
    assert not any(path["is_grand_total"] for path in result["row_paths"])
    assert not any(path["is_grand_total"] for path in result["column_paths"])


def test_pivot_sort_settings_affect_cache_but_formatting_is_presentation_only(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "claims.parquet"
    pl.DataFrame(
        {
            "region": ["North", "South"],
            "year": [2024, 2024],
            "claims": [10.0, 20.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    base_value = {
        "id": "sum_claims",
        "field": "claims",
        "aggregation": "sum",
        "reference": "claims_sum",
        "display_name": "Claims",
        "sort_rows": "none",
        "color_scale": "none",
    }

    first, first_result = _run_pivot(
        client,
        graph,
        _pivot(
            rows=[{"id": "row_1", "field": "region", "sort": "ascending"}],
            values=[base_value],
        ),
    )
    recoloured, recoloured_result = _run_pivot(
        client,
        graph,
        _pivot(
            rows=[{"id": "row_1", "field": "region", "sort": "ascending"}],
            values=[{**base_value, "color_scale": "low_red_high_green"}],
        ),
    )
    split_recoloured, split_recoloured_result = _run_pivot(
        client,
        graph,
        _pivot(
            rows=[{"id": "row_1", "field": "region", "sort": "ascending"}],
            values=[
                {
                    **base_value,
                    "color_scale": "low_red_high_green",
                    "color_scale_split_by": "row_1",
                }
            ],
        ),
    )
    number_formatted, number_formatted_result = _run_pivot(
        client,
        graph,
        _pivot(
            columns=[
                {
                    "id": "column_1",
                    "field": "year",
                    "number_format": "percent",
                    "decimal_places": 0,
                    "use_grouping": False,
                }
            ],
            rows=[
                {
                    "id": "row_1",
                    "field": "region",
                    "sort": "ascending",
                    "number_format": "currency_usd",
                    "decimal_places": 2,
                    "use_grouping": True,
                }
            ],
            values=[
                {
                    **base_value,
                    "number_format": "currency_eur",
                    "decimal_places": 3,
                    "use_grouping": False,
                }
            ],
        ),
    )
    explicitly_selected_default, explicitly_selected_default_result = _run_pivot(
        client,
        graph,
        _pivot(
            rows=[{"id": "row_1", "field": "region", "sort": "ascending"}],
            values=[base_value],
            options={
                "row_grand_totals": True,
                "column_grand_totals": True,
                "sort_by": "row_1",
            },
        ),
    )
    resorted, resorted_result = _run_pivot(
        client,
        graph,
        _pivot(
            rows=[{"id": "row_1", "field": "region", "sort": "descending"}],
            values=[base_value],
            options={
                "row_grand_totals": True,
                "column_grand_totals": True,
                "sort_by": "row_1",
            },
        ),
    )
    value_sorted, value_sorted_result = _run_pivot(
        client,
        graph,
        _pivot(
            rows=[{"id": "row_1", "field": "region", "sort": "ascending"}],
            values=[{**base_value, "sort_rows": "descending"}],
            options={
                "row_grand_totals": True,
                "column_grand_totals": True,
                "sort_by": "sum_claims",
            },
        ),
    )

    assert first["status"] == "completed"
    assert recoloured["cached"] is True
    assert recoloured_result == first_result
    assert split_recoloured["cached"] is True
    assert split_recoloured_result == first_result
    assert number_formatted["cached"] is True
    assert number_formatted_result == first_result
    assert explicitly_selected_default["cached"] is True
    assert explicitly_selected_default_result == first_result
    assert resorted["status"] == "completed"
    assert "cached" not in resorted
    assert resorted_result is not None
    assert first_result is not None
    assert resorted_result["calculation_key"] != first_result["calculation_key"]
    assert [path["members"][0]["value"] for path in resorted_result["row_paths"][:-1]] == [
        "South",
        "North",
    ]
    assert value_sorted["status"] == "completed"
    assert "cached" not in value_sorted
    assert value_sorted_result is not None
    assert value_sorted_result["calculation_key"] != first_result["calculation_key"]
    assert [path["members"][0]["value"] for path in value_sorted_result["row_paths"][:-1]] == [
        "South",
        "North",
    ]
    assert value_sorted_result["row_paths"][-1]["is_grand_total"] is True
    assert value_sorted_result["column_paths"][-1]["is_grand_total"] is True


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

    # Starting a sibling pivot (a different card, therefore a different
    # family) registers it synchronously before its worker runs. If the
    # family key wrongly ignored the pivot id, this registration would
    # supersede the running first job right here — so this is the assertion
    # that fails against that regression.
    sibling = client.post(
        "/api/explore/pivots/run",
        json={
            "graph": graph,
            "node_id": "explore",
            "source": "live",
            "pivot": _pivot(id="pivot_2", name="Sibling pivot"),
        },
    ).json()
    assert sibling["status"] == "started"
    first_after_sibling = client.get(f"/api/explore/pivots/status/{first['job_id']}").json()
    assert first_after_sibling["status"] == "running"

    # The sibling reaches its own terminal state: completed if execution
    # admission had headroom beside the blocked first job, memory_limited if
    # not — never superseded or cancelled by the other family.
    sibling_final = _poll(client, "/api/explore/pivots/status", sibling["job_id"])
    assert sibling_final["status"] in {"completed", "memory_limited"}, sibling_final

    updated = _pivot(
        values=[
            {
                "id": "sum_claims",
                "field": "claims",
                "aggregation": "average",
                "reference": "claims_mean",
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
    _wait_for_pivot_worker(first["job_id"])

    # The same-family supersession above must not have touched the sibling's
    # terminal state or its published result.
    sibling_status = client.get(f"/api/explore/pivots/status/{sibling['job_id']}").json()
    assert sibling_status["status"] == sibling_final["status"]
    assert sibling_status["result"] == sibling_final["result"]


def test_explore_and_pivot_endpoints_reject_each_others_job_ids(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes.explore import _pivot_service

    path = tmp_path / "claims.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    # Force a fresh Explore materialisation job so its id exists in the store.
    explore_run = client.post(
        "/api/explore/run",
        json={"graph": graph, "node_id": "explore", "source": "live", "refresh": True},
    ).json()
    assert explore_run["status"] == "started"
    explore_job_id = explore_run["job_id"]
    assert _poll(client, "/api/explore/status", explore_job_id)["status"] == "completed"

    assert client.get(f"/api/explore/pivots/status/{explore_job_id}").status_code == 404
    assert client.post(f"/api/explore/pivots/cancel/{explore_job_id}").status_code == 404

    original = _pivot_service._calculate
    pivot_started = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def first_call_blocks(spec, context):
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            pivot_started.set()
            while True:
                context.checkpoint(label="pivot_test_cross_kind")
                time.sleep(0.01)
        return original(spec, context)

    monkeypatch.setattr(_pivot_service, "_calculate", first_call_blocks)
    pivot_run = client.post(
        "/api/explore/pivots/run",
        json={"graph": graph, "node_id": "explore", "source": "live", "pivot": _pivot()},
    ).json()
    assert pivot_run["status"] == "started"
    pivot_job_id = pivot_run["job_id"]
    assert pivot_started.wait(3)

    # Explore status/cancel must reject the pivot job id without mutating it:
    # a 404 here proves an Explore cancel can never mark a pivot job cancelled
    # while its calculation keeps running.
    assert client.get(f"/api/explore/status/{pivot_job_id}").status_code == 404
    assert client.post(f"/api/explore/cancel/{pivot_job_id}").status_code == 404
    still_running = client.get(f"/api/explore/pivots/status/{pivot_job_id}").json()
    assert still_running["status"] == "running"

    cancelled = client.post(f"/api/explore/pivots/cancel/{pivot_job_id}").json()
    assert cancelled["status"] == "cancelled"
    _wait_for_pivot_worker(pivot_job_id)


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


def test_pivot_runtime_members_and_exact_filters_cover_all_persisted_kinds(
    client: TestClient, tmp_path: Path
) -> None:
    """The runtime path, not only config validation, preserves every member kind."""
    path = tmp_path / "typed-members.parquet"
    pl.DataFrame(
        {
            "text": [None, "North"],
            "floating": [math.nan, 1.5],
            "flag": [True, False],
            "whole": [7, 8],
            "decimal": pl.Series([Decimal("1.20"), Decimal("2.30")], dtype=pl.Decimal(8, 2)),
            "day": [date(2024, 2, 29), date(2024, 3, 1)],
            "moment": [datetime(2024, 2, 29, 12, 30), datetime(2024, 3, 1, 1, 2)],
            "clock": [datetime_time(12, 30, 0, 123456), datetime_time(1, 2, 3)],
            "claims": [11.0, 22.0],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    cases = [
        ("text", {"kind": "null", "value": None}, 11.0),
        ("text", {"kind": "string", "value": "North"}, 22.0),
        ("floating", {"kind": "nan", "value": None}, 11.0),
        ("floating", {"kind": "float", "value": 1.5}, 22.0),
        ("flag", {"kind": "boolean", "value": True}, 11.0),
        ("whole", {"kind": "integer", "value": "7"}, 11.0),
        ("decimal", {"kind": "decimal", "value": "1.20"}, 11.0),
        ("day", {"kind": "date", "value": "2024-02-29"}, 11.0),
        ("moment", {"kind": "datetime", "value": "2024-02-29T12:30:00"}, 11.0),
        ("clock", {"kind": "time", "value": "12:30:00.123456"}, 11.0),
    ]
    observed_keys: set[tuple[str, Any]] = set()
    for field, member, expected_sum in cases:
        members = client.post(
            "/api/explore/pivots/members",
            json={"graph": graph, "node_id": "explore", "source": "live", "field": field},
        )
        assert members.status_code == 200, members.text
        payload = members.json()
        assert payload["status"] == "ok", payload
        observed_keys.update(
            (item["key"]["kind"], item["key"]["value"]) for item in payload["members"]
        )
        assert member in [item["key"] for item in payload["members"]]

        final, result = _run_pivot(
            client,
            graph,
            _pivot(
                filters=[{"id": f"filter_{field}", "field": field, "members": [member]}],
                rows=[],
                columns=[],
                options={"row_grand_totals": False, "column_grand_totals": False},
            ),
        )
        assert final["status"] == "completed", final
        assert result is not None
        assert _cell(result, [], [], "sum_claims") == expected_sum

    assert {
        ("null", None),
        ("nan", None),
        ("string", "North"),
        ("boolean", True),
        ("integer", "7"),
        ("float", 1.5),
        ("decimal", "1.20"),
        ("date", "2024-02-29"),
        ("datetime", "2024-02-29T12:30:00"),
        ("time", "12:30:00.123456"),
    } <= observed_keys


@pytest.mark.parametrize(
    ("pivot_updates", "reason_code", "dimensions"),
    [
        (
            {
                "filters": [
                    {
                        "id": "filter_1",
                        "field": "whole",
                        "members": [{"kind": "string", "value": "7"}],
                    }
                ]
            },
            "invalid_pivot_filter_member",
            {"field": "whole", "member_kind": "string"},
        ),
        (
            {"rows": [{"id": "row_1", "field": "nested"}]},
            "invalid_pivot_field",
            {"field": "nested"},
        ),
        (
            {
                "values": [
                    {
                        "id": "sum_text",
                        "field": "text",
                        "aggregation": "sum",
                        "reference": "text_sum",
                        "display_name": "Text",
                    }
                ]
            },
            "invalid_pivot_field",
            {"field": "text"},
        ),
        ({"values": []}, "pivot_unconfigured", {}),
    ],
)
def test_pivot_runtime_rejections_are_typed_and_remediable(
    client: TestClient,
    tmp_path: Path,
    pivot_updates: dict[str, Any],
    reason_code: str,
    dimensions: dict[str, Any],
) -> None:
    path = tmp_path / "invalid-pivot-runtime.parquet"
    pl.DataFrame(
        {
            "region": ["North"],
            "year": [2024],
            "claims": [10.0],
            "whole": [7],
            "text": ["not numeric"],
            "nested": [["not groupable"]],
        }
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)

    final, result = _run_pivot(client, graph, _pivot(**pivot_updates))

    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == reason_code
    assert final["failure"]["dimensions"] == dimensions
    assert final["failure"]["remediation"]


@pytest.mark.parametrize(
    ("limit_name", "pivot", "expected_dimension", "at_limit", "above_limit"),
    [
        (
            "MAX_ROW_GROUPS",
            _pivot(columns=[], options={"row_grand_totals": False, "column_grand_totals": False}),
            "row_groups",
            2,
            1,
        ),
        (
            "MAX_COLUMN_GROUPS",
            _pivot(rows=[], options={"row_grand_totals": False, "column_grand_totals": False}),
            "column_groups",
            2,
            1,
        ),
        (
            "MAX_DISPLAY_CELLS",
            _pivot(options={"row_grand_totals": False, "column_grand_totals": False}),
            "display_cells",
            4,
            3,
        ),
    ],
)
def test_pivot_cardinality_limits_accept_the_boundary_and_reject_one_above(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    pivot: dict[str, Any],
    expected_dimension: str,
    at_limit: int,
    above_limit: int,
) -> None:
    import haute.routes._pivot_service as pivot_service

    path = tmp_path / f"{expected_dimension}.parquet"
    pl.DataFrame(
        {"region": ["North", "South"], "year": [2024, 2025], "claims": [10.0, 20.0]}
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    monkeypatch.setattr(pivot_service, limit_name, at_limit)
    boundary, boundary_result = _run_pivot(client, graph, pivot)
    assert boundary["status"] == "completed", boundary
    assert boundary_result is not None

    monkeypatch.setattr(pivot_service, limit_name, above_limit)
    final, result = _run_pivot(
        client,
        graph,
        _pivot(**{**pivot, "id": f"{expected_dimension}_above_limit"}),
    )
    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "pivot_cardinality_limit"
    assert final["failure"]["remediation"] == "Reduce pivot dimensions or filter the dataset."
    assert final["failure"]["dimensions"] == {
        "dimension": expected_dimension,
        "actual": at_limit,
        "limit": above_limit,
    }


def test_pivot_filter_member_limits_apply_to_calculation_and_members_endpoint(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes._pivot_service as pivot_service

    path = tmp_path / "filter-members.parquet"
    pl.DataFrame(
        {"region": ["North", "South"], "year": [2024, 2024], "claims": [10.0, 20.0]}
    ).write_parquet(path)
    graph = _graph(path)
    _materialise(client, graph)
    members = [{"kind": "string", "value": value} for value in ["North", "South"]]

    monkeypatch.setattr(pivot_service, "MAX_FILTER_MEMBERS", 2)
    boundary, boundary_result = _run_pivot(
        client, graph, _pivot(filters=[{"id": "filter_1", "field": "region", "members": members}])
    )
    assert boundary["status"] == "completed", boundary
    assert boundary_result is not None
    endpoint_boundary = client.post(
        "/api/explore/pivots/members",
        json={"graph": graph, "node_id": "explore", "source": "live", "field": "region"},
    ).json()
    assert endpoint_boundary["status"] == "ok"

    monkeypatch.setattr(pivot_service, "MAX_FILTER_MEMBERS", 1)
    final, result = _run_pivot(
        client,
        graph,
        _pivot(
            id="filter_members_above_limit",
            filters=[{"id": "filter_1", "field": "region", "members": members}],
        ),
    )
    assert final["status"] == "contract_error"
    assert result is None
    assert final["failure"]["reason_code"] == "pivot_cardinality_limit"
    assert final["failure"]["dimensions"] == {
        "dimension": "filter_members",
        "actual": 2,
        "limit": 1,
    }
    endpoint_above = client.post(
        "/api/explore/pivots/members",
        json={"graph": graph, "node_id": "explore", "source": "live", "field": "region"},
    ).json()
    assert endpoint_above["status"] == "error"
    assert endpoint_above["failure"]["reason_code"] == "pivot_cardinality_limit"
    assert endpoint_above["failure"]["dimensions"] == {
        "dimension": "filter_members",
        "actual": 2,
        "limit": 1,
    }


def test_pivot_named_sources_require_and_preserve_their_own_materialised_cache(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "named-sources.parquet"
    pl.DataFrame({"region": ["North"], "year": [2024], "claims": [10.0]}).write_parquet(path)
    graph = _graph(path)
    live_report = _materialise(client, graph, source="live")

    missing, missing_result = _run_pivot(client, graph, _pivot(), source="named")
    assert missing["status"] == "cache_required"
    assert missing_result is None

    named_report = _materialise(client, graph, source="named")
    named_final, named_result = _run_pivot(client, graph, _pivot(), source="named")
    live_final, live_result = _run_pivot(client, graph, _pivot(), source="live")

    assert named_final["status"] == "completed", named_final
    assert named_result is not None
    assert named_result["source"] == "named"
    assert named_result["dataframe_cache_key"] == named_report["dataframe_cache_key"]
    assert named_report["dataframe_cache_key"] != live_report["dataframe_cache_key"]
    assert live_final["status"] == "completed", live_final
    assert live_result is not None
    assert live_result["source"] == "live"
    assert live_result["dataframe_cache_key"] == live_report["dataframe_cache_key"]
