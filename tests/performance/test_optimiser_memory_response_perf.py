from __future__ import annotations

import gc
import weakref
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import JobStore
from haute.routes._optimiser_limits import FRONTIER_POINT_LIMIT
from tests.job_store_support import seed_job
from tests.optimiser_fixtures import (
    library_frontier_frame,
    make_online_frontier_job,
)

pytestmark = pytest.mark.perf

_LARGE_FRONTIER_POINT_COUNT = FRONTIER_POINT_LIMIT * 25
# Each returned point is a strict typed point (thresholds, bounds, totals and
# lambdas per constraint, the sv_* statistics and the solver path): about 850
# bytes for these rows. The capped response grows with the returned points
# only, never with the full frontier, which is 25 times larger. The solve status
# carries the frontier twice (the result's and its own), hence twice the cap.
_MAX_CAPPED_FRONTIER_RESPONSE_BYTES = FRONTIER_POINT_LIMIT * 1_000


@pytest.fixture()
def clean_optimiser_job_store() -> Any:
    from haute.routes.optimiser import _store

    _store.clear_all()
    yield _store
    _store.clear_all()


class _FullFrontierFrame:
    """A library frontier frame too large to serialise: only its capped head may be read.

    ``head`` answers with a real ``library_frontier_frame`` in price-contour's
    exact points schema, so the route's strict point contract runs on it.
    """

    def __init__(self, total_rows: int) -> None:
        self.total_rows = total_rows
        self.head_limits: list[int] = []
        self.full_to_dicts_calls = 0
        self.serialized_rows = 0

    def __len__(self) -> int:
        return self.total_rows

    def head(self, limit: int) -> pl.DataFrame:
        self.head_limits.append(limit)
        row_count = min(limit, self.total_rows)
        self.serialized_rows += row_count
        return library_frontier_frame(
            [
                {
                    "lambda_loss_ratio": float(idx) / 10_000.0,
                    "total_loss_ratio": 0.8 + float(idx % 50) / 1_000.0,
                    "total_objective": 100_000.0 + float(idx),
                }
                for idx in range(row_count)
            ],
            constraint_names=["loss_ratio"],
        )

    def to_dicts(self) -> list[dict[str, float]]:
        self.full_to_dicts_calls += 1
        raise AssertionError("large frontier frame must be sliced before serialization")


class _ManualTimer:
    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if self.cancelled:
            return
        self.callback()


def _recording_timer_factory(timers: list[_ManualTimer]) -> type[_ManualTimer]:
    class RecordingTimer(_ManualTimer):
        def start(self) -> None:
            super().start()
            timers.append(self)

    return RecordingTimer


class _HeavyPayload:
    __slots__ = ("label", "__weakref__")

    def __init__(self, label: str) -> None:
        self.label = label


