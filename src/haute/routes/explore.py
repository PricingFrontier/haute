"""Explore endpoints: pivots and relationships over the shared data point a node reads."""

from __future__ import annotations

from fastapi import APIRouter, Request

from haute.graph_utils import flatten_graph
from haute.routes._explore_relationships import ExploreRelationshipsService
from haute.routes._job_store import get_job_store
from haute.routes._pivot_service import PivotService
from haute.routes._synchronous_analysis import run_until_disconnected
from haute.routes.node_data import _node_data_service
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import (
    ExplorePivotMembersRequest,
    ExplorePivotMembersResponse,
    ExplorePivotRunRequest,
    ExplorePivotRunResponse,
    ExplorePivotStatusResponse,
    ExploreRelationshipsRequest,
    ExploreRelationshipsResponse,
)

router = APIRouter(prefix="/api/explore", tags=["explore"])

_store = get_job_store("explore")
_pivot_service = PivotService(_store)
_explore_relationships_service = ExploreRelationshipsService(_node_data_service)


@router.post("/pivots/run", response_model=ExplorePivotRunResponse)
def run_pivot(body: ExplorePivotRunRequest) -> ExplorePivotRunResponse:
    """Calculate one pivot from an already materialised Explore dataframe."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    return _pivot_service.start(body.model_copy(update={"graph": graph}))


@router.get("/pivots/status/{job_id}", response_model=ExplorePivotStatusResponse)
def pivot_status(job_id: str) -> ExplorePivotStatusResponse:
    return _pivot_service.status(job_id)


@router.post("/pivots/cancel/{job_id}", response_model=ExplorePivotStatusResponse)
def cancel_pivot(job_id: str) -> ExplorePivotStatusResponse:
    return _pivot_service.cancel(job_id)


@router.post("/pivots/members", response_model=ExplorePivotMembersResponse)
async def pivot_members(
    body: ExplorePivotMembersRequest, request: Request
) -> ExplorePivotMembersResponse:
    """List one dimension's members; a client that leaves cancels the scan."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    prepared = body.model_copy(update={"graph": graph})
    return await run_until_disconnected(
        request, lambda token: _pivot_service.members(prepared, cancellation_token=token)
    )


@router.post("/relationships", response_model=ExploreRelationshipsResponse)
async def explore_relationships(
    body: ExploreRelationshipsRequest, request: Request
) -> ExploreRelationshipsResponse:
    """Relate features to a target and check a key; a client that leaves cancels the scan."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    prepared = body.model_copy(update={"graph": graph})
    return await run_until_disconnected(
        request,
        lambda token: _explore_relationships_service.relationships(
            prepared, cancellation_token=token
        ),
    )
