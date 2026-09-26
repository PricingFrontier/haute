"""Focused coverage for critical optimiser route edge paths."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from tests.job_store_support import discard_corrupt_job, seed_job
from tests.optimiser_fixtures import (
    logged_json_artifacts,
    run_frontier_and_wait,
    use_local_mlflow_store,
)
from tests.optimiser_fixtures import make_select_job as _make_select_job

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
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "solver": solver,
        "quote_grid": MagicMock(),
        "heavy_objects_expires_at": time.time() + 3600,
        "frontier_data": {
            "frontier_generation": 0,
            "status": "ok",
            "points": [
                {
                    "threshold_volume": 0.95,
                    "bound_volume": 0.95,
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
            "frontier_generation": 0,
        },
        "created_at": time.time(),
        "completed_at": time.time(),
    }
    if artifact_handles is not None:
        job["artifact_handles"] = artifact_handles
    return job


def test_estimate_schema_rejects_missing_columns() -> None:
    from haute.routes.optimiser import _estimate_quote_id_column_or_raise

    source = pl.LazyFrame({"quote_id": ["q1"]})
    config = {
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }

    with pytest.raises(HTTPException) as exc_info:
        _estimate_quote_id_column_or_raise(source, config)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Missing columns in scored data: "
        "['expected_income', 'scenario_index', 'scenario_value', 'volume']. "
        "Available: ['quote_id']"
    )


def test_estimate_schema_rejects_numeric_quote_ids() -> None:
    from haute.routes.optimiser import _estimate_quote_id_column_or_raise

    source = pl.LazyFrame(
        {
            "quote_id": [1],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [100.0],
        }
    )
    config = {
        "objective": "expected_income",
        "constraints": {},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }

    with pytest.raises(HTTPException) as exc_info:
        _estimate_quote_id_column_or_raise(source, config)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "quote_id must be Utf8 (String), Categorical, or Enum, got Int64. "
        "Numeric, binary, and other dtypes are not supported as quote_id columns."
    )


def test_frontier_lambda_rejects_empty_name() -> None:
    from haute.routes._frontier_point_summary import (
        FrontierPointDataError,
        add_frontier_point_lambda,
    )

    with pytest.raises(FrontierPointDataError) as exc_info:
        add_frontier_point_lambda({}, "", 0.2, field="lambda")

    assert exc_info.value.status_code == 500
    assert str(exc_info.value) == (
        "Frontier point data is malformed: lambda names must be non-empty strings"
    )


def test_frontier_lambda_rejects_conflicting_value() -> None:
    from haute.routes._frontier_point_summary import (
        FrontierPointDataError,
        add_frontier_point_lambda,
    )

    with pytest.raises(FrontierPointDataError) as exc_info:
        add_frontier_point_lambda(
            {"volume": 0.2},
            "volume",
            0.4,
            field="lambda_volume",
        )

    assert exc_info.value.status_code == 500
    assert str(exc_info.value) == (
        "Frontier point data is malformed: conflicting lambda for 'volume'"
    )


def _estimate_graph(tmp_path: Path):
    from haute._sandbox import set_project_root
    from tests.conftest import make_edge, make_graph, make_ready_file_input_config

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
    return make_graph(
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


def test_estimate_metadata_failure_is_an_error(client, tmp_path: Path):
    """An unexpected metadata failure is reported, never an estimate whose
    missing total looks like a source of unknown size."""
    graph = _estimate_graph(tmp_path)

    with patch(
        "haute._ram_estimate._detailed_ancestor_source_metadata",
        side_effect=RuntimeError("metadata resolver defect"),
    ):
        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph.model_dump(), "node_id": "opt"},
        )

    assert resp.status_code == 500


def test_estimate_of_an_unknown_source_size_keeps_the_input_metrics(client, tmp_path: Path):
    from haute._ram_estimate import _AncestorSourceMetadata

    graph = _estimate_graph(tmp_path)

    with patch(
        "haute._ram_estimate._detailed_ancestor_source_metadata",
        return_value=_AncestorSourceMetadata(row_count=None, column_count=0, sources=()),
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


def test_apply_rejects_non_mapping_artifact_handles(client, clean_job_store):
    seed_job(
        clean_job_store,
        "bad_apply_handles",
        {
            "status": "completed",
            "artifact_handles": ["not", "a", "mapping"],
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

    try:
        resp = client.post("/api/optimiser/apply", json={"job_id": "bad_apply_handles"})

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Job artifact handles are invalid"
    finally:
        discard_corrupt_job(clean_job_store, "bad_apply_handles")


def test_apply_rejects_missing_artifact_summary(client, clean_job_store):
    seed_job(
        clean_job_store,
        "missing_apply_summary",
        {
            "status": "completed",
            "artifact_handles": {"apply_result": {"path": "already-validated-by-patch"}},
            "result": "not a summary mapping",
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

    try:
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
    finally:
        discard_corrupt_job(clean_job_store, "missing_apply_summary")


def test_apply_rejects_incomplete_artifact_summary(client, clean_job_store):
    seed_job(
        clean_job_store,
        "incomplete_apply_summary",
        {
            "status": "completed",
            "artifact_handles": {"apply_result": {"path": "already-validated-by-patch"}},
            "result": {"total_objective": "not numeric", "constraints": {"volume": 0.9}},
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

    try:
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
    finally:
        discard_corrupt_job(clean_job_store, "incomplete_apply_summary")


def test_frontier_fails_if_runtime_disappears_after_touch(client, clean_job_store):
    seed_job(
        clean_job_store,
        "frontier_runtime_race",
        {
            "status": "completed",
            "solver": MagicMock(),
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

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
    seed_job(
        clean_job_store,
        "select_runtime_race",
        {
            "status": "completed",
            "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
            "frontier_data": {
                "frontier_generation": 0,
                "status": "ok",
                "points": [
                    {
                        "threshold_volume": 0.95,
                        "bound_volume": 0.95,
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
                "frontier_generation": 0,
            },
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_runtime_race", "point_index": 0},
    )

    assert resp.status_code == 200
    assert resp.json()["constraints"] == {"volume": 0.95}


def test_frontier_apply_rejects_non_mapping_artifact_handles(client, clean_job_store):
    seed_job(
        clean_job_store,
        "select_bad_handles",
        _frontier_job(
            artifact_handles="not a mapping",
        ),
    )

    try:
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "select_bad_handles", "point_index": 0},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Job artifact handles are invalid"
    finally:
        discard_corrupt_job(clean_job_store, "select_bad_handles")


def test_frontier_apply_rejects_invalid_existing_apply_handle(client, clean_job_store):
    seed_job(
        clean_job_store,
        "select_bad_apply_handle",
        _frontier_job(
            artifact_handles={"frontier_apply_result:0": "not a handle mapping"},
        ),
    )

    try:
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "select_bad_apply_handle", "point_index": 0},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Job frontier apply artifact handle is invalid"
    finally:
        discard_corrupt_job(clean_job_store, "select_bad_apply_handle")


def test_frontier_apply_cleans_new_artifact_after_unexpected_store_failure(
    client,
    clean_job_store,
):
    from haute.routes._optimiser_artifacts import _persist_apply_result_artifact

    orphan_handle = _persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}))
    )
    assert orphan_handle is not None
    orphan_path = Path(orphan_handle["path"])
    orphan_dir = Path(orphan_handle["directory"])
    seed_job(clean_job_store, "select_store_failure", _frontier_job(artifact_handles={}))
    apply_result = SimpleNamespace(
        dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}),
    )

    with (
        patch("price_contour.apply_from_grid", return_value=apply_result),
        patch(
            "haute.routes._optimiser_frontier._persist_apply_result_artifact",
            return_value=orphan_handle,
        ),
        patch.object(
            clean_job_store,
            "atomic_update_if_heavy_present",
            side_effect=RuntimeError("store write failed"),
        ),
        patch("haute.server.logger.error") as log_error,
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "select_store_failure", "point_index": 0},
        )

    # The request-created artifact is removed before the failure propagates.
    assert not orphan_path.exists()
    assert not orphan_dir.exists()
    assert resp.status_code == 500
    assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
    # The application handler logs it; the route no longer catches it.
    log_error.assert_called_once()
    assert log_error.call_args.args == ("unhandled_exception",)
    assert log_error.call_args.kwargs["error_class"] == "RuntimeError"
    assert log_error.call_args.kwargs["path"] == "/api/optimiser/apply"


def test_save_reraises_http_exception_from_artifact_build(
    client,
    clean_job_store,
    tmp_path: Path,
):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    seed_job(
        clean_job_store,
        "save_artifact_http_error",
        {
            "status": "completed",
            "result": {
                "lambdas": {},
                "total_objective": 1.0,
                "constraints": {},
                "baseline_objective": 0.0,
                "baseline_constraints": {},
                "converged": True,
            },
            "config": {"mode": "online"},
            "node_label": "opt",
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

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
    seed_job(clean_job_store, "select_deselect", job)

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
    stored = clean_job_store.require_job("select_deselect")
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
    seed_job(clean_job_store, "select_race", job)

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
    assert clean_job_store.require_job("select_race")["status"] == "completed"


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

    seed_job(clean_job_store, "select_boom", _make_select_job())

    # Make the in-route helper raise an unexpected error mid-flow.
    with (
        patch(
            "haute.routes._optimiser_frontier._frontier_point_result_dict",
            side_effect=ZeroDivisionError("kaboom"),
        ),
        patch("haute.server.logger.error") as log_error,
    ):
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "select_boom", "point_index": 0},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
    # The application handler logs it; the route no longer catches it.
    log_error.assert_called_once()
    assert log_error.call_args.args == ("unhandled_exception",)
    assert log_error.call_args.kwargs["error_class"] == "ZeroDivisionError"
    assert log_error.call_args.kwargs["path"] == "/api/optimiser/frontier/select"


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
        points=pl.DataFrame(
            {
                "total_objective": [100.0],
                "volume": [0.9],
                "lambda_volume": [0.25],
                "bound_volume": [0.9],
                "converged": [True],
            }
        )
    )
    seed_job(
        clean_job_store,
        "frontier_race",
        {
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
            "completed_at": time.time(),
        },
    )

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
    from haute.routes._optimiser_artifacts import _persist_apply_result_artifact

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
        "bound_volume": 0.93,
        "converged": True,
    }
    seed_job(
        clean_job_store,
        "apply_cached",
        {
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
            "completed_at": time.time(),
        },
    )

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
    seed_job(
        clean_job_store,
        "apply_evicted",
        {
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
                        "bound_volume": 0.93,
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
            "completed_at": time.time(),
        },
    )

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
    seed_job(
        clean_job_store,
        "apply_none_grid",
        {
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
                        "bound_volume": 0.93,
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
            "completed_at": time.time(),
        },
    )

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

    seed_job(
        clean_job_store,
        "apply_orphan",
        {
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
                        "bound_volume": 0.93,
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
            "completed_at": time.time(),
        },
    )

    with (
        patch("price_contour.apply_from_grid", return_value=apply_result),
        patch(
            "haute.routes._optimiser_frontier._persist_apply_result_artifact",
            return_value=new_handle,
        ),
        patch.object(
            clean_job_store,
            "atomic_update_if_heavy_present",
            return_value=None,
        ),
        patch(
            "haute.routes._optimiser_frontier._cleanup_apply_result_artifact",
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
# /mlflow/log — the ratebook anchor publishes its own factor tables
# ---------------------------------------------------------------------------


def test_mlflow_log_ratebook_anchor_uses_its_own_factor_tables(
    client,
    clean_job_store,
    tmp_path,
    monkeypatch,
):
    """With a materialised frontier point selected, ``result`` holds that
    point's tables; logging the anchor must still log the anchor's own."""
    anchor_tables = {"region": [{"__factor_group__": "North", "value": 1.0}]}
    point_tables = {"region": [{"__factor_group__": "North", "value": 1.3}]}
    factor_dtypes = {"region": [{"column": "region", "dtype": {"kind": "String"}}]}
    anchor = {
        "mode": "ratebook",
        "total_objective": 100.0,
        "baseline_objective": 90.0,
        "constraints": {"volume": 0.95},
        "baseline_constraints": {"volume": 0.85},
        "lambdas": {"volume": 0.1},
        "converged": True,
        "cd_iterations": 3,
        "clamp_rate": 0.02,
        "factor_tables": anchor_tables,
        "combined_factor_bounds": {"min": 0.9, "max": 1.1},
        "factor_dtypes": factor_dtypes,
    }
    seed_job(
        clean_job_store,
        "mlflow_anchor_tables",
        {
            "status": "completed",
            "config": {"mode": "ratebook", "objective": "expected_margin"},
            "base_result": anchor,
            "result": {
                **anchor,
                "total_objective": 120.0,
                "factor_tables": point_tables,
                "selected_frontier_point": 0,
            },
            "selected_frontier_point": 0,
            "publish_summary": {
                "params": {"mode": "ratebook"},
                "metrics": {"total_objective": 100.0},
                "artifacts": {"factor_tables": anchor_tables},
            },
            "node_label": "opt",
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

    store = use_local_mlflow_store(tmp_path, monkeypatch)

    resp = client.post(
        "/api/optimiser/mlflow/log",
        json={"job_id": "mlflow_anchor_tables"},
    )

    assert resp.status_code == 200, resp.text
    logged = logged_json_artifacts(store, resp.json()["run_id"], tmp_path / "logged")
    payload = logged["optimiser_result.json"]
    assert payload["total_objective"] == 100.0
    assert payload["factor_tables"] == anchor_tables
    assert payload["factor_dtypes"] == factor_dtypes
    assert payload["clamp_rate"] == 0.02
    assert payload["combined_factor_bounds"] == {"min": 0.9, "max": 1.1}
    assert payload["cd_iterations"] == 3
    assert payload["solver_settings"]["max_cd_iterations"] == 10
    params = store.get_run(resp.json()["run_id"]).data.params
    assert params["solver_settings.cd_tolerance"] == "0.001"


# ---------------------------------------------------------------------------
# Ratebook materialisation: warning message + cached return + 409
# ---------------------------------------------------------------------------


_RATEBOOK_POINT_TABLES = {"region": {"North": 1.0}}
_RATEBOOK_FACTOR_DTYPES = {"region": [{"column": "region", "dtype": {"kind": "String"}}]}


def _ratebook_materialise_job(**overrides: object) -> dict:
    """A completed ratebook job with one retained frontier point and its tables.

    Carries no solver, quote grid or factor contexts: materialising a
    ratebook point reads the tables the frontier kept, never re-solving.
    """
    job = {
        "status": "completed",
        "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        "frontier_data": {
            "frontier_generation": 0,
            "status": "ok",
            "points": [
                {
                    "total_objective": 130.0,
                    "total_volume": 0.93,
                    "lambda_volume": 0.55,
                    "threshold_volume": 0.93,
                    "bound_volume": 0.93,
                    "iterations": 4,
                    "clamp_rate": 0.01,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "frontier_factor_tables": [_RATEBOOK_POINT_TABLES],
        "result": {
            "mode": "ratebook",
            "total_objective": 95.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.85},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.0},
            "converged": True,
            "frontier_generation": 0,
        },
        "factor_columns_valid": [["region"]],
        "factor_level_counts": {"region": {"North": 1}},
        "combined_factor_bounds": {"min": 0.1, "max": 10.0},
        "factor_dtypes": _RATEBOOK_FACTOR_DTYPES,
        "artifact_handles": {},
        "created_at": time.time(),
        "completed_at": time.time(),
    }
    job.update(overrides)
    return job


def _select_ratebook_point(client, job_id: str):
    return client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": job_id, "point_index": 0, "include_ratebook_tables": True},
    )


def test_ratebook_materialise_reads_totals_and_tables_from_one_frontier(
    client,
    clean_job_store,
    monkeypatch,
):
    """A recompute that publishes between the select route's first read and the
    materialisation must not pair one point's totals with another's tables:
    materialisation re-reads the row and its tables together under the parent
    lock that recompute publishes under."""
    import haute.routes._optimiser_frontier as frontier_module

    seed_job(clean_job_store, "ratebook_recompute_race", _ratebook_materialise_job())
    new_point = {
        "total_objective": 240.0,
        "total_volume": 1.02,
        "lambda_volume": 0.3,
        "threshold_volume": 1.0,
        "bound_volume": 1.0,
        "iterations": 6,
        "clamp_rate": 0.02,
        "converged": True,
    }
    new_tables = {"region": {"North": 1.21}}
    original = frontier_module._frontier_point_result_dict
    recomputed = False

    def recompute_after_first_read(job, point_index):
        nonlocal recomputed
        result = original(job, point_index)
        if not recomputed:
            recomputed = True
            assert clean_job_store.atomic_update(
                "ratebook_recompute_race",
                {
                    "frontier_data": {
                        "status": "ok",
                        "points": [new_point],
                        "n_points": 1,
                        "constraint_names": ["volume"],
                    },
                    "frontier_factor_tables": [new_tables],
                },
                expected_status="completed",
            )
        return result

    monkeypatch.setattr(frontier_module, "_frontier_point_result_dict", recompute_after_first_read)
    resp = _select_ratebook_point(client, "ratebook_recompute_race")

    assert resp.status_code == 200, resp.text
    selected = resp.json()
    assert selected["total_objective"] == 240.0
    assert selected["constraints"] == {"volume": 1.02}
    assert selected["cd_iterations"] == 6
    assert [row["optimal_scenario_value"] for row in selected["factor_tables"]["region"]] == [1.21]


def test_ratebook_materialise_keeps_unswept_constraint_totals(client, clean_job_store):
    """A materialised ratebook point (which is what save and MLflow publish)
    carries the frontier row's total and bound for every configured constraint,
    swept or not."""
    job = _ratebook_materialise_job(
        config={
            "mode": "ratebook",
            "constraints": {"volume": {"min": 0.9}, "loss": {"max": 25.0}},
        }
    )
    job["frontier_data"]["points"][0].update(
        {"total_loss": 20.0, "lambda_loss": 0.1, "threshold_loss": 25.0, "bound_loss": 25.0}
    )
    job["frontier_data"]["constraint_names"] = ["volume", "loss"]
    job["frontier_data"]["swept_axes"] = ["volume"]
    job["result"]["constraints"] = {"volume": 0.85, "loss": 21.0}
    job["result"]["baseline_constraints"] = {"volume": 0.85, "loss": 21.0}
    seed_job(clean_job_store, "ratebook_unswept", job)

    resp = _select_ratebook_point(client, "ratebook_unswept")

    assert resp.status_code == 200, resp.text
    assert resp.json()["constraints"] == {"volume": 0.93, "loss": 20.0}
    assert resp.json()["effective_bounds"] == {
        "volume": {"kind": "min", "bound": 0.93},
        "loss": {"kind": "max", "bound": 25.0},
    }
    stored = clean_job_store.require_job("ratebook_unswept")["result"]
    assert stored["constraints"] == {"volume": 0.93, "loss": 20.0}


def test_ratebook_materialise_attaches_kept_tables_to_frontier_row_without_heavy_state(
    client,
    clean_job_store,
):
    """With no solver, grid or factor contexts on the job, a ratebook point
    materialises from the frontier row and the tables the frontier kept."""
    seed_job(clean_job_store, "ratebook_no_heavy", _ratebook_materialise_job())

    resp = _select_ratebook_point(client, "ratebook_no_heavy")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_objective"] == 130.0
    assert data["constraints"] == {"volume": 0.93}
    assert data["lambdas"] == {"volume": 0.55}
    assert data["converged"] is True
    assert data["cd_iterations"] == 4
    assert data["clamp_rate"] == 0.01
    assert data["factor_tables"] == {
        "region": [{"__factor_group__": "North", "optimal_scenario_value": 1.0, "quote_count": 1}]
    }
    assert data.get("warning") is None
    stored = clean_job_store.require_job("ratebook_no_heavy")["result"]
    assert stored["factor_tables"] == data["factor_tables"]
    assert stored["factor_dtypes"] == _RATEBOOK_FACTOR_DTYPES


@pytest.mark.parametrize(
    "frontier_factor_tables",
    [
        pytest.param(None, id="missing"),
        pytest.param([_RATEBOOK_POINT_TABLES, _RATEBOOK_POINT_TABLES], id="misaligned"),
    ],
)
def test_ratebook_materialise_rejects_missing_or_misaligned_frontier_factor_tables(
    client,
    clean_job_store,
    frontier_factor_tables,
):
    """A ratebook point cannot be materialised without the tables the frontier
    kept for it; a missing or misaligned list is a loud 500, never a re-solve."""
    job = _ratebook_materialise_job()
    if frontier_factor_tables is None:
        del job["frontier_factor_tables"]
    else:
        job["frontier_factor_tables"] = frontier_factor_tables
    seed_job(clean_job_store, "ratebook_no_tables", job)

    resp = _select_ratebook_point(client, "ratebook_no_tables")

    assert resp.status_code == 500
    assert resp.json()["detail"] == (
        "Job frontier factor tables are missing or do not match the frontier points"
    )
    assert clean_job_store.require_job("ratebook_no_tables").get("selected_frontier_point") is None


def test_ratebook_materialise_emits_non_converged_warning_in_response(
    client,
    clean_job_store,
):
    """When the frontier row at the selected point did not converge, the
    response and stored result must carry the standard warning so the UI can
    show it.  Without this, non-convergence is silent."""
    job = _ratebook_materialise_job()
    job["frontier_data"]["points"][0]["converged"] = False
    seed_job(clean_job_store, "ratebook_warn", job)

    resp = _select_ratebook_point(client, "ratebook_warn")

    assert resp.status_code == 200
    data = resp.json()
    assert data["converged"] is False
    assert data["cd_iterations"] == 4
    assert data["factor_tables"]["region"][0]["optimal_scenario_value"] == 1.0
    assert data["warning"] is not None
    assert "did not converge" in data["warning"].lower()
    # The same warning is in the stored result (so subsequent reads see it).
    assert (
        "did not converge"
        in clean_job_store.require_job("ratebook_warn")["result"]["warning"].lower()
    )


def test_ratebook_materialise_rejects_missing_dtype_metadata(
    client,
    clean_job_store,
):
    """A persisted ratebook cannot be materialised without its dtype contract."""
    job = _ratebook_materialise_job()
    del job["factor_dtypes"]
    seed_job(clean_job_store, "ratebook_missing_dtypes", job)

    response = _select_ratebook_point(client, "ratebook_missing_dtypes")

    assert response.status_code == 500
    assert response.json()["detail"] == "Ratebook factor dtype metadata is missing"


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
        "bound_volume": 0.93,
        "converged": True,
    }
    cached_result = {
        "frontier_generation": 0,
        "mode": "ratebook",
        "total_objective": 130.0,
        "baseline_objective": 90.0,
        "constraints": {"volume": 0.93},
        "baseline_constraints": {"volume": 0.85},
        "effective_bounds": {"volume": {"kind": "min", "bound": 0.93}},
        "lambdas": {"volume": 0.55},
        "converged": True,
        "selected_frontier_point": 0,
        "factor_tables": factor_tables,
        "combined_factor_bounds": {"min": 0.1, "max": 10.0},
        "factor_dtypes": factor_dtypes,
    }
    seed_job(
        clean_job_store,
        "ratebook_cached",
        {
            "status": "completed",
            "config": {"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
            "frontier_data": {
                "frontier_generation": 0,
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
            "combined_factor_bounds": {"min": 0.1, "max": 10.0},
            "factor_dtypes": factor_dtypes,
            "artifact_handles": {},
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )

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
    """Concurrent state change between reading the point and writing it
    surfaces as 409, not a generic 500.  The user gets a clear "re-run the
    solve" instruction."""
    seed_job(clean_job_store, "ratebook_race", _ratebook_materialise_job())

    with patch.object(clean_job_store, "atomic_update", return_value=None):
        resp = _select_ratebook_point(client, "ratebook_race")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "materialising" in detail.lower()
    assert "re-run the solve" in detail.lower()


