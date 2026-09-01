"""Optimiser service input-finiteness contract and single-quote lifecycle.

Covers remediation items:

- 3b.1 [C7]: ``_validate_and_project`` proactively rejects NaN/inf in the
  objective, constraint, and scenario columns as a named contract error
  before any grid construction or solver work happens. Grid construction
  keeps its own loud rejection as a second line of defence (pinned here).
- 3b.4 [M]: a single-quote solve must complete with sane distribution
  diagnostics instead of crashing in ``_compute_scenario_value_stats``
  after the solver already succeeded (``std()`` of one element is null).
- 3b.8 share: the multi-quote solve and the single-quote lifecycle run the
  real ``price_contour`` solver end-to-end and pin the real result shape
  consumed by ``_optimiser_service``.
"""

from __future__ import annotations

import json
import math
import time
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute.errors import GroupByExecutionUnsupportedError
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import (
    OptimiserSolveService,
    SolveContext,
    _compute_scenario_value_stats,
    _memory_limit_message,
    _normalise_memory_limit_payload,
    _optional_positive_int,
)
from haute.schemas import OptimiserFrontierAutoRangeRequest, OptimiserSolveRequest
from tests.conftest import (
    make_edge,
    make_graph,
    make_ready_file_input_config,
)

_TERMINAL_STATUSES = {"completed", "error", "contract_error", "cancelled", "memory_limited"}

_SCENARIO_STAT_KEYS = {
    "mean",
    "std",
    "min",
    "max",
    "p5",
    "p25",
    "p50",
    "p75",
    "p95",
    "pct_increase",
    "pct_decrease",
}


@pytest.fixture()
def clean_job_store():
    from haute.routes.optimiser import _store

    _store.clear_all()
    yield _store
    _store.clear_all()


def _poll_solve_terminal(client, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    data: dict = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_STATUSES:
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Solve job {job_id} still {data.get('status')!r} after {timeout}s")


def _solver_config(**overrides) -> dict:
    config = {
        "mode": "online",
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.5}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "max_iter": 50,
        "tolerance": 1e-6,
    }
    config.update(overrides)
    return config


def _source_to_optimiser_graph(source_path: str, opt_config: dict) -> dict:
    return make_graph(
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
                        "config": opt_config,
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    ).model_dump()


def _two_quote_frame() -> pl.DataFrame:
    """Minimal well-formed solver input: 2 quotes x 2 scenarios, all finite."""
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1, 0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 110.0, 200.0, 220.0], dtype=pl.Float32),
            "volume": pl.Series([1.0, 0.9, 1.1, 1.0], dtype=pl.Float32),
        }
    )


# ---------------------------------------------------------------------------
# 3b.1 [C7] — non-finite rejection through the service entry point
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
@pytest.mark.parametrize(
    ("column", "bad_value", "expected_fragment"),
    [
        pytest.param(
            "expected_income",
            float("nan"),
            "'expected_income' (1 NaN row)",
            id="objective-nan",
        ),
        pytest.param(
            "expected_income",
            float("inf"),
            "'expected_income' (1 infinite row)",
            id="objective-inf",
        ),
        pytest.param("volume", float("nan"), "'volume' (1 NaN row)", id="constraint-nan"),
        pytest.param(
            "volume",
            float("-inf"),
            "'volume' (1 infinite row)",
            id="constraint-neg-inf",
        ),
        pytest.param(
            "scenario_value",
            float("nan"),
            "'scenario_value' (1 NaN row)",
            id="scenario-value-nan",
        ),
        pytest.param(
            "scenario_value",
            float("inf"),
            "'scenario_value' (1 infinite row)",
            id="scenario-value-inf",
        ),
    ],
)
def test_solve_rejects_non_finite_input_before_any_solver_work(
    client,
    tmp_path,
    clean_job_store,
    column,
    bad_value,
    expected_fragment,
) -> None:
    """NaN/inf in any solver column is a named contract error; the solver
    stack (grid build + launch) must never be reached."""
    frame = _two_quote_frame()
    values = frame[column].to_list()
    values[1] = bad_value
    frame = frame.with_columns(pl.Series(column, values, dtype=pl.Float32))
    source_path = tmp_path / "scored.parquet"
    frame.write_parquet(source_path)

    graph = _source_to_optimiser_graph(str(source_path), _solver_config())

    from haute.routes import optimiser as optimiser_routes

    with (
        patch.object(optimiser_routes._solve_service, "_build_grid") as build_grid_spy,
        patch.object(optimiser_routes._solve_service, "_launch_background") as launch_spy,
    ):
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 200
        status = _poll_solve_terminal(client, resp.json()["job_id"])

    assert status["status"] == "contract_error", status.get("message")
    message = status["message"]
    assert "Non-finite values found in optimiser input" in message
    assert expected_fragment in message
    build_grid_spy.assert_not_called()
    launch_spy.assert_not_called()


