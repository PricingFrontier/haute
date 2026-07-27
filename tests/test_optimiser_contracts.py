"""Contract-heavy tests for optimiser node data boundaries."""

from __future__ import annotations

import ast
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute.executor import _build_node_fn
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import OptimiserSolveService
from haute.schemas import OptimiserFrontierRequest
from tests.conftest import make_edge, make_file_input_config, make_graph, make_node

ROOT = Path(__file__).resolve().parents[1]


def test_optimiser_service_never_reads_job_store_backing_mapping() -> None:
    source_path = ROOT / "src" / "haute" / "routes" / "_optimiser_service.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "jobs"
    ]

    assert offenders == [], (
        "Optimiser workers must read through JobStore.get_job() so concurrent "
        f"eviction cannot race an unlocked backing-mapping access: {offenders}"
    )


@pytest.fixture()
def clean_job_store():
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


def _poll_solve_status(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] != "running":
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Solve job {job_id} did not finish within {timeout}s")


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
    from haute._polars_utils import bounded_sink

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
    bounded_sink(expand(source), str(out_path))
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
                        "nodeType": "dataInput",
                        "config": make_file_input_config(data_path),
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
                            "contract": {
                                "inputs": ["base_income", "base_volume"],
                                "outputs": [
                                    "scenario_index",
                                    "scenario_value",
                                    "expected_income",
                                    "volume",
                                    "unused_payload",
                                ],
                            },
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
    grid_captured = threading.Event()

    def capture_grid(scored_lf, constraint_cols, config, node_id, job_id, **_kwargs):
        captured["df"] = scored_lf.collect()
        captured["constraint_cols"] = constraint_cols
        captured["config"] = dict(config)
        grid_captured.set()
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
        assert grid_captured.wait(timeout=2.0)

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


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_ratebook_solve_preserves_non_source_banding_input_after_target_checkpoint(
    client,
    tmp_path,
    clean_job_store,
) -> None:
    """Ratebook side inputs must survive optimiser target checkpoint cleanup."""
    scored_path = tmp_path / "scored.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 110.0, 200.0, 220.0], dtype=pl.Float32),
            "volume": pl.Series([1.0, 0.9, 1.1, 1.0], dtype=pl.Float32),
        }
    ).write_parquet(scored_path)
    banding_path = tmp_path / "banding.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "region": ["North", "South"],
        }
    ).write_parquet(banding_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "scored",
                    "data": {
                        "label": "scored",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(scored_path),
                    },
                },
                {
                    "id": "banding_source",
                    "data": {
                        "label": "banding source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(banding_path),
                    },
                },
                {
                    "id": "banding_transform",
                    "data": {
                        "label": "banding transform",
                        "nodeType": "polars",
                        "config": {"code": ""},
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "ratebook",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 1.0}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "data_input": "scored",
                            "banding_source": "banding_transform",
                            "factor_columns": [["region"]],
                            "chunk_size": 4,
                        },
                    },
                },
            ],
            "edges": [
                make_edge("scored", "opt").model_dump(),
                make_edge("banding_source", "banding_transform").model_dump(),
                make_edge("banding_transform", "opt").model_dump(),
            ],
        }
    ).model_dump()
    captured: dict[str, object] = {}
    launched = threading.Event()

    def capture_launch(ctx, *, config, quote_grid, ratebook_factors_handle, **kwargs):
        captured["job_id"] = ctx.job_id
        captured["mode"] = ctx.mode
        captured["ratebook_factors"] = ratebook_factors_handle
        launched.set()

    from haute.routes import optimiser as optimiser_routes

    with (
        patch.object(optimiser_routes._solve_service, "_build_grid", return_value=MagicMock()),
        patch.object(
            optimiser_routes._solve_service,
            "_launch_background",
            side_effect=capture_launch,
        ),
    ):
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert launched.wait(timeout=2.0)

    assert resp.status_code == 200
    assert captured["mode"] == "ratebook"
    ratebook_factors = captured["ratebook_factors"]
    assert isinstance(ratebook_factors, dict)
    factors_df = pl.read_parquet(ratebook_factors["path"])
    assert factors_df.select("quote_id", "region").to_dicts() == [
        {"quote_id": "q1", "region": "North"},
        {"quote_id": "q2", "region": "South"},
    ]