def test_ratebook_runtime_state_or_raise_rejects_partial_heavy_objects(
    client,
    clean_job_store,
):
    """A ratebook frontier recompute needs the solver and quote grid.  Touch
    may report success but the underlying values can still be None under a
    tight TTL race; the explicit check in ``ratebook_runtime_state_or_raise``
    catches that and surfaces a 400 rather than an AttributeError 500.
    """
    seed_job(
        clean_job_store,
        "ratebook_partial",
        _ratebook_materialise_job(
            # Keys present, values None — the race window.
            solver=None,
            quote_grid=None,
            factors_df=None,
        ),
    )

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/frontier",
            json={"job_id": "ratebook_partial", "threshold_ranges": {"volume": [0.85, 0.95]}},
        )

    assert resp.status_code == 400
    assert "ratebook runtime state is not available" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "request_route",
    [pytest.param("select", id="materialise"), pytest.param("frontier", id="recompute")],
)
def test_ratebook_runtime_state_rejects_invalid_factor_columns_metadata(
    client,
    clean_job_store,
    request_route,
):
    """If ``factor_columns_valid`` is malformed (e.g. a list containing
    non-string entries), both the materialise path and the recompute path
    must surface a 500 with a typed message — not blow up later inside the
    solver."""
    solver = MagicMock()  # Must NOT be called: validation rejects first.
    seed_job(
        clean_job_store,
        "ratebook_bad_factors",
        _ratebook_materialise_job(
            solver=solver,
            quote_grid=MagicMock(),
            ratebook_factor_contexts=SimpleNamespace(n_quotes=1, factor_specs=[["region"]]),
            # Malformed: list-of-list-of-string is required.
            factor_columns_valid=[[42]],
        ),
    )

    if request_route == "select":
        resp = _select_ratebook_point(client, "ratebook_bad_factors")
    else:
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "ratebook_bad_factors",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Ratebook factor column metadata is invalid"
    solver.solve.assert_not_called()
    solver.frontier.assert_not_called()


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
        points=pl.DataFrame(
            {
                "total_objective": [100.0],
                "volume": [0.9],
                "lambda_volume": [0.25],
                "bound_volume": [0.9],
                "converged": [True],
            }
        )
    )
    seed_job(
        clean_job_store,
        "frontier_bad_handle",
        {
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
            "completed_at": time.time(),
        },
    )

    try:
        status = run_frontier_and_wait(
            client,
            {"job_id": "frontier_bad_handle"},
        )

        assert status["status"] == "error"
        assert status["http_status_code"] == 500
        assert "frontier apply artifact handle is invalid" in status["message"].lower()
    finally:
        discard_corrupt_job(clean_job_store, "frontier_bad_handle")
