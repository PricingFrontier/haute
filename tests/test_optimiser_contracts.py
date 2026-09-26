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

from haute.codegen import graph_to_code
from haute.errors import ConfigError
from haute.executor import _build_node_fn
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import OptimiserSolveService
from haute.schemas import OptimiserFrontierRequest
from tests.conftest import (
    make_edge,
    make_graph,
    make_node,
    make_ready_file_input_config,
)
from tests.job_store_support import seed_job

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

    _store.clear_all()
    yield _store
    _store.clear_all()


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
                    "stepCount": 4,
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


def test_optimiser_data_input_uses_exact_connected_frame_name() -> None:
    node = make_node(
        {
            "id": "optimiser",
            "data": {
                "label": "optimiser",
                "nodeType": "optimiser",
                "config": {"data_input": "selected_frame"},
            },
        }
    )
    _, fn, _ = _build_node_fn(
        node,
        source_names=["other_frame", "selected_frame"],
        # Distinct frame identities may originate from one API node.
        source_ids=["api_request", "api_request"],
    )

    other = pl.DataFrame({"chosen": [False]}).lazy()
    selected = pl.DataFrame({"chosen": [True]}).lazy()

    assert fn(other, selected).collect()["chosen"].to_list() == [True]


def test_optimiser_data_input_rejects_non_exact_connected_name() -> None:
    node = make_node(
        {
            "id": "optimiser",
            "data": {
                "label": "optimiser",
                "nodeType": "optimiser",
                "config": {"data_input": "api_request"},
            },
        }
    )
    with pytest.raises(ConfigError, match="api_request"):
        _build_node_fn(
            node,
            source_names=["quotes_frame", "drivers_frame"],
            source_ids=["api_request", "api_request"],
        )


def test_optimiser_data_input_rejects_falsey_non_string_selector() -> None:
    node = make_node(
        {
            "id": "optimiser",
            "data": {
                "label": "optimiser",
                "nodeType": "optimiser",
                "config": {"data_input": 0},
            },
        }
    )

    with pytest.raises(ConfigError, match="incoming-edge frame name"):
        _build_node_fn(node, source_names=["quotes_frame"])


@pytest.mark.parametrize("removed_key", ["scored_input", "factors_input"])
def test_optimiser_runtime_rejects_removed_input_selector_fields(removed_key: str) -> None:
    node = make_node(
        {
            "id": "optimiser",
            "data": {
                "label": "optimiser",
                "nodeType": "optimiser",
                "config": {removed_key: "legacy-node-id"},
            },
        }
    )

    with pytest.raises(ConfigError, match=removed_key):
        _build_node_fn(node, source_names=["quotes_frame"])


def test_optimiser_requires_exact_data_input_when_multiple_frames_are_connected() -> None:
    node = make_node(
        {
            "id": "optimiser",
            "data": {
                "label": "optimiser",
                "nodeType": "optimiser",
                "config": {},
            },
        }
    )

    with pytest.raises(ConfigError, match="data_input is required when multiple"):
        _build_node_fn(node, source_names=["quotes_frame", "drivers_frame"])


def test_optimiser_codegen_returns_the_exact_selected_api_frame() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api_request",
                    "data": {
                        "label": "Request",
                        "nodeType": "apiInput",
                        "config": {},
                    },
                },
                {
                    "id": "optimiser",
                    "data": {
                        "label": "Optimiser",
                        "nodeType": "optimiser",
                        "config": {"data_input": "quote_info"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "drivers",
                    "source": "api_request",
                    "sourceHandle": "driver_info",
                    "target": "optimiser",
                },
                {
                    "id": "quotes",
                    "source": "api_request",
                    "sourceHandle": "quote_info",
                    "target": "optimiser",
                },
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="optimiser_identity")

    assert "def Optimiser(driver_info: pl.LazyFrame, quote_info: pl.LazyFrame)" in code
    assert "    return quote_info" in code


def test_optimiser_codegen_rejects_node_id_selector_for_multi_frame_api_input() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api_request",
                    "data": {
                        "label": "Request",
                        "nodeType": "apiInput",
                        "config": {},
                    },
                },
                {
                    "id": "optimiser",
                    "data": {
                        "label": "Optimiser",
                        "nodeType": "optimiser",
                        "config": {"data_input": "api_request"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "drivers",
                    "source": "api_request",
                    "sourceHandle": "driver_info",
                    "target": "optimiser",
                },
                {
                    "id": "quotes",
                    "source": "api_request",
                    "sourceHandle": "quote_info",
                    "target": "optimiser",
                },
            ],
        }
    )

    with pytest.raises(ConfigError, match="api_request"):
        graph_to_code(graph, pipeline_name="optimiser_identity")


