from __future__ import annotations

import gc
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from haute.routes._job_store import JobStore
from haute.routes._optimiser_limits import FRONTIER_POINT_LIMIT

pytestmark = pytest.mark.perf

_LARGE_FRONTIER_POINT_COUNT = FRONTIER_POINT_LIMIT * 25
_MAX_CAPPED_FRONTIER_RESPONSE_BYTES = 350_000


@pytest.fixture()
def clean_optimiser_job_store() -> Any:
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    _store.jobs.clear()
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


class _FullFrontierFrame:
    def __init__(self, total_rows: int) -> None:
        self.total_rows = total_rows
        self.head_limits: list[int] = []
        self.full_to_dicts_calls = 0
        self.slice_to_dicts_calls = 0
        self.serialized_rows = 0

    def __len__(self) -> int:
        return self.total_rows

    def head(self, limit: int) -> _FrontierFrameSlice:
        self.head_limits.append(limit)
        return _FrontierFrameSlice(parent=self, row_count=min(limit, self.total_rows))

    def to_dicts(self) -> list[dict[str, float]]:
        self.full_to_dicts_calls += 1
        raise AssertionError("large frontier frame must be sliced before serialization")


class _FrontierFrameSlice:
    def __init__(self, *, parent: _FullFrontierFrame, row_count: int) -> None:
        self._parent = parent
        self._row_count = row_count

    def __len__(self) -> int:
        return self._row_count

    def to_dicts(self) -> list[dict[str, float]]:
        self._parent.slice_to_dicts_calls += 1
        self._parent.serialized_rows += self._row_count
        return [
            {
                "lambda_loss_ratio": float(idx) / 10_000.0,
                "loss_ratio": 0.8 + float(idx % 50) / 1_000.0,
                "total_objective": 100_000.0 + float(idx),
            }
            for idx in range(self._row_count)
        ]


class _FrontierSolver:
    def __init__(self, points: _FullFrontierFrame) -> None:
        self._points = points
        self.calls: list[dict[str, Any]] = []

    def frontier(
        self,
        quote_grid: object,
        *,
        threshold_ranges: dict[str, tuple[float, float]],
        n_points_per_dim: int,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "quote_grid": quote_grid,
                "threshold_ranges": threshold_ranges,
                "n_points_per_dim": n_points_per_dim,
            }
        )
        return SimpleNamespace(points=self._points)


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


def test_frontier_route_caps_response_before_serialising_large_point_frame(
    client: Any,
    clean_optimiser_job_store: Any,
) -> None:
    points = _FullFrontierFrame(_LARGE_FRONTIER_POINT_COUNT)
    quote_grid = object()
    solver = _FrontierSolver(points)
    clean_optimiser_job_store.jobs["large-frontier"] = {
        "status": "completed",
        "solver": solver,
        "quote_grid": quote_grid,
        "created_at": time.time(),
        "completed_at": time.time(),
    }

    start = client.post(
        "/api/optimiser/frontier",
        json={
            "job_id": "large-frontier",
            "threshold_ranges": {"loss_ratio": [0.80, 0.95]},
            "n_points_per_dim": 100,
        },
    )
    assert start.status_code == 200, start.text
    frontier_job_id = start.json()["job_id"]
    deadline = time.monotonic() + 30.0
    while True:
        response = client.get(f"/api/optimiser/frontier/status/{frontier_job_id}")
        assert response.status_code == 200, response.text
        if response.json()["status"] != "running":
            break
        assert time.monotonic() < deadline, "frontier sweep did not finish in time"
        time.sleep(0.02)

    status_payload = response.json()
    assert status_payload["status"] == "completed", status_payload.get("message", "")
    payload = status_payload["result"]
    assert payload["n_points"] == _LARGE_FRONTIER_POINT_COUNT
    assert payload["points_returned"] == FRONTIER_POINT_LIMIT
    assert payload["points_limit"] == FRONTIER_POINT_LIMIT
    assert payload["points_truncated"] is True
    assert len(payload["points"]) == FRONTIER_POINT_LIMIT
    assert payload["constraint_names"] == ["loss_ratio"]
    assert len(response.content) < _MAX_CAPPED_FRONTIER_RESPONSE_BYTES

    assert solver.calls == [
        {
            "quote_grid": quote_grid,
            "threshold_ranges": {"loss_ratio": (0.8, 0.95)},
            "n_points_per_dim": 100,
        }
    ]
    assert points.head_limits == [FRONTIER_POINT_LIMIT]
    assert points.full_to_dicts_calls == 0
    assert points.slice_to_dicts_calls == 1
    assert points.serialized_rows == FRONTIER_POINT_LIMIT


def test_completed_optimiser_jobs_slim_heavy_objects_then_evict_owned_artifacts() -> None:
    import polars as pl

    from haute.routes._optimiser_service import _persist_apply_result_artifact

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
        store.atomic_update(
            job_id,
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Completed",
                "elapsed_seconds": 12.5,
                "completed_at": time.time(),
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