def test_ratebook_factor_extraction_uses_execution_context_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_context import ExecutionContext, ExecutionProfile
    from haute.routes import _optimiser_service as optimiser_service

    context = ExecutionContext(
        operation="optimiser_solve",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_sampler=lambda: 1_000,
    )
    calls: list[ExecutionProfile | str] = []

    def fail_if_streaming_collect_reached(*_args, **_kwargs) -> pl.DataFrame:
        calls.append("streaming_collect")
        raise AssertionError("ratebook factor extraction must not collect the full frame")

    monkeypatch.setattr(optimiser_service, "streaming_collect", fail_if_streaming_collect_reached)

    factors_handle = optimiser_service.OptimiserSolveService._extract_factors(
        {
            "banding": pl.LazyFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["North", "South"],
                }
            )
        },
        {
            "mode": "ratebook",
            "banding_source": "banding",
            "factor_columns": [["region"]],
        },
        "ratebook",
        execution_context=context,
    )

    assert pl.read_parquet(factors_handle["path"]).to_dicts() == [
        {"quote_id": "q1", "region": "North"},
        {"quote_id": "q2", "region": "South"},
    ]
    assert calls == []
    assert "optimiser_extract_factors" in context.metrics_summary().stage_elapsed_ms


def test_ratebook_factor_source_sinks_without_final_collect_under_low_memory_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_context import (
        ExecutionContext,
        ExecutionProfile,
    )
    from haute.routes import _optimiser_service as optimiser_service

    context = ExecutionContext(
        operation="optimiser_solve",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_limit_bytes=1,
        memory_baseline_bytes=0,
        rss_limit_bytes=1,
        memory_sampler=lambda: 0,
    )

    def fail_if_collect_reached(*_args, **_kwargs):
        raise AssertionError("factor extraction should reject before final collect")

    monkeypatch.setattr(optimiser_service, "streaming_collect", fail_if_collect_reached)

    factors_handle = optimiser_service.OptimiserSolveService._extract_factors(
        {
            "banding": pl.LazyFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["North", "South"],
                }
            )
        },
        {
            "mode": "ratebook",
            "banding_source": "banding",
            "factor_columns": [["region"]],
        },
        "ratebook",
        execution_context=context,
    )

    assert factors_handle["row_count"] == 2


def test_optimiser_projection_rule_is_not_hard_coded_in_lazy_executor() -> None:
    source = (ROOT / "src/haute/_execute_lazy.py").read_text(encoding="utf-8")

    assert re.search(r"NodeType\.OPTIMISER(?!_)", source) is None


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
    assert job["status"] == "contract_error"
    assert job["terminal_reason"] == "contract_error"
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
                        "nodeType": "dataInput",
                        "config": make_file_input_config(source_path),
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
        assert resp.status_code == 200
        status = _poll_solve_status(client, resp.json()["job_id"])

    assert status["status"] == "contract_error"
    assert "Null quote_id values found in optimiser input (2 rows)." in status["message"]


def test_build_grid_sanitises_unknown_interleaved_quote_failure() -> None:
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

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Grid construction failed. Check the server logs for details."
    assert "contiguous rows" not in exc_info.value.detail
    assert "scenario_index order" not in exc_info.value.detail
    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert job["terminal_reason"] == "error"


@pytest.mark.parametrize(
    ("threshold_ranges", "message"),
    [
        ({"volume": [0.9]}, "must contain min and max values"),
        ({"volume": [0.8, 0.9, 1.0]}, "must contain min and max values"),
        ({"volume": [1.1, 0.9]}, "min must be less than or equal to max"),
        ({"volume": [0.9, float("inf")]}, "must contain finite min and max values"),
    ],
)
def test_explicit_frontier_ranges_rejected_at_schema_layer(
    threshold_ranges,
    message,
) -> None:
    """Schema validator rejects malformed ranges with the same wording as the
    config-side path, so request-body and saved-config UX agree."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        OptimiserFrontierRequest(
            job_id="job",
            threshold_ranges=threshold_ranges,
        )

    # Pydantic prefixes the field path; the underlying message is preserved.
    assert message in str(exc_info.value)


def test_explicit_frontier_route_rejects_bad_ranges_without_calling_solver(
    client,
    clean_job_store,
) -> None:
    """End-to-end: the solver must never be invoked for malformed ranges.

    FastAPI surfaces Pydantic validation as 422; the previous 400 came from
    the duplicated runtime check that has now been removed.
    """
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

    assert resp.status_code == 422
    assert "min must be less than or equal to max" in resp.text
    solver.frontier.assert_not_called()


@pytest.mark.usefixtures("_widen_sandbox_root")
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
                        "nodeType": "dataInput",
                        "config": make_file_input_config(source_path),
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
    assert applied["total_objective"] == pytest.approx(selected_source["expected_income"].sum())
    assert applied["constraints"]["volume"] == pytest.approx(selected_source["volume"].sum())