def test_solve_status_caps_the_frontier_before_serialising_a_large_point_frame(
    client: Any,
    clean_optimiser_job_store: Any,
) -> None:
    from haute.routes._frontier_point_summary import constraint_kinds
    from haute.routes._optimiser_limits import limited_frontier_payload

    points = _FullFrontierFrame(_LARGE_FRONTIER_POINT_COUNT)
    config = {
        "mode": "online",
        "objective": "income",
        "constraints": {"loss_ratio": {"max": 0.95}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }
    payload = limited_frontier_payload(
        points,
        mode="online",
        constraint_kinds=constraint_kinds(config["constraints"]),
        swept_axes=["loss_ratio"],
    )
    job = make_online_frontier_job(config=config)
    job["frontier_data"] = payload
    job["result"]["frontier"] = payload
    seed_job(clean_optimiser_job_store, "large-frontier", job)

    response = client.get("/api/optimiser/solve/status/large-frontier")

    assert response.status_code == 200, response.text
    frontier = response.json()["frontier"]
    assert frontier["n_points"] == _LARGE_FRONTIER_POINT_COUNT
    assert frontier["points_returned"] == FRONTIER_POINT_LIMIT
    assert frontier["points_limit"] == FRONTIER_POINT_LIMIT
    assert frontier["points_truncated"] is True
    assert len(frontier["points"]) == FRONTIER_POINT_LIMIT
    assert frontier["constraint_names"] == ["loss_ratio"]
    assert len(response.content) < 2 * _MAX_CAPPED_FRONTIER_RESPONSE_BYTES
    assert points.head_limits == [FRONTIER_POINT_LIMIT]
    assert points.full_to_dicts_calls == 0
    assert points.serialized_rows == FRONTIER_POINT_LIMIT


def test_completed_optimiser_jobs_slim_heavy_objects_then_evict_owned_artifacts() -> None:
    import polars as pl

    from haute.routes._optimiser_artifacts import _persist_apply_result_artifact

    timers: list[_ManualTimer] = []
    store = JobStore(
        ttl_seconds=5,
        heavy_object_ttl_seconds=1,
        heavy_object_timer_factory=_recording_timer_factory(timers),
    )
    apply_artifact_handle = _persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"quote_id": ["q1"]})),
    )
    assert apply_artifact_handle is not None
    artifact_dir = Path(str(apply_artifact_handle["directory"]))
    artifact_path = Path(str(apply_artifact_handle["path"]))

    solver = _HeavyPayload("solver")
    solve_result = _HeavyPayload("solve_result")
    quote_grid = _HeavyPayload("quote_grid")
    retained_refs = {
        "solver": weakref.ref(solver),
        "solve_result": weakref.ref(solve_result),
        "quote_grid": weakref.ref(quote_grid),
    }

    with patch("haute.routes._job_store.time.time", return_value=100.0):
        job_id = store.create_job(
            {
                "status": "running",
                "progress": 0.2,
                "message": "Solving",
                "config": {"objective": "premium", "constraints": {"loss_ratio": 0.91}},
                "node_label": "pricing optimiser",
            }
        )
        JobLifecycle(store).transition(
            job_id,
            to="completed",
            message="Completed",
            elapsed_seconds=12.5,
            fields={
                "progress": 1.0,
                "solver": solver,
                "solve_result": solve_result,
                "quote_grid": quote_grid,
                "result": {
                    "mode": "online",
                    "total_objective": 1234.5,
                    "converged": True,
                    "frontier": {"n_points": 50_000, "points": []},
                },
                "frontier_data": {
                    "status": "ok",
                    "n_points": 50_000,
                    "points_returned": FRONTIER_POINT_LIMIT,
                    "points": [],
                },
                "artifact_handles": {"apply_result": apply_artifact_handle},
            },
        )

    del solver, solve_result, quote_grid
    gc.collect()
    assert all(ref() is not None for ref in retained_refs.values())
    assert len(timers) == 1
    assert timers[0].started is True
    assert timers[0].daemon is True
    assert timers[0].delay == pytest.approx(1.0)

    with patch("haute.routes._job_store.time.time", return_value=100.5):
        retained = store.get_job(job_id)
    assert retained is not None
    assert {"solver", "solve_result", "quote_grid"}.issubset(retained)
    assert artifact_path.is_file()
    del retained

    with patch("haute.routes._job_store.time.time", return_value=102.0):
        slimmed = store.get_job(job_id)
    gc.collect()

    assert slimmed is not None
    assert "solver" not in slimmed
    assert "solve_result" not in slimmed
    assert "quote_grid" not in slimmed
    assert all(ref() is None for ref in retained_refs.values())
    assert slimmed["status"] == "completed"
    assert slimmed["progress"] == 1.0
    assert slimmed["message"] == "Completed"
    assert slimmed["elapsed_seconds"] == 12.5
    assert slimmed["config"] == {"objective": "premium", "constraints": {"loss_ratio": 0.91}}
    assert slimmed["result"]["total_objective"] == 1234.5
    assert slimmed["frontier_data"]["points_returned"] == FRONTIER_POINT_LIMIT
    assert slimmed["artifact_handles"]["apply_result"]["path"] == str(artifact_path)
    assert slimmed["artifact_handles"]["apply_result"]["row_count"] == 1
    assert slimmed["heavy_objects_cleared_at"] == 102.0
    assert slimmed["heavy_objects_retention_seconds"] == 1
    assert timers[0].cancelled is True
    assert artifact_path.is_file()

    with patch("haute.routes._job_store.time.time", return_value=106.0):
        assert store.get_job(job_id) is None
    assert not artifact_dir.exists()