def test_optimiser_codegen_rejects_whitespace_only_selector_instead_of_inferring() -> None:
    """A whitespace-only selector is a stale value, never an absent one."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "Source",
                        "nodeType": "polars",
                        "config": {},
                    },
                },
                {
                    "id": "optimiser",
                    "data": {
                        "label": "Optimiser",
                        "nodeType": "optimiser",
                        "config": {"data_input": " "},
                    },
                },
            ],
            "edges": [
                {
                    "id": "source-to-optimiser",
                    "source": "source",
                    "target": "optimiser",
                },
            ],
        }
    )

    with pytest.raises(ConfigError, match="not an exact connected input name"):
        graph_to_code(graph, pipeline_name="optimiser_identity")


def test_optimiser_codegen_rejects_falsey_non_string_selector() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "Source",
                        "nodeType": "polars",
                        "config": {},
                    },
                },
                {
                    "id": "optimiser",
                    "data": {
                        "label": "Optimiser",
                        "nodeType": "optimiser",
                        "config": {"data_input": 0},
                    },
                },
            ],
            "edges": [
                {
                    "id": "source-to-optimiser",
                    "source": "source",
                    "target": "optimiser",
                },
            ],
        }
    )

    with pytest.raises(ConfigError, match="incoming-edge frame name"):
        graph_to_code(graph, pipeline_name="optimiser_identity")


@pytest.mark.parametrize("removed_key", ["scored_input", "factors_input"])
def test_optimiser_codegen_rejects_removed_input_selector_fields(removed_key: str) -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "Source",
                        "nodeType": "polars",
                        "config": {},
                    },
                },
                {
                    "id": "optimiser",
                    "data": {
                        "label": "Optimiser",
                        "nodeType": "optimiser",
                        "config": {removed_key: "legacy-node-id"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "source-to-optimiser",
                    "source": "source",
                    "target": "optimiser",
                },
            ],
        }
    )

    with pytest.raises(ConfigError, match=removed_key):
        graph_to_code(graph, pipeline_name="optimiser_identity")


def _make_expander_optimiser_graph(data_path: str) -> dict:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(data_path),
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
                            "stepCount": 5,
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
        return setup_grid_stub(MagicMock())

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
        assert grid_captured.wait(timeout=10.0)

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
                        "config": make_ready_file_input_config(scored_path),
                    },
                },
                {
                    "id": "banding_source",
                    "data": {
                        "label": "banding source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(banding_path),
                    },
                },
                {
                    "id": "banding_transform",
                    "data": {
                        "label": "banding transform",
                        "nodeType": "polars",
                        "config": {"code": "df = banding_source"},
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

    def capture_launch(ctx, *, config, quote_grid, ratebook_factors_handle, **kwargs):
        captured["job_id"] = ctx.job_id
        captured["mode"] = ctx.mode
        captured["ratebook_factors"] = ratebook_factors_handle

    from haute.routes import optimiser as optimiser_routes

    with (
        patch.object(optimiser_routes._solve_service, "_launch_setup_background") as setup_launch,
        patch.object(
            optimiser_routes._solve_service,
            "_build_grid",
            return_value=setup_grid_stub(MagicMock()),
        ),
        patch.object(
            optimiser_routes._solve_service,
            "_launch_background",
            side_effect=capture_launch,
        ) as solver_launch,
    ):
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        assert resp.status_code == 200
        setup_launch.assert_called_once()
        # This checks the data contract, so drive the queued setup to completion
        # without making checkpoint I/O race a two-second wall-clock deadline.
        optimiser_routes._solve_service._run_solve_setup_and_launch(
            *setup_launch.call_args.args, **setup_launch.call_args.kwargs
        )
        solver_launch.assert_called_once()

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

    config = {
        "mode": "ratebook",
        "banding_source": "banding",
        "factor_columns": [["region"]],
    }
    graph = make_graph(
        {
            "nodes": [
                {"id": "banding", "data": {"label": "banding", "nodeType": "banding"}},
                {
                    "id": "optimiser",
                    "data": {"label": "optimiser", "nodeType": "optimiser", "config": config},
                },
            ],
            "edges": [{"id": "factor-edge", "source": "banding", "target": "optimiser"}],
        }
    )
    factors_handle = optimiser_service.OptimiserSolveService._extract_factors(
        {
            "banding": pl.LazyFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["North", "South"],
                }
            )
        },
        graph,
        "optimiser",
        config,
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

    config = {
        "mode": "ratebook",
        "banding_source": "banding",
        "factor_columns": [["region"]],
    }
    graph = make_graph(
        {
            "nodes": [
                {"id": "banding", "data": {"label": "banding", "nodeType": "banding"}},
                {
                    "id": "optimiser",
                    "data": {"label": "optimiser", "nodeType": "optimiser", "config": config},
                },
            ],
            "edges": [{"id": "factor-edge", "source": "banding", "target": "optimiser"}],
        }
    )
    factors_handle = optimiser_service.OptimiserSolveService._extract_factors(
        {
            "banding": pl.LazyFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["North", "South"],
                }
            )
        },
        graph,
        "optimiser",
        config,
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
                        "config": make_ready_file_input_config(source_path),
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
    seed_job(
        clean_job_store,
        "frontier_bad_ranges",
        {
            "status": "completed",
            "solver": solver,
            "quote_grid": MagicMock(),
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "created_at": time.time(),
        },
    )

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
                        "config": make_ready_file_input_config(source_path),
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


# ---------------------------------------------------------------------------
# OPT-V04: strict result rows, input provenance, diagnostics_errors, finite JSON
# ---------------------------------------------------------------------------

from typing import Any  # noqa: E402

from pydantic import ValidationError  # noqa: E402

from haute.schemas import (  # noqa: E402
    OptimiserFactorTableRow,
    OptimiserFrontierPointSummary,
    OptimiserFrontierResponse,
    OptimiserFrontierSelectResponse,
    OptimiserOnlineFrontierPoint,
    OptimiserRatebookFrontierPoint,
    OptimiserSolveResult,
)
from tests.optimiser_fixtures import (  # noqa: E402
    SOLVE_SCENARIO_GRID,
    make_completed_job,
    make_frontier_data,
    make_frontier_point,
    make_input_summary,
    make_scenario_grid,
    make_solved_result,
    setup_grid_stub,
)

_PROVENANCE = {
    "node_id": "opt",
    "data_source": "batch",
    "source_file": "main.py",
    "graph_fingerprint": "fp-1",
}


def _summary_for(point: dict[str, Any]) -> dict[str, Any]:
    from haute.routes._frontier_point_summary import frontier_point_summary

    return frontier_point_summary(point, {name: "min" for name in point["totals"]})


def _frontier(points: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    names = list(points[0]["totals"]) if points else ["volume"]
    payload = make_frontier_data(
        points,
        constraint_names=names,
        swept_axes=names,
        point_summaries=[_summary_for(point) for point in points],
    )
    payload.update(overrides)
    return payload


class TestStrictFrontierPoints:
    def test_both_point_shapes_validate(self) -> None:
        OptimiserOnlineFrontierPoint.model_validate(make_frontier_point())
        OptimiserRatebookFrontierPoint.model_validate(make_frontier_point(mode="ratebook"))

    @pytest.mark.parametrize(
        ("mode", "change", "message"),
        [
            ("online", {"cd_iterations": 3}, "Extra inputs are not permitted"),
            ("online", {"sv_median": None}, "sv_median"),
            ("online", {"total_objective": float("nan")}, "finite number"),
            ("online", {"totals": {"volume": float("inf")}}, "finite number"),
            ("online", {"iterations": True}, "iterations"),
            ("online", {"iterations": "7"}, "iterations"),
            ("online", {"solver_path": "newton"}, "solver_path"),
            ("ratebook", {"sv_mean": 1.0}, "Extra inputs are not permitted"),
            ("ratebook", {"n_quotes_clamped_low": -1}, "n_quotes_clamped_low"),
        ],
    )
    def test_a_malformed_point_is_rejected(self, mode: str, change: dict, message: str) -> None:
        model = OptimiserOnlineFrontierPoint if mode == "online" else OptimiserRatebookFrontierPoint
        with pytest.raises(ValidationError, match=message):
            model.model_validate({**make_frontier_point(mode=mode), **change})

    def test_an_online_point_requires_every_sv_statistic(self) -> None:
        point = make_frontier_point()
        del point["sv_p95"]
        with pytest.raises(ValidationError, match="sv_p95"):
            OptimiserOnlineFrontierPoint.model_validate(point)

    @pytest.mark.parametrize("field", ["thresholds", "bounds", "totals", "lambdas"])
    def test_a_point_whose_constraint_maps_disagree_is_rejected(self, field: str) -> None:
        point = make_frontier_point()
        point[field] = {"volume": 1.0, "margin": 2.0}
        with pytest.raises(ValidationError, match="constraint names"):
            OptimiserOnlineFrontierPoint.model_validate(point)


class TestFrontierResponseCompleteness:
    def test_a_complete_frontier_validates(self) -> None:
        OptimiserFrontierResponse.model_validate(_frontier([make_frontier_point()]))

    @pytest.mark.parametrize("field", ["thresholds", "bounds", "totals", "lambdas"])
    def test_a_point_missing_a_configured_constraint_is_rejected(self, field: str) -> None:
        point = make_frontier_point(
            thresholds={"volume": 0.9, "margin": 1.0},
            bounds={"volume": 0.9, "margin": 1.0},
            totals={"volume": 0.91, "margin": 1.1},
            lambdas={"volume": 0.4, "margin": 0.0},
        )
        payload = _frontier([point])
        payload["points"][0][field] = {"volume": 1.0}
        for other in ("thresholds", "bounds", "totals", "lambdas"):
            payload["points"][0][other] = {"volume": 1.0}
        with pytest.raises(ValidationError, match="constraint names"):
            OptimiserFrontierResponse.model_validate(payload)

    @pytest.mark.parametrize("field", ["constraints", "effective_bounds", "lambdas"])
    def test_a_summary_missing_a_configured_constraint_is_rejected(self, field: str) -> None:
        payload = _frontier([make_frontier_point()])
        payload["point_summaries"][0][field] = {}
        with pytest.raises(ValidationError, match="constraint names"):
            OptimiserFrontierResponse.model_validate(payload)

    def test_points_of_two_modes_are_rejected(self) -> None:
        payload = _frontier([make_frontier_point(), make_frontier_point(mode="ratebook")])
        with pytest.raises(ValidationError, match="one mode"):
            OptimiserFrontierResponse.model_validate(payload)

    def test_a_summary_per_point_is_required(self) -> None:
        payload = _frontier([make_frontier_point(), make_frontier_point()])
        payload["point_summaries"] = payload["point_summaries"][:1]
        with pytest.raises(ValidationError, match="one summary per point"):
            OptimiserFrontierResponse.model_validate(payload)

    def test_a_swept_axis_outside_the_constraints_is_rejected(self) -> None:
        payload = _frontier([make_frontier_point()], swept_axes=["margin"])
        with pytest.raises(ValidationError, match="swept_axes"):
            OptimiserFrontierResponse.model_validate(payload)


class TestStrictSummariesAndRows:
    @pytest.mark.parametrize(
        "row",
        [
            {"__factor_group__": "North", "optimal_scenario_value": 1.1, "quote_count": 3, "x": 1},
            {"__factor_group__": "North", "optimal_scenario_value": 1.1},
            {"__factor_group__": "North", "optimal_scenario_value": float("nan"), "quote_count": 3},
            {"__factor_group__": "North", "optimal_scenario_value": 1.1, "quote_count": -1},
            {"__factor_group__": 7, "optimal_scenario_value": 1.1, "quote_count": 3},
            {"level": "North", "optimal_scenario_value": 1.1, "quote_count": 3},
        ],
    )
    def test_a_malformed_factor_table_row_is_rejected(self, row: dict) -> None:
        with pytest.raises(ValidationError):
            OptimiserFactorTableRow.model_validate(row)

    def test_a_factor_table_row_serialises_under_its_wire_keys(self) -> None:
        row = {"__factor_group__": "North", "optimal_scenario_value": 1.1, "quote_count": 3}
        assert OptimiserFactorTableRow.model_validate(row).model_dump(by_alias=True) == row

    @pytest.mark.parametrize("field", ["constraints", "effective_bounds", "lambdas"])
    def test_a_point_summary_whose_constraint_keys_disagree_is_rejected(self, field: str) -> None:
        summary = _summary_for(make_frontier_point())
        summary[field] = {**summary[field], "margin": summary[field]["volume"]}
        with pytest.raises(ValidationError, match="constraint names"):
            OptimiserFrontierPointSummary.model_validate(summary)

    def test_a_point_summary_rejects_unknown_fields(self) -> None:
        summary = {**_summary_for(make_frontier_point()), "baseline_objective": 1.0}
        with pytest.raises(ValidationError, match="Extra inputs"):
            OptimiserFrontierPointSummary.model_validate(summary)


class TestSolveResultContract:
    def test_the_fixture_result_validates(self) -> None:
        result = OptimiserSolveResult.model_validate(make_solved_result())
        assert result.input_summary.data_source == "batch"
        assert result.diagnostics_errors == []
        assert [step.optimal_step for step in result.scenario_grid] == [0, 1, 2]

    @pytest.mark.parametrize(
        ("grid", "message"),
        [
            ([], "at least 1"),
            (
                [
                    {"optimal_step": 1, "scenario_value": 0.9},
                    {"optimal_step": 0, "scenario_value": 1.0},
                ],
                "steps 0..n-1 in order",
            ),
            (
                [
                    {"optimal_step": 0, "scenario_value": 1.0},
                    {"optimal_step": 1, "scenario_value": 1.0},
                ],
                "strictly increasing",
            ),
            ([{"optimal_step": 0, "scenario_value": 1.0, "x": 1}], "Extra inputs"),
        ],
    )
    def test_a_malformed_scenario_grid_is_rejected(
        self, grid: list[dict[str, Any]], message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            OptimiserSolveResult.model_validate(make_solved_result(scenario_grid=grid))

    def test_n_steps_must_match_the_scenario_grid(self) -> None:
        with pytest.raises(ValidationError, match="n_steps"):
            OptimiserSolveResult.model_validate(
                make_solved_result(n_steps=5, scenario_grid=make_scenario_grid(3))
            )

    @pytest.mark.parametrize(
        "field", ["constraints", "baseline_constraints", "lambdas", "effective_bounds"]
    )
    def test_constraint_keyed_maps_must_agree(self, field: str) -> None:
        result = make_solved_result()
        result[field] = {}
        with pytest.raises(ValidationError, match="constraint names"):
            OptimiserSolveResult.model_validate(result)

    @pytest.mark.parametrize(
        "missing", ["mode", "input_summary", "diagnostics_errors", "scenario_grid", "segment_keys"]
    )
    def test_required_fields_have_no_silent_default(self, missing: str) -> None:
        result = make_solved_result()
        del result[missing]
        with pytest.raises(ValidationError, match=missing):
            OptimiserSolveResult.model_validate(result)

    @pytest.mark.parametrize(
        "change",
        [
            {"graph_fingerprint": None},
            {"extra": "x"},
            {"solver_settings": {"max_iter": 50, "tolerance": 1e-6, "chunk_size": None, "x": 1}},
        ],
    )
    def test_the_input_summary_is_strict(self, change: dict) -> None:
        result = make_solved_result(input_summary={**make_input_summary(), **change})
        with pytest.raises(ValidationError, match="input_summary"):
            OptimiserSolveResult.model_validate(result)

    @pytest.mark.parametrize(
        "entry",
        [
            {"diagnostic": "shap", "error_type": "ValueError", "message": "x"},
            {"diagnostic": "frontier", "error_type": "ValueError"},
            {"diagnostic": "frontier", "error_type": "ValueError", "error": "x"},
        ],
    )
    def test_a_malformed_diagnostics_error_is_rejected(self, entry: dict) -> None:
        with pytest.raises(ValidationError, match="diagnostics_errors"):
            OptimiserSolveResult.model_validate(make_solved_result(diagnostics_errors=[entry]))

    @staticmethod
    def _trace(**record_changes: Any) -> dict[str, Any]:
        record = {
            "cd_iteration": 1,
            "factor": "region",
            "factor_index": 0,
            "total_objective": 95.0,
            "total_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            **record_changes,
        }
        return {"records": [record], "truncated": False}

    def test_a_ratebook_result_carries_its_cd_trace(self) -> None:
        result = OptimiserSolveResult.model_validate(
            make_solved_result(mode="ratebook", ratebook_cd_trace=self._trace())
        )
        assert result.ratebook_cd_trace is not None
        assert result.ratebook_cd_trace.records[0].factor == "region"

    def test_the_cd_trace_is_ratebook_only_and_history_online_only(self) -> None:
        with pytest.raises(ValidationError, match="ratebook_cd_trace"):
            OptimiserSolveResult.model_validate(
                make_solved_result(mode="online", ratebook_cd_trace=self._trace())
            )
        entry = {"iteration": 0, "total_objective": 1.0, "max_lambda_change": 0.0}
        with pytest.raises(ValidationError, match="history"):
            OptimiserSolveResult.model_validate(
                make_solved_result(mode="ratebook", history=[entry])
            )

    @pytest.mark.parametrize("field", ["total_constraints", "lambdas"])
    def test_a_cd_trace_record_holds_exactly_the_results_constraints(self, field: str) -> None:
        trace = self._trace(**{field: {"volume": 0.5, "margin": 0.1}})
        with pytest.raises(ValidationError, match="constraint names"):
            OptimiserSolveResult.model_validate(
                make_solved_result(mode="ratebook", ratebook_cd_trace=trace)
            )

    @pytest.mark.parametrize(
        "change",
        [
            {"cd_iteration": 0},
            {"factor_index": -1},
            {"total_objective": float("inf")},
            {"clamp_rate": 0.1},
        ],
    )
    def test_a_malformed_cd_trace_record_is_rejected(self, change: dict) -> None:
        with pytest.raises(ValidationError, match="ratebook_cd_trace"):
            OptimiserSolveResult.model_validate(
                make_solved_result(mode="ratebook", ratebook_cd_trace=self._trace(**change))
            )

    def test_a_cd_trace_has_at_least_one_record(self) -> None:
        with pytest.raises(ValidationError, match="at least 1"):
            OptimiserSolveResult.model_validate(
                make_solved_result(
                    mode="ratebook", ratebook_cd_trace={"records": [], "truncated": False}
                )
            )

    def test_ratebook_factor_tables_are_typed_rows(self) -> None:
        tables = {"region": [{"__factor_group__": "N", "optimal_scenario_value": 1.0}]}
        with pytest.raises(ValidationError, match="quote_count"):
            OptimiserSolveResult.model_validate(
                make_solved_result(mode="ratebook", factor_tables=tables)
            )

    @pytest.mark.parametrize(
        "missing", ["total_objective", "baseline_objective", "baseline_constraints", "converged"]
    )
    def test_a_select_response_has_no_default_baseline_or_totals(self, missing: str) -> None:
        response = {
            "status": "ok",
            "point_index": 0,
            "total_objective": 1.0,
            "constraints": {"volume": 1.0},
            "baseline_objective": 1.0,
            "baseline_constraints": {"volume": 1.0},
            "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
            "lambdas": {"volume": 0.0},
            "converged": True,
            "diagnostics_errors": [],
            "frontier_generation": 0,
        }
        OptimiserFrontierSelectResponse.model_validate(response)
        del response[missing]
        with pytest.raises(ValidationError, match=missing):
            OptimiserFrontierSelectResponse.model_validate(response)


class _StatsResult:
    """An online solve result whose per-quote frame the test controls."""

    def __init__(self, dataframe: Any = None) -> None:
        self.converged = True
        self.total_objective = 100.0
        self.baseline_objective = 95.0
        self.total_constraints = {"loss": 1.0}
        self.baseline_constraints = {"loss": 0.9}
        self.lambdas = {"loss": 0.5}
        self.constraint_bounds = {"loss": 1.05}
        if dataframe is not None:
            self.dataframe = dataframe


def _finalize(result: Any, *, mode: str = "online", config: dict | None = None, **job: Any):
    from haute.routes._optimiser_solver import _finalize_solve_result

    store = JobStore()
    job_id = store.create_job(
        {
            "status": "running",
            "config": config
            if config is not None
            else {"mode": mode, "constraints": {"loss": {"max": 1.05}}, "max_iter": 12},
            "input_provenance": dict(_PROVENANCE),
            "scenario_grid": SOLVE_SCENARIO_GRID,
            **job,
        }
    )
    _finalize_solve_result(
        result,
        mode=mode,
        solver=MagicMock(),
        quote_grid=MagicMock(),
        store=store,
        job_id=job_id,
        elapsed=0.1,
    )
    return store.require_job(job_id)


def _choices(steps: list[int], values: list[float]) -> pl.DataFrame:
    """An online apply frame choosing *steps* of ``SOLVE_SCENARIO_GRID``."""
    n = len(steps)
    return pl.DataFrame(
        {
            "quote_id": pl.Series([f"q{i}" for i in range(n)], dtype=pl.String),
            "optimal_step": pl.Series(steps, dtype=pl.Int32),
            "optimal_scenario_value": pl.Series(values, dtype=pl.Float32),
            "optimal_objective": pl.Series([1.0] * n, dtype=pl.Float32),
            "optimal_loss": pl.Series([1.0] * n, dtype=pl.Float32),
        }
    )


class TestResultDiagnostics:
    @pytest.mark.parametrize(
        ("dataframe", "message"),
        [
            (pl.DataFrame({"quote_id": ["a"]}), "optimal_step"),
            (_choices([], []), "no quotes"),
            (_choices([5], [1.4]), "outside the recorded scenario grid"),
        ],
    )
    def test_a_report_that_cannot_be_built_is_reported_not_dropped(
        self, dataframe: Any, message: str
    ) -> None:
        job = _finalize(_StatsResult(dataframe))

        assert job["status"] == "completed"
        result = job["result"]
        assert result["adjustments"] is None
        (error,) = result["diagnostics_errors"]
        assert error["diagnostic"] == "adjustments"
        assert error["error_type"]
        assert message in error["message"]
        OptimiserSolveResult.model_validate(result)

    def test_a_built_report_records_no_diagnostic(self) -> None:
        job = _finalize(_StatsResult(_choices([0, 2], [0.9, 1.1])))

        assert job["result"]["adjustments"]["weightings"][0]["mean"] == pytest.approx(1.0)
        assert job["result"]["diagnostics_errors"] == []
        OptimiserSolveResult.model_validate(job["result"])

    def test_a_ratebook_solve_reports_its_canonical_per_quote_evaluation(self) -> None:
        result = _StatsResult()
        # q0's product 0.93 rounds to the 0.9 step; q1's lies past the 1.1 end.
        result.quote_results = _choices([0, 2], [0.9, 1.1]).with_columns(
            pl.Series("factor_product", [0.93, 1.3], dtype=pl.Float32),
            pl.Series("clamped_low", [False, False]),
            pl.Series("clamped_high", [False, True]),
        )
        job = _finalize(
            result,
            mode="ratebook",
            config={
                "mode": "ratebook",
                "constraints": {"loss": {"max": 1.05}},
                "factor_columns": [["region"]],
            },
        )

        report = job["result"]["adjustments"]
        assert job["result"]["diagnostics_errors"] == []
        assert [bar["quotes"] for bar in report["bars"]] == [1, 0, 1]
        assert report["deployed_factor_differs"] == 1
        OptimiserSolveResult.model_validate(job["result"])

    def test_a_ratebook_result_without_its_per_quote_evaluation_fails_the_completion(
        self,
    ) -> None:
        with pytest.raises(AttributeError, match="quote_results"):
            _finalize(_StatsResult(), mode="ratebook")

    def test_a_failed_frontier_is_in_the_list_and_keeps_frontier_error(self) -> None:
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "status": "running",
                "config": {
                    "mode": "online",
                    "constraints": {"loss": {"max": 1.05}},
                    "frontier_enabled": True,
                    "frontier_ranges": {"loss": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 2,
                },
                "input_provenance": dict(_PROVENANCE),
                "scenario_grid": SOLVE_SCENARIO_GRID,
            }
        )
        from haute.routes._optimiser_solver import solver_worker_context

        solver = MagicMock()
        solver.frontier.side_effect = RuntimeError("frontier exploded")
        with solver_worker_context():
            _finalize_solve_result(
                _StatsResult(_choices([1], [1.0])),
                mode="online",
                solver=solver,
                quote_grid=MagicMock(),
                store=store,
                job_id=job_id,
                elapsed=0.1,
            )

        result = store.require_job(job_id)["result"]
        assert result["frontier_error"] == "Frontier unavailable: frontier exploded"
        assert result["diagnostics_errors"] == [
            {"diagnostic": "frontier", "error_type": "RuntimeError", "message": "frontier exploded"}
        ]


class TestInputSummary:
    def test_the_result_carries_the_job_provenance_and_solver_settings(self) -> None:
        job = _finalize(_StatsResult(pl.DataFrame({"optimal_scenario_value": [1.0]})))

        assert job["result"]["input_summary"] == {
            **_PROVENANCE,
            "solver_settings": {
                "max_iter": 12,
                "tolerance": 1e-6,
                "chunk_size": None,
            },
        }
        OptimiserSolveResult.model_validate(job["result"])

    def test_a_solve_job_without_provenance_fails_loudly(self) -> None:
        from haute.routes._optimiser_solver import solve_input_summary

        with pytest.raises(KeyError, match="input_provenance"):
            solve_input_summary({"config": {"mode": "online"}})

    def test_the_artifact_reads_the_result_summary_rather_than_building_another(self) -> None:
        from haute.routes._optimiser_frontier import _summary_solve_result
        from haute.routes.optimiser import _build_artifact_payload

        summary = make_input_summary(
            data_source="scenario_b",
            solver_settings={"max_iter": 9, "tolerance": 0.1, "chunk_size": 64},
        )
        result = make_solved_result(input_summary=summary, n_quotes=10, n_steps=3)
        job = make_completed_job(result=result, config={"mode": "online", "max_iter": 50})

        payload = _build_artifact_payload(job, _summary_solve_result(result))

        assert payload["solver_settings"] == {"max_iter": 9, "tolerance": 0.1, "chunk_size": 64}
        assert payload["input_summary"] == {
            "n_quotes": 10,
            "n_steps": 3,
            "node_id": "opt",
            "data_source": "scenario_b",
            "source_file": "main.py",
            "graph_fingerprint": "graph-fingerprint",
        }


class TestStatusFiniteWalk:
    def _seed(self, store: JobStore, job_id: str, **overrides: Any) -> None:
        from tests.job_store_support import seed_job

        seed_job(store, job_id, make_completed_job(**overrides))

    def test_a_non_finite_result_value_turns_the_job_into_an_error(
        self, client, clean_job_store
    ) -> None:
        result = make_solved_result(constraints={"volume": float("nan")})
        self._seed(clean_job_store, "nan_job", result=result)

        response = client.get("/api/optimiser/solve/status/nan_job")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["result"] is None
        assert "non-finite" in body["message"]
        assert "result.constraints.volume" in body["message"]

    def test_a_non_finite_frontier_value_turns_the_job_into_an_error(
        self, client, clean_job_store
    ) -> None:
        frontier = _frontier([make_frontier_point(sv_mean=float("inf"))])
        self._seed(clean_job_store, "inf_job", frontier_data=frontier)

        body = client.get("/api/optimiser/solve/status/inf_job").json()

        assert body["status"] == "error"
        assert "frontier.points[0].sv_mean" in body["message"]

    def test_a_clean_walk_is_cached_for_its_generation_and_selection(
        self, client, clean_job_store
    ) -> None:
        self._seed(clean_job_store, "clean_job")

        first = client.get("/api/optimiser/solve/status/clean_job")

        assert first.status_code == 200
        assert first.json()["status"] == "completed"
        assert "_result_finite_validated_for" not in first.text
        job = clean_job_store.require_job("clean_job")
        assert job["_result_finite_validated_for"] == [0, None]

        # A later selection rewrites the result: the next poll walks it again.
        clean_job_store.atomic_update(
            "clean_job",
            {
                "selected_frontier_point": 1,
                "result": make_solved_result(lambdas={"volume": float("nan")}),
            },
            expected_status="completed",
        )
        assert client.get("/api/optimiser/solve/status/clean_job").json()["status"] == "error"