def test_validate_and_project_names_every_non_finite_column() -> None:
    """One pass reports all offending columns with their row counts."""
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1, 0.9, float("nan")], dtype=pl.Float32),
            "expected_income": pl.Series(
                [float("nan"), 110.0, float("inf"), 220.0],
                dtype=pl.Float32,
            ),
            "volume": pl.Series([1.0, 0.9, 1.1, float("-inf")], dtype=pl.Float32),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._validate_and_project(
            source_lf,
            _solver_config(),
            job_id,
        )

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert "Non-finite values found in optimiser input" in detail
    assert "'expected_income' (1 NaN row, 1 infinite row)" in detail
    assert "'scenario_value' (1 NaN row)" in detail
    assert "'volume' (1 infinite row)" in detail
    job = store.require_job(job_id)
    assert job["status"] == "contract_error"
    assert job["terminal_reason"] == "contract_error"
    assert "Non-finite values found in optimiser input" in job["message"]


def test_validate_and_project_accepts_finite_extremes() -> None:
    """No false positives: zeros, negatives, and large-but-Float32-finite
    Float64 values must pass."""
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.0, 1.1, 0.9, 1.1], dtype=pl.Float32),
            # Float64 source: 1e30 is finite in Float32 too (max ~3.4e38).
            "expected_income": pl.Series([-1e30, 110.0, 0.0, 1e30], dtype=pl.Float64),
            "volume": pl.Series([1.0, -0.9, 1.1, 1.0], dtype=pl.Float32),
        }
    )

    constraint_cols, scored_lf = service._validate_and_project(
        source_lf,
        _solver_config(),
        job_id,
    )

    assert constraint_cols == ["volume"]
    projected = scored_lf.collect()
    assert projected.height == 4
    assert store.require_job(job_id)["status"] == "running"


def test_validate_and_project_rejects_float64_overflowing_float32() -> None:
    """The solver consumes Float32; a Float64 objective that overflows to
    inf at that precision is rejected with the column named."""
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", "q1"],
            "scenario_index": pl.Series([0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 1e300], dtype=pl.Float64),
            "volume": pl.Series([1.0, 0.9], dtype=pl.Float32),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._validate_and_project(source_lf, _solver_config(), job_id)

    assert exc_info.value.status_code == 400
    assert "'expected_income' (1 infinite row)" in exc_info.value.detail
    assert store.require_job(job_id)["status"] == "contract_error"


def test_validate_and_project_rejects_non_finite_float_scenario_index() -> None:
    """A float-typed scenario_index with NaN is named instead of failing as
    an opaque cast error downstream."""
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", "q1"],
            "scenario_index": pl.Series([0.0, float("nan")], dtype=pl.Float64),
            "scenario_value": pl.Series([0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 110.0], dtype=pl.Float32),
            "volume": pl.Series([1.0, 0.9], dtype=pl.Float32),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._validate_and_project(source_lf, _solver_config(), job_id)

    assert exc_info.value.status_code == 400
    assert "'scenario_index' (1 NaN row)" in exc_info.value.detail
    assert store.require_job(job_id)["status"] == "contract_error"


