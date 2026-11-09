"""The optimiser estimate and frontier auto-range routes.

``POST /api/optimiser/estimate`` previews the input volume of an optimiser's data, and the frontier
auto-range job reports its status, cancels and projects the inputs it reads.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._project_context import context_thread
from haute.errors import InvalidRequestError, RequestRefusedError
from haute.routes._optimiser_input import _optimiser_solve_required_columns_by_node
from haute.routes._optimiser_service import (
    FrontierAutoRangeContext,
    _estimate_scenario_frontier_ranges,
)
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _TERMINAL_JOB_STATUSES,
    _make_optimiser_graph,
    _poll_until_done,
    _snapshot_parquet_data_input,
)
from tests._process_state import clear_execution_admission_reservations
from tests._projection_helpers import pair_value
from tests.conftest import make_edge, make_graph
from tests.job_store_support import seed_job
from tests.optimiser_fixtures import (
    setup_grid_stub,
)

# Fixtures shared with the other modules split from one suite.
scored_data = _optimiser_route_helpers.scored_data


if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


# ---------------------------------------------------------------------------
# Test data: build a scored DataFrame in the shape price-contour expects
# ---------------------------------------------------------------------------


def _make_estimate_contract_free_fan_in_graph(left_path: str, right_path: str) -> dict:
    """Build a fan-in optimiser graph that needs parent ownership metadata."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(left_path),
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(right_path),
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left.join(right, on='quote_id', how='left')",
                            "contract": {
                                "inputs": [
                                    "quote_id",
                                    "scenario_index",
                                    "scenario_value",
                                    "expected_income",
                                    "volume",
                                ],
                                "outputs": [],
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
                            "data_input": "joined",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "opt").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def _make_auto_range_runtime_projectable_graph(left_path: str, right_path: str) -> dict:
    """Build a fan-in whose parent ownership is resolved from runtime schemas."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(left_path),
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "dataInput",
                        "config": _snapshot_parquet_data_input(right_path),
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left.join(right, on='quote_id', how='left')",
                            "contract": {
                                "inputs": ["quote_id", "premium", "burn_cost", "factor"],
                                "outputs": [],
                            },
                        },
                    },
                },
                {
                    "id": "premium",
                    "data": {
                        "label": "premium",
                        "nodeType": "scenarioExpander",
                        "config": {
                            "quote_id": "quote_id",
                            "column_name": "premium_multiplier",
                            "min_value": 1.0,
                            "max_value": 2.0,
                            "step_count": 2,
                            "code": (
                                "df = df.with_columns("
                                "premium=pl.col('premium') * pl.col('premium_multiplier'))"
                            ),
                            "contract": {
                                "inputs": ["premium"],
                                "outputs": ["premium_multiplier", "scenario_index"],
                            },
                        },
                    },
                },
                {
                    "id": "optimiser_input",
                    "data": {
                        "label": "optimiser_input",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = premium.with_columns("
                                "conversion_prediction=pl.col('premium') * pl.col('factor'), "
                                "margin=pl.col('premium') - pl.col('burn_cost'))"
                            ),
                            "contract": {
                                "inputs": ["premium", "factor", "burn_cost"],
                                "outputs": ["conversion_prediction", "margin"],
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
                            # A real column: auto-range captures what the following solve
                            # reads, so an objective nothing produces would widen its reads.
                            "objective": "margin",
                            "constraints": {
                                "conversion_prediction": {"min": 0.0},
                                "margin": {"min": 0.0},
                            },
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "premium_multiplier",
                            "data_input": "optimiser_input",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "premium").model_dump(),
                make_edge("premium", "optimiser_input").model_dump(),
                make_edge("optimiser_input", "opt").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def _poll_auto_range_until_done(client: TestClient, job_id: str, timeout: float = 10) -> dict:
    """Poll /frontier/auto-range/status/{job_id} until a terminal status."""
    poll_interval = 0.02
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/frontier/auto-range/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_JOB_STATUSES:
            return data
        time.sleep(poll_interval)
    raise TimeoutError(f"Auto-range job {job_id} did not finish within {timeout}s")


@pytest.fixture()
def modest_solve_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the solve profile's grant, so a job reaches the code under test.

    Unpinned, the grant is a share of the RAM free when it is admitted, which a
    busy CI runner can lack: the job is then refused as
    ``in_flight_memory_budget_exceeded`` before it runs.
    """
    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "512")


