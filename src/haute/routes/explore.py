"""Explore endpoints: run, status, cancel."""

from __future__ import annotations

from fastapi import APIRouter

from haute.graph_utils import flatten_graph
from haute.routes._explore_service import ExploreService
from haute.routes._job_store import get_job_store
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import ExploreRunRequest, ExploreRunResponse, ExploreStatusResponse

router = APIRouter(prefix="/api/explore", tags=["explore"])

_store = get_job_store("explore")
_explore_service = ExploreService(_store)


@router.post("/run", response_model=ExploreRunResponse)
def run_explore(body: ExploreRunRequest) -> ExploreRunResponse:
    """Start (or reuse cached) materialisation of an Explore node's upstream dataset."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    return _explore_service.start(body.model_copy(update={"graph": graph}))


@router.get("/status/{job_id}", response_model=ExploreStatusResponse)
def explore_status(job_id: str) -> ExploreStatusResponse:
    """Poll an Explore cache materialisation job."""
    return _explore_service.status(job_id)


@router.post("/cancel/{job_id}", response_model=ExploreStatusResponse)
def cancel_explore(job_id: str) -> ExploreStatusResponse:
    """Cancel an in-progress Explore cache materialisation job."""
    return _explore_service.cancel(job_id)