def test_validate_and_project_null_quote_id_reported_before_non_finite() -> None:
    """Existing precedence pinned: null quote_id errors win when both
    contract violations are present in the same input."""
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    source_lf = pl.LazyFrame(
        {
            "quote_id": ["q1", None],
            "scenario_index": pl.Series([0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, float("nan")], dtype=pl.Float32),
            "volume": pl.Series([1.0, 0.9], dtype=pl.Float32),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._validate_and_project(source_lf, _solver_config(), job_id)

    assert exc_info.value.status_code == 400
    assert "Null quote_id values found in optimiser input (1 rows)." in exc_info.value.detail


def test_build_grid_still_rejects_non_finite_scenario_grid() -> None:
    """Characterization: grid construction remains a loud second line of
    defence for a non-finite scenario grid (division of labour with
    ``_validate_and_project``, which now rejects first)."""
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    scored_lf = pl.LazyFrame(
        {
            "quote_id": pl.Series(["q1", "q1", "q2", "q2"], dtype=pl.Categorical),
            "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
            "scenario_value": pl.Series([0.9, float("nan"), 0.9, 1.1], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 110.0, 200.0, 220.0], dtype=pl.Float32),
            "volume": pl.Series([1.0, 0.9, 1.1, 1.0], dtype=pl.Float32),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        service._build_grid(
            scored_lf,
            ["volume"],
            _solver_config(chunk_size=10),
            "opt",
            job_id,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Grid construction failed. Check the server logs for details."
    job = store.require_job(job_id)
    assert job["status"] == "error"
    assert job["terminal_reason"] == "error"


def _group_by_contract_error() -> GroupByExecutionUnsupportedError:
    return GroupByExecutionUnsupportedError(
        "group-by needs an admitted materialisation boundary",
        node_id="opt",
        operator="groupBy",
        profile="optimiser_setup",
        reason_code="profile_requires_bounded_execution",
        remediation="use an admitted eager profile",
        estimated_peak_bytes=1_024,
        headroom_bytes=512,
    )


def test_execute_pipeline_adapts_public_contract_errors(tmp_path) -> None:
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    body = OptimiserSolveRequest.model_validate(
        {
            "graph": _source_to_optimiser_graph("missing.parquet", _solver_config()),
            "node_id": "opt",
        }
    )
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()

    with (
        patch(
            "haute.routes._optimiser_service.execute_lazy_graph",
            side_effect=_group_by_contract_error(),
        ),
        patch("haute.executor._compile_preamble", return_value={}),
        patch("haute.executor._pipeline_dir", return_value=None),
        patch("haute.executor._resolve_batch_scenario", return_value="batch"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            service._execute_pipeline(body, job_id, checkpoint_dir)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error_code"] == "group_by_execution_unsupported"
    assert store.require_job(job_id)["status"] == "contract_error"


def test_build_grid_adapts_public_contract_errors() -> None:
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})

    with patch(
        "haute.routes._optimiser_service.bounded_sink",
        side_effect=_group_by_contract_error(),
    ):
        with pytest.raises(HTTPException) as exc_info:
            service._build_grid(
                _two_quote_frame().lazy(),
                ["volume"],
                _solver_config(),
                "opt",
                job_id,
            )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error_code"] == "group_by_execution_unsupported"
    assert store.require_job(job_id)["status"] == "contract_error"


def test_frontier_auto_range_adapts_public_contract_errors(tmp_path) -> None:
    source_path = tmp_path / "source.parquet"
    _two_quote_frame().write_parquet(source_path)
    body = OptimiserFrontierAutoRangeRequest.model_validate(
        {
            "graph": _source_to_optimiser_graph(str(source_path), _solver_config()),
            "node_id": "opt",
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
    prepared = service._prepare_frontier_auto_range(body)
    prepared["streaming_plan"] = None

    with (
        patch.object(service, "_execute_pipeline", side_effect=_group_by_contract_error()),
        pytest.raises(HTTPException) as exc_info,
    ):
        service._run_frontier_auto_range_job(body, job_id, **prepared)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error_code"] == "group_by_execution_unsupported"
    job = store.require_job(job_id)
    assert job["status"] == "contract_error"
    assert job["error_code"] == "group_by_execution_unsupported"


def test_solve_worker_records_public_contract_errors() -> None:
    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self) -> None:
            self.target()

    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})

    with (
        patch("haute.routes._optimiser_service.threading.Thread", InlineThread),
        patch(
            "haute.routes._optimiser_service._solve_online",
            side_effect=_group_by_contract_error(),
        ),
    ):
        service._launch_background(
            SolveContext(job_id=job_id, node_id="opt", mode="online"),
            config={},
            quote_grid=object(),
            ratebook_factors_handle=None,
        )

    job = store.require_job(job_id)
    assert job["status"] == "contract_error"
    assert job["error_code"] == "group_by_execution_unsupported"


def test_memory_limit_helpers_cover_unstructured_and_absent_values() -> None:
    assert _normalise_memory_limit_payload("memory exhausted") == {
        "message": "memory exhausted",
        "error_code": "memory_limit",
    }
    assert _optional_positive_int(None, field="timeout") is None
    assert _memory_limit_message({}) == "Auto-range exceeded its memory budget."


def test_memory_limit_message_prefers_the_curated_http_payload_wording() -> None:
    """_memory_limit_http_exception stamps the shared curated message; the
    job's terminal message must reuse it rather than the generic fallback."""
    from haute._execution_context import ExecutionMemoryLimitExceededError
    from haute.routes._optimiser_service import _memory_limit_http_exception

    exc = ExecutionMemoryLimitExceededError(
        "frontier_auto_range",
        rss_bytes=2048,
        limit_bytes=1024,
        reason="process_rss_limit_exceeded",
        rss_limit_bytes=1024,
    )
    detail = _memory_limit_http_exception(exc).detail
    assert isinstance(detail, dict)
    message = _memory_limit_message(_normalise_memory_limit_payload(detail))
    assert message == detail["message"]
    assert message.startswith("Auto-range needs more memory than this server allows")
    assert "2.0 KiB used, 1.0 KiB allowed" in message
    assert "frontier_auto_range" not in message


# ---------------------------------------------------------------------------
# 3b.4 [M] — single-quote solve must not crash after solving
# ---------------------------------------------------------------------------


def test_compute_scenario_value_stats_single_row_has_zero_spread() -> None:
    """``std()`` of one element is null in polars; the stats builder must
    report the true zero spread of a complete one-quote result set instead
    of crashing on ``float(None)``."""
    df = pl.DataFrame({"optimal_scenario_value": pl.Series([1.1], dtype=pl.Float64)})
    stats, histogram = _compute_scenario_value_stats(SimpleNamespace(dataframe=df))

    assert stats is not None
    assert set(stats) == _SCENARIO_STAT_KEYS
    assert stats["std"] == 0.0
    assert stats["mean"] == pytest.approx(1.1)
    assert stats["min"] == stats["max"] == pytest.approx(1.1)
    assert stats["p5"] == stats["p50"] == stats["p95"] == pytest.approx(1.1)
    assert stats["pct_increase"] == 1.0
    assert stats["pct_decrease"] == 0.0
    assert histogram is not None
    assert sum(histogram["counts"]) == 1
    assert all(math.isfinite(edge) for edge in histogram["edges"])


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_single_quote_solve_lifecycle_real_solver(client, tmp_path, clean_job_store) -> None:
    """Full single-quote lifecycle against the real price-contour solver:
    solve -> converged -> status retrievable -> save -> apply."""
    source_path = tmp_path / "single_quote.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1"],
            "scenario_index": pl.Series([0, 1, 2], dtype=pl.Int32),
            "scenario_value": pl.Series([0.8, 1.0, 1.2], dtype=pl.Float32),
            "expected_income": pl.Series([100.0, 130.0, 120.0], dtype=pl.Float32),
            "volume": pl.Series([1.0, 1.0, 1.0], dtype=pl.Float32),
        }
    ).write_parquet(source_path)
    graph = _source_to_optimiser_graph(str(source_path), _solver_config(chunk_size=6))

    resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    status = _poll_solve_terminal(client, job_id, timeout=30.0)

    assert status["status"] == "completed", status.get("message")
    result = status["result"]
    assert result["mode"] == "online"
    assert result["converged"] is True
    assert result["n_quotes"] == 1
    assert result["n_steps"] == 3
    assert result["total_objective"] == pytest.approx(130.0)
    assert result["baseline_objective"] == pytest.approx(130.0)
    assert result["constraints"]["volume"] == pytest.approx(1.0)
    assert result["lambdas"]["volume"] == pytest.approx(0.0)

    stats = result["scenario_value_stats"]
    assert stats is not None
    assert stats["std"] == 0.0
    assert stats["mean"] == pytest.approx(1.0)
    assert stats["min"] == stats["max"] == pytest.approx(1.0)
    assert stats["p5"] == stats["p50"] == stats["p95"] == pytest.approx(1.0)
    assert stats["pct_increase"] == 0.0
    assert stats["pct_decrease"] == 0.0
    histogram = result["scenario_value_histogram"]
    assert sum(histogram["counts"]) == 1

    # Results stay retrievable on a repeat poll.
    second = client.get(f"/api/optimiser/solve/status/{job_id}")
    assert second.status_code == 200
    assert second.json()["result"]["scenario_value_stats"]["std"] == 0.0

    # Save path.
    out_path = tmp_path / "artifact.json"
    save_resp = client.post(
        "/api/optimiser/save",
        json={"job_id": job_id, "output_path": str(out_path)},
    )
    assert save_resp.status_code == 200, save_resp.text
    saved = json.loads(out_path.read_text())
    assert saved["converged"] is True
    assert saved["total_objective"] == pytest.approx(130.0)

    # Apply path (artifact-backed after save slims the heavy objects).
    apply_resp = client.post("/api/optimiser/apply", json={"job_id": job_id})
    assert apply_resp.status_code == 200, apply_resp.text
    applied = apply_resp.json()
    assert applied["status"] == "ok"
    assert applied["row_count"] == 1
    assert applied["total_objective"] == pytest.approx(130.0)
    assert applied["constraints"]["volume"] == pytest.approx(1.0)
    preview = pl.DataFrame(applied["preview"])
    assert preview.height == 1
    assert preview["quote_id"].to_list() == ["q1"]
    assert preview["optimal_step"].to_list() == [1]
    assert preview["optimal_scenario_value"].to_list() == pytest.approx([1.0])