# ---------------------------------------------------------------------------
# Step 2: API routes
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestEstimateRoute:
    """Exercises ``POST /api/optimiser/estimate`` — the input-volume preview
    consumed by the frontend's shared ``useStaleConfigEstimate`` hook.  Exact
    counts execute the pipeline plus one aggregation scan (cost contract
    pinned in ``test_optimiser_routes_real_library.py``)."""

    def test_estimate_returns_total_rows(self, client, scored_data):
        from haute.routes.optimiser import _store

        graph = _make_optimiser_graph(scored_data)

        with patch.object(_store, "create_job", wraps=_store.create_job) as create_job:
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Source metadata may or may not resolve depending on the test
        # fixture; the response must always include total_rows (possibly
        # null) so the frontend's shared useStaleConfigEstimate hook has a
        # stable shape to render against.
        assert "total_rows" in data
        assert any(call.args[0].get("job_type") == "estimate" for call in create_job.call_args_list)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_returns_scenario_expanded_input_counts(self, client, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2", "q2"],
                "scenario_index": [0, 1, 0, 1, 2],
                "scenario_value": [0.9, 1.1, 0.8, 1.0, 1.2],
                "expected_income": [100.0, 110.0, 90.0, 95.0, 98.0],
                "volume": [1.0, 0.9, 1.2, 1.1, 1.0],
            }
        )
        path = tmp_path / "ragged_scenarios.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_rows"] == 5
        assert data["expanded_row_count"] == 5
        assert data["quote_count"] == 2
        assert data["scenarios_per_quote_min"] == 2
        assert data["scenarios_per_quote_max"] == 3
        assert data["scenarios_per_quote_mean"] == pytest.approx(2.5)

    def test_estimate_forecasts_the_solve_against_what_a_solve_would_be_granted(
        self, client, tmp_path, monkeypatch
    ):
        """The forecast prices the quote ids' width and the ratebook factors; the cap is
        the growth grant a solve started now would get, with the estimate's own
        reservation already released (OPT-W02)."""
        from haute._ram_estimate import forecast_solve_memory_bytes
        from haute.routes._optimiser_input import grid_chunk_decision

        gib = 1024**3
        monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
        monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)
        for name in (
            "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_BYTES",
            "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB",
            "HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_BYTES",
            "HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_MB",
            "HAUTE_EXECUTION_MEMORY_LIMIT_BYTES",
            "HAUTE_EXECUTION_MEMORY_LIMIT_MB",
            "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_BYTES",
            "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_MB",
            "HAUTE_EXECUTION_MEMORY_POLICY",
        ):
            monkeypatch.delenv(name, raising=False)
        df = pl.DataFrame(
            {
                "quote_id": ["a", "a", "abcdef", "abcdef"],
                "scenario_index": [0, 1, 0, 1],
                "scenario_value": [0.9, 1.1, 0.9, 1.1],
                "expected_income": [100.0, 110.0, 90.0, 95.0],
                "volume": [1.0, 0.9, 1.2, 1.1],
            }
        )
        path = tmp_path / "widths.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post("/api/optimiser/estimate", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["solve_memory_forecast_bytes"] == forecast_solve_memory_bytes(
            mode="online",
            quote_count=2,
            row_count=4,
            constraint_count=1,
            factor_count=0,
            quote_id_bytes=3.5,
            chunk_rows=grid_chunk_decision(2).chunk_size,
        )
        # 10 GiB available less the 2 GiB OS reserve, nothing else in flight.
        assert data["solve_memory_cap_bytes"] == 8 * gib

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_accepts_projection_safe_data_source_limit_code(
        self,
        client,
        tmp_path,
    ):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": [0, 1, 0, 1],
                "scenario_value": [0.9, 1.1, 0.9, 1.1],
                "expected_income": [100.0, 110.0, 200.0, 220.0],
                "volume": [1.0, 0.9, 1.2, 1.1],
                "unused_payload": ["wide", "column", "must", "drop"],
            }
        )
        path = tmp_path / "limited_source.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))
        graph["nodes"][0]["data"]["config"].update(
            {
                "contract": "opaque",
                "code": "df = df.limit(4)",
            }
        )

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["expanded_row_count"] == 4
        assert data["quote_count"] == 2

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_rejects_null_quote_id_loudly(self, client, tmp_path):
        df = pl.DataFrame(
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
        )
        path = tmp_path / "null_quote_ids.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 400
        assert "Null quote_id values found in optimiser input (2 rows)." in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("bad_value", "label"),
        [(float("nan"), "NaN"), (float("inf"), "Inf")],
    )
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_rejects_non_finite_scenario_value(
        self,
        client,
        tmp_path,
        clean_job_store,
        bad_value,
        label,
    ):
        """``NaN`` and ``Inf`` in ``scenario_value`` would silently corrupt
        the lambdas the optimiser produces — and through them, every
        downstream price.

        Pin the contract: bad values are rejected by the proactive
        non-finite validation in ``_validate_and_project`` (3b.1/C7) and
        surface as a contract-error solve status with the offending column
        name (grid construction keeps its own loud rejection as a second
        line of defence, pinned in test_optimiser_service_validation.py).
        No job is left behind in a "running" state, no lambdas are ever
        produced from corrupt data, and the user gets an actionable message
        naming the input that failed.
        """
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series(
                    [0.9, bad_value, 0.9, 1.1],
                    dtype=pl.Float32,
                ),
                "expected_income": pl.Series(
                    [100.0, 110.0, 80.0, 90.0],
                    dtype=pl.Float32,
                ),
                "volume": pl.Series([0.9, 0.95, 1.0, 0.9], dtype=pl.Float32),
            }
        )
        path = tmp_path / f"{label.lower()}_scenario.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )

        # ── User-visible contract ────────────────────────────────────
        # 400 is the right status: bad input, not a server fault.
        assert resp.status_code == 200, resp.text
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "contract_error"
        detail = status["message"]
        # The message must name the offending column AND the offending
        # value.  Generic "grid construction failed" without specifics
        # leaves the user guessing which row of which file is bad.
        assert "scenario_value" in detail, (
            f"Error detail does not name the offending column: {detail!r}"
        )
        # ``label.lower() in detail.lower()`` accommodates the
        # price-contour error rendering ("inf"/"nan" in lowercase).
        assert label.lower() in detail.lower(), (
            f"Error detail does not name the offending value type ({label!r}): {detail!r}"
        )

        # ── Job-store hygiene ───────────────────────────────────────
        # The synchronous failure must NOT leave a "running" job behind
        # to confuse the next /solve attempt or the status poller.
        # Either the job exists with status=error, or it was deleted —
        # both are acceptable; a stuck "running" job is not.
        running_jobs = [
            (jid, j)
            for jid, j in clean_job_store.list_jobs().items()
            if j.get("status") == "running"
        ]
        assert running_jobs == [], f"Validation failure left running job(s) behind: {running_jobs}"

    @pytest.mark.parametrize(
        ("column", "null_series", "expected_fragment"),
        [
            pytest.param(
                "volume",
                pl.Series([0.9, None, 1.0, 0.9], dtype=pl.Float32),
                "'volume' (1 null row)",
                id="constraint-null",
            ),
            pytest.param(
                "scenario_value",
                pl.Series([0.9, None, 0.9, 1.1], dtype=pl.Float32),
                "'scenario_value' (1 null row)",
                id="scenario-value-null",
            ),
            pytest.param(
                "scenario_index",
                pl.Series([0, None, 0, 1], dtype=pl.Int32),
                "'scenario_index' (1 null row)",
                id="scenario-index-null",
            ),
        ],
    )
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_rejects_null_input_values(
        self,
        client,
        tmp_path,
        clean_job_store,
        column,
        null_series,
        expected_fragment,
    ):
        """A genuinely-null objective/constraint/scenario value must fail as
        loudly as NaN does.

        The non-finite gate only sees NaN/inf: ``is_nan()``/``is_infinite()``
        return null for null inputs, so ``sum()`` skips them and a null value
        sails through to the solver, where the external aggregation's
        treatment of null is undefined (silent-corruption risk). Pin the
        contract: nulls are rejected before any solve, with a 400-class
        contract error naming the offending column, and no running job is
        left behind.
        """
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": pl.Series([0, 1, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series([0.9, 1.0, 0.9, 1.1], dtype=pl.Float32),
                "expected_income": pl.Series([100.0, 110.0, 80.0, 90.0], dtype=pl.Float32),
                "volume": pl.Series([0.9, 0.95, 1.0, 0.9], dtype=pl.Float32),
            }
        ).with_columns(null_series.alias(column))
        path = tmp_path / f"null_{column}.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 200, resp.text
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "contract_error"
        detail = status["message"]
        assert "Null values found in optimiser input" in detail
        assert expected_fragment in detail, (
            f"Error detail does not name the offending column: {detail!r}"
        )

        running_jobs = [
            (jid, j)
            for jid, j in clean_job_store.list_jobs().items()
            if j.get("status") == "running"
        ]
        assert running_jobs == [], f"Validation failure left running job(s) behind: {running_jobs}"

    def test_estimate_rejects_unknown_node_loudly(self, client, scored_data):
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "nonexistent"},
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_surfaces_projected_input_errors(self, client, tmp_path):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "scenario_index": [0, 1],
                "scenario_value": [0.9, 1.1],
                "expected_income": [100.0, 110.0],
            }
        )
        path = tmp_path / "missing_constraint.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 400
        assert "Source projection references columns missing" in resp.json()["detail"]
        assert "volume" in resp.json()["detail"]

    def test_estimate_maps_bounded_streaming_collect_failure_to_422(
        self,
        client,
        scored_data,
    ):
        from haute.errors import BoundedMemoryUnsupportedError

        graph = _make_optimiser_graph(scored_data)
        error = BoundedMemoryUnsupportedError(
            "Bounded streaming collect failed",
            profile="optimiser_setup",
            cause="ComputeError",
        )

        with patch("haute.routes.optimiser._optimiser_input_metrics", side_effect=error):
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 422
        assert "bounded streaming mode" in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("failure", "status"),
        [
            pytest.param(
                "start",
                503,
                id="worker-did-not-start",
            ),
            pytest.param("host", 503, id="host-cannot-start-workers"),
            pytest.param("protocol", 500, id="worker-protocol-failure"),
        ],
    )
    def test_estimate_answers_a_worker_that_could_not_run_it(
        self, client, scored_data, monkeypatch: pytest.MonkeyPatch, failure: str, status: int
    ) -> None:
        """The warm pool's start, host and protocol failures are answered as such, never
        a bare internal error."""
        from haute._worker_failures import WorkerHostError, WorkerProtocolError, WorkerStartError

        errors = {
            "start": WorkerStartError(
                "Failed to start interactive worker 0: not ready", terminal_reason="error"
            ),
            "host": WorkerHostError("the resource tracker died"),
            "protocol": WorkerProtocolError(
                "Interactive worker returned a malformed result envelope"
            ),
        }

        class _FailingPool:
            def run(self, *_args: object, **_kwargs: object) -> object:
                raise errors[failure]

        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
        monkeypatch.setattr("haute.routes.optimiser.interactive_worker_pool", _FailingPool)
        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": _make_optimiser_graph(scored_data), "node_id": "opt"},
        )

        assert resp.status_code == status, resp.text
        if status == 503:
            assert str(errors[failure]) in resp.json()["detail"]

    def test_estimate_maps_admission_failure_to_memory_limit_response(
        self,
        client,
        scored_data,
    ) -> None:
        from haute._execution_admission import ExecutionAdmissionError

        graph = _make_optimiser_graph(scored_data)
        error = ExecutionAdmissionError(
            "optimiser_estimate",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_limit_bytes=1024,
            rss_at_admission_bytes=512,
            reason="in_flight_memory_budget_exceeded",
            in_flight_reserved_bytes=2048,
            in_flight_limit_bytes=1024,
        )

        with patch("haute.routes.optimiser._optimiser_input_metrics", side_effect=error):
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 507
        detail = resp.json()["detail"]
        assert detail["error_code"] == "memory_limit"
        assert detail["profile"] == ExecutionProfile.OPTIMISER_SETUP.value
        assert detail["reason"] == "in_flight_memory_budget_exceeded"
        assert detail["message"].startswith("Optimiser estimate was not started")

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_estimate_allows_contract_free_fan_in_boundary(self, client, tmp_path):
        left_path = tmp_path / "estimate_left.parquet"
        right_path = tmp_path / "estimate_right.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "scenario_index": [0, 0],
                "scenario_value": [1.0, 1.0],
                "expected_income": [100.0, 120.0],
            }
        ).write_parquet(left_path)
        pl.DataFrame({"quote_id": ["q1", "q2"], "volume": [1.0, 0.8]}).write_parquet(right_path)
        graph = _make_estimate_contract_free_fan_in_graph(
            str(left_path),
            str(right_path),
        )

        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_rows"] == 2
        assert data["quote_count"] == 2
        assert data["scenarios_per_quote_min"] == 1
        assert data["scenarios_per_quote_max"] == 1
        assert data["scenarios_per_quote_mean"] == 1.0
        assert data["expanded_row_count"] == 2

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_uses_per_quote_scenario_extrema(
        self,
        client,
        tmp_path,
    ):
        """Auto range scans every scenario per quote, so middle extrema count."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
                "scenario_index": [0, 1, 2, 0, 1, 2],
                "scenario_value": [0.8, 1.0, 1.2, 0.8, 1.0, 1.2],
                "expected_income": [100.0, 120.0, 110.0, 90.0, 95.0, 98.0],
                "expected_margin": [10.0, 30.0, 20.0, 5.0, 1.0, 9.0],
            }
        )
        path = tmp_path / "scored.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(
            str(path),
            config={
                "objective": "expected_income",
                "constraints": {"expected_margin": {"max": 35.0}},
            },
        )

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed"
        data = status["result"]
        assert data["status"] == "ok"
        assert data["method"] == "scenario_envelope"
        assert data["ranges"]["expected_margin"]["min"] == pytest.approx(11.0)
        assert data["ranges"]["expected_margin"]["max"] == pytest.approx(39.0)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_requires_the_solves_scenario_columns(
        self,
        client,
        tmp_path,
    ):
        """Auto-range runs the solve's stage, so it asks for the solve's scenario columns."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2"],
                "expected_income": [10.0, 12.0, 9.0],
                "volume": [2.0, 5.0, 7.0],
            }
        )
        path = tmp_path / "constraint_only.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "contract_error"
        detail = status["error_detail"]
        assert "Source projection references columns missing" in detail
        assert "scenario_index" in detail
        assert "scenario_value" in detail

    def test_frontier_auto_range_checks_values_per_batch_with_whole_frame_totals(
        self,
        scored_data,
    ):
        """Violations in different batches are totalled, and no whole-frame query runs."""
        from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        volume = [float(i) for i in range(10)]
        volume[2] = float("nan")
        volume[8] = float("nan")
        source_lf = pl.LazyFrame(
            {
                "quote_id": [f"q{i}" for i in range(10)],
                "expected_income": [100.0] * 10,
                "volume": volume,
            }
        )
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        previous = current_streaming_chunk_size()
        set_streaming_chunk_size(3)
        try:
            with (
                patch.object(service, "_execute_pipeline", return_value={"source": source_lf}),
                patch(
                    "haute.routes._optimiser_input.validate_input_value_contracts",
                    side_effect=AssertionError("auto-range must not run a whole-frame query"),
                ),
                pytest.raises(RequestRefusedError) as exc_info,
            ):
                service._run_frontier_auto_range_job(body, job_id, **prepared)
        finally:
            set_streaming_chunk_size(previous)

        assert exc_info.value.status_code == 400
        # Rows 2 and 8 sit in different batches of 3; both are counted.
        assert "'volume' (2 NaN rows)" in exc_info.value.detail
        assert service.frontier_auto_range_status(job_id).status == "contract_error"

    def test_frontier_auto_range_batches_carry_a_string_quote_id_without_a_categorical(
        self,
        scored_data,
    ):
        """No Categorical cast is built for auto-range: the quote id stays text end to end."""
        from haute.routes._optimiser_input import validate_and_project_auto_range

        source_lf = pl.LazyFrame(
            {"quote_id": ["q1", "q2"], "expected_income": [1.0, 2.0], "volume": [1.0, 2.0]}
        )
        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.0}},
            "quote_id": "quote_id",
        }

        _constraints, projected_lf, _check = validate_and_project_auto_range(source_lf, config)

        assert projected_lf.collect_schema()["quote_id"] == pl.String
        assert "Categorical" not in projected_lf.explain()

    def test_frontier_auto_range_rejects_null_quote_id(
        self,
        scored_data,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        source_lf = pl.LazyFrame(
            {
                "quote_id": ["q1", None],
                "volume": [1.0, 2.0],
            }
        )
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)

        with (
            patch.object(service, "_execute_pipeline", return_value={"source": source_lf}),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert exc_info.value.status_code == 400
        assert (
            exc_info.value.detail == "Null quote_id values found in optimiser input (1 rows). "
            "Every row must have a non-null quote_id; check upstream filters and joins."
        )
        status = service.frontier_auto_range_status(job_id)
        assert status.status == "contract_error"
        assert status.http_status_code == 400
        assert status.error_detail == exc_info.value.detail

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
            pytest.param("volume", float("-inf"), "'volume' (1 infinite row)", id="constraint-inf"),
        ],
    )
    def test_frontier_auto_range_rejects_non_finite_values(
        self,
        scored_data,
        column,
        bad_value,
        expected_fragment,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        source_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "expected_income": pl.Series([100.0, 110.0], dtype=pl.Float32),
                "volume": pl.Series([1.0, 2.0], dtype=pl.Float32),
            }
        )
        values = source_df[column].to_list()
        values[1] = bad_value
        source_lf = source_df.with_columns(pl.Series(column, values, dtype=pl.Float32)).lazy()
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)

        with (
            patch.object(service, "_execute_pipeline", return_value={"source": source_lf}),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert exc_info.value.status_code == 400
        assert "Non-finite values found in optimiser input" in exc_info.value.detail
        assert expected_fragment in exc_info.value.detail
        assert "finite objective, constraint, and scenario values" in exc_info.value.detail
        status = service.frontier_auto_range_status(job_id)
        assert status.status == "contract_error"
        assert status.http_status_code == 400
        assert status.error_detail == exc_info.value.detail

    @pytest.mark.parametrize(
        ("column", "expected_fragment"),
        [
            pytest.param("expected_income", "'expected_income' (1 null row)", id="objective-null"),
            pytest.param("volume", "'volume' (1 null row)", id="constraint-null"),
        ],
    )
    def test_frontier_auto_range_rejects_null_values(
        self,
        scored_data,
        column,
        expected_fragment,
    ):
        """Null objective/constraint values must fail as loudly as NaN: the
        finite check skips nulls, so without a dedicated null gate they reach
        the range estimator silently."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        source_df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "expected_income": pl.Series([100.0, 110.0], dtype=pl.Float32),
                "volume": pl.Series([1.0, 2.0], dtype=pl.Float32),
            }
        )
        values = source_df[column].to_list()
        values[1] = None
        source_lf = source_df.with_columns(pl.Series(column, values, dtype=pl.Float32)).lazy()
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)

        with (
            patch.object(service, "_execute_pipeline", return_value={"source": source_lf}),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert exc_info.value.status_code == 400
        assert "Null values found in optimiser input" in exc_info.value.detail
        assert expected_fragment in exc_info.value.detail
        assert "non-null objective, constraint, and scenario values" in exc_info.value.detail
        status = service.frontier_auto_range_status(job_id)
        assert status.status == "contract_error"
        assert status.http_status_code == 400
        assert status.error_detail == exc_info.value.detail

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_rejects_non_finite_objective_after_projection(
        self,
        client,
        tmp_path,
    ):
        """Projected execution must keep the configured objective for validation."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2"],
                "scenario_index": [0, 1, 0],
                "scenario_value": [0.9, 1.1, 0.9],
                "expected_income": pl.Series([100.0, float("nan"), 90.0], dtype=pl.Float32),
                "volume": pl.Series([2.0, 5.0, 7.0], dtype=pl.Float32),
            }
        )
        path = tmp_path / "non_finite_objective.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "contract_error"
        detail = status["error_detail"]
        assert "Non-finite values found in optimiser input" in detail
        assert "'expected_income' (1 NaN row)" in detail
        assert "finite objective, constraint, and scenario values" in detail

    def test_frontier_auto_range_keeps_large_lazy_projection_narrow(
        self,
        scored_data,
    ):
        from haute.routes import _optimiser_service as optimiser_service
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        n_quotes = 6_000
        base = pl.DataFrame(
            {
                "quote_id": [f"q{i:05d}" for i in range(n_quotes) for _ in range(2)],
                "volume": [
                    value
                    for quote_index in range(n_quotes)
                    for value in (float(quote_index + 1), float(quote_index + 2))
                ],
            }
        )

        def fail_if_materialised(_value: object) -> float:
            raise AssertionError("unrelated lazy column was materialised")

        source_lf = base.lazy().with_columns(
            pl.col("volume")
            .map_elements(fail_if_materialised, return_dtype=pl.Float64)
            .alias("unrelated_poison")
        )
        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        real_bounded_collect_batches = optimiser_service.bounded_collect_batches

        with (
            patch.object(service, "_execute_pipeline", return_value={"source": source_lf}),
            patch.object(
                optimiser_service,
                "bounded_collect_batches",
                side_effect=real_bounded_collect_batches,
            ) as bounded_collect_batches,
        ):
            response = service._run_frontier_auto_range_job(body, job_id, **prepared)

        expected_min = n_quotes * (n_quotes + 1) / 2
        expected_max = expected_min + n_quotes
        assert response.status == "ok"
        assert response.ranges["volume"].min == pytest.approx(expected_min)
        assert response.ranges["volume"].max == pytest.approx(expected_max)
        assert bounded_collect_batches.call_count >= 1

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_preserves_configured_data_input_when_optimiser_checkpoints(
        self,
        client,
        tmp_path,
    ):
        """Auto-range consumes configured data_input after lazy graph execution."""
        scored_path = tmp_path / "scored.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2"],
                "scenario_index": [0, 1, 0],
                "scenario_value": [0.9, 1.1, 0.9],
                "expected_income": [1.0, 2.0, 3.0],
                "volume": [2.0, 5.0, 7.0],
            }
        ).write_parquet(scored_path)
        extra_path = tmp_path / "extra.parquet"
        pl.DataFrame({"unused": [1]}).write_parquet(extra_path)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(scored_path)),
                        },
                    },
                    {
                        "id": "optimiser_input",
                        "data": {
                            "label": "optimiser_input",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = source",
                                "contract": {"inputs": [], "outputs": []},
                            },
                        },
                    },
                    {
                        "id": "unused_parent",
                        "data": {
                            "label": "unused_parent",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(extra_path)),
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
                                "constraints": {"volume": {"min": 0.0}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "data_input": "optimiser_input",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "optimiser_input").model_dump(),
                    make_edge("optimiser_input", "opt").model_dump(),
                    make_edge("unused_parent", "opt").model_dump(),
                ],
            }
        )

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph.model_dump(), "node_id": "opt"},
        )

        assert start_resp.status_code == 200, start_resp.text
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed"
        data = status["result"]
        assert data["ranges"]["volume"]["min"] == pytest.approx(9.0)
        assert data["ranges"]["volume"]["max"] == pytest.approx(12.0)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_background_job_completes(
        self,
        client,
        scored_data,
        clean_job_store,
    ):
        """GUI auto-range uses a short start request plus pollable status."""
        graph = _make_optimiser_graph(scored_data)

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        job_id = start_resp.json()["job_id"]
        assert job_id
        deadline = time.monotonic() + 10
        status_data = None
        while time.monotonic() < deadline:
            status_resp = client.get(f"/api/optimiser/frontier/auto-range/status/{job_id}")
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            if status_data["status"] in {"completed", "error"}:
                break
            time.sleep(0.02)

        assert status_data is not None
        assert status_data["status"] == "completed"
        assert status_data["result"]["status"] == "ok"
        assert (
            status_data["result"]["ranges"]["volume"]["min"]
            <= status_data["result"]["ranges"]["volume"]["max"]
        )
        assert clean_job_store.require_job(job_id)["job_type"] == "frontier_auto_range"

    @pytest.mark.parametrize(
        "stored_status",
        [
            "running",
            "completed",
            "error",
            "cancelled",
            "superseded",
            "timed_out",
            "memory_limited",
            "contract_error",
        ],
    )
    def test_frontier_auto_range_status_returns_canonical_job_status(
        self,
        stored_status: str,
    ):
        """The status endpoint exposes canonical job state; category lives in metadata."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        job = {
            "status": stored_status,
            "job_type": "frontier_auto_range",
            "message": "Stopped",
        }
        if stored_status != "running":
            job["terminal_reason"] = stored_status
        if stored_status == "completed":
            job["result"] = {"status": "ok", "ranges": {}, "method": "scenario_envelope"}
        job_id = "canonical-status"
        seed_job(store, job_id, job)

        response = OptimiserSolveService(store).frontier_auto_range_status(job_id)

        assert response.status == stored_status
        assert response.terminal_reason == job.get("terminal_reason")

    def test_frontier_auto_range_status_fails_loudly_for_corrupt_status(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        with store._write_lock:  # noqa: SLF001 - deliberate corrupt-state boundary test
            store._jobs[job_id]["status"] = "contract-ish"  # noqa: SLF001

        with pytest.raises(ValueError, match="invalid status"):
            OptimiserSolveService(store).frontier_auto_range_status(job_id)

    def test_frontier_auto_range_start_deduplicates_previous_running_job(self, scored_data):
        """Repeated GUI starts for the same graph/node reuse the active heavy job."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserFrontierAutoRangeRequest(
            graph=_make_optimiser_graph(scored_data),
            node_id="opt",
        )

        with patch.object(service, "_launch_frontier_auto_range_background", return_value=None):
            first = service.start_frontier_auto_range(body)
            second = service.start_frontier_auto_range(body)

        assert first.job_id is not None
        assert second.job_id == first.job_id
        first_job = store.require_job(first.job_id)
        assert first_job["status"] == "running"
        assert len(store.list_jobs()) == 1

    def test_frontier_auto_range_retry_after_cancel_conflicts_until_worker_releases(
        self,
        scored_data,
    ):
        """Terminal-but-still-unwinding auto-range jobs must not be reused as fresh starts."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserFrontierAutoRangeRequest(
            graph=_make_optimiser_graph(scored_data),
            node_id="opt",
        )

        with patch.object(service, "_launch_frontier_auto_range_background", return_value=None):
            first = service.start_frontier_auto_range(body)

        service.cancel_frontier_auto_range(first.job_id)

        with pytest.raises(RequestRefusedError) as exc_info:
            service.start_frontier_auto_range(body)

        assert exc_info.value.status_code == 409
        assert store.require_job(first.job_id)["status"] == "cancelled"
        assert len(store.list_jobs()) == 1

    def test_frontier_auto_range_thread_start_failure_raises_and_marks_error(
        self,
        scored_data,
    ):
        """A failed auto-range worker launch must not return a misleading started response."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserFrontierAutoRangeRequest(
            graph=_make_optimiser_graph(scored_data),
            node_id="opt",
        )

        with (
            patch(
                "haute.routes._optimiser_service.threading.Thread.start",
                side_effect=RuntimeError("thread boom"),
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service.start_frontier_auto_range(body)

        assert exc_info.value.status_code == 500
        job = next(iter(store.list_jobs().values()))
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert "Failed to start auto-range worker" in job["message"]

    def test_solve_conflicts_with_running_auto_range_for_same_graph_node(self, scored_data):
        """Solve setup cannot overlap a running auto-range for the same graph/node."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest, OptimiserSolveRequest

        graph = _make_optimiser_graph(scored_data)
        store = JobStore()
        service = OptimiserSolveService(store)
        auto_body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        solve_body = OptimiserSolveRequest(graph=graph, node_id="opt")

        with patch.object(service, "_launch_frontier_auto_range_background", return_value=None):
            auto_started = service.start_frontier_auto_range(auto_body)

        with pytest.raises(RequestRefusedError) as exc_info:
            service.start(solve_body)

        auto_job = store.require_job(auto_started.job_id)
        assert exc_info.value.status_code == 409
        assert auto_job["status"] == "running"
        assert len(store.list_jobs()) == 1

    def test_auto_range_conflicts_with_running_solve_for_same_graph_node(self, scored_data):
        """Auto-range cannot overlap a running solve for the same graph/node."""

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest, OptimiserSolveRequest

        graph = _make_optimiser_graph(scored_data)
        store = JobStore()
        service = OptimiserSolveService(store)
        solve_body = OptimiserSolveRequest(graph=graph, node_id="opt")
        auto_body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        lazy_outputs = {
            "source": pl.LazyFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "expected_income": [100.0],
                    "volume": [1.0],
                }
            )
        }

        with (
            patch.object(service, "_execute_pipeline", return_value=lazy_outputs),
            patch.object(service, "_build_grid", return_value=setup_grid_stub()),
            patch.object(service, "_launch_background", return_value=None),
        ):
            solve_started = service.start(solve_body)

        with pytest.raises(RequestRefusedError) as exc_info:
            service.start_frontier_auto_range(auto_body)

        solve_job = store.require_job(solve_started.job_id)
        assert exc_info.value.status_code == 409
        assert solve_job["status"] == "running"
        assert len(store.list_jobs()) == 1

    @staticmethod
    def _auto_range_that_finishes(service):
        """A launcher whose worker publishes ``completed``, then frees the graph/node on cue.

        Like the real worker, it still holds the graph/node after the terminal
        transition (the real one records its metrics and returns its admission
        first). It exits once the returned ``exit_now`` is set.
        """
        finished = threading.Event()
        exit_now = threading.Event()

        def launch(body, job_id, *, setup_singleflight_key, **_kwargs):
            def worker() -> None:
                with service._job_ownership_scope(
                    job_id, setup_singleflight_key=setup_singleflight_key
                ):
                    service._lifecycle.transition(job_id, to="completed", message="Completed")
                    finished.set()
                    exit_now.wait()

            context_thread(worker, daemon=True).start()

        return launch, finished, exit_now

    def test_solve_started_as_auto_range_completes_waits_for_its_worker_to_exit(
        self, scored_data, monkeypatch
    ):
        """A caller that saw ``completed`` starts the next setup as soon as the worker exits."""
        from haute.routes import _optimiser_service
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest, OptimiserSolveRequest

        # Long enough that a start that missed the worker's exit would sit it out visibly.
        monkeypatch.setattr(_optimiser_service, "_FINISHED_OWNER_EXIT_SECONDS", 30.0)
        graph = _make_optimiser_graph(scored_data)
        store = JobStore()
        service = OptimiserSolveService(store)
        launch, finished, exit_now = self._auto_range_that_finishes(service)

        try:
            with patch.object(service, "_launch_frontier_auto_range_background", launch):
                auto_started = service.start_frontier_auto_range(
                    OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
                )
            assert finished.wait(5)
            assert store.require_job(auto_started.job_id)["status"] == "completed"

            threading.Timer(0.2, exit_now.set).start()
            began = time.monotonic()
            with patch.object(service, "_launch_setup_background", return_value=None):
                solve_started = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))
            waited = time.monotonic() - began
        finally:
            exit_now.set()

        assert store.require_job(solve_started.job_id)["status"] == "running"
        # The worker exited 0.2 s into the wait; the start followed it, not its 30 s bound.
        assert waited < 10

    def test_a_finished_owner_that_never_exits_still_conflicts(self, scored_data, monkeypatch):
        """The wait for a finished owner is bounded; a worker that never exits keeps its 409."""
        from haute.routes import _optimiser_service
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest, OptimiserSolveRequest

        monkeypatch.setattr(_optimiser_service, "_FINISHED_OWNER_EXIT_SECONDS", 0.05)
        graph = _make_optimiser_graph(scored_data)
        store = JobStore()
        service = OptimiserSolveService(store)
        launch, finished, exit_now = self._auto_range_that_finishes(service)

        try:
            with patch.object(service, "_launch_frontier_auto_range_background", launch):
                service.start_frontier_auto_range(
                    OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
                )
            assert finished.wait(5)

            with pytest.raises(RequestRefusedError) as exc_info:
                service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))
        finally:
            exit_now.set()

        assert exc_info.value.status_code == 409
        assert "already running for this graph/node" in exc_info.value.detail

    def test_frontier_auto_range_cancel_marks_job_terminal(
        self,
        scored_data,
        monkeypatch,
    ):
        """Explicit cancellation stops stale workers from later completing the job."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        clear_execution_admission_reservations()
        monkeypatch.setattr(
            "haute._execution_admission.current_rss_bytes",
            lambda: 1,
        )
        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserFrontierAutoRangeRequest(
            graph=_make_optimiser_graph(scored_data),
            node_id="opt",
        )

        with patch.object(service, "_launch_frontier_auto_range_background", return_value=None):
            started = service.start_frontier_auto_range(body)

        assert started.job_id is not None
        response = service.cancel_frontier_auto_range(started.job_id)

        assert response.status == "cancelled"
        assert response.message == "Cancelled"
        from haute.routes._job_lifecycle import JobLifecycle

        published = False

        def publish_stale_completion():
            nonlocal published
            published = True
            return {"progress": 1.0}

        assert (
            JobLifecycle(store).publish_completion(
                started.job_id,
                publish=publish_stale_completion,
            )
            is None
        )
        assert published is False

    def test_an_auto_range_stopped_without_its_cancel_route_is_recorded(
        self,
        scored_data,
        monkeypatch,
    ):
        """A server shutdown cancels the job's token, not its route: the stop is recorded."""
        from haute.routes._background_jobs import BackgroundJobStoppedError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        class InlineThread:
            def __init__(self, *, target, daemon, args=(), kwargs=None, name=None):
                self.target = functools.partial(target, *args, **(kwargs or {}))
                self.daemon = daemon

            def start(self) -> None:
                self.target()

            def is_alive(self) -> bool:
                return False

        def stopped(_body, job_id, **_kwargs):
            raise BackgroundJobStoppedError(job_id, "cancelled")

        clear_execution_admission_reservations()
        monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
        store = JobStore()
        service = OptimiserSolveService(store)
        body = OptimiserFrontierAutoRangeRequest(
            graph=_make_optimiser_graph(scored_data), node_id="opt"
        )

        with (
            patch.object(service, "_run_frontier_auto_range_job", side_effect=stopped),
            patch("haute.routes._optimiser_service.threading.Thread", InlineThread),
        ):
            started = service.start_frontier_auto_range(body)

        job = store.require_job(started.job_id)
        assert (job["status"], job["message"]) == ("cancelled", "Cancelled")

    def test_frontier_auto_range_estimator_honors_cancellation_between_batches(self):
        """The chunked estimator exposes cooperative cancellation checkpoints."""
        from haute.routes._background_jobs import BackgroundJobStoppedError

        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", "q1"],
                "volume": [1.0, 2.0, 3.0],
            }
        )
        checks = 0

        def check_cancelled() -> None:
            nonlocal checks
            checks += 1
            if checks >= 2:
                raise BackgroundJobStoppedError("range-job", "cancelled")

        with pytest.raises(BackgroundJobStoppedError):
            _estimate_scenario_frontier_ranges(
                FrontierAutoRangeContext(chunk_size=1),
                scored_lf=df.lazy(),
                quote_id_col="quote_id",
                constraint_cols=["volume"],
                check_cancelled=check_cancelled,
            )

    def test_frontier_auto_range_estimator_handles_quotes_split_across_batches(self):
        """Chunking must not double-count quote extrema when a quote spans read batches."""
        df = pl.DataFrame(
            {
                "quote_id": pl.Series(
                    ["q1", "q2", "q1", "q3", "q2", "q1", "q3"],
                    dtype=pl.Categorical,
                ),
                "scenario_index": [0, 0, 1, 0, 1, 2, 1],
                "scenario_value": [0.8, 0.9, 1.0, 0.95, 1.1, 1.2, 1.05],
                "expected_income": [100.0, 90.0, 120.0, 70.0, 95.0, 110.0, 74.0],
                "volume": [10.0, 4.0, 3.0, -1.0, 8.0, 9.0, 2.0],
                "margin": [100.0, 40.0, 90.0, 5.0, 30.0, 150.0, 10.0],
            }
        )

        ranges = _estimate_scenario_frontier_ranges(
            FrontierAutoRangeContext(chunk_size=2),
            scored_lf=df.lazy(),
            quote_id_col="quote_id",
            constraint_cols=["volume", "margin"],
        )

        assert ranges == {
            "volume": {"min": pytest.approx(6.0), "max": pytest.approx(20.0)},
            "margin": {"min": pytest.approx(125.0), "max": pytest.approx(200.0)},
        }

    def test_frontier_auto_range_estimator_checks_memory_during_batch_reduce(self):
        """Batch bucket-file reduction must have its own memory checkpoint."""
        from haute._execution_context import (
            ExecutionContext,
            ExecutionMemoryLimitExceededError,
            ExecutionProfile,
        )
        from haute.routes import _optimiser_service as optimiser_service

        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "volume": [1.0, 2.0],
            }
        )
        batch_added = False
        original_add_batch = optimiser_service._ScenarioFrontierRangeAccumulator.add_batch

        def recording_add_batch(self, batch, *, batch_index):
            nonlocal batch_added
            original_add_batch(self, batch, batch_index=batch_index)
            batch_added = True

        context = ExecutionContext(
            operation="frontier-auto-range-test",
            profile=ExecutionProfile.OPTIMISER_SOLVE,
            memory_limit_bytes=100,
            memory_sampler=lambda: 1_000 if batch_added else 1,
        )

        with (
            patch.object(
                optimiser_service._ScenarioFrontierRangeAccumulator,
                "add_batch",
                recording_add_batch,
            ),
            pytest.raises(ExecutionMemoryLimitExceededError),
        ):
            _estimate_scenario_frontier_ranges(
                FrontierAutoRangeContext(
                    chunk_size=2,
                    execution_context=context,
                ),
                scored_lf=df.lazy(),
                quote_id_col="quote_id",
                constraint_cols=["volume"],
            )

        assert batch_added
        assert "frontier_range_batch_reduce" in context.metrics_payload()["stage_elapsed_ms"]

    def test_frontier_auto_range_lazy_maps_bounded_collect_failure_to_422(
        self,
        scored_data,
    ):
        from haute.errors import BoundedMemoryUnsupportedError
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        error = BoundedMemoryUnsupportedError(
            "Bounded streaming collect failed",
            profile="optimiser_solve",
            cause="ComputeError",
        )

        with (
            patch.object(
                service,
                "_execute_pipeline",
                return_value={"source": pl.scan_parquet(scored_data)},
            ),
            patch(
                "haute.routes._optimiser_service._estimate_scenario_frontier_ranges",
                side_effect=error,
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert exc_info.value.status_code == 422
        assert "bounded streaming mode" in str(exc_info.value.detail)
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert "bounded streaming mode" in job["message"]
        assert job["http_status_code"] == 422
        assert "bounded streaming mode" in job["error_detail"]

    @pytest.mark.parametrize(
        ("raised", "status_code", "terminal_reason", "detail"),
        [
            pytest.param(
                InvalidRequestError("bad lazy range"),
                400,
                "contract_error",
                "bad lazy range",
                id="typed-refusal",
            ),
            pytest.param(
                ValueError("bad lazy range"),
                500,
                "error",
                "Auto-range failed. Check the server logs for details.",
                id="library-value-error",
            ),
        ],
    )
    def test_frontier_auto_range_classifies_a_reducer_failure(
        self,
        scored_data,
        modest_solve_budget,
        raised,
        status_code,
        terminal_reason,
        detail,
    ):
        """OPT-R03: a typed refusal is the input's fault; a bare ``ValueError`` is the server's."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)

        with (
            patch.object(
                service,
                "_execute_pipeline",
                return_value={"source": pl.scan_parquet(scored_data)},
            ),
            patch(
                "haute.routes._optimiser_service._estimate_scenario_frontier_ranges",
                side_effect=raised,
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert exc_info.value.status_code == status_code
        assert exc_info.value.detail == detail
        job = store.require_job(job_id)
        assert job["status"] == terminal_reason
        assert job["terminal_reason"] == terminal_reason
        assert job["http_status_code"] == status_code
        assert job["error_detail"] == detail
        assert job["execution_metrics"]["terminal_reason"] == terminal_reason

    def test_frontier_auto_range_reducer_budget_below_minimum_is_memory_limited(
        self,
        scored_data,
        modest_solve_budget,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import (
            AutoRangeReducerBudgetError,
            OptimiserSolveService,
        )
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        error = AutoRangeReducerBudgetError(
            budget_bytes=64 * 1024 * 1024,
            minimum_bytes=384 * 1024 * 1024,
            violation="batch_not_contiguous",
            setting="HAUTE_OPTIMISER_REDUCER_BUDGET_MB",
        )

        with (
            patch.object(
                service,
                "_execute_pipeline",
                return_value={"source": pl.scan_parquet(scored_data)},
            ),
            patch(
                "haute.routes._optimiser_service._estimate_scenario_frontier_ranges",
                side_effect=error,
            ),
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert exc_info.value.status_code == 507
        detail = exc_info.value.detail
        assert detail["reason"] == "reducer_budget_below_minimum"
        assert detail["reducer_budget_bytes"] == 64 * 1024 * 1024
        assert detail["reducer_min_budget_bytes"] == 384 * 1024 * 1024
        assert detail["violation"] == "batch_not_contiguous"
        job = store.require_job(job_id)
        assert job["status"] == "memory_limited"
        assert job["terminal_reason"] == "memory_limited"

    def test_frontier_auto_range_missing_column_status_includes_http_metadata(
        self,
        scored_data,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        missing_constraint_lf = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "expected_income": [100.0, 120.0],
            }
        ).lazy()

        with (
            patch.object(
                service,
                "_execute_pipeline",
                return_value={"source": missing_constraint_lf},
            ),
            pytest.raises(RequestRefusedError),
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        status = service.frontier_auto_range_status(job_id)
        assert status.status == "contract_error"
        assert status.terminal_reason == "contract_error"
        assert status.http_status_code == 400
        assert isinstance(status.error_detail, str)
        assert "Missing columns in scored data" in status.error_detail
        assert "volume" in status.error_detail

    def test_frontier_auto_range_invalid_quote_id_status_includes_http_metadata(
        self,
        scored_data,
    ):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        numeric_quote_id_lf = pl.DataFrame(
            {
                "quote_id": [1, 2],
                "volume": [1.0, 2.0],
            }
        ).lazy()

        with (
            patch.object(
                service,
                "_execute_pipeline",
                return_value={"source": numeric_quote_id_lf},
            ),
            pytest.raises(RequestRefusedError),
        ):
            service._run_frontier_auto_range_job(body, job_id, **prepared)

        status = service.frontier_auto_range_status(job_id)
        assert status.status == "contract_error"
        assert status.terminal_reason == "contract_error"
        assert status.http_status_code == 400
        assert isinstance(status.error_detail, str)
        assert "quote_id must be Utf8" in status.error_detail

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_reduces_a_fan_in_base_expanded_downstream(
        self,
        tmp_path,
    ):
        """A fan-in before the scenario expander yields exact per-quote envelope totals."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        left_path = tmp_path / "left.parquet"
        right_path = tmp_path / "right.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "premium": [100.0, 50.0],
                "burn_cost": [60.0, 20.0],
            }
        ).write_parquet(left_path)
        pl.DataFrame({"quote_id": ["q1", "q2"], "factor": [2.0, 3.0]}).write_parquet(right_path)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "left",
                        "data": {
                            "label": "left",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(left_path)),
                        },
                    },
                    {
                        "id": "right",
                        "data": {
                            "label": "right",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(right_path)),
                        },
                    },
                    {
                        "id": "joined",
                        "data": {
                            "label": "joined",
                            "nodeType": "polars",
                            "config": {
                                "code": ("df = left.join(right, on='quote_id', how='left')"),
                                "contract": {
                                    "inputs": [
                                        "quote_id",
                                        "premium",
                                        "burn_cost",
                                        "factor",
                                    ],
                                    "outputs": [],
                                    "inputs_by_parent": {
                                        "left": ["quote_id", "premium", "burn_cost"],
                                        "right": ["quote_id", "factor"],
                                    },
                                },
                            },
                        },
                    },
                    {
                        "id": "premium",
                        "data": {
                            "label": "premium",
                            "nodeType": "scenarioExpander",
                            "config": {
                                "quote_id": "quote_id",
                                "column_name": "premium_multiplier",
                                "min_value": 1.0,
                                "max_value": 2.0,
                                "step_count": 2,
                                "code": (
                                    "df = df.with_columns("
                                    "premium=pl.col('premium') * "
                                    "pl.col('premium_multiplier'))"
                                ),
                                "contract": {
                                    "inputs": ["premium"],
                                    "outputs": [
                                        "premium",
                                        "premium_multiplier",
                                        "scenario_index",
                                    ],
                                },
                            },
                        },
                    },
                    {
                        "id": "optimiser_input",
                        "data": {
                            "label": "optimiser_input",
                            "nodeType": "polars",
                            "config": {
                                "code": (
                                    "df = premium.with_columns("
                                    "conversion_prediction="
                                    "pl.col('premium') * pl.col('factor'), "
                                    "margin=pl.col('premium') - pl.col('burn_cost'))"
                                ),
                                "contract": {
                                    "inputs": ["premium", "factor", "burn_cost"],
                                    "outputs": ["conversion_prediction", "margin"],
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
                                "objective": "margin",
                                "constraints": {
                                    "conversion_prediction": {"min": 0.0},
                                    "margin": {"min": 0.0},
                                },
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "premium_multiplier",
                                "data_input": "optimiser_input",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("left", "joined").model_dump(),
                    make_edge("right", "joined").model_dump(),
                    make_edge("joined", "premium").model_dump(),
                    make_edge("premium", "optimiser_input").model_dump(),
                    make_edge("optimiser_input", "opt").model_dump(),
                ],
            }
        )
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")

        store = JobStore()
        service = OptimiserSolveService(store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
        _node, prepared = service._prepare_frontier_auto_range(body)
        response = service._run_frontier_auto_range_job(body, job_id, **prepared)

        assert response.status == "ok"
        assert response.ranges["conversion_prediction"].min == pytest.approx(350.0)
        assert response.ranges["conversion_prediction"].max == pytest.approx(700.0)
        assert response.ranges["margin"].min == pytest.approx(70.0)
        assert response.ranges["margin"].max == pytest.approx(220.0)

    @pytest.mark.parametrize("chunk_size", [0, -1, 1.5, "1000", True])
    def test_frontier_auto_range_estimator_rejects_invalid_chunk_size(self, chunk_size):
        """The chunked estimator should fail loudly for invalid chunk configuration."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "scenario_index": [0],
                "scenario_value": [1.0],
                "expected_income": [100.0],
                "volume": [1.0],
            }
        )

        with pytest.raises(ValueError, match="chunk_size must be a positive integer"):
            _estimate_scenario_frontier_ranges(
                FrontierAutoRangeContext(chunk_size=chunk_size),
                scored_lf=df.lazy(),
                quote_id_col="quote_id",
                constraint_cols=["volume"],
            )

    def test_frontier_auto_range_prepare_resolves_the_solves_demand(self, scored_data):
        """Auto-range asks the pipeline for exactly what the solve asks for."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        graph = _make_optimiser_graph(scored_data)
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        node, prepared = OptimiserSolveService(JobStore())._prepare_frontier_auto_range(body)

        assert node.id == "opt"
        assert prepared["required_columns_by_node"] == _optimiser_solve_required_columns_by_node(
            body.graph, "opt", node.data.config
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_runtime_projects_contract_free_fan_in(
        self,
        client,
        tmp_path,
    ):
        left_path = tmp_path / "auto_range_left.parquet"
        right_path = tmp_path / "auto_range_right.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "premium": [100.0, 50.0],
                "burn_cost": [60.0, 20.0],
            }
        ).write_parquet(left_path)
        pl.DataFrame({"quote_id": ["q1", "q2"], "factor": [2.0, 3.0]}).write_parquet(right_path)
        graph = _make_auto_range_runtime_projectable_graph(
            str(left_path),
            str(right_path),
        )

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed"
        data = status["result"]
        assert data["ranges"] == {
            "conversion_prediction": {"min": 350.0, "max": 700.0},
            "margin": {"min": 70.0, "max": 220.0},
        }

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_start_records_runtime_projection_diagnostics(
        self,
        client,
        tmp_path,
    ):
        left_path = tmp_path / "auto_range_start_left.parquet"
        right_path = tmp_path / "auto_range_start_right.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "premium": [100.0, 50.0],
                "burn_cost": [60.0, 20.0],
            }
        ).write_parquet(left_path)
        pl.DataFrame({"quote_id": ["q1", "q2"], "factor": [2.0, 3.0]}).write_parquet(right_path)
        graph = _make_auto_range_runtime_projectable_graph(
            str(left_path),
            str(right_path),
        )

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed"
        assert status["terminal_reason"] == "completed"
        assert status["result"]["ranges"] == {
            "conversion_prediction": {"min": 350.0, "max": 700.0},
            "margin": {"min": 70.0, "max": 220.0},
        }
        diagnostics = status["execution_metrics"]["execution_strategy"]
        assert diagnostics["schema_version"] == 1
        assert diagnostics["profile"] == "optimiser_solve"
        # The fan-in node joins, which EXEC-P07 admits as a materialisation
        # boundary; the runtime projection reasons below are unaffected.
        assert diagnostics["strategy"] == "materialisation-boundary"
        assert diagnostics["status"] == "boundary"
        assert diagnostics["boundedness"] == "unbounded"
        assert diagnostics["boundaries"]["total_count"] == 1
        assert diagnostics["blocking_operator"] == "join"
        reason_edges = {
            (item["parent_node_id"], item["node_id"], item["reason_code"])
            for item in diagnostics["reasons"]["items"]
        }
        assert ("left", "joined", "runtime_inferred_streaming") in reason_edges
        assert ("right", "joined", "runtime_inferred_streaming") in reason_edges

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_rejects_null_quote_id_in_chunked_estimator(
        self,
        client,
        tmp_path,
    ):
        """Auto-range validates quote_id nulls inside the chunked estimator pass."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1", None, None],
                "scenario_index": [0, 0, 1],
                "scenario_value": [0.9, 0.9, 1.1],
                "expected_income": [100.0, 90.0, 110.0],
                "volume": [1.0, 2.0, 3.0],
            }
        )
        path = tmp_path / "auto_range_null_quote.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "contract_error"
        detail = status["error_detail"]
        assert "Null quote_id values found in optimiser input" in detail
        assert "(2 rows)" in detail

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_missing_required_column_returns_400(
        self,
        client,
        tmp_path,
    ):
        """Projection seeding must preserve the existing loud column validation."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "scenario_index": [0, 1],
                "scenario_value": [0.9, 1.1],
                "expected_income": [100.0, 120.0],
            }
        )
        path = tmp_path / "missing_auto_range_constraint.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(
            str(path),
            config={
                "objective": "expected_income",
                "constraints": {"expected_margin": {"max": 35.0}},
            },
        )

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "contract_error"
        # The source scan refuses the projection before anything reads it.
        assert "Source projection references columns missing" in status["error_detail"]
        assert "expected_margin" in status["error_detail"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_ratebook_returns_no_warning(
        self,
        client,
        tmp_path,
    ):
        """Ratebook auto-range should not show an explanatory warning."""
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": [0, 1, 0, 1],
                "scenario_value": [0.9, 1.1, 0.9, 1.1],
                "expected_income": [100.0, 120.0, 90.0, 95.0],
                "expected_margin": [10.0, 30.0, 5.0, 9.0],
            }
        )
        path = tmp_path / "ratebook_scored.parquet"
        banding_path = tmp_path / "ratebook_banding.parquet"
        df.write_parquet(path)
        pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "territory": ["north", "south"],
            }
        ).write_parquet(banding_path)
        graph = _make_optimiser_graph(
            str(path),
            config={
                "mode": "ratebook",
                "objective": "expected_income",
                "constraints": {"expected_margin": {"max": 35.0}},
                "factor_columns": [["territory"]],
                "data_input": "source",
                "banding_source": "Territory_Banding",
            },
        )
        graph["nodes"].insert(
            1,
            {
                "id": "banding_node",
                "data": {
                    "label": "Territory Banding",
                    "nodeType": "dataInput",
                    "config": _snapshot_parquet_data_input(str(banding_path)),
                },
            },
        )
        graph["edges"].append(make_edge("banding_node", "opt").model_dump())

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed"
        data = status["result"]
        assert data["status"] == "ok"
        assert "warning" not in data
        assert "Ratebook factor-table coupling" not in str(status)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_ratebook_preserves_intermediate_data_input(
        self,
        client,
        tmp_path,
    ):
        """Configured non-source data_input must survive execution for auto-range."""
        source_path = tmp_path / "scored_source.parquet"
        banding_path = tmp_path / "banding_source.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": [0, 1, 0, 1],
                "scenario_value": [0.9, 1.1, 0.9, 1.1],
                "expected_income": [100.0, 120.0, 90.0, 95.0],
                "volume": [1.0, 2.0, 3.0, 4.0],
            }
        ).write_parquet(source_path)
        pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "territory": ["north", "south"],
            }
        ).write_parquet(banding_path)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(source_path)),
                        },
                    },
                    {
                        "id": "optimiser_input",
                        "data": {
                            "label": "optimiser_input",
                            "nodeType": "polars",
                            "config": {
                                "code": ("df = source.with_columns(volume=pl.col('volume') * 1.0)"),
                                "contract": {
                                    "inputs": ["volume"],
                                    "outputs": [],
                                },
                            },
                        },
                    },
                    {
                        "id": "banding",
                        "data": {
                            "label": "banding",
                            "nodeType": "dataInput",
                            "config": _snapshot_parquet_data_input(str(banding_path)),
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
                                "constraints": {"volume": {"min": 0.9}},
                                "quote_id": "quote_id",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "factor_columns": [["territory"]],
                                "banding_source": "banding",
                                "data_input": "optimiser_input",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "optimiser_input").model_dump(),
                    make_edge("optimiser_input", "opt").model_dump(),
                    make_edge("banding", "opt").model_dump(),
                ],
            }
        ).model_dump()

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed", status.get("message")
        assert status["result"]["ranges"]["volume"] == {"min": 4.0, "max": 6.0}

    def test_frontier_auto_range_ratebook_projection_seeds_data_and_banding_inputs(
        self,
    ):
        """Ratebook auto-range (the solve's demand) proves both optimiser parents narrow."""
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "scored",
                        "data": {
                            "label": "scored",
                            "nodeType": "dataInput",
                            "config": {},
                        },
                    },
                    {
                        "id": "banding",
                        "data": {
                            "label": "banding",
                            "nodeType": "dataInput",
                            "config": {},
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "ratebook optimiser",
                            "nodeType": "optimiser",
                            "config": {
                                "mode": "ratebook",
                                "data_input": "scored",
                                "banding_source": "banding",
                                "quote_id": "quote_ref",
                                "scenario_index": "scenario_index",
                                "scenario_value": "scenario_value",
                                "objective": "expected_income",
                                "constraints": {"expected_margin": {"min": 0.0}},
                                "factor_columns": [["territory"], ["channel", "age_band"]],
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("scored", "opt").model_dump(),
                    make_edge("banding", "opt").model_dump(),
                ],
            }
        )
        config = graph.node_map["opt"].data.config

        required = _optimiser_solve_required_columns_by_node(graph, "opt", config)

        solve_columns = frozenset(
            {"quote_ref", "scenario_index", "scenario_value", "expected_income", "expected_margin"}
        )
        assert required == {"scored": solve_columns}

        from haute._execution_context import ExecutionProfile
        from haute.projection import ProjectionRequest, plan

        projection = plan(
            ProjectionRequest(
                graph=graph,
                target_node_id="opt",
                profile=ExecutionProfile.OPTIMISER_SOLVE,
                source="batch",
                required_columns_by_node=required,
            )
        )

        assert pair_value(projection.edge_demands, "scored", "opt") == solve_columns
        assert pair_value(projection.edge_demands, "banding", "opt") == frozenset(
            {"quote_ref", "territory", "channel", "age_band"}
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_online_preserves_intermediate_data_input(
        self,
        client,
        tmp_path,
    ):
        """Configured online data_input must survive when it is an intermediate node."""
        source_path = tmp_path / "online_source.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2"],
                "scenario_index": [0, 1, 0, 1],
                "scenario_value": [0.9, 1.1, 0.9, 1.1],
                "expected_income": [100.0, 120.0, 90.0, 95.0],
                "volume": [1.0, 2.0, 3.0, 4.0],
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
                            "config": _snapshot_parquet_data_input(str(source_path)),
                        },
                    },
                    {
                        "id": "optimiser_input",
                        "data": {
                            "label": "optimiser_input",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = source",
                                "contract": {"inputs": [], "outputs": []},
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
                                "data_input": "optimiser_input",
                            },
                        },
                    },
                ],
                "edges": [
                    make_edge("source", "optimiser_input").model_dump(),
                    make_edge("optimiser_input", "opt").model_dump(),
                ],
            }
        ).model_dump()

        start_resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert start_resp.status_code == 200
        status = _poll_auto_range_until_done(client, start_resp.json()["job_id"])
        assert status["status"] == "completed", status.get("message")
        assert status["result"]["ranges"]["volume"] == {"min": 4.0, "max": 6.0}

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_frontier_auto_range_ratebook_still_validates_factor_columns(
        self,
        client,
        tmp_path,
    ):
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1"],
                "scenario_index": [0, 1],
                "scenario_value": [0.9, 1.1],
                "expected_income": [100.0, 120.0],
                "expected_margin": [10.0, 30.0],
            }
        )
        path = tmp_path / "ratebook_missing_factors.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(
            str(path),
            config={
                "mode": "ratebook",
                "objective": "expected_income",
                "constraints": {"expected_margin": {"max": 35.0}},
                "factor_columns": [],
            },
        )

        resp = client.post(
            "/api/optimiser/frontier/auto-range/start",
            json={"graph": graph, "node_id": "opt"},
        )

        assert resp.status_code == 400
        assert "factor_columns" in resp.json()["detail"]
