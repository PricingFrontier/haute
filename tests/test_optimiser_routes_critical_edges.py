"""Focused coverage for critical optimiser route edge paths."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
from fastapi import HTTPException

from tests.optimiser_fixtures import make_select_job as _make_select_job
from tests.optimiser_fixtures import run_frontier_and_wait

# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


def _frontier_job(*, artifact_handles: object | None = None) -> dict:
    solver = MagicMock()
    solver.solve.return_value = SimpleNamespace(
        total_objective=200.0,
        baseline_objective=190.0,
        total_constraints={"volume": 0.95},
        baseline_constraints={"volume": 0.90},
        lambdas={"volume": 0.7},
        converged=True,
        dataframe=pl.DataFrame({"optimal_scenario_value": [0.9, 1.0]}),
    )
    job = {
        "status": "completed",
        "solver": solver,
        "quote_grid": MagicMock(),
        "heavy_objects_expires_at": time.time() + 3600,
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "threshold_volume": 0.95,
                    "total_volume": 0.95,
                    "lambda_volume": 0.7,
                    "total_objective": 200.0,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "total_objective": 100.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.9},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.3},
            "converged": True,
        },
        "created_at": time.time(),
    }
    if artifact_handles is not None:
        job["artifact_handles"] = artifact_handles
    return job


def test_estimate_returns_input_metrics_when_metadata_lookup_fails(client, tmp_path: Path):
    from haute._sandbox import set_project_root
    from tests.conftest import make_edge, make_file_input_config, make_graph

    set_project_root(tmp_path)
    data_path = tmp_path / "scored.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": [0, 1, 0, 1],
            "scenario_value": [0.9, 1.1, 0.8, 1.0],
            "expected_income": [100.0, 110.0, 90.0, 95.0],
            "volume": [1.0, 0.9, 1.2, 1.1],
        }
    ).write_parquet(data_path)
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
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    )

    with (
        patch(
            "haute._ram_estimate._detailed_ancestor_source_metadata",
            side_effect=RuntimeError("metadata unavailable"),
        ),
        patch("haute.routes.optimiser.logger.warning") as log_warning,
    ):
        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph.model_dump(), "node_id": "opt"},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "total_rows": None,
        "quote_count": 2,
        "scenarios_per_quote_min": 2,
        "scenarios_per_quote_max": 2,
        "scenarios_per_quote_mean": 2.0,
        "expanded_row_count": 4,
    }
    assert log_warning.call_count == 1
    assert log_warning.call_args_list[0].args == ("optimiser_estimate_failed",)
    assert log_warning.call_args_list[0].kwargs["error"] == "metadata unavailable"
    assert log_warning.call_args_list[0].kwargs["node_id"] == "opt"


def test_apply_rejects_non_mapping_artifact_handles(client, clean_job_store):
    clean_job_store.jobs["bad_apply_handles"] = {
        "status": "completed",
        "artifact_handles": ["not", "a", "mapping"],
        "created_at": time.time(),
    }

    resp = client.post("/api/optimiser/apply", json={"job_id": "bad_apply_handles"})

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job artifact handles are invalid"


def test_apply_rejects_missing_artifact_summary(client, clean_job_store):
    clean_job_store.jobs["missing_apply_summary"] = {
        "status": "completed",
        "artifact_handles": {"apply_result": {"path": "already-validated-by-patch"}},
        "result": "not a summary mapping",
        "created_at": time.time(),
    }

    with patch(
        "haute.routes.optimiser._load_apply_result_artifact",
        return_value=pl.DataFrame({"quote_id": ["q1"]}),
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "missing_apply_summary"},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job summary is missing"


def test_apply_rejects_incomplete_artifact_summary(client, clean_job_store):
    clean_job_store.jobs["incomplete_apply_summary"] = {
        "status": "completed",
        "artifact_handles": {"apply_result": {"path": "already-validated-by-patch"}},
        "result": {"total_objective": "not numeric", "constraints": {"volume": 0.9}},
        "created_at": time.time(),
    }

    with patch(
        "haute.routes.optimiser._load_apply_result_artifact",
        return_value=pl.DataFrame({"quote_id": ["q1"]}),
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "incomplete_apply_summary"},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job summary is incomplete"


def test_frontier_fails_if_runtime_disappears_after_touch(client, clean_job_store):
    clean_job_store.jobs["frontier_runtime_race"] = {
        "status": "completed",
        "solver": MagicMock(),
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_runtime_race",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )

    assert resp.status_code == 400
    assert "re-run the solve" in resp.json()["detail"].lower()


def test_frontier_select_succeeds_when_runtime_is_absent(client, clean_job_store):
    clean_job_store.jobs["select_runtime_race"] = {
        "status": "completed",
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "threshold_volume": 0.95,
                    "total_volume": 0.95,
                    "lambda_volume": 0.7,
                    "total_objective": 200.0,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "baseline_objective": 90.0,
            "baseline_constraints": {"volume": 0.85},
        },
        "created_at": time.time(),
    }

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_runtime_race", "point_index": 0},
    )

    assert resp.status_code == 200
    assert resp.json()["constraints"] == {"volume": 0.95}


def test_frontier_apply_rejects_non_mapping_artifact_handles(client, clean_job_store):
    clean_job_store.jobs["select_bad_handles"] = _frontier_job(
        artifact_handles="not a mapping",
    )

    resp = client.post(
        "/api/optimiser/apply",
        json={"job_id": "select_bad_handles", "point_index": 0},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job artifact handles are invalid"


def test_frontier_apply_rejects_invalid_existing_apply_handle(client, clean_job_store):
    clean_job_store.jobs["select_bad_apply_handle"] = _frontier_job(
        artifact_handles={"frontier_apply_result:0": "not a handle mapping"},
    )

    resp = client.post(
        "/api/optimiser/apply",
        json={"job_id": "select_bad_apply_handle", "point_index": 0},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job frontier apply artifact handle is invalid"


def test_frontier_apply_cleans_new_artifact_after_unexpected_store_failure(
    client,
    clean_job_store,
):
    from haute.routes._optimiser_service import _persist_apply_result_artifact

    orphan_handle = _persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}))
    )
    assert orphan_handle is not None
    orphan_path = Path(orphan_handle["path"])
    orphan_dir = Path(orphan_handle["directory"])
    clean_job_store.jobs["select_store_failure"] = _frontier_job(artifact_handles={})
    apply_result = SimpleNamespace(
        dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}),
    )

    with (
        patch("price_contour.apply_from_grid", return_value=apply_result),
        patch(
            "haute.routes.optimiser._persist_apply_result_artifact",
            return_value=orphan_handle,
        ),
        patch.object(
            clean_job_store,
            "atomic_update_if_heavy_present",
            side_effect=RuntimeError("store write failed"),
        ),
        patch("haute.routes.optimiser.logger.error") as log_error,
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "select_store_failure", "point_index": 0},
        )

    assert resp.status_code == 500
    assert not orphan_path.exists()
    assert not orphan_dir.exists()
    log_error.assert_called_once()
    assert log_error.call_args.args == ("frontier_apply_materialise_failed",)
    assert log_error.call_args.kwargs["error"] == "store write failed"
    assert log_error.call_args.kwargs["job_id"] == "select_store_failure"
    assert log_error.call_args.kwargs["exc_info"] is True


def test_save_rechecks_solve_result_after_touch(client, clean_job_store, tmp_path: Path):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    clean_job_store.jobs["save_missing_after_touch"] = {
        "status": "completed",
        "solve_result": None,
        "created_at": time.time(),
    }

    try:
        set_project_root(tmp_path)
        with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
            resp = client.post(
                "/api/optimiser/save",
                json={
                    "job_id": "save_missing_after_touch",
                    "output_path": str(tmp_path / "out.json"),
                },
            )
    finally:
        set_project_root(original_root)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Job has no solve result"


def test_save_reraises_http_exception_from_artifact_build(
    client,
    clean_job_store,
    tmp_path: Path,
):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    clean_job_store.jobs["save_artifact_http_error"] = {
        "status": "completed",
        "solve_result": SimpleNamespace(),
        "config": {"mode": "online"},
        "node_label": "opt",
        "created_at": time.time(),
    }

    try:
        set_project_root(tmp_path)
        with patch(
            "haute.routes.optimiser._build_artifact_payload",
            side_effect=HTTPException(status_code=418, detail="artifact rejected"),
        ):
            resp = client.post(
                "/api/optimiser/save",
                json={
                    "job_id": "save_artifact_http_error",
                    "output_path": str(tmp_path / "out.json"),
                },
            )
    finally:
        set_project_root(original_root)

    assert resp.status_code == 418
    assert resp.json()["detail"] == "artifact rejected"


def test_mlflow_log_rechecks_solve_result_after_touch(client, clean_job_store):
    clean_job_store.jobs["mlflow_missing_after_touch"] = {
        "status": "completed",
        "solver": MagicMock(),
        "solve_result": None,
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "mlflow_missing_after_touch"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Job has no solve result"


def test_mlflow_log_rechecks_solver_after_touch(client, clean_job_store):
    clean_job_store.jobs["mlflow_solver_missing_after_touch"] = {
        "status": "completed",
        "solver": None,
        "solve_result": SimpleNamespace(),
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "mlflow_solver_missing_after_touch"},
        )

    assert resp.status_code == 400
    assert "re-run the solve" in resp.json()["detail"].lower()


# ===========================================================================
# Coverage-targeted behavioural tests for uncovered legitimate paths.
#
# Each test below pins a specific user-observable behaviour at a code path
# the existing happy-path tests do not exercise.  The pattern is:
#   1) Stand up a realistic mock job state that matches the production
#      precondition for the path under test.
#   2) Drive the public HTTP route (or, where the path is only reachable
#      through a private helper, call the helper directly through the
#      module).
#   3) Assert the response shape, the post-call job state, and the side
#      effects (artifacts, logs, store mutations).
# ===========================================================================


# ---------------------------------------------------------------------------
# /frontier/select — deselect (point_index = null)
# ---------------------------------------------------------------------------


def test_frontier_select_with_null_point_index_clears_selection_and_returns_base(
    client,
    clean_job_store,
):
    """Posting ``point_index=null`` after a prior selection must clear
    ``selected_frontier_point`` and return the base summary, not the
    previously-selected point's summary.  Without this branch a user can
    never go back to the un-selected baseline view.
    """
    job = _make_select_job()
    # Simulate a prior selection of point 0.
    job["selected_frontier_point"] = 0
    job["base_result"] = dict(job["result"])
    job["result"]["selected_frontier_point"] = 0
    job["result"]["total_objective"] = 100.0
    clean_job_store.jobs["select_deselect"] = job

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_deselect", "point_index": None},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["point_index"] is None
    # Returned values are the base, not point 0's (100.0).
    assert data["total_objective"] == 95.0
    assert data["constraints"] == {"volume": 0.85}

    # Job state agrees with the response.
    stored = clean_job_store.jobs["select_deselect"]
    assert stored["selected_frontier_point"] is None
    assert "selected_frontier_point" not in stored["result"]


# ---------------------------------------------------------------------------
# /frontier/select — atomic_update conflict yields 409
# ---------------------------------------------------------------------------


def test_frontier_select_returns_409_when_atomic_update_loses_race(
    client,
    clean_job_store,
):
    """If the job's status changes between the read and the atomic write,
    the user gets a clear 409 Conflict — not a silent overwrite or a
    generic 500."""
    job = _make_select_job()
    clean_job_store.jobs["select_race"] = job

    # Simulate a concurrent transition: ``atomic_update`` returns None to
    # signal "expected_status mismatch".
    with patch.object(clean_job_store, "atomic_update", return_value=None):
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "select_race", "point_index": 1},
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "job state changed" in detail.lower()
    assert "re-run the solve" in detail.lower()
    # Job state is left intact for inspection (no partial mutation).
    assert clean_job_store.jobs["select_race"]["status"] == "completed"


# ---------------------------------------------------------------------------
# /frontier/select — unexpected exception → 500 with internal-only detail
# ---------------------------------------------------------------------------


def test_frontier_select_unhandled_exception_logged_and_500(
    client,
    clean_job_store,
):
    """Unexpected errors in select must be logged (with traceback) and
    returned as a generic 500 — never bubbling internal state to the
    client."""
    from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
    from haute.routes.optimiser import logger as optimiser_logger

    clean_job_store.jobs["select_boom"] = _make_select_job()

    # Make the in-route helper raise an unexpected error mid-flow.
    with (
        patch(
            "haute.routes.optimiser._frontier_point_result_dict",
            side_effect=ZeroDivisionError("kaboom"),
        ),
        patch.object(optimiser_logger, "error") as log_error,
    ):
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "select_boom", "point_index": 0},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
    # The cause was logged with full context so it can be triaged.
    assert log_error.call_count == 1
    assert log_error.call_args.args == ("frontier_select_failed",)
    assert log_error.call_args.kwargs["job_id"] == "select_boom"
    assert log_error.call_args.kwargs["error"] == "kaboom"
    assert log_error.call_args.kwargs["exc_info"] is True


# ---------------------------------------------------------------------------
# /frontier — atomic_update conflict yields 409
# ---------------------------------------------------------------------------


def test_run_frontier_returns_409_when_atomic_update_loses_race(
    client,
    clean_job_store,
):
    """Same race semantics as select, on the recompute path.  Frontier
    artefacts created up to this point must be cleaned up and a 409 raised."""
    solver = MagicMock()
    solver.frontier.return_value = SimpleNamespace(
        points=pl.DataFrame({"total_objective": [100.0], "volume": [0.9], "lambda_volume": [0.25]})
    )
    clean_job_store.jobs["frontier_race"] = {
        "status": "completed",
        "solver": solver,
        "quote_grid": MagicMock(),
        "config": {
            "mode": "online",
            "constraints": {"volume": {"min": 0.9}},
            "frontier_ranges": {"volume": {"min": 0.85, "max": 0.95}},
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "artifact_handles": {},
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "atomic_update", return_value=None):
        status = run_frontier_and_wait(
            client,
            {"job_id": "frontier_race"},
        )

    assert status["status"] == "contract_error"
    assert status["http_status_code"] == 409
    assert "recomputing the frontier" in status["message"].lower()
    assert "re-run the solve" in status["message"].lower()


# ---------------------------------------------------------------------------
# /apply — non-ratebook reuse of cached frontier-apply artifact
# ---------------------------------------------------------------------------


def test_apply_reuses_cached_frontier_apply_artifact_for_online_mode(
    client,
    clean_job_store,
    tmp_path: Path,
):
    """When a frontier point has already been applied, a subsequent apply
    for the same point must reuse the persisted artifact (no re-execution
    of ``apply_from_grid``).  This is the user-visible "Save result" round-trip
    where the artifact file should be served from disk on the second call.
    """
    from haute.routes._optimiser_service import _persist_apply_result_artifact

    persisted_df = pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "optimal_scenario_value": [1.04, 0.97],
        }
    )
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=persisted_df))
    assert handle is not None

    point = {
        "total_objective": 130.0,
        "total_volume": 0.93,
        "lambda_volume": 0.55,
        "threshold_volume": 0.93,
        "converged": True,
    }
    clean_job_store.jobs["apply_cached"] = {
        "status": "completed",
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [point],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "artifact_handles": {"frontier_apply_result:0": handle},
        "created_at": time.time(),
    }

    # ``apply_from_grid`` MUST NOT be called when reusing the cached artifact.
    with patch("price_contour.apply_from_grid") as apply_mock:
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_cached", "point_index": 0},
        )

    assert resp.status_code == 200
    data = resp.json()
    # Response is sourced from the persisted artifact, not a fresh solve.
    assert data["from_artifact"] is True
    assert data["row_count"] == persisted_df.height
    response_preview = pl.DataFrame(data["preview"])
    assert response_preview.equals(persisted_df)
    apply_mock.assert_not_called()
    # The artifact file is still on disk afterwards (not consumed).
    assert Path(handle["path"]).is_file()


# ---------------------------------------------------------------------------
# /apply — quote grid touch returns False (heavy state evicted)
# ---------------------------------------------------------------------------


def test_apply_returns_400_when_quote_grid_evicted_from_heavy_state(
    client,
    clean_job_store,
):
    """If the quote grid's heavy-object TTL has elapsed, applying a frontier
    point must return a clear 400 instructing the user to re-run the solve.
    Earlier this path returned a confusing 500.
    """
    clean_job_store.jobs["apply_evicted"] = {
        "status": "completed",
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        # No quote_grid in the dict, no artifact_handles either — heavy
        # state has been slimmed by TTL.
        "artifact_handles": {},
        "created_at": time.time(),
    }

    # ``touch_heavy_objects`` returns False when the required keys are
    # missing — the dispatcher must surface that as a clean 400.
    with patch.object(clean_job_store, "touch_heavy_objects", return_value=False):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_evicted", "point_index": 0},
        )

    assert resp.status_code == 400
    assert "quote grid is not available" in resp.json()["detail"].lower()


def test_apply_returns_400_when_quote_grid_value_is_none_after_touch(
    client,
    clean_job_store,
):
    """Touch may report success but the actual value can still be None
    under a race — the second guard inside the apply path catches that."""
    clean_job_store.jobs["apply_none_grid"] = {
        "status": "completed",
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "quote_grid": None,  # touch passes (key present), value is None
        "artifact_handles": {},
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_none_grid", "point_index": 0},
        )

    assert resp.status_code == 400
    assert "quote grid is not available" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /apply — persistence returned None (in-memory only path)
# ---------------------------------------------------------------------------


def test_apply_falls_back_to_in_memory_when_persistence_unavailable(
    client,
    clean_job_store,
):
    """When ``_persist_apply_result_artifact`` returns None (e.g. artifact
    root is unwritable), the apply must still return the correct preview
    from the in-memory dataframe — without crashing or persisting a partial
    artifact handle."""
    persisted_df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [0.99]})
    apply_result = SimpleNamespace(
        total_objective=130.0,
        baseline_objective=90.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=True,
        dataframe=persisted_df,
    )
    quote_grid = MagicMock()
    clean_job_store.jobs["apply_no_persist"] = {
        "status": "completed",
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "quote_grid": quote_grid,
        "artifact_handles": {},
        "created_at": time.time(),
    }

    with (
        patch("price_contour.apply_from_grid", return_value=apply_result),
        patch("haute.routes.optimiser._persist_apply_result_artifact", return_value=None),
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_no_persist", "point_index": 0},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["from_artifact"] is False
    assert data["row_count"] == persisted_df.height
    response_preview = pl.DataFrame(data["preview"])
    assert response_preview.equals(persisted_df)
    # No frontier_apply_result handle was registered (persistence failed).
    job = clean_job_store.jobs["apply_no_persist"]
    assert "frontier_apply_result:0" not in job.get("artifact_handles", {})


# ---------------------------------------------------------------------------
# /apply — atomic_update race with successful persistence triggers cleanup
# ---------------------------------------------------------------------------


def test_apply_cleans_up_orphan_artifact_when_atomic_update_loses_race(
    client,
    clean_job_store,
):
    """When the artifact was persisted but the atomic write loses to a
    concurrent state change, the just-written artifact must be cleaned up
    so it does not leak.  The user gets a 409 Conflict, not a 500.
    """
    persisted_df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [0.99]})
    apply_result = SimpleNamespace(
        total_objective=130.0,
        baseline_objective=90.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=True,
        dataframe=persisted_df,
    )
    new_handle = {"path": "/tmp/fake/handle.parquet", "directory": "/tmp/fake"}

    clean_job_store.jobs["apply_orphan"] = {
        "status": "completed",
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "quote_grid": MagicMock(),
        "artifact_handles": {},
        "created_at": time.time(),
    }

    with (
        patch("price_contour.apply_from_grid", return_value=apply_result),
        patch(
            "haute.routes.optimiser._persist_apply_result_artifact",
            return_value=new_handle,
        ),
        patch.object(
            clean_job_store,
            "atomic_update_if_heavy_present",
            return_value=None,
        ),
        patch(
            "haute.routes.optimiser._cleanup_apply_result_artifact",
        ) as cleanup_mock,
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_orphan", "point_index": 0},
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "runtime state changed" in detail.lower()
    # The orphan artifact was scheduled for cleanup.
    cleanup_mock.assert_called_once_with(new_handle)


# ---------------------------------------------------------------------------
# /mlflow/log — ratebook factor_tables fallback to solve_result
# ---------------------------------------------------------------------------


def test_mlflow_log_ratebook_falls_back_to_solve_result_factor_tables(
    client,
    clean_job_store,
):
    """The artifact payload prefers ``result.factor_tables`` but falls
    back to ``solve_result.factor_tables`` when the result has been
    slimmed.  This test pins the fallback path so a future cleanup of
    ``result`` doesn't silently log empty factor tables to MLflow.
    """
    factor_tables = {"region": [{"__factor_group__": "North", "value": 1.0}]}
    factor_dtypes = {"region": [{"column": "region", "dtype": {"kind": "String"}}]}
    solve_result = SimpleNamespace(
        total_objective=100.0,
        baseline_objective=90.0,
        total_constraints={"volume": 0.95},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.1},
        converged=True,
        clamp_rate=0.02,
        factor_tables=factor_tables,
        factor_dtypes=factor_dtypes,
        cd_iterations=3,
        iterations=5,
    )
    solver = MagicMock()
    solver.summary.return_value = {
        "params": {"mode": "ratebook"},
        "metrics": {"total_objective": 100.0},
        "artifacts": {"factor_tables": factor_tables},
    }

    clean_job_store.jobs["mlflow_fallback"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "objective": "expected_margin"},
        # Note: no factor_tables here — forces the fallback.
        "result": {
            "mode": "ratebook",
            "total_objective": 100.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.95},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.1},
            "converged": True,
        },
        "solver": solver,
        "solve_result": solve_result,
        "node_label": "opt",
        "created_at": time.time(),
    }

    captured_payloads: list[str] = []

    def _capture_log_artifact(artifact_path: str, *args, **kwargs) -> None:
        # ``mlflow_log`` writes the JSON payload to a tempfile and then
        # asks mlflow to log it; capture the file content so we can
        # inspect what would have shipped to MLflow.
        if artifact_path.endswith("optimiser_result.json"):
            captured_payloads.append(Path(artifact_path).read_text(encoding="utf-8"))

    fake_mlflow = MagicMock()
    fake_run = MagicMock()
    fake_run.info.run_id = "run-x"
    fake_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=fake_run)
    fake_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)
    fake_mlflow.log_artifact.side_effect = _capture_log_artifact

    with (
        patch.dict("sys.modules", {"mlflow": fake_mlflow}),
        patch(
            "haute.modelling._mlflow_log.configure_mlflow_tracking",
            return_value=("file:///tmp", "local"),
        ),
        patch("haute.modelling._mlflow_log.resolve_experiment_name", return_value="haute-opt"),
        patch("haute.modelling._mlflow_log.build_run_url", return_value="http://run-x"),
        patch.object(clean_job_store, "touch_heavy_objects", return_value=True),
    ):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "mlflow_fallback"},
        )

    assert resp.status_code == 200
    assert resp.json()["run_id"] == "run-x"
    # The captured payload reflects the fallback: factor_tables came from
    # solve_result, not the (slimmed) result dict.
    import json as _json

    assert captured_payloads, "expected optimiser_result.json to be logged"
    payload = _json.loads(captured_payloads[-1])
    assert payload["factor_tables"] == factor_tables
    assert payload["factor_dtypes"] == factor_dtypes
    assert payload["clamp_rate"] == 0.02


# ---------------------------------------------------------------------------
# Ratebook materialisation: warning message + cached return + 409
# ---------------------------------------------------------------------------


def test_ratebook_materialise_emits_non_converged_warning_in_response(
    client,
    clean_job_store,
):
    """When the ratebook re-solve at a frontier point fails to converge,
    the response and stored result must carry the standard warning so the
    UI can show it.  Without this, non-convergence is silent."""
    factor_contexts = SimpleNamespace(n_quotes=1, factor_specs=[["region"]])
    solver = MagicMock()
    # Solver returns a non-converged result for the frontier point.
    solver.solve.return_value = SimpleNamespace(
        total_objective=120.0,
        baseline_objective=90.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=False,
        cd_iterations=1,
        clamp_rate=0.0,
        # Solver-side factor_tables are {factor: {level: scenario_value}}.
        factor_tables={"region": {"North": 1.0}},
    )
    point = {
        "total_objective": 120.0,
        "total_volume": 0.93,
        "lambda_volume": 0.55,
        "threshold_volume": 0.93,
        "converged": False,
    }
    clean_job_store.jobs["ratebook_warn"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [point],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "solver": solver,
        "quote_grid": MagicMock(),
        "ratebook_factor_contexts": factor_contexts,
        "factor_columns_valid": [["region"]],
        "factor_level_counts": {"region": {"North": 1}},
        "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
        "artifact_handles": {},
        "created_at": time.time(),
    }

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={
            "job_id": "ratebook_warn",
            "point_index": 0,
            "include_ratebook_tables": True,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["converged"] is False
    assert data["warning"] is not None
    assert "did not converge" in data["warning"].lower()
    # The same warning is in the stored result (so subsequent reads see it).
    assert "did not converge" in clean_job_store.jobs["ratebook_warn"]["result"]["warning"].lower()


def test_ratebook_materialise_rejects_missing_dtype_metadata(
    client,
    clean_job_store,
):
    """A persisted ratebook cannot be materialised without its dtype contract."""
    factor_contexts = SimpleNamespace(n_quotes=1, factor_specs=[["region"]])
    solver = MagicMock()
    solver.solve.return_value = SimpleNamespace(
        total_objective=120.0,
        baseline_objective=90.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=True,
        cd_iterations=1,
        clamp_rate=0.0,
        factor_tables={"region": {"North": 1.0}},
    )
    point = {
        "total_objective": 120.0,
        "total_volume": 0.93,
        "lambda_volume": 0.55,
        "threshold_volume": 0.93,
        "converged": True,
    }
    clean_job_store.jobs["ratebook_missing_dtypes"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [point],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "solver": solver,
        "quote_grid": MagicMock(),
        "ratebook_factor_contexts": factor_contexts,
        "factor_columns_valid": [["region"]],
        "factor_level_counts": {"region": {"North": 1}},
        "artifact_handles": {},
        "created_at": time.time(),
    }

    response = client.post(
        "/api/optimiser/frontier/select",
        json={
            "job_id": "ratebook_missing_dtypes",
            "point_index": 0,
            "include_ratebook_tables": True,
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Ratebook factor dtype metadata is missing"
    solver.solve.assert_called_once()


def test_ratebook_materialise_returns_cached_when_lambdas_match_and_no_dataframe_required(
    client,
    clean_job_store,
):
    """A second select for an already-materialised ratebook frontier point
    must reuse the cached result without re-invoking ``solver.solve`` —
    this is the hot-path the UI hits when toggling between tabs.
    """
    factor_tables = {"region": [{"__factor_group__": "North", "value": 1.0}]}
    factor_dtypes = {"region": [{"column": "region", "dtype": {"kind": "String"}}]}
    factor_contexts = SimpleNamespace(n_quotes=1, factor_specs=[["region"]])
    solver = MagicMock()  # Must NOT be called.
    point = {
        "total_objective": 130.0,
        "total_volume": 0.93,
        "lambda_volume": 0.55,
        "threshold_volume": 0.93,
        "converged": True,
    }
    cached_result = {
        "mode": "ratebook",
        "total_objective": 130.0,
        "baseline_objective": 90.0,
        "constraints": {"volume": 0.93},
        "baseline_constraints": {"volume": 0.85},
        "lambdas": {"volume": 0.55},
        "converged": True,
        "selected_frontier_point": 0,
        "factor_tables": factor_tables,
        "factor_dtypes": factor_dtypes,
    }
    clean_job_store.jobs["ratebook_cached"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [point],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": cached_result,
        "base_result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "selected_frontier_point": 0,
        "solver": solver,
        "quote_grid": MagicMock(),
        "ratebook_factor_contexts": factor_contexts,
        "factor_columns_valid": [["region"]],
        "factor_level_counts": {"region": {"North": 1}},
        "factor_dtypes": factor_dtypes,
        "artifact_handles": {},
        "created_at": time.time(),
    }

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={
            "job_id": "ratebook_cached",
            "point_index": 0,
            "include_ratebook_tables": True,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["factor_tables"] == factor_tables
    # Solver was NOT re-invoked — the cached result was reused.
    solver.solve.assert_not_called()


def test_ratebook_materialise_returns_409_on_atomic_update_race(
    client,
    clean_job_store,
):
    """Concurrent state change between solve and write surfaces as 409, not
    a generic 500.  The user gets a clear "re-run the solve" instruction."""
    factor_contexts = SimpleNamespace(n_quotes=1, factor_specs=[["region"]])
    solver = MagicMock()
    solver.solve.return_value = SimpleNamespace(
        total_objective=130.0,
        baseline_objective=90.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=True,
        cd_iterations=1,
        clamp_rate=0.0,
        # Solver-side factor_tables are {factor: {level: scenario_value}}.
        factor_tables={"region": {"North": 1.0}},
    )
    point = {
        "total_objective": 130.0,
        "total_volume": 0.93,
        "lambda_volume": 0.55,
        "threshold_volume": 0.93,
        "converged": True,
    }
    clean_job_store.jobs["ratebook_race"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [point],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "solver": solver,
        "quote_grid": MagicMock(),
        "ratebook_factor_contexts": factor_contexts,
        "factor_columns_valid": [["region"]],
        "factor_level_counts": {"region": {"North": 1}},
        "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
        "artifact_handles": {},
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "atomic_update", return_value=None):
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "ratebook_race",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "materialising" in detail.lower()
    assert "re-run the solve" in detail.lower()


def test_ratebook_runtime_state_or_raise_rejects_partial_heavy_objects(
    client,
    clean_job_store,
):
    """Touch may report success but the underlying values can still be
    None under a tight TTL race; the explicit check at line 637 is what
    catches that and surfaces a 400 rather than an AttributeError 500.
    """
    clean_job_store.jobs["ratebook_partial"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        # Keys present, values None — the race window.
        "solver": None,
        "quote_grid": None,
        "factors_df": None,
        "factor_columns_valid": [["region"]],
        "artifact_handles": {},
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "ratebook_partial",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )

    assert resp.status_code == 400
    assert "ratebook runtime state is not available" in resp.json()["detail"].lower()


def test_ratebook_runtime_state_rejects_invalid_factor_columns_metadata(
    client,
    clean_job_store,
):
    """If ``factor_columns_valid`` is malformed (e.g. a list containing
    non-string entries), the materialise path must surface a 500 with a
    typed message — not blow up later inside ``solver.solve``."""
    factors_df = pl.DataFrame({"region": ["North"]})
    solver = MagicMock()  # Must NOT be called: validation rejects first.
    clean_job_store.jobs["ratebook_bad_factors"] = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        "solver": solver,
        "quote_grid": MagicMock(),
        "factors_df": factors_df,
        # Malformed: list-of-list-of-string is required.
        "factor_columns_valid": [[42]],
        "artifact_handles": {},
        "created_at": time.time(),
    }

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={
            "job_id": "ratebook_bad_factors",
            "point_index": 0,
            "include_ratebook_tables": True,
        },
    )

    assert resp.status_code == 500
    assert "factor column metadata" in resp.json()["detail"].lower()
    solver.solve.assert_not_called()


# ---------------------------------------------------------------------------
# _invalidate_frontier_apply_artifact_handles: bad handle shape
# ---------------------------------------------------------------------------


def test_run_frontier_rejects_invalid_apply_handle_shape(
    client,
    clean_job_store,
):
    """If a job has an artefact handle that isn't a dict (data corruption),
    the frontier recompute must fail loudly with a typed 500 rather than
    silently dropping the handle."""
    solver = MagicMock()
    solver.frontier.return_value = SimpleNamespace(
        points=pl.DataFrame({"total_objective": [100.0], "volume": [0.9], "lambda_volume": [0.25]})
    )
    clean_job_store.jobs["frontier_bad_handle"] = {
        "status": "completed",
        "solver": solver,
        "quote_grid": MagicMock(),
        "config": {
            "mode": "online",
            "constraints": {"volume": {"min": 0.9}},
            "frontier_ranges": {"volume": {"min": 0.85, "max": 0.95}},
        },
        "result": {
            "mode": "online",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
        },
        # Frontier-apply handle exists but is not a dict — corruption.
        "artifact_handles": {"frontier_apply_result:0": "not-a-dict"},
        "created_at": time.time(),
    }

    status = run_frontier_and_wait(
        client,
        {"job_id": "frontier_bad_handle"},
    )

    assert status["status"] == "error"
    assert status["http_status_code"] == 500
    assert "frontier apply artifact handle is invalid" in status["message"].lower()
