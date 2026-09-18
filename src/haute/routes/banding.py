"""Banding endpoints: whole-dataset statistics for the factor being edited."""

from __future__ import annotations

from fastapi import APIRouter, Request

from haute.graph_utils import flatten_graph
from haute.routes._banding_stats import BandingStatsService
from haute.routes._synchronous_analysis import run_until_disconnected
from haute.routes.node_data import _node_data_service
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import BandingStatsRequest, BandingStatsResponse

router = APIRouter(prefix="/api/banding", tags=["banding"])

_banding_stats_service = BandingStatsService(_node_data_service)


@router.post("/stats", response_model=BandingStatsResponse)
async def banding_stats(body: BandingStatsRequest, request: Request) -> BandingStatsResponse:
    """Return whole-dataset statistics for one banding factor.

    The editor supersedes its own request on every edit, so the abandoned one is
    cancelled rather than left scanning the whole dataset for an answer nobody
    will read.
    """
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    prepared = body.model_copy(update={"graph": graph})
    return await run_until_disconnected(
        request, lambda token: _banding_stats_service.stats(prepared, cancellation_token=token)
    )
