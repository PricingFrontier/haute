"""Contract-heavy tests for optimiser node data boundaries."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute.executor import _build_node_fn
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import OptimiserSolveService
from haute.schemas import OptimiserFrontierRequest
from tests.conftest import make_edge, make_graph, make_node


@pytest.fixture()
def clean_job_store():
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


def _assert_quote_scenario_blocks(
    df: pl.DataFrame,
    *,
    quote_col: str = "quote_id",
    scenario_col: str = "scenario_index",
    expected_steps: int,
) -> None:
    rows = df.select(quote_col, scenario_col).to_dicts()
    quotes_in_order: list[str] = []
    seen: set[str] = set()

    offset = 0
    while offset < len(rows):
        quote_id = rows[offset][quote_col]
        assert quote_id not in seen, f"{quote_id!r} appears in more than one block"
        seen.add(quote_id)
        quotes_in_order.append(quote_id)

        block = rows[offset : offset + expected_steps]
        assert len(block) == expected_steps
        assert [row[quote_col] for row in block] == [quote_id] * expected_steps
        assert [row[scenario_col] for row in block] == list(range(expected_steps))
        offset += expected_steps

    assert len(rows) == len(quotes_in_order) * expected_steps


def test_scenario_expander_streaming_output_is_quote_contiguous_for_optimiser(
    tmp_path,
) -> None:
    """Streaming parquet output must keep one ordered scenario block per quote."""
    from haute._polars_utils import safe_sink

    node = make_node(
        {
            "id": "expander",
            "data": {
                "label": "expander",
                "nodeType": "scenarioExpander",
                "config": {
                    "quote_id": "quote_id",
                    "column_name": "scenario_value",
                    "min_value": 0.8,
                    "max_value": 1.1,
                    "steps": 4,
                    "step_column": "scenario_index",
                },
            },
        }
    )
    _, expand, _ = _build_node_fn(node, source_names=["source"])
    source = pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "base_income": [100.0, 200.0, 300.0],
        }
    ).lazy()

    out_path = tmp_path / "expanded.parquet"
    safe_sink(expand(source), str(out_path))
    expanded = pl.read_parquet(out_path)

    assert expanded["scenario_index"].dtype == pl.Int32
    assert expanded["scenario_value"].dtype == pl.Float32
    assert expanded["quote_id"].to_list() == [
        "q1",
        "q1",
        "q1",
        "q1",
        "q2",
        "q2",
        "q2",
        "q2",
        "q3",
        "q3",
        "q3",
        "q3",
    ]
    _assert_quote_scenario_blocks(expanded, expected_steps=4)


def _make_expander_optimiser_graph(data_path: str) -> dict:
    graph = make_graph(
        {
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
                    "id": "expander",
                    "data": {
                        "label": "expander",
                        "nodeType": "scenarioExpander",
                        "config": {
                            "column_name": "scenario_value",
                            "min_value": 0.8,
                            "max_value": 1.2,
                            "steps": 5,
                            "step_column": "scenario_index",
                            "code": (
                                "df = df.with_columns([\n"
                                "    (pl.col('base_income') * pl.col('scenario_value'))"
                                ".alias('expected_income'),\n"
                                "    (pl.col('base_volume') * "
                                "(2.0 - pl.col('scenario_value'))).alias('volume'),\n"
                                "    pl.lit('wide-unused').alias('unused_payload'),\n"
                                "])"
                            ),
                        },
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "online",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.9}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "data_input": "expander",
                            "max_iter": 20,
                            "tolerance": 1e-4,
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "expander").model_dump(),
                make_edge("expander", "opt").model_dump(),
            ],
        }
    )
    return graph.model_dump()


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_optimiser_receives_slim_quote_contiguous_expander_projection(
    client,
    tmp_path,
    clean_job_store,
) -> None:
    """The optimiser boundary should receive expanded, slim, typed solver columns."""
    source_path = tmp_path / "quotes.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "base_income": [100.0, 200.0, 300.0],
            "base_volume": [1.0, 1.1, 1.2],
            "wide_unused_before_expansion": ["a", "b", "c"],
        }
    ).write_parquet(source_path)
    captured: dict[str, object] = {}

    def capture_grid(scored_lf, constraint_cols, config, node_id, job_id):
        captured["df"] = scored_lf.collect()
        captured["constraint_cols"] = constraint_cols
        captured["config"] = dict(config)
        return MagicMock()

    from haute.routes import optimiser as optimiser_routes

    with (
        patch.object(
            optimiser_routes._solve_service,
            "_build_grid",
            side_effect=capture_grid,
        ),
        patch.object(optimiser_routes._solve_service, "_launch_background"),
    ):
        resp = client.post(
            "/api/optimiser/solve",
            json={
                "graph": _make_expander_optimiser_graph(str(source_path)),
                "node_id": "opt",
            },
        )

    assert resp.status_code == 200
    assert captured["constraint_cols"] == ["volume"]
    projected = captured["df"]
    assert isinstance(projected, pl.DataFrame)
    assert projected.columns == [
        "quote_id",
        "scenario_index",
        "scenario_value",
        "expected_income",
        "volume",
    ]
    assert projected["quote_id"].dtype == pl.Categorical
    assert projected["scenario_index"].dtype == pl.Int32
    assert projected["scenario_value"].dtype == pl.Float32
    assert projected["expected_income"].dtype == pl.Float32
    assert projected["volume"].dtype == pl.Float32
    _assert_quote_scenario_blocks(projected, expected_steps=5)


def test_validate_and_project_keeps_only_price_contour_columns() -> None:
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", "q1"],
            "scenario_index": [0, 1],
            "scenario_value": [0.9, 1.1],
            "expected_income": [100.0, 110.0],
            "volume": [0.9, 0.8],
            "wide_unused_1": ["drop", "drop"],
            "wide_unused_2": [999.0, 999.0],
        }
    )

    constraint_cols, scored_lf = service._validate_and_project(
        source_lf,
        {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        },
        job_id,
    )

    projected = scored_lf.collect()
    assert constraint_cols == ["volume"]
    assert projected.columns == [
        "quote_id",
        "scenario_index",
        "scenario_value",
        "expected_income",
        "volume",
    ]
    assert projected["quote_id"].dtype == pl.Categorical
    assert projected["scenario_index"].dtype == pl.Int32
    assert projected["scenario_value"].dtype == pl.Float32
    assert projected["expected_income"].dtype == pl.Float32
    assert projected["volume"].dtype == pl.Float32


def test_validate_and_project_rejects_null_quote_id_loudly() -> None:
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", "q1", None, None, "q2", "q2"],
            "scenario_index": [0, 1, 0, 1, 0, 1],
            "scenario_value": [0.9, 1.1, 0.9, 1.1, 0.9, 1.1],
            "expected_income": [100.0, 110.0, 999.0, 999.0, 200.0, 220.0],
            "volume": [0.9, 0.8, 0.1, 0.1, 0.95, 0.9],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._validate_and_project(
            source_lf,
            {
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.9}},
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
            },
            job_id,
        )

    expected = "Null quote_id values found in optimiser input (2 rows)."
    assert exc_info.value.status_code == 400
    assert expected in exc_info.value.detail
    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert expected in job["message"]


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_solve_rejects_null_quote_id_instead_of_dropping_rows(
    client,
    tmp_path,
    clean_job_store,
) -> None:
    """A null quote_id is invalid optimiser input, not a row to silently drop."""
    source_path = tmp_path / "scored.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", None, None, "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1, 0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series(
                [100.0, 110.0, 999.0, 999.0, 200.0, 220.0],
                dtype=pl.Float32,
            ),
            "volume": pl.Series([0.9, 0.8, 0.1, 0.1, 0.95, 0.9], dtype=pl.Float32),
        }
    ).write_parquet(source_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": str(source_path)},
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "online",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.9}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "chunk_size": 10,
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    ).model_dump()

    from haute.routes import optimiser as optimiser_routes

    with patch.object(optimiser_routes._solve_service, "_launch_background"):
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )

    assert resp.status_code == 400
    assert "Null quote_id values found in optimiser input (2 rows)." in resp.json()["detail"]


def test_build_grid_rejects_interleaved_quote_blocks_loudly() -> None:
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    scored_lf = pl.LazyFrame(
        {
            "quote_id": pl.Series(["q1", "q2", "q1", "q2"], dtype=pl.Categorical),
            "scenario_index": pl.Series([0, 0, 1, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 0.9, 1.1, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 200.0, 110.0, 220.0], dtype=pl.Float32),
            "volume": pl.Series([0.9, 0.95, 0.8, 0.9], dtype=pl.Float32),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._build_grid(
            scored_lf,
            ["volume"],
            {
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.9}},
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
                "chunk_size": 10,
            },
            "opt",
            job_id,
        )

    assert exc_info.value.status_code == 400
    assert "contiguous rows" in exc_info.value.detail
    assert "scenario_index order" in exc_info.value.detail
    assert store.require_job(job_id)["status"] == "error"


@pytest.mark.parametrize(
    ("threshold_ranges", "message"),
    [
        ({"volume": [0.9]}, "must contain min and max values"),
        ({"volume": [0.8, 0.9, 1.0]}, "must contain min and max values"),
        ({"volume": [1.1, 0.9]}, "min must be less than or equal to max"),
        ({"volume": [0.9, float("inf")]}, "must contain finite min and max values"),
    ],
)
def test_explicit_frontier_ranges_are_validated_before_solver_call(
    threshold_ranges,
    message,
) -> None:
    from haute.routes.optimiser import _frontier_ranges_for_request

    with pytest.raises(HTTPException) as exc_info:
        _frontier_ranges_for_request(
            OptimiserFrontierRequest(
                job_id="job",
                threshold_ranges=threshold_ranges,
            ),
            {
                "status": "completed",
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "created_at": time.time(),
            },
        )

    assert exc_info.value.status_code == 400
    assert message in exc_info.value.detail


def test_explicit_frontier_route_rejects_bad_ranges_without_calling_solver(
    client,
    clean_job_store,
) -> None:
    solver = MagicMock()
    clean_job_store.jobs["frontier_bad_ranges"] = {
        "status": "completed",
        "solver": solver,
        "quote_grid": MagicMock(),
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "created_at": time.time(),
    }

    resp = client.post(
        "/api/optimiser/frontier",
        json={
            "job_id": "frontier_bad_ranges",
            "threshold_ranges": {"volume": [1.1, 0.9]},
        },
    )

    assert resp.status_code == 400
    assert "min must be less than or equal to max" in resp.json()["detail"]
    solver.frontier.assert_not_called()


def test_real_solve_apply_totals_match_selected_rows(
    client,
    tmp_path,
    clean_job_store,
) -> None:
    source_path = tmp_path / "deterministic_scored.parquet"
    source_df = pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 2, 0, 1, 2], dtype=pl.Int32),
            "scenario_value": pl.Series([0.8, 1.0, 1.2, 0.8, 1.0, 1.2], dtype=pl.Float32),
            "expected_income": pl.Series(
                [100.0, 130.0, 120.0, 80.0, 90.0, 140.0],
                dtype=pl.Float32,
            ),
            "volume": pl.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=pl.Float32),
        }
    )
    source_df.write_parquet(source_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": str(source_path)},
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "online",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 1.5}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "chunk_size": 6,
                            "max_iter": 50,
                            "tolerance": 1e-6,
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    ).model_dump()

    solve_resp = client.post(
        "/api/optimiser/solve",
        json={"graph": graph, "node_id": "opt"},
    )
    assert solve_resp.status_code == 200
    job_id = solve_resp.json()["job_id"]

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status_resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()
        if status["status"] in {"completed", "error"}:
            break
        time.sleep(0.02)
    else:  # pragma: no cover - defensive timeout message
        raise AssertionError("Optimiser solve did not finish")
    assert status["status"] == "completed", status.get("message")

    apply_resp = client.post("/api/optimiser/apply", json={"job_id": job_id})
    assert apply_resp.status_code == 200
    applied = apply_resp.json()
    selected_df = pl.DataFrame(applied["preview"])
    assert selected_df.height == 2
    assert selected_df["quote_id"].to_list() == ["q1", "q2"]

    selected_source = selected_df.select("quote_id", "optimal_step").join(
        source_df,
        left_on=["quote_id", "optimal_step"],
        right_on=["quote_id", "scenario_index"],
        how="inner",
    )
    assert selected_source.height == 2
    assert applied["total_objective"] == pytest.approx(
        selected_source["expected_income"].sum()
    )
    assert applied["constraints"]["volume"] == pytest.approx(
        selected_source["volume"].sum()
    )