# ---------------------------------------------------------------------------
# 3b.8 share — real-solver result shape pin (multi-quote)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_multi_quote_real_solve_pins_result_shape(client, tmp_path, clean_job_store) -> None:
    """Pin the real price-contour result fields consumed by
    ``_finalize_solve_result`` with deterministic expected values."""
    source_path = tmp_path / "three_quotes.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1"] * 3 + ["q2"] * 3 + ["q3"] * 3,
            "scenario_index": pl.Series([0, 1, 2] * 3, dtype=pl.Int32),
            "scenario_value": pl.Series([0.8, 1.0, 1.2] * 3, dtype=pl.Float32),
            "expected_income": pl.Series(
                [100.0, 130.0, 120.0, 80.0, 90.0, 140.0, 150.0, 120.0, 100.0],
                dtype=pl.Float32,
            ),
            "volume": pl.Series([1.0] * 9, dtype=pl.Float32),
        }
    ).write_parquet(source_path)
    graph = _source_to_optimiser_graph(
        str(source_path),
        _solver_config(constraints={"volume": {"min": 1.5}}, chunk_size=9),
    )

    resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert resp.status_code == 200
    status = _poll_solve_terminal(client, resp.json()["job_id"], timeout=30.0)

    assert status["status"] == "completed", status.get("message")
    result = status["result"]
    assert result["mode"] == "online"
    assert result["converged"] is True
    assert result["iterations"] >= 1
    assert result["n_quotes"] == 3
    assert result["n_steps"] == 3
    # Unconstrained optimum: q1 -> 130 @ sv 1.0, q2 -> 140 @ sv 1.2, q3 -> 150 @ sv 0.8.
    assert result["total_objective"] == pytest.approx(420.0)
    # Baseline = scenario_value 1.0 row per quote: 130 + 90 + 120.
    assert result["baseline_objective"] == pytest.approx(340.0)
    assert result["constraints"] == {"volume": pytest.approx(3.0)}
    assert result["baseline_constraints"] == {"volume": pytest.approx(3.0)}
    assert set(result["lambdas"]) == {"volume"}
    assert result["history"] is None

    stats = result["scenario_value_stats"]
    assert set(stats) == _SCENARIO_STAT_KEYS
    assert stats["mean"] == pytest.approx(1.0, rel=1e-6)
    # Sample std (ddof=1) of [1.0, 1.2, 0.8].
    assert stats["std"] == pytest.approx(0.2, rel=1e-5)
    assert stats["min"] == pytest.approx(0.8, rel=1e-6)
    assert stats["max"] == pytest.approx(1.2, rel=1e-6)
    assert stats["p50"] == pytest.approx(1.0, rel=1e-6)
    assert stats["pct_increase"] == pytest.approx(1 / 3)
    assert stats["pct_decrease"] == pytest.approx(1 / 3)

    histogram = result["scenario_value_histogram"]
    assert len(histogram["counts"]) == 20
    assert len(histogram["edges"]) == 21
    assert sum(histogram["counts"]) == 3


def test_online_solver_value_error_is_wrapped_as_solver_execution_error() -> None:
    """A price-contour ValueError is an algorithm failure, not a data error."""
    from haute.routes._optimiser_service import (
        _OptimiserSolverExecutionError,
        _solve_online,
        solver_worker_context,
    )

    store = JobStore()
    job_id = store.create_job({"status": "running"})
    with (
        solver_worker_context(),
        patch("price_contour.OnlineOptimiser") as optimiser,
        pytest.raises(_OptimiserSolverExecutionError, match="invalid solver state"),
    ):
        optimiser.return_value.solve.side_effect = ValueError("invalid solver state")
        _solve_online(
            SolveContext(
                job_id=job_id,
                node_id="opt",
                mode="online",
                store=store,
                start_time=time.monotonic(),
            ),
            quote_grid=SimpleNamespace(),
            config={"objective": "expected_income", "constraints": {}},
        )
